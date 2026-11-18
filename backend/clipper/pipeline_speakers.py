"""Who is talking in a session (Phase 5, stage E): store speech turns, label them, label words.

Labels: ``streamer`` | ``other:<n>`` | ``unknown`` | ``overlap`` (two voices inside one turn).

* Profile per streamer (:class:`SpeakerProfile`). Without one, every voice is clustered as
  ``other:<n>``; after ``HYPOTHESIS_MIN_S`` of speech the dominant cluster becomes a HYPOTHESIS
  (``confirmed=False``) - never the loudest voice, the one with the most speech time, and only when
  it clearly dominates. The owner confirms it (or picks another cluster) in the panel.
* Uncertain turns stay ``unknown``: a wrong label is worse than no label.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .ai import speaker as spk
from .models import SpeakerProfile, SpeechTurn, StreamSession, TranscriptWord

log = logging.getLogger(__name__)

HYPOTHESIS_MIN_S = 300.0  # speech seconds in the session before guessing the streamer's voice
HYPOTHESIS_SHARE = 0.40  # the guessed cluster must hold at least this share of the clustered speech
LABELS_SHOWN = ("streamer", "unknown", "overlap")


# ------------------------------------------------------------------ state helpers
def _state(sess: StreamSession) -> dict:
    st = dict((sess.stats or {}).get("speakers") or {})
    st.setdefault("clusters", [])
    return st


def _save_state(sess: StreamSession, st: dict) -> None:
    sess.stats = {**(sess.stats or {}), "speakers": st}  # reassign: plain JSON column


def profile_for(db: Session, streamer_id: int) -> SpeakerProfile | None:
    p = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == streamer_id))
    return p if p is not None and p.centroid else None


def _rejected(db: Session, streamer_id: int) -> list[list]:
    p = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == streamer_id))
    return list((p.sample or {}).get("rejected") or []) if p is not None else []


# ------------------------------------------------------------------ labelling
def _label_turn(embs: list, profile: list | None, clusters: list[dict]) -> tuple[str, float, list[str], list[dict]]:
    """(turn label, confidence, per-half labels or [], clusters)."""
    label, conf, clusters = spk.assign(embs[0], profile, clusters)
    halves: list[str] = []
    if len(embs) == 3 and profile is not None:
        s1, s2 = spk.cosine(embs[1], profile), spk.cosine(embs[2], profile)
        if max(s1, s2) >= spk.STREAMER_SIM and min(s1, s2) <= spk.OTHER_SIM:
            # one half is clearly the streamer, the other clearly someone else: two voices in one turn
            other = spk.classify(embs[2] if s1 > s2 else embs[1], profile, clusters)[0]
            halves = ["streamer", other] if s1 > s2 else [other, "streamer"]
            label, conf = "overlap", round(min(max(s1, s2), 1.0 - min(s1, s2)), 3)
    return label, conf, halves, clusters


def _word_label(t: float, turns: list[tuple[float, float, str, list[str]]]) -> str:
    for a, b, label, halves in turns:
        if a - 0.05 <= t <= b + 0.05:
            if halves and label == "overlap":
                return halves[0] if t < (a + b) / 2 else halves[1]
            return label
    return "unknown"


def _label_words(db: Session, session_id: int, a: float, b: float,
                 turns: list[tuple[float, float, str, list[str]]]) -> None:
    rows = db.scalars(select(TranscriptWord).where(TranscriptWord.session_id == session_id,
                                                   TranscriptWord.start >= a - 0.1,
                                                   TranscriptWord.start < b + 0.1)).all()
    for w in rows:
        w.speaker = _word_label((w.start + w.end) / 2, turns)


def store_turns(db: Session, sess: StreamSession, idx: int, seg_start: float, turns: list[dict]) -> None:
    """Called by post_analysis (words of this segment are already flushed)."""
    db.query(SpeechTurn).filter(SpeechTurn.session_id == sess.id, SpeechTurn.segment_idx == idx).delete()
    if not turns:
        return
    st = _state(sess)
    prof = profile_for(db, sess.streamer_id)
    centroid = prof.centroid if prof is not None else None
    clusters = st["clusters"]
    labelled = []
    for t in turns:
        embs = [e for e in (t.get("emb") or []) if isinstance(e, list) and len(e) == 256]
        if not embs:
            continue
        if len(embs) not in (1, 3):
            embs = embs[:1]
        a, b = seg_start + float(t["start"]), seg_start + float(t["end"])
        label, conf, halves, clusters = _label_turn(embs, centroid, clusters)
        if label.startswith("other:"):
            cid = int(label.split(":")[1])
            for c in clusters:
                if c["id"] == cid:
                    c["seconds"] = round(c.get("seconds", 0.0) + (b - a), 1)
        st["seconds_" + ("streamer" if label == "streamer" else "other")] = round(
            st.get("seconds_" + ("streamer" if label == "streamer" else "other"), 0.0) + (b - a), 1)
        db.add(SpeechTurn(session_id=sess.id, segment_idx=idx, start=round(a, 2), end=round(b, 2), emb=embs,
                          label=label, confidence=conf, text=str(t.get("text") or "")[:400]))
        labelled.append((a, b, label, halves))
    st["clusters"] = clusters
    _save_state(sess, st)
    db.flush()
    if labelled:
        _label_words(db, sess.id, labelled[0][0], labelled[-1][1], labelled)
    if prof is None:
        maybe_hypothesis(db, sess)


def maybe_hypothesis(db: Session, sess: StreamSession) -> bool:
    """Guess the streamer's voice: the cluster with the most speech time, if it clearly dominates."""
    st = _state(sess)
    clusters = st["clusters"]
    total = sum(c.get("seconds", 0.0) for c in clusters)
    if total < HYPOTHESIS_MIN_S or not clusters:
        return False
    rejected = _rejected(db, sess.streamer_id)
    ranked = sorted(clusters, key=lambda c: -c.get("seconds", 0.0))
    for c in ranked:
        if any(spk.cosine(c["centroid"], r) >= spk.CLUSTER_SIM for r in rejected):
            continue  # the owner already said this voice is not the streamer
        if c.get("seconds", 0.0) / total < HYPOTHESIS_SHARE:
            return False
        _set_profile(db, sess.streamer_id, c["centroid"], c.get("n", 1), confirmed=False, source="auto",
                     sample={"session_id": sess.id, "cluster": c["id"]})
        log.info("streamer voice hypothesis", extra={"session_id": sess.id, "cluster": c["id"],
                                                     "share": round(c["seconds"] / total, 2)})
        relabel_session(db, sess.id)
        return True
    return False


def _set_profile(db: Session, streamer_id: int, centroid: list, n: int, *, confirmed: bool, source: str,
                 sample: dict) -> SpeakerProfile:
    p = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == streamer_id))
    if p is None:
        p = SpeakerProfile(streamer_id=streamer_id)
        db.add(p)
    keep = {"rejected": (p.sample or {}).get("rejected", [])} if p.sample else {}
    p.centroid, p.n, p.confirmed, p.source, p.sample = list(centroid), int(n), confirmed, source, {**keep, **sample}
    db.flush()
    return p


def relabel_session(db: Session, session_id: int) -> dict:
    """Re-assign every turn of the session with the current profile (fresh clusters), then the words."""
    sess = db.get(StreamSession, session_id)
    if sess is None:
        return {}
    prof = profile_for(db, sess.streamer_id)
    centroid = prof.centroid if prof is not None else None
    clusters: list[dict] = []
    turns = db.scalars(select(SpeechTurn).where(SpeechTurn.session_id == session_id).order_by(SpeechTurn.start)).all()
    labelled = []
    secs = {"streamer": 0.0, "other": 0.0}
    for t in turns:
        embs = t.emb if len(t.emb) in (1, 3) else t.emb[:1]
        label, conf, halves, clusters = _label_turn(embs, centroid, clusters)
        if label.startswith("other:"):
            cid = int(label.split(":")[1])
            for c in clusters:
                if c["id"] == cid:
                    c["seconds"] = round(c.get("seconds", 0.0) + (t.end - t.start), 1)
        secs["streamer" if label == "streamer" else "other"] += t.end - t.start
        t.label, t.confidence = label, conf
        labelled.append((t.start, t.end, label, halves))
    _save_state(sess, {"clusters": clusters, "seconds_streamer": round(secs["streamer"], 1),
                       "seconds_other": round(secs["other"], 1)})
    db.execute(update(TranscriptWord).where(TranscriptWord.session_id == session_id, TranscriptWord.speaker != "")
               .values(speaker="unknown"))  # "" = analysed before diarization existed: left alone
    db.flush()
    if labelled:
        _label_words(db, session_id, labelled[0][0], labelled[-1][1], labelled)
    db.flush()
    return {"turns": len(turns), "clusters": len(clusters)}


# ------------------------------------------------------------------ panel
def overview(db: Session, streamer_id: int, session_id: int | None = None) -> dict:
    from .models import Clip

    prof = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == streamer_id))
    if session_id is None:
        session_id = db.scalar(select(SpeechTurn.session_id).join(StreamSession, StreamSession.id == SpeechTurn.session_id)
                               .where(StreamSession.streamer_id == streamer_id)
                               .order_by(SpeechTurn.id.desc()).limit(1))
    groups: dict[str, dict] = {}
    if session_id is not None:
        clips = db.execute(select(Clip.source_start, Clip.source_duration).where(
            Clip.session_id == session_id, Clip.source_path != "")).all()

        def has_audio(a: float, b: float) -> bool:
            return any(s <= a and s + d >= b for s, d in clips)

        for t in db.scalars(select(SpeechTurn).where(SpeechTurn.session_id == session_id)):
            g = groups.setdefault(t.label, {"label": t.label, "seconds": 0.0, "turns": 0, "samples": []})
            g["seconds"] += t.end - t.start
            g["turns"] += 1
            g["samples"].append({"id": t.id, "start": t.start, "end": t.end, "text": t.text,
                                 "confidence": t.confidence, "audio": has_audio(t.start, t.end)})
        for g in groups.values():
            g["seconds"] = round(g["seconds"], 1)
            # best samples: with audio, longer, more words
            g["samples"] = sorted(g["samples"], key=lambda s: (not s["audio"], -(s["end"] - s["start"])))[:3]
    order = sorted(groups.values(), key=lambda g: (g["label"] != "streamer", -g["seconds"]))
    return {
        "streamer_id": streamer_id,
        "session_id": session_id,
        "profile": None if prof is None or not prof.centroid else {
            "confirmed": prof.confirmed, "source": prof.source, "n": prof.n,
            "updated_at": prof.updated_at.isoformat() if prof.updated_at else None},
        "rejected": len((prof.sample or {}).get("rejected") or []) if prof is not None else 0,
        "groups": order,
    }


def _cluster_centroid(db: Session, session_id: int, label: str) -> tuple[list, int] | None:
    embs = [t.emb[0] for t in db.scalars(select(SpeechTurn).where(SpeechTurn.session_id == session_id,
                                                                    SpeechTurn.label == label))]
    if not embs:
        return None
    import numpy as np

    c = np.mean(np.asarray(embs, dtype=np.float32), axis=0)
    c = c / (np.linalg.norm(c) or 1.0)
    return [round(float(v), 4) for v in c], len(embs)


def confirm(db: Session, streamer_id: int, session_id: int, label: str) -> dict:
    """The owner says: the voice of ``label`` in this session IS the streamer."""
    found = _cluster_centroid(db, session_id, label)
    if found is None:
        raise ValueError("no turns with this label in the session")
    centroid, n = found
    _set_profile(db, streamer_id, centroid, n, confirmed=True,
                 source="confirmed_cluster", sample={"session_id": session_id, "label": label})
    return relabel_session(db, session_id)


def reject(db: Session, streamer_id: int, session_id: int, label: str) -> dict:
    """The owner says: this voice is NOT the streamer (drops a wrong hypothesis, remembers it)."""
    found = _cluster_centroid(db, session_id, label)
    if found is None:
        raise ValueError("no turns with this label in the session")
    centroid, _n = found
    p = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == streamer_id))
    if p is None:
        p = SpeakerProfile(streamer_id=streamer_id, centroid=[], n=0, confirmed=False, source="auto", sample={})
        db.add(p)
    rejected = list((p.sample or {}).get("rejected") or [])[-9:] + [centroid]
    if p.centroid and spk.cosine(p.centroid, centroid) >= spk.CLUSTER_SIM:
        p.centroid, p.n, p.confirmed = [], 0, False  # the profile itself was this voice: drop it
    p.sample = {**(p.sample or {}), "rejected": rejected}
    db.flush()
    out = relabel_session(db, session_id)
    sess = db.get(StreamSession, session_id)
    if sess is not None and profile_for(db, streamer_id) is None:
        maybe_hypothesis(db, sess)
    return out
