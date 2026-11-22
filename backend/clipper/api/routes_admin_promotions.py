"""HTS promotion codes in the internal panel (/api/admin/promotions). Admin only, CSRF, audited.

The codes are MONEY, so they live in the commercial service (``commercial/server/promotions.js``): this API only
forwards validated administrative requests over the signed billing link and never computes a price. When the
commercial service is not connected (``ASC_BILLING_SECRET`` / ``ASC_COMMERCIAL_URL``), every route answers 503 and the
panel says so: no code is shown as working while payments are off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing_link
from ..db import get_db
from ..models import User
from .deps import CurrentUser, audit, require_admin

router = APIRouter(prefix="/api/admin/promotions", tags=["admin-promotions"])

CODE = r"^[A-Za-z0-9]{3,32}$"
UNAVAILABLE = "serviço comercial não conectado: os cupons só funcionam com os pagamentos ativos"

# Reasons sent by the commercial service, in the panel's language (PT; the dashboard translates)
REASONS = {
    "code": "código inválido: use de 3 a 32 letras e números", "code_taken": "este código já existe",
    "kind": "tipo de desconto inválido", "percent": "percentual entre 1 e 100", "plans": "escolha ao menos um plano",
    "markets": "escolha ao menos um mercado", "amount_br": "valor em reais inválido (maior que zero e até o preço do plano mais barato)",
    "amount_us": "valor em dólares inválido (maior que zero e até o preço do plano mais barato)",
    "free_days": "dias grátis entre 1 e 90", "free_days_new_only": "dias grátis só existem para assinaturas novas",
    "applies_to": "aplicação inválida", "duration": "duração inválida", "duration_months": "meses entre 1 e 24",
    "starts_at": "início inválido", "ends_at": "a expiração precisa ser depois do início e no futuro",
    "max_redemptions": "limite total inválido", "max_per_account": "limite por cliente entre 1 e 100",
    "nothing_to_change": "nada para alterar", "not_found": "cupom não encontrado",
}


def _call(path: str, body: dict) -> tuple[int, dict]:
    try:
        return billing_link.call(path, body)
    except billing_link.BillingUnavailable as exc:
        raise HTTPException(503, UNAVAILABLE) from exc


def _raise_for(status: int, data: dict) -> None:
    if status in (200, 201):
        return
    if status in (404, 422) and data.get("error") == "invalid_promotion":
        raise HTTPException(status, REASONS.get(str(data.get("reason")), "cupom inválido"))
    raise HTTPException(503, UNAVAILABLE)


class PromotionIn(BaseModel):
    code: str = Field(..., pattern=CODE)
    kind: str = Field(..., pattern="^(percent|amount|free_days)$")
    percent: int | None = Field(None, ge=1, le=100)
    amount_brl: int | None = Field(None, gt=0, le=1_000_000)  # centavos
    amount_usd: int | None = Field(None, gt=0, le=1_000_000)  # cents
    free_days: int | None = Field(None, ge=1, le=90)
    # 'new': only at the start of a subscription (first payment); 'existing': recurring, for running subscriptions
    applies_to: str = Field("new", pattern="^(new|existing)$")
    duration: str = Field("once", pattern="^(once|repeating|forever)$")
    duration_months: int | None = Field(None, ge=1, le=24)
    plans: list[str] = Field(..., min_length=1, max_length=3)
    markets: list[str] = Field(..., min_length=1, max_length=2)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: int | None = Field(None, ge=1, le=1_000_000)
    max_per_account: int = Field(1, ge=1, le=100)
    note: str = Field("", max_length=200)

    @field_validator("plans")
    @classmethod
    def _plans(cls, v):
        if not set(v) <= {"basic", "creator", "pro"}:
            raise ValueError("plano desconhecido")
        return sorted(set(v))

    @field_validator("markets")
    @classmethod
    def _markets(cls, v):
        if not set(v) <= {"BR", "US"}:
            raise ValueError("mercado desconhecido")
        return sorted(set(v))


class PromotionPatch(BaseModel):
    active: bool | None = None
    ends_at: datetime | None = None
    clear_ends_at: bool = False
    max_redemptions: int | None = Field(None, ge=1, le=1_000_000)
    clear_max_redemptions: bool = False
    max_per_account: int | None = Field(None, ge=1, le=100)


def _epoch(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _with_emails(db: Session, detail: dict) -> dict:
    """Redemptions show the customer's e-mail (the commercial service only knows account ids)."""
    ids = {int(r["account_id"]) for r in detail.get("redemptions", []) if str(r.get("account_id", "")).isdigit()}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}
    for r in detail.get("redemptions", []):
        r["email"] = emails.get(int(r["account_id"]), "")
    return detail


@router.get("")
def list_promotions(_: CurrentUser = Depends(require_admin)):
    if not billing_link.configured():
        return {"connected": False, "codes": [], "stripe": False, "infinitepay": False}
    status, data = _call("/internal/promotions/list", {})
    _raise_for(status, data)
    return {"connected": True, **data}


@router.post("", status_code=201)
def create_promotion(body: PromotionIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    definition = body.model_dump(exclude={"starts_at", "ends_at"})
    definition["code"] = body.code.upper()
    definition["starts_at"], definition["ends_at"] = _epoch(body.starts_at), _epoch(body.ends_at)
    status, data = _call("/internal/promotions/create", {"definition": definition, "actor": admin.email})
    _raise_for(status, data)
    audit(db, request, admin, "admin.promotion_create", f"promo:{definition['code']}",
          {k: definition[k] for k in ("kind", "percent", "amount_brl", "amount_usd", "free_days", "applies_to", "duration",
                                      "duration_months", "plans", "markets", "max_redemptions", "max_per_account")})
    db.commit()
    return data


@router.get("/{code}")
def promotion(code: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    if not billing_link.configured():
        raise HTTPException(503, UNAVAILABLE)
    status, data = _call("/internal/promotions/details", {"code": code.upper()})
    _raise_for(status, data)
    return _with_emails(db, data)


@router.patch("/{code}")
def update_promotion(code: str, body: PromotionPatch, request: Request, db: Session = Depends(get_db),
                     admin: CurrentUser = Depends(require_admin)):
    changes: dict = {"code": code.upper()}
    if body.active is not None:
        changes["active"] = body.active
    if body.clear_ends_at:
        changes["ends_at"] = None
    elif body.ends_at is not None:
        changes["ends_at"] = _epoch(body.ends_at)
    if body.clear_max_redemptions:
        changes["max_redemptions"] = None
    elif body.max_redemptions is not None:
        changes["max_redemptions"] = body.max_redemptions
    if body.max_per_account is not None:
        changes["max_per_account"] = body.max_per_account
    status, data = _call("/internal/promotions/update", {"changes": changes, "actor": admin.email})
    _raise_for(status, data)
    audit(db, request, admin, "admin.promotion_update", f"promo:{code.upper()}", changes)
    db.commit()
    return data


@router.post("/{code}/sync")
def sync_promotion(code: str, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """Retries the mirroring of a USD code in Stripe after an error."""
    status, data = _call("/internal/promotions/sync", {"code": code.upper(), "actor": admin.email})
    _raise_for(status, data)
    audit(db, request, admin, "admin.promotion_sync", f"promo:{code.upper()}", {"stripe": (data.get("stripe") or {}).get("state")})
    db.commit()
    return data
