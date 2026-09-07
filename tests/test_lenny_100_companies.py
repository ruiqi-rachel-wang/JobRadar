import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Published at https://lenny.trueup.io/lenny100, last updated August 2026.
LENNY_100_AUGUST_2026 = """
Abridge
Airbnb
Airwallex
Anduril
Anthropic
Applied Compute
Applied Intuition
Base
BaseTen
Basis
Bedrock Robotics
Black Forest Labs
Chai Discovery
Chainguard
Clay
ClickHouse
Cloudflare
Cognition
CoreWeave
Crusoe
Cursor
Cyera
Databricks
Decagon
Deel
ElevenLabs
Etched
Exa
Factory
Fal
Figma
Figure
Fireworks
Gamma
Genspark
Glean
Google
Granola
Handshake
Harvey
Helsing
Hex
Kalshi
Lambda
LangChain
Legora
Linear
Liquid AI
LlamaIndex
Lovable
Mercor
Mercury
Mistral AI
Modal
n8n
Notion
NVIDIA
OpenAI
OpenEvidence
Palantir
Parallel Web Systems
Periodic Labs
Physical Intelligence
Pierre Computer
Polymarket
PostHog
Prime Intellect
Profound
Ramp
Reflection AI
Replit
Revolut
Rippling
Runway
Safe Superintelligence
Saronic
Shield AI
Shopify
Sierra
Skild
SpaceX
Stripe
Sunday Robotics
Supabase
Surge AI
Temporal
Tesla
Thinking Machines
Together
Turbopuffer
Vanta
Vercel
Waymo
Whatnot
Wispr Flow
WorkOS
World Labs
""".strip().splitlines()

DISPLAY_NAME_ALIASES = {
    "Base": "Base Power",
    "Bedrock Robotics": "Bedrock",
    "Fireworks": "Fireworks AI",
    "Hex": "Hex Technologies",
    "Thinking Machines": "Thinking Machines Lab",
    "Together": "Together AI",
}


def _key(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class Lenny100CompanyTests(unittest.TestCase):
    def test_every_published_company_is_in_the_catalog(self):
        companies = json.loads(
            (ROOT / "app" / "companies.json").read_text(encoding="utf-8")
        )
        catalog = {_key(company["name"]) for company in companies}
        public_catalog = {
            _key(company["name"])
            for company in companies
            if company.get("catalog_visible") is True
            or (
                company.get("scan_enabled", True) is not False
                and not company.get("policy")
            )
        }
        missing = [
            name
            for name in LENNY_100_AUGUST_2026
            if _key(DISPLAY_NAME_ALIASES.get(name, name)) not in catalog
        ]

        self.assertEqual(len(LENNY_100_AUGUST_2026), 97)
        self.assertEqual(missing, [])
        self.assertEqual(
            [
                name
                for name in LENNY_100_AUGUST_2026
                if _key(DISPLAY_NAME_ALIASES.get(name, name)) not in public_catalog
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
