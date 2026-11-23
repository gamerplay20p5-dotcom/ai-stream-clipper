"""Local, template-based title / description / hashtag suggestions (no paid APIs)."""

from __future__ import annotations

import re

from ..ai.schemas import VisualAnalysis
from ..ai.visual_evidence import confirmed_event_kind

from ..analysis import lexicon

_TEMPLATES = {
    "pt": {
        "humor": ["Não aguentei de rir com isso 😂", "O chat inteiro chorou de rir 😂", "Isso foi engraçado demais 🤣"],
        "surprise": ["Ninguém esperava por essa 😳", "Ele não acreditou no que aconteceu 😱", "Olha o que aconteceu do nada 😳"],
        "fear": ["O desespero foi real 😰", "Quase deu tudo errado 😱", "Corre que deu ruim 💀"],
        "hype": ["Que jogada absurda 🔥", "Isso foi insano 🔥", "Momento lendário na live 🏆"],
        "anger": ["Ele ficou revoltado 😤", "Não acreditou nessa sacanagem 😤"],
        "scream": ["O grito que ninguém esperava 😱", "Ele surtou ao vivo 😱"],
        "default": ["Momento insano na live 🔥", "Você precisa ver isso 👀", "Isso aconteceu ao vivo 😳"],
    },
    "en": {
        "humor": ["I couldn't stop laughing 😂", "This was way too funny 🤣"],
        "surprise": ["Nobody expected this 😳", "He couldn't believe what happened 😱"],
        "fear": ["The panic was real 😰", "This almost went so wrong 😱"],
        "hype": ["That play was insane 🔥", "Legendary moment on stream 🏆"],
        "anger": ["He was furious 😤"],
        "scream": ["That scream though 😱"],
        "default": ["Insane moment on stream 🔥", "You need to see this 👀"],
    },
}


def _dominant(kinds: list[str], transcript: str) -> str:
    tokens = lexicon.tokenize(transcript.split())
    cats = [h[3] for h in lexicon.find_hits(tokens)]
    if "laughter" in kinds or cats.count("humor") >= 1:
        return "humor"
    if "scream" in kinds:
        return "scream"
    for c in ("fear", "surprise", "hype", "anger"):
        if c in cats:
            return c
    if "silence_break" in kinds:
        return "surprise"
    return "default"


def _best_quote(transcript: str) -> str:
    parts = [p.strip(" ,;-") for p in re.split(r"(?<=[.!?])\s+", transcript) if p.strip()]
    scored = []
    for p in parts:
        n = len(p.split())
        if 2 <= n <= 9:
            hits = lexicon.find_hits(lexicon.tokenize(p.split()))
            scored.append((sum(h[4] for h in hits) + (1 if p.endswith("!") else 0), p))
    if not scored:
        return ""
    s, p = max(scored)
    return p.rstrip(".") if s > 0 else ""


def suggest_metadata(transcript: str, kinds: list[str], streamer: dict, language: str) -> dict:
    lang = "en" if (language or "").startswith("en") else "pt"
    cat = _dominant(kinds, transcript)
    templates = _TEMPLATES[lang].get(cat) or _TEMPLATES[lang]["default"]
    name = streamer.get("display_name") or streamer.get("channel") or ""
    game = streamer.get("game") or ""
    suggestions: list[str] = []
    quote = _best_quote(transcript)
    if quote:
        suggestions.append(f"“{quote[:70]}” 😱")
    suggestions.extend(templates[:3])
    if game:
        suggestions = [f"{s} | {game}" if len(s) + len(game) < 90 else s for s in suggestions]
    suggestions = list(dict.fromkeys(s[:100] for s in suggestions))[:4]
    tags = ["#shorts"]
    if game:
        tags.append("#" + re.sub(r"[^A-Za-z0-9]", "", game.lower()))
    if name:
        tags.append("#" + re.sub(r"[^A-Za-z0-9_]", "", name.lower()))
    tags = list(dict.fromkeys(t for t in tags if len(t) > 1))
    platform = {"twitch": "Twitch", "youtube": "YouTube"}.get(streamer.get("platform", ""), "live")
    if lang == "pt":
        desc = (f"Momento da live de {name} ({platform}).\n"
                "Corte sugerido automaticamente — revise e confirme os direitos de uso antes de publicar.")
    else:
        desc = (f"Moment from {name}'s stream ({platform}).\n"
                "Automatically suggested clip — review and confirm usage rights before publishing.")
    return {
        "title": suggestions[0] if suggestions else "",
        "suggestions": suggestions,
        "description": desc + "\n\n" + " ".join(tags),
        "hashtags": tags,
    }


_EVENT_NAMES = {
    "rollover": ("Capotamento", "Vehicle rollover", "capotamento", "rollover"),
    "explosion": ("Explosão", "Explosion", "explosao", "explosion"),
    "collision": ("Colisão", "Collision", "colisao", "collision"),
    "fall": ("Queda", "Fall", "queda", "fall"),
    "police_chase": ("Perseguição policial", "Police chase", "perseguicao", "policechase"),
    "escape": ("Fuga bem-sucedida", "Successful escape", "fuga", "escape"),
    "failed_escape": ("Fuga que deu errado", "Failed escape", "fuga", "escape"),
    "prank": ("Trollagem", "Prank", "trollagem", "prank"),
    "funny_interaction": ("Interação engraçada", "Funny interaction", "humor", "funny"),
    "surprise": ("Acontecimento inesperado", "Unexpected event", "", ""),
    "unlikely_win": ("Vitória improvável", "Unlikely win", "vitoria", "win"),
    "unexpected_failure": ("Falha inesperada", "Unexpected failure", "falha", "fail"),
}


def grounded_metadata(streamer: dict, language: str, visual: VisualAnalysis | None = None,
                      *, start: float = 0, peak: float = 0, end: float = 0) -> dict:
    """Use registered channel data and closed event vocabulary. Keep the actor unspecified."""
    english = (language or "").lower().startswith("en")
    name = " ".join(str(streamer.get("display_name") or streamer.get("channel") or "").split())[:65]
    game = " ".join(str(streamer.get("registered_game") or "").split())[:60]
    origin = {"twitch": "Twitch", "youtube": "YouTube"}.get(streamer.get("platform"))
    event = confirmed_event_kind(visual, peak, start, end)
    confirmed = event != "unknown"
    tag = ""
    if event in _EVENT_NAMES:
        pt, en, pt_tag, en_tag = _EVENT_NAMES[event]
        label, tag = (en, en_tag) if english else (pt, pt_tag)
    else:
        label = "Stream highlight" if english else "Trecho da live"
    lead = f"{label} {'in' if english else 'em'} {game}" if game else label
    primary = f"{lead} | {name}" if name else lead
    if len(primary) > 100:
        primary = f"{label} | {name}" if name else lead
    suggestions = [primary, f"{name} | {lead}" if name else lead, f"{label} | {game}" if game else label]
    suggestions = list(dict.fromkeys(s for s in suggestions if len(s) <= 100))[:3]
    source = (f"{name}'s stream" if english else f"live de {name}") if name else (
        "the stream" if english else "a live")
    origin_text = (f" ({origin})" if origin else "")
    if confirmed:
        description = f"{lead} during {source}{origin_text}." if english else (
            f"{lead} durante a {source}{origin_text}." if name else f"{lead} durante {source}{origin_text}.")
    else:
        description = f"Clip from {source}{origin_text}." if english else (
            f"Trecho da {source}{origin_text}." if name else f"Trecho de uma live{origin_text}.")
    raw_tags = [game, name, tag]
    tags = ["#" + t for raw in raw_tags if (t := re.sub(r"[^\w]", "", raw, flags=re.UNICODE)[:30])]
    tags = list(dict.fromkeys(tags))
    return {"title": suggestions[0], "suggestions": suggestions, "description": description,
            "hashtags": tags, "event": event}
