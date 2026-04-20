# Low-Level Design: Tic Tac Toe Game - Uber SDE-2 Interview Guide

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me first make sure I understand the problem correctly, then clarify a few things."

### Your Understanding (State This)

- Standard Tic Tac Toe: two players alternate turns on a 3x3 grid
- Players place 'X' or 'O' on empty cells
- First to get 3 in a row (horizontal, vertical, diagonal) wins
- If all 9 cells are filled with no winner, it's a draw
- A player cannot place on an already occupied cell

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Are we designing for a standard 3x3 board, or should it support NxN? | Start with 3x3, but design for extensibility |
| 2 | Two human players only, or should we support AI? | Two human players, but keep it extensible for AI |
| 3 | Is this a console-based game or do we need a UI? | Console-based is fine |
| 4 | Do we need to persist game state or is in-memory okay? | In-memory is fine |

### Confirmed Requirements

1. A 3x3 game board (extensible to NxN)
2. Two human players
3. Alternating turns between 'X' and 'O'
4. Move validation (bounds + empty cell check)
5. Win detection (row, column, diagonal)
6. Draw detection (board full, no winner)

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Let me identify the key entities and how they relate to each other."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **Symbol (Enum)** | Represents X, O, EMPTY |
| **Position** | Encapsulates (row, col) coordinates |
| **Board** | Holds the grid, validates moves, makes moves |
| **Player** | Holds a symbol and a strategy for making moves |
| **PlayerStrategy (Interface)** | Defines how a player picks their move (Strategy Pattern) |
| **GameState (Interface)** | Represents current state of the game (State Pattern) |
| **GameContext** | Manages state transitions |
| **TicTacToeGame** | Orchestrates the game loop (Controller) |

### Relationships

```
TicTacToeGame (Controller)
    |
    |--- has-a ---> Board (grid + move logic)
    |--- has-a ---> List[Player] (players taking turns)
    |--- has-a ---> GameContext (tracks game state)
    
Player
    |--- has-a ---> Symbol (X or O)
    |--- has-a ---> PlayerStrategy (how they pick moves)

PlayerStrategy (interface)
    |--- implemented by ---> HumanPlayerStrategy
    |--- implemented by ---> (future: AIPlayerStrategy)

GameState (interface)
    |--- implemented by ---> XTurnState, OTurnState
    |--- implemented by ---> XWonState, OWonState, DrawState
```

---

## Step 3: Design Patterns & Tradeoffs (2-3 mins)

> **What to say:** "I plan to use a few design patterns to keep this extensible and clean."

### Pattern 1: Strategy Pattern (Player Moves)

**Why:** Different player types (human, AI, network) need different move logic, but the game shouldn't care which type it's dealing with.

**How:** `PlayerStrategy` interface with a `make_move(board)` method. Each player type implements this differently.

**Tradeoff:** Adds a layer of indirection, but makes adding new player types trivial - just implement a new strategy class. No changes to existing code (Open/Closed Principle).

```
PlayerStrategy (ABC)
    |-- HumanPlayerStrategy  (reads from console input)
    |-- AIPlayerStrategy     (future: minimax, random, etc.)
```

### Pattern 2: State Pattern (Game Flow)

**Why:** The game has distinct states (X's turn, O's turn, X won, O won, Draw) and behavior changes based on state. Using if/else chains for state management becomes messy.

**How:** Each state is a class implementing `GameState`. A `GameContext` holds the current state and delegates transitions.

**Tradeoff:** More classes, but each state's logic is isolated and testable. Adding new states (e.g., "Paused") requires no changes to existing states.

```
GameState (ABC)
    |-- InProgressState  (game is still going)
    |-- WonState         (someone won, game over)
    |-- DrawState        (board full, no winner)
```

### Pattern 3: Observer Pattern (Event Notifications) - Extensibility

**Why:** When a move is made or game state changes, multiple things might need to react (UI update, logging, analytics). Hardcoding these into the Board couples everything.

**How:** Board maintains a list of listeners. After each move or state change, it notifies all listeners.

**Tradeoff:** Slight overhead for notification dispatch, but completely decouples the game logic from side effects.

> **Interview tip:** Mention Observer as an extensibility point, but don't implement it in the 20-min code unless asked. It shows you're thinking ahead.

### Pattern 4: Factory Pattern (Player Creation) - Extensibility

**Why:** As player types grow (Human, AI-Easy, AI-Hard, Network), centralizing creation logic keeps it manageable.

**How:** A `PlayerFactory` that takes a config/type and returns the right Player + Strategy combo.

**Tradeoff:** Overkill for two players, but demonstrates you know when and how to apply it.

> **Interview tip:** Same as Observer - mention it, but only code it if there's time.

---

## Step 4: Implementation - Full Design (Reference)

> This section is the complete, pattern-rich implementation. Use it to study the design. The 20-minute version is at the bottom.

### 4.1 Symbol Enum

```python
from enum import Enum

class Symbol(Enum):
    X = "X"
    O = "O"
    EMPTY = "."
```

### 4.2 Position

```python
class Position:
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

### 4.3 Board

```python
class Board:
    def __init__(self, rows: int = 3, cols: int = 3):
        self.rows = rows
        self.cols = cols
        self.grid = [[Symbol.EMPTY for _ in range(cols)] for _ in range(rows)]

    def is_valid_move(self, pos: Position) -> bool:
        return (0 <= pos.row < self.rows
                and 0 <= pos.col < self.cols
                and self.grid[pos.row][pos.col] == Symbol.EMPTY)

    def make_move(self, pos: Position, symbol: Symbol):
        self.grid[pos.row][pos.col] = symbol

    def is_full(self) -> bool:
        return all(self.grid[r][c] != Symbol.EMPTY
                   for r in range(self.rows) for c in range(self.cols))

    def check_winner(self) -> Symbol | None:
        """Returns the winning Symbol, or None if no winner yet."""
        # Check rows
        for r in range(self.rows):
            if self.grid[r][0] != Symbol.EMPTY and self._all_same(self.grid[r]):
                return self.grid[r][0]
        # Check columns
        for c in range(self.cols):
            col = [self.grid[r][c] for r in range(self.rows)]
            if col[0] != Symbol.EMPTY and self._all_same(col):
                return col[0]
        # Check diagonals
        diag1 = [self.grid[i][i] for i in range(min(self.rows, self.cols))]
        diag2 = [self.grid[i][self.cols - 1 - i] for i in range(min(self.rows, self.cols))]
        for diag in [diag1, diag2]:
            if diag[0] != Symbol.EMPTY and self._all_same(diag):
                return diag[0]
        return None

    def _all_same(self, line: list) -> bool:
        return all(s == line[0] for s in line)

    def print_board(self):
        for r in range(self.rows):
            row_str = " | ".join(s.value for s in self.grid[r])
            print(f" {row_str}")
            if r < self.rows - 1:
                print("---+" * (self.cols - 1) + "---")
        print()
```

### 4.4 Strategy Pattern - Player Strategies

```python
from abc import ABC, abstractmethod

class PlayerStrategy(ABC):
    @abstractmethod
    def make_move(self, board: Board) -> Position:
        pass


class HumanPlayerStrategy(PlayerStrategy):
    def __init__(self, name: str):
        self.name = name

    def make_move(self, board: Board) -> Position:
        while True:
            try:
                raw = input(f"{self.name}, enter row and col (e.g. 1 2): ")
                row, col = map(int, raw.split())
                pos = Position(row, col)
                if board.is_valid_move(pos):
                    return pos
                print("Invalid move. Cell is occupied or out of bounds.")
            except (ValueError, IndexError):
                print("Invalid input. Enter two numbers separated by space.")
```

### 4.5 Player

```python
class Player:
    def __init__(self, symbol: Symbol, strategy: PlayerStrategy):
        self.symbol = symbol
        self.strategy = strategy

    def get_move(self, board: Board) -> Position:
        return self.strategy.make_move(board)
```

### 4.6 State Pattern - Game States

```python
from abc import ABC, abstractmethod

class GameState(ABC):
    @abstractmethod
    def is_game_over(self) -> bool:
        pass

    @abstractmethod
    def status_message(self) -> str:
        pass


class InProgressState(GameState):
    def is_game_over(self) -> bool:
        return False

    def status_message(self) -> str:
        return "Game in progress"


class WonState(GameState):
    def __init__(self, winner: Symbol):
        self.winner = winner

    def is_game_over(self) -> bool:
        return True

    def status_message(self) -> str:
        return f"Player {self.winner.value} wins!"


class DrawState(GameState):
    def is_game_over(self) -> bool:
        return True

    def status_message(self) -> str:
        return "It's a draw!"


class GameContext:
    def __init__(self):
        self.state: GameState = InProgressState()

    def update_state(self, board: Board):
        winner = board.check_winner()
        if winner:
            self.state = WonState(winner)
        elif board.is_full():
            self.state = DrawState()
        # else stays InProgressState

    def is_game_over(self) -> bool:
        return self.state.is_game_over()
```

### 4.7 Game Controller

```python
class TicTacToeGame:
    def __init__(self, players: list[Player], rows: int = 3, cols: int = 3):
        self.board = Board(rows, cols)
        self.players = players
        self.current_index = 0
        self.context = GameContext()

    def play(self):
        while not self.context.is_game_over():
            self.board.print_board()
            current = self.players[self.current_index]
            print(f"Turn: Player {current.symbol.value}")

            move = current.get_move(self.board)
            self.board.make_move(move, current.symbol)
            self.context.update_state(self.board)

            self.current_index = (self.current_index + 1) % len(self.players)

        self.board.print_board()
        print(self.context.state.status_message())
```

### 4.8 Main Entry Point

```python
if __name__ == "__main__":
    p1 = Player(Symbol.X, HumanPlayerStrategy("Player X"))
    p2 = Player(Symbol.O, HumanPlayerStrategy("Player O"))
    game = TicTacToeGame([p1, p2])
    game.play()
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design is extensible in several ways..."

### 5.1 NxN Board Support

Already built in - `Board(rows, cols)` accepts any size. The win-check logic works for any grid.

### 5.2 AI Player

Just add a new strategy:

```python
import random

class RandomAIStrategy(PlayerStrategy):
    def make_move(self, board: Board) -> Position:
        empty = [Position(r, c)
                 for r in range(board.rows) for c in range(board.cols)
                 if board.grid[r][c] == Symbol.EMPTY]
        return random.choice(empty)
```

No changes to Board, Player, or Game. Open/Closed Principle.

### 5.3 Multiple Players (>2)

Already supported - `TicTacToeGame` takes a `list[Player]` and cycles through them with modulo.

### 5.4 Observer Pattern (Event Notifications)

```python
class GameEventListener(ABC):
    @abstractmethod
    def on_move_made(self, pos: Position, symbol: Symbol): pass

    @abstractmethod
    def on_game_over(self, state: GameState): pass

class ConsoleLogger(GameEventListener):
    def on_move_made(self, pos, symbol):
        print(f"[LOG] {symbol.value} placed at {pos}")

    def on_game_over(self, state):
        print(f"[LOG] Game ended: {state.status_message()}")
```

Add `listeners: list[GameEventListener]` to Board and call `notify()` after each move.

### 5.5 Factory Pattern

```python
class PlayerFactory:
    @staticmethod
    def create(symbol: Symbol, player_type: str, name: str = "") -> Player:
        if player_type == "human":
            return Player(symbol, HumanPlayerStrategy(name))
        elif player_type == "ai":
            return Player(symbol, RandomAIStrategy())
        raise ValueError(f"Unknown player type: {player_type}")
```

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | State understanding, ask clarifying questions | Show you don't jump into code |
| 2-5 min | Identify entities, draw relationships | Symbol, Position, Board, Player, Strategy, State, Game |
| 5-7 min | Discuss design patterns | Strategy (players), State (game flow), mention Observer & Factory |
| 7-22 min | Code the solution | Start with enums/models, then Board, then Game loop |
| 22-25 min | Discuss extensibility | NxN, AI, multiple players, Observer, Factory |

---

## FINAL CODE: Write This in 20 Minutes

> This is the **streamlined, interview-ready** version. It has everything needed: Strategy Pattern, State Pattern, move validation, win/draw detection, and extensibility. All in one file, ~120 lines.

```python
from enum import Enum
from abc import ABC, abstractmethod


# ---- Enums & Value Objects ----

class Symbol(Enum):
    X = "X"
    O = "O"
    EMPTY = "."


class Position:
    def __init__(self, row: int, col: int):
        self.row = row
        self.col = col


# ---- Board ----

class Board:
    def __init__(self, size: int = 3):
        self.size = size
        self.grid = [[Symbol.EMPTY] * size for _ in range(size)]

    def is_valid_move(self, pos: Position) -> bool:
        return (0 <= pos.row < self.size
                and 0 <= pos.col < self.size
                and self.grid[pos.row][pos.col] == Symbol.EMPTY)

    def make_move(self, pos: Position, symbol: Symbol):
        self.grid[pos.row][pos.col] = symbol

    def is_full(self) -> bool:
        return all(self.grid[r][c] != Symbol.EMPTY
                   for r in range(self.size) for c in range(self.size))

    def check_winner(self) -> Symbol | None:
        n = self.size
        lines = []
        # rows and cols
        for i in range(n):
            lines.append([self.grid[i][j] for j in range(n)])
            lines.append([self.grid[j][i] for j in range(n)])
        # diagonals
        lines.append([self.grid[i][i] for i in range(n)])
        lines.append([self.grid[i][n - 1 - i] for i in range(n)])

        for line in lines:
            if line[0] != Symbol.EMPTY and len(set(line)) == 1:
                return line[0]
        return None

    def display(self):
        for r in range(self.size):
            print(" | ".join(s.value for s in self.grid[r]))
            if r < self.size - 1:
                print("--+" * (self.size - 1) + "--")
        print()


# ---- Strategy Pattern: Player Moves ----

class PlayerStrategy(ABC):
    @abstractmethod
    def make_move(self, board: Board) -> Position:
        pass


class HumanPlayerStrategy(PlayerStrategy):
    def __init__(self, name: str):
        self.name = name

    def make_move(self, board: Board) -> Position:
        while True:
            try:
                row, col = map(int, input(f"{self.name}, enter row col: ").split())
                pos = Position(row, col)
                if board.is_valid_move(pos):
                    return pos
                print("Invalid move, try again.")
            except ValueError:
                print("Enter two numbers separated by space.")


# ---- Player ----

class Player:
    def __init__(self, symbol: Symbol, strategy: PlayerStrategy):
        self.symbol = symbol
        self.strategy = strategy

    def get_move(self, board: Board) -> Position:
        return self.strategy.make_move(board)


# ---- State Pattern: Game State ----

class GameState(ABC):
    @abstractmethod
    def is_over(self) -> bool: pass
    @abstractmethod
    def message(self) -> str: pass

class InProgress(GameState):
    def is_over(self): return False
    def message(self): return "Game in progress"

class Won(GameState):
    def __init__(self, symbol: Symbol):
        self.symbol = symbol
    def is_over(self): return True
    def message(self): return f"Player {self.symbol.value} wins!"

class Draw(GameState):
    def is_over(self): return True
    def message(self): return "It's a draw!"


# ---- Game Controller ----

class TicTacToeGame:
    def __init__(self, players: list, size: int = 3):
        self.board = Board(size)
        self.players = players
        self.turn = 0
        self.state: GameState = InProgress()

    def _update_state(self):
        winner = self.board.check_winner()
        if winner:
            self.state = Won(winner)
        elif self.board.is_full():
            self.state = Draw()

    def play(self):
        while not self.state.is_over():
            self.board.display()
            current = self.players[self.turn]
            print(f"Turn: {current.symbol.value}")

            move = current.get_move(self.board)
            self.board.make_move(move, current.symbol)
            self._update_state()

            self.turn = (self.turn + 1) % len(self.players)

        self.board.display()
        print(self.state.message())


# ---- Main ----

if __name__ == "__main__":
    p1 = Player(Symbol.X, HumanPlayerStrategy("Player X"))
    p2 = Player(Symbol.O, HumanPlayerStrategy("Player O"))
    TicTacToeGame([p1, p2]).play()
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| Move validation | `Board.is_valid_move()` |
| Win detection (row/col/diag) | `Board.check_winner()` |
| Draw detection | `Board.is_full()` |
| Strategy Pattern | `PlayerStrategy` ABC + `HumanPlayerStrategy` |
| State Pattern | `GameState` ABC + `InProgress`, `Won`, `Draw` |
| Extensibility: NxN board | `Board(size)` param |
| Extensibility: AI player | New strategy class, no other changes |
| Extensibility: N players | `list[Player]` + modulo turn cycling |
| Clean separation | Board logic, player logic, game flow all separate |

### Writing Order (for the interview)

1. **Symbol enum + Position** (~1 min) - smallest, no dependencies
2. **Board class** (~5 min) - grid, validation, move, winner check, display
3. **PlayerStrategy + HumanPlayerStrategy** (~3 min) - ABC + concrete
4. **Player** (~1 min) - simple wrapper
5. **GameState + InProgress/Won/Draw** (~3 min) - ABC + three small classes
6. **TicTacToeGame** (~4 min) - game loop, state updates
7. **Main** (~1 min) - wire it all together

**Total: ~18 minutes**
