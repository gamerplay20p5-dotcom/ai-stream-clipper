"""Reaction lexicon (PT-BR + EN) used by the cheap text layer.

Weights are heuristic. Categories: ``surprise``, ``hype``, ``fear``, ``anger``, ``humor``.
Multi-word phrases are matched on accent-free, lower-cased token sequences.
"""

from __future__ import annotations

import re
import unicodedata

PHRASES: dict[str, tuple[str, float]] = {
    # --- PT surprise / disbelief
    "meu deus": ("surprise", 2.5),
    "nao acredito": ("surprise", 3.0),
    "nao e possivel": ("surprise", 2.5),
    "como assim": ("surprise", 2.0),
    "que isso": ("surprise", 2.0),
    "o que": ("surprise", 0.8),
    "caramba": ("surprise", 2.0),
    "nossa": ("surprise", 1.2),
    "mano do ceu": ("surprise", 2.5),
    "jesus": ("surprise", 1.5),
    "inacreditavel": ("surprise", 2.5),
    "olha isso": ("surprise", 1.8),
    "olha so": ("surprise", 1.2),
    "serio": ("surprise", 1.0),
    # --- PT hype
    "vamo": ("hype", 1.5),
    "vamos": ("hype", 0.8),
    "bora": ("hype", 1.0),
    "isso ai": ("hype", 1.2),
    "que jogada": ("hype", 2.5),
    "insano": ("hype", 2.5),
    "absurdo": ("hype", 2.2),
    "brabo": ("hype", 2.0),
    "lenda": ("hype", 1.5),
    "consegui": ("hype", 1.8),
    "conseguimos": ("hype", 2.0),
    # --- PT fear / panic
    "socorro": ("fear", 2.5),
    "corre": ("fear", 2.0),
    "foge": ("fear", 2.0),
    "morri": ("fear", 2.2),
    "morreu": ("fear", 1.8),
    "me ajuda": ("fear", 2.0),
    "cuidado": ("fear", 1.5),
    "nao nao nao": ("fear", 3.0),
    "para para": ("fear", 2.0),
    # --- PT anger
    "que odio": ("anger", 2.5),
    "nao acredito nisso": ("anger", 2.0),
    "sacanagem": ("anger", 2.0),
    "mentira": ("anger", 1.5),
    "ridiculo": ("anger", 1.8),
    # --- EN
    "oh my god": ("surprise", 2.5),
    "omg": ("surprise", 2.0),
    "no way": ("surprise", 3.0),
    "what the": ("surprise", 2.5),
    "holy": ("surprise", 2.2),
    "are you kidding": ("surprise", 2.5),
    "wow": ("surprise", 1.5),
    "insane": ("hype", 2.5),
    "lets go": ("hype", 2.0),
    "let's go": ("hype", 2.0),
    "clutch": ("hype", 2.2),
    "run": ("fear", 1.2),
    "help me": ("fear", 2.0),
    "oh no": ("fear", 2.5),
    "no no no": ("fear", 3.0),
    "i'm dead": ("humor", 2.0),
}

LAUGH_RE = re.compile(r"^(?:(?:ha){2,}h?|(?:he){2,}|(?:hi){2,}|k{3,}|(?:rs){2,}|lol|lmao|risos|laughs?|laughter)$")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokenize(words: list[str]) -> list[str]:
    return [normalize(w) for w in words]


_MAX_N = max(len(p.split()) for p in PHRASES)


def find_hits(tokens: list[str]) -> list[tuple[int, int, str, str, float]]:
    """Return ``(first_idx, last_idx, phrase, category, weight)`` for lexicon matches,
    preferring the longest phrase at each position."""
    hits = []
    i = 0
    while i < len(tokens):
        matched = False
        for n in range(min(_MAX_N, len(tokens) - i), 0, -1):
            phrase = " ".join(t for t in tokens[i:i + n] if t)
            if phrase in PHRASES:
                cat, w = PHRASES[phrase]
                hits.append((i, i + n - 1, phrase, cat, w))
                i += n
                matched = True
                break
        if not matched:
            if LAUGH_RE.match(tokens[i].replace(" ", "")):
                hits.append((i, i, tokens[i], "humor", 2.5))
            i += 1
    return hits


def is_emphasis(token: str) -> bool:
    """Words highlighted in captions."""
    n = normalize(token)
    if not n:
        return False
    if n in PHRASES or LAUGH_RE.match(n.replace(" ", "")):
        return True
    raw = re.sub(r"[^\w]", "", token)
    return len(raw) >= 3 and raw.isupper()
