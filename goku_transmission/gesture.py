"""Instant Transmission pose detection.

The pose: index + middle finger straight and held together, ring + pinky
curled, fingertips resting on the forehead. Everything here is pure Python on
normalized MediaPipe landmarks (x, y in 0..1), so it is fully unit-testable
without a camera or a model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

# MediaPipe hand landmark indices
WRIST = 0
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

Point = Tuple[float, float]
FaceBox = Tuple[float, float, float, float]  # x, y, w, h (normalized)


@dataclass(frozen=True)
class PoseConfig:
    extended_ratio: float = 1.15   # tip-to-wrist / pip-to-wrist above this = finger straight
    curled_ratio: float = 1.05     # ring / pinky below this = curled
    together_ratio: float = 0.55   # index-middle tip gap / palm width below this = "together"
    forehead_pad_x: float = 0.30   # forehead zone padding, in face widths
    forehead_top: float = 0.45     # zone starts this many face heights above the face box
    forehead_bottom: float = 0.45  # ...and ends this far down into the face box
    no_face_max_y: float = 0.60    # fallback when no face is known: tips in the upper 60% of frame


@dataclass
class PoseResult:
    is_pose: bool
    reasons: dict = field(default_factory=dict)


def _xy(p) -> Point:
    if isinstance(p, (tuple, list)):
        return float(p[0]), float(p[1])
    return float(p.x), float(p.y)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def finger_ratio(lm: Sequence[Point], tip: int, pip: int) -> float:
    """How far the tip reaches past the middle joint, measured from the wrist.

    Rotation-invariant, so it works whether the fingers point up or sideways
    (the Goku pose is usually sideways across the forehead).
    """
    w = lm[WRIST]
    return _dist(w, lm[tip]) / max(_dist(w, lm[pip]), 1e-6)


def fingertip_point(lm: Sequence[Point]) -> Point:
    a, b = lm[INDEX_TIP], lm[MIDDLE_TIP]
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def in_forehead_zone(pt: Point, face: FaceBox, cfg: PoseConfig) -> bool:
    x, y, w, h = face
    x0, x1 = x - cfg.forehead_pad_x * w, x + w + cfg.forehead_pad_x * w
    y0, y1 = y - cfg.forehead_top * h, y + cfg.forehead_bottom * h
    return x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1


def check_pose(landmarks: Sequence, face: Optional[FaceBox], cfg: PoseConfig = PoseConfig()) -> PoseResult:
    lm = [_xy(p) for p in landmarks]
    if len(lm) < 21:
        return PoseResult(False, {"error": "need 21 landmarks"})

    idx = finger_ratio(lm, INDEX_TIP, INDEX_PIP)
    mid = finger_ratio(lm, MIDDLE_TIP, MIDDLE_PIP)
    ring = finger_ratio(lm, RING_TIP, RING_PIP)
    pinky = finger_ratio(lm, PINKY_TIP, PINKY_PIP)
    palm = max(_dist(lm[INDEX_MCP], lm[PINKY_MCP]), 1e-6)
    gap = _dist(lm[INDEX_TIP], lm[MIDDLE_TIP]) / palm
    tip = fingertip_point(lm)

    two_straight = idx > cfg.extended_ratio and mid > cfg.extended_ratio
    others_curled = ring < cfg.curled_ratio and pinky < cfg.curled_ratio
    together = gap < cfg.together_ratio
    if face is not None:
        on_forehead = in_forehead_zone(tip, face, cfg)
    else:
        on_forehead = tip[1] <= cfg.no_face_max_y

    reasons = {
        "index": round(idx, 2), "middle": round(mid, 2), "ring": round(ring, 2), "pinky": round(pinky, 2),
        "gap": round(gap, 2), "tip": (round(tip[0], 3), round(tip[1], 3)), "face": face is not None,
        "two_straight": two_straight, "others_curled": others_curled, "together": together,
        "on_forehead": on_forehead,
    }
    return PoseResult(two_straight and others_curled and together and on_forehead, reasons)


class GestureTrigger:
    """Turns a noisy per-frame pose signal into clean, deliberate triggers.

    * the pose has to be held for ``hold_s`` seconds (brief dropouts up to
      ``grace_s`` are forgiven so a single missed frame doesn't reset it)
    * after firing, the hand must leave the pose for ``rearm_s`` before the
      next trigger can start (lower your hand, then do it again)
    """

    def __init__(self, hold_s: float = 0.5, grace_s: float = 0.25, rearm_s: float = 0.3):
        self.hold_s, self.grace_s, self.rearm_s = hold_s, grace_s, rearm_s
        self._since: Optional[float] = None
        self._last_seen: Optional[float] = None
        self._armed = True
        self._released_since: Optional[float] = None

    def reset(self) -> None:
        self.__init__(self.hold_s, self.grace_s, self.rearm_s)

    def progress(self, now: float) -> float:
        if self._since is None or not self._armed:
            return 0.0
        return min(1.0, (now - self._since) / self.hold_s)

    def update(self, pose: bool, now: float) -> bool:
        if not self._armed:
            if pose:
                self._released_since = None
            else:
                self._released_since = self._released_since or now
                if now - self._released_since >= self.rearm_s:
                    self._armed, self._released_since = True, None
            return False

        if pose:
            self._last_seen = now
            if self._since is None:
                self._since = now
            if now - self._since >= self.hold_s:
                self._since, self._armed = None, False
                return True
        elif self._since is not None and (self._last_seen is None or now - self._last_seen > self.grace_s):
            self._since = None
        return False
