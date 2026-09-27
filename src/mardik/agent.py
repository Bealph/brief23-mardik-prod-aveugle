"""The Mardik agent: turns a user message into a reply, calling tools as needed."""
from __future__ import annotations

import contextvars
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from opentelemetry.trace import Status, StatusCode

from .errors import LLMTimeoutError, ToolExecutionError
from .session import SessionStore
from .telemetry import NoOpTelemetry, truncate


@dataclass
class Reply:
    content: str
    tool_calls: list[dict[str, Any]]


@dataclass
class TurnResult:
    session_id: str
    reply: str


class LLM(Protocol):
    def invoke(self, messages: list[dict[str, Any]]) -> Reply: ...


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: dict[str, Callable[..., str]],
        telemetry: Any | None = None,
        model_name: str | None = None,
    ) -> None:
        self.llm = llm
        self._tools = tools
        self.telemetry = telemetry if telemetry is not None else NoOpTelemetry()
        self.model_name = model_name

    def _invoke_llm_sync(self, messages: list[dict[str, Any]]) -> Reply:
        with self.telemetry.tracer.start_as_current_span("llm.invoke") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            if self.model_name:
                span.set_attribute("gen_ai.request.model", self.model_name)
            span.set_attribute("mardik.llm.input_messages", len(messages))
            start = time.perf_counter()
            try:
                reply = self.llm.invoke(messages)
            except TimeoutError as exc:
                # INC-02: the timeout used to be swallowed and turned into None.
                span.set_attribute("error.type", "timeout")
                raise LLMTimeoutError("LLM invocation exceeded its deadline") from exc
            finally:
                self.telemetry.llm_latency_ms.record(_elapsed_ms(start))
            span.set_attribute(
                "mardik.llm.tool_calls_requested",
                [str(call.get("name")) for call in reply.tool_calls],
            )
            if self.telemetry.capture_content:
                span.set_attribute("mardik.llm.completion", truncate(str(reply.content)))
            span.set_status(Status(StatusCode.OK))
            return reply

    def _invoke_llm(self, messages: list[dict[str, Any]]) -> Reply:
        # The Azure SDK call is blocking, so run it on a worker thread.
        # INC-04: copy_context() carries the active span into the worker, so
        # llm.invoke stays in the turn's trace. INC-02: the worker's exception
        # is re-raised here instead of being lost with the thread.
        ctx = contextvars.copy_context()
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["reply"] = ctx.run(self._invoke_llm_sync, messages)
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(target=worker, name="mardik-llm")
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box["reply"]

    def _dispatch_tool(self, call: dict[str, Any]) -> str:
        name = str(call.get("name"))
        args = call.get("args") or {}
        tool = self._tools.get(name)
        # Metric labels must stay bounded: an unknown name is not a label value.
        tool_label = name if tool is not None else "unknown"
        with self.telemetry.tracer.start_as_current_span("tool.call") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            if self.telemetry.capture_content:
                span.set_attribute(
                    "mardik.tool.arguments", truncate(json.dumps(args, ensure_ascii=False))
                )
            start = time.perf_counter()
            status = "error"
            try:
                if tool is None:
                    span.set_attribute("error.type", "unknown_tool")
                    raise ToolExecutionError(f"unknown tool requested by the model: {name!r}")
                try:
                    result = tool(**args)
                except Exception as exc:
                    span.set_attribute("error.type", "tool_failure")
                    raise ToolExecutionError(f"tool {name!r} failed: {exc}") from exc
                status = "ok"
            finally:
                span.set_attribute("mardik.tool.status", status)
                self.telemetry.tool_calls.add(1, {"tool": tool_label, "status": status})
                self.telemetry.tool_latency_ms.record(_elapsed_ms(start), {"tool": tool_label})
            if self.telemetry.capture_content:
                span.set_attribute("mardik.tool.result", truncate(result))
            span.set_status(Status(StatusCode.OK))
            return result

    def run_turn(
        self, store: SessionStore, session_id: str, user_message: str
    ) -> TurnResult:
        telemetry = self.telemetry
        with telemetry.tracer.start_as_current_span("agent.turn") as span:
            start = time.perf_counter()
            span.set_attribute("mardik.session.id", session_id)
            outcome = "error"
            turn_index = 0
            try:
                store.append(session_id, {"role": "user", "content": user_message})
                turn_index = store.record_turn(session_id)
                span.set_attribute("mardik.turn.index", turn_index)
                history = store.history(session_id)
                span.set_attribute("mardik.context.messages", len(history))
                telemetry.context_messages.record(len(history))
                if telemetry.capture_content:
                    span.set_attribute("mardik.turn.user_message", truncate(user_message))

                reply = self._invoke_llm(history)

                text = reply.content
                if reply.tool_calls:
                    # Keep every tool result: the last one used to overwrite the others.
                    text = "\n".join(self._dispatch_tool(call) for call in reply.tool_calls)

                store.append(session_id, {"role": "assistant", "content": text})
                outcome = "completed" if text.strip() else "empty"
                # Status follows the business outcome, not the absence of exception.
                if outcome == "empty":
                    span.set_status(Status(StatusCode.ERROR, "empty reply"))
                else:
                    span.set_status(Status(StatusCode.OK))
                if telemetry.capture_content:
                    span.set_attribute("mardik.turn.reply", truncate(text))
                return TurnResult(session_id=session_id, reply=text)
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                telemetry.errors.add(1, {"error.type": type(exc).__name__})
                telemetry.logger.error(
                    "turn.failed",
                    session_id=session_id,
                    turn_index=turn_index,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    duration_ms=round(_elapsed_ms(start), 1),
                )
                raise
            finally:
                elapsed_ms = _elapsed_ms(start)
                span.set_attribute("mardik.turn.outcome", outcome)
                telemetry.record_latency(elapsed_ms, outcome=outcome)
                telemetry.turns.add(1, {"outcome": outcome})
                if outcome != "error":
                    log = telemetry.logger.info if outcome == "completed" else telemetry.logger.warning
                    log(
                        "turn.completed",
                        session_id=session_id,
                        turn_index=turn_index,
                        outcome=outcome,
                        duration_ms=round(elapsed_ms, 1),
                    )
