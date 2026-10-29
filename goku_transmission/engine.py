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
from .effects import TeleportFX
from .gesture import GestureTrigger, PoseConfig, check_pose


class State(str, Enum):
    PRESENT = "present"
    VANISHING = "vanishing"
    GONE = "gone"
    APPEARING = "appearing"


class GokuEngine:
    def __init__(self, vision, *, hold_s: float = 0.5, anim_s: float = 0.3, face_memory_s: float = 2.0,
                 pose_cfg: PoseConfig = PoseConfig(), on_event: Optional[Callable[[str], None]] = None,
                 debug: bool = False, require_clean_room: bool = True):
        self.vision = vision
        self.bg = BackgroundModel()
        self.trigger = GestureTrigger(hold_s=hold_s)
        self.anim_s = anim_s
        self.fx = TeleportFX()
        self.face_memory_s = face_memory_s
        self.pose_cfg = pose_cfg
        self.on_event = on_event or (lambda e: None)
        self.debug = debug
        self.require_clean_room = require_clean_room
        self.state = State.PRESENT
        self._anim_start = 0.0
        self._face = None
        self._face_t = -1e9
        self.last_pose = False
        self.last_reasons: dict = {}
        self.capture_at: Optional[float] = None    # countdown room capture (key B)
        self.capture_blocked = False               # countdown over, but you're still in the picture
        self._clean_frames = 0
        self._blocked_since: Optional[float] = None
        self._told = False

    # -- controls -------------------------------------------------------
    @property
    def room_ready(self) -> bool:
        return self.bg.clean or not self.require_clean_room

    def toggle(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if self.state == State.PRESENT:
            self._start(State.VANISHING, now)
        elif self.state == State.GONE:
            self._start(State.APPEARING, now)

    def start_room_capture(self, now: Optional[float] = None, delay_s: float = 3.0) -> None:
        """Countdown capture: you get `delay_s` seconds to leave. When it ends the frame is only
        taken if no face, no hand and (almost) no body is visible for a few frames in a row;
        otherwise it waits, and the old room is kept. Also brings you back if you were 'gone'."""
        now = time.monotonic() if now is None else now
        self.state = State.PRESENT
        self.fx.reset()
        self.trigger = GestureTrigger(hold_s=self.trigger.hold_s)
        self.capture_at = now + delay_s
        self.capture_blocked = False
        self._clean_frames = 0
        self._blocked_since: Optional[float] = None
        self._told = False

    def capture_countdown(self, now: float) -> Optional[float]:
        return None if self.capture_at is None else max(0.0, self.capture_at - now)

    def set_background(self, image: np.ndarray) -> None:
        self.bg.set_plate(image)

    def _start(self, state: State, now: float) -> None:
        self.state, self._anim_start = state, now
        self.fx.reset()
        self.on_event(state.value)

    # -- per frame ------------------------------------------------------
    def process(self, frame: np.ndarray, now: Optional[float] = None) -> np.ndarray:
        now = time.monotonic() if now is None else now
        if self.capture_at is not None:
            obs = self.vision.observe(frame, int(now * 1000))
            if now >= self.capture_at:
                body = float((obs.person_mask > 0.5).mean())
                someone = bool(obs.hands) or obs.face is not None
                if self._blocked_since is None:
                    self._blocked_since = now
                # A face or hand is never accepted. A small body blob alone is accepted after
                # 4 extra seconds, since a chair or poster can fool the body model forever.
                clean = not someone and (body < 0.01 or (body < 0.08 and now - self._blocked_since > 4.0))
                self._clean_frames = self._clean_frames + 1 if clean else 0
                self.capture_blocked = self._clean_frames == 0
                if self._clean_frames >= 3:
                    self.capture_at = None
                    self.capture_blocked = False
                    self.bg.set_plate(frame)
                    self.on_event("room_captured")
                elif self.capture_blocked and self._clean_frames == 0 and not getattr(self, "_told", False):
                    self._told = True
                    self.on_event("room_blocked")
            return frame
        obs = self.vision.observe(frame, int(now * 1000))

        # Learn the room only from pixels where you're not, and only while you're
        # visible as yourself (not mid-animation, when the mask is about to lie).
        # Pause while hands are up: the mask lags fast hands and would paint a ghost arm into the room.
        # While GONE you are still sitting there, so the room plate is frozen: learning then
        # could only paint you back into it. Recapturing happens when you step out (or press B).
        if self.state == State.PRESENT:
            self.bg.update(frame, obs.person_mask, now, frozen=bool(obs.hands),
                           someone_seen=bool(obs.hands) or obs.face is not None)

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
            if self.state == State.PRESENT and self.require_clean_room and not self.bg.clean:
                # Without a real snapshot of the empty room the "gone" frame would be a smeared guess.
                self.on_event("need_room")
            else:
                self._start(State.VANISHING if self.state == State.PRESENT else State.APPEARING, now)
        elif self.state in (State.VANISHING, State.APPEARING):
            self.trigger.update(pose, now)   # keep tracking so the hand has to come down first

        return self._compose(frame, obs.person_mask, now)

    def _compose(self, frame: np.ndarray, mask: np.ndarray, now: float) -> np.ndarray:
        if not self.bg.ready:
            return frame
        plate = self.bg.render(now)
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
        if self.state == State.VANISHING:
            return self.fx.render(frame, plate, mask, t)
        return self.fx.render_appear(frame, plate, mask, t)
