"""Fresh session graphs backed by explicitly declared application state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from pysx.reactive.reactive import Signal, untracked

from .persistence import (
    RecoveryUnavailable,
    StateCodec,
    StateIdentity,
    StateSnapshot,
    StateStore,
    StoreUnavailable,
    migrate_state,
)
from .session import Session

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from string.templatelib import Template
    from typing import Literal

    from pysx.hosting.request import RequestContext
    from pysx.rendering.render import Fragment

    from .wire import JSONValue


class _StateSignal[T](Signal[T]):
    def __init__(self, value: T, check: Callable[[T], None]) -> None:
        super().__init__(value)
        self._write_guard = None
        self._check = check

    def set(self, value: T) -> None:
        self._check(value)
        super().set(value)


class DurableSession[T](Session):
    """One declared root value; graph objects and DOM handles are always rebuilt."""

    durability: Literal["volatile", "durable"] = "durable"

    def __init__(
        self,
        app: Callable[[Signal[T]], Template | Fragment],
        *,
        initial: Callable[[], T],
        codec: StateCodec[T],
        store: StateStore,
        identity: StateIdentity,
        schema: int = 1,
        migrations: Mapping[int, Callable[[JSONValue], JSONValue]] | None = None,
        request: RequestContext | None = None,
        location: str = "/",
        development: bool = False,
        mutation_capacity: int = 64,
        task_capacity: int = 1024,
        outbox_bytes: int = 2097152,
    ) -> None:
        previous = store.load(identity)
        value = (
            initial()
            if previous is None
            else migrate_state(previous, schema, codec, migrations or {})
        )
        candidate = StateSnapshot(identity, schema, codec.dumps(value))

        if previous is not None:
            candidate = replace(
                candidate,
                owner_epoch=previous.owner_epoch,
                generation=previous.generation,
                event_epoch=previous.event_epoch,
                sequence=previous.sequence,
                revision=previous.revision,
            )
        self.snapshot = store.claim(candidate)
        self.store = store
        self.codec = codec
        self.reconstructed = previous is not None
        self._constructing = True
        self.state: Signal[T] = _StateSignal(value, self._check_state)
        super().__init__(
            lambda: app(self.state),
            request=request,
            location=location,
            development=development,
            mutation_capacity=mutation_capacity,
            task_capacity=task_capacity,
            outbox_bytes=outbox_bytes,
        )
        self.runtime.admit = self._admit
        self._constructing = False
        self.checkpoint()

    def fence(self) -> None:
        self.recovery_failed = True
        self.changed.set()
        self.dispose()

    def _admit(self) -> None:
        try:
            self.store.check(self.snapshot)
        except StoreUnavailable:
            raise
        except Exception as error:
            self.fence()

            raise RecoveryUnavailable("session ownership unavailable") from error

    def _check_state(self, value: T) -> None:
        if self.closed:
            raise RuntimeError("session has been disposed")
        self.runtime.check_write()
        self.codec.dumps(value)
        self.store.check(self.snapshot)

    def checkpoint(self) -> None:
        if self.closed or self._constructing:
            return

        try:
            with untracked():
                payload = self.codec.dumps(self.state())
            candidate = replace(
                self.snapshot,
                payload=payload,
                event_epoch=self.transport.epoch,
                sequence=self.transport.sequence,
                revision=self.transport.revision,
            )
            self.snapshot = self.store.commit(candidate)
        except StoreUnavailable:
            raise
        except Exception as error:
            try:
                self.fence()
            except Exception as cleanup_error:
                error.add_note(f"session cleanup also failed: {cleanup_error}")

            raise RecoveryUnavailable("durable checkpoint failed") from error

    def _finished(self) -> None:
        self.checkpoint()
        super()._finished()
