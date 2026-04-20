from abc import ABC, abstractmethod
from collections import deque
from enum import Enum


class Direction(Enum):
    U = "U"
    R = "R"
    L = "L"
    D: "D"


# ──────────────────────────────────────────────
# 1. Position  (value object for grid coordinates)
# ──────────────────────────────────────────────
class Position:
    def __init__(self, row: int, col: int):
        self.row = row
        self.col = col

    def __eq__(self, other):
        return isinstance(other, Position) and self.row == other.row and self.col == other.col

    def __hash__(self):
        return hash((self.row, self.col))


# ──────────────────────────────────────────────
# 2. GameBoard
# ──────────────────────────────────────────────
class GameBoard:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    def is_within_bounds(self, pos: Position) -> bool:
        return 0 <= pos.row < self.height and 0 <= pos.col < self.width


# ──────────────────────────────────────────────
# 3. Strategy Pattern — Movement
# ──────────────────────────────────────────────
class MovementStrategy(ABC):
    @abstractmethod
    def get_next_position(self, head: Position, direction: str) -> Position:
        pass


class HumanMovementStrategy(MovementStrategy):
    DIRECTIONS = {Direction.U: (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}

    def get_next_position(self, head: Position, direction: str) -> Position:
        dr, dc = self.DIRECTIONS.get(direction, (0, 0))
        return Position(head.row + dr, head.col + dc)


# ──────────────────────────────────────────────
# 4. Factory Pattern — Food
# ──────────────────────────────────────────────
class FoodItem(ABC):
    def __init__(self, row: int, col: int):
        self.position = Position(row, col)
        self.points = 0


class NormalFood(FoodItem):
    def __init__(self, row: int, col: int):
        super().__init__(row, col)
        self.points = 1


class BonusFood(FoodItem):
    def __init__(self, row: int, col: int):
        super().__init__(row, col)
        self.points = 3


class FoodFactory:
    @staticmethod
    def create(position: tuple, food_type: str = "normal") -> FoodItem:
        if food_type == "bonus":
            return BonusFood(position[0], position[1])
        return NormalFood(position[0], position[1])


# ──────────────────────────────────────────────
# 5. Snake  (deque + set for O(1) ops)
# ──────────────────────────────────────────────
class Snake:
    def __init__(self, start: Position):
        self.body = deque([start])          # front = head, back = tail
        self.position_set = {start}         # O(1) collision lookup

    @property
    def head(self) -> Position:
        return self.body[0]

    @property
    def tail(self) -> Position:
        return self.body[-1]

    def move_to(self, new_head: Position, grow: bool = False):
        self.body.appendleft(new_head)
        self.position_set.add(new_head)
        if not grow:
            removed = self.body.pop()
            self.position_set.discard(removed)

    def collides_with_self(self, new_head: Position) -> bool:
        # Tail will move away, so moving into the tail's current spot is safe
        return new_head in self.position_set and new_head != self.tail

    def __len__(self):
        return len(self.body)


# ──────────────────────────────────────────────
# 6. SnakeGame  (controller)
# ──────────────────────────────────────────────
class SnakeGame:
    def __init__(self, width: int, height: int, food_positions: list,
                 has_walls: bool = True):
        self.board = GameBoard(width, height)
        self.snake = Snake(Position(0, 0))
        self.food_positions = food_positions
        self.food_index = 0
        self.score = 0
        self.has_walls = has_walls
        self.strategy = HumanMovementStrategy()

    def set_strategy(self, strategy: MovementStrategy):
        self.strategy = strategy

    def _wrap(self, pos: Position) -> Position:
        return Position(pos.row % self.board.height, pos.col % self.board.width)

    def move(self, direction: str) -> int:
        """Returns new score, or -1 if game over."""
        new_head = self.strategy.get_next_position(self.snake.head, direction)

        # Wall check
        if self.has_walls:
            if not self.board.is_within_bounds(new_head):
                return -1
        else:
            new_head = self._wrap(new_head)

        # Self-collision
        if self.snake.collides_with_self(new_head):
            return -1

        # Food check
        ate = False
        if self.food_index < len(self.food_positions):
            fp = self.food_positions[self.food_index]
            if new_head.row == fp[0] and new_head.col == fp[1]:
                ate = True
                self.food_index += 1

        # Move snake
        self.snake.move_to(new_head, grow=ate)
        self.score = len(self.snake) - 1
        return self.score


# ──────────────────────────────────────────────
# 7. Main
# ──────────────────────────────────────────────
if __name__ == "__main__":
    food = [(1, 2), (0, 1)]
    game = SnakeGame(width=3, height=3, food_positions=food)

    moves = ["R", "R", "D", "R", "L"]  # sample trace
    for ind, m in enumerate(moves):
        result = game.move(m)
        if result == -1:
            print(f"Game Over after move '{m}', TOTAL MOVES: {ind+1}")
            break
        print(f"Move {m} → score {result}")