# azsre – AI SRE for Azure Application Insights

Ask a question in plain English; the agent writes KQL, pulls the exact correlated traces
(`operation_Id`), and returns a root-cause analysis. Modeled on the OpenSRE pattern
(agent loop + tool registry + integrations + guardrails).

## Layout
```
src/azsre/
  config.py            typed settings from env (no secrets in code)
  agent/               system prompt + tool-calling loop (Azure OpenAI)
  tools/registry.py    read-only tools: run_kql, get_trace
  integrations/        Application Insights client (Entra ID auth)
  security/            kql_guard (read-only policy), redaction (secrets/PII)
  web/                 FastAPI + chat UI
  cli.py               azsre ask | chat | web
tests/                 guardrail tests
```

## Prerequisites
- Python 3.11+ (`python --version`)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (`az --version`)
- An Azure Application Insights resource and an Azure OpenAI chat deployment
- Azure roles for your signed-in identity:
  - **Monitoring Reader** (or Log Analytics Reader) on the App Insights resource
  - **Cognitive Services OpenAI User** on the Azure OpenAI resource

## Install dependencies
Run these from the project root (Windows PowerShell). The virtualenv is named `opensre`.

```powershell
# 1. Create and activate the virtual environment
python -m venv opensre
.\opensre\Scripts\Activate.ps1

# 2. Install the project and its dependencies (from pyproject.toml)
pip install -e ".[dev]"

# If your network is slow / PyPI times out:
pip install --default-timeout 120 --retries 10 -e ".[dev]"
```

On macOS/Linux, activate with `source opensre/bin/activate` instead.

Dependencies installed: `azure-identity`, `azure-monitor-query`, `openai`, `pydantic`,
`pydantic-settings`, `fastapi`, `uvicorn`, `rich` (plus `pytest`, `ruff` for dev).

## Configure
```powershell
copy .env.example .env
```
Edit `.env` and set at least:

| Variable | Description |
| --- | --- |
| `AZSRE_APPINSIGHTS_RESOURCE_ID` | Full ARM resource ID of the App Insights component |
| `AZSRE_AOAI_ENDPOINT` | e.g. `https://<name>.openai.azure.com/` |
| `AZSRE_AOAI_DEPLOYMENT` | Your chat model deployment name |
| `AZSRE_AOAI_API_KEY` | Optional. Leave empty for keyless Entra ID auth |

Find the resource ID with:
```powershell
az monitor app-insights component show --app <name> -g <resource-group> --query id -o tsv
```

Then sign in to Azure:
```powershell
az login
```

## Run
```powershell
# Run the tests
pytest

# One-shot question
azsre ask "Why are checkouts failing in the last hour?"

# Interactive chat in the terminal (type 'exit' to quit)
azsre chat

# Web chat UI at http://127.0.0.1:8080
azsre web
```

Without installing the script entry point you can also run `python -m azsre.cli ask "..."`.

## Logs
Every component (agent, tool registry, App Insights client, CLI, web service) writes to a
shared rotating log file at `logs/azsre.log` (also echoed to the console). Each line is
passed through the redaction filter, including tracebacks.

```powershell
Get-Content .\logs\azsre.log -Wait -Tail 50   # follow logs live
```

What is logged: questions (redacted), LLM steps and token usage, tool names and arguments,
KQL queries, row/column counts, rejected queries and errors. Telemetry row contents are
**never** logged. Set `AZSRE_LOG_LEVEL=DEBUG` and `AZSRE_LOG_DIR` in `.env` to tune.

## Troubleshooting
- **`Missing required settings`**: fill the required values in `.env`.
- **401/403 from Azure**: re-run `az login` and check the role assignments above.
- **`azsre` not found**: activate the virtualenv first.
- **pip read timeout**: use the `--default-timeout 120 --retries 10` install command above.

See [SECURITY.md](SECURITY.md) for the security model and
[docs/DEPLOY_AZURE_FUNCTIONS.md](docs/DEPLOY_AZURE_FUNCTIONS.md) to run it in Azure Functions.
