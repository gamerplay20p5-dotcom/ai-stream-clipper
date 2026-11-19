"""End to end, two different accounts against a REAL API process (uvicorn, HTTP, cookies):
the admin shares three videos; the guest sees only those three, cannot download without permission
and loses access immediately after revocation (same open session)."""

from __future__ import annotations

import httpx
import sqlalchemy as sa

from conftest import ADMIN, CSRF
from test_e2e import Stack


def _seed(stack: Stack, n: int = 5) -> list[int]:
    """Rendered clips written straight into the server's database and storage (no workers needed)."""
    eng = sa.create_engine(stack.env["ASC_DATABASE_URL"])
    storage = __import__("pathlib").Path(stack.env["ASC_STORAGE_DIR"])
    md = sa.MetaData()
    md.reflect(eng)
    t = md.tables
    ids = []
    with eng.begin() as c:
        sid = c.execute(t["streamers"].insert().values(**{**_defaults(t["streamers"]), "platform": "twitch",
                        "channel": "e2e_share", "url": "https://www.twitch.tv/e2e_share",
                        "display_name": "Streamer E2E"})).inserted_primary_key[0]
        sess = c.execute(t["stream_sessions"].insert().values(**{**_defaults(t["stream_sessions"]),
                         "streamer_id": sid, "status": "ended"})).inserted_primary_key[0]
        for i in range(n):
            cid = c.execute(t["clips"].insert().values(**{**_defaults(t["clips"]), "session_id": sess,
                            "streamer_id": sid, "start": 0.0, "end": 20.0, "peak": 10.0, "score": 80.0,
                            "status": "ready", "version": 1, "title": f"Video {i + 1}"})).inserted_primary_key[0]
            d = storage / "clips" / str(cid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "clip_v1_e2e.mp4").write_bytes(bytes([i]) * 8192)
            (d / "thumb_v1_e2e.jpg").write_bytes(bytes([0xFF, 0xD8]) + bytes([i]) * 100)
            c.execute(t["clips"].update().where(t["clips"].c.id == cid).values(
                video_path=f"clips/{cid}/clip_v1_e2e.mp4", thumb_path=f"clips/{cid}/thumb_v1_e2e.jpg"))
            ids.append(cid)
    return ids


def _defaults(table) -> dict:
    """NOT NULL columns without a server default need a value when inserting with raw SQL."""
    from datetime import datetime

    out = {}
    for col in table.columns:
        if col.primary_key or col.nullable or col.server_default is not None:
            continue
        kind = str(col.type).upper()
        out[col.name] = (datetime(2026, 11, 19) if "DATE" in kind or "TIME" in kind else {} if "JSON" in kind
                         else 0 if ("INT" in kind or "BOOL" in kind) else 0.0 if ("FLOAT" in kind or "REAL" in kind)
                         else "")
    return out


def test_two_accounts_end_to_end(tmp_path):
    stack = Stack(tmp_path)
    stack.start("api", "api", "--host", "127.0.0.1", "--port", str(stack.api_port))
    try:
        stack.wait_api()
        ids = _seed(stack)
        shared, hidden = ids[:3], ids[3:]

        admin = httpx.Client(base_url=stack.api, timeout=30, headers=CSRF)
        assert admin.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        acct = admin.post("/api/admin/users", json={"email": "Convidado@Externo.com"}).json()
        assert acct["role"] == "viewer"
        r = admin.post("/api/shares", json={"email": "convidado@externo.com", "clip_ids": shared})
        assert r.status_code == 200 and len(r.json()["shared"]) == 3, r.text

        guest = httpx.Client(base_url=stack.api, timeout=30, headers=CSRF)  # second account, own cookies
        assert guest.post("/api/auth/login", json={"email": "convidado@externo.com",
                                                   "password": acct["initial_password"]}).status_code == 200
        me = guest.get("/api/auth/me").json()
        assert me["staff"] is False and me["areas"] == ["library"]

        items = guest.get("/api/library").json()["items"]
        assert sorted(i["clip_id"] for i in items) == sorted(shared)  # exactly the three
        for cid in shared:
            v = guest.get(f"/api/library/{cid}/video", headers={"Range": "bytes=0-1023"})
            assert v.status_code == 206 and len(v.content) == 1024  # the browser player works
            assert guest.get(f"/api/library/{cid}/download").status_code == 403  # no download permission
        for cid in hidden:
            assert guest.get(f"/api/library/{cid}/video").status_code == 404
            assert guest.get(f"/api/clips/{cid}/video").status_code == 403
        assert guest.get("/api/clips").status_code == 403
        assert guest.get("/api/admin/users").status_code == 403

        share_id = next(s["id"] for s in admin.get(f"/api/shares?clip_id={shared[0]}").json())
        assert admin.delete(f"/api/shares/{share_id}").status_code == 200  # revoke one
        assert guest.get(f"/api/library/{shared[0]}/video").status_code == 404  # immediately, same session
        assert sorted(i["clip_id"] for i in guest.get("/api/library").json()["items"]) == sorted(shared[1:])

        uid = next(u["id"] for u in admin.get("/api/admin/users").json() if u["email"] == "convidado@externo.com")
        admin.patch(f"/api/admin/users/{uid}", json={"active": False})
        assert guest.get("/api/library").status_code == 401  # deactivated: the open session ends
    finally:
        stack.stop("api")
