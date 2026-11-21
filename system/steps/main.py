"""Pipeline: load data, resolve each side, decide each hit, write one line per hit."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # system/, works under -I

from steps import customer  # noqa: E402
from steps import decide  # noqa: E402
import steps.hit as hitmod  # noqa: E402
from steps.data import load  # noqa: E402
from steps.model import REVIEW, Decision  # noqa: E402


def safe_review(hit: dict, err: Exception) -> Decision:
    """If anything crashes on a hit, never clear it: send it to review."""
    return Decision(
        hit_id=hit["hit_id"], alert_id=hit["alert_id"], decision=REVIEW,
        reason_code="INSUFFICIENT_DATA", customer_record_id=None,
        reason="The system could not evaluate this hit, so it is left for review.",
        evidence=[], missing=[f"ERROR: {type(err).__name__}: {err}"],
    )


def run(folder: str, out_path: str) -> None:
    t0 = time.time()
    data = load(folder)
    decisions, errors = decide_all(data)
    write_output(decisions, out_path)
    counts = Counter(d.decision for d in decisions)
    print(f"{len(decisions)} hits -> {out_path} in {time.time() - t0:.1f}s; "
          f"{dict(sorted(counts.items()))}; errors={errors}", file=sys.stderr)


def write_output(decisions: list[Decision], out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for d in decisions:
            f.write(json.dumps(d.to_output(), ensure_ascii=False) + "\n")


def decide_all(data, on_hit=None) -> tuple[list[Decision], int]:
    """Decide every hit, in file order. on_hit(i, total) reports progress (Vera UI)."""
    resolved: dict[str, list] = {}  # alert_id -> candidates (resolve each alert once)
    decisions: list[Decision] = []
    errors = 0
    total = len(data.hits)
    for i, h in enumerate(data.hits):
        if on_hit:
            on_hit(i, total)
        try:
            alert = data.alerts_by_id.get(h["alert_id"], {})
            if h["alert_id"] not in resolved:
                resolved[h["alert_id"]] = customer.resolve(alert, data)
            d = decide.decide(alert, hitmod.profile(h, data), resolved[h["alert_id"]])
            d.check()
        except Exception as e:  # noqa: BLE001
            errors += 1
            if os.environ.get("DEBUG"):
                traceback.print_exc()
            d = safe_review(h, e)
        decisions.append(d)
    return decisions, errors


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: main.py <data folder> <output file>")
    run(sys.argv[1], sys.argv[2])
