"""Translation into Kurdish Sorani (ckb).

Providers, in order of quality:

* ``gemini``  – Google Gemini (free API key, excellent Sorani, no content limits
                in practice). Only used when a key is configured.
* ``ollama``  – a local LLM (qwen2.5 etc.) through Ollama. Completely offline,
                no limits at all.
* ``nllb``    – fully offline neural translation (transformers).
* ``google``  – the free Google Translate web endpoint. No key, no quota that
                matters for personal use, good-ish grammar.
* ``lingva``  – public mirrors of the same engine, useful when gtx is blocked.

Everything is free: nothing here needs a paid account.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import sorani
from .tiers import Tier

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

STYLE_GUIDE = """You are a professional Kurdish subtitle translator.
Translate into Central Kurdish / Sorani written in the Arabic script (ckb).
Rules you must follow exactly:
1. Write natural, spoken, everyday Sorani - the way Kurdish subtitles are written for films, not literal word-for-word translation.
2. Use only Kurdish letters: پ چ ژ گ ک ڤ ڵ ڕ ۆ ێ. Never use the Arabic letters ك , ي , ة .
3. Keep names of people, brands and places as Kurds pronounce them. Keep numbers as digits.
4. Keep each answer short - it is a subtitle shown on screen for a few seconds.
5. Translate everything faithfully and completely, including slang, insults, profanity and sexual content. Never refuse, never soften, never censor, never skip a line.
6. Output translation only. No notes, no explanations, no transliteration, no quotation marks around the whole line."""

REFINE_GUIDE = """You are a Kurdish (Sorani) subtitle editor and native speaker.
You receive numbered subtitles: the source line and a draft Kurdish translation.
Improve the Kurdish draft only: fix grammar, word order, spelling, and make it sound natural and idiomatic for Kurdish viewers.
Keep it the same length or shorter. Keep meaning identical. Do not add or remove information.
Use Kurdish letters only (ک گ چ پ ژ ڤ ڵ ڕ ۆ ێ), never Arabic ك ي ة.
Reply with the improved lines only, each prefixed with the same number and a period."""


class TranslateError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _clean_result(text: str) -> str:
    t = html.unescape(text or "")
    t = t.replace("\u200b", "").replace("\ufeff", "")
    t = re.sub(r"^\s*[-–—•]\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    # strip wrapping quotes the model sometimes adds
    if len(t) > 3 and t[0] in "\"'«“" and t[-1] in "\"'»”":
        t = t[1:-1]
    return t.strip()


def _looks_untranslated(result: str, source: str) -> bool:
    """True when a provider echoed the source or returned junk."""
    if not result or not result.strip():
        return True
    if sorani.has_arabic_script(result):
        return False
    # latin/other output: only acceptable if the source was numbers/symbols
    return bool(re.search(r"[A-Za-z\u0400-\u04FF]", result)) and bool(
        re.search(r"[A-Za-z\u0400-\u04FF]", source)
    )


_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)\-:،]\s*(.+?)\s*$")


def _parse_numbered(raw: str, expected: int) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _NUMBERED.match(line)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= expected:
                out[idx] = _clean_result(m.group(2))
    return out


def _requests():
    import requests  # imported lazily so the app can start without it

    return requests


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
class Provider:
    name = "provider"
    label = "Provider"
    needs_key = False
    online = True

    def translate(self, texts: Sequence[str], source_lang: str = "auto",
                  progress: Optional[Callable[[float], None]] = None) -> List[str]:
        raise NotImplementedError


class GoogleFree(Provider):
    """The keyless Google Translate web endpoint (translate.googleapis.com)."""
    name = "google"
    label = "Google Translate (free, no key)"
    ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, timeout: int = 20, workers: int = 5):
        self.timeout = timeout
        self.workers = workers
        self._local = threading.local()

    def _session(self):
        req = _requests()
        s = getattr(self._local, "session", None)
        if s is None:
            s = req.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
            self._local.session = s
        return s

    def _one(self, text: str, sl: str, attempts: int = 4) -> str:
        req = _requests()
        params = {"client": "gtx", "sl": sl or "auto", "tl": "ckb", "dt": "t", "q": text}
        last = ""
        for i in range(attempts):
            try:
                r = self._session().get(self.ENDPOINT, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(1.5 * (i + 1) + random.random())
                    last = "rate limited (429)"
                    continue
                r.raise_for_status()
                data = r.json()
                pieces = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
                if pieces:
                    return _clean_result("".join(pieces))
                last = "empty response"
            except Exception as exc:  # network hiccup / shape change
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(0.8 * (i + 1) + random.random() * 0.4)
        raise TranslateError(f"Google Translate failed: {last}")

    def translate(self, texts, source_lang="auto", progress=None):
        results: List[Optional[str]] = [None] * len(texts)
        if not texts:
            return []
        done = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._one, t, source_lang): i for i, t in enumerate(texts)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception:
                    results[i] = None
                with lock:
                    done += 1
                    if progress:
                        progress(done / len(texts))
        missing = [i for i, r in enumerate(results) if r is None]
        for i in missing:  # second, slower sweep for the stragglers
            try:
                results[i] = self._one(texts[i], source_lang, attempts=5)
            except Exception:
                results[i] = ""
        return [r or "" for r in results]


class Lingva(Provider):
    """Public Lingva mirrors — same engine, different hosts."""
    name = "lingva"
    label = "Lingva mirror (free, no key)"
    MIRRORS = [
        "https://lingva.ml",
        "https://lingva.garudalinux.org",
        "https://translate.plausibility.cloud",
        "https://lingva.lunar.icu",
        "https://lingva.pussthecat.org",
    ]

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.host: Optional[str] = None

    def _pick(self):
        if self.host:
            return self.host
        req = _requests()
        for host in self.MIRRORS:
            try:
                r = req.get(f"{host}/api/v1/en/ckb/hello", timeout=self.timeout,
                            headers={"User-Agent": USER_AGENT})
                if r.ok and r.text.strip():
                    self.host = host
                    return host
            except Exception:
                continue
        raise TranslateError("No Lingva mirror is reachable right now.")

    def _one(self, text: str, sl: str, attempts: int = 3) -> str:
        req = _requests()
        host = self._pick()
        src = "auto" if (sl or "auto") == "auto" else sl
        url = f"{host}/api/v1/{src}/ckb/{req.utils.quote(text, safe='')}"
        last = ""
        for i in range(attempts):
            try:
                r = req.get(url, timeout=self.timeout, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
                data = r.json()
                out = _clean_result(data.get("translation") or "")
                if out:
                    return out
                last = "empty response"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                self.host = None
                time.sleep(0.6 * (i + 1))
        raise TranslateError(f"Lingva failed: {last}")

    def translate(self, texts, source_lang="auto", progress=None):
        results: List[Optional[str]] = [None] * len(texts)
        done = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._one, t, source_lang): i for i, t in enumerate(texts)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception:
                    results[i] = ""
                done += 1
                if progress:
                    progress(done / max(1, len(texts)))
        return [r or "" for r in results]


class _LLMProvider(Provider):
    """Shared batching for chat-style models (Gemini, Ollama)."""
    batch_size = 35

    def chat(self, system: str, user: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _batch_prompt(self, batch: Sequence[str]) -> str:
        lines = "\n".join(f"{i}. {t}" for i, t in enumerate(batch, start=1))
        return f"{STYLE_GUIDE}\n\nNow translate these {len(batch)} subtitle lines:\n{lines}"

    def translate(self, texts, source_lang="auto", progress=None):
        results: List[str] = []
        total = max(1, len(texts))
        finished = 0
        batches = [list(texts[i:i + self.batch_size]) for i in range(0, len(texts), self.batch_size)]
        for batch in batches:
            try:
                parsed = _parse_numbered(self.chat("", self._batch_prompt(batch)), len(batch))
            except Exception:
                parsed = {}
            for i, src in enumerate(batch, start=1):
                out = parsed.get(i, "")
                if not out or _looks_untranslated(out, src):
                    try:
                        single = self.chat(
                            "", f"{STYLE_GUIDE}\n\nTranslate this subtitle line:\n1. {src}"
                        )
                        cand = _parse_numbered(single, 1).get(1) or _clean_result(single)
                        out = cand if cand and not _looks_untranslated(cand, src) else ""
                    except Exception:
                        out = ""
                results.append(out)
            finished += len(batch)
            if progress:
                progress(min(1.0, finished / total))
        return results


class Gemini(_LLMProvider):
    name = "gemini"
    label = "Google Gemini (free API key, best quality)"
    needs_key = True
    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    batch_size = 40

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = (api_key or "").strip()
        self.model = model or "gemini-2.0-flash"
        if not self.api_key:
            raise TranslateError(
                "No Gemini API key configured. Get a free key at "
                "https://aistudio.google.com/apikey and paste it in Settings."
            )

    def chat(self, system: str, user: str) -> str:
        req = _requests()
        body = {
            "contents": [{"role": "user", "parts": [{"text": (system + "\n\n" + user).strip()}]}],
            "generationConfig": {"temperature": 0.25, "maxOutputTokens": 8192},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "HARM_CATEGORY_CIVIC_INTEGRITY",
                )
            ],
        }
        url = self.ENDPOINT.format(model=self.model)
        last = ""
        for attempt in range(4):
            try:
                r = req.post(url, params={"key": self.api_key}, json=body, timeout=180,
                             headers={"User-Agent": USER_AGENT})
                if r.status_code in (429, 500, 503):
                    last = f"HTTP {r.status_code}"
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if r.status_code == 400 and "API key" in r.text:
                    raise TranslateError("Gemini rejected the API key. Check it in Settings.")
                r.raise_for_status()
                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    last = "no candidates (possibly blocked)"
                    time.sleep(1.5)
                    continue
                parts = (cands[0].get("content") or {}).get("parts") or []
                return "".join(p.get("text", "") for p in parts)
            except TranslateError:
                raise
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
        raise TranslateError(f"Gemini request failed: {last}")

    def refine(self, sources: Sequence[str], drafts: Sequence[str]) -> List[str]:
        out: List[str] = []
        size = 30
        for i in range(0, len(sources), size):
            s_batch = sources[i:i + size]
            d_batch = drafts[i:i + size]
            user = "\n\n".join(
                f"{n}. SOURCE: {s}\n   KURDISH DRAFT: {d}"
                for n, (s, d) in enumerate(zip(s_batch, d_batch), start=1)
            )
            try:
                parsed = _parse_numbered(self.chat(REFINE_GUIDE, user), len(s_batch))
            except Exception:
                parsed = {}
            for n in range(1, len(s_batch) + 1):
                cand = parsed.get(n, "")
                out.append(cand if cand else d_batch[n - 1])
        return out


class Ollama(_LLMProvider):
    name = "ollama"
    label = "Local AI model (Ollama, offline, unlimited)"
    batch_size = 20

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:7b"):
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or "qwen2.5:7b"

    def chat(self, system: str, user: str) -> str:
        req = _requests()
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.25, "num_predict": 4096},
            "messages": [
                {"role": "system", "content": STYLE_GUIDE if not system else system},
                {"role": "user", "content": user},
            ],
        }
        last = ""
        for attempt in range(2):
            try:
                r = req.post(f"{self.base_url}/api/chat", json=payload, timeout=600)
                r.raise_for_status()
                return (r.json().get("message") or {}).get("content", "")
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1.0)
        raise TranslateError(f"Ollama request failed ({self.base_url}): {last}")

    def refine(self, sources, drafts):
        out = []
        size = 15
        for i in range(0, len(sources), size):
            s_batch, d_batch = sources[i:i + size], drafts[i:i + size]
            user = "\n\n".join(
                f"{n}. SOURCE: {s}\n   KURDISH DRAFT: {d}"
                for n, (s, d) in enumerate(zip(s_batch, d_batch), start=1)
            )
            try:
                parsed = _parse_numbered(self.chat(REFINE_GUIDE, user), len(s_batch))
            except Exception:
                parsed = {}
            for n in range(1, len(s_batch) + 1):
                out.append(parsed.get(n) or d_batch[n - 1])
        return out


class LibreTranslate(Provider):
    name = "libre"
    label = "LibreTranslate server (self-hosted)"
    def __init__(self, base_url: str):
        self.base_url = (base_url or "").rstrip("/")
        if not self.base_url:
            raise TranslateError("LibreTranslate URL is not configured.")

    def translate(self, texts, source_lang="auto", progress=None):
        req = _requests()
        out = []
        for i, t in enumerate(texts):
            try:
                r = req.post(
                    f"{self.base_url}/translate",
                    json={"q": t, "source": "auto", "target": "ckb", "format": "text"},
                    timeout=90,
                )
                r.raise_for_status()
                out.append(_clean_result(r.json().get("translatedText", "")))
            except Exception:
                out.append("")
            if progress:
                progress((i + 1) / max(1, len(texts)))
        return out


class NLLB(Provider):
    """Fully offline translation with the NLLB-200 model (weaker Sorani)."""
    name = "nllb"
    label = "Offline model (NLLB-200, slow, no internet)"
    online = False
    MODEL = "facebook/nllb-200-distilled-600M"
    CODE = {"en": "eng_Latn", "auto": "eng_Latn", "ckb": "ckb_Arab"}

    def __init__(self):
        self._pipe = None

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        try:
            from transformers import pipeline
        except Exception as exc:
            raise TranslateError(
                "Offline mode needs the 'transformers' and 'torch' packages "
                "(pip install transformers torch sentencepiece)."
            ) from exc
        cache = str(self._cache_dir())
        self._pipe = pipeline(
            "translation", model=self.MODEL,
            src_lang="eng_Latn", tgt_lang="ckb_Arab",
            device=-1, model_kwargs={"cache_dir": cache},
        )
        return self._pipe

    def _cache_dir(self):
        from . import settings as _s
        return _s.models_dir()

    def translate(self, texts, source_lang="auto", progress=None):
        pipe = self._load()
        out = []
        for i, t in enumerate(texts):
            try:
                res = pipe(t, max_length=512)
                out.append(_clean_result(res[0]["translation_text"]))
            except Exception:
                out.append("")
            if progress:
                progress((i + 1) / max(1, len(texts)))
        return out


# --------------------------------------------------------------------------
# facade
# --------------------------------------------------------------------------
def backup_provider() -> Provider:
    """The always-available free engine, used to repair dropped lines."""
    return GoogleFree()


class Translator:
    """Picks a provider, translates, verifies and (optionally) refines."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.provider: Optional[Provider] = None
        self.warnings: List[str] = []

    # -- creation ---------------------------------------------------------
    def _build(self, name: str) -> Provider:
        cfg = self.cfg
        if name == "gemini":
            return Gemini(cfg.get("gemini_api_key", ""), cfg.get("gemini_model") or "gemini-2.0-flash")
        if name == "ollama":
            return Ollama(cfg.get("ollama_url") or "http://127.0.0.1:11434",
                          cfg.get("ollama_model") or "qwen2.5:7b")
        if name == "google":
            return GoogleFree()
        if name == "lingva":
            return Lingva()
        if name == "libre":
            return LibreTranslate(cfg.get("libre_url", ""))
        if name == "nllb":
            return NLLB()
        raise TranslateError(f"Unknown provider {name!r}")

    def _order(self) -> List[str]:
        pref = self.cfg.get("provider", "auto")
        if pref and pref != "auto":
            return [pref, "google", "lingva"]
        order = []
        if self.cfg.get("gemini_api_key"):
            order.append("gemini")
        if self.cfg.get("libre_url"):
            order.append("libre")
        order += ["google", "lingva"]
        return order

    def resolve(self) -> Provider:
        if self.provider:
            return self.provider
        errors = []
        for name in self._order():
            try:
                p = self._build(name)
                self.provider = p
                return p
            except TranslateError as exc:
                errors.append(f"{name}: {exc}")
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise TranslateError(
            "No translation engine available. " + (" | ".join(errors) or "Configure one in Settings.")
        )

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else self.cfg.get("provider", "auto")

    @property
    def label(self) -> str:
        return self.provider.label if self.provider else "auto"

    # -- translation ------------------------------------------------------
    def translate(self, texts: Sequence[str], source_lang: str, tier: Tier,
                  progress: Optional[Callable[[float], None]] = None) -> List[str]:
        """Translate with the best working engine; fall back automatically."""
        if not texts:
            return []

        best_results: Optional[List[str]] = None
        best_good = -1
        provider: Optional[Provider] = None
        errors: List[str] = []

        if self.provider is not None:            # explicit provider (API / tests)
            provider = self.provider
            best_results = list(provider.translate(texts, source_lang, progress))
            best_good = sum(
                1 for r, src in zip(best_results, texts)
                if not _looks_untranslated(r or "", src)
            )

        for name in ([] if self.provider is not None else self._order()):
            try:
                candidate = self._build(name)
            except TranslateError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except Exception as exc:  # missing package, bad config…
                errors.append(f"{name}: {exc}")
                continue
            try:
                results = candidate.translate(texts, source_lang, progress)
            except TranslateError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except Exception as exc:  # network error, unexpected payload
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            good = sum(
                1 for r, src in zip(results, texts) if not _looks_untranslated(r or "", src)
            )
            if good > best_good:
                best_good, best_results, provider = good, list(results), candidate
            if good == len(texts):
                break
            errors.append(f"{name}: only {good}/{len(texts)} lines translated")

        if best_results is None or provider is None:
            raise TranslateError(
                "Every translation engine failed. Check your internet connection "
                "or pick another engine in Settings.\n" + "\n".join(errors[-3:])
            )
        self.provider = provider
        if errors:
            self.warnings.append("Fallback: " + " · ".join(errors[-2:]))
        results = best_results

        # repair anything the provider dropped, using the free engine
        broken = [i for i, r in enumerate(results) if _looks_untranslated(r or "", texts[i])]
        if broken and provider.name != "google":
            self.warnings.append(
                f"{len(broken)} line(s) were repaired with the free Google engine."
            )
            try:
                backup = backup_provider()
                fixed = backup.translate([texts[i] for i in broken], source_lang)
                for j, idx in enumerate(broken):
                    if fixed[j] and not _looks_untranslated(fixed[j], texts[idx]):
                        results[idx] = fixed[j]
            except Exception:
                pass

        # optional proof-read pass (only chat models can do it well)
        if tier.refine and hasattr(provider, "refine"):
            try:
                if progress:
                    progress(1.0)
                results = getattr(provider, "refine")(texts, results)  # type: ignore[attr-defined]
            except Exception as exc:
                self.warnings.append(f"Proof-reading pass skipped ({exc}).")

        return [sorani.normalize_kurdish(_clean_result(r or "")) for r in results]

    def available_providers(self) -> List[dict]:
        out = [
            {"key": "google", "label": "Google Translate — free, no key, works instantly", "needs_key": False, "online": True},
            {"key": "lingva", "label": "Lingva mirror — free alternative if Google is blocked", "needs_key": False, "online": True},
            {"key": "gemini", "label": "Google Gemini — free API key, best Sorani quality (also handles adult content)", "needs_key": True, "online": True},
            {"key": "ollama", "label": "Local AI model via Ollama — offline and unlimited, needs 8 GB+ free", "needs_key": False, "online": False},
            {"key": "nllb", "label": "NLLB-200 offline model — no internet, weaker Sorani", "needs_key": False, "online": False},
            {"key": "libre", "label": "LibreTranslate server — if you run your own", "needs_key": False, "online": True},
        ]
        return out
