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
