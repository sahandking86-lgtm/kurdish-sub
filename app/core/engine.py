"""The job engine: turns an uploaded video into a Kurdish subtitled video."""
from __future__ import annotations

import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import ffmpeg_utils as ff
from . import settings as S
from . import sorani, subtitles
from .subtitles import Cue, Unit
from .tiers import Tier, get_tier, TIERS
from .transcriber import Transcriber, TranscribeError, gpu_available, resolve_device
from .translator import TranslateError, Translator

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpg", ".mpeg",
             ".wmv", ".flv", ".ts", ".m2ts", ".3gp", ".ogv"}

STAGE_WEIGHTS = {
    "probe": (0.00, 0.02),
    "extract": (0.02, 0.09),
    "transcribe": (0.09, 0.58),
    "translate": (0.58, 0.80),
    "format": (0.80, 0.84),
    "burn": (0.84, 1.00),
}

STAGE_LABEL = {
    "probe": "Reading the video",
    "extract": "Extracting the audio",
    "transcribe": "Listening to the speech",
    "translate": "Translating into Kurdish Sorani",
    "format": "Styling the subtitles",
    "burn": "Burning the subtitles into the video",
    "queued": "Waiting in the queue",
    "done": "Finished",
    "error": "Something went wrong",
    "cancelled": "Cancelled",
}


def _demo_mode(cfg: dict) -> bool:
    return bool(cfg.get("demo")) or os.environ.get("KURDISH_SUB_DEMO") == "1"


@dataclass
class Job:
    id: str
    source_path: Path
    original_name: str
    tier_key: str
    created_at: float = field(default_factory=time.time)
    status: str = "queued"            # queued|running|waiting_review|done|error|cancelled
    stage: str = "queued"
    progress: float = 0.0
    message: str = "Waiting in the queue"
    error: str = ""
    warnings: List[str] = field(default_factory=list)
    media: Dict = field(default_factory=dict)
    language: str = ""
    language_name: str = ""
    model: str = ""
    device: str = ""
    units: List[Unit] = field(default_factory=list)
    cues: List[Cue] = field(default_factory=list)
    translations: List[str] = field(default_factory=list)
    srt_path: Optional[Path] = None
    ass_path: Optional[Path] = None
    output_path: Optional[Path] = None
    soft_path: Optional[Path] = None
    preview_path: Optional[Path] = None
    style: Dict = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _frame_cache: Dict[float, Path] = field(default_factory=dict)

    # -- paths ----------------------------------------------------------
    @property
    def work(self) -> Path:
        d = S.work_dir() / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def audio_path(self) -> Path:
        return self.work / "audio.wav"

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def eta(self) -> Optional[float]:
        if self.progress <= 0.02 or self.status not in ("running", "queued"):
            return None
        rate = self.progress / max(0.5, self.elapsed)
        if rate <= 0:
            return None
        return max(0.0, (1.0 - self.progress) / rate)

    # -- progress helpers ------------------------------------------------
    def set_stage(self, stage: str, message: str = "", fraction: float = 0.0):
        self.stage = stage
        lo, hi = STAGE_WEIGHTS.get(stage, (0.0, 1.0))
        self.progress = max(self.progress, lo + (hi - lo) * max(0.0, min(1.0, fraction)))
        if message:
            self.message = message
        elif stage in STAGE_LABEL:
            self.message = STAGE_LABEL[stage]

    def stage_progress(self, stage: str):
        lo, hi = STAGE_WEIGHTS.get(stage, (0.0, 1.0))

        def cb(fraction: float, message: Optional[str] = None):
            self.progress = max(self.progress, lo + (hi - lo) * max(0.0, min(1.0, fraction)))
            if message:
                self.message = message

        return cb

    def cancel(self):
        self.cancel_event.set()
        self.message = "Cancelling …"

    def as_dict(self, include_cues: bool = True) -> Dict:
        d = {
            "id": self.id,
            "name": self.original_name,
            "tier": self.tier_key,
            "tier_name": TIERS[self.tier_key].name if self.tier_key in TIERS else self.tier_key,
            "status": self.status,
            "stage": self.stage,
            "stage_label": STAGE_LABEL.get(self.stage, self.stage),
            "progress": round(self.progress * 100, 1),
            "message": self.message,
            "error": self.error,
            "warnings": self.warnings,
            "language": self.language,
            "language_name": self.language_name,
            "model": self.model,
            "device": self.device,
            "duration": round(self.media.get("duration", 0.0), 2),
            "media": self.media,
            "elapsed": round(self.elapsed, 1),
            "eta": round(self.eta(), 1) if self.eta() is not None else None,
            "cue_count": len(self.cues),
            "has_srt": bool(self.srt_path and self.srt_path.exists()),
            "has_output": bool(self.output_path and self.output_path.exists()),
            "has_soft": bool(self.soft_path and self.soft_path.exists()),
            "output_name": self.output_path.name if self.output_path else "",
            "output_size": (self.output_path.stat().st_size
                            if self.output_path and self.output_path.exists() else 0),
            "created_at": self.created_at,
        }
        if include_cues:
            d["cues"] = [
                {"start": round(c.start, 2), "end": round(c.end, 2), "text": c.text, "source": c.source}
                for c in self.cues
            ]
        return d


class Engine:
    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or S.load_settings()
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ksub-job")

    # ------------------------------------------------------------------
    def settings(self) -> dict:
        return S.load_settings()

    def create_job(self, source: Path, original_name: str, tier_key: str,
                   cfg: Optional[dict] = None) -> Job:
        jid = uuid.uuid4().hex[:12]
        job = Job(id=jid, source_path=source, original_name=original_name, tier_key=tier_key)
        job.style = self._style_from_settings(cfg or self.settings())
        with self.lock:
            self.jobs[jid] = job
        self.pool.submit(self._run_job, job)
        return job

    def get(self, jid: str) -> Optional[Job]:
        with self.lock:
            return self.jobs.get(jid)

    def all(self) -> List[Job]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    # ------------------------------------------------------------------
    def _style_from_settings(self, cfg: dict) -> Dict:
        return {
            "font": cfg.get("font") or "Noto Naskh Arabic",
            "font_scale": float(cfg.get("font_scale") or 1.0),
            "position": cfg.get("position") or "bottom",
            "bilingual": bool(cfg.get("bilingual")),
            "max_chars": int(cfg.get("max_chars") or 42),
            "crf": int(cfg.get("crf") or 20),
            "preset": cfg.get("preset") or "medium",
            "encoder": cfg.get("encoder") or "auto",
        }

    # ------------------------------------------------------------------
    def _run_job(self, job: Job):
        cfg = self.settings()
        tier = get_tier(job.tier_key)
        job.status = "running"
        job.started_at = time.time()
        try:
            # 1. probe ---------------------------------------------------
            job.set_stage("probe", "Reading the video file …", 0.2)
            media = ff.probe(job.source_path)
            job.media = media
            if not media.get("duration"):
                raise RuntimeError(
                    "This file does not look like a video that can be read. "
                    "Try an MP4, MKV or MOV file."
                )
            if not media.get("has_audio"):
                raise RuntimeError("This video has no audio track, so there is nothing to subtitle.")
            job.set_stage("probe", f"Video: {media['width']}x{media['height']}", 1.0)

            # 2. audio ---------------------------------------------------
            job.set_stage("extract", "Extracting the audio track …", 0.05)
            ff.extract_audio(job.source_path, job.audio_path,
                             progress=lambda f: job.set_stage("extract", None, f),
                             duration=media["duration"])
            if job.cancel_event.is_set():
                raise _Cancelled()
            job.set_stage("extract", "Audio ready.", 1.0)

            # 3. speech --------------------------------------------------
            job.set_stage("transcribe", "Loading the speech model …", 0.01)
            if _demo_mode(cfg):
                from .demo import DemoTranscriber
                transcriber = DemoTranscriber(cfg)
            else:
                transcriber = Transcriber(cfg)
            result = transcriber.transcribe(
                job.audio_path, tier, duration=media["duration"],
                progress=lambda f, m=None: job.set_stage("transcribe", m, f),
                cancel=job.cancel_event,
                on_status=lambda m: setattr(job, "message", m),
            )
            if job.cancel_event.is_set() or result.get("cancelled"):
                raise _Cancelled()
            job.language = result.get("language", "")
            job.language_name = result.get("language_name", "")
            job.model = result.get("model", "")
            job.device = result.get("device", "")
            segments = result.get("segments") or []
            if not segments:
                raise RuntimeError(
                    "No speech was recognised in this video. Is there really talking in it?"
                )

            # 4. build subtitle units ------------------------------------
            units = subtitles.build_units(segments, tier)
            if not units:
                raise RuntimeError("Nothing to translate was found in the audio.")
            job.units = units
            job.set_stage("transcribe", f"{len(units)} subtitle lines detected.", 1.0)

            # 5. translate -----------------------------------------------
            job.set_stage("translate", "Connecting to the translation engine …", 0.01)
            if _demo_mode(cfg):
                from .demo import DemoTranslator
                translator = DemoTranslator(cfg)
            else:
                translator = Translator(cfg)
            translations = translator.translate(
                [u.source for u in units], job.language or "auto", tier,
                progress=lambda f: job.set_stage("translate", None, f),
            )
            if job.cancel_event.is_set():
                raise _Cancelled()
            job.translations = translations
            for w in getattr(translator, "warnings", []) or []:
                job.warnings.append(w)
            missing = sum(1 for t in translations if not t.strip())
            if missing:
                job.warnings.append(f"{missing} line(s) could not be translated and were skipped.")

            # 6. format --------------------------------------------------
            job.set_stage("format", "Building the subtitle file …", 0.3)
            job.cues = subtitles.assemble_cues(units, translations, tier,
                                               max_chars=job.style["max_chars"])
            stem = S.safe_name(job.original_name)
            out_dir = S.output_dir() / job.id
            out_dir.mkdir(parents=True, exist_ok=True)
            job.srt_path = subtitles.write_srt(job.cues, out_dir / f"{stem}.ckb.srt")
            subtitles.write_vtt(job.cues, out_dir / f"{stem}.ckb.vtt")
            job.ass_path = self._write_ass(job)
            job.set_stage("format", f"{len(job.cues)} Kurdish subtitles created.", 1.0)
            self._mirror(job)

            # 7. first preview frame -------------------------------------
            self._make_preview(job)

            if not job.cues:
                raise RuntimeError("The subtitle file ended up empty.")

            if cfg.get("auto_burn", True):
                self._burn(job, cfg)
                job.finished_at = time.time()
                job.status = "done"
                job.progress = 1.0
                job.stage = "done"
                job.message = f"Done — Kurdish subtitles burned into “{job.output_path.name}”"
            else:
                job.status = "waiting_review"
                job.progress = 0.85
                job.stage = "done"
                job.message = "Subtitles are ready — check the preview, then burn them in."
        except _Cancelled:
            job.status = "cancelled"
            job.stage = "cancelled"
            job.message = "Cancelled."
            job.finished_at = time.time()
        except (TranslateError, TranscribeError, RuntimeError) as exc:
            job.status = "error"
            job.stage = "error"
            job.error = str(exc)
            job.message = str(exc).splitlines()[0][:220]
            job.finished_at = time.time()
        except Exception as exc:  # pragma: no cover - unexpected
            job.status = "error"
            job.stage = "error"
            job.error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-1200:]}"
            job.message = str(exc)[:220]
            job.finished_at = time.time()

    # ------------------------------------------------------------------
    def _ass_dims(self, job: Job) -> tuple:
        w = job.media.get("display_width") or job.media.get("width") or 1280
        h = job.media.get("display_height") or job.media.get("height") or 720
        return int(w), int(h)

    def _write_ass(self, job: Job) -> Path:
        w, h = self._ass_dims(job)
        st = job.style
        path = (job.ass_path.parent if job.ass_path else job.work) / "sub.ass"
        path.parent.mkdir(parents=True, exist_ok=True)
        return subtitles.write_ass(
            job.cues, path, w, h,
            font=st.get("font") or "Noto Naskh Arabic",
            font_scale=float(st.get("font_scale") or 1.0),
            position=st.get("position") or "bottom",
            bilingual=bool(st.get("bilingual")),
        )

    def _mirror(self, job: Job):
        """Copy the finished files somewhere else (Google Drive in cloud mode)."""
        target = S.mirror_dir()
        if target is None:
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        for path in (job.srt_path, job.output_path):
            if not path or not Path(path).exists():
                continue
            try:
                job.message = f"Copying “{Path(path).name}” to your Drive folder …"
                shutil.copy2(path, target / Path(path).name)
            except Exception as exc:
                job.warnings.append(
                    f"The copy in {target} could not be saved ({exc}). "
                    "The file is still available here for download."
                )

    def _make_preview(self, job: Job):
        if not job.ass_path or not job.cues:
            return
        # pick a moment where a subtitle is actually on screen
        target = job.media.get("duration", 0) * 0.35
        cue = min(job.cues, key=lambda c: abs(((c.start + c.end) / 2) - target))
        t = (cue.start + min(cue.end, cue.start + 2.2)) / 2
        png = job.work / "preview.png"
        w, h = self._ass_dims(job)
        scale = 1280.0 / max(1, w)
        got = ff.preview_frame(job.source_path, job.ass_path, t, png,
                               width=1280, height=max(2, int(round(h * scale))))
        if got:
            job.preview_path = got

    def _burn(self, job: Job, cfg: dict):
        job.set_stage("burn", "Burning the Kurdish subtitles into the video …", 0.01)
        stem = S.safe_name(job.original_name)
        out_dir = S.output_dir() / job.id
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{stem}.kurdish.mp4"
        try:
            ff.burn(
                job.source_path, job.ass_path, out,
                crf=int(job.style.get("crf") or 20),
                preset=job.style.get("preset") or "medium",
                duration=job.media.get("duration", 0.0),
                progress=lambda f: job.set_stage("burn", None, f),
                cancel=job.cancel_event,
                encoder=job.style.get("encoder") or "auto",
            )
        except RuntimeError as exc:
            if "__cancelled__" in str(exc):
                raise _Cancelled()
            raise
        job.output_path = out
        job.set_stage("burn", f"Saved “{out.name}”", 1.0)
        self._mirror(job)

    # ------------------------------------------------------------------
    # public actions
    # ------------------------------------------------------------------
    def restyle(self, jid: str, style: Dict, burn: bool = False) -> Job:
        job = self.get(jid)
        if not job:
            raise KeyError(jid)
        for k in ("font", "font_scale", "position", "bilingual", "max_chars", "crf", "preset"):
            if k in style and style[k] is not None:
                job.style[k] = style[k]
        if "max_chars" in style and job.units and job.translations:
            tier = get_tier(job.tier_key)
            job.cues = subtitles.assemble_cues(job.units, job.translations, tier,
                                               max_chars=int(job.style["max_chars"]))
            if job.srt_path:
                subtitles.write_srt(job.cues, job.srt_path)
        job.ass_path = self._write_ass(job)
        self._make_preview(job)
        if burn:
            self._burn(job, self.settings())
            job.status = "done"
            job.progress = 1.0
            job.message = f"Done — “{job.output_path.name}” is ready."
        return job

    def burn_job(self, jid: str) -> Job:
        job = self.get(jid)
        if not job:
            raise KeyError(jid)
        job.status = "running"
        job.cancel_event.clear()
        job.started_at = job.started_at or time.time()

        def work():
            try:
                job.ass_path = self._write_ass(job)
                self._burn(job, self.settings())
                job.status = "done"
                job.progress = 1.0
                job.message = f"Done — “{job.output_path.name}” is ready."
            except _Cancelled:
                job.status = "cancelled"
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                job.message = str(exc)[:200]
            job.finished_at = time.time()

        self.pool.submit(work)
        return job

    def update_cue(self, jid: str, index: int, text: Optional[str] = None,
                   start: Optional[float] = None, end: Optional[float] = None) -> Job:
        job = self.get(jid)
        if not job:
            raise KeyError(jid)
        if not (0 <= index < len(job.cues)):
            raise IndexError(index)
        cue = job.cues[index]
        if text is not None:
            cue.text = sorani.normalize_kurdish(text)
        if start is not None:
            cue.start = max(0.0, float(start))
        if end is not None:
            cue.end = max(cue.start + 0.2, float(end))
        if job.srt_path:
            subtitles.write_srt(job.cues, job.srt_path)
        job.ass_path = self._write_ass(job)
        return job

    def preview_at(self, jid: str, t: float) -> Optional[Path]:
        job = self.get(jid)
        if not job or not job.ass_path:
            return None
        key = round(float(t), 1)
        cached = job._frame_cache.get(key)
        if cached and cached.exists():
            return cached
        png = job.work / f"frame_{int(key * 10):06d}.png"
        w, h = self._ass_dims(job)
        scale = 1280.0 / max(1, w)
        got = ff.preview_frame(job.source_path, job.ass_path, float(t), png,
                               width=1280, height=max(2, int(round(h * scale))))
        if got:
            job._frame_cache[key] = got
            if len(job._frame_cache) > 40:  # keep the folder tidy
                job._frame_cache.clear()
        return got

    def delete(self, jid: str):
        job = self.get(jid)
        if not job:
            return
        job.cancel()
        try:
            shutil.rmtree(job.work, ignore_errors=True)
            shutil.rmtree(S.output_dir() / job.id, ignore_errors=True)
        except Exception:
            pass
        with self.lock:
            self.jobs.pop(jid, None)


class _Cancelled(Exception):
    pass


def system_report(cfg: Optional[dict] = None) -> Dict:
    cfg = cfg or S.load_settings()
    device, ctype = resolve_device(cfg)
    try:
        ffmpeg_ok = Path(ff.ffmpeg_exe()).exists() or shutil.which(ff.ffmpeg_exe()) is not None
    except Exception:
        ffmpeg_ok = False
    enc = ff.encoder_info()
    return {
        "gpu": gpu_available(),
        "device": device,
        "compute_type": ctype,
        "encoder": enc["encoder"],
        "hardware_encoder": enc["hardware"],
        "cloud": S.cloud_mode(),
        "mirror_dir": str(S.mirror_dir() or ""),
        "ffmpeg": ffmpeg_ok,
        "ffmpeg_path": ff.ffmpeg_exe(),
        "free_space_gb": S.free_space_gb(S.work_dir()),
        "app_dir": str(S.app_dir()),
        "output_dir": str(S.output_dir()),
        "demo": _demo_mode(cfg),
    }
