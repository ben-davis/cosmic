"""The error vocabulary shared by the domain, service, and adapter layers.

Every error carries the HTTP status it means, so an adapter needs one handler
instead of a mapping table it has to keep in sync by hand. The status lives here
rather than in the adapter because the *meaning* ("this input is invalid", "this
isn't yours") is a domain decision; only the encoding is HTTP's.

Nothing in this module imports a web framework. `status` is just an int.
"""


class CosmicError(Exception):
    """Base for every error the domain and service layers raise deliberately.

    Anything else escaping a handler is a bug, and an adapter should let it
    become a 500 rather than dressing it up as a client error.
    """

    status: int = 500


class ValidationError(CosmicError):
    """Input is well-formed but violates a domain rule. → 400."""

    status = 400


class InvalidCursor(ValidationError):
    """A pagination cursor was malformed or not issued by this repository."""


class NotEditable(ValidationError):
    """A field was submitted for edit that the aggregate does not expose."""


class NotFound(CosmicError):
    """A repository lookup found no *visible* row. → 404.

    Deliberately the same error for "does not exist" and "exists but is out of
    your scope": the difference is only observable to someone probing for it,
    and confirming it leaks the existence of other people's records.
    """

    status = 404


class AuthenticationFailed(CosmicError):
    """Credentials were absent, malformed, or wrong. → 401."""

    status = 401


class PermissionDenied(CosmicError):
    """The principal is known but not allowed to do this. → 403.

    Prefer `NotFound` for row-level access failures — see its docstring.
    `PermissionDenied` is for operations the caller may not perform *at all*.
    """

    status = 403


class ConflictError(CosmicError):
    """The request conflicts with existing state. → 409."""

    status = 409
