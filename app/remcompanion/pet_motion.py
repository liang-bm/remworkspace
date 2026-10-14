"""Time-based sprite playback and bounded desktop strolls, independent of Tk.

The clock never invents missing artwork: a frame is held for its authored
duration and a late callback selects the current frame without replaying a
backlog. Position easing is for actual screen travel, not image deformation.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

from .window_geometry import WindowRect


MOTION_HZ = 30


class FrameTimeline:
    def __init__(self, durations_ms: tuple[int, ...], started_at: float) -> None:
        if not durations_ms or any(not isinstance(ms, int) or ms <= 0 for ms in durations_ms):
            raise ValueError("An animation requires positive integer frame durations")
        ends = []
        total = 0
        for ms in durations_ms:
            total += ms
            ends.append(total / 1000)
        self.ends = tuple(ends)
        self.duration = total / 1000
        self.started_at = started_at

    def sample(self, now: float, *, looping: bool) -> tuple[int, bool, float]:
        elapsed = max(0.0, now - self.started_at)
        complete = not looping and elapsed >= self.duration
        if complete:
            return len(self.ends) - 1, True, 0.0
        if looping:
            elapsed %= self.duration
        index = min(bisect_right(self.ends, elapsed), len(self.ends) - 1)
        return index, False, max(0.0, self.ends[index] - elapsed)


def clamp_pet_position(x: float, y: float, width: int, height: int,
                       work: WindowRect) -> tuple[int, int]:
    """Keep the entire own pet window inside one display's work area."""
    return (round(min(max(x, work.left), max(work.left, work.right - width))),
            round(min(max(y, work.top), max(work.top, work.bottom - height))))


@dataclass(frozen=True)
class RoamPlan:
    x0: int
    y: int
    x1: int
    started_at: float
    seconds: float
    work_area: WindowRect

    def sample(self, now: float, width: int, height: int) -> tuple[int, int, bool]:
        if not math.isfinite(now):
            return self.x0, self.y, False
        t = min(1.0, max(0.0, (now - self.started_at) / self.seconds))
        # Analytic minimum-jerk travel starts/stops at rest; no accumulated dt
        # can produce jumps or oscillation after a stalled UI callback.
        progress = t * t * t * (10 + t * (-15 + 6 * t))
        x, y = clamp_pet_position(self.x0 + (self.x1-self.x0)*progress,
                                 self.y, width, height, self.work_area)
        return x, y, t >= 1.0
