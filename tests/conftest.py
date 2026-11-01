"""Shared pytest fixtures: an isolated app home + a tiny generated video."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def app_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("ksub-home")
    os.environ["KURDISH_SUB_HOME"] = str(home)
    os.environ["KURDISH_SUB_DEMO"] = "1"
    return home


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests must never touch the internet: make every HTTP call fail instantly."""
    import requests

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests.Session, "get", boom, raising=False)
    monkeypatch.setattr(requests.Session, "post", boom, raising=False)
    yield


@pytest.fixture(scope="session")
def sample_video(app_home, tmp_path_factory):
    """A 12 second 640x360 test clip with an audio track."""
    from imageio_ffmpeg import get_ffmpeg_exe

    out = tmp_path_factory.mktemp("media") / "clip.mp4"
    cmd = [
        get_ffmpeg_exe(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=12",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


@pytest.fixture(scope="session")
def client(app_home):
    from fastapi.testclient import TestClient

    from app.api import app

    with TestClient(app) as c:
        yield c
