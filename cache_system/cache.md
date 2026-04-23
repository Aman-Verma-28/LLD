# Low-Level Design: In-Memory Cache with TTL + Concurrency — Uber SDE-2 Interview Guide

> **Target time budget:** 10-12 min architecture + 15-20 min coding = ~30-35 min total.

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me restate the problem in my own words and then ask a few clarifying questions before jumping into design."

### Your Understanding (State This)

- Build an in-memory key-value cache supporting three operations:
  - `set(key, value, ttl)` — store a key with an optional time-to-live
  - `get(key)` — return the value if present and not expired, else `None`
  - Automatic **expire after X minutes** — expired keys must be *actively* removed, not just lazily checked on access
- Must be **thread-safe** under concurrent `get`/`set` from multiple threads
- Optimize **both time and space complexity** — no dead entries piling up in memory

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Single-process in-memory, or distributed? | Single-process for this round — design so it could be sharded later |
| 2 | Is TTL mandatory on every key, or optional? | Optional. Some keys live forever |
| 3 | Max size / memory bound? Do we evict when full? | Yes, bounded — use LRU when at capacity |
| 4 | Sliding expiration (TTL resets on `get`) or absolute? | Absolute — simpler and more common |
| 5 | Lazy vs active expiration? | **Active** — deleted soon after expiry, not only on access |
| 6 | What are the read/write ratios? | Read-heavy (typical cache workload) — informs lock choice |
| 7 | Can `set` on an existing key overwrite + reset TTL? | Yes |

### Confirmed Requirements

1. `set(key, value, ttl_seconds=None)` — overwrite-on-repeat
2. `get(key) -> value | None`
3. `delete(key)`
4. **Active** deletion of expired keys (background cleanup)
5. Bounded size with **LRU** eviction when full
6. Thread-safe for concurrent access
7. O(1) average `get`/`set`; O(log N) expiration bookkeeping

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Let me pull out the key entities. I want to separate three concerns: storage, eviction, and expiration — each is a different axis that could change."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **CacheEntry** | Value object: holds `value` + absolute `expiry_time` |
| **EvictionPolicy (Interface)** | Strategy for picking a victim when the cache is full (LRU, LFU, None) |
| **ExpirationManager** | Background worker that actively deletes expired keys using a min-heap |
| **Cache (Facade)** | Public API: `get`, `set`, `delete`. Owns the hash map + lock + wires everything together |
| **Clock (Interface)** | Abstraction over `time.time()` — makes TTL testable without `sleep` |

### Relationships

```
Cache (Facade / public API)
   |--- has-a ---> dict[str, CacheEntry]      (O(1) storage)
   |--- has-a ---> EvictionPolicy              (Strategy — LRU / LFU / None)
   |--- has-a ---> ExpirationManager           (background cleanup)
   |--- has-a ---> Clock                       (testable time source)
   |--- has-a ---> Lock                        (concurrency primitive)

EvictionPolicy (ABC)
   |-- LRUEvictionPolicy     (OrderedDict — move_to_end on access)
   |-- NoEvictionPolicy      (unbounded)
   |-- (future) LFUEvictionPolicy

ExpirationManager
   |--- has-a ---> min-heap[(expiry_time, key)]
   |--- runs a daemon thread that sleeps until the next expiry
   |--- calls back into Cache.remove_if_expired(key)
```

### Key data-structure decisions (say this out loud)

- **`dict` for storage** — O(1) average get/set/delete.
- **Min-heap keyed by expiry time** — O(log N) to schedule, O(1) to peek the next-to-expire. The background thread only wakes up when there's actual work.
- **`OrderedDict` for LRU** — O(1) access + O(1) move-to-end. This is Python's built-in LRU primitive.
- **Heap staleness is handled lazily**: when a key's TTL is overwritten, the old heap entry is still there. On pop we re-check the current entry — if the stored expiry doesn't match, we skip it. This avoids expensive heap deletions.

---

## Step 3: Design Patterns & Tradeoffs (3-4 mins)

> **What to say:** "I'll lean on a few patterns so the design stays open for extension but closed for modification."

### Pattern 1: Strategy Pattern (Eviction Policy)

**Why:** The eviction rule (LRU today, maybe LFU or FIFO tomorrow) is orthogonal to the core get/set flow. Hardcoding LRU into `Cache` couples two concerns that change at different rates.

**How:** `EvictionPolicy` ABC with `on_access`, `on_insert`, `on_remove`, `evict_victim` hooks. The cache calls into these; the policy decides what to track.

**Tradeoff:** One extra interface, but swapping LRU ↔ LFU ↔ NoEviction is a one-line change at construction.

```
EvictionPolicy (ABC)
    |-- LRUEvictionPolicy   (OrderedDict, move_to_end on access)
    |-- NoEvictionPolicy    (no-op — unbounded cache)
    |-- (future) LFUEvictionPolicy  (frequency counters + min-heap)
```

### Pattern 2: Template Method (set / get skeleton)

**Why:** `set` always does: acquire lock → validate → check capacity → evict if needed → store → schedule expiry → notify policy. That skeleton is stable; only the eviction/notification details vary.

**How:** `Cache.set` is the template; policy hooks (`on_insert`, `evict_victim`) are the variation points.

**Tradeoff:** Slightly more indirection, but keeps the top-level flow readable as a story.

### Pattern 3: Decorator Pattern (Cross-cutting concerns)

**Why:** Thread-safety, metrics, and logging are cross-cutting. Baking them into `Cache` bloats the class. Wrapping with decorators keeps the core logic clean.

**How:** `ThreadSafeCache(cache)`, `MetricsCache(cache)`, `LoggingCache(cache)` — each wraps a `Cache` interface and adds one concern.

**Tradeoff:** A few extra classes, but each one is trivial and composable (`MetricsCache(ThreadSafeCache(Cache()))`).

> **Interview tip:** In the 20-minute code, I'll inline the lock into `Cache` for brevity and *mention* the decorator approach as the cleaner long-term design.

### Pattern 4: Observer Pattern (Eviction/Expiration Events) — Extensibility

**Why:** Downstream systems may care when keys are evicted or expired (metrics, cache-warming, replication). Hardcoding those calls into `Cache` couples it to every consumer.

**How:** `Cache` maintains a list of `CacheEventListener`s. After eviction/expiration, notify all listeners.

**Tradeoff:** Tiny dispatch overhead; full decoupling. Mention it — don't code it.

### Pattern 5: Singleton / Factory — Extensibility

**Why:** Applications usually want one cache instance per namespace. A `CacheFactory` centralizes configuration (size, TTL defaults, eviction policy).

**Tradeoff:** Overkill for the interview; mention it as a deployment concern.

---

### Concurrency design (this is the Uber follow-up — spend time here)

> **What to say:** "For concurrency I want to step through the options from simplest to best, and explain why I'd pick one."

| Option | Concurrency | Complexity | When to use |
|--------|-------------|------------|-------------|
| **1. Single `RLock`** | Serialized — only one op at a time | Simplest | MVP / low contention / what I'll code first |
| **2. Read-Write Lock** | Many concurrent reads, exclusive write | Medium | Read-heavy workloads (our case) |
| **3. Striped locking** | N shards, each with its own lock | Medium-high | High throughput, reduces contention by `1/N` |
| **4. Lock-free (CAS)** | No locks | Hard | Only if profiling demands it |

**My pick for a read-heavy cache:** start with a single `RLock` to ship, then move to **striped locking** (hash the key into N shards, each shard has its own dict + lock). Striping scales throughput roughly linearly with shard count until you hit the GIL ceiling (in Python) or true parallelism limits (in a JVM-style runtime).

**Important gotcha to mention:** the background expiration thread must acquire the same lock as `get`/`set` — otherwise it can race with a concurrent `set` and delete a key that was just refreshed. That's why we **re-verify the expiry inside the lock** before deleting.

---

## Step 4: Implementation — Full Design (Reference)

> This section is the complete, pattern-rich implementation. Use it to study the design. The 20-minute streamlined version is at the bottom.

### 4.1 CacheEntry

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class CacheEntry:
    value: Any
    expiry_time: float  # absolute epoch seconds; float('inf') = never expires
```

### 4.2 Clock (Testable Time Source)

```python
import time

class Clock:
    def now(self) -> float:
        return time.time()
```

### 4.3 Eviction Policy — Strategy Pattern

```python
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Optional

class EvictionPolicy(ABC):
    @abstractmethod
    def on_access(self, key: str) -> None: ...
    @abstractmethod
    def on_insert(self, key: str) -> None: ...
    @abstractmethod
    def on_remove(self, key: str) -> None: ...
    @abstractmethod
    def evict_victim(self) -> Optional[str]: ...


class LRUEvictionPolicy(EvictionPolicy):
    def __init__(self):
        self._order: OrderedDict[str, None] = OrderedDict()

    def on_access(self, key: str) -> None:
        if key in self._order:
            self._order.move_to_end(key)

    def on_insert(self, key: str) -> None:
        self._order[key] = None
        self._order.move_to_end(key)

    def on_remove(self, key: str) -> None:
        self._order.pop(key, None)

    def evict_victim(self) -> Optional[str]:
        if not self._order:
            return None
        key, _ = self._order.popitem(last=False)  # oldest
        return key


class NoEvictionPolicy(EvictionPolicy):
    def on_access(self, key: str) -> None: pass
    def on_insert(self, key: str) -> None: pass
    def on_remove(self, key: str) -> None: pass
    def evict_victim(self) -> Optional[str]: return None
```

### 4.4 Expiration Manager — Active Deletion via Min-Heap + Background Thread

```python
import heapq
import threading

class ExpirationManager:
    """
    Owns a min-heap of (expiry_time, key). A daemon thread sleeps until the
    next expiry and calls back into the cache to delete expired keys.

    Heap staleness: when a key's TTL changes, the old heap entry stays. The
    cache re-validates the entry's current expiry before deleting, so stale
    heap entries are a no-op. This is cheaper than O(log N) heap removal.
    """

    def __init__(self, cache: "Cache"):
        self._cache = cache
        self._heap: list[tuple[float, str]] = []
        self._cond = threading.Condition()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def schedule(self, key: str, expiry_time: float) -> None:
        with self._cond:
            heapq.heappush(self._heap, (expiry_time, key))
            self._cond.notify()  # wake the worker if it was idle

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._heap:
                    self._cond.wait()
                if not self._running:
                    return
                expiry, key = self._heap[0]
                now = self._cache.clock.now()
                if expiry > now:
                    self._cond.wait(timeout=expiry - now)
                    continue
                heapq.heappop(self._heap)
            # Delete outside the condition lock; cache has its own lock
            self._cache.remove_if_expired(key, expiry)
```

### 4.5 Cache — Facade + Template Method

```python
from typing import Any, Optional

class Cache:
    def __init__(
        self,
        max_size: int = 1000,
        eviction: Optional[EvictionPolicy] = None,
        clock: Optional[Clock] = None,
    ):
        self._store: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._eviction = eviction or LRUEvictionPolicy()
        self.clock = clock or Clock()
        self._lock = threading.RLock()
        self._expiration = ExpirationManager(self)

    # ---- Public API ----

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expiry_time <= self.clock.now():
                self._remove_locked(key)
                return None
            self._eviction.on_access(key)
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        expiry = (
            self.clock.now() + ttl_seconds if ttl_seconds is not None else float("inf")
        )
        with self._lock:
            if key in self._store:
                self._remove_locked(key)
            if len(self._store) >= self._max_size:
                victim = self._eviction.evict_victim()
                if victim is not None:
                    self._remove_locked(victim)
            self._store[key] = CacheEntry(value=value, expiry_time=expiry)
            self._eviction.on_insert(key)

        if expiry != float("inf"):
            self._expiration.schedule(key, expiry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._remove_locked(key)

    def remove_if_expired(self, key: str, scheduled_expiry: float) -> None:
        """Called by ExpirationManager. Re-verifies because the entry's TTL
        may have been overwritten since this heap slot was scheduled."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return
            if entry.expiry_time != scheduled_expiry:
                return  # stale heap entry — ignore
            if entry.expiry_time <= self.clock.now():
                self._remove_locked(key)

    # ---- Internal ----

    def _remove_locked(self, key: str) -> None:
        if key in self._store:
            del self._store[key]
            self._eviction.on_remove(key)
```

### 4.6 Usage

```python
if __name__ == "__main__":
    cache = Cache(max_size=100, eviction=LRUEvictionPolicy())
    cache.set("user:1", {"name": "Aman"}, ttl_seconds=60)
    print(cache.get("user:1"))   # {'name': 'Aman'}
    cache.set("session", "abc", ttl_seconds=2)
    import time; time.sleep(2.5)
    print(cache.get("session"))  # None — actively removed by background thread
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design opens up in several directions..."

### 5.1 Pluggable Eviction — LFU, FIFO, Custom

Implement `EvictionPolicy` — zero changes to `Cache`. LFU = `dict[key, count]` + min-heap by count.

### 5.2 Striped Locking (true concurrent throughput)

Partition the cache into `N` shards. Each shard has its own `dict` + `RLock` + `ExpirationManager`. Route by `hash(key) % N`. Get `~N×` throughput until the shard count exceeds contention.

```python
class ShardedCache:
    def __init__(self, shards: int = 16, max_size_per_shard: int = 1000):
        self._shards = [Cache(max_size=max_size_per_shard) for _ in range(shards)]

    def _shard(self, key: str) -> Cache:
        return self._shards[hash(key) % len(self._shards)]

    def get(self, key: str): return self._shard(key).get(key)
    def set(self, key: str, value, ttl_seconds=None): self._shard(key).set(key, value, ttl_seconds)
    def delete(self, key: str): self._shard(key).delete(key)
```

### 5.3 Read-Write Lock for read-heavy workloads

Python's stdlib has no `RWLock`, but it's ~30 lines of code with `threading.Condition`. Lets N readers proceed in parallel, serializes writers.

### 5.4 Observer Pattern for eviction/expiration hooks

```python
class CacheEventListener(ABC):
    @abstractmethod
    def on_evicted(self, key: str, reason: str): ...

class MetricsListener(CacheEventListener):
    def on_evicted(self, key, reason):
        metrics.increment(f"cache.evicted.{reason}")
```

### 5.5 Sliding TTL

One-liner in `get`: if present, push `entry.expiry_time = now + default_ttl` and re-schedule. Gate behind a constructor flag.

### 5.6 Persistence / Durability (the HLD variant follow-up)

- **Write-Ahead Log** — append every `set`/`delete` to a log before applying; replay on restart.
- **Snapshots** — periodic full dump to disk (like Redis RDB).
- **Hybrid (AOF + RDB)** — snapshots bound replay time, log ensures no loss.

### 5.7 Distributed Cache

- Consistent hashing across nodes (Memcached-style)
- Gossip / Raft for membership
- Replication factor for hot keys

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | Restate problem, ask clarifying questions | Active vs lazy, bounded size, read/write ratio |
| 2-6 min | Entities + relationships | CacheEntry, EvictionPolicy, ExpirationManager, Cache |
| 6-9 min | Data structures + time/space | dict (O(1)) + min-heap (O(log N)) + OrderedDict (O(1) LRU) |
| 9-12 min | Design patterns | Strategy (eviction), Template (set skeleton), Decorator (concurrency), Observer (events) |
| 12-30 min | Code | Order: CacheEntry → EvictionPolicy → ExpirationManager → Cache → main |
| 30-35 min | Follow-ups | Concurrency (RLock → RWLock → striped), WAL, distributed |

---

## FINAL CODE: Write This in 15-20 Minutes

> This is the **streamlined, interview-ready** version. It has everything needed: active expiration with a background thread, LRU eviction via Strategy, thread-safety, and the heap-staleness fix. ~110 lines.

```python
import time
import heapq
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional


# ---- Value Object ----

@dataclass
class CacheEntry:
    value: Any
    expiry_time: float  # float('inf') = never expires


# ---- Strategy Pattern: Eviction ----

class EvictionPolicy(ABC):
    @abstractmethod
    def on_access(self, key: str): ...
    @abstractmethod
    def on_insert(self, key: str): ...
    @abstractmethod
    def on_remove(self, key: str): ...
    @abstractmethod
    def evict_victim(self) -> Optional[str]: ...


class LRUEvictionPolicy(EvictionPolicy):
    def __init__(self):
        self._order: OrderedDict[str, None] = OrderedDict()

    def on_access(self, key):
        if key in self._order:
            self._order.move_to_end(key)

    def on_insert(self, key):
        self._order[key] = None
        self._order.move_to_end(key)

    def on_remove(self, key):
        self._order.pop(key, None)

    def evict_victim(self):
        if not self._order:
            return None
        return self._order.popitem(last=False)[0]


# ---- Active Expiration: Min-Heap + Background Thread ----

class ExpirationManager:
    def __init__(self, cache: "Cache"):
        self._cache = cache
        self._heap: list[tuple[float, str]] = []
        self._cond = threading.Condition()
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def schedule(self, key: str, expiry_time: float):
        with self._cond:
            heapq.heappush(self._heap, (expiry_time, key))
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify_all()

    def _run(self):
        while True:
            with self._cond:
                while self._running and not self._heap:
                    self._cond.wait()
                if not self._running:
                    return
                expiry, key = self._heap[0]
                now = time.time()
                if expiry > now:
                    self._cond.wait(timeout=expiry - now)
                    continue
                heapq.heappop(self._heap)
            self._cache.remove_if_expired(key, expiry)


# ---- Cache Facade ----

class Cache:
    def __init__(self, max_size: int = 1000, eviction: Optional[EvictionPolicy] = None):
        self._store: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._eviction = eviction or LRUEvictionPolicy()
        self._lock = threading.RLock()
        self._expiration = ExpirationManager(self)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expiry_time <= time.time():
                self._remove_locked(key)
                return None
            self._eviction.on_access(key)
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None):
        expiry = time.time() + ttl_seconds if ttl_seconds is not None else float("inf")
        with self._lock:
            if key in self._store:
                self._remove_locked(key)
            if len(self._store) >= self._max_size:
                victim = self._eviction.evict_victim()
                if victim is not None:
                    self._remove_locked(victim)
            self._store[key] = CacheEntry(value, expiry)
            self._eviction.on_insert(key)
        if expiry != float("inf"):
            self._expiration.schedule(key, expiry)

    def delete(self, key: str):
        with self._lock:
            self._remove_locked(key)

    def remove_if_expired(self, key: str, scheduled_expiry: float):
        with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.expiry_time != scheduled_expiry:
                return  # stale heap entry — key was overwritten or already gone
            if entry.expiry_time <= time.time():
                self._remove_locked(key)

    def _remove_locked(self, key: str):
        if key in self._store:
            del self._store[key]
            self._eviction.on_remove(key)


# ---- Demo ----

if __name__ == "__main__":
    cache = Cache(max_size=3)
    cache.set("a", 1, ttl_seconds=1)
    cache.set("b", 2)           # never expires
    cache.set("c", 3, ttl_seconds=5)
    print(cache.get("a"))       # 1
    time.sleep(1.2)
    print(cache.get("a"))       # None — actively removed
    cache.set("d", 4)           # triggers LRU eviction of oldest
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| `get` / `set` / `delete` | `Cache.get`, `Cache.set`, `Cache.delete` |
| TTL with active expiration | `ExpirationManager` (heap + daemon thread) |
| Stale heap entry handling | `Cache.remove_if_expired` re-checks `scheduled_expiry` |
| Thread safety | `threading.RLock` around every state mutation |
| Bounded memory + eviction | `max_size` + `LRUEvictionPolicy` via Strategy |
| O(1) get/set | `dict` + `OrderedDict` |
| O(log N) expiration bookkeeping | `heapq` |
| Extensibility | New `EvictionPolicy` subclasses — zero changes to `Cache` |

### Writing Order (for the interview)

1. **`CacheEntry` dataclass** (~30 sec) — value + expiry
2. **`EvictionPolicy` ABC + `LRUEvictionPolicy`** (~3 min) — Strategy skeleton
3. **`ExpirationManager`** (~5 min) — heap, condition variable, daemon thread, heap-staleness comment
4. **`Cache`** (~6 min) — `get`, `set`, `delete`, `remove_if_expired`, `_remove_locked`
5. **Demo `__main__`** (~1 min) — shows TTL expiry + LRU eviction

**Total: ~15-16 min of coding, leaving 4-5 min for follow-ups.**

---

## Step 7: Common Follow-Up Questions & Answers

> **What to say:** "Happy to go deeper on any of these — which direction interests you most?"

### Q1: "How do you handle concurrent `get` and `set`?"

**A:** A single `RLock` serializes all operations — simple and correct. For a read-heavy workload, I'd upgrade to a **read-write lock** (N concurrent readers, exclusive writers) or **striped locking** — partition keys across N shards, each with its own lock. Striping is the standard choice at scale; `ConcurrentHashMap` in Java and Caffeine use the same idea. The heap-staleness design means the background expirer doesn't need global coordination — it re-validates inside the shard's lock.

### Q2: "Why min-heap instead of a sorted set or priority queue per bucket?"

**A:**
- Min-heap: O(log N) insert, O(1) peek, O(log N) pop. The background thread only needs the next-to-expire, so peek-amortized is ideal.
- Sorted set (like `SortedContainers.SortedList`): O(log N) everywhere but higher constants and memory overhead.
- Bucketed expiration (hashed time-wheel): O(1) amortized if TTLs are bounded and you can pick a bucket granularity — what Kafka and Netty use. Mention it if asked about extreme scale.

### Q3: "What happens if a key's TTL is updated? Isn't the old heap entry still there?"

**A:** Yes — and we deliberately leave it. Eagerly removing from the heap is O(N) or requires an indirection map. Instead, when the background thread pops an entry, it checks `entry.expiry_time == scheduled_expiry` — if not, the heap entry is stale and we skip it. Space overhead is at most proportional to the number of TTL updates between expirations; in practice negligible and bounded by the total number of live keys.

### Q4: "What if the background thread crashes?"

**A:** Three-layer defense:
1. The worker is a `daemon=True` thread with a broad `try/except` inside the loop so individual failures don't kill it.
2. `get` still checks expiry inline — so lazy fallback keeps correctness even if active cleanup stops.
3. A supervisor (watchdog thread or health check) can restart the manager. In production, I'd use something like a scheduled executor with restart-on-failure.

### Q5: "How would you scale this beyond one machine?"

**A:** Two axes:
- **Horizontal partitioning** — consistent hashing across N nodes (Memcached-style). Each node runs this exact cache for its slice of keys.
- **Replication** — for hot keys or HA, replicate each slice to K nodes with leader-follower or quorum writes (Redis Cluster model).

Consistent hashing means adding/removing a node only reshuffles `1/N` of the keys, not everything.

### Q6: "How would you add persistence / Write-Ahead Log (WAL)?"

**A:** Append every mutation (`set key value ttl`, `delete key`) to a log file *before* applying. On restart, replay the log. To bound replay time, periodically snapshot the in-memory state and truncate the log. This is the Redis AOF+RDB model. Tradeoffs:
- `fsync` per write = durable but slow. Redis's `appendfsync everysec` is a good default — at most 1 second of data loss.
- Serialization cost is non-trivial; batch writes if throughput matters.

### Q7: "What about cache stampede / thundering herd on expiry?"

**A:** When a hot key expires, many threads can miss simultaneously and stampede the underlying data source. Mitigations:
- **Single-flight** — first miss recomputes while others wait on a future/promise keyed by the cache key.
- **Probabilistic early expiration** — refresh before expiry with probability that rises as TTL approaches zero (XFetch algorithm).
- **Stale-while-revalidate** — return stale value, refresh asynchronously.

### Q8: "LRU vs LFU — when would you pick which?"

**A:**
- **LRU** — simple, great for recency-biased workloads (session caches, request caches).
- **LFU** — better for workloads with a stable hot set + bursty noise; a one-off scan doesn't evict your hot keys. TinyLFU / W-TinyLFU (used by Caffeine) is the modern standard — keeps a small admission filter so scans can't pollute the main cache.

### Q9: "How do you prevent a single giant key from dominating memory?"

**A:** Two things: (a) track entry size (via `sys.getsizeof` or user-supplied sizer) and enforce a byte budget in addition to count; (b) reject or shard oversized values at `set`. Real caches (Caffeine, Memcached) use byte-weighted eviction, not count-based.

### Q10: "Python's GIL — does striped locking actually buy anything?"

**A:** Honest answer: in pure CPython, the GIL serializes bytecode so striping only helps when threads release the GIL (I/O, C extensions). For a pure-Python cache, the win is mostly from reducing *lock contention latency*, not true parallelism. In a JVM/Go/Rust runtime the parallelism win is real. If I/O is in the mix (network-fetched values, disk WAL), striping helps meaningfully even in Python.

### Q11: "What metrics would you expose?"

**A:** Hit rate, miss rate, eviction rate (by reason: capacity vs TTL), p50/p99 latency for `get`/`set`, size (count and bytes), heap size, and background-thread lag (how far behind the current time the oldest unprocessed expiry is). Wrap the cache in a `MetricsCache` decorator so instrumentation is opt-in.

### Q12: "Show where your design is O(1) vs O(log N)."

**A:**
- `get` — O(1): dict lookup + `OrderedDict.move_to_end`
- `set` (no eviction) — O(log N): dict insert O(1) + heap push O(log N)
- `set` (with eviction) — O(log N): same + `OrderedDict.popitem` O(1)
- `delete` — O(1): dict delete + `OrderedDict.pop`
- Background expiration — O(log N) per expiry
- **Space** — O(N) for the dict, O(N + stale) for the heap. Stale entries are bounded by TTL-update rate; in the worst case we could add a periodic compaction pass if it matters.
