"""Integration fixtures: agents assembled through the production wiring."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mardik.agent import Agent
from mardik.app import build_agent
from mardik.config import Settings
from mardik.telemetry import Telemetry


@pytest.fixture
def settings() -> Settings:
    return Settings(
        azure_endpoint="",
        azure_api_key="",
        azure_model="Kimi-K2.6",
        otel_endpoint="http://localhost:4317",
        service_name="mardik-test",
        log_level="INFO",
        app_env="test",
    )


@pytest.fixture
def make_agent(telemetry: Telemetry, settings: Settings) -> Callable[[Any], Agent]:
    """Build an agent with build_agent(), the same path as production, around a
    scripted LLM and the in-memory telemetry."""

    def factory(llm: Any) -> Agent:
        return build_agent(llm=llm, telemetry=telemetry, settings=settings)

    return factory
