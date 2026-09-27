"""Observability primitives: structured logging, tracing and metrics.

The :class:`Telemetry` object bundles a tracer, a logger and the metric
instruments the agent emits. It is injectable so that tests can wire in-memory
exporters and inspect what was recorded; production wiring lives in
:func:`build_default_telemetry`.

The span names, attributes and metrics emitted here are specified in
``docs/schema-observabilite.md``. Keep both in sync.
"""
from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import baggage, trace
from opentelemetry.context import Context
from opentelemetry.metrics import Meter, NoOpMeter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import NoOpTracer, Tracer

from . import __version__
from .config import Settings, env_flag

# Prompts, tool arguments and replies are only recorded on spans when this
# variable is truthy: they may contain personal data (see the schema, § 7).
CONTENT_ENV_VAR = "MARDIK_TRACE_CONTENT"
MAX_CONTENT_CHARS = 1000

# Baggage entries with this prefix are copied onto every span (test context).
TEST_BAGGAGE_PREFIX = "test."


def truncate(text: str) -> str:
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[:MAX_CONTENT_CHARS] + "…[truncated]"


def add_trace_context(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: stamp each log line with the active trace and span ids."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit structured JSON lines."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            add_trace_context,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        cache_logger_on_first_use=False,
    )


class BaggageTestContextProcessor(SpanProcessor):
    """Copy ``test.*`` baggage entries onto every span started in that context.

    An integration test puts ``test.case_id`` and ``test.run_id`` in the
    baggage; every span the agent emits then names the test that caused it,
    while each turn keeps its own trace.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for key, value in baggage.get_all(parent_context).items():
            if key.startswith(TEST_BAGGAGE_PREFIX):
                span.set_attribute(key, str(value))


class _Instruments:
    """Metric instruments shared by Telemetry and NoOpTelemetry."""

    def __init__(self, meter: Meter) -> None:
        self.latency_ms = meter.create_histogram(
            "latency_ms",
            unit="ms",
            description="End-to-end latency of an agent turn.",
        )
        self.llm_latency_ms = meter.create_histogram(
            "llm_latency_ms",
            unit="ms",
            description="Latency of a single LLM invocation.",
        )
        self.tool_latency_ms = meter.create_histogram(
            "tool_latency_ms",
            unit="ms",
            description="Latency of a single tool execution.",
        )
        self.turns = meter.create_counter(
            "turns_total",
            description="Agent turns, by outcome (completed, empty, error).",
        )
        self.errors = meter.create_counter(
            "errors_total",
            description="Count of agent turns that ended in an error, by error type.",
        )
        self.tool_calls = meter.create_counter(
            "tool_calls_total",
            description="Tool executions, by tool and status.",
        )
        self.context_messages = meter.create_histogram(
            "context_messages",
            unit="{message}",
            description="Number of messages sent to the model for one turn.",
        )


class Telemetry(_Instruments):
    """Bundle of tracer + logger + metric instruments used by the agent."""

    def __init__(
        self,
        tracer: Tracer,
        meter: Meter,
        capture_content: bool = False,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> None:
        self.tracer = tracer
        self.logger = structlog.get_logger("mardik")
        self.capture_content = capture_content
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        super().__init__(meter)

    def record_latency(self, value_ms: float, **attributes: str) -> None:
        self.latency_ms.record(value_ms, attributes=attributes)

    def shutdown(self) -> None:
        """Flush pending spans and metrics; call before the process exits."""
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()
        if self._meter_provider is not None:
            self._meter_provider.shutdown()


def build_telemetry(
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
    level: str = "INFO",
    resource: Resource | None = None,
    capture_content: bool | None = None,
    batch: bool = False,
) -> Telemetry:
    """Build a self-contained Telemetry bundle.

    Defaults to console exporters; tests pass in-memory exporters/readers.
    """
    configure_logging(level)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BaggageTestContextProcessor())
    exporter = span_exporter or ConsoleSpanExporter()
    tracer_provider.add_span_processor(
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    )

    reader = metric_reader or PeriodicExportingMetricReader(ConsoleMetricExporter())
    meter_provider = MeterProvider(metric_readers=[reader], resource=resource)

    return Telemetry(
        tracer=tracer_provider.get_tracer("mardik", __version__),
        meter=meter_provider.get_meter("mardik", __version__),
        capture_content=(
            env_flag(CONTENT_ENV_VAR) if capture_content is None else capture_content
        ),
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


def _metric_reader_from_settings(settings: Settings) -> MetricReader | None:
    if settings.metrics_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        return PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=settings.otel_endpoint)
        )
    if settings.metrics_exporter == "none":
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        return InMemoryMetricReader()
    return PeriodicExportingMetricReader(ConsoleMetricExporter())


def build_default_telemetry(
    settings: Settings | None = None, level: str | None = None
) -> Telemetry:
    """Production wiring: OTLP/gRPC span export to the collector + periodic metrics."""
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    from .config import load_settings

    settings = settings or load_settings()
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": __version__,
            "deployment.environment.name": settings.app_env,
        }
    )
    return build_telemetry(
        span_exporter=OTLPSpanExporter(endpoint=settings.otel_endpoint),
        metric_reader=_metric_reader_from_settings(settings),
        level=level or settings.log_level,
        resource=resource,
        capture_content=settings.trace_content,
        batch=True,
    )


class NoOpTelemetry(_Instruments):
    """Telemetry that records nothing — used when observability is not wired.

    Deliberately not a :class:`Telemetry`, so wiring tests can tell them apart.
    """

    def __init__(self) -> None:
        self.tracer = NoOpTracer()
        self.logger = structlog.get_logger("mardik")
        self.capture_content = False
        super().__init__(NoOpMeter("mardik"))

    def record_latency(self, value_ms: float, **attributes: str) -> None:
        pass

    def shutdown(self) -> None:
        pass
