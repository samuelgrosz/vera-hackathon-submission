"""Vera on the graded engine (system/vera/engine_bridge.py, DECISIONS.md R9)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .test_screening import customer, save, scenario  # importing it puts system/ on sys.path

from engine.s1_load import Dataset  # noqa: E402
from engine.s5_batch import Batch  # noqa: E402
from vera import engine_bridge  # noqa: E402


def run_both(table):
    with TemporaryDirectory() as folder:
        save(Path(folder), table)
        decisions, rows, _ = engine_bridge.decide_all(folder)
        graded = Batch(Dataset(Path(folder))).rows()
    return decisions, rows, graded


def tree(d):
    return {name: (status, detail) for name, status, detail in d.checks}


class VeraBridgeTests(unittest.TestCase):
    def test_rows_are_the_graded_rows(self):
        for table in (scenario(dobs=['1961']), scenario(dobs=['1994']), scenario()):
            decisions, rows, graded = run_both(table)
            self.assertEqual(rows, graded)
            d = decisions[0]
            self.assertEqual((d.decision, d.reason_code, d.customer_record_id, d.evidence),
                             (graded[0]['decision'], graded[0]['reason_code'],
                              graded[0]['customer_record_id'], graded[0]['evidence']))

    def test_clearance_tree_marks_the_deciding_check(self):
        d = run_both(scenario(dobs=['1961']))[0][0]
        self.assertEqual(d.reason_code, 'DOB_MISMATCH')
        self.assertEqual(tree(d)['Date of birth'][0], 'fired')
        self.assertEqual([s for _, s, _ in d.checks].count('fired'), 1)
        self.assertEqual(tree(d)['Confirmation'][0], 'skip')
        self.assertEqual(d.missing, [])

    def test_match_tree_fires_confirmation(self):
        d = run_both(scenario(dobs=['1994']))[0][0]
        self.assertEqual(d.decision, 'SAME_ENTITY')
        self.assertEqual(tree(d)['Confirmation'][0], 'fired')

    def test_review_gets_missing_facts_for_emails(self):
        d = run_both(scenario())[0][0]  # the listing gives no date of birth
        self.assertEqual(d.decision, 'REVIEW')
        self.assertIn('NO_HIT_DOB', d.missing)
        self.assertEqual(tree(d)['Date of birth'][0], 'unknown')

    def test_disagreeing_candidates_stop_at_customer_record(self):
        # Two account holders: C1's approved KYC date agrees with the listing, C2's CRM date contradicts it.
        table = scenario(dobs=['1994'])
        table['customers'].append(customer('C2', full_name='Alex Morgan', dob='1961-02-03'))
        table['accounts'].append(dict(account_id='ACC1', customer_id='C2', login_handle='alex2'))
        d = run_both(table)[0][0]
        self.assertEqual(d.reason_code, 'AMBIGUOUS_CUSTOMER_RECORD')
        self.assertEqual(tree(d)['Customer record'][0], 'fired')
        self.assertIn('AMBIGUOUS_HOLDER', d.missing)


if __name__ == '__main__':
    unittest.main()
