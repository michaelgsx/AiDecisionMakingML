from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .config import calibration_id
from .logistic_stages import (
    STAGE_FREEZE,
    STAGE_MANUAL,
    STAGE_REJECT,
    CascadePrediction,
    StageModel,
    predict_cascade,
    train_stage_freeze,
    train_stage_manual,
    train_stage_reject,
)
from .sql_data import load_binned_matrix


@dataclass
class RiskPipelineArtifact:
    version: str
    trained_at: str
    calibration_id: str
    feature_names: list[str]
    flatten_layout: list[dict[str, Any]]
    stages: dict[str, StageModel]
    training_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "calibration_id": self.calibration_id,
            "model_type": "logistic_regression_cascade",
            "inference_order": [STAGE_REJECT, STAGE_FREEZE, STAGE_MANUAL],
            "business_logic": {
                "step_1": "reject vs non-reject (highest priority)",
                "step_2": "freeze vs pass on non-reject (uncertain lane if ever frozen)",
                "step_3": "manual_review when freeze path and manual score high",
            },
            "feature_names": self.feature_names,
            "flatten_layout": self.flatten_layout,
            "stages": {
                name: {
                    "stage": m.stage,
                    "positive_class": m.positive_class,
                    "negative_class": m.negative_class,
                    "intercept": m.intercept,
                    "weights": m.weights,
                    "weights_normalized": True,
                    "weights_l2_norm_before_normalization": m.weights_l2_norm_before,
                    "threshold": m.threshold,
                    "train_size": m.train_size,
                    "positive_rate": m.positive_rate,
                    "metrics": m.metrics,
                }
                for name, m in self.stages.items()
            },
            "training_summary": self.training_summary,
        }


def train_pipeline(
    cal_id: str | None = None,
    *,
    version: str = "v1",
) -> RiskPipelineArtifact:
    cid = cal_id or calibration_id()
    request_ids, X, feature_names, layout = load_binned_matrix(cid)
    from .sql_data import load_decisions

    labels = load_decisions()

    labeled_ids = [rid for rid in request_ids if rid in labels]
    if len(labeled_ids) < 2:
        raise RuntimeError(
            f"Need at least 2 request_ids with both binned features and risk_decisions; got {len(labeled_ids)}"
        )

    idx = [request_ids.index(rid) for rid in labeled_ids]
    X_lab = X[idx]

    stages: dict[str, StageModel] = {}
    errors: list[str] = []

    try:
        stages[STAGE_REJECT] = train_stage_reject(X_lab, labels, labeled_ids, feature_names)
    except Exception as e:
        errors.append(f"{STAGE_REJECT}: {e}")

    try:
        stages[STAGE_FREEZE] = train_stage_freeze(X_lab, labels, labeled_ids, feature_names)
    except Exception as e:
        errors.append(f"{STAGE_FREEZE}: {e}")

    try:
        stages[STAGE_MANUAL] = train_stage_manual(X_lab, labels, labeled_ids, feature_names)
    except Exception as e:
        errors.append(f"{STAGE_MANUAL}: {e}")

    if len(stages) < 3:
        raise RuntimeError("Training failed:\n  " + "\n  ".join(errors))

    eval_rows: list[dict[str, Any]] = []
    for i, rid in enumerate(labeled_ids):
        pred = predict_cascade(X_lab[i], stages)
        eval_rows.append(
            {
                "request_id": rid,
                "final_label": labels[rid].final_decision,
                "recommended_action": pred.recommended_action,
                "scores": pred.stage_scores,
            }
        )

    summary = {
        "n_binned": len(request_ids),
        "n_labeled": len(labeled_ids),
        "label_distribution": _label_counts(labels, labeled_ids),
        "evaluation": eval_rows,
        "errors": errors,
    }

    return RiskPipelineArtifact(
        version=version,
        trained_at=datetime.now(timezone.utc).isoformat(),
        calibration_id=cid,
        feature_names=feature_names,
        flatten_layout=layout,
        stages=stages,
        training_summary=summary,
    )


def _label_counts(labels, request_ids) -> dict[str, int]:
    c: dict[str, int] = {}
    for rid in request_ids:
        key = labels[rid].final_decision
        c[key] = c.get(key, 0) + 1
    return c


def train_and_export(
    *,
    cal_id: str | None = None,
    version: str = "v1",
    upload_blob: bool = True,
    out_path: str | None = None,
) -> RiskPipelineArtifact:
    artifact = train_pipeline(cal_id=cal_id, version=version)
    payload = artifact.to_dict()

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    if upload_blob:
        from .blob_export import publish_artifact

        try:
            paths = publish_artifact(payload, version)
        except Exception as e:
            raise RuntimeError(f"Blob upload failed: {e}") from e
        artifact.training_summary["blob_paths"] = paths

    return artifact
