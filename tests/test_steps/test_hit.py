"""Unit tests for system/steps/hit.py (Step 3). Run: python -m unittest discover -s tests -t .
Invented hits and search results only: the dev data must not be committed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "system"))

import steps.hit as H  # noqa: E402
from steps.data import Data, NameVariants  # noqa: E402
from steps.model import PartialDate as P  # noqa: E402


def make_data(results=()):
    d = Data(folder="", tables={}, variants=NameVariants())
    for r in results:
        d.search_by_query.setdefault(r["query"], []).append(r)
    return d


def person(**kw):
    base = {"hit_id": "H-TEST01", "alert_id": "A-TEST01", "entity_type": "Individual",
            "name": "Okoro, Daniel", "categories": "PEP", "primary_category": None}
    base.update(kw)
    return base


def years(pic):
    return sorted({f.value.year for f in pic.dobs})


class Structured(unittest.TestCase):
    def test_dobs_field(self):
        pic = H.profile(person(dobs="1996; 1997"), make_data())
        self.assertEqual([f.value for f in pic.dobs], [P(1996), P(1997)])
        self.assertTrue(all(f.source == "H-TEST01" and f.kind == "dob" for f in pic.dobs))

    def test_list_fields_from_jsonl(self):
        pic = H.profile(person(dobs=["1960-05-01"], nationality=["Nigerian"],
                               addresses=[{"city": "Accra", "country": "Ghana"}]), make_data())
        self.assertEqual(pic.dobs[0].value, P(1960, 5, 1))
        self.assertEqual({f.value for f in pic.countries}, {"NG", "GH"})

    def test_countries_from_every_field(self):
        pic = H.profile(person(countries="Spain; International", nationality="Peru",
                               citizenship="Italy", addresses="1 Main St, Toronto, Canada [Business]; Europe [PEP]",
                               place_of_birth="Codogno, Lombardy, Italy"), make_data())
        self.assertEqual({f.value for f in pic.countries}, {"ES", "PE", "IT", "CA"})

    def test_names_and_aliases(self):
        pic = H.profile(person(aliases="Danny Okoro; Даниэль Окоро"), make_data())
        self.assertEqual([f.value for f in pic.names], ["Okoro, Daniel", "Danny Okoro", "Даниэль Окоро"])
        self.assertEqual([f.origin for f in pic.names], ["name", "alias", "alias"])

    def test_category_falls_back_to_first(self):
        self.assertEqual(H.profile(person(), make_data()).primary_category, "PEP")
        self.assertEqual(H.category({"primary_category": "Sanctions", "categories": "PEP"}), "Sanctions")
        self.assertEqual(H.category({}), "Other")


class Death(unittest.TestCase):
    def test_death_date(self):
        pic = H.profile(person(life_status="Deceased", deceased_date="2019-05"), make_data())
        self.assertEqual(pic.death_date.value, P(2019, 5))

    def test_deceased_without_date_is_not_a_fact(self):
        pic = H.profile(person(life_status="Deceased"), make_data())
        self.assertIsNone(pic.death_date)


class Companies(unittest.TestCase):
    def test_only_dobs_counts_as_incorporation(self):
        hit = person(entity_type="Company", name="Acme Widgets Ltd", dobs="2015",
                     profile_notes="Acme Widgets was founded by a man aged 40 in March 2010.")
        pic = H.profile(hit, make_data())
        self.assertEqual([(f.kind, f.value) for f in pic.dobs], [("incorporation", P(2015))])

    def test_company_never_uses_search(self):
        r = {"result_id": "SR-AAA001", "query": "acme ltd widgets", "title": "Acme",
             "snippet": "Acme Widgets Ltd, 12, named on a watchlist in Spain in May 2020."}
        pic = H.profile(person(entity_type="Company", name="Acme Widgets Ltd", countries="Spain"),
                        make_data([r]))
        self.assertEqual(pic.dobs, [])


class ProfileNotes(unittest.TestCase):
    def notes(self, text):
        return H.profile(person(profile_notes=text), make_data())

    def test_explicit_dates(self):
        self.assertEqual([f.value for f in self.notes("Daniel Okoro was born on 4 March 1961.").dobs], [P(1961, 3, 4)])
        self.assertEqual([f.value for f in self.notes("Daniel Okoro was born in 1958.").dobs], [P(1958)])
        self.assertEqual([f.value for f in self.notes("DOB: 6/20/1955").dobs], [P(1955, 6, 20)])

    def test_age_wordings(self):
        for text in [
            "Charges were filed against Daniel Okoro, aged 30, in November 1998.",
            "November 1998 saw Daniel Okoro, a 30-year-old, face charges.",
            "In November 1998 the 30-year-old Daniel Okoro was charged.",
            "Daniel Okoro reached the age of 30 in November 1998.",
            "Questioned in November 1998, Daniel Okoro was 30 years old.",
            "The age of Daniel Okoro was 30 when he was charged in November 1998.",
            "Daniel Okoro reached 30 years of age by November 1998.",
            "Daniel Okoro, at age 30, was charged in November 1998.",
            "Daniel Okoro, 30, was charged in November 1998.",
        ]:
            self.assertEqual(years(self.notes(text)), [1967, 1968], text)

    def test_age_without_date_gives_nothing(self):
        self.assertEqual(self.notes("Name: DANIEL OKORO\nAge: 55\nSex: Male").dobs, [])
        self.assertEqual(self.notes("Daniel Okoro, aged 30, was charged.").dobs, [])

    def test_only_first_paragraph(self):
        pic = self.notes("Daniel Okoro is a senator.\n\nHis brother was born in 1950.")
        self.assertEqual(pic.dobs, [])

    def test_dobs_field_wins_over_notes(self):
        pic = H.profile(person(dobs="1961", profile_notes="Daniel Okoro, aged 30, in November 1998."), make_data())
        self.assertEqual(years(pic), [1961])


class DatedReports(unittest.TestCase):
    def test_exact_form_only_and_sorted(self):
        notes = ("According to the Gazette; September 10, 2025: Daniel Okoro was charged.\n"
                 "According to Reuters; March 02, 2011: earlier story.\n"
                 "According to the Courier; June 2014: no day, does not count.\n"
                 "According to the Bureau of Prisons, information as of December 13, 2017:\n"
                 "  According to the Herald; May 5, 2019: indented still counts.")
        pic = H.profile(person(profile_notes=notes), make_data())
        self.assertEqual([f.value for f in pic.dated_reports], [P(2011, 3, 2), P(2019, 5, 5), P(2025, 9, 10)])


class Search(unittest.TestCase):
    Q = "daniel okoro"

    def result(self, rid, snippet):
        return {"result_id": rid, "query": self.Q, "title": "Daniel Okoro - news", "snippet": snippet}

    def test_kept_only_with_phrase_and_tied_country(self):
        results = [
            self.result("SR-000001", "A regulator's notice from May 2002 listed Daniel Okoro, 29, a politically exposed person in Ghana."),
            self.result("SR-000002", "Daniel Okoro, 57, a politically exposed person in Kenya, in June 2010."),   # wrong country
            self.result("SR-000003", "Daniel Okoro, 40, a marathon runner in Ghana, in June 2010."),            # wrong phrase
            self.result("SR-000004", "Daniel Okoro, 45, named on a watchlist in Ghana in June 2010."),          # other category's phrase
        ]
        pic = H.profile(person(countries="Ghana"), make_data(results))
        self.assertEqual(years(pic), [1972, 1973])
        self.assertEqual({f.source for f in pic.dobs}, {"SR-000001"})

    def test_every_kept_result_counts(self):
        results = [
            self.result("SR-000001", "In May 2002, Daniel Okoro, 29, a politically exposed person in Ghana."),
            self.result("SR-000002", "In May 2012, Daniel Okoro, 50, a politically exposed person in Ghana."),
        ]
        pic = H.profile(person(countries="Ghana"), make_data(results))
        self.assertEqual(years(pic), [1961, 1962, 1972, 1973])

    def test_no_tied_country_means_nothing_kept(self):
        results = [self.result("SR-000001", "In May 2002, Daniel Okoro, 29, a politically exposed person in Ghana.")]
        self.assertEqual(H.profile(person(), make_data(results)).dobs, [])

    def test_person_name_is_not_a_country(self):
        r = {"result_id": "SR-000009", "query": "jordan okoro", "title": "Jordan Okoro - news",
             "snippet": "In May 2002, Jordan Okoro, 29, a politically exposed person in Kenya."}
        pic = H.profile(person(name="Okoro, Jordan", countries="Jordan"), make_data([r]))
        self.assertEqual(pic.dobs, [])

    def test_search_only_when_no_dob(self):
        results = [self.result("SR-000001", "In May 2002, Daniel Okoro, 29, a politically exposed person in Ghana.")]
        pic = H.profile(person(countries="Ghana", dobs="1961"), make_data(results))
        self.assertEqual(years(pic), [1961])

    def test_accented_name_finds_unaccented_key(self):
        r = {"result_id": "SR-000010", "query": "muller stefan", "title": "Stefan Müller - news",
             "snippet": "In May 2002, Stefan Müller, 29, a politically exposed person in Germany."}
        pic = H.profile(person(name="Müller, Stefan", countries="Germany"), make_data([r]))
        self.assertEqual(years(pic), [1972, 1973])

    def test_listing_phrases(self):
        self.assertEqual(H.listing_phrase("Sanctions"), "a sanctioned individual")
        self.assertEqual(H.listing_phrase("adverse media"), "named in adverse media")
        self.assertEqual(H.listing_phrase("Registration"), "named on a watchlist")


class Determinism(unittest.TestCase):
    def test_same_picture_every_time(self):
        hit = person(countries="Ghana", profile_notes="According to X; May 5, 2019: y")
        a = H.profile(hit, make_data())
        for _ in range(5):
            self.assertEqual(H.profile(hit, make_data()), a)


if __name__ == "__main__":
    unittest.main()
