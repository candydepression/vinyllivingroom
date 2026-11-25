"""Command line:  ./vinyl app | new FOLDER | preview FOLDER | make FOLDER [--fast] | upload FOLDER"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .episode import Episode, find_media
from .media import VLRError


_last = [-1]


def _bar(x: float) -> None:
    if int(x * 100) == _last[0] and x < 1:
        return
    _last[0] = int(x * 100)
    n = int(x * 30)
    print(f"\r  [{'#' * n}{'.' * (30 - n)}] {x * 100:5.1f}%", end="" if x < 1 else "\n", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vinyl", description="Vinyl Living Room video maker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("app", help="open the drop-in page in your browser (easiest)")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--no-browser", action="store_true")
    n = sub.add_parser("new", help="create episode.toml in a folder holding the video + audio")
    n.add_argument("folder")
    for name, text in (("preview", "render short preview clips + colour stills"),
                       ("make", "render the full video"),
                       ("upload", "upload the finished video to YouTube as PRIVATE")):
        p = sub.add_parser(name, help=text)
        p.add_argument("folder")
        if name == "make":
            p.add_argument("--fast", action="store_true", help="use the Mac's hardware encoder (quicker, bigger file)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "app":
            from .app import serve
            serve(args.port, open_browser=not args.no_browser)
            return 0
        if args.cmd == "new":
            folder = Path(args.folder).expanduser().resolve()
            if (folder / "episode.toml").exists():
                raise VLRError(f"{folder}/episode.toml already exists.")
            v, au = find_media(folder)
            Episode(folder=folder, video=v, audio=au).save()
            print(f"Created {folder / 'episode.toml'} — open it and fill in artist, album and the YouTube text.")
            return 0
        ep = Episode.load(Path(args.folder))
        if args.cmd == "preview":
            from .render import preview
            for f in preview(ep, on_progress=_bar):
                print("  ", f)
        elif args.cmd == "make":
            from .render import make
            make(ep, fast=args.fast, on_progress=_bar)
        elif args.cmd == "upload":
            from .upload import upload
            upload(ep, confirm=True)
        return 0
    except VLRError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
