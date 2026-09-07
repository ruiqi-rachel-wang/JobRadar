"""Canonical company taxonomy shared by filtering and data migrations."""

from __future__ import annotations


CATEGORY_ALIASES = {
    "AI": "AI & Machine Learning",
    "Academia": "Education & Academia",
    "Aerospace": "Aerospace & Defense",
    "Airline": "Transportation & Mobility",
    "Asset Management": "Investment & Asset Management",
    "Banking": "Finance & Banking",
    "Cloud": "Cloud & Infrastructure",
    "Climate & Energy": "Climate Tech",
    "Construction & Real Estate": "Real Estate & Construction",
    "Consumer": "Consumer Tech",
    "Customer Support": "Software & SaaS",
    "Data": "Data & Analytics",
    "Defense & Government": "Aerospace & Defense",
    "DevOps": "Developer Tools",
    "E-commerce": "E-commerce & Marketplace",
    "Education": "Education & Academia",
    "Equipment": "Industrials & Manufacturing",
    "Finance": "Finance & Banking",
    "Fintech": "FinTech & Payments",
    "Health Care": "Healthcare & Biotech",
    "HealthTech": "Healthcare & Biotech",
    "Industrials": "Industrials & Manufacturing",
    "Investment Banking": "Finance & Banking",
    "Logistics": "Logistics & Supply Chain",
    "Manufacturing": "Industrials & Manufacturing",
    "Marketplace": "E-commerce & Marketplace",
    "Payments": "FinTech & Payments",
    "Quant": "Quant Trading",
    "Real Estate": "Real Estate & Construction",
    "Retail": "Consumer Goods & Retail",
    "Robotics": "Robotics & Automation",
    "Social": "Social Media",
    "Transportation": "Transportation & Mobility",
    "Universities": "Education & Academia",
    "Wealth Management": "Investment & Asset Management",
}

ATTRIBUTE_CATEGORY_ALIASES = {
    "Interview Friendly": "Interview Friendly",
    # This old label was an internal target-list marker, not a verified company
    # attribute. It intentionally migrates to no filter rather than misleading
    # users as an industry or remote-work guarantee.
    "US / Remote": "",
}


def canonical_category(value: str | None) -> str:
    """Return the current name for a legacy category filter."""
    value = (value or "").strip()
    return CATEGORY_ALIASES.get(value, value)


def normalize_categories(values) -> list[str]:
    """Canonicalize, de-duplicate, and sort a company's industry categories."""
    result = []
    seen = set()
    for raw in values or []:
        value = canonical_category(raw)
        if not value or value in ATTRIBUTE_CATEGORY_ALIASES or value == "Technology":
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result, key=str.casefold)


def normalize_tags(values) -> list[str]:
    """De-duplicate company attributes independently of industries."""
    return sorted({str(value).strip() for value in values or [] if str(value).strip()}, key=str.casefold)


def migrate_saved_filter(category: str | None, tag: str | None = None) -> tuple[str, str]:
    """Translate a saved legacy category into the category/tag split."""
    category = (category or "All").strip() or "All"
    tag = (tag or "").strip()
    if category in ATTRIBUTE_CATEGORY_ALIASES:
        return "All", tag or ATTRIBUTE_CATEGORY_ALIASES[category]
    return canonical_category(category), tag
