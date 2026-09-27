"""Application wiring: build a ready-to-run agent and a CLI entrypoint."""
from __future__ import annotations

from typing import Any

from .agent import Agent
from .config import Settings, load_settings
from .session import SessionStore
from .tools import DEFAULT_TOOLS


def build_agent(
    llm: Any | None = None,
    telemetry: Any | None = None,
    settings: Settings | None = None,
) -> Agent:
    """Assemble the production agent from configuration.

    INC-05: the telemetry argument used to be ignored, so the production agent
    always ran with NoOpTelemetry and emitted nothing.
    """
    settings = settings or load_settings()
    if llm is None:
        from .llm import get_llm

        llm = get_llm(settings, tools=DEFAULT_TOOLS)
    if telemetry is None:
        from .telemetry import build_default_telemetry

        telemetry = build_default_telemetry(settings)
    return Agent(
        llm=llm,
        tools=DEFAULT_TOOLS,
        telemetry=telemetry,
        model_name=settings.azure_model,
    )


def main() -> None:
    settings = load_settings()
    agent = build_agent(settings=settings)
    store = SessionStore()
    try:
        result = agent.run_turn(store, "cli", "Bonjour")
        print(result.reply)
    finally:
        agent.telemetry.shutdown()


if __name__ == "__main__":
    main()
