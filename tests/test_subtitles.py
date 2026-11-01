"""Timeline building, cue assembly and subtitle writers."""
import re

from app.core import subtitles
from app.core.tiers import get_tier

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Hello there.", "words": []},
    {"start": 2.1, "end": 4.0, "text": "How are you doing today?", "words": []},
    {"start": 4.2, "end": 5.0, "text": "Fine.", "words": []},
    {"start": 20.0, "end": 32.0, "text": "This is a very long segment of talking that goes on and on "
                                         "and on so it must be divided into more than one subtitle cue.",
     "words": []},
]


def test_build_units_merges_short_fragments():
    units = subtitles.build_units(SEGMENTS[:3], get_tier("medium"))
    assert units
    assert all(u.end > u.start for u in units)
    # the 0.8s "Fine." must not be left alone as a cue
    assert not any(u.source == "Fine." for u in units)


def test_long_segment_is_split():
    units = subtitles.build_units([SEGMENTS[3]], get_tier("high"))
    assert len(units) >= 2
    assert all(u.duration <= 8 for u in units)
    assert " ".join(u.source for u in units).split() == SEGMENTS[3]["text"].split()


def test_units_never_overlap():
    units = subtitles.build_units(SEGMENTS, get_tier("medium"))
    for a, b in zip(units, units[1:]):
        assert a.end <= b.start + 0.001


def test_units_drop_empty_segments():
    units = subtitles.build_units([{"start": 0, "end": 1, "text": "[Music]", "words": []}],
                                  get_tier("low"))
    assert units == []


def test_assemble_cues_keeps_text_and_timing():
    units = subtitles.build_units(SEGMENTS[:2], get_tier("medium"))
    kurdish = ["سڵاو لەوێ.", "چۆنی ئەمڕۆ؟"]
    cues = subtitles.assemble_cues(units, kurdish, get_tier("medium"))
    assert len(cues) == len(units)
    for cue in cues:
        assert cue.text.strip()
        assert cue.end > cue.start
        assert "\u0643" not in cue.text  # no Arabic kaf


def test_assemble_cues_splits_unreadable_lines():
    long_text = "ئەمە زۆر درێژە و دەبێت بشکێت " * 6
    units = subtitles.build_units([{"start": 0, "end": 3.0, "text": "x " * 40, "words": []}],
                                  get_tier("medium"))
    cues = subtitles.assemble_cues(units, [long_text], get_tier("medium"), max_chars=42)
    assert len(cues) >= 2
    for cue in cues:
        chars = len(cue.text.replace("\n", " "))
        assert chars / cue.duration <= get_tier("medium").cps * 1.3 + 0.5


def test_write_srt_format(tmp_path):
    from app.core.subtitles import Cue

    cues = [Cue(0.5, 2.25, "سڵاو\nکوردستان")]
    p = subtitles.write_srt(cues, tmp_path / "a.srt")
    text = p.read_text(encoding="utf-8-sig")
    assert "00:00:00,500 --> 00:00:02,250" in text
    assert "سڵاو" in text and "کوردستان" in text
    assert text.startswith("1")


def test_write_ass_has_styles_and_escaped_newlines(tmp_path):
    from app.core.subtitles import Cue

    cues = [Cue(0.0, 1.5, "بەڵێ\nباشە")]
    p = subtitles.write_ass(cues, tmp_path / "a.ass", 1920, 1080, position="bottom")
    text = p.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in text
    assert "Style: KUR,Noto Naskh Arabic" in text
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,KUR" in text
    assert "بەڵێ\\Nباشە" in text


def test_ass_font_size_scales_with_resolution():
    small = subtitles.font_size_for(480)
    big = subtitles.font_size_for(2160)
    assert big > small > 10


def test_write_vtt_header(tmp_path):
    from app.core.subtitles import Cue

    p = subtitles.write_vtt([Cue(0, 1, "سڵاو")], tmp_path / "a.vtt")
    assert p.read_text(encoding="utf-8").startswith("WEBVTT")
