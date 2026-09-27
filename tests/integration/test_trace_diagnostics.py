"""Incident replays: the failure must be explicit, and the trace must name its cause.

Each test replays a recorded incident and checks the signature the diagnosis
procedure relies on (README, "Diagnostiquer un test rouge").
"""
from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode
from structlog.testing import capture_logs
from tracing_support import (
    describe,
    hex_span_id,
    hex_trace_id,
    metric_points,
    render_trace_report,
    spans_named,
)

from mardik.errors import LLMTimeoutError, ToolExecutionError
from mardik.runner import load_session, replay
from mardik.session import SessionStore


def test_llm_timeout_is_explicit_and_traced(make_agent, timeout_llm, span_exporter, metric_reader):
    """INC-02: a timeout surfaces as LLMTimeoutError, traced, counted and logged."""
    data = load_session("incident_timeout")

    with capture_logs() as logs, pytest.raises(LLMTimeoutError):
        replay(data, make_agent(timeout_llm), SessionStore())

    spans = span_exporter.get_finished_spans()
    (turn,) = spans_named(spans, "agent.turn")
    (llm,) = spans_named(spans, "llm.invoke")
    # The failing span is inside the turn's trace, not in an orphan trace.
    assert hex_trace_id(llm) == hex_trace_id(turn)
    assert llm.status.status_code is StatusCode.ERROR, describe(llm)
    assert llm.attributes["error.type"] == "timeout"
    assert turn.status.status_code is StatusCode.ERROR, describe(turn)
    assert turn.attributes["mardik.turn.outcome"] == "error"
    assert turn.attributes["error.type"] == "LLMTimeoutError"

    assert ({"error.type": "LLMTimeoutError"}, 1) in metric_points(metric_reader, "errors_total")
    assert ({"outcome": "error"}, 1) in metric_points(metric_reader, "turns_total")

    (failed,) = [entry for entry in logs if entry["event"] == "turn.failed"]
    assert failed["error_type"] == "LLMTimeoutError"
    assert failed["session_id"] == data["session_id"]


def test_unknown_tool_is_not_absorbed(make_agent, unknown_tool_llm, span_exporter, metric_reader):
    """A tool the agent lacks fails the turn loudly; the tool.call span says which one."""
    with pytest.raises(ToolExecutionError):
        replay(load_session("replay_delivery"), make_agent(unknown_tool_llm), SessionStore())

    (tool,) = spans_named(span_exporter.get_finished_spans(), "tool.call")
    assert tool.status.status_code is StatusCode.ERROR
    assert tool.attributes["gen_ai.tool.name"] == "cancel_order"
    assert tool.attributes["error.type"] == "unknown_tool"
    # The unbounded tool name is kept on the span, never as a metric label.
    assert ({"tool": "unknown", "status": "error"}, 1) in metric_points(
        metric_reader, "tool_calls_total"
    )


def test_spans_name_the_test_that_produced_them(request, make_agent, fake_llm, span_exporter):
    """Trace -> test: every span carries test.case_id (note de diagnostic, point 4)."""
    replay(load_session("replay_delivery"), make_agent(fake_llm), SessionStore())

    spans = span_exporter.get_finished_spans()
    assert spans
    assert {s.attributes.get("test.case_id") for s in spans} == {request.node.nodeid}
    assert all("test.run_id" in s.attributes for s in spans)


def test_logs_carry_the_trace_and_span_ids(make_agent, fake_llm, span_exporter, capsys):
    """Log -> trace: the JSON log line of a turn points at the agent.turn span."""
    import json

    capsys.readouterr()
    replay(load_session("replay_delivery"), make_agent(fake_llm), SessionStore())
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    (completed,) = [line for line in lines if line.get("event") == "turn.completed"]

    (turn,) = spans_named(span_exporter.get_finished_spans(), "agent.turn")
    assert completed["trace_id"] == hex_trace_id(turn)
    assert completed["span_id"] == hex_span_id(turn)


def test_trace_report_points_at_the_failing_span(make_agent, timeout_llm, span_exporter):
    """The report attached to a red test flags the first ERROR span and its exception."""
    with pytest.raises(LLMTimeoutError):
        replay(load_session("incident_timeout"), make_agent(timeout_llm), SessionStore())

    report = render_trace_report(span_exporter.get_finished_spans())
    assert "!! agent.turn [ERROR" in report
    assert "!! llm.invoke [ERROR" in report
    assert "error.type=timeout" in report
    assert "exception mardik.errors.LLMTimeoutError" in report
