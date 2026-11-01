"""Paths, persistent settings and small filesystem helpers."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict

APP_NAME = "KurdishSub"

_LOCK = threading.RLock()

DEFAULT_SETTINGS: Dict[str, Any] = {
    # ---- translation ----------------------------------------------------
    "provider": "auto",           # auto | google | lingva | gemini | ollama | nllb
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:7b",
    "libre_url": "",
    # ---- speech recognition --------------------------------------------
    "device": "auto",             # auto | cpu | cuda
    "compute_type": "auto",       # auto | int8 | float16 | int8_float16
    "cpu_threads": 0,             # 0 = let ctranslate2 decide
    "source_language": "auto",
    # ---- rendering ------------------------------------------------------
    "font": "Noto Naskh Arabic",
    "font_scale": 1.0,            # multiplier on the auto font size
    "position": "bottom",         # bottom | lower | middle | top
    "bilingual": False,           # show the original language line above
    "max_chars": 42,              # per line
    "crf": 20,
    "preset": "medium",
    "output_format": "mp4",
    "keep_temp": False,
    "auto_burn": True,            # burn immediately instead of waiting for review
    "open_browser": True,
    "port": 8765,
}


def app_dir() -> Path:
    """Writable application folder (created on demand)."""
    override = os.environ.get("KURDISH_SUB_HOME")
    if override:
        root = Path(override).expanduser()
    elif sys.platform == "win32":
        root = Path(os.environ.get("USERPROFILE", Path.home())) / APP_NAME
    else:
        root = Path.home() / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def work_dir() -> Path:
    d = app_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir() -> Path:
    d = app_dir() / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = app_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_dir() / "config.json"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def fonts_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "fonts"


def load_settings() -> Dict[str, Any]:
    with _LOCK:
        data = dict(DEFAULT_SETTINGS)
        p = config_path()
        if p.exists():
            try:
                data.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        # env overrides for headless use
        if os.environ.get("KURDISH_SUB_PROVIDER"):
            data["provider"] = os.environ["KURDISH_SUB_PROVIDER"]
        return data


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        data = load_settings()
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                data[k] = v
        config_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return data


def free_space_gb(path: Path) -> float:
    try:
        return round(shutil.disk_usage(str(path)).free / (1024 ** 3), 1)
    except Exception:
        return -1.0


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(name: str, max_len: int = 80) -> str:
    """Filesystem-safe stem for user supplied filenames."""
    stem = Path(name).stem or "video"
    keep = []
    for ch in stem:
        if ch.isalnum() or ch in "-_ .":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip(" ._") or "video"
    return out[:max_len]
