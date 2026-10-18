"""Azure Application Insights integration (read-only, via Entra ID)."""

from datetime import timedelta
from typing import Any

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from azsre.config import Settings
from azsre.logging_setup import get_logger
from azsre.security.kql_guard import KqlRejected, sanitize_kql, validate_operation_id
from azsre.security.redaction import redact_value

TRACE_KQL = """
let opId = '{op_id}';
union requests, dependencies, exceptions, traces
| where operation_Id == opId
| extend kind = itemType
| project timestamp, kind, name = coalesce(name, type, tostring(severityLevel)),
          id, operation_ParentId, duration, success, resultCode,
          target, data, message = coalesce(message, outerMessage), problemId,
          details = tostring(details), cloud_RoleName, cloud_RoleInstance
| order by timestamp asc
"""


class AppInsightsClient:
    def __init__(self, settings: Settings) -> None:
        self._log = get_logger(__name__)
        self._settings = settings
        self._client = LogsQueryClient(DefaultAzureCredential())
        self._log.info("App Insights client ready (Entra ID credential)")

    def run_kql(self, query: str, lookback_hours: int | None = None) -> dict[str, Any]:
        """Run a validated KQL query and return redacted, size-capped rows."""
        s = self._settings
        try:
            safe = sanitize_kql(query, s.max_rows)
        except KqlRejected as exc:
            self._log.warning("KQL rejected reason=%s query=%r", exc, query[:500])
            raise
        hours = lookback_hours or s.default_lookback_hours
        self._log.info("Running KQL lookback_h=%d query=%r", hours, safe[:1000])
        try:
            resp = self._client.query_resource(
                s.appinsights_resource_id,
                safe,
                timespan=timedelta(hours=hours),
                server_timeout=s.query_timeout_s,
            )
        except HttpResponseError as exc:
            self._log.error("KQL query failed status=%s msg=%s", exc.status_code, exc.message)
            return {"error": f"Query failed: {exc.message}", "query": safe}

        if resp.status == LogsQueryStatus.PARTIAL:
            table = resp.partial_data[0] if resp.partial_data else None
            note = "partial results"
        else:
            table = resp.tables[0] if resp.tables else None
            note = None
        if table is None:
            self._log.info("KQL returned no table")
            return {"query": safe, "columns": [], "rows": [], "row_count": 0}

        rows = [
            {c: _jsonable(v) for c, v in zip(table.columns, row)} for row in table.rows
        ]
        out: dict[str, Any] = {
            "query": safe,
            "columns": list(table.columns),
            "row_count": len(rows),
            "rows": redact_value(rows),
        }
        if note:
            out["note"] = note
        # Log shape only; never log row contents (telemetry may hold sensitive data).
        self._log.info("KQL ok rows=%d columns=%d note=%s", len(rows), len(table.columns), note)
        return out

    def get_trace(self, operation_id: str, lookback_hours: int | None = None) -> dict[str, Any]:
        op_id = validate_operation_id(operation_id)
        self._log.info("Fetching trace operation_Id=%s", op_id)
        return self.run_kql(TRACE_KQL.format(op_id=op_id), lookback_hours)


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
