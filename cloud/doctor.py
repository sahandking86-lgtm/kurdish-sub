#!/usr/bin/env python3
"""Check (and repair) the machine before Kurdish Sub runs on it.

    python cloud/doctor.py          # look only
    python cloud/doctor.py --fix    # look, then repair what can be repaired

It answers one question for you: *will the GPU actually be used?*  A wrong CUDA
library is the single most common reason a free GPU notebook silently falls back
to the slow CPU, so this script tests the real thing on a real model instead of
trusting the labels.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OK, BAD, WARN = "✅", "❌", "⚠️ "


def line(mark: str, text: str, fix: str = "") -> None:
    print(f"  {mark} {text}")
    if fix:
        print(f"      → {fix}")


def _run(cmd: list[str], timeout: int = 120, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=env or os.environ.copy())


# --------------------------------------------------------------------------
def check_python() -> bool:
    v = sys.version_info
    good = (3, 10) <= (v.major, v.minor)
    line(OK if good else BAD, f"Python {v.major}.{v.minor}.{v.micro}")
    return good


def check_packages() -> bool:
    ok = True
    for mod, fix in (("fastapi", "pip install -r requirements.txt"),
                     ("faster_whisper", "pip install faster-whisper"),
                     ("imageio_ffmpeg", "pip install imageio-ffmpeg")):
        try:
            __import__(mod)
            line(OK, f"{mod} installed")
        except Exception:
            ok = False
            line(BAD, f"{mod} is missing", fix)
    return ok


def check_gpu() -> bool:
    try:
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        name = (out.stdout or "").strip()
        if out.returncode == 0 and name:
            line(OK, f"NVIDIA GPU: {name}")
            return True
    except Exception:
        pass
    line(WARN, "No GPU visible",
         "Runtime → Change runtime type → Hardware accelerator → T4 GPU, then run again")
    return False


def check_ffmpeg() -> str:
    sys.path.insert(0, str(ROOT))
    from app.core import ffmpeg_utils as ff

    path = ff.prefer_hardware_ffmpeg()
    exists = Path(path).exists() or shutil.which(path) is not None
    line(OK if exists else BAD, f"ffmpeg: {path}",
         "" if exists else "sudo apt-get install -y ffmpeg")
    info = ff.encoder_info()
    line(OK if info["hardware"] else WARN,
         f"video encoder: {info['encoder']}"
         + (" (hardware — fast)" if info["hardware"] else " (CPU — subtitles burn slower)"),
         "" if info["hardware"] else
         "not a problem for subtitles, but the final video render takes longer")
    return path


def check_disk() -> bool:
    target = Path(tempfile.gettempdir())
    total, used, free = shutil.disk_usage(target)
    gb = free / (1024 ** 3)
    good = gb > 8
    line(OK if good else BAD, f"free disk space: {gb:.0f} GB")
    return good


def check_network() -> bool:
    import urllib.request

    ok = True
    for name, url in (
        ("huggingface.co (speech models)", "https://huggingface.co"),
        ("translate.googleapis.com (free translation)", "https://translate.googleapis.com"),
    ):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kurdish-sub-doctor"})
            with urllib.request.urlopen(req, timeout=15) as r:
                reachable = r.status < 500
        except Exception:
            reachable = False
        line(OK if reachable else BAD, name)
        ok = ok and reachable
    return ok


# --------------------------------------------------------------------------
def _cuda_lib_dirs() -> list[str]:
    """Folders of the pip-installed CUDA parts (cublas / cudnn), if present."""
    dirs: list[str] = []
    try:
        import site

        roots = [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]
        for root in roots:
            nvidia = root / "nvidia"
            if not nvidia.is_dir():
                continue
            for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
                for candidate in ((nvidia / sub / "lib"), (nvidia / sub / "bin")):
                    if candidate.is_dir():
                        dirs.append(str(candidate))
    except Exception:
        pass
    return dirs


def _cuda_env(extra: list[str]) -> dict:
    env = os.environ.copy()
    if extra:
        env["LD_LIBRARY_PATH"] = ":".join(extra + [env.get("LD_LIBRARY_PATH", "")]).strip(":")
    return env


GPU_TEST = r"""
import sys, numpy as np
from faster_whisper import WhisperModel
m = WhisperModel("tiny", device="cuda", compute_type="float16")
audio = np.zeros(16000, dtype=np.float32)
segments, info = m.transcribe(audio, language="en", beam_size=1)
list(segments)
print("GPU-OK")
"""


def gpu_test(env: dict | None = None) -> tuple[bool, str]:
    """Load the smallest model on the GPU for real (a crash cannot hurt us here)."""
    try:
        p = _run([sys.executable, "-c", GPU_TEST], timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return False, "the GPU test timed out"
    output = (p.stdout + "\n" + p.stderr).strip()
    if p.returncode == 0 and "GPU-OK" in output:
        return True, ""
    tail = "\n".join(output.splitlines()[-6:])
    return False, tail


def check_gpu_really_works(fix: bool) -> tuple[bool, list[str]]:
    """Returns (works, extra library paths that must be on LD_LIBRARY_PATH)."""
    base = _cuda_lib_dirs()
    for label, extra in (("clean environment", []), ("with the pip CUDA libraries", base)):
        ok, err = gpu_test(_cuda_env(extra))
        if ok:
            line(OK, f"Speech model runs on the GPU ({label})")
            return True, extra
        if "cudnn" in err.lower() and "libcuda" not in err.lower() and extra == base:
            line(WARN, "The GPU needs cuDNN next to CUDA", "installing it now …")
            break

    if not fix:
        line(BAD, "The GPU cannot run the speech model yet",
             "run this cell again with: python cloud/doctor.py --fix")
        return False, base

    line(WARN, "Installing the missing NVIDIA parts (one time, ~700 MB) …")
    cmds = [
        [sys.executable, "-m", "pip", "install", "-q", "nvidia-cublas-cu12"],
        [sys.executable, "-m", "pip", "install", "-q", "nvidia-cudnn-cu12==9.*"],
    ]
    for cmd in cmds:
        try:
            p = _run(cmd, timeout=1800)
            if p.returncode != 0:
                line(WARN, f"{cmd[-1]} could not be installed")
        except Exception as exc:
            line(WARN, f"{cmd[-1]} could not be installed ({exc})")

    extra = _cuda_lib_dirs()
    ok, err = gpu_test(_cuda_env(extra))
    if ok:
        line(OK, "Speech model now runs on the GPU")
        return True, extra
    line(BAD, "Still no GPU — everything will run on the CPU (slow but correct)")
    for row in err.splitlines()[-3:]:
        print(f"      {row}")
    return False, extra


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Is this machine ready for Kurdish Sub?")
    parser.add_argument("--fix", action="store_true", help="repair what can be repaired")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    print("\n  Kurdish Sub — machine check")
    print("  " + "─" * 40)

    good = True
    good &= check_python()
    good &= check_packages()
    has_gpu = check_gpu()
    check_ffmpeg()
    good &= check_disk()
    online = check_network()

    if has_gpu:
        works, extra = check_gpu_really_works(args.fix)
        if works and extra:
            Path(tempfile.gettempdir(), "ksub-cuda-libs").write_text("\n".join(extra))
            print("  (the launcher will put those libraries on the path for you)")
        if not works:
            print("  The app still works — just choose “Medium” and expect the speed of a CPU.")
    else:
        print("  The app still works on the CPU — just slower.")

    print("  " + "─" * 40)
    if not good:
        print("  SOME PIECES ARE MISSING — see the ❌ lines above\n")
        return 1
    if not online:
        print("  READY, but the internet check failed.")
        print("  The speech model and the translator are downloaded — check the connection")
        print("  (or VPN) and run this cell again.\n")
        return 0
    print("  READY\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
