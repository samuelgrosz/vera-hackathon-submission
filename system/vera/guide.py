"""Decision trees.

1. GUIDE: an interactive yes/no walk through the bank's identity SOP (POLICY.md),
   in the same order Vera's engine checks things. An analyst answers questions
   about a hit and lands on a decision and reason code.
2. render_path(): draws the path one specific hit took through the checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from steps.model import CHECK_ORDER, Decision

from . import term, widgets
from .term import c


@dataclass
class Node:
    """A question (with options) or, if `result` is set, an outcome."""
    key: str
    text: str
    why: str = ""                                   # the policy behind the question
    options: list[tuple[str, str]] = field(default_factory=list)  # (label, next key)
    result: Optional[tuple[str, str]] = None        # (decision, reason code)


def _q(key, text, why, yes, no, yes_label="Yes", no_label="No"):
    return Node(key, text, why, [(yes_label, yes), (no_label, no)])


def _r(key, decision, code, text, why=""):
    return Node(key, text, why, result=(decision, code))


GUIDE: dict[str, Node] = {n.key: n for n in [
    _q("start", "Is one of them a person and the other a company?",
       "Principle 6, Type: a person can never be a company.",
       "r_type", "who"),
    _q("who", "Does more than one bank customer fit this alert, and would they get different answers?",
       "Principle 3: if several people fit and they lead to different decisions, a person decides.",
       "r_ambiguous", "dead"),
    _q("dead", "Does the listed profile give a date of death before the customer was screened?",
       "Principle 6, Time: a person who died before screening cannot be the customer.",
       "r_dead", "young"),
    _q("young", "Was the customer under 16 at the earliest dated report (or the company not yet incorporated)?",
       "Principle 6, Time. Only lines of the form 'According to …; September 10, 2025: …' count as dated reports.",
       "r_young", "dob"),
    _q("dob", "Does the customer's date of birth contradict EVERY birth date the hit gives?",
       "Principle 6, DOB: more than 1 year apart, or same year but month/day differ. "
       "One agreeing date is enough to stop this. A missing date is not a contradiction.",
       "r_dob", "name"),
    _q("name", "Does the customer's VERIFIED legal name (approved ID check) agree with none of the hit's names or aliases?",
       "Principle 6, Name, and principle 4: only the verified legal name can clear on name. "
       "Names agree when 2+ parts match, or every part of the shorter name matches.",
       "r_name", "loc"),
    _q("loc", "Does every country tied to the customer differ from every country tied to the hit?",
       "Principle 6, Location: residence and nationality on all records, the ID document, "
       "and places the notes say they lived, against the hit's countries, nationality, "
       "citizenship, address countries and place of birth.",
       "loc_guard", "kind"),
    _q("loc_guard", "Do the name AND date of birth agree?",
       "Principle 6: a country difference does not clear a hit whose name and date of birth agree.",
       "r_loc_review", "r_loc"),
    _q("kind", "Is this a person or a company?",
       "Principle 7: people are confirmed on name + date of birth, companies on name + country.",
       "person", "company", yes_label="A person", no_label="A company"),
    _q("person", "Do the name and the date of birth both agree, with nothing contradicting them?",
       "Principle 7: a birth year alone can agree with a full date, if every part both give is equal.",
       "person_loc", "r_insufficient"),
    _q("person_loc", "Do they also share a country?",
       "Either code is accepted for a confirmed match; this picks the more specific one.",
       "r_ndl", "r_nd"),
    _q("company", "Do the name and the country both agree, with nothing contradicting them?",
       "Principle 7. Company legal forms (Ltd, GmbH, SA …) are not name parts.",
       "r_nl", "r_insufficient"),

    _r("r_type", "DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH", "One is a person and the other a company, so the hit can be cleared."),
    _r("r_ambiguous", "REVIEW", "AMBIGUOUS_CUSTOMER_RECORD",
       "More than one person fits the alert and they lead to different decisions. Report customer_record_id as AMBIGUOUS."),
    _r("r_dead", "DIFFERENT_ENTITY", "DECEASED", "The listed person died before the customer was screened."),
    _r("r_young", "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", "The customer was too young (or the company didn't exist) when the reported events happened."),
    _r("r_dob", "DIFFERENT_ENTITY", "DOB_MISMATCH", "Every birth date the hit gives is contradicted by the customer's date of birth."),
    _r("r_name", "DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED", "The customer's verified legal name doesn't agree with any of the hit's names."),
    _r("r_loc", "DIFFERENT_ENTITY", "LOCATION_MISMATCH", "No country tied to the customer is tied to the hit."),
    _r("r_loc_review", "REVIEW", "INSUFFICIENT_DATA",
       "Name and date of birth agree but the countries differ. That is a conflict, not a clearance: a person decides.",
       "Ask for proof of address history (Vera can draft the email)."),
    _r("r_ndl", "SAME_ENTITY", "NAME_DOB_LOCATION_MATCH", "Name, date of birth and country all agree. Escalate to compliance."),
    _r("r_nd", "SAME_ENTITY", "NAME_DOB_MATCH", "Name and date of birth agree and nothing contradicts them. Escalate to compliance."),
    _r("r_nl", "SAME_ENTITY", "NAME_LOCATION_MATCH", "The company's name and country agree. Escalate to compliance."),
    _r("r_insufficient", "REVIEW", "INSUFFICIENT_DATA",
       "No contradiction, but not enough agreement to confirm. A person decides.",
       "Vera can draft an email asking for the missing facts."),
]}


def run_guide() -> None:
    """Interactive walk through GUIDE. Back steps up one question."""
    path: list[str] = ["start"]
    while True:
        node = GUIDE[path[-1]]
        trail = "  " + c(" › ".join(f"Q{i + 1}" for i in range(len(path))), "grey") + "\n\n"
        head = widgets.header_bar("Decision guide", f"step {len(path)}") + trail
        if node.result:
            decision, code = node.result
            body = (head + f"  {widgets.badge(decision)}  {c(code, 'bold')}\n\n"
                    + widgets.wrap(node.text) + "\n")
            if node.why:
                body += "\n" + widgets.wrap(c("Next: ", "violet", "bold") + node.why) + "\n"
            body += "\n" + c("  Your answers", "grey") + "\n" + _answers(path) + "\n\n"
            choice = widgets.select(body, ["Start again", "Back one step"])
            if choice == 0:
                path = ["start"]
            elif choice == 1 or choice is widgets.BACK:
                path.pop()
            continue
        body = (head + "  " + c(node.text, "bold") + "\n\n"
                + widgets.wrap(c(node.why, "grey"), indent=4) + "\n\n")
        choice = widgets.select(body, [label for label, _ in node.options])
        if choice is widgets.BACK:
            if len(path) == 1:
                return
            path.pop()
        elif isinstance(choice, int):
            path.append(node.options[choice][1])


def _answers(path: list[str]) -> str:
    lines = []
    for here, nxt in zip(path, path[1:]):
        node = GUIDE[here]
        label = next(lbl for lbl, k in node.options if k == nxt)
        lines.append(f"    {c('•', 'violet')} {node.text}  {c('→ ' + label, 'bold')}")
    return "\n".join(lines) if lines else "    " + c("(none yet)", "grey")


# --- a single hit's path ------------------------------------------------------------

STATUS = {
    "pass": ("✓", "green", "no conflict"),
    "fired": ("◆", "violet", "decided it"),
    "unknown": ("?", "amber", "missing data"),
    "skip": ("·", "grey", "not needed"),
    "pending": ("○", "grey", "not evaluated yet"),
}


def render_path(d: Decision) -> str:
    """Draw the checks in CHECK_ORDER with the status the engine recorded."""
    recorded = {name: (status, detail) for name, status, detail in d.checks}
    lines = [f"  {c('Policy checks for ' + d.hit_id, 'bold')}", "  │"]
    for name in CHECK_ORDER:
        status, detail = recorded.get(name, ("pending", ""))
        icon, colour, word = STATUS.get(status, STATUS["pending"])
        text = detail or word
        lines.append(f"  ├─{c(icon, colour, 'bold')} {name.ljust(16)} {c(text, colour if status == 'fired' else 'grey')}")
    lines.append(f"  ╰─▶ {widgets.badge(d.decision)} {c(d.reason_code, 'bold')}")
    if not d.checks:
        lines.append("")
        lines.append(widgets.wrap(c("No checks were recorded for this hit, so the tree cannot show "
                                    "which check decided it.", "grey")))
    return "\n".join(lines) + "\n"
