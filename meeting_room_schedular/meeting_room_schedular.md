# Low-Level Design: Meeting Room Scheduler - Uber SDE-2 Interview Guide

> **Total budget:** ~30-35 mins. Architecture talk: 10-12 mins. Code: 15-20 mins.

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Before I jump into design, let me restate the problem and lock down a few assumptions."

### Your Understanding (State This)

- We have **N meeting rooms**, each with a fixed seating capacity.
- A **stream of booking requests** arrives, each with `(start_time, end_time, required_capacity)`.
- For every request, the system must **assign a room** such that:
  - The room's capacity >= required capacity
  - The room is **not double-booked** for the requested interval
- Every booking/cancel action is **audited per-room** (room-scoped audit log).
- Audit log entries older than **X days** are **automatically cleaned up**.
- Follow-up: strictly enforce capacity constraints (already in the core).

### Clarifying Questions (Ask These)

| # | Question | Expected / Reasonable Answer |
|---|----------|------------------------------|
| 1 | Are rooms heterogeneous (different capacities) or all the same? | Different capacities — e.g., 4-seater, 10-seater, 50-seater |
| 2 | Are intervals continuous (any minute) or discrete slots (e.g., 15-min)? | Continuous — `[start, end)` half-open intervals |
| 3 | If no room is available, do we reject, queue, or suggest the next slot? | Reject for v1; mention queue/suggestion as an extension |
| 4 | When multiple rooms fit, how do we pick? Smallest-fit, first-fit, round-robin? | Best-fit (smallest room that satisfies capacity) — minimizes waste |
| 5 | Concurrent booking requests from multiple threads/services? | Yes, treat as concurrent — need locking around per-room state |
| 6 | Is cleanup background (cron-like) or lazy (on read)? | Background sweep; mention lazy as alternative |
| 7 | What goes into the audit log? | `action, meeting_id, room_id, actor, timestamp, details` |
| 8 | Do we persist anything, or in-memory? | In-memory for the interview; design with a Repository seam for swap |
| 9 | Cancellation supported? | Yes — booking + cancellation are both audited |
| 10 | Time zone handling? | Assume all times are UTC `datetime` objects |

### Confirmed Requirements

1. N heterogeneous rooms with capacity.
2. Booking requests arrive as a stream — `(start, end, capacity)`.
3. Allocator picks a room: capacity-satisfying + non-overlapping.
4. Best-fit allocation strategy by default; pluggable.
5. Per-room audit log capturing every booking/cancel.
6. Automatic cleanup of audit entries older than `X` days (background job).
7. Thread-safe for concurrent requests.

---

## Step 2: Core Entities & Relationships (2-3 mins)

> **What to say:** "Here are the entities I'd model and how they connect."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **TimeInterval** | Value object: `(start, end)` with overlap check |
| **Room** | `id`, `capacity`, owns its booking calendar |
| **Booking** | `id`, `room_id`, `interval`, `requested_capacity`, `organizer`, `status` |
| **RoomCalendar** | Per-room sorted list of bookings; overlap detection + insert |
| **AllocationStrategy (Interface)** | Picks a room from the candidate list (Strategy Pattern) |
| **BookingResult** | Success(booking) \| Failure(reason) — explicit, no exceptions for control flow |
| **AuditEvent** | Immutable record: `(action, room_id, booking_id, actor, timestamp, details)` |
| **AuditLog** | Per-room append-only event store + time-window query + purge |
| **AuditService** | Receives events from observers, routes to per-room `AuditLog` |
| **EventListener (Interface)** | Observer Pattern — notified on booking lifecycle events |
| **CleanupPolicy** | Defines retention window (X days) and sweep cadence |
| **CleanupScheduler** | Background worker that calls `purge_older_than(...)` periodically |
| **MeetingRoomScheduler** | Facade/Controller — accepts requests, runs allocation, persists, notifies |

### Relationships

```
MeetingRoomScheduler (Facade / Controller)
    |
    |--- has-a ---> List[Room]
    |--- has-a ---> AllocationStrategy        (pluggable)
    |--- has-a ---> EventBus                  (Observer dispatch)
    |--- has-a ---> CleanupScheduler          (lifecycle owner)

Room
    |--- has-a ---> RoomCalendar              (its own bookings)
    |--- has-a ---> capacity (int)

RoomCalendar
    |--- holds ---> sorted List[Booking]      (interval-ordered)

AllocationStrategy (ABC)
    |-- BestFitStrategy        (smallest room that satisfies capacity)
    |-- FirstFitStrategy       (first room that fits)
    |-- (future) RoundRobinStrategy, LoadBalancedStrategy

EventBus
    |--- notifies --> List[EventListener]

EventListener (ABC)
    |-- AuditListener          (writes AuditEvents into AuditLog)
    |-- (future) MetricsListener, NotificationListener

AuditService
    |--- has-a ---> Dict[room_id, AuditLog]   (per-room log)

AuditLog
    |--- holds ---> List[AuditEvent]          (append-only, time-ordered)
    |--- supports --> purge_older_than(cutoff)

CleanupScheduler
    |--- uses ----> CleanupPolicy
    |--- calls ---> AuditService.purge(...)   (periodic background sweep)
```

### Key API surface (mention this aloud)

```
scheduler.book(start, end, capacity, organizer) -> BookingResult
scheduler.cancel(booking_id, actor)             -> BookingResult
scheduler.get_audit_log(room_id, since=None)    -> list[AuditEvent]
scheduler.shutdown()                            -> stops cleanup thread
```

---

## Step 3: Design Patterns & Tradeoffs (3-4 mins)

> **What to say:** "I'd lean on four core patterns. Each one solves a specific extensibility concern, and I'll call out the cost."

### Pattern 1: Strategy Pattern (Room Allocation)

**Why:** Allocation policy will change — best-fit today, load-balanced or "prefer-VC-equipped-rooms" tomorrow. The scheduler shouldn't care *how* a room is picked.

**How:** `AllocationStrategy` interface with `pick(candidates, request) -> Room | None`. Default `BestFitStrategy`. Strategy is injected into the scheduler.

**Tradeoff:** One extra abstraction. But it makes A/B-testing allocation policies trivial and keeps `MeetingRoomScheduler` closed for modification (Open/Closed).

```
AllocationStrategy (ABC)
    |-- BestFitStrategy
    |-- FirstFitStrategy
    |-- (future) LoadBalancedStrategy
```

### Pattern 2: Observer Pattern (Audit + Future Listeners)

**Why:** Audit logging shouldn't be hardcoded into the booking flow. Tomorrow we'll want metrics emission, Slack notifications, or downstream syncs — all reacting to the same events.

**How:** `EventBus` holds a list of `EventListener`s. The scheduler publishes `BookingCreated` / `BookingCancelled` events. `AuditListener` subscribes and writes to the per-room `AuditLog`.

**Tradeoff:** Slight notification overhead and ordering caveats (sync dispatch keeps it simple). Decouples cross-cutting concerns from the core booking path.

```
EventBus
    |-- AuditListener        (writes audit events)
    |-- (future) MetricsListener, NotificationListener
```

### Pattern 3: Repository Pattern (Per-Room Calendar + AuditLog)

**Why:** Today the calendars and audit logs live in memory. Tomorrow they live in Postgres or DynamoDB. The scheduler's logic should not change.

**How:** `RoomCalendar` and `AuditLog` are abstractions; their in-memory implementations are swappable behind the same interface.

**Tradeoff:** A bit of ceremony for an in-memory v1, but it's the seam that lets us scale storage without rewriting allocation.

### Pattern 4: Scheduled Task / Background Worker (Cleanup)

**Why:** Audit retention is a recurring chore. We don't want to bolt it onto every read or write.

**How:** `CleanupScheduler` runs a daemon thread (or in prod, a cron / k8s CronJob). It reads `CleanupPolicy.retention_days` and calls `AuditService.purge_older_than(now - retention)`.

**Tradeoff:** Background thread complicates shutdown semantics — must be a daemon and have a clean stop. Alternative is lazy purge on read, which is simpler but causes unbounded growth between reads.

> **Interview tip:** Mention you'd use a real scheduler (APScheduler / Celery beat / cron) in production; the daemon thread is just to keep the demo self-contained.

### Pattern 5 (mention, don't code unless asked): Command Pattern

**Why:** Each booking/cancel could be a `Command` object — gives undo/replay and clean audit payload.

**Tradeoff:** Overkill for v1 but a natural fit when we add transactional rollback or event sourcing.

### Concurrency note (say this aloud)

> "I'd guard each `RoomCalendar` with its own lock — per-room locks scale better than a global scheduler lock since unrelated rooms book independently. The allocation phase reads multiple rooms, so I'd take a brief read lock per candidate during the overlap check, then upgrade to a write lock on the chosen room before insert. For an interview I'll use a single `threading.RLock` per room and acquire it during `try_book` to keep the code clean."

### Data structure for overlap detection (say this aloud)

> "For each room I keep bookings sorted by `start_time`. Insertion uses binary search to find the position, then I check the neighbors for overlap. That's `O(log n)` lookup, `O(n)` insertion in a Python list. For very high-traffic rooms I'd swap to an interval tree or a balanced BST for `O(log n)` insert."

---

## Step 4: Implementation - Full Reference Design

> Study this section for depth. The 20-minute interview-ready version is at the bottom.

### 4.1 TimeInterval (Value Object)

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")

    def overlaps(self, other: "TimeInterval") -> bool:
        # Half-open [start, end): touching boundaries do NOT overlap
        return self.start < other.end and other.start < self.end
```

### 4.2 Booking & BookingResult

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import uuid

class BookingStatus(Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"

@dataclass
class Booking:
    id: str
    room_id: str
    interval: TimeInterval
    requested_capacity: int
    organizer: str
    status: BookingStatus = BookingStatus.CONFIRMED

    @staticmethod
    def new(room_id, interval, capacity, organizer) -> "Booking":
        return Booking(str(uuid.uuid4()), room_id, interval, capacity, organizer)

@dataclass
class BookingResult:
    success: bool
    booking: Optional[Booking] = None
    reason: Optional[str] = None

    @staticmethod
    def ok(b: Booking) -> "BookingResult":
        return BookingResult(True, booking=b)

    @staticmethod
    def fail(reason: str) -> "BookingResult":
        return BookingResult(False, reason=reason)
```

### 4.3 RoomCalendar (per-room booking store)

```python
import threading
from bisect import insort
from typing import List

class RoomCalendar:
    def __init__(self):
        self._bookings: List[Booking] = []  # sorted by interval.start
        self._lock = threading.RLock()

    def has_overlap(self, interval: TimeInterval) -> bool:
        with self._lock:
            for b in self._bookings:  # for interview: linear; mention interval tree
                if b.status == BookingStatus.CONFIRMED and b.interval.overlaps(interval):
                    return True
            return False

    def add(self, booking: Booking) -> None:
        with self._lock:
            insort(self._bookings, booking, key=lambda b: b.interval.start)

    def cancel(self, booking_id: str) -> Optional[Booking]:
        with self._lock:
            for b in self._bookings:
                if b.id == booking_id and b.status == BookingStatus.CONFIRMED:
                    b.status = BookingStatus.CANCELLED
                    return b
            return None
```

### 4.4 Room

```python
@dataclass
class Room:
    id: str
    capacity: int
    calendar: RoomCalendar

    @staticmethod
    def new(id: str, capacity: int) -> "Room":
        return Room(id, capacity, RoomCalendar())
```

### 4.5 Strategy Pattern - Allocation

```python
from abc import ABC, abstractmethod

@dataclass
class BookingRequest:
    interval: TimeInterval
    capacity: int
    organizer: str

class AllocationStrategy(ABC):
    @abstractmethod
    def pick(self, rooms: List[Room], req: BookingRequest) -> Optional[Room]:
        pass

class BestFitStrategy(AllocationStrategy):
    """Smallest room that satisfies capacity AND has no overlap. Minimizes waste."""
    def pick(self, rooms, req):
        candidates = sorted(
            (r for r in rooms if r.capacity >= req.capacity),
            key=lambda r: r.capacity,
        )
        for room in candidates:
            if not room.calendar.has_overlap(req.interval):
                return room
        return None

class FirstFitStrategy(AllocationStrategy):
    def pick(self, rooms, req):
        for room in rooms:
            if room.capacity >= req.capacity and not room.calendar.has_overlap(req.interval):
                return room
        return None
```

### 4.6 Audit (Observer Pattern + per-room log)

```python
from datetime import timedelta

class AuditAction(Enum):
    BOOKING_CREATED = "BOOKING_CREATED"
    BOOKING_CANCELLED = "BOOKING_CANCELLED"
    BOOKING_REJECTED = "BOOKING_REJECTED"

@dataclass(frozen=True)
class AuditEvent:
    action: AuditAction
    room_id: Optional[str]
    booking_id: Optional[str]
    actor: str
    timestamp: datetime
    details: str = ""

class AuditLog:
    """Per-room append-only log with time-window purge."""
    def __init__(self):
        self._events: List[AuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def query(self, since: Optional[datetime] = None) -> List[AuditEvent]:
        with self._lock:
            if since is None:
                return list(self._events)
            return [e for e in self._events if e.timestamp >= since]

    def purge_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            before = len(self._events)
            self._events = [e for e in self._events if e.timestamp >= cutoff]
            return before - len(self._events)


class EventListener(ABC):
    @abstractmethod
    def on_event(self, event: AuditEvent) -> None: ...

class AuditListener(EventListener):
    def __init__(self):
        self._logs: dict[str, AuditLog] = {}
        self._lock = threading.RLock()

    def on_event(self, event: AuditEvent) -> None:
        if event.room_id is None:
            return  # only persist room-scoped events
        with self._lock:
            self._logs.setdefault(event.room_id, AuditLog()).append(event)

    def get_log(self, room_id: str) -> AuditLog:
        with self._lock:
            return self._logs.setdefault(room_id, AuditLog())

    def purge(self, cutoff: datetime) -> int:
        total = 0
        with self._lock:
            for log in self._logs.values():
                total += log.purge_older_than(cutoff)
        return total


class EventBus:
    def __init__(self):
        self._listeners: List[EventListener] = []

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def publish(self, event: AuditEvent) -> None:
        for l in self._listeners:
            l.on_event(event)  # synchronous; swap to a queue for async
```

### 4.7 Cleanup Scheduler (background worker)

```python
import threading, time

@dataclass
class CleanupPolicy:
    retention_days: int
    sweep_interval_seconds: int = 3600  # hourly

class CleanupScheduler:
    def __init__(self, audit_listener: AuditListener, policy: CleanupPolicy,
                 clock=datetime.utcnow):
        self._audit = audit_listener
        self._policy = policy
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._policy.sweep_interval_seconds):
            cutoff = self._clock() - timedelta(days=self._policy.retention_days)
            self._audit.purge(cutoff)
```

### 4.8 The Scheduler (Facade / Controller)

```python
class MeetingRoomScheduler:
    def __init__(self,
                 rooms: List[Room],
                 allocator: AllocationStrategy,
                 event_bus: EventBus,
                 clock=datetime.utcnow):
        self._rooms = rooms
        self._allocator = allocator
        self._bus = event_bus
        self._clock = clock
        self._index: dict[str, Booking] = {}  # booking_id -> Booking
        self._index_lock = threading.RLock()

    def book(self, start, end, capacity, organizer) -> BookingResult:
        try:
            interval = TimeInterval(start, end)
        except ValueError as e:
            return BookingResult.fail(str(e))

        req = BookingRequest(interval, capacity, organizer)
        room = self._allocator.pick(self._rooms, req)
        if room is None:
            self._bus.publish(AuditEvent(
                AuditAction.BOOKING_REJECTED, None, None, organizer,
                self._clock(), f"no room for cap={capacity} {interval}"))
            return BookingResult.fail("No room available")

        booking = Booking.new(room.id, interval, capacity, organizer)

        # Re-check + insert under the room's lock to avoid TOCTOU race
        with room.calendar._lock:
            if room.calendar.has_overlap(interval):
                return self.book(start, end, capacity, organizer)  # retry
            room.calendar.add(booking)

        with self._index_lock:
            self._index[booking.id] = booking

        self._bus.publish(AuditEvent(
            AuditAction.BOOKING_CREATED, room.id, booking.id, organizer,
            self._clock(), f"cap={capacity} {interval}"))
        return BookingResult.ok(booking)

    def cancel(self, booking_id: str, actor: str) -> BookingResult:
        with self._index_lock:
            booking = self._index.get(booking_id)
        if booking is None or booking.status != BookingStatus.CONFIRMED:
            return BookingResult.fail("Booking not found or already cancelled")
        room = next(r for r in self._rooms if r.id == booking.room_id)
        room.calendar.cancel(booking_id)
        self._bus.publish(AuditEvent(
            AuditAction.BOOKING_CANCELLED, room.id, booking_id, actor,
            self._clock(), ""))
        return BookingResult.ok(booking)
```

---

## Step 5: Extensibility Points (mention these in interview)

> **What to say:** "Here's how this design scales without rewrites."

### 5.1 New Allocation Policies
Add a class implementing `AllocationStrategy`. No changes to scheduler.

### 5.2 Persistent Storage
`RoomCalendar` and `AuditLog` become interfaces; ship `PostgresRoomCalendar` / `DynamoAuditLog`. Allocator code is untouched.

### 5.3 More Listeners
`MetricsListener` (Prometheus counters), `NotificationListener` (Slack/email), `AnalyticsListener` (Kafka emit) — all just `EventBus.subscribe(...)`.

### 5.4 Distributed Scheduling
Replace per-room `RLock` with a distributed lock (Redis `SETNX` / Zookeeper / Postgres advisory lock) keyed by `room_id`.

### 5.5 Recurring Meetings
Wrap `BookingRequest` into a `RecurringBookingRequest` that expands into N `TimeInterval`s and books transactionally (all-or-nothing).

### 5.6 Suggest Next Available Slot
Add `scheduler.suggest(duration, capacity)` — scans all rooms' calendars for the earliest gap. Strategy Pattern again.

### 5.7 Capacity Tiers / Equipment
Extend `Room` with `features: set[str]` (whiteboard, VC, dial-in). Allocator filters by features as well as capacity. Backwards compatible.

### 5.8 Real Production Cleanup
Replace daemon thread with APScheduler / k8s CronJob hitting an admin endpoint. The `CleanupPolicy` + `purge_older_than` API stays identical.

---

## Step 6: Interview Flow (Cheat Sheet)

| Time | What To Do | Key Points to Hit |
|------|-----------|-------------------|
| 0-3 min | Restate problem, ask 4-5 clarifying Qs | Capacity heterogeneity, overlap semantics, concurrency, cleanup model |
| 3-6 min | Entities + relationships diagram | Room, RoomCalendar, Booking, Allocator, AuditLog, EventBus, CleanupScheduler |
| 6-10 min | Design patterns + tradeoffs | Strategy (allocation), Observer (audit), Repository (storage), background worker (cleanup), concurrency story |
| 10-12 min | API surface + data structures | `book / cancel / get_audit_log`; sorted list now, interval tree later |
| 12-30 min | Code the interview-ready version | See "FINAL CODE" below |
| 30-35 min | Extensibility + follow-ups | Persistence, distributed locks, recurring meetings, capacity-tier follow-up |

---

## FINAL CODE: Write This in 15-20 Minutes

> Streamlined, interview-ready version. Has all four patterns + concurrency + cleanup. ~180 lines.

```python
import threading
import time
import uuid
from abc import ABC, abstractmethod
from bisect import insort
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict


# ---- Value Objects ----

@dataclass(frozen=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")

    def overlaps(self, other: "TimeInterval") -> bool:
        return self.start < other.end and other.start < self.end


# ---- Booking ----

class BookingStatus(Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"

@dataclass
class Booking:
    id: str
    room_id: str
    interval: TimeInterval
    capacity: int
    organizer: str
    status: BookingStatus = BookingStatus.CONFIRMED

@dataclass
class BookingResult:
    success: bool
    booking: Optional[Booking] = None
    reason: Optional[str] = None


# ---- Room + Calendar ----

class RoomCalendar:
    def __init__(self):
        self._bookings: List[Booking] = []
        self.lock = threading.RLock()

    def has_overlap(self, interval: TimeInterval) -> bool:
        return any(b.status == BookingStatus.CONFIRMED and b.interval.overlaps(interval)
                   for b in self._bookings)

    def add(self, booking: Booking) -> None:
        insort(self._bookings, booking, key=lambda b: b.interval.start)

    def cancel(self, booking_id: str) -> Optional[Booking]:
        for b in self._bookings:
            if b.id == booking_id and b.status == BookingStatus.CONFIRMED:
                b.status = BookingStatus.CANCELLED
                return b
        return None

@dataclass
class Room:
    id: str
    capacity: int
    calendar: RoomCalendar = field(default_factory=RoomCalendar)


# ---- Strategy Pattern: Allocation ----

@dataclass
class BookingRequest:
    interval: TimeInterval
    capacity: int
    organizer: str

class AllocationStrategy(ABC):
    @abstractmethod
    def pick(self, rooms: List[Room], req: BookingRequest) -> Optional[Room]: ...

class BestFitStrategy(AllocationStrategy):
    def pick(self, rooms, req):
        for room in sorted((r for r in rooms if r.capacity >= req.capacity),
                           key=lambda r: r.capacity):
            with room.calendar.lock:
                if not room.calendar.has_overlap(req.interval):
                    return room
        return None


# ---- Observer Pattern: Events + Audit ----

class AuditAction(Enum):
    CREATED = "BOOKING_CREATED"
    CANCELLED = "BOOKING_CANCELLED"
    REJECTED = "BOOKING_REJECTED"

@dataclass(frozen=True)
class AuditEvent:
    action: AuditAction
    room_id: Optional[str]
    booking_id: Optional[str]
    actor: str
    timestamp: datetime
    details: str = ""

class EventListener(ABC):
    @abstractmethod
    def on_event(self, event: AuditEvent) -> None: ...

class AuditLog:
    def __init__(self):
        self._events: List[AuditEvent] = []
        self._lock = threading.RLock()

    def append(self, e: AuditEvent):
        with self._lock:
            self._events.append(e)

    def query(self, since: Optional[datetime] = None) -> List[AuditEvent]:
        with self._lock:
            return [e for e in self._events if since is None or e.timestamp >= since]

    def purge_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            before = len(self._events)
            self._events = [e for e in self._events if e.timestamp >= cutoff]
            return before - len(self._events)

class AuditListener(EventListener):
    def __init__(self):
        self._logs: Dict[str, AuditLog] = {}
        self._lock = threading.RLock()

    def on_event(self, e: AuditEvent) -> None:
        if e.room_id is None:
            return
        with self._lock:
            self._logs.setdefault(e.room_id, AuditLog()).append(e)

    def log_for(self, room_id: str) -> AuditLog:
        with self._lock:
            return self._logs.setdefault(room_id, AuditLog())

    def purge(self, cutoff: datetime) -> int:
        with self._lock:
            return sum(log.purge_older_than(cutoff) for log in self._logs.values())

class EventBus:
    def __init__(self):
        self._listeners: List[EventListener] = []
    def subscribe(self, l: EventListener): self._listeners.append(l)
    def publish(self, e: AuditEvent):
        for l in self._listeners:
            l.on_event(e)


# ---- Cleanup Scheduler ----

@dataclass
class CleanupPolicy:
    retention_days: int
    sweep_interval_seconds: int = 3600

class CleanupScheduler:
    def __init__(self, audit: AuditListener, policy: CleanupPolicy, clock=datetime.utcnow):
        self._audit, self._policy, self._clock = audit, policy, clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.wait(self._policy.sweep_interval_seconds):
            self._audit.purge(self._clock() - timedelta(days=self._policy.retention_days))


# ---- Scheduler Facade ----

class MeetingRoomScheduler:
    def __init__(self, rooms, allocator, bus: EventBus, clock=datetime.utcnow):
        self._rooms, self._allocator, self._bus, self._clock = rooms, allocator, bus, clock
        self._index: Dict[str, Booking] = {}
        self._index_lock = threading.RLock()

    def book(self, start, end, capacity, organizer) -> BookingResult:
        try:
            interval = TimeInterval(start, end)
        except ValueError as e:
            return BookingResult(False, reason=str(e))

        req = BookingRequest(interval, capacity, organizer)
        room = self._allocator.pick(self._rooms, req)
        if room is None:
            self._bus.publish(AuditEvent(AuditAction.REJECTED, None, None, organizer,
                                         self._clock(), f"cap={capacity}"))
            return BookingResult(False, reason="No room available")

        booking = Booking(str(uuid.uuid4()), room.id, interval, capacity, organizer)
        with room.calendar.lock:
            if room.calendar.has_overlap(interval):
                return self.book(start, end, capacity, organizer)  # race: retry
            room.calendar.add(booking)

        with self._index_lock:
            self._index[booking.id] = booking

        self._bus.publish(AuditEvent(AuditAction.CREATED, room.id, booking.id,
                                     organizer, self._clock(),
                                     f"cap={capacity} {interval}"))
        return BookingResult(True, booking=booking)

    def cancel(self, booking_id: str, actor: str) -> BookingResult:
        with self._index_lock:
            b = self._index.get(booking_id)
        if not b or b.status != BookingStatus.CONFIRMED:
            return BookingResult(False, reason="Not found or already cancelled")
        room = next(r for r in self._rooms if r.id == b.room_id)
        with room.calendar.lock:
            room.calendar.cancel(booking_id)
        self._bus.publish(AuditEvent(AuditAction.CANCELLED, room.id, booking_id,
                                     actor, self._clock()))
        return BookingResult(True, booking=b)


# ---- Main / Demo ----

if __name__ == "__main__":
    rooms = [Room("R-small", 4), Room("R-medium", 10), Room("R-large", 50)]
    bus = EventBus()
    audit = AuditListener()
    bus.subscribe(audit)

    cleanup = CleanupScheduler(audit, CleanupPolicy(retention_days=30,
                                                    sweep_interval_seconds=60))
    cleanup.start()

    scheduler = MeetingRoomScheduler(rooms, BestFitStrategy(), bus)

    now = datetime.utcnow()
    r1 = scheduler.book(now, now + timedelta(hours=1), capacity=3, organizer="alice")
    r2 = scheduler.book(now, now + timedelta(hours=1), capacity=3, organizer="bob")
    r3 = scheduler.book(now, now + timedelta(hours=1), capacity=8, organizer="carol")
    print(r1.booking.room_id, r2.booking.room_id, r3.booking.room_id)
    # Expect: R-small, R-medium, R-large (best-fit, no overlap)

    print(audit.log_for("R-small").query())
    cleanup.stop()
```

### Coverage Map (for the interviewer)

| Requirement | Where |
|-------------|-------|
| N rooms, heterogeneous capacity | `Room(capacity=...)` list |
| Stream of requests | `scheduler.book(...)` |
| Overlap prevention | `RoomCalendar.has_overlap` + per-room `RLock` |
| Capacity constraint (follow-up) | `BestFitStrategy.pick` filters `r.capacity >= req.capacity` |
| Per-room audit log | `AuditListener._logs[room_id]` |
| Auto cleanup after X days | `CleanupScheduler` + `CleanupPolicy(retention_days=X)` |
| Strategy Pattern | `AllocationStrategy` ABC |
| Observer Pattern | `EventBus` + `EventListener` |
| Concurrency safety | per-room `RLock` + index lock |
| Cancellation | `scheduler.cancel(...)` + audit event |
| Extensibility (storage / policies / listeners) | All swappable behind interfaces |

### Writing Order (during the 15-20 min coding window)

1. **TimeInterval + Booking + BookingResult** (~2 min) — value objects
2. **RoomCalendar + Room** (~3 min) — sorted list, lock, overlap check
3. **AllocationStrategy + BestFitStrategy** (~2 min) — Strategy Pattern
4. **AuditAction + AuditEvent + AuditLog + AuditListener + EventBus** (~4 min) — Observer Pattern
5. **CleanupPolicy + CleanupScheduler** (~3 min) — background worker
6. **MeetingRoomScheduler** facade (~3 min) — book/cancel orchestration
7. **Main demo** (~1 min) — wire everything

**Total: ~18 minutes**

---

## Step 7: Likely Follow-Ups & How to Answer

> Practice these answers — they're the difference between "good design" and "SDE-2 ready."

### Q1: "What if two booking requests for the same room arrive concurrently?"
**A:** "Each `RoomCalendar` has its own `RLock`. The `book` flow does the overlap re-check **inside** the lock, then inserts. If the re-check fails (another thread won the race), I retry the whole allocation — which may now pick a different room. This is per-room locking, so unrelated rooms book in parallel. In a distributed system I'd swap the `RLock` for a Redis `SETNX` keyed by `room_id` with a short TTL."

### Q2: "Linear scan for overlap is O(n) per booking. How would you scale?"
**A:** "Three options: (a) keep the sorted list and binary-search the predecessor — overlap can only happen with the immediate left/right neighbors, giving `O(log n)`; (b) interval tree per room for `O(log n)` insert and overlap query; (c) bucketize time into fixed slots (e.g., 15-min) and store an occupancy bitmap. I'd start with (a) — minimal code change."

### Q3: "How do you handle audit log growth between sweeps?"
**A:** "Two complementary safeguards: (1) lower `sweep_interval_seconds` for tighter bounds; (2) hard cap per room (`maxlen` deque) to bound memory under traffic spikes. In production the log itself goes to a TTL-based store like DynamoDB or S3 with lifecycle rules — the in-memory `AuditLog` is just an interview-shaped abstraction."

### Q4: "How do you support recurring meetings (weekly standup)?"
**A:** "Wrap `BookingRequest` in a `RecurringBookingRequest(rule, count)` that expands to N `TimeInterval`s. Then book transactionally — either all instances succeed or none. I'd implement that as a two-phase: dry-run check overlaps for all expansions across rooms, then commit. The Strategy interface gets a `pick_for_series(...)` overload, or I prefer the same room across all instances for UX."

### Q5: "What if the user wants the 'next available slot' if their requested time fails?"
**A:** "Add `scheduler.suggest(duration, capacity, after=now)` — for each room satisfying capacity, walk the sorted bookings to find the earliest gap >= duration. Take the global minimum. That's a separate `SuggestionService` to avoid bloating the scheduler."

### Q6: "Why Observer for audit instead of just calling `audit.log(...)` directly?"
**A:** "Two reasons. First, audit is one of *many* cross-cutting concerns — metrics, notifications, downstream sync are all coming. Hardcoding any of them couples booking to ops concerns. Second, listeners can be added/removed dynamically — easy to disable analytics in tests by not subscribing the listener. The cost is one indirection, which is cheap."

### Q7: "Cleanup runs on a daemon thread — what happens if it crashes?"
**A:** "Daemon thread silently dies, retention is violated. In production this is a cron job or k8s CronJob with retry + alerting. The `CleanupPolicy` + `purge_older_than` API stays identical — the runner is what changes. For the demo I'd at least wrap `_run` in a try/except + log + restart loop."

### Q8: "How would you persist this?"
**A:** "Two repositories: `RoomCalendarRepository` (per-room booking storage — Postgres with a `room_id, start, end` btree index, or DynamoDB partition by `room_id` and sort by `start`) and `AuditEventRepository` (append-heavy — DynamoDB or a Kafka-backed event log with lifecycle TTL). The scheduler depends on the abstractions, so swapping is a constructor change."

### Q9: "Two users see the same 'available' room and both click book at the exact same millisecond on different web servers. Who wins?"
**A:** "Single-process: per-room `RLock` serializes them. Multi-process / multi-server: distributed lock keyed by `room_id` (Redis `SET NX EX`), or DB-level — `INSERT ... WHERE NOT EXISTS (overlap)` inside a transaction with `SERIALIZABLE` isolation, or a Postgres exclusion constraint on `(room_id, tsrange)` with `&&` operator, which is the cleanest because the DB enforces non-overlap as an invariant."

### Q10: "Capacity follow-up — what if a meeting's actual attendee count exceeds the booked capacity?"
**A:** "Enforce at booking: `BestFitStrategy` already filters by `room.capacity >= req.capacity`. Enforce at check-in: a `CheckInService` records actual headcount and emits a `CAPACITY_EXCEEDED` audit event if `actual > room.capacity`. That feeds an analytics listener so admins can right-size rooms. The Observer Pattern carries this for free."

---

## Final Tips for the Interview

- **Talk while you code.** Narrate which pattern you're applying and why. The interviewer is grading your reasoning, not your typing speed.
- **Concurrency is non-optional at SDE-2.** Mention locking even if you don't fully implement it — being aware that `book` is a TOCTOU hot spot scores points.
- **Don't over-engineer in code, but mention it verbally.** Code Strategy + Observer + Cleanup. *Mention* Repository, Command, distributed locks, and persistence as extensions.
- **Have a coverage table ready** (the one above) — when the interviewer asks "did you handle X?", point at the line.
- **Time-box yourself.** If you're at 15 mins of coding and the cleanup scheduler isn't done, stub it with `# CleanupScheduler: same shape as AuditListener.purge on a timer` and move on. A working end-to-end stub beats a half-finished masterpiece.
