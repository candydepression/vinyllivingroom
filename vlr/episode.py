"""An episode = one folder holding the phone video, the TP-7 audio and episode.toml."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .media import VLRError

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac"}

# Defaults for every episode. Any of these can be overridden in episode.toml under [settings].
DEFAULT_SETTINGS = {
    "title_seconds": 6.0,      # title card on black before the picture
    "fade_in_seconds": 5.0,    # picture fades up from black
    "fade_out_seconds": 5.0,   # picture fades down to black at the end
    "audio_fade_in": 0.5,      # tiny fade so the sound doesn't start with a click
    "audio_fade_out": 3.0,     # sound fades out under the last seconds of the picture fade
    "look": 1.0,               # nostalgic colour look: 0 = off, 1 = default, up to 2 = stronger
    "loudness_target": -14.0,  # LUFS; plain volume change only, never compression
    "true_peak_ceiling": -1.0, # dBTP; volume is never raised past this
    "resolution": 1080,        # output height: 1080 or 2160
    "fps": 30,
    "font": "",                # empty = bundled Jost Light
}


@dataclass
class Episode:
    folder: Path
    artist: str = ""
    album: str = ""
    video: str = ""            # file name inside folder
    audio: str = ""
    start: str = ""            # optional cut points, in the phone video's own time ("1:02")
    end: str = ""
    yt_title: str = ""
    yt_description: str = ""
    yt_tags: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    # ---- paths -------------------------------------------------------------
    @property
    def toml_path(self) -> Path:
        return self.folder / "episode.toml"

    @property
    def out_dir(self) -> Path:
        d = self.folder / "out"
        d.mkdir(exist_ok=True)
        return d

    @property
    def video_path(self) -> Path:
        return self.folder / self.video

    @property
    def audio_path(self) -> Path:
        return self.folder / self.audio

    @property
    def final_path(self) -> Path:
        return self.out_dir / f"{safe_name(self.artist)} - {safe_name(self.album)}.mp4"

    def setting(self, key: str):
        return self.settings.get(key, DEFAULT_SETTINGS[key])

    # ---- validation --------------------------------------------------------
    def check(self) -> None:
        missing = [n for n, v in (("artist", self.artist), ("album", self.album)) if not v.strip()]
        if missing:
            raise VLRError("Please fill in: " + ", ".join(missing))
        for label, name in (("video", self.video), ("audio", self.audio)):
            if not name:
                raise VLRError(f"No {label} file yet.")
            if not (self.folder / name).exists():
                raise VLRError(f"The {label} file {name} isn't in {self.folder}.")
        unknown = set(self.settings) - set(DEFAULT_SETTINGS)
        if unknown:
            raise VLRError("Unknown setting(s) in episode.toml: " + ", ".join(sorted(unknown)))

    # ---- load / save -------------------------------------------------------
    @classmethod
    def load(cls, where: Path) -> "Episode":
        where = Path(where).expanduser().resolve()
        folder = where.parent if where.suffix == ".toml" else where
        path = folder / "episode.toml"
        if not path.exists():
            raise VLRError(f"No episode.toml in {folder}")
        try:
            d = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise VLRError(f"episode.toml has a typo: {e}") from None
        yt = d.get("youtube", {})
        return cls(
            folder=folder,
            artist=d.get("artist", ""), album=d.get("album", ""),
            video=d.get("video", ""), audio=d.get("audio", ""),
            start=str(d.get("start", "")), end=str(d.get("end", "")),
            yt_title=yt.get("title", ""), yt_description=yt.get("description", ""),
            yt_tags=list(yt.get("tags", [])),
            settings=dict(d.get("settings", {})),
        )

    def save(self) -> None:
        q = json.dumps  # JSON string escaping is valid TOML basic-string escaping
        lines = [
            "# One episode = this folder. Edit freely; blank lines and comments are fine.",
            f"artist = {q(self.artist)}",
            f"album  = {q(self.album)}",
            f"video  = {q(self.video)}",
            f"audio  = {q(self.audio)}",
            "",
            "# Optional cut points, in the phone video's own time as QuickTime shows it (e.g. \"0:12\", \"44:30\").",
            "# Leave empty to keep everything both devices recorded.",
            f"start = {q(self.start)}",
            f"end   = {q(self.end)}",
            "",
            "[youtube]",
            f"title = {q(self.yt_title)}",
            f"description = {q(self.yt_description)}",
            f"tags = [{', '.join(q(t) for t in self.yt_tags)}]",
            "",
            "# Override any default here, e.g. look = 0.6 or fade_in_seconds = 4",
            "[settings]",
        ]
        lines += [f"{k} = {q(v)}" for k, v in self.settings.items()]
        self.toml_path.write_text("\n".join(lines) + "\n")


def safe_name(s: str) -> str:
    return "".join(c for c in s if c not in '/\\:*?"<>|').strip() or "untitled"


def parse_time(s: str) -> float | None:
    """'1:02:03' / '44:30' / '12' / '12.5' -> seconds. Empty -> None."""
    s = str(s).strip()
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        raise VLRError(f"Can't read the time '{s}'. Use minutes:seconds, like 44:30.") from None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def find_media(folder: Path) -> tuple[str, str]:
    """Guess the video and audio file in a folder (used by `vinyl new`)."""
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    vids = [p.name for p in files if p.suffix.lower() in VIDEO_EXT]
    auds = [p.name for p in files if p.suffix.lower() in AUDIO_EXT]
    for label, found in (("video", vids), ("audio", auds)):
        if len(found) > 1:
            raise VLRError(f"More than one {label} file in {folder}: {', '.join(found)}. Keep just one.")
    return (vids[0] if vids else ""), (auds[0] if auds else "")
