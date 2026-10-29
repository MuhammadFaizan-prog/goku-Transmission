"""MediaPipe Tasks wrapper: hands, face and person segmentation in one call."""
from __future__ import annotations

import os
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

MODEL_URLS = {
    "hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "blaze_face_short_range.tflite":
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
    "selfie_segmenter_landscape.tflite":
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter_landscape/float16/latest/selfie_segmenter_landscape.tflite",
}


def models_dir() -> Path:
    d = Path(os.environ.get("GOKU_MODELS_DIR", Path(__file__).resolve().parent.parent / "models"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_models(verbose: bool = True) -> Path:
    d = models_dir()
    for name, url in MODEL_URLS.items():
        p = d / name
        if p.exists() and p.stat().st_size > 10_000:
            continue
        if verbose:
            print(f"[models] downloading {name} ...")
        tmp = p.with_suffix(p.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(p)
    return d


@dataclass
class Observation:
    hands: List[list]                         # each: 21 (x, y) normalized points
    face: Optional[Tuple[float, float, float, float]]  # x, y, w, h normalized
    person_mask: np.ndarray                   # float32 HxW, 1 = person


class Vision:
    """Runs the three models on a BGR frame. ``video=True`` uses MediaPipe's
    tracking mode (faster and steadier for a live stream)."""

    def __init__(self, video: bool = True, hand_conf: float = 0.5, face_conf: float = 0.5, max_width: int = 640):
        self.max_width = max_width
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision as V

        self.mp = mp
        d = ensure_models()
        mode = V.RunningMode.VIDEO if video else V.RunningMode.IMAGE
        self.video = video
        B = mpt.BaseOptions
        self.hands = V.HandLandmarker.create_from_options(V.HandLandmarkerOptions(
            base_options=B(model_asset_path=str(d / "hand_landmarker.task")), running_mode=mode,
            num_hands=1, min_hand_detection_confidence=hand_conf, min_hand_presence_confidence=hand_conf,
            min_tracking_confidence=0.5))
        self.faces = V.FaceDetector.create_from_options(V.FaceDetectorOptions(
            base_options=B(model_asset_path=str(d / "blaze_face_short_range.tflite")), running_mode=mode,
            min_detection_confidence=face_conf))
        self.seg = V.ImageSegmenter.create_from_options(V.ImageSegmenterOptions(
            base_options=B(model_asset_path=str(d / "selfie_segmenter_landscape.tflite")), running_mode=mode,
            output_confidence_masks=True, output_category_mask=False))
        self._t0 = time.monotonic()
        self._n = 0
        self._last_face = None
        self._last_ts = -1

    def _ts(self, ts_ms: Optional[int] = None) -> int:
        ts = int((time.monotonic() - self._t0) * 1000) if ts_ms is None else int(ts_ms)
        ts = max(ts, self._last_ts + 1)   # MediaPipe needs strictly increasing timestamps
        self._last_ts = ts
        return ts

    def segment(self, frame_bgr: np.ndarray) -> np.ndarray:
        img = self._image(frame_bgr)
        r = self.seg.segment_for_video(img, self._ts()) if self.video else self.seg.segment(img)
        return self._mask(r, frame_bgr.shape[:2])

    def _image(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

    @staticmethod
    def _mask(r, shape) -> np.ndarray:
        m = r.confidence_masks[0].numpy_view()
        m = np.squeeze(m).astype(np.float32)
        if m.shape != shape:
            m = cv2.resize(m, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
        return m

    def observe(self, frame_bgr: np.ndarray, ts_ms: Optional[int] = None) -> Observation:
        h, w = frame_bgr.shape[:2]
        small = frame_bgr
        if w > self.max_width:   # models work at <=256-ish px anyway; this just saves copying
            small = cv2.resize(frame_bgr, (self.max_width, int(h * self.max_width / w)), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        img = self._image(small)
        ts = self._ts(ts_ms)
        self._n += 1
        run_face = not self.video or self._n % 3 == 1   # the face barely moves; every 3rd frame is plenty
        if self.video:
            hr = self.hands.detect_for_video(img, ts)
            fr = self.faces.detect_for_video(img, ts) if run_face else None
            sr = self.seg.segment_for_video(img, ts)
        else:
            hr, fr, sr = self.hands.detect(img), self.faces.detect(img), self.seg.segment(img)

        hands = [[(p.x, p.y) for p in lm] for lm in hr.hand_landmarks]
        face = self._last_face if fr is None else None
        if fr is not None and fr.detections:
            best = max(fr.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
            b = best.bounding_box
            face = (b.origin_x / sw, b.origin_y / sh, b.width / sw, b.height / sh)
        if fr is not None:
            self._last_face = face
        return Observation(hands, face, self._mask(sr, (h, w)))

    def close(self) -> None:
        for name in ("hands", "faces", "seg"):
            t = getattr(self, name, None)
            if t is not None:
                try:
                    t.close()
                except Exception:
                    pass
                setattr(self, name, None)

    def __del__(self):
        self.close()


class AsyncVision:
    """Runs a :class:`Vision` on its own thread so the camera feed never waits for the models.

    ``observe()`` hands the newest frame to the worker and immediately returns the most
    recent finished :class:`Observation` (one frame behind at most). Frames the worker
    can't keep up with are skipped, never queued, so lag can't build up. Same interface
    as :class:`Vision`, so the engine doesn't know the difference.
    """

    def __init__(self, vision: "Vision"):
        import threading
        self.vision = vision
        self._cv = threading.Condition()
        self._job = None          # (frame, ts) waiting to be processed (newest wins)
        self._obs: Optional[Observation] = None
        self._done = 0
        self._running = True
        self._t = threading.Thread(target=self._run, name="goku-vision", daemon=True)
        self._t.start()

    def __getattr__(self, name):   # segment(), video, max_width ... pass straight through
        return getattr(self.vision, name)

    def _run(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._job is not None or not self._running)
                if not self._running:
                    return
                frame, ts = self._job
                self._job = None
            try:
                obs = self.vision.observe(frame, ts)
            except Exception as e:  # noqa: BLE001 - keep the feed alive, report once in a while
                print(f"[vision] frame skipped: {e}")
                continue
            with self._cv:
                self._obs = obs
                self._done += 1
                self._cv.notify_all()

    def observe(self, frame_bgr: np.ndarray, ts_ms: Optional[int] = None) -> Observation:
        with self._cv:
            self._job = (frame_bgr.copy(), ts_ms)
            self._cv.notify_all()
            if self._obs is None:   # very first frame: wait so the engine starts with real data
                self._cv.wait_for(lambda: self._obs is not None or not self._running, timeout=5.0)
            obs = self._obs
        if obs is None:
            h, w = frame_bgr.shape[:2]
            return Observation([], None, np.zeros((h, w), np.float32))
        mh, mw = obs.person_mask.shape
        if (mh, mw) != frame_bgr.shape[:2]:   # resolution changed under us
            m = cv2.resize(obs.person_mask, (frame_bgr.shape[1], frame_bgr.shape[0]))
            obs = Observation(obs.hands, obs.face, m)
        return obs

    def close(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        self._t.join(timeout=2.0)
        self.vision.close()
