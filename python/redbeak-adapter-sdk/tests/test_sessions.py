"""Session state is isolated, and reset from a fixture is deterministic.

These are the two properties that a serial test run will happily hide: a shared
default, a fixture handed out by reference, a reset that merges rather than
replaces. Each test below mutates one session and then asserts about a different
one, or about the fixture itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from _sdk_fixtures import CASE_EXECUTION_ID, OTHER_CASE_EXECUTION_ID
from redbeak_adapter_sdk import SessionStore, UnknownSessionError

FIXTURE: dict[str, Any] = {
    "GorillaFileSystem": {
        "root": {"alex": {"type": "directory", "contents": {"notes.txt": {"type": "file"}}}}
    }
}


@pytest.fixture
def store() -> SessionStore[dict[str, Any]]:
    return SessionStore()


class TestIsolation:
    def test_two_sessions_from_one_fixture_do_not_share_state(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        first = store.open(CASE_EXECUTION_ID, FIXTURE)
        second = store.open(OTHER_CASE_EXECUTION_ID, FIXTURE)
        first["GorillaFileSystem"]["root"]["alex"]["contents"]["new.txt"] = {"type": "file"}
        assert "new.txt" not in second["GorillaFileSystem"]["root"]["alex"]["contents"]

    def test_mutating_a_session_does_not_touch_the_fixture(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        """A shared fixture mutated by the first case would poison every later one."""
        state = store.open(CASE_EXECUTION_ID, FIXTURE)
        state["GorillaFileSystem"]["root"].clear()
        assert FIXTURE["GorillaFileSystem"]["root"]["alex"]["contents"]

    def test_a_snapshot_cannot_be_used_to_reach_back_into_the_session(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        store.open(CASE_EXECUTION_ID, FIXTURE)
        snapshot = store.snapshot(CASE_EXECUTION_ID)
        snapshot["GorillaFileSystem"]["root"] = {}
        assert store.get(CASE_EXECUTION_ID)["GorillaFileSystem"]["root"]

    def test_state_is_reachable_only_through_a_session_id(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        """Ids are enumerable; the states behind them are reachable one at a time.

        Pinned as a surface test because the leak would be an *addition* — a
        convenience accessor returning the whole map, added later by someone who
        needed to iterate — not a change to anything written here today.
        """
        store.open(CASE_EXECUTION_ID, FIXTURE)
        store.open(OTHER_CASE_EXECUTION_ID, FIXTURE)
        assert set(store.session_ids()) == {CASE_EXECUTION_ID, OTHER_CASE_EXECUTION_ID}
        public = {name for name in dir(store) if not name.startswith("_")}
        assert public == {"close", "get", "is_open", "open", "session_ids", "snapshot"}


class TestResetDeterminism:
    def test_reopening_restores_the_initial_state(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        state = store.open(CASE_EXECUTION_ID, FIXTURE)
        state["GorillaFileSystem"]["root"]["alex"]["contents"]["scratch.txt"] = {"type": "file"}
        reopened = store.open(CASE_EXECUTION_ID, FIXTURE)
        assert reopened == FIXTURE

    def test_reopening_replaces_rather_than_merges(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        """A merge would let the previous attempt's leftovers survive a retry."""
        store.open(CASE_EXECUTION_ID, {"TradingBot": {"balance": 10.0}})
        assert store.get(CASE_EXECUTION_ID) == {"TradingBot": {"balance": 10.0}}
        store.open(CASE_EXECUTION_ID, {"TravelAPI": {"budget_limit": 100.0}})
        assert store.get(CASE_EXECUTION_ID) == {"TravelAPI": {"budget_limit": 100.0}}

    def test_two_resets_of_the_same_fixture_are_equal(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        assert store.open(CASE_EXECUTION_ID, FIXTURE) == store.open(CASE_EXECUTION_ID, FIXTURE)


class TestLifecycle:
    def test_an_unopened_session_is_an_adapter_failure(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        with pytest.raises(UnknownSessionError):
            store.get(CASE_EXECUTION_ID)

    def test_a_closed_session_is_gone(self, store: SessionStore[dict[str, Any]]) -> None:
        store.open(CASE_EXECUTION_ID, FIXTURE)
        store.close(CASE_EXECUTION_ID)
        assert not store.is_open(CASE_EXECUTION_ID)
        with pytest.raises(UnknownSessionError):
            store.get(CASE_EXECUTION_ID)

    def test_closing_twice_is_not_an_error(self, store: SessionStore[dict[str, Any]]) -> None:
        """A runner that retries cleanup must not be punished for it."""
        store.open(CASE_EXECUTION_ID, FIXTURE)
        store.close(CASE_EXECUTION_ID)
        store.close(CASE_EXECUTION_ID)

    def test_closing_one_session_leaves_the_other_open(
        self, store: SessionStore[dict[str, Any]]
    ) -> None:
        store.open(CASE_EXECUTION_ID, FIXTURE)
        store.open(OTHER_CASE_EXECUTION_ID, FIXTURE)
        store.close(CASE_EXECUTION_ID)
        assert store.is_open(OTHER_CASE_EXECUTION_ID)
        assert len(store) == 1
