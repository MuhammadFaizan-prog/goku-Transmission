"""Teleport sound.

By default a short synthesized "shyun" (a bright zip that drops in pitch over
a burst of air) is generated at runtime, so no copyrighted audio ships with
the repo. Want the real anime sound? Pass ``--sound path/to/file.wav`` (or put
a ``teleport.wav`` in the ``sounds/`` folder) and that file is played instead.

Played on your speakers only. It never goes into the meeting.
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

USER_SOUND = Path(__file__).resolve().parent.parent / "sounds" / "teleport.wav"
_custom: Path | None = None
_generated: Path | None = None


def set_custom(path: str | None) -> None:
    global _custom
    _custom = Path(path) if path else None


def make_shyun_wav(path: Path, rate: int = 44100) -> Path:
    dur = 0.38
    n = int(rate * dur)
    t = np.arange(n) / rate
    rng = np.random.default_rng(3)
    # pitch: snaps up fast, then falls - the "shyun"
    f = np.where(t < 0.03, 1200 + 60000 * t, 3000 * np.exp(-(t - 0.03) * 7) + 500)
    phase = 2 * np.pi * np.cumsum(f) / rate
    tone = np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.15 * np.sin(3.01 * phase)
    noise = np.diff(rng.standard_normal(n + 1))          # bright, airy
    noise = np.convolve(noise, np.ones(4) / 4, mode="same")
    env_t = np.minimum(1, t / 0.008) * np.exp(-t * 8.5)
    env_n = np.minimum(1, t / 0.004) * np.exp(-t * 22)
    y = 0.55 * tone * env_t + 0.35 * noise * env_n
    y += 0.25 * np.sin(2 * np.pi * 90 * t) * np.exp(-t * 18)   # small low "thump"
    y = y / max(1e-6, np.abs(y).max()) * 0.85
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    return path


def sound_path() -> Path:
    global _generated
    if _custom and _custom.exists():
        return _custom
    if USER_SOUND.exists():
        return USER_SOUND
    if _generated is None or not _generated.exists():
        _generated = make_shyun_wav(Path(tempfile.gettempdir()) / "goku_transmission_shyun.wav")
    return _generated


def play() -> None:
    if os.environ.get("GOKU_MUTE") == "1":
        return

    def _run():
        try:
            p = str(sound_path())
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["aplay", "-q", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            print(f"[sound] couldn't play: {e}")
    threading.Thread(target=_run, daemon=True).start()
