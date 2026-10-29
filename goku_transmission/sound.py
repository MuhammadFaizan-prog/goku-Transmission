"""Teleport sound.

By default a short synthesized "shyun" is generated at runtime, so no copyrighted audio
ships with the repo. Want the real anime sound? Put it in the ``sounds/`` folder
as ``teleport.mp3`` or ``teleport.wav`` (or pass ``--sound file``) and it plays
every time you vanish and every time you come back.

MP3 needs nothing extra on Windows (built-in MCI player) or macOS (afplay);
on Linux it uses ffplay/mpg123/paplay, whichever is installed.
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

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "sounds"
USER_SOUNDS = [SOUNDS_DIR / "teleport.mp3", SOUNDS_DIR / "teleport.wav"]
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
    for f in USER_SOUNDS:
        if f.exists():
            return f
    if _generated is None or not _generated.exists():
        _generated = make_shyun_wav(Path(tempfile.gettempdir()) / "goku_transmission_shyun.wav")
    return _generated


class _WinPlayer:
    """One long-lived thread owns the MCI device (MCI devices belong to the thread that opened them)."""

    def __init__(self):
        import queue
        self.q: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        opened = None
        while True:
            p = self.q.get()
            try:
                if p.lower().endswith(".wav"):
                    import winsound
                    winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    continue
                if opened != p:
                    if opened:
                        mci("close goku_snd", None, 0, None)
                    err = mci(f'open "{p}" type mpegvideo alias goku_snd', None, 0, None)
                    if err:
                        print(f"[sound] Windows couldn't open {p} (MCI error {err})")
                        opened = None
                        continue
                    opened = p
                mci("play goku_snd from 0", None, 0, None)   # restarts even if still playing
            except Exception as e:  # noqa: BLE001
                print(f"[sound] couldn't play: {e}")


_win: _WinPlayer | None = None


def _unix_cmd(p: str) -> list[str] | None:
    import shutil
    if sys.platform == "darwin":
        return ["afplay", p]
    if p.lower().endswith(".wav") and shutil.which("aplay"):
        return ["aplay", "-q", p]
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", p], ["mpg123", "-q", p], ["paplay", p]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def play() -> None:
    """Fire and forget. Called on every vanish and every reappear."""
    global _win
    if os.environ.get("GOKU_MUTE") == "1":
        return
    try:
        p = str(sound_path())
        if sys.platform.startswith("win"):
            if _win is None:
                _win = _WinPlayer()
            _win.q.put(p)
            return
        cmd = _unix_cmd(p)
        if cmd is None:
            print("[sound] no audio player found (install ffmpeg or mpg123)")
            return
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"[sound] couldn't play: {e}")
