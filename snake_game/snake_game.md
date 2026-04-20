# Low-Level Design: Snake & Food Game — Uber SDE-2 Interview Guide

---

## Step 1: Understanding & Clarifying Requirements

> **What to say:** "Let me make sure I understand the problem correctly before jumping into design."

### Core Understanding

- The game is played on an **N x M grid**.
- A **snake** starts at an initial position with length 1.
- The player controls the snake using directional inputs: **Up, Down, Left, Right**.
- When the snake eats **food**, it grows by one unit, and a new food item appears.
- The game ends when the snake **hits a wall** or **collides with itself**.

### Clarifying Questions to Ask the Interviewer

| # | Question | Why It Matters |
|---|----------|----------------|
| 1 | Is the board size fixed or configurable? | Determines if we need a Singleton or parameterized board. |
| 2 | Is food placed at predefined positions or randomly? | Affects the Food class design. |
| 3 | Can there be multiple types of food (bonus, poison)? | Tells us if we need a Factory pattern. |
| 4 | Are there walls, or does the snake wrap around edges? | Impacts boundary-checking logic. |
| 5 | Is the snake controlled by a human or could an AI also play? | Motivates the Strategy pattern for movement. |
| 6 | Do we need to notify external systems (UI, score tracker) on events? | Motivates the Observer pattern. |

> **Tip:** At Uber's SDE-2 level, asking these questions shows you think about extensibility and edge cases before coding.

---

## Step 2: Identify Core Entities & Relationships

> **What to say:** "Let me identify the main entities and how they interact."

### Entities

```
+----------------+       has-a        +----------------+
|   SnakeGame    |-------------------→|   GameBoard    |
|  (Controller)  |                    | (width, height)|
+----------------+                    +----------------+
       |
       | has-a
       ↓
+----------------+       has-a        +----------------+
|     Snake      |-------------------→| MovementStrategy|
| (body, movement)|                   | (get_next_pos) |
+----------------+                    +----------------+
       |
       | uses
       ↓
+----------------+
|   FoodManager  |
| (spawn, consume)|
+----------------+
       |
       | creates via Factory
       ↓
+----------------+
|   FoodItem     |
| (row, col, pts)|
+----------------+
```

### Relationship Summary

| Entity | Responsibility | Collaborators |
|--------|---------------|---------------|
| **SnakeGame** | Orchestrates the game loop, processes moves, tracks score | Board, Snake, FoodManager |
| **GameBoard** | Holds grid dimensions, validates positions | — |
| **Snake** | Maintains body segments, grows, detects self-collision | MovementStrategy |
| **MovementStrategy** | Determines next head position given a direction | — |
| **FoodManager** | Manages food spawning and consumption | FoodItem (via Factory) |
| **FoodItem** | Represents a single food item with position and points | — |

---

## Step 3: Design Patterns & Tradeoffs

> **What to say:** "I'd like to apply a few design patterns here to keep the system modular and extensible."

### 3.1 Strategy Pattern — Snake Movement

**Problem:** The snake can be controlled by a human player today, but we might want an AI player or different control schemes later.

**Solution:** Define a `MovementStrategy` interface. Swap implementations without changing game logic.

```
<<interface>>
MovementStrategy
  + get_next_position(head, direction) -> (row, col)
        ↑                    ↑
        |                    |
HumanMovementStrategy   AIMovementStrategy
```

**Tradeoff:**
- **Pro:** Open/Closed Principle — add new movement logic without modifying existing code.
- **Con:** Slight indirection for a simple game, but well worth it at SDE-2 level.

### 3.2 Factory Pattern — Food Creation

**Problem:** We may need different food types (normal = 1 point, bonus = 3 points, poison = -1 point) without modifying existing code.

**Solution:** A `FoodFactory` creates `FoodItem` subclasses based on a type string.

```
FoodFactory.create_food(position, type)
        |
        ├──→ NormalFood(points=1)
        ├──→ BonusFood(points=3)
        └──→ PoisonFood(points=-1)
```

**Tradeoff:**
- **Pro:** Adding a new food type is a single new class + one line in the factory.
- **Con:** For only one food type, a factory is overkill — but it shows extensible thinking.

### 3.3 Singleton Pattern — Game Board

**Problem:** There should only ever be one game board instance during a game session.

**Solution:** Classic Singleton with a class method.

**Tradeoff:**
- **Pro:** Prevents accidental creation of multiple boards.
- **Con:** Makes unit testing harder (global state). In Python, a module-level instance often suffices, but Singleton demonstrates the pattern knowledge expected in interviews.

### 3.4 Observer Pattern — Game Events

**Problem:** Multiple systems (UI, logger, score tracker) need to react to game events (move, food eaten, game over).

**Solution:** `SnakeGame` maintains a list of observers and notifies them on key events.

**Tradeoff:**
- **Pro:** Decouples game logic from presentation/logging.
- **Con:** Adds complexity; only introduce if interviewer asks about extensibility or event tracking.

---

## Step 4: Full Implementation in Python

> **What to say:** "Let me now code this up, starting with the foundational classes and building up to the game controller."

### 4.1 Pair — Position Helper

```python
class Position:
    """Represents a (row, col) coordinate on the board."""

    def __init__(self, row: int, col: int):
        self.row = row
        self.col = col

    def __eq__(self, other):
        return isinstance(other, Position) and self.row == other.row and self.col == other.col

    def __hash__(self):
        return hash((self.row, self.col))

    def __repr__(self):
        return f"({self.row}, {self.col})"
```

> **Why `__eq__` and `__hash__`?** We store positions in a `set` for O(1) self-collision checks. Without these, Python compares by identity, not value.

---

### 4.2 GameBoard — Singleton Pattern

```python
class GameBoard:
    """Singleton game board with configurable dimensions."""

    _instance = None

    def __new__(cls, width: int, height: int):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.width = width
            cls._instance.height = height
        return cls._instance

    def is_within_bounds(self, position: Position) -> bool:
        return 0 <= position.row < self.height and 0 <= position.col < self.width

    @classmethod
    def reset(cls):
        """Reset singleton — useful for testing."""
        cls._instance = None
```

---

### 4.3 MovementStrategy — Strategy Pattern

```python
from abc import ABC, abstractmethod


class MovementStrategy(ABC):
    """Interface for movement strategies."""

    @abstractmethod
    def get_next_position(self, head: Position, direction: str) -> Position:
        pass


class HumanMovementStrategy(MovementStrategy):
    """Standard directional movement controlled by the player."""

    DIRECTION_MAP = {
        "U": (-1, 0),
        "D": (1, 0),
        "L": (0, -1),
        "R": (0, 1),
    }

    def get_next_position(self, head: Position, direction: str) -> Position:
        dr, dc = self.DIRECTION_MAP.get(direction, (0, 0))
        return Position(head.row + dr, head.col + dc)


class AIMovementStrategy(MovementStrategy):
    """Placeholder for AI-driven movement (e.g., BFS toward food)."""

    def get_next_position(self, head: Position, direction: str) -> Position:
        # In a real implementation: pathfinding toward food, avoiding obstacles
        dr, dc = HumanMovementStrategy.DIRECTION_MAP.get(direction, (0, 0))
        return Position(head.row + dr, head.col + dc)
```

---

### 4.4 FoodItem & FoodFactory — Factory Pattern

```python
class FoodItem(ABC):
    """Abstract food item on the board."""

    def __init__(self, row: int, col: int):
        self.position = Position(row, col)
        self.points = 0  # Subclasses set this


class NormalFood(FoodItem):
    def __init__(self, row: int, col: int):
        super().__init__(row, col)
        self.points = 1


class BonusFood(FoodItem):
    def __init__(self, row: int, col: int):
        super().__init__(row, col)
        self.points = 3


class FoodFactory:
    """Creates food items based on type string."""

    @staticmethod
    def create_food(position: tuple, food_type: str = "normal") -> FoodItem:
        if food_type == "bonus":
            return BonusFood(position[0], position[1])
        return NormalFood(position[0], position[1])
```

---

### 4.5 Observer — Observer Pattern

```python
class GameObserver(ABC):
    """Interface for observing game events."""

    @abstractmethod
    def on_move_made(self, new_head: Position):
        pass

    @abstractmethod
    def on_food_eaten(self, food_index: int, new_score: int):
        pass

    @abstractmethod
    def on_game_over(self, final_score: int):
        pass


class ConsoleGameObserver(GameObserver):
    """Logs game events to the console."""

    def on_move_made(self, new_head: Position):
        print(f"  -> Snake moved to {new_head}")

    def on_food_eaten(self, food_index: int, new_score: int):
        print(f"  -> Food #{food_index} eaten! Score: {new_score}")

    def on_game_over(self, final_score: int):
        print(f"  -> GAME OVER! Final score: {final_score}")
```

---

### 4.6 Snake

```python
from collections import deque


class Snake:
    """
    The snake entity.
    - body: deque of Position (front = head, back = tail)
    - position_set: set of Position for O(1) self-collision detection
    """

    def __init__(self, start: Position):
        self.body = deque([start])
        self.position_set = {start}

    @property
    def head(self) -> Position:
        return self.body[0]

    @property
    def tail(self) -> Position:
        return self.body[-1]

    def move_to(self, new_head: Position, grow: bool = False):
        """Move the snake to new_head. If grow=True, don't remove the tail."""
        self.body.appendleft(new_head)
        self.position_set.add(new_head)

        if not grow:
            removed_tail = self.body.pop()
            self.position_set.discard(removed_tail)

    def will_collide_with_self(self, new_head: Position) -> bool:
        """Check if new_head hits the body (excluding current tail, which will move away)."""
        if new_head not in self.position_set:
            return False
        # The tail moves away on a non-growing move, so it's safe to move into its current spot
        return new_head != self.tail

    def __len__(self):
        return len(self.body)
```

> **Key insight for the interviewer:** Using a `deque` gives us O(1) append/pop at both ends. The `set` gives O(1) collision checks. This is the optimal data structure combination.

---

### 4.7 SnakeGame — Game Controller

```python
class SnakeGame:
    """
    Main game controller that orchestrates board, snake, food, and scoring.
    
    Data Structures:
        - Snake body  → deque  (O(1) head insert + tail remove)
        - Collision   → set    (O(1) lookup)
        - Food list   → list   (sequential access by index)
    """

    def __init__(self, width: int, height: int, food_positions: list, has_walls: bool = True):
        GameBoard.reset()
        self.board = GameBoard(width, height)
        self.snake = Snake(Position(0, 0))
        self.food_positions = food_positions
        self.food_index = 0
        self.score = 0
        self.has_walls = has_walls
        self.movement_strategy: MovementStrategy = HumanMovementStrategy()
        self.observers: list = []

    def set_movement_strategy(self, strategy: MovementStrategy):
        self.movement_strategy = strategy

    def add_observer(self, observer: GameObserver):
        self.observers.append(observer)

    # ---------- Observer notification helpers ----------

    def _notify_move(self, pos: Position):
        for obs in self.observers:
            obs.on_move_made(pos)

    def _notify_food_eaten(self, idx: int, score: int):
        for obs in self.observers:
            obs.on_food_eaten(idx, score)

    def _notify_game_over(self, score: int):
        for obs in self.observers:
            obs.on_game_over(score)

    # ---------- Wrap-around for no-walls mode ----------

    def _wrap_position(self, pos: Position) -> Position:
        row = pos.row % self.board.height
        col = pos.col % self.board.width
        return Position(row, col)

    # ---------- Core game logic ----------

    def move(self, direction: str) -> int:
        """
        Process one move. Returns the new score, or -1 if game over.

        Steps:
            1. Compute new head position via the movement strategy.
            2. Handle boundary: wall check (or wrap-around).
            3. Check self-collision.
            4. Check food consumption → grow or move tail.
            5. Update snake body.
            6. Return score.
        """
        # 1. Next position
        new_head = self.movement_strategy.get_next_position(self.snake.head, direction)

        # 2. Boundary handling
        if self.has_walls:
            if not self.board.is_within_bounds(new_head):
                self._notify_game_over(self.score)
                return -1
        else:
            new_head = self._wrap_position(new_head)

        # 3. Self-collision
        if self.snake.will_collide_with_self(new_head):
            self._notify_game_over(self.score)
            return -1

        # 4. Food check
        ate_food = False
        if self.food_index < len(self.food_positions):
            food_pos = self.food_positions[self.food_index]
            if new_head.row == food_pos[0] and new_head.col == food_pos[1]:
                ate_food = True
                self.food_index += 1

        # 5. Move snake (grow if food was eaten)
        self.snake.move_to(new_head, grow=ate_food)

        # 6. Update score
        self.score = len(self.snake) - 1

        # Notify observers
        self._notify_move(new_head)
        if ate_food:
            self._notify_food_eaten(self.food_index - 1, self.score)

        return self.score
```

---

### 4.8 Main — Running the Game

```python
def main():
    width, height = 20, 15
    food_positions = [
        (5, 5),
        (10, 8),
        (3, 12),
        (8, 17),
        (12, 3),
    ]

    game = SnakeGame(width, height, food_positions, has_walls=True)
    game.add_observer(ConsoleGameObserver())

    input_map = {"W": "U", "S": "D", "A": "L", "D": "R"}

    print("===== SNAKE GAME =====")
    print("Controls: W (Up), S (Down), A (Left), D (Right), Q (Quit)")
    print("======================")

    while True:
        user_input = input("Move (W/A/S/D) or Q to quit: ").upper()

        if user_input == "Q":
            print(f"You quit. Final score: {game.score}")
            break

        direction = input_map.get(user_input)
        if not direction:
            print("Invalid input.")
            continue

        result = game.move(direction)
        if result == -1:
            print(f"GAME OVER! Final score: {game.score}")
            break

        print(f"Score: {result}")


if __name__ == "__main__":
    main()
```

---

## Step 5: Extensibility Discussion

> **What to say:** "Here's how this design handles future requirements without modifying existing code."

### 5.1 No-Walls / Wrap-Around Mode

Already supported via the `has_walls` flag in `SnakeGame.__init__`. When `False`, positions wrap around using modulo arithmetic instead of triggering game over.

### 5.2 New Food Types

Add a new subclass (e.g., `PoisonFood` with negative points) and a single entry in `FoodFactory`. No changes to `SnakeGame`.

### 5.3 AI Player

Create `AIMovementStrategy` with pathfinding logic (BFS/A*). Swap it in with `game.set_movement_strategy(AIMovementStrategy())`. Zero changes to game controller.

### 5.4 Multiplayer

Each player gets their own `Snake` instance. `SnakeGame` manages a list of snakes and checks cross-snake collisions. The `move()` method takes a `player_id` parameter.

### 5.5 Event-Driven UI

Add a new `GameObserver` subclass (e.g., `WebSocketObserver`) that pushes events to a frontend. Attach it with `game.add_observer(...)`. Game logic stays untouched.

---

## Step 6: Complexity Analysis

| Operation | Time Complexity | Why |
|-----------|----------------|-----|
| Move (no food) | O(1) | Deque appendleft + pop, set add + discard |
| Move (with food) | O(1) | Deque appendleft only (no pop), set add |
| Self-collision check | O(1) | HashSet lookup |
| Wall collision check | O(1) | Bounds comparison |
| Space for snake | O(S) | S = snake length (deque + set) |

---

## Quick Reference: Interview Flow

```
[1] Restate the problem, confirm understanding          ~2 min
[2] Ask clarifying questions (walls? food types? AI?)    ~3 min
[3] Identify entities: Board, Snake, Food, Game          ~3 min
[4] Draw relationships on whiteboard / paper             ~2 min
[5] Discuss design patterns + tradeoffs                  ~5 min
     - Strategy  → Movement
     - Factory   → Food types
     - Singleton → Board
     - Observer  → Events
[6] Code the solution bottom-up                         ~20 min
     Position → Board → Strategy → Food → Snake → Game
[7] Walk through a sample game trace                     ~3 min
[8] Discuss extensibility (no walls, AI, multiplayer)    ~2 min
```

---

## Key Talking Points for Uber SDE-2

1. **Data structure choice matters** — `deque` + `set` gives O(1) everything. Mention this explicitly; Uber cares about performance.
2. **Strategy pattern is the star** — It directly maps to Uber's multi-algorithm world (routing, pricing, matching). Show you think in swappable strategies.
3. **Don't over-engineer** — Implement Strategy and Factory. Mention Observer and Singleton verbally but only code them if asked. Shows judgment.
4. **Trace through an example** — After coding, walk through 3-4 moves showing head/tail updates, food consumption, and a game-over scenario. This proves your code works.
5. **SOLID principles** — Call them out: Single Responsibility (each class does one thing), Open/Closed (new food type = new class, not modified code), Dependency Inversion (game depends on `MovementStrategy` abstraction, not concrete class).

---

## Final Code — Write This in 20 Minutes

> **What to actually code in the interview.** This is the lean, working version.
> Mention Observer and Singleton *verbally* as extensibility options — don't waste time coding them.
>
> **Writing order:** Position → Board → Strategy → FoodItem + Factory → Snake → SnakeGame → main

```python
from abc import ABC, abstractmethod
from collections import deque


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
    DIRECTIONS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}

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

    moves = ["R", "R", "D", "L", "L"]  # sample trace
    for m in moves:
        result = game.move(m)
        if result == -1:
            print(f"Game Over after move '{m}'")
            break
        print(f"Move {m} → score {result}")
```

### How to write this in order (whiteboard tips)

| Order | What to write | Time |
|-------|--------------|------|
| 1 | `Position` — 6 lines, mention `__eq__`/`__hash__` for set usage | 1 min |
| 2 | `GameBoard` — 4 lines, just width/height + bounds check | 1 min |
| 3 | `MovementStrategy` + `HumanMovementStrategy` — direction dict + one method | 3 min |
| 4 | `FoodItem` + `NormalFood` + `BonusFood` + `FoodFactory` — all short | 3 min |
| 5 | `Snake` — deque + set, `move_to`, `collides_with_self` | 4 min |
| 6 | `SnakeGame` — init + `move()` method (the main logic) | 6 min |
| 7 | Quick `main` trace to prove it works | 2 min |
| | **Total** | **~20 min** |

### What to say vs. what to code

| Say it (don't code it) | Code it |
|------------------------|---------|
| Singleton for GameBoard | Plain `GameBoard` class |
| Observer for UI/logging | Print in main for demo |
| AI movement strategy | `HumanMovementStrategy` only |
| Multiplayer support | Single snake |
| `PoisonFood` type | `NormalFood` + `BonusFood` + Factory |

> The interviewer sees you *know* these patterns. Coding the core 6 classes proves you can *implement*. That's the balance for 20 minutes.
