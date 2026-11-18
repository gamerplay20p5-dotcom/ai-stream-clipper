"""Extract one JSON object from free-form model output (no guessing beyond syntax)."""

from __future__ import annotations

import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


_BROKEN = re.compile("\ufffd([\u0080-\u00bf])")


def fix_text(s: str) -> str:
    """Repair UTF-8 sequences split by the detokenizer: U+FFFD followed by the
    continuation byte of a 2-byte sequence starting with 0xC3 (Portuguese accents)."""
    if "\ufffd" not in s:
        return s
    return _BROKEN.sub(lambda m: bytes([0xC3, ord(m.group(1))]).decode("utf-8", "replace"), s)


def _fix_all(obj):
    if isinstance(obj, str):
        return fix_text(obj)
    if isinstance(obj, list):
        return [_fix_all(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _fix_all(v) for k, v in obj.items()}
    return obj


def strip_thinking(text: str) -> str:
    text = _THINK.sub("", text)
    if "<think>" in text and "</think>" not in text:  # truncated reasoning: nothing usable before it
        text = text.split("<think>")[0]
    return text.strip()


def salvage_truncated(text: str) -> dict | None:
    """Recover an object whose generation stopped mid-way (token limit), syntactically only:
    cut at the last complete member/element and close the open brackets. No value is invented."""
    text = re.sub(r"```(?:json)?", "", strip_thinking(text))
    start = text.find("{")
    if start == -1:
        return None
    stack: list[str] = []
    cuts: list[tuple[int, str]] = []  # (end index of last complete value, closers needed)
    in_str = esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                return None
            stack.pop()
            if not stack:
                return None  # balanced object: extract_json handles (or rejected) it
            cuts.append((i + 1, "".join(reversed(stack))))
        elif ch == "," and (len(stack) == 1 or stack[-1] == "]"):
            # top-level member or list element; never leave a half-written nested object
            cuts.append((i, "".join(reversed(stack))))
    for end, closers in reversed(cuts[-50:]):
        try:
            obj = json.loads(text[start:end] + closers)
        except json.JSONDecodeError:
            continue
        return _fix_all(obj) if isinstance(obj, dict) else None
    return None


def extract_json(text: str) -> dict | None:
    """Return the first balanced top-level JSON object in ``text`` (code fences allowed)."""
    text = strip_thinking(text)
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        # tolerate trailing commas only - a purely syntactic fix
                        try:
                            obj = json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
                        except json.JSONDecodeError:
                            break
                    return _fix_all(obj) if isinstance(obj, dict) else None
        else:
            return None  # unterminated object: later braces are nested in it, not top-level
        start = text.find("{", start + 1)
    return None
