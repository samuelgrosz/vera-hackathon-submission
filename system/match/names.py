"""Step 1a: name matching (POLICY principle 8). Owner: feat/names.

Planned API:
  parts(name, is_company, variants) -> list[str]     normalised, deduped real parts
  parts_match(a, b, variants) -> bool                identical / variant / joined / edit distance
  names_agree(a, b, variants) -> (bool, matched_parts)
  is_undecidable(name) -> bool                       fewer than 2 real parts

`variants` is the dict from data.NameVariants.lookup(): part -> every form that
shares a group with it. Two parts are variants only if they share a group
(Yusuf~Joseph and Joseph~Jose, but not Yusuf~Jose).
"""
from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass

from match.translit import COVERED_SCRIPTS, script_of, transliterate

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")

NATIVE = "native"   # written in Latin letters already
TABLE = "table"     # transliterated with the fixed tables in translit.py
NONE = "none"       # a script we cannot transliterate reliably: no Latin form


@dataclass(frozen=True)
class NameForm:
    """One name as written, plus the Latin form we compare.

    original: the name exactly as in the file (kept for evidence and analysts).
    scripts:  the non-Latin scripts found in it, e.g. ('CYRILLIC',); () if all Latin.
    latin:    lowercase, accent-free Latin text with brackets removed, or None if
              part of the name is in a script we cannot transliterate.
    method:   NATIVE, TABLE or NONE.

    One-way rule: a TABLE form may count towards name AGREEMENT (which blocks a
    clearance), but must never be the reason a name DISAGREES. A NONE form takes
    part in neither. Either way the name is flagged (non_latin_name) so a person
    can see the original.
    """
    original: str
    scripts: tuple[str, ...]
    latin: str | None
    method: str

    @property
    def non_latin(self) -> bool:
        return self.method != NATIVE

    @property
    def can_disagree(self) -> bool:
        """Only names read natively in Latin letters may count as a mismatch."""
        return self.method == NATIVE


def name_form(text: str) -> NameForm:
    """'Армен Мкртчян (None, Cyrillic)' -> NameForm(latin='armen mkrtchyan', method=TABLE)."""
    original = text or ""
    # NFKC folds full-width letters (Ｓ -> S); apostrophe-like marks (ʿ) are removed
    # before the script check so 'ʿAbd' counts as Latin.
    body = unicodedata.normalize("NFKC", _BRACKETS.sub(" ", original)).lower()
    body = _APOSTROPHES.sub("", body)
    scripts = tuple(sorted({s for c in body if (s := script_of(c)) and s != "LATIN"}))
    if not scripts:
        return NameForm(original, (), strip_accents(body), NATIVE)
    if set(scripts) <= COVERED_SCRIPTS:
        latin = strip_accents(transliterate(body))
        if all(script_of(c) in (None, "LATIN") for c in latin):
            return NameForm(original, scripts, latin, TABLE)
    return NameForm(original, scripts, None, NONE)


# Latin letters that Unicode does not decompose into base letter + accent.
_NO_DECOMPOSE = str.maketrans({
    "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
    "ħ": "h", "Ħ": "H", "ı": "i", "ŧ": "t", "Ŧ": "T", "þ": "th", "Þ": "Th",
    "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss",
})


def strip_accents(text: str) -> str:
    """'José Müller' -> 'Jose Muller', 'Đặng Søren' -> 'Dang Soren'.
    Letters with no Latin base (Cyrillic, Arabic, ...) are kept as they are."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_NO_DECOMPOSE))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalise(text: str) -> str:
    """Lowercase, no accents, nothing in brackets. Punctuation is handled by tokenise()."""
    if not text:
        return ""
    text = _BRACKETS.sub(" ", str(text))
    return strip_accents(text).lower()


# Apostrophe-like marks are deleted inside a word (Sa'id -> said); the garbled
# U+FFFD the provider sometimes writes in their place counts too.
_APOSTROPHES = re.compile(r"['‘’ʼʻʾʿ`´�]")
# Anything that is not a letter or digit separates parts (spaces, hyphens, commas, dots).
_SEPARATORS = re.compile(r"[^\w]+|_")


def tokenise(text: str) -> list[str]:
    """Raw tokens of a name, in order, before initials/placeholders are removed.
    'Lefèvre-Dubois, Jean (AKA)' -> ['lefevre', 'dubois', 'jean']."""
    text = _APOSTROPHES.sub("", normalise(text))
    return [t for t in _SEPARATORS.split(text) if t]


def is_latin(token: str) -> bool:
    """True if every letter is basic Latin (after accents were stripped)."""
    return all(("a" <= c <= "z") or c.isdigit() for c in token)


# Placeholders are not name parts (principle 8: NA, N/A, TEST, UNKNOWN, XXX, NONE,
# digits "and the like"). N/A tokenises to 'n','a', which the initials rule drops.
# FNU/LNU/NFN/NMN = first/last/no (middle) name unknown, as written on US lists.
_PLACEHOLDERS = frozenset({
    "na", "nan", "test", "unknown", "unk", "none", "null", "nil", "tbc", "tba",
    "dummy", "fnu", "lnu", "nfn", "nmn", "noname",
})
_PLACEHOLDER_PATTERN = re.compile(r"\d+|x{2,}|test\d*|unknown\d*")


def is_placeholder(token: str) -> bool:
    return token in _PLACEHOLDERS or bool(_PLACEHOLDER_PATTERN.fullmatch(token))


def parts(name: str | NameForm, is_company: bool = False,
          legal_forms: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """The real name parts, in order, each distinct part once (principle 8).

    Drops single letters (initials), placeholders and, for companies, legal forms.
    Particles such as al, abu, bin, de, dos are kept as normal parts (team decision).
    A name with no Latin form (NameForm.method == NONE) has no comparable parts: [].
    """
    form = name if isinstance(name, NameForm) else name_form(name)
    if form.latin is None:
        return []
    out: list[str] = []
    for t in tokenise(form.latin):
        if len(t) < 2 or is_placeholder(t):
            continue
        if is_company and t in legal_forms:
            continue
        if t not in out:
            out.append(t)
    return out


def is_undecidable(name: str | NameForm) -> bool:
    """An individual with fewer than two real name parts can be neither confirmed nor
    cleared on name (principle 8). Callers pass the customer's best name; a hit's
    single-part alias is NOT undecidable and can still agree."""
    return len(parts(name)) < 2


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance: add, remove or change one letter = 1 edit.
    Swapping two letters is 2 edits, as POLICY principle 8 says."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def max_edits(a: str, b: str) -> int:
    """Edits allowed between two parts; the shorter part's length decides.
    <4 letters: must be identical. 4-6: 1 edit. 7+: 2 edits."""
    n = min(len(a), len(b))
    if n < 4:
        return 0
    if n <= 6:
        return 1
    return 2


# --- comparing parts ------------------------------------------------------------

Variants = dict[str, set[str]]   # data.NameVariants.lookup()

IDENTICAL, VARIANT, TYPO, VARIANT_TYPO = "identical", "variant", "typo", "variant+typo"
MAX_JOIN = 3   # up to 3 neighbouring parts may be written as one (team decision)


@functools.lru_cache(maxsize=1 << 20)
def _typo(a: str, b: str) -> bool:
    # Same answer as edit_distance(a, b) <= max_edits(a, b), but cheap exits first:
    # the length gap alone is a lower bound on the edits. Cached because customer
    # search (Step 2) compares the same common parts many thousands of times.
    k = max_edits(a, b)
    if k == 0:
        return a == b
    if abs(len(a) - len(b)) > k:
        return False
    return edit_distance(a, b) <= k


def part_match(a: str, b: str, variants: Variants) -> str | None:
    """How two parts match, or None. Checked in this fixed order so the label is stable.

    variant+typo (team decision): one part is a typo of a listed variant of the other
    (Jonny ~ Johnny ~ John). Only ONE variant hop is taken, so variant groups still do
    not chain (Yusuf ~ Joseph, Joseph ~ Jose, but no Yusuf -> Joseph -> Jose)."""
    if a == b:
        return IDENTICAL
    va, vb = variants.get(a, set()), variants.get(b, set())
    if b in va:
        return VARIANT
    if _typo(a, b):
        return TYPO
    if any(_typo(a, x) for x in sorted(vb)) or any(_typo(x, b) for x in sorted(va)):
        return VARIANT_TYPO
    return None


@dataclass(frozen=True)
class PartMatch:
    a: str            # part(s) on side A, joined with ' ' if several
    b: str
    how: str          # identical / variant / typo / variant+typo, prefixed 'joined ' if joined

    def __str__(self) -> str:
        return self.a if self.how == IDENTICAL else f"{self.a}~{self.b} ({self.how})"


@dataclass(frozen=True)
class Agreement:
    agree: bool
    matches: tuple[PartMatch, ...]
    a_parts: tuple[str, ...]
    b_parts: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.agree


def _candidates(a: list[str], b: list[str], variants: Variants):
    """Every possible pairing of a run of A with a run of B. One side is a single part;
    the other is 1..MAX_JOIN neighbouring parts written together (Abdul Salam ~ Abdulsalam)."""
    out = []
    for i in range(len(a)):
        for j in range(len(b)):
            for la, lb in [(1, 1)] + [(k, 1) for k in range(2, MAX_JOIN + 1)] \
                                   + [(1, k) for k in range(2, MAX_JOIN + 1)]:
                if i + la > len(a) or j + lb > len(b):
                    continue
                sa, sb = "".join(a[i:i + la]), "".join(b[j:j + lb])
                how = part_match(sa, sb, variants)
                if how is None:
                    continue
                if la > 1 or lb > 1:
                    how = "joined " + how
                ma = ((1 << la) - 1) << i
                mb = ((1 << lb) - 1) << j
                out.append((ma, mb, PartMatch(" ".join(a[i:i + la]), " ".join(b[j:j + lb]), how)))
    return out


def _best_pairing(cands, n_a: int, n_b: int):
    """Largest set of non-overlapping pairings (each part used once). Ties are broken
    by covering more parts, then by the fixed candidate order, so it is deterministic."""
    memo: dict[tuple[int, int, int], tuple] = {}

    def score(sel):
        cov_a = bin(sum(c[0] for c in sel)).count("1")
        cov_b = bin(sum(c[1] for c in sel)).count("1")
        return (len(sel), cov_a + cov_b)

    def go(k: int, used_a: int, used_b: int) -> tuple:
        key = (k, used_a, used_b)
        if key in memo:
            return memo[key]
        if k == len(cands):
            return ()
        best = go(k + 1, used_a, used_b)
        ma, mb, _ = cands[k]
        if not (ma & used_a) and not (mb & used_b):
            take = (cands[k],) + go(k + 1, used_a | ma, used_b | mb)
            if score(take) > score(best):
                best = take
        memo[key] = best
        return best

    return go(0, 0, 0)


def names_agree(a: str | NameForm | list[str], b: str | NameForm | list[str],
                variants: Variants, is_company: bool = False,
                legal_forms: frozenset[str] | set[str] = frozenset()) -> Agreement:
    """Principle 8: names agree when at least two parts match, or every part of the
    shorter name matches. A joined match counts as one part that covers both."""
    pa = a if isinstance(a, list) else parts(a, is_company, legal_forms)
    pb = b if isinstance(b, list) else parts(b, is_company, legal_forms)
    if not pa or not pb:
        return Agreement(False, (), tuple(pa), tuple(pb))
    chosen = _best_pairing(_candidates(pa, pb, variants), len(pa), len(pb))
    cov_a = sum(bin(c[0]).count("1") for c in chosen)
    cov_b = sum(bin(c[1]).count("1") for c in chosen)
    full_a, full_b = cov_a == len(pa), cov_b == len(pb)
    shorter_covered = (full_a and len(pa) <= len(pb)) or (full_b and len(pb) <= len(pa))
    agree = len(chosen) >= 2 or shorter_covered
    return Agreement(agree, tuple(c[2] for c in chosen), tuple(pa), tuple(pb))


def any_agree(a_names, b_names, variants: Variants, is_company: bool = False,
              legal_forms=frozenset()) -> Agreement | None:
    """First agreeing pair between two lists of names (fixed order), or None."""
    for x in a_names:
        for y in b_names:
            r = names_agree(x, y, variants, is_company, legal_forms)
            if r:
                return r
    return None


def name_mismatch(customer_name: str | NameForm, hit_names: list[str | NameForm],
                  variants: Variants, is_company: bool = False,
                  legal_forms=frozenset()) -> bool:
    """For NAME_MISMATCH_VERIFIED: the customer's verified legal name agrees with none
    of the hit's names. One-way rule: only names written natively in Latin letters can
    support a mismatch, so a hit whose names are ALL transliterated or unreadable is
    never a name mismatch; a transliterated alias can still block one by agreeing."""
    forms = [h if isinstance(h, NameForm) else name_form(h) for h in hit_names]
    if not parts(customer_name, is_company, legal_forms):
        return False
    if not any(f.can_disagree and parts(f, is_company, legal_forms) for f in forms):
        return False
    return any_agree([customer_name], forms, variants, is_company, legal_forms) is None
