"""The Instant Transmission look, drawn in code (no anime frames or assets).

A teleport is one function of ``t`` in 0..1:

    0.00-0.45  charge   cyan aura + vertical speed lines run over your silhouette
    0.45-0.80  zip      your silhouette squeezes to a thin bright line and blinks out
    0.80-1.00  trail    a fading vertical streak where you stood

Reappearing plays the same animation backwards.
"""
from __future__ import annotations

import cv2
import numpy as np

AURA_BGR = np.array([255, 245, 150], np.float32)   # cyan-white (BGR)


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _seg(t: float, a: float, b: float) -> float:
    return _ease((t - a) / (b - a))


def soft_mask(person_mask: np.ndarray, blur: int = 7) -> np.ndarray:
    m = np.clip(person_mask.astype(np.float32), 0, 1)
    if blur > 1:
        m = cv2.GaussianBlur(m, (blur | 1, blur | 1), 0)
    return m


def _bbox(mask: np.ndarray, thr: float = 0.5):
    ys, xs = np.where(mask > thr)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


def _speed_lines(h: int, w: int, seed: int, phase: float) -> np.ndarray:
    """Animated vertical comic speed lines, as a 0..1 float map."""
    rng = np.random.default_rng(seed)
    cols = rng.choice(w, size=max(8, w // 18), replace=False)
    img = np.zeros((h, w), np.float32)
    for c in cols:
        length = int(h * rng.uniform(0.25, 0.8))
        start = int((rng.uniform(0, 1) + phase * rng.uniform(1.5, 3.0)) * h) % h
        thick = int(rng.integers(1, 3))
        y0, y1 = start, min(h, start + length)
        img[y0:y1, max(0, c - thick):c + thick] = rng.uniform(0.6, 1.0)
        if start + length > h:  # wrap around
            img[0:start + length - h, max(0, c - thick):c + thick] = rng.uniform(0.6, 1.0)
    return cv2.GaussianBlur(img, (1, 9), 0)


def _squeeze_x(layer: np.ndarray, alpha: np.ndarray, cx: float, s: float):
    """Scale a layer horizontally around column cx (s=1 unchanged, s->0 a thin line)."""
    h, w = alpha.shape
    s = max(s, 0.02)
    M = np.float32([[s, 0, cx * (1 - s)], [0, 1, 0]])
    lay = cv2.warpAffine(layer, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    al = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return lay, al


def teleport_frame(frame: np.ndarray, plate: np.ndarray, person_mask: np.ndarray, t: float, seed: int = 7) -> np.ndarray:
    """Render the vanish animation at progress ``t`` (0 = fully here, 1 = gone)."""
    t = float(min(max(t, 0.0), 1.0))
    if t <= 0.0:
        return frame.copy()
    f = frame.astype(np.float32)
    bg = plate.astype(np.float32)
    a = soft_mask(person_mask)
    h, w = a.shape
    box = _bbox(a)
    if box is None:  # nobody in frame - nothing to animate
        return plate.copy() if t >= 0.8 else frame.copy()
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2

    charge = _seg(t, 0.0, 0.45)
    zip_ = _seg(t, 0.45, 0.80)
    trail = 1.0 - _seg(t, 0.80, 1.0)

    # 1) charge: tint the silhouette cyan + speed lines over it
    person = f.copy()
    lines = _speed_lines(h, w, seed, phase=t * 4.0) * a
    person = person * (1 - 0.35 * charge) + AURA_BGR * 0.35 * charge
    person = person * (1 - lines[..., None] * charge) + 255.0 * lines[..., None] * charge

    # 2) zip: squeeze to a line, brighten, fade
    s = 1.0 - zip_
    person, alpha = _squeeze_x(person, a, cx, s)
    alpha = alpha * (1.0 - _seg(t, 0.70, 0.80))
    person = person * (1 - 0.6 * zip_) + 255.0 * 0.6 * zip_

    out = bg * (1 - alpha[..., None]) + person * alpha[..., None]

    # aura glow around the silhouette edge while charging
    edge = cv2.GaussianBlur(alpha, (0, 0), 9) - alpha * 0.6
    out += np.clip(edge, 0, 1)[..., None] * AURA_BGR * 0.9 * charge * (1 - zip_ * 0.5)

    # 3) trail: thin vertical streak at the vanish point
    if t >= 0.6:
        streak = np.zeros((h, w), np.float32)
        half = max(2, int(w * 0.006))
        streak[max(0, y0 - 20):min(h, y1 + 20), max(0, int(cx) - half):int(cx) + half] = 1.0
        streak = cv2.GaussianBlur(streak, (0, 0), 6)
        out += streak[..., None] * AURA_BGR * 1.4 * trail * _seg(t, 0.6, 0.8)

    # quick flash on the blink-out
    flash = max(0.0, 1.0 - abs(t - 0.80) / 0.06)
    out = out * (1 - 0.35 * flash) + 255.0 * 0.35 * flash

    return np.clip(out, 0, 255).astype(np.uint8)
