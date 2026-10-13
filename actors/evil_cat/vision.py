"""Multimodal 2D Viewport Cutout Renderer & Cardinal Target Legend for Evil Cat (cat-3).

Generates a compact, North-up 2D PNG image of Evil Cat's active vision rectangle
(always <= 384x384 px so Gemini 2.5 Flash encodes it as a single 258-token visual tile)
alongside a zero-math cardinal target legend.

Uses Python's standard library (`zlib`, `struct`, `base64`) with a built-in 5x7
pixel font — zero external imaging dependencies required, sub-millisecond rendering.
"""

from __future__ import annotations

import base64
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

from actors.models import Observation

# 5x7 bitmap font for uppercase ASCII, digits, and tactical symbols.
# Each character is 7 rows of 5-bit integers (MSB = left pixel).
_FONT_5X7: Dict[str, Tuple[int, int, int, int, int, int, int]] = {
    " ": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000),
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "B": (0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110),
    "C": (0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110),
    "D": (0b11100, 0b10010, 0b10001, 0b10001, 0b10001, 0b10010, 0b11100),
    "E": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111),
    "F": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000),
    "G": (0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110),
    "H": (0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "I": (0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "J": (0b00111, 0b00010, 0b00010, 0b00010, 0b10010, 0b10010, 0b01100),
    "K": (0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001),
    "L": (0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111),
    "M": (0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001),
    "N": (0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001),
    "O": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "P": (0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000),
    "Q": (0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101),
    "R": (0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "U": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "V": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "W": (0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001),
    "X": (0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001),
    "Y": (0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100),
    "Z": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111),
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111),
    "3": (0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b10000, 0b11110, 0b00001, 0b00001, 0b11110),
    "6": (0b01110, 0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00001, 0b01110),
    "-": (0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000),
    "+": (0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000),
    ":": (0b00000, 0b00100, 0b00100, 0b00000, 0b00100, 0b00100, 0b00000),
    "(": (0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010),
    ")": (0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000),
    "#": (0b01010, 0b11111, 0b01010, 0b01010, 0b11111, 0b01010, 0b00000),
    "@": (0b01110, 0b10001, 0b10111, 0b10101, 0b10111, 0b10000, 0b01110),
    "!": (0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100),
    "^": (0b00100, 0b01110, 0b10101, 0b00100, 0b00100, 0b00100, 0b00000),
    "v": (0b00000, 0b00100, 0b00100, 0b00100, 0b10101, 0b01110, 0b00100),
    "<": (0b00010, 0b00100, 0b01000, 0b11111, 0b01000, 0b00100, 0b00010),
    ">": (0b01000, 0b00100, 0b00010, 0b11111, 0b00010, 0b00100, 0b01000),
}

RGB = Tuple[int, int, int]


class _RGBCanvas:
    """Fast in-memory RGB pixel buffer with pure-Python PNG serialization."""

    def __init__(self, width: int, height: int, bg: RGB = (11, 15, 25)) -> None:
        self.width = width
        self.height = height
        row_bytes = bytes(bg) * width
        self.buf = bytearray(row_bytes * height)

    def set_px(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            idx = (y * self.width + x) * 3
            self.buf[idx] = color[0]
            self.buf[idx + 1] = color[1]
            self.buf[idx + 2] = color[2]

    def fill_rect(self, x0: int, y0: int, w: int, h: int, color: RGB) -> None:
        x_start = max(0, x0)
        y_start = max(0, y0)
        x_end = min(self.width, x0 + w)
        y_end = min(self.height, y0 + h)
        if x_start >= x_end or y_start >= y_end:
            return
        span = x_end - x_start
        row_patch = bytes(color) * span
        stride = self.width * 3
        for y in range(y_start, y_end):
            start_idx = y * stride + x_start * 3
            self.buf[start_idx : start_idx + span * 3] = row_patch

    def stroke_rect(self, x0: int, y0: int, w: int, h: int, color: RGB, thickness: int = 1) -> None:
        for t in range(thickness):
            self.fill_rect(x0 + t, y0 + t, w - 2 * t, 1, color)
            self.fill_rect(x0 + t, y0 + h - 1 - t, w - 2 * t, 1, color)
            self.fill_rect(x0 + t, y0 + t, 1, h - 2 * t, color)
            self.fill_rect(x0 + w - 1 - t, y0 + t, 1, h - 2 * t, color)

    def draw_text(
        self,
        x: int,
        y: int,
        text: str,
        color: RGB,
        scale: int = 1,
        center_x: bool = False,
    ) -> None:
        if not text:
            return
        char_w = 5 * scale
        spacing = 1 * scale
        total_w = len(text) * char_w + max(0, len(text) - 1) * spacing
        cur_x = (x - total_w // 2) if center_x else x
        for ch in text:
            glyph = _FONT_5X7.get(ch) or _FONT_5X7.get(ch.upper()) or _FONT_5X7[" "]
            for row_idx, bits in enumerate(glyph):
                for col_idx in range(5):
                    if bits & (1 << (4 - col_idx)):
                        px = cur_x + col_idx * scale
                        py = y + row_idx * scale
                        if scale == 1:
                            self.set_px(px, py, color)
                        else:
                            self.fill_rect(px, py, scale, scale, color)
            cur_x += char_w + spacing

    def to_png_bytes(self) -> bytes:
        """Encodes the RGB buffer into a standard, lossless PNG byte stream."""
        stride = self.width * 3
        raw_rows = bytearray()
        for y in range(self.height):
            raw_rows.append(0)  # filter type 0 (None)
            row_start = y * stride
            raw_rows.extend(self.buf[row_start : row_start + stride])

        def _chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag)
            crc = zlib.crc32(data, crc) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw_rows), level=6)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", idat)
            + _chunk(b"IEND", b"")
        )


def compute_relative_coords(
    sx: int,
    sy: int,
    facing: str,
    tx: int,
    ty: int,
    attack_range: int = 2,
) -> Tuple[int, int, bool]:
    """Returns `(forward, offaxis, in_strike_zone)` for `(tx, ty)` relative to `(sx, sy, facing)`."""
    dx = tx - sx
    dy = ty - sy
    f = (facing or "north").lower()
    if f == "north":
        forward, offaxis = -dy, dx
    elif f == "south":
        forward, offaxis = dy, -dx
    elif f == "east":
        forward, offaxis = dx, dy
    elif f == "west":
        forward, offaxis = -dx, -dy
    else:
        forward, offaxis = -dy, dx

    in_strike = (1 <= forward <= attack_range) and (abs(offaxis) <= 1)
    return forward, offaxis, in_strike


def compute_viewport_bounds(obs: Observation) -> Tuple[int, int, int, int, int]:
    """Computes `(x_min, x_max, y_min, y_max, attack_range)` for the cat's vision rectangle."""
    pos = (obs.self or {}).get("position") or {}
    sx = int(pos.get("x", 0))
    sy = int(pos.get("y", 0))
    facing = str((obs.self or {}).get("facing") or "north").lower()

    caps = obs.capabilities or {}
    vision_rad = int(caps.get("vision_radius", 4))
    vision_rear = int(caps.get("vision_rear", 2))
    vision_half = int(caps.get("vision_half_width", 5))
    atk_range = max(int(caps.get("attack_range", 2)), 2)

    if facing == "north":
        x_min, x_max = sx - vision_half, sx + vision_half
        y_min, y_max = sy - vision_rad, sy + vision_rear
    elif facing == "south":
        x_min, x_max = sx - vision_half, sx + vision_half
        y_min, y_min_s, y_max = sy - vision_rear, sy - vision_rear, sy + vision_rad
    elif facing == "east":
        x_min, x_max = sx - vision_rear, sx + vision_rad
        y_min, y_max = sy - vision_half, sy + vision_half
    elif facing == "west":
        x_min, x_max = sx - vision_rad, sx + vision_rear
        y_min, y_max = sy - vision_half, sy + vision_half
    else:
        x_min, x_max = sx - vision_half, sx + vision_half
        y_min, y_max = sy - vision_rad, sy + vision_rear

    # Ensure any reported visible_entities are inside the rendered rectangle (up to +/-7 tiles)
    for ent in obs.visible_entities or []:
        epos = ent.get("position") or {}
        ex = epos.get("x")
        ey = epos.get("y")
        if isinstance(ex, int) and abs(ex - sx) <= 7:
            x_min = min(x_min, ex)
            x_max = max(x_max, ex)
        if isinstance(ey, int) and abs(ey - sy) <= 7:
            y_min = min(y_min, ey)
            y_max = max(y_max, ey)

    return x_min, x_max, y_min, y_max, atk_range


def short_entity_tag(ent: Dict[str, Any]) -> str:
    """Produces a compact 2-3 char badge label for an entity (e.g. `cat-2` -> `C2`, `mouse-14` -> `M14`)."""
    eid = str(ent.get("id") or "")
    etype = str(ent.get("type") or "").lower()
    suffix = eid.split("-")[-1] if "-" in eid else eid[-2:]
    if etype == "cat" or eid.startswith("cat"):
        return f"C{suffix}"[:3].upper()
    if etype == "mouse" or eid.startswith("mouse"):
        return f"M{suffix}"[:3].upper()
    return (eid[:3] or "E").upper()


def _draw_facing_arrow(canvas: _RGBCanvas, cx: int, cy: int, facing: str, color: RGB) -> None:
    """Draws a directional indicator arrow inside the cat's tile."""
    f = (facing or "north").lower()
    if f == "north":
        canvas.draw_text(cx, cy, "^", color, scale=1, center_x=True)
    elif f == "south":
        canvas.draw_text(cx, cy, "v", color, scale=1, center_x=True)
    elif f == "east":
        canvas.draw_text(cx, cy, ">", color, scale=1, center_x=True)
    elif f == "west":
        canvas.draw_text(cx, cy, "<", color, scale=1, center_x=True)


def render_viewport_png(obs: Observation, agent_id: str = "cat-3") -> bytes:
    """Renders a North-up 2D tactical image of the cat's vision rectangle as PNG bytes."""
    pos = (obs.self or {}).get("position") or {}
    sx = int(pos.get("x", 0))
    sy = int(pos.get("y", 0))
    facing = str((obs.self or {}).get("facing") or "north").lower()

    grid_info = getattr(obs, "grid", None) or (obs.capabilities or {}).get("grid") or {}
    grid_w = int(grid_info.get("width", 100) or 100)
    grid_h = int(grid_info.get("height", 100) or 100)

    x_min, x_max, y_min, y_max, atk_range = compute_viewport_bounds(obs)
    cols = max(1, x_max - x_min + 1)
    rows = max(1, y_max - y_min + 1)

    # Keep total image size <= 384x384 so Gemini encodes as a single 258-token tile
    tile_size = 24
    left_margin = 36
    right_margin = 36
    top_margin = 32
    bottom_margin = 32

    img_w = left_margin + cols * tile_size + right_margin
    img_h = top_margin + rows * tile_size + bottom_margin

    canvas = _RGBCanvas(img_w, img_h, bg=(11, 15, 25))

    # Index visible entities by (x, y)
    entities_by_pos: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for ent in obs.visible_entities or []:
        epos = ent.get("position") or {}
        ex, ey = epos.get("x"), epos.get("y")
        if isinstance(ex, int) and isinstance(ey, int):
            # Prefer rival cats over mice if stacked on the same tile
            existing = entities_by_pos.get((ex, ey))
            if not existing or ent.get("type") == "cat":
                entities_by_pos[(ex, ey)] = ent

    # 1. Compass edge banners (North-Up orientation)
    compass_color: RGB = (56, 189, 248)
    canvas.draw_text(img_w // 2, 5, "^ NORTH (Y-) ^", compass_color, scale=1, center_x=True)
    canvas.draw_text(
        img_w // 2,
        img_h - 22,
        "v SOUTH (Y+) v",
        compass_color,
        scale=1,
        center_x=True,
    )
    canvas.draw_text(
        img_w // 2,
        img_h - 10,
        "YOU:PURPLE CONE:ORANGE HIT:GREEN CAT:RED",
        (148, 163, 184),
        scale=1,
        center_x=True,
    )

    mid_y = top_margin + (rows * tile_size) // 2 - 4
    canvas.draw_text(4, mid_y - 6, "WEST", compass_color, scale=1, center_x=False)
    canvas.draw_text(4, mid_y + 4, "(X-)", compass_color, scale=1, center_x=False)
    canvas.draw_text(
        img_w - right_margin + 6, mid_y - 6, "EAST", compass_color, scale=1, center_x=False
    )
    canvas.draw_text(
        img_w - right_margin + 6, mid_y + 4, "(X+)", compass_color, scale=1, center_x=False
    )

    # 2. Column X coordinate headers
    coord_color: RGB = (148, 163, 184)
    for col_idx in range(cols):
        gx = x_min + col_idx
        cx = left_margin + col_idx * tile_size + tile_size // 2
        if 0 <= gx < grid_w:
            canvas.draw_text(cx, top_margin - 11, str(gx), coord_color, scale=1, center_x=True)

    # 3. Row Y coordinate headers + Grid Tiles
    for row_idx in range(rows):
        gy = y_min + row_idx
        py = top_margin + row_idx * tile_size
        if 0 <= gy < grid_h:
            canvas.draw_text(
                left_margin - 10, py + (tile_size - 7) // 2, str(gy), coord_color, scale=1, center_x=True
            )

        for col_idx in range(cols):
            gx = x_min + col_idx
            px = left_margin + col_idx * tile_size

            out_of_bounds = gx < 0 or gx >= grid_w or gy < 0 or gy >= grid_h
            if out_of_bounds:
                # Out-of-bounds arena wall tile
                canvas.fill_rect(px, py, tile_size, tile_size, (30, 41, 59))
                canvas.stroke_rect(px, py, tile_size, tile_size, (51, 65, 85), thickness=1)
                canvas.draw_text(
                    px + tile_size // 2, py + (tile_size - 7) // 2, "#", (71, 85, 105), scale=1, center_x=True
                )
                continue

            _fwd, _off, computed_strike = compute_relative_coords(sx, sy, facing, gx, gy, atk_range)
            ent = entities_by_pos.get((gx, gy))
            ent_in_strike = (
                bool(ent.get("in_strike_zone"))
                if (ent and ent.get("in_strike_zone") is not None)
                else computed_strike
            )
            is_strike_tile = computed_strike or ent_in_strike

            # Base floor or strike-zone shading
            if is_strike_tile and ent is not None:
                # Active target inside strike zone -> neon green LOCK-ON highlight
                canvas.fill_rect(px, py, tile_size, tile_size, (20, 83, 45))
                canvas.stroke_rect(px, py, tile_size, tile_size, (74, 222, 128), thickness=2)
            elif is_strike_tile:
                # Empty strike zone tile -> warm amber/orange highlight
                canvas.fill_rect(px, py, tile_size, tile_size, (76, 29, 14))
                canvas.stroke_rect(px, py, tile_size, tile_size, (249, 115, 22), thickness=2)
                canvas.set_px(px + tile_size // 2, py + tile_size // 2, (251, 146, 60))
            else:
                # Standard walkable floor tile
                canvas.fill_rect(px, py, tile_size, tile_size, (19, 28, 46))
                canvas.stroke_rect(px, py, tile_size, tile_size, (40, 53, 77), thickness=1)
                canvas.set_px(px + tile_size // 2, py + tile_size // 2, (56, 72, 102))

            # Draw Evil Cat (`self`) at (sx, sy)
            if gx == sx and gy == sy:
                canvas.fill_rect(px + 2, py + 2, tile_size - 4, tile_size - 4, (147, 51, 234))
                canvas.stroke_rect(px + 1, py + 1, tile_size - 2, tile_size - 2, (255, 255, 255), thickness=1)
                _draw_facing_arrow(canvas, px + tile_size // 2, py + 3, facing, (254, 240, 138))
                canvas.draw_text(
                    px + tile_size // 2, py + 13, "YOU", (255, 255, 255), scale=1, center_x=True
                )
                continue

            # Draw visible entity on tile
            if ent is not None:
                etype = str(ent.get("type") or "").lower()
                tag = short_entity_tag(ent)
                if etype == "cat" or str(ent.get("id", "")).startswith("cat"):
                    # Rival Cat: crimson badge (with bright gold border if in strike zone)
                    badge_border: RGB = (250, 204, 21) if ent_in_strike else (254, 202, 202)
                    canvas.fill_rect(px + 3, py + 3, tile_size - 6, tile_size - 6, (220, 38, 38))
                    canvas.stroke_rect(px + 2, py + 2, tile_size - 4, tile_size - 4, badge_border, thickness=1)
                    canvas.draw_text(
                        px + tile_size // 2, py + 6, tag, (255, 255, 255), scale=1, center_x=True
                    )
                    # Mini HP bar at bottom of rival cat badge
                    hp = int(ent.get("hp", 100) or 100)
                    max_hp = int(ent.get("max_hp", 100) or 100)
                    bar_w = tile_size - 8
                    fill_w = max(1, min(bar_w, int(round((hp / max(1, max_hp)) * bar_w))))
                    canvas.fill_rect(px + 4, py + tile_size - 6, bar_w, 2, (69, 10, 10))
                    canvas.fill_rect(px + 4, py + tile_size - 6, fill_w, 2, (74, 222, 128))
                else:
                    # Mouse: cyan/blue badge
                    badge_border = (74, 222, 128) if ent_in_strike else (186, 230, 253)
                    canvas.fill_rect(px + 4, py + 4, tile_size - 8, tile_size - 8, (2, 132, 199))
                    canvas.stroke_rect(px + 3, py + 3, tile_size - 6, tile_size - 6, badge_border, thickness=1)
                    canvas.draw_text(
                        px + tile_size // 2,
                        py + (tile_size - 7) // 2,
                        tag[:2],
                        (255, 255, 255),
                        scale=1,
                        center_x=True,
                    )

    return canvas.to_png_bytes()


def render_viewport_data_uri(obs: Observation, agent_id: str = "cat-3") -> Tuple[bytes, str]:
    """Returns `(png_bytes, data_uri)` for the observation's 2D viewport cutout."""
    png_bytes = render_viewport_png(obs, agent_id=agent_id)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return png_bytes, f"data:image/png;base64,{b64}"


def format_cardinal_target_legend(obs: Observation, agent_id: str = "cat-3") -> str:
    """Formats visible entities with zero-math cardinal deltas and strike-zone status."""
    pos = (obs.self or {}).get("position") or {}
    sx = int(pos.get("x", 0))
    sy = int(pos.get("y", 0))
    facing = str((obs.self or {}).get("facing") or "north").lower()
    caps = obs.capabilities or {}
    atk_range = max(int(caps.get("attack_range", 2)), 2)
    scores = obs.scoreboard or {}

    entities = obs.visible_entities or []
    if not entities:
        return (
            "Visible Targets in Sight (0): None in viewport "
            "(see attached 2D Viewport Image for walls and orange strike-zone orientation)."
        )

    lines: List[str] = [
        f"Visible Targets in Sight ({len(entities)}) [Matched to 2D Viewport Image Badges]:"
    ]
    for ent in entities:
        eid = str(ent.get("id") or "unknown")
        etype = str(ent.get("type") or "entity").lower()
        epos = ent.get("position") or {}
        ex = int(epos.get("x", sx))
        ey = int(epos.get("y", sy))
        tag = short_entity_tag(ent)

        dx = ex - sx
        dy = ey - sy
        ew_str = f"{abs(dx)} {'EAST' if dx > 0 else 'WEST'}" if dx != 0 else "0 E/W"
        ns_str = f"{abs(dy)} {'SOUTH' if dy > 0 else 'NORTH'}" if dy != 0 else "0 N/S"

        fwd, off, calc_strike = compute_relative_coords(sx, sy, facing, ex, ey, atk_range)
        if ent.get("forward") is not None:
            fwd = int(ent["forward"])
        if ent.get("offaxis") is not None:
            off = int(ent["offaxis"])
        in_strike = (
            bool(ent["in_strike_zone"])
            if ent.get("in_strike_zone") is not None
            else calc_strike
        )

        # Recommend cardinal step to align or close gap if outside strike zone
        step_hints: List[str] = []
        if dy < 0:
            step_hints.append("NORTH")
        elif dy > 0:
            step_hints.append("SOUTH")
        if dx > 0:
            step_hints.append("EAST")
        elif dx < 0:
            step_hints.append("WEST")
        align_hint = "/".join(step_hints) if step_hints else facing.upper()

        if etype == "cat" or eid.startswith("cat"):
            hp = ent.get("hp", 100)
            max_hp = ent.get("max_hp", 100)
            bounty = int(scores.get(eid, 0))
            role_desc = f"RIVAL CAT | HP: {hp}/{max_hp} | Bounty: +{bounty} pts"
        else:
            role_desc = "mouse | Emergency +40 HP heal if wounded, 0 pts"

        if in_strike:
            strike_status = "🎯 IN STRIKE ZONE (GREEN LOCK-ON — `attack` connects this tick!)"
        else:
            strike_status = f"Outside strike zone (step {align_hint} to close/align)"

        lines.append(
            f"- [{tag}] {eid} ({role_desc}) at (x={ex}, y={ey}) | "
            f"Cardinal Δ: ({ew_str}, {ns_str}) | Cone: fwd={fwd:+d}, off={off:+d} -> {strike_status}"
        )

    return "\n".join(lines)
