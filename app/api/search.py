"""Keyword parsing, SQL query builders, and display-formatting helpers for job search."""
import re
from urllib.parse import unquote

from .database import IS_POSTGRES
from .utils import _COUNTRY_CODES, _US_STATES, norm, normalize_location

# ── Display constants ─────────────────────────────────────────────────────────

_DISPLAY_CITIES = (
    "New York", "Hong Kong", "San Francisco", "Los Angeles", "Bengaluru",
    "Hyderabad", "Mumbai", "London", "Singapore", "Tokyo", "Paris",
    "Dublin", "Toronto", "Chicago", "Dallas", "Warsaw", "Salt Lake City",
    "The Hague", "Richardson", "Albany", "Birmingham", "Stockholm", "Sydney",
    "Shenzhen",
)

# ── Keyword aliases ───────────────────────────────────────────────────────────

_KEYWORD_ALIASES = {
    "admin": {"administrative", "administrator"},
    "administrative": {"admin", "administrator"},
    "administrator": {"admin", "administrative"},
    "analysis": {"analyst", "analytics", "analy"},
    "analyst": {"analysis", "analytics", "analy"},
    "analytics": {"analysis", "analyst", "analy"},
    "business": {"biz"},
    "customer": {"client", "cx"},
    "client": {"customer"},
    "data": {"analytics", "analyst"},
    "design": {"designer", "ux", "ui"},
    "designer": {"design", "ux", "ui"},
    "dev": {"develop", "developer", "development", "developement", "engineering", "engineer"},
    "developer": {"dev", "develop", "development", "developement", "engineering", "engineer"},
    "development": {"dev", "develop", "developer", "developement", "engineering", "engineer"},
    "developement": {"dev", "develop", "developer", "development", "engineering", "engineer"},
    "engineer": {"engineering", "developer", "development", "developement", "sde"},
    "engineering": {"engineer", "developer", "development", "developement", "sde"},
    "finance": {"financial"},
    "financial": {"finance"},
    "frontend": {"front", "ui"},
    "front": {"frontend"},
    "backend": {"back", "server"},
    "marketing": {"market"},
    "market": {"marketing"},
    "machine": {"ml"},
    "learning": {"ml"},
    "manager": {"manage", "management", "mgr"},
    "management": {"manage", "manager", "mgr"},
    "mgr": {"manager", "management"},
    "operation": {"operations", "ops"},
    "operations": {"operation", "ops"},
    "ops": {"operation", "operations"},
    "people": {"hr", "human resources"},
    "pm": {"product", "program", "project", "manager", "management"},
    "product": {"pm"},
    "program": {"project", "pm"},
    "project": {"program", "pm"},
    "recruiter": {"recruiting", "talent"},
    "recruiting": {"recruiter", "talent"},
    "sales": {"account"},
    "security": {"cybersecurity", "cyber"},
    "software": {"swe", "sde"},
    "sde": {"software", "engineer", "engineering", "developer", "development"},
    "swe": {"software", "engineer", "engineering", "developer", "development"},
    "talent": {"recruiter", "recruiting"},
}

# ── Experience scoring ────────────────────────────────────────────────────────

def experience_score_sql(years):
    y = float(years)
    close = int(y) + 1
    return (
        "CASE "
        "WHEN exp_min IS NULL AND exp_max IS NULL THEN 0 "
        f"WHEN exp_min > {close} THEN -100 "
        f"WHEN COALESCE(exp_min,0) <= {y} AND exp_max IS NOT NULL AND exp_max >= {y} THEN 100 "
        f"WHEN COALESCE(exp_min,0) <= {y} AND exp_max IS NULL THEN 90 "
        f"WHEN exp_min <= {close} THEN 60 "
        "ELSE 10 END"
    )

# ── Keyword parsing & SQL ─────────────────────────────────────────────────────

def keyword_groups(keyword):
    tokens = [
        t for t in re.findall(r"[a-z0-9+#.]+", (keyword or "").lower())
        if t not in {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to"}
    ]
    groups = []
    seen = set()
    for token in tokens:
        group = _keyword_variants(token)
        key = tuple(sorted(group))
        if key not in seen:
            groups.append(sorted(group))
            seen.add(key)
    return groups

def keyword_where(groups):
    clauses, params = [], []
    for group in groups:
        bits = []
        for term in group:
            title_bits, title_params = _keyword_term_clauses("LOWER(title)", term)
            company_bits, company_params = _keyword_term_clauses("LOWER(company)", term)
            bits.extend(title_bits + company_bits)
            params.extend(title_params + company_params)
        clauses.append("(" + " OR ".join(bits) + ")")
    return clauses, params

def keyword_relevance_sql(keyword, groups):
    terms = {term for group in groups for term in group}
    phrase = " ".join(re.findall(r"[a-z0-9+#.]+", (keyword or "").lower()))
    parts = []
    if phrase:
        parts.extend([
            f"(CASE WHEN LOWER(title) = {_sql_lit(phrase)} THEN 1000 ELSE 0 END)",
            f"(CASE WHEN LOWER(title) LIKE {_sql_lit('%' + phrase + '%')} THEN 260 ELSE 0 END)",
        ])
    for group in groups:
        term_scores = [
            f"(CASE WHEN {_keyword_term_sql('LOWER(title)', term)} THEN {180 if term == group[0] else 120} ELSE 0 END)"
            for term in group
        ]
        company_scores = [
            f"(CASE WHEN {_keyword_term_sql('LOWER(company)', term)} THEN 20 ELSE 0 END)"
            for term in group
        ]
        best_fn = "GREATEST" if IS_POSTGRES else "max"
        parts.append(best_fn + "(" + ", ".join(term_scores) + ")" if len(term_scores) > 1 else term_scores[0])
        parts.append(best_fn + "(" + ", ".join(company_scores) + ")" if len(company_scores) > 1 else company_scores[0])
    if "software" in terms and any(t in terms for t in ("engineer", "engineering", "developer", "development", "developement")):
        parts.extend([
            "(CASE WHEN LOWER(title) LIKE '%software development engineer%' THEN 420 ELSE 0 END)",
            "(CASE WHEN LOWER(title) LIKE '%software developer%' THEN 360 ELSE 0 END)",
            "(CASE WHEN LOWER(title) LIKE '%sde%' THEN 340 ELSE 0 END)",
            "(CASE WHEN LOWER(title) LIKE '%senior%' THEN -90 ELSE 0 END)",
            "(CASE WHEN LOWER(title) LIKE '%staff%' OR LOWER(title) LIKE '%principal%' THEN -70 ELSE 0 END)",
        ])
    if "senior" not in terms and "sr" not in terms:
        parts.append("(CASE WHEN LOWER(title) LIKE '%senior%' OR LOWER(title) LIKE 'sr.%' THEN -40 ELSE 0 END)")
    return "(" + " + ".join(parts or ["0"]) + ")"

def _keyword_variants(token):
    variants = {token, *_KEYWORD_ALIASES.get(token, set())}
    if len(token) > 3:
        variants.add(token.rstrip("s"))
    if token.endswith("ies") and len(token) > 4:
        variants.add(token[:-3] + "y")
    if token.endswith("ing") and len(token) > 6:
        root = token[:-3]
        variants.update({root, root + "e"})
    if token.endswith("er") and len(token) > 5:
        variants.add(token[:-2])
    if token.endswith("or") and len(token) > 5:
        variants.add(token[:-2])
    if token.endswith("ment") and len(token) > 7:
        variants.add(token[:-4])
    if token.endswith("tion") and len(token) > 7:
        variants.add(token[:-4])
    if len(token) >= 6:
        variants.add(token[:5])
    return {v for v in variants if v}

def _keyword_term_clauses(field, term):
    if len(term) <= 2 and term.isalnum():
        patterns = [term, f"{term} %", f"% {term}", f"% {term} %",
                    f"%-{term}-%", f"%/{term}/%", f"%({term})%"]
        return [f"{field} LIKE ?" for _ in patterns], patterns
    return [f"{field} LIKE ?"], [f"%{term}%"]

def _keyword_term_sql(field, term):
    if len(term) <= 2 and term.isalnum():
        patterns = [term, f"{term} %", f"% {term}", f"% {term} %",
                    f"%-{term}-%", f"%/{term}/%", f"%({term})%"]
        return "(" + " OR ".join(f"{field} LIKE {_sql_lit(p)}" for p in patterns) + ")"
    return f"{field} LIKE {_sql_lit('%' + term + '%')}"

def _sql_lit(value):
    return "'" + str(value).replace("'", "''") + "'"

# ── Display title / location helpers ─────────────────────────────────────────

def display_title(title, url=""):
    slug_title = _title_from_opening_slug(url)
    if slug_title:
        return slug_title
    title = norm(title)
    if "·" in title:
        title = title.split("·", 1)[0].strip()
    title = _dedupe_joined_city(title)
    title = re.sub(
        r'\b(Analyst|Associate|Senior Associate|Vice President|Director)'
        r'(New York|Hong Kong|San Francisco|Los Angeles|Bengaluru|Hyderabad|Mumbai|'
        r'London|Singapore|Tokyo|Paris|Dublin|Toronto|Chicago)\b',
        r'\1 - \2', title,
    )
    title = re.sub(
        r'\b(Hong Kong|Beijing|Shanghai|Shenzhen|London|Bengaluru|Hyderabad|Mumbai|'
        r'Singapore|Tokyo|Paris|Dublin|Toronto|Chicago|New York)'
        r'(China|India|United Kingdom|United States|Canada)\b',
        r'\1', title,
    )
    return norm(title)

def display_location(raw_location, title, url=""):
    loc = normalize_location(raw_location or "")
    title_loc = _location_from_title_noise(title, url)
    url_loc = _location_from_job_url(url)
    if _bad_display_location(loc, title):
        return title_loc or url_loc or ""
    return loc or title_loc or url_loc

def _bad_display_location(loc, title=""):
    loc = norm(loc)
    if not loc:
        return False
    if re.fullmatch(r'(?:ATCI[-\w]*|(?:REQ|JR|WD|EB|R|S)[-_]?\d[\w-]*|\d{5,})', loc, re.I):
        return True
    title_l = norm(title).lower()
    loc_l = loc.lower()
    has_geo_signal = (
        loc_l in {"remote", "multiple locations", "us", "uk", "india", "canada"}
        or any(city.lower() in loc_l for city in _DISPLAY_CITIES)
        or any(code in _US_STATES or code in _COUNTRY_CODES for code in re.findall(r'\b[A-Z]{2}\b', loc))
    )
    if loc_l and loc_l in title_l and not has_geo_signal:
        return True
    if re.search(r'\b(software|engineer|engineering|runtime|backend|frontend|solutions|'
                 r'foundation|architecture|architect|product|program|project|analyst|associate)\b',
                 loc, re.I) and not has_geo_signal:
        return True
    return False

def _location_from_title_noise(title, url=""):
    slug_location = _location_from_opening_slug(url)
    if slug_location:
        return slug_location
    title = norm(title)
    country = ""
    if "·" in title:
        parts = [p.strip() for p in title.split("·") if p.strip()]
        for part in parts[1:]:
            if part.lower() in {"analyst", "associate", "senior associate", "vice president", "director"}:
                continue
            country = part
            break
    base = title.split("·", 1)[0]
    m = re.search(
        r'\b(New York|Hong Kong|San Francisco|Los Angeles|Bengaluru|Hyderabad|Mumbai|'
        r'London|Singapore|Tokyo|Paris|Dublin|Toronto|Chicago)\1\b', base,
    ) or re.search(r'\b([A-Z][a-z]+)\1\b', base)
    if m:
        return normalize_location(f"{m.group(1)}, {country}") if country else normalize_location(m.group(1))
    m = re.search(
        r'\b(Hong Kong|Beijing|Shanghai|Shenzhen|London|Bengaluru|Hyderabad|Mumbai|'
        r'Singapore|Tokyo|Paris|Dublin|Toronto|Chicago|New York)'
        r'(China|India|United Kingdom|United States|Canada)\b', base,
    )
    if m:
        return normalize_location(f"{m.group(1)}, {m.group(2)}")
    m = re.search(r'([A-Z][A-Za-z.-]+(?:\s+[A-Z][A-Za-z.-]+){0,2}),\s*([A-Z]{2})\b', base)
    if m and m.group(2) in _US_STATES:
        return normalize_location(f"{m.group(1)}, {m.group(2)}")
    for city in _DISPLAY_CITIES:
        if re.search(rf'(?:^|[-,\s]){re.escape(city)}\s*$', base):
            return normalize_location(city)
        if city in base[-80:]:
            return normalize_location(city)
    return ""

def _location_from_job_url(url):
    try:
        path = url.split("?", 1)[0]
    except Exception:
        return ""
    m = re.search(r'/job/([^/]+)/[^/]+$', path)
    if not m:
        return ""
    slug = unquote(m.group(1)).replace("_", "-")
    raw = norm(slug.replace("-", " ")).strip()
    if not raw:
        return ""
    for city in sorted(_DISPLAY_CITIES, key=len, reverse=True):
        if raw.lower().startswith(city.lower()):
            return normalize_location(city)
    for city in ("pune", "bengaluru", "bangalore", "hyderabad", "mumbai", "chennai", "gurugram"):
        if raw.lower().startswith(city):
            return normalize_location(city)
    return normalize_location(raw)

def _dedupe_joined_city(value):
    for city in _DISPLAY_CITIES:
        value = re.sub(re.escape(city) + re.escape(city), city, value)
    value = re.sub(r'\b([A-Z][a-z]+)\1\b', r'\1', value)
    return value

def _title_from_opening_slug(url):
    slug = _opening_slug(url)
    if not slug or "-at-" not in slug:
        return ""
    title_part = slug.split("-at-", 1)[0]
    return norm(title_part.replace("-", " ").title())

def _location_from_opening_slug(url):
    slug = _opening_slug(url)
    if not slug or "-at-" not in slug:
        return ""
    loc_part = slug.split("-at-", 1)[1]
    parts = [p for p in loc_part.split("-") if p]
    if len(parts) >= 2 and re.fullmatch(r'[a-z]{2}', parts[1], re.I):
        return normalize_location(f"{parts[0]}, {parts[1].upper()}")
    if len(parts) >= 3 and re.fullmatch(r'[a-z]{2}', parts[2], re.I):
        return normalize_location(f"{parts[0]} {parts[1]}, {parts[2].upper()}")
    return ""

def _opening_slug(url):
    try:
        path = url.split("?", 1)[0].rstrip("/")
    except Exception:
        return ""
    m = re.search(r'/openings/\d+/([^/]+)$', path)
    return m.group(1).lower() if m else ""
