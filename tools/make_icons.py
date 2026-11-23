"""Render the app icons (PNG + SVG) with numpy only. Run: python tools/make_icons.py

Motif: a source dot with two broadcast arcs, amber on deep navy.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
NAVY = (0x10, 0x16, 0x2B)
AMBER = (0xF5, 0xB8, 0x3D)

# Geometry in a 512 x 512 design space.
DOT = (183.0, 256.0, 46.0)
ARCS = ((110.0, 34.0), (175.0, 34.0))
ARC_HALF_ANGLE = math.radians(50)


def png(path: Path, img: np.ndarray) -> None:
    """Write an RGBA (h, w, 4) or opaque RGB (h, w, 3) image."""
    h, w, channels = img.shape
    color_type = 6 if channels == 4 else 2
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(data)


def sdf_glyph(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    cx, cy, r = DOT
    d = np.hypot(x - cx, y - cy) - r
    ang = np.arctan2(y - cy, x - cx)
    rad = np.hypot(x - cx, y - cy)
    for radius, width in ARCS:
        ring = np.abs(rad - radius) - width / 2
        ex, ey = cx + radius * math.cos(ARC_HALF_ANGLE), radius * math.sin(ARC_HALF_ANGLE)
        cap = np.minimum(np.hypot(x - ex, y - (cy - ey)), np.hypot(x - ex, y - (cy + ey))) - width / 2
        arc = np.where(np.abs(ang) <= ARC_HALF_ANGLE, ring, cap)
        d = np.minimum(d, arc)
    return d


def sdf_round_rect(x, y, size, radius):
    half = size / 2
    qx = np.abs(x - half) - (half - radius)
    qy = np.abs(y - half) - (half - radius)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - radius


def render(size: int, *, rounded: bool, content_scale: float = 1.0) -> np.ndarray:
    s = 512.0 / size
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    x = (xs + 0.5) * s
    y = (ys + 0.5) * s
    # Scale the glyph around the centre (maskable icons need a safe zone).
    gx = (x - 256) / content_scale + 256
    gy = (y - 256) / content_scale + 256
    glyph = np.clip(0.5 - sdf_glyph(gx, gy) * content_scale / s, 0, 1)
    if rounded:
        bg = np.clip(0.5 - sdf_round_rect(x, y, 512, 112) / s, 0, 1)
    else:
        bg = np.ones_like(x)
    img = np.zeros((size, size, 4), dtype=np.float64)
    for i in range(3):
        img[..., i] = NAVY[i] * (1 - glyph) + AMBER[i] * glyph
    img[..., 3] = bg * 255
    return np.round(img).astype(np.uint8)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png(OUT / "icon-192.png", render(192, rounded=True))
    png(OUT / "icon-512.png", render(512, rounded=True))
    png(OUT / "maskable-512.png", render(512, rounded=False, content_scale=0.78))
    png(OUT / "apple-touch-icon.png", render(180, rounded=False, content_scale=0.86))
    # Lock screen / notification artwork (Media Session). The system draws it as a
    # square and rounds the corners itself, so it must be full-bleed and opaque:
    # transparent corners of the rounded app icon show up as white wedges on iOS.
    for size in (256, 512):
        png(OUT / f"artwork-{size}.png", render(size, rounded=False, content_scale=0.8)[..., :3])
    cx, cy, r = DOT
    paths = []
    for radius, _ in ARCS:
        ex = cx + radius * math.cos(ARC_HALF_ANGLE)
        ey = radius * math.sin(ARC_HALF_ANGLE)
        paths.append(f"M{ex:.1f} {cy - ey:.1f}A{radius:.0f} {radius:.0f} 0 0 1 {ex:.1f} {cy + ey:.1f}")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#10162B"/>'
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#F5B83D"/>'
        f'<path d="{"".join(paths)}" fill="none" stroke="#F5B83D" stroke-width="{ARCS[0][1]:.0f}" stroke-linecap="round"/>'
        "</svg>\n"
    )
    (OUT / "icon.svg").write_text(svg, "utf-8")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
