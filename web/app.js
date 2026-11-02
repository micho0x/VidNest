/* ============================================================
   VidNest — front end
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

const urlInput = $("#url");
const downloadBtn = $("#downloadBtn");
const downloadSub = $("#downloadSub");
const qualitySel = $("#quality");
const audioSel = $("#audio");
const folderPath = $("#folderPath");
const hint = $("#hint");
const jobsEl = $("#jobs");
const queueEl = $("#queue");
const countEl = $("#count");
const toasts = $("#toasts");
const engine = $("#engine");
const engineText = $("#engineText");
const engineVersion = $("#engineVersion");
const jsRuntimeEl = $("#jsRuntime");
const preview = $("#preview");
const pvTitle = $("#pvTitle");
const pvMeta = $("#pvMeta");
const pvChips = $("#pvChips");
const pvThumb = $("#pvThumb");
const advanced = $("#advanced");
const moreBtn = $("#moreBtn");

const AUDIO_FORMATS = [
  ["m4a", "M4A · best quality"],
  ["mp3", "MP3"],
  ["opus", "Opus"],
  ["flac", "FLAC"],
  ["wav", "WAV"],
];

const AUDIO_FORMAT_LABEL = Object.fromEntries(AUDIO_FORMATS);

const state = {
  folder: "",
  engineReady: false,
  audioFormat: "m4a",
  muteVideo: false,
  infoUrl: "",
  infoCache: new Map(),
  prefsApplied: false,
  cookiesFile: null,
};

const QUALITY_OPTIONS = ["best", "2160", "1440", "1080", "720", "480", "360", "none"];
const COOKIE_OPTIONS = ["", "file", "edge", "chrome", "firefox", "brave", "opera", "vivaldi"];

const cards = new Map();
let previous = new Map();
let pollTimer = null;
let infoTimer = null;
let prefsTimer = null;
let infoSeq = 0;
let toldAboutDone = false;
let starting = false;
let polling = false;
let pollQueued = false;
let titleTimer = null;
let titleOriginal = null;
let titleRestore = null;
let audioCtx = null;
let offlineTold = false;

/* ----------------------------- helpers ----------------------------- */

const isLink = (v) => /^https?:\/\/\S+/i.test(v.trim());
const splitLinks = (text) => text.split(/\s+/).map((s) => s.trim()).filter(isLink);

function toast(message, kind = "info", ttl = 4200) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  if (kind === "err") el.setAttribute("role", "alert");
  el.textContent = message;
  toasts.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 320);
  }, ttl);
}

function setHint(text, kind = "") {
  const cls = "hint" + (kind ? " " + kind : "");
  if (hint.textContent === text && hint.className === cls) return;
  hint.textContent = text;
  hint.className = cls;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function beep() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const ctx = audioCtx;
    const now = ctx.currentTime;
    [880, 1320].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.16);
      gain.gain.linearRampToValueAtTime(0.12, now + i * 0.16 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.16 + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.16);
      osc.stop(now + i * 0.16 + 0.24);
    });
  } catch (_) { /* audio blocked */ }
}

function flashTitle(text) {
  if (titleOriginal === null) titleOriginal = document.title;
  clearTimeout(titleTimer);
  if (titleRestore) window.removeEventListener("focus", titleRestore);
  titleRestore = () => {
    clearTimeout(titleTimer);
    document.title = titleOriginal || document.title;
    titleOriginal = null;
    titleRestore = null;
  };
  document.title = text;
  window.addEventListener("focus", titleRestore);
  titleTimer = setTimeout(titleRestore, 12000);
}

function qualityLabel(q) {
  if (!q || q === "best") return "Best video";
  if (q === "2160") return "4K video";
  return `${q}p video`;
}

/* ----------------------------- preferences ----------------------------- */

function readPrefs() {
  return {
    quality: qualitySel.value,
    audioFormat: state.audioFormat,
    muteVideo: state.muteVideo,
    cookies: $("#cookies").value,
    subs: $("#optSubs").checked,
    subLangs: $("#subLangs").value,
    playlist: $("#optPlaylist").checked,
    playlistItems: $("#playlistItems").value.trim(),
    sponsor: $("#optSponsor").checked,
    archive: $("#optArchive").checked,
    sound: $("#optSound").checked,
    rateLimit: $("#rateLimit").value.trim(),
    clipStart: $("#clipStart").value.trim(),
    clipEnd: $("#clipEnd").value.trim(),
  };
}

function applyPrefs(p) {
  if (!p) return;
  const set = (id, val) => { const el = $(id); if (el && val !== undefined && val !== null) el.value = val; };
  const check = (id, val) => { const el = $(id); if (el) el.checked = !!val; };

  set("#quality", QUALITY_OPTIONS.includes(p.quality) ? p.quality : "best");
  state.audioFormat = AUDIO_FORMAT_LABEL[p.audioFormat] ? p.audioFormat : "m4a";
  state.muteVideo = !!p.muteVideo;
  const cookieChoice = p.cookies === "file" && !state.cookiesFile?.set ? "" : p.cookies;
  set("#cookies", COOKIE_OPTIONS.includes(cookieChoice) ? cookieChoice : "");
  set("#subLangs", p.subLangs);
  set("#playlistItems", p.playlistItems);
  set("#rateLimit", p.rateLimit);
  set("#clipStart", p.clipStart);
  set("#clipEnd", p.clipEnd);
  check("#optSubs", p.subs);
  check("#optPlaylist", p.playlist);
  check("#optSponsor", p.sponsor);
  check("#optArchive", p.archive);
  check("#optSound", p.sound);

  if (p.subs || p.playlist || p.playlistItems || p.sponsor || p.archive ||
      p.rateLimit || p.clipStart || p.clipEnd ||
      (p.subLangs && p.subLangs !== "en")) {
    advanced.hidden = false;
    moreBtn.setAttribute("aria-expanded", "true");
  }

  syncAudioOptions();
  syncChips();
}

function savePrefs() {
  if (!state.prefsApplied) return;   // don't overwrite saved settings with DOM defaults
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => {
    api("/api/prefs", {
      method: "POST",
      body: JSON.stringify({ prefs: readPrefs() }),
    }).catch(() => {});
  }, 450);
}

/* ----------------------------- cookies.txt file ----------------------------- */

function syncCookieLabel() {
  const opt = $('#cookies option[value="file"]');
  if (!opt) return;
  const info = state.cookiesFile;
  if (info && info.set) {
    const leaf = String(info.name || "cookies.txt").replace(/^(www\.)?youtube\.com_/i, "");
    opt.textContent = `${leaf} · ${info.cookies}`;
    $("#cookies")?.setAttribute("title", `Using ${info.path || info.name}. Your browser can stay open.`);
  } else {
    opt.textContent = "cookies.txt file…";
  }
}

/** Ask the server to open the native file dialog and remember the export. */
async function pickCookieFile() {
  try {
    const res = await api("/api/cookies/pick", { method: "POST", body: "{}" });
    if (res.cancelled) {
      toast("No file picked — choose cookies.txt again.", "info", 3200);
      return false;
    }
    if (!res.ok) {
      toast(res.error || "That file can't be used.", "err", 6500);
      return false;
    }

    state.cookiesFile = { set: true, name: res.name, cookies: res.cookies, path: res.path };
    syncCookieLabel();
    state.infoUrl = "";
    state.infoCache.clear();

    if (!res.youtube) {
      toast(`${res.name} has no YouTube cookies — export it while youtube.com is open.`, "warn", 8000);
    } else if (!res.signedIn) {
      toast(`${res.name}: ${res.cookies} cookies, but no YouTube login. Sign in, export again.`, "warn", 8000);
    } else {
      toast(`Using ${res.name} — ${res.cookies} cookies. Your browser can stay open.`, "ok", 5200);
    }
    return true;
  } catch (err) {
    toast(err.message, "err", 6500);
    return false;
  }
}

/* ----------------------------- the audio picker ----------------------------- */

/** The audio dropdown means two different things depending on the video choice,
 *  so it is rebuilt whenever quality changes — no impossible combinations. */
function syncAudioOptions() {
  const audioOnly = qualitySel.value === "none";
  const wanted = audioOnly
    ? (AUDIO_FORMAT_LABEL[state.audioFormat] ? state.audioFormat : "m4a")
    : (state.muteVideo ? "none" : "best");

  const choices = audioOnly
    ? AUDIO_FORMATS
    : [["best", "Best audio"], ["none", "No audio — video only"]];

  audioSel.innerHTML = "";
  choices.forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    audioSel.appendChild(opt);
  });
  audioSel.value = wanted;
  audioSel.title = audioOnly
    ? "Format for the audio file"
    : "Sound track to pair with the video";
}

function updateDownloadButton() {
  const q = qualitySel.value;
  const a = audioSel.value;
  let kind, sub;

  if (q === "none") {
    kind = "audio";
    const fmt = (AUDIO_FORMAT_LABEL[a] || "M4A").split(" · ")[0];
    sub = "Audio only · " + fmt;
  } else if (a === "none") {
    kind = "silent";
    sub = qualityLabel(q) + " · no sound";
  } else {
    kind = "merge";
    sub = qualityLabel(q) + " + best audio";
  }

  downloadBtn.dataset.kind = kind;
  downloadSub.textContent = sub;
}

/* ----------------------------- link state ----------------------------- */

function refresh() {
  const links = splitLinks(urlInput.value);
  const ready = state.engineReady && links.length > 0 && !starting;
  downloadBtn.disabled = !ready;

  if (!state.engineReady) {
    setHint("Setting up the download engine — this happens once.", "warn");
  } else if (urlInput.value.trim() && !links.length) {
    setHint("That doesn't look like a link yet — it should start with http.", "warn");
  } else if (!links.length) {
    setHint("Paste a link, pick what you want, hit Download.");
  } else if (qualitySel.value === "none") {
    setHint("Audio only — the file will have no video.");
  } else if (audioSel.value === "none") {
    setHint("Video only — the file will have no sound.");
  } else if (links.length > 1) {
    setHint(`${links.length} links ready — they'll download one after another.`);
  } else {
    setHint("Ready. Hit Download.");
  }

  updateDownloadButton();
}

async function loadState() {
  try {
    const s = await api("/api/state");
    offlineTold = false;
    const wasReady = state.engineReady;
    state.folder = s.folder;
    state.engineReady = s.engineReady;
    state.cookiesFile = s.cookiesFile || null;
    folderPath.textContent = s.folder;
    folderPath.title = s.folder;

    engine.classList.toggle("ok", s.engineReady);
    engine.classList.toggle("bad", !s.engineReady);
    engineText.textContent = s.engineReady ? "engine ready" : "setting up…";
    engine.setAttribute("aria-label", engineText.textContent);
    engineVersion.textContent = s.engineVersion ? `yt-dlp ${s.engineVersion}` : "—";
    jsRuntimeEl.textContent = s.jsRuntime ? ` · ${s.jsRuntime}` : "";

    if (!state.prefsApplied) {
      applyPrefs(s.prefs);
      state.prefsApplied = true;
    }
    syncCookieLabel();
    cookiePrev = $("#cookies").value;
    refresh();

    if (!wasReady && s.engineReady) {
      state.infoUrl = "";
      maybeLoadPreview();
    }
    if (!s.engineReady) setTimeout(loadState, 2500);
  } catch (err) {
    engine.classList.add("bad");
    engineText.textContent = "offline";
    refresh();
    if (!offlineTold) {
      offlineTold = true;
      toast("Can't reach the VidNest server. Is the window still open?", "err", 7000);
    }
    setTimeout(loadState, 3000);
  }
}

/* ----------------------------- folder ----------------------------- */

async function chooseFolder() {
  try {
    $("#chooseBtn").disabled = true;
    const { folder } = await api("/api/folder/pick", { method: "POST", body: "{}" });
    if (folder) {
      state.folder = folder;
      folderPath.textContent = folder;
      folderPath.title = folder;
      toast("Downloads will go to " + folder, "ok");
    }
  } catch (err) {
    toast(err.message, "err");
  } finally {
    $("#chooseBtn").disabled = false;
  }
}

async function openFolder(folder) {
  try {
    await api("/api/folder/open", {
      method: "POST",
      body: JSON.stringify({ folder: folder || state.folder }),
    });
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ----------------------------- preview ----------------------------- */

const QUALITY_VALUES = ["2160", "1440", "1080", "720", "480", "360"];

function syncChips() {
  const current = qualitySel.value;
  [...pvChips.querySelectorAll(".chip")].forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.q === current);
  });
}

function renderPreview(info, url) {
  preview.hidden = false;
  pvTitle.className = "preview-title";
  pvTitle.textContent = info.title || url;
  pvTitle.title = info.title || url;

  const bits = [];
  if (info.uploader) bits.push(info.uploader);
  if (info.duration) bits.push(info.duration);
  if (info.live) bits.push("live");
  if (info.heights?.length) bits.push(`up to ${info.heights[0]}p`);
  pvMeta.textContent = bits.join(" · ");

  if (info.thumbnail) {
    pvThumb.onload = () => pvThumb.classList.add("loaded");
    pvThumb.onerror = () => { pvThumb.style.display = "none"; };
    pvThumb.style.display = "";
    pvThumb.src = info.thumbnail;
  } else {
    pvThumb.removeAttribute("src");
    pvThumb.classList.remove("loaded");
  }

  pvChips.innerHTML = "";
  const available = new Set(
    (info.heights || []).length
      ? QUALITY_VALUES.filter((q) => Number(q) <= Math.max(...info.heights) + 1)
      : []
  );

  const addChip = (value, label, title) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.q = value;
    chip.textContent = label;
    if (title) chip.title = title;
    chip.addEventListener("click", () => {
      qualitySel.value = value;
      syncAudioOptions();
      syncChips();
      refresh();
      savePrefs();
    });
    pvChips.appendChild(chip);
  };

  addChip("best", "Best", "Always take whatever is highest");
  available.forEach((q) => {
    addChip(q, q === "2160" ? "4K" : `${q}p`);
  });
  syncChips();
}

function renderPreviewLoading() {
  preview.hidden = false;
  pvTitle.className = "preview-title loading";
  pvTitle.textContent = "Reading the video…";
  pvMeta.textContent = "";
  pvChips.innerHTML = "";
  pvThumb.removeAttribute("src");
  pvThumb.classList.remove("loaded");
}

function renderPreviewError(message) {
  preview.hidden = false;
  pvTitle.className = "preview-title error";
  pvTitle.textContent = message;
  pvMeta.textContent = "";
  pvChips.innerHTML = "";
  pvThumb.removeAttribute("src");
  pvThumb.classList.remove("loaded");
}

async function maybeLoadPreview() {
  const links = splitLinks(urlInput.value);
  if (links.length !== 1) {
    state.infoUrl = "";
    preview.hidden = true;
    return;
  }
  if (!state.engineReady) return;
  const url = links[0];
  if (url === state.infoUrl) return;
  state.infoUrl = url;

  const cookies = $("#cookies").value;
  const cacheKey = cookies + "|" + url;
  if (state.infoCache.has(cacheKey)) {
    renderPreview(state.infoCache.get(cacheKey), url);
    return;
  }

  const seq = ++infoSeq;
  renderPreviewLoading();
  try {
    const info = await api("/api/info", {
      method: "POST",
      body: JSON.stringify({ url, cookies }),
    });
    if (seq !== infoSeq || state.infoUrl !== url) return;
    if (state.infoCache.size > 60) state.infoCache.clear();
    state.infoCache.set(cacheKey, info);
    renderPreview(info, url);
  } catch (err) {
    if (seq !== infoSeq || state.infoUrl !== url) return;
    state.infoUrl = "";          // let the next blur/input try again
    renderPreviewError(err.message);
  }
}

/* ----------------------------- queue ----------------------------- */

const STATUS_TEXT = {
  queued: "Waiting in queue…",
  downloading: "Downloading",
  processing: "Merging & finalizing…",
  done: "Saved",
  error: "Failed",
  cancelled: "Cancelled",
};

const BADGE_CLASS = { merge: "main", audio: "audio", video: "video" };

function makeCard(job) {
  const el = document.createElement("article");
  el.className = "job";
  el.innerHTML = `
    <div class="job-top">
      <span class="badge"></span>
      <span class="job-name"></span>
      <button class="job-open" type="button" hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 4h6v6"/><path d="M20 4 11 13"/>
          <path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>
        </svg>
        Folder
      </button>
      <button class="job-cancel" type="button" title="Cancel" aria-label="Cancel download">
        <svg viewBox="0 0 24 24" fill="none" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </div>
    <div class="bar"><div class="bar-fill"></div></div>
    <div class="job-meta">
      <span class="pct"></span>
      <span class="state"></span>
      <span class="extra"></span>
    </div>`;

  const refs = {
    el,
    badge: el.querySelector(".badge"),
    name: el.querySelector(".job-name"),
    open: el.querySelector(".job-open"),
    cancel: el.querySelector(".job-cancel"),
    bar: el.querySelector(".bar"),
    fill: el.querySelector(".bar-fill"),
    pct: el.querySelector(".pct"),
    metaState: el.querySelector(".state"),
    extra: el.querySelector(".extra"),
    job: null,
  };

  refs.cancel.addEventListener("click", async () => {
    refs.cancel.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST", body: "{}" });
      refs.metaState.textContent = "Cancelling…";
    } catch (err) {
      toast(err.message, "err");
      refs.cancel.disabled = false;
    }
  });

  refs.open.addEventListener("click", () => openFolder(refs.job?.folder));

  return refs;
}

function updateCard(refs, job) {
  refs.job = job;
  const status = job.status;
  refs.el.className = "job " + status;
  refs.badge.className = "badge " + (BADGE_CLASS[job.mode] || "main");
  refs.badge.textContent = job.label || "DOWNLOAD";

  refs.name.textContent = job.filename || job.title || job.url.replace(/^https?:\/\//, "").slice(0, 60);
  refs.name.title = job.title || job.url;
  refs.name.classList.toggle("faint", !job.filename && !job.title);

  const busy = status === "queued" || status === "processing";
  refs.bar.classList.toggle("indeterminate", busy);

  if (status === "done") {
    refs.fill.style.width = "100%";
  } else if (status === "downloading" || status === "error" || status === "cancelled") {
    refs.fill.style.width = Math.max(2, job.percent) + "%";
  }

  refs.pct.textContent = status === "downloading" ? job.percent.toFixed(0) + "%" : "";

  let text;
  if (status === "error") text = job.error || STATUS_TEXT.error;
  else if (status === "done") {
    if (job.note) text = job.note;
    else if (job.count > 1) text = `Saved ${job.count} files`;
    else text = "Saved — ready to play";
  } else if (status === "downloading" && !job.title) text = "Reading the video info…";
  else text = STATUS_TEXT[status] || status;
  refs.metaState.textContent = text;

  const bits = [];
  if (job.item) bits.push(`item ${job.item}`);
  if (status === "downloading") {
    if (job.speed) bits.push(job.speed);
    if (job.eta) bits.push("ETA " + job.eta);
    if (job.size) bits.push(job.size);
  }
  refs.extra.textContent = bits.join(" · ");

  refs.cancel.hidden = ["done", "error", "cancelled"].includes(status);
  refs.open.hidden = status !== "done";

  const before = previous.get(job.id);
  if (before && before !== status) {
    if (status === "done") {
      if (job.note) toast(job.note, "info");
      else toast("Saved: " + (job.filename || "download complete"), "ok");
      if (job.count > 0 && $("#optSound").checked && !toldAboutDone) {
        beep();
        flashTitle("✓ Download finished — VidNest");
        toldAboutDone = true;
        setTimeout(() => (toldAboutDone = false), 1500);
      }
    }
    if (status === "error") toast("Download failed — " + (job.error || "unknown error").slice(0, 120), "err", 7000);
    if (status === "cancelled") toast("Download cancelled", "info", 3000);
  }
  previous.set(job.id, status);
}

function renderJobs(list) {
  queueEl.hidden = list.length === 0;
  const active = list.filter((j) => ["queued", "downloading", "processing"].includes(j.status)).length;
  countEl.textContent = list.length ? `· ${list.length}${active ? ` (${active} active)` : ""}` : "";

  const seen = new Set();
  list.forEach((job) => {
    seen.add(job.id);
    let refs = cards.get(job.id);
    if (!refs) {
      refs = makeCard(job);
      cards.set(job.id, refs);
      jobsEl.appendChild(refs.el);
    }
    updateCard(refs, job);
  });

  let ordered = true;
  list.forEach((job, i) => {
    const el = cards.get(job.id)?.el;
    if (el && jobsEl.children[i] !== el) ordered = false;
  });
  if (!ordered) {
    list.forEach((job) => {
      const el = cards.get(job.id)?.el;
      if (el) jobsEl.appendChild(el);
    });
  }

  [...cards.keys()].forEach((id) => {
    if (!seen.has(id)) {
      cards.get(id).el.remove();
      cards.delete(id);
      previous.delete(id);
    }
  });
}

async function poll() {
  if (polling) {
    pollQueued = true;
    return;
  }
  polling = true;
  try {
    const { jobs } = await api("/api/jobs");
    renderJobs(jobs);
  } catch (_) { /* server busy or closed */ }
  finally {
    polling = false;
    if (pollQueued) {
      pollQueued = false;
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, 0);
    } else {
      schedule();
    }
  }
}

function schedule() {
  clearTimeout(pollTimer);
  const active = [...cards.values()].some(
    (r) => r.job && ["queued", "downloading", "processing"].includes(r.job.status)
  );
  pollTimer = setTimeout(poll, active ? 700 : 2500);
}

/* ----------------------------- actions ----------------------------- */

async function start() {
  if (starting) return;
  const links = splitLinks(urlInput.value);
  if (!state.engineReady) return toast("The engine is still setting up…", "warn");
  if (!links.length) return toast("Paste a full link first (it starts with http).", "err");

  const prefs = readPrefs();
  const audio = prefs.quality === "none"
    ? prefs.audioFormat
    : (prefs.muteVideo ? "none" : "best");

  starting = true;
  refresh();
  try {
    const res = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        urls: links.slice(0, 25),
        folder: state.folder,
        ...prefs,
        audio,
      }),
    });
    const n = res.jobs.length;
    res.jobs.forEach((job) => previous.set(job.id, "queued"));
    toast(`Queued ${n} download${n > 1 ? "s" : ""}`, "ok", 2600);
    urlInput.value = "";
    state.infoUrl = "";
    preview.hidden = true;
    poll();
  } catch (err) {
    toast(err.message, "err", 6500);
  } finally {
    starting = false;
    refresh();
    urlInput.focus();
  }
}

/* ----------------------------- wiring ----------------------------- */

downloadBtn.addEventListener("click", start);

urlInput.addEventListener("input", () => {
  refresh();
  clearTimeout(infoTimer);
  infoTimer = setTimeout(maybeLoadPreview, 700);
  if (splitLinks(urlInput.value).length !== 1) {
    preview.hidden = true;
    state.infoUrl = "";
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === "Enter") {
    e.preventDefault();
    if (splitLinks(urlInput.value).length) start();
    else setHint("Paste a link that starts with http first.", "warn");
  }
  if (e.key === "Escape") {
    urlInput.value = "";
    preview.hidden = true;
    state.infoUrl = "";
    refresh();
  }
});

urlInput.addEventListener("blur", () => setTimeout(maybeLoadPreview, 120));

/** A single-line input destroys line breaks, so parse pasted text ourselves.
 *  Handles "title\nhttps://…" and several links pasted at once. */
function acceptText(text) {
  const raw = String(text || "");
  const links = raw.split(/\s+/).map((s) => s.trim()).filter(isLink);
  urlInput.value = links.length ? links.join(" ") : raw.replace(/\s+/g, " ").trim();
  refresh();
  clearTimeout(infoTimer);
  infoTimer = setTimeout(maybeLoadPreview, 250);
}

urlInput.addEventListener("paste", (e) => {
  const text = e.clipboardData?.getData("text");
  if (!text) return;
  e.preventDefault();
  acceptText(text);
});

$("#pasteBtn").addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text.trim()) acceptText(text);
    else toast("Clipboard is empty", "info", 2600);
  } catch (_) {
    urlInput.focus();
    toast("Press Ctrl+V to paste the link", "info", 3200);
  }
});

$("#chooseBtn").addEventListener("click", chooseFolder);
$("#folderChip").addEventListener("click", chooseFolder);
$("#openBtn").addEventListener("click", () => openFolder());

moreBtn.addEventListener("click", () => {
  const open = advanced.hidden;
  advanced.hidden = !open;
  moreBtn.setAttribute("aria-expanded", String(open));
});

qualitySel.addEventListener("change", () => {
  syncAudioOptions();
  syncChips();
  refresh();
  savePrefs();
});

audioSel.addEventListener("change", () => {
  if (qualitySel.value === "none") state.audioFormat = audioSel.value;
  else state.muteVideo = audioSel.value === "none";
  refresh();
  savePrefs();
});

["#subLangs", "#playlistItems", "#rateLimit", "#clipStart",
 "#clipEnd", "#optSubs", "#optPlaylist", "#optSponsor", "#optArchive",
 "#optSound"].forEach((sel) => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener("change", () => {
    savePrefs();
  });
});

let cookiePrev = "";

$("#cookies").addEventListener("change", async () => {
  const chosen = $("#cookies").value;

  if (chosen !== "file") {
    cookiePrev = chosen;
    savePrefs();
    state.infoUrl = "";
    state.infoCache.clear();
    maybeLoadPreview();
    return;
  }

  const ok = await pickCookieFile();
  if (!ok) {
    $("#cookies").value = cookiePrev;   // cancelled or unusable file
    return;
  }

  cookiePrev = "file";
  savePrefs();
  maybeLoadPreview();
});

$("#clearBtn").addEventListener("click", async () => {
  try {
    await api("/api/jobs/clear", { method: "POST", body: "{}" });
    await poll();
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#updateBtn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Updating…";
  try {
    const res = await api("/api/engine/update", { method: "POST", body: "{}" });
    engineVersion.textContent = res.version ? `yt-dlp ${res.version}` : "—";
    state.infoCache.clear();
    toast("Engine updated — you're on the latest build.", "ok");
  } catch (err) {
    toast("Update failed: " + err.message, "err", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

refresh();
loadState();
poll();
urlInput.focus();
