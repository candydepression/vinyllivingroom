"""End-to-end checks on fake sessions. Run:  .venv/bin/python -m pytest -q"""
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.fixture import make_session
from vlr.episode import Episode
from vlr.render import plan, render_segment
from vlr.sync import sync


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    folder = tmp_path_factory.mktemp("session")
    # 1000 ppm is ~20x a real phone's drift; it makes any drift bug obvious in a short test.
    truth = make_session(folder, seconds=120, offset=-4.25, drift_ppm=1000, markers=True)
    ep = Episode(folder=folder, artist="Test Artist", album="Test Album",
                 video="IMG_0001.MOV", audio="TP7_0001.wav")
    ep.save()
    return ep, truth


def test_sync_finds_offset_and_drift(session):
    ep, truth = session
    r = sync(ep.video_path, ep.audio_path, log=lambda *_: None)
    assert abs(r.offset - truth["offset"]) < 0.002
    assert abs(r.drift - truth["drift"]) < 20e-6


def _flash_times(video: Path, fps: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", "scale=32:18,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    luma = np.frombuffer(raw, np.uint8).reshape(-1, 32 * 18).mean(axis=1)
    bright = luma > 150
    starts = np.flatnonzero(bright & ~np.r_[False, bright[:-1]])
    return starts / fps


def _click_times(video: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", "48000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, np.float32))
    hits = np.flatnonzero(x > 0.6 * x.max())
    groups = hits[np.r_[True, np.diff(hits) > 4800]]
    return groups / 48000


def test_finished_video_is_in_sync(session):
    ep, truth = session
    p = plan(ep, log=lambda *_: None)
    out = render_segment(ep, p, ep.out_dir / "full.mp4", p.start, p.length, True, True, "preview")
    flashes, clicks = _flash_times(out, 30), _click_times(out)
    assert len(clicks) >= 8
    errors = []
    for c in clicks:
        near = flashes[np.abs(flashes - c) < 0.5]
        if len(near):
            errors.append(near[0] - c)
    assert len(errors) >= len(clicks) - 2          # first/last can be hidden by the fades
    errors = np.array(errors)
    # The fake flashes start on the next frame boundary after the true instant (0-33 ms late) and the
    # output is re-timed to a steady 30 fps (±17 ms), so up to ~2 frames late is expected; early is not.
    assert np.all((errors > -0.02) & (errors < 2 / 30 + 0.005)), errors
    # And no creeping drift from start to end.
    assert abs(np.polyfit(np.arange(len(errors)), errors, 1)[0]) < 0.002


def test_middle_segment_with_seek_is_in_sync(session):
    ep, truth = session
    p = plan(ep, log=lambda *_: None)
    s0 = p.start + 40
    out = render_segment(ep, p, ep.out_dir / "mid.mp4", s0, 40, False, False, "preview")
    flashes, clicks = _flash_times(out, 30), _click_times(out)
    assert len(clicks) >= 2
    for c in clicks:
        e = flashes[np.argmin(np.abs(flashes - c))] - c
        assert -0.02 < e < 2 / 30 + 0.005


def test_opening_starts_black_with_title(session):
    ep, _ = session
    p = plan(ep, log=lambda *_: None)
    out = render_segment(ep, p, ep.out_dir / "open.mp4", p.start, 14, True, False, "preview")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920 * 1080)
    assert frames[0].max() < 30                     # starts on black
    assert frames[int(3 * 30)].max() > 120          # title text visible at 3 s
    assert frames[int(3 * 30)].mean() < 20          # …on black
    assert frames[int(13 * 30)].mean() > 30         # picture up by 13 s
