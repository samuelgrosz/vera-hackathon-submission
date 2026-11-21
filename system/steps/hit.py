"""Step 3: hit profile (POLICY principle 5). Owner: feat/hit.

Contract: profile(hit, data) -> HitPicture

Gathers the listed person's or company's facts wherever they are, each as a Fact
carrying the record it came from (H-... for the hit, SR-... for a search result):

  1. structured fields: dobs, deceased_date, nationality, citizenship, countries,
     addresses (last part), place_of_birth. 'International'/'Europe' are not countries.
  2. date of birth from the FIRST paragraph of profile_notes: an explicit date
     ("born on 17 July 1939", "DOB: 6/20/1955") or an age at a date ("aged 30, in
     November 1998" -> 1967 or 1968).
  3. dated reports: only lines exactly "According to <source>; <Month D, YYYY>: ...".
  4. web search results, only when 1 and 2 give no date of birth: a result counts only
     if its snippet has the listing phrase of the hit's category AND a country tied to
     the hit; then its age at a date gives two possible birth years. Every kept result
     counts (we never pick one).
Companies: only the dobs field counts, as an incorporation date.

Safety: a hit with no date of birth must stay without one. A guessed date could create
a DOB_MISMATCH clearance, so nothing is inferred without an explicit date or an age
tied to a date in the same sentence. When a sentence or result gives several, all are
kept (more possible dates can only block a mismatch).
"""
from __future__ import annotations

import re

from steps.data import Data, as_list, search_query
from match import countries, dates
from match.names import strip_accents
from steps.model import Fact, HitPicture, PartialDate

PERSON = "Individual"

LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
OTHER_PHRASE = "named on a watchlist"

# "According to <source>; September 10, 2025: ..." (exact form; policy principle 5)
_DATED_REPORT = re.compile(
    r"^\s*According to (?P<src>[^;\n]+?); (?P<date>(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December) \d{1,2}, \d{4}):", re.M)

# An age written in a sentence. Each pattern captures the number as group 'n'.
_AGE_PATTERNS = [
    r"\baged\s*:?\s*(?P<n>\d{1,3})\b",                         # aged 30 / aged: 30
    r"\bat (?:the )?age (?:of )?(?P<n>\d{1,3})\b",             # at age 31 / at the age of 43
    r"\b(?:reached|turned) (?:the age of )?(?P<n>\d{1,3})\b",  # reached the age of 38 / turned 40
    r"\bage of (?:[^\d.;,]{1,60}? )?(?:was|is|being) (?P<n>\d{1,3})\b",  # the age of X was 38
    r"\bage (?:was|is|of) (?P<n>\d{1,3})\b",                   # age was 38 / age of 38
    r"\b(?P<n>\d{1,3})[- ]years?[- ]old\b",                    # 38-year-old / 42 years old
    r"\b(?P<n>\d{1,3}) years? of age\b",                       # 37 years of age
    r"\b(?:who|he|she) (?:was|is) (?P<n>\d{1,3})\b(?! (?:year|month|day|percent|%|million))",  # who was 36
    r"(?:(?<=[^\W\d_])|(?<=[.'’)])),\s*(?P<n>\d{1,3})\s*,",   # Fabio Pinna, 24, / Raković, 29,
]
_AGE_ONE = [re.compile(p, re.I) for p in _AGE_PATTERNS]
_BIRTH_CUE = re.compile(r"\b(?:born|birth|DOB|d\.o\.b\.?)\b", re.I)
MIN_AGE, MAX_AGE = 1, 110


# --- small helpers -----------------------------------------------------------------

def _texts(value) -> list[str]:
    """Every string inside a field, whatever its shape: 'a; b', ['a', 'b'], or a list of
    objects as JSONL may give ({'country': 'Spain', 'city': ...})."""
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        out: list[str] = []
        for k in ("country", "country_name", "value", "name", "full", "address", "text", "date"):
            if value.get(k):
                out += _texts(value[k])
        return out or [", ".join(str(v) for v in value.values() if isinstance(v, str) and v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return as_list(str(value))


def first_paragraph(text: str | None) -> str:
    """Profile notes up to the first blank line."""
    return re.split(r"\r?\n[ \t]*\r?\n", (text or "").strip(), maxsplit=1)[0]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", text or "") if s.strip()]


def category(hit: dict) -> str:
    """Primary category, or the first of categories when it is blank (345 dev hits)."""
    primary = (hit.get("primary_category") or "").strip()
    if primary:
        return primary
    cats = _texts(hit.get("categories"))
    return cats[0] if cats else "Other"


def listing_phrase(cat: str) -> str:
    return LISTING_PHRASES.get((cat or "").strip().lower(), OTHER_PHRASE)


def ages_in(sentence: str) -> list[int]:
    """Every age written in a sentence, in order, deduped."""
    out: list[int] = []
    spans: list[tuple[int, int]] = []
    for pat in _AGE_ONE:
        for m in pat.finditer(sentence):
            if any(a <= m.start("n") < b for a, b in spans):
                continue
            n = int(m.group("n"))
            if MIN_AGE <= n <= MAX_AGE:
                spans.append((m.start("n"), m.end("n")))
                if n not in out:
                    out.append(n)
    return out


def _dates_in(sentence: str) -> list[PartialDate]:
    """Dates in a sentence (month+year or full; bare years only if nothing better)."""
    found = [d for d, _, _ in dates.find_dates(sentence)]
    if not found:
        found = [d for d, _, _ in dates.find_dates(sentence, bare_years=True)]
    out: list[PartialDate] = []
    for d in found:
        if d not in out:
            out.append(d)
    return out


def birth_years_from_sentence(sentence: str) -> list[tuple[int, int, PartialDate]]:
    """(birth year, age, at-date) for every age paired with every date in the sentence.
    An age with no date in the same sentence gives nothing (unknown stays unknown)."""
    ages = ages_in(sentence)
    if not ages:
        return []
    out = []
    for at in _dates_in(sentence):
        for age in ages:
            for y in dates.birth_years_from_age(age, at):
                if (y, age, at) not in out:
                    out.append((y, age, at))
    return out


def explicit_dobs(sentence: str) -> list[PartialDate]:
    """'was born in 1952', 'born on 17 July 1939', 'DOB: 6/20/1955', 'date of birth 1960-04-10'.
    Only a date written right AFTER the birth cue counts."""
    m = _BIRTH_CUE.search(sentence)
    if not m:
        return []
    tail = sentence[m.end():]
    # The slash form needs every reading (6/20/1955 can only be month-first).
    slash = re.match(r"\W{0,3}(?:on |in )?(\d{1,2}/\d{1,2}/\d{4})\b", tail)
    if slash:
        return dates.readings(slash.group(1))
    found = dates.find_dates(tail, bare_years=True)
    if not found:
        return []
    first, start, _ = found[0]
    # The date must follow the cue closely ("born in 1952", "born on 17 July 1939").
    return [first] if start <= 25 else []


# --- the profile --------------------------------------------------------------------

def profile(hit: dict, data: Data) -> HitPicture:
    hid = hit["hit_id"]
    cat = category(hit)
    entity = hit.get("entity_type") or ""
    pic = HitPicture(hit_id=hid, alert_id=hit["alert_id"], entity_type=entity, primary_category=cat)
    is_person = entity == PERSON

    # names: name + aliases (original text kept; names.py does the comparing)
    for i, n in enumerate([hit.get("name"), *_texts(hit.get("aliases"))]):
        if n and n.strip():
            _add(pic.names, Fact("name", n.strip(), hid, "name" if i == 0 else "alias"))

    _structured_dobs(hit, pic, is_person)
    _death(hit, pic)
    _countries(hit, pic)
    _dated_reports(hit, pic)

    if is_person:
        if not pic.dobs:
            _dobs_from_profile(hit, pic)
        if not pic.dobs:
            _dobs_from_search(hit, pic, data)
    if not pic.dobs:
        pic.trace.append("no date of birth found")
    return pic


def _add(facts: list[Fact], fact: Fact) -> None:
    if not any(f.kind == fact.kind and f.value == fact.value and f.source == fact.source for f in facts):
        facts.append(fact)


def _structured_dobs(hit: dict, pic: HitPicture, is_person: bool) -> None:
    kind = "dob" if is_person else "incorporation"
    raw = _texts(hit.get("dobs"))
    for d in dates.parse_many("; ".join(raw)):
        _add(pic.dobs, Fact(kind, d, pic.hit_id, f"dobs field: {d}"))
    if raw:
        pic.trace.append(f"{kind} from dobs field: {'; '.join(raw)}")


def _death(hit: dict, pic: HitPicture) -> None:
    for text in _texts(hit.get("deceased_date")):
        d = dates.parse(text)
        if d:
            pic.death_date = Fact("death_date", d, pic.hit_id, f"deceased_date: {text}")
            pic.trace.append(f"died {d}")
            return
    if (hit.get("life_status") or "").strip().lower() == "deceased":
        # A death with no date cannot show the person died before screening.
        pic.trace.append("life_status Deceased but no date of death: not usable")


def _countries(hit: dict, pic: HitPicture) -> None:
    hid = pic.hit_id
    for field in ("countries", "nationality", "citizenship"):
        for text in _texts(hit.get(field)):
            for code in sorted(countries.canonical(text)):
                _add(pic.countries, Fact("country", code, hid, f"{field}: {text}"))
    for field in ("addresses", "place_of_birth"):
        for text in _texts(hit.get(field)):
            for code in sorted(countries.from_address(text)):
                _add(pic.countries, Fact("country", code, hid, f"{field}: {text[:60]}"))


def _dated_reports(hit: dict, pic: HitPicture) -> None:
    for m in _DATED_REPORT.finditer(hit.get("profile_notes") or ""):
        d = dates.parse(m.group("date"))
        if d:
            _add(pic.dated_reports, Fact("dated_report", d, pic.hit_id,
                                         f"According to {m.group('src').strip()[:60]}; {m.group('date')}"))
    pic.dated_reports.sort(key=lambda f: (f.value.year, f.value.month or 0, f.value.day or 0))
    if pic.dated_reports:
        pic.trace.append(f"earliest dated report {pic.dated_reports[0].value} "
                         f"({len(pic.dated_reports)} dated report lines)")


def _dobs_from_profile(hit: dict, pic: HitPicture) -> None:
    for sentence in _sentences(first_paragraph(hit.get("profile_notes"))):
        explicit = explicit_dobs(sentence)
        for d in explicit:
            _add(pic.dobs, Fact("dob", d, pic.hit_id, f"profile notes: {sentence.strip()[:90]}"))
        if explicit:
            continue
        for y, age, at in birth_years_from_sentence(sentence):
            _add(pic.dobs, Fact("dob", PartialDate(y), pic.hit_id, f"profile notes: aged {age} in {at}"))
    if pic.dobs:
        pic.trace.append("date of birth from the first paragraph of profile notes")


def search_results_for(name: str, data: Data) -> tuple[str, list[dict]]:
    """Search results for a name. Results are keyed by the name's lowercase words in
    alphabetical order WITHOUT accents ('Markus Müller' -> 'markus muller'), but
    data.search_query keeps accents, so try both keys (58 dev hits need the second)."""
    keys = [search_query(name), search_query(strip_accents(name))]
    for key in dict.fromkeys(keys):
        if key in data.search_by_query:
            return key, data.search_by_query[key]
    return keys[-1], []


def _dobs_from_search(hit: dict, pic: HitPicture, data: Data) -> None:
    name = hit.get("name") or ""
    query, results = search_results_for(name, data)
    if not results:
        pic.trace.append(f"search '{query}': no results")
        return
    tied = {f.value for f in pic.countries}
    if not tied:
        pic.trace.append(f"search '{query}': {len(results)} results, but the hit has no tied "
                         "country, so none can be shown to be about it")
        return
    phrase = listing_phrase(pic.primary_category)
    exclude = [name, query, *_texts(hit.get("aliases"))]
    kept = 0
    for r in sorted(results, key=lambda r: r.get("result_id") or ""):
        snippet = r.get("snippet") or ""
        if phrase not in snippet.lower():
            continue
        found = countries.find_in_text(" ".join([r.get("title") or "", snippet]), exclude_names=exclude)
        if not found & tied:
            continue
        years = birth_years_from_sentence(snippet)
        if not years:
            continue
        kept += 1
        for y, age, at in years:
            _add(pic.dobs, Fact("dob", PartialDate(y), r["result_id"],
                                f"search result: {phrase}, {age} in {at}"))
    pic.trace.append(f"search '{query}': {len(results)} results, {kept} kept "
                     f"(phrase '{phrase}' + a tied country)")
