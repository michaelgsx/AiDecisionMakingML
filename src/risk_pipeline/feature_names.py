from __future__ import annotations

from typing import Any


def build_feature_names(flatten_layout: list[dict[str, Any]]) -> list[str]:
    """One name per one-hot dimension (aligned with sklearn coef_ columns)."""
    names: list[str] = []
    for seg in flatten_layout:
        feat = seg["feature"]
        kind = seg["kind"]
        size = int(seg["size"])
        if kind == "categorical":
            cats = seg.get("categories") or []
            for i in range(size):
                label = cats[i] if i < len(cats) else f"slot_{i}"
                names.append(f"{feat}|{label}")
        elif kind == "numeric_bin":
            for i in range(size):
                names.append(f"{feat}|bin_{i}")
        else:
            for i in range(size):
                names.append(f"{feat}|{kind}_{i}")
    return names
