"""Sorani (Central Kurdish) text helpers: normalising, splitting and wrapping."""
from __future__ import annotations

import re
from typing import List

# Kurdish Sorani uses these letters; the Arabic look-alikes must be folded.
_LETTER_FIX = {
    "\u064a": "\u06cc",  # ARABIC YEH        -> FARSI YEH (Kurdish ی)
    "\u0649": "\u06cc",  # ALEF MAKSURA      -> ی
    "\u0643": "\u06a9",  # ARABIC KAF        -> KEHEH (Kurdish ک)
    "\u06a9": "\u06a9",
    "\u0629": "\u0647",  # TEH MARBUTA       -> HEH (Kurdish ه)
    "\u06c0": "\u0647\u0654",
    "\u0640": "",        # TATWEEL
}

# invisible / directional controls that break libass rendering
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2060, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF], None
)

MUSIC_TAGS = re.compile(
    r"[\(\[]\s*(music|musi\w*|applause|laughter|laughs|sighs|inaudible|silence|"
    r"foreign|no audio|no speech|sound effect\w*|beep\w*|bleep\w*)\s*[\)\]]",
    re.IGNORECASE,
)

_SENT_END = re.compile(r"(?<=[.!?…؟۔])\s+")
_ARABIC_RANGE = re.compile(r"[\u0600-\u06FF]")


def normalize_kurdish(text: str) -> str:
    """Fold Arabic look-alikes into proper Kurdish letters and tidy spacing."""
    if not text:
        return ""
    t = text.translate(_INVISIBLE)
    for bad, good in _LETTER_FIX.items():
        if bad in t:
            t = t.replace(bad, good)
    t = t.replace("ـ", "")
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    # no space before punctuation, one space after
    t = re.sub(r"\s+([،؛؟!,.:%])", r"\1", t)
    t = re.sub(r"([،؛؟!])(?=[^\s\d])", r"\1 ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def clean_source(text: str) -> str:
    """Tidy a raw transcript line before it is translated."""
    if not text:
        return ""
    t = text.replace("\u200b", "").replace("\ufeff", "")
    t = MUSIC_TAGS.sub(" ", t)
    t = re.sub(r"♪+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip("-–—• ")
    if not re.search(r"\w", t):
        return ""
    return t


def has_arabic_script(text: str) -> bool:
    return bool(_ARABIC_RANGE.search(text or ""))


def is_translatable(text: str) -> bool:
    """False for pure punctuation / numbers / single stray characters."""
    letters = re.findall(r"[^\W\d_]", text or "")
    return len(letters) >= 1


def split_sentences(text: str, max_words: int = 40) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_END.split(text) if p.strip()]
    out: List[str] = []
    for p in parts:
        words = p.split()
        if len(words) <= max_words:
            out.append(p)
        else:
            # very long run-on sentence: break on commas, then on word count
            chunks = re.split(r"(?<=[،,])\s+", p)
            buf = ""
            for c in chunks:
                if len((buf + " " + c).split()) > max_words or len(buf) > 220:
                    if buf:
                        out.append(buf.strip())
                    buf = c
                else:
                    buf = (buf + " " + c).strip()
            if buf:
                out.append(buf.strip())
    return out


def _greedy_wrap(words: List[str], width: int) -> List[str]:
    lines: List[str] = []
    cur = ""
    for w in words:
        candidate = w if not cur else cur + " " + w
        if len(candidate) <= width or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def wrap_lines(text: str, max_chars: int, max_lines: int = 2) -> List[str]:
    """Wrap into at most `max_lines` visually balanced lines of <= max_chars.

    The narrowest width that still fits in `max_lines` is used, which keeps the
    two lines about equally long — that reads much better as a subtitle.
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    words = text.split(" ")
    if len(text) <= max_chars or len(words) < 2:
        return [text]

    widest_word = max(len(w) for w in words)
    lo, hi = max(widest_word, 8), max(len(text), widest_word)
    best = _greedy_wrap(words, max_chars)
    while lo <= hi:
        mid = (lo + hi) // 2
        lines = _greedy_wrap(words, mid)
        if len(lines) <= max_lines:
            best = lines
            hi = mid - 1
        else:
            lo = mid + 1
    if len(best) > max_lines:
        # Cannot respect both limits: a very long single line is worse than an
        # extra line, so keep natural wrapping (callers split the cue instead).
        best = [text] if max_lines == 1 else _greedy_wrap(words, max_chars)
    return best


def reading_speed_ok(text: str, duration: float, cps: int) -> bool:
    if duration <= 0:
        return False
    chars = len(text.replace("\n", " "))
    return chars / duration <= cps


def min_duration_for(text: str, cps: int, floor: float = 0.8) -> float:
    chars = max(1, len((text or "").replace("\n", " ")))
    return max(floor, chars / float(max(6, cps)))


def strip_rtl_marks(text: str) -> str:
    return (text or "").translate(_INVISIBLE)
