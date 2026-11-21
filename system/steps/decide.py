"""Step 4: decision engine. Owner: feat/decide.

Contract: decide(alert, hit, candidates) -> Decision
Stub: everything goes to REVIEW / INSUFFICIENT_DATA (scores 0, never unsafe).
"""
from __future__ import annotations

from steps.model import AMBIGUOUS, REVIEW, CustomerPicture, Decision, HitPicture


def customer_record_id(candidates: list[CustomerPicture]) -> str | None:
    if not candidates:
        return None
    if len(candidates) > 1:
        return AMBIGUOUS
    return candidates[0].primary_record_id


def decide(alert: dict, hit: HitPicture, candidates: list[CustomerPicture]) -> Decision:
    # TODO 4.1 contradiction checks  4.2 guard rails  4.3 confirmation
    # TODO 4.4 several candidates     4.5 code priority 4.6 reason text 4.7 evidence 4.8 missing
    return Decision(
        hit_id=hit.hit_id,
        alert_id=hit.alert_id,
        decision=REVIEW,
        reason_code="INSUFFICIENT_DATA",
        customer_record_id=customer_record_id(candidates),
        reason="Nothing settles this hit yet: no contradiction was found and there is "
               "not enough agreement to confirm it.",
        evidence=[],
        missing=["NOT_IMPLEMENTED"],
    )
