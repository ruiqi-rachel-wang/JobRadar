#!/usr/bin/env python3
"""Apply the reviewed 2026-07 company taxonomy to companies.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.taxonomy import normalize_categories, normalize_tags  # noqa: E402


# These companies previously had only the generic "Technology" label. Each
# replacement is based on the employer's product/business, not its open roles.
TECHNOLOGY_REPLACEMENTS = {
    "Alation": ["Data & Analytics", "Software & SaaS"],
    "Alteryx": ["Data & Analytics", "Software & SaaS"],
    "Ancestry": ["Consumer Tech"],
    "Anterior": ["AI & Machine Learning", "Healthcare & Biotech", "Software & SaaS"],
    "Apex": ["Aerospace & Defense", "Industrials & Manufacturing", "Space"],
    "Applied Materials": ["Industrials & Manufacturing", "Semiconductors & Hardware"],
    "Arine": ["Healthcare & Biotech", "Software & SaaS"],
    "Arkansas Children's": ["Healthcare & Biotech"],
    "Artefact": ["AI & Machine Learning", "Consulting", "Data & Analytics"],
    "Astera": ["Data & Analytics", "Software & SaaS"],
    "Authorium": ["Government", "Software & SaaS"],
    "Baton Corporation": ["Crypto", "Software & SaaS"],
    "BillionToOne": ["Healthcare & Biotech"],
    "Black Box": ["Cloud & Infrastructure", "IT Services"],
    "Boston Properties": ["Real Estate & Construction"],
    "Boys Town": ["Healthcare & Biotech", "Research"],
    "Brunswick": ["Consumer Goods & Retail", "Industrials & Manufacturing"],
    "Captivation": ["Aerospace & Defense", "Cybersecurity", "Software & SaaS"],
    "Centerfield": ["Ad Tech & MarTech"],
    "CesiumAstro": ["Aerospace & Defense", "Semiconductors & Hardware", "Space"],
    "CIBC": ["Finance & Banking"],
    "Clarity Innovations": ["Consulting", "EdTech"],
    "Clinical Architecture": ["Healthcare & Biotech", "Software & SaaS"],
    "Collective": ["Finance & Banking", "FinTech & Payments", "Software & SaaS"],
    "Compa": ["HR Tech", "Software & SaaS"],
    "Continental": ["Automotive", "Industrials & Manufacturing"],
    "Cox": ["Media & Entertainment", "Telecom"],
    "Cresta": ["AI & Machine Learning", "Software & SaaS"],
    "d-Matrix": ["AI & Machine Learning", "Semiconductors & Hardware"],
    "D3": ["Crypto", "Software & SaaS"],
    "Deliveroo": ["E-commerce & Marketplace", "Food & Beverage", "Logistics & Supply Chain"],
    "Delta Dental": ["Healthcare & Biotech", "Insurance"],
    "Docugami": ["AI & Machine Learning", "Data & Analytics", "Software & SaaS"],
    "Egis Group": ["Consulting", "Real Estate & Construction"],
    "Ellipsis Labs": ["Crypto", "Software & SaaS"],
    "Epic Kids": ["Consumer Tech", "EdTech", "Media & Entertainment"],
    "Eurofins": ["Healthcare & Biotech", "Research"],
    "Evolver": ["Consulting", "IT Services"],
    "EXL": ["Consulting", "Data & Analytics"],
    "Fife Council": ["Government"],
    "Flagright": ["Cybersecurity", "FinTech & Payments"],
    "Fluxergy": ["Healthcare & Biotech", "Medical Devices"],
    "Foxglove": ["Developer Tools", "Robotics & Automation", "Software & SaaS"],
    "Fullbay": ["Software & SaaS"],
    "Further": ["AI & Machine Learning", "Consulting", "Data & Analytics"],
    "GCI": ["Telecom"],
    "Gelber Group": ["Finance & Banking", "Quant Trading"],
    "GenScript": ["Healthcare & Biotech", "Research"],
    "Great Question": ["Data & Analytics", "Software & SaaS"],
    "HARMAN International": ["Automotive", "Consumer Electronics"],
    "Icon": ["Healthcare & Biotech", "Research"],
    "iHerb": ["Consumer Goods & Retail", "E-commerce & Marketplace"],
    "Imerys": ["Industrials & Manufacturing", "Materials & Chemicals"],
    "Inizio": ["Consulting", "Healthcare & Biotech"],
    "Instead": ["FinTech & Payments", "Software & SaaS"],
    "iSpot.tv": ["Ad Tech & MarTech", "Data & Analytics"],
    "Itron": ["Industrials & Manufacturing", "Utilities"],
    "JINGDONG": ["Consumer Goods & Retail", "E-commerce & Marketplace", "Logistics & Supply Chain"],
    "KBR": ["Aerospace & Defense", "Consulting"],
    "LG Ad Solutions": ["Ad Tech & MarTech", "Data & Analytics"],
    "Life Fitness": ["Health & Fitness", "Industrials & Manufacturing"],
    "Lightfield": ["AI & Machine Learning", "Software & SaaS"],
    "Liquid": ["Finance & Banking", "Quant Trading"],
    "LiveRamp": ["Ad Tech & MarTech", "Data & Analytics"],
    "Magna": ["Automotive", "Industrials & Manufacturing"],
    "MedImpact": ["Healthcare & Biotech", "Insurance"],
    "Menasha Corporation": ["Industrials & Manufacturing"],
    "Mercedes-Benz": ["Automotive", "Industrials & Manufacturing"],
    "Meshy": ["AI & Machine Learning", "Software & SaaS"],
    "Munters": ["Industrials & Manufacturing"],
    "N1": ["Crypto", "Software & SaaS"],
    "Nash": ["Logistics & Supply Chain", "Software & SaaS"],
    "Nationwide Children's Hospital": ["Healthcare & Biotech"],
    "Neighborly": ["Consumer Tech", "E-commerce & Marketplace"],
    "Nelnet": ["EdTech", "Finance & Banking"],
    "Neuralink": ["Healthcare & Biotech", "Medical Devices"],
    "NewsBreak": ["Consumer Tech", "Media & Entertainment"],
    "Nightwing Intelligence Solutions": ["Aerospace & Defense", "Cybersecurity"],
    "NOV": ["Energy", "Industrials & Manufacturing"],
    "Nuclear Promise X": ["Consulting", "Energy"],
    "OfferUp": ["Consumer Tech", "E-commerce & Marketplace"],
    "Oklahoma State Government": ["Government"],
    "Pebl": ["FinTech & Payments", "HR Tech", "Software & SaaS"],
    "Periodic Labs": ["AI & Machine Learning", "Research"],
    "Ping Identity": ["Cybersecurity", "Software & SaaS"],
    "PingWind": ["Government", "IT Services"],
    "Podium": ["Ad Tech & MarTech", "Software & SaaS"],
    "Quadric": ["AI & Machine Learning", "Semiconductors & Hardware"],
    "QuEra Computing": ["Research", "Semiconductors & Hardware"],
    "Ralliant": ["Industrials & Manufacturing"],
    "Realm": ["AI & Machine Learning", "Mining", "Software & SaaS"],
    "RELX": ["Data & Analytics", "Media & Entertainment"],
    "Reply": ["Consulting", "IT Services"],
    "Rho": ["FinTech & Payments", "Software & SaaS"],
    "Rokt": ["Ad Tech & MarTech", "E-commerce & Marketplace"],
    "Sandisk": ["Semiconductors & Hardware"],
    "Sandvik": ["Industrials & Manufacturing"],
    "SentiLink": ["Cybersecurity", "FinTech & Payments"],
    "SHEIN": ["E-commerce & Marketplace", "Fashion"],
    "Sundt": ["Real Estate & Construction"],
    "Super": ["Consumer Tech", "FinTech & Payments", "Travel & Hospitality"],
    "Symbotic": ["Industrials & Manufacturing", "Robotics & Automation"],
    "Syntax": ["Cloud & Infrastructure", "IT Services"],
    "Teamworks": ["Software & SaaS", "Sports"],
    "Tempo": ["Developer Tools", "Software & SaaS"],
    "Tencent": ["Gaming", "Media & Entertainment", "Software & SaaS"],
    "Tenstorrent": ["AI & Machine Learning", "Semiconductors & Hardware"],
    "The Coca-Cola Company": ["Consumer Goods & Retail", "Food & Beverage"],
    "Titan": ["AI & Machine Learning", "IT Services", "Software & SaaS"],
    "Toyota Research Institute": ["AI & Machine Learning", "Automotive", "Research"],
    "Trexon": ["Industrials & Manufacturing"],
    "True Anomaly": ["Aerospace & Defense", "Space"],
    "TrueBuilt": ["Real Estate & Construction", "Software & SaaS"],
    "TSC": ["Aerospace & Defense", "Industrials & Manufacturing"],
    "UL Solutions": ["Consulting", "Industrials & Manufacturing"],
    "Uncountable": ["Data & Analytics", "Materials & Chemicals", "Software & SaaS"],
    "Veolia": ["Energy", "Utilities"],
    "Viridien": ["Data & Analytics", "Energy"],
    "WebFX": ["Ad Tech & MarTech", "Consulting"],
    "Wonderschool": ["EdTech", "Software & SaaS"],
    "XPENG Motors": ["Automotive", "Industrials & Manufacturing"],
}


ATTRIBUTE_ONLY_REPLACEMENTS = {
    "3M": ["Industrials & Manufacturing", "Materials & Chemicals"],
    "ADM": ["Food & Beverage", "Industrials & Manufacturing"],
    "Advance Auto Parts": ["Automotive", "Consumer Goods & Retail"],
    "Aflac": ["Finance & Banking", "Insurance"],
    "Altria": ["Consumer Goods & Retail"],
    "Arizona State University": ["Education & Academia", "Research"],
    "Brown University": ["Education & Academia", "Research"],
    "Carnegie Mellon University": ["Education & Academia", "Research"],
    "Carnival Cruise Line": ["Transportation & Mobility", "Travel & Hospitality"],
    "Chick-fil-A": ["Consumer Goods & Retail", "Food & Beverage"],
    "Cornell University": ["Education & Academia", "Research"],
    "Emory University": ["Education & Academia", "Research"],
    "Fannie Mae": ["Finance & Banking", "Real Estate & Construction"],
    "Freddie Mac": ["Finance & Banking", "Real Estate & Construction"],
    "Genuine Parts Company": ["Automotive", "Consumer Goods & Retail"],
    "Georgetown University": ["Education & Academia", "Research"],
    "Goodyear": ["Automotive", "Industrials & Manufacturing"],
    "H-E-B": ["Consumer Goods & Retail", "Food & Beverage"],
    "Harvard University": ["Education & Academia", "Research"],
    "JetBlue Airways": ["Transportation & Mobility", "Travel & Hospitality"],
    "LPL Financial": ["Finance & Banking", "Investment & Asset Management"],
    "Marathon Petroleum": ["Energy"],
    "Mattel": ["Consumer Goods & Retail"],
    "New York University": ["Education & Academia", "Research"],
    "Northeastern University": ["Education & Academia", "Research"],
    "Ohio State University": ["Education & Academia", "Research"],
    "Penn State": ["Education & Academia", "Research"],
    "Princeton University": ["Education & Academia", "Research"],
    "Publix Super Markets": ["Consumer Goods & Retail", "Food & Beverage"],
    "QuikTrip": ["Consumer Goods & Retail", "Food & Beverage"],
    "REI": ["Consumer Goods & Retail"],
    "Rice University": ["Education & Academia", "Research"],
    "Southwest Airlines": ["Transportation & Mobility", "Travel & Hospitality"],
    "Stanford University": ["Education & Academia", "Research"],
    "Tapestry": ["Consumer Goods & Retail", "Fashion"],
    "Texas A&M University": ["Education & Academia", "Research"],
    "Texas Roadhouse": ["Consumer Goods & Retail", "Food & Beverage"],
    "UC San Diego": ["Education & Academia", "Research"],
    "UCLA": ["Education & Academia", "Research"],
    "UCSF": ["Education & Academia", "Healthcare & Biotech", "Research"],
    "University of Colorado Boulder": ["Education & Academia", "Research"],
    "University of Iowa": ["Education & Academia", "Research"],
    "University of Michigan": ["Education & Academia", "Research"],
    "University of Pennsylvania": ["Education & Academia", "Research"],
    "University of Southern California": ["Education & Academia", "Research"],
    "University of Texas at Austin": ["Education & Academia", "Research"],
    "Vanderbilt University": ["Education & Academia", "Research"],
    "Washington University in St. Louis": ["Education & Academia", "Research"],
    "Wawa": ["Consumer Goods & Retail", "Food & Beverage"],
    "Wegmans Food Markets": ["Consumer Goods & Retail", "Food & Beverage"],
    "Yale University": ["Education & Academia", "Research"],
}


# Confirmed false positives from the prior auto-tagging pass and other obvious
# cross-industry contradictions found in the full audit.
CORRECTED_COMPANIES = {
    "160over90": ["Ad Tech & MarTech", "Consulting"],
    "Artsy": ["E-commerce & Marketplace", "Media & Entertainment"],
    "Benchling": ["Healthcare & Biotech", "Software & SaaS"],
    "Character.AI": ["AI & Machine Learning", "Consumer Tech"],
    "Cintas": ["Business Services", "Industrials & Manufacturing"],
    "Clever": ["EdTech", "Software & SaaS"],
    "Clipboard": ["E-commerce & Marketplace", "Healthcare & Biotech", "HR Tech"],
    "Coffee 'n Clothes": ["Ad Tech & MarTech", "Consulting"],
    "Compass": ["Real Estate & Construction", "Software & SaaS"],
    "Copart": ["Automotive", "E-commerce & Marketplace"],
    "CoreOS": ["Cloud & Infrastructure", "Developer Tools"],
    "Cencora": ["Healthcare & Biotech", "Logistics & Supply Chain"],
    "Deel": ["FinTech & Payments", "HR Tech", "Software & SaaS"],
    "Deeplocal": ["Ad Tech & MarTech", "Consulting"],
    "Dropbox": ["Cloud & Infrastructure", "Software & SaaS"],
    "Encore Global": ["Business Services", "Media & Entertainment"],
    "Equifax": ["Data & Analytics", "Finance & Banking"],
    "Feld Entertainment": ["Media & Entertainment"],
    "Fivetran": ["Cloud & Infrastructure", "Data & Analytics", "Software & SaaS"],
    "Gensler": ["Consulting", "Real Estate & Construction"],
    "Giant Spoon": ["Ad Tech & MarTech", "Consulting"],
    "GitLab": ["Cybersecurity", "Developer Tools", "Software & SaaS"],
    "Gradient Experience": ["Ad Tech & MarTech", "Consulting"],
    "Houzz": ["E-commerce & Marketplace", "Real Estate & Construction"],
    "Inmar": ["Ad Tech & MarTech", "Data & Analytics", "Software & SaaS"],
    "Jack Morton": ["Ad Tech & MarTech", "Consulting"],
    "Kustomer": ["Software & SaaS"],
    "Lennar": ["Real Estate & Construction"],
    "Live Nation": ["Media & Entertainment", "Travel & Hospitality"],
    "Major League Soccer": ["Media & Entertainment", "Sports"],
    "ManTech": ["Aerospace & Defense", "Government", "IT Services"],
    "McKesson": ["Healthcare & Biotech", "Logistics & Supply Chain"],
    "Meow Wolf": ["Media & Entertainment", "Travel & Hospitality"],
    "MGM Resorts": ["Gaming", "Travel & Hospitality"],
    "Mixpanel": ["Data & Analytics", "Software & SaaS"],
    "NVE Experience Agency": ["Ad Tech & MarTech", "Consulting"],
    "Odeko": ["Food & Beverage", "Logistics & Supply Chain", "Software & SaaS"],
    "PagerDuty": ["Cloud & Infrastructure", "Developer Tools", "Software & SaaS"],
    "PulteGroup": ["Real Estate & Construction"],
    "Purdue University": ["Education & Academia", "Research"],
    "Redfin": ["E-commerce & Marketplace", "Real Estate & Construction"],
    "rEvolution": ["Ad Tech & MarTech", "Consulting"],
    "Scale AI": ["AI & Machine Learning", "Data & Analytics", "Software & SaaS"],
    "SAIC": ["Aerospace & Defense", "Government", "IT Services"],
    "SmartAsset": ["FinTech & Payments", "Finance & Banking"],
    "Swiftly": ["Ad Tech & MarTech", "Software & SaaS"],
    "TAO Group Hospitality": ["Food & Beverage", "Travel & Hospitality"],
    "Tekion": ["Automotive", "Software & SaaS"],
    "Textron": ["Aerospace & Defense", "Industrials & Manufacturing"],
    "TransMarket Group": ["Finance & Banking", "Quant Trading"],
    "Valassis Digital": ["Ad Tech & MarTech"],
    "Weave": ["Healthcare & Biotech", "Software & SaaS"],
    "Webflow": ["Developer Tools", "Software & SaaS"],
    "Weedmaps": ["E-commerce & Marketplace"],
    "WHOOP": ["Consumer Tech", "Health & Fitness"],
    "Yelp": ["Consumer Tech", "E-commerce & Marketplace"],
    "Zapier": ["Developer Tools", "Software & SaaS"],
}


def main() -> None:
    path = ROOT / "app" / "companies.json"
    companies = json.loads(path.read_text(encoding="utf-8"))
    by_name = {company["name"]: company for company in companies}
    replacements = {
        **TECHNOLOGY_REPLACEMENTS,
        **ATTRIBUTE_ONLY_REPLACEMENTS,
        **CORRECTED_COMPANIES,
    }
    missing = sorted(set(replacements) - set(by_name))
    if missing:
        raise SystemExit(f"Unknown companies in reviewed taxonomy: {missing}")

    before_categories = {category for c in companies for category in c.get("categories", [])}
    changed = 0
    for company in companies:
        old_categories = list(company.get("categories") or [])
        categories = replacements.get(company["name"], old_categories)
        company["categories"] = normalize_categories(categories)

        tags = list(company.get("tags") or [])
        interview = company.get("interview") or {}
        if interview.get("no_whiteboard") is True:
            tags.append("Interview Friendly")
        company["tags"] = normalize_tags(tags)
        if not company["tags"]:
            company.pop("tags", None)

        if old_categories != company["categories"]:
            changed += 1

    invalid = [
        company["name"]
        for company in companies
        if not company.get("categories")
        or "Technology" in company["categories"]
        or "Interview Friendly" in company["categories"]
        or "US / Remote" in company["categories"]
        or len(company["categories"]) != len(set(company["categories"]))
    ]
    if invalid:
        raise SystemExit(f"Taxonomy validation failed for: {invalid}")

    path.write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    after_categories = {category for c in companies for category in c.get("categories", [])}
    tagged = sum(bool(c.get("tags")) for c in companies)
    print(
        f"updated {changed}/{len(companies)} companies; "
        f"categories {len(before_categories)} -> {len(after_categories)}; "
        f"attribute-tagged companies: {tagged}"
    )


if __name__ == "__main__":
    main()
