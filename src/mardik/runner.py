"""Replay recorded sessions through the agent for integration testing."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .agent import Agent, TurnResult
from .session import SessionStore


def sessions_dir() -> Path:
    return Path(os.environ.get("MARDIK_SESSIONS_DIR", "sessions"))


def load_session(name: str) -> dict[str, Any]:
    path = sessions_dir() / f"{name}.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def list_sessions(prefix: str = "") -> list[str]:
    """Names of the recorded sessions, optionally filtered by prefix."""
    return sorted(p.stem for p in sessions_dir().glob(f"{prefix}*.json"))


def replay(session_data: dict[str, Any], agent: Agent, store: SessionStore) -> TurnResult:
    """Replay a recorded session and return the result of its final turn.

    INC-01: every recorded message before the final user message is first
    loaded into the store (and each earlier user message counted as a turn),
    so the agent answers with the same context as in production. Replaying
    the last message alone silently dropped the conversation.
    """
    session_id = session_data["session_id"]
    *previous, last = session_data["messages"]
    if last.get("role") != "user":
        raise ValueError(f"session {session_id!r} must end with a user message")
    for message in previous:
        store.append(session_id, message)
        if message.get("role") == "user":
            store.record_turn(session_id)
    return agent.run_turn(store, session_id, last["content"])


def replay_turn_by_turn(
    session_data: dict[str, Any], agent: Agent, store: SessionStore
) -> list[TurnResult]:
    """Run every recorded user message through the agent, in order.

    Recorded assistant messages are ignored: the agent produces its own, so
    the test exercises how state accumulates across turns.
    """
    session_id = session_data["session_id"]
    return [
        agent.run_turn(store, session_id, message["content"])
        for message in session_data["messages"]
        if message.get("role") == "user"
    ]
