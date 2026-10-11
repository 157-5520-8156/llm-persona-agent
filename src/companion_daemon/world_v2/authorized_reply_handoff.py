"""A scoped hint to drain a committed reply, never delivery authority."""
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _Handoff:
    callback: Callable[[tuple[str, ...]], Awaitable[None]]
    active: bool = True


_current: ContextVar[_Handoff | None] = ContextVar('authorized_reply_handoff', default=None)


@contextmanager
def authorized_reply_handoff(callback) -> Iterator[None]:
    handoff = _Handoff(callback)
    token = _current.set(handoff)
    try:
        yield
    finally:
        # Child tasks may inherit the ContextVar. Closing the shared handle
        # also retires their hint without cancelling durable Action work.
        handoff.active = False
        _current.reset(token)


async def handoff_authorized_reply(action_ids: tuple[str, ...]) -> None:
    handoff = _current.get()
    if handoff is not None and handoff.active and action_ids:
        await handoff.callback(action_ids)
