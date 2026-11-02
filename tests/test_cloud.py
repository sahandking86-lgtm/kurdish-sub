"""Cloud mode: private links, resumable uploads, GPU encoder choice, Drive copies."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------
# resumable upload (what lets a big film pass through a 100 MB tunnel)
# --------------------------------------------------------------------------
def test_chunked_upload_creates_a_job(client, sample_video):
    data = sample_video.read_bytes()
    r = client.post("/api/uploads/start", json={"name": "movie.mp4", "size": len(data)})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]

    piece = 120_000
    starts = list(range(0, len(data), piece))
    indexes = list(range(len(starts)))
    for index, start in enumerate(starts):
        chunk = data[start:start + piece]
        r = client.post(f"/api/uploads/{uid}/part?index={index}", content=chunk,
                        headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text

    status = client.get(f"/api/uploads/{uid}").json()
    assert status["parts"] == indexes
    assert status["bytes"] == len(data)

    r = client.post(f"/api/uploads/{uid}/finish", json={"tier": "medium"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["name"] == "movie.mp4"
    assert job["tier"] == "medium"

    # the pieces are consumed, not left behind
    assert client.get(f"/api/uploads/{uid}").status_code == 404


def test_chunked_upload_refuses_a_missing_piece(client, sample_video):
    data = sample_video.read_bytes()
    uid = client.post("/api/uploads/start",
                      json={"name": "movie.mp4", "size": len(data)}).json()["upload_id"]
    client.post(f"/api/uploads/{uid}/part?index=0", content=data[:1000],
                headers={"Content-Type": "application/octet-stream"})
    client.post(f"/api/uploads/{uid}/part?index=2", content=data[:1000],
                headers={"Content-Type": "application/octet-stream"})
    r = client.post(f"/api/uploads/{uid}/finish", json={"tier": "medium"})
    assert r.status_code == 400
    assert "missing" in r.json()["detail"].lower()


def test_chunked_upload_rejects_unknown_id_and_bad_files(client):
    assert client.post("/api/uploads/deadbeef/part?index=0", content=b"x").status_code == 400
    assert client.post("/api/uploads/start",
                       json={"name": "song.mp3", "size": 10}).status_code == 400
    uid = client.post("/api/uploads/start",
                      json={"name": "ok.mp4", "size": 10}).json()["upload_id"]
    assert client.post(f"/api/uploads/{uid}/finish", json={"tier": "nope"}).status_code == 400


# --------------------------------------------------------------------------
# private access token
# --------------------------------------------------------------------------
def test_token_protects_everything(client, monkeypatch):
    from app import api as api_module

    monkeypatch.setattr(api_module, "ACCESS_TOKEN", "s3cret")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-KSub-Token": "nope"}).status_code == 401
    ok = client.get("/api/health", headers={"X-KSub-Token": "s3cret"})
    assert ok.status_code == 200

    page = client.get("/")
    assert page.status_code == 401 and "private" in page.text.lower()

    # the link from the notebook sets a cookie, so the rest of the page just works
    first = client.get("/?token=s3cret")
    assert first.status_code == 200
    assert "ksub_token" in first.cookies
    assert client.get("/").status_code == 200


def test_token_off_by_default(client):
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------
# encoder choice
# --------------------------------------------------------------------------
def test_encoder_can_be_forced():
    from app.core import ffmpeg_utils as ff

    assert ff.resolve_encoder("libx264") == "libx264"
    assert ff.resolve_encoder("auto") in ("libx264", "h264_nvenc")
    assert ff._nvenc_preset("veryfast") == "p2"
    assert ff._nvenc_preset("medium") == "p4"


def test_burn_accepts_an_encoder_and_still_works(tmp_path, sample_video):
    from app.core import ffmpeg_utils as ff
    from app.core.subtitles import Cue, write_ass

    ass = tmp_path / "sub.ass"
    write_ass([Cue(0.5, 3.0, "سڵاو")], ass, 640, 360)
    out = tmp_path / "out.mp4"
    ff.burn(sample_video, ass, out, preset="veryfast", duration=12, encoder="libx264")
    assert out.exists() and out.stat().st_size > 10_000


# --------------------------------------------------------------------------
# Drive copy
# --------------------------------------------------------------------------
def test_mirror_copies_finished_files(tmp_path, monkeypatch):
    from app.core import engine as engine_module

    src = tmp_path / "movie.ckb.srt"
    src.write_text("1\n00:00:00,000 --> 00:00:02,000\nسڵاو\n", encoding="utf-8")
    movie = tmp_path / "movie.kurdish.mp4"
    movie.write_bytes(b"\x00" * 2048)
    drive = tmp_path / "drive"
    monkeypatch.setattr(engine_module.S, "mirror_dir", lambda: drive)

    job = SimpleNamespace(srt_path=src, output_path=movie, message="", warnings=[])
    engine_module.Engine(cfg={})._mirror(job)

    assert (drive / src.name).read_bytes() == src.read_bytes()
    assert (drive / movie.name).stat().st_size == 2048


def test_mirror_is_optional(tmp_path, monkeypatch):
    from app.core import engine as engine_module

    monkeypatch.setattr(engine_module.S, "mirror_dir", lambda: None)
    job = SimpleNamespace(srt_path=None, output_path=None, message="", warnings=[])
    engine_module.Engine(cfg={})._mirror(job)     # must not raise
    assert job.warnings == []


# --------------------------------------------------------------------------
# folders can be overridden (the cloud keeps models on fast local disk)
# --------------------------------------------------------------------------
def test_folder_overrides(tmp_path, monkeypatch):
    from app.core import settings

    monkeypatch.setenv("KURDISH_SUB_WORK", str(tmp_path / "w"))
    monkeypatch.setenv("KURDISH_SUB_OUTPUT", str(tmp_path / "o"))
    monkeypatch.setenv("KURDISH_SUB_MODELS", str(tmp_path / "m"))
    monkeypatch.setenv("KURDISH_SUB_MIRROR", str(tmp_path / "mirror"))
    assert settings.work_dir() == tmp_path / "w"
    assert settings.output_dir() == tmp_path / "o"
    assert settings.models_dir() == tmp_path / "m"
    assert settings.mirror_dir() == tmp_path / "mirror"
    assert settings.cloud_mode() is False
    monkeypatch.setenv("KURDISH_SUB_CLOUD", "1")
    assert settings.cloud_mode() is True


# --------------------------------------------------------------------------
# a broken graphics card must never lose the video
# --------------------------------------------------------------------------
def test_gpu_problem_falls_back_to_the_cpu(monkeypatch):
    import sys
    import types

    import app.core.transcriber as T
    from app.core.tiers import get_tier

    calls = []

    class FakeWhisper:
        def __init__(self, name, device, compute_type, **kwargs):
            calls.append((name, device, compute_type))
            if device == "cuda":
                raise RuntimeError("Could not load library libcudnn_ops.so.9")

        def transcribe(self, *a, **kw):      # pragma: no cover - not used here
            return iter([]), types.SimpleNamespace(language="en", duration=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(T, "resolve_device", lambda cfg: ("cuda", "float16"))
    T._MODEL_CACHE.clear()

    transcriber = T.Transcriber({"device": "cuda", "compute_type": "float16"})
    transcriber.load(get_tier("medium"))

    assert transcriber.device == "cpu"
    assert transcriber.compute_type == "int8"
    assert transcriber.load_note == "gpu_fallback"
    assert [c[1] for c in calls] == ["cuda", "cpu"]
    T._MODEL_CACHE.clear()


def test_unrelated_model_errors_still_report_downloads(monkeypatch):
    import sys
    import types

    import app.core.transcriber as T
    from app.core.tiers import get_tier

    class FakeWhisper:
        def __init__(self, name, device, compute_type, **kwargs):
            raise RuntimeError("Connection refused while downloading from huggingface.co")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(T, "resolve_device", lambda cfg: ("cpu", "int8"))
    T._MODEL_CACHE.clear()

    with pytest.raises(T.TranscribeError) as err:
        T.Transcriber({"device": "cpu"}).load(get_tier("tiny"))
    assert "could not be downloaded" in str(err.value)
    T._MODEL_CACHE.clear()
