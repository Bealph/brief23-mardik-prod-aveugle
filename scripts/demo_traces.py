"""Produce demonstration traces and open them: HTML viewer or Jaeger.

Replays the recorded sessions and the incidents through the production wiring
(build_agent + OTLP export) with scripted LLMs, so no Azure key is needed.
Content capture is forced on: spans show prompts, tool arguments and replies.
The recorded sessions are synthetic, so no personal data is exported.

Usage:
    uv run python scripts/demo_traces.py            # writes test-artifacts/trace-viewer.html
    make up                                          # (needs Docker) start Jaeger, then:
    uv run python scripts/demo_traces.py --jaeger   # open http://localhost:16686
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
os.environ.setdefault("MARDIK_SESSIONS_DIR", str(REPO / "sessions"))

from conftest import ContextAwareFakeLLM, TimeoutFakeLLM, UnknownToolFakeLLM  # noqa: E402
from tracing_support import baggage_test_context, run_concurrently  # noqa: E402

from mardik.app import build_agent  # noqa: E402
from mardik.config import load_settings  # noqa: E402
from mardik.errors import MardikError  # noqa: E402
from mardik.runner import list_sessions, load_session, replay, replay_turn_by_turn  # noqa: E402
from mardik.session import SessionStore  # noqa: E402
from mardik.telemetry import build_default_telemetry, build_telemetry  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from trace_viewer import write_html  # noqa: E402

VIEWER = REPO / "test-artifacts" / "trace-viewer.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jaeger", action="store_true", help="export over OTLP to Jaeger")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    options = parser.parse_args()

    settings = replace(load_settings(), trace_content=True, metrics_exporter="none")
    memory = InMemorySpanExporter()
    if options.jaeger:
        telemetry = build_default_telemetry(settings)
    else:
        telemetry = build_telemetry(
            span_exporter=memory,
            metric_reader=InMemoryMetricReader(),
            resource=Resource.create({"service.name": settings.service_name}),
            capture_content=True,
        )
    healthy = build_agent(llm=ContextAwareFakeLLM(), telemetry=telemetry, settings=settings)
    store = SessionStore()

    def scenario(case_id: str, action) -> None:
        with baggage_test_context(f"demo::{case_id}", "demo"):
            try:
                action()
                print(f"ok      {case_id}")
            except MardikError as exc:
                print(f"erreur  {case_id} : {type(exc).__name__} (attendu, voir la trace)")

    for name in list_sessions("replay_"):
        scenario(f"replay::{name}", lambda n=name: replay(load_session(n), healthy, store))

    scenario(
        "longitudinal::replay_clarification",
        lambda: replay_turn_by_turn(
            dict(load_session("replay_clarification"), session_id="demo-longitudinal"),
            healthy,
            store,
        ),
    )
    scenario(
        "transversal::4-sessions",
        lambda: run_concurrently(
            [
                lambda n=n: healthy.run_turn(store, f"demo-iso-{n}", f"Commande #{7000 + n} ?")
                for n in range(4)
            ]
        ),
    )
    for case_id, llm, session in (
        ("incident::timeout", TimeoutFakeLLM(), "incident_timeout"),
        ("incident::unknown_tool", UnknownToolFakeLLM(), "replay_delivery"),
    ):
        agent = build_agent(llm=llm, telemetry=telemetry, settings=settings)
        scenario(case_id, lambda a=agent, s=session: replay(load_session(s), a, SessionStore()))

    telemetry.shutdown()  # flush the batch processor before exiting
    if options.jaeger:
        print(f"\nTraces envoyées à {settings.otel_endpoint} (service « {settings.service_name} »).")
        print("Ouvrir http://localhost:16686, choisir le service, puis « Find Traces ».")
        return
    spans = memory.get_finished_spans()
    path = write_html(spans, VIEWER)
    print(f"\n{len(spans)} spans écrits dans {path}")
    if not options.no_open:
        webbrowser.open(path.as_uri())


if __name__ == "__main__":
    main()
