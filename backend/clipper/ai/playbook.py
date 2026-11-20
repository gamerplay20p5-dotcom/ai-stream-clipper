"""Editorial playbook (Phase 7): what a professional short-form editor checks before publishing a cut.

Turns craft knowledge into measurable, explainable criteria. Every value is 0-10 with the evidence that
produced it, or ``None`` when the signal is not available (never a made-up middle value):

* **ingame** - the cut is about what happens IN the game/RP, not the stream itself (settings, Discord,
  subscriptions, sponsors, "let me show you", channel goals). Loud talk about the stream is not an event.
* **payoff** - something actually resolves inside the cut: an outcome (escaped, caught, died, won), a
  success/failure exclamation after the peak, a laugh after the joke. Energy without a payoff is filler.
* **hook** - the first seconds give a reason to keep watching (tension cue, exclamation, question,
  action). A silent or mundane opening loses the viewer before the event.
* **ending** - the cut ends shortly after the payoff/reaction, not on trailing small talk.
* **intelligible** - a viewer who never watched the stream understands the speech (language mix and
  very sparse/garbled speech hurt).

Pure functions over the data the pipeline already has (timed words, detected kinds, story, visual).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

VERSION = "playbook-2026-11-20a"


def _norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(ch for ch in w if not unicodedata.combining(ch)))


# talking about the stream/platform instead of the game (weights: 1 = strong, 0.5 = weak alone)
META_TERMS = {
    "discord": 1, "nitro": 1, "assinatura": 1, "assinar": 1, "assinante": 1, "inscreve": 1, "inscrevam": 1,
    "inscricao": 1, "inscritos": 1, "pix": 1, "doacao": 1, "doacoes": 1, "donate": 1, "patrocinio": 1,
    "patrocinador": 1, "cupom": 1, "configuracoes": 1, "configuracao": 1, "configurar": 1, "pagamento": 1,
    "cartao": 0.5, "plano": 0.5, "planos": 0.5, "link": 0.5, "bio": 0.5, "descricao": 0.5, "canal": 0.5,
    "stream": 0.5, "streamer": 0.5, "obs": 0.5, "microfone": 0.5, "gravar": 0.5, "gravado": 0.5, "gravacao": 0.5,
    "meta": 0.5, "subs": 0.5, "prime": 0.5, "seguidores": 0.5, "notificacao": 0.5,
}
META_PHRASES = ("mostrar pra voces", "mostrar para voces", "queria mostrar", "vou mostrar", "na live", "essa live",
                "da live", "o chat", "fala chat", "pro chat", "trocar de plano", "proxima meta")
VISUAL_META = ("menu", "configura", "discord", "navegador", "area de trabalho", "desktop", "tela de carregamento",
               "loading", "configuracoes", "janela do")

# a resolution heard right after the peak (success, failure, outcome, strong reaction)
PAYOFF_TERMS = {
    "consegui", "conseguiu", "conseguimos", "ganhei", "ganhamos", "venci", "passei", "passou", "completou",
    "completei", "acertei", "acertou", "matei", "matou", "morri", "morreu", "morremos", "perdi", "perdeu",
    "perdemos", "prendeu", "preso", "presos", "fugi", "fugiu", "escapei", "escapou", "escapamos", "caiu",
    "bateu", "explodiu", "pegou", "pegaram", "errou", "errei", "acabou", "nice", "boa", "isso", "finalmente",
    "aeee", "aee", "uhuu", "gg", "kkkk", "kkk", "hahaha", "kkkkk",
}
PAYOFF_PHRASES = ("nao acredito", "meu deus", "deu certo", "deu ruim", "olha isso", "que isso", "nao e possivel")
HOOK_TERMS = {"corre", "vai", "policia", "socorro", "cuidado", "olha", "atira", "foge", "pega", "rapido", "para",
              "espera", "calma", "caramba", "nossa", "mano", "ai", "meu", "que"}
EN_FUNCTION = {"the", "you", "and", "is", "to", "it", "that", "what", "but", "so", "yeah", "this", "are", "was",
               "of", "my", "your", "be", "have", "can", "do", "not", "just", "like", "with", "anyways", "i", "im"}

HOOK_S = 3.0  # the opening a viewer decides on
GOOD_TAIL_S, LONG_TAIL_S = 3.0, 8.0


@dataclass
class Craft:
    criteria: dict  # name -> (value | None, [evidence])
    offgame: bool = False
    notes: list[str] = field(default_factory=list)

    def value(self, k: str) -> float | None:
        return self.criteria.get(k, (None, []))[0]


def _tokens(words, a: float = -1e18, b: float = 1e18) -> list[tuple[float, str]]:
    return [(float(s), _norm(str(w))) for s, _e, w in words if a <= float(s) < b and _norm(str(w))]


def _phrase_hits(tokens: list[str], phrases) -> list[str]:
    text = " " + " ".join(tokens) + " "
    return [p for p in phrases if f" {p} " in text]


def assess(*, words: list, start: float, end: float, peak: float, kinds: list[str] | None = None,
           story: dict | None = None, visual: dict | None = None) -> Craft:
    """``words``: [(start, end, word)] in session time (may extend a little beyond the cut)."""
    kinds = list(kinds or [])
    inside = _tokens(words, start, end)
    toks = [t for _, t in inside]
    crit: dict = {}
    dur = max(1.0, end - start)
    vis_ok = bool(visual) and not visual.get("fallback")

    # 1 ingame ------------------------------------------------------------------------------------
    meta_w, meta_ev = 0.0, []
    for t in toks:
        if t in META_TERMS:
            meta_w += META_TERMS[t]
            meta_ev.append(t)
    for p in _phrase_hits(toks, META_PHRASES):
        meta_w += 1.0
        meta_ev.append(f"“{p}”")
    vis_meta = []
    if vis_ok:
        vtxt = _norm(f"{visual.get('summary', '')} {visual.get('key_event', '')}")
        vis_meta = [m for m in VISUAL_META if m in vtxt]
        meta_w += 1.5 * len(vis_meta)
    density = meta_w * 30.0 / dur  # per 30 s of cut
    if toks or vis_ok:
        ingame = max(0.0, 10.0 - 3.0 * density)
        ev = ([f"fala sobre a live/plataforma: {', '.join(dict.fromkeys(meta_ev))}"] if meta_ev else []) + \
             ([f"imagem fora do jogo: {', '.join(vis_meta)}"] if vis_meta else [])
        crit["ingame"] = (round(ingame, 1), ev or ["fala e imagem ligadas ao jogo"])
    offgame = "ingame" in crit and crit["ingame"][0] < 4.0

    # 2 payoff --------------------------------------------------------------------------------------
    after = [t for s, t in inside if s >= peak - 1.0]
    hits = [t for t in after if t in PAYOFF_TERMS] + _phrase_hits(after, PAYOFF_PHRASES)
    if story and story.get("outcome") in ("arrested", "escaped", "laughter"):
        crit["payoff"] = (9.0, [f"desfecho da história: {story['outcome']}"])
    elif vis_ok and visual.get("complete") and visual.get("arc", {}).get("resolution_end") is not None:
        crit["payoff"] = (8.0, ["começo, meio e desfecho visíveis"])
    elif hits:
        crit["payoff"] = (round(min(8.5, 6.0 + 0.8 * len(set(hits))), 1), [f"depois do pico: {', '.join(dict.fromkeys(hits))}"])
    elif "laughter" in kinds:
        crit["payoff"] = (6.5, ["risada no corte"])
    elif toks:
        crit["payoff"] = (3.0, ["energia na voz, mas nenhum desfecho depois do pico"])

    # 3 hook ------------------------------------------------------------------------------------------
    opening = [t for s, t in inside if s < start + HOOK_S]
    if inside or vis_ok:
        if any(t in HOOK_TERMS for t in opening) or peak - start <= 4.0:
            crit["hook"] = (8.0, ["algo acontece nos primeiros segundos"])
        elif len(opening) >= 4:
            crit["hook"] = (5.5, ["começa com fala, sem gancho claro"])
        else:
            crit["hook"] = (3.0, [f"primeiros {HOOK_S:.0f} s quase sem nada"])

    # 4 ending (tail after the last payoff/reaction) -------------------------------------------------
    pay_t = [s for s, t in inside if s >= peak - 1.0 and (t in PAYOFF_TERMS)]
    anchor = max([peak] + pay_t)
    tail = end - anchor
    if inside:
        v = 9.0 if tail <= GOOD_TAIL_S else (6.5 if tail <= LONG_TAIL_S else max(2.0, 6.5 - 0.4 * (tail - LONG_TAIL_S)))
        crit["ending"] = (round(v, 1), [f"termina {tail:.0f} s depois do ponto alto"])

    # 5 intelligible -----------------------------------------------------------------------------------
    if len(toks) >= 6:
        en = sum(1 for t in toks if t in EN_FUNCTION) / len(toks)
        rate = len(toks) / dur
        v = 9.0 - 14.0 * en - (2.0 if rate < 0.6 else 0.0)
        ev = [f"{en * 100:.0f}% de palavras em inglês"] if en >= 0.12 else []
        if rate < 0.6:
            ev.append("fala muito esparsa")
        crit["intelligible"] = (round(max(1.0, min(9.0, v)), 1), ev or ["fala clara em português"])

    craft = Craft(criteria=crit, offgame=offgame)
    if offgame:
        craft.notes.append("conteúdo fora do jogo: conversa sobre a live/plataforma não é acontecimento")
    return craft


WEIGHTS = {"ingame": 1.4, "payoff": 1.3, "hook": 0.9, "ending": 0.7, "intelligible": 0.8}
LABELS = {"ingame": "Acontece dentro do jogo", "payoff": "Tem desfecho/payoff", "hook": "Gancho no início",
          "ending": "Termina no ponto certo", "intelligible": "Fala compreensível"}


# Measured on 123 human decisions (2026-11-20): ingame, payoff and intelligible separate approved from
# rejected clips; hook and ending do NOT (AUC ~0.50) - they describe the CUT, which editing can fix, not the
# happening. So they never lower the score: they become edit fixes (see ``edit_fixes``).
SCORE_KEYS = ("ingame", "payoff", "intelligible")


def edit_fixes(c: Craft, *, start: float, end: float, peak: float, words: list) -> list[dict]:
    """Concrete, applicable fixes a professional editor would make (bounds in session time)."""
    out = []
    hook = c.value("hook")
    if hook is not None and hook < 5.0:
        first = [float(s) for s, _e, w in words if start <= float(s) < peak and _norm(str(w)) in HOOK_TERMS]
        new_start = max(start, (first[0] if first else peak - 6.0) - 1.0)
        if new_start - start >= 1.5:
            out.append({"kind": "hook", "detail": "começar mais perto da ação (introdução sem gancho)",
                        "start": round(new_start, 2), "end": None})
    ending = c.value("ending")
    if ending is not None and ending < 6.0:
        pay = [float(e) for s, e, w in words if peak - 1.0 <= float(s) < end and _norm(str(w)) in PAYOFF_TERMS]
        new_end = max([peak + 2.0] + [p + 1.5 for p in pay])
        if end - new_end >= 2.0:
            out.append({"kind": "tail", "detail": "terminar logo depois da reação (conversa depois do ponto alto)",
                        "start": None, "end": round(new_end, 2)})
    return out


def craft_score(c: Craft) -> float | None:
    known = {k: v for k, (v, _ev) in c.criteria.items() if v is not None and k in SCORE_KEYS}
    if not known:
        return None
    return sum(WEIGHTS[k] * v for k, v in known.items()) / sum(WEIGHTS[k] for k in known)


def adjust(score: float, c: Craft) -> float:
    """Apply the playbook to a 0-100 score: off-game content is cut hard, the rest nudges the score."""
    cs = craft_score(c)
    if cs is None:
        return score
    factor = 0.85 + 0.03 * (cs - 5.0)  # craft 5 -> x0.85+0 ; 10 -> x1.0 ; 0 -> x0.70
    if c.offgame:
        factor *= 0.7
    return round(max(0.0, min(100.0, score * min(1.0, factor + 0.15))), 1)
