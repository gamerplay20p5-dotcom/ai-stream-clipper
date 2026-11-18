"""Stream URL parsing, validation (SSRF protection) and live-status probing.

Only public Twitch / YouTube channel URLs are accepted from users. The resolved media
URLs are produced by streamlink from those trusted hosts; user input never reaches
ffmpeg directly. ``file`` sources (controlled test videos) are admin-only and confined to
``ASC_TEST_MEDIA_DIR``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

_TWITCH_RESERVED = {"directory", "videos", "settings", "p", "downloads", "jobs", "search", "subscriptions",
                    "inventory", "wallet", "drops", "turbo", "friends", "messages", "prime"}
_TWITCH_CHANNEL = re.compile(r"^[A-Za-z0-9_]{3,25}$")
_YT_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,100}$")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


class InvalidStreamURL(ValueError):
    pass


@dataclass(frozen=True)
class StreamRef:
    platform: str  # twitch | youtube | file
    channel: str  # canonical identifier
    url: str  # canonical URL handed to streamlink (or file path)
    display_name: str


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped and not _is_public_ip(str(addr.ipv4_mapped)))
    )


def assert_public_host(host: str, resolve: bool = True) -> None:
    """Reject hosts resolving to private / loopback / link-local addresses (SSRF)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise InvalidStreamURL("IP literals are not allowed")
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise InvalidStreamURL(f"cannot resolve host {host}") from exc
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise InvalidStreamURL(f"host {host} resolves to non-public address")


def parse_stream_url(raw: str, *, resolve_dns: bool = True) -> StreamRef:
    raw = (raw or "").strip()
    if len(raw) > 512:
        raise InvalidStreamURL("URL too long")
    if not raw:
        raise InvalidStreamURL("empty URL")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidStreamURL("only http(s) URLs are accepted")
    if parts.username or parts.password:
        raise InvalidStreamURL("credentials in URL are not allowed")
    if parts.port not in (None, 80, 443):
        raise InvalidStreamURL("custom ports are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    segs = [s for s in parts.path.split("/") if s]

    if host in TWITCH_HOSTS:
        if not segs:
            raise InvalidStreamURL("missing Twitch channel")
        name = segs[0]
        if name.lower() in _TWITCH_RESERVED or not _TWITCH_CHANNEL.match(name):
            raise InvalidStreamURL("invalid Twitch channel name")
        if len(segs) > 1 and segs[1] not in ("", "home", "about"):
            raise InvalidStreamURL("only live channel URLs are supported (not VODs/clips)")
        assert_public_host("www.twitch.tv", resolve_dns)
        ch = name.lower()
        return StreamRef("twitch", ch, f"https://www.twitch.tv/{ch}", name)

    if host in YOUTUBE_HOSTS:
        query = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
        assert_public_host("www.youtube.com", resolve_dns)
        if host == "youtu.be":
            if segs and _YT_ID.match(segs[0]):
                return StreamRef("youtube", f"v:{segs[0]}", f"https://www.youtube.com/watch?v={segs[0]}", segs[0])
            raise InvalidStreamURL("invalid youtu.be link")
        if segs and segs[0] == "watch" and _YT_ID.match(query.get("v", "")):
            vid = query["v"]
            return StreamRef("youtube", f"v:{vid}", f"https://www.youtube.com/watch?v={vid}", vid)
        if len(segs) >= 2 and segs[0] == "live" and _YT_ID.match(segs[1]):
            return StreamRef("youtube", f"v:{segs[1]}", f"https://www.youtube.com/watch?v={segs[1]}", segs[1])
        if segs and _YT_HANDLE.match(segs[0]):
            handle = segs[0]
            return StreamRef("youtube", handle.lower(), f"https://www.youtube.com/{handle}/live", handle)
        if len(segs) >= 2 and segs[0] == "channel" and _YT_CHANNEL_ID.match(segs[1]):
            return StreamRef("youtube", segs[1], f"https://www.youtube.com/channel/{segs[1]}/live", segs[1])
        if len(segs) >= 2 and segs[0] in ("c", "user") and re.match(r"^[A-Za-z0-9._-]{2,100}$", segs[1]):
            return StreamRef("youtube", f"{segs[0]}/{segs[1]}".lower(),
                             f"https://www.youtube.com/{segs[0]}/{segs[1]}/live", segs[1])
        raise InvalidStreamURL("unsupported YouTube URL (use a channel @handle, /channel/ID or watch?v=)")

    raise InvalidStreamURL("only Twitch and YouTube URLs are supported")


def parse_file_source(name: str, media_dir: Path, must_exist: bool = True) -> StreamRef:
    """Admin-only controlled test video, confined to ``media_dir`` (no traversal).
    ``must_exist=False`` when the media lives on another host (the capture worker checks it)."""
    name = (name or "").strip()
    if name.startswith("file://"):
        name = name[len("file://"):]
    if not re.match(r"^[A-Za-z0-9._ -]{1,200}$", name) or ".." in name:
        raise InvalidStreamURL("invalid test media name (plain file name inside the test media directory)")
    if not must_exist:
        return StreamRef("file", name, f"file://{name}", Path(name).stem)
    root = media_dir.resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise InvalidStreamURL("test media file not found")
    return StreamRef("file", name, f"file://{name}", Path(name).stem)


# ---------------------------------------------------------------------------
# YouTube: a watch?v= link is ONE broadcast; the channel is what must be monitored
# ---------------------------------------------------------------------------

_YT_WATCH = "https://www.youtube.com/watch?v={vid}"  # fixed host: no user-controlled destination
_YT_CHANNEL_IN_PAGE = (re.compile(r'"channelId":"(UC[A-Za-z0-9_-]{22})"'),
                       re.compile(r'itemprop="channelId" content="(UC[A-Za-z0-9_-]{22})"'))
_YT_AUTHOR_IN_PAGE = re.compile(r'"ownerChannelName":"([^"]{1,120})"')
YT_REMOVED = ("removed by the uploader", "video unavailable", "this video is private", "has been removed",
              "account associated with this video has been terminated", "no longer available")


class VideoUnavailable(InvalidStreamURL):
    pass


def channel_ref(channel_id: str, name: str = "") -> StreamRef:
    if not _YT_CHANNEL_ID.match(channel_id):
        raise InvalidStreamURL("invalid YouTube channel id")
    return StreamRef("youtube", channel_id, f"https://www.youtube.com/channel/{channel_id}/live", name or channel_id)


def resolve_youtube_channel(video_id: str, timeout: float = 15.0) -> tuple[str, str]:
    """Return ``(channel_id, channel_name)`` of the channel that owns ``video_id``.
    Raises :class:`VideoUnavailable` when YouTube says the video no longer exists and
    ``InvalidStreamURL`` when the page could not be read (network, consent wall...)."""
    import httpx

    if not _YT_ID.match(video_id):
        raise InvalidStreamURL("invalid YouTube video id")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
               "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8", "Cookie": "CONSENT=YES+1"}
    try:
        r = httpx.get(_YT_WATCH.format(vid=video_id), headers=headers, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise InvalidStreamURL(f"could not read the YouTube page: {type(exc).__name__}") from exc
    text = r.text if r.status_code == 200 else ""
    for rx in _YT_CHANNEL_IN_PAGE:
        m = rx.search(text)
        if m:
            a = _YT_AUTHOR_IN_PAGE.search(text)
            return m.group(1), (a.group(1) if a else "")
    low = text.lower()
    if r.status_code == 200 and any(k in low for k in YT_REMOVED) or '"status":"error"' in low:
        raise VideoUnavailable("video unavailable")
    raise InvalidStreamURL(f"could not find the channel of this video (HTTP {r.status_code})")


def canonical_youtube(ref: StreamRef) -> StreamRef:
    """Turn a single-broadcast YouTube reference (``v:<id>``) into the owner's channel."""
    if ref.platform != "youtube" or not ref.channel.startswith("v:"):
        return ref
    try:
        cid, name = resolve_youtube_channel(ref.channel[2:])
    except VideoUnavailable as exc:
        raise VideoUnavailable(
            "este vídeo do YouTube não existe mais (removido ou privado). Links watch?v= apontam para uma "
            "transmissão só; cadastre o canal: youtube.com/@handle ou youtube.com/channel/ID") from exc
    return channel_ref(cid, name)


# ---------------------------------------------------------------------------
# Live status probing (streamlink)
# ---------------------------------------------------------------------------


@dataclass
class LiveStatus:
    live: bool
    title: str = ""
    error: str = ""
    note: str = ""  # informational (e.g. scheduled broadcast), not an error
    channel_id: str = ""  # YouTube: owner channel of a v:<id> target, when it could be resolved


class _ThreadLog(logging.Handler):
    """Collect streamlink plugin messages emitted by the current thread only (probes run in parallel)."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.tid = threading.get_ident()
        self.messages: list[str] = []

    def emit(self, record):
        if record.thread == self.tid:
            self.messages.append(record.getMessage())


def explain_youtube(messages: list[str]) -> tuple[str, str]:
    """Map streamlink's YouTube messages to (error, note) for the panel."""
    for m in messages:
        low = m.lower()
        if any(k in low for k in YT_REMOVED):
            return ("O vídeo deste link foi removido ou ficou privado. Links watch?v= apontam para uma transmissão "
                    "só; cadastre o canal (youtube.com/@handle ou /channel/ID)."), ""
        if "live_stream_offline" in low or "will begin" in low or "premieres" in low:
            when = m.split(":", 2)[-1].strip()
            return "", f"Transmissão agendada no YouTube ({when[:160]})"
        if "sign in to confirm" in low or "not a bot" in low:
            return "O YouTube pediu verificação anti-robô para este servidor (bloqueio de IP).", ""
    return "", ""


def probe_live(url: str, timeout: float = 20.0) -> LiveStatus:
    """Blocking: ask streamlink whether a public channel URL currently has streams."""
    try:
        from streamlink import Streamlink
        from streamlink.exceptions import NoPluginError, PluginError
    except ImportError:  # pragma: no cover
        return LiveStatus(False, error="streamlink not installed")
    session = Streamlink()
    session.set_option("http-timeout", timeout)
    catcher = _ThreadLog()
    slog = logging.getLogger("streamlink")
    slog.addHandler(catcher)
    try:
        st = _probe(session, url, PluginError, NoPluginError)
    finally:
        slog.removeHandler(catcher)
    if "youtube.com" in url and not st.live:
        err, note = explain_youtube(catcher.messages)
        st.error = st.error or err
        st.note = note
        vid = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
        if vid and not err:  # single-broadcast link that still exists: find its channel for migration
            try:
                st.channel_id = resolve_youtube_channel(vid.group(1))[0]
            except InvalidStreamURL:
                pass
    return st


def _probe(session, url: str, PluginError, NoPluginError) -> LiveStatus:
    try:
        _, plugin_cls, resolved = session.resolve_url(url)
        plugin = plugin_cls(session, resolved)
        streams = plugin.streams()
        title = ""
        try:
            title = plugin.get_title() or ""
        except Exception:  # metadata is best-effort
            pass
        return LiveStatus(bool(streams), title=title[:500])
    except NoPluginError:
        return LiveStatus(False, error="no streamlink plugin for URL")
    except PluginError as exc:
        msg = str(exc)
        # offline channels commonly surface as PluginError on YouTube
        return LiveStatus(False, error="" if "offline" in msg.lower() or "no live" in msg.lower() else msg[:300])
    except Exception as exc:  # network etc.
        return LiveStatus(False, error=f"{type(exc).__name__}: {exc}"[:300])
