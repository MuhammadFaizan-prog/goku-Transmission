"""Runs the real MediaPipe models. Skipped automatically if mediapipe or the
models can't load (e.g. a CI box with no OpenGL).

Optional: point GOKU_SAMPLE_VIDEO at a clip of yourself doing the pose to also
check that the full pipeline vanishes and comes back.
"""
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")


@pytest.fixture(scope="module")
def vision():
    try:
        from goku_transmission.vision import Vision
        v = Vision(video=False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"mediapipe unavailable here: {e}")
    yield v
    v.close()


def test_models_run_on_blank_frame(vision):
    obs = vision.observe(np.full((360, 640, 3), 127, np.uint8))
    assert obs.hands == [] and obs.face is None
    assert obs.person_mask.shape == (360, 640)
    assert obs.person_mask.max() < 0.5


@pytest.mark.skipif(not os.environ.get("GOKU_SAMPLE_VIDEO"), reason="set GOKU_SAMPLE_VIDEO to a clip")
def test_sample_video_teleports():
    from goku_transmission.engine import GokuEngine
    from goku_transmission.vision import Vision

    events = []
    eng = GokuEngine(Vision(video=True), on_event=events.append)
    cap = cv2.VideoCapture(os.environ["GOKU_SAMPLE_VIDEO"])
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        eng.process(f, i / fps)
        i += 1
    eng.vision.close()
    assert "vanishing" in events, "the pose was never recognised in the sample clip"
