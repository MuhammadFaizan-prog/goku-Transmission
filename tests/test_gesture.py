"""Pose rules on synthetic hands - no camera, no model."""
from goku_transmission.gesture import GestureTrigger, PoseConfig, check_pose

FACE = (0.40, 0.30, 0.20, 0.25)  # x, y, w, h


def make_hand(index=True, middle=True, ring=False, pinky=False, spread=0.0, tip_at=(0.50, 0.30)):
    """21 landmarks of a right hand pointing left (fingers sideways across the forehead).

    The wrist sits to the right; straight fingers reach far left, curled ones fold back.
    """
    tx, ty = tip_at
    wrist = (tx + 0.22, ty + 0.10)
    lm = [wrist] + [(wrist[0] - 0.02, wrist[1])] * 4   # thumb chain (unused)

    def finger(dy, straight):
        mcp = (wrist[0] - 0.08, wrist[1] - 0.05 + dy)
        pip = (wrist[0] - 0.13, wrist[1] - 0.07 + dy)
        dip = (wrist[0] - (0.18 if straight else 0.11), wrist[1] - 0.08 + dy)
        tip = (wrist[0] - (0.22 if straight else 0.08), wrist[1] - 0.09 + dy)
        return [mcp, pip, dip, tip]

    lm += finger(-0.010 - spread, index)
    lm += finger(+0.000 + spread, middle)
    lm += finger(+0.020, ring)
    lm += finger(+0.035, pinky)
    return lm


def test_goku_pose_detected():
    assert check_pose(make_hand(), FACE).is_pose


def test_v_sign_rejected_because_fingers_spread():
    r = check_pose(make_hand(spread=0.06), FACE)
    assert not r.is_pose and not r.reasons["together"]


def test_open_palm_rejected():
    r = check_pose(make_hand(ring=True, pinky=True), FACE)
    assert not r.is_pose and not r.reasons["others_curled"]


def test_single_finger_rejected():
    r = check_pose(make_hand(middle=False), FACE)
    assert not r.is_pose and not r.reasons["two_straight"]


def test_pose_away_from_forehead_rejected():
    r = check_pose(make_hand(tip_at=(0.10, 0.85)), FACE)
    assert not r.is_pose and not r.reasons["on_forehead"]


def test_no_face_falls_back_to_upper_frame():
    assert check_pose(make_hand(tip_at=(0.5, 0.3)), None).is_pose
    assert not check_pose(make_hand(tip_at=(0.3, 0.8)), None).is_pose


def test_accepts_mediapipe_style_objects():
    class P:
        def __init__(self, x, y):
            self.x, self.y = x, y
    assert check_pose([P(*p) for p in make_hand()], FACE).is_pose


def test_needs_21_points():
    assert not check_pose(make_hand()[:10], FACE).is_pose


# --- trigger timing -------------------------------------------------------

def run(trigger, signal, fps=30):
    """Feed a list of booleans, return the times it fired."""
    return [i / fps for i, s in enumerate(signal) if trigger.update(s, i / fps)]


def test_fires_after_hold():
    fired = run(GestureTrigger(hold_s=0.5), [True] * 30)
    assert len(fired) == 1 and abs(fired[0] - 0.5) < 0.04


def test_short_pose_does_not_fire():
    assert run(GestureTrigger(hold_s=0.5), [True] * 10 + [False] * 30) == []


def test_single_dropped_frame_is_forgiven():
    sig = [True] * 8 + [False] + [True] * 12
    assert len(run(GestureTrigger(hold_s=0.5), sig)) == 1


def test_must_lower_hand_before_next_trigger():
    t = GestureTrigger(hold_s=0.5, rearm_s=0.3)
    assert len(run(t, [True] * 120)) == 1            # holding forever fires once
    sig = [True] * 30 + [False] * 15 + [True] * 30   # fire, lower hand, fire again
    assert len(run(GestureTrigger(hold_s=0.5, rearm_s=0.3), sig)) == 2


def test_progress_reports_hold():
    t = GestureTrigger(hold_s=1.0)
    t.update(True, 0.0)
    t.update(True, 0.5)
    assert abs(t.progress(0.5) - 0.5) < 1e-6
