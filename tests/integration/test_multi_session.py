"""Multi-session integration tests: recorded sessions replayed end to end.

Two axes (note de diagnostic, point 1, § 2.2):
- longitudinal: N turns in one session, state must accumulate correctly;
- transversal: M sessions at once on the shared store, no cross-talk.

Assertions combine the reply (what the user sees) with the trace (what the
agent did), so a failure points at a span, not only at a string.
"""
from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode
from tracing_support import (
    children_of,
    describe,
    hex_trace_id,
    run_concurrently,
    spans_named,
)

from mardik.runner import list_sessions, load_session, replay, replay_turn_by_turn
from mardik.session import SessionStore

# Expected outcome of the final turn of each recorded "replay_*" session.
EXPECTED_FINAL_REPLY = {
    "replay_delivery": ("1042", "expédiée"),
    "replay_return": ("3157", "remboursement en cours"),
    "replay_clarification": ("2098", "en préparation"),
    "replay_unknown_order": ("9999", "introuvable"),
}


def test_every_recorded_session_has_an_expectation():
    """A session added to sessions/ without an expected outcome is never tested."""
    assert set(list_sessions("replay_")) == set(EXPECTED_FINAL_REPLY)


@pytest.mark.parametrize("name", sorted(EXPECTED_FINAL_REPLY))
def test_replayed_session_answers_from_its_own_context(name, make_agent, fake_llm, span_exporter):
    data = load_session(name)
    store = SessionStore()
    order_id, status = EXPECTED_FINAL_REPLY[name]

    result = replay(data, make_agent(fake_llm), store)

    # Reply: the final question never repeats the order id, so the answer
    # can only come from the recorded history (INC-01).
    assert f"#{order_id}" in result.reply and status in result.reply, result.reply

    # Trajectory: exactly one lookup, with the order id taken from the history.
    (turn,) = spans_named(span_exporter.get_finished_spans(), "agent.turn")
    user_turns = sum(1 for m in data["messages"] if m["role"] == "user")
    assert turn.attributes["mardik.turn.index"] == user_turns, describe(turn)
    assert turn.attributes["mardik.context.messages"] == len(data["messages"]), describe(turn)
    tools = spans_named(span_exporter.get_finished_spans(), "tool.call")
    assert [t.attributes["gen_ai.tool.name"] for t in tools] == ["lookup_order"]
    assert order_id in tools[0].attributes["mardik.tool.arguments"], describe(tools[0])


def test_longitudinal_context_survives_every_turn(make_agent, fake_llm, span_exporter):
    """Turn 1 has no order id, turn 2 gives it, turn 3 must still use it."""
    data = load_session("replay_clarification")
    store = SessionStore()

    results = replay_turn_by_turn(data, make_agent(fake_llm), store)

    assert "numéro de commande" in results[0].reply
    assert "#2098" in results[1].reply
    assert "#2098" in results[2].reply, "turn 3 lost the order id given at turn 2"
    assert store.turns(data["session_id"]) == 3
    assert len(store.history(data["session_id"])) == 6

    turns = spans_named(span_exporter.get_finished_spans(), "agent.turn")
    # One trace per turn, tied together by the session id attribute.
    assert len({hex_trace_id(t) for t in turns}) == 3
    assert {t.attributes["mardik.session.id"] for t in turns} == {data["session_id"]}
    assert sorted(t.attributes["mardik.turn.index"] for t in turns) == [1, 2, 3]


def test_concurrent_sessions_do_not_leak_into_each_other(make_agent, fake_llm, span_exporter):
    """M sessions at once on one shared store, each with a unique marker (its order id)."""
    agent = make_agent(fake_llm)
    store = SessionStore()
    sessions = {f"iso-{n}": f"{7000 + n}" for n in range(8)}

    def conversation(session_id: str, order_id: str) -> list[str]:
        return [
            agent.run_turn(store, session_id, f"Bonjour, commande #{order_id} ?").reply,
            agent.run_turn(store, session_id, "Et elle arrive quand ?").reply,
            agent.run_turn(store, session_id, "Merci.").reply,
        ]

    replies = run_concurrently(
        [lambda s=s, o=o: conversation(s, o) for s, o in sessions.items()]
    )

    for (session_id, order_id), session_replies in zip(sessions.items(), replies):
        foreign = [o for o in sessions.values() if o != order_id]
        for reply in session_replies:
            assert f"#{order_id}" in reply, f"{session_id}: {reply!r}"
            leaked = [o for o in foreign if o in reply]
            assert not leaked, f"{session_id} answered with another session's order: {leaked}"
        assert store.turns(session_id) == 3
        assert len(store.history(session_id)) == 6

    turns = spans_named(span_exporter.get_finished_spans(), "agent.turn")
    assert len(turns) == 3 * len(sessions)
    for session_id in sessions:
        indexes = sorted(
            t.attributes["mardik.turn.index"]
            for t in turns
            if t.attributes["mardik.session.id"] == session_id
        )
        assert indexes == [1, 2, 3], f"{session_id}: turn indexes {indexes}"


def test_concurrent_turns_on_one_session_are_all_counted(make_agent, fake_llm, span_exporter):
    """A user who double-sends: several turns of the same session land together (INC-03)."""
    agent = make_agent(fake_llm)
    store = SessionStore()
    session_id = "double-send"
    parallel_turns = 16

    run_concurrently(
        [
            lambda: agent.run_turn(store, session_id, "Où en est la commande #1042 ?")
            for _ in range(parallel_turns)
        ]
    )

    assert store.turns(session_id) == parallel_turns
    assert len(store.history(session_id)) == 2 * parallel_turns
    indexes = sorted(
        t.attributes["mardik.turn.index"]
        for t in spans_named(span_exporter.get_finished_spans(), "agent.turn")
    )
    duplicates = sorted({i for i in indexes if indexes.count(i) > 1})
    assert not duplicates, f"turn indexes handed out twice: {duplicates}"
    assert indexes == list(range(1, parallel_turns + 1))


def test_every_turn_is_one_complete_trace(make_agent, fake_llm, span_exporter):
    """llm.invoke and tool.call are children of agent.turn in the same trace (INC-04)."""
    replay(load_session("replay_delivery"), make_agent(fake_llm), SessionStore())

    spans = span_exporter.get_finished_spans()
    (turn,) = spans_named(spans, "agent.turn")
    children = {child.name for child in children_of(spans, turn)}
    assert {"llm.invoke", "tool.call"} <= children, [describe(s) for s in spans]
    assert {hex_trace_id(s) for s in spans} == {hex_trace_id(turn)}
    # A healthy turn is decided OK, not left UNSET (note de diagnostic, point 3, § 1).
    assert {s.status.status_code for s in spans} == {StatusCode.OK}
