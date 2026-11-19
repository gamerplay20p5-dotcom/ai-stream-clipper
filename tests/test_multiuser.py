"""Multi-user stage 1: an external account only watches what the admin explicitly shared."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from clipper import storage
from clipper.db import session_scope, utcnow
from clipper.models import AuditLog, Clip, ClipShare, Streamer, StreamSession, User
from clipper.security import hash_password
from conftest import CSRF

PW = "senha-forte-de-teste-123"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "root", lambda: tmp_path)
    return tmp_path


def _account(email: str, role: str = "viewer", active: bool = True) -> int:
    with session_scope() as db:
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, password_hash=hash_password(PW), role=role, active=active)
            db.add(u)
        else:
            u.role, u.active, u.password_hash = role, active, hash_password(PW)
        db.flush()
        return u.id


def _login(app, email: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _clip(store, tag: str, *, status="ready", variants=False) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"mu_{tag}", url=f"https://www.twitch.tv/mu_{tag}", display_name="Streamer X")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=0, end=20, peak=10, score=80, status=status, version=1,
                 title=f"Corte {tag}", transcript="fala secreta do streamer", sha256="a" * 64)
        db.add(c)
        db.flush()
        d = store / "clips" / str(c.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "clip_v1_x.mp4").write_bytes(b"\x00" * 4096)
        (d / "thumb_v1_x.jpg").write_bytes(b"\xff\xd8thumb")
        c.video_path, c.thumb_path = f"clips/{c.id}/clip_v1_x.mp4", f"clips/{c.id}/thumb_v1_x.jpg"
        if variants:
            (d / "clip_v1_edit_y.mp4").write_bytes(b"\x01" * 2048)
            c.provenance = {"variants": {"edit": {"state": "done", "video_path": f"clips/{c.id}/clip_v1_edit_y.mp4",
                                                  "label": "Gancho imediato"}}}
        return c.id


def _share(admin, email, clip_ids, **kw):
    r = admin.post("/api/shares", json={"email": email, "clip_ids": clip_ids, **kw}, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ the basic promise
def test_new_account_starts_with_nothing_and_admin_sees_everything(app, admin, store):
    cid = _clip(store, "base")
    _account("novo@externo.com")
    u = _login(app, "novo@externo.com")
    assert u.get("/api/library").json() == {"items": []}
    assert u.get("/api/auth/me").json()["areas"] == ["library"]
    assert u.get("/api/clips").status_code == 403  # the panel is staff-only
    assert u.get(f"/api/library/{cid}").status_code == 404
    assert admin.get(f"/api/clips/{cid}").status_code == 200
    assert admin.get(f"/api/library/{cid}/video").status_code == 200  # admin preview


def test_user_sees_only_shared_clips_and_another_email_grants_nothing(app, admin, store):
    a, b, other = _clip(store, "a"), _clip(store, "b"), _clip(store, "c")
    _account("ana@externo.com")
    _account("beto@externo.com")
    _share(admin, " ANA@Externo.com ", [a, b])  # normalised e-mail
    ana, beto = _login(app, "ana@externo.com"), _login(app, "beto@externo.com")
    assert sorted(i["clip_id"] for i in ana.get("/api/library").json()["items"]) == sorted([a, b])
    assert beto.get("/api/library").json()["items"] == []
    for cid in (a, b, other):
        assert beto.get(f"/api/library/{cid}/video").status_code == 404
    assert ana.get(f"/api/library/{other}/video").status_code == 404


def test_shared_view_exposes_no_paths_transcripts_or_scores(app, admin, store):
    cid = _clip(store, "priv")
    _account("priv@externo.com")
    _share(admin, "priv@externo.com", [cid])
    body = _login(app, "priv@externo.com").get(f"/api/library/{cid}").text
    assert "clips/" not in body and "fala secreta" not in body and "score" not in body and '"sha256"' not in body
    assert "Streamer X" in body


def test_unknown_account_is_reported_and_never_created(admin, store):
    cid = _clip(store, "unk")
    r = admin.post("/api/shares", json={"email": "ninguem@externo.com", "clip_ids": [cid]}, headers=CSRF)
    assert r.status_code == 404 and "conta não encontrada" in r.json()["detail"]
    with session_scope() as db:
        assert db.scalar(select(User).where(User.email == "ninguem@externo.com")) is None


def test_only_rendered_clips_can_be_shared(admin, store):
    cid = _clip(store, "proc", status="rendering")
    _account("proc@externo.com")
    out = _share(admin, "proc@externo.com", [cid])
    assert out["shared"] == [] and str(cid) in {str(k) for k in out["refused"]}


# ------------------------------------------------------------------ media
def test_player_works_with_range_and_direct_panel_urls_stay_closed(app, admin, store):
    cid = _clip(store, "play")
    _account("play@externo.com")
    _share(admin, "play@externo.com", [cid])
    u = _login(app, "play@externo.com")
    full = u.get(f"/api/library/{cid}/video")
    assert full.status_code == 200 and full.headers["cache-control"] == "private, no-store"
    part = u.get(f"/api/library/{cid}/video", headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206 and len(part.content) == 1024
    assert u.get(f"/api/library/{cid}/thumbnail").status_code == 200
    for panel_url in (f"/api/clips/{cid}/video", f"/api/clips/{cid}/thumbnail", f"/api/clips/{cid}/download",
                      f"/api/clips/{cid}", f"/api/clips/{cid}/captions", f"/api/clips/{cid}/variants"):
        assert u.get(panel_url).status_code == 403, panel_url


def test_download_needs_its_own_permission(app, admin, store):
    cid = _clip(store, "dl")
    _account("dl@externo.com")
    sid = _share(admin, "dl@externo.com", [cid])["shared"][0]
    u = _login(app, "dl@externo.com")
    assert u.get(f"/api/library/{cid}").json()["download_url"] is None
    assert u.get(f"/api/library/{cid}/download").status_code == 403
    admin.patch(f"/api/shares/{sid}", json={"can_download": True}, headers=CSRF)
    r = u.get(f"/api/library/{cid}/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]


def test_variants_only_when_allowed(app, admin, store):
    cid = _clip(store, "var", variants=True)
    _account("var@externo.com")
    sid = _share(admin, "var@externo.com", [cid])["shared"][0]
    u = _login(app, "var@externo.com")
    assert u.get(f"/api/library/{cid}").json()["variants"] == []
    assert u.get(f"/api/library/{cid}/variants/edit/video").status_code == 404
    admin.patch(f"/api/shares/{sid}", json={"can_variants": True}, headers=CSRF)
    item = u.get(f"/api/library/{cid}").json()
    assert [v["name"] for v in item["variants"]] == ["edit"]
    assert u.get(item["variants"][0]["video_url"]).status_code == 200


# ------------------------------------------------------------------ immediate effect on open sessions
def test_revocation_is_immediate_in_an_open_session(app, admin, store):
    cid = _clip(store, "rev")
    _account("rev@externo.com")
    sid = _share(admin, "rev@externo.com", [cid])["shared"][0]
    u = _login(app, "rev@externo.com")
    assert u.get(f"/api/library/{cid}/video").status_code == 200
    assert admin.delete(f"/api/shares/{sid}", headers=CSRF).json()["state"] == "revogado"
    assert u.get(f"/api/library/{cid}/video").status_code == 404
    assert u.get("/api/library").json()["items"] == []
    with session_scope() as db:
        assert db.get(ClipShare, sid).revoked_at is not None  # history kept


def test_expired_share_is_refused(app, admin, store):
    cid = _clip(store, "exp")
    _account("exp@externo.com")
    sid = _share(admin, "exp@externo.com", [cid], expires_at=(utcnow() + timedelta(days=1)).isoformat() + "Z")["shared"][0]
    u = _login(app, "exp@externo.com")
    assert u.get(f"/api/library/{cid}/video").status_code == 200
    with session_scope() as db:
        db.get(ClipShare, sid).expires_at = utcnow() - timedelta(seconds=1)
    assert u.get(f"/api/library/{cid}/video").status_code == 404
    past = admin.post("/api/shares", json={"email": "exp@externo.com", "clip_ids": [cid],
                                           "expires_at": (utcnow() - timedelta(days=1)).isoformat() + "Z"}, headers=CSRF)
    assert past.status_code == 422


def test_deactivated_account_loses_access_immediately(app, admin, store):
    cid = _clip(store, "off")
    uid = _account("off@externo.com")
    _share(admin, "off@externo.com", [cid])
    u = _login(app, "off@externo.com")
    assert u.get(f"/api/library/{cid}/video").status_code == 200
    admin.patch(f"/api/admin/users/{uid}", json={"active": False}, headers=CSRF)
    assert u.get(f"/api/library/{cid}/video").status_code == 401
    assert u.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"email": "off@externo.com", "password": PW}).status_code == 401


def test_role_change_invalidates_old_privileges(app, admin, store):
    uid = _account("ex-admin@hts.com", role="admin")
    old = _login(app, "ex-admin@hts.com")
    assert old.get("/api/settings").status_code == 200
    admin.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}, headers=CSRF)
    assert old.get("/api/settings").status_code == 401  # the open admin session ended
    new = _login(app, "ex-admin@hts.com")
    assert new.get("/api/settings").status_code == 403 and new.get("/api/auth/me").json()["role"] == "viewer"


def test_end_sessions_and_password_reset(app, admin, store):
    uid = _account("sess@externo.com")
    u = _login(app, "sess@externo.com")
    admin.post(f"/api/admin/users/{uid}/end-sessions", headers=CSRF)
    assert u.get("/api/library").status_code == 401
    pw = admin.post(f"/api/admin/users/{uid}/reset-password", headers=CSRF).json()["initial_password"]
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "sess@externo.com", "password": pw}).status_code == 200
    assert c.post("/api/auth/login", json={"email": "sess@externo.com", "password": PW}).status_code == 401


# ------------------------------------------------------------------ account administration
def test_created_accounts_are_viewers_with_a_one_time_password(app, admin, store):
    r = admin.post("/api/admin/users", json={"email": "Nova.Conta@Externo.com"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    pw = r.json()["initial_password"]
    assert len(pw) >= 16
    assert admin.post("/api/admin/users", json={"email": "nova.conta@externo.com"}, headers=CSRF).status_code == 409
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "nova.conta@externo.com", "password": pw}).status_code == 200
    with session_scope() as db:
        u = db.scalar(select(User).where(User.email == "nova.conta@externo.com"))
        assert pw not in u.password_hash
        assert not any(pw in str(a.details) for a in db.scalars(select(AuditLog)))  # never logged
        assert User(email="x@y.z").role in (None, "viewer")  # model default is never admin
    assert admin.post("/api/admin/users", json={"email": "nao-e-email"}, headers=CSRF).status_code == 422


def test_admin_cannot_lock_everyone_out(admin):
    me = admin.get("/api/auth/me").json()
    with session_scope() as db:
        my_id = db.scalar(select(User.id).where(User.email == me["email"]))
    r = admin.patch(f"/api/admin/users/{my_id}", json={"role": "viewer"}, headers=CSRF)
    assert r.status_code == 409


# ------------------------------------------------------------------ IDOR and privilege escalation
def test_external_account_cannot_change_permissions_or_reach_admin_apis(app, admin, store):
    cid = _clip(store, "idor")
    victim = _account("vitima@externo.com")
    _share(admin, "vitima@externo.com", [cid])
    _account("atacante@externo.com")
    at = _login(app, "atacante@externo.com")
    h = CSRF
    assert at.post("/api/shares", json={"email": "atacante@externo.com", "clip_ids": [cid]}, headers=h).status_code == 403
    assert at.get(f"/api/shares?user_id={victim}").status_code == 403
    assert at.get(f"/api/admin/users/{victim}/shares").status_code == 403
    assert at.patch("/api/shares/1", json={"can_download": True}, headers=h).status_code == 403
    assert at.patch(f"/api/admin/users/{victim}", json={"role": "admin"}, headers=h).status_code == 403
    assert at.post("/api/admin/users", json={"email": "x@externo.com", "role": "admin"}, headers=h).status_code == 403
    # query/body parameters naming another user are ignored: the account comes from the session
    assert at.get(f"/api/library?user_id={victim}&email=vitima@externo.com").json()["items"] == []
    assert at.get(f"/api/library/{cid}/video?user_id={victim}").status_code == 404


def test_every_api_route_is_closed_to_an_external_account(app, store):
    """Crawl the whole API with a viewer session: nothing outside the allow-list may answer 2xx."""
    _account("crawler@externo.com")
    u = _login(app, "crawler@externo.com")
    allow = re.compile(r"^/api/(auth/(login|logout|me|config)|library(/.*)?|publish/oauth/\{platform\}/callback)$")
    spec = app.openapi()
    checked = 0
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/") or allow.match(path):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in ops:
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            r = u.request(method.upper(), url, json={}, headers=CSRF)
            checked += 1
            assert r.status_code in (401, 403, 404, 405, 422), (method, path, r.status_code)
            assert not (200 <= r.status_code < 300), (method, path)
    assert checked > 100  # the whole panel was exercised


# ------------------------------------------------------------------ migration
def test_0012_keeps_share_history_on_downgrade(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import downgrade_db, upgrade_db

    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        c.execute(sa.text("INSERT INTO clip_shares (id, clip_id, user_id, can_view, can_download, can_variants, "
                          "created_at) VALUES (1, 1, 1, 1, 0, 0, '2026-11-19 00:00:00')"))
        c.execute(sa.text("INSERT INTO clip_shares (id, clip_id, user_id, can_view, can_download, can_variants, "
                          "created_at, revoked_at) VALUES (2, 1, 1, 1, 0, 0, '2026-11-19 00:00:00', '2026-11-19 01:00:00')"))
    with pytest.raises(Exception):  # only ONE active share per clip+user
        with eng.begin() as c:
            c.execute(sa.text("INSERT INTO clip_shares (id, clip_id, user_id, can_view, can_download, can_variants, "
                              "created_at) VALUES (3, 1, 1, 1, 0, 0, '2026-11-19 00:00:00')"))
    downgrade_db(url, "0010")
    assert "clip_shares_preserved" in sa.inspect(eng).get_table_names()
    assert "session_version" not in {c["name"] for c in sa.inspect(eng).get_columns("users")}
    upgrade_db(url, "head")
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT count(*) FROM clip_shares")).scalar() == 2
