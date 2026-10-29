"""Engine, background plate and effects with a fake detector - no model needed."""
import numpy as np

from goku_transmission.background import BackgroundModel
from goku_transmission.effects import teleport_frame
from goku_transmission.engine import GokuEngine, State
from goku_transmission.vision import Observation
from tests.test_gesture import FACE, make_hand

H, W = 180, 320
ROOM = np.zeros((H, W, 3), np.uint8)
ROOM[:] = (60, 140, 200)
ROOM[:, ::20] = (30, 30, 30)       # some texture so we can tell it apart
PERSON_BOX = (slice(40, 180), slice(130, 200))


def frame_with_person():
    f = ROOM.copy()
    f[PERSON_BOX] = (200, 200, 255)
    return f


def person_mask():
    m = np.zeros((H, W), np.float32)
    m[PERSON_BOX] = 1.0
    return m


class FakeVision:
    """Returns a scripted observation each frame."""
    def __init__(self):
        self.pose = False
        self.present = True

    def observe(self, frame, ts_ms=None):
        hands = [make_hand()] if self.pose else []
        return Observation(hands, FACE if self.present else None,
                           person_mask() if self.present else np.zeros((H, W), np.float32))


def test_background_learns_room_and_inpaints_hidden_pixels():
    bg = BackgroundModel(dilate_px=0)
    bg.update(frame_with_person(), person_mask())
    plate = bg.render()
    # pixels behind the person were never seen -> inpainted, no person colour left
    assert not np.any(np.all(plate[PERSON_BOX] == (200, 200, 255), axis=-1))
    # visible room is copied exactly
    assert np.array_equal(plate[:, :100], ROOM[:, :100])
    # once the person leaves, the hidden area is learned for real
    bg.update(ROOM, np.zeros((H, W), np.float32))
    assert np.array_equal(bg.render(), ROOM)


def test_effect_endpoints():
    f, m = frame_with_person(), person_mask()
    assert np.array_equal(teleport_frame(f, ROOM, m, 0.0), f)
    end = teleport_frame(f, ROOM, m, 1.0)
    assert np.abs(end.astype(int) - ROOM.astype(int)).mean() < 2   # fully gone
    mid = teleport_frame(f, ROOM, m, 0.5)
    assert mid.shape == f.shape and mid.dtype == np.uint8


def test_full_teleport_cycle():
    v = FakeVision()
    events = []
    eng = GokuEngine(v, hold_s=0.5, anim_s=0.9, on_event=events.append)
    eng.set_background(ROOM)
    t, dt = 0.0, 1 / 30

    def step(n):
        nonlocal t
        out = None
        for _ in range(n):
            out = eng.process(frame_with_person(), t)
            t += dt
        return out

    assert np.array_equal(step(10), frame_with_person())       # normal webcam
    v.pose = True
    step(20)                                                     # hold 0.66s -> fires
    assert eng.state == State.VANISHING
    v.pose = False
    out = step(40)                                               # animation finishes
    assert eng.state == State.GONE
    assert np.array_equal(out, ROOM)                             # meeting sees an empty room
    v.pose = True
    step(20)
    assert eng.state == State.APPEARING
    v.pose = False
    out = step(40)
    assert eng.state == State.PRESENT
    assert np.array_equal(out, frame_with_person())
    assert events == ["vanishing", "gone", "appearing", "present"]


def test_holding_pose_does_not_bounce_back():
    v = FakeVision()
    eng = GokuEngine(v, hold_s=0.5, anim_s=0.5)
    eng.set_background(ROOM)
    v.pose = True
    for i in range(200):  # hold the pose for ~6.6 seconds straight
        eng.process(frame_with_person(), i / 30)
    assert eng.state == State.GONE


def test_manual_toggle():
    v = FakeVision()
    eng = GokuEngine(v, anim_s=0.3)
    eng.set_background(ROOM)
    eng.process(frame_with_person(), 0.0)
    eng.toggle(0.0)
    for i in range(1, 20):
        eng.process(frame_with_person(), i / 30)
    assert eng.state == State.GONE
