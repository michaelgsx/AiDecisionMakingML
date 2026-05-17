# AiDecisionMakingML

Three-stage **logistic regression** risk pipeline trained on **binned one-hot** features from Azure SQL (`ai-rag-db-1`) and labels from `risk_decisions`.

## Business cascade

| Step | Model | Positive class | When |
|------|--------|----------------|------|
| 1 | `reject_vs_non_reject` | reject | Highest priority — block first |
| 2 | `freeze_vs_pass` | freeze (ever froze) | Only if step 1 says non-reject |
| 3 | `manual_review` | manual path | If step 2 freeze and manual score high |

**Inference:** reject → else freeze vs pass → if freeze and manual score → `manual_review`, else `freeze` → else `pass`.

Coefficients are **L2-normalized** per stage before export (weights unit norm; intercept scaled consistently).

## Data sources

| Table | Use |
|-------|-----|
| `dbo.risk_feature_binned` | `onehot_json` (join `calibration_id`) |
| `dbo.risk_feature_bin_calibrations` | `flatten_layout_json`, feature names |
| `dbo.risk_decisions` | Labels (latest row = final; history for freeze / manual) |

Run backend bin calibration first:

```bash
cd ../AiDecisionMakingBackend
python db/offline_bin_calibration.py --save-db
```

## Train & publish

```bash
cd AiDecisionMakingML
pip install -r requirements.txt
cp .env.example .env
# SQL: copy from AiDecisionMakingBackend/db/.env
# Blob: AZURE_STORAGE_ACCOUNT_NAME=airagblob, AZURE_STORAGE_ACCOUNT_KEY=...

python train.py
python train.py --daily          # version tag = UTC date, for scheduled jobs
python train.py --no-upload --out artifacts/risk_pipeline_v1.json
```

## Daily train on Azure (`ai-rag-ml`)

**Deploy workflow** (`.github/workflows/deploy-aca-daily-train.yml`):

1. Build Docker in **airagacr** (ACR)  
2. Deploy **Container Apps Job** with cron **`15 2 * * *`** (daily 02:15 UTC)  
3. Each run executes `python train.py --daily` → **airagblob** / **logistic**

Actions → **Deploy ai-rag-ml (Docker + daily scheduler)** → Run workflow.

| GitHub secret | Purpose |
|---------------|---------|
| `AZURE_CREDENTIALS` | Deploy to Azure |
| `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD` | Job runtime → SQL |
| `AZURE_STORAGE_ACCOUNT_KEY` | Job runtime → Blob |

Optional variable `USE_KEYVAULT_FOR_ACA_DEPLOY=true` to pull passwords from **ai-rag-key** instead of GitHub.

`daily-train.yml` is optional (GitHub runner, manual only); **scheduler lives on Azure after deploy**.

### Local / CLI deploy

See `infra/secrets-cloud-only.md`, `infra/aca-job-daily-train.md`, `Dockerfile`.

**Blob layout** (container `logistic`):

- `models/risk_pipeline_latest.json`
- `models/risk_pipeline_YYYY-MM-DD.json`
- `models/daily/YYYY-MM-DD/risk_pipeline.json`

## Env

| Variable | Default |
|----------|---------|
| `RISK_CALIBRATION_ID` | `00000000-0000-0000-0000-000000000001` |
| `AZURE_STORAGE_ACCOUNT_NAME` | `airagblob` |
| `AZURE_STORAGE_CONTAINER` | `logistic` |
| `AZURE_STORAGE_BLOB_PREFIX` | `models` |

## Layout

```
src/risk_pipeline/
  config.py           # SQL + blob env
  sql_data.py         # Load onehot + decisions
  labels.py           # Per-request label aggregation
  logistic_stages.py  # Train / normalize / cascade predict
  pipeline.py         # Orchestration + artifact JSON
  blob_export.py      # Upload to Azure Blob
train.py              # CLI
```
