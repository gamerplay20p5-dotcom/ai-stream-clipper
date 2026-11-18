"""Phase 5 stage E: speaker-aware captions, branding, caption correction and per-platform metadata."""

from __future__ import annotations

from clipper import pipeline
from clipper.ai.narrative import PROMPT, _facts, clean_tags
from clipper.ai.schemas import DirectorDecision
from clipper.db import session_scope
from clipper.models import Clip, SituationRow, SpeakerProfile, Streamer, StreamSession, TranscriptWord
from clipper.publish.service import suggest_metadata
from clipper.render.renderer import caption_options
from clipper.render.subtitles import SAFE_SIDE, SAFE_TOP_CAPTIONS, CaptionOptions, SubWord, build_ass, group_words
from clipper.settings_store import load_ai_settings, save_ai_settings
from conftest import CSRF

WORDS = [SubWord(0.0, 0.4, "Corre,", "streamer"), SubWord(0.5, 0.9, "corre!", "streamer"),
         SubWord(1.0, 1.4, "Tô", "other:1"), SubWord(1.5, 1.9, "chegando.", "other:1"),
         SubWord(2.0, 2.4, "Valeu...", "streamer")]


def _dialogues(ass: str, style: str) -> list[str]:
    return [l for l in ass.splitlines() if l.startswith("Dialogue:") and f",{style}," in l]


def test_groups_never_mix_the_streamer_with_another_voice():
    groups = group_words(WORDS, max_words=5, max_chars=80)
    for g in groups:
        assert len({w.speaker.startswith("other:") for w in g}) == 1


def test_other_voices_are_smaller_and_coloured_and_hidden_only_when_confirmed():
    ass = build_ass(WORDS, duration=10)
    assert _dialogues(ass, "Other") and _dialogues(ass, "Cap")
    cap_size = int([l for l in ass.splitlines() if l.startswith("Style: Cap,")][0].split(",")[2])
    other_size = int([l for l in ass.splitlines() if l.startswith("Style: Other,")][0].split(",")[2])
    assert other_size < cap_size
    # streamer_only with a mere hypothesis: nothing is hidden (a wrong guess must not erase speech)
    assert _dialogues(build_ass(WORDS, duration=10, opts=CaptionOptions(mode="streamer_only")), "Other")
    hidden = build_ass(WORDS, duration=10, opts=CaptionOptions(mode="streamer_only", streamer_confirmed=True))
    assert not _dialogues(hidden, "Other") and "CHEGANDO" not in hidden
    same = build_ass(WORDS, duration=10, opts=CaptionOptions(mode="all"))
    assert not _dialogues(same, "Other") and "CHEGANDO" in same


def test_faithful_text_short_video_punctuation_and_emphasis():
    ass = build_ass(WORDS, duration=10)
    assert "CORRE " in ass or "CORRE{" in ass or "CORRE\\" in ass  # trailing comma dropped
    assert "CORRE!" in ass and "VALEU..." in ass and "CHEGANDO." not in ass


def test_safe_areas_font_size_position_and_animation():
    style = [l for l in build_ass(WORDS, opts=CaptionOptions(position="high", size_pct=150, font="Comic Sans")).splitlines()
             if l.startswith("Style: Cap,")][0].split(",")
    assert style[1] != "Comic Sans"  # only installed fonts
    assert int(style[2]) == 126  # 84 * 1.5
    assert int(style[19]) >= SAFE_SIDE and int(style[20]) >= SAFE_SIDE
    assert int(style[21]) <= SAFE_TOP_CAPTIONS
    plain = [SubWord(0.0, 0.3, "vem"), SubWord(0.35, 0.6, "aqui"), SubWord(0.65, 0.9, "agora")]
    static = build_ass(plain, duration=10, opts=CaptionOptions(animation="none"))
    assert len(_dialogues(static, "Cap")) == 1 < len(_dialogues(build_ass(plain, duration=10), "Cap"))


def test_branding_watermark_and_end_card():
    ass = build_ass(WORDS, duration=20, opts=CaptionOptions(watermark="Malaio Cortes 🔥", cta="Siga para mais cortes",
                                                           cta_seconds=3))
    mark = _dialogues(ass, "Mark")
    assert mark and "Malaio Cortes" in mark[0] and "🔥" not in mark[0]
    cta = _dialogues(ass, "Cta")
    assert cta and "0:00:17.00" in cta[0]
    assert not build_ass(WORDS, duration=20).count(",Mark,,")  # nothing added unless configured


def test_renderer_ignores_unknown_caption_keys():
    o = caption_options({"mode": "all", "from_the_future": 1})
    assert o.mode == "all"


def _clip(tag: str) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"cap_{tag}", url=f"https://www.twitch.tv/cap_{tag}", display_name="Malaio")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        for t, w, sp in ((11.0, "olha", "streamer"), (12.0, "isso", "streamer"), (13.0, "câmbio", "other:1")):
            db.add(TranscriptWord(session_id=sess.id, segment_idx=1, start=t, end=t + 0.4, word=w, speaker=sp))
        sit = SituationRow(session_id=sess.id, kind="police_chase", start=5.0, last=30.0, status="closed",
                           confidence=0.8, summary="fuga", data={})
        db.add(sit)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=10.0, end=30.0, peak=15.0, score=70, status="ready",
                 title="A fuga", description="Fugiu da polícia.", hashtags=["gtarp"], situation_id=sit.id,
                 source_path="clips/x/source.mp4", source_start=0.0, source_duration=40.0)
        db.add(c)
        db.flush()
        return c.id


def test_render_spec_carries_speakers_and_caption_options(client):
    cid = _clip("spec")
    with session_scope() as db:
        spec = pipeline.render_spec(db, cid)
        assert [w[3] for w in spec["words"]] == ["streamer", "streamer", "other:1"]
        assert spec["captions"]["mode"] == "streamer_highlight" and spec["captions"]["streamer_confirmed"] is False
        clip = db.get(Clip, cid)
        db.add(SpeakerProfile(streamer_id=clip.streamer_id, centroid=[0.1] * 256, n=3, confirmed=True,
                              source="confirmed_cluster", sample={}))
        db.flush()
        assert pipeline.render_spec(db, cid)["captions"]["streamer_confirmed"] is True


def test_caption_correction_overrides_the_transcript_and_can_be_reset(admin):
    cid = _clip("edit")
    got = admin.get(f"/api/clips/{cid}/captions").json()
    assert [w["speaker"] for w in got["words"]] == ["streamer", "streamer", "other:1"] and not got["override"]
    before = admin.get(f"/api/clips/{cid}").json()["version"]
    words = got["words"]
    words[2] = {**words[2], "text": "câmbio!", "speaker": "streamer"}
    r = admin.put(f"/api/clips/{cid}/captions", json={"words": words}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == before + 1  # re-rendered with the corrected captions
    with session_scope() as db:
        spec = pipeline.render_spec(db, cid)
        assert spec["words"][2][2:] == ["câmbio!", "streamer"]
    bad = [{**words[0], "start": 999.0}]
    assert admin.put(f"/api/clips/{cid}/captions", json={"words": bad}, headers=CSRF).status_code == 422
    assert admin.put(f"/api/clips/{cid}/captions", json={"words": [{**words[0], "speaker": "dono"}]},
                     headers=CSRF).status_code == 422
    assert admin.delete(f"/api/clips/{cid}/captions", headers=CSRF).status_code == 200
    assert admin.get(f"/api/clips/{cid}/captions").json()["override"] is False


def test_metadata_per_platform_uses_story_and_branding(client):
    cid = _clip("meta")
    with session_scope() as db:
        s = load_ai_settings(db)
        s.brand_hashtags, s.brand_description_footer = ["malaiocortes"], "Lives toda noite"
        save_ai_settings(db, s)
    try:
        with session_scope() as db:
            clip = db.get(Clip, cid)
            yt, ig, tt = (suggest_metadata(db, clip, p) for p in ("youtube", "instagram", "tiktok"))
    finally:
        with session_scope() as db:
            s = load_ai_settings(db)
            s.brand_hashtags, s.brand_description_footer = [], ""
            save_ai_settings(db, s)
    assert "#perseguicao" in yt["description"] and "#malaiocortes" in yt["description"] and "#Shorts" in yt["description"]
    assert "Lives toda noite" in yt["description"] and "Lives toda noite" in ig["description"]
    assert ig["description"].startswith("A fuga")  # the line shown under the Reel
    assert "Fugiu da polícia" not in tt["description"] and len(tt["description"]) < len(yt["description"])


def test_narrative_prompt_separates_the_streamer_from_other_voices():
    d = DirectorDecision(start_abs=0, end_abs=10, peak_abs=5, score=60, base_score=60, keep=True, mode="audio_only")
    f = _facts("eu vou fugir", None, d, others="parado polícia", story="perseguição desde 2 min")
    prompt = PROMPT.format(game="GTA", **f)
    assert 'O streamer disse: "eu vou fugir"' in prompt and "parado polícia" in prompt and "perseguição" in prompt
    assert clean_tags(["#GTA RP", "perseguição", "x", "análise", "gtarp"]) == ["GTARP", "perseguição"]
