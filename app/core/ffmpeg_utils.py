"""Thin, dependency-light wrappers around ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import settings

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _no_window_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def ffmpeg_exe() -> str:
    env = os.environ.get("KURDISH_SUB_FFMPEG")
    if env and Path(env).exists():
        return env
    local = settings.project_root() / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.exists():
        return str(local)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # optional pip package that ships a static build
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def ffprobe_exe() -> Optional[str]:
    env = os.environ.get("KURDISH_SUB_FFPROBE")
    if env and Path(env).exists():
        return env
    local = settings.project_root() / "bin" / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    if local.exists():
        return str(local)
    return shutil.which("ffprobe")


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        **_no_window_kwargs(),
    )


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------
def _parse_duration_from_stderr(text: str) -> Tuple[float, int, int, float]:
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    w = h = 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    fps = 0.0
    m = re.search(r"(\d+\.?\d*)\s*fps", text)
    if m:
        fps = float(m.group(1))
    return dur, w, h, fps


def _rotation_from_tags(stream: dict) -> int:
    rot = 0
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rot = int(float(tags["rotate"]))
        except Exception:
            rot = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except Exception:
                pass
    return rot


def probe(path: Path) -> Dict:
    """Return duration/size/fps/audio info for a media file (never raises)."""
    info: Dict = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "has_audio": False,
        "audio_channels": 0,
        "audio_sample_rate": 0,
        "video_codec": "",
        "audio_codec": "",
        "bitrate": 0,
        "size": path.stat().st_size if path.exists() else 0,
        "rotation": 0,
        "display_width": 0,
        "display_height": 0,
        "probe_ok": False,
    }
    fp = ffprobe_exe()
    if fp:
        try:
            out = run(
                [
                    fp, "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(path),
                ],
                timeout=120,
            )
            data = json.loads(out.stdout.decode("utf-8", "ignore") or "{}")
            fmt = data.get("format") or {}
            info["duration"] = float(fmt.get("duration") or 0.0)
            info["bitrate"] = int(float(fmt.get("bit_rate") or 0))
            for s in data.get("streams") or []:
                if s.get("codec_type") == "video" and not info["video_codec"]:
                    info["video_codec"] = s.get("codec_name") or ""
                    info["width"] = int(s.get("width") or 0)
                    info["height"] = int(s.get("height") or 0)
                    fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
                    try:
                        num, _, den = fr.partition("/")
                        info["fps"] = round(float(num) / float(den or 1), 3)
                    except Exception:
                        info["fps"] = 0.0
                    info["rotation"] = _rotation_from_tags(s)
                elif s.get("codec_type") == "audio" and not info["has_audio"]:
                    info["has_audio"] = True
                    info["audio_codec"] = s.get("codec_name") or ""
                    info["audio_channels"] = int(s.get("channels") or 0)
                    info["audio_sample_rate"] = int(s.get("sample_rate") or 0)
            if not info["duration"]:
                for s in data.get("streams") or []:
                    try:
                        info["duration"] = max(info["duration"], float(s.get("duration") or 0))
                    except Exception:
                        pass
            info["probe_ok"] = True
        except Exception:
            pass
    if not info["probe_ok"]:
        try:
            p = run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], timeout=120)
            text = p.stderr.decode("utf-8", "ignore")
            dur, w, h, fps = _parse_duration_from_stderr(text)
            info.update(duration=dur, width=w, height=h, fps=fps)
            info["has_audio"] = "Audio:" in text
            info["probe_ok"] = bool(dur)
        except Exception:
            pass
    rot = abs(info["rotation"]) % 180
    if rot == 90:
        info["display_width"] = info["height"]
        info["display_height"] = info["width"]
    else:
        info["display_width"] = info["width"]
        info["display_height"] = info["height"]
    return info


# --------------------------------------------------------------------------
# audio / frames
# --------------------------------------------------------------------------
def extract_audio(video: Path, wav: Path, sample_rate: int = 16000,
                  progress: Optional[Callable[[float], None]] = None,
                  duration: float = 0.0) -> Path:
    """Decode the audio track to a mono PCM wav Whisper can consume."""
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-y",
        "-i", str(video),
        "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(wav),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_no_window_kwargs()
    )
    tail = []
    assert proc.stderr is not None
    for line in proc.stderr:
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        if progress and duration > 0:
            m = re.search(rb"time=(\d+):(\d+):(\d+\.?\d*)", line)
            if m:
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                progress(min(0.99, t / duration))
    proc.wait()
    if proc.returncode != 0 or not wav.exists() or wav.stat().st_size < 1024:
        msg = b"".join(tail).decode("utf-8", "ignore")[-800:]
        raise RuntimeError(f"Could not read the audio track.\n{msg}")
    if progress:
        progress(1.0)
    return wav


def grab_frame(video: Path, t: float, out_png: Path, width: int = 1280) -> Optional[Path]:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-y",
        "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={width}:-2",
        str(out_png),
    ]
    try:
        run(cmd, timeout=180)
    except Exception:
        return None
    return out_png if out_png.exists() else None


# --------------------------------------------------------------------------
# subtitle rendering
# --------------------------------------------------------------------------
def write_fontconfig(fonts: Path) -> Path:
    """A private fontconfig file so libass sees the bundled Sorani fonts."""
    cache = settings.work_dir() / "fontcache"
    cache.mkdir(parents=True, exist_ok=True)
    conf = settings.work_dir() / "fonts.conf"
    escaped = str(fonts).replace("\\", "/")
    escaped_cache = str(cache).replace("\\", "/")
    conf.write_text(
        f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>{escaped}</dir>
  <cachedir>{escaped_cache}</cachedir>
  <match target="pattern">
    <test qual="any" name="family"><string>sans-serif</string></test>
    <edit name="family" mode="assign" binding="same"><string>Noto Naskh Arabic</string></edit>
  </match>
  <match target="pattern">
    <test qual="any" name="family"><string>Arial</string></test>
    <edit name="family" mode="assign" binding="same"><string>Noto Naskh Arabic</string></edit>
  </match>
</fontconfig>
""",
        encoding="utf-8",
    )
    return conf


def _tail_text(path: Path, chars: int = 1200) -> str:
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    text = data.decode("utf-8", "ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-18:])[-chars:]


def ffmpeg_env() -> Dict[str, str]:
    env = dict(os.environ)
    try:
        conf = write_fontconfig(settings.fonts_dir())
        env["FONTCONFIG_FILE"] = str(conf)
        env["FONTCONFIG_PATH"] = str(conf.parent)
    except Exception:
        pass
    return env


def _escape_filter_path(path: str) -> str:
    """Escape a path for ffmpeg filter arguments (Windows drive letters included)."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'")
    return p


def _fonts_dir() -> str:
    try:
        return _escape_filter_path(settings.fonts_dir())
    except Exception:
        return ""


def _style_filter(ass_name: str) -> str:
    """The libass filter, with the bundled Sorani fonts loaded explicitly.

    Loading the folder through `fontsdir` means the Kurdish glyphs render the same
    on Windows (DirectWrite) and Linux (fontconfig), with no system fonts needed.
    """
    name = _escape_filter_path(ass_name)
    fonts = _fonts_dir()
    return f"ass={name}" + (f":fontsdir={fonts}" if fonts else "")


def burn(video: Path, ass_file: Path, out: Path, crf: int = 20, preset: str = "medium",
         duration: float = 0.0, progress: Optional[Callable[[float], None]] = None,
         scale_to: int = 0, threads: int = 0,
         cancel: Optional[threading.Event] = None) -> Path:
    """Render the video with the ASS subtitle burned into the picture."""
    out.parent.mkdir(parents=True, exist_ok=True)
    work = ass_file.parent
    vf = _style_filter(ass_file.name)
    if scale_to and scale_to > 0:
        vf = f"scale=-2:{scale_to}," + vf

    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-i", str(video.resolve())]
    if threads:
        cmd += ["-threads", str(threads)]
    cmd += [
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-max_muxing_queue_size", "2048",
        "-progress", "pipe:1", "-nostats", str(out.resolve()),
    ]

    env = ffmpeg_env()
    # stderr goes to a file: reading two pipes from one thread deadlocks ffmpeg
    log_path = work / "ffmpeg-burn.log"
    log_file = open(log_path, "wb")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(work), stdout=subprocess.PIPE, stderr=log_file,
            env=env, **_no_window_kwargs()
        )
    except Exception:
        log_file.close()
        raise
    tail = []
    assert proc.stdout is not None
    cancelled = False
    for raw in proc.stdout:
        if cancel is not None and cancel.is_set():
            cancelled = True
            try:
                proc.kill()
            except Exception:
                pass
            break
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("out_time_ms=") and duration > 0 and progress:
            try:
                ms = int(line.split("=", 1)[1])
                progress(min(0.995, (ms / 1_000_000.0) / duration))
            except Exception:
                pass
        elif line.startswith("progress=end") and progress:
            progress(1.0)
    proc.wait()
    log_file.close()
    if cancelled:
        raise RuntimeError("__cancelled__")
    if proc.returncode != 0:
        text = _tail_text(log_path, 2000)
        # Retry with a plain copy of the audio-less filter chain if the audio map failed
        raise RuntimeError(f"Burning subtitles failed.\n{text}")
    return out


def remux_soft(video: Path, srt_file: Path, out: Path) -> Path:
    """Keeps the original video untouched and adds a selectable subtitle track."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-i", str(video), "-i", str(srt_file),
        "-map", "0", "-map", "1:0", "-c", "copy",
        "-c:s", "mov_text" if out.suffix.lower() == ".mp4" else "srt",
        "-metadata:s:s:0", "language=ckb",
        "-metadata:s:s:0", "title=Kurdi Sorani",
        str(out),
    ]
    p = run(cmd, timeout=None)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[-1200:])
    return out


def preview_frame(video: Path, ass_file: Path, t: float, out_png: Path,
                  width: int = 1280, height: int = 0) -> Optional[Path]:
    """One frame of the video with the subtitle style applied (for the editor).

    The frame keeps the video's aspect ratio; because the .ass PlayRes matches the
    video, libass scales the text exactly like it will in the final burn.
    """
    out_png.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={width}:{height}" if height else f"scale={width}:-2"
    vf = _style_filter(ass_file.name) + "," + scale
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-y",
        "-ss", f"{max(0.0, t):.3f}", "-i", str(video.resolve()),
        "-frames:v", "1", "-vf", vf, str(out_png.resolve()),
    ]
    try:
        p = run(cmd, cwd=ass_file.parent, timeout=180)
    except Exception:
        return None
    return out_png if out_png.exists() and p.returncode == 0 else None
