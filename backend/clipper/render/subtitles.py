"""ASS subtitle generation: short word groups, active-word highlight, emphasis colours."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..analysis.lexicon import is_emphasis

PLAY_W, PLAY_H = 1080, 1920
FONT = "Montserrat"

# ASS colours are &HAABBGGRR
WHITE = "&H00FFFFFF"
YELLOW = "&H0000E5FF"
GREEN = "&H0066FF7A"
BLACK = "&H00000000"
BOX = "&H96000000"

STYLES = {
    # name: (fontsize, primary, outline_colour, back, border_style, outline, shadow, active, emphasis)
    "bold": (84, WHITE, BLACK, BLACK, 1, 7, 2, YELLOW, GREEN),
    "clean": (66, WHITE, BLACK, "&H80000000", 1, 3, 3, YELLOW, YELLOW),
    "boxed": (70, WHITE, BOX, BOX, 3, 14, 0, YELLOW, GREEN),
}


@dataclass
class SubWord:
    start: float
    end: float
    text: str


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ovc(colour: str) -> str:
    """Style colour (&HAABBGGRR) -> override-tag colour (&HBBGGRR&)."""
    return f"&H{colour[-6:]}&"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words: list[SubWord], max_words: int = 3, max_chars: int = 18) -> list[list[SubWord]]:
    groups: list[list[SubWord]] = []
    cur: list[SubWord] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            ends_sentence = cur[-1].text[-1:] in ".!?,"
            if len(cur) >= max_words or chars > max_chars or gap > 0.6 or ends_sentence:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(
    words: list[SubWord],
    *,
    style: str = "bold",
    margin_v: int = 560,
    title: str = "",
    title_margin_v: int = 260,
    duration: float = 0.0,
    emphasis: set[str] | None = None,
) -> str:
    emph_set = {e.lower().strip(".,!?") for e in (emphasis or set())}
    fs, prim, outc, back, bstyle, outline, shadow, active, emph = STYLES.get(style, STYLES["bold"])
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\nPlayResY: {PLAY_H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Cap,{FONT},{fs},{prim},{prim},{outc},{back},-1,0,0,0,100,100,0,0,{bstyle},{outline},{shadow},2,60,60,"
        f"{margin_v},1\n"
        f"Style: Title,{FONT},58,{WHITE},{WHITE},{BOX},{BOX},-1,0,0,0,100,100,0,0,3,18,0,8,80,80,{title_margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    # caption fonts have no colour-emoji glyphs: keep emoji in metadata, not burned in
    title = re.sub(r"[^\w\s\"'“”‘’!?.,:;|#&()+\-]", "", title or "").strip(" |")
    title = re.sub(r"\s{2,}", " ", title)
    if title:
        end = duration if duration > 0 else 3600
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(end)},Title,,0,0,0,,{_esc(title)}")
    if style != "none":
        groups = group_words(words)
        for gi, g in enumerate(groups):
            g_end = g[-1].end
            nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else None
            if nxt is not None and nxt - g_end < 0.35:
                g_end = nxt
            g_end = max(g_end, g[0].start + 0.4)
            for wi, w in enumerate(g):
                s = w.start if wi > 0 else g[0].start
                e = g[wi + 1].start if wi + 1 < len(g) else g_end
                if e <= s:
                    e = s + 0.05
                parts = []
                for k, x in enumerate(g):
                    txt = _esc(x.text.upper())
                    if k == wi:
                        parts.append(f"{{\\1c{_ovc(active)}\\fscx112\\fscy112}}{txt}{{\\r}}")
                    elif is_emphasis(x.text) or x.text.lower().strip(".,!?") in emph_set:
                        parts.append(f"{{\\1c{_ovc(emph)}}}{txt}{{\\r}}")
                    else:
                        parts.append(txt)
                pop = "{\\fad(40,0)}" if wi == 0 else ""
                lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"
