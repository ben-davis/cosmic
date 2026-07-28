import logging
from collections import defaultdict
from typing import Any, Callable, Optional

from fastapi_resources.domain import Command, Event

logger = logging.getLogger(__name__)


class MessageBus:
    """App-level dispatch table for commands and events.

    The bus is a singleton (one per app) holding only the message→handler map.
    The per-request execution context (the UnitOfWork) is passed at dispatch:
    ``bus.handle(command, uow)``. Command handlers are ``(command, uow)``; after a
    command runs, the bus drains that uow's new domain events and dispatches them
    to event handlers / projectors (registered as ``(event)`` with their own deps
    bound at bootstrap).
    """

    def __init__(self):
        self._command_handlers: dict[type, Callable[..., Any]] = {}
        self._event_handlers: dict[type, list[Callable[..., Any]]] = defaultdict(list)

    def register(self, message_type: type, handler: Callable[..., Any]) -> None:
        if issubclass(message_type, Command):
            self._command_handlers[message_type] = handler
        elif issubclass(message_type, Event):
            self._event_handlers[message_type].append(handler)
        else:
            raise ValueError(f"{message_type} is not a Command or Event subclass")

    # Read-side subscribers (projectors). Alias of register for clarity at call sites.
    def register_projector(self, event_type: type, handler: Callable[..., Any]) -> None:
        self.register(event_type, handler)

    def handle(self, message, uow=None):
        """Dispatch a command (with its UoW) or an event, draining domain events."""
        queue = [message]
        result = None

        while queue:
            msg = queue.pop(0)
            if isinstance(msg, Command):
                result = self._handle_command(msg, uow, queue)
            elif isinstance(msg, Event):
                self._handle_event(msg, queue)

        return result

    def _handle_command(self, cmd: Command, uow, queue: list):
        handler = self._command_handlers.get(type(cmd))
        if handler is None:
            raise ValueError(f"No handler registered for command {type(cmd).__name__}")

        result = handler(cmd, uow) if uow is not None else handler(cmd)
        if uow is not None:
            queue.extend(uow.collect_new_events())
        return result

    def _handle_event(self, event: Event, queue: list):
        for handler in self._event_handlers.get(type(event), []):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s", handler, type(event).__name__
                )
