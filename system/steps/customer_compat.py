"""Bridge between Step 2 (customer.py) and the Step 1 matchers in system/match/.

Step 2 was written against stand-in matchers; this module keeps the names it calls
but delegates to Step 1, so customers and hits are compared by the same rules.
Only the city helpers are Step 2's own (Step 1 has no city matching).

Differences from the old stand-ins, all in the safe direction (they add agreement or
countries, never remove them):
  - canon_country()/countries_in_text() return SETS: a word that may mean several
    countries ("Congo", "Korea") maps to all of them (DECISIONS C2).
  - every country in the ISO table, not the ~60 the stand-in knew.
  - names use the policy's name_variants plus Step 1's transliteration and stacking rules.
Standard library only.
"""
from __future__ import annotations

import re

from match import countries as _countries
from match import dates as _dates
from match import names as _names

# --- names (set from the data folder by use_variants) ---------------------------
_VARIANTS: dict[str, set[str]] = {}
_LEGAL_FORMS: frozenset[str] = frozenset()


def use_variants(name_variants) -> None:
    """name_variants: data.NameVariants loaded from the data folder."""
    global _VARIANTS, _LEGAL_FORMS
    _VARIANTS = name_variants.lookup()
    _LEGAL_FORMS = frozenset(name_variants.legal_forms)


def strip_accents(s: str) -> str:
    return _names.strip_accents(s)


def name_parts(name, company: bool = False) -> list[str]:
    if not name:
        return []
    return _names.parts(str(name), company, _LEGAL_FORMS)


def names_agree_parts(pa: list[str], pb: list[str]) -> bool:
    return bool(_names.names_agree(pa, pb, _VARIANTS))


def names_agree(a, b, company: bool = False) -> bool:
    return bool(_names.names_agree(str(a or ""), str(b or ""), _VARIANTS, company, _LEGAL_FORMS))


# --- dates: (y, m, d) tuples with None for missing parts --------------------------

def _t(p) -> tuple | None:
    return (p.year, p.month, p.day) if p else None


def parse_partial(s):
    """The primary (day-first) reading of a date field. Callers that need both
    readings of an ambiguous slash date use match.dates.readings(raw)."""
    if s is None:
        return None
    return _t(_dates.parse(str(s)))


def partial_equal(a, b) -> bool:
    """Equal in every part both give (None = part not given)."""
    if a is None or b is None:
        return True
    return all(x is None or y is None or x == y for x, y in zip(a, b))


def is_full(d) -> bool:
    return d is not None and None not in d


def find_dates(text: str):
    """[(start, end, (y, m, d), raw)] for every date written in free text."""
    return [(s, e, _t(p), text[s:e]) for p, s, e in _dates.find_dates(text or "")]


# --- countries: sets of ISO2 codes ------------------------------------------------

def canon_country(s) -> set[str]:
    """A country field value -> every ISO2 code it may mean (empty if none)."""
    if s is None:
        return set()
    return _countries.canonical(str(s))


def countries_in_text(text: str, exclude_names=()) -> set[str]:
    return _countries.find_in_text(text or "", exclude_names=exclude_names)


# --- cities (Step 2's own) ----------------------------------------------------------
_CITY_ALIASES = [("munich", "munchen", "muenchen"), ("milan", "milano"), ("naples", "napoli"),
                 ("rome", "roma"), ("lisbon", "lisboa"), ("vienna", "wien"),
                 ("cologne", "koln"), ("copenhagen", "kobenhavn"), ("gothenburg", "goteborg")]
_CITY_CANON = {a: g[0] for g in _CITY_ALIASES for a in g}


def city_tokens(s):
    """'Cherry Hill, NJ / Wilmington, DE' -> {'cherry hill', 'wilmington', ...} (accent-folded)."""
    if not s:
        return set()
    out = set()
    for tok in re.split(r"[,/;]", str(s)):
        t = re.sub(r"\s+", " ", strip_accents(tok.lower()).strip())
        if len(t) > 2:
            out.add(_CITY_CANON.get(t, t))
    return out


def city_in(screened_city, city_field, address) -> bool:
    """Screened city appears in the record's city or address (accent/alias-insensitive)."""
    want = city_tokens(screened_city)
    if not want:
        return True
    have = city_tokens(city_field)
    addr = strip_accents((address or "").lower())
    for w in want:
        if w in have or w in addr:
            return True
        for alias, canon in _CITY_CANON.items():
            if canon == w and (alias in have or alias in addr):
                return True
    return False
