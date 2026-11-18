import time

import pytest

from clipper.platforms import InvalidStreamURL, parse_file_source, parse_stream_url
from clipper.security import hash_password, sign_request, verify_password, verify_signature


@pytest.mark.parametrize("url,platform,channel", [
    ("https://www.twitch.tv/Gaules", "twitch", "gaules"),
    ("twitch.tv/some_streamer", "twitch", "some_streamer"),
    ("https://m.twitch.tv/abc_def/", "twitch", "abc_def"),
    ("https://www.youtube.com/@CazeTV", "youtube", "@cazetv"),
    ("https://www.youtube.com/@CazeTV/live", "youtube", "@cazetv"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "v:dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "youtube", "v:dQw4w9WgXcQ"),
    ("https://www.youtube.com/channel/UC1234567890123456789012", "youtube", "UC1234567890123456789012"),
])
def test_parse_valid_urls(url, platform, channel):
    ref = parse_stream_url(url, resolve_dns=False)
    assert ref.platform == platform
    assert ref.channel == channel
    assert ref.url.startswith("https://www.")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/stream.m3u8",
    "http://192.168.1.210:8780/internal/jobs",
    "https://evil.example.com/twitch.tv/gaules",
    "https://twitch.tv.evil.com/gaules",
    "file:///etc/passwd",
    "ftp://twitch.tv/x",
    "https://user:pass@twitch.tv/gaules",
    "https://twitch.tv:8443/gaules",
    "https://www.twitch.tv/directory",
    "https://www.twitch.tv/videos/123456",
    "https://www.twitch.tv/gaules/clip/abc",
    "https://www.youtube.com/results?search_query=x",
    "",
    "x" * 600,
])
def test_parse_rejects_unsafe_or_unsupported(url):
    with pytest.raises(InvalidStreamURL):
        parse_stream_url(url, resolve_dns=False)


def test_file_source_is_confined(tmp_path):
    (tmp_path / "ok.mp4").write_bytes(b"x")
    assert parse_file_source("file://ok.mp4", tmp_path).channel == "ok.mp4"
    for bad in ("../secret.mp4", "..\\x.mp4", "/etc/passwd", "sub/../../x.mp4", "missing.mp4"):
        with pytest.raises(InvalidStreamURL):
            parse_file_source(bad, tmp_path)


def test_password_hashing():
    h = hash_password("s3cret-password")
    assert verify_password(h, "s3cret-password")
    assert not verify_password(h, "wrong")
    assert not verify_password("", "x")


def test_signature_roundtrip_and_tampering():
    secret = "k" * 32
    sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    h = sign_request(secret, "w1", "POST", "/internal/jobs/claim", sha)
    assert verify_signature(secret, h, "POST", "/internal/jobs/claim", sha).ok
    assert not verify_signature(secret, h, "POST", "/internal/jobs/claim?x=1", sha).ok  # path tamper
    assert not verify_signature(secret, h, "GET", "/internal/jobs/claim", sha).ok  # method tamper
    assert not verify_signature("other" * 8, h, "POST", "/internal/jobs/claim", sha).ok  # wrong key
    assert not verify_signature(secret, h, "POST", "/internal/jobs/claim", "0" * 64).ok  # body tamper
    old = dict(h)
    old["X-ASC-Timestamp"] = str(int(time.time()) - 3600)
    assert not verify_signature(secret, old, "POST", "/internal/jobs/claim", sha).ok  # replay window
    assert not verify_signature("", h, "POST", "/internal/jobs/claim", sha).ok  # no secret configured
