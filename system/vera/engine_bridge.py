"""Vera's link to the graded engine (system/engine/).

decide_all() runs the same Batch as system/run, so every decision, reason code, customer
record, reason and evidence list Vera shows is exactly what is graded. Each engine result
is converted into Vera's Decision (steps/model.py), adding two things the graded file
leaves out:

  checks   the decision tree: one row per policy check in CHECK_ORDER, built from the
           engine's own findings (which contradictions fired, which one decided) and,
           for a REVIEW, its blockers (what is unresolved and what to check next).
  missing  missing-fact codes for the email drafts (vera/emails.py MISSING), mapped
           from the same blockers.

Nothing here changes a decision. write_last_run() uses the engine's writer, so
.vera/last_run.jsonl is byte-identical to system/run's output for the same folder.
"""
from __future__ import annotations

from engine.s1_load import Dataset
from engine.s4_decide import CLEAR, REVIEW as ENGINE_REVIEW
from engine.s5_batch import Batch, verify_rows, write_rows
from steps.model import AMBIGUOUS, CHECK_ORDER, REVIEW, Decision

# reason code -> the CHECK_ORDER row it belongs to
CODE_CHECK = {
    "ENTITY_TYPE_MISMATCH": "Entity type",
    "AMBIGUOUS_CUSTOMER_RECORD": "Customer record",
    "DECEASED": "Deceased",
    "TEMPORAL_IMPOSSIBILITY": "Too young",
    "DOB_MISMATCH": "Date of birth",
    "NAME_MISMATCH_VERIFIED": "Verified name",
    "LOCATION_MISMATCH": "Location",
    "NAME_DOB_LOCATION_MATCH": "Confirmation",
    "NAME_DOB_MATCH": "Confirmation",
    "NAME_LOCATION_MATCH": "Confirmation",
}

# words in an engine blocker -> (CHECK_ORDER row, missing-fact code or None).
# First match wins; the blocker texts are written in engine/s4_decide.py.
BLOCKERS = (
    ("customer record is unresolved", "Customer record", "NO_CUSTOMER_RECORD"),
    ("several plausible customer records", "Customer record", "AMBIGUOUS_HOLDER"),
    ("entity type", "Entity type", None),
    ("customer birth date", "Date of birth", "NO_CUSTOMER_DOB"),
    ("listing birth date", "Date of birth", "NO_HIT_DOB"),
    ("countries differ", "Location", "COUNTRY_CONFLICT_NAME_DOB_AGREE"),
    ("birth information", "Date of birth", "NO_HIT_DOB"),
    ("birth dates neither", "Date of birth", None),
    ("search result", "Date of birth", "NO_HIT_DOB"),
    ("country", "Location", None),
    ("report chronology", "Too young", None),
    ("names do not establish", "Confirmation", None),
)


def _blocker(problem: str) -> tuple[str, str | None]:
    low = problem.lower()
    for words, check, code in BLOCKERS:
        if words in low:
            return check, code
    return "Confirmation", None


def _checks(result, candidates: int, customer_id) -> list[tuple[str, str, str]]:
    """Tree rows for one hit. `result` is the engine's CandidateResult for the first
    candidate, or None when no customer was found."""
    rows: dict[str, tuple[str, str]] = {}
    if customer_id is None:
        rows["Customer record"] = ("unknown", "no customer record found for this alert")
    elif customer_id == AMBIGUOUS:
        rows["Customer record"] = ("pass", f"{candidates} people fit; all reach the same decision")
    else:
        rows["Customer record"] = ("pass", f"resolved to {customer_id}")
    final = result.finding if result else None
    if result:
        fired = {f.code: f for f in result.alternatives}
        if final.decision != ENGINE_REVIEW:
            fired.setdefault(final.code, final)
        for code, f in fired.items():
            if code == final.code:
                rows[CODE_CHECK[code]] = ("fired", " ".join(f.sentences))
            else:
                rows.setdefault(CODE_CHECK[code], ("skip", "also contradicts (a higher-priority check decided)"))
        for problem, next_step in result.blockers:
            rows.setdefault(_blocker(problem)[0], ("unknown", f"{problem}. Next: {next_step}"))
    out = []
    for name in CHECK_ORDER:
        if name in rows:
            status, detail = rows[name]
        elif result is None:
            status, detail = "skip", "needs a customer record first"
        elif name == "Confirmation":
            status, detail = (("skip", "not needed: the hit is cleared") if final.decision == CLEAR
                              else ("unknown", "name and date of birth do not both agree"))
        else:
            status, detail = "pass", "no contradiction under the policy rule"
        out.append((name, status, detail))
    return out


def _missing(blockers) -> list[str]:
    codes = []
    for problem, _ in blockers:
        code = _blocker(problem)[1]
        if code and code not in codes:
            codes.append(code)
    return codes


def to_vera(alert_id: str, hit_id: str, engine_decision, *, evaluation_failed=False) -> Decision:
    """One engine Decision -> Vera's Decision (same graded fields, plus checks and missing)."""
    finding = engine_decision.finding
    split = finding.code == "AMBIGUOUS_CUSTOMER_RECORD"
    if evaluation_failed:
        checks = [(name, 'skip', 'evaluation interrupted') for name in CHECK_ORDER]
    elif split:  # candidates disagree: the tree stops at "Customer record"
        outcomes = ", ".join(f"{cid}: {r.finding.decision}" for cid, r in engine_decision.candidates)
        checks = [(n, "fired", f"candidates disagree ({outcomes})") if n == "Customer record"
                  else (n, "skip", "decided per candidate") for n in CHECK_ORDER]
    else:
        first = engine_decision.candidates[0][1] if engine_decision.candidates else None
        checks = _checks(first, len(engine_decision.candidates), engine_decision.customer_id)
    d = Decision(hit_id=hit_id, alert_id=alert_id, decision=finding.decision, reason_code=finding.code,
                 customer_record_id=engine_decision.customer_id, reason=" ".join(finding.sentences),
                 evidence=list(finding.evidence),
                 missing=_missing(engine_decision.blockers) if finding.decision == REVIEW else [],
                 checks=checks)
    d.check()
    return d


def decide_all(folder: str, on_hit=None):
    """Decide every hit with the graded engine.
    -> (Vera decisions, graded rows, engine Dataset). Fails the same way system/run does."""
    data = Dataset(folder)
    batch = Batch(data)
    decisions, rows = [], []
    total = len(data.inventory)
    for i, (alert_id, hit_id) in enumerate(data.inventory):
        if on_hit:
            on_hit(i, total)
        engine_decision = batch.decision(hit_id)
        f = engine_decision.finding
        rows.append(dict(alert_id=alert_id, hit_id=hit_id, decision=f.decision, reason_code=f.code,
                         customer_record_id=engine_decision.customer_id, reason=" ".join(f.sentences),
                         evidence=list(f.evidence)))
        decisions.append(to_vera(alert_id, hit_id, engine_decision, evaluation_failed=hit_id in batch.fallbacks))
    verify_rows(data, rows)
    return decisions, rows, data


def write_last_run(rows, data, path: str) -> None:
    write_rows(rows, path, data.input_paths)
