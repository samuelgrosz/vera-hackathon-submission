"""Draft information-request emails for hits Vera leaves for review.

Two kinds of draft:
  internal  to the screening review team: the full picture, IDs and what's missing.
  customer  a neutral "please update your details" request. It must NEVER mention
            screening, watchlists, sanctions, PEPs, alerts or matches: telling a
            customer can count as tipping off under anti-money-laundering rules.
            tipping_off_check() enforces this, and a draft that fails is not written.

Drafts are files (.eml) and are never sent. No dates or random IDs go in them, so the
same run always produces the same drafts.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from steps.data import Data, as_list
from steps.model import REVIEW, Decision

REVIEW_TEAM = "screening-review@bank.example"
SENDER = "vera@bank.example"
SUPPORT_SENDER = "customer-support@bank.example"

# missing-fact code -> (what the review team needs, what the customer can be asked for or None)
MISSING = {
    "NO_CUSTOMER_DOB": ("The customer's date of birth is not on file.",
                        "your date of birth, with a copy of a valid photo ID (passport, national ID card or driving licence)"),
    "KYC_EXPIRED": ("The customer's identity check has expired or was rejected, so there is no verified legal name or date of birth.",
                    "a current photo ID so we can refresh your identity verification"),
    "COUNTRY_CONFLICT_NAME_DOB_AGREE": ("Name and date of birth agree but the countries differ.",
                                        "proof of your addresses over the last five years (for example a utility bill or bank statement for each)"),
    "NAME_UNDECIDABLE": ("The customer's name on file has fewer than two real name parts.",
                         "your full legal name as it appears on your ID document"),
    "AMBIGUOUS_HOLDER": ("More than one customer fits this alert (for example joint account holders). Confirm which person was screened.", None),
    "NO_CUSTOMER_RECORD": ("No customer record could be found for this alert. Locate the customer before deciding.", None),
    "NO_HIT_DOB": ("The listed profile gives no date of birth, in its fields, notes or matching search results. Research the listed person's date of birth.", None),
    "UNRESOLVED": ("The records on file neither establish a match nor rule it out. Review the listing and the customer's records side by side.", None),
}

FORBIDDEN = re.compile(
    r"screen|watch ?list|sanction|\bpep\b|politically|exposed|adverse|enforcement|alert|"
    r"\bhit\b|\bmatch|listed|suspicio|investigat|compliance|aml|money laundering|terror",
    re.IGNORECASE,
)


def tipping_off_check(text: str) -> list[str]:
    """Words that must not appear in anything sent to a customer."""
    return sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)})


@dataclass
class Draft:
    kind: str           # "internal" or "customer"
    filename: str
    message: EmailMessage

    def preview(self) -> str:
        m = self.message
        return (f"From:    {m['From']}\nTo:      {m['To']}\nSubject: {m['Subject']}\n\n"
                + m.get_content().rstrip())


def infer_missing(d: Decision, alert: dict, hit: dict, record_id: Optional[str]) -> list[str]:
    """Use the engine's missing list (mapped from its REVIEW blockers in engine_bridge.py);
    when it maps to no code, make a best guess from the raw records so the drafts stay specific. Guesses only shape the wording of
    a draft; they never change a decision."""
    codes = [m for m in d.missing if m in MISSING]
    if codes:
        return codes
    guess = []
    if d.customer_record_id == "AMBIGUOUS":
        guess.append("AMBIGUOUS_HOLDER")
    elif not record_id and not any(alert.get(k) for k in ("account_ref", "login_handle", "email")):
        guess.append("NO_CUSTOMER_RECORD")
    if not alert.get("dob") and alert.get("entity_type") != "Company":
        guess.append("NO_CUSTOMER_DOB")
    if not as_list(hit.get("dobs")) and hit.get("entity_type") != "Company":
        guess.append("NO_HIT_DOB")
    return guess or ["UNRESOLVED"]


def _customer_contact(data: Data, alert: dict, record_id: Optional[str]) -> tuple[Optional[str], str]:
    rec = data.customers_by_id.get(record_id or "") if record_id and record_id != "AMBIGUOUS" else None
    email = (rec or {}).get("email") or alert.get("email")
    # Only greet by name from a resolved record; screened names can be surname-first,
    # nicknames or typos ("Dear FIORANI"), so otherwise stay neutral.
    full = ((rec or {}).get("full_name") or "").split()
    first = (rec or {}).get("preferred_name") or (full[0] if full else "")
    return email, first.title() if first.isupper() else first


def drafts_for(d: Decision, data: Data) -> list[Draft]:
    """Drafts for one REVIEW hit: always an internal one, plus a customer one when the
    customer can supply something and we have an address."""
    if d.decision != REVIEW:
        return []
    alert = data.alerts_by_id.get(d.alert_id, {})
    hit = next((h for h in data.hits if h["hit_id"] == d.hit_id), {})
    codes = infer_missing(d, alert, hit, d.customer_record_id)
    out = [_internal(d, alert, hit, codes)]
    asks = [MISSING[c][1] for c in codes if MISSING[c][1]]
    email, first = _customer_contact(data, alert, d.customer_record_id)
    if asks and email:
        draft = _customer(d, email, first, asks)
        if not tipping_off_check(draft.message.get_content()) and not tipping_off_check(draft.message["Subject"]):
            out.append(draft)
    return out


def _internal(d: Decision, alert: dict, hit: dict, codes: list[str]) -> Draft:
    screened = ", ".join(x for x in (alert.get("customer_name"), alert.get("dob"),
                                      alert.get("country"), alert.get("city")) if x) or "unknown"
    listed = f"{hit.get('name', '?')} ({hit.get('primary_category') or (as_list(hit.get('categories')) or ['?'])[0]})"
    needs = "\n".join(f"  - {MISSING[c][0]}" for c in codes)
    evidence = ", ".join(sorted(set(d.evidence))) or "none recorded"
    body = f"""Hello team,

Vera could not settle hit {d.hit_id} on alert {d.alert_id} and has left it for review.

Customer as screened:   {screened}
Customer record:        {d.customer_record_id or 'not found'}
Listed profile:         {listed}
Vera's decision:        {d.decision} / {d.reason_code}

Why:
  {d.reason}

What would settle it:
{needs}

Records cited: {evidence}

Draft generated by Vera. It has not been sent.
"""
    m = EmailMessage()
    m["From"] = SENDER
    m["To"] = REVIEW_TEAM
    m["Subject"] = f"[Vera] Review needed: alert {d.alert_id} / hit {d.hit_id}"
    m.set_content(body, cte="8bit")
    return Draft("internal", f"{d.alert_id}_{d.hit_id}_internal.eml", m)


def _customer(d: Decision, email: str, first: str, asks: list[str]) -> Draft:
    greeting = f"Dear {first}," if first else "Dear customer,"
    items = "\n".join(f"  - {a}" for a in asks)
    body = f"""{greeting}

We regularly check that the details we hold for our customers are up to date. Could you help us by sending:

{items}

You can reply to this email or upload documents securely in the app, under Profile > Documents.

Thank you for your help.

Kind regards,
Customer Support
"""
    m = EmailMessage()
    m["From"] = SUPPORT_SENDER
    m["To"] = email
    m["Subject"] = "Please help us keep your details up to date"
    m.set_content(body, cte="8bit")
    return Draft("customer", f"{d.alert_id}_{d.hit_id}_customer.eml", m)


def save(drafts: list[Draft], folder: str) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    paths = []
    for dr in drafts:
        p = os.path.join(folder, dr.filename)
        with open(p, "wb") as f:
            f.write(bytes(dr.message))
        paths.append(p)
    return paths
