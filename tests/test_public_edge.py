"""Public domain behind the host reverse proxy (clipper.howto.software -> Caddy -> API)."""

from __future__ import annotations

from starlette.requests import Request

from clipper.api.deps import client_ip, via_proxy

PROXY = "172.22.0.1"  # docker bridge gateway: how Caddy on the host reaches the published port


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 5555),
                    "query_string": b"", "scheme": "https", "server": ("api", 8780)})


def test_real_client_ip_only_trusted_from_the_local_proxy():
    assert client_ip(_req(PROXY, "203.0.113.7")) == "203.0.113.7"
    assert client_ip(_req(PROXY, "1.2.3.4, 203.0.113.7")) == "203.0.113.7"  # right-most = appended by proxy
    assert client_ip(_req("192.168.3.3", "203.0.113.7")) == "192.168.3.3"  # LAN cannot forge it
    assert client_ip(_req("8.8.8.8", "10.0.0.1")) == "8.8.8.8"
    assert client_ip(_req(PROXY)) == PROXY and not via_proxy(_req(PROXY))  # worker inside docker, no XFF
    assert client_ip(_req(PROXY, "garbage")) == PROXY


def test_public_domain_cannot_reach_internal_api_and_gets_hsts(app, client):
    from fastapi.testclient import TestClient

    public = TestClient(app, base_url="https://clipper.howto.software", client=(PROXY, 40000))
    r = public.get("/internal/monitor/targets", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 404  # not even the HMAC check is reachable from the internet
    r = public.get("/healthz", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 200 and "max-age" in r.headers.get("strict-transport-security", "")
    lan = TestClient(app, base_url="https://192.168.1.210:8780", client=("192.168.1.206", 40000))
    assert lan.get("/internal/monitor/targets").status_code == 401  # LAN workers keep working (signed)


def test_login_rate_limit_is_per_real_client_behind_the_proxy(app, client):
    from fastapi.testclient import TestClient

    from clipper.api import deps

    deps._attempts.clear()
    attacker = TestClient(app, base_url="https://clipper.howto.software", client=(PROXY, 40001))
    for _ in range(10):
        attacker.post("/api/auth/login", json={"email": "admin@asc.local", "password": "wrong"},
                      headers={"X-Forwarded-For": "198.51.100.66"})
    assert attacker.post("/api/auth/login", json={"email": "admin@asc.local", "password": "wrong"},
                         headers={"X-Forwarded-For": "198.51.100.66"}).status_code == 429
    # another visitor through the same proxy is NOT locked out by the attacker
    other = attacker.post("/api/auth/login", json={"email": "admin@asc.local", "password": "wrong"},
                          headers={"X-Forwarded-For": "198.51.100.77"})
    assert other.status_code == 401
    deps._attempts.clear()


def test_public_pages_without_login_and_dashboard_with_session(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.api.app import create_app
    from clipper.config import get_settings

    (tmp_path / "site").mkdir()
    for name in ("index.html", "privacidade.html", "termos.html"):
        (tmp_path / "site" / name).write_text(f"<html>{name}</html>", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html>DASHBOARD</html>", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "dashboard_dir", tmp_path)
    app = create_app(run_maintenance=False)
    with TestClient(app) as anon:
        assert "site/index.html" not in anon.get("/").text and "index.html" in anon.get("/").text
        assert anon.get("/").text == "<html>index.html</html>"  # presentation for visitors
        assert anon.get("/privacidade").text == "<html>privacidade.html</html>"
        assert anon.get("/termos").text == "<html>termos.html</html>"
        assert anon.get("/login").text == "<html>DASHBOARD</html>"  # login page unchanged
        assert anon.get("/api/streamers").status_code == 401  # admin API still protected
        from conftest import ADMIN

        assert anon.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        assert anon.get("/").text == "<html>DASHBOARD</html>"  # with a session "/" is the dashboard
        assert anon.get("/privacidade").text == "<html>privacidade.html</html>"
