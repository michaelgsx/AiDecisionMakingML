# Azure Container Apps Job: `ai-rag-ml` (optional)

Use this if you prefer **Azure-hosted** daily cron instead of GitHub Actions.

## 1. Build and push image

```bash
cd AiDecisionMakingML
ACR=yourregistry.azurecr.io
az acr build -r $ACR -t ai-rag-ml/train:latest .
```

## 2. Store secrets in Container Apps environment

| Secret | Source |
|--------|--------|
| `azure-sql-server` | `ai-rag-sql-server.database.windows.net` |
| `azure-sql-database` | `ai-rag-db-1` |
| `azure-sql-user` / `azure-sql-password` | SQL login |
| `azure-storage-account-key` | `airagblob` access key |

## 3. Create scheduled job (example CLI)

```bash
az containerapp job create \
  --name ai-rag-ml-daily-train \
  --resource-group <rg> \
  --environment <aca-env> \
  --trigger-type Schedule \
  --cron-expression "15 2 * * *" \
  --replica-timeout 1800 \
  --replica-retry-limit 1 \
  --image $ACR/ai-rag-ml/train:latest \
  --cpu 0.5 --memory 1Gi \
  --secrets azure-sql-password=<pwd> azure-storage-account-key=<key> \
  --env-vars \
    AZURE_SQL_SERVER=ai-rag-sql-server.database.windows.net \
    AZURE_SQL_DATABASE=ai-rag-db-1 \
    AZURE_SQL_USER=<user> \
    AZURE_SQL_PASSWORD=secretref:azure-sql-password \
    AZURE_STORAGE_ACCOUNT_NAME=airagblob \
    AZURE_STORAGE_ACCOUNT_KEY=secretref:azure-storage-account-key \
    AZURE_STORAGE_CONTAINER=logistic \
    RISK_CALIBRATION_ID=00000000-0000-0000-0000-000000000001
```

Image `CMD` runs `python train.py --daily` → uploads to:

- `logistic/models/risk_pipeline_latest.json`
- `logistic/models/risk_pipeline_YYYY-MM-DD.json`
- `logistic/models/daily/YYYY-MM-DD/risk_pipeline.json`

## GitHub Actions (recommended)

See `.github/workflows/daily-train.yml` — no ACR required; runs on `schedule` + `workflow_dispatch`.
