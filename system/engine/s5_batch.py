import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from time import monotonic

from engine.s2_customers import Customers
from engine.s3_hit_facts import DateEvidence, build_hit, date_evidence, first_profile_paragraph, query_key
from engine.s1_load import Dataset, InputError, index, list_values, value
from engine.s4_decide import CLEAR, CODES, REVIEW, Decision, Finding, decide
from engine.grok import configured_grok, profile_birth_evidence


OUTPUT_FIELDS = {'alert_id', 'hit_id', 'decision', 'reason_code', 'customer_record_id', 'reason', 'evidence'}
GROK_DOB_REASONS = {'DOB_MISMATCH', 'NAME_DOB_MATCH', 'NAME_DOB_LOCATION_MATCH'}


class Batch:
    def __init__(self, data, *, text_extraction=True, grok=None):
        self.data = data
        self.text_extraction = text_extraction
        self.customers = Customers(data, text_extraction=text_extraction)
        self.search = index(data.tables['search_results'], lambda row: query_key(value(row, 'query')))
        self.hit_to_alert = {hit: alert for alert, hit in data.inventory}
        self.resolved_customers = {}
        self.screening_dates = {}
        self.fallbacks = set()
        self.grok = (grok if grok is not None else configured_grok()) if text_extraction else None

    def evidence(self, hit_id):
        alert_id = self.hit_to_alert[hit_id]
        alert = self.data.alerts[alert_id]
        if alert_id not in self.resolved_customers:
            self.resolved_customers[alert_id] = self.customers.resolve(alert)
        if alert_id not in self.screening_dates:
            self.screening_dates[alert_id] = date_evidence(list_values(alert, 'screened_at'), (alert_id,))
        hit = build_hit(self.data.hits[hit_id], self.search,
                        workbook=self.data.mode == 'csv', text_extraction=self.text_extraction)
        return alert_id, self.resolved_customers[alert_id], hit, self.screening_dates[alert_id]

    def decision(self, hit_id):
        alert_id = self.hit_to_alert[hit_id]
        self.fallbacks.discard(hit_id)
        try:
            _, customers, hit, screened = self.evidence(hit_id)
            baseline = decide(customers, hit, screened, self.data.names, alert_id=alert_id)
            if (self.grok is None or baseline.finding.decision != REVIEW or hit.kind != 'person'
                    or hit.dob.values or len(customers) != 1):
                return baseline
            customer, = customers
            if customer.kind != 'person' or not customer.dob.values or customer.dob.uncertain:
                return baseline
            row = self.data.hits[hit_id]
            notes = value(row, 'profile_notes')
            if list_values(row, 'dobs') or (self.data.mode == 'csv' and len(notes) >= 32000):
                return baseline
            paragraph = first_profile_paragraph(notes)
            profile = profile_birth_evidence(paragraph, hit.names)
            if profile is None:
                return baseline
            enriched = replace(hit, dob=DateEvidence(profile.dates, (hit.id,)), problems=())
            preview = decide(customers, enriched, screened, self.data.names, alert_id=alert_id)
            if preview.finding.code not in GROK_DOB_REASONS:
                return baseline
            confirmed = self.grok.birth_dates(paragraph, hit.names, evidence=profile)
            return preview if confirmed == profile.dates else baseline
        except Exception as exc:
            self.fallbacks.add(hit_id)
            print(f'Hit {hit_id!r} evaluation failed ({type(exc).__name__})', file=sys.stderr)
            customers = self.resolved_customers.get(alert_id, ())
            customer_id = customers[0].id if len(customers) == 1 else 'AMBIGUOUS' if customers else None
            finding = Finding(REVIEW, 'INSUFFICIENT_DATA',
                              ('The system could not evaluate this hit.',
                               'An analyst should review the source records.'),
                              (alert_id, hit_id))
            return Decision(finding, customer_id, ())

    def rows(self):
        rows = []
        for alert_id, hit_id in self.data.inventory:
            decision = self.decision(hit_id)
            finding = decision.finding
            rows.append(dict(alert_id=alert_id, hit_id=hit_id, decision=finding.decision,
                             reason_code=finding.code, customer_record_id=decision.customer_id,
                             reason=' '.join(finding.sentences), evidence=list(finding.evidence)))
        verify_rows(self.data, rows)
        return rows


def verify_rows(dataset, rows):
    expected = set(dataset.inventory)
    seen = set()
    customer_ids = {row['record_id'] for row in dataset.tables['customers']}
    for row in rows:
        if set(row) != OUTPUT_FIELDS:
            raise InputError('output schema mismatch')
        key = (row['alert_id'], row['hit_id'])
        if key not in expected or key in seen:
            raise InputError('output includes an unexpected or repeated hit')
        seen.add(key)
        if row['reason_code'] not in CODES.get(row['decision'], ()):
            raise InputError('reason code is invalid for the output decision')
        if row['customer_record_id'] not in (None, 'AMBIGUOUS') and row['customer_record_id'] not in customer_ids:
            raise InputError('output customer record ID is invalid')
        evidence = row['evidence']
        if not isinstance(evidence, list) or not evidence or len(set(evidence)) != len(evidence):
            raise InputError('output is missing a unique list of evidence IDs')
        if any(not isinstance(identifier, str) or identifier not in dataset.valid_ids for identifier in evidence):
            raise InputError('output cites an unknown source record ID')
        if not isinstance(row['reason'], str) or not row['reason'].strip():
            raise InputError('output decision explanation is empty')
    if seen != expected:
        raise InputError('output is missing hits')


def alert_status(dataset, rows):
    by_alert = defaultdict(list)
    for row in rows:
        by_alert[row['alert_id']].append(row['decision'])
    return {identifier: len(by_alert[identifier]) == len(list_values(alert, 'hit_ids'))
                        and all(decision == CLEAR for decision in by_alert[identifier])
            for identifier, alert in dataset.alerts.items()}


def write_rows(rows, output, input_paths):
    output = Path(output)
    if output.resolve() in input_paths:
        raise InputError('output would overwrite an input source')
    output.parent.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                prefix='.predictions-', dir=output.parent, delete=False) as handle:
            path = Path(handle.name)
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(path, output)
    finally:
        if path and path.exists():
            path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the deterministic Track 2 screening evaluator.')
    parser.add_argument('data_folder', type=Path)
    parser.add_argument('output_file', type=Path)
    parser.add_argument('--format', choices=('jsonl', 'csv'), help='Select an input adapter explicitly.')
    parser.add_argument('--structured-only', action='store_true', help='Disable free-text extraction for a baseline comparison.')
    options = parser.parse_args(argv)
    start = monotonic()
    try:
        data = Dataset(options.data_folder, options.format)
        batch = Batch(data, text_extraction=not options.structured_only)
        rows = batch.rows()
        write_rows(rows, options.output_file, data.input_paths)
        decisions = Counter(row['decision'] for row in rows)
        cleared_alerts = sum(alert_status(data, rows).values())
    except (OSError, ValueError) as exc:
        print(f'Screening run failed ({type(exc).__name__}). {exc}', file=sys.stderr)
        return 1
    grok = batch.grok
    model_counts = (f'grok_requests={grok.requests} grok_accepted={grok.accepted} grok_errors={grok.errors} | '
                    if grok else '')
    print(f'{data.mode.upper()} | {len(rows)} hits | {len(data.alerts)} alerts | '
          f'{cleared_alerts} cleared alerts | {dict(sorted(decisions.items()))} | '
          f'fallbacks={len(batch.fallbacks)} | {model_counts}{monotonic()-start:.2f}s',
          file=sys.stderr)
    return 0
