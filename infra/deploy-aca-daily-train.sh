#!/usr/bin/env bash
# Build ai-rag-ml/train image in ACR (airagacr) and deploy/update ACA scheduled Job.
#
# Prereqs:
#   az login
#   az extension add --name containerapp   # once
#
# Secrets: AiDecisionMakingML/.env or ../AiDecisionMakingBackend/db/.env
#   AZURE_SQL_* , AZURE_STORAGE_ACCOUNT_KEY (or connection strings)
#
# Usage:
#   ./infra/deploy-aca-daily-train.sh
#   ./infra/deploy-aca-daily-train.sh --run-now    # trigger one execution after deploy

set -euo pipefail

ACR_NAME="${ACR_NAME:-airagacr}"
JOB_NAME="${JOB_NAME:-ai-rag-ml-daily-train}"
ACA_ENV_NAME="${ACA_ENV_NAME:-airag-aca-env}"
IMAGE_NAME="ai-rag-ml/train:latest"
CRON="${CRON:-15 2 * * *}"
RUN_NOW=false

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

for arg in "$@"; do
  case "$arg" in
    --run-now) RUN_NOW=true ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

load_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  echo "Loading env from $f"
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f" | sed 's/\r$//')
  set +a
}

load_env_file "$ROOT/.env"
load_env_file "$ROOT/../AiDecisionMakingBackend/db/.env"

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

: "${AZURE_SQL_SERVER:?Set AZURE_SQL_SERVER in .env}"
: "${AZURE_SQL_DATABASE:?Set AZURE_SQL_DATABASE in .env}"
: "${AZURE_SQL_USER:?Set AZURE_SQL_USER in .env}"
: "${AZURE_SQL_PASSWORD:?Set AZURE_SQL_PASSWORD in .env}"

if [[ -z "${AZURE_STORAGE_ACCOUNT_KEY:-}" && -z "${AZURE_STORAGE_CONNECTION_STRING:-}" ]]; then
  echo "ERROR: Set AZURE_STORAGE_ACCOUNT_KEY or AZURE_STORAGE_CONNECTION_STRING" >&2
  exit 1
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
if az containerapp job show --name "$JOB_NAME" --resource-group "$RG" -o none 2>/dev/null; then
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --image "$FULL_IMAGE" \
    --cpu 0.5 \
    --memory 1.0Gi \
    --registry-server "$LOGIN_SERVER" \
    --registry-username "$ACR_USER" \
    --registry-password "$ACR_PASS" \
    --set-env-vars "${ENV_VARS[@]}" \
    --secrets "azure-sql-password=$AZURE_SQL_PASSWORD" "azure-storage-account-key=$STORAGE_KEY" \
    --cron-expression "$CRON"
else
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
