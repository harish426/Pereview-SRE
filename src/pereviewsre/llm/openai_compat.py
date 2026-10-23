"""OpenAI-style providers: Azure OpenAI, OpenAI, and any OpenAI-compatible server (Ollama, vLLM...)."""

from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI, OpenAI

from pereviewsre.config import Settings
from pereviewsre.llm.base import LLMProvider, LLMResponse, ToolCall


class _OpenAIStyleProvider(LLMProvider):
    """Shared request/response conversion for the OpenAI chat-completions API."""

    _client: Any
    _model: str

    def __init__(self, temperature: float) -> None:
        super().__init__()
        self._temperature = temperature

    @staticmethod
    def _to_openai(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append(
                    {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
                )
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def chat(self, system, messages, tools=None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai(system, messages),
        }
        if tools:
            kwargs["tools"] = tools
        if self._temperature >= 0:  # negative = omit (needed by some reasoning models)
            kwargs["temperature"] = self._temperature
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (msg.tool_calls or [])
        ]
        usage = resp.usage
        return LLMResponse(
            content=msg.content or "",
            tool_calls=calls,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )


class AzureOpenAIProvider(_OpenAIStyleProvider):
    name = "azure_openai"

    def __init__(self, s: Settings) -> None:
        super().__init__(s.llm_temperature)
        self._model = s.aoai_deployment  # Azure uses the *deployment name*
        key = s.aoai_api_key.get_secret_value()
        if key:
            self._client = AzureOpenAI(
                azure_endpoint=s.aoai_endpoint, api_key=key, api_version=s.aoai_api_version
            )
            auth = "api_key"
        else:
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
            self._client = AzureOpenAI(
                azure_endpoint=s.aoai_endpoint,
                azure_ad_token_provider=token_provider,
                api_version=s.aoai_api_version,
            )
            auth = "entra_id"
        self._log.info("Azure OpenAI ready deployment=%s auth=%s", self._model, auth)


class OpenAIProvider(_OpenAIStyleProvider):
    """OpenAI, or any OpenAI-compatible endpoint via PEREVIEWSRE_OPENAI_BASE_URL."""

    name = "openai"

    def __init__(self, s: Settings) -> None:
        super().__init__(s.llm_temperature)
        self._model = s.openai_model
        self._client = OpenAI(
            api_key=s.openai_api_key.get_secret_value() or "not-needed",
            base_url=s.openai_base_url or None,
        )
        self._log.info(
            "OpenAI ready model=%s base_url=%s", self._model, s.openai_base_url or "default"
        )
