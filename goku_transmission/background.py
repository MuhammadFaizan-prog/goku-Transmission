"""The empty-room plate that replaces you when you teleport.

Learns the room from every pixel the segmenter says is *not* you, so the plate
stays fresh when the light changes. Pixels that have never been seen (they've
always been behind you) are filled with OpenCV inpainting, so it still works
if you never step out of frame - stepping out for a few seconds just makes it
perfect.
"""
from __future__ import annotations

import cv2
import numpy as np


class BackgroundModel:
    def __init__(self, learn_rate: float = 0.15, bg_threshold: float = 0.25, dilate_px: int = 15):
        self.learn_rate = learn_rate
        self.bg_threshold = bg_threshold
        self.dilate_px = dilate_px
        self.plate: np.ndarray | None = None
        self.seen: np.ndarray | None = None   # bool HxW: pixel observed as background at least once
        self._filled: np.ndarray | None = None
        self._dirty = True

    @property
    def ready(self) -> bool:
        return self.plate is not None

    def coverage(self) -> float:
        return 0.0 if self.seen is None else float(self.seen.mean())

    def set_plate(self, image: np.ndarray) -> None:
        self.plate = image.astype(np.float32)
        self.seen = np.ones(image.shape[:2], bool)
        self._dirty = True

    def background_mask(self, person_mask: np.ndarray) -> np.ndarray:
        """Pixels safely away from the person (mask dilated so hair/edges don't leak in)."""
        person = (person_mask > self.bg_threshold).astype(np.uint8)
        if self.dilate_px > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.dilate_px, self.dilate_px))
            person = cv2.dilate(person, k)
        return person == 0

    def update(self, frame: np.ndarray, person_mask: np.ndarray) -> None:
        bg = self.background_mask(person_mask)
        f = frame.astype(np.float32)
        if self.plate is None:
            self.plate = f.copy()
            self.seen = bg.copy()
        else:
            new = bg & ~self.seen
            self.plate[new] = f[new]                      # first sighting: take it as-is
            old = bg & self.seen
            self.plate[old] += self.learn_rate * (f[old] - self.plate[old])  # then track slowly
            self.seen |= bg
        self._dirty = True

    def render(self) -> np.ndarray:
        """Plate as uint8 BGR with never-seen holes inpainted."""
        if self.plate is None:
            raise RuntimeError("background not learned yet")
        if self._dirty or self._filled is None:
            img = np.clip(self.plate, 0, 255).astype(np.uint8)
            holes = (~self.seen).astype(np.uint8)
            if holes.any():
                # inpaint at half resolution - fast, and the room behind you is low-detail anyway
                h, w = holes.shape
                small = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
                small_h = cv2.resize(holes, (w // 2, h // 2), interpolation=cv2.INTER_NEAREST)
                filled = cv2.inpaint(small, small_h, 5, cv2.INPAINT_TELEA)
                filled = cv2.resize(filled, (w, h), interpolation=cv2.INTER_LINEAR)
                img = np.where(holes[..., None].astype(bool), filled, img)
            self._filled, self._dirty = img, False
        return self._filled
