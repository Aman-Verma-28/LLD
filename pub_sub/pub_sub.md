# Low-Level Design: Multi-threaded Publisher-Subscriber Message Queue — Uber SDE-2 Interview Guide

> **Timing budget:** 10-12 mins architecture + 15-20 mins code = ~30-35 mins total.

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me restate the problem and then ask a few clarifying questions before I jump into design."

### Your Understanding (State This)

- Build an in-memory, thread-safe **Publisher-Subscriber** message queue.
- **Multiple publishers** can publish messages to a named **topic**.
- **Multiple subscribers** can subscribe to one or more topics and receive every message published to that topic.
- Delivery must be **concurrent** — publishers and subscribers should not block each other unnecessarily.
- Messages on a single topic must preserve **FIFO order** from the perspective of the broker.
- Subscriptions and un-subscriptions can happen at any time, concurrently with publishing.

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Is this in-process / in-memory, or distributed across machines? | In-process, in-memory. Kafka-style is out of scope, but design should be extensible toward it. |
| 2 | What are the delivery guarantees — at-most-once, at-least-once, exactly-once? | At-least-once is the realistic default. Mention the tradeoffs. |
| 3 | Do we need message persistence or replay from offsets? | No persistence for the 20-min implementation, but design should allow a persistence hook. |
| 4 | Should message ordering be per-topic, per-publisher, or global? | **Per-topic FIFO** (Kafka-style). Global ordering is too expensive. |
| 5 | Should delivery to subscribers be synchronous (blocking the publisher) or asynchronous (fan-out via worker pool)? | Asynchronous — publisher returns fast, each subscriber is invoked on a worker thread. |
| 6 | How do we handle a slow/blocking subscriber? | Bounded per-subscriber buffer; drop-newest or block-publisher, or route to a Dead Letter Queue. I'll keep it simple and mention the hook. |
| 7 | Is the subscriber set fixed at startup or dynamic? | Dynamic — `subscribe`/`unsubscribe` at runtime, thread-safe. |
| 8 | Do we need filtering (message selectors) or wildcards on topic names? | Not for v1. Design should not block it though. |

### Confirmed Requirements (v1)

1. Thread-safe `Broker` that owns topics.
2. `Publisher.publish(topic, message)` — non-blocking, thread-safe.
3. `Subscriber.on_message(message)` — called by the broker for every message on a subscribed topic.
4. `Broker.subscribe(topic, subscriber)` / `unsubscribe(topic, subscriber)` — thread-safe.
5. Per-topic **FIFO** ordering.
6. Concurrent publishers and subscribers without data races.
7. Graceful shutdown that drains in-flight messages.

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Let me list the key entities, their responsibilities, and the relationships between them."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **Message** | Immutable value object: `id`, `topic`, `payload`, `timestamp`. Immutability is a thread-safety primitive. |
| **Subscriber (Interface)** | Defines `on_message(message)`. Any consumer implements this. |
| **Publisher** | Thin façade with a reference to the broker; exposes `publish(topic, payload)`. |
| **Topic** | Holds a thread-safe FIFO queue of messages + the set of subscribers + a dispatcher thread. Encapsulates per-topic ordering. |
| **Broker** | Central orchestrator. Registers topics, accepts publishes, dispatches to subscribers via a worker pool. Singleton in practice. |
| **DeliveryExecutor** | `ThreadPoolExecutor` that runs subscriber callbacks off the publisher's thread. |
| **SubscriberFactory** (optional) | Creates subscribers (sync, async, logging, etc.) — extensibility. |

### Relationships

```
Publisher ---> Broker ---> Topic ---> [Subscriber, Subscriber, ...]
                 |             |
                 |             +--> MessageQueue (thread-safe FIFO per topic)
                 |             +--> DispatcherThread (drains queue, fans out)
                 |
                 +---> DeliveryExecutor (ThreadPoolExecutor — runs on_message)
                 +---> topics: Dict[str, Topic]  (guarded by RLock)

Subscriber (ABC)
    |-- PrintSubscriber       (logs to stdout)
    |-- BufferingSubscriber   (collects to list — used in tests)
    |-- (future) PersistingSubscriber, NetworkSubscriber, etc.
```

### Why this shape

- **Per-topic dispatcher thread** is the cleanest way to guarantee **per-topic FIFO**. If we fan out every message on the pool directly from `publish()`, messages can be delivered out of order under contention.
- **Broker owns topics** (not publishers) so publishers remain stateless and cheap.
- **Subscribers implement an interface** — the broker doesn't care what they do with the message (Dependency Inversion).

---

## Step 3: Design Patterns & Tradeoffs (2-3 mins)

> **What to say:** "I'm going to lean on five patterns; each one earns its place."

### Pattern 1: Observer Pattern (Core of Pub-Sub)

**Why:** Pub-Sub *is* the Observer pattern generalized across topics. Subscribers observe a topic; the topic notifies them on every publish.

**How:** `Topic` holds a `set[Subscriber]`. On publish → enqueue → dispatcher notifies every subscriber.

**Tradeoff:** Tight coupling between the subject (topic) and observer list — mitigated by the `Subscriber` interface so the topic only knows the contract.

### Pattern 2: Singleton (Broker)

**Why:** One process should have one broker — multiple brokers would split the topic namespace and confuse publishers.

**How:** Module-level `Broker()` instance, or a classmethod `Broker.instance()`.

**Tradeoff:** Singletons are hard to test. I'll make the class itself instantiable and just use a module-level instance in `main`. Tests can build their own broker — best of both worlds.

### Pattern 3: Producer-Consumer (Publisher → Topic Queue → Dispatcher)

**Why:** Decouples publish rate from delivery rate. Publishers drop messages into a bounded queue; the dispatcher drains at its own pace.

**How:** `queue.Queue` per topic. A dedicated dispatcher thread does `q.get()` in a loop.

**Tradeoff:** One thread per topic can be expensive at scale (1000+ topics). Production would use a shared dispatcher pool keyed by topic partition. For this interview, per-topic is clean and easy to defend.

### Pattern 4: Strategy Pattern (Delivery Mode — mention)

**Why:** Future need to support **push** (broker calls subscriber) vs **pull** (subscriber polls) delivery without forking the broker.

**How:** A `DeliveryStrategy` interface the `Topic` uses.

**Tradeoff:** Over-engineered for v1 — I'll mention it as an extensibility hook, not implement it.

### Pattern 5: Factory Pattern (Subscribers — mention)

**Why:** As subscriber variants grow (sync, async, persistent, filtered), centralizing construction keeps config-driven wiring sane.

**Tradeoff:** Overkill for v1. Name-drop it during extensibility.

> **Interview tip:** The two patterns the interviewer definitely wants to hear are **Observer** and **Producer-Consumer**. Singleton is a nice-to-have. Strategy and Factory are extensibility name-drops — don't burn time coding them.

---

## Step 4: Implementation — Full Design (Reference)

> This is the complete version with every piece explained. The streamlined 20-minute version is at the bottom.

### 4.1 Message (Immutable Value Object)

```python
import uuid
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)   # frozen=True makes it immutable → thread-safe to share
class Message:
    topic: str
    payload: Any
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
```

**Why immutable:** A shared object that can't mutate needs no lock. Passing the same `Message` reference to 100 subscribers is safe.

### 4.2 Subscriber Interface (Strategy / Observer)

```python
from abc import ABC, abstractmethod


class Subscriber(ABC):
    @abstractmethod
    def on_message(self, message: Message) -> None:
        ...

    # Stable identity so a subscriber can be added to sets and unsubscribed
    @property
    def id(self) -> str:
        return f"{type(self).__name__}#{id(self)}"
```

```python
class PrintSubscriber(Subscriber):
    def __init__(self, name: str):
        self.name = name

    def on_message(self, message: Message) -> None:
        print(f"[{self.name}] topic={message.topic} payload={message.payload}")
```

### 4.3 Topic (Per-topic Queue + Dispatcher Thread)

```python
import queue
import threading
from typing import Set


class Topic:
    def __init__(self, name: str, buffer_size: int = 1000):
        self.name = name
        # queue.Queue is thread-safe (internally uses a Lock + Condition)
        # and guarantees FIFO — this is how we preserve message ordering.
        self._queue: queue.Queue = queue.Queue(maxsize=buffer_size)
        self._subscribers: Set[Subscriber] = set()
        # RLock so the dispatcher can call subscribe/unsubscribe safely
        # (re-entrancy isn't strictly required here, but it's cheap insurance).
        self._sub_lock = threading.RLock()
        self._shutdown = threading.Event()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name=f"dispatcher-{name}",
            daemon=True,
        )
        self._dispatcher.start()

    def subscribe(self, sub: Subscriber) -> None:
        with self._sub_lock:
            self._subscribers.add(sub)

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._sub_lock:
            self._subscribers.discard(sub)

    def publish(self, message: Message) -> None:
        # Put blocks when buffer is full → natural backpressure on publishers.
        self._queue.put(message)

    def _snapshot_subscribers(self) -> list:
        # Copy under lock so dispatch isn't racing with subscribe/unsubscribe.
        with self._sub_lock:
            return list(self._subscribers)

    def _dispatch_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                # Blocking get with timeout lets us check shutdown flag.
                msg = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            for sub in self._snapshot_subscribers():
                # Deliver on an executor so one slow subscriber doesn't block
                # the dispatcher. See Broker._executor below.
                Broker.instance()._executor.submit(self._safe_deliver, sub, msg)
            self._queue.task_done()

    @staticmethod
    def _safe_deliver(sub: Subscriber, msg: Message) -> None:
        try:
            sub.on_message(msg)
        except Exception as e:
            # Never let one subscriber kill the dispatcher.
            print(f"[ERROR] subscriber {sub.id} failed: {e}")

    def shutdown(self) -> None:
        self._shutdown.set()
        self._dispatcher.join(timeout=2)
```

**Key thread-safety claims, with justification:**

- **`queue.Queue`** uses an internal mutex + Condition variable → producers (publishers) and the consumer (dispatcher) are safe.
- **`threading.RLock` around `_subscribers`** → `subscribe`/`unsubscribe`/`_snapshot_subscribers` are mutually exclusive.
- **Snapshot before dispatch** → avoids "collection modified during iteration" and avoids holding the lock while calling user code (which could be slow).
- **`_shutdown` is a `threading.Event`** → safe publish/check from multiple threads.

### 4.4 Broker (Singleton Orchestrator)

```python
from concurrent.futures import ThreadPoolExecutor
from typing import Dict


class Broker:
    _instance: "Broker" = None
    _instance_lock = threading.Lock()

    def __init__(self, worker_threads: int = 8):
        self._topics: Dict[str, Topic] = {}
        self._topics_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="delivery",
        )

    @classmethod
    def instance(cls) -> "Broker":
        # Double-checked locking for the singleton
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _get_or_create_topic(self, name: str) -> Topic:
        with self._topics_lock:
            topic = self._topics.get(name)
            if topic is None:
                topic = Topic(name)
                self._topics[name] = topic
            return topic

    def publish(self, topic_name: str, payload) -> None:
        message = Message(topic=topic_name, payload=payload)
        self._get_or_create_topic(topic_name).publish(message)

    def subscribe(self, topic_name: str, sub: Subscriber) -> None:
        self._get_or_create_topic(topic_name).subscribe(sub)

    def unsubscribe(self, topic_name: str, sub: Subscriber) -> None:
        with self._topics_lock:
            topic = self._topics.get(topic_name)
        if topic:
            topic.unsubscribe(sub)

    def shutdown(self) -> None:
        with self._topics_lock:
            topics = list(self._topics.values())
        for t in topics:
            t.shutdown()
        self._executor.shutdown(wait=True)
```

### 4.5 Publisher (Thin Façade)

```python
class Publisher:
    def __init__(self, name: str, broker: Broker = None):
        self.name = name
        self._broker = broker or Broker.instance()

    def publish(self, topic: str, payload) -> None:
        self._broker.publish(topic, payload)
```

Publishers are stateless — they just call the broker. Keeping this class exists only so the domain vocabulary matches the problem statement and so we can add metrics / authz hooks per-publisher later.

### 4.6 Demo Main

```python
import time


if __name__ == "__main__":
    broker = Broker.instance()

    # Two subscribers on "orders", one on "payments"
    s1 = PrintSubscriber("OrderIndexer")
    s2 = PrintSubscriber("OrderAnalytics")
    s3 = PrintSubscriber("PaymentAuditor")

    broker.subscribe("orders", s1)
    broker.subscribe("orders", s2)
    broker.subscribe("payments", s3)

    # Two concurrent publishers
    def publish_orders():
        p = Publisher("order-svc")
        for i in range(5):
            p.publish("orders", {"order_id": i})

    def publish_payments():
        p = Publisher("payment-svc")
        for i in range(3):
            p.publish("payments", {"txn_id": i})

    t1 = threading.Thread(target=publish_orders)
    t2 = threading.Thread(target=publish_payments)
    t1.start(); t2.start()
    t1.join();  t2.join()

    time.sleep(0.5)   # let the dispatchers drain
    broker.shutdown()
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design is intentionally extensible in these directions."

### 5.1 Delivery Semantics

Default is **at-least-once** (subscriber may receive a duplicate on retry). To support **at-most-once**, drop the retry. To approximate **exactly-once**, subscribers must de-duplicate by `message.id` (idempotency). True exactly-once requires distributed transactions — out of scope.

### 5.2 Dead Letter Queue for Failing Subscribers

Wrap `_safe_deliver` to retry N times with backoff, then send the message to a `__dlq__` topic. Operators subscribe to the DLQ for alerting.

### 5.3 Backpressure

`queue.Queue(maxsize=N)` makes `publish` block when the buffer is full. Alternatives: drop-oldest, drop-newest, or bounded per-subscriber queues so one slow consumer doesn't back up the whole topic.

### 5.4 Per-subscriber Queues (Strong Isolation)

Instead of fanning out from one topic queue, give each subscriber its own queue + worker. A slow `Analytics` subscriber can never block the `Indexer`. Tradeoff: memory grows O(topics × subscribers).

### 5.5 Partitioning / Sharding (Kafka-style scaling)

Split a topic into N partitions; hash messages by key so same-key messages always land on the same partition → preserves per-key ordering while allowing parallel dispatch across partitions.

### 5.6 Persistence

Swap `queue.Queue` for a durable log (append-only file, SQLite, RocksDB). Add consumer offsets. That's how we'd evolve toward Kafka.

### 5.7 Filtering / Wildcards

Subscribers can register with a predicate or a topic pattern (`orders.*`). Implement with a trie of topic segments on the broker.

### 5.8 Pull-mode Consumers

Expose `Topic.poll(subscriber_id, max_messages)` alongside push. Needed for batch consumers (ETL jobs).

### 5.9 Observability

Add metrics counters (published, delivered, failed, queue-depth) per topic; expose via `/metrics`. Trivial once the Broker is the chokepoint.

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | Restate problem, ask clarifying questions | Delivery guarantees, ordering scope, persistence, sync vs async delivery |
| 2-5 min | Identify entities + relationships | Message, Subscriber, Publisher, Topic, Broker, DeliveryExecutor |
| 5-8 min | Discuss patterns | Observer (core), Producer-Consumer, Singleton-Broker; name-drop Strategy/Factory |
| 8-10 min | Outline thread-safety strategy | Immutable Message, `queue.Queue` for FIFO, RLock on subscriber set, ThreadPoolExecutor for fan-out, per-topic dispatcher thread for ordering |
| 10-30 min | Code: Message → Subscriber → Topic → Broker → Publisher → main | Write in that order — dependencies flow that way |
| 30-35 min | Walk through extensibility + follow-ups | DLQ, backpressure, partitioning, persistence |

---

## FINAL CODE: Write This in ~18 Minutes

> Streamlined, runnable. Covers: Observer (Subscriber), Producer-Consumer (per-topic queue + dispatcher), Singleton (Broker), immutable messages, thread-safe subscribe/unsubscribe, per-topic FIFO, async fan-out via ThreadPoolExecutor, graceful shutdown. ~150 lines.

```python
import queue
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Set


# ---- Immutable Message ----

@dataclass(frozen=True)
class Message:
    topic: str
    payload: Any
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


# ---- Subscriber Interface (Observer) ----

class Subscriber(ABC):
    @abstractmethod
    def on_message(self, message: Message) -> None:
        ...


class PrintSubscriber(Subscriber):
    def __init__(self, name: str):
        self.name = name

    def on_message(self, message: Message) -> None:
        print(f"[{self.name}] topic={message.topic} payload={message.payload}")


# ---- Topic: per-topic FIFO queue + dispatcher thread ----

class Topic:
    def __init__(self, name: str, executor: ThreadPoolExecutor, buffer_size: int = 1000):
        self.name = name
        self._queue: queue.Queue = queue.Queue(maxsize=buffer_size)
        self._subscribers: Set[Subscriber] = set()
        self._lock = threading.RLock()
        self._executor = executor
        self._shutdown = threading.Event()
        self._dispatcher = threading.Thread(
            target=self._run, name=f"dispatcher-{name}", daemon=True
        )
        self._dispatcher.start()

    def subscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subscribers.add(sub)

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self, message: Message) -> None:
        self._queue.put(message)            # blocks on backpressure

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                msg = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            # Snapshot under lock so iteration is safe and we don't hold
            # the lock while invoking user code.
            with self._lock:
                subs = list(self._subscribers)
            for sub in subs:
                self._executor.submit(self._safe_deliver, sub, msg)
            self._queue.task_done()

    @staticmethod
    def _safe_deliver(sub: Subscriber, msg: Message) -> None:
        try:
            sub.on_message(msg)
        except Exception as e:
            print(f"[ERROR] subscriber failed: {e}")

    def shutdown(self) -> None:
        self._shutdown.set()
        self._dispatcher.join(timeout=2)


# ---- Broker: thread-safe singleton orchestrator ----

class Broker:
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self, workers: int = 8):
        self._topics: Dict[str, Topic] = {}
        self._topics_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="delivery"
        )

    @classmethod
    def instance(cls) -> "Broker":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _topic(self, name: str) -> Topic:
        with self._topics_lock:
            t = self._topics.get(name)
            if t is None:
                t = Topic(name, self._executor)
                self._topics[name] = t
            return t

    def publish(self, topic: str, payload) -> None:
        self._topic(topic).publish(Message(topic=topic, payload=payload))

    def subscribe(self, topic: str, sub: Subscriber) -> None:
        self._topic(topic).subscribe(sub)

    def unsubscribe(self, topic: str, sub: Subscriber) -> None:
        with self._topics_lock:
            t = self._topics.get(topic)
        if t:
            t.unsubscribe(sub)

    def shutdown(self) -> None:
        with self._topics_lock:
            topics = list(self._topics.values())
        for t in topics:
            t.shutdown()
        self._executor.shutdown(wait=True)


# ---- Publisher: thin façade ----

class Publisher:
    def __init__(self, name: str, broker: Broker = None):
        self.name = name
        self._broker = broker or Broker.instance()

    def publish(self, topic: str, payload) -> None:
        self._broker.publish(topic, payload)


# ---- Demo ----

if __name__ == "__main__":
    broker = Broker.instance()

    indexer   = PrintSubscriber("OrderIndexer")
    analytics = PrintSubscriber("OrderAnalytics")
    auditor   = PrintSubscriber("PaymentAuditor")

    broker.subscribe("orders", indexer)
    broker.subscribe("orders", analytics)
    broker.subscribe("payments", auditor)

    def publish_orders():
        p = Publisher("order-svc")
        for i in range(5):
            p.publish("orders", {"order_id": i})

    def publish_payments():
        p = Publisher("payment-svc")
        for i in range(3):
            p.publish("payments", {"txn_id": i})

    t1 = threading.Thread(target=publish_orders)
    t2 = threading.Thread(target=publish_payments)
    t1.start(); t2.start()
    t1.join();  t2.join()

    time.sleep(0.5)
    broker.shutdown()
```

### What this covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| Thread-safe publish | `queue.Queue.put` |
| Thread-safe subscribe/unsubscribe | `RLock` around `_subscribers` set |
| Per-topic FIFO | Single dispatcher thread draining one `queue.Queue` per topic |
| Concurrent fan-out | `ThreadPoolExecutor.submit` per (subscriber, message) |
| Publishers don't block on slow subs | Executor decouples dispatcher from subscriber latency |
| Observer pattern | `Subscriber` ABC + `Topic._subscribers` |
| Producer-Consumer pattern | Publisher → topic queue → dispatcher → executor |
| Singleton broker | `Broker.instance()` with double-checked locking |
| Graceful shutdown | `Event` flag + `executor.shutdown(wait=True)` |
| Immutable messages | `@dataclass(frozen=True)` |

### Writing order (for the interview)

1. **Imports + Message** (~1 min) — imports and the frozen dataclass.
2. **Subscriber ABC + PrintSubscriber** (~2 min).
3. **Topic** (~7 min) — the meatiest class: queue, lock, dispatcher thread, safe delivery, shutdown.
4. **Broker** (~5 min) — singleton, topic registry, submit wiring.
5. **Publisher** (~1 min) — trivial wrapper.
6. **Main demo** (~2 min) — two publisher threads, three subscribers, join + shutdown.

**Total: ~18 min.** Leaves 2 min slack to debug a typo or answer a mid-code question.

---

## Step 7: Follow-up Q&A (Likely Questions)

### Q1. How did you apply SOLID?

- **S — Single Responsibility:** `Message` is a value object only. `Topic` only manages one topic's queue + subscribers. `Broker` only routes. `Publisher` only publishes. `Subscriber` only consumes. No class does two jobs.
- **O — Open/Closed:** Adding a new subscriber type (persisting, filtering, batch) means writing a new `Subscriber` subclass — zero changes to `Topic` or `Broker`. Same for new delivery strategies.
- **L — Liskov:** Every `Subscriber` subclass honors the `on_message(Message) -> None` contract. The broker uses them interchangeably.
- **I — Interface Segregation:** The `Subscriber` interface has exactly one method. Publishers don't depend on subscriber internals; subscribers don't depend on publisher internals.
- **D — Dependency Inversion:** `Topic` depends on the **`Subscriber` abstraction**, not any concrete subscriber. `Publisher` depends on the `Broker` abstraction (easy to inject a fake broker in tests).

### Q2. Which design patterns did you use and why?

| Pattern | Where | Why |
|---------|-------|-----|
| **Observer** | `Subscriber` + `Topic._subscribers` | Pub-Sub is Observer scaled across named channels |
| **Producer-Consumer** | Publisher → `queue.Queue` → dispatcher | Decouples publish rate from delivery rate; natural backpressure |
| **Singleton** | `Broker.instance()` | One broker per process; avoids fragmented namespaces |
| **Strategy** (mention) | `DeliveryStrategy` for push vs pull | Extensibility hook for pull-mode consumers |
| **Factory** (mention) | `SubscriberFactory` | Centralizes config-driven subscriber construction |

### Q3. Explain locks, mutex, and synchronized in your code.

- A **lock** and a **mutex** are the same concept: a mutual-exclusion primitive that ensures only one thread holds it at a time. In Java, `synchronized` is sugar for acquiring the object's intrinsic lock.
- In Python I used:
  - **`threading.Lock`** — the basic mutex. Used implicitly in `queue.Queue` and for the singleton's double-checked locking.
  - **`threading.RLock`** — reentrant lock. Used for `_subscribers` and `_topics` so the same thread can re-acquire without deadlocking (cheap insurance if I later add a method that calls another method under the same lock).
  - **`threading.Event`** — a cross-thread flag for shutdown. Safer than polling a plain bool.
  - **`queue.Queue`** — internally uses `Lock` + `Condition` to give me thread-safe FIFO semantics. I don't reimplement that.
- The equivalent of a Java `synchronized` method is `with self._lock:` in Python. That's what I use for every mutation of `_subscribers` and `_topics`.

### Q4. How is message ordering maintained?

**Per-topic FIFO is guaranteed**, by two things:

1. **Each topic has exactly one `queue.Queue`.** `queue.Queue.put` is atomic and FIFO.
2. **Each topic has exactly one dispatcher thread** draining that queue. So messages leave the queue in publish order and are submitted to the executor in that order.

**Caveat I'd volunteer:** once messages are on the `ThreadPoolExecutor`, multiple workers can run `on_message` in parallel. So subscriber A might see message 2 complete before message 1. **That's fine** — order is preserved *at dispatch*, not at completion. If a subscriber needs strict single-threaded receipt, give it its own queue + single worker (see extensibility §5.4).

**Global ordering across topics is not guaranteed** — by design. Enforcing it would require a global lock and kill concurrency.

### Q5. What thread-safety problems did you explicitly prevent?

1. **Lost updates on subscriber set** → solved by `RLock` around every read/write.
2. **Iterating while modifying** → solved by snapshotting the subscriber set under the lock, then iterating on the copy.
3. **Mutable shared state in messages** → solved by `frozen=True` on the `Message` dataclass.
4. **Broker singleton race** → solved by double-checked locking on `_instance`.
5. **Slow subscriber blocking the dispatcher** → solved by submitting delivery to a `ThreadPoolExecutor`.
6. **Exception in a subscriber killing the dispatcher** → solved by wrapping `on_message` in try/except.

### Q6. At-most-once vs at-least-once vs exactly-once — what did you implement?

**At-most-once** as coded (no retries on subscriber exception). To get **at-least-once**, add retry-with-backoff around `_safe_deliver` and route to DLQ after N failures. **Exactly-once** requires idempotent subscribers + de-dup by `message.id`, or distributed transactions — which is outside in-memory scope.

### Q7. What about Python's GIL? Does your code even benefit from threads?

- The GIL serializes CPU-bound Python bytecode. But pub-sub subscribers are almost always **I/O-bound** (DB writes, HTTP calls, log writes) — and threads release the GIL during I/O, so concurrency is real.
- For CPU-bound subscribers, swap the `ThreadPoolExecutor` for a `ProcessPoolExecutor`. The interface `executor.submit(...)` stays the same. That's another OCP win.

### Q8. What if a subscriber is slow — does it block publishers or other subscribers?

- It does **not** block publishers — publishers only interact with the topic queue, and the dispatcher submits to the executor immediately.
- It **can** starve other subscribers if all executor workers are busy on slow calls. Two fixes:
  1. **Size the executor** generously (8-32 workers).
  2. **Per-subscriber queue + dedicated worker** — full isolation at the cost of memory.

### Q9. How would you scale this horizontally?

- **Partition topics by key** (same key → same partition → same dispatcher) so per-key ordering holds while throughput scales with partition count.
- **Shard the broker** across machines; use a consistent-hash ring keyed on topic name.
- At that point you're basically re-implementing Kafka. The right answer in real life is "use Kafka."

### Q10. Why `queue.Queue` instead of a `list` + `Lock`?

- `queue.Queue` gives you FIFO, thread-safe enqueue/dequeue, blocking get, size-bounded backpressure, and `task_done()`/`join()` for draining — all for free.
- A `list` + `Lock` gets you none of that. You'd reinvent `Condition` variables for the "empty queue" blocking case, and you'd still have to write the backpressure logic. It's strictly worse.

### Q11. Why `ThreadPoolExecutor` instead of spawning a thread per message?

- Thread creation is expensive (~ms on Linux, plus 8MB stack by default). At any realistic message rate, per-message threads exhaust memory and schedule poorly.
- The executor amortizes thread cost across many tasks and caps concurrency so we don't overwhelm the OS.

### Q12. Graceful shutdown — walk me through it.

1. `Broker.shutdown()` snapshots the topics list.
2. For each topic: set the `_shutdown` Event → the dispatcher exits its loop on the next timeout.
3. `join(timeout=2)` the dispatcher thread.
4. `executor.shutdown(wait=True)` blocks until every in-flight `on_message` finishes.
5. No orphan threads, no half-delivered messages.

A stronger version would `queue.join()` on each topic queue before shutting the dispatcher down, so no enqueued messages are dropped.

### Q13. How would you test this?

- **Unit:** `Topic` with a fake `Executor` that runs synchronously → deterministic ordering assertions. Verify `subscribe`/`unsubscribe` race safety with a stress test that races N threads for 1 second against the same set.
- **Integration:** spin up the real broker, publish 10k messages from 4 threads, assert every `BufferingSubscriber` received exactly 10k and that per-topic IDs are monotonically non-decreasing in arrival order.
- **Chaos:** a subscriber that raises randomly → assert the dispatcher keeps running and sibling subscribers still receive everything.

### Q14. If the interviewer pushes: "Can you do this without the dispatcher thread?"

Yes — you could fan out inline from `publish()` by submitting directly to the executor. **But** you lose per-topic FIFO, because two concurrent `publish` calls can interleave their `submit` calls arbitrarily. The dispatcher thread is the thing that serializes per-topic dispatch order. Removing it is a correctness regression, not a simplification. That's the case for keeping it.

---

## Closing Statement (30 seconds at the end)

> "So to recap: the design treats pub-sub as a scaled-up Observer, wraps each topic in a Producer-Consumer with its own FIFO queue and dispatcher thread to guarantee per-topic ordering, and uses a thread pool to fan out deliveries without letting a slow subscriber block anyone. Thread-safety comes from three places — immutable messages, `queue.Queue` for the FIFO, and `RLock` around the subscriber set. The Broker is a singleton but testable because the class is instantiable. Extensibility points — DLQ, backpressure, per-subscriber queues, partitioning, persistence — are all additive without touching existing code, which is how I'd evolve it toward Kafka."
