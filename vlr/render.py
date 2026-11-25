"""Turn an episode into a finished video (or a quick preview)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .episode import Episode, parse_time
from .media import VLRError, duration, first_stream, has_filter, measure_loudness, probe, run_ffmpeg
from .sync import SyncResult, sync
from .title import render_title


@dataclass
class Plan:
    sync: SyncResult
    start: float      # TP-7 time where the finished video begins
    end: float        # TP-7 time where it ends
    gain_db: float
    loudness: float   # measured LUFS before gain
    true_peak: float  # measured dBTP before gain
    hdr: bool
    width: int
    height: int

    @property
    def length(self) -> float:
        return self.end - self.start


def _fmt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(round(t)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _sync_cached(ep: Episode, log) -> SyncResult:
    """Sync is the slow analysis step; cache it per pair of files."""
    key = {n: [p.name, p.stat().st_size, int(p.stat().st_mtime)] for n, p in
           (("video", ep.video_path), ("audio", ep.audio_path))}
    cache = ep.out_dir / "sync.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            if c.get("key") == key:
                r = c["result"]
                return SyncResult(**{**r, "points": [tuple(p) for p in r["points"]]})
        except (ValueError, KeyError, TypeError):
            pass
    res = sync(ep.video_path, ep.audio_path, log)
    cache.write_text(json.dumps({"key": key, "result": res.to_dict()}, indent=1))
    return res


def plan(ep: Episode, log=print) -> Plan:
    ep.check()
    vinfo, ainfo = probe(ep.video_path), probe(ep.audio_path)
    vs = first_stream(vinfo, "video")
    if not vs:
        raise VLRError(f"{ep.video} has no picture.")
    if not first_stream(vinfo, "audio"):
        raise VLRError(f"{ep.video} has no sound, so it can't be lined up with the TP-7 audio.")
    if not first_stream(ainfo, "audio"):
        raise VLRError(f"{ep.audio} has no sound.")

    s = _sync_cached(ep, log)
    vdur, adur = duration(vinfo), duration(ainfo)
    # Where both recordings exist, on the TP-7 clock.
    lo = max(0.0, s.phone_to_tp7(0.0))
    hi = min(adur, s.phone_to_tp7(vdur))
    start = parse_time(ep.start)
    end = parse_time(ep.end)
    start = lo if start is None else max(lo, s.phone_to_tp7(start))
    end = hi if end is None else min(hi, s.phone_to_tp7(end))
    lead = ep.setting("title_seconds") + ep.setting("fade_in_seconds") + ep.setting("fade_out_seconds")
    if end - start < lead + 5:
        raise VLRError(f"Only {end - start:.0f}s to work with after lining up; check the files and start/end.")

    loud, peak = measure_loudness(ep.audio_path, start, end)
    target, ceiling = ep.setting("loudness_target"), ep.setting("true_peak_ceiling")
    gain = 0.0
    if math.isfinite(loud) and math.isfinite(peak):
        gain = min(target - loud, ceiling - peak)

    transfer = vs.get("color_transfer", "")
    hdr = transfer in ("arib-std-b67", "smpte2084")
    height = int(ep.setting("resolution"))
    width = height * 16 // 9
    p = Plan(s, start, end, gain, loud, peak, hdr, width, height)
    log(f"  Video will run {_fmt(p.length)} — from {_fmt(s.tp7_to_phone(start))} to "
        f"{_fmt(s.tp7_to_phone(end))} of the phone video.")
    log(f"  Audio loudness {loud:.1f} LUFS, peak {peak:.1f} dBTP → volume {gain:+.1f} dB "
        f"(plain volume change, no compression).")
    if hdr:
        log("  Phone video is HDR; converting it to normal video. "
            "(Tip: iPhone Settings › Camera › Record Video › HDR Video off avoids this.)")
    return p


def _look(strength: float) -> list[str]:
    """The nostalgic house look: warmer, a little less colour and contrast, soft
    lifted blacks, rolled-off highlights, fine luma grain and a gentle vignette."""
    k = max(0.0, min(2.0, float(strength)))
    if k == 0:
        return []
    return [
        f"eq=saturation={1 - 0.15 * k:.3f}:contrast={1 - 0.06 * k:.3f}",
        f"colorbalance=rs={0.05 * k:.3f}:bs={-0.05 * k:.3f}:rm={0.04 * k:.3f}:bm={-0.04 * k:.3f}"
        f":rh={0.02 * k:.3f}:bh={-0.04 * k:.3f}",
        f"curves=all='0/{0.05 * k:.3f} 0.5/0.5 1/{1 - 0.04 * k:.3f}'",
        f"noise=c0s={round(5 * k)}:c0f=t",
        f"vignette=angle={0.5 * k:.3f}",
    ]


def _video_chain(ep: Episode, p: Plan, seg_start: float, seg_len: float,
                 title: bool, fade_in: bool, fade_out: bool, seeked: float = 0.0) -> str:
    """seeked = where the input was opened (-ss), in phone time; frames then start at 0."""
    s = p.sync
    f = []
    if p.hdr:
        if not has_filter("zscale"):
            raise VLRError("This HDR video needs an ffmpeg with zscale. Run: brew reinstall ffmpeg, "
                           "or turn off HDR Video on the phone.")
        f += ["zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
              "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv"]
    # phone clock -> TP-7 clock, then cut. (Exact arithmetic, no STARTPTS guessing.)
    f += [
        f"setpts=(PTS-{s.offset - seeked:.6f}/TB)/{1 + s.drift:.9f}",
        f"trim=start={seg_start:.6f}:end={seg_start + seg_len:.6f}",
        f"setpts=PTS-{seg_start:.6f}/TB",
        f"fps={ep.setting('fps')}:start_time=0",
        f"scale={p.width}:{p.height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={p.width}:{p.height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ]
    f += _look(ep.setting("look"))
    t = ep.setting("title_seconds") if title else 0.0
    if fade_in:
        f.append(f"fade=t=in:st={t:.3f}:d={ep.setting('fade_in_seconds'):.3f}")
    if fade_out:
        d = ep.setting("fade_out_seconds")
        f.append(f"fade=t=out:st={seg_len - d:.3f}:d={d:.3f}")
    return ",".join(f)


def _audio_chain(ep: Episode, p: Plan, seg_start: float, seg_len: float, fade_in: bool, fade_out: bool,
                 seeked: float = 0.0) -> str:
    a = seg_start - seeked
    f = [f"atrim=start={a:.6f}:end={a + seg_len:.6f}", "asetpts=PTS-STARTPTS",
         f"volume={p.gain_db:.2f}dB"]
    if fade_in:
        f.append(f"afade=t=in:st=0:d={ep.setting('audio_fade_in'):.3f}")
    if fade_out:
        d = ep.setting("audio_fade_out")
        f.append(f"afade=t=out:st={seg_len - d:.3f}:d={d:.3f}")
    f += ["aresample=48000", "aformat=channel_layouts=stereo"]
    return ",".join(f)


def render_segment(ep: Episode, p: Plan, out: Path, seg_start: float, seg_len: float,
                   opening: bool, closing: bool, quality: str, on_progress=None) -> Path:
    """Render [seg_start, seg_start+seg_len] (TP-7 time). opening = title + fade in; closing = fade out."""
    # Open both files a few seconds before the segment so we don't decode from the top.
    vss = max(0.0, p.sync.tp7_to_phone(seg_start) - 3.0)
    ass = max(0.0, seg_start - 3.0)
    args = ["-ss", f"{vss:.6f}", "-i", str(ep.video_path), "-ss", f"{ass:.6f}", "-i", str(ep.audio_path)]
    graph = [f"[0:v:0]{_video_chain(ep, p, seg_start, seg_len, opening, opening, closing, vss)}[v]"]
    vout = "[v]"
    if opening:
        T = ep.setting("title_seconds")
        png = render_title(ep.album, ep.artist, p.width, p.height, ep.out_dir / "title.png", ep.setting("font"))
        args += ["-loop", "1", "-framerate", str(ep.setting("fps")), "-t", f"{T:.3f}", "-i", str(png)]
        fd = min(1.2, T / 4)
        graph.append(f"[2:v]format=rgba,fade=t=in:st=0:d={fd:.2f}:alpha=1,"
                     f"fade=t=out:st={T - fd - 0.4:.2f}:d={fd:.2f}:alpha=1[ttl]")
        graph.append("[v][ttl]overlay=eof_action=pass:format=auto[vt]")
        vout = "[vt]"
    graph.append(f"{vout}format=yuv420p[vf]")
    graph.append(f"[1:a:0]{_audio_chain(ep, p, seg_start, seg_len, opening, closing, ass)}[a]")
    args += ["-filter_complex", ";".join(graph), "-map", "[vf]", "-map", "[a]", "-t", f"{seg_len:.3f}"]
    if quality == "final":
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    elif quality == "fast":
        venc = ["-c:v", "h264_videotoolbox", "-b:v", "16M"]
    else:  # preview
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    args += venc + [
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "aac", "-b:a", "384k" if quality != "preview" else "192k",
        "-movflags", "+faststart", str(out),
    ]
    run_ffmpeg(args, seg_len, on_progress)
    return out


def look_stills(ep: Episode, p: Plan, out: Path) -> Path:
    """Before/after frames at three points, stacked, to judge the colour look."""
    rows = []
    args = []
    for i, frac in enumerate((0.25, 0.5, 0.75)):
        t = p.sync.tp7_to_phone(p.start + p.length * frac)
        args += ["-ss", f"{t:.2f}", "-i", str(ep.video_path)]
        tone = ""
        if p.hdr:
            tone = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,")
        base = f"{tone}scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1"
        look = ",".join(_look(ep.setting("look"))) or "null"
        rows.append(f"[{i}:v:0]trim=end_frame=1,{base},split[o{i}][g{i}];[g{i}]{look}[l{i}];"
                    f"[o{i}][l{i}]hstack[r{i}]")
    graph = ";".join(rows) + ";[r0][r1][r2]vstack=inputs=3[out]"
    run_ffmpeg(args + ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-update", "1", str(out)], 0)
    return out


def preview(ep: Episode, log=print, on_progress=None) -> list[Path]:
    """Quick files to check before the full render: the opening, a minute from the
    middle (to check lip-sync), the ending, and before/after colour stills."""
    p = plan(ep, log)
    o = ep.out_dir
    lead = ep.setting("title_seconds") + ep.setting("fade_in_seconds")
    open_len = min(p.length, lead + 20)
    mid = p.start + p.length / 2 - 30
    end_len = min(p.length, ep.setting("fade_out_seconds") + 15)
    jobs = [
        ("preview-1-opening.mp4", p.start, open_len, True, False),
        ("preview-2-middle.mp4", mid, 60.0, False, False),
        ("preview-3-ending.mp4", p.end - end_len, end_len, False, True),
    ]
    total = sum(j[2] for j in jobs)
    done = 0.0
    outs = []
    for name, s0, ln, op, cl in jobs:
        log(f"Rendering {name}…")
        prog = (lambda x, d=done, l=ln: on_progress((d + x * l) / total)) if on_progress else None
        outs.append(render_segment(ep, p, o / name, s0, ln, op, cl, "preview", prog))
        done += ln
    log("Making before/after colour stills…")
    outs.append(look_stills(ep, p, o / "preview-look.jpg"))
    return outs


def make(ep: Episode, fast: bool = False, log=print, on_progress=None) -> Path:
    p = plan(ep, log)
    out = ep.final_path
    tmp = out.with_name(out.stem + ".partial.mp4")
    log(f"Rendering the full video ({_fmt(p.length)}). This takes a while…")
    render_segment(ep, p, tmp, p.start, p.length, True, True, "fast" if fast else "final", on_progress)
    tmp.replace(out)
    (ep.out_dir / "report.txt").write_text(
        f"{ep.artist} — {ep.album}\n"
        f"Length: {_fmt(p.length)}\n"
        f"Sync: phone offset {p.sync.offset:+.3f}s, clock drift {p.sync.drift * 1e6:+.1f} ppm, "
        f"fit error {p.sync.residual_ms:.1f} ms ({p.sync.windows_used}/{p.sync.windows_total} checkpoints)\n"
        f"Audio: {p.loudness:.1f} LUFS / {p.true_peak:.1f} dBTP in, volume {p.gain_db:+.1f} dB\n"
        f"Phone video used: {_fmt(p.sync.tp7_to_phone(p.start))} to {_fmt(p.sync.tp7_to_phone(p.end))}\n"
    )
    log(f"Done: {out}")
    return out
