"""Customer accounts: subscription, usage, channels and checkout - plus the signed billing inbox.

* ``/internal/billing/events``: entitlement snapshots from the commercial service (HMAC with
  ``billing_secret``, caller ``commercial``). Applied idempotently and in order (``entitlements.apply_snapshot``).
* ``/api/account/*``: only for ``customer`` accounts. Everything is derived from the signed-in session: no
  route takes a user id, and nothing here lets the browser set a plan, a limit or a subscription state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import billing_link, entitlements, queue, sharing, storage
from ..config import get_settings
from ..db import get_db
from ..models import AccountSubscription, Clip, Job, Streamer
from ..platforms import InvalidStreamURL, VideoUnavailable, canonical_youtube, parse_stream_url
from ..security import sha256_bytes, verify_signature
from .deps import CurrentUser, _path_qs, audit, authenticated_user

log = logging.getLogger(__name__)
internal = APIRouter(tags=["billing-internal"])
router = APIRouter(prefix="/api/account", tags=["account"])

PLANS = ("basic", "creator", "pro")
MARKETS = ("BR", "US")
LANGUAGES = ("pt-BR", "en")


def customer_user(user: CurrentUser = Depends(authenticated_user)) -> CurrentUser:
    if user.role != "customer":
        raise HTTPException(403, "área exclusiva de assinantes")
    return user


# ---------------------------------------------------------------------------
# signed inbox from the commercial service
# ---------------------------------------------------------------------------


async def _signed_by_commercial(request: Request) -> bytes:
    """Body of a request signed by the commercial service (``billing_secret``, caller ``commercial``)."""
    s = get_settings()
    body = await request.body()
    chk = verify_signature(s.billing_secret if len(s.billing_secret or "") >= 32 else "", request.headers,
                           request.method, _path_qs(request), sha256_bytes(body), s.signature_max_skew_s)
    if not chk.ok or chk.worker != "commercial":
        log.warning("billing link request rejected", extra={"reason": chk.reason or "caller"})
        raise HTTPException(401, "invalid signature")
    return body


@internal.post("/internal/billing/events")
async def billing_event(request: Request, db: Session = Depends(get_db)):
    body = await _signed_by_commercial(request)
    try:
        payload = json.loads(body or b"{}")
        outcome = entitlements.apply_snapshot(db, payload)
    except entitlements.SnapshotRejected as exc:
        db.rollback()
        log.error("billing snapshot rejected", extra={"field": str(exc)})
        raise HTTPException(422, f"invalid snapshot: {exc}") from exc
    except entitlements.AccountNotReady as exc:
        db.rollback()
        raise HTTPException(409, "account not ready") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, "invalid json") from exc
    db.commit()
    return {"outcome": outcome}


@internal.post("/internal/billing/state")
async def billing_state(request: Request, db: Session = Depends(get_db)):
    """Reconciliation: what this API has applied for the given subscriptions (read-only, signed).

    The commercial service compares each ``last_seq`` with its own outbox to find snapshots that never arrived
    (for example after a database restore) and paid subscriptions that are not active here."""
    body = await _signed_by_commercial(request)
    try:
        refs = json.loads(body or b"{}").get("refs")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(422, "invalid json") from exc
    if not isinstance(refs, list) or len(refs) > 500:
        raise HTTPException(422, "refs must be a list of at most 500 items")
    wanted = set()
    for item in refs:
        if (not isinstance(item, dict) or item.get("provider") not in entitlements.PROVIDERS
                or not isinstance(item.get("subscription_ref"), str) or not 0 < len(item["subscription_ref"]) <= 120):
            raise HTTPException(422, "invalid ref")
        wanted.add((item["provider"], item["subscription_ref"]))
    items = []
    for provider in {p for p, _ in wanted}:
        names = [r for p, r in wanted if p == provider]
        for row in db.scalars(select(AccountSubscription).where(AccountSubscription.provider == provider,
                                                               AccountSubscription.subscription_ref.in_(names))):
            items.append({"provider": row.provider, "subscription_ref": row.subscription_ref, "account_id": row.user_id,
                          "last_seq": row.last_seq, "plan": row.plan, "access_status": row.access_status,
                          "access_until": _iso(row.access_until)})
    return {"items": items}


# ---------------------------------------------------------------------------
# subscription and usage
# ---------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _subscription_view(row: AccountSubscription | None) -> dict | None:
    if row is None:
        return None
    return {
        "plan": row.plan, "provider": row.provider, "market": row.market,
        "status": row.access_status, "financial_status": row.financial_status, "review": row.review,
        "access_until": _iso(row.access_until), "period_start": _iso(row.period_start), "period_end": _iso(row.period_end),
        "renews_at": _iso(row.renews_at), "cancel_at_period_end": row.cancel_at_period_end, "manage": row.manage,
        "renewal": row.renewal, "payments": row.payments or [],
    }


@router.get("/subscription")
def subscription(db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    ent = entitlements.current(db, user.id)
    row = db.get(AccountSubscription, ent.subscription_id) if ent else db.scalar(
        select(AccountSubscription).where(AccountSubscription.user_id == user.id).order_by(AccountSubscription.updated_at.desc()).limit(1))
    usage = entitlements.usage_summary(db, user.id)
    return {
        "entitled": ent is not None,
        "subscription": _subscription_view(row),
        "limits": ent.limits if ent else None,
        "usage": usage,
        "checkout_available": billing_link.configured(),
    }


class CheckoutIn(BaseModel):
    plan: str = Field(pattern="^(basic|creator|pro)$")
    market: str = Field(pattern="^(BR|US)$")
    language: str = Field("pt-BR", pattern="^(pt-BR|en)$")


def _provider_url(url: str, market: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    host = u.hostname or ""
    return host == "checkout.stripe.com" if market == "US" else (host == "infinitepay.io" or host.endswith(".infinitepay.io"))


@router.post("/checkout", status_code=201)
def checkout(body: CheckoutIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    """Starts a checkout bound to THIS account. The payment only becomes access once the provider confirms it
    (webhook / payment_check) and the commercial service delivers the snapshot."""
    if entitlements.current(db, user.id) is not None:
        raise HTTPException(409, "você já tem uma assinatura ativa: use as opções de gerenciamento")
    try:
        status, data = billing_link.call("/internal/billing/checkout", {
            "account_id": user.id, "email": user.email, "plan": body.plan, "market": body.market, "language": body.language})
    except billing_link.BillingUnavailable as exc:
        raise HTTPException(503, "pagamentos indisponíveis no momento") from exc
    url = str(data.get("url") or "")
    if status != 201 or not _provider_url(url, body.market):
        log.error("checkout refused by the commercial service", extra={"status": status})
        raise HTTPException(503, "pagamentos indisponíveis no momento")
    audit(db, request, user, "billing.checkout_started", f"user:{user.id}", {"plan": body.plan, "market": body.market})
    db.commit()
    return {"url": url}


@router.post("/portal")
def portal(db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    """Stripe's official customer portal (USD). InfinitePay has no portal: renewal is a new Pix/card payment."""
    try:
        status, data = billing_link.call("/internal/billing/portal", {"account_id": user.id})
    except billing_link.BillingUnavailable as exc:
        raise HTTPException(503, "gerenciamento indisponível no momento") from exc
    url = str(data.get("url") or "")
    if status == 404:
        raise HTTPException(404, "nenhuma assinatura em dólar para gerenciar")
    if status != 200 or not url.startswith("https://billing.stripe.com/"):
        raise HTTPException(503, "gerenciamento indisponível no momento")
    return {"url": url}


# ---------------------------------------------------------------------------
# channels owned by the customer
# ---------------------------------------------------------------------------


def _channel_view(db: Session, s: Streamer) -> dict:
    clips = db.scalar(select(func.count()).select_from(Clip).where(Clip.streamer_id == s.id)) or 0
    return {"id": s.id, "platform": s.platform, "channel": s.channel, "url": s.url, "display_name": s.display_name,
            "enabled": s.enabled, "status": s.status, "note": s.last_error, "clips": clips,
            "last_live_at": _iso(s.last_live_at)}


@router.get("/channels")
def channels(db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    rows = db.scalars(select(Streamer).where(Streamer.owner_id == user.id).order_by(Streamer.created_at, Streamer.id))
    return [_channel_view(db, s) for s in rows]


class ChannelIn(BaseModel):
    url: str = Field(max_length=512)
    display_name: str = Field("", max_length=255)


@router.post("/channels", status_code=201)
async def add_channel(body: ChannelIn, request: Request, db: Session = Depends(get_db),
                      user: CurrentUser = Depends(customer_user)):
    raw = body.url.strip()
    if raw.lower().startswith("file:"):
        raise HTTPException(422, "informe o link de um canal da Twitch ou do YouTube")
    try:
        ref = await asyncio.to_thread(parse_stream_url, raw)
        if ref.platform == "youtube" and ref.channel.startswith("v:"):
            try:
                ref = await asyncio.to_thread(canonical_youtube, ref)
            except VideoUnavailable:
                raise
            except InvalidStreamURL:
                pass
    except InvalidStreamURL as exc:
        raise HTTPException(422, str(exc)) from exc
    if ref.platform not in ("twitch", "youtube"):
        raise HTTPException(422, "plataforma ainda não suportada")
    ent = entitlements.current(db, user.id)
    if ent is None:
        raise HTTPException(402, "é preciso uma assinatura ativa para monitorar canais")
    # Serialize per account so two simultaneous requests cannot both take the last free slot
    db.scalar(select(AccountSubscription.id).where(AccountSubscription.id == ent.subscription_id).with_for_update())
    if entitlements.channel_count(db, user.id) >= int(ent.limits["channels"]):
        raise HTTPException(409, f"o plano permite {int(ent.limits['channels'])} canal(is)")
    if db.scalar(select(Streamer.id).where(Streamer.platform == ref.platform, Streamer.channel == ref.channel)) is not None:
        raise HTTPException(409, "este canal já está cadastrado")  # never reveals who monitors it
    s = Streamer(platform=ref.platform, channel=ref.channel, url=ref.url, owner_id=user.id,
                 display_name=body.display_name.strip() or ref.display_name, preferences={}, status="unknown",
                 check_requested=True)
    db.add(s)
    db.flush()
    # The owner sees every clip of the channel (including the ones still in review) in the existing library
    sharing.grant_streamer(db, admin_id=user.id, email=user.email, streamer_id=s.id, can_download=True, include_pending=True)
    audit(db, request, user, "account.channel_add", f"streamer:{s.id}", {"url": ref.url})
    db.commit()
    return _channel_view(db, s)


@router.delete("/channels/{sid}")
def delete_channel(sid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    s = db.get(Streamer, sid)
    if s is None or s.owner_id != user.id:
        raise HTTPException(404, "canal não encontrado")  # same answer for someone else's channel
    session_ids = [x.id for x in s.sessions]
    for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))):
        if (j.payload or {}).get("session_id") in session_ids or (j.payload or {}).get("streamer_id") == sid:
            queue.request_cancel(db, j)
    clip_ids = [c for (c,) in db.execute(select(Clip.id).where(Clip.streamer_id == sid))]
    db.delete(s)
    audit(db, request, user, "account.channel_delete", f"streamer:{sid}", {"clips": len(clip_ids)})
    db.commit()
    for cid in clip_ids:
        storage.delete_clip_files(cid)
    return {"ok": True}


@router.post("/clips/{cid}/export-4k")
def export_4k(cid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    """4K export entitlement check. The renderer has no 4K output yet: the quota is enforced but no export runs
    (and nothing is consumed) until that render path exists."""
    clip = db.get(Clip, cid)
    streamer = db.get(Streamer, clip.streamer_id) if clip else None
    if clip is None or streamer is None or streamer.owner_id != user.id:
        raise HTTPException(404, "corte não encontrado")
    reason = entitlements.export_4k_block_reason(db, user.id)
    if reason:
        raise HTTPException(403 if "não incluída" in reason or "inativa" in reason else 409, reason)
    raise HTTPException(501, "exportação 4K ainda não disponível no renderizador")
