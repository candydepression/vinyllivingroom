"""Line up the phone video with the TP-7 audio.

The TP-7 recording is the master clock: its audio is never stretched or resampled.
We measure how the phone's own (room-mic) audio lines up against it at many points
along the recording and fit a straight line:

    phone_time = tp7_time * (1 + drift) + offset

`offset` is how much later the phone started; `drift` is the tiny clock-speed
difference between the two devices (typically tens of parts per million, which
adds up to tens of milliseconds over an album). The video's timestamps are then
remapped onto the TP-7 timeline, which is invisible (a frame is repeated or
dropped every few minutes at most) and leaves the audio untouched.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .media import VLRError, load_mono

COARSE_SR = 1000   # Hz, for finding the rough offset over the whole recording
FINE_SR = 4000     # Hz, for precise measurements (0.25 ms resolution)
WINDOW = 8.0       # seconds per precise measurement (short, so clock drift inside one window is negligible)
SEARCH = 1.5       # seconds either side of the rough offset to search


@dataclass
class SyncResult:
    offset: float            # seconds; phone_time = tp7_time * (1 + drift) + offset
    drift: float             # dimensionless (e.g. 4e-5 = 40 ppm)
    residual_ms: float       # RMS error of the fit, milliseconds
    windows_used: int
    windows_total: int
    points: list = field(default_factory=list)  # (tp7_time, measured_offset) pairs used

    def phone_to_tp7(self, p: float) -> float:
        return (p - self.offset) / (1 + self.drift)

    def tp7_to_phone(self, t: float) -> float:
        return t * (1 + self.drift) + self.offset

    def to_dict(self) -> dict:
        return asdict(self)


def _next_pow2(n: int) -> int:
    return 1 << (int(n) - 1).bit_length()


def _xcorr(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
    """c[k] = sum_i a[i+k] * b[i] (circular, length n), with partial phase-transform
    weighting so the peak stays sharp even though the phone hears the room."""
    A = np.fft.rfft(a, n)
    B = np.fft.rfft(b, n)
    R = A * np.conj(B)
    R /= np.abs(R) ** 0.8 + 1e-12
    return np.fft.irfft(R, n)


def _peak(c: np.ndarray, guard: int) -> tuple[float, float]:
    """Sub-sample peak position and how much it stands out from the next-best peak."""
    mag = np.abs(c)
    k = int(np.argmax(mag))
    if 0 < k < len(c) - 1:  # parabolic interpolation
        y0, y1, y2 = mag[k - 1], mag[k], mag[k + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
    else:
        frac = 0.0
    masked = mag.copy()
    masked[max(0, k - guard): k + guard + 1] = 0
    second = masked.max() if masked.size else 0.0
    return k + frac, (mag[k] / second) if second > 0 else float("inf")


def coarse_offset(phone: np.ndarray, tp7: np.ndarray, sr: int) -> tuple[float, float]:
    n = _next_pow2(len(phone) + len(tp7))
    c = _xcorr(phone, tp7, n)
    k, clarity = _peak(c, guard=int(0.25 * sr))
    lag = k if k < len(phone) else k - n
    return lag / sr, clarity


def measure(phone: np.ndarray, tp7: np.ndarray, sr: int, rough: float,
            drift: float = 0.0) -> list[tuple[float, float, float]]:
    """Precise offset measurements (tp7_time, offset, clarity) spread over the overlap.
    `drift` (from a first pass) pre-stretches each TP-7 window so it matches the phone's clock."""
    tp7_dur, phone_dur = len(tp7) / sr, len(phone) / sr
    lo = max(0.0, -rough) + WINDOW / 2 + SEARCH
    hi = min(tp7_dur, phone_dur - rough) - WINDOW / 2 - SEARCH
    if hi <= lo:
        raise VLRError("The video and audio barely overlap. Are these files from the same session?")
    count = int(np.clip((hi - lo) / 20, 5, 150))  # about one every 20 s
    out = []
    w, s = int(WINDOW * sr), int(SEARCH * sr)
    for centre in np.linspace(lo, hi, count):
        b0 = int((centre - WINDOW / 2) * sr)
        b = tp7[b0: b0 + w]
        if np.sqrt(np.mean(b ** 2)) < 1e-4:  # silence (e.g. the flip): nothing to match
            continue
        if drift:
            b = np.interp(b0 + np.arange(int(w * (1 + drift))) / (1 + drift), np.arange(len(tp7)), tp7)
        a0 = int(round(b0 + (rough + drift * b0 / sr) * sr)) - s
        a = phone[a0: a0 + w + 2 * s]
        if a0 < 0 or len(a) < w + 2 * s:
            continue
        c = _xcorr(a, b, _next_pow2(len(a) + len(b)))[: 2 * s + 1]
        k, clarity = _peak(c, guard=int(0.05 * sr))
        out.append((b0 / sr, (a0 + k - b0) / sr, clarity))  # phone sample a0+k == TP-7 sample b0
    return out


def fit(points: list[tuple[float, float, float]], total: int) -> SyncResult:
    good = [(t, d) for t, d, c in points if c > 1.3]
    if len(good) < 3:
        raise VLRError(
            "Couldn't line up the phone audio with the TP-7 audio "
            f"(only {len(good)} clear matches). Check that both files are from the same session "
            "and that the phone recorded sound.")
    t = np.array([g[0] for g in good])
    d = np.array([g[1] for g in good])
    keep = np.ones(len(t), bool)
    for _ in range(5):  # drop outliers, refit
        slope, icpt = np.polyfit(t[keep], d[keep], 1)
        resid = d - (icpt + slope * t)
        limit = max(0.004, 3 * np.median(np.abs(resid[keep])) * 1.4826)
        new_keep = np.abs(resid) <= limit
        if new_keep.sum() < 3 or (new_keep == keep).all():
            break
        keep = new_keep
    slope, icpt = np.polyfit(t[keep], d[keep], 1)
    resid = d[keep] - (icpt + slope * t[keep])
    return SyncResult(
        offset=float(icpt), drift=float(slope),
        residual_ms=float(np.sqrt(np.mean(resid ** 2)) * 1000),
        windows_used=int(keep.sum()), windows_total=total,
        points=[(float(a), float(b)) for a, b in zip(t[keep], d[keep])],
    )


def sync(video: Path, audio: Path, log=print) -> SyncResult:
    log("Listening to both recordings to line them up…")
    phone_c, tp7_c = load_mono(video, COARSE_SR), load_mono(audio, COARSE_SR)
    rough, clarity = coarse_offset(phone_c, tp7_c, COARSE_SR)
    if clarity < 1.2:
        log(f"  (rough match is weak: clarity {clarity:.2f})")
    phone_f, tp7_f = load_mono(video, FINE_SR), load_mono(audio, FINE_SR)
    res = fit(measure(phone_f, tp7_f, FINE_SR, rough), total=0)
    for _ in range(2):  # refine with drift-corrected windows
        pts = measure(phone_f, tp7_f, FINE_SR, res.offset, res.drift)
        res = fit(pts, total=len(pts))
    log(f"  Phone started {res.offset:+.3f}s relative to the TP-7; clocks differ by "
        f"{res.drift * 1e6:+.1f} ppm; fit error {res.residual_ms:.1f} ms "
        f"({res.windows_used}/{res.windows_total} checkpoints).")
    if res.residual_ms > 15:
        raise VLRError(
            f"Sync is unreliable (error {res.residual_ms:.0f} ms). Was the recording paused or cut partway?")
    return res
