"""Viral Intelligence - references lab (Phase 6C): references, collections, format profiles."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import refs_lab, storage
from ..db import get_db
from ..format_lab import FormatParams, budget_ok, clean_params, enqueue_analysis, plan_format_variant
from ..models import Clip, FormatProfile, RefCollection, RefCollectionItem, RefItem
from ..settings_store import load_ai_settings
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/refs", tags=["references"])
Tag = Field(default_factory=list, max_length=20)


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _item(db: Session, it: RefItem) -> dict:
    cols = [c for (c,) in db.execute(select(RefCollectionItem.collection_id).where(RefCollectionItem.item_id == it.id))]
    return {"id": it.id, "platform": it.platform, "kind": it.kind, "url": it.url, "external_id": it.external_id,
            "handle": it.handle, "title": it.title, "category": it.category, "games": it.games, "humor": it.humor,
            "formats": it.formats, "tags": it.tags, "why": it.why, "notes": it.notes, "favorite": it.favorite,
            "has_image": bool(it.image_path), "api_data": it.api_data or {}, "api_source": it.api_source,
            "api_fetched_at": _iso(it.api_fetched_at), "api_expires_at": _iso(it.api_expires_at),
            "collections": cols, "created_at": _iso(it.created_at)}


def _profile(p: FormatProfile) -> dict:
    return {"id": p.id, "name": p.name, "item_id": p.item_id, "status": p.status, "error": p.error,
            "favorite": p.favorite, "params": p.params or {}, "has_image": bool(p.image_path),
            "has_avatar": bool(p.avatar_path), "analysis": {k: (p.analysis or {}).get(k) for k in
                                                            ("vlm", "vlm_error", "version")},
            "measured": {k: ((p.analysis or {}).get("measured") or {}).get(k) for k in
                         ("aspect", "vertical", "split", "faces", "caption", "palette", "contrast", "badge")},
            "updated_at": _iso(p.updated_at)}


async def _upload(request: Request) -> bytes:
    size = int(request.headers.get("content-length") or 0)
    if size > refs_lab.MAX_IMAGE_BYTES:
        raise HTTPException(413, "imagem maior que 8 MB")
    data = await request.body()
    if len(data) > refs_lab.MAX_IMAGE_BYTES:
        raise HTTPException(413, "imagem maior que 8 MB")
    return data


# ------------------------------------------------------------------ capabilities
@router.get("/capabilities")
def capabilities(_: CurrentUser = Depends(current_user)):
    return {"measured_at": "2026-11-19", "items": refs_lab.CAPABILITIES}


# ------------------------------------------------------------------ references
class ItemIn(BaseModel):
    url: str = Field("", max_length=1024)
    title: str = Field("", max_length=300)
    category: str = Field("", max_length=64)
    games: list[str] = Tag
    humor: list[str] = Tag
    formats: list[str] = Tag
    tags: list[str] = Tag
    why: str = Field("", max_length=3000)
    notes: str = Field("", max_length=5000)
    favorite: bool = False


class ItemPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    category: str | None = Field(None, max_length=64)
    games: list[str] | None = Field(None, max_length=20)
    humor: list[str] | None = Field(None, max_length=20)
    formats: list[str] | None = Field(None, max_length=20)
    tags: list[str] | None = Field(None, max_length=20)
    why: str | None = Field(None, max_length=3000)
    notes: str | None = Field(None, max_length=5000)
    favorite: bool | None = None


def _get_item(db: Session, iid: int) -> RefItem:
    it = db.get(RefItem, iid)
    if it is None:
        raise HTTPException(404, "referência não encontrada")
    return it


@router.get("/items")
def list_items(collection_id: int | None = None, platform: str | None = None, category: str | None = None,
               q: str | None = None, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    stmt = select(RefItem).order_by(RefItem.favorite.desc(), RefItem.id.desc())
    if collection_id:
        stmt = stmt.join(RefCollectionItem, RefCollectionItem.item_id == RefItem.id).where(
            RefCollectionItem.collection_id == collection_id)
    if platform:
        stmt = stmt.where(RefItem.platform == platform)
    if category:
        stmt = stmt.where(RefItem.category == category)
    rows = db.scalars(stmt.limit(500)).all()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join([r.title, r.handle, r.why, r.notes, " ".join(r.tags or []),
                                                   " ".join(r.games or [])]).lower()]
    return [_item(db, r) for r in rows]


def _clean_tags(v: list[str]) -> list[str]:
    return list(dict.fromkeys(t.strip()[:40] for t in v if t and t.strip()))[:20]


@router.post("/items")
def create_item(body: ItemIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    link = {"platform": "other", "kind": "image", "external_id": "", "handle": ""}
    if body.url.strip():
        try:
            link = refs_lab.parse_link(body.url)
        except refs_lab.BadLink as exc:
            raise HTTPException(422, str(exc)) from exc
    elif not body.title.strip():
        raise HTTPException(422, "informe um link ou um título (para referência por imagem)")
    it = RefItem(**link, url=body.url.strip(), title=body.title.strip(), category=body.category.strip(),
                 games=_clean_tags(body.games), humor=_clean_tags(body.humor), formats=_clean_tags(body.formats),
                 tags=_clean_tags(body.tags), why=body.why, notes=body.notes, favorite=body.favorite,
                 created_by=user.email, api_data={})
    db.add(it)
    db.flush()
    audit(db, request, user, "refs.create", f"ref:{it.id}", {"platform": it.platform, "kind": it.kind})
    db.commit()
    return _item(db, it)


@router.patch("/items/{iid}")
def patch_item(iid: int, body: ItemPatch, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    it = _get_item(db, iid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(it, k, _clean_tags(v) if isinstance(v, list) else v)
    db.commit()
    return _item(db, it)


@router.delete("/items/{iid}")
def delete_item(iid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    it = _get_item(db, iid)
    path = it.image_path
    db.delete(it)
    audit(db, request, user, "refs.delete", f"ref:{iid}")
    db.commit()
    if path:
        try:
            storage.resolve(path).unlink(missing_ok=True)
        except ValueError:
            pass
    return {"ok": True}


@router.post("/items/{iid}/refresh")
def refresh_item(iid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Public metadata through the AUTHORIZED API only (YouTube). Instagram: unavailable (see capabilities)."""
    it = _get_item(db, iid)
    if it.platform != "youtube":
        raise HTTPException(422, "metadados automáticos só para o YouTube: o Instagram não permite consultar outras "
                                 "contas com a autorização atual (Business Discovery #10). Use o cadastro manual.")
    try:
        data = refs_lab.fetch_youtube(db, it)
    except refs_lab.BadLink as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # network / quota: no partial write
        db.rollback()
        raise HTTPException(502, f"falha ao consultar a API do YouTube: {type(exc).__name__}") from exc
    audit(db, request, user, "refs.refresh", f"ref:{iid}", {"source": it.api_source})
    db.commit()
    return {**_item(db, it), "fetched": data}


@router.post("/items/{iid}/image")
async def item_image(iid: int, request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    it = _get_item(db, iid)
    data = await _upload(request)
    old = it.image_path
    try:
        it.image_path = refs_lab.store_image(data, storage.root() / "refs" / "items", f"{iid}_{uuid.uuid4().hex[:8]}")
    except refs_lab.BadLink as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    if old:
        storage.resolve(old).unlink(missing_ok=True)
    return _item(db, it)


@router.get("/items/{iid}/image")
def get_item_image(iid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    it = _get_item(db, iid)
    if not it.image_path:
        raise HTTPException(404, "sem imagem")
    return FileResponse(storage.resolve(it.image_path), headers={"Cache-Control": "private, max-age=300"})


# ------------------------------------------------------------------ collections
class CollectionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=1000)


@router.get("/collections")
def list_collections(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    out = []
    for c in db.scalars(select(RefCollection).order_by(RefCollection.name)):
        n = db.query(RefCollectionItem).filter(RefCollectionItem.collection_id == c.id).count()
        out.append({"id": c.id, "name": c.name, "description": c.description, "items": n})
    return out


@router.post("/collections")
def create_collection(body: CollectionIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    c = RefCollection(name=body.name.strip(), description=body.description)
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name, "description": c.description, "items": 0}


@router.delete("/collections/{cid}")
def delete_collection(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    c = db.get(RefCollection, cid)
    if c is None:
        raise HTTPException(404, "coleção não encontrada")
    db.query(RefCollectionItem).filter(RefCollectionItem.collection_id == cid).delete()
    db.delete(c)  # the references themselves stay
    db.commit()
    return {"ok": True}


class MemberIn(BaseModel):
    item_id: int


@router.post("/collections/{cid}/items")
def add_member(cid: int, body: MemberIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    if db.get(RefCollection, cid) is None:
        raise HTTPException(404, "coleção não encontrada")
    _get_item(db, body.item_id)
    if db.get(RefCollectionItem, (cid, body.item_id)) is None:
        db.add(RefCollectionItem(collection_id=cid, item_id=body.item_id))
    db.commit()
    return {"ok": True}


@router.delete("/collections/{cid}/items/{iid}")
def remove_member(cid: int, iid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    row = db.get(RefCollectionItem, (cid, iid))
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ format profiles
class ProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    item_id: int | None = None
    params: dict | None = None


class ProfilePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    params: dict | None = None
    favorite: bool | None = None


def _get_profile(db: Session, pid: int) -> FormatProfile:
    p = db.get(FormatProfile, pid)
    if p is None:
        raise HTTPException(404, "perfil de formato não encontrado")
    return p


def _validated(params: dict) -> dict:
    try:
        FormatParams.model_validate({k: params.get(k) for k in ("layout", "webcam", "captions", "badge")
                                     if params.get(k) is not None})
    except Exception as exc:
        raise HTTPException(422, f"parâmetros inválidos: {str(exc)[:300]}") from exc
    return clean_params(params)


@router.get("/formats")
def list_formats(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return [_profile(p) for p in db.scalars(select(FormatProfile).order_by(FormatProfile.favorite.desc(),
                                                                          FormatProfile.id.desc()))]


@router.post("/formats")
def create_format(body: ProfileIn, request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_admin)):
    if body.item_id is not None:
        _get_item(db, body.item_id)
    p = FormatProfile(name=body.name.strip(), item_id=body.item_id, created_by=user.email,
                      params=_validated(body.params) if body.params else clean_params({}),
                      status="ready" if body.params else "draft", analysis={})
    db.add(p)
    db.flush()
    audit(db, request, user, "refs.format_create", f"format:{p.id}")
    db.commit()
    return _profile(p)


@router.put("/formats/{pid}")
def update_format(pid: int, body: ProfilePatch, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    p = _get_profile(db, pid)
    if body.name is not None:
        p.name = body.name.strip()
    if body.favorite is not None:
        p.favorite = body.favorite
    if body.params is not None:
        keep = {k: (p.params or {}).get(k) for k in ("palette", "contrast", "sources", "confidence", "notes", "version")
                if (p.params or {}).get(k) is not None}
        p.params = {**keep, **_validated(body.params)}
        src = dict(p.params.get("sources") or {})
        src["edited"] = "manual"
        p.params["sources"] = src
        p.status = "ready"
    db.commit()
    return _profile(p)


@router.delete("/formats/{pid}")
def delete_format(pid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    p = _get_profile(db, pid)
    files = [p.image_path, p.avatar_path]
    db.delete(p)
    db.commit()
    for f in files:
        if f:
            storage.resolve(f).unlink(missing_ok=True)
    return {"ok": True}


@router.post("/formats/{pid}/image")
async def format_image(pid: int, request: Request, db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_admin)):
    """Upload a print and queue its analysis (vision workers, low priority, hourly budget)."""
    p = _get_profile(db, pid)
    data = await _upload(request)
    try:
        path = refs_lab.store_image(data, storage.root() / "refs" / "formats", f"{pid}_{uuid.uuid4().hex[:8]}")
    except refs_lab.BadLink as exc:
        raise HTTPException(422, str(exc)) from exc
    old, p.image_path = p.image_path, path
    queued = _queue(db, p)
    audit(db, request, user, "refs.format_image", f"format:{pid}", {"queued": queued})
    db.commit()
    if old:
        storage.resolve(old).unlink(missing_ok=True)
    return {**_profile(p), "queued": queued}


def _queue(db: Session, p: FormatProfile) -> bool:
    s = load_ai_settings(db)
    if not budget_ok(db, s.refs_analyses_per_hour):
        p.status, p.error = "draft", "limite de análises por hora atingido: tente de novo mais tarde"
        return False
    enqueue_analysis(db, p, s)
    return True


@router.post("/formats/{pid}/analyze")
def reanalyze_format(pid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    p = _get_profile(db, pid)
    if not p.image_path:
        raise HTTPException(422, "envie um print antes")
    queued = _queue(db, p)
    db.commit()
    if not queued:
        raise HTTPException(429, p.error)
    return _profile(p)


@router.get("/formats/{pid}/image")
def get_format_image(pid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    p = _get_profile(db, pid)
    if not p.image_path:
        raise HTTPException(404, "sem imagem")
    return FileResponse(storage.resolve(p.image_path), headers={"Cache-Control": "private, max-age=300"})


@router.post("/formats/{pid}/avatar")
async def format_avatar(pid: int, request: Request, db: Session = Depends(get_db),
                        _: CurrentUser = Depends(require_admin)):
    """OUR profile picture for the identification badge (never a third party's)."""
    p = _get_profile(db, pid)
    data = await _upload(request)
    try:
        path = refs_lab.store_image(data, storage.root() / "refs" / "avatars", f"{pid}_{uuid.uuid4().hex[:8]}")
    except refs_lab.BadLink as exc:
        raise HTTPException(422, str(exc)) from exc
    old, p.avatar_path = p.avatar_path, path
    db.commit()
    if old:
        storage.resolve(old).unlink(missing_ok=True)
    return _profile(p)


@router.get("/formats/{pid}/avatar")
def get_format_avatar(pid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    p = _get_profile(db, pid)
    if not p.avatar_path:
        raise HTTPException(404, "sem avatar")
    return FileResponse(storage.resolve(p.avatar_path), headers={"Cache-Control": "private, max-age=300"})


class PreviewIn(BaseModel):
    clip_id: int


@router.post("/formats/{pid}/preview")
def preview_format(pid: int, body: PreviewIn, request: Request, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_admin)):
    """Render one of OUR clips with this profile as a 'format' variant (shadow: main video unchanged)."""
    p = _get_profile(db, pid)
    clip = db.get(Clip, body.clip_id)
    if clip is None:
        raise HTTPException(404, "corte não encontrado")
    try:
        rec = plan_format_variant(db, clip, p)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, request, user, "refs.format_preview", f"clip:{clip.id}", {"format": pid})
    db.commit()
    return {"ok": True, "clip_id": clip.id, "variant": "format", "label": rec["label"],
            "badge": rec["format_params"]["badge"]["enabled"]}
