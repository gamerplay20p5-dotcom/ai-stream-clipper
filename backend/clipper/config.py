"""Process configuration (environment variables, prefix ``ASC_``).

Runtime-tunable AI/clip settings live in the database (see ``clipper.settings_store``);
this module only holds deployment-level configuration that must be known at boot.
"""

from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASC_", env_file=None, extra="ignore")

    # --- general -------------------------------------------------------
    env: str = "development"  # development | production
    log_level: str = "INFO"
    log_json: bool = True

    # --- API -------------------------------------------------------------
    database_url: str = "sqlite:///./data/clipper.db"
    storage_dir: Path = Path("./data/storage")  # rendered clips, sources, thumbnails
    storage_max_gb: float = 200.0
    jwt_secret: str = Field(default="", repr=False)
    jwt_ttl_minutes: int = 720
    cookie_secure: bool = False
    admin_email: str = ""
    admin_password: str = Field(default="", repr=False)
    dashboard_dir: Path | None = None  # built SPA to serve (optional)
    maintenance_interval_s: float = 5.0
    metrics_token: str = Field(default="", repr=False)
    # Future OIDC (Authentik). Disabled unless issuer is set.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = Field(default="", repr=False)

    # --- internal (API <-> workers <-> segment servers) -----------------
    internal_secret: str = Field(default="", repr=False)
    api_url: str = "http://127.0.0.1:8780"
    signature_max_skew_s: int = 60
    # --- billing link (API <-> commercial service) -------------------------
    # Own secret, never the workers' internal_secret. The commercial service holds the same value.
    billing_secret: str = Field(default="", repr=False)
    commercial_url: str = ""  # internal address of the commercial service, e.g. http://asc-commercial:8790
    # Canonical public address of the dashboard, used in every e-mailed link (invite, reset, sign-up).
    # Never derived from Host / Origin / X-Forwarded-Host. Required (https) in production.
    public_origin: str = ""
    # TLS for the API (dashboard logins and worker traffic are never sent in clear text)
    tls_cert: Path | None = None  # server certificate (PEM) - enables HTTPS on `clipper api`
    tls_key: Path | None = None
    api_ca_file: Path | None = None  # CA used by workers to verify the API certificate

    # --- worker ----------------------------------------------------------
    worker_id: str = ""
    worker_roles: Annotated[list[str], NoDecode] = Field(default_factory=list)
    worker_host_label: str = ""  # e.g. "210" - shown in the infrastructure page
    work_dir: Path = Path("./data/work")
    heartbeat_interval_s: float = 10.0
    # --- live monitor (v0.6.2-hotfix.1) ---
    monitor_interval_s: float = 60.0  # how often each enabled channel is checked
    monitor_concurrency: int = 4  # probes running at the same time
    monitor_probe_timeout_s: float = 45.0  # hard deadline: the probe process is killed after it
    monitor_http_timeout_s: float = 20.0  # streamlink per-request timeout inside the probe
    monitor_stall_s: float = 600.0  # poll loop without any attempt for this long -> supervisor restarts it
    monitor_stale_after_s: float = 300.0  # panel: a channel not checked for this long is "monitoramento atrasado"
    loop_stall_exit_s: float = 300.0  # event loop blocked this long -> stack dump + exit 70 (container restarts); 0 = off
    poll_interval_s: float = 2.0
    lease_seconds: int = 90
    concurrency_audio: int = 2
    concurrency_render: int = 1
    concurrency_vision: int = 1
    concurrency_scoring: int = 2
    gpu_device: str = ""  # e.g. /dev/dri/renderD128 - presence enables gpu capability probing
    ffmpeg_threads: int = 4

    # --- capture worker --------------------------------------------------
    capture_dir: Path = Path("./data/capture")
    max_concurrent_captures: int = 2
    segment_seconds: int = 10
    buffer_seconds: int = 1800
    max_session_hours: float = 12.0
    max_capture_disk_gb: float = 60.0
    capture_quality: str = "1080p60,1080p,936p60,720p60,720p,best"
    capture_reconnect_attempts: int = 6
    segment_server_bind: str = "0.0.0.0"
    segment_server_port: int = 8781
    segment_server_public_url: str = ""  # how other workers reach this capture worker
    allow_file_sources: bool = False  # controlled test videos (admin only)
    test_media_dir: Path = Path("./data/test-media")
    file_source_realtime: bool = True  # read test files at native speed (-re)

    # --- audio worker ----------------------------------------------------
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 4
    whisper_model_dir: Path | None = None

    # --- vision / AI worker ---------------------------------------------
    vision_provider: str = "heuristic"  # heuristic | openvino_vlm (default when the job does not say)
    openvino_model_dir: Path | None = None  # legacy single-model path (Phase 1)
    openvino_device: str = "GPU"  # preferred device; CPU is used as fallback
    models_dir: Path = Path("./data/models")
    ai_allow_cpu_fallback: bool = True
    # models this worker is allowed to serve (comma list); empty = every installed model
    ai_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    yamnet_enabled: bool = True
    # Encoder on this render worker. Set a hardware backend only after a local smoke render.
    render_encoder: Literal["libx264", "h264_qsv", "h264_nvenc"] = "libx264"

    @field_validator("ai_models", mode="before")
    @classmethod
    def _split_models(cls, v):
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v

    @field_validator("public_origin", mode="before")
    @classmethod
    def _canonical_origin(cls, v):
        return canonical_origin(v)

    @field_validator("worker_roles", mode="before")
    @classmethod
    def _split_roles(cls, v):  # allow "audio,render"
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    def resolved_worker_id(self) -> str:
        return self.worker_id or f"{socket.gethostname()}-{'-'.join(self.worker_roles) or 'worker'}"

    def require_public_origin(self) -> None:
        """Production: e-mailed links must point to an https origin chosen by the operator."""
        if not self.public_origin:
            raise RuntimeError("Missing required configuration: ASC_PUBLIC_ORIGIN (e.g. https://clipper.example.com)")
        # http only for a dashboard reached on this machine itself (local compose): links never leave it
        if not self.public_origin.startswith("https://") and not is_loopback(urlsplit(self.public_origin).hostname or ""):
            raise RuntimeError("ASC_PUBLIC_ORIGIN must use https in production")

    def require_secrets(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "Missing required configuration: " + ", ".join(f"ASC_{n.upper()}" for n in missing)
            )


def canonical_origin(value) -> str:
    """``scheme://host[:port]`` or ``""``. Rejects anything that is not a bare http(s) origin."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("ASC_PUBLIC_ORIGIN must be an http(s) origin, e.g. https://clipper.example.com")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("ASC_PUBLIC_ORIGIN must be only scheme://host[:port], without path, query or credentials")
    host = parts.hostname.lower()
    if not all(c.isascii() and (c.isalnum() or c in ".-:") for c in host):
        raise ValueError("ASC_PUBLIC_ORIGIN has an invalid host")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("ASC_PUBLIC_ORIGIN has an invalid port") from exc
    netloc = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{netloc}{f':{port}' if port else ''}"


def is_loopback(host: str) -> bool:
    host = (host or "").strip().lower().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
