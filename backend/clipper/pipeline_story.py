"""Phase 5 stage A: keeps the story memory of each live session up to date from DB data.

Called by the scoring path (API side, cheap): for every new scored stretch it builds 10 s bins
(transcript words, YAMNet maxima, acoustic events, visual labels of analysed clips), feeds the
``StoryTracker`` and persists the situations (bounded JSON). Candidates and clips are linked to
the situation that contains their peak.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.story import BIN_S, Bin, Situation, StoryTracker
from .models import AudioFeature, Clip, Event, SituationRow, StreamSession, TranscriptWord

log = logging.getLogger(__name__)

MAX_CATCHUP_S = 900.0  # never rebuild more than 15 min in one call (bounded work)
KEEP_CLOSED = 40  # closed situations kept in the tracker context (older ones stay only in the DB)


def _bins(db: Session, sess_id: int, a: float, b: float) -> list[Bin]:
    n = max(0, int(math.ceil((b - a) / BIN_S)))
    bins = [Bin(a + i * BIN_S, a + (i + 1) * BIN_S) for i in range(n)]
    if not bins:
        return []

    def idx(t: float) -> int | None:
        i = int((t - a) // BIN_S)
        return i if 0 <= i < n else None

    for t, w in db.execute(select(TranscriptWord.start, TranscriptWord.word).where(
            TranscriptWord.session_id == sess_id, TranscriptWord.start >= a, TranscriptWord.start < b)
            .order_by(TranscriptWord.start)):
        i = idx(t)
        if i is not None:
            bins[i].words.append((t, w))
    for f in db.scalars(select(AudioFeature).where(AudioFeature.session_id == sess_id,
                                                   AudioFeature.start < b, AudioFeature.start > a - 15)):
        y = (f.extra or {}).get("yamnet") or {}
        hop = float(y.get("hop", 0.48))
        for cat, vals in (y.get("cats") or {}).items():
            for k, v in enumerate(vals):
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                i = idx(f.start + k * hop + hop)
                if i is not None and v > bins[i].yam.get(cat, 0.0):
                    bins[i].yam[cat] = float(v)
    for e in db.scalars(select(Event).where(Event.session_id == sess_id, Event.t >= a, Event.t < b)):
        i = idx(e.t)
        if i is not None:
            bins[i].kinds.append((e.t, e.kind))
    for c in db.scalars(select(Clip).where(Clip.session_id == sess_id, Clip.end > a, Clip.start < b)):
        vis = ((c.analysis or {}).get("visual") or {})
        for ev in vis.get("events") or []:
            base = (vis.get("window_abs") or [c.start])[0]
            t = base + float(ev.get("t", 0))
            i = idx(t)
            if i is not None and ev.get("label"):
                bins[i].visual.append((t, str(ev["label"])))
    return bins


def load_tracker(db: Session, sess_id: int) -> tuple[StoryTracker, dict[int, SituationRow]]:
    rows = list(db.scalars(select(SituationRow).where(SituationRow.session_id == sess_id,
                                                      SituationRow.status != "discarded")
                           .order_by(SituationRow.start)))
    ongoing = [r for r in rows if r.status == "ongoing"]
    closed = [r for r in rows if r.status != "ongoing"][-KEEP_CLOSED:]
    sits = [Situation.from_dict({**(r.data or {}), "id": r.id, "status": r.status}) for r in closed + ongoing]
    sits.sort(key=lambda s: s.start)
    return StoryTracker(sits), {r.id: r for r in rows}


def save_tracker(db: Session, sess_id: int, tracker: StoryTracker, rows: dict[int, SituationRow]) -> None:
    alive = {s.id for s in tracker.situations if s.id is not None}
    for rid, row in rows.items():  # provisional stories that ended unconfirmed
        if row.status == "ongoing" and rid not in alive:
            row.status = "discarded"
    for s in tracker.situations:
        d = s.as_dict()
        d["name_counts_raw"] = dict(list(s.name_counts.items())[:40])
        row = rows.get(s.id) if s.id is not None else None
        if row is None:
            row = SituationRow(session_id=sess_id, kind=s.kind, start=s.start, last=s.last)
            db.add(row)
            db.flush()
            s.id = row.id
            rows[row.id] = row
            d["id"] = row.id
        row.kind, row.start, row.last, row.status = s.kind, s.start, s.last, s.status
        row.confidence, row.summary, row.related_to = s.confidence, s.summary(), s.related_to
        row.data = d


def update_story(db: Session, sess: StreamSession, until: float) -> StoryTracker:
    """Advance the story memory of ``sess`` up to ``until`` (session seconds). Bounded and idempotent."""
    import time as _t

    t0 = _t.perf_counter()
    st = dict(sess.stats or {})
    done = float(st.get("story_until", 0.0))
    tracker, rows = load_tracker(db, sess.id)
    if until - done > MAX_CATCHUP_S:  # long gap (first run on an old session): only the recent part
        done = until - MAX_CATCHUP_S
    if until > done + 1e-6:
        for b in _bins(db, sess.id, done, until):
            tracker.feed(b)
        save_tracker(db, sess.id, tracker, rows)
        st["story_until"] = round(until, 2)
        st["situations_open"] = sum(1 for s in tracker.situations if s.status == "ongoing")
        ms = round((_t.perf_counter() - t0) * 1000, 1)
        st["story_ms_last"] = ms
        st["story_ms_max"] = max(ms, float(st.get("story_ms_max", 0.0)))
        sess.stats = st
    return tracker


def plan_cut(db: Session, sess_id: int, sit: Situation | None, start: float, end: float, peak: float,
             max_story: float) -> tuple[str, float, float, Clip | None]:
    """Decide how a new accepted candidate relates to its story:
    isolated | story (context extended to the situation start) | merge (into a not-yet-rendered clip of the
    same situation) | second_cut (same situation, clearly later moment) | duplicate (same moment again).
    Never merges only because two moments are close in time: they must share the situation."""
    if sit is None or sit.id is None:
        return "isolated", start, end, None
    others = list(db.scalars(select(Clip).where(Clip.session_id == sess_id, Clip.situation_id == sit.id,
                                                Clip.is_test.is_(False)).order_by(Clip.start)))
    for o in others:
        if o.status in ("candidate", "analyzing", "waiting") and end - o.start <= max_story:
            return "merge", min(o.start, start), max(o.end, end), o
    for o in others:
        if min(o.end, end) - max(o.start, start) > 0.35 * (end - start):
            return "duplicate", start, end, o
    new_start = start
    if sit.start < start - 1.0 and (end - sit.start) <= max_story:
        new_start = max(0.0, sit.start - 1.0)  # the minimum needed: from where the situation begins
    action = "second_cut" if others else ("story" if new_start < start else "isolated")
    return action, new_start, end, (others[-1] if others else None)


def story_context(db: Session, clip: Clip, window: float = 240.0) -> dict:
    """Bounded context for the VLM/director: the clip's situation and what happened before it."""
    rows = list(db.scalars(select(SituationRow).where(
        SituationRow.session_id == clip.session_id, SituationRow.status != "discarded",
        SituationRow.last >= clip.start - window,
        SituationRow.start <= clip.end + 30).order_by(SituationRow.start)))
    main = db.get(SituationRow, clip.situation_id) if clip.situation_id else None
    return {
        "situation": None if main is None else {
            "id": main.id, "kind": main.kind, "start": main.start, "last": main.last, "status": main.status,
            "summary": main.summary, "confidence": main.confidence,
            "setup": [e for e in (main.data or {}).get("events", []) if e.get("phase") == "setup"][:3],
            "climax_t": (main.data or {}).get("climax_t"), "complete": (main.data or {}).get("complete"),
            "outcome": (main.data or {}).get("outcome", ""), "changes": (main.data or {}).get("changes", [])[:5],
            "participants": (main.data or {}).get("participants", []),
            "uncertain": (main.data or {}).get("uncertain", [])[:4]},
        "before": [r.summary for r in rows if main is None or r.id != main.id][-4:],
    }
