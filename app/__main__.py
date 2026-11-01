"""Command line entry point: ``python -m app``."""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

BANNER = r"""
   _  __              _ _     _      ____        _
  | |/ /_   _ _ __ __| (_)___| |__  / ___| _   _| |__
  | ' /| | | | '__/ _` | / __| '_ \ \___ \| | | | '_ \
  | . \| |_| | | | (_| | \__ \ | | | ___) | |_| | |_) |
  |_|\_\\__,_|_|  \__,_|_|___/_| |_||____/ \__,_|_.__/
        Kurdish Sorani subtitles for your own videos
"""


def _free_port(start: int) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Kurdish Sub — Sorani subtitles for local videos")
    parser.add_argument("--port", type=int, default=int(os.environ.get("KURDISH_SUB_PORT", 0)) or None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="run with fake speech/translation (UI testing)")
    args = parser.parse_args(argv)

    if args.demo:
        os.environ["KURDISH_SUB_DEMO"] = "1"

    try:
        import uvicorn
    except ImportError:
        print("FastAPI/uvicorn are missing. Install everything with:\n"
              "    pip install -r requirements.txt")
        return 1

    from app.core import settings as S

    cfg = S.load_settings()
    port = args.port or _free_port(int(cfg.get("port") or 8765))
    url = f"http://127.0.0.1:{port}"

    print(BANNER)
    hostname = socket.gethostname()
    print(f"  Open your browser at:  {url}")
    print(f"  Files are saved in:    {S.output_dir()}")
    print(f"  Settings file:         {S.config_path()}")
    if args.demo:
        print("  DEMO MODE: no real speech recognition or translation is being used.")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser and cfg.get("open_browser", True):
        def _open():
            time.sleep(1.6)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    try:
        uvicorn.run("app.api:app", host=args.host, port=port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
