import json
import unittest
from pathlib import Path

from app.api.taxonomy import migrate_saved_filter, normalize_categories


ROOT = Path(__file__).resolve().parents[1]


class CompanyTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.companies = json.loads((ROOT / "app" / "companies.json").read_text(encoding="utf-8"))
        cls.by_name = {company["name"]: company for company in cls.companies}

    def test_every_company_has_canonical_unique_industries(self):
        forbidden = {
            "Technology", "Interview Friendly", "US / Remote", "AI", "Cloud",
            "Finance", "Fintech", "Retail", "Universities", "Academia",
        }
        for company in self.companies:
            categories = company.get("categories") or []
            self.assertTrue(categories, company["name"])
            self.assertEqual(categories, sorted(set(categories), key=str.casefold), company["name"])
            self.assertFalse(forbidden.intersection(categories), company["name"])

    def test_interview_friendly_is_an_attribute_not_an_industry(self):
        tagged = 0
        for company in self.companies:
            expected = (company.get("interview") or {}).get("no_whiteboard") is True
            actual = "Interview Friendly" in (company.get("tags") or [])
            self.assertEqual(actual, expected, company["name"])
            tagged += int(actual)
        self.assertEqual(tagged, 275)

    def test_reviewed_false_positive_examples(self):
        self.assertEqual(
            self.by_name["Fivetran"]["categories"],
            ["Cloud & Infrastructure", "Data & Analytics", "Software & SaaS"],
        )
        self.assertEqual(
            self.by_name["Scale AI"]["categories"],
            ["AI & Machine Learning", "Data & Analytics", "Software & SaaS"],
        )
        self.assertEqual(
            self.by_name["Apex"]["categories"],
            ["Aerospace & Defense", "Industrials & Manufacturing", "Space"],
        )

    def test_legacy_saved_filters_remain_usable(self):
        self.assertEqual(migrate_saved_filter("AI", ""), ("AI & Machine Learning", ""))
        self.assertEqual(migrate_saved_filter("Interview Friendly", ""), ("All", "Interview Friendly"))
        self.assertEqual(migrate_saved_filter("US / Remote", ""), ("All", ""))
        self.assertEqual(normalize_categories(["Retail", "Consumer Goods & Retail"]), ["Consumer Goods & Retail"])

    def test_zero_result_source_review_is_fully_documented(self):
        reviewed = {
            "Alexander Chapman", "Archer Aviation", "Articulate", "Aviator", "Basecamp",
            "Chesapeake Technology", "Coffee 'n Clothes", "Clerk", "Cummins", "dbt Labs",
            "DeepFin Research", "Delta Air Lines", "Dragonfly", "End Point Dev",
            "Freeport-McMoRan", "Georgia Tech", "Gower Street Analytics", "Hero Digital",
            "Giant Spoon", "HudsonGray", "Informatica", "Infosys", "Jefferies",
            "Johns Hopkins University", "Lindy", "ManTech", "Marriott", "MIT", "Mixmax",
            "Newfront", "Nubank", "Optiver", "Precision Neuroscience",
            "Principal Financial Group", "Pulley", "Purdue University", "Retool", "Sempra",
            "Shopify", "Teradata", "Tesla", "Truss", "Uber", "UKG", "Upstatement",
            "Vimeo", "Yum Brands",
        }
        authoritative_empty = {
            "Basecamp", "Clerk", "End Point Dev", "Giant Spoon", "Mixmax", "Pulley", "Truss",
        }
        retired = reviewed - authoritative_empty - {
            "Aviator", "dbt Labs", "Delta Air Lines", "Freeport-McMoRan", "Marriott",
            "Optiver", "Shopify", "Teradata", "Uber", "UKG",
        }
        self.assertEqual(len(reviewed), 47)
        for name in reviewed:
            self.assertIn("2026-07-21", self.by_name[name].get("note", ""), name)
        for name in authoritative_empty:
            self.assertTrue(self.by_name[name]["scrape"].get("allow_empty_full_scan"), name)
        for name in retired:
            self.assertIs(self.by_name[name].get("scan_enabled"), False, name)


if __name__ == "__main__":
    unittest.main()
