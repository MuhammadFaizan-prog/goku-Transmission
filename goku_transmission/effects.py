"""Instant Transmission, drawn in code (no anime frames or copyrighted assets).

Modelled on how the move reads on screen: two fingers to the forehead, a burst
of vertical ki streaks runs over the body, the body flickers in and out a few
times, and it's gone, with a few streaks hanging in the air for a moment.

    t 0.00-0.35  charge   vertical cyan/white streaks scroll up over your silhouette, rim glow
    t 0.35-0.70  flicker  you blink in and out (on, off, on, off...) with a slight jitter
    t 0.70-1.00  gone     the streaks flare, then fade where you stood

Reappearing plays it backwards. Everything is done inside your bounding box
only, so it's cheap even at 1080p.
"""
from __future__ import annotations

import cv2
import numpy as np

CYAN = np.array([255, 230, 120], np.float32)   # BGR
WHITE = np.array([255, 255, 255], np.float32)
FLICKER = [1.0, 0.15, 0.9, 0.0, 0.55, 0.0, 0.25, 0.0]   # visibility during the flicker phase


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class TeleportFX:
    def __init__(self, seed: int = 7):
        self.seed = seed
        self._tex: np.ndarray | None = None
        self._tex_key = None
        self._box = None

    def reset(self) -> None:
        self._box = None   # recompute the bounding box for the next teleport
        self.seed += 1

    # -- helpers --------------------------------------------------------
    def _streaks(self, h: int, w: int) -> np.ndarray:
        """Vertical streak texture (h*2 tall so it can scroll), values 0..1."""
        key = (h, w, self.seed)
        if self._tex_key != key:
            rng = np.random.default_rng(self.seed)
            tex = np.zeros((h * 2, w), np.float32)
            n = max(6, w // 14)
            for c in rng.choice(w, size=min(n, w), replace=False):
                thick = int(rng.integers(1, 4))
                for _ in range(int(rng.integers(1, 3))):
                    y0 = int(rng.integers(0, h * 2))
                    ln = int(h * rng.uniform(0.3, 0.9))
                    ys = np.arange(y0, y0 + ln) % (h * 2)
                    fade = np.linspace(0.2, 1.0, ln, dtype=np.float32)   # bright head, soft tail
                    tex[ys, max(0, c - thick // 2):c + thick // 2 + 1] = np.maximum(
                        tex[ys, max(0, c - thick // 2):c + thick // 2 + 1], fade[:, None] * rng.uniform(0.6, 1.0))
            self._tex = cv2.GaussianBlur(tex, (3, 1), 0)
            self._tex_key = key
        return self._tex

    def _bbox(self, mask: np.ndarray, shape):
        H, W = shape
        m = (mask > 0.5).astype(np.uint8)
        x, y, w, h = cv2.boundingRect(m)
        if w == 0 or h == 0:
            return self._box
        pad = int(0.05 * W)
        box = (max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad))
        if self._box is None:
            self._box = box
        else:   # grow only, so the effect never clips while you move
            b = self._box
            self._box = (min(b[0], box[0]), min(b[1], box[1]), max(b[2], box[2]), max(b[3], box[3]))
        return self._box

    # -- main -----------------------------------------------------------
    def render(self, frame: np.ndarray, plate: np.ndarray, person_mask: np.ndarray, t: float) -> np.ndarray:
        """Vanish animation at progress ``t`` (0 = fully here, 1 = gone)."""
        t = float(min(max(t, 0.0), 1.0))
        if t <= 0.0:
            return frame
        if t >= 1.0:
            return plate.copy()
        H, W = frame.shape[:2]
        box = self._bbox(person_mask, (H, W))
        if box is None:
            return frame if t < 0.7 else plate.copy()
        x0, y0, x1, y1 = box
        out = frame.copy()
        bh, bw = y1 - y0, x1 - x0
        # big boxes are animated at half resolution: it's a 0.7s blur of light, nobody sees the difference
        sc = 0.5 if bh * bw > 160_000 else 1.0
        fs, ps, ms = frame[y0:y1, x0:x1], plate[y0:y1, x0:x1], person_mask[y0:y1, x0:x1]
        if sc != 1.0:
            size = (max(1, int(bw * sc)), max(1, int(bh * sc)))
            fs = cv2.resize(fs, size, interpolation=cv2.INTER_AREA)
            ps = cv2.resize(ps, size, interpolation=cv2.INTER_AREA)
            ms = cv2.resize(ms, size, interpolation=cv2.INTER_AREA)
        f = fs.astype(np.float32)
        p = ps.astype(np.float32)
        a = cv2.GaussianBlur(np.clip(ms, 0, 1).astype(np.float32), (5, 5), 0)
        h, w = a.shape
        W = int(W * sc)
        k = lambda n: max(3, int(n * sc)) | 1   # noqa: E731  kernel sizes follow the working scale
        # where the person is (grown a little) the room comes from the plate
        halo = cv2.blur(cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_RECT, (k(15), k(15)))), (k(11), k(11)))
        room = f + (p - f) * halo[..., None]

        tex = self._streaks(h, w)
        off = int((t * 3.0 * h)) % (h * 2)
        lines = np.roll(tex, -off, axis=0)[:h]   # scroll upward

        charge = _ease(t / 0.35)
        if t < 0.35:
            vis, jitter = 1.0, 0
        elif t < 0.70:
            step = min(len(FLICKER) - 1, int((t - 0.35) / 0.35 * len(FLICKER)))
            vis = FLICKER[step]
            jitter = (-1) ** step * int(0.006 * W) if vis > 0 else 0
        else:
            vis, jitter = 0.0, 0

        # body: cyan tint + streaks running over it
        body = f * (1 - 0.25 * charge) + CYAN * 0.25 * charge
        sl = (lines * charge)[..., None]
        body = body * (1 - sl) + (WHITE * 0.6 + CYAN * 0.4) * sl
        alpha = a * vis
        if jitter:
            M = np.float32([[1, 0, jitter], [0, 1, 0]])
            body = cv2.warpAffine(body, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
            alpha = cv2.warpAffine(alpha, M, (w, h))
        comp = room * (1 - alpha[..., None]) + body * alpha[..., None]

        # rim glow while you're visible
        if vis > 0:
            rim = cv2.blur(alpha, (k(15), k(15))) - alpha
            comp += np.clip(rim, 0, 1)[..., None] * CYAN * 1.2 * charge

        # after you blink out: streaks linger in the air where you stood, then fade
        if t >= 0.6:
            fade = (1 - _ease((t - 0.7) / 0.3)) if t >= 0.7 else _ease((t - 0.6) / 0.1)
            column = cv2.blur(halo, (k(31), k(31)))
            ls = (lines * column * fade * 0.8)[..., None]
            comp = comp * (1 - ls) + (WHITE * 0.5 + CYAN * 0.5) * ls

        # a short burst on the streaks at the moment you vanish (on the lines only, never a silhouette)
        flash = max(0.0, 1.0 - abs(t - 0.70) / 0.06)
        if flash > 0:
            fl = (lines * cv2.blur(halo, (k(31), k(31))) * flash)[..., None]
            comp = comp * (1 - fl) + WHITE * fl

        comp = np.clip(comp, 0, 255).astype(np.uint8)
        if sc != 1.0:
            comp = cv2.resize(comp, (bw, bh), interpolation=cv2.INTER_LINEAR)
        out[y0:y1, x0:x1] = comp
        if t > 0.7:   # ease from the live room to the plate so the hand-off to "gone" is seamless
            kb = _ease((t - 0.7) / 0.3)
            cv2.addWeighted(out, 1 - kb, plate, kb, 0, dst=out)
        return out


def teleport_frame(frame, plate, person_mask, t, seed: int = 7):
    """Stateless convenience wrapper (used by tests and the self-test)."""
    fx = TeleportFX(seed)
    return fx.render(frame, plate, person_mask, t)
