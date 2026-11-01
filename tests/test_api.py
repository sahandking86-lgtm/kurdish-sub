"""End-to-end tests through the HTTP API (demo mode, no models, no network)."""
import time

import pytest


def _wait(client, jid, timeout=180):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{jid}").json()["job"]
        if job["status"] in ("done", "error", "cancelled", "waiting_review"):
            return job
        time.sleep(0.5)
    pytest.fail(f"job did not finish: {job}")


def test_health_reports_system(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["system"]["ffmpeg"] is True
    assert len(body["tiers"]) == 5
    assert [t["key"] for t in body["tiers"]] == ["very_low", "low", "medium", "high", "perfect"]
    assert body["tiers"][0]["ku"].strip()  # Kurdish label present
    assert body["system"]["demo"] is True


def test_tier_payload_has_all_levels(client):
    body = client.get("/api/tiers").json()
    assert body["order"] == ["very_low", "low", "medium", "high", "perfect"]
    for t in body["tiers"]:
        assert t["blurb"] and t["model"] and t["ku"]


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"font_scale": 1.2, "position": "lower"})
    assert r.status_code == 200
    assert r.json()["settings"]["font_scale"] == 1.2
    assert r.json()["settings"]["position"] == "lower"
    # out-of-range values are clamped
    r = client.post("/api/settings", json={"max_chars": 500})
    assert r.json()["settings"]["max_chars"] <= 90
    # the API key is never echoed back
    r = client.post("/api/settings", json={"gemini_api_key": "secret"})
    assert r.json()["settings"]["gemini_api_key"] == ""
    assert r.json()["settings"]["gemini_api_key_set"] is True


def test_upload_rejects_non_video(client):
    r = client.post("/api/jobs/upload",
                    files={"file": ("song.mp3", b"not a video", "audio/mpeg")},
                    data={"tier": "medium"})
    assert r.status_code == 400


def test_upload_rejects_bad_tier(client, sample_video):
    with sample_video.open("rb") as fh:
        r = client.post("/api/jobs/upload",
                        files={"file": ("clip.mp4", fh, "video/mp4")},
                        data={"tier": "ultimate"})
    assert r.status_code == 400


def test_full_pipeline_burns_subtitles(client, sample_video):
    with sample_video.open("rb") as fh:
        r = client.post("/api/jobs/upload",
                        files={"file": ("clip.mp4", fh, "video/mp4")},
                        data={"tier": "high"})
    assert r.status_code == 200, r.text
    jid = r.json()["job"]["id"]

    job = _wait(client, jid)
    assert job["status"] == "done", job
    assert job["progress"] == 100.0
    assert job["cue_count"] >= 3
    assert job["has_output"] and job["has_srt"]
    assert job["media"]["duration"] > 0

    # the finished video is downloadable and non-trivial
    video = client.get(f"/api/jobs/{jid}/video")
    assert video.status_code == 200
    assert len(video.content) > 20_000

    srt = client.get(f"/api/jobs/{jid}/srt")
    assert srt.status_code == 200
    assert "-->" in srt.text
    assert "سەرسوڕهێنەر" in srt.text or "کەواتە" in srt.text

    # cues are Kurdish and have Arabic script
    cues = job["cues"]
    assert all(c["text"].strip() for c in cues)
    assert any("\u0600" <= ch <= "\u06ff" for ch in cues[0]["text"])


def test_editing_a_cue_updates_the_srt(client, sample_video):
    with sample_video.open("rb") as fh:
        jid = client.post("/api/jobs/upload",
                          files={"file": ("edit.mp4", fh, "video/mp4")},
                          data={"tier": "low"}).json()["job"]["id"]
    _wait(client, jid)
    r = client.post(f"/api/jobs/{jid}/cue", json={"index": 0, "text": "دەقی نوێ"})
    assert r.status_code == 200
    srt = client.get(f"/api/jobs/{jid}/srt").text
    assert "دەقی نوێ" in srt
    # editing a missing line is a clean 400
    assert client.post(f"/api/jobs/{jid}/cue", json={"index": 999, "text": "x"}).status_code == 400


def test_restyle_rewrites_the_ass(client, sample_video):
    with sample_video.open("rb") as fh:
        jid = client.post("/api/jobs/upload",
                          files={"file": ("style.mp4", fh, "video/mp4")},
                          data={"tier": "medium"}).json()["job"]["id"]
    _wait(client, jid)
    r = client.post(f"/api/jobs/{jid}/restyle",
                    json={"font_scale": 1.3, "position": "top", "bilingual": True})
    assert r.status_code == 200
    ass = client.get(f"/api/jobs/{jid}/ass").text
    assert "Style: SRC" in ass        # bilingual style used
    assert ",8," in ass               # alignment 8 = top


def test_preview_frame_is_a_png(client, sample_video):
    with sample_video.open("rb") as fh:
        jid = client.post(f"/api/jobs/upload",
                          files={"file": ("prev.mp4", fh, "video/mp4")},
                          data={"tier": "medium"}).json()["job"]["id"]
    _wait(client, jid)
    r = client.get(f"/api/jobs/{jid}/preview", params={"t": 2.0})
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_cancel_and_delete(client, sample_video):
    with sample_video.open("rb") as fh:
        jid = client.post("/api/jobs/upload",
                          files={"file": ("cancel.mp4", fh, "video/mp4")},
                          data={"tier": "perfect"}).json()["job"]["id"]
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
    time.sleep(1.5)
    job = client.get(f"/api/jobs/{jid}").json()["job"]
    assert job["status"] in ("cancelled", "error", "done")
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/video").status_code == 404


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Kurdish Sub" in r.text
    assert "accuracy" in r.text.lower()
    # the bundled Sorani font is reachable for the UI
    assert client.get("/fonts/NotoNaskhArabic-400.woff2").status_code == 200


def test_languages_list(client):
    body = client.get("/api/languages").json()
    codes = [l["code"] for l in body["languages"]]
    assert codes[0] == "auto"
    assert "en" in codes
