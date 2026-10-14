"""Public pet geometry contracts and current-platform discovery facade."""
from __future__ import annotations

from .window_geometry import (
    BUBBLE_TAIL_ANCHOR_X, WindowRect, WindowCandidate, compute_follow_position,
)
from .platforms import native_module

CodexPetTracker = native_module("pet_tracker").CodexPetTracker
