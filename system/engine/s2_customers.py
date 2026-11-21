from dataclasses import dataclass
import re

from engine.s3_hit_facts import BIRTH, BIRTH_MARKER, DateEvidence, Geography, former_residence
from engine.s3_hit_facts import date_evidence
from engine.s1_load import index, source_id, value
from engine.normalize import COUNTRY_PHRASE, DATE_TEXT, countries, country, entity, name_parts, words
from engine.normalize import dates as _dates

# Bank-side slash dates (CRM dob, KYC verified_dob, note dates) are day-first: in the dev
# data 9,909 slash dates have a first number above 12 and none has a second number above
# 12. Reading them day-first lets "06/09/1985" and "1985-09-06" merge as one person
# (same phone + same full DOB) and lets screened details pick the customer, which the
# practice labels expect. The convention is inferred, not documented, so it is used ONLY
# to link and select records. The DOB evidence that can clear a hit (date_evidence) keeps
# every reading, so a month-first record can never cause a wrong clearance. docs/plan/DECISIONS.md R2.
SLASH_DAY_FIRST = True


def dates(raw, day_first=None):
    return _dates(raw, SLASH_DAY_FIRST)


@dataclass(frozen=True)
class Check:
    source: str
    when: str
    name: str
    dob: str
    country: str
    document: str
    type: str


def _email(row, key):
    return value(row, key)


APPROVED_NOTE = re.compile(
    r'\b(?:approved|verified|confirmed|accepted|cleared|passed|validated|'
    r'successfully processed|completed (?:identity |[a-z]+ )?verification|successful (?:identity )?check|'
    r'check complete|validation success|matches)\b', re.I)
FAILED_NOTE = re.compile(r'\b(?:rejected|expired|unverified|failed|pending|declined|unsuccessful|forged|'
                         r'(?:not|never)\s+(?:verified|approved|accepted|validated|passed|completed|cleared))\b', re.I)
DOCUMENT = re.compile(r'\b(?:passport|national id|identity card|driving licen[cs]e|registry extract|identity document)\b', re.I)
PERSON_MARKERS = (
    r'\b(?:name on document|verified legal name|holder|legal name|name)\s*:\s*([^;.\n]+)',
    r'\b(?:shows|holder|belonging to|for|of|matches)\s+([^\d;,.(]+?)\s*(?=\s*[,;(]\s*(?:\(?\s*(?:DOB|born|\d)))',
)
NOTE_WORD = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)?")
NOTE_DOB = re.compile(rf'[\(,]\s*({DATE_TEXT})(?=\s*[,.)]|\s+(?:confirmed|passed|on|was|via|cleared|had|from|has)\b)', re.I)
NOTE_DOCUMENT = re.compile(r'\b(?:document|passport|id)\s+(?:number|no\.?)\s*[:#]?\s*([A-Z0-9-]{4,})\b', re.I)


def uppercase_names(text):
    runs = []
    current = []
    end = 0
    for token in NOTE_WORD.finditer(text):
        word = token.group()
        if not word.isupper() or word in {'ID', 'DOB', 'KYC', 'CRM', 'UK', 'US', 'USA'} or text[end:token.start()].strip():
            if len(current) >= 2:
                runs.append(' '.join(current))
            current.clear()
        if word.isupper() and word not in {'ID', 'DOB', 'KYC', 'CRM', 'UK', 'US', 'USA'}:
            current.append(word)
        end = token.end()
    if len(current) >= 2:
        runs.append(' '.join(current))
    return runs


def check_from_note(note):
    text = value(note, 'text')
    if (value(note, 'channel').lower() not in {'kyc', 'onboarding'} or not DOCUMENT.search(text)
            or not APPROVED_NOTE.search(text) or FAILED_NOTE.search(text)):
        return None
    verified_dates = list(BIRTH.finditer(text))
    if not verified_dates:
        verified_dates = list(NOTE_DOB.finditer(text))
    birth_values = {match[1] for match in verified_dates}
    other_text = text
    for match in reversed(verified_dates):
        start, end = match.span(1)
        other_text = other_text[:start] + (' ' * (end - start)) + other_text[end:]
    event_dates = set(re.findall(r'\b(?:19|20)\d{2}-\d{2}-\d{2}\b', other_text))
    when = next(iter(event_dates), value(note, 'created_at')) if len(event_dates) <= 1 else ''
    explicit = [m[1].strip() for pattern in PERSON_MARKERS for m in re.finditer(pattern, text, re.I)]
    if not explicit:
        explicit = [name for name in uppercase_names(text) if len(name_parts(name)) >= 2]
    normalized_names = {words(name): name for name in explicit if name}
    legal_name = next(iter(normalized_names.values()), '') if len(normalized_names) == 1 else ''
    doc_match = NOTE_DOCUMENT.search(text)
    doc_spans = {countries[m.group()] for m in COUNTRY_PHRASE.finditer(words(text))}
    doc_country = next(iter(doc_spans), '') if len(doc_spans) == 1 else ''
    birth = next(iter(birth_values)) if len(birth_values) == 1 else ''
    if not birth and (birth_values or BIRTH_MARKER.search(text)):
        birth = 'unresolved date from approved note'
    if not (legal_name or birth or doc_country):
        return None
    return Check(note['note_id'], when, legal_name, birth, doc_country,
                 doc_match[1] if doc_match else '', DOCUMENT.search(text)[0])


def _order_date(raw):
    try:
        candidate, = dates(raw)
        return candidate if candidate.day else None
    except ValueError:
        return None


def latest(checks):
    if not checks:
        return ()
    dated = [(check, _order_date(check.when)) for check in checks]
    most_recent = max((when for _, when in dated if when), default=None)
    return tuple(sorted((check for check, when in dated if when == most_recent or when is None),
                        key=lambda check: check.source))


def _phone(raw):
    if not re.fullmatch(r'\s*\+?[\d ().-]+\s*', raw):
        return ''
    stripped = raw.strip()
    digits = re.sub(r'\D', '', stripped)
    if len(digits) < 7 or len(set(digits)) < 3:
        return ''
    return ('+' + digits[2:] if stripped.startswith('00') else '+' + digits if stripped.startswith('+') else digits)


@dataclass
class Customer:
    id: str
    records: tuple
    link_ids: tuple
    merge_sources: tuple
    kind: str | None
    names: tuple
    verified_names: tuple
    verified_sources: tuple
    verified_uncertain: bool
    dob: DateEvidence
    geography: Geography


class Customers:
    def __init__(self, data, *, text_extraction=True):
        tables = data.tables
        accounts, onboardings = tables['accounts'], tables['onboarding']
        self.data = data
        self.text_extraction = text_extraction
        self.rows = index(tables['customers'], 'record_id', unique=True)
        self.by_account = index(accounts, lambda row: source_id(row, 'account_id'))
        self.by_login = index(accounts, lambda row: value(row, 'login_handle'))
        self.by_email = index(self.rows.values(), lambda row: _email(row, 'email'))
        self.notes_by_email = index(tables['notes'], lambda row: _email(row, 'requester_email'))
        self.accounts_by_customer = index(accounts, lambda row: source_id(row, 'customer_id'))
        applicants = index(onboardings, lambda row: (source_id(row, 'account_id'), source_id(row, 'customer_id')))
        checks = index(tables['kyc_checks'], lambda row: source_id(row, 'applicant_id'))
        self.structured_checks = {}
        comparisons = {}
        merge_edges = []

        parent = {record_id: record_id for record_id in self.rows}

        def root(item):
            while item != parent[item]:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        for record_id, row in self.rows.items():
            relevant = {}
            for account in self.accounts_by_customer.get(record_id, ()):
                for onboarding in applicants.get((source_id(account, 'account_id'), record_id), ()):
                    for check in checks.get(source_id(onboarding, 'kyc_applicant_id'), ()):
                        if value(check, 'result').lower() == 'approved':
                            key = check['check_id']
                            relevant[key] = Check(key, value(check, 'checked_at'), value(check, 'verified_name'),
                                                  value(check, 'verified_dob'), value(check, 'document_country'),
                                                  value(check, 'document_number'), value(check, 'document_type'))
            ordered_checks = tuple(relevant[key] for key in sorted(relevant))
            self.structured_checks[record_id] = ordered_checks
            kind = entity(value(row, 'entity_type'))
            keys = []
            phone = _phone(value(row, 'phone'))
            dob = dates(value(row, 'dob'))
            if kind == 'person' and phone and len(dob) == 1 and dob[0].day:
                keys.append((('phone and dob', phone, dob[0]), record_id))
            keys.extend((('document', check.document), check.source) for check in ordered_checks if check.document)
            email = _email(row, 'email')
            if text_extraction and email and len(self.by_email[email]) == 1:
                for note in self.notes_by_email.get(email, ()):
                    if NOTE_DOCUMENT.search(value(note, 'text')) and (check := check_from_note(note)) and check.document:
                        keys.append((('document', check.document), check.source))
            for key, witness in keys:
                if key not in comparisons:
                    comparisons[key] = (record_id, witness)
                else:
                    previous, old_witness = comparisons[key]
                    left, right = root(record_id), root(previous)
                    if left != right:
                        parent[max(left, right)] = min(left, right)
                        merge_edges.append((record_id, previous, witness, old_witness))
        self.root = {item: root(item) for item in self.rows}
        self.members = index(self.rows.values(), lambda row: self.root[row['record_id']])
        self.merge_edges = index(merge_edges, lambda edge: self.root[edge[0]])
        self.cache = {}

    def resolve(self, alert):
        referenced = {key: source_id(alert, key) for key in ('account_ref', 'login_handle', 'email')}
        linked = {}
        for table, key in ((self.by_account, 'account_ref'), (self.by_login, 'login_handle')):
            for account in table.get(referenced[key], ()):
                customer_id = source_id(account, 'customer_id')
                if customer_id in self.rows:
                    linked.setdefault(customer_id, set()).add(source_id(account, 'account_id'))
        for row in self.by_email.get(referenced['email'], ()):
            linked.setdefault(row['record_id'], set())
        distinct_groups = {self.root[key] for key in linked}
        if len(distinct_groups) == 1:
            seeds = linked
        elif linked:
            seeds = {key: via for key, via in linked.items() if self.fits_group(alert, self.root[key])}
        elif any(referenced.values()):
            return ()
        else:
            seeds = self.search(alert)
        grouped = {}
        for key, via in sorted(seeds.items()):
            existing, sources = grouped.setdefault(self.root[key], (key, set()))
            sources.update(via)
        return tuple(self.profile(group_id, key, tuple(sorted(sources - {''})))
                     for group_id, (key, sources) in sorted(grouped.items()))

    def fits_group(self, alert, group_id):
        return any(self.fits(alert, row) for row in self.members[group_id])

    def fits(self, alert, row):
        screened_city = words(value(alert, 'city'))
        if screened_city and screened_city != words(value(row, 'city')):
            if f' {screened_city} ' not in f' {words(value(row, "address"))} ':
                return False
        raw_country = value(alert, 'country')
        screened_country = country(raw_country)
        if raw_country and (not screened_country or screened_country not in
                            (country(value(row, 'residence_country')), country(value(row, 'nationality')))):
            return False
        screened = dates(value(alert, 'dob'))
        observed = dates(value(row, 'dob'))
        if value(alert, 'dob') and not screened:
            return False
        if screened and observed and not any(
                all(a == b or not a or not b for a, b in zip((left.year, left.month, left.day),
                                                            (right.year, right.month, right.day)))
                for left in screened for right in observed):
            return False
        names = self.data.names
        company = entity(value(row, 'entity_type')) == 'company'
        apparent = names.parts(value(alert, 'customer_name'), company)
        if not apparent or (not company and len(apparent) < 2):
            return False
        variants = [value(row, key) for key in ('full_name', 'preferred_name', 'login_name')]
        variants.extend(value(account, 'login_handle') for account in self.accounts_by_customer.get(row['record_id'], ()))
        return any(names.agree(apparent, parts) for variant in variants
                   if len(parts := names.parts(variant, company)) >= (1 if company else 2))

    def search(self, alert):
        # Geography, date parts and name variants are evaluated without a blocking exact-name key.
        return {record_id: set() for record_id, row in self.rows.items() if self.fits(alert, row)}

    def profile(self, group_id, selected, link_ids):
        key = (group_id, selected, link_ids)
        if key in self.cache:
            return self.cache[key]
        records = tuple(sorted(self.members[group_id], key=lambda row: row['record_id']))
        record_ids = tuple(row['record_id'] for row in records)
        merge_sources = tuple(sorted({identifier for edge in self.merge_edges.get(group_id, ())
                                      for identifier in edge}))
        kinds = {entity(value(row, 'entity_type')) for row in records if value(row, 'entity_type')}
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        all_checks = [check for record_id in record_ids for check in self.structured_checks[record_id]]
        geography = Geography()
        seen_notes = set()
        for row in records:
            customer_id = row['record_id']
            geography.field(row, 'residence_country', customer_id)
            geography.field(row, 'nationality', customer_id)
            if not self.text_extraction:
                continue
            email = _email(row, 'email')
            for note in self.notes_by_email.get(email, ()):
                note_id = note['note_id']
                if note_id in seen_notes:
                    continue
                seen_notes.add(note_id)
                if any(self.root[other['record_id']] != group_id for other in self.by_email[email]):
                    geography.uncertain_sources.add(note_id)
                    continue
                if check := check_from_note(note):
                    all_checks.append(check)
                if value(note, 'channel').lower() not in {'kyc', 'onboarding'}:
                    codes, uncertain = former_residence(value(note, 'text'))
                    for code in codes:
                        geography.add(code, note_id)
                    if uncertain:
                        geography.uncertain_sources.add(note_id)
        checks = latest(all_checks)
        for check in checks:
            geography.field({'doc_country': check.country}, 'doc_country', check.source)
        approved_names = tuple(sorted({check.name for check in checks if check.name}))
        names = tuple(sorted({value(row, field) for row in records for field in ('full_name', 'preferred_name') if value(row, field)}))
        name_sources = tuple(sorted({check.source for check in checks if check.name}))
        type_date_field = 'incorporation_date' if kind == 'company' else 'dob'
        dob_checks = [check for check in checks if check.dob
                      and (kind != 'company' or re.search(r'regist|incorporat', check.type, re.I))]
        if dob_checks:
            dob = date_evidence((check.dob for check in dob_checks), (check.source for check in dob_checks))
        else:
            rows_with_date = [row for row in records if row.get(type_date_field) not in ('', None)]
            dob = date_evidence((row[type_date_field] for row in rows_with_date),
                                (row['record_id'] for row in rows_with_date))
        profile = Customer(selected, record_ids, link_ids, merge_sources, kind, names, approved_names,
                           name_sources, bool(approved_names and any(not check.name for check in checks)),
                           dob, geography)
        self.cache[key] = profile
        return profile
