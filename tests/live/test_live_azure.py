"""Live tests against the real Azure model. Excluded by default (marker ``live``).

Run with:  make test-live   (loads .env; each run costs a few model calls)

The model is not deterministic: a case declares its repetitions and its pass
threshold instead of a boolean (note de diagnostic, point 4, § 9).
"""
from __future__ import annotations

import os

import pytest
from tracing_support import describe, spans_named

from mardik.app import build_agent
from mardik.config import load_settings
from mardik.runner import load_session, replay
from mardik.session import SessionStore

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("AZURE_AI_ENDPOINT") and os.environ.get("AZURE_AI_API_KEY")),
        reason="AZURE_AI_ENDPOINT and AZURE_AI_API_KEY are not set",
    ),
]

RUNS = 3
REQUIRED = 2


def test_real_model_looks_the_order_up_from_the_history(telemetry, span_exporter):
    """Replaying replay_delivery, the real model must call lookup_order for #1042."""
    agent = build_agent(telemetry=telemetry, settings=load_settings())
    successes, failures = 0, []
    for _ in range(RUNS):
        span_exporter.clear()
        result = replay(load_session("replay_delivery"), agent, SessionStore())
        tools = spans_named(span_exporter.get_finished_spans(), "tool.call")
        called = [t.attributes.get("gen_ai.tool.name") for t in tools]
        args = [t.attributes.get("mardik.tool.arguments", "") for t in tools]
        if called == ["lookup_order"] and "1042" in args[0] and "expédiée" in result.reply:
            successes += 1
        else:
            failures.append(f"tools={called} args={args} reply={result.reply[:120]!r}")

    assert successes >= REQUIRED, f"{successes}/{RUNS} runs passed; failures: {failures}"

    (llm,) = spans_named(span_exporter.get_finished_spans(), "llm.invoke")
    assert llm.attributes.get("gen_ai.usage.input_tokens", 0) > 0, describe(llm)
    assert llm.attributes.get("gen_ai.usage.output_tokens", 0) > 0, describe(llm)
