#!/usr/bin/env bash
# Build ai-rag-ml/train image in ACR (airagacr) and deploy/update ACA scheduled Job.
#
# Prereqs:
#   az login
#   az extension add --name containerapp   # once
#
# Secrets (pick one — no local password file required for cloud):
#   --from-keyvault [vault]   Read SQL/Blob secrets from Azure Key Vault (recommended)
#   env vars already exported  e.g. GitHub Actions secrets → script (never written to disk)
#   .env file                  optional local dev only
#
# At runtime the Job uses ACA encrypted secrets (secretref:), not .env on disk.
#
# Usage:
#   ./infra/deploy-aca-daily-train.sh --from-keyvault ai-rag-key --run-now
#   ./infra/deploy-aca-daily-train.sh --skip-secret-update   # image-only update

set -euo pipefail

ACR_NAME="${ACR_NAME:-airagacr}"
JOB_NAME="${JOB_NAME:-ai-rag-ml-daily-train}"
ACA_ENV_NAME="${ACA_ENV_NAME:-airag-aca-env}"
IMAGE_NAME="ai-rag-ml/train:latest"
CRON="${CRON:-15 2 * * *}"
RUN_NOW=false
FROM_KEYVAULT=""
SKIP_SECRET_UPDATE=false
KEYVAULT_SQL_PASSWORD_SECRET="${KEYVAULT_SQL_PASSWORD_SECRET:-azure-sql-password}"
KEYVAULT_STORAGE_KEY_SECRET="${KEYVAULT_STORAGE_KEY_SECRET:-airagblob-account-key}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-now) RUN_NOW=true; shift ;;
    --from-keyvault)
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        FROM_KEYVAULT="$2"
        shift 2
      else
        FROM_KEYVAULT="ai-rag-key"
        shift
      fi
      ;;
    --skip-secret-update) SKIP_SECRET_UPDATE=true; shift ;;
    -h|--help) sed -n '1,28p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

load_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  echo "Loading optional env from $f (non-secret defaults only if unset)"
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f" | sed 's/\r$//')
  set +a
}

kv_secret() {
  az keyvault secret show --vault-name "$1" --name "$2" --query value -o tsv 2>/dev/null
}

if [[ -n "$FROM_KEYVAULT" ]]; then
  echo "Loading secrets from Key Vault: $FROM_KEYVAULT"
  AZURE_SQL_PASSWORD="${AZURE_SQL_PASSWORD:-$(kv_secret "$FROM_KEYVAULT" "$KEYVAULT_SQL_PASSWORD_SECRET")}"
  AZURE_STORAGE_ACCOUNT_KEY="${AZURE_STORAGE_ACCOUNT_KEY:-$(kv_secret "$FROM_KEYVAULT" "$KEYVAULT_STORAGE_KEY_SECRET")}"
  export AZURE_SQL_PASSWORD AZURE_STORAGE_ACCOUNT_KEY
fi

# Non-secret defaults (safe in .env or hardcoded)
export AZURE_SQL_SERVER="${AZURE_SQL_SERVER:-ai-rag-sql-server.database.windows.net}"
export AZURE_SQL_DATABASE="${AZURE_SQL_DATABASE:-ai-rag-db-1}"

if [[ -z "${AZURE_SQL_PASSWORD:-}" || -z "${AZURE_STORAGE_ACCOUNT_KEY:-}" ]]; then
  load_env_file "$ROOT/.env"
  load_env_file "$ROOT/../AiDecisionMakingBackend/db/.env"
fi

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: Azure CLI not found. Install: brew install azure-cli" >&2
  exit 1
fi

if ! az account show -o none 2>/dev/null; then
  echo "ERROR: Not logged in. Run: az login" >&2
  exit 1
fi

if ! az acr show --name "$ACR_NAME" -o none 2>/dev/null; then
  echo "ERROR: ACR '$ACR_NAME' not found in current subscription." >&2
  exit 1
fi

RG="$(az acr show --name "$ACR_NAME" --query resourceGroup -o tsv)"
LOCATION="$(az acr show --name "$ACR_NAME" --query location -o tsv)"
LOGIN_SERVER="$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)"
FULL_IMAGE="${LOGIN_SERVER}/${IMAGE_NAME}"

echo "ACR:      $LOGIN_SERVER (RG=$RG, location=$LOCATION)"
echo "Image:    $FULL_IMAGE"
echo "ACA env:  $ACA_ENV_NAME"
echo "Job:      $JOB_NAME"

: "${AZURE_SQL_USER:?Set AZURE_SQL_USER (env or Key Vault metadata)}"

if [[ "$SKIP_SECRET_UPDATE" != true ]]; then
  if [[ -z "${AZURE_SQL_PASSWORD:-}" ]]; then
    echo "ERROR: SQL password missing. Use --from-keyvault, export AZURE_SQL_PASSWORD, or .env (local dev)." >&2
    exit 1
  fi
  if [[ -z "${AZURE_STORAGE_ACCOUNT_KEY:-}" && -z "${AZURE_STORAGE_CONNECTION_STRING:-}" ]]; then
    echo "ERROR: Storage key missing. Use --from-keyvault or export AZURE_STORAGE_ACCOUNT_KEY." >&2
    exit 1
  fi
fi

STORAGE_KEY="${AZURE_STORAGE_ACCOUNT_KEY:-}"
if [[ -z "$STORAGE_KEY" && -n "${AZURE_STORAGE_CONNECTION_STRING:-}" ]]; then
  STORAGE_KEY="$(echo "$AZURE_STORAGE_CONNECTION_STRING" | sed -n 's/.*AccountKey=\([^;]*\).*/\1/p')"
fi

az extension add --name containerapp --only-show-errors 2>/dev/null || true

if [[ "${SKIP_ACR_BUILD:-}" != "1" ]]; then
  echo ""
  echo "=== 1/4 Build image in ACR (cloud build) ==="
  az acr build \
    --registry "$ACR_NAME" \
    --image "$IMAGE_NAME" \
    --file Dockerfile \
    .
else
  echo ""
  echo "=== 1/4 Skip ACR build (SKIP_ACR_BUILD=1) ==="
fi

echo ""
echo "=== 2/4 Ensure Container Apps environment ==="
if ! az containerapp env show --name "$ACA_ENV_NAME" --resource-group "$RG" -o none 2>/dev/null; then
  az containerapp env create \
    --name "$ACA_ENV_NAME" \
    --resource-group "$RG" \
    --location "$LOCATION"
else
  echo "Environment $ACA_ENV_NAME already exists."
fi

ACR_USER="$(az acr credential show --name "$ACR_NAME" --query username -o tsv)"
ACR_PASS="$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)"

ENV_VARS=(
  "AZURE_SQL_SERVER=$AZURE_SQL_SERVER"
  "AZURE_SQL_DATABASE=$AZURE_SQL_DATABASE"
  "AZURE_SQL_USER=$AZURE_SQL_USER"
  "AZURE_SQL_PASSWORD=secretref:azure-sql-password"
  "AZURE_STORAGE_ACCOUNT_NAME=${AZURE_STORAGE_ACCOUNT_NAME:-airagblob}"
  "AZURE_STORAGE_ACCOUNT_KEY=secretref:azure-storage-account-key"
  "AZURE_STORAGE_CONTAINER=${AZURE_STORAGE_CONTAINER:-logistic}"
  "AZURE_STORAGE_BLOB_PREFIX=${AZURE_STORAGE_BLOB_PREFIX:-models}"
  "RISK_CALIBRATION_ID=${RISK_CALIBRATION_ID:-00000000-0000-0000-0000-000000000001}"
)

echo ""
echo "=== 3/4 Create or update scheduled Job ==="
SECRET_ARGS=()
if [[ "$SKIP_SECRET_UPDATE" != true ]]; then
  SECRET_ARGS=(--secrets "azure-sql-password=$AZURE_SQL_PASSWORD" "azure-storage-account-key=$STORAGE_KEY")
else
  echo "Skipping secret injection (--skip-secret-update); using secrets already stored on the Job."
fi

if az containerapp job show --name "$JOB_NAME" --resource-group "$RG" -o none 2>/dev/null; then
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --image "$FULL_IMAGE" \
    --cpu 0.5 \
    --memory 1.0Gi \
    --cron-expression "$CRON" \
    --set-env-vars "${ENV_VARS[@]}"
  az containerapp job registry set \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --server "$LOGIN_SERVER" \
    --username "$ACR_USER" \
    --password "$ACR_PASS"
  if [[ ${#SECRET_ARGS[@]} -gt 0 ]]; then
    az containerapp job secret set \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      "${SECRET_ARGS[@]}"
  fi
else
  if [[ "$SKIP_SECRET_UPDATE" == true ]]; then
    echo "ERROR: Job does not exist yet; cannot use --skip-secret-update on first deploy." >&2
    exit 1
  fi
  az containerapp job create \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --environment "$ACA_ENV_NAME" \
    --trigger-type Schedule \
    --cron-expression "$CRON" \
    --replica-timeout 1800 \
    --replica-retry-limit 1 \
    --image "$FULL_IMAGE" \
    --cpu 0.5 \
    --memory 1.0Gi \
    --registry-server "$LOGIN_SERVER" \
    --registry-username "$ACR_USER" \
    --registry-password "$ACR_PASS" \
    --secrets "azure-sql-password=$AZURE_SQL_PASSWORD" "azure-storage-account-key=$STORAGE_KEY" \
    --env-vars "${ENV_VARS[@]}"
fi

echo ""
echo "=== 4/4 Done ==="
az containerapp job show --name "$JOB_NAME" --resource-group "$RG" \
  --query "{name:name,provisioningState:properties.provisioningState,schedule:properties.configuration.scheduleTriggerConfig.cronExpression}" \
  -o table

if [[ "$RUN_NOW" == true ]]; then
  echo ""
  echo "Starting manual job run..."
  az containerapp job start --name "$JOB_NAME" --resource-group "$RG"
  echo "Check logs: az containerapp job logs show -n $JOB_NAME -g $RG --container $JOB_NAME"
fi

echo ""
echo "Manual run:  az containerapp job start -n $JOB_NAME -g $RG"
echo "Logs:        az containerapp job logs show -n $JOB_NAME -g $RG --follow"
