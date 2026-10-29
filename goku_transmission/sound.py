"""A short synthesized "zip" for the teleport. No audio files, no extra deps.

Played locally on your speakers only - it never goes into the meeting.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

_WAV: Path | None = None


def make_zip_wav(path: Path, rate: int = 22050) -> Path:
    t = np.linspace(0, 0.32, int(rate * 0.32), endpoint=False)
    sweep = np.sin(2 * np.pi * (300 * t + 4200 * t ** 2))     # rising chirp
    shimmer = 0.35 * np.sin(2 * np.pi * 2400 * t) * np.sin(2 * np.pi * 37 * t)
    env = np.minimum(1, t / 0.02) * np.exp(-t * 9)
    y = (0.5 * (sweep + shimmer) * env * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(y.tobytes())
    return path


def _wav() -> Path:
    global _WAV
    if _WAV is None or not _WAV.exists():
        _WAV = make_zip_wav(Path(tempfile.gettempdir()) / "goku_transmission_zip.wav")
    return _WAV


def play() -> None:
    def _run():
        try:
            p = str(_wav())
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["aplay", "-q", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass  # sound is a nice-to-have
    if os.environ.get("GOKU_MUTE") != "1":
        threading.Thread(target=_run, daemon=True).start()
