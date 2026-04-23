# Low-Level Design: In-Memory Task / Job Scheduler (Multi-Threaded) — Uber SDE-2 Interview Guide

> **Timing target:** 10–12 min architecture discussion + 15–20 min coding = **~30–35 min**.

---

## Step 1: Understanding & Clarifying Requirements  *(~2 min)*

> **What to say:** "Let me restate the problem to make sure I'm solving the right thing before I jump into design."

### Core Understanding

- Build an **in-memory** job scheduler (no persistence required).
- Two public APIs:
  - `schedule(task, delay)` — run `task` **once** after `delay` seconds.
  - `scheduleAtFixedInterval(task, interval)` — run `task` **repeatedly** every `interval` seconds.
- A **configurable worker pool** actually executes the tasks — scheduling ≠ execution.
- System must be **thread-safe** (multiple producers, multiple consumers).

### Clarifying Questions to Ask the Interviewer

| # | Question | Why It Matters |
|---|----------|----------------|
| 1 | Is this in-memory only, or do we need persistence / crash-recovery? | Persistence pulls in WAL/DB — big scope change. |
| 2 | Single-process or distributed across machines? | Distributed = leader election, sharding, Redis/Zookeeper. Confirm scope. |
| 3 | Can a task be **cancelled** after scheduling? | Affects the `Task` API and the queue data structure. |
| 4 | What's the execution guarantee — **at-most-once**, **at-least-once**, **exactly-once**? | Drives retry & dedup logic. Default to at-least-once with idempotency. |
| 5 | Should recurring tasks run at **fixed rate** (every N sec regardless of duration) or **fixed delay** (N sec after previous finishes)? | Two different semantics; confirm which one. |
| 6 | What if a task throws? Retry? Swallow? Dead-letter? | Motivates the Strategy pattern for retries. |
| 7 | Do we need **priorities**, or is ordering purely by time? | Changes the priority-queue key. |
| 8 | Expected QPS and max concurrent tasks? | Sizing the worker pool + choice of data structure. |

> **Tip:** At Uber SDE-2 level, questions 4 and 5 are the discriminators — they show you've thought about real production semantics, not just a toy API.

### Assumed Scope (state this explicitly)

> "For this exercise I'll assume: single-process, in-memory, at-least-once, fixed-delay semantics for recurring, with optional retry strategy and cancel. I'll call out distribution and persistence as extensibility points at the end."

---

## Step 2: Identify Core Entities & Relationships  *(~3 min)*

> **What to say:** "Here are the main entities and how they collaborate."

### Entities

```
         +---------------------+
         |      Scheduler      |  ← public API: schedule / scheduleAtFixedInterval / cancel
         |  (Singleton-ish)    |
         +----------+----------+
                    |
        ┌───────────┼─────────────┐
        │           │             │
        ▼           ▼             ▼
 +-------------+  +-------------+ +----------------+
 | PriorityQ   |  | Dispatcher  | |  WorkerPool    |
 | (min-heap   |  | Thread      | |  (N threads)   |
 |  by runAt)  |  | (1 thread)  | +-------+--------+
 +-------------+  +------+------+         |
                         |                | executes
                         | pops ready     ▼
                         ▼          +-----------+
                    +---------+     |   Task    |
                    |  Task   | ◄───| (Command) |
                    +----+----+     +-----+-----+
                         |                │ uses
                         │ uses           ▼
                         ▼          +---------------+
                  +-------------+   | RetryStrategy |
                  | TaskStatus  |   | (Strategy)    |
                  +-------------+   +---------------+
```

### Relationship Summary

| Entity | Responsibility | Collaborators |
|--------|----------------|---------------|
| **Scheduler** | Public API. Accepts tasks, enqueues them, exposes cancel/shutdown. | PriorityQueue, Dispatcher, WorkerPool |
| **Task** | Encapsulates a runnable + its schedule metadata (runAt, interval, status). Command pattern. | RetryStrategy |
| **PriorityQueue** | Min-heap keyed by `next_run_time`. O(log n) insert/pop. | — |
| **Dispatcher** | Single thread that sleeps until the earliest task is due, then hands it to the worker pool. Producer-consumer mediator. | PriorityQueue, WorkerPool |
| **WorkerPool** | Configurable thread pool that actually runs tasks. | — |
| **RetryStrategy** | Pluggable policy for how to retry failed tasks. | — |
| **TaskStatus** | Enum: PENDING / RUNNING / COMPLETED / FAILED / CANCELLED. | — |

### Why separate Dispatcher from WorkerPool?

> **Say this:** "The dispatcher is a single thread whose only job is to wait until the next task is *due* and hand it off. The worker pool then executes it. This separation means a long-running task never blocks scheduling of other tasks — the dispatcher returns immediately after handoff."

---

## Step 3: Design Patterns & Tradeoffs  *(~4 min)*

> **What to say:** "I'd use four patterns here. Let me walk through each with the tradeoff."

### 3.1 Command Pattern — `Task`

**Problem:** The scheduler must store "work to do later" without knowing what the work is.

**Solution:** Wrap each submitted callable into a `Task` object carrying the runnable + metadata (id, runAt, interval, status, retry policy).

**Tradeoff:**
- **Pro:** Decouples scheduling from execution; easy to add fields (priority, owner, timeout) later.
- **Con:** Slight object overhead vs. storing raw callables — negligible.

### 3.2 Producer–Consumer — Scheduler + Dispatcher + WorkerPool

**Problem:** Multiple threads may call `schedule()` concurrently; multiple workers must pull tasks concurrently; scheduling shouldn't block execution.

**Solution:** A thread-safe priority queue sits between producers (user threads calling `schedule`) and the consumer (dispatcher). The dispatcher then offloads to a worker pool.

**Key primitive:** `threading.Condition` — lets the dispatcher `wait(timeout = runAt - now)` efficiently and be woken early when a new, earlier task arrives.

**Tradeoff:**
- **Pro:** Clean separation, no busy-wait, horizontally scales workers.
- **Con:** Single dispatcher is a theoretical bottleneck — but dispatch is O(log n) per task, so millions/sec are fine on one thread.

### 3.3 Strategy Pattern — Retry Policy

**Problem:** Different tasks want different retry behavior: none, fixed delay, exponential backoff.

**Solution:** `RetryStrategy` interface with `should_retry(attempt, error)` and `next_delay(attempt)`. Implementations: `NoRetry`, `FixedDelayRetry`, `ExponentialBackoffRetry`.

**Tradeoff:**
- **Pro:** Open/Closed — add a new policy without touching the scheduler.
- **Con:** One more abstraction — but this is exactly what interviewers want to see at SDE-2.

### 3.4 Singleton (mention verbally) — Scheduler

**Problem:** Typically one scheduler per process.

**Solution:** Expose a `get_instance()` or rely on module-level instance. **Don't code it** — just say it.

**Tradeoff:** Global state hurts testability. In prod I'd use DI; in an interview I mention both.

### Patterns I'd mention but not code

| Pattern | Where it fits | Why skip coding |
|---------|---------------|-----------------|
| **Observer** | Notify listeners on task completion/failure | Mention for "how would you build a UI dashboard" follow-up |
| **Builder** | Fluent `TaskBuilder().every(5).withRetry(...).build()` | Nice-to-have; distracts from core |
| **Factory** | `TaskFactory` for recurring vs. one-shot | Simple constructor args suffice |

---

## Step 4: Full Implementation in Python  *(reference — not the interview version)*

> **What to say:** "Let me build this bottom-up: enum → Task → RetryStrategy → WorkerPool → Scheduler."

### 4.1 TaskStatus — Enum

```python
from enum import Enum


class TaskStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
```

---

### 4.2 RetryStrategy — Strategy Pattern

```python
from abc import ABC, abstractmethod


class RetryStrategy(ABC):
    @abstractmethod
    def should_retry(self, attempt: int, error: Exception) -> bool: ...

    @abstractmethod
    def next_delay(self, attempt: int) -> float: ...


class NoRetry(RetryStrategy):
    def should_retry(self, attempt, error): return False
    def next_delay(self, attempt): return 0


class ExponentialBackoffRetry(RetryStrategy):
    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    def should_retry(self, attempt, error):
        return attempt < self.max_attempts

    def next_delay(self, attempt):
        return self.base_delay * (2 ** attempt)   # 1, 2, 4, 8 ...
```

---

### 4.3 Task — Command Pattern

```python
import uuid
import itertools
from typing import Callable, Optional


_seq = itertools.count()   # tie-breaker for heap equality


class Task:
    """
    A schedulable unit of work.
      - runnable:       the callable to execute
      - next_run_time:  absolute epoch seconds
      - interval:       None for one-shot, else seconds between runs
      - retry_strategy: pluggable retry policy
      - cancelled:      tombstone flag (lazy deletion from the heap)
    """

    def __init__(self,
                 runnable: Callable[[], None],
                 next_run_time: float,
                 interval: Optional[float] = None,
                 retry_strategy: Optional[RetryStrategy] = None):
        self.task_id = str(uuid.uuid4())
        self.runnable = runnable
        self.next_run_time = next_run_time
        self.interval = interval
        self.retry_strategy = retry_strategy or NoRetry()
        self.status = TaskStatus.PENDING
        self.cancelled = False
        self._seq = next(_seq)   # tie-breaker: FIFO among equal run times

    @property
    def is_recurring(self) -> bool:
        return self.interval is not None

    # Min-heap ordered by (next_run_time, _seq). _seq avoids comparing Task objects directly.
    def __lt__(self, other: "Task") -> bool:
        return (self.next_run_time, self._seq) < (other.next_run_time, other._seq)
```

> **Why `_seq`?** When two tasks share the same `next_run_time`, Python's `heapq` falls back to comparing the next tuple element. Without a tie-breaker it would try to compare `Task` objects and either raise or pick arbitrarily. This gives us **deterministic FIFO for equal run times** — a subtle correctness point that impresses interviewers.

---

### 4.4 WorkerPool

```python
from concurrent.futures import ThreadPoolExecutor


class WorkerPool:
    """Thin wrapper around ThreadPoolExecutor. Single responsibility: run callables."""

    def __init__(self, num_workers: int):
        self._executor = ThreadPoolExecutor(max_workers=num_workers,
                                            thread_name_prefix="worker")

    def submit(self, fn: Callable[[], None]):
        return self._executor.submit(fn)

    def shutdown(self, wait: bool = True):
        self._executor.shutdown(wait=wait)
```

> **Why use `ThreadPoolExecutor`?** In an interview, say: "I'd roll my own `BlockingQueue` + N worker threads to show I understand the primitives, but in Python I'd use `ThreadPoolExecutor` in prod — same design, battle-tested."

---

### 4.5 Scheduler — The Core

```python
import heapq
import threading
import time
from typing import Dict


class Scheduler:
    """
    In-memory, multi-threaded task scheduler.

    Architecture:
        producers ─► PriorityQueue ─► Dispatcher thread ─► WorkerPool (N threads)

    Thread-safety:
        A single Condition guards the heap and wakes the dispatcher when:
          (a) a new task is scheduled, or
          (b) shutdown is requested.
    """

    def __init__(self, num_workers: int = 4):
        self._heap: list[Task] = []
        self._tasks: Dict[str, Task] = {}
        self._cv = threading.Condition()
        self._pool = WorkerPool(num_workers)
        self._running = False
        self._dispatcher: Optional[threading.Thread] = None

    # ---------- Public API ----------

    def start(self) -> None:
        with self._cv:
            if self._running:
                return
            self._running = True
        self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                            name="dispatcher", daemon=True)
        self._dispatcher.start()

    def schedule(self, runnable: Callable[[], None],
                 delay: float,
                 retry_strategy: Optional[RetryStrategy] = None) -> str:
        """Run `runnable` once, `delay` seconds from now. Returns task_id."""
        return self._enqueue(Task(runnable, time.time() + delay,
                                  interval=None, retry_strategy=retry_strategy))

    def schedule_at_fixed_interval(self, runnable: Callable[[], None],
                                   interval: float,
                                   initial_delay: float = 0.0,
                                   retry_strategy: Optional[RetryStrategy] = None) -> str:
        """Run `runnable` every `interval` seconds (fixed delay semantics)."""
        return self._enqueue(Task(runnable, time.time() + initial_delay,
                                  interval=interval, retry_strategy=retry_strategy))

    def cancel(self, task_id: str) -> bool:
        """Mark task as cancelled. Lazy deletion — dispatcher skips it when popped."""
        with self._cv:
            task = self._tasks.get(task_id)
            if not task or task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return False
            task.cancelled = True
            task.status = TaskStatus.CANCELLED
            self._cv.notify_all()   # in case cancelled task was the head
            return True

    def shutdown(self, wait: bool = True) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        if self._dispatcher:
            self._dispatcher.join()
        self._pool.shutdown(wait=wait)

    # ---------- Internals ----------

    def _enqueue(self, task: Task) -> str:
        with self._cv:
            heapq.heappush(self._heap, task)
            self._tasks[task.task_id] = task
            self._cv.notify_all()   # wake dispatcher — new head may be earlier
        return task.task_id

    def _dispatch_loop(self) -> None:
        """
        Single-threaded loop:
          1. If heap empty -> wait.
          2. Peek head. If not due -> wait(timeout = runAt - now).
          3. If due -> pop, skip if cancelled, hand to worker pool.
          4. If recurring -> re-enqueue with next_run_time = now + interval.
        """
        while True:
            with self._cv:
                while self._running and not self._heap:
                    self._cv.wait()

                if not self._running:
                    return

                now = time.time()
                head = self._heap[0]

                if head.next_run_time > now:
                    self._cv.wait(timeout=head.next_run_time - now)
                    continue   # re-check — maybe an earlier task arrived, or we got cancelled

                task = heapq.heappop(self._heap)

                if task.cancelled:
                    continue

                if task.is_recurring:
                    # Fixed-delay recurring: reschedule BEFORE execution so missed ticks don't stack.
                    follow_up = Task(task.runnable,
                                     now + task.interval,
                                     interval=task.interval,
                                     retry_strategy=task.retry_strategy)
                    follow_up.task_id = task.task_id   # preserve id across runs
                    heapq.heappush(self._heap, follow_up)
                    self._tasks[task.task_id] = follow_up

            # Submit OUTSIDE the lock so slow submission can't block new schedules.
            self._pool.submit(lambda t=task: self._execute(t))

    def _execute(self, task: Task) -> None:
        attempt = 0
        while True:
            try:
                task.status = TaskStatus.RUNNING
                task.runnable()
                task.status = TaskStatus.COMPLETED
                return
            except Exception as err:
                task.status = TaskStatus.FAILED
                if not task.retry_strategy.should_retry(attempt, err):
                    return
                time.sleep(task.retry_strategy.next_delay(attempt))
                attempt += 1
```

> **Critical correctness points to call out:**
> 1. The dispatcher **releases the lock** before submitting — otherwise a slow pool can block scheduling.
> 2. Recurring tasks are **re-enqueued before execution** so execution time doesn't drift the schedule.
> 3. `cancel()` uses **lazy deletion** — O(1) instead of O(n) heap search.
> 4. `_cv.wait(timeout=...)` wakes early if a newly-scheduled earlier task calls `notify_all()`.

---

### 4.6 Main — Quick Smoke Test

```python
def main():
    scheduler = Scheduler(num_workers=3)
    scheduler.start()

    scheduler.schedule(lambda: print(f"[{time.time():.1f}] one-shot"), delay=2)

    tick = 0
    def heartbeat():
        nonlocal tick
        tick += 1
        print(f"[{time.time():.1f}] heartbeat #{tick}")

    rec_id = scheduler.schedule_at_fixed_interval(heartbeat, interval=1.0)

    time.sleep(5)
    scheduler.cancel(rec_id)
    time.sleep(1)
    scheduler.shutdown()


if __name__ == "__main__":
    main()
```

---

## Step 5: Extensibility Discussion  *(~1 min)*

> **What to say:** "Here's how this design absorbs common follow-ups."

| Future Requirement | How the design handles it |
|--------------------|---------------------------|
| **Persistence / crash recovery** | Replace the in-memory heap with a durable store (Redis `ZSET`, Postgres, or a WAL). Dispatcher logic is unchanged. |
| **Distributed scheduler** | Shard tasks by `task_id % N`; use Redis/Zookeeper for leader election so exactly one dispatcher per shard pops. |
| **Task priorities** | Change heap key from `next_run_time` to `(next_run_time, -priority)`. |
| **Fixed-rate vs. fixed-delay** | Toggle per task: fixed-rate re-enqueues at `prev_run_time + interval`; fixed-delay at `now + interval`. |
| **Dead-letter queue** | In `_execute`, after retries exhausted, push to a `dlq` list instead of dropping. |
| **Metrics / Observability** | Introduce an `Observer` interface (`on_scheduled`, `on_started`, `on_completed`, `on_failed`). |
| **Timeouts per task** | Wrap `task.runnable()` in `concurrent.futures.wait(timeout=...)`; cancel on timeout. |

---

## Step 6: Complexity Analysis

| Operation | Time | Why |
|-----------|------|-----|
| `schedule` / `schedule_at_fixed_interval` | O(log n) | Heap push |
| Dispatcher pop | O(log n) | Heap pop |
| `cancel` | O(1) | Lazy tombstone — actual removal happens when task surfaces at heap head |
| Space | O(n) | n = live tasks in the queue |

---

## Quick Reference: Interview Flow

```
[1] Restate the problem, confirm understanding            ~2 min
[2] Ask clarifying questions (persistence? semantics?)    ~2 min
[3] Identify entities: Scheduler, Task, Dispatcher,       ~2 min
    WorkerPool, PriorityQueue, RetryStrategy
[4] Draw the producer-consumer diagram                    ~1 min
[5] Discuss patterns + tradeoffs                          ~3 min
     - Command   → Task
     - Producer/Consumer  → Scheduler + Dispatcher + Pool
     - Strategy  → Retry
     - Singleton → Scheduler (verbal only)
[6] Code the solution bottom-up                          ~18 min
     TaskStatus → RetryStrategy → Task → WorkerPool → Scheduler → main
[7] Walk through a trace (1 one-shot + 1 recurring)       ~2 min
[8] Extensibility: persistence, distribution, DLQ         ~2 min
```

---

## Key Talking Points for Uber SDE-2

1. **Thread-safety is the core challenge, not the heap.** The `Condition` + `notify_all()` pattern is what makes this scalable and correct.
2. **Dispatch ≠ Execute.** Keeping them on separate threads means one slow task doesn't block scheduling — Uber runs this everywhere (dispatch ≠ driver matching, pricing ≠ request handling).
3. **Lazy cancellation** is the right call at scale — O(1) cancel beats O(n) heap search 100% of the time.
4. **Fixed-delay before execution** — re-enqueue before running so retries/slow runs don't drift the schedule. This is the kind of detail that separates an SDE-1 from SDE-2.
5. **SOLID call-outs:**
   - *SRP*: Scheduler dispatches, Pool executes, Task stores state.
   - *OCP*: Add a new `RetryStrategy` — zero changes to `Scheduler`.
   - *DIP*: Scheduler depends on the `RetryStrategy` abstraction.

---

## Final Code — Write This in 15–20 Minutes

> **What to actually code in the interview.** Lean version. Mention Observer/Builder/Singleton verbally.
>
> **Writing order:** `TaskStatus` → `RetryStrategy` → `Task` → `WorkerPool` → `Scheduler` → `main`

```python
import heapq
import itertools
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Callable, Dict, Optional


# ──────────────────────────────────────────────
# 1. TaskStatus
# ──────────────────────────────────────────────
class TaskStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ──────────────────────────────────────────────
# 2. RetryStrategy  (Strategy pattern)
# ──────────────────────────────────────────────
class RetryStrategy(ABC):
    @abstractmethod
    def should_retry(self, attempt: int, error: Exception) -> bool: ...
    @abstractmethod
    def next_delay(self, attempt: int) -> float: ...


class NoRetry(RetryStrategy):
    def should_retry(self, attempt, error): return False
    def next_delay(self, attempt): return 0


class ExponentialBackoffRetry(RetryStrategy):
    def __init__(self, max_attempts=3, base_delay=1.0):
        self.max_attempts, self.base_delay = max_attempts, base_delay
    def should_retry(self, attempt, error): return attempt < self.max_attempts
    def next_delay(self, attempt): return self.base_delay * (2 ** attempt)


# ──────────────────────────────────────────────
# 3. Task  (Command pattern)
# ──────────────────────────────────────────────
_seq = itertools.count()


class Task:
    def __init__(self, runnable: Callable[[], None], next_run_time: float,
                 interval: Optional[float] = None,
                 retry_strategy: Optional[RetryStrategy] = None):
        self.task_id = str(uuid.uuid4())
        self.runnable = runnable
        self.next_run_time = next_run_time
        self.interval = interval
        self.retry_strategy = retry_strategy or NoRetry()
        self.status = TaskStatus.PENDING
        self.cancelled = False
        self._seq = next(_seq)

    @property
    def is_recurring(self): return self.interval is not None

    def __lt__(self, other):
        return (self.next_run_time, self._seq) < (other.next_run_time, other._seq)


# ──────────────────────────────────────────────
# 4. WorkerPool
# ──────────────────────────────────────────────
class WorkerPool:
    def __init__(self, n: int):
        self._ex = ThreadPoolExecutor(max_workers=n, thread_name_prefix="worker")

    def submit(self, fn): return self._ex.submit(fn)
    def shutdown(self, wait=True): self._ex.shutdown(wait=wait)


# ──────────────────────────────────────────────
# 5. Scheduler  (core)
# ──────────────────────────────────────────────
class Scheduler:
    def __init__(self, num_workers: int = 4):
        self._heap: list[Task] = []
        self._tasks: Dict[str, Task] = {}
        self._cv = threading.Condition()
        self._pool = WorkerPool(num_workers)
        self._running = False
        self._dispatcher: Optional[threading.Thread] = None

    # ---- Public API ----
    def start(self):
        with self._cv:
            if self._running: return
            self._running = True
        self._dispatcher = threading.Thread(target=self._loop,
                                            name="dispatcher", daemon=True)
        self._dispatcher.start()

    def schedule(self, runnable, delay: float, retry=None) -> str:
        return self._enqueue(Task(runnable, time.time() + delay,
                                  None, retry))

    def schedule_at_fixed_interval(self, runnable, interval: float,
                                   initial_delay: float = 0.0, retry=None) -> str:
        return self._enqueue(Task(runnable, time.time() + initial_delay,
                                  interval, retry))

    def cancel(self, task_id: str) -> bool:
        with self._cv:
            t = self._tasks.get(task_id)
            if not t or t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return False
            t.cancelled = True
            t.status = TaskStatus.CANCELLED
            self._cv.notify_all()
            return True

    def shutdown(self, wait=True):
        with self._cv:
            self._running = False
            self._cv.notify_all()
        if self._dispatcher: self._dispatcher.join()
        self._pool.shutdown(wait=wait)

    # ---- Internals ----
    def _enqueue(self, task: Task) -> str:
        with self._cv:
            heapq.heappush(self._heap, task)
            self._tasks[task.task_id] = task
            self._cv.notify_all()
        return task.task_id

    def _loop(self):
        while True:
            with self._cv:
                while self._running and not self._heap:
                    self._cv.wait()
                if not self._running: return

                now = time.time()
                head = self._heap[0]
                if head.next_run_time > now:
                    self._cv.wait(timeout=head.next_run_time - now)
                    continue

                task = heapq.heappop(self._heap)
                if task.cancelled:
                    continue

                if task.is_recurring:
                    nxt = Task(task.runnable, now + task.interval,
                               task.interval, task.retry_strategy)
                    nxt.task_id = task.task_id          # preserve id across runs
                    heapq.heappush(self._heap, nxt)
                    self._tasks[task.task_id] = nxt

            # Submit OUTSIDE the lock
            self._pool.submit(lambda t=task: self._execute(t))

    def _execute(self, task: Task):
        attempt = 0
        while True:
            try:
                task.status = TaskStatus.RUNNING
                task.runnable()
                task.status = TaskStatus.COMPLETED
                return
            except Exception as err:
                task.status = TaskStatus.FAILED
                if not task.retry_strategy.should_retry(attempt, err):
                    return
                time.sleep(task.retry_strategy.next_delay(attempt))
                attempt += 1


# ──────────────────────────────────────────────
# 6. Main
# ──────────────────────────────────────────────
if __name__ == "__main__":
    s = Scheduler(num_workers=3)
    s.start()

    s.schedule(lambda: print(f"[{time.time():.1f}] one-shot"), delay=2)

    tick = [0]
    def hb():
        tick[0] += 1
        print(f"[{time.time():.1f}] heartbeat #{tick[0]}")

    rid = s.schedule_at_fixed_interval(hb, interval=1.0)
    time.sleep(5)
    s.cancel(rid)
    time.sleep(1)
    s.shutdown()
```

### Writing Order on the Whiteboard

| Order | What to write | Time |
|-------|--------------|------|
| 1 | `TaskStatus` enum — 5 lines | 0.5 min |
| 2 | `RetryStrategy` ABC + `NoRetry` + `ExponentialBackoffRetry` | 3 min |
| 3 | `Task` — remember `__lt__` with `_seq` tie-breaker | 3 min |
| 4 | `WorkerPool` — 5-line ThreadPoolExecutor wrapper | 1 min |
| 5 | `Scheduler.__init__`, `start`, `schedule`, `schedule_at_fixed_interval` | 4 min |
| 6 | `Scheduler._loop` — the star of the show | 5 min |
| 7 | `Scheduler._execute` + `cancel` + `shutdown` | 2 min |
| 8 | `main` with one-shot + recurring trace | 1.5 min |
| | **Total** | **~20 min** |

### What to Say vs. What to Code

| Say it (don't code it) | Code it |
|------------------------|---------|
| Singleton for `Scheduler` | Plain `Scheduler` class |
| Observer for metrics/UI | `print` in `main` |
| Builder for fluent Task creation | Direct constructor args |
| Persistence via Redis/WAL | In-memory heap |
| Distributed with leader election | Single process |
| `DeadLetterQueue` | Just return on final failure |

---

## Follow-up Questions & Answers

> **What to say:** "I've designed for these — want me to walk through any?"

### Q1. "How do you cancel a task already inside the heap without breaking heap invariants?"

> **Answer:** Lazy tombstone. I flip `task.cancelled = True` in O(1). The dispatcher checks the flag after popping and skips it. Cost: cancelled tasks linger in memory until their scheduled time arrives. For high cancel rates, I'd periodically rebuild the heap (stop-the-world O(n)) or switch to an indexed priority queue (O(log n) remove by key) using a dict `task_id -> heap_index` plus `sift_down`/`sift_up`. Tradeoff is extra bookkeeping.

### Q2. "What's the difference between fixed-rate and fixed-delay, and which did you implement?"

> **Answer:** **Fixed-delay** = `next_run_time = now + interval` (measured from *end* of previous run). **Fixed-rate** = `next_run_time = scheduled_run_time + interval` (measured from *start* of previous scheduled run, regardless of duration). I implemented fixed-delay because it prevents "task pile-up" when a run overruns its interval. To support fixed-rate, I'd store `original_run_time` on the task and add an enum `ScheduleMode`. If a fixed-rate task runs long and misses N ticks, the policy question becomes: "run all N now, skip them, or coalesce into one?" — usually coalesce.

### Q3. "A task throws. What happens?"

> **Answer:** `_execute` catches, marks FAILED, consults `RetryStrategy`. If it says retry, sleeps `next_delay(attempt)` and re-runs *on the same worker thread* (ties up one worker — acceptable for bounded retries). For long retry chains I'd re-schedule the task back into the heap with `next_run_time = now + delay` so the worker is freed — trade: more scheduler churn. After retries exhausted I'd push to a dead-letter queue for inspection rather than silently dropping.

### Q4. "What if I schedule a task *before* calling `start()`?"

> **Answer:** Works fine — `_enqueue` only touches the heap + condition variable, not any thread state. When `start()` finally fires, the dispatcher immediately sees the non-empty heap and processes it. I'd call that out in `start()` docs. Downside: there's no "validation" point; an invalid callable wouldn't surface until execution.

### Q5. "How do you make this horizontally scalable / distributed?"

> **Answer:** Three changes:
> 1. Replace the in-memory heap with **Redis sorted set** (`ZADD` by `next_run_time`, `ZRANGEBYSCORE` to pop due tasks).
> 2. Use **Redis/Zookeeper leader election** so exactly one dispatcher per shard is active.
> 3. **Shard by `hash(task_id) % N`** so each shard has an independent dispatcher + worker pool.
>
> Worker pool itself can stay per-node — pull from the shared queue. This is roughly how Quartz clustered mode and AWS EventBridge work.

### Q6. "How do you guarantee exactly-once execution?"

> **Answer:** Pure exactly-once is impossible across a fault-tolerant distributed system. In practice:
> - **At-least-once** delivery (retry on worker crash) + **idempotent tasks** (task.runnable is deterministic, or uses an external idempotency key table).
> - For single-node in-memory: if I atomically move the task out of the heap before executing, a worker crash loses the task. If I keep it and mark COMPLETED after, a scheduler crash replays it. I'd choose the latter and rely on task idempotency.

### Q7. "Why a single dispatcher thread? Isn't that a bottleneck?"

> **Answer:** Dispatch is O(log n) per task — on commodity hardware that's **millions of tasks per second**. The bottleneck is always execution, not dispatch. A single dispatcher also avoids the ugly race of two threads popping the same head. If I ever needed multiple dispatchers (truly massive scale), I'd shard the heap by `task_id hash` — same pattern as distributed.

### Q8. "How do you handle a task that runs longer than its interval?"

> **Answer:** Because I re-enqueue *before* execution with `next_run_time = now + interval`, and `now` is the scheduled pop time (not end-of-run), the next run is scheduled as expected. If the run overruns, the worker is still busy when the next scheduled time arrives — the dispatcher pops it and hands it to *another* worker. So recurring tasks can overlap unless I explicitly guard with a per-task lock or `CAS on task.status == RUNNING` to skip the new run. I'd expose that as a per-task flag: `allow_concurrent=False`.

### Q9. "Where is the GIL an issue here?"

> **Answer:** For CPU-bound tasks, yes — Python threads serialize on the GIL, so increasing `num_workers` past 1 won't help. For I/O-bound tasks (HTTP calls, DB queries — the common scheduler workload), the GIL releases on blocking calls, and threads scale well. For CPU-heavy tasks I'd swap `ThreadPoolExecutor` for `ProcessPoolExecutor` behind the same `WorkerPool` interface — one-line change. This is why the `WorkerPool` abstraction pays off.

### Q10. "What tests would you write?"

> **Answer:**
> 1. **Ordering** — schedule three tasks with different delays in reverse order, assert they fire in time order.
> 2. **Recurring** — interval task fires ~N times in N×interval seconds (±tolerance).
> 3. **Cancel** — cancel before run → task never executes; cancel a recurring task → stops firing.
> 4. **Concurrency** — 100 threads each calling `schedule()` → all 100 tasks run.
> 5. **Retry** — task throws twice, succeeds third time → `ExponentialBackoffRetry(max_attempts=3)` lets it run.
> 6. **Shutdown** — `shutdown()` stops dispatcher, flushes in-flight tasks, doesn't hang.
> 7. **Early wake** — schedule a 10-min task, then schedule a 1-sec task → 1-sec fires in ~1 sec (confirms `notify_all` wakes dispatcher).

### Q11. "Can you make it work without a dedicated dispatcher thread?"

> **Answer:** Yes — use `sched` (Python stdlib) or a single executor where each worker, after finishing, pops the next task. But this entangles dispatch and execution and complicates early-wake on new schedules. I'd only do this for extreme minimalism. The separate dispatcher is cleaner and scales better.

### Q12. "How would you add priorities?"

> **Answer:** Change heap key from `(next_run_time, _seq)` to `(next_run_time, -priority, _seq)`. Higher-priority tasks with the same run time run first. **But priority only matters among tasks due *at the same time*** — you can't preempt a task that's already scheduled later just because a higher-priority one arrives. If the interviewer wants preemption across times, that's a different system (a real-time scheduler with deadlines), and I'd push back on scope.
