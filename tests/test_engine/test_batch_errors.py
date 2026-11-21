from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from .test_screening import customer, save, scenario

from engine import s4_decide, s5_batch
from engine.s1_load import Dataset
from vera import engine_bridge


class BatchErrorTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.data = root / 'data'
        self.output = root / 'result.jsonl'
        self.data.mkdir()
        self.table = scenario(dobs=['1961'])
        self.table['alerts'][0].update(hit_ids=['H1', 'H2'], hit_count=2)
        self.table['hits'].append(self.table['hits'][0] | {'hit_id': 'H2'})
        save(self.data, self.table)
        self.expected = s5_batch.Batch(Dataset(self.data)).rows()

    def run_cli(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            status = s5_batch.main([str(self.data), str(self.output)])
        self.assertEqual(status, 0, stderr.getvalue())
        return [json.loads(line) for line in self.output.read_text(encoding='utf-8').splitlines()], stderr.getvalue()

    def assert_fallback(self, rows, customer_id):
        failed, healthy = rows
        self.assertEqual(healthy, self.expected[1])
        self.assertEqual(failed, {
            'alert_id': 'A1',
            'hit_id': 'H1',
            'decision': 'REVIEW',
            'reason_code': 'INSUFFICIENT_DATA',
            'customer_record_id': customer_id,
            'reason': 'The system could not evaluate this hit. An analyst should review the source records.',
            'evidence': ['A1', 'H1'],
        })

    def test_bad_hit_is_reviewed_and_vera_uses_the_same_rows(self):
        build_hit = s5_batch.build_hit

        def invalid_hit(row, *args, **kwargs):
            if row['hit_id'] == 'H1':
                raise TypeError('private synthetic value')
            return build_hit(row, *args, **kwargs)

        with patch.object(s5_batch, 'build_hit', side_effect=invalid_hit):
            rows, stderr = self.run_cli()
            first_output = self.output.read_bytes()
            self.assertEqual(self.run_cli()[0], rows)
            self.assertEqual(self.output.read_bytes(), first_output)
            with redirect_stderr(StringIO()):
                vera_decisions, vera_rows, dataset = engine_bridge.decide_all(str(self.data))
            vera_path = self.output.with_name('vera.jsonl')
            engine_bridge.write_last_run(vera_rows, dataset, str(vera_path))
        self.assert_fallback(rows, 'C1')
        self.assertIn("Hit 'H1' evaluation failed (TypeError)", stderr)
        self.assertIn('fallbacks=1', stderr)
        self.assertIn('0 cleared alerts', stderr)
        self.assertNotIn('private synthetic value', stderr)
        self.assertEqual(vera_path.read_bytes(), first_output)
        self.assertEqual(vera_decisions[0].decision, 'REVIEW')
        self.assertTrue(all(status == 'skip' for _, status, _ in vera_decisions[0].checks))

    def test_failed_clearance_replay_becomes_review(self):
        evaluate = s4_decide.evaluate
        first_hit_calls = 0

        def inconsistent_replay(customer_profile, hit, screened, names):
            nonlocal first_hit_calls
            result = evaluate(customer_profile, hit, screened, names)
            if hit.id == 'H1':
                first_hit_calls += 1
                if first_hit_calls == 2:
                    return replace(result, finding=replace(result.finding, evidence=()))
            return result

        with patch.object(s4_decide, 'evaluate', side_effect=inconsistent_replay):
            rows, stderr = self.run_cli()
        self.assert_fallback(rows, 'C1')
        self.assertEqual(first_hit_calls, 2)
        self.assertIn("Hit 'H1' evaluation failed (ValueError)", stderr)
        self.assertIn('fallbacks=1', stderr)

    def test_resolution_failure_keeps_customer_id_null(self):
        resolve = s5_batch.Customers.resolve

        def invalid_first_resolution(customers, alert):
            if alert['alert_id'] == 'A1':
                raise KeyError('private synthetic value')
            return resolve(customers, alert)

        self.table['alerts'][0].update(hit_ids=['H1'], hit_count=1)
        self.table['alerts'].append(self.table['alerts'][0] | {'alert_id': 'A2', 'hit_ids': ['H2']})
        self.table['hits'][1]['alert_id'] = 'A2'
        save(self.data, self.table)
        self.expected = s5_batch.Batch(Dataset(self.data)).rows()
        with patch.object(s5_batch.Customers, 'resolve', new=invalid_first_resolution):
            rows, stderr = self.run_cli()
        self.assert_fallback(rows, None)
        self.assertIn('fallbacks=1', stderr)

    def test_resolved_ambiguity_survives_hit_failure(self):
        self.table['customers'].append(customer('C2', dob='1970-01-01'))
        self.table['accounts'].append({'account_id': 'ACC1', 'customer_id': 'C2', 'login_handle': 'alex2'})
        save(self.data, self.table)
        batch = s5_batch.Batch(Dataset(self.data))
        self.expected = batch.rows()
        self.assertEqual(self.expected[0]['customer_record_id'], 'AMBIGUOUS')
        with patch.object(s5_batch, 'decide', side_effect=[IndexError('bad evidence'), batch.decision('H2')]):
            rows, stderr = self.run_cli()
        self.assert_fallback(rows, 'AMBIGUOUS')
        self.assertIn('fallbacks=1', stderr)
