from __future__ import annotations

import json
from typing import Any

import numpy as np

from .config import calibration_id, sql_connect
from .feature_names import build_feature_names
from .labels import RequestLabels, labels_from_decision_rows


def load_decisions() -> dict[str, RequestLabels]:
    conn = sql_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT request_id, decision, decided_by, created_at
        FROM dbo.risk_decisions
        ORDER BY request_id, created_at ASC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return labels_from_decision_rows(rows)


def load_binned_matrix(
    cal_id: str | None = None,
) -> tuple[list[str], np.ndarray, list[str], list[dict[str, Any]]]:
    """
    Returns request_ids, X (n_samples, flatten_dim), feature_names, flatten_layout.
    """
    cid = cal_id or calibration_id()
    conn = sql_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT TOP 1 flatten_layout_json, flatten_dim
        FROM dbo.risk_feature_bin_calibrations
        WHERE calibration_id = %s
        """,
        (cid,),
    )
    cal_row = cur.fetchone()
    if not cal_row:
        raise RuntimeError(f"No calibration {cid} in risk_feature_bin_calibrations")

    layout = json.loads(cal_row[0]) if isinstance(cal_row[0], str) else cal_row[0]
    expected_dim = int(cal_row[1])
    feature_names = build_feature_names(layout)

    cur.execute(
        """
        SELECT request_id, onehot_json, flatten_dim
        FROM dbo.risk_feature_binned
        WHERE calibration_id = %s AND onehot_json IS NOT NULL
        ORDER BY request_id
        """,
        (cid,),
    )
    request_ids: list[str] = []
    vectors: list[list[float]] = []
    for request_id, onehot_raw, dim in cur.fetchall():
        vec = json.loads(onehot_raw) if isinstance(onehot_raw, str) else onehot_raw
        if not isinstance(vec, list):
            continue
        if len(vec) != expected_dim:
            raise RuntimeError(
                f"onehot length {len(vec)} != flatten_dim {expected_dim} for {request_id}"
            )
        request_ids.append(str(request_id).strip())
        vectors.append([float(v) for v in vec])

    cur.close()
    conn.close()

    if not vectors:
        raise RuntimeError("No rows in risk_feature_binned for calibration " + cid)

    return request_ids, np.asarray(vectors, dtype=np.float64), feature_names, layout


def build_training_frame(
    cal_id: str | None = None,
) -> tuple[list[str], np.ndarray, list[str], dict[str, RequestLabels]]:
    request_ids, X, feature_names, _layout = load_binned_matrix(cal_id)
    labels = load_decisions()
    return request_ids, X, feature_names, labels
