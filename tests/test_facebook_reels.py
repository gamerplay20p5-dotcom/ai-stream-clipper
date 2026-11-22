"""Facebook Reels provider against a simulated Graph API (httpx MockTransport): no network, no real Page."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from clipper.publish import facebook
from clipper.publish.base import App, PublishError, Tokens


class Graph:
    def __init__(self, pages=None, status=None, finish_status=200):
        self.calls = []
        self.pages = pages if pages is not None else [
            {"id": "111", "name": "Página A", "access_token": "page-a", "tasks": ["CREATE_CONTENT", "MANAGE"]},
            {"id": "222", "name": "Página só leitura", "access_token": "page-b", "tasks": ["ANALYZE"]},
        ]
        self.status = status or {"video_status": "ready", "publishing_phase": {"status": "complete", "publish_status": "published"}}
        self.finish_status = finish_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url.split("?")[0], dict(request.headers), request.content[:200]))
        if "/oauth/access_token" in url:
            return httpx.Response(200, json={"access_token": "user-token", "expires_in": 5183944})
        if url.split("?")[0].endswith("/me/accounts"):
            return httpx.Response(200, json={"data": self.pages})
        if "rupload.facebook.com" in url:
            return httpx.Response(200, json={"success": True})
        if url.split("?")[0].endswith("/video_reels"):
            body = request.content.decode() if request.content else ""
            if '"upload_phase": "start"' in body or '"upload_phase":"start"' in body:
                return httpx.Response(200, json={"video_id": "vid1", "upload_url": "https://rupload.facebook.com/video-upload/vid1"})
            return httpx.Response(self.finish_status, json={"success": True} if self.finish_status < 400 else {})
        if "/vid1" in url:
            return httpx.Response(200, json={"status": self.status})
        return httpx.Response(404, json={"error": {"message": "unexpected", "code": 100}})


@pytest.fixture
def graph(monkeypatch):
    g = Graph()
    monkeypatch.setattr(facebook, "_client", lambda: httpx.Client(transport=httpx.MockTransport(g.handler)))
    return g


def test_login_requests_only_page_permissions():
    url = facebook.auth_url(App("facebook", "app-id", "secret", "https://clipper.example.test/cb"), "st", "")
    assert url.startswith("https://www.facebook.com/v27.0/dialog/oauth?")
    assert "scope=pages_show_list%2Cpages_read_engagement%2Cpages_manage_posts" in url
    assert "publish_actions" not in url and "user_" not in url


def test_only_pages_where_the_person_can_create_content(graph):
    tokens = facebook.exchange(App("facebook", "app-id", "secret", "https://x/cb"), "code", "")
    info = facebook.account_info(tokens)
    assert info["external_id"] == "111" and info["additional"] == []
    assert info["token_extra"] == {"page_id": "111", "page_token": "page-a"}
    assert info["meta"]["privacy_options"] == ["public"]


def test_personal_profiles_are_not_supported(graph):
    graph.pages = []
    with pytest.raises(PublishError, match="perfis pessoais não são suportados"):
        facebook.account_info(Tokens("user-token"))


def test_three_step_upload_and_finish_called_once(graph, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0123456789")
    saved = []
    pub = SimpleNamespace(upload_state={}, description="Momento", title="T", tags=["gta"])
    tokens = Tokens("user-token", extra={"page_id": "111", "page_token": "page-a"})
    res = facebook.upload(pub, tokens, video, lambda st: saved.append(dict(st)))
    assert res.remote_id == "vid1"
    steps = [(m, u.rsplit("/", 2)[-2:]) for m, u, *_ in graph.calls]
    assert steps[0] == ("POST", ["111", "video_reels"])
    rupload = graph.calls[1]
    assert rupload[1] == "https://rupload.facebook.com/video-upload/v27.0/vid1"
    assert rupload[2]["authorization"] == "OAuth page-a" and rupload[2]["file_size"] == "10" and rupload[2]["offset"] == "0"
    finish = graph.calls[2]
    assert finish[1].endswith("/111/video_reels") and b"upload_phase=finish" in finish[3] and b"video_state=PUBLISHED" in finish[3]
    assert saved[-1]["finish_called_at"], "the publishing call is recorded before it is sent"
    # A restart after the finish call: never published twice
    pub.upload_state = saved[-1]
    before = len(graph.calls)
    facebook.upload(pub, tokens, video, saved.append)
    assert len(graph.calls) == before


def test_lost_finish_answer_is_reconciled_by_status(graph, tmp_path):
    graph.finish_status = 503
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    saved = []
    pub = SimpleNamespace(upload_state={}, description="", title="", tags=[])
    tokens = Tokens("u", extra={"page_id": "111", "page_token": "page-a"})
    res = facebook.upload(pub, tokens, video, lambda st: saved.append(dict(st)))
    assert res.remote_status == {"phase": "finish_unconfirmed"}
    pub.upload_state = saved[-1]
    assert facebook.status(pub, tokens)["state"] == "published"
    graph.status = {"video_status": "error", "processing_phase": {"status": "error"}}
    assert facebook.status(pub, tokens) == {"state": "failed", "failure": "rejected_by_platform",
                                            "detail": "o Facebook recusou o vídeo (error)"}


def test_invalid_page_token_is_an_auth_error(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(400, json={"error": {"code": 190, "message": "Error validating access token"}})
    monkeypatch.setattr(facebook, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(PublishError) as err:
        facebook.upload(SimpleNamespace(upload_state={}, description="", title="", tags=[]),
                        Tokens("u", extra={"page_id": "111", "page_token": "bad"}), video, lambda st: None)
    assert err.value.auth is True
