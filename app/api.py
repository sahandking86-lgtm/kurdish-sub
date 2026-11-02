"""HTTP API + static hosting for the Kurdish Sub desktop app."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import settings as S
from .core import subtitles
from .core.engine import Engine, system_report
from .core.tiers import get_tier, tiers_payload, TIER_ORDER
from .core.translator import Translator

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="Kurdish Sub", version="1.0.0", docs_url="/api/docs")
engine = Engine()

UPLOAD_DIR = S.work_dir() / "uploads"
CHUNK_DIR = UPLOAD_DIR / "chunks"

# Cloud launchers set this so a public link is useless to anybody else.
ACCESS_TOKEN = os.environ.get("KURDISH_SUB_TOKEN", "").strip()

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpg", ".mpeg",
              ".wmv", ".flv", ".ts", ".m2ts", ".3gp", ".ogv"}

_UID_RE = re.compile(r"^[a-f0-9]{12,40}$")


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
class SettingsPatch(BaseModel):
    provider: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    libre_url: Optional[str] = None
    device: Optional[str] = None
    compute_type: Optional[str] = None
    source_language: Optional[str] = None
    font: Optional[str] = None
    font_scale: Optional[float] = None
    position: Optional[str] = None
    bilingual: Optional[bool] = None
    max_chars: Optional[int] = None
    crf: Optional[int] = None
    preset: Optional[str] = None
    keep_temp: Optional[bool] = None
    auto_burn: Optional[bool] = None
    open_browser: Optional[bool] = None


class UploadStart(BaseModel):
    name: str
    size: int = 0


class UploadFinish(BaseModel):
    tier: str = "medium"


class LocalFile(BaseModel):
    path: str
    tier: str = "medium"


class PathProbe(BaseModel):
    path: str


class StyleRequest(BaseModel):
    font: Optional[str] = None
    font_scale: Optional[float] = None
    position: Optional[str] = None
    bilingual: Optional[bool] = None
    max_chars: Optional[int] = None
    crf: Optional[int] = None
    preset: Optional[str] = None
    burn: bool = False


class CuePatch(BaseModel):
    index: int
    text: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def public_settings(cfg: dict) -> dict:
    out = dict(cfg)
    out["gemini_api_key"] = ""
    out["gemini_api_key_set"] = bool(cfg.get("gemini_api_key"))
    return out


def job_or_404(jid: str):
    job = engine.get(jid)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# --------------------------------------------------------------------------
# app info
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    cfg = S.load_settings()
    return {
        "ok": True,
        "version": app.version,
        "system": system_report(cfg),
        "tiers": tiers_payload(),
        "default_tier": cfg.get("default_tier", "medium"),
    }


@app.get("/api/tiers")
def tiers():
    return {"tiers": tiers_payload(), "order": TIER_ORDER}


@app.get("/api/settings")
def get_settings():
    cfg = S.load_settings()
    providers = Translator(cfg).available_providers()
    return {"settings": public_settings(cfg), "providers": providers}


@app.post("/api/settings")
def post_settings(patch: SettingsPatch):
    data = patch.model_dump(exclude_none=True)
    if "gemini_api_key" in data and not str(data["gemini_api_key"]).strip():
        data.pop("gemini_api_key")
    if "max_chars" in data:
        data["max_chars"] = max(16, min(90, int(data["max_chars"])))
    if "font_scale" in data:
        data["font_scale"] = max(0.6, min(1.8, float(data["font_scale"])))
    if "position" in data and data["position"] not in ("bottom", "lower", "middle", "top"):
        data.pop("position")
    cfg = S.save_settings(data)
    return {"settings": public_settings(cfg)}


@app.get("/api/languages")
def languages():
    from .core.transcriber import LANG_NAMES
    return {"languages": [{"code": "auto", "name": "Detect automatically"}] +
            [{"code": k, "name": v} for k, v in sorted(LANG_NAMES.items())]}


# --------------------------------------------------------------------------
# creating jobs
# --------------------------------------------------------------------------
@app.post("/api/jobs/upload")
async def upload_video(file: UploadFile = File(...), tier: str = Form("medium")):
    if tier not in TIER_ORDER:
        raise HTTPException(400, "Unknown accuracy level")
    name = file.filename or "video.mp4"
    ext = Path(name).suffix.lower()
    if ext and ext not in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpg",
                           ".mpeg", ".wmv", ".flv", ".ts", ".m2ts", ".3gp", ".ogv"}:
        raise HTTPException(400, f"“{ext}” is not a video format this app can read.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:10]}{ext or '.mp4'}"
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(4 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)
    await file.close()
    if size < 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That file looks empty.")
    job = engine.create_job(dest, name, tier)
    return {"job": job.as_dict(include_cues=False)}


@app.post("/api/jobs/local")
def create_from_local(req: LocalFile):
    if req.tier not in TIER_ORDER:
        raise HTTPException(400, "Unknown accuracy level")
    p = Path(req.path).expanduser()
    if not p.exists() or not p.is_file():
        raise HTTPException(400, "That file does not exist on this computer.")
    if p.stat().st_size < 1024:
        raise HTTPException(400, "That file looks empty.")
    job = engine.create_job(p, p.name, req.tier)
    return {"job": job.as_dict(include_cues=False)}


@app.post("/api/probe")
def probe_path(req: PathProbe):
    """Read duration / resolution / size of a file on this computer."""
    from .core import ffmpeg_utils as ff

    p = Path(req.path).expanduser()
    if not p.exists() or not p.is_file():
        raise HTTPException(400, "That file does not exist on this computer.")
    media = ff.probe(p)
    return {"ok": True, "name": p.name, "media": media}


@app.post("/api/pick-file")
def pick_file():
    """Open the OS file dialog on the machine running the app."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return JSONResponse({"ok": False, "reason": f"No native dialog available ({exc})"})

    result: dict = {}

    def run():
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="Choose a video",
                filetypes=[("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv *.flv *.ts"),
                           ("All files", "*.*")],
            )
            root.destroy()
            result["path"] = path
        except Exception as exc:  # pragma: no cover - depends on desktop
            result["error"] = str(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=180)
    if "error" in result:
        return JSONResponse({"ok": False, "reason": result["error"]})
    if not result.get("path"):
        return JSONResponse({"ok": False, "reason": "cancelled"})
    return {"ok": True, "path": result["path"], "name": Path(result["path"]).name}


# --------------------------------------------------------------------------
# resumable uploads (a tunnel only allows ~100 MB per request)
# --------------------------------------------------------------------------
def _chunk_root(uid: str) -> Path:
    if not _UID_RE.match(uid or ""):
        raise HTTPException(400, "Unknown upload")
    return CHUNK_DIR / uid


def _chunk_meta(uid: str) -> dict:
    root = _chunk_root(uid)
    meta = root / "meta.json"
    if not meta.exists():
        raise HTTPException(404, "This upload expired. Choose the video again.")
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(400, "This upload is damaged. Choose the video again.")


def _clean_old_chunks(max_age_hours: float = 24.0):
    if not CHUNK_DIR.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for d in CHUNK_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


@app.post("/api/uploads/start")
def upload_start(req: UploadStart):
    """Begin a resumable upload. Works through tunnels and flaky connections."""
    name = Path(req.name or "video.mp4").name
    ext = Path(name).suffix.lower()
    if ext and ext not in VIDEO_EXTS:
        raise HTTPException(400, f"“{ext}” is not a video format this app can read.")
    _clean_old_chunks()
    uid = uuid.uuid4().hex
    root = CHUNK_DIR / uid
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(
        json.dumps({"name": name, "size": int(req.size or 0), "created": time.time()}),
        encoding="utf-8",
    )
    return {"upload_id": uid, "chunk_size": 8 * 1024 * 1024}


@app.post("/api/uploads/{uid}/part")
async def upload_part(uid: str, index: int, request: Request):
    """Store one piece (raw bytes) of a bigger video."""
    _chunk_meta(uid)
    if index < 0 or index > 100000:
        raise HTTPException(400, "Bad chunk number")
    root = _chunk_root(uid)
    dest = root / f"{index:06d}.part"
    size = 0
    tmp = dest.with_suffix(".tmp")
    with tmp.open("wb") as out:
        async for block in request.stream():
            if not block:
                continue
            size += len(block)
            if size > 32 * 1024 * 1024:
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "That piece is too large")
            out.write(block)
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "Empty piece")
    tmp.replace(dest)
    return {"ok": True, "index": index, "bytes": size}


@app.get("/api/uploads/{uid}")
def upload_status(uid: str):
    meta = _chunk_meta(uid)
    root = _chunk_root(uid)
    parts = sorted(int(p.stem) for p in root.glob("*.part") if p.stem.isdigit())
    return {"upload_id": uid, "name": meta.get("name", "video"), "parts": parts,
            "bytes": sum(p.stat().st_size for p in root.glob("*.part"))}


@app.post("/api/uploads/{uid}/finish")
def upload_finish(uid: str, req: UploadFinish):
    """Glue the pieces together and start the job."""
    if req.tier not in TIER_ORDER:
        raise HTTPException(400, "Unknown accuracy level")
    meta = _chunk_meta(uid)
    root = _chunk_root(uid)
    parts = sorted((int(p.stem), p) for p in root.glob("*.part") if p.stem.isdigit())
    if not parts:
        raise HTTPException(400, "No video data arrived. Try again.")
    if [i for i, _ in parts] != list(range(len(parts))):
        raise HTTPException(400, "Some pieces of the video are missing. Try again.")
    name = meta.get("name") or "video.mp4"
    ext = Path(name).suffix.lower() or ".mp4"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:10]}{ext}"
    try:
        with dest.open("wb") as out:
            for _, part in parts:
                with part.open("rb") as fh:
                    shutil.copyfileobj(fh, out, 4 * 1024 * 1024)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"The video could not be assembled: {exc}")
    shutil.rmtree(root, ignore_errors=True)
    if not dest.exists() or dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That file looks empty.")
    job = engine.create_job(dest, name, req.tier)
    return {"job": job.as_dict(include_cues=False)}


# --------------------------------------------------------------------------
# job status / files
# --------------------------------------------------------------------------
@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [j.as_dict(include_cues=False) for j in engine.all()]}


@app.get("/api/jobs/{jid}")
def job_status(jid: str, cues: bool = True):
    job = job_or_404(jid)
    return {"job": job.as_dict(include_cues=cues)}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    job = job_or_404(jid)
    job.cancel()
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    job = job_or_404(jid)
    src = job.source_path
    engine.delete(jid)
    try:
        if src and src.parent == UPLOAD_DIR:
            src.unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/jobs/{jid}/restyle")
def restyle(jid: str, req: StyleRequest):
    job = job_or_404(jid)
    style = req.model_dump(exclude_none=True)
    burn = bool(style.pop("burn", False))
    if burn and job.status in ("running", "queued"):
        raise HTTPException(409, "The job is still running.")
    engine.restyle(jid, style, burn=False)
    if burn:
        engine.burn_job(jid)          # renders in the background, never blocks the request
    return {"job": job.as_dict(include_cues=False)}


@app.post("/api/jobs/{jid}/burn")
def burn(jid: str):
    job = job_or_404(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "The job is still running.")
    if not job.cues:
        raise HTTPException(400, "There are no subtitles to burn yet.")
    engine.burn_job(jid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/cue")
def edit_cue(jid: str, patch: CuePatch):
    job = job_or_404(jid)
    try:
        engine.update_cue(jid, patch.index, text=patch.text, start=patch.start, end=patch.end)
    except IndexError:
        raise HTTPException(400, "That subtitle line does not exist.")
    return {"ok": True, "cues": len(job.cues)}


@app.get("/api/jobs/{jid}/preview")
def preview(jid: str, t: float = 0.0):
    job = job_or_404(jid)
    png = engine.preview_at(jid, t)
    if not png or not png.exists():
        raise HTTPException(404, "No preview available")
    return FileResponse(png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{jid}/reveal")
def reveal_output(jid: str):
    """Open the folder that holds the finished video in the file manager."""
    import subprocess
    import sys

    job = job_or_404(jid)
    target = job.output_path or job.source_path
    if not target or not Path(target).exists():
        raise HTTPException(404, "Nothing to show yet")
    folder = Path(target).parent
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        return JSONResponse({"ok": False, "folder": str(folder), "reason": str(exc)})
    return {"ok": True, "folder": str(folder)}


@app.get("/api/jobs/{jid}/srt")
def get_srt(jid: str):
    job = job_or_404(jid)
    if not job.srt_path or not job.srt_path.exists():
        raise HTTPException(404, "No subtitle file yet")
    return FileResponse(job.srt_path, media_type="application/x-subrip",
                        filename=job.srt_path.name)


@app.get("/api/jobs/{jid}/ass")
def get_ass(jid: str):
    job = job_or_404(jid)
    if not job.ass_path or not job.ass_path.exists():
        raise HTTPException(404, "No subtitle file yet")
    return FileResponse(job.ass_path, media_type="text/plain", filename="subtitles.ass")


@app.get("/api/jobs/{jid}/video")
def get_video(jid: str, download: bool = False):
    job = job_or_404(jid)
    if not job.output_path or not job.output_path.exists():
        raise HTTPException(404, "The final video is not ready yet")
    return FileResponse(
        job.output_path,
        media_type="video/mp4",
        filename=job.output_path.name if download else None,
    )


@app.get("/api/jobs/{jid}/source")
def get_source(jid: str):
    job = job_or_404(jid)
    if not job.source_path.exists():
        raise HTTPException(404, "Source file is gone")
    guess = {".webm": "video/webm", ".ogv": "video/ogg", ".mov": "video/quicktime",
             ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
    mime = guess.get(job.source_path.suffix.lower(), "video/mp4")
    return FileResponse(job.source_path, media_type=mime)


# --------------------------------------------------------------------------
# private access (only active when KURDISH_SUB_TOKEN is set)
# --------------------------------------------------------------------------
_TOKEN_PAGE = """<!doctype html><meta charset="utf-8">
<title>Kurdish Sub — private link</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;
background:#07171c;color:#e8f6f2}a{color:#22d3a7}code{background:#122a31;padding:2px 6px;border-radius:6px}
h1{font-size:22px}</style>
<h1>This Kurdish Sub link is private</h1>
<p>Open the <b>full address</b> that your Colab notebook printed — it has a
<code>?token=…</code> part at the end. After that, this browser is remembered.</p>
<p>If you bookmarked this page, delete the bookmark and copy the link from the
notebook again.</p>
"""


@app.middleware("http")
async def private_access(request: Request, call_next):
    if not ACCESS_TOKEN:
        return await call_next(request)
    given = (
        request.query_params.get("token")
        or request.headers.get("x-ksub-token")
        or request.cookies.get("ksub_token")
        or ""
    )
    if given != ACCESS_TOKEN:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "This app is private. Open the link with ?token=… that Colab printed."},
                status_code=401,
            )
        return HTMLResponse(_TOKEN_PAGE, status_code=401)
    response = await call_next(request)
    if request.query_params.get("token") == ACCESS_TOKEN:
        response.set_cookie("ksub_token", ACCESS_TOKEN, max_age=7 * 24 * 3600,
                            httponly=True, samesite="lax", path="/")
    return response


# --------------------------------------------------------------------------
# static UI
# --------------------------------------------------------------------------
app.mount("/fonts", StaticFiles(directory=str(WEB_DIR / "fonts")), name="fonts")
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.middleware("http")
async def no_cache_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/index.html") or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store"
    return response
