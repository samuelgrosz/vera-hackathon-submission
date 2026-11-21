from contextlib import redirect_stderr
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from .test_screening import customer, save, scenario

from engine import grok
from engine.s1_load import Dataset
from engine.s5_batch import Batch
from vera import engine_bridge


def response(quotes, *, ambiguous=False, finish_reason='stop'):
    payload = {'quotes': quotes, 'ambiguous': ambiguous}
    return BytesIO(json.dumps({'choices': [{'finish_reason': finish_reason,
                                           'message': {'content': json.dumps(payload)}}]}).encode())


class GrokFallbackTests(unittest.TestCase):
    def batch(self, table, client=None, *, text_extraction=True):
        with TemporaryDirectory() as folder:
            save(Path(folder), table)
            return Batch(Dataset(folder), grok=client, text_extraction=text_extraction)

    def row(self, note, opener, **fields):
        table = scenario(profile_notes=note, **fields)
        client = grok.GrokProfileFallback('test-token', grok.DEFAULT_MODEL, opener=opener)
        return self.batch(table, client).rows()[0]

    def test_search_cannot_replace_unreadable_profile_birth_evidence(self):
        table = scenario(profile_notes='Alex Morgan was born in nineteen ninety-four.')
        table['search_results'] = [
            {'result_id': 'SR-1', 'query': 'alex morgan', 'title': 'Alex Morgan',
             'snippet': 'A public report throughout March 2009 listed Alex Morgan, 30, as a sanctioned individual from United Kingdom.'},
        ]
        self.assertEqual(self.batch(table).rows()[0]['decision'], 'REVIEW')

    def test_profile_age_takes_precedence_over_a_strict_search_date(self):
        table = scenario(profile_notes='Questioning of Alex Morgan occurred in June 2021, at which point he was 27.')
        table['search_results'] = [
            {'result_id': 'SR-1', 'query': 'alex morgan', 'title': 'Alex Morgan',
             'snippet': 'Alex Morgan, born in 1961, was a sanctioned individual in United Kingdom.'},
        ]
        row, = self.batch(table).rows()
        self.assertEqual(row['decision'], 'SAME_ENTITY')
        self.assertNotIn('SR-1', row['evidence'])

    def test_exact_quotes_go_through_the_existing_policy(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        note = quote + '\n\nThis paragraph stays on the computer.'
        opener = Mock(return_value=response([quote]))
        row = self.row(note, opener)
        self.assertEqual((row['decision'], row['reason_code']), ('DIFFERENT_ENTITY', 'DOB_MISMATCH'))
        self.assertIn('1961', row['reason'])
        self.assertIn('H1', row['evidence'])

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, grok.OPENROUTER_CHAT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        payload = json.loads(request.data)
        sent = json.loads(payload['messages'][1]['content'])
        self.assertEqual(sent, {'listing_names': ['Alex Morgan'], 'first_paragraph': quote})
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(opener.call_args.kwargs['timeout'], grok.TIMEOUT_SECONDS)

    def test_grok_can_prevent_mismatch_on_a_matching_date(self):
        quote = 'Alex Morgan was born in nineteen ninety-four.'
        row = self.row(quote, Mock(return_value=response([quote])))
        self.assertEqual(row['decision'], 'SAME_ENTITY')

    def test_keeps_all_supported_quotes_when_profile_gives_two_dates(self):
        older = 'Alex Morgan was born in nineteen sixty-one.'
        newer = "Alex Morgan's birth year is nineteen ninety-four."
        row = self.row(f'{older} {newer}', Mock(return_value=response([older, newer])))
        self.assertEqual(row['decision'], 'SAME_ENTITY')
        self.assertIn('1961, 1994', row['reason'])

    def test_dated_age_keeps_both_years(self):
        quote = 'Alex Morgan was 30 years of age in July 2024.'
        row = self.row(quote, Mock(return_value=response([quote])))
        self.assertEqual(row['decision'], 'SAME_ENTITY')
        self.assertIn('1993, 1994', row['reason'])

    def test_rejects_invented_partial_ambiguous_and_unattributed_quotes(self):
        old = 'Alex Morgan was born in nineteen sixty-one.'
        for paragraph, quotes, ambiguous in (
            (old, ['Alex Morgan was born in 1961.'], False),
            (old, [old.removesuffix('.')], False),
            (old, [old], True),
            (old + ' The alternate birth year is 1994.', [old], False),
            ('Alex Morgan met Taylor Reed, who was born in nineteen sixty-one.',
             ['Alex Morgan met Taylor Reed, who was born in nineteen sixty-one.'], False),
            ("Alex Morgan's father was born in nineteen sixty-one.",
             ["Alex Morgan's father was born in nineteen sixty-one."], False),
            ('Alex Morgan was born in nineteen oh.', ['Alex Morgan was born in nineteen oh.'], False),
        ):
            with self.subTest(paragraph=paragraph, quotes=quotes, ambiguous=ambiguous):
                row = self.row(paragraph, Mock(return_value=response(quotes, ambiguous=ambiguous)))
                self.assertEqual(row['decision'], 'REVIEW')

    def test_unavailable_or_incomplete_model_keeps_review(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        for opener in (Mock(side_effect=TimeoutError('connection timed out')),
                       Mock(return_value=BytesIO(b'{broken json')),
                       Mock(return_value=response([quote], finish_reason='length'))):
            with self.subTest(opener=opener):
                row = self.row(quote, opener)
                self.assertEqual(row['decision'], 'REVIEW')
                self.assertIn('birth', row['reason'])

    def test_no_request_when_local_evidence_settles_the_hit(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        opener = Mock()
        row = self.row(quote, opener, dobs=['1961'])
        self.assertEqual(row['decision'], 'DIFFERENT_ENTITY')
        opener.assert_not_called()

    def test_skips_reports_and_unverifiable_birth_evidence(self):
        for paragraph in (
            'Alex Morgan was mentioned in a report published in 2018.',
            'Alex Morgan was born in a year the authorities withheld.',
            'Alex Morgan was born in nineteen sixty-one. An alternate birth year is unconfirmed.',
            'The wife of Alex Morgan was born in nineteen sixty-one.',
        ):
            with self.subTest(paragraph=paragraph):
                opener = Mock()
                row = self.row(paragraph, opener)
                self.assertEqual(row['decision'], 'REVIEW')
                opener.assert_not_called()

    def test_skips_a_birth_date_that_cannot_settle_the_hit_on_dob(self):
        for quote, countries in (
            ('Alex Morgan was born in nineteen ninety-five.', ['GB']),
            ('Alex Morgan was born in nineteen ninety-four.', ['US']),
            ('Alex Morgan was born in nineteen ninety-five.', ['US']),
        ):
            with self.subTest(quote=quote, countries=countries):
                opener = Mock()
                row = self.row(quote, opener, countries=countries)
                self.assertEqual(row['decision'], 'REVIEW')
                opener.assert_not_called()

    def test_skips_unresolved_customer_identity(self):
        table = scenario(profile_notes='Alex Morgan was born in nineteen sixty-one.')
        table['customers'].append(customer('C2', dob='1970-02-03'))
        table['accounts'].append(dict(account_id='ACC1', customer_id='C2', login_handle='alex2'))
        opener = Mock()
        client = grok.GrokProfileFallback('test-token', grok.DEFAULT_MODEL, opener=opener)
        row, = self.batch(table, client).rows()
        self.assertEqual((row['decision'], row['customer_record_id']), ('REVIEW', 'AMBIGUOUS'))
        opener.assert_not_called()

    def test_keeps_the_request_budget_and_caches_repeat_profiles(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        opener = Mock(return_value=response([quote]))
        client = grok.GrokProfileFallback('test-token', grok.DEFAULT_MODEL, opener=opener)
        with patch.object(grok, 'MAX_CALLS', 1):
            first = client.birth_dates(quote, ('Alex Morgan',))
            self.assertTrue(first)
            self.assertEqual(client.birth_dates(quote, ('Alex Morgan',)), first)
            self.assertEqual(client.birth_dates('Another profile.', ('Taylor Reed',)), ())
        self.assertEqual(client.requests, 1)
        opener.assert_called_once()

    def test_requires_explicit_opt_in_and_uses_the_configured_key(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        table = scenario(profile_notes=quote)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-token'}, clear=True):
            self.assertIsNone(self.batch(table).grok)
        with patch.dict(os.environ, {'SCREENING_GROK_FALLBACK': '1'}, clear=True):
            with redirect_stderr(StringIO()):
                self.assertIsNone(self.batch(table).grok)
        with patch.dict(os.environ, {'SCREENING_GROK_FALLBACK': '1', 'OPENROUTER_API_KEY': 'test-token'}, clear=True):
            with patch('engine.grok.build_opener'):
                self.assertEqual(self.batch(table).grok.model, grok.DEFAULT_MODEL)
                self.assertIsNone(self.batch(table, text_extraction=False).grok)

    def test_vera_and_batch_use_the_same_backup(self):
        quote = 'Alex Morgan was born in nineteen sixty-one.'
        with TemporaryDirectory() as folder:
            save(Path(folder), scenario(profile_notes=quote))
            with patch('engine.s5_batch.configured_grok', side_effect=lambda: grok.GrokProfileFallback(
                    'test-token', grok.DEFAULT_MODEL, opener=lambda *_a, **_kw: response([quote]))):
                expected = Batch(Dataset(folder)).rows()
                _, vera_rows, _ = engine_bridge.decide_all(folder)
        self.assertEqual(vera_rows, expected)
        self.assertEqual(vera_rows[0]['decision'], 'DIFFERENT_ENTITY')


if __name__ == '__main__':
    unittest.main()
