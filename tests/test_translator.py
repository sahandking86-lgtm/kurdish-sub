"""Translator plumbing: parsing, fallbacks and provider selection."""
import pytest

from app.core import translator as T
from app.core.tiers import get_tier


def test_parse_numbered_lines():
    raw = "1. سڵاو لەوێ\n2. چۆنی؟\n3) باشم، سوپاس"
    parsed = T._parse_numbered(raw, 3)
    assert parsed[1] == "سڵاو لەوێ"
    assert parsed[2] == "چۆنی؟"
    assert parsed[3] == "باشم، سوپاس"


def test_parse_numbered_ignores_out_of_range():
    parsed = T._parse_numbered("9. nope\n1. بەڵێ", 2)
    assert parsed == {1: "بەڵێ"}


def test_clean_result_strips_quotes_and_bullets():
    assert T._clean_result('"سڵاو"') == "سڵاو"
    assert T._clean_result("- سڵاو") == "سڵاو"


def test_looks_untranslated():
    assert T._looks_untranslated("", "hello") is True
    assert T._looks_untranslated("hello", "hello") is True
    assert T._looks_untranslated("سڵاو", "hello") is False


def test_google_provider_parses_payload(monkeypatch):
    provider = T.GoogleFree()

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [[["سڵاو لەوێ", "Hello there", None, None]], None, "en"]

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            assert params["tl"] == "ckb"
            assert params["q"] == "Hello there"
            return FakeResp()

    monkeypatch.setattr(provider, "_session", lambda: FakeSession())
    out = provider.translate(["Hello there"], "en")
    assert out == ["سڵاو لەوێ"]


def test_google_provider_retries_then_reports(monkeypatch):
    provider = T.GoogleFree()

    class BoomSession:
        def __init__(self):
            self.headers = {}

        def get(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(provider, "_session", lambda: BoomSession())
    monkeypatch.setattr(T.time, "sleep", lambda *_a, **_k: None)
    out = provider.translate(["hi"], "en")
    assert out == [""]


class FakeChatProvider(T._LLMProvider):
    name = "fake"

    def __init__(self):
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        # echo one numbered line per requested line
        n = user.count("\n")  # crude but enough for the test
        return "\n".join(f"{i}. وەرگێڕانی {i}" for i in range(1, n + 2))


def test_llm_provider_batches_and_numbers():
    p = FakeChatProvider()
    out = p.translate(["a", "b", "c"], "en")
    assert len(out) == 3
    assert all(line.startswith("وەرگێڕانی") for line in out)


def test_translator_picks_configured_provider(monkeypatch):
    tr = T.Translator({"provider": "google"})
    provider = tr.resolve()
    assert provider.name == "google"


def test_translator_auto_order_prefers_gemini_when_key_present(monkeypatch):
    tr = T.Translator({"provider": "auto", "gemini_api_key": "fake-key"})
    assert tr._order()[0] == "gemini"
    tr2 = T.Translator({"provider": "auto"})
    assert tr2._order()[0] == "google"


def test_translator_repairs_missing_lines(monkeypatch):
    tr = T.Translator({"provider": "google"})

    class Broken(T.Provider):
        name = "broken"

        def translate(self, texts, source_lang="auto", progress=None):
            return ["" for _ in texts]

    class Fixed(T.Provider):
        name = "google"

        def translate(self, texts, source_lang="auto", progress=None):
            return ["باشە" for _ in texts]

    tr.provider = Broken()
    monkeypatch.setattr(T, "backup_provider", lambda: Fixed())
    out = tr.translate(["ok"], "en", get_tier("medium"))
    assert out == ["باشە"]
    assert tr.warnings


def test_refine_pass_is_used_when_tier_asks(monkeypatch):
    class Chatty(FakeChatProvider):
        def translate(self, texts, source_lang="auto", progress=None):
            return ["دەقی سەرەتا" for _ in texts]

        def refine(self, sources, drafts):
            return ["دەقی باشتر" for _ in drafts]

    tr = T.Translator({"provider": "gemini", "gemini_api_key": "k"})
    tr.provider = Chatty()
    out = tr.translate(["hello"], "en", get_tier("perfect"))
    assert out == ["دەقی باشتر"]


def test_falls_back_to_next_engine_when_first_fails(monkeypatch):
    class Dead(T.Provider):
        name = "google"

        def translate(self, texts, source_lang="auto", progress=None):
            raise T.TranslateError("blocked")

    class Alive(T.Provider):
        name = "lingva"

        def translate(self, texts, source_lang="auto", progress=None):
            return ["باشە" for _ in texts]

    tr = T.Translator({"provider": "auto"})
    monkeypatch.setattr(tr, "_build", lambda name: Dead() if name == "google" else Alive())
    out = tr.translate(["ok"], "en", get_tier("medium"))
    assert out == ["باشە"]
    assert any("Fallback" in w for w in tr.warnings)


def test_all_engines_failing_raises_helpful_error(monkeypatch):
    class Dead(T.Provider):
        name = "google"

        def translate(self, texts, source_lang="auto", progress=None):
            raise T.TranslateError("blocked")

    tr = T.Translator({"provider": "auto"})
    monkeypatch.setattr(tr, "_build", lambda name: Dead())
    with pytest.raises(T.TranslateError) as err:
        tr.translate(["ok"], "en", get_tier("medium"))
    assert "internet" in str(err.value).lower()


def test_partial_engine_result_is_still_used(monkeypatch):
    class Half(T.Provider):
        name = "google"

        def translate(self, texts, source_lang="auto", progress=None):
            return ["باشە" if i == 0 else "" for i in range(len(texts))]

    tr = T.Translator({"provider": "google"})
    tr.provider = Half()
    monkeypatch.setattr(T, "backup_provider", lambda: Half())
    out = tr.translate(["a", "b"], "en", get_tier("medium"))
    assert out[0] == "باشە"
