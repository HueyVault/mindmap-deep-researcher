from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Callable, TypeAlias

import numpy as np
# import autograd.numpy as np  # This is only a thin wrapper around NumPy...
# from autograd import grad  # ...to enable automatic differentation with `grad`.
from tqdm.auto import tqdm

# --- Game ---


# 🌊 = no ship present
# 🚢 = ship present
# 💦 = missile miss
# 💥 = missile hit
BattleshipGameBoard: TypeAlias = np.ndarray[tuple[int, int], np.str_]


@dataclass
class BattleshipGameRules:
    board_size: int = 5  # A 5 x 5 game board
    ships: tuple[int, ...] = (0, 0, 1, 1, 1, 0)  # 1x destroyer, 1x cruiser/submarine, 1x battleship


@dataclass
class BattleshipGame:
    board: BattleshipGameBoard
    rules: BattleshipGameRules = field(default_factory=BattleshipGameRules)

    @staticmethod
    def random_board(rules: BattleshipGameRules | None = None, seed: int | None = None) -> BattleshipGameBoard:
        rules = rules or BattleshipGameRules()
        random_state = np.random.RandomState(seed)
        ships = [ship_size for ship_size, ship_count in enumerate(rules.ships) for _ in range(ship_count)]
        ships_placed = False
        while not ships_placed:
            board = np.full((rules.board_size, rules.board_size), "🌊", dtype=np.str_)
            for ship_size in ships:
                if ship_size > rules.board_size:
                    return
                ship_top_left = random_state.randint(low=0, high=rules.board_size - (ship_size - 1), size=2)
                ship_bottom_right = ship_top_left + 1
                ship_bottom_right[random_state.randint(low=0, high=2)] += ship_size - 1
                if np.all(board[ship_top_left[0] : ship_bottom_right[0], ship_top_left[1] : ship_bottom_right[1]] == "🌊"):
                    board[ship_top_left[0] : ship_bottom_right[0], ship_top_left[1] : ship_bottom_right[1]] = "🚢"
                else:
                    break
            else:
                ships_placed = True
        return board

    def play(self, fire: tuple[int, int]) -> bool:
        hit = self.board[fire] in ("🚢", "💥")
        self.board[fire] = "💥" if hit else "💦"
        return hit

    def score(self) -> float:
        done = not np.any(self.board == "🚢")
        efficiency: float = 1.0 - np.sum(self.board == "💦") / (self.board.size - np.sum(self.board == "💥") + 1) if done else 0.0
        return efficiency

    def __repr__(self) -> str:
        return "\n".join("".join(row) for row in self.board)