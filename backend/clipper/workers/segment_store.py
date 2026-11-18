"""Local, persistent index of captured segments (one JSONL file per session)."""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class LocalSegment:
    idx: int
    run_id: int
    start: float
    duration: float
    file: str
    size: int
    sha256: str

    @property
    def end(self) -> float:
        return self.start + self.duration


class SegmentStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[int, dict[int, LocalSegment]] = {}

    def session_dir(self, session_id: int) -> Path:
        d = self.root / f"session_{int(session_id)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _index_file(self, session_id: int) -> Path:
        return self.session_dir(session_id) / "index.jsonl"

    def _load(self, session_id: int) -> dict[int, LocalSegment]:
        if session_id in self._cache:
            return self._cache[session_id]
        segs: dict[int, LocalSegment] = {}
        f = self.root / f"session_{int(session_id)}" / "index.jsonl"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("removed"):
                    segs.pop(rec["idx"], None)
                else:
                    segs[rec["idx"]] = LocalSegment(**rec)
        self._cache[session_id] = segs
        return segs

    def add(self, session_id: int, seg: LocalSegment) -> None:
        with self._lock:
            self._load(session_id)[seg.idx] = seg
            with open(self._index_file(session_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(seg)) + "\n")

    def remove(self, session_id: int, idxs: list[int]) -> None:
        with self._lock:
            segs = self._load(session_id)
            d = self.session_dir(session_id)
            with open(self._index_file(session_id), "a", encoding="utf-8") as f:
                for i in idxs:
                    seg = segs.pop(i, None)
                    if seg:
                        (d / seg.file).unlink(missing_ok=True)
                    f.write(json.dumps({"idx": i, "removed": True}) + "\n")

    def get(self, session_id: int, idx: int) -> LocalSegment | None:
        with self._lock:
            return self._load(session_id).get(idx)

    def list(self, session_id: int) -> list[LocalSegment]:
        with self._lock:
            return sorted(self._load(session_id).values(), key=lambda s: s.idx)

    def path(self, session_id: int, seg: LocalSegment) -> Path:
        return self.session_dir(session_id) / seg.file

    def covering(self, session_id: int, start: float, end: float) -> list[LocalSegment]:
        """Contiguous chain of segments with the largest overlap with ``[start, end]``."""
        segs = [s for s in self.list(session_id) if s.start < end and s.end > start]
        chains: list[list[LocalSegment]] = []
        for s in segs:
            if chains and s.run_id == chains[-1][-1].run_id and abs(s.start - chains[-1][-1].end) < 0.75 \
                    and s.idx == chains[-1][-1].idx + 1:
                chains[-1].append(s)
            else:
                chains.append([s])
        if not chains:
            return []

        def overlap(ch):
            return max(0.0, min(end, ch[-1].end) - max(start, ch[0].start))

        return max(chains, key=overlap)

    def total_bytes(self) -> int:
        total = 0
        for p in self.root.rglob("*.ts"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total

    def drop_session(self, session_id: int) -> None:
        with self._lock:
            self._cache.pop(session_id, None)
            shutil.rmtree(self.root / f"session_{int(session_id)}", ignore_errors=True)
