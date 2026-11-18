"""Temporal story memory (Phase 5, stage A).

Follows situations that last minutes (a discussion with the police that turns into a chase, a
crash, an escape on foot, a funny ending) from cheap signals already produced by the pipeline:
transcript words, YAMNet categories, acoustic events and - when available - visual summaries.

Design rules:
* pure and deterministic (no DB, no model): the pipeline feeds 10 s bins in time order;
* evidence-based: every phase/change carries a timestamp and its source; uncertain items are
  listed as uncertain instead of being asserted;
* bounded memory: per situation at most ``MAX_EVENTS`` events (first, strongest and latest kept)
  and a summary rebuilt from them, never an ever-growing transcript;
* loudness alone never creates a story (a loud game sound without reaction is "noise").
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..analysis import lexicon

BIN_S = 10.0
MAX_EVENTS = 24
MAX_OPEN = 12

# --- cue lists (accent-free, lower case; matched on normalized token sequences) ---------------
CONFLICT = ("cala a boca", "cala boca", "vai se ferrar", "vai tomar", "para com isso", "nao mexe", "sai daqui",
            "seu idiota", "idiota", "otario", "palhaco", "tu ta maluco", "ta maluco", "qual foi", "qual e a tua",
            "vem pra cima", "bora resolver", "me respeita", "respeita", "discussao", "briga", "shut up",
            "back off", "what's your problem", "fight me")
VEHICLE = ("carro", "moto", "volante", "pista", "estrada", "curva", "freia", "freio", "acelera", "marcha", "pneu",
           "car", "bike", "drive", "driving")
POLICE = ("policia", "policial", "viatura", "pm", "sirene", "cops", "police", "officer", "helicoptero", "blitz",
          "perseguicao", "chase", "atras de nos", "atras da gente", "seguindo a gente", "following us")
CRASH = ("bateu", "batemos", "bati", "capotou", "capotei", "capotamos", "acidente", "crash", "crashed", "explodiu",
         "perda total", "destruiu")
ON_FOOT = ("a pe", "desce do carro", "sai do carro", "pula", "pulei", "corre corre", "esconde", "escondi",
           "se esconde", "on foot", "hide")
ARRESTED = ("preso", "prenderam", "algemado", "algemaram", "me pegaram", "pegaram a gente", "busted", "caught",
            "arrested")
ESCAPED = ("despistei", "despistamos", "escapamos", "escapei", "fugimos", "conseguimos", "lost them", "we made it",
           "got away")
SURPRISE_EXTRA = ("susto", "que susto", "assustei", "me assustou", "jump scare")

GAP = {"police_chase": 45.0, "shootout": 30.0, "conflict": 45.0, "funny_conversation": 30.0, "scare": 15.0}
KIND_PT = {"police_chase": "Perseguição policial", "shootout": "Tiroteio", "conflict": "Discussão/conflito",
           "funny_conversation": "Conversa engraçada", "scare": "Susto"}
# causal links: an earlier situation of kind A can lead to B
CAUSES = {("conflict", "police_chase"), ("conflict", "shootout"), ("police_chase", "shootout"),
          ("shootout", "police_chase"), ("conflict", "conflict")}


def _norm_tokens(words: list[tuple[float, str]]) -> list[tuple[float, str]]:
    return [(t, lexicon.normalize(w)) for t, w in words if lexicon.normalize(w)]


def _find(tokens: list[tuple[float, str]], phrases: tuple[str, ...]) -> list[tuple[float, str]]:
    """(t, phrase) matches; phrases matched on consecutive tokens."""
    out = []
    toks = [w for _, w in tokens]
    maxn = max(len(p.split()) for p in phrases)
    pset = set(phrases)
    i = 0
    while i < len(toks):
        hit = None
        for n in range(min(maxn, len(toks) - i), 0, -1):
            ph = " ".join(toks[i:i + n])
            if ph in pset:
                hit = (tokens[i][0], ph, n)
                break
        if hit:
            out.append(hit[:2])
            i += hit[2]
        else:
            i += 1
    return out


@dataclass
class Bin:
    """Signals of one 10 s slice of the session (all times are session offsets)."""

    t0: float
    t1: float
    words: list[tuple[float, str]] = field(default_factory=list)  # (t, raw word)
    yam: dict = field(default_factory=dict)  # category -> max score in the bin (NaN/absent = unknown)
    kinds: list[tuple[float, str]] = field(default_factory=list)  # acoustic events (t, kind)
    visual: list[tuple[float, str]] = field(default_factory=list)  # visual labels (t, label) when analysed


@dataclass
class BinFacts:
    t0: float
    t1: float
    police: list = field(default_factory=list)
    vehicle_words: list = field(default_factory=list)
    vehicle_sound: float = 0.0
    conflict: list = field(default_factory=list)
    crash: list = field(default_factory=list)
    on_foot: list = field(default_factory=list)
    arrested: list = field(default_factory=list)
    escaped: list = field(default_factory=list)
    laughs: list = field(default_factory=list)
    surprise: list = field(default_factory=list)
    voice: float = 0.0  # laughter/scream/shout from YAMNet
    gunfire: float = 0.0
    impact: float = 0.0
    speech_words: int = 0
    names: list = field(default_factory=list)

    @property
    def reaction(self) -> bool:
        return bool(self.laughs or self.surprise) or self.voice >= 0.3

    @property
    def driving(self) -> bool:
        return self.vehicle_sound >= 0.25 or len(self.vehicle_words) >= 2


_NAME_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,14}$")
_NOT_NAMES = {"Não", "Nao", "Sim", "Mano", "Cara", "Velho", "Bora", "Vai", "Olha", "Ele", "Ela", "Que", "Meu", "Deus",
              "Isso", "Aqui", "Ali", "Então", "Entao", "Porra", "Caralho", "Calma", "Para", "Pode", "Tem", "Vamos",
              "Gente", "Polícia", "Policia", "Hoje", "Agora", "Depois", "Tipo", "Beleza", "Opa", "Ok", "Okay"}


def facts(b: Bin) -> BinFacts:
    toks = _norm_tokens(b.words)
    f = BinFacts(b.t0, b.t1)
    f.police = _find(toks, POLICE) if toks else []
    f.vehicle_words = _find(toks, VEHICLE) if toks else []
    f.conflict = _find(toks, CONFLICT) if toks else []
    f.crash = _find(toks, CRASH) if toks else []
    f.on_foot = _find(toks, ON_FOOT) if toks else []
    f.arrested = _find(toks, ARRESTED) if toks else []
    f.escaped = _find(toks, ESCAPED) if toks else []
    f.surprise = (_find(toks, SURPRISE_EXTRA) if toks else []) + [
        (toks[h[0]][0], h[2]) for h in lexicon.find_hits([w for _, w in toks]) if h[3] in ("surprise", "fear")]
    f.laughs = [(toks[h[0]][0], h[2]) for h in lexicon.find_hits([w for _, w in toks]) if h[3] == "humor"]
    y = b.yam or {}
    f.vehicle_sound = float(y.get("vehicle") or 0.0)
    f.gunfire = float(y.get("gunfire") or 0.0)
    f.impact = float(y.get("game_impact") or 0.0)
    f.voice = max(float(y.get(k) or 0.0) for k in ("laughter", "scream", "shout"))
    if float(y.get("laughter") or 0.0) >= 0.3:
        f.laughs.append((b.t0 + BIN_S / 2, "risada (áudio)"))
    for t, k in b.kinds:
        if k == "laughter":
            f.laughs.append((t, "risada"))
        elif k in ("scream", "silence_break"):
            f.surprise.append((t, "grito" if k == "scream" else "silêncio quebrado"))
    f.speech_words = len(b.words)
    f.names = [w for _, w in b.words if _NAME_RE.match(w.strip(".,!?")) and w.strip(".,!?") not in _NOT_NAMES]
    return f


@dataclass
class StoryEvent:
    t: float
    phase: str  # setup | development | climax | resolution
    label: str
    source: str  # transcript | acoustic | visual
    strength: float = 1.0


@dataclass
class Situation:
    kind: str
    start: float
    last: float
    status: str = "ongoing"  # ongoing | closed
    events: list[StoryEvent] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    modalities: set = field(default_factory=set)
    name_counts: dict = field(default_factory=dict)
    related_to: int | None = None  # index/id of an earlier situation that led to this one
    id: int | None = None
    outcome: str = ""  # arrested | escaped | laughter | none

    def add(self, ev: StoryEvent) -> None:
        if any(abs(e.t - ev.t) < 1.0 and e.label == ev.label for e in self.events):
            return
        self.events.append(ev)
        self.modalities.add(ev.source)
        self.last = max(self.last, ev.t)
        if len(self.events) > MAX_EVENTS:  # bounded: keep first, the strongest and the latest
            first, rest = self.events[0], self.events[1:]
            keep = sorted(rest, key=lambda e: (-e.strength, -e.t))[:MAX_EVENTS // 2]
            latest = sorted(rest, key=lambda e: -e.t)[:MAX_EVENTS // 2 - 1]
            merged = {id(e): e for e in keep + latest}
            self.events = [first] + sorted(merged.values(), key=lambda e: e.t)

    @property
    def phases(self) -> dict:
        out: dict[str, list] = {"setup": [], "development": [], "climax": [], "resolution": []}
        for e in self.events:
            out[e.phase].append(e)
        return out

    @property
    def complete(self) -> bool:
        p = self.phases
        return bool(p["setup"]) and bool(p["climax"]) and bool(p["resolution"])

    @property
    def climax_t(self) -> float | None:
        c = self.phases["climax"]
        return max(c, key=lambda e: e.strength).t if c else None

    @property
    def confidence(self) -> float:
        dur = max(0.0, self.last - self.start)
        conf = 0.25 * len(self.modalities & {"transcript", "acoustic", "visual"}) + min(0.3, dur / 300.0)
        conf += 0.15 if self.complete else 0.0
        return round(min(1.0, conf), 2)

    def summary(self) -> str:
        """Short, rebuilt from events (bounded): never grows with the live."""
        steps = []
        for e in self.events:
            if e.phase != "development" or e.strength >= 1.5:
                if not steps or steps[-1] != e.label:
                    steps.append(e.label)
        txt = f"{KIND_PT.get(self.kind, self.kind)} ({_mmss(self.start)}–{_mmss(self.last)}): " + " → ".join(steps[:8])
        if not self.phases["resolution"] and self.status == "closed":
            txt += " (sem desfecho observado)"
        return txt[:400]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["modalities"] = sorted(self.modalities)
        d["events"] = [asdict(e) for e in self.events]
        d.pop("name_counts", None)
        d.update(summary=self.summary(), confidence=self.confidence, complete=self.complete,
                 climax_t=self.climax_t)
        return d

    @staticmethod
    def from_dict(d: dict) -> Situation:
        s = Situation(kind=d["kind"], start=d["start"], last=d["last"], status=d.get("status", "ongoing"),
                      participants=list(d.get("participants") or []), goals=list(d.get("goals") or []),
                      conflicts=list(d.get("conflicts") or []), changes=list(d.get("changes") or []),
                      uncertain=list(d.get("uncertain") or []), related_to=d.get("related_to"), id=d.get("id"),
                      outcome=d.get("outcome", ""))
        s.events = [StoryEvent(**e) for e in d.get("events") or []]
        s.modalities = set(d.get("modalities") or [])
        s.name_counts = dict(d.get("name_counts_raw") or {})
        return s


def _mmss(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _first(lst):
    return lst[0][0] if lst else None


class StoryTracker:
    """Feed bins in time order; read ``situations`` (open + recently closed)."""

    def __init__(self, situations: list[Situation] | None = None):
        self.situations: list[Situation] = list(situations or [])
        self.noise: list[tuple[float, str]] = []  # loud game sounds without reaction (never a story)
        self.recent: list[BinFacts] = []  # last 6 bins (60 s) for confirmation windows

    # --------------------------------------------------------------------- helpers
    def _open(self, kind: str) -> Situation | None:
        for s in reversed(self.situations):
            if s.kind == kind and s.status == "ongoing":
                return s
        return None

    def _start(self, kind: str, t: float) -> Situation:
        s = Situation(kind=kind, start=t, last=t)
        prev = [o for o in self.situations if (o.kind, kind) in CAUSES and t - o.last <= 180.0 and o is not s]
        if prev:
            s.related_to = prev[-1].id if prev[-1].id is not None else self.situations.index(prev[-1])
            s.changes.append(f"continua {KIND_PT.get(prev[-1].kind, prev[-1].kind).lower()} iniciada em "
                             f"{_mmss(prev[-1].start)}")
        self.situations.append(s)
        if len([o for o in self.situations if o.status == "ongoing"]) > MAX_OPEN:
            oldest = min((o for o in self.situations if o.status == "ongoing"), key=lambda o: o.last)
            oldest.status = "closed"
        return s

    def _window(self, attr: str) -> list:
        return [x for f in self.recent for x in getattr(f, attr)]

    # --------------------------------------------------------------------- main step
    def feed(self, b: Bin) -> BinFacts:
        f = facts(b)
        self.recent = (self.recent + [f])[-6:]
        mid = (b.t0 + b.t1) / 2

        # close situations whose evidence stopped
        for s in self.situations:
            if s.status == "ongoing" and b.t0 - s.last > GAP.get(s.kind, 30.0):
                s.status = "closed"
                if not s.outcome:
                    s.outcome = "none"

        # --- police chase: police/chase cues AND driving evidence inside the last 60 s
        police_win = self._window("police")
        driving_win = any(x.driving for x in self.recent)
        chase = self._open("police_chase")
        if chase is None and police_win and driving_win:
            t = min(t for t, _ in police_win)
            chase = self._start("police_chase", t)
            chase.add(StoryEvent(t, "setup", f"polícia mencionada (“{police_win[0][1]}”)", "transcript", 1.5))
            chase.goals.append("fugir da polícia")
            chase.conflicts.append("jogador × polícia")
        elif chase is None and f.driving and not police_win:
            pass  # just driving: not a story
        if chase is not None:
            if f.police:
                chase.add(StoryEvent(_first(f.police), "development", "perseguição continua", "transcript", 1.0))
            if f.driving:
                chase.add(StoryEvent(mid, "development", "dirigindo em fuga", "acoustic" if f.vehicle_sound >= .25
                                     else "transcript", 0.8))
            if (f.crash or f.impact >= 0.4) and (f.reaction or f.crash):
                t = _first(f.crash) or mid
                chase.add(StoryEvent(t, "climax", "colisão durante a fuga", "transcript" if f.crash else "acoustic", 3.0))
                chase.changes.append(f"colisão em {_mmss(t)}")
            if f.on_foot:
                t = _first(f.on_foot)
                chase.add(StoryEvent(t, "development", "fuga a pé", "transcript", 2.0))
                chase.changes.append(f"passa a fugir a pé em {_mmss(t)}")
            if f.arrested:
                chase.add(StoryEvent(_first(f.arrested), "resolution", "foi pego pela polícia", "transcript", 2.5))
                chase.outcome = "arrested"
            elif f.escaped:
                chase.add(StoryEvent(_first(f.escaped), "resolution", "conseguiu escapar", "transcript", 2.5))
                chase.outcome = "escaped"
            if f.laughs and chase.phases["climax"]:
                chase.add(StoryEvent(f.laughs[0][0], "resolution", "reação engraçada ao final", "transcript", 2.0))
                chase.outcome = chase.outcome or "laughter"

        # --- shootout: gunfire repeated (two bins in 30 s) with tension or reaction
        guns = [x for x in self.recent[-3:] if x.gunfire >= 0.35]
        shoot = self._open("shootout")
        if shoot is None and len(guns) >= 2 and (police_win or any(x.reaction for x in self.recent[-3:])):
            shoot = self._start("shootout", guns[0].t0)
            shoot.add(StoryEvent(guns[0].t0, "setup", "troca de tiros começa", "acoustic", 2.0))
        if shoot is not None and f.gunfire >= 0.35:
            shoot.add(StoryEvent(mid, "climax" if f.reaction else "development",
                                 "tiros com reação" if f.reaction else "tiros", "acoustic", 2.5 if f.reaction else 1.0))
        if f.gunfire >= 0.35 or f.impact >= 0.45:
            if not f.reaction and not police_win and shoot is None and chase is None:
                self.noise.append((mid, "som alto do jogo sem reação"))
                self.noise = self.noise[-50:]

        # --- conflict: two argument cues within 60 s (or one plus a shout)
        conf_win = self._window("conflict")
        conflict = self._open("conflict")
        if conflict is None and (len(conf_win) >= 2 or (conf_win and f.voice >= 0.4)):
            conflict = self._start("conflict", conf_win[0][0])
            conflict.add(StoryEvent(conf_win[0][0], "setup", f"discussão começa (“{conf_win[0][1]}”)", "transcript", 1.5))
            conflict.conflicts.append("discussão entre personagens")
        if conflict is not None:
            for t, ph in f.conflict:
                conflict.add(StoryEvent(t, "development", f"discussão (“{ph}”)", "transcript", 1.0))
            if (chase is not None and chase.start >= conflict.start) or (shoot is not None and shoot.start >= conflict.start):
                if not any("escalou" in c for c in conflict.changes):
                    conflict.changes.append("a discussão escalou para ação")
                    conflict.add(StoryEvent(mid, "climax", "discussão vira ação", "transcript", 2.5))
            if f.laughs and conflict.phases["development"]:
                conflict.add(StoryEvent(f.laughs[0][0], "resolution", "termina em risada", "transcript", 1.5))
                conflict.outcome = "laughter"

        # --- funny conversation: repeated laughter with speech, without needing loudness
        laughs_win = self._window("laughs")
        funny = self._open("funny_conversation")
        talk = sum(x.speech_words for x in self.recent[-4:])
        if funny is None and len(laughs_win) >= 2 and talk >= 12 and chase is None and shoot is None:
            funny = self._start("funny_conversation", laughs_win[0][0])
            funny.add(StoryEvent(laughs_win[0][0], "setup", "conversa com risadas", "transcript", 1.5))
        if funny is not None and f.laughs:
            phase = "climax" if len(f.laughs) >= 2 or f.voice >= 0.5 else "development"
            funny.add(StoryEvent(f.laughs[0][0], phase, "risadas" if phase == "climax" else "risada", "transcript",
                                 2.0 if phase == "climax" else 1.0))

        # --- scare: scream/surprise with context (surprise words or a silence before)
        if f.surprise and f.voice >= 0.35:
            sc = self._open("scare") or self._start("scare", _first(f.surprise))
            sc.add(StoryEvent(_first(f.surprise), "climax", "susto com reação", "acoustic", 2.0))
        elif f.voice >= 0.5 and not f.surprise and not f.laughs and not police_win and conflict is None:
            self.noise.append((mid, "grito sem contexto"))

        # participants: capitalised names repeated inside open situations (low certainty until 3x)
        for s in self.situations:
            if s.status != "ongoing":
                continue
            for n in f.names:
                s.name_counts[n] = s.name_counts.get(n, 0) + 1
            s.participants = sorted([n for n, c in s.name_counts.items() if c >= 3])[:6]
            maybe = sorted([n for n, c in s.name_counts.items() if c == 2])[:4]
            s.uncertain = [u for u in s.uncertain if not u.startswith("participante?")] + \
                [f"participante? {n}" for n in maybe]
        # visual labels (from analysed clips) enrich the situation that contains them
        for t, label in b.visual:
            for s in self.situations:
                if s.start - 5 <= t <= s.last + 10:
                    s.add(StoryEvent(t, "development", f"na tela: {label[:60]}", "visual", 1.2))
        return f

    def at(self, t: float, slack: float = 20.0) -> Situation | None:
        """The most relevant situation covering ``t`` (stories beat scares)."""
        cands = [s for s in self.situations if s.start - slack <= t <= s.last + slack]
        if not cands:
            return None
        rank = {"police_chase": 0, "shootout": 1, "conflict": 2, "funny_conversation": 3, "scare": 4}
        return min(cands, key=lambda s: (rank.get(s.kind, 9), -len(s.events)))

    def noise_near(self, t: float, w: float = 6.0) -> list[str]:
        return [lbl for tt, lbl in self.noise if abs(tt - t) <= w]
