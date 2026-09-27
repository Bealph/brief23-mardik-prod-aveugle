"""The production assembly, checked offline: no call leaves the machine.

build_agent() without an llm builds the real Azure client. These tests check
what it is wired with, and how a timeout of the real SDK surfaces.
"""
from __future__ import annotations

import socket
from dataclasses import replace

import pytest
from azure.core.exceptions import ServiceResponseTimeoutError
from opentelemetry.trace import StatusCode
from tracing_support import describe, spans_named

from mardik.app import build_agent
from mardik.errors import LLMTimeoutError
from mardik.llm import AzureLLM
from mardik.runner import load_session, replay
from mardik.session import SessionStore
from mardik.tools import DEFAULT_TOOLS


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any socket connection fails the test: these checks must stay offline."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted during an offline wiring test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def v1_settings(settings):
    return replace(
        settings,
        azure_endpoint="https://example.services.ai.azure.com/openai/v1",
        azure_api_key="not-a-real-key",
        azure_model="gpt-5.4-mini",
    )


def test_production_llm_is_given_the_agent_tools(v1_settings, telemetry):
    """INC-06: without bound tools the model can never ask for lookup_order."""
    agent = build_agent(telemetry=telemetry, settings=v1_settings)

    assert isinstance(agent.llm, AzureLLM)
    bound = agent.llm._model
    tools = [t["function"]["name"] for t in bound.kwargs.get("tools", [])]
    assert sorted(tools) == sorted(DEFAULT_TOOLS)


def test_production_llm_accepts_the_openai_v1_route(v1_settings, telemetry):
    """The /openai/v1 route rejects the client's dated api-version (HTTP 400)."""
    model = build_agent(telemetry=telemetry, settings=v1_settings).llm._model.bound
    # INC-07: without it the service answers "Missed model deployment".
    assert model.model_name == v1_settings.azure_model
    assert model.api_version == "preview"
    assert model.client_kwargs["read_timeout"] == v1_settings.llm_timeout_s
    assert model.client_kwargs["retry_total"] == v1_settings.llm_max_retries


def test_injected_model_is_not_reported_as_the_deployment(make_agent, fake_llm, span_exporter):
    """A scripted model must not appear in traces as the configured Azure deployment."""
    replay(load_session("replay_delivery"), make_agent(fake_llm), SessionStore())

    (llm,) = spans_named(span_exporter.get_finished_spans(), "llm.invoke")
    assert "gen_ai.request.model" not in llm.attributes, describe(llm)


def test_built_model_is_reported_on_the_spans(v1_settings, telemetry):
    """When build_agent builds the Azure client, the spans name its deployment."""
    agent = build_agent(telemetry=telemetry, settings=v1_settings)
    assert agent.model_name == v1_settings.azure_model


class _SdkTimeoutModel:
    """Raises what azure-core raises on a read timeout (observed on the real service)."""

    def invoke(self, messages):
        raise ServiceResponseTimeoutError("Read timed out. (read timeout=30)")


def test_azure_sdk_timeout_surfaces_as_llm_timeout(make_agent, span_exporter):
    """INC-02, real SDK: ServiceResponseTimeoutError is not a TimeoutError."""
    agent = make_agent(AzureLLM(_SdkTimeoutModel()))

    with pytest.raises(LLMTimeoutError) as raised:
        replay(load_session("incident_timeout"), agent, SessionStore())

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert isinstance(raised.value.__cause__.__cause__, ServiceResponseTimeoutError)
    (llm,) = spans_named(span_exporter.get_finished_spans(), "llm.invoke")
    assert llm.status.status_code is StatusCode.ERROR, describe(llm)
    assert llm.attributes["error.type"] == "timeout"
