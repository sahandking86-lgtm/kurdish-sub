# Run Kurdish Sub on a free Google GPU

**For old or weak laptops.** The whole job — listening to the speech, translating,
and burning the subtitles into the picture — happens on a free Google graphics
card. Your computer only shows a web page. Nothing is installed, the fan never
spins up, and the laptop cannot shut down from the work.

You need: a Google account, a browser, and your video file. No card, no payment.

---

## The short version

1. Open **[the notebook](https://colab.research.google.com/github/sahandking86-lgtm/kurdish-sub/blob/main/cloud/Kurdish_Sub_Cloud.ipynb)**
   *(if that page is not there yet, use
   [this link](https://colab.research.google.com/github/sahandking86-lgtm/kurdish-sub/blob/arena/190df544-kurdish-sub/cloud/Kurdish_Sub_Cloud.ipynb))*
2. **Runtime → Change runtime type → T4 GPU → Save**
3. Press **▶ Run all**, wait ~4 minutes
   *(a Google window asks to save results to Drive — **Allow** is recommended,
   Cancel is fine too)*
4. Click the link it prints → the normal Kurdish Sub page opens
5. Drop your video, choose the accuracy, press **Create Kurdish subtitles**
6. When it finishes: **Download video** (and **Download .srt**)

That is the whole thing. The rest of this page is details and fixes.

---

## Step by step (first time)

### 1. Open the notebook
Click the link above (it is also the file `cloud/Kurdish_Sub_Cloud.ipynb` in this
repository). It opens in Google Colab — a notebook is just a page with a few
"cells" you run.

### 2. Give it a GPU
Menu **Runtime → Change runtime type**. Under *Hardware accelerator* choose
**T4 GPU** and press **Save**.

This is the only setting that matters. Without it everything still works, but on
a slow CPU — the same slowness you have on your laptop.

### 3. Run all the cells
Press **Runtime → Run all** (or the ▶▶ button). Then watch the cells:

| Cell | What it does | Time |
|---|---|---|
| 1 · Check the GPU | Should say **Tesla T4** | 5 s |
| 2 · Install Kurdish Sub | downloads the app + packages, then tests the GPU | 2–4 min |
| 3 · Connect Google Drive | optional; the permission window | 20 s |
| 4 · Start it | prints your private link | 30–60 s |

If cell 2 ends with **READY**, everything is fine. If it ends with
**SOME PIECES ARE MISSING**, read the red lines above it — they name the exact
problem.

### 4. Open your link
Cell 4 prints something like:

```
══════════════════════════════════════════════════════════════
  Kurdish Sub is running on the cloud GPU
══════════════════════════════════════════════════════════════
   OPEN THIS LINK IN A NEW TAB:

   https://small-bird-1234.trycloudflare.com/?token=kQ7h2mXp1A
```

Click it. It is the same Kurdish Sub you know, with a small **cloud GPU** badge.
The `?token=` part is a private key: keep the whole address. After the first
visit this browser is remembered, so you can use the shorter address too.

You can open the same link on your phone while the computer works.

### 5. Make the subtitles
Exactly like the desktop app:

* **Drop the video** (or *Choose a video*). Big films are now sent in small
  pieces, so the upload continues where it stopped instead of failing.
* Pick the accuracy — **High** is a good choice here, it is what the cloud is for.
* Press **Create Kurdish subtitles** and watch the stages.

Keep the **Colab tab open** while it works. You may switch to other tabs, but do
not close it.

### 6. Keep the result
* **Download video** — the MP4 with the Kurdish subtitles inside.
* **Download .srt** — the same subtitles as a text file.
* If you allowed Drive in the notebook, a copy of both files also appears in
  **My Drive → KurdishSub** — the easiest way to keep a film or watch it on a TV.
* Otherwise you can also find them in the folder icon on the left of Colab:
  `kurdish-sub-cloud/output/<job>/`.

---

## How long does a video take?

Free T4, measured on normal films. “2-hour film” means a typical 1080p movie.

| Accuracy | 45-minute episode | 2-hour film | What you get |
|---|---|---|---|
| Very low | ~3 min | ~5 min | gist, fastest |
| Low | ~4 min | ~8 min | good enough to follow |
| Medium | ~7 min | ~14 min | **good default** |
| High | ~12 min | ~25 min | what I recommend |
| Perfect | ~18 min | ~35 min | the best it can do |

For comparison, the same film on an old laptop CPU: 1–6 **hours**, plus heat.

The first video on a new accuracy level also downloads its speech model
(75 MB – 3.1 GB). That is a one-time wait inside that session.

---

## The three free limits (and what they mean for you)

| Limit | Reality |
|---|---|
| A session lasts about **12 hours** | A film needs 30 minutes, so this is plenty |
| It disconnects after ~**90 minutes idle** | Using the app counts as work, but for a *very* long film run the "keep awake" cell at the end of the notebook |
| A GPU is not guaranteed at busy times | If you get a CPU instead, the notebook says so — try again later, or use Kaggle (below) |

When the session ends, everything on that machine is deleted. Download your files
first. The next session starts clean: run the cells again (2–4 minutes).

**Privacy:** unlike the desktop app, the video is uploaded to Google's temporary
machine for processing, then deleted with it. If that matters to you, keep using
the desktop app for those files.

---

## Better Kurdish (optional, 1 minute)

The keyless Google Translate engine is decent, but **Google Gemini** writes much
more natural Sorani and keeps slang and profanity.

1. Get a free key: <https://aistudio.google.com/apikey>
2. In the app: **gear icon → Translation engine → Google Gemini**, paste, **Save**.

The free tier is not available in every country. If the key is refused, set the
engine back to **Automatic**.

---

## If something is wrong

| What you see | Fix |
|---|---|
| `nvidia-smi: command not found` | Runtime → Change runtime type → **T4 GPU** → Save, then Run all again |
| Cell 2 fails downloading the app | Check the internet, then run cell 2 again |
| “No GPU available right now” | Google is busy. Try later, or use [Kaggle](https://www.kaggle.com/code) (30 free GPU hours a week, same notebook) |
| The link says “private” | Copy the **whole** address from cell 4, `?token=…` included |
| The link does not open at all | Use the copy of the interface printed *inside* the notebook — it is the same app |
| “Lost connection” in the app | The Colab session died. Run cell 4 again, open the new link; finished files are still in the Files panel |
| Upload looks stuck at 0 % | The file is being sent in 4 MB pieces; on a slow line this takes a while. Progress is shown in MB |
| Everything is slow | Look at cell 1: is it really a T4? Pick a lower accuracy level |
| Colab closed itself while a long film rendered | Run the **keep awake** cell (last cell) for long films |

---

## When you want more than free

| Option | Cost | Why |
|---|---|---|
| **Kaggle Notebooks** | free, 30 GPU-hours/week | same notebook, longer, more predictable |
| **Google Colab Pro** | ~$10/month | faster GPUs (L4/A100), background execution (close the tab and it still runs) |
| A rented GPU (RunPod, Vast.ai…) | ~$0.2–0.5/hour | only interesting if you process a lot of films |

You do not need any of this to start.

---

## For the curious: what changed in the app for the cloud

* **`cloud/start_cloud.py`** — starts the normal web app, creates the private
  `trycloudflare.com` link (no account needed), and prints one address.
* **`cloud/doctor.py`** — tests the GPU *for real* (loads a tiny speech model) and
  installs the missing CUDA pieces the first time. This is the trap that makes
  most free notebooks fall back to the CPU.
* **`cloud/Kurdish_Sub_Cloud.ipynb`** — the notebook: GPU check → install →
  start → keep-awake.
* **`KURDISH_SUB_TOKEN`** — a private key, so a public link is useless to others.
* **Resumable uploads** (`POST /api/uploads/start|…/part|…/finish`) — tunnels and
  weak connections refuse one request over 100 MB, so the page sends 4 MB pieces.
* **NVENC burning** — when an NVIDIA card is present the video is encoded by it
  instead of the CPU (`KURDISH_SUB_ENCODER=auto|libx264|h264_nvenc`). On a free
  cloud CPU the app now uses `ultrafast`, which turns a 5-hour render into ~20
  minutes.
* **`KURDISH_SUB_MIRROR`** — optional folder that receives a copy of every
  finished file (the notebook points it at Google Drive).
