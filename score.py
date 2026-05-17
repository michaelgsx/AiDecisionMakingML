#!/usr/bin/env python3
"""Score one request by request_id using trained artifact (local JSON or default path)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from risk_pipeline.config import calibration_id, load_env, sql_connect  # noqa: E402
from risk_pipeline.logistic_stages import (  # noqa: E402
    STAGE_FREEZE,
    STAGE_MANUAL,
    STAGE_REJECT,
    StageModel,
    predict_cascade,
)


def load_stages(path: Path) -> dict[str, StageModel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    stages: dict[str, StageModel] = {}
    for key, raw in data["stages"].items():
        stages[key] = StageModel(
            stage=raw["stage"],
            positive_class=raw["positive_class"],
            negative_class=raw["negative_class"],
            feature_names=data["feature_names"],
            intercept=raw["intercept"],
            weights=raw["weights"],
            weights_l2_norm_before=raw.get("weights_l2_norm_before_normalization", 0.0),
            threshold=raw.get("threshold", 0.5),
            train_size=raw.get("train_size", 0),
            positive_rate=raw.get("positive_rate", 0.0),
            metrics=raw.get("metrics", {}),
        )
    return stages


def load_onehot(request_id: str, cal_id: str) -> np.ndarray:
    conn = sql_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT onehot_json FROM dbo.risk_feature_binned
        WHERE request_id = %s AND calibration_id = %s
        """,
        (request_id, cal_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise SystemExit(f"No binned row for request_id={request_id}")
    vec = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return np.asarray(vec, dtype=np.float64)


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("request_id")
    p.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "artifacts" / "risk_pipeline_latest.json",
    )
    p.add_argument("--calibration-id", default="")
    args = p.parse_args()

    stages = load_stages(args.artifact)
    cal = args.calibration_id.strip() or calibration_id()
    x = load_onehot(args.request_id, cal)
    pred = predict_cascade(x, stages)
    print(json.dumps({"request_id": args.request_id, **pred.__dict__}, indent=2))


if __name__ == "__main__":
    main()
