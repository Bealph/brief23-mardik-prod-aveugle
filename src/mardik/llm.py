"""Factory for the production LLM client (Azure AI Inference)."""
from __future__ import annotations

from typing import Any, Callable

from .config import Settings

# Azure OpenAI deployments are served under this route. Observed (not documented):
# it answers HTTP 400 "API version not supported" to the client's default api-version.
OPENAI_V1_ROUTE = "/openai/v1"


class AzureLLM:
    """Adapter between the agent and the Azure chat model.

    Maps the Azure SDK timeouts, which do not inherit from the builtin
    ``TimeoutError``, to ``TimeoutError`` so the agent reports them as
    ``LLMTimeoutError`` (incident INC-02, as observed against the real service).
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def invoke(self, messages: list[dict[str, Any]]) -> Any:
        from azure.core.exceptions import ServiceRequestTimeoutError, ServiceResponseTimeoutError

        try:
            return self._model.invoke(messages)
        except (ServiceRequestTimeoutError, ServiceResponseTimeoutError) as exc:
            raise TimeoutError(f"Azure SDK timeout: {type(exc).__name__}") from exc


def get_llm(settings: Settings, tools: dict[str, Callable[..., str]] | None = None) -> AzureLLM:
    """Build the Azure-hosted chat model used in production.

    The agent's tools are bound to the model: without it the model cannot
    request them and answers without looking anything up (incident INC-06).

    Imported lazily so the rest of the package does not require the Azure SDK
    to be installed for offline test runs.
    """
    from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel

    extra: dict[str, Any] = {}
    if settings.azure_endpoint.rstrip("/").endswith(OPENAI_V1_ROUTE):
        extra["api_version"] = "preview"
    model = AzureAIChatCompletionsModel(
        endpoint=settings.azure_endpoint,
        credential=settings.azure_api_key,
        # INC-07: the field is declared with the alias "model"; model_name= was silently
        # ignored, so every call went out without a deployment name.
        model=settings.azure_model,
        # azure-core defaults: 300 s per socket read, 10 retries (timeouts themselves are
        # not retried). read_timeout is per read, not a deadline for the whole call.
        client_kwargs={
            "read_timeout": settings.llm_timeout_s,
            "retry_total": settings.llm_max_retries,
        },
        **extra,
    )
    runnable = model.bind_tools(list(tools.values())) if tools else model
    return AzureLLM(runnable)
