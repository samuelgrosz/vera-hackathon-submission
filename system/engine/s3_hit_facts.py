from dataclasses import dataclass, field
import re

from engine.s1_load import list_values, source_id, value
from engine.normalize import (COUNTRY_PHRASE, DATE_TEXT, MONTH, MONTHS, YEAR, PartialDate, countries, country,
                       country_end, dates, entity, present, words)

MONTH_WORDS = {words(m) for m in MONTHS} | {'a', 'an', 'the'}


@dataclass(frozen=True)
class DateEvidence:
    values: tuple = ()
    source_ids: tuple = ()
    uncertain: bool = False


def date_evidence(raw_values, source_ids, *, day_first=None):
    values = set()
    uncertain = False
    for raw in raw_values:
        if not isinstance(raw, (str, int)) or isinstance(raw, bool):
            uncertain = True
            continue
        text = str(raw).strip()
        if not present(text):
            continue
        parsed = dates(text, day_first)
        values.update(parsed)
        uncertain |= not parsed
    return DateEvidence(tuple(sorted(values)), tuple(sorted(set(filter(None, source_ids)))), uncertain)


@dataclass
class Geography:
    sources: dict = field(default_factory=dict)
    uncertain_sources: set = field(default_factory=set)

    def add(self, code, source):
        self.sources.setdefault(code, set()).add(source)

    def field(self, row, key, source, *, location=False):
        for raw in list_values(row, key):
            if not isinstance(raw, str):
                self.uncertain_sources.add(source)
                continue
            if not present(raw):
                continue
            code = (country_end if location else country)(raw)
            if code:
                self.add(code, source)
            elif not location or present(re.sub(r'\s*\[[^\]]*\]\s*$', '', raw).rsplit(',', 1)[-1]):
                self.uncertain_sources.add(source)

    def evidence(self, codes):
        return tuple(sorted({source for code in codes for source in self.sources[code] if source}))


REPORT = re.compile(rf'^\s*According to [^\n]+?;\s*({MONTH}\s+\d{{1,2}},\s+{YEAR}):', re.I)
BIRTH = re.compile(rf'\b(?:date of birth|d\.?o\.?b\.?|born|birth\s+date)\s*'
                   rf'(?:on document\b|confirmed as\b|verified as\b|recorded as\b|is\b|as\b|on\b|in\b|of\b)?'
                   rf'\s*[:=]?\s*(?:either\s+)?({DATE_TEXT})(?![\w/-])', re.I)
BIRTH_ALTERNATIVE = re.compile(rf'\s*[,;]?\s*(?:\b(?:or|and/or|alternatively|versus)\s+|(?<=[,;])\s*)'
                               rf'({DATE_TEXT})(?![\w/-])', re.I)
BIRTH_MARKER = re.compile(r'\b(?:date of birth|year of birth|birth date|birth year|birthdate|dob|born)\b', re.I)
AGE = re.compile(r'\b(?:aged?(?:\s+of)?\s*[:\-]?\s*(\d{1,3})|(\d{1,3})[- ]years?[- ]old|'
                 r'(?:he|she)\s+was\s+(\d{1,3})(?![\d/-]))\b', re.I)
APPOSITIVE_AGE = re.compile(r',\s*(\d{1,3})\s*,|\((\d{1,3})\)')
YEARS = re.compile(rf'\b{YEAR}\b')
THIRD_PARTY = re.compile(r"\b(?:spouse|wife|husband|son|daughter|father|mother|brother|sister|relative|child|friend)\b", re.I)
PHRASES = {
    'sanctions': 'a sanctioned individual',
    'pep': 'a politically exposed person',
    'enforcement': 'the subject of enforcement action',
    'adverse media': 'named in adverse media',
    'soe': 'a state owned enterprise official',
}


def name_mentions(text, names):
    positions = tuple(re.finditer(r'[^\W_]+', text))
    tokens = [words(match[0]) for match in positions]
    mentions = set()
    for name in names:
        parts = name.split()
        if len(parts) == 1 and len(parts[0]) < 4:
            continue
        expected = sorted(parts)
        for i, token in enumerate(tokens):
            if token in parts and sorted(tokens[i:i+len(parts)]) == expected:
                mentions.add((positions[i].start(), positions[i+len(parts)-1].end()))
    return tuple(sorted(mentions))


def named_subject(text, names):
    return bool(name_mentions(text, names))


def text_birth(text, names, *, search=False):
    parsed = set()
    uncertain = False
    prior_subject = False
    segments = [text] if search else re.split(r'(?<=[.!?])\s+|\n', text)
    for segment in segments:
        if not segment.strip():
            continue
        mentions = name_mentions(segment, names)
        if THIRD_PARTY.search(segment):
            prior_subject = False
            continue
        record_field = bool(re.match(r'\s*(?:dob|date of birth|year of birth|birth date|birth year)\s*[:=]', segment, re.I))
        pronoun_opening = bool(re.match(r'\s*(?:he|she|they|the subject|the individual)\s+(?:was|were|is)\b', segment, re.I))
        directly_referred = prior_subject and pronoun_opening
        prior_subject = bool(mentions) or directly_referred
        if not (mentions or record_field or directly_referred):
            continue
        matches = list(BIRTH.finditer(segment))
        for match in matches:
            adjacent = any(end <= match.start() and re.fullmatch(
                r"[\s,:('’-]*(?:(?:was|is|were|whose|had|has|with|s)\b[\s,:('’-]*){0,3}",
                segment[end:match.start()], re.I) for _, end in mentions)
            if not (adjacent or record_field and match.start() == len(segment) - len(segment.lstrip())
                    or directly_referred and match.start() < 24):
                uncertain = True
                continue
            captures = [match[1]]
            end = match.end()
            while further := BIRTH_ALTERNATIVE.match(segment, end):
                captures.append(further[1])
                end = further.end()
            if re.match(r'\s*[,;]?\s*(?:or|and/or|alternatively|versus)\b', segment[end:], re.I):
                uncertain = True
            for raw in captures:
                possibilities = dates(raw)
                parsed.update(possibilities)
                uncertain |= not possibilities
        if not matches and BIRTH_MARKER.search(segment):
            uncertain = True
        if matches:
            continue
        ages = list(AGE.finditer(segment))
        if not ages and search:
            ages = list(APPOSITIVE_AGE.finditer(segment))
            if not ages:
                ages = list(re.finditer(r'\b(?:listed|documented|profiled|identified)\s+(\d{1,3}),', segment, re.I))
        if not ages:
            continue
        years = set(map(int, YEARS.findall(segment)))
        if len(years) != 1 or len(ages) != 1:
            uncertain = True
            continue
        mark = ages[0]
        left_gap = re.compile(rf"[\s,:('’.-]*(?:(?:in|on|during)\s+(?:{DATE_TEXT})[\s,]*)?"
                              r'(?:when\s+(?:(?:he|she)\s+(?:reached\s+the\s+|was\s+))?|a\s+)?', re.I)
        right_gap = re.compile(r'[\s,:.)-]*(?:\s+(?:old\s+)?(?:individual|subject)\s+)?', re.I)
        attached = any((end <= mark.start() and left_gap.fullmatch(segment[end:mark.start()]))
                       or (mark.end() <= start and right_gap.fullmatch(segment[mark.end():start]))
                       for start, end in mentions)
        if not (attached or directly_referred):
            uncertain = True
            continue
        age = int(next(item for item in mark.groups() if item))
        year = next(iter(years))
        if age <= 125 and year - age - 1 >= 1500:
            parsed.update((PartialDate(year - age - 1), PartialDate(year - age)))
        else:
            uncertain = True
    return parsed, uncertain


def _category_gap(gap, subject_first):
    if len(gap) > 130 or re.search(r'[;!?]|\b(?:whereas|but)\b', gap, re.I):
        return False
    if not subject_first:
        return words(gap) in {'', 'named', 'known as'}
    gap = BIRTH.sub(' ', gap)
    gap = AGE.sub(' ', gap)
    gap = APPOSITIVE_AGE.sub(' ', gap)
    normalized = words(gap)
    if not normalized:
        return True
    if re.fullmatch(r'(?:(?:who|and) )?(?:was|were|is|being|became|served as|worked as|acted as|(?:had )?been)', normalized):
        return True
    if re.fullmatch(r'(?:(?:who|and) )?(?:(?:was|were|is|being|had been) )?'
                    r'(?:(?:formally|publicly|officially|subsequently) )?'
                    r'(?:listed|described|identified|designated|named|reported|noted|classified|recognized|recorded) (?:as|to be)', normalized):
        return True
    if re.fullmatch(r'(?:was|is) the (?:subject|focus) of (?:a|the) (?:\w+ ){1,8}after being', normalized):
        return True
    residence = re.fullmatch(r'(?:a )?(.+) (?:resident|citizen|national) and', normalized)
    return bool(residence and country(residence[1]))


def linked_search_country(text, category_phrase, hit_countries, names):
    category = re.compile(r'\b' + r'[\s-]+'.join(map(re.escape, category_phrase.split())) + r'\b', re.I)
    subjects = name_mentions(text, names)
    related = []
    for mark in category.finditer(text):
        if re.search(r'\b(?:not|never|no longer|denied)\b[^.;]*$', text[max(0, mark.start()-45):mark.start()], re.I):
            continue
        for start, end in subjects:
            subject_first = end <= mark.start()
            gap = text[end:mark.start()] if subject_first else text[mark.end():start]
            if _category_gap(gap, subject_first):
                related.append((mark, start, gap))
    if not related:
        return set(), 'A same-name search result leaves the listing category unverified for this person'
    missing_country = 'A same-name search result gives birth information but lacks a country tied to this listing'
    if not hit_countries:
        return set(), missing_country
    if THIRD_PARTY.search(text):
        return set(), 'A same-name search result mentions another person, leaving its birth information unattributed'
    linked = set()
    for mark, start, gap in related:
        # Attach the country to this category phrase or to a residence descriptor for this subject.
        tail = re.split(r'[!?;]|\.(?:\s|$)', text[mark.end():], maxsplit=1)[0]
        location = re.match(r'^\s+(?:in|within|from|living in|based in|of)\s+(?:the\s+)?(.+)', tail, re.I)
        if location:
            raw = location[1]
            name = COUNTRY_PHRASE.match(words(raw))
            short = re.match(r'([A-Z]{2,3})\b', raw)
            code = countries[name[0]] if name else country(short[1]) if short else None
            if code in hit_countries:
                linked.add(code)
            continue
        before = re.split(r'[!?;]|\.(?:\s|$)', text[:start])[-1]
        for descriptor in (words(gap), words(before)):
            residence = re.search(r'\b(.+?)\s+(?:resident|citizen|national)(?:\s+and)?$', descriptor)
            if residence:
                parts = residence[1].split()
                for i in range(len(parts)):
                    code = country(' '.join(parts[i:]))
                    if code:
                        if code in hit_countries:
                            linked.add(code)
                        break
    return linked, None if linked else missing_country


RESIDENCE = re.compile(r'\b(?:lived|living|resided|residing|residence|residency|resident|residential|home|moved)\b', re.I)
CUSTOMER_SUBJECT = re.compile(r'\b(?:customer|account holder|client|individual|user|subject|they|he|she|their|his|her|i|my)\b', re.I)


def former_residence(text):
    normalized = words(text)
    if (not RESIDENCE.search(normalized) or not CUSTOMER_SUBJECT.search(normalized)):
        return set(), False
    if THIRD_PARTY.search(normalized) or re.search(r'\b(?:never|did not|has not|denied|plans to|move in future)\b', normalized):
        return set(), True
    matches = {countries[match.group()] for match in COUNTRY_PHRASE.finditer(normalized)}
    if len(matches) != 1:
        return set(), True
    return matches, False


@dataclass
class HitFacts:
    id: str
    kind: str | None
    names: tuple
    name_uncertain: bool
    dob: DateEvidence
    geography: Geography
    first_report: PartialDate | None
    report_uncertain: bool
    death: DateEvidence
    problems: tuple


def query_key(value):
    return ' '.join(sorted(words(value).split()))


def _names_someone_else(text, names):
    """A run of two or more capitalised words with no part of the subject's names, such as
    "Taylor Reed" or "Federal Court", after dropping months and articles. Errs towards
    True, which only withholds a date."""
    subject = {part for name in names for part in name.split()}
    run = []
    for token in re.findall(r"[^\W\d_][\w'’-]*|\S", text) + ['.']:
        if token[0].isupper() and words(token) not in MONTH_WORDS:
            run.append(words(token))
            continue
        if len(run) >= 2 and not subject & set(run) and not country(' '.join(run)):
            return True
        run = []
    return False


def _about_subject(text, names):
    """The text names the listed subject and nobody else, and mentions no relative or
    associate, so an age in it describes the subject (failure mode B05)."""
    return (named_subject(text, names) and not THIRD_PARTY.search(text)
            and not _names_someone_else(text, names))


def first_profile_paragraph(notes):
    return re.split(r'\n\s*\n', notes, maxsplit=1)[0]


def step3_profile_dobs(paragraph, names):
    """Recover dated ages from the first paragraph when the stricter parser misses them."""
    import steps.hit as step3

    if BIRTH_MARKER.search(paragraph):
        return ()
    return tuple(sorted({PartialDate(year) for sentence in step3._sentences(paragraph)
                         if _about_subject(sentence, names)
                         for year, _, _ in step3.birth_years_from_sentence(sentence)}))


def step3_search_dobs(row, hit_countries, search_index, names):
    """Recover dated ages from eligible supplied snippets after the profile has been checked."""
    import steps.hit as step3
    from match import countries as step3_countries

    if not hit_countries:
        return {}
    found = {}
    name = value(row, 'name')
    phrase = step3.listing_phrase(step3.category(row))
    exclude = [name, query_key(name), *(a for a in list_values(row, 'aliases') if isinstance(a, str))]
    for result in sorted(search_index.get(query_key(name), ()), key=lambda r: source_id(r, 'result_id')):
        snippet = value(result, 'snippet')
        if phrase not in snippet.lower() or not _about_subject(snippet, names):
            continue
        text = ' '.join([value(result, 'title'), snippet])
        if not step3_countries.find_in_text(text, exclude_names=exclude) & hit_countries:
            continue
        for year, _, _ in step3.birth_years_from_sentence(snippet):
            found.setdefault(PartialDate(year), source_id(result, 'result_id'))
    return found


def build_hit(row, search_index, *, workbook=False, text_extraction=True):
    hit_id = source_id(row, 'hit_id')
    kind = entity(value(row, 'entity_type'))
    name = value(row, 'name')
    aliases = list_values(row, 'aliases')
    names = tuple(dict.fromkeys(item for item in (name, *aliases) if isinstance(item, str) and item))
    normalized_names = tuple(words(n) for n in names if words(n))
    name_uncertain = not name or any(not isinstance(item, str) for item in aliases)
    geography = Geography()
    for key in ('countries', 'nationality', 'citizenship', 'addresses', 'place_of_birth'):
        geography.field(row, key, hit_id, location=key in ('addresses', 'place_of_birth', 'nationality', 'citizenship'))
    dob = date_evidence(list_values(row, 'dobs'), (hit_id,))
    problems = []
    notes = value(row, 'profile_notes')
    clipped = workbook and len(notes) >= 32000
    if text_extraction and kind == 'person' and not (dob.values or dob.uncertain):
        first = first_profile_paragraph(notes)
        parsed, uncertain = text_birth(first, normalized_names)
        if not parsed and not clipped:
            recovered = step3_profile_dobs(first, normalized_names)
            if recovered:
                parsed, uncertain = recovered, False
        dob = DateEvidence(tuple(sorted(parsed)), (hit_id,) if parsed or uncertain else (), uncertain or clipped)
        if not (dob.values or dob.uncertain):
            categories = list_values(row, 'categories')
            primary = value(row, 'primary_category') or (categories[0] if categories and isinstance(categories[0], str) else '')
            phrase = PHRASES.get(words(primary), 'named on a watchlist')
            parsed, deciding, rejected = set(), set(), set()
            for result in search_index.get(query_key(name), ()):
                snippet = value(result, 'snippet')
                if not named_subject(snippet, normalized_names):
                    continue
                values, unresolved = text_birth(snippet, normalized_names, search=True)
                if not (values or unresolved):
                    continue
                linked, issue = linked_search_country(snippet, phrase, geography.sources, normalized_names)
                if linked:
                    parsed.update(values)
                    deciding.add(source_id(result, 'result_id'))
                    uncertain |= unresolved
                elif issue:
                    rejected.add(issue)
            if not (parsed or uncertain):
                found = step3_search_dobs(row, set(geography.sources), search_index, normalized_names)
                parsed, deciding = found.keys(), found.values()
            dob = DateEvidence(tuple(sorted(parsed)), tuple(sorted(set(deciding))), uncertain)
            if rejected and not dob.values:
                problems.extend(sorted(rejected, key=lambda issue: ('lacks a country' not in issue, issue)))
    if dob.uncertain:
        problems.append('The listing has birth information that cannot be read confidently')
    report_candidates = []
    report_uncertain = clipped
    if text_extraction:
        for line in notes.splitlines():
            mark = REPORT.match(line)
            if mark:
                parsed = dates(mark[1])
                if parsed and parsed[0].day:
                    report_candidates.extend(parsed)
                else:
                    report_uncertain = True
    return HitFacts(hit_id, kind, names, name_uncertain, dob, geography,
                    min(report_candidates, default=None), report_uncertain,
                    date_evidence(list_values(row, 'deceased_date'), (hit_id,)), tuple(problems))
