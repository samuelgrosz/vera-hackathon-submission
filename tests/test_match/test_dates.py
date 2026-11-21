"""Unit tests for system/match/dates.py (Step 1b). Run: python -m unittest discover -s tests -t ."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "system"))

from match import dates as D  # noqa: E402
from steps.model import PartialDate as P  # noqa: E402


class Parsing(unittest.TestCase):
    def test_every_field_format(self):
        for text in ["1988-03-14", "14/03/1988", "14 March 1988", "March 14, 1988",
                     "Mar 14, 1988", "14th Mar 1988", "14.03.1988", "1988/03/14"]:
            self.assertEqual(D.parse(text), P(1988, 3, 14), text)

    def test_partial(self):
        self.assertEqual(D.parse("1988"), P(1988))
        self.assertEqual(D.parse("1988-03"), P(1988, 3))
        self.assertEqual(D.parse("March 1988"), P(1988, 3))
        self.assertEqual(D.parse("circa 1970"), P(1970))

    def test_day_first_with_two_readings(self):
        self.assertEqual(D.readings("03/04/1988"), [P(1988, 4, 3), P(1988, 3, 4)])
        self.assertEqual(D.readings("04/04/1988"), [P(1988, 4, 4)])

    def test_month_first_only_when_forced(self):
        self.assertEqual(D.readings("04/13/1988"), [P(1988, 4, 13)])

    def test_invalid_is_none(self):
        for text in ["31/02/1988", "1988-13-01", "", "N/A", "0000", "32 May 1990"]:
            self.assertIsNone(D.parse(text), text)

    def test_many(self):
        self.assertEqual(D.parse_many("1996; 1997"), [P(1996), P(1997)])
        self.assertEqual(D.parse_many("1965-1968"), [P(1965), P(1966), P(1967), P(1968)])
        self.assertEqual(D.parse_many("between 1965 and 1966"), [P(1965), P(1966)])
        self.assertEqual(len(D.parse_many("1970s")), 10)
        self.assertEqual(D.parse_many("1960-05-01; 1960-05-01; 1961"), [P(1960, 5, 1), P(1961)])

    def test_find_in_text(self):
        found = [d for d, _, _ in D.find_dates(
            "According to the Gazette; September 10, 2025: charged in November 1998, "
            "born on 17 July 1939. They may 2019 return.")]
        self.assertEqual(found, [P(2025, 9, 10), P(1998, 11), P(1939, 7, 17)])

    def test_bare_years_only_on_request(self):
        self.assertEqual(D.find_dates("was born in 1952"), [])
        self.assertEqual([d for d, _, _ in D.find_dates("was born in 1952", bare_years=True)], [P(1952)])


class BirthYears(unittest.TestCase):
    def test_age_at_date(self):
        self.assertEqual(D.birth_years_from_age(45, P(2019, 3)), [1973, 1974])


class Comparing(unittest.TestCase):
    def test_agree(self):
        self.assertTrue(D.dob_agrees(P(1988, 3, 14), P(1988)))
        self.assertTrue(D.dob_agrees(P(1988), P(1988, 3, 14)))
        self.assertTrue(D.dob_agrees(P(1988, 3, 14), P(1988, 3)))
        self.assertFalse(D.dob_agrees(P(1988, 3, 14), P(1989)))
        self.assertFalse(D.dob_agrees(P(1988, 3, 14), P(1988, 4)))

    def test_contradict(self):
        self.assertTrue(D.dob_contradicts(P(1988), P(1952)))
        self.assertTrue(D.dob_contradicts(P(1988, 3, 14), P(1988, 4)))
        self.assertTrue(D.dob_contradicts(P(1988, 3, 14), P(1988, 3, 15)))
        self.assertFalse(D.dob_contradicts(P(1988), P(1988, 3, 14)))

    def test_one_year_apart_is_neither(self):
        c, h = P(1988, 12, 31), P(1989, 1, 1)
        self.assertFalse(D.dob_agrees(c, h))
        self.assertFalse(D.dob_contradicts(c, h))

    def test_one_agreeing_hit_date_blocks_mismatch(self):
        self.assertFalse(D.dob_contradicts_all([P(1988, 3, 14)], [P(1952), P(1988)]))
        self.assertTrue(D.dob_contradicts_all([P(1988, 3, 14)], [P(1952), P(1960)]))

    def test_ambiguous_day_month_must_contradict_both_readings(self):
        # 03/04/1988: 3 April or 4 March. Hit says March 1988: agrees with one reading.
        self.assertFalse(D.dob_contradicts_all(D.readings("03/04/1988"), [P(1988, 3)]))
        self.assertTrue(D.dob_contradicts_all(D.readings("03/04/1988"), [P(1988, 7)]))

    def test_missing_is_not_a_mismatch(self):
        self.assertFalse(D.dob_contradicts_all([], [P(1952)]))
        self.assertFalse(D.dob_contradicts_all([P(1952)], []))

    def test_agreeing_pair(self):
        self.assertEqual(D.agreeing_dob([P(1975, 6, 1)], [P(1960), P(1975)]), (P(1975, 6, 1), P(1975)))


class Time(unittest.TestCase):
    def test_age_range(self):
        self.assertEqual(D.age_range(P(2004, 5, 19), P(2011, 3, 2)), (6, 6))
        self.assertEqual(D.age_range(P(2004), P(2011)), (6, 7))

    def test_under_16_uses_oldest_possible_age(self):
        self.assertTrue(D.definitely_younger_than(P(2004, 5, 19), P(2011, 3, 2)))
        self.assertTrue(D.definitely_younger_than(P(1995), P(2010)))       # 14 or 15
        self.assertFalse(D.definitely_younger_than(P(1994), P(2010)))      # 15 or 16
        self.assertFalse(D.definitely_younger_than(P(1994, 6, 1), P(2010, 6, 1)))  # exactly 16

    def test_before(self):
        self.assertTrue(D.definitely_before(P(2019, 5), P(2026, 11, 6)))
        self.assertFalse(D.definitely_before(P(2026, 11), P(2026, 11, 6)))  # same month: unknown

    def test_plausible(self):
        at = P(2026, 11, 6)
        self.assertFalse(D.is_plausible_dob(P(1833, 2, 28), at))
        self.assertFalse(D.is_plausible_dob(P(2027), at))
        self.assertTrue(D.is_plausible_dob(P(2017, 11, 28), at))


if __name__ == "__main__":
    unittest.main()
