#!/usr/bin/env python3
"""
Train 3-stage logistic risk pipeline from SQL binned features + risk_decisions.

Stages (cascade at inference):
  1. reject vs non-reject
  2. freeze vs pass (non-reject; freeze = ever entered freeze)
  3. manual_review (human / multi-step path)

Upload normalized weights to Azure Blob container `logistic` on account `airagblob`.

Usage:
    cd AiDecisionMakingML
    pip install -r requirements.txt
    cp .env.example .env   # or rely on ../AiDecisionMakingBackend/db/.env for SQL
    python train.py
    python train.py --no-upload --out artifacts/risk_pipeline_v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from risk_pipeline.config import calibration_id, load_env  # noqa: E402
from risk_pipeline.pipeline import train_and_export  # noqa: E402


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Train logistic risk cascade and export to blob.")
    parser.add_argument("--calibration-id", default="", help="risk_feature_binned calibration_id")
    parser.add_argument("--version", default="v1", help="Artifact version tag for blob name")
    parser.add_argument("--no-upload", action="store_true", help="Skip Azure Blob upload")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "risk_pipeline_latest.json",
        help="Local JSON path (default: artifacts/risk_pipeline_latest.json)",
    )
    args = parser.parse_args()

    cal = args.calibration_id.strip() or calibration_id()
    print(f"Training with calibration_id={cal} ...")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    artifact = train_and_export(
        cal_id=cal,
        version=args.version,
        upload_blob=not args.no_upload,
        out_path=str(args.out),
    )

    print(f"\nTrained at {artifact.trained_at}")
    for name, stage in artifact.stages.items():
        print(
            f"  [{name}] n={stage.train_size} pos_rate={stage.positive_rate:.3f} "
            f"L2_before={stage.weights_l2_norm_before:.4f}"
        )
    print(f"\nLocal artifact: {args.out}")
    if paths := artifact.training_summary.get("blob_paths"):
        print("Blob uploads:")
        for p in paths:
            print(f"  {p}")
    else:
        print("(Blob upload skipped or not configured)")


if __name__ == "__main__":
    main()
