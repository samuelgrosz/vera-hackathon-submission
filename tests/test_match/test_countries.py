"""Unit tests for system/match/countries.py (Step 1c). Run: python -m unittest discover -s tests -t ."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "system"))

from match import countries as K  # noqa: E402


class Fields(unittest.TestCase):
    def test_codes_names_nationalities(self):
        for v in ["NG", "NGA", "Nigeria", "Nigerian", "nigeria", " Nigeria "]:
            self.assertEqual(K.canonical(v), {"NG"}, v)
        self.assertEqual(K.canonical("British"), {"GB"})
        self.assertEqual(K.canonical("Dutch"), {"NL"})
        self.assertEqual(K.canonical("USA"), {"US"})

    def test_accents_and_apostrophes(self):
        self.assertEqual(K.canonical("Côte d'Ivoire"), {"CI"})
        self.assertEqual(K.canonical("Cote D'Ivoire"), {"CI"})

    def test_not_countries(self):
        for v in ["International", "Europe", "", "N/A", "International [Associated]"]:
            self.assertEqual(K.canonical(v), set(), v)

    def test_uk_nations_are_gb_territories_are_own(self):
        self.assertEqual(K.canonical("Scotland"), {"GB"})
        self.assertEqual(K.canonical("Jersey"), {"JE"})
        self.assertEqual(K.canonical("Puerto Rico"), {"PR"})
        self.assertEqual(K.canonical("Macao Special Administrative Region of China"), {"MO"})

    def test_ambiguous_maps_to_all(self):
        self.assertEqual(K.canonical("Congo"), {"CG", "CD"})
        self.assertEqual(K.canonical("Georgia"), {"GE", "US"})
        self.assertEqual(K.canonical("Korea"), {"KR", "KP"})
        self.assertEqual(K.canonical("Yugoslavia"), {"RS", "HR", "SI", "BA", "MK", "ME", "XK"})

    def test_longest_name_wins(self):
        self.assertEqual(K.canonical("Niger"), {"NE"})
        self.assertEqual(K.canonical("South Sudan"), {"SS"})
        self.assertEqual(K.canonical("Papua New Guinea"), {"PG"})
        self.assertEqual(K.canonical("Dominican Republic"), {"DO"})

    def test_many(self):
        self.assertEqual(K.canonical_many("Spain; Italy; International"), {"ES", "IT"})


class Addresses(unittest.TestCase):
    def test_last_country_part(self):
        self.assertEqual(K.from_address("10 Harbour Rd, Suite 3, St. John's, NL, A1C 1K4, Canada [Business]"), {"CA"})
        self.assertEqual(K.from_address("Leeds, England"), {"GB"})

    def test_no_country(self):
        self.assertEqual(K.from_address("58 High Street, Lagos"), set())

    def test_many(self):
        self.assertEqual(K.from_addresses("Madrid, Spain [Business]; Spain [PEP]; Europe [Associated]"), {"ES"})


class FreeText(unittest.TestCase):
    def test_names_and_nationalities(self):
        self.assertEqual(K.find_in_text("The client resided and worked in Norway between 2008 and 2011"), {"NO"})
        self.assertEqual(K.find_in_text("validated the national ID of a Nigerian applicant"), {"NG"})

    def test_capitalisation_required(self):
        self.assertEqual(K.find_in_text("ate turkey off fine china"), set())
        self.assertEqual(K.find_in_text("moved from Turkey to China"), {"TR", "CN"})

    def test_all_caps_text(self):
        self.assertEqual(K.find_in_text("NATIONAL ID ISSUED IN GERMANY"), {"DE"})

    def test_possessive(self):
        self.assertEqual(K.find_in_text("France's local press"), {"FR"})
        self.assertEqual(K.find_in_text("Canada’s own"), {"CA"})

    def test_abbreviations_only_in_capitals(self):
        self.assertEqual(K.find_in_text("She moved to the UK in 2010"), {"GB"})
        self.assertEqual(K.find_in_text("tell us more"), set())

    def test_exclude_person_name(self):
        text = "Jordan Ellis, 44, a lecturer in Austria"
        self.assertEqual(K.find_in_text(text), {"JO", "AT"})
        self.assertEqual(K.find_in_text(text, ["Jordan Ellis"]), {"AT"})


class ResidenceCue(unittest.TestCase):
    def test_unresolved(self):
        self.assertTrue(K.residence_cue_unresolved("The customer used to live in Lagos."))

    def test_resolved_or_no_cue(self):
        self.assertFalse(K.residence_cue_unresolved("The customer used to live in Ghana."))
        self.assertFalse(K.residence_cue_unresolved("Card frozen and replacement ordered."))


class Names(unittest.TestCase):
    def test_display_name(self):
        self.assertEqual(K.name("NG"), "Nigeria")
        self.assertEqual(K.name("ZZ"), "ZZ")


if __name__ == "__main__":
    unittest.main()
