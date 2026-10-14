"""Platform-independent window geometry and pet-follow alignment."""

from __future__ import annotations

from dataclasses import dataclass


BUBBLE_TAIL_ANCHOR_X = 78


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class WindowCandidate:
    handle: int
    rect: WindowRect
    title: str
    class_name: str
    process_path: str
    ex_style: int
    score: int


def compute_follow_position(
    pet: WindowRect,
    bubble_width: int,
    bubble_height: int,
    *,
    work_area: WindowRect | None = None,
    tight_pet_window: bool = False,
    tail_anchor_x: int = BUBBLE_TAIL_ANCHOR_X,
) -> tuple[int, int]:
    """把气泡尖角对准宠物头顶，并保证窗口不越出工作区。

    Codex 的透明 Electron 宠物窗口比实际角色大，角色头部大约位于窗口
    横向 64%、纵向 60% 处；独立 EXE 的宠物窗口则是紧边界，直接使用顶部
    中心。此前只对齐整个气泡右边缘，导致右侧尖角离角色头部数百像素。
    """

    work = work_area or WindowRect(0, 0, 1920, 1080)
    gap = 14
    if tight_pet_window:
        mascot_anchor_x = pet.left + pet.width // 2
        mascot_anchor_y = pet.top + 16
    else:
        mascot_anchor_x = pet.left + round(pet.width * 0.64)
        mascot_anchor_y = pet.top + round(pet.height * 0.60)

    x = mascot_anchor_x - tail_anchor_x
    y = mascot_anchor_y - bubble_height + 8
    if y < work.top + gap:
        # 屏幕顶边放不下时退到宠物侧面，优先选择空间更大的一侧。
        y = mascot_anchor_y - bubble_height // 2
        right_space = work.right - pet.right
        left_space = pet.left - work.left
        if right_space >= bubble_width + gap or right_space >= left_space:
            x = pet.right + gap
        else:
            x = pet.left - bubble_width - gap
    if y + bubble_height > work.bottom - gap:
        y = work.bottom - bubble_height - gap
    x = max(work.left + gap, x)
    x = min(work.right - bubble_width - gap, x)
    y = max(work.top + gap, y)
    return int(x), int(y)
