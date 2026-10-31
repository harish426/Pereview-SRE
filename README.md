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
  memory/              per-chat memory (facts + recent answers, in RAM only)
  web/                 FastAPI + chat UI
  cli.py               pereviewsre ask | chat | web
function_app.py         Azure Functions entry point (wraps the same web app)
tests/                 guardrail, memory and web API tests
docs/                   Azure deployment guide
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

Optional settings (defaults in brackets): `PEREVIEWSRE_MEMORY_TTL_S` [1800, idle seconds before a
chat's memory expires], `PEREVIEWSRE_MEMORY_MAX_EPISODES` [10, answers remembered per chat],
`PEREVIEWSRE_MEMORY_MAX_SESSIONS` [50, chats remembered at once], `PEREVIEWSRE_MAX_AGENT_STEPS` [8],
`PEREVIEWSRE_MAX_ROWS` [200], `PEREVIEWSRE_QUERY_TIMEOUT_S` [60], `PEREVIEWSRE_DEFAULT_LOOKBACK_HOURS` [24].

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

## Web chat and chat memory
The web UI shows each answer in sections (Summary, Evidence, Root cause, Recommended actions)
with formatted tables and code. The tool calls are in a collapsible "Steps" line.

- **Chats on the left:** press **New chat** to start another one. The old chat stays in the
  sidebar; click it to reopen and keep working on it. **×** deletes a chat.
- **Lifetime:** chats live in the open browser tab only. Closing or reloading the tab clears them.
- **Each chat has its own memory**, so one chat never sees another's context.

Memory is kept in the server's RAM only (nothing on disk) and only the important parts are stored:

1. **Known facts**: time window, services, latest operation_Ids, root causes (with confidence)
   and open next steps. Always sent to the model, and its size is capped.
2. **Recent vs. older answers**: the 2 newest answers in full, older ones as a one-line digest.
3. **No noise**: duplicate and failed tool calls are skipped, answers are trimmed to Root cause and
   Recommended actions, raw results are never stored, and everything is redacted first.

It expires after 30 minutes idle, at 10 answers per chat, and at 50 chats on the server (least
recently used goes first). The `pereviewsre chat` terminal mode has the same memory for one session.

## Deploy to Azure (in order)
Full commands are in [docs/DEPLOY_AZURE_FUNCTIONS.md](docs/DEPLOY_AZURE_FUNCTIONS.md). The order:

1. Make sure it works locally first (`func start` with your `local.settings.json`, and `pytest`).
2. Create the Azure resources: resource group, storage account, Linux Function App (Python 3.11)
   with a system-assigned managed identity.
3. Give the Function App's identity access: *Monitoring Reader* and *Log Analytics Reader* on
   App Insights, *Cognitive Services OpenAI User* on Azure OpenAI.
4. Set the app settings: the variables from the table above, with `PEREVIEWSRE_ENV=production` and a
   long random `PEREVIEWSRE_WEB_TOKEN` (mandatory in production, otherwise requests get 503).
5. Publish: `func azure functionapp publish <app-name>`.
6. Open `https://<app-name>.azurewebsites.net/` (it asks for the token) or call `POST /api/ask`.

In production, chat memory is per server instance and in RAM: if Azure restarts or scales the app,
the agent forgets that chat's context. HTTP calls are cut off by Azure after about 230 seconds.

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
- **Agent forgets earlier chat**: the chat idled past the memory timeout, the 50-chat limit was hit,
  or the server restarted.

See [SECURITY.md](SECURITY.md) for the security model and
[docs/DEPLOY_AZURE_FUNCTIONS.md](docs/DEPLOY_AZURE_FUNCTIONS.md) to run it in Azure Functions.
