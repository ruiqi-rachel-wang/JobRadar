"""Keyword parsing, SQL query builders, and display-formatting helpers for job search."""
import re
from functools import lru_cache
from urllib.parse import unquote, urlparse

from .utils import (
    _CANADA_PROVINCES,
    _CITY_DEFAULTS,
    _COUNTRY_ALIASES,
    _COUNTRY_CODES,
    _US_STATE_NAMES,
    _US_STATES,
    norm,
    normalize_location,
)

# ── Display constants ─────────────────────────────────────────────────────────

_DISPLAY_CITIES = (
    "New York", "Hong Kong", "San Francisco", "Los Angeles", "Bengaluru",
    "Hyderabad", "Mumbai", "London", "Singapore", "Tokyo", "Paris",
    "Dublin", "Toronto", "Chicago", "Dallas", "Warsaw", "Salt Lake City",
    "The Hague", "Richardson", "Albany", "Birmingham", "Stockholm", "Sydney",
    "Shenzhen",
)

# ── Keyword aliases ───────────────────────────────────────────────────────────

JOB_DATE_ORDER_SQL = (
    "COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC, first_seen DESC"
)

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
    "data": set(),
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
    intent = role_intent(groups)
    if intent:
        clause, params = _role_where("title", intent)
        return [clause], params
    clauses, params = [], []
    for group in groups:
        bits = []
        for term in group:
            title_bits, title_params = _keyword_term_clauses("LOWER(title)", term)
            company_bits, company_params = _keyword_term_clauses("LOWER(company)", term)
            keyword_bits, keyword_params = _keyword_term_clauses("LOWER(keywords)", term)
            bits.extend(title_bits + company_bits + keyword_bits)
            params.extend(title_params + company_params + keyword_params)
        clauses.append("(" + " OR ".join(bits) + ")")
    return clauses, params

_ROLE_INTENTS = [
    {
        "name": "software_engineering",
        "buckets": [{"software", "swe"}, {"engineer", "engineering", "developer", "development", "sde"}],
        "patterns": [
            "%software engineer%", "%software developer%", "%software development engineer%",
            "%software development%", "%swe%", "%sde%", "%backend engineer%",
            "%backend developer%", "%back end engineer%", "%back end developer%",
            "%frontend engineer%", "%frontend developer%", "%front end engineer%",
            "%front end developer%", "%full stack engineer%", "%full stack developer%",
            "%fullstack engineer%", "%fullstack developer%", "%platform engineer%",
            "%platform developer%", "%infrastructure engineer%",
            "%product engineer%", "%application engineer%", "%systems engineer%",
            "%application developer%", "%web developer%",
            "%data engineer%", "%machine learning engineer%", "%ml engineer%",
            "%ai engineer%", "%devops engineer%", "%site reliability engineer%",
            "%reliability engineer%", "%security engineer%",
        ],
    },
    {
        "name": "data_science",
        "buckets": [{"data"}, {"scientist", "science", "scient", "ml", "machine"}],
        "patterns": ["%data scientist%", "%machine learning scientist%", "%ml scientist%", "%applied scientist%", "%research scientist%", "%ai scientist%"],
    },
    {
        "name": "data_analytics",
        "buckets": [{"data", "business"}, {"analyst", "analysis", "analytics", "analy"}],
        "patterns": ["%data analyst%", "%business analyst%", "%analytics analyst%", "%product analyst%", "%bi analyst%", "%business intelligence%", "%data analytics%"],
    },
    {
        "name": "data_engineering",
        "buckets": [{"data"}, {"engineer", "engineering", "developer", "development"}],
        "patterns": ["%data engineer%", "%analytics engineer%", "%etl engineer%", "%bi engineer%", "%data platform engineer%", "%data infrastructure engineer%"],
    },
    {
        "name": "product_management",
        "buckets": [{"product"}, {"manager", "management", "mgr", "owner", "lead"}],
        "patterns": ["%product manager%", "%product management%", "%product owner%", "%technical product manager%", "%group product manager%", "%product lead%", "%principal product manager%"],
    },
    {
        "name": "program_project_management",
        "buckets": [{"program", "project"}, {"manager", "management", "mgr", "lead"}],
        "patterns": ["%program manager%", "%project manager%", "%technical program manager%", "%technical project manager%", "%tpm%", "%scrum master%", "%delivery manager%"],
    },
    {
        "name": "design",
        "buckets": [{"design", "designer", "ux", "ui"}],
        "patterns": ["%product designer%", "%ux designer%", "%ui designer%", "%visual designer%", "%brand designer%", "%design lead%", "%user experience%", "%user interface%"],
    },
    {
        "name": "security",
        "buckets": [{"security", "cybersecurity", "cyber"}],
        "patterns": ["%security engineer%", "%cybersecurity%", "%cyber security%", "%security analyst%", "%soc analyst%", "%appsec%", "%application security%", "%information security%", "%security architect%"],
    },
    {
        "name": "devops_cloud",
        "buckets": [{"devops", "cloud", "infrastructure", "platform", "sre"}],
        "patterns": ["%devops engineer%", "%cloud engineer%", "%infrastructure engineer%", "%platform engineer%", "%site reliability engineer%", "%sre%", "%systems engineer%", "%release engineer%"],
    },
    {
        "name": "sales",
        "buckets": [{"sales", "account"}],
        "patterns": ["%sales%", "%account executive%", "%account manager%", "%business development%", "%solution consultant%", "%solutions consultant%", "%sales engineer%", "%revenue%"],
    },
    {
        "name": "marketing",
        "buckets": [{"marketing", "market", "growth"}],
        "patterns": ["%marketing%", "%growth%", "%demand generation%", "%go-to-market%", "%gtm%", "%content strategist%", "%campaign manager%"],
    },
    {
        "name": "recruiting",
        "buckets": [{"recruiter", "recruiting", "talent"}],
        "patterns": ["%recruiter%", "%recruiting%", "%talent acquisition%", "%sourcer%", "%people partner%"],
    },
    {
        "name": "customer_success",
        "buckets": [{"customer", "client", "cx"}, {"success", "support", "solutions"}],
        "patterns": ["%customer success%", "%client success%", "%customer support%", "%solutions engineer%", "%solution engineer%", "%implementation consultant%", "%technical support%"],
    },
    {
        "name": "finance_accounting",
        "buckets": [{"finance", "financial", "accounting", "accountant", "audit", "auditor", "tax", "treasury"}],
        "patterns": ["%financial analyst%", "%finance analyst%", "%accountant%", "%accounting%", "%auditor%", "%audit %", "%tax analyst%", "%tax specialist%", "%treasury%", "%controller%", "%bookkeeper%", "%payroll%", "%accounts payable%", "%accounts receivable%", "%fp&a%"],
    },
    {
        "name": "human_resources",
        "buckets": [{"hr", "human", "people", "personnel"}],
        "patterns": ["%human resources%", "%hr business partner%", "%hr manager%", "%people partner%", "%people operations%", "%employee relations%", "%compensation%", "%benefits specialist%", "%personnel%", "%talent management%"],
    },
    {
        "name": "operations_supply_chain",
        "buckets": [{"operation", "operations", "ops", "logistics", "supply", "procurement", "warehouse"}],
        "patterns": ["%operations%", "%business operations%", "%strategy and operations%", "%logistics%", "%supply chain%", "%procurement%", "%buyer%", "%purchasing%", "%inventory%", "%warehouse%", "%fulfillment%", "%distribution%"],
    },
    {
        "name": "quality_assurance",
        "buckets": [{"qa", "quality", "test", "testing", "tester"}],
        "patterns": ["%quality assurance%", "%quality engineer%", "%qa engineer%", "%test engineer%", "%software tester%", "%automation engineer%", "%sdet%", "%quality analyst%", "%validation engineer%", "%quality control%"],
    },
    {
        "name": "legal_compliance",
        "buckets": [{"legal", "law", "lawyer", "attorney", "counsel", "compliance", "paralegal"}],
        "patterns": ["%legal%", "%attorney%", "%lawyer%", "%counsel%", "%paralegal%", "%compliance%", "%contracts manager%", "%contract specialist%", "%privacy officer%", "%regulatory affairs%"],
    },
    {
        "name": "healthcare_clinical",
        "buckets": [{"healthcare", "health", "clinical", "medical", "nurse", "nursing", "physician", "pharmacy"}],
        "patterns": ["%registered nurse%", "%nurse practitioner%", "%nursing%", "%physician%", "%medical assistant%", "%medical director%", "%clinical%", "%pharmacist%", "%pharmacy%", "%therapist%", "%healthcare%", "%patient care%", "%care coordinator%"],
    },
    {
        "name": "consulting_strategy",
        "buckets": [{"consulting", "consultant", "strategy", "advisory", "advisor"}],
        "patterns": ["%consultant%", "%consulting%", "%strategy analyst%", "%strategy manager%", "%strategic initiatives%", "%business advisor%", "%advisory%", "%management consulting%"],
    },
    {
        "name": "research",
        "buckets": [{"research", "researcher"}],
        "patterns": ["%research scientist%", "%researcher%", "%research analyst%", "%research associate%", "%research engineer%", "%user researcher%", "%ux research%", "%clinical research%", "%laboratory%", "%lab technician%"],
    },
    {
        "name": "education_training",
        "buckets": [{"education", "teacher", "teaching", "instructor", "training", "trainer"}],
        "patterns": ["%teacher%", "%instructor%", "%educator%", "%professor%", "%lecturer%", "%curriculum%", "%instructional designer%", "%learning specialist%", "%training specialist%", "%trainer%", "%academic advisor%"],
    },
    {
        "name": "content_communications",
        "buckets": [{"content", "writer", "writing", "communications", "communication", "editor", "copywriter"}],
        "patterns": ["%content writer%", "%content strategist%", "%technical writer%", "%copywriter%", "%editor%", "%communications%", "%public relations%", "%pr manager%", "%social media%", "%documentation%"],
    },
    {
        "name": "administrative_office",
        "buckets": [{"admin", "administrative", "administrator", "office", "secretary"}],
        "patterns": ["%administrative assistant%", "%administrative coordinator%", "%office manager%", "%office administrator%", "%executive assistant%", "%secretary%", "%receptionist%", "%front desk%", "%clerical%"],
    },
]

def role_intent(groups):
    for intent in _ROLE_INTENTS:
        if _groups_have_buckets(groups, intent["buckets"]):
            return intent
    return None

def role_intent_where(groups, field="title"):
    intent = role_intent(groups)
    if not intent:
        return "", []
    return _role_where(field, intent)

def _role_where(field, intent):
    clauses, params = [], []
    for pattern in intent["patterns"]:
        term = pattern.strip("%").lower()
        if _is_standalone_role_abbreviation(pattern, term):
            clauses.append(f"{_sql_word_boundary_field(field)} LIKE ?")
            params.append(f"% {term} %")
        else:
            clauses.append(f"LOWER({field}) LIKE ?")
            params.append(pattern)
    return "(" + " OR ".join(clauses) + ")", params


def _sql_word_boundary_field(field):
    """Return portable SQL that turns common title punctuation into spaces."""
    expression = f"LOWER({field})"
    for delimiter in ("-", "/", "_", "(", ")", ",", ".", ":", ";", "+", "|"):
        escaped = delimiter.replace("'", "''")
        expression = f"REPLACE({expression}, '{escaped}', ' ')"
    return f"(' ' || {expression} || ' ')"


def _is_standalone_role_abbreviation(pattern, term):
    return pattern == f"%{term}%" and term.isalnum() and len(term) <= 3

def _groups_have_buckets(groups, buckets):
    term_sets = [set(group) for group in groups]
    return all(any(bucket & terms for terms in term_sets) for bucket in buckets)

def keyword_matches(title, company, keywords, groups):
    """Python equivalent of keyword_where, used by the non-DB fallback."""
    intent = role_intent(groups)
    title_haystack = (title or "").lower()
    if intent:
        return any(
            _role_pattern_matches(title_haystack, pattern)
            for pattern in intent["patterns"]
        )

    keyword_text = (
        keywords if isinstance(keywords, str)
        else " ".join(str(value) for value in (keywords or []))
    )
    haystack = f"{title or ''} {company or ''} {keyword_text}".lower()
    return all(any(_term_matches_text(haystack, term) for term in group) for group in groups)


def _role_pattern_matches(title, pattern):
    term = pattern.strip("%").lower()
    if _is_standalone_role_abbreviation(pattern, term):
        return _term_matches_text(title, term, exact=True)
    return term in title


def _term_matches_text(text, term, exact=False):
    if exact or (len(term) <= 2 and term.isalnum()):
        return re.search(rf"(?:^|[^a-z0-9]){re.escape(term)}(?:[^a-z0-9]|$)", text) is not None
    return term in text

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

# ── Display title / location helpers ─────────────────────────────────────────

@lru_cache(maxsize=8192)
def display_title(title, url=""):
    slug_title = _title_from_opening_slug(url)
    if slug_title:
        return slug_title
    title = norm(title)
    if "careers.unitedhealthgroup.com/job/" in (url or "").lower():
        title = re.sub(r"\s*(?:\d+\s*)?\|.*$", "", title).strip()
        if not title or re.fullmatch(r"\d+", title):
            title = _unitedhealth_title_from_url(url)
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


def _unitedhealth_title_from_url(url):
    try:
        path = urlparse(url).path.rstrip("/")
    except Exception:
        return ""
    match = re.search(r"/job/[^/]+/([^/]+)/\d+/\d+$", path, re.I)
    if not match:
        return ""
    words = match.group(1).replace("-", " ").split()
    acronyms = {
        "apc", "cdi", "cma", "ehr", "emr", "ft", "hcc", "ii", "iii",
        "iv", "la", "lpn", "lvn", "ma", "np", "pa", "rn", "tx",
    }
    title = " ".join(word.upper() if word.lower() in acronyms else word.title() for word in words)
    return title.replace("Heathcare", "Healthcare")

@lru_cache(maxsize=8192)
def display_location(raw_location, title, url=""):
    loc = _display_location_from_dash_list(raw_location) or normalize_location(raw_location or "")
    title_loc = _location_from_title_noise(title, url)
    url_loc = _location_from_job_url(url, title)
    loc = _qualify_remote_parts(loc, url_loc)
    if not loc:
        loc = _explicit_country_location(title, url_loc)
    if loc in {"Remote", "Multiple Locations"}:
        qualified = _qualify_generic_location(loc, title, url_loc)
        if qualified:
            loc = qualified
    if loc == "Multiple Locations":
        return _mark_location_country(title_loc or url_loc or loc)
    if _bad_display_location(loc, title):
        return _mark_location_country(title_loc or url_loc)
    return _mark_location_country(loc or title_loc or url_loc)

def _qualify_remote_parts(location, url_location=""):
    """Qualify bare Remote segments when every explicit location agrees."""
    if ";" not in (location or ""):
        return location
    parts = [norm(part) for part in location.split(";") if norm(part)]
    countries = {
        _country_from_location(part)
        for part in parts
        if part.lower() != "remote" and _country_from_location(part)
    }
    url_country = _country_from_location(url_location)
    if url_country:
        countries.add(url_country)
    if len(countries) != 1:
        return location
    country = next(iter(countries))
    return "; ".join(f"Remote, {country}" if part.lower() == "remote" else part for part in parts)

def _explicit_country_location(title, url_location=""):
    """Use a country explicitly written in the job title or URL."""
    for candidate in (url_location, title):
        candidate = norm(candidate)
        if not candidate:
            continue
        for alias, canonical in sorted(_COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf'\b{re.escape(alias)}\b', candidate, re.I):
                if re.search(r'\bremote\b', candidate, re.I):
                    return f"Remote, {canonical}"
                return canonical
    return ""

def _qualify_generic_location(location, title, url_location):
    """Attach country only when the title or URL explicitly supplies it."""
    for candidate in (url_location, title):
        candidate = norm(candidate)
        if not candidate:
            continue
        country = _country_from_location(normalize_location(candidate))
        if not country:
            for alias, canonical in sorted(_COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
                if re.search(rf'\b{re.escape(alias)}\b', candidate, re.I):
                    country = canonical
                    break
        if country:
            return f"{location}, {country}"
    return ""

def _mark_location_country(location):
    """Ensure every displayed job location has an honest country marker."""
    location = norm(location)
    if not location:
        return "Country not listed"
    if location.lower().endswith("country not listed"):
        return location
    if ";" in location:
        parts = [norm(part) for part in location.split(";") if norm(part)]
        countries = {_country_from_location(part) for part in parts}
        countries.discard("")
        if len(countries) == 1:
            country = next(iter(countries))
            return "; ".join(
                part if _country_from_location(part) else f"{part}, {country}"
                for part in parts
            )
        return "; ".join(
            _mark_location_country(part)
            for part in parts
        )

    country = _country_from_location(location)
    if country and (
        location.lower() == country.lower()
        or location.lower().endswith(f", {country.lower()}")
        or location.lower().endswith(f" {country.lower()}")
    ):
        return location
    if country:
        return f"{location}, {country}"
    return f"{location}, Country not listed"

def _country_from_location(location):
    location = norm(location)
    lower = location.lower()
    if not location:
        return ""

    countries = sorted(
        set(_COUNTRY_ALIASES.values()) | set(_COUNTRY_CODES.values()),
        key=len,
        reverse=True,
    )
    for country in countries:
        if (
            lower == country.lower()
            or lower.endswith(f", {country.lower()}")
            or lower.endswith(f" {country.lower()}")
        ):
            return country

    codes = re.findall(r"\b[A-Z]{2}\b", location)
    if any(code in _US_STATES for code in codes):
        return "US"
    if any(code in _CANADA_PROVINCES for code in codes):
        return "Canada"
    if codes:
        country = _COUNTRY_CODES.get(codes[-1])
        if country:
            return country

    city = lower.replace("greater ", "").removesuffix(" area").strip()
    for city_name, (_, _, country) in _CITY_DEFAULTS.items():
        if city == city_name or city.startswith(f"{city_name} ") or city.startswith(f"{city_name},"):
            return country
    return ""

def _display_location_from_dash_list(raw_location):
    raw = norm(raw_location or "")
    if ";" in raw:
        return ""
    m = re.search(r'([A-Z][A-Za-z .\'-]+?)\s+-\s+[^-]+?\s+-\s+([A-Z][A-Za-z ]+)', raw)
    if not m:
        return ""
    return normalize_location(f"{m.group(1)}, {m.group(2)}")

def _bad_display_location(loc, title=""):
    loc = norm(loc)
    if not loc:
        return False
    if (
        re.search(r'\b(?:deloitte|llp|inc\.?|corp\.?|corporation)\b', loc, re.I)
        and not _country_from_location(loc)
    ):
        return True
    if re.fullmatch(r'(?:ATCI[-\w]*|(?:REQ|JR|WD|EB|R|S)[-_]?\d[\w-]*|\d{5,})', loc, re.I):
        return True
    title_l = norm(title).lower()
    loc_l = loc.lower()
    has_geo_signal = (
        loc_l in {"remote", "multiple locations", "us", "uk", "india", "canada"}
        or loc_l in {country.lower() for country in _COUNTRY_ALIASES.values()}
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

def _location_from_job_url(url, title=""):
    try:
        path = url.split("?", 1)[0]
    except Exception:
        return ""
    m = re.search(r'/job/([^/]+)/', path)
    if not m:
        # Clinch-style URLs put title + location + UUID in one /jobs/ slug.
        m = re.search(r'/jobs/([^/]+?)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/?$', path, re.I)
        if m:
            slug = unquote(m.group(1)).replace("_", "-").strip("-")
            title_slug = re.sub(r"[^a-z0-9]+", "-", norm(title).lower()).strip("-")
            if title_slug and slug.lower().startswith(title_slug + "-"):
                slug = slug[len(title_slug) + 1:]

            country_slugs = {
                "united-states": "US",
                "united-kingdom": "United Kingdom",
                "australia": "Australia",
                "canada": "Canada",
                "india": "India",
                "mexico": "Mexico",
            }
            for country_slug, country in country_slugs.items():
                if slug.lower() == country_slug:
                    return country
                suffix = "-" + country_slug
                if not slug.lower().endswith(suffix):
                    continue
                place = slug[:-len(suffix)].strip("-")
                if country == "US":
                    for state_name, state_code in sorted(
                        _US_STATE_NAMES.items(), key=lambda item: len(item[0]), reverse=True
                    ):
                        state_slug = state_name.replace(" ", "-")
                        if place.lower().endswith("-" + state_slug):
                            city = place[:-(len(state_slug) + 1)].replace("-", " ").title()
                            return normalize_location(f"{city}, {state_code}, US")
                place = place.replace("-", " ").title()
                return normalize_location(f"{place}, {country}")
            return normalize_location(slug.replace("-", " ").title())
    if not m:
        return ""
    slug = unquote(m.group(1)).replace("_", "-")
    delimited_parts = [
        p.replace("-", " ").strip()
        for p in re.split(r'-{2,}', slug.strip("-"))
        if p.replace("-", " ").strip()
    ]
    if len(delimited_parts) >= 3 and delimited_parts[0].lower() in {"usa", "us", "united states"}:
        state = _US_STATE_NAMES.get(delimited_parts[1].lower()) or delimited_parts[1].upper()
        if state in _US_STATES:
            return normalize_location(f"{delimited_parts[2]}, {state}, US")
    if len(delimited_parts) >= 2 and delimited_parts[0].upper() in _US_STATES:
        city = re.sub(r'\s+\d{5}(?:-\d{4})?$', '', delimited_parts[1]).strip()
        return normalize_location(f"{city}, {delimited_parts[0].upper()}, US")
    if len(delimited_parts) >= 2 and delimited_parts[1].upper() in _US_STATES:
        return normalize_location(f"{delimited_parts[0]}, {delimited_parts[1].upper()}, US")
    raw = norm(slug.replace("-", " ")).strip()
    raw = re.sub(r'^(?:usa|us|united states)\s+', '', raw, flags=re.I).strip()
    if not raw:
        return ""
    remote_region = re.match(r'^remote\s+(.+)$', raw, re.I)
    if remote_region:
        region = re.sub(r'\s+us$', '', remote_region.group(1), flags=re.I).strip()
        if region.lower() in _US_STATE_NAMES:
            return "Remote, US"
        country = _COUNTRY_ALIASES.get(region.lower()) or _COUNTRY_CODES.get(region.upper())
        if country:
            return f"Remote, {country}"
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
