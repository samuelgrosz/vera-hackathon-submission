"""Shared contract between pipeline stages. Change only in a small, separate PR.

Every fact carries the ID of the record it came from (POLICY principle 9), so the
decision engine can cite exactly the records that decided a hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# --- decisions and reason codes (POLICY.md "Reason codes") ---------------------

DIFFERENT = "DIFFERENT_ENTITY"
SAME = "SAME_ENTITY"
REVIEW = "REVIEW"

REASON_CODES: dict[str, tuple[str, ...]] = {
    DIFFERENT: (
        "TEMPORAL_IMPOSSIBILITY",
        "DOB_MISMATCH",
        "LOCATION_MISMATCH",
        "NAME_MISMATCH_VERIFIED",
        "ENTITY_TYPE_MISMATCH",
        "DECEASED",
    ),
    SAME: ("NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH", "NAME_LOCATION_MATCH"),
    REVIEW: ("INSUFFICIENT_DATA", "AMBIGUOUS_CUSTOMER_RECORD"),
}

AMBIGUOUS = "AMBIGUOUS"  # value of customer_record_id when more than one person fits

# The order the decision engine walks the policy (principles 6 and 7).
CHECK_ORDER = (
    "Entity type",          # person vs company        -> ENTITY_TYPE_MISMATCH
    "Customer record",      # who is the customer?     -> AMBIGUOUS_CUSTOMER_RECORD
    "Deceased",             # death before screening   -> DECEASED
    "Too young",            # under 16 at first report -> TEMPORAL_IMPOSSIBILITY
    "Date of birth",        # every hit DOB contradicted -> DOB_MISMATCH
    "Verified name",        # legal name agrees with none -> NAME_MISMATCH_VERIFIED
    "Location",             # every country differs    -> LOCATION_MISMATCH
    "Confirmation",         # name + DOB (+ country)   -> SAME_ENTITY codes
)

PERSON = "Individual"
COMPANY = "Company"


# --- building blocks -----------------------------------------------------------

@dataclass(frozen=True, order=True)
class PartialDate:
    """A date where month and day may be unknown. Unknown stays unknown."""
    year: int
    month: Optional[int] = None
    day: Optional[int] = None

    def __str__(self) -> str:
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True)
class Fact:
    """One fact and where it came from.

    kind:   what the fact is, e.g. "dob", "country", "name", "legal_name",
            "dated_report", "death_date", "incorporation".
    value:  the normalised value (PartialDate, ISO country code, name string, ...).
    source: the record ID that supports it, exactly as in the files
            (CUS-..., KYC-..., NOTE-..., H-..., SR-...).
    origin: free text for debugging/explain, e.g. "kyc approved 2023-11-14",
            "profile notes: aged 45 in March 2019". Never used for decisions.
    when:   ISO date of the source record (note created_at, check checked_at), if any.
    raw:    the value exactly as the file wrote it (e.g. "03/04/1988"). Step 4 uses it
            for dates that can be read two ways: match.dates.readings(fact.raw).
    """
    kind: str
    value: Any
    source: str
    origin: str = ""
    when: Optional[str] = None
    raw: Optional[str] = None


# --- the two pictures being compared --------------------------------------------

@dataclass
class Verified:
    """The most recent APPROVED identity evidence for a person (principle 4): a KYC
    provider check (KYC-...) or a kyc-channel note by onboarding (NOTE-...).
    Rejected and expired checks are never used."""
    source: str
    name: Optional[str]
    dob: Optional[PartialDate]
    doc_countries: tuple[str, ...]      # ISO2 codes of the document country (usually one)
    when: Optional[str]                 # ISO date used to pick "most recent"
    doc_number: Optional[str] = None
    dob_raw: Optional[str] = None


@dataclass
class CustomerPicture:
    """Everything known about ONE person/company in the bank (principle 3).

    record_ids lists every CRM record merged into this person; any of them is an
    acceptable customer_record_id. primary_record_id (the smallest) is the one we report.

    Fact kinds used by Step 2:
      names      full_name, preferred_name, login_name, verified_name
      dobs       crm_dob, verified_dob
      countries  residence, nationality, document, lived (from support/app notes)
      cities     city, address
    """
    primary_record_id: str
    record_ids: list[str]
    entity_type: str                                  # PERSON or COMPANY
    names: list[Fact] = field(default_factory=list)
    legal_name: Optional[Fact] = None                 # = the verified_name fact, if any
    verified: Optional[Verified] = None
    dobs: list[Fact] = field(default_factory=list)
    countries: list[Fact] = field(default_factory=list)
    cities: list[Fact] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)  # every record a fact came from
    link: str = ""          # how the alert reached this person: account | login | email | search
    trace: list[str] = field(default_factory=list)    # resolution notes, for explain/README


@dataclass
class HitPicture:
    """Everything known about the listed person/company (principle 5)."""
    hit_id: str
    alert_id: str
    entity_type: str
    primary_category: str
    names: list[Fact] = field(default_factory=list)       # name + aliases
    dobs: list[Fact] = field(default_factory=list)        # every possible birth date
    countries: list[Fact] = field(default_factory=list)   # countries tied to the hit
    dated_reports: list[Fact] = field(default_factory=list)
    death_date: Optional[Fact] = None
    trace: list[str] = field(default_factory=list)


# --- output -----------------------------------------------------------------------

OUTPUT_FIELDS = ("hit_id", "alert_id", "decision", "reason_code",
                 "customer_record_id", "reason", "evidence")


@dataclass
class Decision:
    hit_id: str
    alert_id: str
    decision: str
    reason_code: str
    customer_record_id: Optional[str]   # CUS-..., AMBIGUOUS, or None
    reason: str
    evidence: list[str]
    # Internal only (step 4.8 / step 7): what would settle a REVIEW. Not written.
    missing: list[str] = field(default_factory=list)
    # Internal only: the path through the policy checks, for Vera's decision-tree view.
    # Each entry: (check name from CHECK_ORDER, status, detail) where status is
    # "pass" (no conflict), "fired" (decided the hit), "unknown" (missing data) or "skip".
    checks: list[tuple[str, str, str]] = field(default_factory=list)

    def check(self) -> None:
        if self.decision not in REASON_CODES:
            raise ValueError(f"{self.hit_id}: bad decision {self.decision}")
        if self.reason_code not in REASON_CODES[self.decision]:
            raise ValueError(f"{self.hit_id}: {self.reason_code} does not belong to {self.decision}")

    def to_output(self) -> dict:
        """The graded JSON line, in a fixed field order. Evidence is sorted and deduped
        so the output is identical from run to run."""
        self.check()
        row = {
            "hit_id": self.hit_id,
            "alert_id": self.alert_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "customer_record_id": self.customer_record_id,
            "reason": self.reason,
            "evidence": sorted(set(self.evidence)),
        }
        return {k: row[k] for k in OUTPUT_FIELDS}
