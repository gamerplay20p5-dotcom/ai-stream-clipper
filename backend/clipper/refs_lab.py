"""References lab (Phase 6C): channels/profiles/videos the owner studies, organised in collections.

Only legitimate sources: official APIs with the scopes we already hold, plus what the owner types
or uploads. No scraping, no downloads of third-party media, no endpoint that bypasses a platform
restriction. Capabilities were MEASURED on 2026-11-19 with the production credentials
(docs/FASE6.md, "Matriz de capacidades").
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy.orm import Session

from .db import utcnow
from .models import PublishAccount, RefItem

log = logging.getLogger(__name__)

API_TTL = timedelta(days=30)  # YouTube API Services policy: refresh or delete stored API data within 30 days
YT_API = "https://www.googleapis.com/youtube/v3"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 4096

# measured with our tokens (no new scope requested); "how" = what the lab does about it
CAPABILITIES = [
    {"platform": "youtube", "what": "Canal por @handle ou ID: título, descrição, inscritos (se públicos), vídeos, views",
     "status": "comprovado", "how": "channels.list (1 unidade de cota)", "source": "YouTube Data API v3 + youtube.readonly"},
    {"platform": "youtube", "what": "Vídeo ou Short por link: título, duração, views, likes, comentários, data",
     "status": "comprovado", "how": "videos.list (1 unidade)", "source": "YouTube Data API v3"},
    {"platform": "youtube", "what": "Últimos uploads de um canal", "status": "comprovado",
     "how": "playlistItems.list da playlist de uploads (1 unidade)", "source": "YouTube Data API v3"},
    {"platform": "youtube", "what": "Saber se um vídeo é Short", "status": "parcial",
     "how": "a API não tem esse campo: usamos link /shorts/ ou duração <= 3 min, marcado como 'provável'",
     "source": "inferência declarada"},
    {"platform": "youtube", "what": "Busca por termo", "status": "disponível, não usado",
     "how": "search.list custa 100 unidades: o laboratório não usa (cota de 10.000/dia é das publicações)",
     "source": "YouTube Data API v3"},
    {"platform": "youtube", "what": "Retenção e visualizações engajadas (nossos vídeos)", "status": "indisponível",
     "how": "exige ativar a YouTube Analytics API no projeto do Google e o escopo yt-analytics.readonly "
            "(reconectar a conta). Não solicitado.", "source": "erro 403 accessNotConfigured"},
    {"platform": "youtube", "what": "Retenção de vídeos de terceiros", "status": "indisponível",
     "how": "nenhuma API fornece; nunca estimamos curvas de terceiros", "source": "política"},
    {"platform": "instagram", "what": "Perfil de outra conta profissional (Business Discovery)", "status": "indisponível",
     "how": "a Meta respondeu #10 (sem permissão) para o login unificado atual; exigiria Facebook Login com "
            "instagram_basic + página vinculada. Alternativa: cadastro manual com link, prints e observações.",
     "source": "erro OAuthException #10"},
    {"platform": "instagram", "what": "Reel de terceiros por link (oEmbed)", "status": "indisponível",
     "how": "'Meta oEmbed Read' exige revisão do app pela Meta. Alternativa: cadastro manual com print.",
     "source": "erro OAuthException #10"},
    {"platform": "instagram", "what": "Mídias da nossa conta", "status": "comprovado", "how": "/{ig-user}/media",
     "source": "login unificado atual"},
    {"platform": "instagram", "what": "Insights da nossa conta", "status": "indisponível",
     "how": "#10 sem permissão na configuração atual do login unificado; exigiria incluir insights na configuração",
     "source": "erro OAuthException #10"},
    {"platform": "todas", "what": "Baixar vídeos de terceiros para análise", "status": "proibido",
     "how": "nunca. Análise visual só de prints enviados por você e de vídeos nossos.", "source": "política do projeto"},
]


# ------------------------------------------------------------------ links
class BadLink(ValueError):
    pass


def parse_link(url: str) -> dict:
    """{platform, kind, external_id, handle} from a YouTube/Instagram URL (no network)."""
    u = (url or "").strip()
    if not re.match(r"^https?://", u):
        u = "https://" + u
    p = urlsplit(u)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [x for x in p.path.split("/") if x]
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        if host == "youtu.be" and parts:
            return {"platform": "youtube", "kind": "video", "external_id": parts[0][:32], "handle": ""}
        if parts and parts[0] == "shorts" and len(parts) > 1:
            return {"platform": "youtube", "kind": "short", "external_id": parts[1][:32], "handle": ""}
        if parts and parts[0] == "watch":
            v = (parse_qs(p.query).get("v") or [""])[0]
            if v:
                return {"platform": "youtube", "kind": "video", "external_id": v[:32], "handle": ""}
        if parts and parts[0].startswith("@"):
            return {"platform": "youtube", "kind": "channel", "external_id": "", "handle": parts[0][:100]}
        if len(parts) > 1 and parts[0] == "channel" and parts[1].startswith("UC"):
            return {"platform": "youtube", "kind": "channel", "external_id": parts[1][:40], "handle": ""}
        raise BadLink("link do YouTube não reconhecido (use @canal, /channel/UC..., watch?v=, youtu.be ou /shorts/)")
    if host in ("instagram.com", "instagr.am"):
        if len(parts) > 1 and parts[0] in ("reel", "reels", "p", "tv"):
            return {"platform": "instagram", "kind": "reel" if parts[0].startswith("reel") else "post",
                    "external_id": parts[1][:64], "handle": ""}
        if parts and re.match(r"^[A-Za-z0-9._]{1,30}$", parts[0]):
            return {"platform": "instagram", "kind": "profile", "external_id": "", "handle": "@" + parts[0]}
        raise BadLink("link do Instagram não reconhecido (use instagram.com/perfil ou /reel/...)")
    raise BadLink("só links do YouTube ou do Instagram; para outras fontes, envie um print")


# ------------------------------------------------------------------ YouTube metadata (authorized API)
def _yt_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))


def _iso_duration(s: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _add_units(acct: PublishAccount, units: int) -> None:
    """Same bucket accounting as the publisher (so the lab never eats the upload quota unseen)."""
    from .publish.runner import _add_quota

    _add_quota(acct, units=units)


def fetch_youtube(db: Session, item: RefItem) -> dict:
    """Public metadata through the account we already connected (youtube.readonly). Uses only
    1-unit calls (channels.list / videos.list), never search.list."""
    from .publish import service

    acct = db.query(PublishAccount).filter(PublishAccount.platform == "youtube",
                                           PublishAccount.status == "connected").first()
    if acct is None:
        raise BadLink("nenhuma conta do YouTube conectada: os metadados não podem ser consultados")
    tok = service.tokens_for(db, acct)
    h = {"Authorization": f"Bearer {tok.access_token}"}
    with _yt_client() as c:
        if item.kind == "channel":
            q = {"part": "snippet,statistics,contentDetails"}
            q.update({"forHandle": item.handle} if item.handle and not item.external_id else {"id": item.external_id})
            r = c.get(f"{YT_API}/channels", params=q, headers=h)
            _add_units(acct, 1)
            r.raise_for_status()
            it = ((r.json() or {}).get("items") or [None])[0]
            if not it:
                raise BadLink("canal não encontrado na API do YouTube")
            st, sn = it.get("statistics") or {}, it.get("snippet") or {}
            data = {"title": sn.get("title", ""), "description": (sn.get("description") or "")[:600],
                    "custom_url": sn.get("customUrl", ""), "country": sn.get("country", ""),
                    "published_at": sn.get("publishedAt", ""),
                    "subscribers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
                    "views": _int(st.get("viewCount")), "videos": _int(st.get("videoCount"))}
            item.external_id = it.get("id", item.external_id)
            item.title = item.title or data["title"]
        else:
            r = c.get(f"{YT_API}/videos", params={"part": "snippet,contentDetails,statistics", "id": item.external_id},
                      headers=h)
            _add_units(acct, 1)
            r.raise_for_status()
            it = ((r.json() or {}).get("items") or [None])[0]
            if not it:
                raise BadLink("vídeo não encontrado (removido, privado ou link errado)")
            sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
            secs = _iso_duration(cd.get("duration", ""))
            data = {"title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                    "channel_id": sn.get("channelId", ""), "published_at": sn.get("publishedAt", ""),
                    "duration_s": secs, "views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                    "comments": _int(st.get("commentCount")),
                    "short": "provável" if (item.kind == "short" or secs <= 180) else "não",
                    "short_rule": "link /shorts/ ou duração <= 3 min (a API não informa)"}
            item.title = item.title or data["title"]
    now = utcnow()
    item.api_data, item.api_source = data, "youtube-data-api-v3"
    item.api_fetched_at, item.api_expires_at = now, now + API_TTL
    return data


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def expire_api_data(db: Session) -> int:
    """Policy compliance: API-derived data older than 30 days is cleared (the owner's notes, tags,
    images and collections are never touched)."""
    n = 0
    for it in db.query(RefItem).filter(RefItem.api_expires_at.isnot(None), RefItem.api_expires_at < utcnow()):
        it.api_data, it.api_fetched_at, it.api_expires_at = {}, None, None
        n += 1
    return n


# ------------------------------------------------------------------ images (owner uploads)
def store_image(data: bytes, dest_dir, stem: str) -> str:
    """Validate and RE-ENCODE an uploaded image (drops metadata, refuses non-images/huge files).
    Returns the storage-relative path."""
    import cv2
    import numpy as np

    from . import storage

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise BadLink(f"imagem vazia ou maior que {MAX_IMAGE_BYTES // 1024 // 1024} MB")
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim < 2 or min(img.shape[:2]) < 32:
        raise BadLink("arquivo não é uma imagem válida (PNG, JPG ou WebP)")
    if max(img.shape[:2]) > MAX_IMAGE_SIDE:
        raise BadLink(f"imagem maior que {MAX_IMAGE_SIDE}px")
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = ".png" if (img.ndim == 3 and img.shape[2] == 4) else ".jpg"
    path = dest_dir / f"{stem}{ext}"
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, 92] if ext == ".jpg" else [])
    if not ok:
        raise BadLink("não foi possível processar a imagem")
    path.write_bytes(buf.tobytes())
    return storage.rel(path)
