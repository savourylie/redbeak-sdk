"""Per-session state with no path from one session to another.

Isolation is the property most easily lost by accident in a mock environment:
one module-level dictionary, one class attribute holding a default, one fixture
handed straight to a service instead of copied, and two concurrent cases share
state. Any of those is invisible in a serial test run and shows up only as an
unreproducible failure once cases run in parallel.

So the store makes isolation structural rather than careful:

* state is reachable only by session id, and nothing returns the whole map;
* what is stored is a deep copy, so the caller's fixture cannot be mutated
  through it and cannot be mutated *by* a later reset;
* :meth:`SessionStore.snapshot` hands back a deep copy, so an observer cannot
  reach in and change what it is observing.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Generic, TypeVar

from redbeak_adapter_sdk.errors import UnknownSessionError

StateT = TypeVar("StateT")


class SessionStore(Generic[StateT]):
    """A map from session id to independently owned state."""

    __slots__ = ("_states",)

    def __init__(self) -> None:
        self._states: dict[str, StateT] = {}

    def open(self, session_id: str, state: StateT) -> StateT:
        """Install state for a session, replacing anything already there.

        Replacing rather than refusing is what makes ``reset()`` idempotent: the
        runner may reset a session it has already reset — after a retry, or a
        resumed lease — and must get the same initial state both times rather
        than an error or a merge.
        """
        self._states[session_id] = deepcopy(state)
        return self._states[session_id]

    def get(self, session_id: str) -> StateT:
        """The live, mutable state for one session.

        Live on purpose: tool calls mutate it. The isolation guarantee is that
        no session id but this one can reach this object.
        """
        try:
            return self._states[session_id]
        except KeyError:
            raise UnknownSessionError(f"session {session_id!r} is not open") from None

    def snapshot(self, session_id: str) -> StateT:
        """A deep copy of one session's state, safe to hand to an observer."""
        return deepcopy(self.get(session_id))

    def close(self, session_id: str) -> None:
        """Drop a session's state. Closing an unopened session is not an error."""
        self._states.pop(session_id, None)

    def is_open(self, session_id: str) -> bool:
        return session_id in self._states

    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._states)

    def __len__(self) -> int:
        return len(self._states)

    def __iter__(self) -> Iterator[str]:
        return iter(self._states)


__all__ = ["SessionStore"]
