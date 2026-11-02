/* ============================================================
   Kurdish Sub — front-end
   ============================================================ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STAGES = [
  ["probe", "Reading the video", "ڤیدیۆکە دەخوێنرێتەوە"],
  ["extract", "Extracting the audio", "دەنگ جیاکرێتەوە"],
  ["transcribe", "Listening to the speech", "گوێگرتن لە قسەکان"],
  ["translate", "Translating into Kurdish Sorani", "وەرگێڕان بۆ کوردی سۆرانی"],
  ["format", "Styling the subtitles", "ڕێکخستنی ژێرنووس"],
  ["burn", "Burning subtitles into the video", "نووسین لەناو ڤیدیۆ"],
];

// rough processing time as a fraction of the video length (CPU)
const TIER_FACTOR = { very_low: 0.04, low: 0.09, medium: 0.22, high: 0.55, perfect: 1.05 };
const TIER_LEVEL = { very_low: 1, low: 2, medium: 3, high: 4, perfect: 5 };

const state = {
  tiers: [],
  tier: null,
  file: null,
  localPath: null,
  duration: 0,
  width: 0,
  height: 0,
  jobId: null,
  pollTimer: null,
  uploading: false,
  health: null,
  settings: null,
  providers: [],
  cueCount: 0,
  lastJob: null,
  cloud: false,
};

/* ---------------- helpers ---------------- */
function fmtBytes(n) {
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtDur(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
           : `${m}:${String(sec).padStart(2, "0")}`;
}
function fmtClock(s) { return fmtDur(s); }
function esc(t) { return (t || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function show(view) {
  $("#viewChoose").classList.toggle("hidden", view !== "choose");
  $("#viewProgress").classList.toggle("hidden", view !== "progress");
  $("#viewResult").classList.toggle("hidden", view !== "result");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- boot ---------------- */
async function boot() {
  renderSkeletonTiers();
  try {
    const health = await api("/api/health");
    state.health = health;
    state.tiers = health.tiers || [];
    if (health.system && health.system.demo) $("#demoBadge").classList.remove("hidden");
    state.cloud = !!(health.system && health.system.cloud);
    if (state.cloud) {
      $("#cloudBadge").classList.remove("hidden");
      $("#dzNote").textContent = "MP4, MKV, MOV, AVI, WEBM · any length · processed on a cloud GPU, then deleted";
    }
    renderTiers();
    if (health.system && health.system.ffmpeg === false) {
      toast("notice", "<b>ffmpeg was not found.</b> Kurdish Sub needs ffmpeg to read and write videos. " +
        "On Windows run <code>install-windows.bat</code> again, on Linux install the <code>ffmpeg</code> package.");
    }
  } catch (err) {
    renderSkeletonTiers(err.message);
  }
  try {
    const s = await api("/api/settings");
    state.settings = s.settings;
    state.providers = s.providers || [];
    renderSettings();
  } catch (e) { /* settings are optional for the main flow */ }
  loadLanguages();
  loadHistory();
}

/* ---------------- accuracy cards ---------------- */
function renderSkeletonTiers() {
  $("#accGrid").innerHTML = Array.from({ length: 5 }).map(() =>
    `<div class="acc" style="pointer-events:none;opacity:.5"><div class="bars">${"<i></i>".repeat(5)}</div>
     <div class="tier-name">Loading…</div><div class="tier-blurb">…</div></div>`).join("");
}

function estimate(tierKey) {
  if (!state.duration) return null;
  const gpu = state.health && state.health.system && state.health.system.device === "cuda";
  let secs = state.duration * (TIER_FACTOR[tierKey] || 0.3);
  if (gpu) secs /= 5;
  const translate = 12; // network round trips for translation
  return secs + translate;
}

function renderTiers() {
  const grid = $("#accGrid");
  grid.innerHTML = "";
  state.tiers.forEach((t) => {
    const level = TIER_LEVEL[t.key] || 3;
    const bars = Array.from({ length: 5 }).map((_, i) =>
      `<i style="height:${30 + i * 17}%;${i < level ? "background:var(--accent);opacity:.85" : ""}"></i>`).join("");
    const est = estimate(t.key);
    const btn = document.createElement("button");
    btn.className = "acc";
    btn.style.setProperty("--accent", t.accent || "#22d3a7");
    btn.dataset.key = t.key;
    btn.innerHTML = `
      <div class="tick">✓</div>
      <div class="bars">${bars}</div>
      <div class="tier-name">${esc(t.name)}</div>
      <div class="tier-ku ku">${esc(t.ku)}</div>
      <div class="tier-blurb">${esc(t.blurb)}</div>
      <div class="tier-eta">${est ? `<b>≈ ${fmtDur(est)}</b> ${state.cloud ? "on the cloud GPU" : "on this computer"}` : `<b>${esc(t.model_size)}</b> model`}</div>`;
    btn.addEventListener("click", () => selectTier(t.key));
    grid.appendChild(btn);
  });
  if (state.tier) selectTier(state.tier, true);
}

function selectTier(key, silent) {
  state.tier = key;
  $$(".acc").forEach((b) => b.classList.toggle("selected", b.dataset.key === key));
  updateStartState();
  if (!silent) refreshEstimates();
}

function refreshEstimates() {
  if (!state.duration) return;
  state.tiers.forEach((t) => {
    const btn = $(`.acc[data-key="${t.key}"]`);
    const est = estimate(t.key);
    if (btn && est) {
      const eta = btn.querySelector(".tier-eta");
      if (eta) eta.innerHTML = `<b>≈ ${fmtDur(est)}</b> ${state.cloud ? "on the cloud GPU" : "on this computer"}`;
    }
  });
}

function updateStartState() {
  const ready = !!state.tier && (!!state.file || !!state.localPath);
  $("#btnStart").disabled = !ready;
  const hint = $("#startHint");
  if (!state.file && !state.localPath) hint.textContent = "Choose a video first.";
  else if (!state.tier) hint.textContent = "Pick one of the five accuracy levels.";
  else {
    const t = state.tiers.find((x) => x.key === state.tier);
    hint.innerHTML = `Ready — <b style="color:var(--text)">${esc(t ? t.name : "")}</b> into Kurdish Sorani.`;
  }
  const card = $("#accuracyCard");
  card.style.opacity = state.file || state.localPath ? "1" : ".45";
  card.style.pointerEvents = state.file || state.localPath ? "auto" : "none";
}

/* ---------------- file picking ---------------- */
function acceptFile(file) {
  if (!file) return;
  state.localPath = null;
  state.file = file;
  state.duration = 0;
  const url = URL.createObjectURL(file);
  const v = document.createElement("video");
  v.preload = "metadata";
  v.muted = true;
  v.src = url;
  v.addEventListener("loadedmetadata", () => {
    state.duration = v.duration || 0;
    state.width = v.videoWidth; state.height = v.videoHeight;
    paintFileCard(file.name, file.size);
    // thumbnail
    try {
      v.currentTime = Math.min(Math.max(1, v.duration * 0.12), Math.max(0.1, v.duration - 0.2));
    } catch (e) {}
  });
  v.addEventListener("seeked", () => {
    try {
      const c = document.createElement("canvas");
      c.width = 336; c.height = Math.round(336 * (v.videoHeight / Math.max(1, v.videoWidth)));
      c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
      $("#thumb").style.backgroundImage = `url(${c.toDataURL("image/jpeg", 0.8)})`;
    } catch (e) {}
  });
  paintFileCard(file.name, file.size);
  refreshEstimates();
  updateStartState();
}

function paintFileCard(name, size) {
  $("#fileSelected").classList.remove("hidden");
  $("#fName").textContent = name;
  const chips = [];
  if (state.duration) chips.push(`<span class="chip">⏱ <b>${fmtDur(state.duration)}</b></span>`);
  if (size) chips.push(`<span class="chip">💾 <b>${fmtBytes(size)}</b></span>`);
  if (state.width) chips.push(`<span class="chip">🖥 <b>${state.width}×${state.height}</b></span>`);
  chips.push(state.cloud
    ? `<span class="chip">☁ <b>processed on the cloud GPU</b></span>`
    : `<span class="chip">🔒 <b>stays on this computer</b></span>`);
  $("#fChips").innerHTML = chips.join("");
}

function clearFile() {
  state.file = null;
  state.localPath = null;
  state.duration = 0; state.width = 0; state.height = 0;
  $("#fileInput").value = "";
  $("#fileSelected").classList.add("hidden");
  $("#thumb").style.backgroundImage = "";
  updateStartState();
}

async function useLocalPath(path) {
  try {
    const info = await api("/api/probe", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const m = info.media || {};
    state.file = null;
    state.localPath = path;
    state.duration = m.duration || 0;
    state.width = m.display_width || m.width || 0;
    state.height = m.display_height || m.height || 0;
    paintFileCard(path.split(/[\\/]/).pop(), m.size || 0);
    refreshEstimates();
    updateStartState();
  } catch (err) {
    toast("error", `Could not read that file: ${esc(err.message)}`);
  }
}

/* ---------------- start a job ---------------- */
async function startJob() {
  if (!state.tier) return;
  if (state.localPath) {
    try {
      const r = await api("/api/jobs/local", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: state.localPath, tier: state.tier }),
      });
      state.jobId = r.job.id;
      state.lastJob = r.job;
      enterProgress();
    } catch (err) { toast("error", `Could not start: ${esc(err.message)}`); }
    return;
  }
  if (!state.file) return;
  state.uploading = true;
  enterProgress(true);

  // Big files are sent in pieces: tunnels and weak connections refuse one huge
  // request, and a piece that fails is simply sent again.
  if (state.file.size > 40 * 1024 * 1024) {
    uploadInPieces(state.file);
    return;
  }

  const form = new FormData();
  form.append("file", state.file, state.file.name);
  form.append("tier", state.tier);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs/upload");
  xhr.upload.addEventListener("progress", (e) => {
    if (!e.lengthComputable) return;
    const pct = (e.loaded / e.total) * 100;
    $("#pBar").style.width = `${pct * 0.08}%`;
    $("#pPct").textContent = `${Math.round(pct * 0.08)}%`;
    $("#pMsg").textContent = `${state.cloud ? "Sending the video to the cloud" : "Copying the video into the workspace"} … ` +
      `${fmtBytes(e.loaded)} / ${fmtBytes(e.total)}`;
  });
  xhr.addEventListener("load", () => {
    state.uploading = false;
    if (xhr.status >= 200 && xhr.status < 300) {
      const r = JSON.parse(xhr.responseText);
      state.jobId = r.job.id;
      state.lastJob = r.job;
      pollJob();
    } else {
      let msg = "Upload failed";
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
      failJob(msg);
    }
  });
  xhr.addEventListener("error", () => { state.uploading = false; failJob("The video could not be sent to the app (is it still running?)."); });
  xhr.send(form);
}

/* ---------------- resumable, piece-by-piece upload ---------------- */
async function uploadInPieces(file) {
  const CHUNK = 4 * 1024 * 1024;
  const setBar = (sentBytes) => {
    const pct = (sentBytes / Math.max(1, file.size)) * 100;
    $("#pBar").style.width = `${pct * 0.08}%`;
    $("#pPct").textContent = `${Math.round(pct * 0.08)}%`;
    $("#pMsg").textContent = `Sending the video ${state.cloud ? "to the cloud " : ""}… ` +
      `${fmtBytes(sentBytes)} / ${fmtBytes(file.size)}`;
  };
  let start;
  try {
    start = await api("/api/uploads/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, size: file.size }),
    });
  } catch (err) { state.uploading = false; failJob(`Could not start the upload: ${err.message}`); return; }

  const uid = start.upload_id;
  const size = Math.max(1024 * 1024, Math.min(32 * 1024 * 1024, start.chunk_size || CHUNK));
  let sent = 0;
  setBar(0);
  for (let index = 0; index * size < file.size; index++) {
    const blob = file.slice(index * size, Math.min(file.size, (index + 1) * size));
    let ok = false, lastError = "";
    for (let attempt = 0; attempt < 4 && !ok; attempt++) {
      try {
        const res = await fetch(`/api/uploads/${uid}/part?index=${index}`, {
          method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob,
        });
        if (res.ok) { ok = true; break; }
        lastError = res.status === 413 ? "the connection refused a piece" : `HTTP ${res.status}`;
      } catch (err) { lastError = err.message || "network error"; }
      await new Promise((r) => setTimeout(r, 700 * (attempt + 1)));
    }
    if (!ok) {
      state.uploading = false;
      failJob(`The video stopped uploading (${lastError}). Check the internet connection ` +
              `and press “Create Kurdish subtitles” again.`);
      return;
    }
    sent += blob.size;
    setBar(sent);
  }

  $("#pMsg").textContent = "Putting the video together …";
  try {
    const r = await api(`/api/uploads/${uid}/finish`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier: state.tier }),
    });
    state.uploading = false;
    state.jobId = r.job.id;
    state.lastJob = r.job;
    enterProgress();
    pollJob();
  } catch (err) {
    state.uploading = false;
    failJob(`The video could not be put together: ${err.message}`);
  }
}

/* ---------------- progress view ---------------- */
function renderStages() {
  $("#stageList").innerHTML = STAGES.map(([key, label, ku]) => `
    <div class="stage-row" data-stage="${key}">
      <div class="dot">✓</div>
      <div><div>${label}</div><div style="font-size:12px;color:var(--dim)" class="ku">${ku}</div></div>
      <div class="st-extra" data-extra="${key}"></div>
    </div>`).join("");
}

function enterProgress(skeleton) {
  show("progress");
  renderStages();
  $("#pName").textContent = state.file ? state.file.name : (state.localPath || "").split(/[\\/]/).pop();
  $("#pPct").textContent = "0%";
  $("#pBar").style.width = "0%";
  $("#pMsg").textContent = skeleton ? "Copying the video …" : "Starting …";
  $("#pElapsed").textContent = "0:00 elapsed";
  $("#pEta").textContent = "";
  $("#pNotice").innerHTML = "";
  $("#pTip").textContent = "You can keep this window open in the background.";
  if (!skeleton) pollJob();
}

let pollFails = 0;
async function pollJob() {
  if (!state.jobId) return;
  clearTimeout(state.pollTimer);
  try {
    const { job } = await api(`/api/jobs/${state.jobId}?cues=false`);
    pollFails = 0;
    updateProgress(job);
    state.lastJob = job;
    if (job.status === "done") { openResult(job.id); return; }
    if (job.status === "waiting_review") { openResult(job.id); return; }
    if (job.status === "error") { failJob(job.error || job.message); return; }
    if (job.status === "cancelled") { show("choose"); return; }
  } catch (err) {
    pollFails++;
    if (pollFails > 8) { failJob("Lost connection to the local app. Is it still running?"); return; }
  }
  state.pollTimer = setTimeout(pollJob, 850);
}

function updateProgress(job) {
  const pct = Math.max(0, Math.min(100, job.progress || 0));
  $("#pPct").textContent = `${Math.round(pct)}%`;
  $("#pBar").style.width = `${pct}%`;
  $("#pMsg").textContent = job.message || job.stage_label || "Working …";
  $("#pElapsed").textContent = `${fmtClock(job.elapsed)} elapsed`;
  $("#pEta").textContent = job.eta ? `about ${fmtClock(job.eta)} left` : "";
  const order = STAGES.map((s) => s[0]);
  const currentIdx = order.indexOf(job.stage);
  $$(".stage-row").forEach((row) => {
    const key = row.dataset.stage;
    const idx = order.indexOf(key);
    row.classList.toggle("done", currentIdx > idx || job.status === "done");
    row.classList.toggle("active", currentIdx === idx && job.status !== "done");
    const extra = row.querySelector(`[data-extra="${key}"]`);
    if (!extra) return;
    if (key === "transcribe" && (currentIdx === idx || currentIdx > idx) && job.language_name)
      extra.textContent = `${job.language_name}${job.model ? " · " + job.model : ""}`;
    if (key === "translate" && currentIdx > idx && job.cue_count)
      extra.textContent = `${job.cue_count} lines`;
    if (key === "burn" && currentIdx === idx) extra.textContent = "";
  });
  if (job.warnings && job.warnings.length && !$("#pNotice").innerHTML) {
    $("#pNotice").innerHTML = `<div class="notice info">${job.warnings.map(esc).join("<br>")}</div>`;
  }
}

function failJob(message) {
  clearTimeout(state.pollTimer);
  show("progress");
  $("#pBar").style.width = "100%";
  $("#pBar").style.background = "linear-gradient(90deg,#f2565b,#ff8a8d)";
  $("#pPct").textContent = "!";
  $("#pMsg").textContent = "Stopped";
  $("#pNotice").innerHTML = `<div class="notice error"><div><b>Could not finish:</b><br>${esc(message)}</div></div>
    <div class="result-actions"><button class="btn" id="btnRetry">Try again</button>
    <button class="btn ghost" id="btnBackChoose">Back</button></div>`;
  $("#btnRetry").onclick = () => { $("#btnStart").disabled = false; startJob(); };
  $("#btnBackChoose").onclick = () => show("choose");
}

/* ---------------- result view ---------------- */
async function openResult(id) {
  clearTimeout(state.pollTimer);
  show("result");
  let job;
  try {
    const r = await api(`/api/jobs/${id}`);
    job = r.job;
  } catch (e) { return; }
  state.jobId = id;
  state.lastJob = job;
  const outUrl = `/api/jobs/${id}/video`;
  const srcUrl = `/api/jobs/${id}/source`;
  const video = $("#rVideo");
  const burned = job.has_output;
  $("#rTitle").textContent = burned ? "Your Kurdish subtitled video is ready" : "Kurdish subtitles are ready";
  $("#rSub").textContent = burned
    ? "The Sorani subtitles are burned into the picture — this file plays anywhere, on any device."
    : "Check the subtitles below, then burn them into the video.";
  video.src = burned ? `${outUrl}?v=${Date.now()}` : srcUrl;
  const dl = $("#rDownload");
  dl.href = `${outUrl}?download=true`;
  dl.classList.toggle("hidden", !burned);
  $("#rSrt").href = `/api/jobs/${id}/srt`;
  $("#rSrt").classList.toggle("hidden", !job.has_srt);
  $("#rFolder").classList.toggle("hidden", !burned || state.cloud);
  $("#rCount").textContent = `${job.cue_count} lines`;
  state.cueCount = job.cue_count;

  let noticeHtml = "";
  if (job.warnings && job.warnings.length)
    noticeHtml += `<div class="notice info">${job.warnings.map(esc).join("<br>")}</div>`;
  if (state.cloud && burned) {
    noticeHtml += `<div class="notice info">The finished file (${fmtBytes(job.output_size)}) is also kept on the
      cloud machine while this session is open. If a very big download stops, open the folder icon on the
      left of the Colab tab — the same file is under <code>kurdish-sub-cloud/output/</code> — or use the
      Drive copy.</div>`;
  }
  if (!burned && job.status === "waiting_review") {
    noticeHtml += `<div class="notice"><div><b>Review mode.</b> The subtitles are ready but not burned yet.
      ${job.has_output ? "" : ""}<div class="result-actions" style="margin-top:10px">
      <button class="btn primary small" id="btnBurnNow">Burn subtitles into the video</button></div></div></div>`;
  }
  $("#rNotice").innerHTML = noticeHtml;
  const folderBtn = $("#rFolder");
  if (folderBtn) folderBtn.onclick = () => api(`/api/jobs/${id}/reveal`, { method: "POST" }).catch(() => {});
  const burnNow = $("#btnBurnNow");
  if (burnNow) burnNow.onclick = () => burnNowClicked(id);

  renderCues(job);
  $("#stylePanel").classList.add("hidden");
  loadHistory();
}

async function burnNowClicked(id) {
  try {
    await api(`/api/jobs/${id}/burn`, { method: "POST" });
    enterProgress();
    pollJob();
  } catch (err) { toast("error", esc(err.message)); }
}

function renderCues(job) {
  const list = $("#rCues");
  const cues = job.cues || [];
  if (!cues.length) { list.innerHTML = `<div class="skeleton">No subtitles.</div>`; return; }
  list.innerHTML = cues.map((c, i) => `
    <div class="cue" data-t="${c.start}" data-i="${i}">
      <div class="t">${fmtDur(c.start)}</div>
      <div class="txt">${esc(c.text)}${c.source ? `<span class="src">${esc(c.source)}</span>` : ""}</div>
    </div>`).join("");
  $$(".cue", list).forEach((row) => {
    row.addEventListener("click", () => {
      const v = $("#rVideo");
      v.currentTime = parseFloat(row.dataset.t) + 0.05;
      v.play().catch(() => {});
      v.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    row.addEventListener("dblclick", async (ev) => {
      ev.stopPropagation();
      const i = parseInt(row.dataset.i, 10);
      const current = job.cues[i].text.replace(/\n/g, " ");
      const next = prompt("Kurdish text for this line:", current);
      if (next == null) return;
      try {
        await api(`/api/jobs/${job.id}/cue`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ index: i, text: next }),
        });
        const r = await api(`/api/jobs/${job.id}`);
        renderCues(r.job);
        toast("info", "Line saved. Re-render the video to see the change.");
      } catch (err) { toast("error", esc(err.message)); }
    });
  });
}

/* ---------------- settings ---------------- */
function renderSettings() {
  const s = state.settings || {};
  const sel = $("#setProvider");
  const opts = [{ key: "auto", label: "Automatic — best free engine available" }]
    .concat(state.providers.map((p) => ({ key: p.key, label: p.label })));
  sel.innerHTML = opts.map((o) => `<option value="${o.key}">${esc(o.label)}</option>`).join("");
  sel.value = s.provider || "auto";
  $("#geminiField").classList.toggle("hidden", !(sel.value === "gemini" || s.gemini_api_key_set));
  $("#ollamaField").classList.toggle("hidden", sel.value !== "ollama");
  $("#setOllamaModel").value = s.ollama_model || "qwen2.5:7b";
  $("#setGeminiKey").value = "";
  $("#setGeminiKey").placeholder = s.gemini_api_key_set ? "a key is saved — paste a new one to replace it" : "paste your free key (AIza…)";
  $("#setDevice").value = s.device || "auto";
  $("#setFont").value = s.font || "Noto Naskh Arabic";
  $("#setAutoBurn").checked = s.auto_burn !== false;
  $("#setOpenBrowser").checked = s.open_browser !== false;
  if (s.source_language) $("#setLang").value = s.source_language;
  const dev = (state.health && state.health.system) || {};
  $("#deviceHelp").innerHTML = dev.gpu
    ? "✅ NVIDIA GPU detected — the higher accuracy levels will be fast."
    : "No GPU detected: the higher levels will be slower. A GPU makes “High” and “Perfect” much faster.";
  $("#pathsHelp").innerHTML = dev.output_dir
    ? `Finished videos and .srt files: <code>${esc(dev.output_dir)}</code><br>
       ${dev.free_space_gb >= 0 ? `Free disk space: <b>${dev.free_space_gb} GB</b>` : ""}<br>
       ffmpeg: <code>${esc(dev.ffmpeg_path || "not found")}</code>`
    : "—";
}

async function loadLanguages() {
  try {
    const r = await api("/api/languages");
    $("#setLang").innerHTML = r.languages
      .map((l) => `<option value="${l.code}">${esc(l.name)}</option>`).join("");
    if (state.settings && state.settings.source_language)
      $("#setLang").value = state.settings.source_language;
  } catch (e) {}
}

function openSettings() { $("#drawer").classList.add("open"); $("#backdrop").classList.add("open"); }
function closeSettings() { $("#drawer").classList.remove("open"); $("#backdrop").classList.remove("open"); }

async function saveSettings() {
  const patch = {
    provider: $("#setProvider").value,
    device: $("#setDevice").value,
    font: $("#setFont").value,
    source_language: $("#setLang").value,
    ollama_model: $("#setOllamaModel").value || undefined,
    auto_burn: $("#setAutoBurn").checked,
    open_browser: $("#setOpenBrowser").checked,
  };
  const key = $("#setGeminiKey").value.trim();
  if (key) patch.gemini_api_key = key;
  try {
    const r = await api("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    state.settings = r.settings;
    const saved = $("#settingsSaved");
    saved.style.transition = "opacity .2s"; saved.style.opacity = "1";
    setTimeout(() => { saved.style.opacity = "0"; }, 1600);
    renderSettings();
  } catch (err) { toast("error", esc(err.message)); }
}

/* ---------------- history ---------------- */
async function loadHistory() {
  try {
    const r = await api("/api/jobs");
    const jobs = r.jobs || [];
    const box = $("#history");
    if (!jobs.length) { box.innerHTML = `<div class="skeleton">Nothing yet — your videos will appear here.</div>`; return; }
    box.innerHTML = jobs.map((j) => `
      <div class="hrow" data-id="${j.id}">
        <span class="hstatus ${j.status === "done" ? "done" : j.status === "error" ? "error" : ""}">${esc(j.status.replace("_", " "))}</span>
        <span class="hn">${esc(j.name)}</span>
        <span style="color:var(--dim);font-size:12px">${esc(j.tier_name || "")}</span>
        <span style="color:var(--dim);font-size:12px">${j.duration ? fmtDur(j.duration) : ""}</span>
      </div>`).join("");
    $$(".hrow", box).forEach((row) => row.addEventListener("click", () => {
      const id = row.dataset.id;
      state.jobId = id;
      const j = jobs.find((x) => x.id === id);
      if (j && (j.status === "done" || j.status === "waiting_review")) openResult(id);
      else { enterProgress(); pollJob(); }
    }));
  } catch (e) {}
}

/* ---------------- notices ---------------- */
function toast(kind, html) {
  const host = $("#viewChoose");
  let box = $("#globalNotice");
  if (!box) {
    box = document.createElement("div");
    box.id = "globalNotice";
    box.style.marginTop = "18px";
    host.appendChild(box);
  }
  box.innerHTML = `<div class="notice ${kind === "error" ? "error" : kind === "info" ? "info" : ""}">
     <div>${html}</div></div>`;
  clearTimeout(box._t);
  box._t = setTimeout(() => box.remove(), 12000);
}

/* ---------------- style panel ---------------- */
function openStylePanel() {
  const panel = $("#stylePanel");
  panel.classList.toggle("hidden");
}
async function applyStyle() {
  if (!state.jobId) return;
  const style = {
    font_scale: parseInt($("#sSize").value, 10) / 100,
    position: $("#sPos").value,
    bilingual: $("#sBilingual").checked,
    burn: false,
  };
  try {
    await api(`/api/jobs/${state.jobId}/restyle`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(style),
    });
    await api(`/api/jobs/${state.jobId}/burn`, { method: "POST" });
    enterProgress();
    pollJob();
  } catch (err) { toast("error", esc(err.message)); }
}

/* ---------------- wiring ---------------- */
const dz = $("#dropzone");
$("#btnChoose").addEventListener("click", (e) => { e.stopPropagation(); $("#fileInput").click(); });
dz.addEventListener("click", () => $("#fileInput").click());
$("#fileInput").addEventListener("change", (e) => acceptFile(e.target.files[0]));
$("#btnClearFile").addEventListener("click", (e) => { e.stopPropagation(); clearFile(); });
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => {
  e.preventDefault(); dz.classList.add("drag");
}));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => {
  e.preventDefault(); if (ev === "dragleave" && dz.contains(e.relatedTarget)) return; dz.classList.remove("drag");
}));
dz.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) acceptFile(f);
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

$("#btnBrowseDisk").addEventListener("click", async (e) => {
  e.stopPropagation();
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const r = await api("/api/pick-file", { method: "POST" });
    if (r.ok && r.path) await useLocalPath(r.path);
    else if (r.reason === "cancelled") { /* user closed the dialog */ }
    else { $("#fileInput").click(); }
  } catch (err) {
    $("#fileInput").click();
  } finally { btn.disabled = false; }
});

$("#btnStart").addEventListener("click", startJob);
$("#btnCancel").addEventListener("click", async () => {
  if (state.jobId) { try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }); } catch (e) {} }
  clearTimeout(state.pollTimer);
  show("choose");
  loadHistory();
});
$("#btnNew").addEventListener("click", () => { clearFile(); state.tier = null; $$(".acc").forEach(b => b.classList.remove("selected")); updateStartState(); show("choose"); });
$("#btnRestyle").addEventListener("click", openStylePanel);
$("#btnApplyStyle").addEventListener("click", applyStyle);
$("#sSize").addEventListener("input", (e) => { $("#vSize").textContent = `${e.target.value}%`; });
$("#sPos").addEventListener("change", (e) => {
  const map = { bottom: "Bottom", lower: "Lower third", middle: "Middle", top: "Top" };
  $("#vPos").textContent = map[e.target.value] || e.target.value;
});
$("#btnSettings").addEventListener("click", openSettings);
$("#btnCloseSettings").addEventListener("click", closeSettings);
$("#backdrop").addEventListener("click", closeSettings);
$("#btnSaveSettings").addEventListener("click", saveSettings);
$("#setProvider").addEventListener("change", (e) => {
  $("#geminiField").classList.toggle("hidden", e.target.value !== "gemini" && !(state.settings || {}).gemini_api_key_set);
  $("#ollamaField").classList.toggle("hidden", e.target.value !== "ollama");
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSettings(); });

boot();
