# Low-Level Design: HashMap with getRandom() + Concurrency - Uber SDE-2 Interview Guide

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me restate the problem in my own words, then ask a few clarifying questions before I design anything."

### Your Understanding (State This)

- Design a key-value store supporting four operations:
  - `get(key)` — return the value for a key, or `None` if absent
  - `set(key, value)` — insert or update
  - `delete(key)` — remove a key (return success/failure)
  - `getRandom()` — return a **uniformly random** `(key, value)` pair in **O(1)**
- All four operations must be **O(1) average time**
- Extension: make the structure **thread-safe** so multiple threads can call these concurrently without corruption

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Should `getRandom()` be uniformly random over current entries? | Yes — each key equally likely |
| 2 | Should `getRandom()` be with or without replacement? | With replacement (stateless); we can discuss a `getRandomK(n)` extension |
| 3 | What should `get` / `getRandom` return on an empty or missing key? | `None` is fine; raise is also acceptable — we'll pick one and be consistent |
| 4 | Any constraints on key/value types? | Keys must be hashable; values can be anything |
| 5 | For the concurrent version — read-heavy, write-heavy, or balanced workload? | Assume read-heavy (typical cache/KV store) — influences lock choice |
| 6 | Do we need strict linearizability or is eventual consistency acceptable? | Linearizable per-operation; `getRandom` sees a consistent snapshot |
| 7 | Do we need to support iteration / range queries? | No — just these four ops. Keep scope tight |
| 8 | Memory bound? TTL? Eviction? | Not required, but mention as an extension |

### Confirmed Requirements

1. `get`, `set`, `delete`, `getRandom` — all O(1) average
2. `getRandom()` uniformly random, with replacement
3. Keys hashable; values arbitrary
4. Extensible to thread-safe concurrent access
5. Read-heavy workload (influences concurrency strategy)
6. No iteration / range scan required
7. Linearizable semantics — each op appears atomic

---

## Step 2: Core Entities & Relationships (2-3 mins)

> **What to say:** "The central design question is: why can't a plain dictionary do this? Because a dict has O(1) `get` by key, but **O(n) to pick a random element** — you'd have to materialize `list(dict.keys())` or iterate. So we need an auxiliary structure."

### The Key Insight (State This Clearly)

- **Dict alone:** O(1) `get/set/delete` by key, but O(n) `getRandom` (must linearize keys)
- **List alone:** O(1) `getRandom` by index, but O(n) `get/delete` by key
- **Dict + List together:** O(1) for **all four** — the dict stores `key -> index in list`, the list stores `(key, value)` pairs

The only subtlety is `delete`: naively popping from the middle of the list is O(n). We fix that with the **swap-with-last** trick: swap the target entry with the last entry, pop the tail (O(1)), and update the index map for the swapped element.

### Entities

| Entity | Responsibility |
|--------|---------------|
| **KVStore (ABC)** | Interface: `get`, `set`, `delete`, `get_random`, `size` |
| **HashMapWithRandom** | Single-threaded core — `dict[key] -> index` + `list[(key, value)]` |
| **LockingStrategy (ABC)** | Abstract locking policy — `read_lock()`, `write_lock()` as context managers (Strategy Pattern) |
| **NoLockStrategy** | No synchronization — single-threaded baseline |
| **CoarseLockStrategy** | One `threading.Lock` for every operation |
| **RWLockStrategy** | Many concurrent readers, one exclusive writer (read-heavy win) |
| **StripedLockStrategy** | Shard keys across N locks by hash — reduces contention (mention, don't code) |
| **ThreadSafeKVStore** | Decorator wrapping a `KVStore` with a `LockingStrategy` |
| **KVStoreFactory** | Centralized construction (`create_single_threaded`, `create_coarse_locked`, `create_rw_locked`) |

### Relationships

```
KVStore (ABC)
    |--- implemented by ---> HashMapWithRandom     (single-threaded core)
    |--- implemented by ---> ThreadSafeKVStore     (decorator — wraps any KVStore)

ThreadSafeKVStore (Decorator)
    |--- has-a ---> KVStore          (the inner store it delegates to)
    |--- has-a ---> LockingStrategy  (how reads/writes are synchronized)

LockingStrategy (ABC)
    |--- NoLockStrategy
    |--- CoarseLockStrategy     (single threading.Lock)
    |--- RWLockStrategy         (custom — threading.Condition based)
    |--- StripedLockStrategy    (N locks sharded by key hash — extensibility)

KVStoreFactory  ---> builds pre-configured KVStore instances
```

### Why this split?

- **Core data structure** is concurrency-agnostic — it's the algorithm.
- **Locking strategy** is pluggable — swap without touching the core.
- **Decorator** glues them together — you can wrap *any* `KVStore` implementation, not just this one. If we add a persistent-disk-backed store later, the same decorator thread-safes it.

---

## Step 3: Design Patterns & Tradeoffs (3-4 mins)

> **What to say:** "I'm using a few patterns, and each one earns its place."

### Pattern 1: Strategy Pattern (Locking Policy)

**Why:** The *correct* locking strategy depends on workload. Read-heavy → RWLock. High write contention → striped locks. Single-threaded → no lock. I don't want to hardcode one choice; I want to make the decision swappable.

**How:** `LockingStrategy` ABC exposes `read_lock()` and `write_lock()` context managers. Each concrete strategy implements them differently.

**Tradeoff:** One extra layer of indirection per operation — negligible. Buys workload-tuning without changing the core.

```
LockingStrategy (ABC)
    |-- NoLockStrategy
    |-- CoarseLockStrategy
    |-- RWLockStrategy
    |-- StripedLockStrategy  (future)
```

### Pattern 2: Decorator Pattern (Thread-Safety Wrapper)

**Why:** Thread-safety is **orthogonal** to the core data structure. A decorator lets me keep `HashMapWithRandom` clean (pure logic, no locks) and add synchronization as a separate concern. If tomorrow I build a `RedisKVStore` or a `DiskKVStore`, the same `ThreadSafeKVStore` wraps it for free.

**How:** `ThreadSafeKVStore` implements the `KVStore` interface and composes an inner `KVStore` + `LockingStrategy`. Every method delegates to the inner store inside the appropriate lock.

**Tradeoff:** Two objects instead of one. In return: cleanly separated concerns, single-threaded core is directly testable without mocking locks, and I can compose different backends with different lock strategies.

> **Why not subclass `HashMapWithRandom` with a `ConcurrentHashMapWithRandom` child?**
> That couples thread-safety to one specific core implementation. The decorator works with **any** `KVStore`. Composition > inheritance.

### Pattern 3: Interface Segregation via KVStore ABC

**Why:** Both the single-threaded core and the thread-safe wrapper implement the same `KVStore` interface. Clients depend on the interface, not the implementation — Dependency Inversion.

**Tradeoff:** Tiny — one abstract class. Pays off the moment you swap implementations for testing or caching layers.

### Pattern 4: Factory Pattern (Construction) — Extensibility

**Why:** Building a properly-configured store takes multiple steps: core + strategy + decorator. A factory hides that so callers write `KVStoreFactory.create_rw_locked()` instead of plumbing three objects.

**Tradeoff:** Overkill for a single use-site, but shows you know when to centralize object graphs. Mention it; code it only if there's time.

### Pattern 5: Observer Pattern (Change Notifications) — Extensibility

**Why:** Downstream systems may want to react to mutations (cache invalidation, replication, metrics). Hardcoding those into the store couples the concerns.

**How:** Store maintains a list of listeners; notifies after `set`/`delete`.

**Tradeoff:** Adds dispatch overhead and re-entrancy questions. Mention as an extensibility point; don't implement in the 20-min code.

> **Interview tip:** In the spoken pitch, emphasize Strategy + Decorator — those are load-bearing. Interface, Factory, Observer are name-drops that show breadth.

---

## Step 4: Implementation - Full Design (Reference)

> This section is the complete, pattern-rich implementation. Use it to study the design. The streamlined 20-minute version is at the bottom.

### 4.1 KVStore Interface

```python
from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple


class KVStore(ABC):
    @abstractmethod
    def get(self, key: Any) -> Optional[Any]: ...

    @abstractmethod
    def set(self, key: Any, value: Any) -> None: ...

    @abstractmethod
    def delete(self, key: Any) -> bool: ...

    @abstractmethod
    def get_random(self) -> Optional[Tuple[Any, Any]]: ...

    @abstractmethod
    def size(self) -> int: ...
```

### 4.2 HashMapWithRandom (Single-Threaded Core)

```python
import random


class HashMapWithRandom(KVStore):
    """
    Core invariant:
      - `entries[i] == (k, v)`  <=>  `key_to_index[k] == i`
      - `entries` has no holes; length equals len(key_to_index)
    """

    def __init__(self):
        self._key_to_index: dict = {}
        self._entries: list = []  # list of (key, value)

    def get(self, key):
        idx = self._key_to_index.get(key)
        if idx is None:
            return None
        return self._entries[idx][1]

    def set(self, key, value):
        if key in self._key_to_index:
            idx = self._key_to_index[key]
            self._entries[idx] = (key, value)  # update in place
        else:
            self._key_to_index[key] = len(self._entries)
            self._entries.append((key, value))

    def delete(self, key) -> bool:
        idx = self._key_to_index.get(key)
        if idx is None:
            return False

        last_idx = len(self._entries) - 1
        if idx != last_idx:
            # Swap target with last, then pop the last — keeps array contiguous in O(1)
            last_key, last_value = self._entries[last_idx]
            self._entries[idx] = (last_key, last_value)
            self._key_to_index[last_key] = idx

        self._entries.pop()
        del self._key_to_index[key]
        return True

    def get_random(self):
        if not self._entries:
            return None
        return random.choice(self._entries)  # O(1) — random.choice is O(1) on a list

    def size(self) -> int:
        return len(self._entries)
```

**Key points to call out while whiteboarding:**
- `set` is O(1): dict lookup + (append or overwrite).
- `delete` is O(1) because of **swap-with-last**. Without that, list deletion is O(n).
- `get_random` is O(1) because Python's `random.choice` does `list[random.randint(0, len-1)]`.
- The invariant `entries[i] == (k, v) <=> key_to_index[k] == i` must be preserved by every mutation. Name it explicitly — interviewers love seeing invariants.

### 4.3 Strategy Pattern — Locking Strategies

```python
import threading
from contextlib import contextmanager


class LockingStrategy(ABC):
    @abstractmethod
    @contextmanager
    def read_lock(self): ...

    @abstractmethod
    @contextmanager
    def write_lock(self): ...


class NoLockStrategy(LockingStrategy):
    """For single-threaded use or when the caller synchronizes externally."""
    @contextmanager
    def read_lock(self):
        yield

    @contextmanager
    def write_lock(self):
        yield


class CoarseLockStrategy(LockingStrategy):
    """One lock for everything. Simple, correct, low throughput under contention."""
    def __init__(self):
        self._lock = threading.Lock()

    @contextmanager
    def read_lock(self):
        with self._lock:
            yield

    @contextmanager
    def write_lock(self):
        with self._lock:
            yield


class RWLockStrategy(LockingStrategy):
    """
    Many concurrent readers OR one exclusive writer.
    Writer-preference to avoid writer starvation under read-heavy load.
    Python has no built-in RWLock, so we build one with a Condition.
    """
    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer_active = False
        self._writers_waiting = 0

    @contextmanager
    def read_lock(self):
        with self._cond:
            # Wait if a writer is active or waiting (writer preference)
            while self._writer_active or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write_lock(self):
        with self._cond:
            self._writers_waiting += 1
            while self._writer_active or self._readers > 0:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer_active = True
        try:
            yield
        finally:
            with self._cond:
                self._writer_active = False
                self._cond.notify_all()
```

> **Interview note on RWLock:** "Python doesn't ship an RWLock, so I'm building one on `Condition`. The subtle bit is **writer preference** — without it, a steady stream of readers can starve writers indefinitely. I increment `writers_waiting` **before** the wait loop, and readers check that counter, so an arriving reader yields to a queued writer."

### 4.4 Decorator — ThreadSafeKVStore

```python
class ThreadSafeKVStore(KVStore):
    """
    Decorator: wraps any KVStore with a locking strategy.
    The inner store stays concurrency-agnostic.
    """
    def __init__(self, inner: KVStore, lock_strategy: LockingStrategy):
        self._inner = inner
        self._lock = lock_strategy

    def get(self, key):
        with self._lock.read_lock():
            return self._inner.get(key)

    def set(self, key, value):
        with self._lock.write_lock():
            self._inner.set(key, value)

    def delete(self, key) -> bool:
        with self._lock.write_lock():
            return self._inner.delete(key)

    def get_random(self):
        with self._lock.read_lock():
            return self._inner.get_random()

    def size(self) -> int:
        with self._lock.read_lock():
            return self._inner.size()
```

> **Interview note:** "`get_random` takes a **read** lock, not a write lock. It doesn't mutate — but we still need the lock to prevent a concurrent `delete` from making `entries` briefly inconsistent with `key_to_index` between the swap and the pop. The read lock blocks writers but permits other readers."

### 4.5 Factory — Construction

```python
class KVStoreFactory:
    @staticmethod
    def create_single_threaded() -> KVStore:
        return HashMapWithRandom()

    @staticmethod
    def create_coarse_locked() -> KVStore:
        return ThreadSafeKVStore(HashMapWithRandom(), CoarseLockStrategy())

    @staticmethod
    def create_rw_locked() -> KVStore:
        return ThreadSafeKVStore(HashMapWithRandom(), RWLockStrategy())
```

### 4.6 Striped Lock Strategy (Sketch — mention, don't code fully)

```python
class StripedLockStrategy(LockingStrategy):
    """
    N independent locks. Each key maps to one stripe via hash(key) % N.
    Reduces contention when distinct keys are accessed concurrently.

    Caveat: get_random() touches the whole array, so it needs ALL stripes —
    which defeats the purpose. Striped locks suit get/set/delete-heavy
    workloads where getRandom is rare, or require a separate RWLock
    just for the entries array. Mention this tradeoff to the interviewer.
    """
    def __init__(self, stripes: int = 16):
        self._locks = [threading.Lock() for _ in range(stripes)]
        # ... omitted — requires key-aware locking API, not the generic read/write_lock
```

> **Why this matters in the interview:** Proposing striped locks *and* immediately explaining why it fights with `getRandom` shows real concurrency judgment. It's better to name a tradeoff than to propose a "silver bullet."

---

## Step 5: Concurrency Extension — Deep Dive

> **What to say:** "Now for the thread-safety extension. I want to address three things: correctness, the GIL question the interviewer is definitely going to ask, and how I'd tune for the workload."

### 5.1 What Actually Needs Protecting

The core invariant is:
```
entries[i] == (k, v)  <=>  key_to_index[k] == i
```

Both mutating operations (`set`, `delete`) touch **both** structures. Without a lock, a reader can witness them mid-update — e.g., `key_to_index` says index 5 but `entries` only has 4 elements because we're between the pop and the dict `del`. That's a crash, not just a stale read.

So: **every mutation must be atomic w.r.t. every read.** A single lock is sufficient and correct.

### 5.2 The GIL Question

> **If asked "doesn't the GIL already make this thread-safe?":**
>
> "No. The GIL guarantees that individual bytecodes are atomic, but our operations are compound — `set` does a dict check, a list append, and a dict assignment. Each is atomic alone, but a reader can run **between** them. So we still need explicit locking for logical atomicity. The GIL buys us simpler code (no memory barriers, no torn reads on primitives), not free correctness."

### 5.3 Lock Choice by Workload

| Workload | Best choice | Why |
|----------|------------|-----|
| Single-threaded | `NoLockStrategy` | Zero overhead |
| Mixed, low contention | `CoarseLockStrategy` | Simple, correct, cheap |
| **Read-heavy (typical)** | `RWLockStrategy` | N readers in parallel; writer blocks briefly |
| Write-heavy on independent keys | `StripedLockStrategy` | But `getRandom` is problematic — see tradeoff |

### 5.4 What a Lock Does NOT Solve

- **Uniformity under concurrent mutation:** `getRandom` during an in-flight `delete` returns a correct snapshot (thanks to the lock), but the definition of "uniform" is "uniform over keys present *at the moment of the read*" — which is the linearizable semantics we confirmed in Step 1. This is the right behavior, just worth naming.
- **Memory:** locking doesn't bound memory. If you need a cap, add an LRU eviction policy on top — extensibility point.

### 5.5 Correctness Sketch (mention briefly)

"Under `ThreadSafeKVStore`:
1. Every write holds the write lock exclusively → no two writes interleave → invariant preserved.
2. Every read holds a read lock → reads only run when no write is in progress → reads see a consistent state.
3. `delete`'s swap-with-last is a local operation on protected memory → no torn state escapes the lock."

That's the whole argument. Two sentences if you need to compress: "Mutations hold exclusive lock; reads hold shared lock; the invariant only needs to hold between operations, not within one, so the lock gives us that for free."

---

## Step 6: Extensibility Points (Mention in Interview)

> **What to say:** "The design opens up several extensions without touching the core..."

### 6.1 `getRandomK(n)` — Sample N Without Replacement

Add a method that uses `random.sample(self._entries, n)` — O(n) in the sample size, not the store size.

### 6.2 Weighted Random

Store a parallel `weights` list and build a cumulative-sum index on mutations. `getRandom` does a binary search on a uniform variate — O(log n). Or Alias Method — O(1) after O(n) preprocessing.

### 6.3 TTL / Expiration

Wrap `set` to take an optional TTL; store expiry alongside value; lazy-evict on `get`. For accurate expiration, add a background sweeper — but mention the `getRandom` pitfall: expired-but-not-yet-swept entries could be returned. Compensate with an eager check inside `getRandom`.

### 6.4 Size-Bounded (LRU Eviction)

Layer an LRU policy on top. Evict on `set` when `size() >= capacity`. The dual-structure design already supports LRU because we have O(1) delete by key.

### 6.5 Persistence

Introduce a `PersistentKVStore` alongside `HashMapWithRandom`, both implementing `KVStore`. The decorator + factory unchanged — this is where the interface pays off.

### 6.6 Observer / Change Notifications

```python
class ChangeListener(ABC):
    @abstractmethod
    def on_set(self, key, value): ...
    @abstractmethod
    def on_delete(self, key): ...
```

Store holds `listeners: list[ChangeListener]`; notifies after each mutation. Use for cache invalidation, metrics, replication.

### 6.7 Sharding Across Processes / Machines

Consistent hashing over N shards. Each shard is an independent `KVStore`. `getRandom` fans out: pick a shard weighted by its size (to preserve uniformity), then `getRandom` on that shard.

### 6.8 Observability

Wrap the decorator with a `MetricsKVStore` (another decorator!) that records latency histograms and operation counts. Decorators compose — this is the payoff.

---

## Step 7: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | Restate problem, ask clarifying questions | Read-heavy? Uniform? With replacement? |
| 2-5 min | State the key insight — dict + array | Why dict alone fails (O(n) random) |
| 5-7 min | Walk through entities + swap-with-last delete | Name the invariant explicitly |
| 7-10 min | Discuss design patterns | Strategy (locking), Decorator (thread-safety) — these are load-bearing |
| 10-12 min | Address concurrency extension at a high level | GIL note, RWLock for read-heavy |
| 12-30 min | Code the solution | Interface → core → strategies → decorator → factory → demo |
| 30-35 min | Extensibility + follow-ups | Weighted random, TTL, sharding, observability |

---

## FINAL CODE: Write This in 15-20 Minutes

> This is the **streamlined, interview-ready** version. It covers the core data structure, Strategy Pattern for locking, Decorator for thread-safety, Factory, and a runnable multi-threaded demo. ~140 lines.

```python
import random
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Optional, Tuple


# ---- Interface ----

class KVStore(ABC):
    @abstractmethod
    def get(self, key: Any) -> Optional[Any]: ...
    @abstractmethod
    def set(self, key: Any, value: Any) -> None: ...
    @abstractmethod
    def delete(self, key: Any) -> bool: ...
    @abstractmethod
    def get_random(self) -> Optional[Tuple[Any, Any]]: ...
    @abstractmethod
    def size(self) -> int: ...


# ---- Core: single-threaded dict + array ----

class HashMapWithRandom(KVStore):
    """Invariant: entries[i] == (k, v)  <=>  key_to_index[k] == i"""

    def __init__(self):
        self._key_to_index: dict = {}
        self._entries: list = []

    def get(self, key):
        idx = self._key_to_index.get(key)
        return None if idx is None else self._entries[idx][1]

    def set(self, key, value):
        if key in self._key_to_index:
            self._entries[self._key_to_index[key]] = (key, value)
        else:
            self._key_to_index[key] = len(self._entries)
            self._entries.append((key, value))

    def delete(self, key) -> bool:
        idx = self._key_to_index.get(key)
        if idx is None:
            return False
        last = len(self._entries) - 1
        if idx != last:
            last_key, last_val = self._entries[last]
            self._entries[idx] = (last_key, last_val)
            self._key_to_index[last_key] = idx
        self._entries.pop()
        del self._key_to_index[key]
        return True

    def get_random(self):
        return random.choice(self._entries) if self._entries else None

    def size(self) -> int:
        return len(self._entries)


# ---- Strategy: locking ----

class LockingStrategy(ABC):
    @abstractmethod
    @contextmanager
    def read_lock(self): ...
    @abstractmethod
    @contextmanager
    def write_lock(self): ...


class CoarseLockStrategy(LockingStrategy):
    def __init__(self):
        self._lock = threading.Lock()

    @contextmanager
    def read_lock(self):
        with self._lock:
            yield

    @contextmanager
    def write_lock(self):
        with self._lock:
            yield


class RWLockStrategy(LockingStrategy):
    """Many readers OR one writer, with writer-preference to avoid starvation."""
    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer_active = False
        self._writers_waiting = 0

    @contextmanager
    def read_lock(self):
        with self._cond:
            while self._writer_active or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write_lock(self):
        with self._cond:
            self._writers_waiting += 1
            while self._writer_active or self._readers > 0:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer_active = True
        try:
            yield
        finally:
            with self._cond:
                self._writer_active = False
                self._cond.notify_all()


# ---- Decorator: thread-safety ----

class ThreadSafeKVStore(KVStore):
    def __init__(self, inner: KVStore, lock: LockingStrategy):
        self._inner = inner
        self._lock = lock

    def get(self, key):
        with self._lock.read_lock():
            return self._inner.get(key)

    def set(self, key, value):
        with self._lock.write_lock():
            self._inner.set(key, value)

    def delete(self, key) -> bool:
        with self._lock.write_lock():
            return self._inner.delete(key)

    def get_random(self):
        with self._lock.read_lock():
            return self._inner.get_random()

    def size(self) -> int:
        with self._lock.read_lock():
            return self._inner.size()


# ---- Factory ----

class KVStoreFactory:
    @staticmethod
    def create_single_threaded() -> KVStore:
        return HashMapWithRandom()

    @staticmethod
    def create_coarse_locked() -> KVStore:
        return ThreadSafeKVStore(HashMapWithRandom(), CoarseLockStrategy())

    @staticmethod
    def create_rw_locked() -> KVStore:
        return ThreadSafeKVStore(HashMapWithRandom(), RWLockStrategy())


# ---- Demo ----

if __name__ == "__main__":
    store = KVStoreFactory.create_rw_locked()

    def writer(start):
        for i in range(start, start + 100):
            store.set(f"k{i}", i)

    def reader():
        for _ in range(200):
            store.get_random()

    threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    print(f"Final size: {store.size()}")
    print(f"Random sample: {store.get_random()}")
    print(f"get('k42') = {store.get('k42')}")
    print(f"delete('k42') = {store.delete('k42')}")
    print(f"get('k42') after delete = {store.get('k42')}")
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| `get` / `set` / `delete` / `get_random` — all O(1) | `HashMapWithRandom` |
| Swap-with-last O(1) delete | `HashMapWithRandom.delete` |
| Thread-safety | `ThreadSafeKVStore` decorator |
| Read-heavy optimization | `RWLockStrategy` |
| Pluggable locking | `LockingStrategy` ABC |
| Orthogonal concerns (core ↔ sync) | Decorator composition |
| Clean construction | `KVStoreFactory` |
| Extensibility: new backends | implement `KVStore`, decorator unchanged |
| Extensibility: new lock policies | implement `LockingStrategy`, decorator unchanged |

### Writing Order (for the interview)

1. **`KVStore` ABC** (~1 min) — 5 abstract methods
2. **`HashMapWithRandom`** (~6 min) — dict + list; explain swap-with-last out loud
3. **`LockingStrategy` ABC + `CoarseLockStrategy`** (~2 min) — context-manager pattern
4. **`RWLockStrategy`** (~4 min) — the interesting one; mention writer-preference
5. **`ThreadSafeKVStore` decorator** (~2 min) — mechanical; call out read vs write lock for `get_random`
6. **`KVStoreFactory`** (~1 min) — three one-liners
7. **Demo `__main__`** (~2 min) — writers + readers hammering the store

**Total: ~18 minutes**

If time is tight, skip `RWLockStrategy` and use only `CoarseLockStrategy`. Mention RWLock as the optimization you'd add next — the interviewer will credit the judgment without you needing to write it.

---

## Step 8: Follow-up Questions & Answers

### Q1. Why can't you just use `random.choice(list(my_dict.keys()))`?

**Answer:** That's O(n) because `list(dict.keys())` materializes all keys — `random.choice` over an iterator of unknown length forces a full scan. We need O(1), which requires a pre-built array with index access. Hence the dual dict + list structure.

### Q2. Walk me through why swap-with-last delete is correct.

**Answer:** The invariant is `entries[i] == (k, v) <=> key_to_index[k] == i`. When we delete key `K` at index `i`:
1. Read the last entry `(L, v_L)` at index `last`.
2. Overwrite `entries[i] = (L, v_L)` — now `L` lives at index `i`.
3. Update `key_to_index[L] = i` — invariant restored for `L`.
4. `entries.pop()` — removes the now-duplicate entry at `last`.
5. `del key_to_index[K]` — removes the stale mapping.

If `i == last` (deleting the last element), steps 1–3 are no-ops; just pop and delete. All three operations are O(1).

### Q3. Is `random.choice(list)` actually O(1)?

**Answer:** Yes. CPython's `random.choice` is `seq[int(self.random() * len(seq))]` — one RNG call, one multiply, one index. No iteration. It's O(1) on anything that supports `len()` and integer indexing.

### Q4. Map this design to SOLID.

**Answer:**
- **S (Single Responsibility):** `HashMapWithRandom` stores data; `LockingStrategy` synchronizes; `ThreadSafeKVStore` composes them. Each has one reason to change.
- **O (Open/Closed):** Add a new `LockingStrategy` or a new `KVStore` implementation — zero changes to existing code.
- **L (Liskov):** `ThreadSafeKVStore` is substitutable anywhere `KVStore` is expected. Same behavior, just synchronized.
- **I (Interface Segregation):** `KVStore` is exactly 5 methods — no iteration, no bulk ops. Clients don't depend on what they don't use.
- **D (Dependency Inversion):** `ThreadSafeKVStore` depends on `KVStore` abstraction, not `HashMapWithRandom` concretely.

### Q5. Lock vs Mutex vs Synchronized — what are you actually using?

**Answer:** In Python, `threading.Lock` is a mutex — a binary, non-reentrant lock. `threading.RLock` is reentrant (same thread can acquire twice). "Synchronized" is a Java keyword; Python's equivalent is using a `Lock` as a context manager (`with self._lock:`). I used `Lock` in `CoarseLockStrategy`; if a method ever calls another locked method on the same instance, I'd switch to `RLock` to prevent self-deadlock.

### Q6. Why an RWLock for read-heavy? What's the actual win?

**Answer:** With a plain `Lock`, reads are serialized — 4 threads reading each wait their turn. With RWLock, all 4 reads run in parallel (each just increments a counter under a short critical section). Writes are rare by assumption, so the occasional exclusive block is cheap. Order-of-magnitude throughput gain on read-dominated workloads.

### Q7. How did you prevent writer starvation in your RWLock?

**Answer:** Writer-preference. I increment `writers_waiting` **before** entering the wait loop. Arriving readers check that counter and yield to queued writers. Without that, a steady reader stream can starve writers indefinitely. The tradeoff: a burst of writers can briefly starve readers — acceptable because writes are assumed rare.

### Q8. Any deadlock risk in your design?

**Answer:** No — there's exactly one lock (per store instance) and no nested locking. Deadlock requires a cycle in the wait-for graph; with one lock there's no cycle to form. If we added striped locks or cross-store transactions, we'd need a strict lock ordering.

### Q9. What if I want `getRandomK(n)` — N distinct random pairs?

**Answer:** `random.sample(self._entries, n)` — O(n) in the sample size, uniformly random without replacement. Guard against `n > size()`. Under the decorator, it takes a read lock to snapshot consistently.

### Q10. What if I want *weighted* random?

**Answer:** Two approaches:
- **Cumulative sum + binary search:** maintain a parallel `weights` array and a `prefix_sum`. `getRandom` samples a uniform in `[0, total_weight)` and binary-searches — O(log n) query, O(n) mutation (prefix sum rebuild).
- **Alias Method:** O(1) query after O(n) preprocessing. Ideal if weights are static.

I'd start with approach 1; switch to alias if query latency matters more than mutation cost.

### Q11. How would you scale this beyond a single machine?

**Answer:** Consistent hashing across N shards. Each shard is an independent `KVStore`. For `get/set/delete`, route by `hash(key) % N`. For `getRandom`, fan out: sample each shard with probability proportional to its size, then `getRandom` on the chosen shard — preserves uniformity. For iteration or cross-shard consistency, add a coordinator.

### Q12. Why a Decorator for thread-safety instead of subclassing `HashMapWithRandom`?

**Answer:** Subclassing couples thread-safety to one specific core. The decorator works with **any** `KVStore`. Tomorrow if we add a `DiskKVStore` or `RedisKVStore`, the same `ThreadSafeKVStore` wraps them — no parallel hierarchy. Composition scales; inheritance doesn't.

### Q13. What's the memory overhead of your dual structure?

**Answer:** Roughly 2x a plain dict. The dict stores `key -> int`, the list stores `(key, value)` tuples — so keys are referenced twice. Acceptable for the O(1) `getRandom` we gain. If memory-critical, I'd store only indices in the list (not keys) and look up the key from a separate structure — but the common case doesn't need that.

### Q14. What if a caller iterates entries while another thread mutates?

**Answer:** The current API doesn't expose iteration — that's intentional; `getRandom` is the only "scan-like" op and it's bounded to one element. If we added iteration, we'd either (a) snapshot under the read lock (safe, O(n) memory), (b) expose a `keys_snapshot()` method that copies under lock, or (c) use a fail-fast iterator that throws on concurrent modification. Python's dict raises `RuntimeError` on concurrent modification; we'd match that behavior.

### Q15. How would you test this?

**Answer:** Three layers:
- **Unit tests** on `HashMapWithRandom` — single-threaded correctness of `get/set/delete`, swap-with-last edge case (deleting last vs middle), empty-store `getRandom`.
- **Statistical test** on `getRandom` — insert N keys, call `getRandom` 1M times, assert each key's frequency is within expected bounds of N/total (Chi-square).
- **Concurrency tests** — multi-threaded writers + readers hammering the store; after joining, assert `size()` matches expected, no exceptions, and the invariant `len(entries) == len(key_to_index)` holds. Run under `pytest-xdist` or just raw threads in a loop.

---

## Closing Statement (30 seconds at end of interview)

> "So to summarize: the core trick is the dict + array dual structure — the dict gives O(1) key access, the array gives O(1) random access, and swap-with-last delete keeps the array contiguous. For thread-safety, I kept the core concurrency-agnostic and used a Decorator to add a pluggable LockingStrategy — that way, thread-safety is orthogonal to storage, and I can tune the lock for the workload. For a read-heavy KV store I'd use the RWLock; for low-contention workloads a single coarse lock is fine. The design extends naturally to weighted random, TTL, sharding, and observability — each is a new decorator or strategy, not a rewrite."
