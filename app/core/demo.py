"""Demo / self-test doubles.

Used by ``KURDISH_SUB_DEMO=1`` (and the automated tests) so the whole pipeline —
timeline building, styling, ffmpeg burning and the web UI — can be exercised
without downloading any model or touching the network.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Sequence

DEMO_SEGMENTS = [
    (0.4, 3.2, "So, tell me, how did you two meet?"),
    (3.4, 7.1, "It was a rainy night in Erbil, I was waiting for a taxi."),
    (7.3, 10.6, "And I stole the taxi from him, basically."),
    (10.8, 14.9, "That is not how I remember it at all, but okay, keep going."),
    (15.1, 18.4, "Anyway, the wedding is next month and you are both invited."),
    (18.6, 23.0, "Wait, what? You never told me you were getting married, that's insane!"),
    (23.3, 26.0, "Surprise! Now pass me the salt."),
]

DEMO_KU = {
    1: "کەواتە بۆم بڵێ، ئێوە دوو کەس چۆن یەکتریتان بینی؟",
    2: "شەوێکی باراناوی بوو لە هەولێر، چاوەڕوانی تەکسییەک بووم.",
    3: "وە ئەگەر ڕاستی بەیت، تەکسییەکەم لێ سەند.",
    4: "ئەمە بە هیچ شێوەیەک ئەوە نییە کە من بیرم دەکەوێتەوە، بەڵام باشە، بەردەوام بە.",
    5: "بەهەرحاڵ، زەماوەند مانگی داهاتووە و ئێوە هەردووکتان بانگهێشتکراون.",
    6: "بوەستە، چی؟ هەرگیز پێت نەگوتم کە دەچیت هاوسەرگیری بکەیت، ئەمە شێتە!",
    7: "سەرسوڕهێنەر! ئێستا خوێیەکەم پێبدە.",
}


class DemoTranscriber:
    """Mimics faster-whisper with believable segments for a short clip."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or {}

    def transcribe(self, audio, tier, duration=0.0, progress=None, cancel=None, on_status=None):
        if on_status:
            on_status("Demo mode: skipping the real speech model.")
        total = duration or 26.0
        scale = 1.0
        if total and total < 26.0:  # stretch the demo script onto shorter clips
            scale = max(0.12, total / 26.0)
        segments = []
        for start, end, text in DEMO_SEGMENTS:
            start, end = start * scale, end * scale
            if total and start >= total:
                break
            if progress:
                progress(min(0.99, end / total), f"Listening … {int(end)}s / {int(total)}s")
            time.sleep(0.08)
            if cancel and cancel.is_set():
                break
            segments.append({"start": start, "end": end, "text": text, "words": []})
        if progress:
            progress(1.0, "Speech recognition finished (demo).")
        return {
            "language": "en",
            "language_name": "English",
            "language_probability": 0.99,
            "duration": total,
            "model": "demo",
            "device": "demo",
            "compute_type": "demo",
            "segments": segments,
            "cancelled": bool(cancel and cancel.is_set()),
        }


class DemoTranslator:
    name = "demo"
    label = "Demo translator (offline)"
    warnings: List[str] = []

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or {}
        self.warnings = []

    def resolve(self):
        return self

    def translate(self, texts: Sequence[str], source_lang: str, tier, progress=None) -> List[str]:
        out: List[str] = []
        for i, _t in enumerate(texts, start=1):
            time.sleep(0.02)
            out.append(DEMO_KU.get(i, "دەقی نموونەیی بۆ تاقیکردنەوە."))
            if progress:
                progress(i / max(1, len(texts)))
        return out

    def available_providers(self):
        return []
