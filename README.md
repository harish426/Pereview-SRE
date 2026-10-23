# pereviewsre – AI SRE for Azure Application Insights

Ask a question in plain English; the agent writes KQL, pulls the exact correlated traces
(`operation_Id`), and returns a root-cause analysis. Modeled on the OpenSRE pattern
(agent loop + tool registry + integrations + guardrails).

## Layout
```
src/pereviewsre/
  config.py            typed settings from env (no secrets in code)
  agent/               system prompt + tool-calling loop (Azure OpenAI)
  tools/registry.py    read-only tools: run_kql, get_trace
  integrations/        Application Insights client (Entra ID auth)
  security/            kql_guard (read-only policy), redaction (secrets/PII)
  web/                 FastAPI + chat UI
  cli.py               pereviewsre ask | chat | web
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

## Environments (local vs production)
The active environment is set by `PEREVIEWSRE_ENV` (`local` or `production`). If unset, it is
`production` when running in Azure and `local` everywhere else.

Settings are layered; later sources override earlier ones:

1. `.env` (shared defaults)
2. `.env.<environment>` (`.env.local` or `.env.production`)
3. Real environment variables (`local.settings.json` under `func start`, or Azure app settings)

| | Local | Production |
| --- | --- | --- |
| Config source | `local.settings.json` (func) or `.env.local` (CLI) | Function App application settings |
| Azure auth | `az login` (your identity) | Managed identity of the Function App |
| `PEREVIEWSRE_WEB_TOKEN` | Optional | **Required** (requests get 503 without it) |
| Logging | Rotating file + console | Console only (goes to App Insights) |

Never commit real config: `.env*` (except `*.example`) and `local.settings.json` are git-ignored.

## Configure

### Local, with `func start` (Azure Functions host)
```powershell
copy local.settings.json.example local.settings.json
```
Edit `local.settings.json` and set the values below. `AzureWebJobsStorage` needs
[Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) running
(`UseDevelopmentStorage=true`) or a real storage connection string.

### Local, with the CLI / `pereviewsre web`
```powershell
copy .env.local.example .env.local
```

### Required variables

| Variable | Description |
| --- | --- |
| `PEREVIEWSRE_ENV` | `local` or `production` |
| `PEREVIEWSRE_APPINSIGHTS_RESOURCE_ID` | Full ARM resource ID of the App Insights component |
| `PEREVIEWSRE_AOAI_ENDPOINT` | e.g. `https://<name>.openai.azure.com/` |
| `PEREVIEWSRE_AOAI_DEPLOYMENT` | Your chat model deployment name |
| `PEREVIEWSRE_AOAI_API_KEY` | Optional. Leave empty for keyless Entra ID auth |
| `PEREVIEWSRE_WEB_TOKEN` | Bearer token for the API. Required in production |

Find the resource ID with:
```powershell
az monitor app-insights component show --app <name> -g <resource-group> --query id -o tsv
```

Then sign in to Azure (local only):
```powershell
az login
```

### Production
Set the same variables as Function App application settings, with `PEREVIEWSRE_ENV=production`:
```powershell
az functionapp config appsettings set -n <app> -g <rg> --settings `
  PEREVIEWSRE_ENV=production `
  PEREVIEWSRE_APPINSIGHTS_RESOURCE_ID=<id> `
  PEREVIEWSRE_AOAI_ENDPOINT=<endpoint> `
  PEREVIEWSRE_AOAI_DEPLOYMENT=<deployment> `
  PEREVIEWSRE_WEB_TOKEN=<long-random-token>
```
Prefer a Key Vault reference for the token. Enable the app's managed identity and grant it the
roles listed under Prerequisites. See [.env.production.example](.env.production.example) and
[docs/DEPLOY_AZURE_FUNCTIONS.md](docs/DEPLOY_AZURE_FUNCTIONS.md).

## Run
```powershell
# Run the tests
pytest

# Azure Functions host (serves the FastAPI app): http://localhost:7071
func start

# One-shot question
pereviewsre ask "Why are checkouts failing in the last hour?"

# Interactive chat in the terminal (type 'exit' to quit)
pereviewsre chat

# Web chat UI at http://127.0.0.1:8080 (without the Functions host)
pereviewsre web
```

Test the API under `func start` (add the `Authorization` header if a token is set):
```powershell
curl -X POST http://localhost:7071/api/ask -H "Content-Type: application/json" `
  -H "Authorization: Bearer <token>" -d '{\"question\":\"Any errors in the last hour?\"}'
```

Without installing the script entry point you can also run `python -m pereviewsre.cli ask "..."`.

## Logs
Every component (agent, tool registry, App Insights client, CLI, web service) writes to a
shared rotating log file at `logs/pereviewsre.log` (also echoed to the console). Each line is
passed through the redaction filter, including tracebacks.

```powershell
Get-Content .\logs\pereviewsre.log -Wait -Tail 50   # follow logs live
```

What is logged: questions (redacted), LLM steps and token usage, tool names and arguments,
KQL queries, row/column counts, rejected queries and errors. Telemetry row contents are
**never** logged. Set `PEREVIEWSRE_LOG_LEVEL=DEBUG` and `PEREVIEWSRE_LOG_DIR` in `.env` to tune.

## Troubleshooting
- **`Missing required settings`**: fill the required values in `local.settings.json` / `.env.local`
  (or app settings in Azure). `PEREVIEWSRE_WEB_TOKEN` is also required when `PEREVIEWSRE_ENV=production`.
- **503 `Service not configured`**: production mode without `PEREVIEWSRE_WEB_TOKEN`.
- **`func start` storage error**: start Azurite or set a real `AzureWebJobsStorage`.
- **`No module named pereviewsre`** when running pytest: run `pip install -e ".[dev]"` first.
- **401/403 from Azure**: re-run `az login` and check the role assignments above.
- **`pereviewsre` not found**: activate the virtualenv first.
- **pip read timeout**: use the `--default-timeout 120 --retries 10` install command above.

See [SECURITY.md](SECURITY.md) for the security model and
[docs/DEPLOY_AZURE_FUNCTIONS.md](docs/DEPLOY_AZURE_FUNCTIONS.md) to run it in Azure Functions.
