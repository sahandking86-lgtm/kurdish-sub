# Setup — step by step

Kurdish Sub turns a **downloaded video** into the **same video with Kurdish (Sorani)
subtitles burned into the picture**. It runs on your own computer: nothing is uploaded,
there are no accounts and no limits.

Time needed: **about 5 minutes** (+ one-time downloads on the first video).

---

## Step 0 — Get the app onto your computer

The app is in the branch `arena/9840cdb1-kurdish-sub` (pull request **#1**). Pick one:

**Option A — merge, then download (simplest)**
1. Open https://github.com/sahandking86-lgtm/kurdish-sub/pull/1
2. Click **Merge pull request** → **Confirm merge**
3. Go to the repo page → green **Code** button → **Download ZIP**
4. Unzip it somewhere easy, e.g. `C:\KurdishSub`
   (you should see `start-windows.bat`, `start-linux.sh`, `app`, `requirements.txt`)

**Option B — git (if you use git)**
```bash
git clone https://github.com/sahandking86-lgtm/kurdish-sub.git
cd kurdish-sub
git checkout arena/9840cdb1-kurdish-sub
```

> Do not rename or move the `app` folder out of the project folder — the start
> scripts look for it.

---

## Step 1 (Windows) — install Python

Skip if `python --version` already prints 3.10 or newer.

1. Go to https://www.python.org/downloads/windows/
2. Download **Python 3.12** (64-bit installer) and run it.
3. **Important:** on the first screen tick ✅ **“Add python.exe to PATH”**, then
   *Install Now*.
4. To check: open **Start → “cmd”** and type:
   ```
   python --version
   ```
   You should see `Python 3.12.x` (3.10–3.13 all work).

---

## Step 1 (Linux) — install Python + venv

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
# optional but recommended (the app also ships its own ffmpeg):
sudo apt install -y ffmpeg
python3 --version        # 3.10 or newer
```

---

## Step 2 — Start the app

**Windows:** double-click **`start-windows.bat`** in the project folder.

**Linux / macOS:** open a terminal in the project folder and run:
```bash
chmod +x start-linux.sh      # once
./start-linux.sh
```

What happens on the **first** run (one time only, 3–10 minutes):
- it creates a private environment in the `.venv` folder,
- downloads about **300 MB** of Python packages (FastAPI, faster-whisper, a static ffmpeg),
- then starts the server and **opens your browser** at `http://127.0.0.1:8765`.

Keep the black terminal window open while you use the app. Closing it stops the app.
Next time, `start-windows.bat` / `./start-linux.sh` starts in a second or two.

If your browser does not open by itself, type **`http://127.0.0.1:8765`** in it.

---

## Step 3 — (optional, 60 seconds) make the Kurdish even better

Open the app → **gear icon** (top right):

| Setting | What to do | Why |
|---|---|---|
| **Translation engine** | leave on *Automatic*, or pick **Google Gemini** and paste a free key from https://aistudio.google.com/apikey | Gemini gives the most natural, idiomatic Sorani and never softens/censors slang or explicit dialogue. Automatic = keyless Google Translate (already good). |
| **Spoken language** | leave on *Detect automatically* (set it if the video is, say, always English) | Saves a few seconds and avoids wrong detection on short clips |
| **Processing hardware** | *Automatic* | Uses your NVIDIA GPU if you have one; otherwise CPU |
| Press **Save settings**. | | |

Nothing here is required — it works out of the box with the free Google engine.

---

## Step 4 — Make your first subtitles

1. **Drop your video** onto the page (or *Choose a video*, or *Browse this computer* —
   the last one opens the normal file dialog and never copies the file).
2. Pick an accuracy level:

   | Level | Use it when |
   |---|---|
   | وردی کەم — زۆر خێرا | you just want the gist, fastest |
   | وردی کەم | casual, quick |
   | وردی مامناوەند | **good default** |
   | وردی بەرز | you care about quality, have time |
   | وردی تەواو | best possible, slowest |

3. Press **Create Kurdish subtitles**.
4. Watch progress: *Reading → Audio → Listening → Translating → Styling → Burning*.
5. When it finishes you get the player, **Download video** (the movie with Kurdish
   subtitles inside it), **Download .srt**, **Change subtitle look**, **Show in folder**.

### First video on a new accuracy level
The app must download the speech model once (per level):

| Level | Model download | Typical speed on a normal laptop CPU |
|---|---|---|
| Very low | ~75 MB | very fast (≈ 2–4 min for a 20-min video) |
| Low | ~142 MB | ≈ 4–6 min |
| Medium | ~484 MB | ≈ 8–15 min |
| High | ~1.6 GB | ≈ 20–40 min |
| Perfect | ~3.1 GB | ≈ 40–70 min |

Second time you use the same level it starts immediately. With an NVIDIA GPU, *High*
and *Perfect* get much faster (roughly real-time).

---

## Step 5 — Where your files are

```
Windows:  C:\Users\<you>\KurdishSub\
Linux:    ~/KurdishSub/
├── output\<job>\yourvideo.kurdish.mp4     ← the finished video (subtitles burned in)
├── output\<job>\yourvideo.ckb.srt         ← the same subtitles as a text file
├── models\                                 ← downloaded speech models (reused)
├── work\                                   ← temporary audio, previews
└── config.json                             ← your settings (the Gemini key stays here)
```

The result screen's **Show in folder** button opens exactly this place.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `Python was not found` (Windows) | Reinstall Python and tick **Add python.exe to PATH**, then run `start-windows.bat` again |
| `Could not create the venv` (Linux) | `sudo apt install python3-venv` |
| Browser shows “site can't be reached” | The terminal window was closed — start it again; the console prints the exact address |
| Port 8765 is busy | `start-windows.bat --port 8800` (or `./start-linux.sh --port 8800`) then open that address |
| “The speech model could not be downloaded” | Only the model download needs internet (huggingface.co). Check connection/VPN and press **Try again** |
| “No translation engine available” | Internet/`translate.googleapis.com` blocked → Settings → engine → **Lingva mirror**, or use a VPN |
| Kills itself / very slow on an old laptop | Choose a lower accuracy level; close other heavy programs |
| Antivirus warns about `python.exe` | It is the standard Python running locally; allow it |

---

## Quick self-test without any downloads (optional)

To confirm the app itself is healthy before your first real video:

```bash
# Windows
.venv\Scripts\python -m app --demo
# Linux
.venv/bin/python -m app --demo
```

Demo mode fakes the speech and the translation but really renders and burns
subtitles, so you can walk through the whole flow in a minute.
