"""The state machine that ties detection, background and effects together.

    PRESENT --pose held--> VANISHING --anim done--> GONE
       ^                                              |
       +----anim done---- APPEARING <--pose held------+
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional

import numpy as np

from .background import BackgroundModel
from .effects import teleport_frame
from .gesture import GestureTrigger, PoseConfig, check_pose


class State(str, Enum):
    PRESENT = "present"
    VANISHING = "vanishing"
    GONE = "gone"
    APPEARING = "appearing"


class GokuEngine:
    def __init__(self, vision, *, hold_s: float = 0.5, anim_s: float = 0.9, face_memory_s: float = 2.0,
                 pose_cfg: PoseConfig = PoseConfig(), on_event: Optional[Callable[[str], None]] = None,
                 debug: bool = False):
        self.vision = vision
        self.bg = BackgroundModel()
        self.trigger = GestureTrigger(hold_s=hold_s)
        self.anim_s = anim_s
        self.face_memory_s = face_memory_s
        self.pose_cfg = pose_cfg
        self.on_event = on_event or (lambda e: None)
        self.debug = debug
        self.state = State.PRESENT
        self._anim_start = 0.0
        self._face = None
        self._face_t = -1e9
        self._frozen_mask: Optional[np.ndarray] = None
        self.last_pose = False
        self.last_reasons: dict = {}

    # -- controls -------------------------------------------------------
    def toggle(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if self.state == State.PRESENT:
            self._start(State.VANISHING, now)
        elif self.state == State.GONE:
            self._start(State.APPEARING, now)

    def set_background(self, image: np.ndarray) -> None:
        self.bg.set_plate(image)

    def _start(self, state: State, now: float) -> None:
        self.state, self._anim_start = state, now
        self._frozen_mask = None
        self.on_event(state.value)

    # -- per frame ------------------------------------------------------
    def process(self, frame: np.ndarray, now: Optional[float] = None) -> np.ndarray:
        now = time.monotonic() if now is None else now
        obs = self.vision.observe(frame, int(now * 1000))

        # Learn the room only from pixels where you're not, and only while you're
        # visible as yourself (not mid-animation, when the mask is about to lie).
        if self.state in (State.PRESENT, State.GONE):
            self.bg.update(frame, obs.person_mask)

        if obs.face is not None:
            self._face, self._face_t = obs.face, now
        face = self._face if now - self._face_t <= self.face_memory_s else None

        pose = False
        for hand in obs.hands:
            r = check_pose(hand, face, self.pose_cfg)
            self.last_reasons = r.reasons
            if r.is_pose:
                pose = True
                break
        self.last_pose = pose

        if self.state in (State.PRESENT, State.GONE) and self.trigger.update(pose, now):
            self._start(State.VANISHING if self.state == State.PRESENT else State.APPEARING, now)
        elif self.state in (State.VANISHING, State.APPEARING):
            self.trigger.update(pose, now)   # keep tracking so the hand has to come down first

        return self._compose(frame, obs.person_mask, now)

    def _compose(self, frame: np.ndarray, mask: np.ndarray, now: float) -> np.ndarray:
        if not self.bg.ready:
            return frame
        plate = self.bg.render()
        if self.state == State.PRESENT:
            return frame
        if self.state == State.GONE:
            return plate.copy()

        t = (now - self._anim_start) / self.anim_s
        if t >= 1.0:
            done = State.GONE if self.state == State.VANISHING else State.PRESENT
            self.state = done
            self.on_event(done.value)
            return plate.copy() if done == State.GONE else frame
        progress = t if self.state == State.VANISHING else 1.0 - t
        return teleport_frame(frame, plate, mask, progress)
