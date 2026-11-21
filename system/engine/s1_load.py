from collections import defaultdict
import csv
import json
from pathlib import Path

from engine.normalize import Names, words


TABLES = {
    'alerts': 'Alerts', 'hits': 'Hits', 'customers': 'Customers', 'accounts': 'Accounts',
    'onboarding': 'Onboarding', 'kyc_checks': 'KYC checks', 'notes': 'Notes',
    'search_results': 'Search results',
}
REQUIRED_IDS = {'alerts': 'alert_id', 'hits': 'hit_id', 'customers': 'record_id',
                'kyc_checks': 'check_id', 'notes': 'note_id', 'search_results': 'result_id'}
PREFIX = 'Screening Dev Set - browse - '


class InputError(ValueError):
    """An input problem preventing an authoritative run."""


def list_values(row, key):
    value = row.get(key)
    if isinstance(value, list):
        return tuple(item for item in value if item is not None)
    if value in (None, ''):
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(';') if part.strip())
    return (value,)


def value(row, key):
    raw = row.get(key)
    return raw.strip() if isinstance(raw, str) else ''


def source_id(row, key):
    raw = row.get(key)
    return raw if isinstance(raw, str) and raw.strip() else ''


def normalize_alert(row):
    """Expose the screened attributes from the official JSONL customer object."""
    customer = row.get('customer')
    if not isinstance(customer, dict):
        return row
    return {
        'customer_name': customer.get('name'),
        'entity_type': customer.get('entity_type'),
        'dob': customer.get('dob'),
        'country': customer.get('country'),
        'city': customer.get('city'),
        **row,
    }


def index(rows, key, *, unique=False):
    if unique:
        return {row[key]: row for row in rows}
    found = defaultdict(list)
    for row in rows:
        item = key(row)
        if item:
            found[item].append(row)
    return found


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise InputError(f'invalid CSV header in {path.name}')
        for row in reader:
            if None in row or any(item is None for item in row.values()):
                raise InputError(f'invalid CSV row width in {path.name}, line {reader.line_num}')
            yield row


def json_rows(path):
    with path.open(encoding='utf-8-sig') as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputError(f'invalid JSON in {path.name}, line {line_number}') from exc
            if not isinstance(row, dict):
                raise InputError(f'expected an object in {path.name}, line {line_number}')
            yield row


def read_names(root, csv_mode):
    if csv_mode:
        path = root / (PREFIX + 'Policy.csv')
        groups, legal_forms = [], []
        in_variants = False
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.reader(handle, strict=True):
                if not row:
                    continue
                line = row[0].strip()
                if line == 'name_variants.json':
                    in_variants = True
                elif in_variants and line.casefold().startswith('company legal forms:'):
                    legal_forms = line.split(':', 1)[1].split(',')
                elif in_variants and ',' in line and not line.endswith('.'):
                    groups.append(line.split(','))
    else:
        path = root / 'name_variants.json'
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise InputError('name_variants.json must contain an object')
        legal_forms = next((v for k, v in data.items() if 'legal' in k.lower() or 'suffix' in k.lower()), None)
        groups = next((v for k, v in data.items() if 'given' in k.lower() or 'variant' in k.lower() or 'group' in k.lower()), None)
        if isinstance(groups, dict):
            groups = [[key, *variants] for key, variants in groups.items()
                      if isinstance(variants, list)]
        if isinstance(legal_forms, dict):
            legal_forms = list(legal_forms)
        if not isinstance(groups, list) or not isinstance(legal_forms, list):
            raise InputError('name_variants.json must supply name groups and company legal forms')
        if not all(isinstance(group, list) and all(isinstance(name, str) for name in group) for group in groups):
            raise InputError('name variant groups must be lists of strings')
        if not all(isinstance(form, str) for form in legal_forms):
            raise InputError('company legal forms must be strings')
    if csv_mode and (not groups or not legal_forms):
        raise InputError('CSV policy export has no name groups or company legal forms')
    return Names([tuple(words(part.strip()) for part in group) for group in groups], legal_forms), path


class Dataset:
    def __init__(self, root, mode=None):
        root = Path(root)
        if mode == 'csv' or (mode is None and not (root / 'alerts.jsonl').exists()
                            and (root / (PREFIX + 'Alerts.csv')).exists()):
            self.mode = 'csv'
        else:
            self.mode = 'jsonl'
        self.tables = {}
        self.input_paths = set()
        self.valid_ids = set()
        for table, csv_name in TABLES.items():
            is_csv = self.mode == 'csv'
            path = root / (PREFIX + csv_name + '.csv' if is_csv else table + '.jsonl')
            self.input_paths.add(path.resolve())
            rows = list((csv_rows if is_csv else json_rows)(path))
            if table == 'alerts':
                rows = [normalize_alert(row) for row in rows]
            key = REQUIRED_IDS.get(table)
            if key:
                seen = set()
                for row in rows:
                    identifier = source_id(row, key)
                    if not identifier or identifier in seen:
                        raise InputError(f'missing or duplicate {key} in {path.name}')
                    seen.add(identifier)
                self.valid_ids.update(seen)
                rows.sort(key=lambda row: row[key])
            self.tables[table] = rows
        self.valid_ids.update(source_id(account, 'account_id') for account in self.tables['accounts'])
        if self.mode == 'jsonl':
            policy_path = root / 'POLICY.md'
            if not policy_path.read_text(encoding='utf-8-sig').strip():
                raise InputError('POLICY.md is empty')
            self.input_paths.add(policy_path.resolve())
        self.names, names_path = read_names(root, self.mode == 'csv')
        self.input_paths.add(names_path.resolve())
        self.alerts = index(self.tables['alerts'], 'alert_id', unique=True)
        self.hits = index(self.tables['hits'], 'hit_id', unique=True)
        self.inventory = self.reconcile()

    def reconcile(self):
        declared = {}
        for alert_id, row in self.alerts.items():
            hit_ids = list_values(row, 'hit_ids')
            if 'hit_count' in row and row['hit_count'] not in (None, ''):
                count = row['hit_count']
                if isinstance(count, bool) or str(count) != str(len(hit_ids)):
                    raise InputError('alert hit count disagrees with its hit list')
            if not hit_ids:
                raise InputError('alert has an empty hit inventory')
            for hit_id in hit_ids:
                if not isinstance(hit_id, str) or hit_id in declared:
                    raise InputError('hit inventory has a missing, invalid or repeated hit ID')
                declared[hit_id] = alert_id
        if declared.keys() != self.hits.keys():
            raise InputError('alert hit lists and hit rows do not contain the same inventory')
        for hit_id, alert_id in declared.items():
            row = self.hits[hit_id]
            if 'alert_id' in row and row['alert_id'] != alert_id:
                raise InputError('hit points to a different alert than its inventory entry')
        return tuple(sorted((alert_id, hit_id) for hit_id, alert_id in declared.items()))
