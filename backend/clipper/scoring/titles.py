"""Local, template-based title / description / hashtag suggestions (no paid APIs)."""

from __future__ import annotations

import re

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
