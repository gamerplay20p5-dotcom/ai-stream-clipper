"""ASS subtitle generation: short word groups, active-word highlight, emphasis colours.

Phase 5 stage E: speaker-aware captions (the streamer's speech in the main style, other voices
smaller and in another colour, or hidden), configurable font/size/position/animation, safe areas
for the Shorts/Reels/TikTok interface, and the channel's discreet watermark and end-card text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..analysis.lexicon import is_emphasis

PLAY_W, PLAY_H = 1080, 1920
# safe areas (1080x1920): the platform UI covers ~150 px at the top, ~380 px at the bottom and a
# ~140 px column of buttons on the right; captions stay inside, centred
SAFE_SIDE = 110
SAFE_BOTTOM = 380
SAFE_TOP_CAPTIONS = 1350  # highest caption baseline (distance from the bottom)
FONTS = ("Noto Sans", "DejaVu Sans", "Noto Serif")  # installed in the image (fonts-noto-core, dejavu)

# bold sans font for burned captions; the container image sets "Noto Sans" (fonts-noto-core)
FONT = os.environ.get("ASC_CAPTION_FONT", "Montserrat")

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
    speaker: str = ""  # streamer | other:N | unknown | "" (not analysed)


@dataclass
class CaptionOptions:
    mode: str = "streamer_highlight"  # streamer_highlight | streamer_only | all
    font: str = ""  # "" = image default
    size_pct: int = 100
    position: str = "low"  # low | middle | high
    animation: str = "highlight"  # highlight (active word) | pop (highlight + scale + fade) | none
    active_color: str = ""  # "#RRGGBB" ("" = style default)
    emphasis_color: str = ""
    other_color: str = "#9AD7FF"
    streamer_confirmed: bool = False  # streamer_only hides other voices only when the voice is confirmed
    watermark: str = ""
    watermark_position: str = "top_right"  # top_left | top_right | bottom_left
    watermark_opacity: float = 0.45
    cta: str = ""  # end-card text (cross-promotion), shown in the last ``cta_seconds``
    cta_seconds: float = 3.0
    extra: dict = field(default_factory=dict)


def hex_to_ass(colour: str, alpha: int = 0) -> str | None:
    """"#RRGGBB" -> "&HAABBGGRR" (None if invalid)."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", colour or "")
    if not m:
        return None
    rr, gg, bb = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper()


def is_other(speaker: str) -> bool:
    return speaker.startswith("other:")


def display_text(word: str) -> str:
    """Faithful text, short-video punctuation: keep ? ! and ..., drop trailing commas and periods."""
    w = word.strip()
    if w.endswith("...") or w.endswith("…"):
        return w
    return w.rstrip(".,;:") or w


def caption_margin_for(base: int, position: str) -> int:
    lift = {"low": 0, "middle": 260, "high": 520}.get(position, 0)
    return max(SAFE_BOTTOM, min(SAFE_TOP_CAPTIONS, base + lift))


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
    """Short groups that never cross a pause, a sentence end or a change of speaker."""
    groups: list[list[SubWord]] = []
    cur: list[SubWord] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            ends_sentence = cur[-1].text[-1:] in ".!?,"
            new_voice = is_other(w.speaker) != is_other(cur[-1].speaker)
            if len(cur) >= max_words or chars > max_chars or gap > 0.6 or ends_sentence or new_voice:
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
    opts: CaptionOptions | None = None,
    notes: list[tuple[float, float, str]] | None = None,
) -> str:
    o = opts or CaptionOptions()
    emph_set = {e.lower().strip(".,!?") for e in (emphasis or set())}
    fs, prim, outc, back, bstyle, outline, shadow, active, emph = STYLES.get(style, STYLES["bold"])
    font = o.font if o.font in FONTS else FONT
    fs = int(round(fs * max(60, min(160, o.size_pct)) / 100))
    active = hex_to_ass(o.active_color) or active
    emph = hex_to_ass(o.emphasis_color) or emph
    other = hex_to_ass(o.other_color) or WHITE
    margin_v = caption_margin_for(margin_v, o.position)
    ofs = int(round(fs * 0.78))
    wm_alpha = int(round(255 * (1 - max(0.1, min(1.0, o.watermark_opacity)))))
    wm_col = f"&H{wm_alpha:02X}FFFFFF"
    wm_align, wm_mv = {"top_left": (7, 190), "top_right": (9, 190), "bottom_left": (1, 300)}.get(
        o.watermark_position, (9, 190))
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\nPlayResY: {PLAY_H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{fs},{prim},{prim},{outc},{back},-1,0,0,0,100,100,0,0,{bstyle},{outline},{shadow},2,"
        f"{SAFE_SIDE},{SAFE_SIDE},{margin_v},1\n"
        f"Style: Other,{font},{ofs},{other},{other},{outc},{back},-1,0,0,0,100,100,0,0,{bstyle},{max(2, outline - 2)},"
        f"{shadow},2,{SAFE_SIDE},{SAFE_SIDE},{margin_v},1\n"
        f"Style: Title,{font},58,{WHITE},{WHITE},{BOX},{BOX},-1,0,0,0,100,100,0,0,3,18,0,8,80,80,{title_margin_v},1\n"
        f"Style: Mark,{font},34,{wm_col},{wm_col},&H80000000,&H00000000,-1,0,0,0,100,100,1,0,1,1,0,{wm_align},"
        f"60,150,{wm_mv},1\n"
        f"Style: Note,{font},44,{WHITE},{WHITE},{BOX},{BOX},-1,1,0,0,100,100,0,0,3,12,0,8,{SAFE_SIDE},{SAFE_SIDE},"
        f"{title_margin_v + 120},1\n"
        f"Style: Cta,{font},54,{WHITE},{WHITE},{BOX},{BOX},-1,0,0,0,100,100,0,0,3,16,0,5,{SAFE_SIDE},{SAFE_SIDE},0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    # caption fonts have no colour-emoji glyphs: keep emoji in metadata, not burned in
    title = re.sub(r"[^\w\s\"'“”‘’!?.,:;|#&()+\-]", "", title or "").strip(" |")
    title = re.sub(r"\s{2,}", " ", title)
    end_all = duration if duration > 0 else 3600
    if title:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(end_all)},Title,,0,0,0,,{_esc(title)}")
    for a, b, text in notes or []:  # structural edit labels (e.g. the teaser's "Daqui a pouco...")
        lines.append(f"Dialogue: 4,{_ts(a)},{_ts(b)},Note,,0,0,0,,{_esc(_clean_overlay(text)[:60])}")
    mark = _clean_overlay(o.watermark)[:40]
    if mark:
        lines.append(f"Dialogue: 2,{_ts(0)},{_ts(end_all)},Mark,,0,0,0,,{_esc(mark)}")
    cta = _clean_overlay(o.cta)[:80]
    cta_start = None
    if cta and duration > o.cta_seconds + 3:
        cta_start = duration - max(1.5, min(6.0, o.cta_seconds))
        lines.append(f"Dialogue: 3,{_ts(cta_start)},{_ts(duration)},Cta,,0,0,0,,{{\\fad(200,0)}}{_esc(cta)}")
    hide_others = o.mode == "streamer_only" and o.streamer_confirmed
    same_style = o.mode == "all"
    words = [w for w in words if not (hide_others and is_other(w.speaker))]
    if cta_start is not None:
        words = [w for w in words if w.start < cta_start]  # the end card replaces the captions
    if style != "none":
        groups = group_words(words)
        for gi, g in enumerate(groups):
            sty = "Other" if (not same_style and is_other(g[0].speaker)) else "Cap"
            g_end = g[-1].end
            nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else None
            if nxt is not None and nxt - g_end < 0.35:
                g_end = nxt
            g_end = max(g_end, g[0].start + 0.4)
            if cta_start is not None:
                g_end = min(g_end, cta_start)
            if o.animation == "none":
                txt = " ".join(_word_markup(x, emph_set, emph) for x in g)
                lines.append(f"Dialogue: 0,{_ts(g[0].start)},{_ts(g_end)},{sty},,0,0,0,,{txt}")
                continue
            for wi, w in enumerate(g):
                s = w.start if wi > 0 else g[0].start
                e = g[wi + 1].start if wi + 1 < len(g) else g_end
                if e <= s:
                    e = s + 0.05
                parts = []
                scale = "\\fscx112\\fscy112" if o.animation == "pop" or sty == "Cap" else ""
                for k, x in enumerate(g):
                    if k == wi:
                        # other voices keep their own colour: the highlight colour means "the streamer"
                        col = _ovc(other) if sty == "Other" else _ovc(active)
                        parts.append(f"{{\\1c{col}{scale}}}{_esc(display_text(x.text).upper())}{{\\r}}")
                    else:
                        parts.append(_word_markup(x, emph_set, emph))
                pop = "{\\fad(40,0)}" if wi == 0 else ""
                if o.animation == "pop" and wi == 0:
                    pop = "{\\fad(60,0)\\t(0,90,\\fscx108\\fscy108)\\t(90,160,\\fscx100\\fscy100)}"
                lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},{sty},,0,0,0,,{pop}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def _word_markup(x: SubWord, emph_set: set[str], emph: str) -> str:
    txt = _esc(display_text(x.text).upper())
    if is_emphasis(x.text) or x.text.lower().strip(".,!?") in emph_set or x.text.endswith("!"):
        return f"{{\\1c{_ovc(emph)}}}{txt}{{\\r}}"
    return txt


def _clean_overlay(text: str) -> str:
    """Caption fonts have no colour-emoji glyphs: keep letters, digits and basic punctuation."""
    t = re.sub(r"[^\w\s\"'“”‘’!?.,:;|#&()+@/\-]", "", text or "").strip(" |")
    return re.sub(r"\s{2,}", " ", t)
