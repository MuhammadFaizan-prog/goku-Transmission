import threading
import time

import numpy as np

from goku_transmission.vision import AsyncVision, Observation


class SlowVision:
    """Fake models: 40 ms per frame, remembers which frames it actually saw."""
    video = True

    def __init__(self):
        self.seen, self.closed = [], False
        self.threads = set()

    def observe(self, frame, ts=None):
        self.threads.add(threading.get_ident())
        time.sleep(0.04)
        self.seen.append(int(frame[0, 0, 0]))
        return Observation([], None, np.full(frame.shape[:2], frame[0, 0, 0] / 255, np.float32))

    def close(self):
        self.closed = True


def _frame(i):
    return np.full((36, 64, 3), i, np.uint8)


def test_never_blocks_after_first_frame_and_skips_backlog():
    slow = SlowVision()
    av = AsyncVision(slow)
    av.observe(_frame(1), 0)                       # first call waits for real data
    t0 = time.perf_counter()
    for i in range(2, 40):
        obs = av.observe(_frame(i), i * 10)
        assert obs.person_mask.shape == (36, 64)
    assert time.perf_counter() - t0 < 0.5          # 38 frames x 40 ms would be 1.5 s if it waited
    time.sleep(0.15)
    assert slow.seen[-1] == 39                     # newest frame always gets processed...
    assert len(slow.seen) < 20                     # ...stale ones are dropped, not queued
    assert threading.get_ident() not in slow.threads
    av.close()
    assert slow.closed


def test_returns_latest_finished_result():
    av = AsyncVision(SlowVision())
    av.observe(_frame(10), 0)
    time.sleep(0.1)
    obs = av.observe(_frame(20), 10)
    assert abs(float(obs.person_mask[0, 0]) - 10 / 255) < 1e-6   # one frame behind, never garbage
    time.sleep(0.1)
    assert abs(float(av.observe(_frame(30), 20).person_mask[0, 0]) - 20 / 255) < 1e-6
    av.close()
