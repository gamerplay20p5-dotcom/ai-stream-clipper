"""YouTube: watch?v= links are single broadcasts; the system must follow the channel."""

from __future__ import annotations

import json

import httpx
import pytest

from clipper import platforms
from clipper.platforms import InvalidStreamURL, VideoUnavailable, canonical_youtube, explain_youtube, parse_stream_url
from conftest import CSRF, sign_headers

CID = "UCdlJx-w37viXFNq__qZ7ARQ"
PAGE_OK = f'<html>..."ownerChannelName":"PAULINHO O LOKO",..."channelId":"{CID}"...</html>'
PAGE_REMOVED = '<html>{"playabilityStatus":{"status":"ERROR","reason":"This video has been removed by the uploader"}}</html>'


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


@pytest.fixture()
def yt_page(monkeypatch):
    pages = {}

    def fake_get(url, **kw):
        assert url.startswith("https://www.youtube.com/watch?v=")  # fixed host, never user-controlled
        return _Resp(pages.get(url.rsplit("=", 1)[1], ""), 200)

    monkeypatch.setattr(httpx, "get", fake_get)
    return pages


def test_watch_link_becomes_the_channel(yt_page):
    yt_page["S9mtLzJXYCs"] = PAGE_OK
    ref = canonical_youtube(parse_stream_url("https://www.youtube.com/watch?v=S9mtLzJXYCs", resolve_dns=False))
    assert ref.channel == CID
    assert ref.url == f"https://www.youtube.com/channel/{CID}/live"
    assert ref.display_name == "PAULINHO O LOKO"


def test_removed_video_is_explained(yt_page):
    yt_page["S9mtLzJXYCs"] = PAGE_REMOVED
    with pytest.raises(VideoUnavailable, match="cadastre o canal"):
        canonical_youtube(parse_stream_url("https://youtu.be/S9mtLzJXYCs", resolve_dns=False))


def test_unreadable_page_is_not_reported_as_removed(yt_page):
    with pytest.raises(InvalidStreamURL) as exc:
        platforms.resolve_youtube_channel("S9mtLzJXYCs")
    assert not isinstance(exc.value, VideoUnavailable)


def test_channel_links_are_untouched():
    ref = parse_stream_url("https://www.youtube.com/@PaulinhoLOKOoficial", resolve_dns=False)
    assert canonical_youtube(ref) is ref


def test_streamlink_messages_become_panel_text():
    err, note = explain_youtube(["Could not get video info - ERROR: This video has been removed by the uploader"])
    assert "removido" in err and not note
    err, note = explain_youtube(["Could not get video info - LIVE_STREAM_OFFLINE: This live event will begin in 3 hours."])
    assert not err and "agendada" in note and "3 hours" in note
    assert explain_youtube(["Sign in to confirm you're not a bot"])[0].startswith("O YouTube pediu")
    assert explain_youtube([]) == ("", "")


def test_api_registers_the_channel(admin, yt_page, monkeypatch):
    monkeypatch.setattr(platforms, "assert_public_host", lambda host, resolve=True: None)
    yt_page["cxuF5siTUQw"] = PAGE_OK
    r = admin.post("/api/streamers", json={"url": "https://www.youtube.com/watch?v=cxuF5siTUQw"}, headers=CSRF)
    assert r.status_code == 201, r.text
    assert r.json()["channel"] == CID and r.json()["url"].endswith("/live")
    # the same channel through another of its videos is a duplicate
    yt_page["xxTKEsYhnn4"] = PAGE_OK
    assert admin.post("/api/streamers", json={"url": "https://youtu.be/xxTKEsYhnn4"}, headers=CSRF).status_code == 409
    yt_page["8Zul79Ml1nQ"] = PAGE_REMOVED
    bad = admin.post("/api/streamers", json={"url": "https://youtu.be/8Zul79Ml1nQ"}, headers=CSRF)
    assert bad.status_code == 422 and "cadastre o canal" in bad.json()["detail"]
    admin.delete(f"/api/streamers/{r.json()['id']}", headers=CSRF)


def test_monitor_migrates_old_single_video_streamer(client, db):
    from clipper.models import Streamer

    s = Streamer(platform="youtube", channel="v:Abcdefghijk", url="https://www.youtube.com/watch?v=Abcdefghijk",
                 display_name="Antigo", status="offline")
    db.add(s)
    db.commit()
    other = "UC" + "x" * 22
    body = json.dumps({"streamer_id": s.id, "live": False, "title": "", "error": "",
                       "note": "Transmissão agendada no YouTube (em 3 horas)", "channel_id": other}).encode()
    r = client.post("/internal/monitor/report", content=body,
                    headers={**sign_headers("POST", "/internal/monitor/report", body), "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.get(Streamer, s.id)
    assert s.channel == other and s.url == f"https://www.youtube.com/channel/{other}/live"
    assert s.status == "offline" and "agendada" in s.last_error
    db.delete(s)
    db.commit()
