"""The title card ("M3"): album, then artist, small and centred on black."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .media import VLRError

BUNDLED_FONT = Path(__file__).resolve().parent.parent / "fonts" / "Jost.ttf"
CREAM = (238, 230, 214, 255)
CREAM_DIM = (238, 230, 214, 150)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    p = Path(path).expanduser() if path else BUNDLED_FONT
    if not p.exists():
        raise VLRError(f"Font not found: {p}")
    f = ImageFont.truetype(str(p), size)
    if not path:  # bundled variable font -> Light weight
        f.set_variation_by_axes([300])
    return f


def _spaced(d: ImageDraw.ImageDraw, cx: float, y: float, text: str, f, fill, track: float) -> None:
    width = sum(f.getlength(c) for c in text) + track * (len(text) - 1)
    x = cx - width / 2
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += f.getlength(c) + track


def render_title(album: str, artist: str, width: int, height: int, out: Path, font: str = "") -> Path:
    """Transparent PNG with the text only; it is laid over black in the video."""
    k = height / 1080
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    big, small = _font(font, round(36 * k)), _font(font, round(24 * k))
    # shrink the album line if it would run wider than 80% of the frame
    while big.getlength(album) + 2 * k * len(album) > 0.8 * width and big.size > 12:
        big = _font(font, big.size - 2)
    _spaced(d, width / 2, height / 2 - 36 * k, album, big, CREAM, 2 * k)
    _spaced(d, width / 2, height / 2 + 16 * k, artist, small, CREAM_DIM, 2 * k)
    img.save(out)
    return out
