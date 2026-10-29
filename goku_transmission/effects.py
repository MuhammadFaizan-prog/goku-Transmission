"""The Instant Transmission effect, drawn in code.

Timed frame by frame against the reference reel (30 fps, ~0.3 s):

  vanish  frames 0-1  you are still there, thin vertical lines rain over the whole frame
          frame  2    cut: you're gone, a faint afterimage + a glowing outline of your silhouette
          frames 3-8  only the outline and the lines remain, both fading out
  appear  the same, backwards: outline -> you snap back in under the lines -> lines fade

It's a hard cut, not a fade, which is what makes it feel like a teleport.
`t` runs 0 -> 1 for vanishing; the engine runs it 1 -> 0 for reappearing.
"""
from __future__ import annotations

import cv2
import numpy as np

# phase boundaries in t (0..1). At the default 0.3 s / 30 fps that's 2 / 1 / 6 frames.
LINES_ON_PERSON = 0.22     # you + lines
AFTERIMAGE = 0.34          # empty room + faint you + outline + lines
LINES_GONE = 0.9           # lines fully faded by here; the outline fades until 1.0


class TeleportFX:
    def __init__(self, seed: int = 7):
        self.seed = seed
        self.reset()

    def reset(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self._outline = None        # silhouette contour, frozen at the start of the transition
        self._outline_shape = None

    # -- pieces -----------------------------------------------------------
    def _contours(self, mask: np.ndarray):
        h, w = mask.shape
        scale = 320.0 / max(w, 1)
        small = cv2.resize(mask, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
        binm = (cv2.GaussianBlur(small, (5, 5), 0) > 0.5).astype(np.uint8)
        cs, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cs = [c for c in cs if cv2.contourArea(c) > 0.002 * binm.size]
        return [c.astype(np.float32) / scale for c in cs]

    def _draw_outline(self, layer: np.ndarray, alpha: float) -> None:
        if not self._outline or alpha <= 0.01:
            return
        h, w = layer.shape[:2]
        th = max(1, round(w / 640))
        glow = np.zeros_like(layer)
        for c in self._outline:
            pts = c.reshape(-1, 2)
            # a little wobble so it reads as energy, not a vector stroke
            n = len(pts)
            wob = self.rng.normal(0, 0.6 * th, (n, 2)).astype(np.float32)
            wob = cv2.blur(wob.reshape(-1, 1, 2), (1, 9)).reshape(-1, 2) * 3
            p = np.round(pts + wob).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(glow, [p], True, (200, 235, 255), th + 1, cv2.LINE_AA)
            cv2.polylines(layer, [p], True, (235, 250, 255), th, cv2.LINE_AA)
        glow = cv2.GaussianBlur(glow, (0, 0), 2.5 * th)
        cv2.add(layer, (glow * 0.45).astype(np.uint8), dst=layer)
        if alpha < 1.0:
            cv2.multiply(layer, np.full_like(layer, 1), dst=layer, scale=alpha)

    def _draw_lines(self, out: np.ndarray, amount: float) -> None:
        """Thin vertical lines over the whole frame, new random set every frame."""
        if amount <= 0.01:
            return
        h, w = out.shape[:2]
        th = max(1, round(w / 640))
        n = int(34 * amount) + 4
        xs = self.rng.integers(0, w, n)
        y0 = (self.rng.random(n) ** 2 * 0.35 * h).astype(int)
        y1 = np.minimum(h, y0 + ((0.35 + 0.65 * self.rng.random(n)) * h).astype(int))
        dark = self.rng.random(n) < 0.28
        light = out.copy()
        for x, a, b, d in zip(xs, y0, y1, dark):
            col = (45, 45, 50) if d else (245, 250, 255)
            cv2.line(light, (int(x), int(a)), (int(x), int(b)), col, th + (0 if d else int(self.rng.random() < 0.3)))
        cv2.addWeighted(light, min(1.0, 0.35 + 0.75 * amount), out, 1 - min(1.0, 0.35 + 0.75 * amount), 0, dst=out)

    # -- main ---------------------------------------------------------------
    def render(self, frame: np.ndarray, plate: np.ndarray, person_mask: np.ndarray, t: float) -> np.ndarray:
        t = float(min(max(t, 0.0), 1.0))
        if t <= 0.0:
            return frame
        if t >= 1.0:
            return plate.copy()
        if self._outline is None or self._outline_shape != frame.shape:
            self._outline = self._contours(person_mask)
            self._outline_shape = frame.shape

        if t < LINES_ON_PERSON:
            out = frame.copy()
            self._draw_lines(out, 1.0)
            return out

        out = plate.copy()
        if t < AFTERIMAGE:
            m = cv2.GaussianBlur(person_mask, (0, 0), 3)[..., None] * 0.22
            tint = cv2.addWeighted(frame, 0.7, np.full_like(frame, (150, 215, 240)), 0.3, 0)
            out = (out * (1 - m) + tint * m).astype(np.uint8)
            line_amt, outline_a = 1.0, 1.0
        else:
            k = (t - AFTERIMAGE) / (1.0 - AFTERIMAGE)
            line_amt = max(0.0, 1.0 - (t - AFTERIMAGE) / (LINES_GONE - AFTERIMAGE))
            outline_a = (1.0 - k) ** 1.3

        layer = np.zeros_like(out)
        self._draw_outline(layer, outline_a)
        cv2.add(out, layer, dst=out)
        self._draw_lines(out, line_amt)
        return out

    def render_appear(self, frame: np.ndarray, plate: np.ndarray, person_mask: np.ndarray, s: float) -> np.ndarray:
        """Reappearing, s = 0 -> 1: outline flashes on the empty room, you snap in under the
        lines (with a one-frame kick sideways), then the lines thin out over you."""
        s = float(min(max(s, 0.0), 1.0))
        if s >= 1.0:
            return frame
        if self._outline is None or self._outline_shape != frame.shape:
            self._outline = self._contours(person_mask)
            self._outline_shape = frame.shape
        if s < 0.12:
            out = plate.copy()
            layer = np.zeros_like(out)
            self._draw_outline(layer, 1.0)
            cv2.add(out, layer, dst=out)
            self._draw_lines(out, 1.0)
            return out
        out = frame.copy()
        if s < 0.24:   # the snap-in frame jolts a little, like the reel
            dx = int(frame.shape[1] * 0.015)
            out = cv2.warpAffine(frame, np.float32([[1, 0, dx], [0, 1, 0]]), (frame.shape[1], frame.shape[0]),
                                 borderMode=cv2.BORDER_REPLICATE)
        self._draw_lines(out, max(0.0, 1.0 - (s - 0.12) / 0.68))
        return out


def teleport_frame(frame, plate, person_mask, t, seed: int = 7):
    """Stateless helper for tests and the self-test."""
    return TeleportFX(seed).render(frame, plate, person_mask, t)
