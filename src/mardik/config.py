"""Runtime configuration loaded from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    azure_endpoint: str
    azure_api_key: str
    azure_model: str
    otel_endpoint: str
    service_name: str
    log_level: str
    app_env: str = "development"
    # console (default), otlp (needs a collector that accepts metrics) or none.
    metrics_exporter: str = "console"
    # Record prompts, tool arguments and replies on spans (personal data risk).
    trace_content: bool = False
    # Per-read deadline of an LLM call, in seconds, and retries on failure.
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2


def load_settings() -> Settings:
    return Settings(
        azure_endpoint=os.environ.get("AZURE_AI_ENDPOINT", ""),
        azure_api_key=os.environ.get("AZURE_AI_API_KEY", ""),
        azure_model=os.environ.get("AZURE_AI_MODEL", "Kimi-K2.6"),
        otel_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "mardik"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        app_env=os.environ.get("APP_ENV", "development"),
        metrics_exporter=os.environ.get("MARDIK_METRICS_EXPORTER", "console").strip().lower(),
        trace_content=env_flag("MARDIK_TRACE_CONTENT"),
        llm_timeout_s=float(os.environ.get("MARDIK_LLM_TIMEOUT_S", "30")),
        llm_max_retries=int(os.environ.get("MARDIK_LLM_MAX_RETRIES", "2")),
    )
