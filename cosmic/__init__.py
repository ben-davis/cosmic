from cosmic.documents import Attributes, Envelope, input_model
from cosmic.domain import (
    AggregateRoot,
    Command,
    Context,
    Event,
    PartialUpdate,
    apply_changes,
)
from cosmic.errors import (
    AuthenticationFailed,
    ConflictError,
    CosmicError,
    InvalidCursor,
    NotEditable,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from cosmic.message_bus import MessageBus
from cosmic.ports import Repository, UnitOfWork
from cosmic.repositories import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AggregateRepository,
)
from cosmic.serialization import (
    AggregateSchema,
    Child,
    Ref,
    serialize,
    serialize_many,
)
from cosmic.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork

__all__ = [
    # domain
    "AggregateRoot",
    "Command",
    "PartialUpdate",
    "Context",
    "Event",
    "apply_changes",
    # errors
    "CosmicError",
    "ValidationError",
    "InvalidCursor",
    "NotEditable",
    "NotFound",
    "AuthenticationFailed",
    "PermissionDenied",
    "ConflictError",
    # machinery
    "MessageBus",
    "Repository",
    "UnitOfWork",
    "AggregateRepository",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "AbstractUnitOfWork",
    "SqlAlchemyUnitOfWork",
    # documents (out)
    "AggregateSchema",
    "Child",
    "Ref",
    "serialize",
    "serialize_many",
    # documents (in)
    "Attributes",
    "Envelope",
    "input_model",
]
