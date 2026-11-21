"""Load the data room into memory.

Reads the JSON Lines files (the format the graders use). Until we have them, it
falls back to the browse CSV export ("Screening Dev Set - browse - <Tab>.csv").
Values are kept as the files give them; use as_list() for list-like fields,
because the CSV export joins lists with "; ".
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

from engine.s1_load import normalize_alert

# table name -> (jsonl file, browse CSV tab, primary key or None)
TABLES = {
    "alerts":          ("alerts.jsonl",          "Alerts",          "alert_id"),
    "hits":            ("hits.jsonl",            "Hits",            "hit_id"),
    "customers":       ("customers.jsonl",       "Customers",       "record_id"),
    "accounts":        ("accounts.jsonl",        "Accounts",        None),  # joint holders: not unique
    "onboarding":      ("onboarding.jsonl",      "Onboarding",      None),
    "kyc_checks":      ("kyc_checks.jsonl",      "KYC checks",      "check_id"),
    "notes":           ("notes.jsonl",           "Notes",           "note_id"),
    "search_results":  ("search_results.jsonl",  "Search results",  "result_id"),
    "practice_labels": ("practice_labels.jsonl", "Practice labels", "hit_id"),
}


def as_list(value: Any) -> list:
    """A list field from either format: JSON list, '; '-joined string, or empty."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [p.strip() for p in value.split(";") if p.strip()]
    return [value]


def _clean(row: dict) -> dict:
    return {k: (None if v == "" else v) for k, v in row.items()}


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _read_csv(path: str) -> list[dict]:
    csv.field_size_limit(2**31 - 1)
    with open(path, encoding="utf-8", newline="") as f:
        return [_clean(r) for r in csv.DictReader(f)]


def _find_csv(folder: str, tab: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(folder, f"*- {tab}.csv")))
    return hits[0] if hits else None


def _index(rows: Iterable[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get(key)
        if k:
            out.setdefault(k, []).append(r)
    return out


@dataclass
class NameVariants:
    groups: list[list[str]] = field(default_factory=list)
    legal_forms: list[str] = field(default_factory=list)

    def lookup(self) -> dict[str, set[str]]:
        """part -> every form it counts as the same as (a part can be in several groups)."""
        out: dict[str, set[str]] = {}
        for g in self.groups:
            for p in g:
                out.setdefault(p, set()).update(g)
        return out


def _load_variants(folder: str) -> NameVariants:
    path = os.path.join(folder, "name_variants.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        nv = NameVariants()
        items = raw.items() if isinstance(raw, dict) else [("groups", raw)]
        for key, val in items:
            if "legal" in key.lower():
                nv.legal_forms = [str(x).lower() for x in val]
            elif isinstance(val, list):
                nv.groups = [[str(x).lower() for x in g] for g in val]
            elif isinstance(val, dict):  # {"muhammad": ["mohammed", ...]}
                nv.groups = [[k.lower(), *[str(x).lower() for x in v]] for k, v in val.items()]
        return nv
    # Fallback: the Policy tab of the browse export lists the same groups as text.
    policy = _find_csv(folder, "Policy")
    nv = NameVariants()
    if policy:
        with open(policy, encoding="utf-8") as f:
            lines = [l.strip().strip('"') for l in f]
        in_variants = False
        for line in lines:
            if line.startswith("name_variants.json"):
                in_variants = True
                continue
            if not in_variants or not line:
                continue
            if line.lower().startswith("company legal forms:"):
                nv.legal_forms = [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
            elif "," in line and line == line.lower():
                nv.groups.append([p.strip() for p in line.split(",") if p.strip()])
    return nv


@dataclass
class Data:
    folder: str
    tables: dict[str, list[dict]]
    variants: NameVariants
    # lookups built once
    alerts_by_id: dict[str, dict] = field(default_factory=dict)
    customers_by_id: dict[str, dict] = field(default_factory=dict)
    customers_by_email: dict[str, list[dict]] = field(default_factory=dict)
    accounts_by_id: dict[str, list[dict]] = field(default_factory=dict)
    accounts_by_login: dict[str, list[dict]] = field(default_factory=dict)
    accounts_by_customer: dict[str, list[dict]] = field(default_factory=dict)
    onboarding_by_account: dict[str, list[dict]] = field(default_factory=dict)
    kyc_by_applicant: dict[str, list[dict]] = field(default_factory=dict)
    notes_by_email: dict[str, list[dict]] = field(default_factory=dict)
    search_by_query: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def hits(self) -> list[dict]:
        return self.tables["hits"]

    @property
    def alerts(self) -> list[dict]:
        return self.tables["alerts"]


def load(folder: str, on_table=None) -> Data:
    """on_table(name) is called before each table loads (used by Vera's loading screen)."""
    tables: dict[str, list[dict]] = {}
    for name, (jsonl, tab, _key) in TABLES.items():
        if on_table:
            on_table(name)
        jpath = os.path.join(folder, jsonl)
        if os.path.exists(jpath):
            rows = _read_jsonl(jpath)
            tables[name] = [normalize_alert(row) for row in rows] if name == "alerts" else rows
            continue
        cpath = _find_csv(folder, tab)
        if cpath:
            tables[name] = _read_csv(cpath)
        else:
            tables[name] = []
            if name != "practice_labels":
                print(f"warning: no {jsonl} or '{tab}' CSV in {folder}", file=sys.stderr)

    d = Data(folder=folder, tables=tables, variants=_load_variants(folder))
    d.alerts_by_id = {a["alert_id"]: a for a in tables["alerts"]}
    d.customers_by_id = {c["record_id"]: c for c in tables["customers"]}
    d.customers_by_email = _index(tables["customers"], "email")
    d.accounts_by_id = _index(tables["accounts"], "account_id")
    d.accounts_by_login = _index(tables["accounts"], "login_handle")
    d.accounts_by_customer = _index(tables["accounts"], "customer_id")
    d.onboarding_by_account = _index(tables["onboarding"], "account_id")
    d.kyc_by_applicant = _index(tables["kyc_checks"], "applicant_id")
    d.notes_by_email = _index(tables["notes"], "requester_email")
    d.search_by_query = _index(tables["search_results"], "query")
    return d


def search_query(name: str) -> str:
    """Search results are keyed by the name's lowercase words in alphabetical order."""
    import re
    words = re.findall(r"[^\W_]+", name.lower())
    return " ".join(sorted(words))
