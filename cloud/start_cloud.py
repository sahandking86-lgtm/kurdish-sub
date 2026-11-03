#!/usr/bin/env python3
"""Start Kurdish Sub on a free cloud GPU and print one private link.

Designed for Google Colab (also works on Kaggle and on any Linux machine with an
NVIDIA GPU). The notebook calls this file; you can also run it by hand:

    python cloud/start_cloud.py                 # tunnel + server
    python cloud/start_cloud.py --no-tunnel     # only http://127.0.0.1:8765
    python cloud/start_cloud.py --drive         # copy results to Google Drive
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)

LINE = "═" * 66


# --------------------------------------------------------------------------
# tiny console helpers
# --------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def banner(title: str) -> None:
    say(LINE)
    say(f"  {title}")
    say(LINE)


def in_notebook() -> bool:
    """True inside Jupyter/Colab (the cell must finish), False as a plain script."""
    try:
        from IPython import get_ipython  # type: ignore

        return get_ipython() is not None
    except Exception:
        return False


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------
def data_root() -> Path:
    """A fast local folder for models and temporary files inside the machine."""
    for candidate in ("/content", "/kaggle/working"):
        if Path(candidate).is_dir():
            base = Path(candidate)
            break
    else:
        base = Path.home()
    root = base / "kurdish-sub-cloud"
    root.mkdir(parents=True, exist_ok=True)
    return root


def gpu_name() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
        line = (out.stdout or "").strip().splitlines()
        if line:
            return line[0]
    except Exception:
        pass
    return ""


def prepare_environment(args) -> dict:
    """Set the folders and the cloud flag *before* the app is imported."""
    home = data_root()
    os.environ["KURDISH_SUB_CLOUD"] = "1"
    os.environ.setdefault("KURDISH_SUB_HOME", str(home / "app"))
    os.environ.setdefault("KURDISH_SUB_WORK", str(home / "work"))
    os.environ.setdefault("KURDISH_SUB_MODELS", str(home / "models"))
    os.environ.setdefault("KURDISH_SUB_OUTPUT", str(home / "output"))
    os.environ.setdefault("HF_HOME", str(home / "models" / "hf"))

    # If doctor.py had to install the CUDA parts, put them where CTranslate2 looks.
    libs_file = Path(tempfile.gettempdir()) / "ksub-cuda-libs"
    if libs_file.exists():
        extra = [p for p in libs_file.read_text().split("\n") if p.strip()]
        if extra:
            os.environ["LD_LIBRARY_PATH"] = ":".join(
                extra + [os.environ.get("LD_LIBRARY_PATH", "")]
            ).strip(":")

    drive = Path("/content/drive/MyDrive")
    if args.drive and drive.is_dir():
        mirror = drive / "KurdishSub"
        os.environ["KURDISH_SUB_MIRROR"] = str(mirror)
        say(f"  📁 A copy of every finished file goes to: {mirror}")
    elif args.drive:
        say("  ⚠ Google Drive is not connected in this session — results stay")
        say("    downloadable from the app and from the Files panel on the left.")
    return {"home": home}


def preload_cuda_libs() -> None:
    """Load the CUDA parts ourselves before CTranslate2 asks for them.

    ``LD_LIBRARY_PATH`` changed inside a running Python process is ignored by the
    system loader, so a plain export in the cell would not help. Loading each
    library by absolute path does.
    """
    info = Path(tempfile.gettempdir()) / "ksub-cuda-libs"
    if not info.exists():
        return
    import ctypes

    loaded = 0
    for folder in [p for p in info.read_text().split("\n") if p.strip()]:
        for pattern in ("libcudnn_ops.so*", "libcudnn_cnn.so*", "libcudnn.so*",
                        "libcublas.so*", "libcublasLt.so*", "libcudart.so*"):
            for lib in sorted(Path(folder).glob(pattern)):
                try:
                    ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError:
                    pass
    if loaded:
        say(f"  🧩 Pre-loaded {loaded} NVIDIA libraries for the GPU")


def configure_app(args) -> None:
    """Sensible cloud defaults, written into the normal settings file."""
    sys.path.insert(0, str(ROOT))
    from app.core import settings as S                    # noqa: E402
    from app.core import ffmpeg_utils as ff               # noqa: E402
    from app.core.transcriber import gpu_available        # noqa: E402

    ff.prefer_hardware_ffmpeg()
    preload_cuda_libs()
    enc = ff.encoder_info()
    patch = {
        "device": "cuda" if gpu_available() else "cpu",
        "auto_burn": True,
        "open_browser": False,
        # NVENC is fast enough for a good preset; a free cloud CPU is only two
        # cores, where "ultrafast" turns a 5-hour render into ~20 minutes.
        "preset": "fast" if enc["hardware"] else "ultrafast",
        "encoder": "auto",
    }
    if args.gemini_key:
        patch["gemini_api_key"] = args.gemini_key.strip()
        patch["provider"] = "gemini"
    S.save_settings(patch)
    say(f"  🎬 Video encoder: {enc['encoder']}"
        f" ({'graphics card — fast' if enc['hardware'] else 'CPU — slower'})")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 40):
        if not port_is_open(port):
            return port
    return preferred


def health_ok(port: int, token: str = "") -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    req = urllib.request.Request(url, headers={"X-KSub-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------
# the server that the public link points at
# --------------------------------------------------------------------------
def start_server(port: int, token: str) -> None:
    os.environ["KURDISH_SUB_TOKEN"] = token
    sys.path.insert(0, str(ROOT))

    import uvicorn                                # noqa: E402
    from app import api as api_module             # noqa: E402  (reads the token)

    api_module.ACCESS_TOKEN = token               # also after a re-run in one kernel
    config = uvicorn.Config(api_module.app, host="0.0.0.0", port=port,
                            log_level="warning", access_log=False,
                            loop="asyncio")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="ksub-uvicorn").start()

    deadline = time.time() + 90
    while time.time() < deadline:
        if health_ok(port, token):
            return
        time.sleep(0.3)
    raise RuntimeError("The web server did not start in 90 seconds.")


def start_embedded_copy(port: int, log_path: Path) -> bool:
    """A second copy *without* a password, only visible inside the notebook.

    The notebook preview runs inside an iframe; browsers refuse to keep a login
    cookie there, so this copy is served on localhost and only reachable through
    the notebook's own authenticated proxy.
    """
    env = os.environ.copy()
    env.pop("KURDISH_SUB_TOKEN", None)
    try:
        with log_path.open("ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "app", "--port", str(port),
                 "--host", "127.0.0.1", "--no-browser"],
                cwd=str(ROOT), env=env, stdout=log, stderr=log,
                start_new_session=True,
            )
    except Exception as exc:
        say(f"  ⚠ Could not prepare the in-notebook preview ({exc})")
        return False
    deadline = time.time() + 60
    while time.time() < deadline:
        if health_ok(port):
            return True
        time.sleep(0.4)
    return False


# --------------------------------------------------------------------------
# the public link
# --------------------------------------------------------------------------
def cloudflared_path() -> Path | None:
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    local = Path(tempfile.gettempdir()) / "cloudflared"
    if local.exists() and os.access(local, os.X_OK):
        return local
    say("  ⬇ Downloading the tunnel helper (free, ~35 MB) …")
    try:
        req = urllib.request.Request(CLOUDFLARED_URL, headers={"User-Agent": "kurdish-sub"})
        with urllib.request.urlopen(req, timeout=180) as r, local.open("wb") as out:
            shutil.copyfileobj(r, out, 1024 * 1024)
        local.chmod(0o755)
        return local
    except Exception as exc:
        say(f"  ⚠ Could not download the tunnel helper: {exc}")
        return None


def start_tunnel(port: int) -> tuple[subprocess.Popen | None, str]:
    exe = cloudflared_path()
    if exe is None:
        return None, ""
    say("  🔗 Creating your private link …")
    proc = subprocess.Popen(
        [str(exe), "--no-autoupdate", "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    found: list[str] = []
    done = threading.Event()

    def read():
        assert proc.stderr is not None
        for line in proc.stderr:          # keep reading for the whole session
            if not found:
                m = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com", line)
                if m:
                    found.append(m.group(0))
                    done.set()
        done.set()

    threading.Thread(target=read, daemon=True).start()
    done.wait(timeout=75)
    if not found:
        say("  ⚠ The tunnel did not answer; the notebook preview still works.")
        return proc, ""
    return proc, found[0]


def colab_available() -> bool:
    try:
        import google.colab  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def colab_frame(port: int) -> bool:
    """Show the interface inside the notebook (works without any tunnel)."""
    try:
        from google.colab import output  # type: ignore

        output.serve_kernel_port_as_iframe(port, path="/", height="840")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Kurdish Sub in the cloud")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="", help="private code inside the link")
    parser.add_argument("--drive", action="store_true",
                        help="also copy finished files to Google Drive")
    parser.add_argument("--no-tunnel", action="store_true",
                        help="do not create a public link (local testing)")
    parser.add_argument("--no-iframe", action="store_true",
                        help="do not show the app inside the notebook")
    parser.add_argument("--gemini-key", default=os.environ.get("KURDISH_SUB_GEMINI_KEY", ""),
                        help="optional free Gemini key for better Kurdish")
    args = parser.parse_args(argv)

    banner("Kurdish Sub — free cloud GPU")
    gpu = gpu_name()
    say(f"  GPU: {gpu or 'no NVIDIA GPU found (this will be slow)'}")

    paths = prepare_environment(args)
    home: Path = paths["home"]
    state_file = home / "cloud-state.json"

    # Already running? (the user pressed Run again)
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            if state.get("token") and health_ok(state["port"], state["token"]):
                say()
                banner("Already running")
                if state.get("url"):
                    say(f"  OPEN: {state['url']}")
                else:
                    say(f"  Local address: http://127.0.0.1:{state['port']}/?token={state['token']}")
                say()
                if not args.no_iframe and state.get("iframe_port"):
                    colab_frame(int(state["iframe_port"]))
                return 0
        except Exception:
            pass

    configure_app(args)
    token = args.token or secrets.token_urlsafe(9)
    port = free_port(args.port)
    try:
        start_server(port, token)
    except Exception as exc:
        say(f"  ❌ {exc}")
        return 1
    say(f"  ⚙  Web server ready on port {port}")

    iframe_port = 0
    if not args.no_iframe and in_notebook() and colab_available():
        candidate = free_port(port + 1)
        if start_embedded_copy(candidate, home / "preview.log"):
            iframe_port = candidate
        else:
            say("  ⚠ The in-notebook preview is unavailable (the link still works).")

    proc, url = (None, "")
    if not args.no_tunnel:
        proc, url = start_tunnel(port)

    if url:
        full = f"{url}/?token={token}"
        state_file.write_text(json.dumps(
            {"url": full, "port": port, "token": token, "iframe_port": iframe_port,
             "tunnel_pid": proc.pid if proc else 0, "started": time.time()}, indent=2))
        say()
        banner("Kurdish Sub is running on the cloud GPU")
        say()
        say("   OPEN THIS LINK IN A NEW TAB:")
        say()
        say(f"   {full}")
        say()
        say("   • It is private — the long code at the end is your key.")
        say("   • Keep this Colab tab open while you work.")
        say("   • You can also open the link on your phone.")
        say()
    else:
        state_file.write_text(json.dumps(
            {"url": "", "port": port, "token": token, "iframe_port": iframe_port,
             "tunnel_pid": 0, "started": time.time()}, indent=2))
        banner("Kurdish Sub is running (no public link)")
        if args.no_tunnel:
            say(f"  Local address: http://127.0.0.1:{port}/?token={token}")
        else:
            say("  The public link could not be created this time.")
            say("  Use the identical preview just below this cell.")
        say()

    if iframe_port:
        if colab_frame(iframe_port):
            say("   An identical copy opens below, in case your browser blocks the link.")
            say()

    if not in_notebook():
        say("  Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            say("\n  Stopped.")
    return 0


if __name__ == "__main__":
    _exit = main()
    if not in_notebook():
        raise SystemExit(_exit)
