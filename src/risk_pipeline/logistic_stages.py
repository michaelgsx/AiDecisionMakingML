from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from .labels import RequestLabels


STAGE_REJECT = "reject_vs_non_reject"
STAGE_FREEZE = "freeze_vs_pass"
STAGE_MANUAL = "manual_review"


@dataclass
class StageModel:
    stage: str
    positive_class: str
    negative_class: str
    feature_names: list[str]
    intercept: float
    weights: dict[str, float]
    weights_l2_norm_before: float
    threshold: float
    train_size: int
    positive_rate: float
    metrics: dict[str, Any] = field(default_factory=dict)
    sklearn_C: float = 1.0

    def score(self, x: np.ndarray) -> float:
        w = np.array([self.weights.get(n, 0.0) for n in self.feature_names], dtype=np.float64)
        z = float(np.dot(x, w) + self.intercept)
        return float(1.0 / (1.0 + np.exp(-z)))


def normalize_weight_vector(
    coef: np.ndarray, intercept: float
) -> tuple[np.ndarray, float, float]:
    """L2-normalize coefficients; scale intercept consistently for ranking."""
    w = np.asarray(coef, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(w))
    if norm < 1e-12:
        return w, float(intercept), norm
    return w / norm, float(intercept) / norm, norm


def _fit_binary(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    *,
    stage: str,
    positive_class: str,
    negative_class: str,
    C: float = 1.0,
    random_state: int = 42,
) -> StageModel:
    classes = np.unique(y)
    if len(classes) < 2:
        rate = float(np.mean(y))
        eps = 1e-6
        logit = float(np.log((rate + eps) / (1 - rate + eps)))
        w_zero = np.zeros(X.shape[1], dtype=np.float64)
        return StageModel(
            stage=stage,
            positive_class=positive_class,
            negative_class=negative_class,
            feature_names=feature_names,
            intercept=logit,
            weights={n: 0.0 for n in feature_names},
            weights_l2_norm_before=0.0,
            threshold=0.5,
            train_size=len(y),
            positive_rate=rate,
            metrics={
                "n_samples": len(y),
                "warning": "single_class_fallback",
                "class_counts": {int(c): int((y == c).sum()) for c in classes},
            },
        )

    clf = LogisticRegression(
        C=C,
        class_weight="balanced",
        max_iter=2000,
        solver="lbfgs",
        random_state=random_state,
    )
    metrics: dict[str, Any] = {"n_samples": int(len(y))}

    if len(y) >= 8:
        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=0.25, random_state=random_state, stratify=y
        )
        clf.fit(X_tr, y_tr)
        prob = clf.predict_proba(X_te)[:, 1]
        metrics["holdout_accuracy"] = float(accuracy_score(y_te, prob >= 0.5))
        try:
            metrics["holdout_roc_auc"] = float(roc_auc_score(y_te, prob))
        except ValueError:
            metrics["holdout_roc_auc"] = None
    else:
        clf.fit(X, y)
        metrics["note"] = "too few samples for holdout; trained on all rows"

    w_norm, b_norm, raw_norm = normalize_weight_vector(clf.coef_[0], float(clf.intercept_[0]))
    weights = {feature_names[i]: float(w_norm[i]) for i in range(len(feature_names))}

    return StageModel(
        stage=stage,
        positive_class=positive_class,
        negative_class=negative_class,
        feature_names=feature_names,
        intercept=b_norm,
        weights=weights,
        weights_l2_norm_before=raw_norm,
        threshold=0.5,
        train_size=len(y),
        positive_rate=float(np.mean(y)),
        metrics=metrics,
        sklearn_C=C,
    )


def train_stage_reject(
    X: np.ndarray,
    labels: dict[str, RequestLabels],
    request_ids: list[str],
    feature_names: list[str],
) -> StageModel:
    y = np.array(
        [1 if labels[rid].is_reject else 0 for rid in request_ids if rid in labels],
        dtype=np.int32,
    )
    mask = np.array([rid in labels for rid in request_ids], dtype=bool)
    return _fit_binary(
        X[mask],
        y,
        feature_names,
        stage=STAGE_REJECT,
        positive_class="reject",
        negative_class="non_reject",
    )


def train_stage_freeze(
    X: np.ndarray,
    labels: dict[str, RequestLabels],
    request_ids: list[str],
    feature_names: list[str],
) -> StageModel:
    idx = [i for i, rid in enumerate(request_ids) if rid in labels and labels[rid].is_non_reject]
    if not idx:
        raise ValueError("Stage freeze_vs_pass: no non-reject labeled samples")
    X_sub = X[idx]
    # Among non-reject: uncertain lane if case ever hit freeze (even if later pass).
    y = np.array(
        [1 if labels[request_ids[i]].ever_freeze else 0 for i in idx],
        dtype=np.int32,
    )
    return _fit_binary(
        X_sub,
        y,
        feature_names,
        stage=STAGE_FREEZE,
        positive_class="freeze",
        negative_class="pass",
    )


def train_stage_manual(
    X: np.ndarray,
    labels: dict[str, RequestLabels],
    request_ids: list[str],
    feature_names: list[str],
) -> StageModel:
    """Manual review: freeze history, human analyst, or multi-step decision path."""
    y = np.array(
        [1 if labels[rid].manual_review else 0 for rid in request_ids if rid in labels],
        dtype=np.int32,
    )
    mask = np.array([rid in labels for rid in request_ids], dtype=bool)
    return _fit_binary(
        X[mask],
        y,
        feature_names,
        stage=STAGE_MANUAL,
        positive_class="manual_review",
        negative_class="auto",
    )


@dataclass
class CascadePrediction:
    stage_scores: dict[str, float]
    recommended_action: str
    rationale: list[str]


def predict_cascade(
    x: np.ndarray,
    stages: dict[str, StageModel],
) -> CascadePrediction:
    """
    Business order:
      1) reject vs non-reject
      2) freeze vs pass (only if not rejected)
      3) manual review flag (if freeze or borderline manual score)
    """
    scores: dict[str, float] = {}
    rationale: list[str] = []

    s1 = stages[STAGE_REJECT].score(x)
    scores[STAGE_REJECT] = s1
    if s1 >= stages[STAGE_REJECT].threshold:
        rationale.append(f"reject score {s1:.3f} >= threshold")
        return CascadePrediction(scores, "reject", rationale)

    s2 = stages[STAGE_FREEZE].score(x)
    scores[STAGE_FREEZE] = s2
    if s2 >= stages[STAGE_FREEZE].threshold:
        s3 = stages[STAGE_MANUAL].score(x)
        scores[STAGE_MANUAL] = s3
        if s3 >= stages[STAGE_MANUAL].threshold:
            rationale.append(f"freeze {s2:.3f}; manual review {s3:.3f}")
            return CascadePrediction(scores, "manual_review", rationale)
        rationale.append(f"freeze {s2:.3f}; low manual score")
        return CascadePrediction(scores, "freeze", rationale)

    rationale.append(f"pass path (reject={s1:.3f}, freeze={s2:.3f})")
    return CascadePrediction(scores, "pass", rationale)
