# Secrets for cloud-only daily train (no local password files)

Training runs **inside Azure** (Container Apps Job). Passwords are **not** read from a laptop `.env` at schedule time.

## Where secrets live

| Phase | Where |
|-------|--------|
| **Daily run** (02:15 UTC) | ACA Job encrypted secrets → env `secretref:azure-sql-password` |
| **Deploy once** | Key Vault → `az` injects into Job, or GitHub Actions secrets |
| **Local dev** (optional) | `.env` gitignored — not used in production |

```
Key Vault (ai-rag-key)          GitHub Actions secrets
        │                                │
        └──────── deploy script ─────────┘
                      │
                      ▼
         Container Apps Job secrets (Azure-stored)
                      │
                      ▼
              train.py in container
                      │
                      ▼
              airagblob / logistic
```

## Recommended: Key Vault + deploy

1. Store in vault `ai-rag-key` (names are configurable):
   - `azure-sql-password` — SQL login password
   - `airagblob-account-key` — storage account key
   - (optional) set `AZURE_SQL_USER` as env when deploying, not a secret

2. Deploy without local `.env`:

```bash
az login
export AZURE_SQL_USER='your_sql_login'
./infra/deploy-aca-daily-train.sh --from-keyvault ai-rag-key --run-now
```

Custom secret names:

```bash
export KEYVAULT_SQL_PASSWORD_SECRET=db-1-password
export KEYVAULT_STORAGE_KEY_SECRET=airagblob-key
./infra/deploy-aca-daily-train.sh --from-keyvault ai-rag-key
```

## GitHub Actions only (no local machine)

Workflow **Deploy ACA daily train** uses `AZURE_CREDENTIALS` + SQL/Blob **GitHub Secrets** — values exist only in GitHub, passed to `az` during deploy, then stored on the Job in Azure.

Workflow **ai-rag-ml daily train** runs training on GitHub-hosted runners with the same GitHub Secrets (no ACA).

## Update image without touching passwords

```bash
SKIP_ACR_BUILD=0 ./infra/deploy-aca-daily-train.sh --skip-secret-update
```

## Future: zero storage keys

Use **managed identity** on the Job + RBAC on `airagblob` and Azure AD auth for SQL — no account key in any vault. Requires extra IAM setup.
