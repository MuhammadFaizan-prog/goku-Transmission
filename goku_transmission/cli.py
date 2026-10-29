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
    p.add_argument("--anim", type=float, default=0.9, help="teleport animation length in seconds")
    p.add_argument("--background", help="use this image as the empty-room plate instead of learning it")
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
              "gone": "  (you are invisible)", "present": "  (you are visible)"}

    def handle(e: str):
        print(f"[goku] {labels.get(e, e)}")
        if e in ("vanishing", "appearing") and not mute:
            sound.play()
    return handle


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
    vision = Vision(video=video)
    eng = GokuEngine(vision, hold_s=args.hold, anim_s=args.anim, on_event=_events(args.mute), debug=args.debug)
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
    eng = _build_engine(args)
    vcam = _open_virtual(args, w, h)
    print("\n  Pose: index + middle finger together on your forehead, hold ~%.1fs.\n"
          "  Do it again to come back.  Keys (preview window): T toggle  B relearn room  D debug  Q quit\n"
          "  Tip: step out of frame for 2-3 seconds once, so the room behind you is learned perfectly.\n"
          % args.hold)
    if not args.no_preview:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    last_cov, last_dbg = -1, 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[camera] lost the webcam feed.")
                break
            now = time.monotonic()
            out = eng.process(frame, now)

            cov = int(eng.bg.coverage() * 100)
            if cov // 10 != last_cov // 10:
                print(f"[room] background learned: {cov}%")
                last_cov = cov
            if args.debug and now - last_dbg > 0.5 and eng.last_reasons:
                print(f"[debug] pose={eng.last_pose} hold={eng.trigger.progress(now):.0%} {eng.last_reasons}")
                last_dbg = now

            if vcam is not None:
                vcam.send(out)
            if not args.no_preview:
                cv2.imshow(WINDOW, cv2.flip(out, 1))   # mirrored like a normal selfie view
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("t"):
                    eng.toggle(now)
                if k == ord("b"):
                    eng.bg = type(eng.bg)()
                    print("[room] relearning background...")
                if k == ord("d"):
                    args.debug = not args.debug
            if vcam is not None:
                vcam.sleep_until_next_frame()
    except KeyboardInterrupt:
        pass
    finally:
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
    v = Vision(video=False)
    frame = np.full((360, 640, 3), 120, np.uint8)
    obs = v.observe(frame)
    print(f"  models ok  hands={len(obs.hands)} face={obs.face is not None} mask={obs.person_mask.shape}")
    mask = np.zeros((360, 640), np.float32)
    mask[80:330, 260:380] = 1
    teleport_frame(frame, frame, mask, 0.5)
    print("  effects ok")
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
