from __future__ import annotations

import unittest

from remcompanion.pet_tracker import (
    BUBBLE_TAIL_ANCHOR_X,
    WindowRect,
    compute_follow_position,
)


class PetTrackerTests(unittest.TestCase):
    def test_bubble_stays_inside_work_area(self) -> None:
        work = WindowRect(0, 0, 1920, 1040)
        pet = WindowRect(1560, 700, 1910, 1020)
        x, y = compute_follow_position(
            pet,
            430,
            342,
            work_area=work,
        )
        self.assertGreaterEqual(x, work.left)
        self.assertGreaterEqual(y, work.top)
        self.assertLessEqual(x + 430, work.right)
        self.assertLessEqual(y + 342, work.bottom)

    def test_codex_viewport_aligns_tail_with_character_head(self) -> None:
        work = WindowRect(0, 0, 1920, 1040)
        pet = WindowRect(120, 600, 476, 920)
        x, y = compute_follow_position(
            pet,
            430,
            310,
            work_area=work,
        )
        expected_head_x = pet.left + round(pet.width * 0.64)
        self.assertLessEqual(abs((x + BUBBLE_TAIL_ANCHOR_X) - expected_head_x), 1)
        self.assertLess(y, pet.top + round(pet.height * 0.60))

    def test_tight_standalone_pet_uses_top_center_anchor(self) -> None:
        work = WindowRect(0, 0, 1920, 1040)
        pet = WindowRect(800, 700, 992, 908)
        x, _ = compute_follow_position(
            pet,
            440,
            310,
            work_area=work,
            tight_pet_window=True,
        )
        self.assertEqual(
            pet.left + pet.width // 2,
            x + BUBBLE_TAIL_ANCHOR_X,
        )


if __name__ == "__main__":
    unittest.main()
