# Deploy to Azure Functions

The Function App wraps the FastAPI app (`function_app.py`), so `GET /` serves the chat UI and
`POST /api/ask` is the API. Auth to App Insights and Azure OpenAI uses the Function's
**managed identity** (no keys).

## 1. Prerequisites
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local), Azure CLI (`az login`)
- Python 3.11 locally (Functions supports 3.11/3.12)

## 2. Run locally
```powershell
copy local.settings.json.example local.settings.json   # fill in values
func start                                             # http://localhost:7071
```
Locally the app uses your `az login` identity. Test:
```powershell
curl -X POST http://localhost:7071/api/ask -H "Authorization: Bearer <PEREVIEWSRE_WEB_TOKEN>" `
  -H "Content-Type: application/json" -d '{"question":"Why did requests fail in the last hour?"}'
```

## 3. Create the Azure resources
```powershell
$RG="rg-pereviewsre"; $LOC="eastus"; $ST="stpereviewsre$(Get-Random -Maximum 99999)"; $APP="pereviewsre-$(Get-Random -Maximum 99999)"
az group create -n $RG -l $LOC
az storage account create -n $ST -g $RG -l $LOC --sku Standard_LRS --allow-blob-public-access false
az functionapp create -n $APP -g $RG --storage-account $ST --consumption-plan-location $LOC `
  --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux `
  --assign-identity "[system]" --https-only true
```
Use a Premium/Flex plan instead if you need longer investigations (see Limits).

## 4. Grant the managed identity access (least privilege)
```powershell
$PRINCIPAL=(az functionapp identity show -n $APP -g $RG --query principalId -o tsv)
# Read telemetry from the one App Insights resource
az role assignment create --assignee $PRINCIPAL --role "Monitoring Reader" --scope <APPINSIGHTS_RESOURCE_ID>
az role assignment create --assignee $PRINCIPAL --role "Log Analytics Reader" --scope <APPINSIGHTS_RESOURCE_ID>
# Call Azure OpenAI keylessly
az role assignment create --assignee $PRINCIPAL --role "Cognitive Services OpenAI User" --scope <AOAI_RESOURCE_ID>
```

## 5. App settings (secrets in Key Vault references if possible)
```powershell
$TOKEN=[Convert]::ToBase64String((1..32 | % { Get-Random -Maximum 256 }) -as [byte[]])
az functionapp config appsettings set -n $APP -g $RG --settings `
  PEREVIEWSRE_APPINSIGHTS_RESOURCE_ID="<APPINSIGHTS_RESOURCE_ID>" `
  PEREVIEWSRE_AOAI_ENDPOINT="https://<resource>.openai.azure.com/" `
  PEREVIEWSRE_AOAI_DEPLOYMENT="<deployment>" `
  PEREVIEWSRE_WEB_TOKEN="$TOKEN" `
  SCM_DO_BUILD_DURING_DEPLOYMENT=true
echo $TOKEN   # save it: needed as the Bearer token
```
`PEREVIEWSRE_WEB_TOKEN` is **mandatory in Azure**: without it the app returns 503 (fails closed).

## 6. Publish
```powershell
func azure functionapp publish $APP
```
Open `https://<app>.azurewebsites.net/` (the UI will prompt for the token on 401) or call
`POST https://<app>.azurewebsites.net/api/ask`.

## Limits and notes
- **Timeouts:** HTTP-triggered functions are cut off by the Azure load balancer at **~230 s**,
  regardless of `functionTimeout`. Keep `PEREVIEWSRE_MAX_AGENT_STEPS` modest (default 8). For longer
  investigations, switch to a queue-triggered function that stores the result.
- **Logs:** in Azure the file handler is disabled (ephemeral disk). Logs are redacted and go to
  the Function's Application Insights (`traces` table). Query them with KQL, or Log Stream.
- **Cold starts:** first request on Consumption may take several seconds.
- **Hardening for production:** enable App Service Authentication (Entra ID), restrict inbound
  access (IP restrictions / Private Endpoint), and store the token in Key Vault.
- Don't point the Function's own Application Insights at the same resource you investigate
  unless you want the agent's logs included in its own queries.
