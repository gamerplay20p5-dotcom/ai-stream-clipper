"""Phase 6B: structural editor - faithful structures, pause removal, protected reactions."""

from __future__ import annotations

from clipper.ai import structure as st


def W(*spans):
    """Words from (start, end, text) or (start, end)."""
    return [(a, b, t[0] if t else "fala") for a, b, *t in spans]


# a 40 s clip: slow talk with long pauses, peak at 30, laughter 30-33
WORDS = W((1, 1.5), (1.6, 2.0, "então."), (6, 6.4), (6.5, 7.0, "olha."), (14, 14.4), (14.5, 15.0, "vem."),
          (22, 22.4), (22.5, 23.0), (27.0, 27.4), (27.5, 28.0), (28.1, 28.6, "agora!"), (33.5, 34.0), (34.1, 34.6, "kkk."))


def M(**kw):
    base = dict(start=0.0, end=40.0, peak=30.0, words=WORDS, reactions=[(30.0, 33.0)], media=[(-120.0, 120.0)])
    base.update(kw)
    return st.Moment(**base)


def test_pause_removal_never_touches_reactions_peak_or_ending():
    m = M()
    p = st.build("D", m)
    assert p.applicable and len(p.segments) >= 3
    for a, b in st.protected_intervals(m.peak, m.end, m.reactions):
        mid = (a + b) / 2
        assert any(s.a <= mid <= s.b for s in p.segments), (a, b)
    met = st.metrics(p, m)
    orig = st.metrics(st.build("O", m), m)
    assert met["duration"] < orig["duration"] - 10 and met["dead_ratio"] < orig["dead_ratio"]
    assert met["reactions_kept"] and met["chronological"] and met["words_kept"] == met["words_in_span"]


def test_good_event_with_bad_opening_gets_a_hook_near_the_action():
    m = M()
    a = st.build("A", m)
    assert a.applicable and a.segments[0].a >= 20  # starts at the sentence leading to the peak
    assert st.metrics(a, m)["time_to_peak"] < st.metrics(st.build("O", m), m)["time_to_peak"] - 15


def test_reaction_humor_keeps_the_line_and_the_whole_laugh():
    m = M()
    c = st.build("C", m)
    assert c.applicable and c.segments[-1].b >= 33.0 + 1.5  # laugh end + 1.5 s
    assert st.metrics(c, m)["reactions_kept"]


def test_suspense_needs_a_long_situation_and_stays_chronological():
    short = st.build("B", M(story={"start": 20.0, "kind": "police_chase"}))
    assert not short.applicable and "situação" in short.reason
    m = M(start=-60.0, story={"start": -60.0, "kind": "police_chase"},
          words=W((-60, -59.5, "polícia"), (-59.4, -58.8, "atrás."), *[(x, x + 0.4) for x in range(-50, 40, 3)]))
    b = st.build("B", m)
    assert b.applicable and b.segments[0].role == "intro" and b.segments[0].a == -60.0
    assert st.metrics(b, m)["chronological"]


def test_teaser_is_explicit_and_the_real_sequence_stays_in_order():
    m = M()
    assert not any(s.role == "teaser" for s in st.build("A", m, teaser=True).segments)  # A already starts near it
    p = st.build("D", m, teaser=True)
    assert p.segments[0].role == "teaser" and any("Daqui a pouco" in n for n in p.notes)
    met = st.metrics(p, m)
    assert met["teaser"] and met["chronological"]
    # captions follow the chronological occurrence, not the teaser
    assert st.map_time(p.segments, 30.2) > p.segments[0].dur


def test_quick_moment_refuses_situations_that_need_context():
    e = st.build("E", M(story={"start": -40.0, "kind": "shootout"}))
    assert not e.applicable
    ok = st.build("E", M(story=None, start=24.0))
    assert ok.applicable and ok.duration <= 12


def test_segments_outside_the_recording_are_not_applicable():
    m = M(story={"start": -30.0, "kind": "conflict"}, media=[(0.0, 40.0)])
    d = st.build("D", m)
    assert not d.applicable and "gravad" in d.reason


def test_choice_is_explained_and_configurable():
    assert st.choose(M(), kinds=["laughter"])[0] == "C"
    assert st.choose(M(story={"start": -60.0, "kind": "police_chase"}))[0] == "B"
    assert st.choose(M(story={"start": 10.0, "kind": "police_chase"}))[0] == "D"
    key, why = st.choose(M(story={"start": 10.0, "kind": "police_chase"}), prefs={"police_chase": "A"})
    assert key == "A" and "preferência" in why


def test_original_is_preserved_when_a_different_structure_is_not_meaningful():
    tight = W(*[(x * 0.5, x * 0.5 + 0.4) for x in range(0, 30)])  # continuous speech, no pauses
    m = M(start=0.0, end=15.0, peak=6.0, words=tight, reactions=[])
    o, d = st.metrics(st.build("O", m), m), st.metrics(st.build("D", m), m)
    assert not st.meaningful(o, d)  # nothing to gain: the original stays as it is


def test_map_time_drops_removed_moments():
    segs = [st.Seg(0, 5), st.Seg(10, 15)]
    assert st.map_time(segs, 2) == 2 and st.map_time(segs, 12) == 7 and st.map_time(segs, 7) is None


# ------------------------------------------------------------------ PR #2 review: 5 and 6
def test_review_reactions_have_explicit_denominators_and_bound_drops_are_visible():
    # a laugh at 4-6 s (before the hook) and the main one at 30-33 s
    m = M(reactions=[(4.0, 6.0), (30.0, 33.0)])
    a = st.build("A", m)
    met = st.metrics(a, m)
    assert met["reactions_original"] == 2 and met["reactions_preserved"] == 1
    assert met["reactions_dropped_by_bounds"] == 1 and met["reactions_dropped_inside"] == 0
    assert met["reactions_in_selection"] == 1 and met["reactions_preserved_in_selection"] == 1
    assert met["reactions_kept"] is False  # the early laugh was left out by the new start: never hidden
    d = st.metrics(st.build("D", m), m)
    assert d["reactions_preserved"] == d["reactions_original"] == 2 and d["reactions_kept"]


def test_review_teaser_never_breaks_the_duration_limit():
    plain = st.build("D", M()).duration
    m = M(max_len=plain + 1.0)  # the structure fits, the structure + a 2.5 s teaser does not
    p = st.build("D", m, teaser=True)
    assert p.applicable and not any(s.role == "teaser" for s in p.segments)
    assert any("limite" in n for n in p.notes) and p.duration <= m.max_len + 0.5
    roomy = st.build("D", M(max_len=plain + 10), teaser=True)
    assert roomy.segments[0].role == "teaser" and roomy.duration <= plain + 10.5


def test_review_segment_boundaries_never_cut_a_word():
    words = W((10.0, 11.0, "palavra"), (11.1, 12.5, "longa"), (20.0, 20.6, "fim."))
    segs = st.snap_to_words([st.Seg(10.5, 12.0), st.Seg(19.8, 20.3)], words)
    assert segs[0].a <= 10.0 and segs[0].b >= 12.5 and segs[1].b >= 20.6
    m = M()
    for key in "ABCDE":
        p = st.build(key, m)
        if p.applicable:
            assert st.metrics(p, m)["words_cut_at_boundaries"] == 0, key


def test_review_context_loss_is_reported_with_the_text():
    m = M()
    met = st.metrics(st.build("A", m), m)
    assert met["context_lost_before_s"] > 15 and "então." in met["context_lost_text"]
    assert met["words_dropped_vs_original"] > 0


def test_review_captions_stop_at_the_transition():
    from clipper.render.renderer import RenderSpec, remap_for_segments
    from clipper.render.subtitles import SubWord
    from pathlib import Path

    spec = RenderSpec(source=Path("x.mp4"), source_start=0, start=0, end=20, peak=5,
                      words=[SubWord(4.5, 6.0, "atravessa")])  # word runs past the end of segment 1 (5.0)
    _s, _p, (words, _notes) = remap_for_segments(spec, {}, [{"a": 0, "b": 5.0, "role": "main"},
                                                           {"a": 10, "b": 20, "role": "main"}])
    assert words[0].start == 4.5 and words[0].end <= 5.0  # never shown over the next part
