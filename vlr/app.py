"""The drop-in page: a small web app that runs only on this Mac (http://localhost:8765).

Each episode is a folder in ~/Movies/Vinyl Living Room/ (change with the VLR_HOME
environment variable). Dropped files are copied into that folder.
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .episode import AUDIO_EXT, DEFAULT_SETTINGS, VIDEO_EXT, Episode
from .media import VLRError

HOME = Path(os.environ.get("VLR_HOME", Path.home() / "Movies" / "Vinyl Living Room")).expanduser()
STATIC = Path(__file__).resolve().parent / "static"
FONTS = Path(__file__).resolve().parent.parent / "fonts"


# --------------------------------------------------------------------------- jobs
class Job:
    """One long task at a time (preview / make / upload), watched by the page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "episode": None, "action": None, "progress": 0.0,
                      "log": [], "error": None, "done": False}

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def log(self, msg: str) -> None:
        with self.lock:
            self.state["log"].append(str(msg))

    def progress(self, x: float) -> None:
        with self.lock:
            self.state["progress"] = float(x)

    def start(self, ep: Episode, action: str) -> None:
        with self.lock:
            if self.state["running"]:
                raise VLRError("Something is already running. Wait for it to finish.")
            self.state = {"running": True, "episode": ep.folder.name, "action": action, "progress": 0.0,
                          "log": [], "error": None, "done": False}
        threading.Thread(target=self._run, args=(ep, action), daemon=True).start()

    def _run(self, ep: Episode, action: str) -> None:
        try:
            if action == "preview":
                from .render import preview
                preview(ep, log=self.log, on_progress=self.progress)
            elif action == "make":
                from .render import make
                make(ep, log=self.log, on_progress=self.progress)
            elif action == "upload":
                from .upload import upload
                upload(ep, log=self.log, on_progress=self.progress)
            with self.lock:
                self.state["done"] = True
        except VLRError as e:
            with self.lock:
                self.state["error"] = str(e)
        except Exception as e:  # unexpected: keep the details for troubleshooting
            traceback.print_exc()
            with self.lock:
                self.state["error"] = f"Unexpected error: {e}"
        finally:
            with self.lock:
                self.state["running"] = False


JOB = Job()


# --------------------------------------------------------------------------- episodes
def episode_dir(eid: str) -> Path:
    if not eid or "/" in eid or "\\" in eid or eid.startswith("."):
        raise VLRError("Bad episode id.")
    d = HOME / eid
    if not (d / "episode.toml").exists():
        raise VLRError("That episode doesn't exist.")
    return d


def describe(ep: Episode) -> dict:
    from .upload import uploaded_record
    o = ep.folder / "out"
    files = sorted(p.name for p in o.iterdir()) if o.exists() else []
    vid, aud = ep.folder / ep.video if ep.video else None, ep.folder / ep.audio if ep.audio else None
    return {
        "id": ep.folder.name, "folder": str(ep.folder),
        "artist": ep.artist, "album": ep.album, "start": ep.start, "end": ep.end,
        "yt_title": ep.yt_title, "yt_description": ep.yt_description, "yt_tags": ep.yt_tags,
        "look": ep.settings.get("look", DEFAULT_SETTINGS["look"]),
        "video": ep.video if vid and vid.exists() else "", "audio": ep.audio if aud and aud.exists() else "",
        "video_size": vid.stat().st_size if vid and vid.exists() else 0,
        "audio_size": aud.stat().st_size if aud and aud.exists() else 0,
        "previews": [f for f in files if f.startswith("preview-")],
        "final": ep.final_path.name if ep.final_path.exists() else "",
        "uploaded": uploaded_record(ep),
    }


def list_episodes() -> list[dict]:
    HOME.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(HOME.iterdir(), reverse=True):
        if (d / "episode.toml").exists():
            try:
                e = Episode.load(d)
                out.append({"id": d.name, "artist": e.artist, "album": e.album,
                            "final": e.final_path.exists(), "uploaded": (d / "out" / "uploaded.json").exists()})
            except VLRError:
                pass
    return out


def create_episode(data: dict) -> Episode:
    HOME.mkdir(parents=True, exist_ok=True)
    base = dt.datetime.now().strftime("%Y-%m-%d %H%M")  # the page shows artist/album; this just stays unique
    folder, n = HOME / base, 2
    while folder.exists():
        folder, n = HOME / f"{base} ({n})", n + 1
    folder.mkdir(parents=True)
    ep = Episode(folder=folder)
    apply_fields(ep, data)
    ep.save()
    return ep


def apply_fields(ep: Episode, d: dict) -> None:
    for k in ("artist", "album", "start", "end", "yt_title", "yt_description"):
        if k in d:
            setattr(ep, k, str(d[k]).strip() if k != "yt_description" else str(d[k]))
    if "yt_tags" in d:
        tags = d["yt_tags"] if isinstance(d["yt_tags"], list) else str(d["yt_tags"]).split(",")
        ep.yt_tags = [t.strip() for t in tags if t.strip()]
    if "look" in d:
        ep.settings["look"] = max(0.0, min(2.0, float(d["look"])))


# --------------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "VinylLivingRoom"

    def log_message(self, fmt, *args):  # quiet console
        pass

    # -- helpers
    def _ok_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1")

    def _json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _err(self, msg, status=400):
        self._json({"error": msg}, status)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, path: Path, ctype: str | None = None):
        size = path.stat().st_size
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # -- routes
    def do_GET(self):
        if not self._ok_host():
            return self._err("Forbidden", 403)
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._file(STATIC / "index.html", "text/html; charset=utf-8")
            if parts == ["Jost.ttf"]:
                return self._file(FONTS / "Jost.ttf", "font/ttf")
            if parts == ["api", "episodes"]:
                return self._json({"episodes": list_episodes(), "home": str(HOME)})
            if parts == ["api", "job"]:
                return self._json(JOB.snapshot())
            if len(parts) == 3 and parts[:2] == ["api", "episode"]:
                return self._json(describe(Episode.load(episode_dir(parts[2]))))
            if len(parts) == 3 and parts[0] == "out":
                f = episode_dir(parts[1]) / "out" / Path(parts[2]).name
                if f.exists():
                    return self._file(f)
            return self._err("Not found", 404)
        except VLRError as e:
            return self._err(str(e))

    def do_POST(self):
        if not self._ok_host() or self.headers.get("X-VLR") != "1":
            return self._err("Forbidden", 403)
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            if parts == ["api", "episodes"]:
                return self._json(describe(create_episode(self._body())))
            if len(parts) == 3 and parts[:2] == ["api", "episode"]:
                ep = Episode.load(episode_dir(parts[2]))
                apply_fields(ep, self._body())
                ep.save()
                return self._json(describe(ep))
            if len(parts) == 4 and parts[:2] == ["api", "episode"] and parts[3] in ("preview", "make", "upload"):
                ep = Episode.load(episode_dir(parts[2]))
                if parts[3] == "upload" and self._body().get("confirm") != "upload":
                    return self._err("Upload needs confirmation.")
                JOB.start(ep, parts[3])
                return self._json({"started": parts[3]})
            if len(parts) == 4 and parts[:2] == ["api", "episode"] and parts[3] == "reveal":
                d = episode_dir(parts[2])
                ep = Episode.load(d)
                target = ep.final_path if ep.final_path.exists() else d
                if sys.platform == "darwin":
                    subprocess.run(["open", "-R", str(target)])
                return self._json({"revealed": str(target)})
            return self._err("Not found", 404)
        except VLRError as e:
            return self._err(str(e))
        except (ValueError, json.JSONDecodeError):
            return self._err("Bad request")

    def do_PUT(self):
        """Receive a dropped file: PUT /api/episode/<id>/file?kind=video|audio&name=IMG_1234.MOV"""
        if not self._ok_host() or self.headers.get("X-VLR") != "1":
            return self._err("Forbidden", 403)
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        q = parse_qs(u.query)
        try:
            if not (len(parts) == 4 and parts[:2] == ["api", "episode"] and parts[3] == "file"):
                return self._err("Not found", 404)
            d = episode_dir(parts[2])
            kind = (q.get("kind") or [""])[0]
            name = Path((q.get("name") or [""])[0]).name
            ext = Path(name).suffix.lower()
            if kind == "video" and ext not in VIDEO_EXT:
                return self._err(f"That doesn't look like a phone video ({', '.join(sorted(VIDEO_EXT))}).")
            if kind == "audio" and ext not in AUDIO_EXT:
                return self._err(f"That doesn't look like a TP-7 recording ({', '.join(sorted(AUDIO_EXT))}).")
            if kind not in ("video", "audio") or not name:
                return self._err("Bad upload.")
            size = int(self.headers.get("Content-Length") or 0)
            free = shutil.disk_usage(d).free
            if size > free - 2 * 1024 ** 3:
                return self._err("Not enough free disk space for this file (plus room to render).")
            ep = Episode.load(d)
            old = getattr(ep, kind)
            tmp = d / f".{name}.part"
            left = size
            with tmp.open("wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(4 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                tmp.unlink(missing_ok=True)
                return self._err("The file didn't arrive completely. Try again.")
            if old and old != name and (d / old).exists():
                (d / old).unlink()  # replace the previous file of this kind
            tmp.replace(d / name)
            setattr(ep, kind, name)
            ep.save()
            (d / "out" / "sync.json").unlink(missing_ok=True)
            return self._json(describe(ep))
        except VLRError as e:
            return self._err(str(e))


def serve(port: int = 8765, open_browser: bool = True) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/"
    print(f"Vinyl Living Room is running at {url}\nEpisodes are saved in {HOME}\nClose this window to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
