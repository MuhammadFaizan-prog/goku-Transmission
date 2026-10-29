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
    bg = BackgroundModel()
    bg.update(frame_with_person(), person_mask(), 0.0)
    plate = bg.render(0.0)
    # pixels behind the person were never seen -> inpainted, no person colour left
    assert not np.any(np.all(plate[PERSON_BOX] == (200, 200, 255), axis=-1))
    # visible room far from the person is copied exactly
    assert np.array_equal(plate[:, :90], ROOM[:, :90])
    assert not bg.clean


def test_clean_snapshot_when_room_is_empty():
    bg = BackgroundModel(empty_hold_s=0.5)
    bg.update(frame_with_person(), person_mask(), 0.0)
    for i in range(1, 25):                       # you step out for 0.8s
        bg.update(ROOM, np.zeros((H, W), np.float32), i / 30)
    assert bg.clean and bg.coverage() == 1.0
    assert np.array_equal(bg.render(1.0), ROOM)


def test_no_ghost_after_clean_snapshot():
    bg = BackgroundModel()
    bg.set_plate(ROOM)
    for i in range(300):                         # you sit there for 10s
        bg.update(frame_with_person(), person_mask(), i / 30)
    diff = np.abs(bg.render(10.0).astype(int) - ROOM.astype(int))
    assert diff[PERSON_BOX].max() == 0           # nothing of you leaked into the room


def test_arm_the_mask_missed_never_leaks_into_room():
    """Real bug from a Windows test: segmenter missed a raised arm and it got painted into the room."""
    bg = BackgroundModel()
    bg.set_plate(ROOM)
    arm = (slice(20, 120), slice(40, 80))        # far outside the mask, so it isn't covered by dilation
    for i in range(300):
        f = frame_with_person()
        f[arm] = (20, 90, 160)
        bg.update(f, person_mask(), i / 30)
    diff = np.abs(bg.render(10.0).astype(int) - ROOM.astype(int))
    assert diff[arm].max() == 0


def test_lighting_still_followed_after_clean_snapshot():
    bg = BackgroundModel()
    bg.set_plate(ROOM)
    dimmer = np.clip(ROOM.astype(int) - 8, 0, 255).astype(np.uint8)
    for i in range(300):
        bg.update(dimmer, np.zeros((H, W), np.float32) + 0.0, i / 30, someone_seen=True)
    assert np.abs(bg.render(10.0).astype(int) - dimmer.astype(int)).max() <= 1


def test_no_snapshot_while_face_is_visible():
    """A washed-out webcam can give an empty mask while you sit there; a face means not empty."""
    bg = BackgroundModel(empty_hold_s=0.5)
    for i in range(60):
        bg.update(frame_with_person(), np.zeros((H, W), np.float32), i / 30, someone_seen=True)
    assert not bg.clean


def test_learning_pauses_while_hands_move():
    bg = BackgroundModel()
    bg.update(frame_with_person(), person_mask(), 0.0)
    before = bg.plate.copy()
    noisy = np.full_like(ROOM, 7)
    bg.update(noisy, person_mask(), 0.1, frozen=True)
    assert np.array_equal(bg.plate, before)


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


def test_refuses_to_teleport_before_room_is_captured():
    v = FakeVision()
    events = []
    eng = GokuEngine(v, hold_s=0.5, on_event=events.append)
    v.pose = True
    for i in range(30):
        eng.process(frame_with_person(), i / 30)
    assert eng.state == State.PRESENT and events == ["need_room"]


def test_room_captured_when_user_steps_out_then_teleports():
    v = FakeVision()
    events = []
    eng = GokuEngine(v, hold_s=0.5, anim_s=0.5, on_event=events.append)
    t = 0.0
    v.present = False
    for _ in range(30):                     # 1s of empty room
        eng.process(ROOM, t)
        t += 1 / 30
    assert eng.bg.clean
    v.present, v.pose = True, True
    for _ in range(60):
        eng.process(frame_with_person(), t)
        t += 1 / 30
    assert eng.state == State.GONE
    assert np.array_equal(eng.process(frame_with_person(), t), ROOM)
