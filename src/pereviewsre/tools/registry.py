"""Tool registry exposed to the LLM. Every tool is read-only and returns JSON-safe data."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pereviewsre.integrations.app_insights import AppInsightsClient
from pereviewsre.logging_setup import get_logger
from pereviewsre.security.kql_guard import KqlRejected
from pereviewsre.security.redaction import redact_text

SCHEMA_HINT = (
    "Tables: requests(timestamp,name,url,success,resultCode,duration,operation_Id,id,"
    "cloud_RoleName), dependencies(timestamp,name,type,target,data,success,resultCode,"
    "duration,operation_Id,id,operation_ParentId,cloud_RoleName), exceptions(timestamp,type,"
    "outerMessage,innermostMessage,problemId,details,operation_Id,cloud_RoleName), "
    "traces(timestamp,message,severityLevel,operation_Id,cloud_RoleName). "
    "duration is in milliseconds."
)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _build_tools(ai: AppInsightsClient) -> dict[str, Tool]:
    tools = [
        Tool(
            "run_kql",
            "Run a read-only KQL query on Application Insights. " + SCHEMA_HINT,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "KQL query, no management cmds"},
                    "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 720},
                },
                "required": ["query"],
            },
            lambda query, lookback_hours=None: ai.run_kql(query, lookback_hours),
        ),
        Tool(
            "get_trace",
            "Fetch the full correlated trace (requests, dependencies, exceptions, traces) for "
            "one operation_Id, ordered by time.",
            {
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 720},
                },
                "required": ["operation_id"],
            },
            lambda operation_id, lookback_hours=None: ai.get_trace(operation_id, lookback_hours),
        ),
    ]
    return {t.name: t for t in tools}


class ToolRegistry:
    """Holds the read-only tools and executes them with logging + redaction."""

    def __init__(self, ai: AppInsightsClient) -> None:
        self._log = get_logger(__name__)
        self._tools = _build_tools(ai)
        self._log.info("Tool registry ready tools=%s", list(self._tools))

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def execute(self, name: str, raw_args: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            self._log.warning("Unknown tool requested name=%s", name)
            return json.dumps({"error": f"Unknown tool {name}"})
        self._log.info("Executing tool=%s args=%s", name, raw_args)
        try:
            args = json.loads(raw_args or "{}")
            result = tool.handler(**args)
        except KqlRejected as exc:
            self._log.warning("Tool=%s rejected by security policy: %s", name, exc)
            result = {"error": f"Rejected by security policy: {exc}"}
        except (TypeError, json.JSONDecodeError) as exc:
            self._log.warning("Tool=%s bad arguments: %s", name, exc)
            result = {"error": f"Bad arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001 - agent only sees a safe message
            self._log.exception("Tool=%s failed", name)  # full detail goes to the (redacted) log
            result = {"error": f"Tool failed: {type(exc).__name__}"}
        # Final safety net: redact everything that is about to enter the LLM context.
        text = redact_text(json.dumps(result, default=str))
        self._log.info(
            "Tool=%s done result_bytes=%d error=%s",
            name,
            len(text),
            isinstance(result, dict) and "error" in result,
        )
        return text[:60000]  # cap context size
