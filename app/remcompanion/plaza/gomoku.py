"""无禁手五子棋规则与轻量电脑对手，坐标从 0 开始。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re


DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))


@dataclass
class GomokuGame:
    """黑子先行，任意方向连续至少五颗即胜；一盘棋可原样继续。"""

    # New games use 22 intersections per edge. Saved games retain their exact
    # size, because enlarging a live board changes edge threats and legal moves.
    size: int = 22
    moves: list[tuple[int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.size < 5 or self.size > 25:
            raise ValueError("棋盘边长需为 5–25")
        saved = list(self.moves)
        self.board = [[0] * self.size for _ in range(self.size)]
        self.moves = []
        self.turn = 1
        self.winner = 0
        self.winning_line: list[tuple[int, int]] = []
        for row, column, player in saved:
            self.play(row, column, player)

    @property
    def finished(self) -> bool:
        return bool(self.winner) or len(self.moves) == self.size * self.size

    def legal(self, row: int, column: int) -> bool:
        return (
            isinstance(row, int) and isinstance(column, int)
            and not isinstance(row, bool) and not isinstance(column, bool)
            and 0 <= row < self.size and 0 <= column < self.size
            and self.board[row][column] == 0 and not self.finished
        )

    def play(self, row: int, column: int, player: int | None = None) -> None:
        player = self.turn if player is None else player
        if player != self.turn or not self.legal(row, column):
            raise ValueError("这一步不能落在这里")
        self.board[row][column] = player
        self.moves.append((row, column, player))
        line = self._line(row, column, player)
        if line:
            self.winner = player
            self.winning_line = line
        self.turn = 3 - player

    def _line(self, row: int, column: int, player: int) -> list[tuple[int, int]]:
        for dr, dc in DIRECTIONS:
            line = [(row, column)]
            for sign in (-1, 1):
                r, c = row + dr * sign, column + dc * sign
                while 0 <= r < self.size and 0 <= c < self.size:
                    if self.board[r][c] != player:
                        break
                    line.append((r, c))
                    r, c = r + dr * sign, c + dc * sign
            if len(line) >= 5:
                return sorted(line)
        return []

    def undo_round(self) -> None:
        """撤回最近一次用户和电脑落子，不留下胜负状态。"""
        count = 1 if self.moves and self.moves[-1][2] == 1 else 2
        self.moves = self.moves[:-count]
        self.__post_init__()

    def choose_move(self) -> tuple[int, int] | None:
        """先取必胜点、再堵对方必胜点，再比较开放连子与距离。"""
        if self.finished:
            return None
        if not self.moves:
            return self.size // 2, self.size // 2
        empty = [(r, c) for r in range(self.size) for c in range(self.size)
                 if self.board[r][c] == 0]
        for player in (self.turn, 3 - self.turn):
            for row, column in empty:
                self.board[row][column] = player
                wins = bool(self._line(row, column, player))
                self.board[row][column] = 0
                if wins:
                    return row, column
        # 棋局只需在已有棋子两格范围内考虑，无需引入模型依赖。
        candidates = {(r, c) for row, column, _ in self.moves
                      for r in range(max(0, row - 2), min(self.size, row + 3))
                      for c in range(max(0, column - 2), min(self.size, column + 3))
                      if self.board[r][c] == 0}
        center = (self.size - 1) / 2
        def rank(move: tuple[int, int]) -> tuple[int, int, float]:
            own = self._threat(*move, self.turn)
            opponent = self._threat(*move, 3 - self.turn)
            return max(own, opponent), own + opponent, -math.hypot(move[0] - center, move[1] - center)
        return max(sorted(candidates), key=rank) if candidates else empty[0]

    def _threat(self, row: int, column: int, player: int) -> int:
        total = 0
        for dr, dc in DIRECTIONS:
            count, open_ends = 1, 0
            for sign in (-1, 1):
                r, c = row + dr * sign, column + dc * sign
                while 0 <= r < self.size and 0 <= c < self.size and self.board[r][c] == player:
                    count += 1
                    r, c = r + dr * sign, c + dc * sign
                if 0 <= r < self.size and 0 <= c < self.size and self.board[r][c] == 0:
                    open_ends += 1
            # 基数由棋盘最大连子长度决定，确保长连子排在短连子前。
            total += (self.size + 1) ** count * (open_ends + 1)
        return total

    def model_prompt(self) -> str:
        symbols = {0: ".", 1: "X", 2: "O"}
        board = "\n".join(" ".join(symbols[cell] for cell in row) for row in self.board)
        return (
            f"请和用户下一盘 {self.size}×{self.size} 无禁手五子棋。用户黑子 X，你白子 O，"
            "任意方向至少连续五子获胜。棋盘每行自上而下，列自左而右，坐标从 1 开始。"
            "只能选一个空位。先寻找自己一步获胜的点，再阻止对手一步获胜。"
            '只输出 JSON：{"row": 行号, "column": 列号}。\n棋盘：\n' + board
        )

    def parse_model_move(self, text: str) -> tuple[int, int] | None:
        """只接纳合法坐标，无法解析时由调用者退回本地对手。"""
        for match in re.finditer(r"\{[^{}]*\}", text):
            try:
                raw = json.loads(match.group())
                row, column = raw["row"], raw["column"]
                if isinstance(row, bool) or isinstance(column, bool):
                    continue
                if not isinstance(row, int) or not isinstance(column, int):
                    continue
                if self.legal(row - 1, column - 1):
                    return row - 1, column - 1
            except (ValueError, KeyError, TypeError):
                continue
        return None

    def snapshot(self) -> dict[str, object]:
        return {"size": self.size, "moves": [list(move) for move in self.moves]}

    @classmethod
    def restore(cls, raw: dict[str, object]) -> GomokuGame:
        try:
            if raw == {}:
                return cls()
            moves = raw.get("moves", [])
            if not isinstance(moves, list):
                raise ValueError("棋局落子格式错误")
            return cls(size=int(raw.get("size", 15)), moves=[tuple(move) for move in moves])
        except (ValueError, TypeError, IndexError) as exc:
            raise ValueError("棋局存档不完整，原文件已保留") from exc
