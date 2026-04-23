# Low-Level Design: Website Page-Visit Counter (Single + Multi-Threaded) - Uber SDE-2 Interview Guide

> **Problem:** Hundreds of users visit webpages of a website simultaneously. Record visit count per page and return them on demand. Extension: handle concurrent increments safely.

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me restate the problem and clarify a few things before I pick a design."

### Your Understanding (State This)

- A website has many pages, each identified by a URL (or a page id).
- When a user visits a page, we need to record a hit.
- At any time, we need to return the current hit count for any page.
- The system will see concurrent hits — hundreds of threads/requests incrementing counters simultaneously.
- The extension requires that concurrent increments do not lose updates (no race conditions).

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Do we need exact counts, or are approximate counts (e.g., ±1%) acceptable? | Exact for now. Mention HyperLogLog / Count-Min as extensions. |
| 2 | Single machine (in-memory) or distributed across servers? | Single-process first. Design should allow swapping to Redis later. |
| 3 | Do we need persistence (survive restart) or in-memory only? | In-memory is fine for the core design; mention snapshotting as extension. |
| 4 | Is the page identifier a URL string, or an opaque page id? | A string is fine — treat it as an opaque key. |
| 5 | Do we need time-windowed counts (e.g., hits in last 5 min) or all-time only? | All-time for core. Mention sliding-window counter as extension. |
| 6 | Read/write ratio? | Writes dominate (every visit is a write). Reads are ad-hoc (dashboards, APIs). |
| 7 | Do we need to list unknown pages, or only pages we've seen? | Unknown page → count is 0. Auto-create on first hit. |
| 8 | Expected peak QPS? | Hundreds of concurrent threads — optimize for write throughput under contention. |

### Confirmed Requirements

1. `record_hit(page_id)` — increment hit count for a page.
2. `get_hits(page_id) -> int` — return current count (0 for unknown page).
3. `get_all_hits() -> dict[str, int]` — snapshot of all pages.
4. **Thread-safe** — no lost updates under concurrent writes.
5. **Extensible** — single-threaded, locked, and sharded-lock concurrency modes should be swappable.
6. **Storage-pluggable** — in-memory today, Redis/DB tomorrow, with no changes to the service layer.

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Here are the entities. I'm deliberately separating storage from concurrency control — that's the key abstraction."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **Page** | Value object: holds the page id (URL / slug). |
| **CounterStorage (Interface)** | Abstracts where counts live (in-memory dict, Redis, DB). Only knows read/write of raw counts. |
| **InMemoryStorage** | Concrete storage backed by a dict. |
| **ConcurrencyStrategy (Interface)** | Abstracts HOW increments are made safe. Wraps storage ops with the right synchronization. |
| **SingleThreadedStrategy** | No locking. Fastest. Unsafe under concurrency — for single-writer scenarios. |
| **LockedStrategy** | One global `threading.Lock` protects all ops. Safe but serialized. |
| **StripedLockStrategy** | N locks, page-id → lock via hash. Safe AND high concurrency. |
| **HitCounterService (Facade + Singleton)** | Public API: `record_hit`, `get_hits`, `get_all_hits`. Delegates to storage via strategy. |
| **HitCounterFactory** | Builds a service wired with the right storage + strategy (extensibility point). |
| **HitEventListener (Interface)** *(extension)* | Notified on every hit — analytics, alerts, logs. Observer pattern. |
| **TimeWindowCounter** *(extension)* | Rolling counts over a time window — mentioned, not implemented in core. |

### Relationships

```
HitCounterService (Facade + Singleton)
    |
    |--- has-a ---> ConcurrencyStrategy   (how to synchronize)
    |                     |
    |                     +--- wraps ---> CounterStorage  (where counts live)
    |
    |--- has-a ---> List[HitEventListener]   (optional: Observer)


ConcurrencyStrategy (ABC)
    |-- SingleThreadedStrategy   (no lock)
    |-- LockedStrategy           (one global lock)
    |-- StripedLockStrategy      (N locks, hash-sharded)


CounterStorage (ABC)
    |-- InMemoryStorage          (dict)
    |-- RedisStorage             (future)
    |-- DatabaseStorage          (future)
```

> **Key insight to call out:** "Storage answers *where*, Strategy answers *how safely*. Keeping them orthogonal means I can mix any storage with any concurrency mode."

---

## Step 3: Design Patterns & Tradeoffs (2-3 mins)

> **What to say:** "I'm using a small set of well-understood patterns. The central one is Strategy for concurrency — it's exactly what the extension asks for."

### Pattern 1: Strategy Pattern (Concurrency Control) — PRIMARY

**Why:** The problem has two versions — single-threaded and multi-threaded. That's textbook Strategy: same interface, swappable behavior. It also lets me evolve from "one big lock" to "striped locks" without touching the service.

**How:** `ConcurrencyStrategy` ABC with `increment(page_id)` and `get(page_id)`. Each strategy wraps a `CounterStorage` and applies the right synchronization.

**Tradeoff:** Adds a class per mode, but every mode is isolated and testable. Open/Closed: adding an async/asyncio strategy later requires zero changes to service or storage.

```
ConcurrencyStrategy (ABC)
    |-- SingleThreadedStrategy   (no lock, fastest, unsafe for concurrent writes)
    |-- LockedStrategy           (one Lock — safe, but writes serialize)
    |-- StripedLockStrategy      (N locks hashed by page-id — safe AND concurrent)
```

### Pattern 2: Strategy Pattern (Storage Backend)

**Why:** The counter could live in memory, Redis, DynamoDB, or a SQL DB. The service shouldn't know.

**How:** `CounterStorage` ABC with `get`, `set`, `incr`, `snapshot`. `InMemoryStorage` for now; Redis/DB swap-in later.

**Tradeoff:** Extra interface for a one-liner dict today, but essential for the "scale to distributed" follow-up.

### Pattern 3: Facade + Singleton (HitCounterService)

**Why:** Callers (web handlers, dashboards) want a dead-simple API: `record_hit(page)`. They shouldn't see storage or concurrency internals. And there should be exactly one counter per process.

**How:** `HitCounterService` exposes three methods and delegates everything. A singleton via classmethod `get_instance()`.

**Tradeoff:** Singleton is global state — hard to test if misused. Mitigation: accept storage + strategy via constructor injection so tests can build isolated instances; `get_instance()` is just a convenience.

### Pattern 4: Factory (Wiring)

**Why:** Deciding "in-memory + striped locks with 16 shards" shouldn't be hardcoded at every call site.

**How:** `HitCounterFactory.create(mode="multi_threaded", shards=16)` returns a fully wired service.

**Tradeoff:** Minor overhead, but makes configuration changes a one-line swap.

### Pattern 5: Observer (Extensibility — Mention Only)

**Why:** Many things might want to react to a hit: real-time analytics, alerting on spikes, audit logs. Coupling these into the service kills modularity.

**How:** Service keeps `list[HitEventListener]`; notifies after every successful hit.

**Tradeoff:** Adds notification overhead. Keep listeners fast or dispatch async.

> **Interview tip:** Strategy (concurrency) is the star — spend most of your pattern-time here because it directly answers the multi-threaded extension. Mention Observer as extensibility, don't implement in 20-min code unless asked.

### Why NOT just use `collections.Counter` or `dict[str, int] += 1`?

This is the single most important thing to address. State it clearly:

> "In Python, `counter[page] += 1` is **not atomic**. It decomposes into a load, an add, and a store. The GIL prevents bytecode interleaving, but not these three bytecodes in a row — a thread switch between them causes lost updates. I verified this with a threading stress test. That's exactly why we need an explicit `Lock` or an atomic-CAS primitive."

---

## Step 4: Implementation - Full Design (Reference)

> Study this for depth. The 20-minute interview version is at the bottom.

### 4.1 Page (Value Object)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Page:
    page_id: str   # URL or opaque slug

    def __str__(self) -> str:
        return self.page_id
```

### 4.2 Storage Interface

```python
from abc import ABC, abstractmethod

class CounterStorage(ABC):
    @abstractmethod
    def incr(self, page_id: str) -> int:
        """Atomically-from-storage's-perspective increment and return new value."""

    @abstractmethod
    def get(self, page_id: str) -> int:
        """Return current count, 0 if unknown."""

    @abstractmethod
    def snapshot(self) -> dict[str, int]:
        """Return a copy of all counts."""
```

### 4.3 In-Memory Storage

```python
class InMemoryStorage(CounterStorage):
    """Raw storage — NOT thread-safe on its own. ConcurrencyStrategy adds safety."""
    def __init__(self):
        self._counts: dict[str, int] = {}

    def incr(self, page_id: str) -> int:
        self._counts[page_id] = self._counts.get(page_id, 0) + 1
        return self._counts[page_id]

    def get(self, page_id: str) -> int:
        return self._counts.get(page_id, 0)

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)   # shallow copy
```

### 4.4 Concurrency Strategy Interface

```python
class ConcurrencyStrategy(ABC):
    def __init__(self, storage: CounterStorage):
        self.storage = storage

    @abstractmethod
    def increment(self, page_id: str) -> int: pass

    @abstractmethod
    def get(self, page_id: str) -> int: pass

    @abstractmethod
    def snapshot(self) -> dict[str, int]: pass
```

### 4.5 Single-Threaded Strategy

```python
class SingleThreadedStrategy(ConcurrencyStrategy):
    """No locking. Use only when you guarantee a single writer."""
    def increment(self, page_id: str) -> int:
        return self.storage.incr(page_id)

    def get(self, page_id: str) -> int:
        return self.storage.get(page_id)

    def snapshot(self) -> dict[str, int]:
        return self.storage.snapshot()
```

### 4.6 Global-Lock Strategy

```python
import threading

class LockedStrategy(ConcurrencyStrategy):
    """One lock guards all ops. Safe but all writes serialize through one mutex."""
    def __init__(self, storage: CounterStorage):
        super().__init__(storage)
        self._lock = threading.Lock()

    def increment(self, page_id: str) -> int:
        with self._lock:
            return self.storage.incr(page_id)

    def get(self, page_id: str) -> int:
        with self._lock:
            return self.storage.get(page_id)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return self.storage.snapshot()
```

### 4.7 Striped-Lock Strategy (Scaling Answer)

```python
class StripedLockStrategy(ConcurrencyStrategy):
    """
    N locks, page_id -> lock via hash.
    Writes to different stripes run in parallel.
    Tradeoff: snapshot needs all locks (or a lock-free "best-effort" snapshot).
    """
    def __init__(self, storage: CounterStorage, num_stripes: int = 16):
        super().__init__(storage)
        self._locks = [threading.Lock() for _ in range(num_stripes)]
        self._n = num_stripes

    def _lock_for(self, page_id: str) -> threading.Lock:
        return self._locks[hash(page_id) % self._n]

    def increment(self, page_id: str) -> int:
        with self._lock_for(page_id):
            return self.storage.incr(page_id)

    def get(self, page_id: str) -> int:
        with self._lock_for(page_id):
            return self.storage.get(page_id)

    def snapshot(self) -> dict[str, int]:
        # Acquire ALL locks for a consistent snapshot.
        # Always acquire in the same order to avoid deadlock.
        for lk in self._locks:
            lk.acquire()
        try:
            return self.storage.snapshot()
        finally:
            for lk in self._locks:
                lk.release()
```

### 4.8 HitCounterService (Facade + Singleton)

```python
class HitCounterService:
    _instance: "HitCounterService | None" = None
    _singleton_lock = threading.Lock()

    def __init__(self, strategy: ConcurrencyStrategy):
        self._strategy = strategy
        self._listeners: list["HitEventListener"] = []

    @classmethod
    def get_instance(cls, strategy: ConcurrencyStrategy | None = None) -> "HitCounterService":
        # Double-checked locking singleton.
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    if strategy is None:
                        raise ValueError("First call must provide a strategy")
                    cls._instance = cls(strategy)
        return cls._instance

    def record_hit(self, page_id: str) -> int:
        new_count = self._strategy.increment(page_id)
        for lst in self._listeners:
            lst.on_hit(page_id, new_count)
        return new_count

    def get_hits(self, page_id: str) -> int:
        return self._strategy.get(page_id)

    def get_all_hits(self) -> dict[str, int]:
        return self._strategy.snapshot()

    def register_listener(self, listener: "HitEventListener") -> None:
        self._listeners.append(listener)
```

### 4.9 Factory (Wiring)

```python
class HitCounterFactory:
    @staticmethod
    def create(mode: str = "striped", shards: int = 16) -> HitCounterService:
        storage = InMemoryStorage()
        if mode == "single":
            strategy = SingleThreadedStrategy(storage)
        elif mode == "locked":
            strategy = LockedStrategy(storage)
        elif mode == "striped":
            strategy = StripedLockStrategy(storage, num_stripes=shards)
        else:
            raise ValueError(f"Unknown mode: {mode}")
        return HitCounterService(strategy)
```

### 4.10 Observer (Extensibility)

```python
class HitEventListener(ABC):
    @abstractmethod
    def on_hit(self, page_id: str, new_count: int) -> None: pass


class ConsoleLogger(HitEventListener):
    def on_hit(self, page_id: str, new_count: int) -> None:
        print(f"[hit] {page_id} -> {new_count}")


class SpikeAlerter(HitEventListener):
    def __init__(self, threshold: int):
        self.threshold = threshold

    def on_hit(self, page_id: str, new_count: int) -> None:
        if new_count == self.threshold:
            print(f"[ALERT] {page_id} crossed {self.threshold} hits")
```

### 4.11 Main

```python
if __name__ == "__main__":
    service = HitCounterFactory.create(mode="striped", shards=16)
    service.register_listener(ConsoleLogger())

    for _ in range(3):
        service.record_hit("/home")
    service.record_hit("/about")

    print(service.get_hits("/home"))          # 3
    print(service.get_hits("/pricing"))       # 0 (unknown page)
    print(service.get_all_hits())             # {'/home': 3, '/about': 1}
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design is extensible along several axes..."

### 5.1 Distributed Counter (Redis)

Swap `InMemoryStorage` for `RedisStorage` using `INCR` — already atomic, so you pair it with `SingleThreadedStrategy` (Redis handles concurrency server-side).

```python
class RedisStorage(CounterStorage):
    def __init__(self, client): self._r = client
    def incr(self, page_id): return self._r.incr(f"hits:{page_id}")
    def get(self, page_id):  return int(self._r.get(f"hits:{page_id}") or 0)
    def snapshot(self):      # SCAN keys — expensive, use sparingly
        return {k.decode().split(":",1)[1]: int(self._r.get(k))
                for k in self._r.scan_iter("hits:*")}
```

### 5.2 Persistence

Add a `PersistenceListener` that batches counts to disk every N seconds or M hits. Crash recovery reads the snapshot at startup. The core service doesn't care.

### 5.3 Time-Windowed Counters

Replace `int` with a ring buffer of per-second bucket counts. `get_hits_last(seconds)` sums the last N buckets. Classic sliding-window counter.

### 5.4 Top-K Pages

Maintain a heap keyed by count in a listener; or periodically sort the snapshot. For very large scale, use Count-Min Sketch + min-heap.

### 5.5 Approximate Counts at Massive Scale

HyperLogLog for unique visitor counts, Count-Min Sketch for frequency estimates. Trade exactness for O(1) memory per page.

### 5.6 Rate Limiting / Fraud

A listener that detects unrealistic per-IP hit rates and drops or flags them. Pluggable.

### 5.7 Async / Asyncio Strategy

Add `AsyncIOStrategy` using `asyncio.Lock`. Same interface (async variants). Perfect Open/Closed demonstration.

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | Restate understanding + clarifying Qs | Exact counts? Distributed? Persistence? Time window? |
| 2-5 min | Identify entities, draw relationships | Page, CounterStorage, ConcurrencyStrategy, HitCounterService, Factory |
| 5-8 min | Walk through patterns + tradeoffs | **Lead with Strategy for concurrency.** Mention Facade, Singleton, Factory, Observer |
| 8-12 min | Explain single-threaded vs multi-threaded plan | Lock, striped lock; address Python GIL explicitly |
| 12-30 min | Code the solution | Start with Storage, then Strategy hierarchy, then Service, then threading test |
| 30-35 min | Discuss extensibility + follow-ups | Redis, sliding window, Count-Min, top-K, persistence |

---

## FINAL CODE: Write This in 15-20 Minutes

> Streamlined version. Includes a threading test that actually demonstrates the race condition — this is a huge win at Uber because it proves you understand the concurrency model, not just the API.

```python
import threading
from abc import ABC, abstractmethod


# ---- Storage ----

class CounterStorage(ABC):
    @abstractmethod
    def incr(self, page_id: str) -> int: pass
    @abstractmethod
    def get(self, page_id: str) -> int: pass
    @abstractmethod
    def snapshot(self) -> dict: pass


class InMemoryStorage(CounterStorage):
    def __init__(self):
        self._counts = {}

    def incr(self, page_id):
        self._counts[page_id] = self._counts.get(page_id, 0) + 1
        return self._counts[page_id]

    def get(self, page_id):
        return self._counts.get(page_id, 0)

    def snapshot(self):
        return dict(self._counts)


# ---- Concurrency Strategy ----

class ConcurrencyStrategy(ABC):
    def __init__(self, storage: CounterStorage):
        self.storage = storage

    @abstractmethod
    def increment(self, page_id: str) -> int: pass
    @abstractmethod
    def get(self, page_id: str) -> int: pass
    @abstractmethod
    def snapshot(self) -> dict: pass


class SingleThreadedStrategy(ConcurrencyStrategy):
    def increment(self, page_id): return self.storage.incr(page_id)
    def get(self, page_id):       return self.storage.get(page_id)
    def snapshot(self):           return self.storage.snapshot()


class LockedStrategy(ConcurrencyStrategy):
    def __init__(self, storage):
        super().__init__(storage)
        self._lock = threading.Lock()

    def increment(self, page_id):
        with self._lock:
            return self.storage.incr(page_id)

    def get(self, page_id):
        with self._lock:
            return self.storage.get(page_id)

    def snapshot(self):
        with self._lock:
            return self.storage.snapshot()


class StripedLockStrategy(ConcurrencyStrategy):
    def __init__(self, storage, num_stripes=16):
        super().__init__(storage)
        self._locks = [threading.Lock() for _ in range(num_stripes)]
        self._n = num_stripes

    def _lock_for(self, page_id):
        return self._locks[hash(page_id) % self._n]

    def increment(self, page_id):
        with self._lock_for(page_id):
            return self.storage.incr(page_id)

    def get(self, page_id):
        with self._lock_for(page_id):
            return self.storage.get(page_id)

    def snapshot(self):
        for lk in self._locks: lk.acquire()
        try:
            return self.storage.snapshot()
        finally:
            for lk in self._locks: lk.release()


# ---- Service (Facade) ----

class HitCounterService:
    def __init__(self, strategy: ConcurrencyStrategy):
        self._strategy = strategy

    def record_hit(self, page_id):
        return self._strategy.increment(page_id)

    def get_hits(self, page_id):
        return self._strategy.get(page_id)

    def get_all_hits(self):
        return self._strategy.snapshot()


# ---- Factory ----

class HitCounterFactory:
    @staticmethod
    def create(mode="striped", shards=16):
        storage = InMemoryStorage()
        if mode == "single":   return HitCounterService(SingleThreadedStrategy(storage))
        if mode == "locked":   return HitCounterService(LockedStrategy(storage))
        if mode == "striped":  return HitCounterService(StripedLockStrategy(storage, shards))
        raise ValueError(mode)


# ---- Threading Test (proves the design works) ----

def stress_test(service, num_threads=50, hits_per_thread=1000):
    def worker():
        for _ in range(hits_per_thread):
            service.record_hit("/home")

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads: t.start()
    for t in threads: t.join()

    expected = num_threads * hits_per_thread
    actual = service.get_hits("/home")
    print(f"expected={expected}, actual={actual}, lost={expected - actual}")
    return actual == expected


# ---- Main ----

if __name__ == "__main__":
    print("Single-threaded (unsafe under concurrency):")
    stress_test(HitCounterFactory.create(mode="single"))

    print("Locked (safe, serialized):")
    stress_test(HitCounterFactory.create(mode="locked"))

    print("Striped lock (safe, concurrent):")
    stress_test(HitCounterFactory.create(mode="striped", shards=16))
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| Record hit per page | `HitCounterService.record_hit()` |
| Return count on demand | `get_hits()`, `get_all_hits()` |
| Strategy Pattern (concurrency) | `ConcurrencyStrategy` ABC + 3 concretes |
| Strategy Pattern (storage) | `CounterStorage` ABC + `InMemoryStorage` |
| Facade | `HitCounterService` |
| Factory | `HitCounterFactory.create()` |
| Single-threaded support | `SingleThreadedStrategy` |
| Multi-threaded safety | `LockedStrategy`, `StripedLockStrategy` |
| Proof under real concurrency | `stress_test()` |
| Extensibility: distributed | Swap `InMemoryStorage` for `RedisStorage` |
| Extensibility: async | Add `AsyncIOStrategy` — no other changes |

### Writing Order (for the interview)

1. **Storage ABC + InMemoryStorage** (~3 min) — the smallest foundation.
2. **ConcurrencyStrategy ABC + SingleThreadedStrategy** (~2 min) — set up the pattern.
3. **LockedStrategy** (~2 min) — smallest correct multi-threaded version.
4. **StripedLockStrategy** (~4 min) — the scaling story.
5. **HitCounterService (Facade)** (~2 min) — trivial delegation.
6. **HitCounterFactory** (~2 min) — wiring.
7. **stress_test()** (~3 min) — proves it works. **This is your closer.**
8. **Main** (~1 min).

**Total: ~18-19 minutes.**

---

## Step 7: Follow-up Questions & Answers

Interviewers at Uber *always* push on concurrency for this problem. Have these answers ready.

### Q1. Why can't we just use `dict[page] += 1` — isn't the GIL enough?

The GIL prevents two Python bytecodes from running at the exact same instant, but `d[k] += 1` compiles to roughly `LOAD`, `ADD`, `STORE` — three bytecodes. A thread switch between them drops updates. I demonstrated this in the `stress_test` for `SingleThreadedStrategy`: you'll see `actual < expected`. That's why we need an explicit `Lock`.

### Q2. Global lock vs striped locks — when to pick which?

- **Global lock:** few pages, low-to-moderate QPS. Simplicity beats micro-optimization.
- **Striped lock:** many pages, high QPS, and hits distribute across pages. Writes to different stripes run in parallel.
- **Both fail under a "hot key"** — if 90% of traffic hits `/home`, all hot-key requests land on the same stripe and serialize. Mitigation: per-thread local counters flushed periodically (combining tree), or a sharded counter where `/home` itself is split into `/home#0`, `/home#1`, ... and summed on read.

### Q3. How does this scale to multiple servers?

Swap `InMemoryStorage` for `RedisStorage` using `INCR` (atomic, server-side). Pair with `SingleThreadedStrategy` because Redis handles concurrency. No changes to `HitCounterService`. For extreme scale, each app-server keeps a local count and flushes deltas to Redis every N ms — trades a small lag for drastically reduced network round-trips.

### Q4. What if we need approximate counts at massive scale?

Use a **Count-Min Sketch** — `O(1)` memory per page with a tunable error bound. For unique visitors (not hits), use **HyperLogLog**. Redis supports both natively (`PFADD`, `PFCOUNT`). This is how real systems like Twitter's trending-topic counter work.

### Q5. How do you handle persistence / crash recovery?

Add a `SnapshotListener` that writes the full snapshot to disk every N seconds (or after M hits). On startup, load the last snapshot into `InMemoryStorage`. For higher durability, write a WAL (append-only log of increments) and replay on restart. The core design doesn't change — it's all in listeners and a startup hook.

### Q6. How would you support hits-in-last-5-minutes?

Replace `int` with a **ring buffer of per-second buckets**: an array of 300 ints, index = `now_seconds % 300`. On increment, zero the bucket if its timestamp is stale, then add 1. On read, sum the last N buckets. `O(window_size)` read, `O(1)` write. Classic sliding-window counter.

### Q7. What's the memory story if we have 100M pages?

In-memory dict: `~100 bytes/entry` × 100M = 10 GB. Options:
- Shard by page id across servers.
- Use a compact backing store (e.g., LMDB, RocksDB).
- Accept approximation — Count-Min Sketch gives bounded error in fixed memory.

### Q8. How do you test the concurrency correctness?

The `stress_test` I wrote is the first line of defense — launch N threads, each does M hits, assert total == N×M. For the `SingleThreadedStrategy`, this test *should fail* — that failure is itself a test (it proves the race condition exists). Additional tests: randomized interleavings (`hypothesis`), and for the striped strategy, verify snapshot consistency by taking a snapshot while writes are ongoing and checking invariants.

### Q9. What's the tradeoff of the double-checked-locking singleton?

Guarantees single instantiation without locking on every `get_instance()` call. Tradeoff: the singleton holds process-global state, which leaks between tests if tests don't reset it. Mitigation: the constructor accepts a strategy directly, so tests never call `get_instance()` — they build isolated instances. `get_instance()` is for production wiring only.

### Q10. Why Facade? It looks like the service is just delegating.

Two reasons:
1. **Stable public API.** Callers only see `record_hit`, `get_hits`, `get_all_hits`. Internals (storage, strategy, listeners) can change without breaking callers.
2. **Cross-cutting concerns live here.** Listener notification, metrics, tracing, auth checks all slot into the service without polluting storage or strategy. That's exactly what Facade is for — a thin coordinator that owns the cross-cutting glue.

### Q11 (bonus, if they push on locks). Why a `Lock` and not `RLock`?

`Lock` is cheaper and we don't need reentrance — no code path acquires the same lock twice. If you add listeners that call back into the service, you'd either need `RLock` OR (better) fire listeners *outside* the critical section — which is what my design does: `increment` returns, then we call listeners.

### Q12 (bonus, if they push on the `hash()` in striped locks). Isn't `hash()` process-randomized?

Yes — Python randomizes `hash()` for strings per-process by default (for hash-flooding protection). That's fine for us: within a single process, the mapping is stable, which is all we need for correctness. It just means two processes will shard the same key differently — irrelevant for an in-process counter, but would matter if we stored stripe mappings across processes.

---

## Closing Statement (say this at the end)

> "To summarize: I used Strategy for both concurrency and storage so the single-threaded, locked, and striped-lock variants are swappable — and the in-memory backend can be replaced with Redis without touching the service. I addressed the Python GIL pitfall explicitly with a stress test. For scaling, I'd move to striped locks first, then sharded Redis counters with local batching, and for massive scale swap exact counts for Count-Min Sketch. The core of the design stays the same — every scaling answer is a strategy or storage swap, not a rewrite."
