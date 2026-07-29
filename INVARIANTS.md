# cosmic — Architecture Invariants

These are the rules of this library as it exists now. It is a small
**cosmicpython-style aggregate framework**: it provides the message-bus / unit-of-work /
repository / domain-event machinery for building aggregate-oriented apps, plus a
JSON:API **compound-document serializer** for reads and command responses.

> Renamed from `fastapi-resources`, which described what it used to be — a CRUD
> and JSON:API *routing* generator. None of that survives. There is no HTTP,
> FastAPI, or routing code here; routing lives in the consuming app as thin
> adapters that call `serialize()`.

---

## What This Library Is

A toolkit of independent pieces that work together:

- **`AggregateRoot`** — mixin that records domain events on aggregate roots
- **`Command` / `Event`** — frozen-dataclass base classes for messages
- **`MessageBus`** — routes commands to one handler and events to many; `on_error` escalates failed event handlers
- **`AbstractUnitOfWork` / `SqlAlchemyUnitOfWork`** — the transactional boundary; collects domain events at commit
- **`AggregateRepository` / `build_aggregate_repo`** — loads a root + its children as a unit, scopes the root, tracks touched roots, and paginates (`list()`)
- **`serialize` / `serialize_many` / `AggregateSchema` / `Child` / `Ref`** — JSON:API compound-document serialization
- **`Repository` / `UnitOfWork` ports** — Protocols the service layer types against
- **`NotFound` / `InvalidCursor`** — the shared exceptions (→ HTTP 404 / 400 in the app)

Everything is exported from `cosmic/__init__.py`. There is no HTTP,
FastAPI, or JSON:API *routing* code in this library.

---

## Domain messages (`domain.py`)

```python
@dataclass(frozen=True)
class Command: ...      # imperative intent, one handler, may fail loudly

@dataclass(frozen=True)
class Event: ...        # a fact that happened, zero-or-more handlers
```

Concrete commands/events are app-defined frozen dataclasses inheriting these.

### `AggregateRoot`

```python
class AggregateRoot:
    def record(self, event: Event) -> None      # append to lazy self._events
    def pull_events(self) -> list[Event]         # drain and return
```

Rules:
- `_events` is a plain instance attribute created lazily — **never an ORM column**.
- Aggregate roots mix this in: `class Calendar(AggregateRoot, BaseModel)`.
- Roots emit events from their behavior methods via `self.record(...)`.
- The UoW drains events with `pull_events()` at commit, from the roots its repos touched.

---

## Repositories (`repositories/__init__.py`)

### `AggregateRepository` + `build_aggregate_repo`

One repository per aggregate **root**. Loads the whole aggregate and scopes the root.

```python
CalendarRepo = build_aggregate_repo(
    Calendar,
    load=["members"],                                   # child collections to eager-load
    scope=lambda ctx: [Calendar.id.in_(...ctx["principal"]...)],  # row-level predicates on the root
    sort=(Calendar.name, False),                        # (column, descending) for list()
)
```

Behavior:
- `__init__(session, context=None, id_field=None)` — creates `self.seen: dict` (keyed by `id()`).
- `add(root)` — `session.add(root)` **without flush** (PKs are app-assigned uuid7), records `seen[id(root)] = root`.
- `remove(root)` — `session.delete(root)`, records in `seen`.
- `get(pk)` — `_base_select()` + `pk_col == pk`, `.one_or_none()`; raises `NotFound` if absent/out-of-scope; records in `seen`.
- `list(limit=…, cursor=…)` — `_base_select()` ordered by `(sort_column, pk)`, returns `(roots, next_cursor)`; records each root in `seen`.
- `_base_select()` — the shared scoped+eager-loaded select behind **both** `get` and `list`.
- `scope()` returns `[]` by default; `build_aggregate_repo`'s `scope=` becomes `scope(self.context)`.

Rules:
- **There is no repository for child entities.** Children are reached through the loaded root.
- **`get` and `list` must share `_base_select()`.** A root that is listable but not
  loadable (or vice versa) is a row-level authorization bug; deriving both from one
  select makes that divergence impossible rather than merely unlikely.
- `seen` is a **dict keyed by `id()`** because aggregate roots are `MappedAsDataclass` (unhashable) — never a `set`.
- The parameter is `pk`, not `id` (must not shadow the builtin `id()`).

### Pagination

Keyset ("seek"), never OFFSET:

- Ordering is `(sort_column, pk)`. The PK is a **tiebreaker that makes the order
  total** — without it, rows sharing a sort value can be duplicated or skipped
  across a page boundary.
- The cursor is a base64url `[sort_value, pk]` pair. `_decode_cursor` re-hydrates
  the sort value to the column's Python type, because SQLite compares a
  TEXT-bound datetime differently from a DATETIME-bound one.
- `list()` over-fetches by one row to detect a further page without a second
  `COUNT(*)`. `next_cursor` is `None` on the last page.
- `limit` is clamped to `[1, MAX_PAGE_SIZE]`; a malformed cursor raises `InvalidCursor`.
- Index every `sort` column, or pagination degrades to a sort of the whole table.

---

## Unit of Work (`unit_of_work.py`)

```python
class AbstractUnitOfWork(ABC):
    def __enter__(self) -> "AbstractUnitOfWork"
    def __exit__(self, *args)                 # calls self.rollback()
    def commit(self); def rollback(self)
    def collect_new_events(self) -> Iterator[Event]
```

`SqlAlchemyUnitOfWork(session_factory)`:
- `__enter__` opens a fresh `self.session = session_factory()` and resets `collected_events`.
- `__exit__` rolls back and closes the session.
- `commit()` does, in order:
  1. **Re-add** every seen root (`session.add(root)`), **skipping roots in `session.deleted`** — this cascades children appended *after* the initial `add()` (e.g. an account issuing a token) without resurrecting deleted roots.
  2. `session.flush()`.
  3. Collect events: for each repo with a `dict` `seen`, drain `root.pull_events()` from its roots.
  4. `session.commit()`.

Events come **only** from roots a repository touched. An object added straight to
`uow.session` is not an aggregate root and contributes nothing — if its events
matter, load it through a repo.
- `collect_new_events()` drains and clears `collected_events`.

Rules:
- A UoW is **per unit of work** (per command dispatch / per request). Instantiate freely; it's cheap.
- Concrete UoWs subclass `SqlAlchemyUnitOfWork` and assign one repo per aggregate in `__enter__`.
- The session is owned by the UoW and never handed out.

---

## Message Bus (`message_bus.py`)

The bus is an app-level singleton: a dispatch table. The **UoW is passed at dispatch time**.

```python
bus = MessageBus(on_error=report)                   # on_error is optional but wanted
bus.register(SomeCommand, handle_some_command)      # command → exactly one handler
bus.register(SomeEvent, project_some)               # event → append a handler
bus.handle(command, uow)                            # dispatch
```

Behavior of `handle(message, uow=None)`:
- **Command** → look up its single handler (missing → `ValueError`). Call `handler(cmd, uow)` when a uow is passed, else `handler(cmd)`. After it returns, drain `uow.collect_new_events()` and queue them.
- **Event** → call every registered handler `handler(event)`; exceptions are logged and passed to `on_error`, not raised; remaining handlers still run.
- Returns the command handler's return value.

Rules:
- **Command handlers fail loudly**; **event handlers fail soft** — a broken
  subscriber must not undo a command that already committed.
- **Soft failure needs `on_error`.** Without it a permanently broken handler is
  indistinguishable from a working one; this exact gap once hid the fact that no
  background task in the consuming app could be scheduled *at all*. A failing
  `on_error` is itself caught, so it can never break dispatch.
- Event handlers / projectors get their non-uow dependencies bound at bootstrap (e.g. `partial(on_event_created, schedule=...)`); they do **not** receive the command's uow.
- The bus knows nothing about HTTP, sessions, or FastAPI. It is **sync**: a
  handler that schedules async work owns the thread→loop handoff itself.

---

## Serialization (`serialization.py`)

Turns an aggregate into a **JSON:API compound document**.

```python
CALENDAR = AggregateSchema(
    type="calendar",
    read=CalendarRead,                                   # pydantic; must include `id`
    children=[Child(attr="members", type="calendar_member", read=CalendarMemberRead)],
)
SAVED_EVENT = AggregateSchema(
    type="saved_event",
    read=SavedEventRead,
    refs=[Ref(attr="event_id", type="event")],           # another aggregate, by id
)
serialize(root, CALENDAR, self_url="/calendars/1")   # single
serialize_many(roots, CALENDAR, self_url="/calendars", next_cursor=cursor)  # list
```

**`Child` vs `Ref`** — the distinction is ownership, and it is the point:

| | Relationship entry | `included` body |
|---|---|---|
| `Child` — an entity *inside* this aggregate | ✅ | ✅ (the root owns it and loaded it) |
| `Ref` — another aggregate, held by id | ✅ | ❌ (this document doesn't own it) |

`Ref(attr="event_id", type="event")` names the relationship `event` (the `_id`
suffix is stripped unless `name=` overrides it) and emits `{"data": None}` when
the id is null.

Output shape:
- Root is the primary `data` (`{type, id, attributes, relationships}`); `id` is lifted out of attributes.
- Children are `included` resource objects — each has `type` + `id` + `attributes` but **no `self` link** and no endpoint of their own — linked from the root's `relationships`.
- `serialize_many` dedupes `included` by `(type, id)`, sets `meta.count` to **this page's** size, and emits `links.next` when `next_cursor` is given.

Rules:
- Children are addressable only *through* the root. This is exactly what a JSON:API read client (make-resource) normalizes into its entity store.
- Value objects (no identity) do **not** become `included` resources; embed them in attributes.
- A `Ref` must **never** inline the referenced aggregate. Serializing it here
  would bypass that aggregate's own scope rules and let this endpoint leak rows
  the caller isn't allowed to read.

---

## Ports (`ports.py`)

Runtime-checkable Protocols the service layer types against, so handlers can be
driven by in-memory fakes:

```python
class Repository(Protocol):  def add(root); def remove(root); def get(pk); def list(*, limit, cursor)
class UnitOfWork(Protocol):  __enter__/__exit__; def commit(); def collect_new_events()
```

Rule: **a port declares exactly what the machinery calls — no more, no less.**
`UnitOfWork` once declared `repo_for` (which nothing called) while omitting
`collect_new_events` (which `MessageBus._handle_command` calls after every
command). A fake could satisfy the port, silently swallow every domain event, and
the consuming app had to hand-roll its own Protocol to compensate. If you add a
call site in the bus or UoW, update the port in the same change.

---

## What This Library Does NOT Do

- No `build_commands` / `build_handlers` (generated CRUD).
- No `Resource` classes, mixins, or `build_sqlalchemy_resource`.
- No generic non-aggregate repository. `BaseSqlAlchemyRepo` was removed — the
  aggregate repo is the only path, so nothing can quietly reach a child entity
  without going through its root.
- No JSON:API **routing** / route generation. The app writes thin FastAPI routers
  that dispatch commands and call `serialize()`.
- No async dispatch. The bus is sync; scheduling async work is the app's job.

---

## Testing

- `tests/test_aggregate.py` — end-to-end spine: `AggregateRoot` + `build_aggregate_repo`
  (scope + `seen`) + UoW event collection + `bus.handle(cmd, uow)` + projector.
- `tests/test_serialization.py` — compound documents (`Child` bodies vs `Ref` links) + pagination links.
- `tests/test_repositories.py` — scoping, `get`/`list` agreement, keyset pagination (ties, scope on later pages, cursor validation).
- `tests/test_message_bus.py`, `tests/test_unit_of_work.py`, `tests/test_ports.py`.
- Run: `PYTHONPATH=. python -m pytest -q` (uses the parent app's venv).

---

## Module Reference

```
cosmic/
├── __init__.py         # public exports (see "What This Library Is")
├── domain.py           # Command, Event, AggregateRoot
├── message_bus.py      # MessageBus (handle(message, uow))
├── unit_of_work.py     # AbstractUnitOfWork, SqlAlchemyUnitOfWork
├── repositories/       # AggregateRepository, build_aggregate_repo, cursor helpers
├── serialization.py    # AggregateSchema, Child, Ref, serialize, serialize_many
├── ports.py            # Repository, UnitOfWork Protocols
├── exceptions.py       # NotFound
└── types.py
```
