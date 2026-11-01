"""The five accuracy levels.

Each level is a complete recipe: which Whisper model to run, how much work to
spend on timing, how sentences are grouped before translation, and how strict
the subtitle formatting rules are.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Tier:
    key: str
    name: str                      # English name
    ku: str                        # Sorani name
    blurb: str                     # one line description
    model: str                     # faster-whisper model id
    fallback_models: tuple = ()    # tried in order if `model` cannot load
    beam_size: int = 5
    word_timestamps: bool = False
    vad: str = "balanced"          # off | loose | balanced | strict
    sentence_grouping: bool = False
    refine: bool = False           # proof-read pass (Gemini / local AI models only)
    max_lines: int = 2
    cps: int = 18                  # characters per second budget
    min_duration: float = 0.9
    max_duration: float = 7.0
    cue_padding: float = 0.10      # lead-in / lead-out tightening
    model_size: str = ""
    cpu_speed: str = ""            # rough expectation shown in the UI
    accent: str = "#22d3a7"


TIERS: Dict[str, Tier] = {
    "very_low": Tier(
        key="very_low",
        name="Very low accuracy",
        ku="وردی کەم — زۆر خێرا",
        blurb="Fastest possible. Rough meaning, one short line per subtitle.",
        model="tiny",
        fallback_models=("base",),
        beam_size=1,
        word_timestamps=False,
        vad="loose",
        sentence_grouping=False,
        max_lines=1,
        cps=24,
        min_duration=0.8,
        max_duration=6.0,
        cue_padding=0.05,
        model_size="75 MB",
        cpu_speed="about 8x faster than realtime",
        accent="#7dd3fc",
    ),
    "low": Tier(
        key="low",
        name="Low accuracy",
        ku="وردی کەم",
        blurb="Good enough to follow the story. Two lines, plain translation.",
        model="base",
        fallback_models=("small",),
        beam_size=2,
        word_timestamps=False,
        vad="balanced",
        sentence_grouping=False,
        max_lines=2,
        cps=22,
        min_duration=0.85,
        max_duration=6.5,
        cue_padding=0.08,
        model_size="142 MB",
        cpu_speed="about 5x faster than realtime",
        accent="#93c5fd",
    ),
    "medium": Tier(
        key="medium",
        name="Medium accuracy",
        ku="وردی مامناوەند",
        blurb="The sweet spot. Whole-sentence translation, solid timing.",
        model="small",
        fallback_models=("base",),
        beam_size=5,
        word_timestamps=True,
        vad="balanced",
        sentence_grouping=True,
        max_lines=2,
        cps=19,
        min_duration=0.9,
        max_duration=7.0,
        cue_padding=0.10,
        model_size="484 MB",
        cpu_speed="about 1.5x faster than realtime",
        accent="#34d399",
    ),
    "high": Tier(
        key="high",
        name="High accuracy",
        ku="وردی بەرز",
        blurb="Big turbo model, word-level timing, whole-sentence context.",
        model="large-v3-turbo",
        fallback_models=("medium", "small"),
        beam_size=5,
        word_timestamps=True,
        vad="strict",
        sentence_grouping=True,
        max_lines=2,
        cps=17,
        min_duration=0.95,
        max_duration=7.0,
        cue_padding=0.12,
        model_size="1.6 GB",
        cpu_speed="about 0.5x realtime on CPU, near realtime on GPU",
        accent="#fbbf24",
    ),
    "perfect": Tier(
        key="perfect",
        name="Perfect accuracy",
        ku="وردی تەواو — باشترین",
        blurb="The biggest model plus a proof-reading pass over every line.",
        model="large-v3",
        fallback_models=("large-v3-turbo", "medium"),
        beam_size=5,
        word_timestamps=True,
        vad="strict",
        sentence_grouping=True,
        refine=True,
        max_lines=2,
        cps=16,
        min_duration=1.0,
        max_duration=7.0,
        cue_padding=0.14,
        model_size="3.1 GB",
        cpu_speed="slower than realtime on CPU, best used with a GPU",
        accent="#f472b6",
    ),
}

TIER_ORDER: List[str] = ["very_low", "low", "medium", "high", "perfect"]

DEFAULT_TIER = "medium"


def get_tier(key: Optional[str]) -> Tier:
    return TIERS.get((key or DEFAULT_TIER), TIERS[DEFAULT_TIER])


def tiers_payload() -> List[dict]:
    out = []
    for k in TIER_ORDER:
        t = TIERS[k]
        d = asdict(t)
        d["fallback_models"] = list(t.fallback_models)
        out.append(d)
    return out
