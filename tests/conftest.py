"""Shared fixtures: in-memory telemetry exporters, scripted LLMs, test-to-trace link."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tracing_support import baggage_test_context, hex_span_id, hex_trace_id, render_trace_report

from mardik.agent import Reply
from mardik.telemetry import Telemetry, build_telemetry

REPO_ROOT = Path(__file__).resolve().parent.parent


class ContextAwareFakeLLM:
    """Looks across the whole conversation for an order id (#1234).

    Found -> asks for an order lookup; otherwise -> asks the user to clarify.
    """

    def invoke(self, messages: list[dict[str, Any]]) -> Reply:
        text = " ".join(str(m.get("content", "")) for m in messages)
        match = re.search(r"#(\d+)", text)
        if match:
            return Reply(
                content="",
                tool_calls=[{"name": "lookup_order", "args": {"order_id": match.group(1)}}],
            )
        return Reply(
            content="Pouvez-vous indiquer votre numéro de commande ?",
            tool_calls=[],
        )


class TimeoutFakeLLM:
    def invoke(self, messages: list[dict[str, Any]]) -> Reply:
        raise TimeoutError("upstream deadline exceeded")


class UnknownToolFakeLLM:
    """Asks for a tool the agent does not have (model drift, bad tool description)."""

    def invoke(self, messages: list[dict[str, Any]]) -> Reply:
        return Reply(content="", tool_calls=[{"name": "cancel_order", "args": {"order_id": "1"}}])


@pytest.fixture
def fake_llm() -> ContextAwareFakeLLM:
    return ContextAwareFakeLLM()


@pytest.fixture
def timeout_llm() -> TimeoutFakeLLM:
    return TimeoutFakeLLM()


@pytest.fixture
def unknown_tool_llm() -> UnknownToolFakeLLM:
    return UnknownToolFakeLLM()


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def telemetry(
    span_exporter: InMemorySpanExporter, metric_reader: InMemoryMetricReader
) -> Telemetry:
    # Content capture is on in tests: traces must be rich enough to diagnose.
    return build_telemetry(
        span_exporter=span_exporter,
        metric_reader=metric_reader,
        level="INFO",
        capture_content=True,
    )


@pytest.fixture(autouse=True)
def _sessions_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve recorded sessions from the repository, whatever the working directory."""
    monkeypatch.setenv("MARDIK_SESSIONS_DIR", str(REPO_ROOT / "sessions"))


@pytest.fixture(autouse=True)
def _test_trace_context(request: pytest.FixtureRequest) -> Any:
    """Name every span emitted by the test after the test (test.case_id, test.run_id)."""
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    with baggage_test_context(request.node.nodeid, run_id):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    """Attach the test's traces to its report: the span tree when it fails,
    and a JSON file per test when MARDIK_TRACE_ARTIFACTS names a directory."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    exporter = getattr(item, "funcargs", {}).get("span_exporter")
    if exporter is None:
        return
    spans = exporter.get_finished_spans()
    if report.failed and spans:
        report.sections.append(("Traces OpenTelemetry du cas", render_trace_report(spans)))

    artifacts = os.environ.get("MARDIK_TRACE_ARTIFACTS")
    if artifacts:
        target = Path(artifacts)
        target.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.nodeid)
        payload = {
            "test.case_id": item.nodeid,
            "outcome": report.outcome,
            "trace_ids": sorted({hex_trace_id(s) for s in spans}),
            "spans": [
                {
                    "name": s.name,
                    "trace_id": hex_trace_id(s),
                    "span_id": hex_span_id(s),
                    "parent_span_id": format(s.parent.span_id, "016x") if s.parent else None,
                    "status": s.status.status_code.name,
                    "attributes": {k: _jsonable(v) for k, v in (s.attributes or {}).items()},
                }
                for s in spans
            ],
        }
        (target / f"{safe}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _jsonable(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value
