"""Generate app icons programmatically (no external dependencies).

Creates two PNGs from scratch using only stdlib (struct + zlib):
  resources/app_icon.png   — 128×128  window / taskbar icon
  resources/default_icon.png —  64×64   card placeholder icon

The design is an original wrench (spanner) silhouette — clean,
geometric, and unmistakably "tool"-themed.  No stock art, no clip-art,
no copyright concerns.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


# ═══════════════════════════════════════════════════════════════════════════
# Minimal RGBA pixel-buffer → PNG encoder
# ═══════════════════════════════════════════════════════════════════════════

def _make_png(width: int, height: int, pixels: list[tuple[int,int,int,int]]) -> bytes:
    """Encode RGBA pixel list (row-major, top→bottom) as a PNG byte string."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)

    # IDAT — raw scanlines (filter byte 0 + RGBA pixels)
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter: None
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw += struct.pack("BBBB", r, g, b, a)

    idat = _chunk(b"IDAT", zlib.compress(raw))

    # IEND
    iend = _chunk(b"IEND", b"")

    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# ═══════════════════════════════════════════════════════════════════════════
# Drawing helpers
# ═══════════════════════════════════════════════════════════════════════════

def _fill_rect(px, w, x1, y1, x2, y2, color):
    for y in range(max(0, y1), min(len(px) // w, y2 + 1)):
        for x in range(max(0, x1), min(w, x2 + 1)):
            px[y * w + x] = color


def _fill_circle(px, w, cx, cy, r, color):
    """Filled circle (bounding-box check)."""
    for y in range(max(0, cy - r), min(len(px) // w, cy + r + 1)):
        for x in range(max(0, cx - r), min(w, cx + r + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                px[y * w + x] = color


def _clear_circle(px, w, cx, cy, r):
    """Erase to transparent inside a circle."""
    _fill_circle(px, w, cx, cy, r, (0, 0, 0, 0))


def _fill_roundrect(px, w, x1, y1, x2, y2, r, color):
    """Filled rounded rectangle."""
    _fill_rect(px, w, x1 + r, y1, x2 - r, y2, color)
    _fill_rect(px, w, x1, y1 + r, x2, y2 - r, color)
    _fill_circle(px, w, x1 + r, y1 + r, r, color)
    _fill_circle(px, w, x2 - r, y1 + r, r, color)
    _fill_circle(px, w, x1 + r, y2 - r, r, color)
    _fill_circle(px, w, x2 - r, y2 - r, r, color)


# ═══════════════════════════════════════════════════════════════════════════
# Icon:  Wrench (spanner)
# ═══════════════════════════════════════════════════════════════════════════

_BG   = (0, 0, 0, 0)        # transparent
_BLUE = (74, 158, 255, 255)  # accent blue  #4A9EFF
_DARK = (30, 30, 46, 255)    # dark bg       #1E1E2E


def draw_wrench_icon(size: int = 128) -> bytes:
    """Draw a centred wrench / spanner icon at *size*×*size*."""
    px = [_BG] * (size * size)
    s = size

    # Wrench oriented at ~ -30° (handle bottom-left → head top-right).
    # We draw axis-aligned then conceptually rotate, but for simplicity
    # we draw the wrench vertically (handle down, jaw up) — still reads
    # perfectly as a wrench.

    c = s // 2           # centre x
    jaw_top = int(s * 0.15)
    jaw_bot = int(s * 0.38)
    handle_top = jaw_bot
    handle_bot = int(s * 0.88)

    handle_hw = int(s * 0.065)   # handle half-width
    jaw_hw = int(s * 0.11)       # jaw arm half-width
    jaw_outer_hw = int(s * 0.20)
    jaw_inner_r = int(s * 0.08)  # inner gap radius
    head_r = int(s * 0.115)      # rounded head top radius

    # ── Handle (rounded rectangle) ───────────────────────────────────
    _fill_roundrect(px, s,
                    c - handle_hw, handle_top,
                    c + handle_hw, handle_bot,
                    handle_hw, _BLUE)

    # ── Jaw arms (two vertical bars thickening upward) ───────────────
    # Left arm
    _fill_rect(px, s, c - jaw_outer_hw, int(s * 0.22), c - jaw_hw, jaw_bot, _BLUE)
    # Right arm
    _fill_rect(px, s, c + jaw_hw, int(s * 0.22), c + jaw_outer_hw, jaw_bot, _BLUE)

    # ── Rounded head top connecting the two arms ─────────────────────
    # Horizontal bar at very top
    # Use a rounded cap: fill from jaw_top to ~jaw_top + head_r
    _fill_roundrect(px, s,
                    c - jaw_outer_hw, jaw_top,
                    c + jaw_outer_hw, jaw_top + head_r,
                    head_r, _BLUE)

    # ── Jaw inner cutout (transparent circle) ────────────────────────
    _clear_circle(px, s, c, jaw_top + head_r + jaw_inner_r + 4, jaw_inner_r + 2)

    # ── Grip lines on handle ─────────────────────────────────────────
    grip_y_start = int(s * 0.58)
    grip_y_end = int(s * 0.80)
    grip_count = 4
    for i in range(grip_count):
        gy = grip_y_start + i * (grip_y_end - grip_y_start) // (grip_count - 1)
        _fill_rect(px, s,
                   c - handle_hw + 2, gy - 1,
                   c + handle_hw - 2, gy + 1, _DARK)

    return _make_png(s, s, px)


# ═══════════════════════════════════════════════════════════════════════════
# Icon:  Toolbox (card placeholder, smaller & simpler)
# ═══════════════════════════════════════════════════════════════════════════

def draw_toolbox_icon(size: int = 64) -> bytes:
    """Draw a simple toolbox / tool-chest for the card placeholder."""
    px = [_BG] * (size * size)
    s = size

    c = s // 2
    box_top = int(s * 0.30)
    box_bot = int(s * 0.82)
    box_left = int(s * 0.15)
    box_right = int(s * 0.85)
    handle_r = int(s * 0.07)
    lid_h = int(s * 0.12)
    clasp_w = int(s * 0.04)
    clasp_h = int(s * 0.08)

    # Box body
    _fill_roundrect(px, s, box_left, box_top + lid_h, box_right, box_bot,
                    int(s * 0.06), _BLUE)

    # Lid
    _fill_roundrect(px, s, box_left, box_top, box_right, box_top + lid_h,
                    int(s * 0.06), _BLUE)

    # Handle (arc at top of lid)
    _fill_circle(px, s, c, box_top - 2, int(s * 0.10), _BLUE)
    _clear_circle(px, s, c, box_top - 2, int(s * 0.06))

    # Clasps (two small rectangles on the lid)
    clasp_top = box_top + int(s * 0.02)
    _fill_rect(px, s, c - int(s * 0.14), clasp_top,
               c - int(s * 0.14) + clasp_w, clasp_top + clasp_h, _DARK)
    _fill_rect(px, s, c + int(s * 0.14) - clasp_w, clasp_top,
               c + int(s * 0.14), clasp_top + clasp_h, _DARK)

    return _make_png(s, s, px)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    RESOURCES.mkdir(exist_ok=True)

    app_icon = RESOURCES / "app_icon.png"
    app_icon.write_bytes(draw_wrench_icon(128))
    print(f"[OK] {app_icon}  ({app_icon.stat().st_size:,} bytes)")

    default_icon = RESOURCES / "default_icon.png"
    default_icon.write_bytes(draw_toolbox_icon(64))
    print(f"[OK] {default_icon}  ({default_icon.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
