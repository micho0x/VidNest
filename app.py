#!/usr/bin/env python3
"""
VidNest - a tiny local media downloader for Windows.
Server + UI. Powered by yt-dlp and FFmpeg (auto-downloaded into ./bin).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "VidNest"
VERSION = "2.1"

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin"
WEB = ROOT / "web"
HISTORY = ROOT / "history"
CONFIG_FILE = ROOT / "config.json"

YTDLP = BIN / "yt-dlp.exe"
FFMPEG = BIN / "ffmpeg.exe"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# A JavaScript runtime lets yt-dlp solve YouTube's player challenges
# (better format selection, fewer "confirm you're not a bot" errors).
# Look on PATH first, then at the copy install.bat may have put in bin\.
def find_js_runtime() -> tuple[str, str] | None:
    for name in ("deno", "node", "bun"):
        found = shutil.which(name)
        if found:
            return name, found
    for name in ("deno", "node", "bun"):
        local = BIN / f"{name}.exe"
        if local.is_file():
            return name, str(local)
    return None


JS_RUNTIME = find_js_runtime()

URL_RE = re.compile(r"^https?://\S+$", re.I)
ORIGIN_RE = re.compile(r"^https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$", re.I)
HOST_RE = re.compile(r"^(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$", re.I)
PROG_RE = re.compile(r"^VN_PROG\|(.*)$")
FILE_RE = re.compile(r"^VN_FILE\|(.*)$")
TITLE_RE = re.compile(r"^VN_TITLE\|(.*)$")
ITEM_RE = re.compile(r"^\[download\] Downloading item (\d+) of (\d+)$")
DEST_RE = re.compile(r"^\[download\] Destination: (.+)$")
MERGE_RE = re.compile(r"^\[Merger\] Merging formats into \"?(.+?)\"?$")
ARCHIVED_RE = re.compile(r"has already been recorded in the archive", re.I)
CLOCK_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?:\.\d+)?$|^(\d{1,5})(?:\.\d+)?$")
RATE_RE = re.compile(r"^\d+(?:\.\d+)?[KMGkmg]?$")

QUALITIES = {"best": None, "2160": 2160, "1440": 1440, "1080": 1080, "720": 720, "480": 480, "360": 360}
QUALITY_VALUES = set(QUALITIES) | {"none"}      # "none" = audio only, no video
AUDIO_FORMATS = {"m4a", "mp3", "opus", "flac", "wav", "best"}
AUDIO_VALUES = AUDIO_FORMATS | {"none", "best"}  # "none" = video only, no sound
COOKIE_SOURCES = {"", "file", "edge", "chrome", "firefox", "brave", "opera", "vivaldi", "chromium"}
SUB_LANGS = {"en", "ar", "es", "fr", "de", "pt", "hi", "ja", "ko", "ru", "tr", "it", "nl", "pl", "id", "all"}

DEFAULT_PREFS = {
    "quality": "best",
    "audioFormat": "m4a",
    "muteVideo": False,
    "cookies": "",
    "subs": False,
    "subLangs": "en",
    "playlist": False,
    "playlistItems": "",
    "sponsor": False,
    "archive": False,
    "sound": True,
    "rateLimit": "",
    "clipStart": "",
    "clipEnd": "",
}

DEFAULT_FOLDER = str(Path.home() / "Downloads" / APP_NAME)


# --------------------------------------------------------------------------- #
#  tiny helpers
# --------------------------------------------------------------------------- #
def log(*parts):
    print(f"[{APP_NAME}]", *parts, flush=True)


def human_size(num: float | None) -> str:
    if not num or num <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}"
        num /= 1024
    return ""


def human_speed(num: float | None) -> str:
    if not num or num <= 0:
        return ""
    return human_size(num) + "/s"


def human_eta(sec: float | None) -> str:
    if sec is None or sec < 0 or sec > 86400:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def human_duration(sec: float | None) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
    except Exception:
        pass


def sanitize_pref(key: str, value):
    default = DEFAULT_PREFS[key]
    if isinstance(default, bool):
        return bool(value)
    if key == "quality":
        return value if value in QUALITY_VALUES else default
    if key == "audioFormat":
        return value if value in AUDIO_FORMATS else default
    if key == "cookies":
        return value if value in COOKIE_SOURCES else ""
    if key == "subLangs":
        return value if value in SUB_LANGS else default
    return str(value or "")


CONFIG = load_config()
_SAVED_PREFS = CONFIG.get("prefs")
_saved = _SAVED_PREFS if isinstance(_SAVED_PREFS, dict) else {}
PREFS = {key: sanitize_pref(key, _saved.get(key, default)) for key, default in DEFAULT_PREFS.items()}


def save_prefs(new: dict) -> dict:
    global PREFS
    for key in DEFAULT_PREFS:
        if key in new:
            PREFS[key] = sanitize_pref(key, new[key])
    CONFIG["prefs"] = PREFS
    save_config(CONFIG)
    return PREFS


def resolve_mode(quality: str, audio: str) -> str:
    """The download button no longer has a mode: the two pickers define it."""
    if quality == "none":
        return "audio"
    if audio == "none":
        return "video"
    return "merge"


def clean_error(text: str) -> str:
    """Pull the useful line out of yt-dlp chatter."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR")]
    if errors:
        return re.sub(r"^ERROR:\s*", "", errors[0])[:400]
    warn = [ln for ln in lines if ln.startswith("WARNING")]
    if warn:
        return re.sub(r"^WARNING:\s*", "", warn[-1])[:400]
    return (lines[-1] if lines else "Unknown error")[:400]


def parse_clock(text: str) -> float | None:
    """'1:30' / '0:02:15' / '45' -> seconds."""
    m = CLOCK_RE.match(text.strip())
    if not m:
        return None
    if m.group(4) is not None:
        return float(m.group(4))
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def clip_label(start: str, end: str) -> str:
    def fmt(seconds: float) -> str:
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m{s:02d}s"
        return f"{m}m{s:02d}s" if m else f"{s}s"

    a = parse_clock(start)
    b = parse_clock(end) if end else None
    return f"clip {fmt(a) if a is not None else 'start'}-{fmt(b) if b is not None else 'end'}"


def friendly_error(msg: str, cookies: str = "") -> str:
    """Turn yt-dlp's raw errors into something a human can act on."""
    low = (msg or "").lower()
    who = "That cookies file" if cookies == "file" else (cookies or "the browser").capitalize()
    if cookies == "file" and ("cookie" in low or "netscape" in low):
        return "That cookies file can't be used — export it again from YouTube and pick the new file."
    if "cookie database" in low or "could not copy" in low:
        return f"{who} is running and its cookie file is locked — close it completely, then try again."
    if "dpapi" in low:
        return "Windows couldn't decrypt the browser cookies. Try closing the browser and retrying."
    if "unsupported browser" in low:
        return f"{who} isn't installed on this PC."
    if "could not find" in low and cookies:
        return f"No saved cookies found for {who}."
    if "sign in to confirm" in low:
        return ("YouTube wants a signed-in session — export YouTube cookies again, or pick a browser "
                "you're signed into." if cookies == "file" else
                "YouTube wants a signed-in session — turn on Cookies and pick the browser you use.")
    if "private video" in low or "members-only" in low:
        return "That video is private or members-only — turn on Cookies with a browser where you're signed in."
    return msg


def archive_path(folder: str) -> Path:
    HISTORY.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1(str(Path(folder)).lower().encode("utf-8")).hexdigest()[:12]
    return HISTORY / f"{tag}.txt"


# --------------------------------------------------------------------------- #
#  cookies.txt (exported by "Get cookies.txt LOCALLY")
# --------------------------------------------------------------------------- #
LOGIN_COOKIES = {"SAPISID", "__Secure-3PSID", "__Secure-1PSID", "SID", "LOGIN_INFO", "__Secure-3PAPISID"}


def cookie_file_path() -> Path | None:
    raw = str(CONFIG.get("cookiesFile") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def inspect_cookie_file(path: Path) -> dict:
    """Read a Netscape cookies.txt and describe it — values are never echoed."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": f"That file can't be read: {exc}"}

    count = 0
    domains: set[str] = set()
    signed_in = False
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        count += 1
        domains.add(parts[0].lstrip(".").lower())
        if parts[5] in LOGIN_COOKIES and parts[6].strip():
            signed_in = True

    if not count:
        return {
            "ok": False,
            "error": "That file has no cookie lines — export it again from YouTube "
                     "(the extension's Export button).",
        }

    return {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "cookies": count,
        "domains": sorted(domains)[:8],
        "youtube": any("youtube" in d or "google" in d for d in domains),
        "signedIn": signed_in,
    }


_COOKIE_CACHE: dict[str, object] = {}


def cookies_file_state() -> dict:
    path = cookie_file_path()
    if not path:
        return {"set": False, "ok": False, "name": "", "cookies": 0, "youtube": False, "signedIn": False}
    try:
        st = path.stat()
        stamp = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        stamp = str(path)
    info = _COOKIE_CACHE.get("info") if _COOKIE_CACHE.get("stamp") == stamp else None
    if not isinstance(info, dict):
        info = inspect_cookie_file(path)
        _COOKIE_CACHE["stamp"] = stamp
        _COOKIE_CACHE["info"] = info
    return {
        "set": True,
        "ok": bool(info.get("ok")),
        "name": path.name,
        "cookies": int(info.get("cookies") or 0),
        "youtube": bool(info.get("youtube")),
        "signedIn": bool(info.get("signedIn")),
    }


def set_cookies_file(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": "That file doesn't exist any more."}
    info = inspect_cookie_file(p)
    if not info.get("ok"):
        return info
    CONFIG["cookiesFile"] = str(p)
    save_config(CONFIG)
    _COOKIE_CACHE.clear()
    log("cookies file ->", p.name, f"({info['cookies']} cookies)")
    return info


def cookie_args(cookies: str, path: str = "") -> list[str]:
    """Build the yt-dlp cookie flags for a job."""
    if cookies == "file":
        target = Path(path) if path else cookie_file_path()
        if target and target.is_file():
            return ["--cookies", str(target)]
        return []
    if cookies and cookies in COOKIE_SOURCES:
        return ["--cookies-from-browser", cookies]
    return []


# --------------------------------------------------------------------------- #
#  dependencies (yt-dlp + ffmpeg)
# --------------------------------------------------------------------------- #
def tool_ready() -> bool:
    return YTDLP.exists() and FFMPEG.exists()


def download_file(url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {label} ...")
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last = 0.0
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last > 0.5:
                last = now
                pct = f"{done * 100 / total:5.1f}%" if total else human_size(done)
                print(f"\r  {label}: {pct}  ({human_size(done)} / {human_size(total)})", end="", flush=True)
    print()
    tmp.replace(dest)


def ensure_dependencies() -> None:
    """Download yt-dlp.exe and ffmpeg.exe into ./bin on first run."""
    if not YTDLP.exists():
        download_file(YTDLP_URL, YTDLP, "yt-dlp")

    if not FFMPEG.exists():
        print("  Fetching FFmpeg (one time, ~150 MB) ...")
        tmp_zip = BIN / "ffmpeg.zip"
        download_file(FFMPEG_URL, tmp_zip, "FFmpeg")
        wanted = {"ffmpeg.exe", "ffprobe.exe"}
        with zipfile.ZipFile(tmp_zip) as zf:
            for member in zf.namelist():
                name = member.rsplit("/", 1)[-1].lower()
                if name in wanted:
                    with zf.open(member) as src, open(BIN / name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        try:
            tmp_zip.unlink()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  jobs
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, url: str, mode: str, folder: str, opts: dict):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.mode = mode
        self.folder = folder
        self.opts = opts
        self.status = "queued"          # queued | downloading | processing | done | error | cancelled
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.filename = ""
        self.title = ""
        self.size = ""
        self.error = ""
        self.file = ""
        self.note = ""
        self.item = ""
        self.count = 0
        self.created = time.time()
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def label(self) -> str:
        quality = str(self.opts.get("quality") or "best")
        size = "best" if not QUALITIES.get(quality) else f"{quality}p"
        if self.mode == "audio":
            return "AUDIO · " + str(self.opts.get("audioFormat") or "m4a").upper()
        if self.mode == "video":
            return f"VIDEO · {size}"
        return f"MAIN · {size}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "mode": self.mode,
            "label": self.label(),
            "folder": self.folder,
            "status": self.status,
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "filename": self.filename,
            "title": self.title,
            "size": self.size,
            "error": self.error,
            "file": self.file,
            "note": self.note,
            "item": self.item,
            "count": self.count,
            "created": self.created,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE: "queue.Queue[Job]" = queue.Queue()
CURRENT: dict[str, Job | None] = {"job": None}


def build_command(job: Job, opts: dict) -> list[str]:
    clip_start = str(opts.get("clipStart") or "").strip()
    clip_end = str(opts.get("clipEnd") or "").strip()
    has_clip = bool(clip_start) and CLOCK_RE.match(clip_start) is not None

    stem = "%(title).150B [%(id)s]"
    if has_clip:
        stem += f" [{clip_label(clip_start, clip_end)}]"
    out_tpl = str(Path(job.folder) / (stem + ".%(ext)s"))
    cmd = [
        str(YTDLP),
        "--newline",
        "--no-colors",
        "--ffmpeg-location", str(BIN),
        "--progress",
        "--progress-template",
        "download:VN_PROG|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
        "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(progress.status)s",
        "--print", "before_dl:VN_TITLE|%(title)s",
        "--print", "after_move:VN_FILE|%(filepath)s",
        "-o", out_tpl,
        "--windows-filenames",
        "--trim-filenames", "180",
        "--retries", "5",
        "--fragment-retries", "5",
        "--concurrent-fragments", "8",
        "--embed-metadata",
        "--ignore-errors",
    ]

    if JS_RUNTIME:
        cmd += ["--js-runtimes", f"{JS_RUNTIME[0]}:{JS_RUNTIME[1]}"]

    # ---- playlist handling
    if opts.get("playlist"):
        cmd += ["--yes-playlist"]
        items = str(opts.get("playlistItems") or "").strip()
        if re.match(r"^[\d:\-,\.\s]+$", items):
            cmd += ["--playlist-items", items]
    else:
        cmd += ["--no-playlist"]

    # ---- cookies
    cmd += cookie_args(str(opts.get("cookies") or ""), str(opts.get("cookiesFile") or ""))

    # ---- speed limit
    rate = str(opts.get("rateLimit") or "").strip()
    if rate and RATE_RE.match(rate):
        cmd += ["--limit-rate", rate]

    # ---- sponsorblock
    if opts.get("sponsor"):
        cmd += ["--sponsorblock-remove", "default"]

    # ---- clip (download only a section)
    if has_clip:
        section = f"*{clip_start}-{clip_end if clip_end and CLOCK_RE.match(clip_end) else 'inf'}"
        cmd += ["--download-sections", section]

    # ---- skip already-downloaded videos
    # Clips are partial downloads: the archive records the whole video id, which
    # would make every later clip of the same video skip.
    if opts.get("archive") and not has_clip:
        cmd += ["--download-archive", str(archive_path(job.folder))]

    # ---- format selection
    quality = str(opts.get("quality") or "best")
    if quality not in QUALITY_VALUES:
        quality = "best"
    height = QUALITIES.get(quality)
    cap = f"[height<={height}]" if height else ""

    if job.mode == "audio":
        fmt = str(opts.get("audioFormat") or "m4a")
        if fmt not in AUDIO_FORMATS:
            fmt = "m4a"
        if fmt == "m4a":
            cmd += ["-f", "ba[ext=m4a]/ba/b"]
        else:
            cmd += ["-f", "ba/b"]
        cmd += ["-x", "--audio-format", fmt, "--audio-quality", "0"]
        # WAV has no tag/cover-art support — embedding would fail the extras.
        if fmt != "wav":
            cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    else:
        if job.mode == "video":
            # Video only: never fetch a sound track.
            cmd += ["-f", f"bv*{cap}/bv*" if cap else "bv*", "--merge-output-format", "mp4/mkv"]
        else:
            cmd += [
                "-f",
                f"bv*{cap}+ba/b{cap}/bv*+ba/b" if cap else "bv*+ba/b",
                "--merge-output-format", "mp4/mkv",
            ]

        if opts.get("subs"):
            lang = str(opts.get("subLangs") or "en")
            if lang not in SUB_LANGS:
                lang = "en"
            langs = "all" if lang == "all" else f"{lang},{lang}-orig"
            cmd += [
                "--write-subs", "--write-auto-subs",
                "--sub-langs", langs,
                "--embed-subs",
            ]

    cmd.append(job.url)
    return cmd


def cleanup_partials(job: Job) -> None:
    """Remove half-written files left behind by a cancelled download."""
    try:
        for path in Path(job.folder).iterdir():
            if not path.is_file():
                continue
            name = path.name.lower()
            if not (".part" in name or ".ytdl" in name or
                    re.search(r"\.temp\.[a-z0-9]+$", name) or
                    re.search(r"\.f\d+\.[a-z0-9]+$", name)):
                continue
            if path.stat().st_mtime >= job.created - 5:
                path.unlink(missing_ok=True)
    except Exception:
        pass


def run_job(job: Job) -> None:
    if job.cancelled:
        job.status = "cancelled"
        return
    Path(job.folder).mkdir(parents=True, exist_ok=True)
    job.status = "downloading"
    job.percent = 0.0
    CURRENT["job"] = job

    cmd = build_command(job, job.opts)
    log("start", job.mode, job.url)
    try:
        job.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            cwd=str(ROOT),
        )
    except Exception as exc:  # pragma: no cover
        job.status = "error"
        job.error = f"Could not start yt-dlp: {exc}"
        CURRENT["job"] = None
        return

    if job.cancelled:  # a cancel landed between the queue and Popen
        kill_tree(job.proc)

    tail: list[str] = []
    archived = False
    assert job.proc.stdout is not None
    for raw in job.proc.stdout:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)

        m = PROG_RE.match(line)
        if m:
            parts = m.group(1).split("|")
            if len(parts) >= 6:
                done, total, estimate, speed, eta, status = parts[:6]
                try:
                    done_f = float(done) if done not in ("NA", "None", "") else 0.0
                    tot_f = 0.0
                    for cand in (total, estimate):
                        if cand not in ("NA", "None", ""):
                            tot_f = float(cand)
                            break
                    if tot_f > 0:
                        job.percent = min(100.0, done_f * 100.0 / tot_f)
                    job.size = human_size(tot_f)
                    job.speed = human_speed(float(speed)) if speed not in ("NA", "None", "") else ""
                    job.eta = human_eta(float(eta)) if eta not in ("NA", "None", "") else ""
                    if status == "finished":
                        job.percent = 100.0
                        job.speed = ""
                        job.eta = ""
                except ValueError:
                    pass
            continue

        m = TITLE_RE.match(line)
        if m:
            job.title = m.group(1).strip()[:200]
            continue

        m = FILE_RE.match(line)
        if m:
            path = m.group(1).strip()
            job.file = path
            job.filename = Path(path).name
            job.percent = 100.0
            job.count += 1
            continue

        m = ITEM_RE.match(line)
        if m:
            job.item = f"{m.group(1)}/{m.group(2)}"
            job.percent = 0.0
            continue

        if ARCHIVED_RE.search(line):
            archived = True
            continue

        m = DEST_RE.match(line)
        if m:
            job.filename = Path(m.group(1).strip()).name
            continue

        m = MERGE_RE.match(line)
        if m:
            job.filename = Path(m.group(1).strip()).name
            job.status = "processing"
            continue

        if line.startswith(("[ExtractAudio]", "[Merger]", "[SponsorBlock]")):
            job.status = "processing"

    code = job.proc.wait()
    job.proc = None
    CURRENT["job"] = None

    if job.cancelled:
        job.status = "cancelled"
        job.speed = ""
        job.eta = ""
        cleanup_partials(job)
        log("cancelled", job.id)
        return

    errors = [ln for ln in tail if ln.startswith("ERROR")]
    if job.count > 0:
        job.status = "done"
        job.percent = 100.0
        job.speed = ""
        job.eta = ""
        if errors:
            job.note = "Saved — some extras were skipped"
        if job.opts.get("subs"):
            cleanup_sidecars(job)
        log("done ->", job.file or job.filename, f"({job.count} file(s))")
    elif archived and code == 0:
        job.status = "done"
        job.percent = 100.0
        job.speed = ""
        job.eta = ""
        job.note = "Already downloaded — skipped"
        log("skipped (archive)", job.url)
    elif code == 0 and not errors:
        job.speed = ""
        job.eta = ""
        if job.opts.get("archive"):
            job.status = "done"
            job.percent = 100.0
            job.note = "Already downloaded — skipped"
            log("skipped (archive)", job.url)
        else:
            job.status = "error"
            job.error = "Nothing was saved — yt-dlp finished without producing a file."
            log("done (no file)", job.id)
    else:
        job.status = "error"
        job.speed = ""
        job.eta = ""
        job.error = friendly_error(
            clean_error("\n".join(tail)) or f"yt-dlp exited with code {code}",
            str(job.opts.get("cookies") or ""),
        )
        log("failed:", job.error)


def kill_tree(proc: subprocess.Popen | None) -> None:
    """Kill the download process and anything it spawned (FFmpeg etc.)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def cleanup_sidecars(job: Job) -> None:
    """Subtitles get embedded in the video, so drop the loose .vtt/.srt files."""
    try:
        for path in Path(job.folder).glob("*"):
            if path.is_file() and path.suffix.lower() in (".vtt", ".srt", ".ass", ".json3"):
                if path.stat().st_mtime >= job.created - 5:
                    path.unlink(missing_ok=True)
    except Exception:
        pass


def worker_loop() -> None:
    while True:
        job = JOB_QUEUE.get()
        try:
            if job.cancelled:
                job.status = "cancelled"
                continue
            run_job(job)
        except Exception as exc:  # pragma: no cover
            job.status = "error"
            job.error = str(exc)
            CURRENT["job"] = None
        finally:
            JOB_QUEUE.task_done()


# --------------------------------------------------------------------------- #
#  link preview
# --------------------------------------------------------------------------- #
INFO_CACHE: dict[str, dict] = {}
INFO_LOCK = threading.Lock()


def fetch_info(url: str, cookies: str) -> dict:
    state = cookies_file_state()
    key = f"{cookies}|{state.get('name')}|{state.get('cookies')}|{url}"
    with INFO_LOCK:
        if key in INFO_CACHE:
            return INFO_CACHE[key]

    cmd = [
        str(YTDLP), "-J", "--skip-download", "--no-playlist",
        "--no-warnings", "--ffmpeg-location", str(BIN),
    ]
    if JS_RUNTIME:
        cmd += ["--js-runtimes", f"{JS_RUNTIME[0]}:{JS_RUNTIME[1]}"]
    cmd += cookie_args(cookies)
    cmd.append(url)

    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, creationflags=CREATE_NO_WINDOW, cwd=str(ROOT),
    )
    if out.returncode != 0 or not (out.stdout or "").strip():
        raise ValueError(friendly_error(clean_error((out.stderr or "") + "\n" + (out.stdout or "")), cookies))

    data = json.loads(out.stdout)
    heights = sorted(
        {int(f["height"]) for f in (data.get("formats") or []) if f.get("height")},
        reverse=True,
    )
    info = {
        "title": data.get("title") or "",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": human_duration(data.get("duration")),
        "thumbnail": data.get("thumbnail") or "",
        "heights": heights,
        "live": bool(data.get("is_live")),
        "url": data.get("webpage_url") or url,
    }
    with INFO_LOCK:
        if len(INFO_CACHE) > 80:
            INFO_CACHE.clear()
        INFO_CACHE[key] = info
    return info


# --------------------------------------------------------------------------- #
#  folder helpers
# --------------------------------------------------------------------------- #
def pick_folder() -> str:
    script = ROOT / "picker.py"
    try:
        out = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=300,
            creationflags=CREATE_NO_WINDOW,
        )
        path = (out.stdout or "").strip().splitlines()
        return path[-1].strip() if path else ""
    except Exception as exc:
        log("folder picker failed:", exc)
        return ""


def open_folder(path: str) -> None:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    os.startfile(str(p))  # type: ignore[attr-defined]


def pick_cookie_file() -> str:
    script = ROOT / "picker.py"
    try:
        out = subprocess.run(
            [sys.executable, str(script), "file"],
            capture_output=True, text=True, timeout=300,
            creationflags=CREATE_NO_WINDOW,
        )
        path = (out.stdout or "").strip().splitlines()
        return path[-1].strip() if path else ""
    except Exception as exc:
        log("cookie picker failed:", exc)
        return ""


# --------------------------------------------------------------------------- #
#  HTTP server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence per-request noise
        pass

    def handle_one_request(self) -> None:
        """Browsers drop keep-alive sockets all the time; don't print tracebacks."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.timeout):
            self.close_connection = True

    # ---- helpers ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            self.close_connection = True

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length or length > 1_000_000:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _file(self, rel: str, ctype: str) -> None:
        path = (WEB / rel).resolve()
        try:
            path.relative_to(WEB.resolve())
        except ValueError:
            self._json({"error": "bad path"}, 400)
            return
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        self._send(200, path.read_bytes(), ctype)

    def _local_only(self) -> bool:
        """Block DNS-rebinding: a remote page must not reach this server."""
        host = (self.headers.get("Host") or "").strip()
        return not host or bool(HOST_RE.match(host))

    # ---- routes -----------------------------------------------------------
    def do_GET(self):
        if not self._local_only():
            self._json({"error": "forbidden"}, 403)
            return
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif route == "/style.css":
            self._file("style.css", "text/css; charset=utf-8")
        elif route == "/app.js":
            self._file("app.js", "application/javascript; charset=utf-8")
        elif route == "/api/state":
            self._json(self.api_state())
        elif route.startswith("/api/jobs/"):
            job = JOBS.get(route.rsplit("/", 1)[-1])
            if job:
                self._json(job.to_dict())
            else:
                self._json({"error": "no such job"}, 404)
        elif route == "/api/jobs":
            with JOBS_LOCK:
                ordered = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
            active = [j for j in ordered if j.status in ("queued", "downloading", "processing")]
            finished = [j for j in ordered if j not in active][:60]
            self._json({"jobs": [j.to_dict() for j in active + finished]})
        elif route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_only():
            self._json({"error": "forbidden"}, 403)
            return
        route = urlparse(self.path).path
        origin = self.headers.get("Origin")
        if origin and not ORIGIN_RE.match(origin):
            self._json({"error": "Cross-origin request blocked."}, 403)
            return
        data = self._body()

        if route == "/api/folder/pick":
            path = pick_folder()
            if path:
                path = str(Path(path))
                CONFIG["folder"] = path
                save_config(CONFIG)
            self._json({"folder": path})

        elif route == "/api/folder/set":
            raw = str(data.get("folder") or "").strip().strip('"')
            if not raw:
                self._json({"error": "Folder is empty."}, 400)
                return
            try:
                p = Path(os.path.expandvars(raw)).expanduser()
                p.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._json({"error": f"Can't use that folder: {exc}"}, 400)
                return
            CONFIG["folder"] = str(p)
            save_config(CONFIG)
            self._json({"folder": str(p)})

        elif route == "/api/folder/open":
            folder = str(data.get("folder") or CONFIG.get("folder") or DEFAULT_FOLDER)
            try:
                open_folder(folder)
                self._json({"ok": True})
            except Exception as exc:
                self._json({"error": str(exc)}, 400)

        elif route == "/api/cookies/pick":
            path = pick_cookie_file()
            if not path:
                self._json({"cancelled": True})
                return
            self._json(set_cookies_file(path))

        elif route == "/api/cookies/set":
            raw = str(data.get("path") or "").strip().strip('"')
            if not raw:
                self._json({"ok": False, "error": "No file given."}, 400)
                return
            info = set_cookies_file(os.path.expandvars(raw))
            self._json(info, 200 if info.get("ok") else 400)

        elif route == "/api/prefs":
            self._json({"prefs": save_prefs(data.get("prefs") or {})})

        elif route == "/api/info":
            url = str(data.get("url") or "").strip()
            cookies = str(data.get("cookies") or "")
            if cookies not in COOKIE_SOURCES:
                cookies = ""
            if not URL_RE.match(url):
                self._json({"error": "Not a link."}, 400)
                return
            if cookies == "file" and not cookie_file_path():
                self._json({"error": "No cookies.txt picked yet — choose Cookies → cookies.txt file."}, 400)
                return
            if not tool_ready():
                self._json({"error": "The engine is still setting up."}, 503)
                return
            try:
                self._json(fetch_info(url, cookies))
            except subprocess.TimeoutExpired:
                self._json({"error": "The video info took too long to load."}, 504)
            except Exception as exc:
                self._json({"error": str(exc)}, 400)

        elif route == "/api/jobs":
            urls = data.get("urls")
            if not isinstance(urls, list):
                urls = [data.get("url")]
            urls = [str(u).strip() for u in urls if u and str(u).strip()]
            quality = str(data.get("quality") or "best")
            audio = str(data.get("audio") or "best")
            folder = str(data.get("folder") or CONFIG.get("folder") or DEFAULT_FOLDER)

            if not urls:
                self._json({"error": "Paste a link first."}, 400)
                return
            bad = [u for u in urls if not URL_RE.match(u)]
            if bad:
                self._json({"error": f"That doesn't look like a link: {bad[0][:60]}"}, 400)
                return
            if quality not in QUALITY_VALUES:
                self._json({"error": "Unknown video quality."}, 400)
                return
            if audio not in AUDIO_VALUES:
                self._json({"error": "Unknown audio choice."}, 400)
                return
            if quality == "none" and audio == "none":
                self._json({"error": "Pick at least one of video or audio — not neither."}, 400)
                return
            cookies = str(data.get("cookies") or "")
            if cookies not in COOKIE_SOURCES:
                self._json({"error": "Unknown cookies choice."}, 400)
                return
            if cookies == "file" and not cookie_file_path():
                self._json({"error": "No cookies.txt picked yet — choose Cookies → cookies.txt file."}, 400)
                return
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._json({"error": f"Can't write to that folder: {exc}"}, 400)
                return
            if not tool_ready():
                self._json({"error": "The download engine isn't ready yet."}, 503)
                return

            mode = resolve_mode(quality, audio)
            opts = {
                "quality": quality,
                "audioFormat": audio if mode == "audio" else str(data.get("audioFormat") or "m4a"),
                "cookies": cookies,
                "cookiesFile": str(cookie_file_path() or "") if cookies == "file" else "",
                "subs": bool(data.get("subs")),
                "subLangs": data.get("subLangs") or "en",
                "playlist": bool(data.get("playlist")),
                "playlistItems": data.get("playlistItems") or "",
                "sponsor": bool(data.get("sponsor")),
                "archive": bool(data.get("archive")),
                "rateLimit": data.get("rateLimit") or "",
                "clipStart": data.get("clipStart") or "",
                "clipEnd": data.get("clipEnd") or "",
            }

            created = []
            for url in urls[:25]:
                job = Job(url, mode, folder, opts)
                with JOBS_LOCK:
                    JOBS[job.id] = job
                JOB_QUEUE.put(job)
                created.append(job.to_dict())
            self._json({"jobs": created})

        elif route.startswith("/api/jobs/") and route.endswith("/cancel"):
            job = JOBS.get(route.split("/")[3])
            if not job:
                self._json({"error": "no such job"}, 404)
                return
            job.cancelled = True
            log("cancel", job.id, job.status)
            if job.status == "queued":
                job.status = "cancelled"
            kill_tree(job.proc)
            self._json({"ok": True})

        elif route == "/api/engine/update":
            if CURRENT["job"] is not None:
                self._json({"error": "A download is running — wait for it to finish (or cancel it) first."}, 409)
                return
            try:
                download_file(YTDLP_URL, YTDLP, "yt-dlp")
                _YTVERSION.clear()
                INFO_CACHE.clear()
                self._json({"ok": True, "version": ytdlp_version()})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)

        elif route == "/api/jobs/clear":
            with JOBS_LOCK:
                for jid in [j.id for j in JOBS.values() if j.status in ("done", "error", "cancelled")]:
                    JOBS.pop(jid, None)
            self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)

    # ---- state ------------------------------------------------------------
    def api_state(self) -> dict:
        return {
            "app": APP_NAME,
            "version": VERSION,
            "engineReady": tool_ready(),
            "engineVersion": ytdlp_version() if YTDLP.exists() else "",
            "folder": CONFIG.get("folder") or DEFAULT_FOLDER,
            "defaultFolder": DEFAULT_FOLDER,
            "prefs": PREFS,
            "jsRuntime": JS_RUNTIME[0] if JS_RUNTIME else "",
            "cookiesFile": cookies_file_state(),
            "busy": CURRENT["job"].id if CURRENT["job"] else None,
        }


_YTVERSION: dict[str, str] = {}


def ytdlp_version() -> str:
    if not YTDLP.exists():
        return ""
    if _YTVERSION.get("v"):
        return _YTVERSION["v"]
    try:
        out = subprocess.run(
            [str(YTDLP), "--version"],
            capture_output=True, text=True, timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
        _YTVERSION["v"] = (out.stdout or "").strip().splitlines()[0]
    except Exception:
        _YTVERSION["v"] = ""
    return _YTVERSION["v"]


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def find_port(start: int = 8765) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main() -> None:
    print("=" * 54)
    print(f"  {APP_NAME} {VERSION}  -  keep the best quality")
    print("=" * 54)

    if not tool_ready():
        try:
            ensure_dependencies()
        except Exception as exc:
            print()
            print("Setup failed:", exc)
            print("Check your internet connection and run start.bat again.")
            input("Press Enter to close...")
            return

    Path(CONFIG.get("folder") or DEFAULT_FOLDER).mkdir(parents=True, exist_ok=True)

    # Warm the version cache before the UI asks for it, so /api/state is instant.
    ytdlp_version()

    threading.Thread(target=worker_loop, daemon=True).start()

    port = find_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"

    print(f"  Open: {url}")
    print("  Press Ctrl+C to stop.")
    print("=" * 54)

    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print("Fatal:", exc)
        input("Press Enter to close...")
