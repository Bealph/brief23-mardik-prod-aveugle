"""Link integration tests to the traces they produce.

- :func:`baggage_test_context` puts ``test.case_id`` and ``test.run_id`` in the
  OpenTelemetry baggage; ``BaggageTestContextProcessor`` copies them onto every
  span, so each trace names the test that caused it (trace -> test).
- :func:`render_trace_report` turns the spans of a failed test into a tree
  that is attached to the pytest report (test -> trace).
- :func:`run_concurrently` starts threads that keep the caller's context.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from opentelemetry import baggage, context
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

# Attributes worth showing in a report line, in display order.
_REPORT_ATTRIBUTES = (
    "mardik.session.id",
    "mardik.turn.index",
    "mardik.turn.outcome",
    "mardik.context.messages",
    "mardik.llm.input_messages",
    "mardik.llm.tool_calls_requested",
    "gen_ai.tool.name",
    "mardik.tool.status",
    "error.type",
)


@contextlib.contextmanager
def baggage_test_context(case_id: str, run_id: str) -> Iterator[None]:
    ctx = baggage.set_baggage("test.case_id", case_id)
    ctx = baggage.set_baggage("test.run_id", run_id, context=ctx)
    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


def hex_trace_id(span: ReadableSpan) -> str:
    return format(span.context.trace_id, "032x")


def hex_span_id(span: ReadableSpan) -> str:
    return format(span.context.span_id, "016x")


def spans_named(spans: Sequence[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [span for span in spans if span.name == name]


def children_of(spans: Sequence[ReadableSpan], parent: ReadableSpan) -> list[ReadableSpan]:
    return [
        span
        for span in spans
        if span.parent is not None and span.parent.span_id == parent.context.span_id
    ]


def describe(span: ReadableSpan) -> str:
    """One line per span: name, status, span id and the diagnostic attributes."""
    attrs = span.attributes or {}
    status = span.status.status_code.name
    if span.status.status_code is StatusCode.ERROR and span.status.description:
        status += f": {span.status.description}"
    shown = " ".join(
        f"{key}={_fmt(attrs[key])}" for key in _REPORT_ATTRIBUTES if key in attrs
    )
    return f"{span.name} [{status}] span={hex_span_id(span)} {shown}".rstrip()


def _fmt(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(str(v) for v in value) + "]"
    return str(value)


def render_trace_report(spans: Sequence[ReadableSpan]) -> str:
    """Render every trace as an indented span tree, first ERROR span flagged."""
    by_trace: dict[int, list[ReadableSpan]] = defaultdict(list)
    for span in spans:
        by_trace[span.context.trace_id].append(span)

    lines: list[str] = [f"{len(by_trace)} trace(s), {len(spans)} span(s)"]
    for trace_spans in sorted(by_trace.values(), key=lambda s: min(x.start_time or 0 for x in s)):
        ids = {span.context.span_id for span in trace_spans}
        roots = [s for s in trace_spans if s.parent is None or s.parent.span_id not in ids]
        case = (roots[0].attributes or {}).get("test.case_id", "n/a") if roots else "n/a"
        lines.append(f"trace {hex_trace_id(trace_spans[0])} test.case_id={case}")
        for root in sorted(roots, key=lambda s: s.start_time or 0):
            _render(trace_spans, root, 1, lines)
    return "\n".join(lines)


def _render(spans: Sequence[ReadableSpan], span: ReadableSpan, depth: int, out: list[str]) -> None:
    marker = "!! " if span.status.status_code is StatusCode.ERROR else ""
    out.append("  " * depth + marker + describe(span))
    for event in span.events:
        if event.name == "exception" and event.attributes:
            out.append(
                "  " * (depth + 1)
                + f"exception {event.attributes.get('exception.type')}: "
                + f"{event.attributes.get('exception.message')}"
            )
    for child in sorted(children_of(spans, span), key=lambda s: s.start_time or 0):
        _render(spans, child, depth + 1, out)


def metric_points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict[str, Any], Any]]:
    """(attributes, value) of every data point of a metric; value is sum or count."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    points = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    value = getattr(point, "value", None)
                    if value is None:
                        value = getattr(point, "count", None)
                    points.append((dict(point.attributes or {}), value))
    return points


def run_concurrently(tasks: Sequence[Callable[[], Any]]) -> list[Any]:
    """Run tasks in parallel threads released together; re-raise the first error.

    Each thread runs in a copy of the caller's context, so the test baggage
    (test.case_id) follows the work into the thread.
    """
    barrier = threading.Barrier(len(tasks))
    results: list[Any] = [None] * len(tasks)
    errors: list[BaseException] = []

    def make_worker(index: int, task: Callable[[], Any]) -> Callable[[], None]:
        ctx = contextvars.copy_context()

        def worker() -> None:
            barrier.wait()
            try:
                results[index] = ctx.run(task)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
                errors.append(exc)

        return worker

    threads = [threading.Thread(target=make_worker(i, t)) for i, t in enumerate(tasks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return results
