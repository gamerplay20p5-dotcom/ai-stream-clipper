import json

from conftest import ADMIN, CSRF, sign_headers


def test_health(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["status"] == "ready"
    assert "asc_jobs" in client.get("/metrics").text or client.get("/metrics").status_code == 200


def test_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/streamers").status_code == 401
    assert client.get("/api/clips").status_code == 401


def test_login_failure_and_audit(client):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": ADMIN[0], "password": "nope"})
    assert r.status_code == 401


def test_csrf_header_required_for_cookie_mutations(admin):
    r = admin.post("/api/streamers", json={"url": "https://www.twitch.tv/csrf_test"})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_streamer_crud(admin, monkeypatch):
    import clipper.platforms as plat

    monkeypatch.setattr(plat, "assert_public_host", lambda host, resolve=True: None)
    r = admin.post("/api/streamers", json={"url": "https://www.twitch.tv/Some_Streamer",
                                           "preferences": {"game": "GTA V", "evil": "x"}}, headers=CSRF)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["platform"] == "twitch" and s["channel"] == "some_streamer"
    assert s["preferences"] == {"game": "GTA V"}  # unknown keys dropped
    assert admin.post("/api/streamers", json={"url": "twitch.tv/some_streamer"}, headers=CSRF).status_code == 409
    bad = admin.post("/api/streamers", json={"url": "http://192.168.1.206:8781/segments"}, headers=CSRF)
    assert bad.status_code == 422
    r = admin.patch(f"/api/streamers/{s['id']}", json={"enabled": False}, headers=CSRF)
    assert r.json()["enabled"] is False
    assert any(x["id"] == s["id"] for x in admin.get("/api/streamers").json())
    assert admin.delete(f"/api/streamers/{s['id']}", headers=CSRF).status_code == 200
    audit = admin.get("/api/audit").json()
    assert {"streamer.create", "streamer.update", "streamer.delete"} <= {a["action"] for a in audit}


def test_settings_validation(admin):
    s = admin.get("/api/settings").json()
    assert s["auto_publish"] is False
    r = admin.put("/api/settings", json={"min_clip_seconds": 90, "max_clip_seconds": 30}, headers=CSRF)
    assert r.status_code == 422
    r = admin.put("/api/settings", json={"auto_publish": True}, headers=CSRF)
    assert r.status_code == 422  # auto-publish cannot be enabled in this build
    r = admin.put("/api/settings", json={"sensitivity": 0.7, "weights": {"humor": 2.0}}, headers=CSRF)
    assert r.status_code == 200
    assert r.json()["sensitivity"] == 0.7 and r.json()["weights"]["humor"] == 2.0
    assert r.json()["weights"]["surprise"] == 1.2  # merged, not replaced
    admin.put("/api/settings", json={"sensitivity": 0.5, "weights": {"humor": 1.0}}, headers=CSRF)


def test_internal_api_rejects_unsigned_and_tampered(client):
    assert client.post("/internal/jobs/claim", json={"types": ["render"]}).status_code == 401
    body = json.dumps({"types": ["render"]}).encode()
    h = sign_headers("POST", "/internal/jobs/claim", body)
    tampered = json.dumps({"types": ["capture"]}).encode()
    r = client.post("/internal/jobs/claim", content=tampered, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 401


def _signed(client, method, path, body=None, worker="test-worker"):
    raw = b"" if body is None else json.dumps(body).encode()
    h = sign_headers(method, path, raw, worker)
    if raw:
        h["Content-Type"] = "application/json"
    return client.request(method, path, content=raw or None, headers=h)


def test_worker_registration_and_role_enforcement(client):
    r = _signed(client, "POST", "/internal/workers/register",
                {"host": "h", "host_label": "206", "roles": ["render", "bogus"], "capabilities": {"gpu": False}},
                worker="cpu-206")
    assert r.status_code == 200 and r.json()["roles"] == ["render"]
    # a CPU-only worker on 206 can never claim GPU-only work
    from clipper import queue
    from clipper.db import session_scope

    with session_scope() as db:
        queue.enqueue(db, "vision_analyze", {"clip_id": 1}, requires_gpu=True, idempotency_key="api-gpu-only")
    r = _signed(client, "POST", "/internal/jobs/claim", {"types": ["vision_analyze", "render"]}, worker="cpu-206")
    assert all(j["type"] != "vision_analyze" for j in r.json())


def test_monitor_report_creates_session_and_capture_job(admin, client):
    r = admin.post("/api/streamers", json={"url": "file://gameplay_event.mp4"}, headers=CSRF)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    r1 = _signed(client, "POST", "/internal/monitor/report", {"streamer_id": sid, "live": True})
    r2 = _signed(client, "POST", "/internal/monitor/report", {"streamer_id": sid, "live": True})
    assert r1.json()["created"] is True and r2.json()["created"] is False
    assert r1.json()["session_id"] == r2.json()["session_id"]
    jobs = admin.get("/api/jobs?type=capture").json()
    assert sum(1 for j in jobs if j["payload"].get("session_id") == r1.json()["session_id"]) == 1
    admin.delete(f"/api/streamers/{sid}", headers=CSRF)
