"""Phase 6C: references lab, format profiles and the format preview variant."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from sqlalchemy import select

from clipper import pipeline, refs_lab, storage
from clipper.ai import format_profile as fp
from clipper.db import session_scope, utcnow
from clipper.format_lab import plan_format_variant, store_analysis
from clipper.models import Clip, FormatProfile, Job, PublishAccount, RefItem, Streamer, StreamSession
from clipper.publish import crypto
from clipper.publish.base import Tokens
from conftest import CSRF, sign_headers


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "root", lambda: tmp_path)
    return tmp_path


def _png(w=320, h=568, color=(40, 120, 40)) -> bytes:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = color
    return cv2.imencode(".png", img)[1].tobytes()


# ------------------------------------------------------------------ links
@pytest.mark.parametrize("url,platform,kind,ext,handle", [
    ("https://www.youtube.com/@Alanzoka", "youtube", "channel", "", "@Alanzoka"),
    ("youtube.com/channel/UCdlJx-w37viXFNq__qZ7ARQ", "youtube", "channel", "UCdlJx-w37viXFNq__qZ7ARQ", ""),
    ("https://www.youtube.com/watch?v=abc123XYZ_0&t=10", "youtube", "video", "abc123XYZ_0", ""),
    ("https://youtu.be/abc123XYZ_0", "youtube", "video", "abc123XYZ_0", ""),
    ("https://www.youtube.com/shorts/abc123XYZ_0", "youtube", "short", "abc123XYZ_0", ""),
    ("https://www.instagram.com/malaiocortes/", "instagram", "profile", "", "@malaiocortes"),
    ("https://www.instagram.com/reel/C0ABCdef/", "instagram", "reel", "C0ABCdef", ""),
])
def test_links_are_recognised_without_network(url, platform, kind, ext, handle):
    d = refs_lab.parse_link(url)
    assert (d["platform"], d["kind"], d["external_id"], d["handle"]) == (platform, kind, ext, handle)


def test_unknown_links_are_refused():
    for bad in ("https://www.tiktok.com/@x/video/1", "https://www.youtube.com/feed/trending", "ftp://x"):
        with pytest.raises(refs_lab.BadLink):
            refs_lab.parse_link(bad)


# ------------------------------------------------------------------ YouTube metadata (authorized API, mocked)
class FakeYT:
    def __init__(self):
        self.paths: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.paths.append(req.url.path)
        assert req.headers["authorization"] == "Bearer AT"
        if req.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": "UCX", "snippet": {"title": "Canal X", "customUrl": "@x"},
                                                        "statistics": {"subscriberCount": "1200", "viewCount": "99",
                                                                       "videoCount": "7", "hiddenSubscriberCount": False}}]})
        if req.url.path.endswith("/videos"):
            vid = req.url.params.get("id")
            if vid == "missing":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": [{"snippet": {"title": "Fuga", "channelTitle": "X"},
                                                        "contentDetails": {"duration": "PT45S"},
                                                        "statistics": {"viewCount": "10", "likeCount": "2"}}]})
        return httpx.Response(404)


@pytest.fixture()
def yt(monkeypatch, client):
    from clipper.publish import service

    f = FakeYT()
    monkeypatch.setattr(refs_lab, "_yt_client", lambda: httpx.Client(transport=httpx.MockTransport(f.handler)))
    monkeypatch.setattr(service, "tokens_for", lambda db, acct: Tokens("AT", "", None, "", {}))
    with session_scope() as db:
        if not db.scalar(select(PublishAccount).where(PublishAccount.platform == "youtube",
                                                      PublishAccount.status == "connected")):
            db.add(PublishAccount(platform="youtube", external_id="yt-refs", status="connected", connected_by="t@x",
                                  tokens_enc=crypto.encrypt(Tokens("AT", "RT", utcnow() + timedelta(hours=1)).as_dict())))
    return f


def test_youtube_metadata_uses_only_one_unit_calls_and_expires_in_30_days(admin, yt):
    r = admin.post("/api/refs/items", json={"url": "https://www.youtube.com/@x", "category": "GTA RP",
                                            "tags": ["humor", " humor", ""], "why": "ritmo bom"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["tags"] == ["humor"]
    iid = r.json()["id"]
    d = admin.post(f"/api/refs/items/{iid}/refresh", headers=CSRF).json()
    assert d["api_data"]["subscribers"] == 1200 and d["title"] == "Canal X" and d["external_id"] == "UCX"
    assert d["api_source"] == "youtube-data-api-v3" and d["api_expires_at"]
    v = admin.post("/api/refs/items", json={"url": "https://www.youtube.com/shorts/abc"}, headers=CSRF).json()
    d = admin.post(f"/api/refs/items/{v['id']}/refresh", headers=CSRF).json()
    assert d["api_data"]["duration_s"] == 45 and d["api_data"]["short"] == "provável"
    assert all(not p.endswith("/search") for p in yt.paths)  # never the 100-unit search
    m = admin.post("/api/refs/items", json={"url": "https://youtu.be/missing"}, headers=CSRF).json()
    assert admin.post(f"/api/refs/items/{m['id']}/refresh", headers=CSRF).status_code == 422


def test_instagram_references_are_manual_with_the_reason(admin):
    r = admin.post("/api/refs/items", json={"url": "https://www.instagram.com/reel/C0ABC/", "why": "gancho"}, headers=CSRF)
    iid = r.json()["id"]
    r = admin.post(f"/api/refs/items/{iid}/refresh", headers=CSRF)
    assert r.status_code == 422 and "Business Discovery" in r.json()["detail"]
    caps = admin.get("/api/refs/capabilities").json()["items"]
    assert any(c["platform"] == "instagram" and c["status"] == "indisponível" for c in caps)
    assert any("Baixar vídeos de terceiros" in c["what"] and c["status"] == "proibido" for c in caps)


def test_expired_api_data_is_cleared_but_the_owner_notes_stay(client):
    with session_scope() as db:
        it = RefItem(platform="youtube", kind="channel", url="u", title="t", notes="minha nota", tags=["a"],
                     api_data={"views": 1}, api_source="youtube-data-api-v3", api_fetched_at=utcnow(),
                     api_expires_at=utcnow() - timedelta(minutes=1))
        db.add(it)
        db.flush()
        assert refs_lab.expire_api_data(db) >= 1
        assert it.api_data == {} and it.notes == "minha nota" and it.tags == ["a"]


# ------------------------------------------------------------------ images, collections
def test_images_are_validated_and_reencoded(admin, store):
    iid = admin.post("/api/refs/items", json={"title": "print de referência"}, headers=CSRF).json()["id"]
    bad = admin.post(f"/api/refs/items/{iid}/image", content=b"not an image", headers=CSRF)
    assert bad.status_code == 422
    ok = admin.post(f"/api/refs/items/{iid}/image", content=_png(), headers=CSRF)
    assert ok.status_code == 200 and ok.json()["has_image"]
    with session_scope() as db:
        assert storage.resolve(db.get(RefItem, iid).image_path).exists()
    big = admin.post(f"/api/refs/items/{iid}/image", content=b"x" * (8 * 1024 * 1024 + 10), headers=CSRF)
    assert big.status_code == 413


def test_collections_group_references_and_deleting_one_keeps_them(admin):
    a = admin.post("/api/refs/items", json={"title": "A", "games": ["GTA V"]}, headers=CSRF).json()["id"]
    c = admin.post("/api/refs/collections", json={"name": "Perseguições"}, headers=CSRF).json()["id"]
    admin.post(f"/api/refs/collections/{c}/items", json={"item_id": a}, headers=CSRF)
    admin.post(f"/api/refs/collections/{c}/items", json={"item_id": a}, headers=CSRF)  # idempotent
    assert [i["id"] for i in admin.get(f"/api/refs/items?collection_id={c}").json()] == [a]
    admin.delete(f"/api/refs/collections/{c}", headers=CSRF)
    assert admin.get(f"/api/refs/items?q=A").status_code == 200
    with session_scope() as db:
        assert db.get(RefItem, a) is not None


# ------------------------------------------------------------------ format analysis (measurement + model)
def _layout(webcam_bottom=False, caption_y=1320, badge=True):
    img = np.zeros((1920, 1080, 3), np.uint8)
    cam, game = (60, 80, 120), (40, 120, 40)
    split = 1220 if webcam_bottom else 700
    img[:split], img[split:] = (game, cam) if webcam_bottom else (cam, game)
    fy = 1500 if webcam_bottom else 330
    cv2.circle(img, (540, fy), 110, (150, 170, 200), -1)
    cv2.circle(img, (500, fy - 30), 15, (0, 0, 0), -1)
    cv2.circle(img, (580, fy - 30), 15, (0, 0, 0), -1)
    for color, thick in (((0, 0, 0), 22), ((255, 255, 255), 8)):
        cv2.putText(img, "OLHA ISSO", (180, caption_y), cv2.FONT_HERSHEY_SIMPLEX, 3.2, color, thick)
    if badge:
        cv2.circle(img, (90, 120), 60, (230, 230, 230), -1)
    return img


def test_measurement_finds_split_webcam_side_caption_and_badge():
    p = fp.analyze(_layout())["params"]
    assert p["layout"] == "split" and p["webcam"]["position"] == "top" and 30 <= p["webcam"]["height_pct"] <= 42
    assert p["captions"]["position"] == "low" and p["badge"]["enabled"] and p["badge"]["position"] == "top_left"
    assert p["badge"]["name"] == ""  # never read from the print
    assert p["sources"]["layout"] == "medido"
    q = fp.analyze(_layout(webcam_bottom=True, caption_y=700, badge=False))["params"]
    assert q["webcam"]["position"] == "bottom" and q["captions"]["position"] == "high" and not q["badge"]["enabled"]


def test_model_answers_are_closed_categories_and_never_copy_text():
    v = fp.parse_vlm('{"webcam_position": "top", "caption_style": "neon-glow", "badge_position": "top_right",'
                     ' "layout_notes": "GIFT SUB! @canalfamoso"}')
    assert v == {"webcam_position": "top", "badge_position": "top_right"}  # no free text kept at all


def test_model_alone_never_switches_layout_or_enables_the_badge():
    """Real frames (2026-11-19): the model hallucinated a badge and a webcam on a clip without them."""
    plain = np.zeros((1920, 1080, 3), np.uint8)
    plain[:] = (30, 30, 30)
    p = fp.analyze(plain, lambda pr, img: '{"webcam_position": "top", "badge_position": "bottom_left"}')["params"]
    assert p["layout"] == "gameplay" and not p["badge"]["enabled"]
    assert any("não confirmado" in n for n in p["notes"]) and any("confirme" in n for n in p["notes"])


def test_model_failure_keeps_the_measurement():
    def broken(prompt, img):
        raise RuntimeError("GPU busy")

    r = fp.analyze(_layout(), broken)
    assert r["vlm_error"].startswith("RuntimeError") and r["params"]["layout"] == "split"


def test_model_fills_what_cannot_be_measured():
    plain = np.zeros((1920, 1080, 3), np.uint8)
    plain[:] = (30, 30, 30)
    r = fp.analyze(plain, lambda prompt, img: '{"caption_position": "middle", "caption_style": "boxed",'
                                              ' "caption_case": "mixed", "webcam_position": "none"}')
    p = r["params"]
    assert p["captions"]["position"] == "middle" and p["captions"]["style"] == "boxed"
    assert p["captions"]["uppercase"] is False and p["sources"]["captions.position"] == "IA visual"


# ------------------------------------------------------------------ profiles through the API
def test_profile_params_are_validated_and_print_analysis_is_queued_with_a_budget(admin, store):
    bad = admin.post("/api/refs/formats", json={"name": "x", "params": {"layout": "diagonal"}}, headers=CSRF)
    assert bad.status_code == 422
    pid = admin.post("/api/refs/formats", json={"name": "Meu formato"}, headers=CSRF).json()["id"]
    r = admin.post(f"/api/refs/formats/{pid}/image", content=_png(), headers=CSRF)
    assert r.status_code == 200 and r.json()["queued"] and r.json()["status"] == "queued"
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.type == "format_analyze").order_by(Job.id.desc()))
        assert job.payload["profile_id"] == pid and job.priority == 90
        from clipper.settings_store import load_ai_settings, save_ai_settings

        s = load_ai_settings(db)
        s.refs_analyses_per_hour = 0
        save_ai_settings(db, s)
    try:
        assert admin.post(f"/api/refs/formats/{pid}/analyze", headers=CSRF).status_code == 429
    finally:
        with session_scope() as db:
            s = load_ai_settings(db)
            s.refs_analyses_per_hour = 10
            save_ai_settings(db, s)


def test_worker_result_is_stored_as_clean_parameters(client):
    with session_scope() as db:
        p = FormatProfile(name="p", params={}, analysis={})
        db.add(p)
        db.flush()
        res = fp.analyze(_layout())
        res["params"]["captions"]["size_pct"] = 120
        store_analysis(db, p, json.loads(json.dumps(res, default=float)))
        assert p.status == "ready" and p.params["layout"] == "split" and p.params["badge"]["name"] == ""
        store_analysis(db, p, None, "timeout")
        assert p.status == "failed"


# ------------------------------------------------------------------ preview variant on OUR clip
def _clip(store, tag, webcam=True) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"fl_{tag}", url=f"https://www.twitch.tv/fl_{tag}")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=100, end=130, peak=115, score=80, status="ready",
                 version=1, video_path=f"clips/{tag}/clip_v1_a.mp4", sha256="a" * 64, layout="auto",
                 webcam_region={"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3} if webcam else None,
                 source_path="clips/x/source.mp4", source_start=90.0, source_duration=50.0)
        db.add(c)
        db.flush()
        return c.id


def test_preview_variant_applies_the_profile_and_keeps_the_main_video(admin, store):
    cid = _clip(store, "prev")
    params = {"layout": "split", "webcam": {"position": "bottom", "height_pct": 40},
              "captions": {"position": "high", "size_pct": 120, "style": "boxed", "uppercase": False},
              "badge": {"enabled": True, "position": "top_right", "name": "Malaio Cortes"}}
    pid = admin.post("/api/refs/formats", json={"name": "Teste", "params": params}, headers=CSRF).json()["id"]
    r = admin.post(f"/api/refs/formats/{pid}/preview", json={"clip_id": cid}, headers=CSRF)
    assert r.status_code == 200 and r.json()["badge"] is False  # no avatar uploaded: badge skipped, never a placeholder
    admin.post(f"/api/refs/formats/{pid}/avatar", content=_png(200, 200, (0, 140, 255)), headers=CSRF)
    r = admin.post(f"/api/refs/formats/{pid}/preview", json={"clip_id": cid}, headers=CSRF)
    assert r.json()["badge"] is True
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.video_path.endswith("clip_v1_a.mp4") and c.status == "ready"  # shadow: main untouched
        spec = pipeline.render_spec(db, cid, "format")
    ep = spec["edit_plan"]
    assert ep["layout"] == "split" and ep["webcam_position"] == "bottom" and ep["webcam_height_pct"] == 40
    assert ep["badge"]["avatar_url"].endswith(f"/format-profiles/{pid}/avatar") and spec["store_source"] is False
    assert spec["captions"]["badge_name"] == "Malaio Cortes" and spec["captions"]["uppercase"] is False
    assert spec["caption_style"] == "boxed"


def test_split_profile_on_a_clip_without_webcam_keeps_its_layout(admin, store):
    cid = _clip(store, "nocam", webcam=False)
    pid = admin.post("/api/refs/formats", json={"name": "S", "params": {"layout": "split"}}, headers=CSRF).json()["id"]
    admin.post(f"/api/refs/formats/{pid}/preview", json={"clip_id": cid}, headers=CSRF)
    with session_scope() as db:
        assert pipeline.render_spec(db, cid, "format")["edit_plan"].get("layout") != "split"


def test_avatar_is_served_to_workers_only_with_the_internal_signature(admin, client, store):
    pid = admin.post("/api/refs/formats", json={"name": "A"}, headers=CSRF).json()["id"]
    admin.post(f"/api/refs/formats/{pid}/avatar", content=_png(200, 200), headers=CSRF)
    path = f"/internal/format-profiles/{pid}/avatar"
    assert client.get(path).status_code in (401, 403)
    assert client.get(path, headers=sign_headers("GET", path)).status_code == 200


# ------------------------------------------------------------------ renderer (real ffmpeg)
def test_render_with_webcam_bottom_and_our_badge(tmp_path):
    from conftest import FIXTURES
    from clipper import media
    from clipper.render.renderer import RenderSpec, render

    av = tmp_path / "avatar.png"
    av.write_bytes(_png(200, 200, (0, 140, 255)))
    plan = {"layout": "split", "webcam_position": "bottom", "webcam_height_pct": 40, "badge_avatar": str(av),
            "badge_position": "top_left", "caption_style": "bold"}
    spec = RenderSpec(source=FIXTURES / "gameplay_event.mp4", source_start=0.0, start=18.0, end=24.0, peak=21.0,
                      words=[], title="", layout="split", webcam_region={"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3},
                      preset="ultrafast", crf=30, threads=2, plan=plan,
                      captions={"badge_name": "Malaio Cortes", "badge_position": "top_left"})
    res = asyncio.run(render(spec, tmp_path / "r"))
    v = media.video_stream(media.probe_sync(res.video))
    assert (v["width"], v["height"]) == (1080, 1920)
    ass = (tmp_path / "r" / "subs.ass").read_text(encoding="utf-8")
    assert ",Badge," in ass and "Malaio Cortes" in ass


# ------------------------------------------------------------------ migration 0011
def test_0011_downgrade_preserves_the_owner_data(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import downgrade_db, upgrade_db

    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO ref_collections (id, name, description, created_at) "
                          "VALUES (1, 'minha coleção', '', '2026-11-19 00:00:00')"))
    downgrade_db(url, "0010")
    names = sa.inspect(eng).get_table_names()
    assert "ref_collections" not in names and "ref_collections_preserved" in names
    upgrade_db(url, "head")
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT name FROM ref_collections")).scalar() == "minha coleção"
