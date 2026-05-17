from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

PKG_DIR = Path(__file__).resolve().parent
ML_ROOT = PKG_DIR.parent.parent
BACKEND_DB_ENV = ML_ROOT.parent / "AiDecisionMakingBackend" / "db" / ".env"

ACTIVE_CALIBRATION_ID = "00000000-0000-0000-0000-000000000001"
AUTO_DECIDERS = frozenset({"system", "compliance-bot"})


def load_env() -> None:
    load_dotenv(ML_ROOT / ".env")
    if BACKEND_DB_ENV.is_file():
        load_dotenv(BACKEND_DB_ENV, override=False)


def parse_connection_string(cs: str) -> dict:
    def get(name: str) -> str:
        m = re.search(rf"(?:^|;)\s*{name}\s*=\s*([^;]+)", cs, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    server = get("Server") or get("Data Source")
    server = re.sub(r"^tcp:", "", server, flags=re.IGNORECASE)
    port = 1433
    if "," in server:
        parts = server.rsplit(",", 1)
        server = parts[0].strip()
        try:
            port = int(parts[1].strip())
        except ValueError:
            pass
    return {
        "server": server,
        "port": port,
        "database": get("Initial Catalog") or get("Database"),
        "user": get("User ID") or get("UID"),
        "password": get("Password") or get("Pwd"),
    }


def sql_connect():
    import pymssql

    load_env()
    cs = os.getenv("AZURE_SQL_CONNECTION_STRING", "").strip()
    if cs:
        kwargs = parse_connection_string(cs)
    else:
        server = os.getenv("AZURE_SQL_SERVER", "").strip()
        database = os.getenv("AZURE_SQL_DATABASE", "").strip()
        user = os.getenv("AZURE_SQL_USER", "").strip()
        password = os.getenv("AZURE_SQL_PASSWORD", "")
        port = int(os.getenv("AZURE_SQL_PORT", "1433"))
        if not server or not database or not user:
            print(
                "Set AZURE_SQL_* in AiDecisionMakingML/.env or AiDecisionMakingBackend/db/.env",
                file=sys.stderr,
            )
            sys.exit(1)
        kwargs = {
            "server": server,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }
    return pymssql.connect(**kwargs)


def calibration_id() -> str:
    load_env()
    return os.getenv("RISK_CALIBRATION_ID", ACTIVE_CALIBRATION_ID).strip()


def blob_settings() -> dict:
    load_env()
    return {
        "account": os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "airagblob").strip(),
        "account_key": os.getenv("AZURE_STORAGE_ACCOUNT_KEY", "").strip(),
        "connection_string": os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip(),
        "container": os.getenv("AZURE_STORAGE_CONTAINER", "logistic").strip(),
        "prefix": os.getenv("AZURE_STORAGE_BLOB_PREFIX", "models").strip().strip("/"),
    }
