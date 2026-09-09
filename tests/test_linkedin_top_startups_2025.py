import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

LINKEDIN_TOP_STARTUPS_2025 = """
Perplexity
Synctera
Underdog
Cadence
Charlie Health
ARMRA
Mercor
Function Health
Thinking Machines Lab
Inworld AI
David
Medallion
MagicSchool AI
Fireworks AI
Grow Therapy
ElevenLabs
Harvey
Chainguard
Haus
Cyera
Common Room
RYZE
The Nuclear Company
Zip
Together AI
Midi Health
Solace
CareYaya Health Technologies
Vial
Drata
tvScientific
Ambience Healthcare
Feastables
Vast
Surge AI
Transcarent
Phantom
Kindred
Artisan
Subject
Lumos
Truveta
Federato
Dandy
Edia
Reprise
GOODLES
Merge
ARB Interactive
Bilt Rewards
""".strip().splitlines()

ALIASES = {
    "Perplexity": "Perplexity AI",
    "Underdog": "Underdog Fantasy",
    "Vast": "Vast Space",
}


def _key(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class LinkedInTopStartups2025Tests(unittest.TestCase):
    def test_all_50_are_public_and_source_tagged(self):
        companies = json.loads((ROOT / "app" / "companies.json").read_text())
        by_key = {_key(company["name"]): company for company in companies}
        missing = []
        hidden = []
        untagged = []
        for published_name in LINKEDIN_TOP_STARTUPS_2025:
            catalog_name = ALIASES.get(published_name, published_name)
            company = by_key.get(_key(catalog_name))
            if not company:
                missing.append(published_name)
                continue
            is_public = company.get("catalog_visible") is True or (
                company.get("scan_enabled", True) is not False and not company.get("policy")
            )
            if not is_public:
                hidden.append(published_name)
            if "LinkedIn Top Startups 2025" not in (company.get("tags") or []):
                untagged.append(published_name)

        self.assertEqual(len(LINKEDIN_TOP_STARTUPS_2025), 50)
        self.assertEqual(missing, [])
        self.assertEqual(hidden, [])
        self.assertEqual(untagged, [])


if __name__ == "__main__":
    unittest.main()
