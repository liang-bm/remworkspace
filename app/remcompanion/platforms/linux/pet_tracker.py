"""Linux placeholder for external Codex window discovery.

The independently rendered RemChat pet uses a direct Tk anchor instead.
No external Windows HWND enumeration is attempted on Linux.
"""
from __future__ import annotations


class CodexPetTracker:
    def __init__(self) -> None:
        self._last_handle = None
        self._available = False

    def find(self):
        return None

    def work_area_for(self, hwnd: int):
        return None
