"""Subtitle model: timeline building, line breaking and ASS/SRT writers."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import sorani
from .tiers import Tier


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------
@dataclass
class Unit:
    """A chunk of speech that will become one subtitle after translation."""
    index: int
    start: float
    end: float
    source: str
    words: List[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Cue:
    start: float
    end: float
    text: str                 # Kurdish, may contain "\n"
    source: str = ""          # original language (for bilingual mode / subtitles)
    meta: Dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------
def _segment_span(seg: dict, tier: Tier) -> Tuple[float, float]:
    start = float(seg.get("start") or 0.0)
    end = float(seg.get("end") or start)
    words = seg.get("words") or []
    if tier.word_timestamps and words:
        first = next((w for w in words if float(w.get("end", 0)) - float(w.get("start", 0)) > 0.02), None)
        last = next((w for w in reversed(words) if float(w.get("end", 0)) - float(w.get("start", 0)) > 0.02), None)
        if first:
            start = max(start, float(first.get("start", start)))
        if last:
            end = min(max(end, start + 0.2), float(last.get("end", end)))
    if end <= start:
        end = start + 0.6
    return start, end


def build_units(segments: Sequence[dict], tier: Tier) -> List[Unit]:
    """Turn raw Whisper segments into speech units with sane boundaries."""
    raw: List[Unit] = []
    for seg in segments:
        text = sorani.clean_source(seg.get("text") or "")
        if not text or not sorani.is_translatable(text):
            continue
        start, end = _segment_span(seg, tier)
        raw.append(Unit(index=0, start=start, end=end, source=text, words=seg.get("words") or []))

    if not raw:
        return []

    # ---- merge fragments / split run-ons depending on the accuracy level ----
    target_min = 1.1 if tier.sentence_grouping else 0.7
    target_max = 7.5 if tier.sentence_grouping else 6.0
    units: List[Unit] = []
    for u in raw:
        if units:
            prev = units[-1]
            gap = u.start - prev.end
            joinable = (
                gap <= 0.45
                and (prev.duration < target_min or u.duration < target_min)
                and len(prev.source) + len(u.source) < 190
            )
            if joinable:
                prev.end = max(prev.end, u.end)
                prev.source = f"{prev.source} {u.source}".strip()
                prev.words = (prev.words or []) + (u.words or [])
                continue
        units.append(Unit(index=0, start=u.start, end=u.end, source=u.source, words=u.words))

    # split anything that is too long to read comfortably
    split_units: List[Unit] = []
    for u in units:
        if u.duration <= target_max and len(u.source) <= (120 if tier.sentence_grouping else 90):
            split_units.append(u)
            continue
        pieces = _split_unit(u, tier)
        split_units.extend(pieces)

    # enforce readable durations and no overlap
    for i, u in enumerate(split_units):
        u.index = i
        if u.duration < tier.min_duration:
            u.end = min(u.end + (tier.min_duration - u.duration), u.start + tier.max_duration)
    for i in range(len(split_units) - 1):
        nxt = split_units[i + 1]
        cur = split_units[i]
        if cur.end > nxt.start - 0.02:
            cur.end = max(cur.start + 0.35, nxt.start - 0.02)
    return [u for u in split_units if u.source.strip()]


def _split_unit(u: Unit, tier: Tier) -> List[Unit]:
    """Break a long unit into 2 balanced pieces, keeping word timing if present."""
    text = u.source
    mid_words = text.split()
    if len(mid_words) < 4:
        return [u]
    best_i, best_score = len(mid_words) // 2, 1e9
    total = max(1, len(text))
    running = 0
    for i, w in enumerate(mid_words[:-1], start=1):
        running += len(w) + 1
        score = abs(running / total - 0.5)
        # prefer breaking right after punctuation
        if w.endswith((",", ".", "،", "؛", "!", "?", "؟")):
            score -= 0.18
        if score < best_score:
            best_score, best_i = score, i
    left_words, right_words = mid_words[:best_i], mid_words[best_i:]
    words = u.words or []
    if tier.word_timestamps and len(words) >= len(mid_words):
        split_at = min(len(words) - 1, best_i)
        left_end = float(words[split_at - 1].get("end", u.start + u.duration / 2)) if split_at > 0 else u.start + u.duration / 2
        right_start = float(words[split_at].get("start", left_end))
        left = Unit(0, u.start, max(u.start + 0.3, left_end), " ".join(left_words), words[:split_at])
        right = Unit(0, max(right_start, left.end), u.end, " ".join(right_words), words[split_at:])
        return [left, right]
    ratio = len(left_words) / float(len(mid_words))
    split_time = u.start + u.duration * ratio
    return [
        Unit(0, u.start, split_time, " ".join(left_words), []),
        Unit(0, split_time, u.end, " ".join(right_words), []),
    ]


# --------------------------------------------------------------------------
# cue assembly
# --------------------------------------------------------------------------
def _balance_split(text: str, parts: int, max_chars: int) -> List[str]:
    words = text.split()
    if parts <= 1 or len(words) < 2:
        return [text]
    per = max(1, math.ceil(len(words) / parts))
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) >= max_chars or len(cur) >= per + 2:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        if chunks and len(" ".join(cur)) < max_chars / 2:
            chunks[-1] = chunks[-1] + " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return [c for c in chunks if c.strip()]


def assemble_cues(units: Sequence[Unit], translations: Sequence[str], tier: Tier,
                  max_chars: int = 42) -> List[Cue]:
    """Combine timings with translated text, then fit them to reading speed."""
    cues: List[Cue] = []
    for unit, raw_text in zip(units, translations):
        text = sorani.normalize_kurdish(raw_text)
        if not text:
            continue
        lines = sorani.wrap_lines(text, max_chars, tier.max_lines)
        display = "\n".join(lines)
        start, end = unit.start + tier.cue_padding, unit.end - tier.cue_padding * 0.5
        if end - start < 0.45:
            start, end = unit.start, unit.end
        cues.append(Cue(start=start, end=end, text=display, source=unit.source,
                        meta={"unit": unit.index}))

    # ---- reading speed: extend, then split, then trim ----------------------
    cues.sort(key=lambda c: c.start)
    for i, c in enumerate(cues):
        limit = cues[i + 1].start - 0.02 if i + 1 < len(cues) else c.end + 30
        needed = sorani.min_duration_for(c.text, tier.cps, tier.min_duration)
        if c.duration < needed:
            c.end = min(max(c.end, c.start + needed), limit, c.start + tier.max_duration)
        if c.duration > tier.max_duration:
            c.end = c.start + tier.max_duration

    final: List[Cue] = []
    for i, c in enumerate(cues):
        chars = len(c.text.replace("\n", " "))
        limit = cues[i + 1].start - 0.02 if i + 1 < len(cues) else c.end + 30
        too_fast = chars / max(0.2, c.duration) > tier.cps * 1.2
        if too_fast and chars > max_chars * 1.1:
            # how many cues does this text actually need to be readable?
            wanted = math.ceil(chars / max(1.0, c.duration * tier.cps))
            parts = max(2, min(8, wanted))
            chunks = _balance_split(c.text.replace("\n", " "), parts, max_chars)
            if len(chunks) > 1:
                t = c.start
                room = max(0.5, limit - c.start)
                total_chars = sum(len(ch) for ch in chunks) or 1
                for n, ch in enumerate(chunks):
                    need = sorani.min_duration_for(ch, tier.cps, 0.7)
                    if n == len(chunks) - 1:
                        dur = min(max(need, c.duration * len(ch) / total_chars), max(need, room - (t - c.start)))
                    else:
                        dur = need
                    end = t + max(0.5, dur)
                    final.append(Cue(t, end,
                                     "\n".join(sorani.wrap_lines(ch, max_chars, tier.max_lines)),
                                     c.source))
                    t = end
                continue
        final.append(c)

    # ensure strictly increasing and never overlapping
    final.sort(key=lambda c: c.start)
    for i in range(len(final) - 1):
        if final[i].end > final[i + 1].start - 0.01:
            final[i].end = max(final[i].start + 0.3, final[i + 1].start - 0.01)
    return [c for c in final if c.text.strip() and c.end > c.start]


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------
def _ts_srt(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - math.floor(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def write_srt(cues: Iterable[Cue], path: Path) -> Path:
    lines = []
    for i, c in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_ts_srt(c.start)} --> {_ts_srt(c.end)}")
        lines.extend(c.text.split("\n"))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def write_vtt(cues: Iterable[Cue], path: Path) -> Path:
    lines = ["WEBVTT", ""]
    for c in cues:
        lines.append(f"{_ts_srt(c.start).replace(',', '.')} --> {_ts_srt(c.end).replace(',', '.')}")
        lines.extend(c.text.split("\n"))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_escape(text: str) -> str:
    t = text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    return t.replace("\n", "\\N")


def _hex_to_ass(color: str, alpha: int = 0) -> str:
    c = color.lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


ALIGNMENTS = {"bottom": 2, "lower": 2, "middle": 5, "top": 8}


def font_size_for(height: int, font_scale: float = 1.0, bilingual: bool = False) -> int:
    h = height or 720
    # tuned so a 2-line cue stays inside the safe area at every resolution
    base = h * 0.052
    if bilingual:
        base *= 0.88
    return max(16, int(round(base * font_scale)))


SRC_FONT = "Noto Sans"


def write_ass(cues: Sequence[Cue], path: Path, width: int, height: int,
              font: str = "Noto Naskh Arabic", font_scale: float = 1.0,
              position: str = "bottom", bilingual: bool = False,
              margin_v_extra: int = 0) -> Path:
    width = max(320, width or 1280)
    height = max(240, height or 720)
    fs = font_size_for(height, font_scale, bilingual)
    src_fs = max(12, int(fs * 0.78))
    align = ALIGNMENTS.get(position, 2)
    # margin measured from the chosen edge
    if position in ("bottom", "lower"):
        margin_v = int(height * (0.055 if position == "bottom" else 0.16)) + margin_v_extra
    elif position == "top":
        margin_v = int(height * 0.05)
    else:
        margin_v = 0
    outline = max(1.8, height * 0.0036)
    shadow = max(1.0, height * 0.0017)
    margin_h = int(width * 0.06)

    header = f"""[Script Info]
Title: Kurdi Sorani
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KUR,{font},{fs},&H00FFFFFF,&H00202020,&H00101010,&HA0000000,0,0,0,0,100,100,0.5,0,1,{outline:.2f},{shadow:.2f},{align},{margin_h},{margin_h},{margin_v},1
Style: SRC,{SRC_FONT},{src_fs},&H00DCDCDC,&H00202020,&H00101010,&HA0000000,0,0,0,0,100,100,0.3,0,1,{outline * 0.8:.2f},{shadow * 0.7:.2f},{align},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        start, end = _ts_ass(c.start), _ts_ass(c.end)
        body = _ass_escape(c.text)
        if bilingual and c.source:
            body = f"{{\\rSRC}}{_ass_escape(c.source)}\\N{{\\rKUR}}{body}"
        events.append(f"Dialogue: 0,{start},{end},KUR,,0,0,0,,{body}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path
