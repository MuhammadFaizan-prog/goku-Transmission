"""The empty-room plate that replaces you when you teleport.

Two ways it gets the room:

1. **Clean snapshot (best).** Whenever the camera sees nobody for ~0.7s
   (you stepped out of frame), that frame becomes the plate. From then on it
   only drifts slowly to follow lighting changes. No ghosts, no holes.
2. **Live learning (fallback).** Until a clean snapshot exists, every pixel
   well away from you is copied in, and pixels that have always been behind
   you are inpainted (cheaply, at low resolution, at most once a second).

Learning pauses while a hand is moving (``frozen=True``), because the person
mask lags fast hands and would otherwise paint a ghost arm into the room.
"""
from __future__ import annotations

import cv2
import numpy as np


class BackgroundModel:
    def __init__(self, drift_rate: float = 0.02, learn_threshold: float = 0.15,
                 empty_fraction: float = 0.004, empty_hold_s: float = 0.7,
                 inpaint_every_s: float = 1.0):
        self.drift_rate = drift_rate
        self.learn_threshold = learn_threshold
        self.empty_fraction = empty_fraction
        self.empty_hold_s = empty_hold_s
        self.inpaint_every_s = inpaint_every_s
        self.plate: np.ndarray | None = None      # float32 HxWx3
        self.seen: np.ndarray | None = None       # uint8 HxW, 255 = observed as room
        self.clean = False                        # True once a person-free snapshot was taken
        self._empty_since: float | None = None
        self._filled: np.ndarray | None = None
        self._filled_t = -1e9
        self._dirty = True

    # -- state ----------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.plate is not None

    def coverage(self) -> float:
        if self.seen is None:
            return 0.0
        return 1.0 if self.clean else float(np.count_nonzero(self.seen)) / self.seen.size

    def set_plate(self, image: np.ndarray) -> None:
        self.plate = image.astype(np.float32)
        self.seen = np.full(image.shape[:2], 255, np.uint8)
        self.clean = True
        self._filled, self._dirty = image.copy(), False

    def reset(self) -> None:
        self.__init__(self.drift_rate, self.learn_threshold, self.empty_fraction,
                      self.empty_hold_s, self.inpaint_every_s)

    # -- learning -------------------------------------------------------
    def room_mask(self, person_mask: np.ndarray) -> np.ndarray:
        """uint8 mask (255 = safely room), person grown by ~3% of frame width."""
        h, w = person_mask.shape
        sw, sh = max(16, w // 4), max(9, h // 4)        # dilate at quarter res: ~16x cheaper
        small = cv2.resize(person_mask, (sw, sh), interpolation=cv2.INTER_AREA)
        person = (small > self.learn_threshold).astype(np.uint8)
        k = max(3, int(sw * 0.03)) | 1
        person = cv2.dilate(person, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        room = np.where(person > 0, 0, 255).astype(np.uint8)
        return cv2.resize(room, (w, h), interpolation=cv2.INTER_NEAREST)

    def update(self, frame: np.ndarray, person_mask: np.ndarray, now: float = 0.0, frozen: bool = False) -> None:
        # 1) clean snapshot when nobody is in frame for a moment
        if float((person_mask > 0.5).mean()) < self.empty_fraction:
            if self._empty_since is None:
                self._empty_since = now
            if now - self._empty_since >= self.empty_hold_s:
                self.set_plate(frame)          # trusted: nobody here for a while
                return
            if self.plate is None:
                self.set_plate(frame)          # usable right away, not yet trusted
                self.clean = False
                return
        else:
            self._empty_since = None

        if frozen and self.plate is not None:
            return
        room = self.room_mask(person_mask)
        if self.plate is None:
            self.plate = frame.astype(np.float32)
            self.seen = room.copy()
            self._dirty = True
            return
        if self.clean:
            cv2.accumulateWeighted(frame, self.plate, self.drift_rate, mask=room)   # follow lighting only
            self._dirty = True
            return
        new = cv2.bitwise_and(room, cv2.bitwise_not(self.seen))
        if cv2.countNonZero(new):
            cv2.copyTo(frame.astype(np.float32), new, self.plate)
        cv2.accumulateWeighted(frame, self.plate, 0.1, mask=room)
        cv2.bitwise_or(self.seen, room, dst=self.seen)
        self._dirty = True

    # -- output ---------------------------------------------------------
    def render(self, now: float = 0.0) -> np.ndarray:
        """Plate as uint8 BGR, never-seen holes inpainted (re-done at most once a second)."""
        if self.plate is None:
            raise RuntimeError("background not learned yet")
        if not self._dirty and self._filled is not None:
            return self._filled
        img = cv2.convertScaleAbs(self.plate)
        holes = cv2.bitwise_not(self.seen)
        if self.clean or not holes.any():
            self._filled, self._dirty = img, False
            return img
        if self._filled is not None and now - self._filled_t < self.inpaint_every_s:
            # reuse last inpaint for the holes, fresh pixels everywhere else
            cv2.copyTo(self._filled, holes, img)
            return img
        h, w = holes.shape
        sw, sh = max(32, w // 4), max(18, h // 4)
        small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
        small_h = cv2.resize(holes, (sw, sh), interpolation=cv2.INTER_NEAREST)
        filled = cv2.inpaint(small, small_h, 3, cv2.INPAINT_TELEA)
        filled = cv2.GaussianBlur(cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 3)
        cv2.copyTo(filled, holes, img)
        self._filled, self._filled_t, self._dirty = img, now, False
        return img
