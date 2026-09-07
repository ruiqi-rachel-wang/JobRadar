import json
import re
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
            "Climate & Energy",
            "Finance", "Fintech", "Retail", "Universities", "Academia",
        }
        for company in self.companies:
            categories = company.get("categories") or []
            self.assertTrue(categories, company["name"])
            self.assertEqual(categories, sorted(set(categories), key=str.casefold), company["name"])
            self.assertFalse(forbidden.intersection(categories), company["name"])

    def test_no_bulk_import_review_placeholders_remain(self):
        for company in self.companies:
            self.assertNotIn("Needs Review", company.get("tags") or [], company["name"])

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
        self.assertEqual(normalize_categories(["Climate & Energy", "Climate Tech"]), ["Climate Tech"])

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
        recovered = {
            "Articulate", "Cummins", "DeepFin Research", "Jefferies",
            "Johns Hopkins University", "Lindy", "ManTech", "Precision Neuroscience",
            "Principal Financial Group", "Retool", "Yum Brands",
        }
        retired = reviewed - authoritative_empty - recovered - {
            "Aviator", "dbt Labs", "Delta Air Lines", "Freeport-McMoRan", "Marriott",
            "Optiver", "Shopify", "Teradata", "Uber", "UKG",
        }
        self.assertEqual(len(reviewed), 47)
        for name in reviewed:
            self.assertRegex(
                self.by_name[name].get("note", ""),
                r"2026-(?:07-21|08-05|08-28)",
                name,
            )
        for name in authoritative_empty:
            self.assertTrue(self.by_name[name]["scrape"].get("allow_empty_full_scan"), name)
        for name in retired:
            self.assertIs(self.by_name[name].get("scan_enabled"), False, name)

    def test_recovered_company_sources_are_public(self):
        expected_modes = {
            "Arista Networks": "smartrecruiters",
            "Articulate": "lever",
            "Cohere": "ashby",
            "Cummins": "jobsyn",
            "DeepFin Research": "greenhouse",
            "Jefferies": "oraclecloud",
            "Johns Hopkins University": "eightfold",
            "Lindy": "html",
            "ManTech": "avature",
            "Principal Financial Group": "jobsapi",
            "Retool": "html",
            "Tradeweb": "jobsyn",
            "Yum Brands": "yum",
        }
        for name, mode in expected_modes.items():
            company = self.by_name[name]
            self.assertIsNot(company.get("scan_enabled"), False, name)
            self.assertEqual(mode, company["scrape"]["mode"], name)
            expected_date = (
                "2026-08-28"
                if name in {"Jefferies", "Johns Hopkins University", "Retool"}
                else "2026-08-05"
            )
            self.assertIn(expected_date, company.get("note", ""), name)

    def test_manual_search_targets_with_verified_sources_are_configured(self):
        expected_modes = {
            "Activision Blizzard": "workday",
            "AppFolio": "jobvite",
            "Bentley Systems": "successfactors",
            "BNY": "oraclecloud",
            "Capgemini": "html",
            "Canonical": "greenhouse",
            "Commvault": "greenhouse",
            "Criteo": "playwright",
            "Druva": "greenhouse",
            "EarnIn": "greenhouse",
            "Grubhub": "workday",
            "Guidewire": "workday",
            "Karat": "greenhouse",
            "Mobileye": "mobileye",
            "MathWorks": "xml_feed",
            "NCR Voyix": "workday",
            "Precision Neuroscience": "kula",
            "R1": "workday",
            "RBC": "workday",
            "Sanofi": "html",
            "Starbucks": "eightfold",
            "ThoughtSpot": "workday",
            "TikTok": "tiktok",
            "Valve": "html",
            "Verily": "workday",
            "Wealthfront": "lever",
        }
        for name, mode in expected_modes.items():
            company = self.by_name[name]
            self.assertIsNot(company.get("scan_enabled"), False, name)
            self.assertFalse(company.get("policy"), name)
            self.assertEqual(mode, company["scrape"]["mode"], name)

    def test_explicit_automated_access_restrictions_are_not_public(self):
        restriction = re.compile(
            r"robots\.txt.*disallows|disallowed by robots\.txt|blocks automated collection|"
            r"terms of service restrict|restricts automated access|"
            r"restricts third-party job aggregation",
            re.I,
        )
        matched = []
        for company in self.companies:
            text = " ".join([
                company.get("note") or "",
                (company.get("policy") or {}).get("message") or "",
            ])
            if restriction.search(text):
                matched.append(company["name"])
                self.assertIs(
                    company.get("scan_enabled"),
                    False,
                    company["name"],
                )
        self.assertGreaterEqual(len(matched), 10)


if __name__ == "__main__":
    unittest.main()
