"""Unit tests for the Sorani text helpers."""
from app.core import sorani


def test_folds_arabic_lookalikes_into_kurdish_letters():
    text = "كوردي ية"  # Arabic kaf, Arabic yeh, teh marbuta
    out = sorani.normalize_kurdish(text)
    assert "ك" not in out
    assert "ي" not in out
    assert "ة" not in out
    assert out == "کوردی یه"


def test_removes_invisible_bidi_controls():
    assert "\u200f" not in sorani.normalize_kurdish("سڵاو\u200f کوردستان\u200e")
    assert "\u200c" not in sorani.normalize_kurdish("سڵاو\u200c")


def test_keeps_kurdish_letters_and_punctuation():
    src = "سڵاو، چۆنی؟ من باشم."
    assert sorani.normalize_kurdish(src) == src


def test_no_space_before_punctuation():
    assert sorani.normalize_kurdish("بەڵێ ، باشە") == "بەڵێ، باشە"


def test_clean_source_drops_music_tags():
    assert sorani.clean_source("[Music] hello there") == "hello there"
    assert sorani.clean_source("♪ la la ♪") == "la la"
    assert sorani.clean_source("[Applause]") == ""


def test_wrap_lines_balances_two_lines():
    text = "ئەمە دەقێکی درێژە بۆ تاقیکردنەوەی شکانەوەی دێرەکان لە ژێرنووسدا"
    lines = sorani.wrap_lines(text, 42, 2)
    assert len(lines) == 2
    assert all(len(ln) <= 42 for ln in lines)
    assert abs(len(lines[0]) - len(lines[1])) <= 14  # balanced, not lopsided
    assert " ".join(lines) == text


def test_wrap_lines_respects_single_line_tier():
    lines = sorani.wrap_lines("وشەیەکی زۆر درێژ لەسەر شاشە", 12, 1)
    assert len(lines) == 1


def test_split_sentences_breaks_long_runs():
    s = "یەکەم ڕستە. دووەم ڕستە؟ سێیەم ڕستە!"
    assert len(sorani.split_sentences(s)) == 3


def test_min_duration_for_short_text_uses_floor():
    assert sorani.min_duration_for("بەڵێ", cps=18, floor=0.9) == 0.9


def test_has_arabic_script():
    assert sorani.has_arabic_script("سڵاو")
    assert not sorani.has_arabic_script("hello")
