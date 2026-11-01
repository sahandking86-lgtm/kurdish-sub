# Kurdish Sub — کوردی سۆرانی

**Give it any downloaded video, pick how accurate you want the Kurdish (Sorani)
subtitles to be, and get the same video back with Kurdish subtitles inside it.**

Free · runs on your own computer · no uploads, no accounts, no limits, no
censorship of adult or explicit material.

![flow](docs/flow.txt)

---

## Quick start

New to this? Follow **[SETUP.md](SETUP.md)** — step-by-step, with what to expect.

### Windows
1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/windows/)
   and tick **“Add python.exe to PATH”** during setup.
2. Double-click **`start-windows.bat`**.
   The first run installs everything (a few minutes); after that it starts instantly.
3. Your browser opens at `http://127.0.0.1:8765`.

### Linux (and macOS)
```bash
./start-linux.sh
```
It needs `python3-venv` (`sudo apt install python3 python3-venv`) and, if you have
it, the system `ffmpeg` (`sudo apt install ffmpeg`). Without a system ffmpeg the app
falls back to the pip-bundled static build, which works fine.

### Manual start
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # .venv\Scripts\pip on Windows
.venv/bin/python -m app                        # opens the browser
```
Useful flags: `--port 9000`, `--no-browser`, `--demo` (fake speech + translation,
for trying the interface without downloading models).

---

## How it works

```
video  ─►  ffmpeg extracts the audio  ─►  Whisper (on this computer) transcribes
      ─►  sentences are grouped & timed  ─►  translated into Sorani Kurdish
      ─►  styled .ass subtitles  ─►  ffmpeg burns them into the picture  ─►  your video
```

Everything except the (optional) online translation engine happens locally.

## The five accuracy levels

| Level | Kurdish | Speech model | What it does | Speed (CPU) |
|---|---|---|---|---|
| Very low | وردی کەم — زۆر خێرا | `tiny` | one short line per subtitle, fastest | ~0.04× video length |
| Low | وردی کەم | `base` | two lines, plain translation | ~0.09× |
| Medium | وردی مامناوەند | `small` | whole-sentence translation, word timing | ~0.22× |
| High | وردی بەرز | `large-v3-turbo` | turbo model, strict VAD, 2 translation passes | ~0.55× |
| Perfect | وردی تەواو | `large-v3` | biggest model + a second proof-reading pass | ~1.05× |

Higher levels download a bigger speech model **once** (they are cached in
`~/KurdishSub/models`) and spend more compute per line. With an NVIDIA GPU the
`high` and `perfect` levels become roughly real-time.

Models are tried in order — if the biggest one cannot be loaded (low RAM), the
app automatically steps down to the next size instead of failing.

## Translation engines (all free)

| Engine | Needs | Quality | Notes |
|---|---|---|---|
| **Google Translate** (default) | nothing | good, natural | keyless web endpoint, no practical quota for personal use |
| **Lingva mirror** | nothing | same as Google | automatic fallback if `translate.googleapis.com` is blocked |
| **Google Gemini** | free API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | best — idiomatic, keeps slang/profanity/explicit content, understands context | the prompt forbids censoring or softening anything |
| **Local Ollama** (`qwen2.5:7b`…) | [Ollama](https://ollama.com), 8 GB+ RAM | very good | 100 % offline and unlimited |
| **NLLB-200 offline** | `requirements-offline.txt` | weaker | fully offline fallback |
| **LibreTranslate** | your own server | depends | for self-hosters |

Pick one in **Settings** (gear icon). On “Automatic” the app uses Gemini when a key
is saved, otherwise Google, and repairs any dropped line with the free engine.

Adult / explicit videos: nothing in the pipeline filters content. With Gemini the
safety filters are switched off explicitly, and the local models have no filters
at all. Say the word **"translate"** in the line and it is translated — no refusals.

## Subtitle quality details

* Sorani typography is enforced: Arabic look-alike letters (`ك ي ة`) are folded to
  Kurdish (`ک ی ه`), direction marks are removed, spacing around `، ؟ !` is fixed.
* Lines are balanced (two equally long lines, not one long + one short), maximum
  42 characters per line, max 2 lines, ~17–19 characters per second reading speed.
  A cue that is too fast is split into more cues automatically.
* Timings follow the spoken words, are padded slightly, never overlap, and never
  run longer than ~7 s.
* Bundled fonts: **Noto Naskh Arabic** and **Noto Sans Arabic** for the Kurdish
  text plus **Noto Sans** for the Latin line in bilingual mode (SIL OFL). They are
  loaded by ffmpeg through `fontsdir` + a private fontconfig file, so the burned
  text is real Kurdish shaping on any computer, even one with no Arabic fonts.
* Result screen: play the video, click a line to jump to it, double-click a line to
  fix its Kurdish text, change text size / position / bilingual mode and re-render,
  download the video or the `.srt`.

## Where files go

```
~/KurdishSub/
├── config.json      settings (API key stays here, on your machine)
├── models/          downloaded Whisper models
├── work/<job>/      audio, .ass, previews
└── output/<job>/    yourvideo.kurdish.mp4  +  yourvideo.ckb.srt
```
On Windows that is `C:\Users\<you>\KurdishSub`. Set `KURDISH_SUB_HOME` to change it.

## HTTP API

Everything the UI does is a plain JSON endpoint (handy for scripting):

```
GET  /api/health                    system check, tiers
POST /api/jobs/upload               multipart: file, tier  → job
POST /api/jobs/local                {path, tier}          → job (no copy of the file)
GET  /api/jobs/{id}                 status, progress, cues
GET  /api/jobs/{id}/video           finished video
GET  /api/jobs/{id}/srt | /ass      subtitle files
POST /api/jobs/{id}/restyle         {font_scale, position, bilingual, burn}
POST /api/jobs/{id}/cue             {index, text, start, end}
POST /api/jobs/{id}/cancel | /burn | /reveal
GET  /docs                          interactive API docs
```

Example without the UI:
```bash
curl -F "file=@movie.mkv" -F "tier=high" http://127.0.0.1:8765/api/jobs/upload
curl http://127.0.0.1:8765/api/jobs/<id>
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Browser does not open | go to `http://127.0.0.1:8765` manually (the console prints the port) |
| “The speech model could not be downloaded” | the first run needs internet for huggingface.co; retry, or use a VPN if it is blocked |
| “ffmpeg was not found” | re-run the start script (it installs the pip build), or install ffmpeg system-wide |
| Very slow on a laptop | pick a lower accuracy level, close other apps, or use an NVIDIA GPU |
| Subtitle lines too long/short | Settings → or the result screen → “Change subtitle look” |
| Blocked `translate.googleapis.com` | Settings → engine → Lingva mirror |

## Development

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests -q          # 47 tests, no network needed
KURDISH_SUB_DEMO=1 .venv/bin/python -m app --demo   # UI with fake speech
```

Layout:
```
app/
├── api.py              FastAPI routes + static hosting
├── __main__.py         CLI entry point (python -m app)
├── core/
│   ├── engine.py       job pipeline (probe → audio → speech → translate → burn)
│   ├── transcriber.py  faster-whisper wrapper, model download progress
│   ├── translator.py   free engines, Gemini/Ollama, repair + proof-read passes
│   ├── subtitles.py    timeline building, cue fitting, ASS/SRT/VTT writers
│   ├── sorani.py       Sorani normalisation and line wrapping
│   ├── tiers.py        the five accuracy levels
│   ├── ffmpeg_utils.py ffmpeg wrappers (extract, burn, preview, fonts)
│   ├── demo.py         offline doubles used by --demo and the tests
│   └── settings.py     config + paths
├── web/                the interface (no build step, no CDN)
└── assets/fonts/       bundled Sorani fonts (SIL OFL)
```

The original Chrome extension that inspired this project is still in
`Translate.zip`.
