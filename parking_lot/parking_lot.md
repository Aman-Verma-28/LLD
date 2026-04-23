# Low-Level Design: Multi-Floor Parking Lot — Uber SDE-2 Interview Guide

> **Total time budget:** 30-35 mins → Architecture (10-12 min) + Coding (15-20 min) + Follow-ups (5 min)

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Let me restate the problem to confirm my understanding, and then ask a few clarifying questions before jumping into the design."

### Your Understanding (State This)

- Multi-floor parking lot where vehicles (2-wheelers, 4-wheelers) can park
- Each floor has parking spots arranged in rows and columns
- Automated ticketing: ticket issued at entry, payment + validation at exit
- Designated disabled parking spots (reserved, cannot be used by regular cars)
- Security cameras for surveillance on each floor
- Payment kiosks supporting multiple payment methods
- Allocation strategy should be pluggable (Strategy pattern — Uber explicitly asked for this)

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | What vehicle types do we support? | 2-wheelers and 4-wheelers for now, extensible to EV/trucks |
| 2 | Are spot sizes tied to vehicle type? | Yes — compact (2W), regular (4W), disabled (only disabled-permit), large (future trucks) |
| 3 | How is pricing computed? | Hourly rate per vehicle type. Flat for now; extensible for dynamic pricing |
| 4 | Single entry/exit gate or multiple? | Design for multiple (concurrency matters at scale) |
| 5 | What allocation policy — nearest to entrance, floor-wise fill, or random? | Pluggable via Strategy pattern; start with nearest-first |
| 6 | Can one vehicle occupy multiple spots? | No for this design; note it as an extension |
| 7 | Do we persist state or is in-memory fine? | In-memory for the interview; mention DB layer as an extension |
| 8 | What payment methods? | Cash, card, UPI — via Strategy so more can be added |
| 9 | What happens on lost ticket? | Out of scope for MVP; discuss as follow-up |

### Confirmed Requirements

1. Multi-floor lot, each floor has multiple spots (rows × cols)
2. Support 2-wheelers and 4-wheelers, extensible to more
3. Spot types: Compact, Regular, Disabled (extensible: Large, EV)
4. Automated ticketing at entry gate
5. Pluggable allocation strategy (NearestFirst, FloorWise, etc.)
6. Payment kiosk with pluggable payment methods (Cash, Card, UPI)
7. Security cameras observing events (entry, exit, alerts)
8. Multiple entry/exit gates (concurrency-aware)

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Let me identify the key entities, their responsibilities, and how they relate."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **VehicleType (Enum)** | TWO_WHEELER, FOUR_WHEELER |
| **SpotType (Enum)** | COMPACT, REGULAR, DISABLED, LARGE |
| **TicketStatus (Enum)** | ACTIVE, PAID, EXITED (State pattern lite) |
| **Vehicle** | Holds license plate, type, disabled flag |
| **ParkingSpot** | Knows its id, type, floor, occupancy, current vehicle |
| **ParkingFloor** | Collection of spots; find/free/occupy on that floor |
| **Ticket** | id, vehicle, spot, entry_time, exit_time, amount, status |
| **ParkingAllocationStrategy (Interface)** | Decides which spot to give (Strategy pattern) |
| **PaymentStrategy (Interface)** | Processes payment (Strategy pattern) |
| **PaymentKiosk** | Accepts payment via a chosen PaymentStrategy, marks ticket PAID |
| **EntryGate** | Issues ticket, asks lot for a spot |
| **ExitGate** | Validates ticket is PAID, frees the spot |
| **SecurityCamera** | Observer of parking events (Observer pattern) |
| **ParkingLot** | Singleton orchestrator — floors, gates, kiosks, cameras, strategy |

### Relationships

```
ParkingLot (Singleton — orchestrator)
    |--- has-many ---> ParkingFloor
    |--- has-many ---> EntryGate, ExitGate
    |--- has-many ---> PaymentKiosk
    |--- has-many ---> SecurityCamera (observers)
    |--- has-a ------> ParkingAllocationStrategy (pluggable)
    |--- has-many ---> Ticket (active tickets)

ParkingFloor
    |--- has-many ---> ParkingSpot (arranged by rows x cols)

ParkingSpot
    |--- has-a ------> Vehicle  (when occupied, else None)
    |--- has-a ------> SpotType

Ticket
    |--- references -> Vehicle
    |--- references -> ParkingSpot
    |--- has-a ------> TicketStatus

ParkingAllocationStrategy (Interface)
    |-- NearestFirstStrategy     (scan floor 0 onwards, return first fit)
    |-- FloorWiseStrategy        (fill one floor before next)
    |-- (future: RandomStrategy, ReservedStrategy)

PaymentStrategy (Interface)
    |-- CashPayment
    |-- CardPayment
    |-- UPIPayment

SecurityCamera implements ParkingEventListener (Observer)
```

---

## Step 3: Design Patterns & Tradeoffs (2-3 mins)

> **What to say:** "I'll use a few classical patterns here. The big one the problem explicitly calls for is Strategy for allocation."

### Pattern 1: Strategy Pattern (Allocation + Payment) — **Required**

**Why:** Uber's spec explicitly mentions Strategy for allocation. We also use it for payment because new payment methods are added frequently in real systems.

**How:**
- `ParkingAllocationStrategy` interface with `find_spot(floors, vehicle) -> ParkingSpot | None`
- Concrete: `NearestFirstStrategy`, `FloorWiseStrategy`
- `PaymentStrategy` interface with `pay(amount) -> bool`
- Concrete: `CashPayment`, `CardPayment`, `UPIPayment`

**Tradeoff:** Extra interfaces, but the `ParkingLot` doesn't know (or care) how allocation works. Swapping strategies at runtime is trivial. New strategies = new class, zero changes to lot (Open/Closed).

### Pattern 2: Singleton Pattern (ParkingLot)

**Why:** A single physical lot should be one instance in the process — it owns global state (floors, active tickets).

**How:** Classic `__new__` override or module-level instance.

**Tradeoff:** Singletons make unit testing harder (global state). In production I'd inject the lot via DI; Singleton here is fine for the interview scope.

### Pattern 3: Factory Pattern (Vehicle / Spot creation) — Extensibility

**Why:** Creation logic for vehicles and spots (type-specific subclasses / defaults) is centralized so the gate/lot doesn't hard-code constructors.

**How:** `VehicleFactory.create(type, plate)` returns the right `Vehicle` instance.

**Tradeoff:** Minor indirection. Pays off once we add EVs (need charger info) or trucks (need size).

### Pattern 4: Observer Pattern (Security Cameras / Logging)

**Why:** When a vehicle enters, parks, or leaves, multiple subsystems might care — cameras, analytics, alerts. Hardcoding them into the gate couples everything.

**How:** `ParkingLot` keeps a list of `ParkingEventListener`. On each event (`VEHICLE_ENTERED`, `VEHICLE_PARKED`, `VEHICLE_EXITED`, `UNAUTHORIZED_ATTEMPT`), it notifies all listeners. `SecurityCamera` implements the listener interface.

**Tradeoff:** Slight dispatch overhead, but the gate stays unaware of cameras/loggers. Adding new listeners = zero changes to gates.

> **Interview tip:** Mention Observer early as an extensibility point, but only implement the listener interface + one concrete camera. Don't over-engineer in the 20-min code.

### Pattern 5: State Pattern (Ticket Lifecycle) — Lightweight

**Why:** A ticket transitions: `ACTIVE → PAID → EXITED`. Wrong transitions (exit without paying) should be rejected.

**How:** For the interview-sized version, an enum + guard checks in transition methods is enough. Mention that for a richer system, each state could be its own class.

**Tradeoff:** Enum is simpler; state classes scale better as states/rules grow.

---

## Step 4: Implementation — Full Design (Reference)

> This section is the complete, pattern-rich implementation for study. The streamlined 20-min version is at the bottom.

### 4.1 Enums

```python
from enum import Enum

class VehicleType(Enum):
    TWO_WHEELER = "TWO_WHEELER"
    FOUR_WHEELER = "FOUR_WHEELER"

class SpotType(Enum):
    COMPACT = "COMPACT"       # for 2-wheelers
    REGULAR = "REGULAR"       # for 4-wheelers
    DISABLED = "DISABLED"     # only disabled-permit 4-wheelers
    LARGE = "LARGE"           # future: trucks

class TicketStatus(Enum):
    ACTIVE = "ACTIVE"
    PAID = "PAID"
    EXITED = "EXITED"

class PaymentMethod(Enum):
    CASH = "CASH"
    CARD = "CARD"
    UPI = "UPI"
```

### 4.2 Vehicle

```python
from abc import ABC

class Vehicle(ABC):
    def __init__(self, license_plate: str, vehicle_type: VehicleType, has_disabled_permit: bool = False):
        self.license_plate = license_plate
        self.vehicle_type = vehicle_type
        self.has_disabled_permit = has_disabled_permit

class TwoWheeler(Vehicle):
    def __init__(self, license_plate: str):
        super().__init__(license_plate, VehicleType.TWO_WHEELER)

class FourWheeler(Vehicle):
    def __init__(self, license_plate: str, has_disabled_permit: bool = False):
        super().__init__(license_plate, VehicleType.FOUR_WHEELER, has_disabled_permit)
```

### 4.3 ParkingSpot

```python
class ParkingSpot:
    def __init__(self, spot_id: str, spot_type: SpotType, floor_number: int):
        self.spot_id = spot_id
        self.spot_type = spot_type
        self.floor_number = floor_number
        self.vehicle: Vehicle | None = None

    def is_free(self) -> bool:
        return self.vehicle is None

    def can_fit(self, vehicle: Vehicle) -> bool:
        if not self.is_free():
            return False
        if self.spot_type == SpotType.DISABLED:
            return vehicle.has_disabled_permit and vehicle.vehicle_type == VehicleType.FOUR_WHEELER
        if self.spot_type == SpotType.COMPACT:
            return vehicle.vehicle_type == VehicleType.TWO_WHEELER
        if self.spot_type == SpotType.REGULAR:
            return vehicle.vehicle_type == VehicleType.FOUR_WHEELER
        if self.spot_type == SpotType.LARGE:
            return True  # future
        return False

    def park(self, vehicle: Vehicle):
        if not self.can_fit(vehicle):
            raise ValueError(f"Vehicle {vehicle.license_plate} cannot fit in spot {self.spot_id}")
        self.vehicle = vehicle

    def vacate(self) -> Vehicle | None:
        v, self.vehicle = self.vehicle, None
        return v
```

### 4.4 ParkingFloor

```python
class ParkingFloor:
    def __init__(self, floor_number: int, spots: list[ParkingSpot]):
        self.floor_number = floor_number
        self.spots = spots

    def find_free_spot(self, vehicle: Vehicle) -> ParkingSpot | None:
        for spot in self.spots:
            if spot.can_fit(vehicle):
                return spot
        return None

    def available_count(self) -> int:
        return sum(1 for s in self.spots if s.is_free())
```

### 4.5 Strategy Pattern — Allocation

```python
from abc import ABC, abstractmethod

class ParkingAllocationStrategy(ABC):
    @abstractmethod
    def find_spot(self, floors: list[ParkingFloor], vehicle: Vehicle) -> ParkingSpot | None:
        pass


class NearestFirstStrategy(ParkingAllocationStrategy):
    """Scan floors from 0 upwards, return the first spot that fits."""
    def find_spot(self, floors, vehicle):
        for floor in floors:
            spot = floor.find_free_spot(vehicle)
            if spot:
                return spot
        return None


class FloorWiseStrategy(ParkingAllocationStrategy):
    """Fill up a floor completely before moving to the next."""
    def find_spot(self, floors, vehicle):
        for floor in floors:
            if floor.available_count() > 0:
                spot = floor.find_free_spot(vehicle)
                if spot:
                    return spot
        return None
```

### 4.6 Ticket

```python
import uuid
from datetime import datetime

class Ticket:
    HOURLY_RATE = {
        VehicleType.TWO_WHEELER: 10.0,
        VehicleType.FOUR_WHEELER: 30.0,
    }

    def __init__(self, vehicle: Vehicle, spot: ParkingSpot):
        self.ticket_id = str(uuid.uuid4())[:8]
        self.vehicle = vehicle
        self.spot = spot
        self.entry_time = datetime.now()
        self.exit_time: datetime | None = None
        self.amount: float = 0.0
        self.status = TicketStatus.ACTIVE

    def calculate_amount(self) -> float:
        end = self.exit_time or datetime.now()
        hours = max(1, (end - self.entry_time).total_seconds() / 3600)
        return round(hours * Ticket.HOURLY_RATE[self.vehicle.vehicle_type], 2)

    def mark_paid(self, amount: float):
        if self.status != TicketStatus.ACTIVE:
            raise ValueError(f"Cannot pay for ticket in state {self.status}")
        self.amount = amount
        self.status = TicketStatus.PAID

    def mark_exited(self):
        if self.status != TicketStatus.PAID:
            raise ValueError("Ticket must be PAID before exit")
        self.exit_time = datetime.now()
        self.status = TicketStatus.EXITED
```

### 4.7 Strategy Pattern — Payment

```python
class PaymentStrategy(ABC):
    @abstractmethod
    def pay(self, amount: float) -> bool:
        pass

class CashPayment(PaymentStrategy):
    def pay(self, amount):
        print(f"[Cash] Collected ₹{amount}")
        return True

class CardPayment(PaymentStrategy):
    def __init__(self, card_number: str):
        self.card_number = card_number
    def pay(self, amount):
        print(f"[Card ****{self.card_number[-4:]}] Charged ₹{amount}")
        return True

class UPIPayment(PaymentStrategy):
    def __init__(self, upi_id: str):
        self.upi_id = upi_id
    def pay(self, amount):
        print(f"[UPI {self.upi_id}] Collected ₹{amount}")
        return True
```

### 4.8 Payment Kiosk

```python
class PaymentKiosk:
    def __init__(self, kiosk_id: str):
        self.kiosk_id = kiosk_id

    def process_payment(self, ticket: Ticket, strategy: PaymentStrategy) -> bool:
        amount = ticket.calculate_amount()
        if strategy.pay(amount):
            ticket.mark_paid(amount)
            print(f"[Kiosk {self.kiosk_id}] Ticket {ticket.ticket_id} PAID ₹{amount}")
            return True
        return False
```

### 4.9 Observer Pattern — Events & Security Camera

```python
class ParkingEvent(Enum):
    VEHICLE_ENTERED = "VEHICLE_ENTERED"
    VEHICLE_PARKED = "VEHICLE_PARKED"
    VEHICLE_EXITED = "VEHICLE_EXITED"
    UNAUTHORIZED_ATTEMPT = "UNAUTHORIZED_ATTEMPT"
    LOT_FULL = "LOT_FULL"

class ParkingEventListener(ABC):
    @abstractmethod
    def on_event(self, event: ParkingEvent, payload: dict): pass

class SecurityCamera(ParkingEventListener):
    def __init__(self, camera_id: str, floor_number: int):
        self.camera_id = camera_id
        self.floor_number = floor_number

    def on_event(self, event, payload):
        if payload.get("floor_number") == self.floor_number or event == ParkingEvent.UNAUTHORIZED_ATTEMPT:
            print(f"[CAM {self.camera_id} F{self.floor_number}] {event.value} :: {payload}")
```

### 4.10 Entry & Exit Gates

```python
import threading

class EntryGate:
    def __init__(self, gate_id: str, lot: "ParkingLot"):
        self.gate_id = gate_id
        self.lot = lot

    def issue_ticket(self, vehicle: Vehicle) -> Ticket | None:
        return self.lot.park_vehicle(vehicle, gate_id=self.gate_id)


class ExitGate:
    def __init__(self, gate_id: str, lot: "ParkingLot"):
        self.gate_id = gate_id
        self.lot = lot

    def process_exit(self, ticket: Ticket) -> bool:
        return self.lot.exit_vehicle(ticket, gate_id=self.gate_id)
```

### 4.11 ParkingLot (Singleton, Orchestrator)

```python
class ParkingLot:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, floors: list[ParkingFloor] = None, strategy: ParkingAllocationStrategy = None):
        if getattr(self, "_initialized", False):
            return
        self.floors = floors or []
        self.strategy = strategy or NearestFirstStrategy()
        self.active_tickets: dict[str, Ticket] = {}
        self.listeners: list[ParkingEventListener] = []
        self._allocation_lock = threading.Lock()
        self._initialized = True

    # Observer registration
    def add_listener(self, listener: ParkingEventListener):
        self.listeners.append(listener)

    def _notify(self, event: ParkingEvent, payload: dict):
        for l in self.listeners:
            l.on_event(event, payload)

    # Strategy swap at runtime
    def set_strategy(self, strategy: ParkingAllocationStrategy):
        self.strategy = strategy

    # Entry
    def park_vehicle(self, vehicle: Vehicle, gate_id: str) -> Ticket | None:
        with self._allocation_lock:   # guard against concurrent double-allocation
            spot = self.strategy.find_spot(self.floors, vehicle)
            if spot is None:
                self._notify(ParkingEvent.LOT_FULL, {"plate": vehicle.license_plate})
                return None
            try:
                spot.park(vehicle)
            except ValueError:
                self._notify(ParkingEvent.UNAUTHORIZED_ATTEMPT,
                             {"plate": vehicle.license_plate, "spot": spot.spot_id})
                return None
            ticket = Ticket(vehicle, spot)
            self.active_tickets[ticket.ticket_id] = ticket

        self._notify(ParkingEvent.VEHICLE_ENTERED,
                     {"plate": vehicle.license_plate, "gate_id": gate_id})
        self._notify(ParkingEvent.VEHICLE_PARKED,
                     {"plate": vehicle.license_plate, "spot": spot.spot_id,
                      "floor_number": spot.floor_number})
        return ticket

    # Exit
    def exit_vehicle(self, ticket: Ticket, gate_id: str) -> bool:
        if ticket.ticket_id not in self.active_tickets:
            return False
        if ticket.status != TicketStatus.PAID:
            self._notify(ParkingEvent.UNAUTHORIZED_ATTEMPT,
                         {"ticket_id": ticket.ticket_id, "reason": "unpaid exit"})
            return False
        ticket.spot.vacate()
        ticket.mark_exited()
        del self.active_tickets[ticket.ticket_id]
        self._notify(ParkingEvent.VEHICLE_EXITED,
                     {"plate": ticket.vehicle.license_plate, "gate_id": gate_id,
                      "floor_number": ticket.spot.floor_number, "amount": ticket.amount})
        return True
```

### 4.12 Main Entry Point

```python
def build_floor(floor_num: int, compact: int, regular: int, disabled: int) -> ParkingFloor:
    spots = []
    idx = 0
    for _ in range(compact):
        idx += 1; spots.append(ParkingSpot(f"F{floor_num}-C{idx}", SpotType.COMPACT, floor_num))
    for _ in range(regular):
        idx += 1; spots.append(ParkingSpot(f"F{floor_num}-R{idx}", SpotType.REGULAR, floor_num))
    for _ in range(disabled):
        idx += 1; spots.append(ParkingSpot(f"F{floor_num}-D{idx}", SpotType.DISABLED, floor_num))
    return ParkingFloor(floor_num, spots)


if __name__ == "__main__":
    floors = [
        build_floor(0, compact=2, regular=3, disabled=1),
        build_floor(1, compact=2, regular=3, disabled=1),
    ]
    lot = ParkingLot(floors=floors, strategy=NearestFirstStrategy())
    lot.add_listener(SecurityCamera("CAM-F0", 0))
    lot.add_listener(SecurityCamera("CAM-F1", 1))

    entry = EntryGate("E1", lot)
    exit_ = ExitGate("X1", lot)
    kiosk = PaymentKiosk("K1")

    car = FourWheeler("KA01AB1234")
    bike = TwoWheeler("KA01CD5678")

    t1 = entry.issue_ticket(car)
    t2 = entry.issue_ticket(bike)

    kiosk.process_payment(t1, CardPayment("4111111111111111"))
    exit_.process_exit(t1)

    kiosk.process_payment(t2, UPIPayment("aman@upi"))
    exit_.process_exit(t2)
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design is extensible in several directions."

### 5.1 New Vehicle Types (EV, Truck)

Add a new subclass of `Vehicle` + new `SpotType.EV_CHARGING` / `SpotType.LARGE`. Update `ParkingSpot.can_fit` matrix. No changes to `ParkingLot`, allocation strategies, or gates.

### 5.2 New Allocation Strategies

Implement `ParkingAllocationStrategy` — e.g., `NearestToElevatorStrategy`, `ReservedSpotStrategy`, `RandomStrategy`. Swap via `lot.set_strategy(...)`. Zero changes to existing code.

### 5.3 Dynamic Pricing

Replace `Ticket.calculate_amount` with a `PricingStrategy` interface → `FlatHourlyPricing`, `PeakHourPricing`, `DayOfWeekPricing`. Same Strategy pattern.

### 5.4 Reservation System

A `Reservation` entity with a spot + time window. `ReservedSpotStrategy` checks reservations before allocating. Reservations can be held via the Observer pattern (notify when someone tries to use a reserved spot).

### 5.5 Multiple Entry/Exit Gates + Concurrency

`ParkingLot._allocation_lock` already serializes allocation. For scale, shard per floor: one lock per `ParkingFloor`, so two gates can allocate on different floors in parallel.

### 5.6 Observer Use Cases Beyond Cameras

- `AnalyticsListener` — tracks occupancy metrics
- `AlertListener` — pages security on unauthorized attempts
- `SMSNotifier` — sends receipt on exit

All plug into `lot.add_listener(...)` with zero core changes.

### 5.7 Persistence

Wrap `ParkingLot`'s state reads/writes with a `ParkingRepository` interface → `InMemoryRepo`, `PostgresRepo`, `RedisRepo`. Not needed in the interview code, but mention.

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-3 min | Restate problem, ask clarifying questions | Show you slow down before coding |
| 3-7 min | Draw entity diagram + relationships | Vehicle, Spot, Floor, Ticket, Lot, Gates, Kiosk, Camera |
| 7-12 min | Walk through design patterns + tradeoffs | **Strategy (primary)**, Singleton, Factory, Observer, State |
| 12-30 min | Code the streamlined version | Enums → Vehicle/Spot → Floor → Strategy → Ticket → Payment → Lot → Main |
| 30-35 min | Extensibility + answer follow-ups | Dynamic pricing, concurrency, EVs, lost tickets |

---

## FINAL CODE: Write This in 15-20 Minutes

> The **interview-ready** streamlined version. Strategy Pattern (allocation + payment), Singleton, Observer hook, Ticket states. All in one file, ~180 lines.

```python
import uuid
import threading
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime


# ---- Enums ----

class VehicleType(Enum):
    TWO_WHEELER = "2W"
    FOUR_WHEELER = "4W"

class SpotType(Enum):
    COMPACT = "COMPACT"
    REGULAR = "REGULAR"
    DISABLED = "DISABLED"

class TicketStatus(Enum):
    ACTIVE = "ACTIVE"
    PAID = "PAID"
    EXITED = "EXITED"


# ---- Vehicle ----

class Vehicle:
    def __init__(self, plate: str, v_type: VehicleType, has_disabled_permit: bool = False):
        self.plate = plate
        self.v_type = v_type
        self.has_disabled_permit = has_disabled_permit


# ---- Parking Spot ----

class ParkingSpot:
    def __init__(self, spot_id: str, spot_type: SpotType, floor_number: int):
        self.spot_id = spot_id
        self.spot_type = spot_type
        self.floor_number = floor_number
        self.vehicle: Vehicle | None = None

    def is_free(self) -> bool:
        return self.vehicle is None

    def can_fit(self, v: Vehicle) -> bool:
        if not self.is_free():
            return False
        if self.spot_type == SpotType.DISABLED:
            return v.has_disabled_permit and v.v_type == VehicleType.FOUR_WHEELER
        if self.spot_type == SpotType.COMPACT:
            return v.v_type == VehicleType.TWO_WHEELER
        if self.spot_type == SpotType.REGULAR:
            return v.v_type == VehicleType.FOUR_WHEELER
        return False

    def park(self, v: Vehicle):
        if not self.can_fit(v):
            raise ValueError("Cannot fit")
        self.vehicle = v

    def vacate(self):
        self.vehicle = None


# ---- Floor ----

class ParkingFloor:
    def __init__(self, floor_number: int, spots: list[ParkingSpot]):
        self.floor_number = floor_number
        self.spots = spots

    def find_free_spot(self, v: Vehicle) -> ParkingSpot | None:
        for s in self.spots:
            if s.can_fit(v):
                return s
        return None


# ---- Strategy: Allocation ----

class ParkingAllocationStrategy(ABC):
    @abstractmethod
    def find_spot(self, floors: list[ParkingFloor], v: Vehicle) -> ParkingSpot | None: ...

class NearestFirstStrategy(ParkingAllocationStrategy):
    def find_spot(self, floors, v):
        for f in floors:
            s = f.find_free_spot(v)
            if s:
                return s
        return None


# ---- Ticket ----

class Ticket:
    RATE = {VehicleType.TWO_WHEELER: 10.0, VehicleType.FOUR_WHEELER: 30.0}

    def __init__(self, vehicle: Vehicle, spot: ParkingSpot):
        self.id = str(uuid.uuid4())[:8]
        self.vehicle = vehicle
        self.spot = spot
        self.entry = datetime.now()
        self.exit: datetime | None = None
        self.amount = 0.0
        self.status = TicketStatus.ACTIVE

    def calculate(self) -> float:
        end = self.exit or datetime.now()
        hours = max(1, (end - self.entry).total_seconds() / 3600)
        return round(hours * Ticket.RATE[self.vehicle.v_type], 2)

    def mark_paid(self, amount: float):
        if self.status != TicketStatus.ACTIVE:
            raise ValueError("Not active")
        self.amount = amount
        self.status = TicketStatus.PAID

    def mark_exited(self):
        if self.status != TicketStatus.PAID:
            raise ValueError("Not paid")
        self.exit = datetime.now()
        self.status = TicketStatus.EXITED


# ---- Strategy: Payment ----

class PaymentStrategy(ABC):
    @abstractmethod
    def pay(self, amount: float) -> bool: ...

class CashPayment(PaymentStrategy):
    def pay(self, amount): print(f"[Cash] ₹{amount}"); return True

class CardPayment(PaymentStrategy):
    def __init__(self, card): self.card = card
    def pay(self, amount): print(f"[Card ****{self.card[-4:]}] ₹{amount}"); return True

class UPIPayment(PaymentStrategy):
    def __init__(self, upi): self.upi = upi
    def pay(self, amount): print(f"[UPI {self.upi}] ₹{amount}"); return True


class PaymentKiosk:
    def __init__(self, kid): self.kid = kid

    def process(self, ticket: Ticket, strategy: PaymentStrategy) -> bool:
        amount = ticket.calculate()
        if strategy.pay(amount):
            ticket.mark_paid(amount)
            return True
        return False


# ---- Observer (Security Camera) ----

class ParkingEventListener(ABC):
    @abstractmethod
    def on_event(self, event: str, payload: dict): ...

class SecurityCamera(ParkingEventListener):
    def __init__(self, cid, floor): self.cid, self.floor = cid, floor
    def on_event(self, event, payload):
        print(f"[CAM {self.cid} F{self.floor}] {event} :: {payload}")


# ---- ParkingLot (Singleton) ----

class ParkingLot:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *a, **kw):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, floors=None, strategy=None):
        if getattr(self, "_init", False): return
        self.floors = floors or []
        self.strategy = strategy or NearestFirstStrategy()
        self.active: dict[str, Ticket] = {}
        self.listeners: list[ParkingEventListener] = []
        self._alloc_lock = threading.Lock()
        self._init = True

    def add_listener(self, l): self.listeners.append(l)

    def _notify(self, event, payload):
        for l in self.listeners:
            l.on_event(event, payload)

    def set_strategy(self, s): self.strategy = s

    def park(self, vehicle: Vehicle) -> Ticket | None:
        with self._alloc_lock:
            spot = self.strategy.find_spot(self.floors, vehicle)
            if not spot:
                self._notify("LOT_FULL", {"plate": vehicle.plate})
                return None
            spot.park(vehicle)
            ticket = Ticket(vehicle, spot)
            self.active[ticket.id] = ticket
        self._notify("VEHICLE_PARKED", {"plate": vehicle.plate, "spot": spot.spot_id})
        return ticket

    def exit(self, ticket: Ticket) -> bool:
        if ticket.status != TicketStatus.PAID:
            self._notify("UNAUTHORIZED_EXIT", {"ticket": ticket.id})
            return False
        ticket.spot.vacate()
        ticket.mark_exited()
        del self.active[ticket.id]
        self._notify("VEHICLE_EXITED", {"plate": ticket.vehicle.plate, "amount": ticket.amount})
        return True


# ---- Main ----

if __name__ == "__main__":
    f0 = ParkingFloor(0, [
        ParkingSpot("F0-C1", SpotType.COMPACT, 0),
        ParkingSpot("F0-R1", SpotType.REGULAR, 0),
        ParkingSpot("F0-D1", SpotType.DISABLED, 0),
    ])
    lot = ParkingLot(floors=[f0], strategy=NearestFirstStrategy())
    lot.add_listener(SecurityCamera("CAM-F0", 0))

    kiosk = PaymentKiosk("K1")
    car = Vehicle("KA01AB1234", VehicleType.FOUR_WHEELER)
    bike = Vehicle("KA01CD5678", VehicleType.TWO_WHEELER)

    t1 = lot.park(car)
    t2 = lot.park(bike)

    kiosk.process(t1, CardPayment("4111111111111111"))
    lot.exit(t1)

    kiosk.process(t2, UPIPayment("aman@upi"))
    lot.exit(t2)
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| Multi-floor | `ParkingLot.floors: list[ParkingFloor]` |
| Vehicle variety | `VehicleType` + `Vehicle` (plate, type, permit) |
| Disabled spots | `SpotType.DISABLED` + `ParkingSpot.can_fit` checks permit |
| Automated ticketing | `Ticket` created on `park()`, stored in `active` dict |
| Strategy for allocation | `ParkingAllocationStrategy` + `NearestFirstStrategy` |
| Payment kiosk | `PaymentKiosk.process()` uses `PaymentStrategy` |
| Multiple payment methods | `CashPayment`, `CardPayment`, `UPIPayment` |
| Security cameras | `SecurityCamera` implements `ParkingEventListener` (Observer) |
| Concurrency safety | `ParkingLot._alloc_lock` around allocation |
| Ticket state machine | `TicketStatus` + guard checks in `mark_paid` / `mark_exited` |

### Writing Order (for the interview)

1. **Enums** (~1 min) — `VehicleType`, `SpotType`, `TicketStatus`
2. **Vehicle + ParkingSpot** (~3 min) — spot includes `can_fit` matrix
3. **ParkingFloor** (~1 min) — just a list wrapper with `find_free_spot`
4. **AllocationStrategy + NearestFirst** (~2 min) — ABC + one concrete
5. **Ticket** (~2 min) — id, entry/exit, amount, status + transitions
6. **PaymentStrategy + Kiosk** (~3 min) — ABC + 2-3 concretes + kiosk glue
7. **Observer + SecurityCamera** (~2 min) — ABC + one concrete
8. **ParkingLot** (~4 min) — singleton, park/exit, notify, lock
9. **Main** (~2 min) — wire it all up with one car + one bike

**Total: ~18-20 min** (leaving 5+ min for follow-ups)

> **Interview tip:** If you're short on time, skip the Observer and Singleton — ticket flow, Strategy pattern, and spot allocation are the non-negotiables.

---

## Step 7: Follow-up Questions & Answers

> **What to say:** "Happy to discuss how this extends." Common follow-ups Uber asks:

### Q1. Two cars arrive at different gates at the same second — how do you prevent both being assigned the same spot?

**Answer:** `ParkingLot._alloc_lock` serializes the `find_spot → park → create ticket` block. At scale, a global lock is a bottleneck, so:
- **Shard locks per floor** — one `threading.Lock` per `ParkingFloor`; parallel allocation across floors.
- **Optimistic locking** with versioned spots — retry on conflict. Works better in a distributed DB-backed setup.
- **Atomic CAS** via Redis (`SETNX` on `spot:{id}:lock`) for multi-node deployments.

### Q2. How would you scale this to 10,000 spots across 50 locations?

**Answer:**
- Move state to a DB — `ParkingRepository` interface with a Postgres implementation. Spots indexed by `(location_id, floor, spot_type, is_free)`.
- Hot path (find-free-spot) backed by Redis — maintain a `SortedSet` of free spots per (location, floor, type). O(log N) pop.
- Per-location `ParkingLot` instances behind a load balancer; location routing at the API layer.
- Cameras and observers publish to Kafka instead of in-process listeners.
- The in-memory design in the interview is the logical model — production just swaps the storage and concurrency primitives.

### Q3. What if a truck needs two adjacent spots?

**Answer:** Introduce `MultiSpotAllocationStrategy` that returns a `list[ParkingSpot]` instead of one. Model `Ticket` to reference `list[ParkingSpot]`. Add `spot.reserve_group(group_id)` so vacating is atomic. This doesn't break existing single-spot flow — it's an additive strategy.

### Q4. Lost ticket — how does the customer exit?

**Answer:** Exit gate falls back to a **plate-lookup** flow — scans plate, finds the matching `Ticket` in `active` by `vehicle.plate`, charges a flat "lost ticket" penalty on top of the usual amount via a `LostTicketPricingStrategy`. Attendant approval can be an extra step. The `Ticket` entity already has the vehicle, so lookup is trivial.

### Q5. Dynamic pricing — peak hours cost more. How do you add it?

**Answer:** Extract pricing into a `PricingStrategy` interface (`calculate(ticket) -> float`). Concrete: `FlatHourlyPricing`, `PeakHourPricing` (weekday 9-11 AM, 5-8 PM × 1.5), `EventPricing` (override rate window). `Ticket.calculate_amount()` delegates to the injected strategy. Same Strategy pattern we're already using — consistent with the rest of the design.

### Q6. How do EVs with charging requirements fit in?

**Answer:**
- Add `VehicleType.EV` and `SpotType.EV_CHARGING`.
- `EVSpot(ParkingSpot)` subclass holds a `ChargerType` (Type2, CCS, CHAdeMO).
- `Vehicle` gets an optional `charger_compatibility`.
- New `EVAllocationStrategy` prefers compatible charging spots; falls back to regular if none.
- Billing: `PricingStrategy` adds a per-kWh charge component.

### Q7. How does the disabled-spot rule prevent abuse?

**Answer:** `ParkingSpot.can_fit` short-circuits on `SpotType.DISABLED` — it only returns True if `vehicle.has_disabled_permit`. The permit flag comes from a registry check at entry (e.g., license plate → permits DB). If someone parks in a disabled spot without a permit, the entry gate rejects with `UNAUTHORIZED_ATTEMPT`, and the Observer chain notifies security cameras + triggers an alert.

### Q8. What does the SecurityCamera Observer actually do in production?

**Answer:** The in-memory listener pattern is a simplification. In production, each event is published to a message bus (Kafka/SQS). Consumers include:
- **Camera controllers** that pan/zoom to the event location
- **Alert service** that pages on-site security for `UNAUTHORIZED_ATTEMPT`
- **Analytics pipeline** (Flink/Spark) that aggregates occupancy
- **CCTV storage** that tags recordings with ticket IDs for later retrieval
The Observer interface stays the same; only the transport changes.

### Q9. Ticket state — why not use the full State pattern (one class per state)?

**Answer:** For 3 states (ACTIVE, PAID, EXITED) with simple linear transitions, an enum + guard checks is clearer. Full State pattern pays off when state-specific behavior diverges significantly (e.g., if a `PAID` ticket behaves very differently from `ACTIVE` across many methods). I'd refactor to classes if we add states like `OVERDUE`, `DISPUTED`, `REFUNDED`.

### Q10. How do you test this?

**Answer:**
- Unit tests per class: `ParkingSpot.can_fit` matrix, `Ticket.calculate` edge cases (< 1 hour rounds to 1), allocation strategies on mocked floors.
- Concurrency: spawn 100 threads calling `lot.park`, assert no double-allocation.
- State machine: assert `mark_exited` before `mark_paid` raises.
- Observer: fake listener, assert events fire in correct order.
- Integration: end-to-end `park → pay → exit` flow; assert the spot is free at the end.

---

## Summary (for the interviewer)

- **Core asks met:** multi-floor, 2W/4W, automated tickets, disabled spots, cameras, kiosks
- **Primary pattern (as required):** Strategy for allocation
- **Supporting patterns:** Singleton, Strategy (payment), Observer (cameras), State-lite (ticket)
- **Concurrency-aware:** alloc lock with a path to per-floor sharding
- **Extensible:** EVs, trucks, dynamic pricing, reservations, persistence all plug in without touching core
- **Time spent:** ~12 min architecture, ~18 min code, ~5 min follow-ups
