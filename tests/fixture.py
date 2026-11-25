"""Make a fake session: a 'TP-7' WAV and a 'phone' video whose own audio is a
delayed, slightly-wrong-speed, roomy, noisy copy — like the real thing."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 48000


def music(seconds: float, seed: int = 1, flip_at: float | None = None, flip_len: float = 15.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n)
    t = 0.0
    while t < seconds:
        dur = rng.uniform(0.15, 0.6)
        f0 = 110 * 2 ** (rng.integers(0, 36) / 12)
        i0, i1 = int(t * SR), min(n, int((t + dur) * SR))
        tt = np.arange(i1 - i0) / SR
        env = np.exp(-tt * rng.uniform(2, 8))
        note = sum(np.sin(2 * np.pi * f0 * h * tt) / h for h in (1, 2, 3))
        x[i0:i1] += 0.25 * env * note
        if rng.random() < 0.3:  # a drum hit
            k = min(i1 - i0, int(0.05 * SR))
            x[i0:i0 + k] += 0.3 * rng.standard_normal(k) * np.exp(-np.arange(k) / (0.01 * SR))
        t += dur * rng.uniform(0.5, 1.0)
    x += 0.002 * rng.standard_normal(n)  # surface noise
    if flip_at is not None:  # the flip: near-silence
        a, b = int(flip_at * SR), int((flip_at + flip_len) * SR)
        x[a:b] *= 0.0005
    return (x / np.abs(x).max() * 0.5).astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> None:
    stereo = np.repeat(x[:, None], 2, axis=1).astype(np.float32)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "pcm_s24le", str(path)], input=stereo.tobytes(), check=True)


def make_session(folder: Path, seconds: float = 150, offset: float = 7.3, drift_ppm: float = 80,
                 video_extra: float = 4.0, markers: bool = False) -> dict:
    """offset: phone starts `offset` s *after* the TP-7 timeline origin is mapped, i.e.
    phone_time = tp7_time * (1 + drift) + offset."""
    folder.mkdir(parents=True, exist_ok=True)
    tp7 = music(seconds, flip_at=seconds * 0.5)
    drift = drift_ppm * 1e-6
    clicks = []
    if markers:  # a loud click in the audio + a white flash in the video at the same instant
        clicks = [t for t in np.arange(15.0, seconds - 10, 9.0)
                  if not (seconds * 0.5 - 1 < t < seconds * 0.5 + 16)]
        for t in clicks:
            i = int(t * SR)
            tp7[i - int(0.3 * SR): i + int(0.3 * SR)] *= 0.02   # quiet around it so the onset is clear
            tp7[i: i + 48] = 0.9
    write_wav(folder / "TP7_0001.wav", tp7)
    vid_len = max(30.0, seconds - abs(offset) + video_extra)
    p = np.arange(int(vid_len * SR)) / SR
    tp7_t = (p - offset) / (1 + drift)          # which TP-7 instant each phone sample hears
    src = np.interp(tp7_t * SR, np.arange(len(tp7)), tp7, left=0, right=0)
    # room: low-pass-ish smear + echo + hiss + hum
    room = np.convolve(src, np.ones(8) / 8, mode="same")
    d = int(0.023 * SR)
    room[d:] += 0.35 * room[:-d]
    rng = np.random.default_rng(9)
    room += 0.02 * rng.standard_normal(len(room)) + 0.02 * np.sin(2 * np.pi * 60 * p)
    room = (room / np.abs(room).max() * 0.4).astype(np.float32)
    write_wav(folder / "phone_audio.wav", room)
    if markers:
        flashes = "+".join(f"between(t,{t * (1 + drift) + offset:.4f},{t * (1 + drift) + offset + 0.1:.4f})"
                           for t in clicks)
        src = (f"color=c=0x303030:size=1280x720:rate=30:duration={vid_len:.3f},"
               f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{flashes}'")
    else:
        src = f"testsrc2=size=1280x720:rate=30:duration={vid_len:.3f}"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", src,
        "-i", str(folder / "phone_audio.wav"),
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(folder / "IMG_0001.MOV"),
    ], check=True)
    (folder / "phone_audio.wav").unlink()
    return {"offset": offset, "drift": drift, "clicks": clicks}


if __name__ == "__main__":
    import sys
    print(make_session(Path(sys.argv[1]), seconds=float(sys.argv[2]) if len(sys.argv) > 2 else 150))
