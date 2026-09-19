"""Hands-free scanning: when the cursor rests over the game's world map, look for a port tooltip.

Cheap checks first, so nothing heavy runs while sailing or fighting:
  game window in foreground -> cursor has stopped -> screenshot shows the parchment map -> OCR.
"""
import ctypes
import queue
import threading
import time
import traceback

import numpy as np
from PIL import Image

from . import capture, mapgeo, sound, tooltip

POLL = 0.1  # s between cursor checks
MOVE_TOLERANCE = 6  # px of jitter that still counts as "cursor stopped"
ATTEMPTS_AT = (0.35, 1.0)  # s after the cursor stops (the second one covers a tooltip still fading in)
SAME_PORT_COOLDOWN = 5.0  # s: don't rescan the port we just read


def looks_like_map(img: Image.Image) -> bool:
    """The world map is a big parchment sheet: tan pixels fill the middle of the screen."""
    a = np.asarray(img.reduce(10))
    h, w = a.shape[:2]
    a = a[h // 5:h * 9 // 10, w // 5:w * 4 // 5].astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    tan = (r >= g) & (g >= b - 5) & (r - b > 15) & (r - b < 90) & (r > 70) & (r < 235)
    return tan.mean() > 0.5


class AutoScanner(threading.Thread):
    def __init__(self, out: queue.Queue, known_goods, needs_position):
        """out gets ("scan", PortInfo, map_xy | None). needs_position(name) -> bool: whether a port
        still has no fixed position (only then the map is located to place it)."""
        super().__init__(daemon=True)
        self.out, self.known_goods, self.needs_position = out, known_goods, needs_position
        self.hwnd = None
        self.hwnd_checked = 0.0
        self.last_name, self.last_time = None, 0.0

    def run(self):
        user32 = ctypes.windll.user32
        stop_pos, stop_time, attempts = None, 0.0, 0
        while True:
            time.sleep(POLL)
            try:
                now = time.time()
                if self.hwnd is None or now - self.hwnd_checked > 5:
                    self.hwnd, self.hwnd_checked = capture.find_game_window(), now
                if not self.hwnd or user32.GetForegroundWindow() != self.hwnd:
                    stop_pos = None
                    continue
                pos = capture.cursor_pos()
                if stop_pos is None or max(abs(pos[0] - stop_pos[0]), abs(pos[1] - stop_pos[1])) > MOVE_TOLERANCE:
                    stop_pos, stop_time, attempts = pos, now, 0
                    continue
                if attempts < len(ATTEMPTS_AT) and now - stop_time >= ATTEMPTS_AT[attempts]:
                    attempts += 1
                    if self.try_scan() != "retry":
                        attempts = len(ATTEMPTS_AT)
            except Exception:
                traceback.print_exc()
                time.sleep(1)

    def try_scan(self) -> str:
        """Returns "done" (scanned or nothing to do for this stop) or "retry" (maybe the tooltip is fading in)."""
        rect = capture.client_rect(self.hwnd)
        cx, cy = capture.cursor_pos()
        cursor = (cx - rect[0], cy - rect[1])
        if not (0 <= cursor[0] < rect[2] and 0 <= cursor[1] < rect[3]):
            return "done"
        img = capture.grab(rect)
        if not looks_like_map(img):
            return "done"
        return self.handle(img, cursor)

    def handle(self, img: Image.Image, cursor) -> str:
        try:
            info, _, _ = tooltip.read_from_screenshot(img, self.known_goods())
        except tooltip.TooltipNotFound:
            return "retry"
        if not info.goods:
            return "retry"
        if info.name == self.last_name and time.time() - self.last_time < SAME_PORT_COOLDOWN:
            return "done"
        map_xy = None
        if self.needs_position(info.name):
            geo = mapgeo.locate(img)
            map_xy = geo.to_map(*cursor) if geo else None
        self.last_name, self.last_time = info.name, time.time()
        self.out.put(("scan", info, map_xy))
        sound.chime()
        return "done"
