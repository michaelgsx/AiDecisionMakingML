from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from azure.storage.blob import BlobServiceClient

from .config import blob_settings


def upload_json(blob_name: str, payload: dict[str, Any]) -> str:
    cfg = blob_settings()
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")

    if cfg["connection_string"]:
        client = BlobServiceClient.from_connection_string(cfg["connection_string"])
    elif cfg["account"] and cfg["account_key"]:
        account_url = f"https://{cfg['account']}.blob.core.windows.net"
        client = BlobServiceClient(account_url=account_url, credential=cfg["account_key"])
    else:
        raise RuntimeError(
            "Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME + "
            "AZURE_STORAGE_ACCOUNT_KEY for blob upload"
        )

    container = client.get_container_client(cfg["container"])
    try:
        container.create_container()
    except Exception:
        pass

    blob_client = container.get_blob_client(blob_name)
    blob_client.upload_blob(body, overwrite=True)
    return f"{cfg['container']}/{blob_name}"


def publish_artifact(artifact: dict[str, Any], version: str) -> list[str]:
    cfg = blob_settings()
    prefix = cfg["prefix"]
    paths = [
        f"{prefix}/risk_pipeline_{version}.json",
        f"{prefix}/risk_pipeline_latest.json",
    ]
    # Daily snapshots (version = YYYY-MM-DD) also land under dated folder for audit.
    if len(version) == 10 and version[4] == "-" and version[7] == "-":
        paths.append(f"{prefix}/daily/{version}/risk_pipeline.json")

    uploaded: list[str] = []
    for path in paths:
        uploaded.append(upload_json(path, artifact))
    return uploaded
