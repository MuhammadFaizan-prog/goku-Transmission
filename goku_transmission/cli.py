"""Command line entry point.

    python -m goku_transmission                       # live: webcam -> virtual camera + preview
    python -m goku_transmission --input in.mp4 --output out.mp4   # render a recorded clip
    python -m goku_transmission --self-test           # check models + camera stack, no webcam needed
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np

from . import __version__, sound
from .engine import GokuEngine, State
from .gesture import PoseConfig

WINDOW = "Goku Transmission - preview"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="goku-transmission",
                                description="Teleport out of video calls with Goku's Instant Transmission pose.")
    p.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--hold", type=float, default=0.5, help="seconds to hold the pose (default 0.5)")
    p.add_argument("--anim", type=float, default=0.7, help="teleport animation length in seconds (default 0.7)")
    p.add_argument("--sound", help="play this .wav on teleport instead of the built-in one")
    p.add_argument("--background", help="use this image as the empty-room plate instead of learning it")
    p.add_argument("--allow-guess", action="store_true",
                   help="allow teleporting before the empty room was captured (the room behind you is guessed)")
    p.add_argument("--no-virtual", action="store_true", help="preview only, don't open a virtual camera")
    p.add_argument("--virtual-backend", help="pyvirtualcam backend: obs, unitycapture, v4l2loopback, akvcam")
    p.add_argument("--no-preview", action="store_true", help="no preview window")
    p.add_argument("--mute", action="store_true", help="no teleport sound")
    p.add_argument("--debug", action="store_true", help="print gesture diagnostics to the terminal")
    p.add_argument("--input", help="process a video file instead of the webcam")
    p.add_argument("--output", help="where to write the processed video (with --input)")
    p.add_argument("--self-test", action="store_true", help="verify the install without a webcam")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _events(mute: bool):
    labels = {"vanishing": "Instant Transmission... gone", "appearing": "...and back",
              "gone": "  (you are invisible)", "present": "  (you are visible)",
              "need_room": "Pose seen, but I haven't seen your empty room yet. Step out of frame for 1 second, then try again."}

    def handle(e: str):
        print(f"[goku] {labels.get(e, e)}")
        if e in ("vanishing", "appearing") and not mute:
            sound.play()
    return handle


def _banner(img, text, color):
    """Preview-only hint (never sent to the meeting)."""
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, 0), (w, 44), color, -1)
    cv2.putText(img, text, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)


def _hold_bar(img, p):
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, h - 10), (int(w * p), h), (255, 230, 120), -1)


def _open_virtual(args, w, h):
    if args.no_virtual:
        return None
    try:
        import pyvirtualcam
        cam = pyvirtualcam.Camera(width=w, height=h, fps=args.fps, fmt=pyvirtualcam.PixelFormat.BGR,
                                  backend=args.virtual_backend)
        print(f"[camera] virtual camera ready: {cam.device}  -> pick it in Zoom / Meet / Teams")
        return cam
    except Exception as e:  # noqa: BLE001
        print(f"[camera] no virtual camera ({e}).\n"
              "         Install OBS Studio 28+ (it ships 'OBS Virtual Camera') and make sure OBS's own\n"
              "         'Start Virtual Camera' is OFF. Continuing with the preview only.")
        return None


class LatestFrame:
    """Reads the webcam on its own thread and always hands out the newest frame,
    so a slow frame never makes the feed lag behind or look frozen."""

    def __init__(self, cap):
        import threading
        self.cap, self.frame, self.ok, self.seq = cap, None, True, 0
        self._cv = threading.Condition()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while self.ok:
            ok, f = self.cap.read()
            with self._cv:
                self.ok, self.frame = ok, f
                self.seq += 1
                self._cv.notify_all()

    def read(self, last_seq):
        with self._cv:
            self._cv.wait_for(lambda: self.seq != last_seq or not self.ok, timeout=2.0)
            return self.ok and self.frame is not None, self.frame, self.seq

    def stop(self):
        self.ok = False
        self._t.join(timeout=1.0)


def _open_webcam(args):
    backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
    cap = cv2.VideoCapture(args.camera, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    if not cap.isOpened():
        raise SystemExit(f"[camera] can't open webcam {args.camera}. Try --camera 1, and close other apps using it.")
    return cap


def _build_engine(args, video=True):
    from .vision import Vision
    sound.set_custom(args.sound)
    vision = Vision(video=video)
    eng = GokuEngine(vision, hold_s=args.hold, anim_s=args.anim, on_event=_events(args.mute), debug=args.debug,
                     require_clean_room=not args.allow_guess)
    if args.background:
        img = cv2.imread(args.background)
        if img is None:
            raise SystemExit(f"can't read --background {args.background}")
        eng.set_background(cv2.resize(img, (args.width, args.height)))
    return eng


def run_live(args) -> int:
    cap = _open_webcam(args)
    ok, frame = cap.read()
    if not ok:
        raise SystemExit("[camera] webcam opened but returned no frames.")
    h, w = frame.shape[:2]
    reader = LatestFrame(cap)
    seq = 0
    eng = _build_engine(args)
    vcam = _open_virtual(args, w, h)
    print("\n  1. Step out of frame for 1 second so the empty room is captured.\n"
          "  2. Sit down. Index + middle finger together on your forehead, hold ~%.1fs.\n"
          "  3. Lower your hand, do it again to come back.\n"
          "  Keys (preview window): T teleport  B recapture room  D debug  Q quit\n" % args.hold)
    if not args.no_preview:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    last_dbg, room_ok, captured_at = 0.0, None, -1e9
    n_frames, t_fps = 0, time.monotonic()
    try:
        while True:
            ok, frame, seq = reader.read(seq)
            if not ok:
                print("[camera] lost the webcam feed.")
                break
            now = time.monotonic()
            out = eng.process(frame, now)

            if eng.bg.clean != room_ok:
                room_ok = eng.bg.clean
                print("[room] empty room captured. Sit down and teleport!" if room_ok else
                      "[room] step out of frame for 1 second so I can capture the empty room...")
                if room_ok:
                    captured_at = now
            n_frames += 1
            if args.debug and now - last_dbg > 0.5:
                fps = n_frames / max(1e-6, now - t_fps)
                n_frames, t_fps = 0, now
                print(f"[debug] {fps:4.1f} fps  state={eng.state.value}  pose={eng.last_pose} "
                      f"hold={eng.trigger.progress(now):.0%} {eng.last_reasons}")
                last_dbg = now

            if vcam is not None:
                vcam.send(out)
            if not args.no_preview:
                view = cv2.flip(out, 1)   # mirrored like a normal selfie view (the meeting gets the unmirrored one)
                if not eng.bg.clean:
                    _banner(view, "Step out of frame for 1 sec to capture your room", (40, 40, 220))
                elif now - captured_at < 2.5:
                    _banner(view, "Room captured - two fingers on your forehead to teleport", (60, 160, 60))
                elif eng.trigger.progress(now) > 0 and eng.state in (State.PRESENT, State.GONE):
                    _hold_bar(view, eng.trigger.progress(now))
                cv2.imshow(WINDOW, view)
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("t"):
                    eng.toggle(now)
                if k == ord("b"):
                    eng.bg.reset()
                    print("[room] relearning background...")
                if k == ord("d"):
                    args.debug = not args.debug
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        cap.release()
        if vcam is not None:
            vcam.close()
        cv2.destroyAllWindows()
        eng.vision.close()
    return 0


def run_file(args) -> int:
    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        raise SystemExit(f"can't open {args.input}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    args.width, args.height = w, h
    eng = _build_engine(args)
    out = cv2.VideoWriter(args.output or "goku_out.mp4", cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        res = eng.process(frame, t)
        if args.debug and eng.last_reasons:
            print(f"[debug] t={t:5.2f}s state={eng.state.value:9s} pose={eng.last_pose} {eng.last_reasons}")
        out.write(res)
        i += 1
    out.release()
    cap.release()
    eng.vision.close()
    print(f"[file] wrote {i} frames to {args.output or 'goku_out.mp4'}")
    return 0


def self_test() -> int:
    print(f"goku-transmission {__version__}")
    from .vision import Vision
    from .effects import teleport_frame
    from .sound import sound_path
    v = Vision(video=False)
    frame = np.full((360, 640, 3), 120, np.uint8)
    obs = v.observe(frame)
    print(f"  models ok  hands={len(obs.hands)} face={obs.face is not None} mask={obs.person_mask.shape}")
    mask = np.zeros((360, 640), np.float32)
    mask[80:330, 260:380] = 1
    teleport_frame(frame, frame, mask, 0.5)
    print("  effects ok")
    print(f"  teleport sound: {sound_path()}")
    try:
        import pyvirtualcam  # noqa: F401
        print("  pyvirtualcam installed")
    except Exception as e:  # noqa: BLE001
        print(f"  pyvirtualcam missing: {e}")
    v.close()
    print("self-test passed")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.input:
        return run_file(args)
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
