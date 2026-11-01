"""Speech recognition with faster-whisper (bundled, no accounts needed)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import settings
from .tiers import Tier

# VAD profiles: (threshold, min_speech_ms, min_silence_ms, speech_pad_ms)
VAD_PROFILES = {
    "off": None,
    "loose": (0.30, 150, 1200, 250),
    "balanced": (0.50, 200, 700, 300),
    "strict": (0.60, 250, 500, 400),
}

LANG_NAMES = {
    "en": "English", "ku": "Kurdish", "ckb": "Sorani Kurdish", "ar": "Arabic",
    "fa": "Persian", "tr": "Turkish", "fr": "French", "de": "German",
    "es": "Spanish", "it": "Italian", "ru": "Russian", "pt": "Portuguese",
    "hi": "Hindi", "ur": "Urdu", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "id": "Indonesian", "nl": "Dutch", "pl": "Polish",
    "sv": "Swedish", "uk": "Ukrainian", "az": "Azerbaijani", "he": "Hebrew",
}


class TranscribeError(RuntimeError):
    pass


def gpu_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(cfg: dict) -> tuple:
    """(device, compute_type) honouring the user preference when possible."""
    want = (cfg.get("device") or "auto").lower()
    ctype = (cfg.get("compute_type") or "auto").lower()
    has_gpu = gpu_available()
    if want == "cuda" and not has_gpu:
        want = "cpu"
    if want == "auto":
        want = "cuda" if has_gpu else "cpu"
    if ctype == "auto":
        ctype = "float16" if want == "cuda" else "int8"
    if want == "cpu" and ctype in ("float16", "int8_float16"):
        ctype = "int8"
    return want, ctype


_MODEL_CACHE: Dict[str, object] = {}
_CACHE_LOCK = threading.Lock()


def _dir_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


class DownloadWatcher(threading.Thread):
    """Reports model-download progress by watching the cache folder grow."""

    def __init__(self, on_update: Callable[[str], None], baseline_mb: float = 0.0):
        super().__init__(daemon=True)
        self.on_update = on_update
        self.baseline = baseline_mb
        self._stop = threading.Event()

    def run(self):
        last = -1
        while not self._stop.wait(1.2):
            mb = _dir_size_mb(settings.models_dir()) - self.baseline
            whole = int(mb // 5) * 5
            if whole != last and whole >= 5:
                last = whole
                try:
                    self.on_update(
                        f"Downloading the speech model once (free) … {whole} MB saved"
                    )
                except Exception:
                    pass

    def stop(self):
        self._stop.set()


class Transcriber:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = None
        self.model_name = ""
        self.device = ""
        self.compute_type = ""
        self.load_note = ""

    # ------------------------------------------------------------------
    def load(self, tier: Tier, on_status: Optional[Callable[[str], None]] = None,
             cancel: Optional[threading.Event] = None):
        device, ctype = resolve_device(self.cfg)
        self.device, self.compute_type = device, ctype
        candidates = [tier.model] + [m for m in tier.fallback_models if m != tier.model]

        def status(msg: str):
            if on_status:
                try:
                    on_status(msg)
                except Exception:
                    pass

        errors = []
        for name in candidates:
            key = f"{name}|{device}|{ctype}"
            with _CACHE_LOCK:
                cached = _MODEL_CACHE.get(key)
            if cached is not None:
                self.model, self.model_name = cached, name
                status(f"Speech model ready ({name}, {device}).")
                return self.model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                raise TranscribeError(
                    "faster-whisper is not installed. Run: pip install faster-whisper"
                ) from exc

            baseline = 0.0
            watcher = None
            if on_status:
                baseline = _dir_size_mb(settings.models_dir())
                watcher = DownloadWatcher(on_status, baseline)
                watcher.start()
            status(f"Loading speech model “{name}” on {device} ({ctype}) …")
            try:
                model = WhisperModel(
                    name,
                    device=device,
                    compute_type=ctype,
                    download_root=str(settings.models_dir()),
                    cpu_threads=int(self.cfg.get("cpu_threads") or 0),
                    num_workers=1,
                )
                with _CACHE_LOCK:
                    _MODEL_CACHE[key] = model
                self.model, self.model_name = model, name
                if watcher:
                    watcher.stop()
                status(f"Speech model ready ({name}, {device}).")
                return model
            except Exception as exc:
                if watcher:
                    watcher.stop()
                errors.append(f"{name}: {exc}")
                if cancel and cancel.is_set():
                    raise TranscribeError("Cancelled.")
                status(f"Model “{name}” could not be used, trying the next size …")
                continue
        raise TranscribeError(
            "The speech model could not be downloaded.\n\n"
            "The models come from huggingface.co and are downloaded only once "
            f"(they are stored in {settings.models_dir()}).\n"
            "Check your internet connection, VPN or proxy and start again — "
            "everything else in the app is already installed.\n\n"
            + "\n".join(errors[-2:])
        )

    # ------------------------------------------------------------------
    def transcribe(self, audio: Path, tier: Tier, duration: float = 0.0,
                   progress: Optional[Callable[[float, str], None]] = None,
                   cancel: Optional[threading.Event] = None,
                   on_status: Optional[Callable[[str], None]] = None) -> Dict:
        model = self.load(tier, on_status=on_status, cancel=cancel)

        vad = VAD_PROFILES.get(tier.vad, VAD_PROFILES["balanced"])
        kwargs = dict(
            language=None if (self.cfg.get("source_language") or "auto") == "auto"
            else self.cfg.get("source_language"),
            beam_size=tier.beam_size,
            word_timestamps=tier.word_timestamps,
            vad_filter=vad is not None,
            condition_on_previous_text=bool(tier.sentence_grouping),
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            no_speech_threshold=0.6 if tier.vad != "loose" else 0.75,
            compression_ratio_threshold=2.4,
        )
        if vad is not None:
            kwargs["vad_parameters"] = dict(
                threshold=vad[0],
                min_speech_duration_ms=vad[1],
                min_silence_duration_ms=vad[2],
                speech_pad_ms=vad[3],
            )
        try:
            segments_iter, info = model.transcribe(str(audio), **kwargs)
        except Exception as exc:
            raise TranscribeError(f"Speech recognition failed: {exc}") from exc

        language = getattr(info, "language", "") or ""
        lang_prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        total = duration or float(getattr(info, "duration", 0.0) or 0.0)

        segments: List[dict] = []
        for seg in segments_iter:
            if cancel and cancel.is_set():
                break
            text = (seg.text or "").strip()
            words = []
            if getattr(seg, "words", None):
                words = [
                    {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                     "prob": round(getattr(w, "probability", 0.0) or 0.0, 3)}
                    for w in seg.words
                ]
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": text,
                "words": words,
                "avg_logprob": round(getattr(seg, "avg_logprob", 0.0) or 0.0, 3),
                "no_speech_prob": round(getattr(seg, "no_speech_prob", 0.0) or 0.0, 3),
            })
            if progress and total:
                progress(min(0.99, seg.end / total),
                         f"Listening … {int(seg.end)}s / {int(total)}s")
        if progress:
            progress(1.0, "Speech recognition finished.")

        return {
            "language": language,
            "language_name": LANG_NAMES.get(language, language.upper() or "unknown"),
            "language_probability": round(lang_prob, 3),
            "duration": total,
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "segments": segments,
            "cancelled": bool(cancel and cancel.is_set()),
        }
