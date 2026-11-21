"""Step 1b: dates (POLICY principles 5, 6, 7). Owner: feat/match.

Dates are model.PartialDate(year, month?, day?). Unknown stays unknown: a missing
month or day is never filled in, and never counts as a mismatch.

API
  parse(text) -> PartialDate | None          one date field, primary reading
  readings(text) -> list[PartialDate]        every plausible reading (03/04/1988 -> 3 Apr, 4 Mar)
  parse_many(text) -> list[PartialDate]      '1996; 1997', '1965-1968', '1970s'
  find_dates(text) -> list[(PartialDate, start, end)]   dates inside free text
  birth_years_from_age(age, at) -> [y-1, y]  aged 45 in March 2019 -> [1973, 1974]
  dob_agrees(c, h) / dob_contradicts(c, h)   one customer date vs one hit date
  agreeing_dob(cs, hs) / dob_contradicts_all(cs, hs)   lists, for the decision engine
  age_range(dob, at) -> (min, max)           ages possible given partial dates
  definitely_younger_than(dob, at, years)    temporal check (under 16)
  definitely_before(a, b)                    death date before screening
  is_plausible_dob(dob, at)                  born in the future or >120 years old -> False
"""
from __future__ import annotations

import calendar
import re

from steps.model import PartialDate

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_MON = rf"(?P<mon>{_MON_ALT})\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>1[89]\d\d|20\d\d|2100)"
_SEP = r"[\s,\-/.]+"

MIN_YEAR, MAX_YEAR = 1800, 2100

# Whole-field formats, tried in this order. dd/mm is the bank's convention (dev data:
# 6,348 slash dates have a first number >12, none have a second number >12).
_FIELD_PATTERNS = [
    ("ymd", re.compile(rf"^{_YEAR}[-/.](?P<m>\d{{1,2}})[-/.](?P<d>\d{{1,2}})$")),
    ("ym", re.compile(rf"^{_YEAR}[-/.](?P<m>\d{{1,2}})$")),
    ("y", re.compile(rf"^{_YEAR}$")),
    ("num", re.compile(rf"^(?P<p>\d{{1,2}})[-/.](?P<q>\d{{1,2}})[-/.]{_YEAR}$")),
    ("dmy", re.compile(rf"^{_DAY}{_SEP}{_MON}{_SEP}{_YEAR}$", re.I)),
    ("mdy", re.compile(rf"^{_MON}{_SEP}{_DAY}{_SEP}{_YEAR}$", re.I)),
    ("my", re.compile(rf"^{_MON}{_SEP}{_YEAR}$", re.I)),
]
_CIRCA = re.compile(r"^(?:circa|ca\.?|c\.|approx\.?|approximately|about|around)\s*", re.I)


def make(year: int, month: int | None = None, day: int | None = None) -> PartialDate | None:
    """A PartialDate if the parts form a real calendar date, else None."""
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return None
    if month is None:
        return PartialDate(year) if day is None else None
    if not 1 <= month <= 12:
        return None
    if day is None:
        return PartialDate(year, month)
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return PartialDate(year, month, day)


def readings(text: str) -> list[PartialDate]:
    """Every valid reading of one date field, primary reading first.

    Only an all-numeric date with both numbers <= 12 has two readings
    (03/04/1988: 3 April primary, 4 March second). '04/13/1988' can only be
    month-first, so that is its single reading."""
    s = _CIRCA.sub("", (text or "").strip().rstrip("."))
    if not s:
        return []
    for kind, pat in _FIELD_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = m.groupdict()
        y = int(g["year"])
        if kind == "ymd":
            out = [make(y, int(g["m"]), int(g["d"]))]
        elif kind == "ym":
            out = [make(y, int(g["m"]))]
        elif kind == "y":
            out = [make(y)]
        elif kind == "num":
            p, q = int(g["p"]), int(g["q"])
            out = [make(y, q, p), make(y, p, q)]   # day-first, then month-first
        elif kind in ("dmy", "mdy"):
            out = [make(y, MONTHS[g["mon"].lower()], int(g["day"]))]
        else:  # "my"
            out = [make(y, MONTHS[g["mon"].lower()])]
        seen: list[PartialDate] = []
        for d in out:
            if d is not None and d not in seen:
                seen.append(d)
        return seen
    return []


def parse(text: str) -> PartialDate | None:
    """The primary reading of one date field, or None."""
    r = readings(text)
    return r[0] if r else None


_RANGE = re.compile(r"^(?:between\s+)?(?P<a>1[89]\d\d|20\d\d)\s*(?:-|–|to|and)\s*(?P<b>1[89]\d\d|20\d\d)$", re.I)
_DECADE = re.compile(r"^(?P<d>1[89]\d0|20\d0)'?s$", re.I)


def parse_many(text: str) -> list[PartialDate]:
    """A field holding several dates: '1996; 1997', '1965-1968', 'between 1965 and 1968',
    '1970s'. A range or decade becomes one year-only date per year. Order kept, deduped."""
    out: list[PartialDate] = []
    for chunk in re.split(r"[;|\n]", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        found: list[PartialDate] = []
        if m := _RANGE.match(chunk):
            a, b = sorted((int(m["a"]), int(m["b"])))
            if b - a <= 20:
                found = [PartialDate(y) for y in range(a, b + 1)]
        elif m := _DECADE.match(chunk):
            d = int(m["d"])
            found = [PartialDate(y) for y in range(d, d + 10)]
        else:
            p = parse(chunk)
            found = [p] if p else []
        for d in found:
            if d not in out:
                out.append(d)
    return out


# Dates inside free text (profile notes, search snippets, KYC notes). Longest forms first.
_FIND = re.compile(
    rf"\b(?:{_DAY}{_SEP}{_MON}{_SEP}{_YEAR}"
    rf"|(?P<mon2>{_MON_ALT})\.?\s+(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year2>1[89]\d\d|20\d\d)"
    rf"|(?P<year3>1[89]\d\d|20\d\d)-(?P<m3>\d{{2}})-(?P<d3>\d{{2}})"
    rf"|(?P<p4>\d{{1,2}})/(?P<q4>\d{{1,2}})/(?P<year4>1[89]\d\d|20\d\d)"
    rf"|(?P<mon5>{_MON_ALT})\.?,?\s+(?P<year5>1[89]\d\d|20\d\d))\b",
    re.I,
)
_BARE_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def find_dates(text: str, bare_years: bool = False) -> list[tuple[PartialDate, int, int]]:
    """Dates written inside free text, in order: (date, start, end).
    '17 July 1939', 'June 23, 2017', '2019-05-02', '03/04/1988' (day-first),
    'November 1998'. With bare_years=True a lone '1952' counts too (callers decide,
    since bare numbers in prose are often not dates)."""
    out: list[tuple[PartialDate, int, int]] = []
    taken: list[tuple[int, int]] = []
    for m in _FIND.finditer(text or ""):
        g = m.groupdict()
        d = None
        if g["year"]:
            d = make(int(g["year"]), MONTHS[g["mon"].lower()], int(g["day"]))
        elif g["year2"]:
            d = make(int(g["year2"]), MONTHS[g["mon2"].lower()], int(g["day2"]))
        elif g["year3"]:
            d = make(int(g["year3"]), int(g["m3"]), int(g["d3"]))
        elif g["year4"]:
            d = parse(m.group())
        elif g["year5"]:
            # 'May 2019' vs the word 'may': only accept a capitalised month here.
            if g["mon5"][0].isupper():
                d = make(int(g["year5"]), MONTHS[g["mon5"].lower()])
        if d:
            out.append((d, m.start(), m.end()))
            taken.append((m.start(), m.end()))
    if bare_years:
        for m in _BARE_YEAR.finditer(text or ""):
            if not any(a <= m.start() < b for a, b in taken):
                out.append((PartialDate(int(m.group())), m.start(), m.end()))
        out.sort(key=lambda t: t[1])
    return out


# --- birth years from an age -----------------------------------------------------

def birth_years_from_age(age: int, at: PartialDate) -> list[int]:
    """Someone aged 45 in March 2019 was born in 1973 or 1974 (principle 5).
    Always both years: we never know whether the birthday had passed."""
    y = at.year - age
    return [y - 1, y]


# --- comparing dates of birth (principles 6 and 7) --------------------------------

def dob_agrees(c: PartialDate, h: PartialDate) -> bool:
    """Same year, and every part both give is equal. A year alone agrees with a full date."""
    if c.year != h.year:
        return False
    if c.month is not None and h.month is not None:
        if c.month != h.month:
            return False
        if c.day is not None and h.day is not None and c.day != h.day:
            return False
    return True


def dob_contradicts(c: PartialDate, h: PartialDate) -> bool:
    """Birth year more than 1 year apart, or the same year with both months given and
    different (or same month, both days given and different). A year 1 apart is
    neither agreement nor contradiction: the hit stays undecided on date of birth."""
    if abs(c.year - h.year) > 1:
        return True
    if c.year == h.year and c.month is not None and h.month is not None:
        if c.month != h.month:
            return True
        if c.day is not None and h.day is not None and c.day != h.day:
            return True
    return False


def agreeing_dob(customer: list[PartialDate], hit: list[PartialDate]):
    """First (customer date, hit date) pair that agrees, in the given order, or None.
    Pass the customer's PRIMARY readings: agreement is never built on a swapped month."""
    for c in customer:
        for h in hit:
            if dob_agrees(c, h):
                return c, h
    return None


def dob_contradicts_all(customer: list[PartialDate], hit: list[PartialDate]) -> bool:
    """DOB_MISMATCH: every birth date the hit gives is contradicted. Pass ALL readings of
    the customer's date (readings()): an ambiguous 03/04 must contradict under both,
    so a misread day/month can never clear a hit. Empty on either side -> False."""
    if not customer or not hit:
        return False
    return all(dob_contradicts(c, h) for c in customer for h in hit)


# --- ages and order in time --------------------------------------------------------

def _earliest(d: PartialDate) -> tuple[int, int, int]:
    return d.year, d.month or 1, d.day or 1


def _latest(d: PartialDate) -> tuple[int, int, int]:
    m = d.month or 12
    return d.year, m, d.day or calendar.monthrange(d.year, m)[1]


def _age(born: tuple[int, int, int], at: tuple[int, int, int]) -> int:
    return at[0] - born[0] - ((at[1], at[2]) < (born[1], born[2]))


def age_range(dob: PartialDate, at: PartialDate) -> tuple[int, int]:
    """(youngest, oldest) the person could have been on `at`, given partial dates."""
    return _age(_latest(dob), _earliest(at)), _age(_earliest(dob), _latest(at))


def definitely_younger_than(dob: PartialDate, at: PartialDate, years: int = 16) -> bool:
    """True only if the person was under `years` on `at` under every reading of the
    partial dates (temporal check: uses the OLDEST possible age)."""
    return age_range(dob, at)[1] < years


def definitely_before(a: PartialDate, b: PartialDate) -> bool:
    """a ended before b began: a death date in May 2019 is before 2026-11-06."""
    return _latest(a) < _earliest(b)


def is_plausible_dob(dob: PartialDate, at: PartialDate, max_age: int = 120) -> bool:
    """False for a birth date after `at` or more than `max_age` years before it
    (e.g. a CRM 'born 1833'): Step 4 may treat such a date as unknown."""
    lo, hi = age_range(dob, at)
    return hi >= 0 and lo <= max_age
