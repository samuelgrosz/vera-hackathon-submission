import csv
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

SYSTEM = Path(__file__).resolve().parents[2] / 'system'
sys.path.insert(0, str(SYSTEM))

from engine.s2_customers import check_from_note
from engine.s3_hit_facts import build_hit, former_residence, query_key, text_birth
from engine.s1_load import Dataset, InputError, PREFIX, TABLES
from engine.normalize import Names, PartialDate, country, date_comparison, dates
from engine.s4_decide import CLEAR, MATCH, REVIEW
from engine.s5_batch import Batch, alert_status, verify_rows


def customer(key='C1', **fields):
    return dict(record_id=key, full_name='Alex Morgan', entity_type='Individual', email=f'{key}@example.test',
                dob='1994-07-12', phone='', residence_country='GB', nationality='', city='Leeds') | fields


def check(key='K1', applicant='P1', **fields):
    return dict(check_id=key, applicant_id=applicant, result='approved', checked_at='2024-02-01',
                verified_name='Alex Morgan', verified_dob='1994-07-12', document_country='GB',
                document_type='passport', document_number=key) | fields


def note(key='N1', owner='C1', **fields):
    return dict(note_id=key, requester_email=f'{owner}@example.test', created_at='2024-10-01',
                channel='phone', text='Customer requested a copy of their receipt.') | fields


def scenario(**hit_fields):
    return {
        'alerts': [dict(alert_id='A1', hit_ids=['H1'], hit_count=1, screened_at='2026-01-10',
                        customer_name='Alex Morgan', account_ref='ACC1')],
        'hits': [dict(hit_id='H1', alert_id='A1', entity_type='Individual', name='Alex Morgan',
                      countries=['GB'], categories=['Sanctions'], dobs=[]) | hit_fields],
        'customers': [customer()],
        'accounts': [dict(account_id='ACC1', customer_id='C1', login_handle='alex')],
        'onboarding': [dict(account_id='ACC1', customer_id='C1', kyc_applicant_id='P1')],
        'kyc_checks': [check()],
        'notes': [],
        'search_results': [],
    }


def save(root, tables, mode='jsonl'):
    if mode == 'jsonl':
        (root / 'POLICY.md').write_text('Track 2 test policy\n', encoding='utf-8')
        variants = dict(given_name_variants=[['alexander', 'alex']], company_legal_forms=['ltd', 'llc', 'inc'])
        (root / 'name_variants.json').write_text(json.dumps(variants), encoding='utf-8')
        for table in TABLES:
            (root / f'{table}.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in tables[table]), encoding='utf-8')
    else:
        with (root / (PREFIX + 'Policy.csv')).open('w', newline='', encoding='utf-8') as handle:
            csv.writer(handle).writerows([['name_variants.json'], ['alexander, alex'],
                                          ['Company legal forms: ltd, llc, inc']])
        for table, csv_name in TABLES.items():
            with (root / (PREFIX + csv_name + '.csv')).open('w', newline='', encoding='utf-8') as handle:
                rows = tables[table]
                fields = sorted({key for row in rows for key in row}) or ['empty']
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: '; '.join(value) if isinstance(value, list) else value for key, value in row.items()})


class ScreeningTests(unittest.TestCase):
    def batch(self, table, mode='jsonl'):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            save(root, table, mode)
            return Batch(Dataset(root, mode))

    def verdict(self, table, expected, code=None):
        row, = self.batch(table).rows()
        self.assertEqual(row['decision'], expected, row['reason'])
        if code:
            self.assertEqual(row['reason_code'], code)
        self.assertTrue(2 <= row['reason'].count('. ') + 1 <= 4, row['reason'])
        return row

    def test_date_boundary_and_precision(self):
        # C01–C05
        for raw, decision, code in [
            (['1994'], MATCH, 'NAME_DOB_LOCATION_MATCH'), (['1995'], REVIEW, 'INSUFFICIENT_DATA'),
            (['1996'], CLEAR, 'DOB_MISMATCH'), (['1961', '1994-07-13'], CLEAR, 'DOB_MISMATCH'),
            (['1961', '1994-07-13', '1994'], MATCH, 'NAME_DOB_LOCATION_MATCH'),
            (['1994-08'], CLEAR, 'DOB_MISMATCH'), (['1994-07'], MATCH, 'NAME_DOB_LOCATION_MATCH'),
            (['1961', 'unreadable date'], REVIEW, 'INSUFFICIENT_DATA'),
        ]:
            with self.subTest(raw=raw):
                self.verdict(scenario(dobs=raw), decision, code)
        self.assertEqual(dates('04/05/1994'), (PartialDate(1994, 4, 5), PartialDate(1994, 5, 4)))
        table = scenario(dobs=['1994-04-05'])
        table['kyc_checks'][0]['verified_dob'] = '04/05/1994'
        self.verdict(table, REVIEW)
        self.assertEqual(date_comparison(PartialDate(1994), PartialDate(1995)), 'inconclusive')

    def test_official_nested_alert_resolves_customer_without_link(self):
        from steps.data import load

        table = scenario(dobs=['1961'])
        table['alerts'] = [dict(alert_id='A1', screened_at='2026-01-10', hit_ids=['H1'],
                                account_ref=None, login_handle=None, email=None,
                                customer=dict(name='Alex Morgan', entity_type='Individual',
                                              dob='1994-07-12', country='United Kingdom', city='Leeds'))]
        table['customers'].append(customer('C2', full_name='Alex Morgan', dob='1970-01-01'))
        with TemporaryDirectory() as folder:
            save(Path(folder), table)
            batch = Batch(Dataset(folder))
            row, = batch.rows()
            self.assertEqual((row['customer_record_id'], row['decision'], row['reason_code']),
                             ('C1', CLEAR, 'DOB_MISMATCH'))
            self.assertEqual(load(folder).alerts_by_id['A1']['customer_name'], 'Alex Morgan')

    def test_candidate_ambiguity_and_unanimity(self):
        # A03–A05 and D02
        table = scenario(dobs=['1994-07-12'])
        table['customers'].append(customer('C2', dob='1961-02-03'))
        table['accounts'].append(dict(account_id='ACC1', customer_id='C2', login_handle='alex2'))
        row = self.verdict(table, REVIEW, 'AMBIGUOUS_CUSTOMER_RECORD')
        self.assertEqual(row['customer_record_id'], 'AMBIGUOUS')
        self.assertTrue({'C1', 'C2'} <= set(row['evidence']))
        table['alerts'][0]['dob'] = '1994'
        self.assertEqual(self.verdict(table, MATCH)['customer_record_id'], 'C1')
        del table['alerts'][0]['dob']
        table['hits'][0]['dobs'] = ['1899']
        self.assertEqual(self.verdict(table, CLEAR, 'DOB_MISMATCH')['customer_record_id'], 'AMBIGUOUS')
        table['hits'][0]['dobs'] = []
        self.assertEqual(self.verdict(table, REVIEW, 'INSUFFICIENT_DATA')['customer_record_id'], 'AMBIGUOUS')

    def test_unanimous_clearances_can_have_different_grounds(self):
        table = scenario(dobs=['1994'])
        table['kyc_checks'][0]['verified_name'] = 'Elliot Winters'
        table['customers'].append(customer('C2', dob='1964-03-12'))
        table['accounts'].append(dict(account_id='ACC1', customer_id='C2', login_handle='alex2'))
        row = self.verdict(table, CLEAR)
        self.assertEqual(row['customer_record_id'], 'AMBIGUOUS')
        self.assertIn('DOB_MISMATCH', row['reason'])
        self.assertIn('NAME_MISMATCH_VERIFIED', row['reason'])

    def test_joins_are_scoped_to_account_and_customer(self):
        # A06, A08, A09
        table = scenario(dobs=['1994'])
        table['customers'][0]['phone'] = '+44 7000 123456'
        table['customers'].append(customer('C2', phone='+44 7000 123456', dob='1964-02-03'))
        table['accounts'].append(dict(account_id='ACC2', customer_id='C2', login_handle='alex2'))
        table['onboarding'].append(dict(account_id='ACC1', customer_id='C2', kyc_applicant_id='P2'))
        table['kyc_checks'].append(check('K2', 'P2', verified_dob='1964-02-03', document_number='K1', result='rejected'))
        row = self.verdict(table, MATCH)
        self.assertNotIn('K2', row['evidence'])
        self.assertNotIn('C2', row['evidence'])
        table['onboarding'][-1]['account_id'] = 'ACC2'
        self.verdict(table, MATCH)
        table['customers'][0]['dob'] = table['customers'][1]['dob'] = '1994'
        self.verdict(table, MATCH)
        self.assertEqual(self.batch(table).customers.members['C1'][0]['record_id'], 'C1')
        self.assertEqual(len(self.batch(table).customers.members['C1']), 1)

    def test_transitive_supported_merge_preserves_disagreeing_countries(self):
        # A07
        table = scenario(countries=['US'])
        table['customers'][0]['phone'] = '+44 7000 123456'
        table['customers'].extend((customer('C2', phone='+44 7000 123456'), customer('C3', residence_country='US')))
        for index in (2, 3):
            table['accounts'].append(dict(account_id=f'ACC{index}', customer_id=f'C{index}'))
            table['onboarding'].append(dict(account_id=f'ACC{index}', customer_id=f'C{index}', kyc_applicant_id=f'P{index}'))
            table['kyc_checks'].append(check(f'K{index}', f'P{index}', document_number='SHARED2'))
        batch = self.batch(table)
        self.assertEqual(batch.customers.root['C3'], 'C1')
        row, = batch.rows()
        self.assertEqual((row['decision'], row['customer_record_id']), (REVIEW, 'C1'))
        self.assertIn('C3', row['evidence'])

    def test_verification_override_and_tied_checks(self):
        # A10–A12
        table = scenario(dobs=['1994-07-12'])
        table['customers'][0]['dob'] = '1960-04-03'
        table['kyc_checks'] = []
        table['notes'] = [note(channel='kyc',
                               text='Passport verification approved on 2024-09-03. '
                                    'Name on document: Alex Morgan. Date of birth on document: 12 July 1994.')]
        self.assertIn('N1', self.verdict(table, MATCH)['evidence'])
        table['notes'][0]['text'] = 'The customer uploaded a passport.'
        self.assertIsNone(check_from_note(table['notes'][0]))
        table['kyc_checks'] = [check(), check('K2', result='rejected', verified_dob='1960-04-03', checked_at='2025-03-01')]
        self.assertNotIn('K2', self.verdict(table, MATCH)['evidence'])
        table['kyc_checks'][1].update(result='approved', checked_at='2024-02-01')
        self.verdict(table, REVIEW)

    def test_link_free_name_and_missing_customer(self):
        # A13–A14
        table = scenario(dobs=['1994'])
        table['alerts'][0].update(account_ref='', customer_name='Alexander Morgan', dob='1994', city='Leeds')
        table['customers'].append(customer('C2', dob='1964-03-12'))
        self.assertEqual(self.verdict(table, MATCH)['customer_record_id'], 'C1')
        table['alerts'][0]['city'] = 'Halifax'
        self.assertIsNone(self.verdict(table, REVIEW)['customer_record_id'])

    def test_explicit_no_link_data_stays_in_review(self):
        table = scenario(dobs=['1961'])
        table['alerts'][0].update(account_ref='MISSING', dob='1994')
        row = self.verdict(table, REVIEW, 'INSUFFICIENT_DATA')
        self.assertIsNone(row['customer_record_id'])

    def test_search_attribution_and_precedence(self):
        # B01–B04, B10
        table = scenario()
        for snippet, answer in [
            ('Alex Morgan, born in 1960, was named in adverse media in the United Kingdom.', REVIEW),
            ('Alex Morgan, born in 1960, was a sanctioned individual in the United Kingdom.', CLEAR),
            ('A United Kingdom news service said Alex Morgan, born in 1960, was a sanctioned individual.', REVIEW),
            ('Alex Morgan, born in 1960, was a sanctioned individual in China, according to UK News.', REVIEW),
            ('Alex Morgan, born in 1960, spoke with Taylor Reed, a sanctioned individual in the United Kingdom.', REVIEW),
            ('Alex Morgan, born in 1960, was never a sanctioned individual in the United Kingdom.', REVIEW),
            ('Alex Morgan, born in 1960, was a sanctioned individual. Taylor Reed is a British resident.', REVIEW),
        ]:
            with self.subTest(snippet=snippet):
                table['search_results'] = [dict(result_id='S1', query='alex morgan', snippet=snippet)]
                row = self.verdict(table, answer)
                self.assertEqual('S1' in row['evidence'], answer == CLEAR)
        table['search_results'][0]['snippet'] = 'Alex Morgan, born in 1960, was a sanctioned individual in the United Kingdom.'
        table['hits'][0].update(primary_category='Adverse Media', categories=['Sanctions'])
        self.verdict(table, REVIEW)
        table['hits'][0]['primary_category'] = ''
        self.verdict(table, CLEAR)
        table['hits'][0]['dobs'] = ['1994']
        self.verdict(table, MATCH)
        table['hits'][0].update(dobs=[], profile_notes='Alex Morgan was born in 1994.')
        self.verdict(table, MATCH)

    def test_attributed_profile_age_retains_two_years(self):
        # B05–B06
        self.verdict(scenario(profile_notes='Alex Morgan’s spouse was born in 1961.'), REVIEW)
        table = scenario(name='Morgan, Alex', profile_notes='Investigators questioned Alex Morgan in March 2019, when he reached the age of 45.')
        table['kyc_checks'][0]['verified_dob'] = '1974-05-02'
        batch = self.batch(table)
        self.assertEqual(batch.evidence('H1')[2].dob.values, (PartialDate(1973), PartialDate(1974)))
        self.verdict(table, MATCH)
        self.assertEqual(query_key('Gómez, Alex'), 'alex gomez')
        for sentence in ('March 2019 saw Alex Morgan, a 45-year-old, face charges.',
                         'Investigators questioned Alex Morgan in March 2019, when he was 45.'):
            with self.subTest(sentence=sentence):
                table['hits'][0]['profile_notes'] = sentence
                self.verdict(table, MATCH)

    def test_step3_age_fallback_reads_only_the_subject(self):
        # R4: Step 3's age reader fills in when text_birth finds nothing. B05 still holds.
        table = scenario(profile_notes='Questioning of Alex Morgan occurred in June 2006, at which point he was 27.')
        self.assertEqual(self.batch(table).evidence('H1')[2].dob.values, (PartialDate(1978), PartialDate(1979)))
        self.verdict(table, CLEAR, 'DOB_MISMATCH')
        for sentence in ('The spouse of Alex Morgan was questioned in June 2006, at which point she was 27.',
                         'Alex Morgan interviewed Taylor Reed in June 2006, at which point he was 27.',
                         'Questioning occurred in June 2006, at which point he was 27.'):
            with self.subTest(sentence=sentence):
                table['hits'][0]['profile_notes'] = sentence
                self.verdict(table, REVIEW)

    def test_step3_search_age_needs_phrase_country_and_subject(self):
        # R4: a supplied search result counts only with the category phrase, a tied country
        # and the listed subject as the only person named.
        table = scenario()
        result = dict(result_id='SR-1', query='alex morgan', title='Alex Morgan - report',
                      snippet='A public report throughout March 2009 listed Alex Morgan, 30, as a sanctioned individual from United Kingdom.')
        table['search_results'] = [result]
        self.verdict(table, CLEAR, 'DOB_MISMATCH')
        for snippet in ('A public report throughout March 2009 listed Alex Morgan, 30, as named in adverse media from United Kingdom.',
                        'A public report throughout March 2009 listed Alex Morgan, 30, as a sanctioned individual from Spain.',
                        'A March 2009 report listed Taylor Reed, 30, with Alex Morgan as a sanctioned individual from United Kingdom.'):
            with self.subTest(snippet=snippet):
                result['snippet'] = snippet
                self.verdict(table, REVIEW)

    def test_slash_dates_link_records_but_never_clear_alone(self):
        # R2: day-first merges 06/09/1985 with 1985-09-06 (same phone); C05 still holds.
        table = scenario(dobs=['1994-04-05'])
        table['kyc_checks'] = []
        table['customers'][0]['dob'] = '04/05/1994'
        self.verdict(table, REVIEW)
        from engine.s2_customers import Customers
        table = scenario()
        table['customers'] = [customer('C1', dob='06/09/1985', phone='+44 7700 900123'),
                              customer('C2', dob='1985-09-06', phone='+44 7700 900123')]
        with TemporaryDirectory() as folder:
            save(Path(folder), table)
            people = Customers(Dataset(Path(folder), 'jsonl'))
        self.assertEqual(people.root['C1'], people.root['C2'])

    def test_profile_birth_alternatives_and_independent_subjects(self):
        self.verdict(scenario(profile_notes='Alex Morgan was born in either 1961 or 1994.'), MATCH)
        self.verdict(scenario(profile_notes='Alex Morgan was born in 1961, or 1994.'), MATCH)
        self.verdict(scenario(profile_notes='Alex Morgan interviewed Taylor Reed, who was born in 1961.'), REVIEW)

    def test_review_gaps_only_block_dependent_rules(self):
        # B09
        table = scenario(dobs=['unreadable'], profile_notes='According to Example Registry; September 9, 2010: report issued.')
        table['kyc_checks'][0]['verified_dob'] = '1994-09-10'
        self.verdict(table, CLEAR, 'TEMPORAL_IMPOSSIBILITY')

    def test_countries_all_sources_and_exception(self):
        # C06–C10
        table = scenario(countries=['US'])
        self.verdict(table, CLEAR, 'LOCATION_MISMATCH')
        for field, source in [('nationality', 'American'), ('addresses', 'City, United States [Business]')]:
            changed = deepcopy(table)
            if field == 'addresses':
                changed['hits'][0].update(countries=['GB'], addresses=['City, United States [Business]'])
                changed['customers'][0].update(residence_country='US')
                changed['kyc_checks'][0].update(document_country='US')
            else:
                changed['customers'][0][field] = source
            self.verdict(changed, REVIEW)
        table['notes'] = [note(text='The customer visited the United States for a weekend.')]
        self.verdict(table, CLEAR)
        table['notes'][0]['text'] = 'The customer maintained both a home and a job in the United States.'
        self.verdict(table, REVIEW)
        self.assertEqual(former_residence(table['notes'][0]['text']), ({'US'}, False))
        table['notes'] = []
        table['hits'][0]['dobs'] = ['1994']
        self.verdict(table, REVIEW)
        table['hits'][0].update(dobs=[], countries=['International', 'Europe'])
        self.verdict(table, REVIEW)
        self.assertNotEqual(country('Jersey'), country('British'))
        self.assertNotEqual(country('Macao Special Administrative Region of China'), country('China'))

    def test_verified_name_check_with_alias_and_placeholder(self):
        # C11–C15
        table = scenario(name='Jonathan Fletcher')
        self.verdict(table, CLEAR, 'NAME_MISMATCH_VERIFIED')
        table['hits'][0]['aliases'] = ['Morgan']
        self.verdict(table, REVIEW)
        table['hits'][0]['dobs'] = ['1994']
        self.verdict(table, MATCH)
        table['hits'][0]['aliases'] = []
        table['kyc_checks'][0]['verified_name'] = 'N/A A'
        self.verdict(table, REVIEW)
        table['kyc_checks'][0]['result'] = 'expired'
        self.verdict(table, REVIEW)
        names = Names([['alex', 'alexander']], ['lt d', 'ltd'])
        self.assertTrue(names.agree(names.parts('Ålex (old name) Morgan'), names.parts('Morgan Alexander')))
        self.assertTrue(names.agree(names.parts('Abdul Salam Akram'), names.parts('Akram Abdulsalam')))
        self.assertTrue(names.part_agrees('gomes', 'gomez'))
        self.assertFalse(names.part_agrees('smith', 'smtih'))
        self.assertFalse(names.part_agrees('kim', 'kin'))
        self.assertTrue(names.part_agrees('alexandra', 'alxeandra'))
        self.assertFalse(names.agree(names.parts('Lee Lee Morgan'), names.parts('Lee Lee Watson')))

    def test_report_age_death_and_corporate_boundaries(self):
        # C16–C21
        for day, expected in [(9, CLEAR), (10, REVIEW)]:
            table = scenario(profile_notes=f'According to Example Registry; September {day}, 2020: report issued.')
            table['kyc_checks'][0]['verified_dob'] = '2004-09-10'
            self.verdict(table, expected)
        table['kyc_checks'][0]['verified_dob'] = '2004'
        self.verdict(table, REVIEW)
        for raw, expected in [('2025-09-10', CLEAR), ('2026-01-10', REVIEW), ('2026', REVIEW), ('', REVIEW)]:
            self.verdict(scenario(deceased_date=raw, life_status='Deceased'), expected)
        table = scenario(entity_type='Company', name='Alex Morgan Ltd', profile_notes='According to Example; September 10, 2015: report issued.')
        self.verdict(table, CLEAR, 'ENTITY_TYPE_MISMATCH')
        table['customers'][0].update(entity_type='Company', incorporation_date='2016-01-01')
        table['kyc_checks'] = []
        self.verdict(table, CLEAR, 'TEMPORAL_IMPOSSIBILITY')
        table['customers'][0].update(incorporation_date='', onboarded_at='2016-01-01')
        self.verdict(table, MATCH, 'NAME_LOCATION_MATCH')
        table['customers'][0]['entity_type'] = 'Unknown'
        self.verdict(table, REVIEW)

    def test_report_order_and_grammar(self):
        reports = ['According to Example; September 7, 2021: report issued.',
                   'Earlier event May 4, 2007: excluded by policy.',
                   'According to Example; September 9, 2020: report issued.']
        table = scenario(profile_notes='\n'.join(reports))
        table['kyc_checks'][0]['verified_dob'] = '2004-09-10'
        self.verdict(table, CLEAR, 'TEMPORAL_IMPOSSIBILITY')
        table['hits'][0]['profile_notes'] = '\n'.join(reversed(reports))
        self.verdict(table, CLEAR, 'TEMPORAL_IMPOSSIBILITY')

    def test_csv_jsonl_parity_and_alert_status(self):
        # A01, B08
        table = scenario(profile_notes='Alex Morgan was born in 1960.\n\nAdditional quoted text; at 2, City.', addresses=['2, City, UK [Business]'])
        table['alerts'][0].update(hit_ids=['H1', 'H2'], hit_count=2)
        table['hits'].append(dict(hit_id='H2', alert_id='A1', entity_type='Individual',
                                  name='Alex Morgan', countries=['GB'], categories=['Sanctions']))
        json_batch = self.batch(table)
        rows = json_batch.rows()
        self.assertEqual(rows, self.batch(table, 'csv').rows())
        self.assertEqual([row['decision'] for row in rows], [CLEAR, REVIEW])
        self.assertEqual(alert_status(json_batch.data, rows), {'A1': False})

    def test_output_verification_rejects_unsafe_mutations(self):
        # A02, D07
        batch = self.batch(scenario(dobs=['1961']))
        rows = batch.rows()
        for invalid in [[], rows * 2, [rows[0] | {'reason_code': 'UNRECOGNIZED'}],
                        [rows[0] | {'evidence': ['unrecognized']}]]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(InputError):
                    verify_rows(batch.data, invalid)
        table = scenario()
        table['alerts'][0].update(hit_ids=['H1', 'H1'], hit_count=2)
        with self.assertRaises(InputError):
            self.batch(table)

    def test_executable_is_atomic_and_working_directory_independent(self):
        # D04–D05, D07
        with TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'data'
            data.mkdir()
            output = root / 'output.jsonl'
            table = scenario(dobs=['1961'])
            save(data, table)
            command = [sys.executable, '-I', SYSTEM / 'run.py', data, output]
            subprocess.run(command, cwd='/', check=True, capture_output=True)
            before = output.read_bytes()
            (data / 'practice_labels.jsonl').write_text('intentionally invalid\n', encoding='utf-8')
            subprocess.run(command, cwd=root, check=True, capture_output=True)
            self.assertEqual(before, output.read_bytes())
            table['hits'][0]['alert_id'] = 'A2'
            save(data, table)
            failed = subprocess.run(command, cwd=root, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(before, output.read_bytes())

    def test_table_row_order_does_not_select_the_customer(self):
        table = scenario(dobs=['1994'])
        table['customers'].append(customer('C2', dob='1961-02-03'))
        table['accounts'].append(dict(account_id='ACC1', customer_id='C2', login_handle='alex2'))
        original = self.batch(table).rows()
        reversed_tables = {table_name: list(reversed(rows)) for table_name, rows in table.items()}
        self.assertEqual(original, self.batch(reversed_tables).rows())

    def test_demo_conditional_outputs_leave_current_status_in_review(self):
        # D03
        result = subprocess.run([sys.executable, SYSTEM.parent / 'tools' / 'review', '--demo'], check=True, capture_output=True, text=True)
        current, previews = result.stdout.split('Conditional outcomes')
        self.assertIn('REVIEW (INSUFFICIENT_DATA)', current)
        self.assertIn('Check the birth date in the original listing', current)
        for outcome in (CLEAR, MATCH, REVIEW):
            self.assertIn(outcome, previews)


if __name__ == '__main__':
    unittest.main()
