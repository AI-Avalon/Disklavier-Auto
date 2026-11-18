"""
Valid 16x16 32-bit RGBA Piano Keyboard Favicon (.ico) Generator.

Served on GET /favicon.ico by both stage_server.py and mission_control_server.py
to eliminate 404 Not Found logs in browsers and headless captures.
"""

from __future__ import annotations

import struct


def build_piano_favicon_ico() -> bytes:
    w, h = 16, 16
    # BGRA pixel buffer (bottom-up for ICO BMP)
    pixels = bytearray(w * h * 4)

    def _set_px(x: int, y_top: int, r: int, g: int, b: int, a: int = 255) -> None:
        y_bot = h - 1 - y_top
        idx = (y_bot * w + x) * 4
        pixels[idx : idx + 4] = bytes((b, g, r, a))

    # Editorial dark background (#060608) with off-white piano keys (#EAE6DF) and vermilion accent (#C83E2B)
    for y in range(h):
        for x in range(w):
            _set_px(x, y, 6, 6, 8, 255)

    # Outer border
    for x in range(1, 15):
        _set_px(x, 2, 234, 230, 223)
        _set_px(x, 13, 234, 230, 223)
    for y in range(2, 14):
        _set_px(1, y, 234, 230, 223)
        _set_px(14, y, 234, 230, 223)

    # White keys inside
    for y in range(3, 13):
        for x in range(2, 14):
            if x in (5, 8, 11):
                _set_px(x, y, 36, 34, 30)  # key gap
            else:
                _set_px(x, y, 234, 230, 223)

    # Black keys at upper half
    for y in range(3, 8):
        for x in (4, 5, 7, 8, 10, 11):
            _set_px(x, y, 12, 12, 14)

    # Vermilion active hammer dot on center key
    _set_px(6, 10, 200, 62, 43)
    _set_px(6, 11, 200, 62, 43)

    # AND mask (16 rows * 4 bytes padded to 32-bit boundary)
    and_mask = b"\x00\x00\x00\x00" * h

    bmp_header = struct.pack(
        "<IIIHHIIIIII",
        40,          # biSize
        w,           # biWidth
        h * 2,       # biHeight (XOR + AND)
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression (BI_RGB)
        len(pixels) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    image_data = bmp_header + bytes(pixels) + and_mask
    ico_header = struct.pack("<HHH", 0, 1, 1)  # Reserved, Type=ICO(1), Count=1
    dir_entry = struct.pack(
        "<BBBBHHII",
        w,
        h,
        0,           # ColorCount
        0,           # Reserved
        1,           # Planes
        32,          # BitCount
        len(image_data),
        6 + 16,      # Offset
    )
    return ico_header + dir_entry + image_data


PIANO_FAVICON_ICO_BYTES: bytes = build_piano_favicon_ico()
