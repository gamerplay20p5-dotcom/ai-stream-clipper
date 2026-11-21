"""Access by streamer: an account sees every APPROVED clip of a streamer (current and future approvals),
organised by streamer in its library. Built on ordinary shares, so every rule of the library still applies."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from clipper.db import session_scope, utcnow
from clipper.models import Clip, ClipShare, Streamer, StreamSession
from conftest import CSRF
from test_multiuser import _account, _login, _share, store  # noqa: F401  (fixture re-export)


def _streamer(name: str) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"sg_{name}", url=f"https://www.twitch.tv/sg_{name}", display_name=name)
        db.add(s)
        db.flush()
        return s.id


def _clip_of(store, streamer_id: int, tag: str, status: str = "approved") -> int:
    with session_scope() as db:
        sess = StreamSession(streamer_id=streamer_id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=streamer_id, start=0, end=20, peak=10, score=80, status=status,
                 version=1, title=f"Corte {tag}", sha256="b" * 64)
        db.add(c)
        db.flush()
        d = store / "clips" / str(c.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "clip_v1_g.mp4").write_bytes(b"\x02" * 2048)
        (d / "thumb_v1_g.jpg").write_bytes(b"\xff\xd8g")
        c.video_path, c.thumb_path = f"clips/{c.id}/clip_v1_g.mp4", f"clips/{c.id}/thumb_v1_g.jpg"
        return c.id


def _grant(admin, email, streamer_id, **kw):
    r = admin.post("/api/streamer-grants", json={"email": email, "streamer_id": streamer_id, **kw}, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()


def _library(c) -> dict[int, dict]:
    return {i["clip_id"]: i for i in c.get("/api/library").json()["items"]}


def test_grant_shares_only_the_approved_clips_of_that_streamer(app, admin, store):
    _account("sg1@cliente.com")
    neutro, other = _streamer("Neutro SG1"), _streamer("Outro SG1")
    ok1, ok2 = _clip_of(store, neutro, "a"), _clip_of(store, neutro, "b")
    pending = _clip_of(store, neutro, "c", status="ready")
    rejected = _clip_of(store, neutro, "d", status="rejected")
    foreign = _clip_of(store, other, "e")
    g = _grant(admin, "sg1@cliente.com", neutro)
    assert g["shared_now"] == 2 and g["state"] == "ativo" and g["streamer"] == "Neutro SG1"
    guest = _login(app, "sg1@cliente.com")
    lib = _library(guest)
    assert set(lib) == {ok1, ok2}
    assert all(lib[i]["streamer"] == "Neutro SG1" for i in lib)
    for cid in (pending, rejected, foreign):
        assert guest.get(f"/api/library/{cid}/video").status_code == 404
    assert guest.get(f"/api/library/{ok1}/download").status_code == 403  # download off by default


def test_later_approval_is_shared_and_rejection_withdraws_it(app, admin, store):
    _account("sg2@cliente.com")
    st = _streamer("Neutro SG2")
    _grant(admin, "sg2@cliente.com", st)
    guest = _login(app, "sg2@cliente.com")
    new = _clip_of(store, st, "novo", status="ready")
    assert new not in _library(guest)
    assert admin.post(f"/api/clips/{new}/approve", headers=CSRF).status_code == 200
    assert new in _library(guest)  # approved now -> in the library at once
    assert admin.post(f"/api/clips/{new}/reject", headers=CSRF).status_code == 200
    assert new not in _library(guest)  # no longer approved -> withdrawn


def test_batch_approval_and_the_maintenance_safety_net(app, admin, store):
    _account("sg3@cliente.com")
    st = _streamer("Neutro SG3")
    _grant(admin, "sg3@cliente.com", st)
    a, b = _clip_of(store, st, "x", status="ready"), _clip_of(store, st, "y", status="ready")
    assert admin.post("/api/clips/batch", json={"ids": [a], "action": "approve"}, headers=CSRF).status_code == 200
    with session_scope() as db:  # approved by another path (script / old worker): no hook ran
        db.get(Clip, b).status = "approved"
    from clipper import sharing

    with session_scope() as db:
        assert sharing.sync_all_grants(db) == 1
    assert {a, b} <= set(_library(_login(app, "sg3@cliente.com")))


def test_explicit_shares_are_kept_and_never_duplicated(app, admin, store):
    _account("sg4@cliente.com")
    st = _streamer("Neutro SG4")
    c = _clip_of(store, st, "z")
    _share(admin, "sg4@cliente.com", [c], can_download=True)
    g = _grant(admin, "sg4@cliente.com", st)
    assert g["shared_now"] == 0  # already shared explicitly: kept as the admin chose
    with session_scope() as db:
        active = db.scalars(select(ClipShare).where(ClipShare.clip_id == c, ClipShare.revoked_at.is_(None))).all()
        assert len(active) == 1 and active[0].grant_id is None and active[0].can_download
    assert admin.delete(f"/api/streamer-grants/{g['id']}", headers=CSRF).json()["shares_revoked"] == 0
    assert c in _library(_login(app, "sg4@cliente.com"))  # the explicit share survives the revocation
    assert admin.post(f"/api/clips/{c}/reject", headers=CSRF).status_code == 200
    assert c in _library(_login(app, "sg4@cliente.com"))  # explicit shares are never withdrawn by a rejection


def test_revoking_the_grant_removes_its_clips_immediately(app, admin, store):
    _account("sg5@cliente.com")
    st = _streamer("Neutro SG5")
    ids = [_clip_of(store, st, f"r{i}") for i in range(3)]
    g = _grant(admin, "sg5@cliente.com", st, can_download=True)
    guest = _login(app, "sg5@cliente.com")
    assert set(_library(guest)) == set(ids)
    assert guest.get(f"/api/library/{ids[0]}/download").status_code == 200
    r = admin.delete(f"/api/streamer-grants/{g['id']}", headers=CSRF).json()
    assert r["shares_revoked"] == 3 and r["state"] == "revogado"
    assert _library(guest) == {}  # same open session
    later = _clip_of(store, st, "depois", status="ready")
    admin.post(f"/api/clips/{later}/approve", headers=CSRF)
    assert _library(guest) == {}  # a revoked grant receives nothing new


def test_changing_the_grant_updates_its_shares_and_expiry(app, admin, store):
    _account("sg6@cliente.com")
    st = _streamer("Neutro SG6")
    c = _clip_of(store, st, "e1")
    _grant(admin, "sg6@cliente.com", st)
    when = (utcnow() + timedelta(days=2)).isoformat() + "Z"
    g = _grant(admin, "sg6@cliente.com", st, can_download=True, expires_at=when)  # same account+streamer: update
    assert g["can_download"] and g["expires_at"]
    with session_scope() as db:
        s = db.scalar(select(ClipShare).where(ClipShare.clip_id == c, ClipShare.revoked_at.is_(None)))
        assert s.can_download and s.expires_at is not None
    assert len(admin.get("/api/streamer-grants", params={"user_id": g["user_id"]}).json()) == 1


def test_refusals(app, admin, store):
    _account("sg7@cliente.com", active=False)
    _account("sg7b@cliente.com", role="admin")
    st = _streamer("Neutro SG7")
    r = admin.post("/api/streamer-grants", json={"email": "sg7@cliente.com", "streamer_id": st}, headers=CSRF)
    assert r.status_code == 422 and "desativada" in r.json()["detail"]
    r = admin.post("/api/streamer-grants", json={"email": "ninguem@cliente.com", "streamer_id": st}, headers=CSRF)
    assert r.status_code == 404
    r = admin.post("/api/streamer-grants", json={"email": "sg7b@cliente.com", "streamer_id": st}, headers=CSRF)
    assert r.status_code == 422  # staff already sees everything
    r = admin.post("/api/streamer-grants", json={"email": "sg1@cliente.com", "streamer_id": 999999}, headers=CSRF)
    assert r.status_code in (404, 422)


def test_guests_cannot_manage_grants(app, admin, store):
    _account("sg8@cliente.com")
    guest = _login(app, "sg8@cliente.com")
    st = _streamer("Neutro SG8")
    assert guest.get("/api/streamer-grants").status_code == 403
    assert guest.post("/api/streamer-grants", json={"email": "sg8@cliente.com", "streamer_id": st},
                      headers=CSRF).status_code == 403
    assert guest.delete("/api/streamer-grants/1", headers=CSRF).status_code == 403


def test_migration_0013_upgrade_downgrade_keeps_history(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import downgrade_db, upgrade_db

    url = f"sqlite:///{(tmp_path / 'g.db').as_posix()}"
    upgrade_db(url)
    eng = sa.create_engine(url)
    with eng.begin() as c:
        assert c.execute(sa.text("select version_num from alembic_version")).scalar() in ("0013", "0014", "0015", "0016", "0017", "0018", "0019")  # head moves on with later migrations
        c.execute(sa.text("insert into users (email, password_hash, role, active, session_version, created_at) "
                          "values ('h@x', 'x', 'viewer', 1, 0, '2026-11-20')"))
        c.execute(sa.text("insert into streamers (platform, channel, url, display_name, enabled, preferences, status, "
                          "stream_title, last_error, check_requested, created_at) values "
                          "('twitch','h','u','H',1,'{}','unknown','','',0,'2026-11-20')"))
        c.execute(sa.text("insert into streamer_grants (user_id, streamer_id, can_download, created_at) values (1, 1, 0, '2026-11-20')"))
    downgrade_db(url, "0012")
    with eng.connect() as c:
        tables = sa.inspect(c).get_table_names()
        assert "streamer_grants_preserved" in tables and "streamer_grants" not in tables
    upgrade_db(url)
    with eng.connect() as c:
        assert c.execute(sa.text("select count(*) from streamer_grants")).scalar() == 1  # history back


def test_access_can_include_clips_still_waiting_for_review(app, admin, store):
    from clipper import pipeline
    from clipper.db import session_scope as _ss

    _account("sgp@cliente.com")
    st = _streamer("Pendentes SGP")
    approved, pending = _clip_of(store, st, "a"), _clip_of(store, st, "b", status="ready")
    g = _grant(admin, "sgp@cliente.com", st, include_pending=True)
    assert g["shared_now"] == 2 and g["include_pending"] is True
    guest = _login(app, "sgp@cliente.com")
    lib = _library(guest)
    assert lib[approved]["approved"] is True and lib[pending]["approved"] is False  # labelled in the library
    # a clip rendered later arrives at once (no approval needed for this access)
    later = _clip_of(store, st, "c", status="rendering")
    with _ss() as db:
        c = db.get(Clip, later)
        assert pipeline.store_render(db, later, c.version, c.video_path, c.thumb_path, 2048, "c" * 64, 1.0, "gameplay")
    assert later in _library(guest)
    # rejecting it removes it from the library
    assert admin.post(f"/api/clips/{pending}/reject", headers=CSRF).status_code == 200
    assert pending not in _library(guest)
    # back to "approved only": pending clips leave, approved ones stay
    _grant(admin, "sgp@cliente.com", st, include_pending=False)
    lib = _library(guest)
    assert approved in lib and later not in lib


def test_approved_only_access_ignores_clips_waiting_for_review(app, admin, store):
    _account("sgq@cliente.com")
    st = _streamer("Só aprovados SGQ")
    _grant(admin, "sgq@cliente.com", st)
    ready = _clip_of(store, st, "r", status="rendering")
    from clipper import pipeline
    from clipper.db import session_scope as _ss

    with _ss() as db:
        c = db.get(Clip, ready)
        pipeline.store_render(db, ready, c.version, c.video_path, c.thumb_path, 2048, "d" * 64, 1.0, "gameplay")
    assert ready not in _library(_login(app, "sgq@cliente.com"))


def test_select_every_clip_of_a_filter_and_batch_more_than_100(admin, store):
    st = _streamer("Lote SGL")
    ids = [_clip_of(store, st, f"l{i}", status="ready") for i in range(120)]
    got = admin.get(f"/api/clips/ids?status=ready&streamer_id={st}").json()["ids"]
    assert sorted(got) == sorted(ids)
    r = admin.post("/api/clips/batch", json={"ids": ids, "action": "approve"}, headers=CSRF).json()
    assert len(r["done"]) == 120
