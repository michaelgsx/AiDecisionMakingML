from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import AUTO_DECIDERS


@dataclass
class RequestLabels:
    request_id: str
    final_decision: str
    ever_freeze: bool
    ever_reject: bool
    manual_review: bool
    decision_count: int

    @property
    def is_reject(self) -> bool:
        return self.final_decision == "reject"

    @property
    def is_non_reject(self) -> bool:
        return self.final_decision in ("pass", "freeze")

    @property
    def is_freeze_final(self) -> bool:
        return self.final_decision == "freeze"

    @property
    def is_pass_final(self) -> bool:
        return self.final_decision == "pass"


def _is_human_decider(decided_by: str | None) -> bool:
    if not decided_by:
        return False
    key = decided_by.strip().lower()
    if key in AUTO_DECIDERS:
        return False
    return True


def labels_from_decision_rows(
    rows: list[tuple[str, str, str | None, Any]],
) -> dict[str, RequestLabels]:
    """Aggregate risk_decisions audit rows per request_id."""
    by_request: dict[str, list[tuple[str, str | None]]] = {}
    for request_id, decision, decided_by, _created in rows:
        rid = str(request_id).strip()
        by_request.setdefault(rid, []).append((decision.strip().lower(), decided_by))

    out: dict[str, RequestLabels] = {}
    for rid, events in by_request.items():
        final = events[-1][0]
        ever_freeze = any(d == "freeze" for d, _ in events)
        ever_reject = any(d == "reject" for d, _ in events)
        human = any(_is_human_decider(db) for _, db in events)
        manual = ever_freeze or human or len(events) > 1
        out[rid] = RequestLabels(
            request_id=rid,
            final_decision=final,
            ever_freeze=ever_freeze,
            ever_reject=ever_reject,
            manual_review=manual,
            decision_count=len(events),
        )
    return out
