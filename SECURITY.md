# Security model

- **Read-only by design**: only `run_kql` / `get_trace` tools; no write or remediation tools.
- **KQL guard** (`security/kql_guard.py`): table allow-list, blocks management commands,
  `externaldata`, `evaluate`, plugins, and cross-workspace/cluster references; enforces a row cap.
- **Redaction** (`security/redaction.py`): tokens, JWTs, passwords, keys, emails and card-like
  numbers are masked in telemetry before reaching the LLM and in final answers.
- **Prompt-injection stance**: telemetry is treated as untrusted data; the system prompt forbids
  following instructions found in tool output, and tools cannot take destructive actions.
- **Auth**: Entra ID via `DefaultAzureCredential`; no keys stored. Use least privilege
  (Monitoring Reader / Log Analytics Reader on the one App Insights resource).
- **Secrets**: only via environment / `.env` (git-ignored). `SecretStr` prevents accidental logging.
- **Web**: binds to 127.0.0.1 by default, optional constant-time bearer token
  (`PEREVIEWSRE_WEB_TOKEN`), no OpenAPI docs exposed, CSP header, UI renders text only.
  Put behind TLS + SSO (e.g. Azure Container Apps auth) before exposing beyond localhost.
- **Limits**: query timeout, max rows, max agent steps, tool output size cap.
