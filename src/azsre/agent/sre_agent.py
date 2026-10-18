"""Tool-calling investigation loop on Azure OpenAI."""

from collections.abc import Callable
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from azsre.agent.prompts import SYSTEM_PROMPT
from azsre.config import Settings
from azsre.integrations.app_insights import AppInsightsClient
from azsre.logging_setup import get_logger
from azsre.security.redaction import redact_text
from azsre.tools.registry import ToolRegistry

EventCb = Callable[[str, dict[str, Any]], None]


def _make_llm(s: Settings) -> AzureOpenAI:
    key = s.aoai_api_key.get_secret_value()
    if key:
        return AzureOpenAI(
            azure_endpoint=s.aoai_endpoint, api_key=key, api_version=s.aoai_api_version
        )
    provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=s.aoai_endpoint,
        azure_ad_token_provider=provider,
        api_version=s.aoai_api_version,
    )


class SreAgent:
    def __init__(self, settings: Settings) -> None:
        self._log = get_logger(__name__)
        settings.validate_required()
        self._s = settings
        self._llm = _make_llm(settings)
        self._tools = ToolRegistry(AppInsightsClient(settings))
        self._log.info(
            "Agent ready deployment=%s max_steps=%d", settings.aoai_deployment, settings.max_agent_steps
        )

    def investigate(self, question: str, on_event: EventCb | None = None) -> str:
        emit = on_event or (lambda *_: None)
        # Users may paste secrets into questions: redact before it leaves the process.
        question = redact_text(question)
        self._log.info("Investigation started question=%r", question[:500])
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        specs = self._tools.specs()

        for step in range(1, self._s.max_agent_steps + 1):
            self._log.info("LLM call step=%d/%d", step, self._s.max_agent_steps)
            resp = self._llm.chat.completions.create(
                model=self._s.aoai_deployment,
                messages=messages,
                tools=specs,
                temperature=0,
            )
            msg = resp.choices[0].message
            usage = resp.usage
            if usage:
                self._log.info(
                    "LLM usage prompt_tokens=%d completion_tokens=%d",
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )
            if not msg.tool_calls:
                self._log.info("Investigation finished steps=%d", step)
                return redact_text(msg.content or "")

            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                args = redact_text(call.function.arguments or "")
                emit("tool_call", {"name": call.function.name, "args": args})
                result = self._tools.execute(call.function.name, call.function.arguments)
                emit("tool_result", {"name": call.function.name, "size": len(result)})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        self._log.warning("Step budget exhausted; forcing final answer")
        # Step budget exhausted: force a final answer without tools.
        messages.append(
            {"role": "user", "content": "Step budget reached. Give your best final analysis now."}
        )
        final = self._llm.chat.completions.create(
            model=self._s.aoai_deployment, messages=messages, temperature=0
        )
        return redact_text(final.choices[0].message.content or "")
