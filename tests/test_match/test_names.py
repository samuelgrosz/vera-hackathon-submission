"""Unit tests for system/match/names.py and translit.py (Step 1a).

Run from the repo root:  python -m unittest discover tests
Uses invented names and a hand-built variants list only: the dev data must not be
committed, so nothing here is copied from it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "system"))

from steps.data import NameVariants  # noqa: E402
from match import names as N  # noqa: E402

VARIANTS = NameVariants(groups=[
    ["muhammad", "mohammed", "mohamed", "mohammad"],
    ["ali", "aly"],
    ["john", "johnny", "jon", "johann"],
    ["thomas", "tom", "tommy"],
    ["yusuf", "yousef", "josef", "joseph", "joe"],
    ["joseph", "joe", "jose", "giuseppe"],
], legal_forms=["ltd", "sa", "gmbh", "llc", "plc", "na", "ag", "se"])
V = VARIANTS.lookup()
LF = set(VARIANTS.legal_forms)


def agree(a, b, **kw):
    return N.names_agree(a, b, V, **kw).agree


class Normalising(unittest.TestCase):
    def test_accents_brackets_case(self):
        self.assertEqual(N.tokenise("José MÜLLER (AKA)"), ["jose", "muller"])

    def test_letters_without_decomposition(self):
        self.assertEqual(N.tokenise("Đặng Søren Łukasz Strauß"), ["dang", "soren", "lukasz", "strauss"])

    def test_hyphen_splits_apostrophe_deleted(self):
        self.assertEqual(N.tokenise("Abu-Sa'id Al-Rawi"), ["abu", "said", "al", "rawi"])
        self.assertEqual(N.name_form("ʿAbd Al-ʿAziz").latin.split(), ["abd", "al-aziz"])

    def test_fullwidth_folded(self):
        self.assertEqual(N.name_form("Ｓａｍ Ｌｅｅ").method, N.NATIVE)


class Transliteration(unittest.TestCase):
    def test_cyrillic(self):
        f = N.name_form("Иван Петров (None, Cyrillic)")
        self.assertEqual((f.method, f.scripts), (N.TABLE, ("CYRILLIC",)))
        self.assertEqual(N.parts(f), ["ivan", "petrov"])

    def test_cyrillic_short_i_and_yo(self):
        self.assertEqual(N.parts("Йосиф Ёлкин"), ["yosif", "elkin"])

    def test_greek_armenian_georgian(self):
        self.assertEqual(N.parts("Γιώργος Παπαδόπουλος"), ["giorgos", "papadopoulos"])
        self.assertEqual(N.parts("Արամ Պետրոսյան"), ["aram", "petrosyan"])
        self.assertEqual(N.parts("ნიკა"), ["nika"])

    def test_kana(self):
        self.assertEqual(N.parts("ジョン・スミス"), ["jon", "sumisu"])

    def test_uncovered_script_kept_but_not_comparable(self):
        f = N.name_form("محمد علي")
        self.assertEqual(f.method, N.NONE)
        self.assertIsNone(f.latin)
        self.assertEqual(f.original, "محمد علي")
        self.assertTrue(f.non_latin)
        self.assertEqual(N.parts(f), [])


class Parts(unittest.TestCase):
    def test_initials_placeholders_dedupe(self):
        self.assertEqual(N.parts("Anna K. N/A Petrova XXX 0000 Petrova"), ["anna", "petrova"])
        self.assertEqual(N.parts("FNU Hassan"), ["hassan"])

    def test_particles_kept(self):
        self.assertEqual(N.parts("Omar bin Al-Hadi"), ["omar", "bin", "al", "hadi"])

    def test_legal_forms_only_for_companies(self):
        self.assertEqual(N.parts("Acme Widgets Ltd", True, LF), ["acme", "widgets"])
        self.assertEqual(N.parts("Acme Widgets Ltd", False, LF), ["acme", "widgets", "ltd"])
        self.assertEqual(N.parts("Acme S.A.", True, LF), ["acme"])

    def test_undecidable(self):
        self.assertTrue(N.is_undecidable("Pavel N/a N/a"))
        self.assertFalse(N.is_undecidable("Pavel Novak"))


class PartMatch(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(N.part_match("ali", "ali", V), N.IDENTICAL)
        self.assertEqual(N.part_match("mohamed", "muhammad", V), N.VARIANT)
        self.assertEqual(N.part_match("petrova", "petrovna", V), N.TYPO)
        self.assertEqual(N.part_match("jonny", "john", V), N.VARIANT_TYPO)

    def test_edit_allowance_by_shorter_length(self):
        self.assertIsNone(N.part_match("lee", "lea", V))          # <4 letters: exact
        self.assertIsNone(N.part_match("anna", "ana", V))         # shorter is 3: exact
        self.assertEqual(N.part_match("maria", "marie", V), N.TYPO)
        self.assertIsNone(N.part_match("maria", "mairo", V))      # 2 edits, 5 letters
        self.assertEqual(N.part_match("aleksander", "aleksandr", V), N.TYPO)
        self.assertIsNone(N.part_match("alexander", "aleksandr", V))   # 3 edits: policy says no

    def test_swap_is_two_edits(self):
        self.assertEqual(N.edit_distance("marta", "mrata"), 2)
        self.assertIsNone(N.part_match("marta", "mrata", V))

    def test_variant_groups_do_not_chain_through_shared_member(self):
        # yusuf and giuseppe share no group, though joe/joseph sit in both groups.
        self.assertIsNone(N.part_match("yusuf", "giuseppe", V))


class Agreement(unittest.TestCase):
    def test_reordered(self):
        self.assertTrue(agree("Wei Li", "Li Wei"))

    def test_two_parts_match(self):
        self.assertTrue(agree("Tom Okafor", "Thomas Okafor"))
        self.assertTrue(agree("Mohamed Ali Hassan", "Muhammad Aly"))

    def test_shorter_name_fully_matched(self):
        self.assertTrue(agree("Hassan", "Ahmed Hassan"))

    def test_one_shared_part_is_not_enough(self):
        self.assertFalse(agree("John Whitmore", "Peter Whitmore"))
        self.assertFalse(agree("Jonathan Hale Whitmore", "John Whitmore"))

    def test_each_part_used_once(self):
        r = N.names_agree("Ali Hassan Ali", "Ali Karim", V)
        self.assertEqual([m.a for m in r.matches], ["ali"])

    def test_joined_and_split(self):
        self.assertTrue(agree("Abdulsalam", "Abdul Salam"))
        self.assertTrue(agree("Abdulsalaam Okoro", "Abdul Salam Okoro"))
        self.assertTrue(agree("Abdulrahmanal Okoro", "Abdul Rahman Al Okoro"))

    def test_transliterated_alias_agrees(self):
        self.assertTrue(agree("Ivan Petrov", "Иван Петров"))

    def test_companies(self):
        self.assertTrue(agree("Acme Widgets Ltd", "ACME WIDGETS LLC", is_company=True, legal_forms=LF))
        self.assertTrue(agree("Acme S.A.", "Acme, Sociedad Anonima", is_company=True, legal_forms=LF))

    def test_deterministic(self):
        a = N.names_agree("Mohamed Ali Ali Hassan", "Ali Muhammad Hasan", V)
        for _ in range(5):
            self.assertEqual(N.names_agree("Mohamed Ali Ali Hassan", "Ali Muhammad Hasan", V), a)


class NameMismatch(unittest.TestCase):
    def test_clear_mismatch(self):
        self.assertTrue(N.name_mismatch("Sarah Whitmore", ["Whitmore, James Peter"], V))

    def test_any_agreeing_alias_blocks(self):
        self.assertFalse(N.name_mismatch("Ivan Petrov", ["Pyotr Sidorov", "Иван Петров"], V))

    def test_one_way_rule_only_non_latin_names(self):
        self.assertFalse(N.name_mismatch("Sarah Whitmore", ["Иван Петров"], V))
        self.assertFalse(N.name_mismatch("Sarah Whitmore", ["محمد علي"], V))

    def test_latin_name_present_still_decides(self):
        self.assertTrue(N.name_mismatch("Sarah Whitmore", ["Ivan Petrov", "Иван Петров", "محمد"], V))

    def test_empty_customer_name_is_never_a_mismatch(self):
        self.assertFalse(N.name_mismatch("N/A XXX", ["Ivan Petrov"], V))


if __name__ == "__main__":
    unittest.main()
