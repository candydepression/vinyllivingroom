"""Thin helpers around ffmpeg / ffprobe."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np


class VLRError(Exception):
    """A problem the user can act on. The message is shown as-is."""


def need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise VLRError(f"{tool} is not installed. Run: brew install ffmpeg")


def probe(path: Path) -> dict:
    need_ffmpeg()
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if p.returncode:
        raise VLRError(f"Can't read {path.name}: {p.stderr.strip()}")
    return json.loads(p.stdout)


def first_stream(info: dict, kind: str) -> dict | None:
    return next((s for s in info["streams"] if s.get("codec_type") == kind), None)


def duration(info: dict) -> float:
    return float(info["format"]["duration"])


def has_filter(name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return any(len(l.split()) > 1 and l.split()[1] == name for l in out.splitlines())


def load_mono(path: Path, sr: int) -> np.ndarray:
    """Decode the first audio stream to mono float32 at `sr`, high-passed to drop rumble/hum."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
         "-af", "highpass=f=120", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if p.returncode:
        raise VLRError(f"Can't decode audio from {path.name}: {p.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)


def measure_loudness(path: Path, start: float, end: float) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of path[start:end]."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(path),
         "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    summary = p.stderr[p.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    tp = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if not i or not tp:
        raise VLRError("Couldn't measure the audio loudness.")
    to_f = lambda s: float("-inf") if s == "-inf" else float(s)
    return to_f(i.group(1)), to_f(tp.group(1))


def run_ffmpeg(args: list[str], total_seconds: float, on_progress: Callable[[float], None] | None = None) -> None:
    """Run ffmpeg, reporting progress 0..1. Raises VLRError with ffmpeg's error text on failure."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-v", "error", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and on_progress and total_seconds > 0:
            try:
                on_progress(min(1.0, int(line.split("=")[1]) / 1e6 / total_seconds))
            except ValueError:
                pass
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait():
        raise VLRError("ffmpeg failed:\n" + err.strip()[-2000:])
    if on_progress:
        on_progress(1.0)
