import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

def make_id(company, url, title):
    return hashlib.sha256(f"{company}|{url}|{title}".encode()).hexdigest()[:20]

def norm(s):
    return " ".join((s or "").split())

_US_STATES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC',
}

_US_STATE_NAMES = {
    'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR','california':'CA',
    'colorado':'CO','connecticut':'CT','delaware':'DE','florida':'FL','georgia':'GA',
    'hawaii':'HI','idaho':'ID','illinois':'IL','indiana':'IN','iowa':'IA','kansas':'KS',
    'kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD','massachusetts':'MA',
    'michigan':'MI','minnesota':'MN','mississippi':'MS','missouri':'MO','montana':'MT',
    'nebraska':'NE','nevada':'NV','new hampshire':'NH','new jersey':'NJ','new mexico':'NM',
    'new york':'NY','north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK',
    'oregon':'OR','pennsylvania':'PA','rhode island':'RI','south carolina':'SC',
    'south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT','vermont':'VT',
    'virginia':'VA','washington':'WA','west virginia':'WV','wisconsin':'WI','wyoming':'WY',
    'district of columbia':'DC',
}

_CANADA_PROVINCES = {
    'AB','BC','MB','NB','NL','NS','NT','NU','ON','PE','QC','SK','YT',
}

_CANADA_PROVINCE_NAMES = {
    'alberta':'AB','british columbia':'BC','manitoba':'MB','new brunswick':'NB',
    'newfoundland and labrador':'NL','nova scotia':'NS','northwest territories':'NT',
    'nunavut':'NU','ontario':'ON','prince edward island':'PE','quebec':'QC',
    'saskatchewan':'SK','yukon':'YT',
}

_COUNTRY_ALIASES = {
    'united states of america':'US','united states':'US','usa':'US','u.s.a.':'US','u.s.':'US','us':'US',
    'united kingdom':'UK','great britain':'UK','england':'UK','scotland':'UK','wales':'UK','uk':'UK',
    'canada':'Canada','singapore':'Singapore','switzerland':'Switzerland','germany':'Germany',
    'france':'France','netherlands':'Netherlands','ireland':'Ireland','india':'India','japan':'Japan',
    'australia':'Australia','new zealand':'New Zealand','china':'China','hong kong':'Hong Kong',
    'south korea':'South Korea','korea':'South Korea','taiwan':'Taiwan','mexico':'Mexico','brazil':'Brazil',
    'spain':'Spain','italy':'Italy','poland':'Poland','sweden':'Sweden','denmark':'Denmark','norway':'Norway',
    'finland':'Finland','belgium':'Belgium','austria':'Austria','israel':'Israel','uae':'UAE',
    'united arab emirates':'UAE','saudi arabia':'Saudi Arabia',
}

_COUNTRY_CODES = {
    'AU':'Australia','AT':'Austria','BE':'Belgium','BR':'Brazil','CA':'Canada','CH':'Switzerland',
    'CN':'China','DE':'Germany','DK':'Denmark','ES':'Spain','FI':'Finland','FR':'France',
    'GB':'UK','HK':'Hong Kong','IE':'Ireland','IL':'Israel','IN':'India','IT':'Italy',
    'JP':'Japan','KR':'South Korea','MX':'Mexico','NL':'Netherlands','NO':'Norway',
    'NZ':'New Zealand','PL':'Poland','SE':'Sweden','SG':'Singapore','TW':'Taiwan',
    'UK':'UK','US':'US',
}

_CITY_DEFAULTS = {
    'atlanta': ('Atlanta','GA','US'), 'austin': ('Austin','TX','US'), 'boston': ('Boston','MA','US'),
    'chicago': ('Chicago','IL','US'), 'dallas': ('Dallas','TX','US'), 'denver': ('Denver','CO','US'),
    'detroit': ('Detroit','MI','US'), 'houston': ('Houston','TX','US'), 'los angeles': ('Los Angeles','CA','US'),
    'miami': ('Miami','FL','US'), 'nashville': ('Nashville','TN','US'), 'new york': ('New York','NY','US'),
    'nyc': ('New York','NY','US'), 'palo alto': ('Palo Alto','CA','US'), 'philadelphia': ('Philadelphia','PA','US'),
    'phoenix': ('Phoenix','AZ','US'), 'portland': ('Portland','OR','US'), 'raleigh': ('Raleigh','NC','US'),
    'redmond': ('Redmond','WA','US'), 'san diego': ('San Diego','CA','US'), 'san francisco': ('San Francisco','CA','US'),
    'san jose': ('San Jose','CA','US'), 'santa clara': ('Santa Clara','CA','US'), 'seattle': ('Seattle','WA','US'),
    'washington': ('Washington','DC','US'), 'washington dc': ('Washington','DC','US'),
    'london': ('London','','UK'), 'toronto': ('Toronto','ON','Canada'), 'vancouver': ('Vancouver','BC','Canada'),
    'montreal': ('Montreal','QC','Canada'), 'singapore': ('Singapore','','Singapore'),
    'pune': ('Pune','','India'), 'pune city': ('Pune','','India'), 'bengaluru': ('Bengaluru','','India'),
    'bangalore': ('Bengaluru','','India'), 'hyderabad': ('Hyderabad','','India'), 'mumbai': ('Mumbai','','India'),
    'gurugram': ('Gurugram','','India'), 'gurgaon': ('Gurugram','','India'), 'chennai': ('Chennai','','India'),
}

_LOCATION_NOISE = re.compile(
    r'\b(posted|posting date|date posted|apply|save job|view job|job id|requisition|req\d+)\b',
    re.I,
)

_JOB_TITLE_LOCATION_NOISE = re.compile(
    r'\b(software|engineer|developer|architect|manager|director|analyst|'
    r'specialist|consultant|product|program|project|pricing|incentives|'
    r'membership|consumer|senior|staff|principal|lead|intern)\b',
    re.I,
)

def _title_case_city(s):
    return " ".join(w if w.isupper() else w.capitalize() for w in (s or "").split())

def _country_code(raw):
    return _COUNTRY_ALIASES.get((raw or "").strip().lower())

def normalize_location(loc):
    if not loc:
        return ''
    loc = ' '.join(loc.split())
    loc = re.sub(r'^(?:location|office|city|work location|job location)s?[:\s-]+', '', loc, flags=re.I).strip()
    loc = re.sub(r'\s*\([^)]*(?:hybrid|onsite|on-site|remote)[^)]*\)\s*', ' ', loc, flags=re.I).strip()
    loc = re.sub(r'\s*,?\s*(?:hybrid|onsite|on-site)\s*$', '', loc, flags=re.I).strip()
    loc = loc.replace('United States - Remote', 'Remote, US')
    loc = re.sub(r'\s*[•|│]\s*', ', ', loc)
    loc = re.sub(r'\s*/\s*', ', ', loc)
    loc = re.sub(r'\s*,\s*', ', ', loc).strip(' ,-')
    if ";" in loc:
        normalized_parts = []
        seen_parts = set()
        for part in [p.strip() for p in loc.split(";") if p.strip()]:
            cleaned = normalize_location(part)
            if cleaned and cleaned.lower() not in seen_parts:
                normalized_parts.append(cleaned)
                seen_parts.add(cleaned.lower())
        if len(normalized_parts) > 1:
            normalized_parts = [
                p for p in normalized_parts
                if "," in p or not any(other != p and other.endswith(f", {p}") for other in normalized_parts)
            ]
        return "; ".join(normalized_parts)
    lower = loc.lower()

    if not loc:
        return ''

    # Reject title fragments that generic HTML/Playwright extractors sometimes
    # mistake for locations, e.g. "Software Engineer, Consumer Pricing".
    first_piece = lower.split(",", 1)[0].strip()
    last_piece = lower.rsplit(",", 1)[-1].strip()
    has_geo_signal = (
        first_piece in _CITY_DEFAULTS
        or last_piece in _COUNTRY_ALIASES
        or last_piece in _US_STATE_NAMES
        or bool(re.search(r'\b[A-Z]{2}\b', loc))
        or lower in ('remote', 'multiple locations', 'various locations')
    )
    if _JOB_TITLE_LOCATION_NOISE.search(loc) and not has_geo_signal:
        return ''

    # "US FL Orlando" / "US-FL-Orlando" / "USA, FL, Orlando"
    m_us_state_city = re.match(r'^(?:US|USA|United States)\s*[-,\s]\s*([A-Z]{2})\s*[-,\s]\s*(.+)$', loc, re.I)
    if m_us_state_city and m_us_state_city.group(1).upper() in _US_STATES:
        city = m_us_state_city.group(2).strip()
        state = m_us_state_city.group(1).upper()
        if _country_code(city):
            return f"{state}, US"
        return f"{_title_case_city(city)}, {state}, US"

    # "Tampa Florida, US"
    m_city_state_name = re.match(r'^(.+?)\s+([A-Za-z ]+),\s*(US|USA|United States)$', loc, re.I)
    if m_city_state_name:
        state = _US_STATE_NAMES.get(m_city_state_name.group(2).strip().lower())
        if state:
            return normalize_location(f"{m_city_state_name.group(1)}, {state}, US")

    m_city_state_country = re.match(r'^(.+?)\s+([A-Z]{2})\s+(US|USA|United States)$', loc, re.I)
    if m_city_state_country and m_city_state_country.group(2).upper() in _US_STATES:
        return f"{_title_case_city(m_city_state_country.group(1))}, {m_city_state_country.group(2).upper()}, US"

    # Workday sometimes emits "Hyderabad TS IN 26" (city region country postal).
    m_city_region_country_zip = re.match(r'^(.+?)\s+([A-Z]{2})\s+([A-Z]{2})\s+\d{2,6}$', loc)
    if m_city_region_country_zip:
        country = _COUNTRY_CODES.get(m_city_region_country_zip.group(3))
        if country:
            city = m_city_region_country_zip.group(1)
            if country == "US" and m_city_region_country_zip.group(2) in _US_STATES:
                return f"{_title_case_city(city)}, {m_city_region_country_zip.group(2)}, US"
            return f"{_title_case_city(city)}, {country}"

    # "Pune City,India Software & Product Development" -> "Pune City, India"
    for country_name in sorted(_COUNTRY_ALIASES, key=len, reverse=True):
        country = _COUNTRY_ALIASES[country_name]
        m_country_tail = re.match(rf'^(.+?),\s*{re.escape(country_name)}\b\s+(.+)$', lower, re.I)
        if m_country_tail:
            before = loc[:m_country_tail.end(1)].strip(' ,')
            return normalize_location(f"{before}, {country}")

    m_code_city = re.match(r'^([A-Z]{2})\s*-\s*(.+)$', loc)
    if m_code_city:
        code = m_code_city.group(1)
        city = m_code_city.group(2).strip()
        # US state codes take priority over country codes (IL=Illinois, not Israel)
        if code in _US_STATES:
            return normalize_location(f"{city}, {code}, US")
        country = _COUNTRY_CODES.get(code)
        if country:
            return normalize_location(f"{city}, {country}")

    if re.fullmatch(r'\d+\s+locations?', lower) or lower in ('multiple locations', 'various locations'):
        return 'Multiple Locations'

    if ' or remote' in lower:
        first = re.split(r'\s+or\s+remote\b', loc, flags=re.I)[0].strip(' ,-/')
        base = normalize_location(first)
        return f"{base}; Remote" if base else 'Remote'

    # Remote variants
    if lower in ('remote','anywhere','worldwide','global','distributed',
                 'work from home','wfh','remote / anywhere','remote-first'):
        return 'Remote'
    m_remote = re.match(r'remote\s*(?:-|,|\(|/)?\s*([^)]+)?\)?$', loc, re.I)
    if m_remote:
        country = _country_code(m_remote.group(1) or "")
        return f"Remote, {country}" if country else 'Remote'

    area = lower.replace('greater ', '').removesuffix(' area').strip()
    if area in _CITY_DEFAULTS:
        city, state, country = _CITY_DEFAULTS[area]
        return ", ".join(x for x in (city, state, country) if x)

    # Strip US country suffix and re-add normalized ", US"
    m = re.search(r',?\s*(united states of america|united states|usa|u\.s\.a\.|u\.s\.)\s*$', lower)
    if m:
        base = loc[:m.start()].strip().rstrip(',').strip()
        return normalize_location(base + ', US') if base else 'US'

    parts = [p.strip() for p in loc.split(',') if p.strip()]
    if len(parts) >= 3 and re.fullmatch(r'\d{4,6}(?:-\d{4})?', parts[-1]):
        parts = parts[:-1]
    if len(parts) >= 3:
        city, region, country_raw = parts[0], parts[1], parts[-1]
        country = _country_code(country_raw) or country_raw
        state = _US_STATE_NAMES.get(region.lower(), region.upper() if region.upper() in _US_STATES else region)
        if country == "Canada":
            state = _CANADA_PROVINCE_NAMES.get(region.lower(), region.upper() if region.upper() in _CANADA_PROVINCES else region)
        return ", ".join(x for x in (_title_case_city(city), state, country) if x)

    if len(parts) == 2:
        first, second = parts
        first_country = _country_code(first)
        if first_country:
            if second.lower() == first_country.lower():
                return first_country
            return ", ".join(x for x in (_title_case_city(second), first_country) if x)
        country = _country_code(second)
        if country:
            if first.lower() == country.lower():
                return country
            city_key = first.lower()
            if city_key in _CITY_DEFAULTS and country == _CITY_DEFAULTS[city_key][2]:
                city, state, _ = _CITY_DEFAULTS[city_key]
                return ", ".join(x for x in (city, state, country) if x)
            return ", ".join(x for x in (_title_case_city(first), country) if x)
        state = _US_STATE_NAMES.get(second.lower()) or (second.upper() if second.upper() in _US_STATES else None)
        if state:
            return f"{_title_case_city(first)}, {state}, US"
        province = _CANADA_PROVINCE_NAMES.get(second.lower()) or (second.upper() if second.upper() in _CANADA_PROVINCES else None)
        if province:
            return f"{_title_case_city(first)}, {province}, Canada"
        if first.lower() in _CITY_DEFAULTS and re.search(r'\b(campus|office|center|centre|sez|tower|building|comm(er)?zone|park)\b', second, re.I):
            city, state, country = _CITY_DEFAULTS[first.lower()]
            return ", ".join(x for x in (city, state, country) if x)

    m2 = re.search(r'^(.*?)[,\s]+([A-Z]{2})\s*$', loc)
    if m2 and m2.group(2) in _US_STATES:
        return f"{_title_case_city(m2.group(1).strip())}, {m2.group(2)}, US"

    if lower in _CITY_DEFAULTS:
        city, state, country = _CITY_DEFAULTS[lower]
        return ", ".join(x for x in (city, state, country) if x)

    country = _country_code(loc)
    if country:
        return country

    return loc

def extract_location_from_text(text):
    """Best-effort location extraction from job-card text."""
    if not text:
        return ''
    compact = " ".join(str(text).split())

    label = re.search(
        r'\b(?:location|office|city|work location|job location)s?[:\s-]+'
        r'([^|•\n;]+?)(?=\s+(?:posted|posting date|date posted|job id|requisition|apply)\b|$)',
        compact,
        re.I,
    )
    if label:
        candidate = normalize_location(label.group(1).strip(" ,-"))
        if candidate and not _LOCATION_NOISE.search(candidate):
            return candidate

    parts = [p.strip() for p in re.split(r'\s*[|│•]\s*', compact) if p.strip()]
    for part in parts:
        if _LOCATION_NOISE.search(part):
            continue
        normalized = normalize_location(part)
        if normalized != part or normalized in ("Remote", "Multiple Locations"):
            return normalized

    # Require 2-letter state code to avoid matching job title words as city names
    # (e.g. "Sr. Specialist, Securities" → false match without this restriction).
    _UNLIKELY_CITY_RE = re.compile(
        r'^(?:Sr\.?|Jr\.?|Senior|Junior|Manager|Director|Engineer|Analyst|'
        r'Specialist|Associate|Executive|Principal|Lead|VP|President|Officer|'
        r'Consultant|Head|Chief|General|Operations|Order|Management|Product|'
        r'Project|Program|Business|Global|Regional|Production|System|Systems|'
        r'Financial|Legal|Quantitative|Counsel|Support|ALM|NET|BCG)\b',
        re.I,
    )
    city_region = re.search(
        r'\b([A-Z][A-Za-z.-]+(?:\s+[A-Z][A-Za-z.-]+){0,1}),\s*'
        r'([A-Z]{2}|United\s+(?:Kingdom|States(?:\s+of\s+America)?)|[A-Z][a-z]+)\b',
        compact,
    )
    if city_region and not _UNLIKELY_CITY_RE.match(city_region.group(1)):
        candidate = city_region.group(0)
        if len(candidate) < 50:
            return normalize_location(candidate)

    for city in sorted(_CITY_DEFAULTS, key=len, reverse=True):
        if re.search(rf'\b{re.escape(city)}\b', compact, re.I):
            return normalize_location(city)

    return ''

def location_matches(query, loc):
    q = (query or "").strip().lower()
    hay = (loc or "").lower()
    if not q:
        return True
    aliases = {q}
    if q in ("united states", "usa", "u.s.", "u.s.a.", "america"):
        aliases.update([" us", ", us", "us"])
    if q in ("uk", "u.k.", "united kingdom", "england", "scotland", "wales"):
        aliases.update([" uk", ", uk", "united kingdom", "england", "scotland", "wales"])
    return any(a in hay for a in aliases)

def _parse_posted(raw):
    """Parse a date string to ISO YYYY-MM-DD; return '' on failure."""
    if not raw:
        return ''
    raw = str(raw).strip()
    if not raw:
        return ''
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', raw)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    lower = raw.lower()
    today = datetime.now().date()   # local time — matches what the user sees
    if lower in ("today", "posted today"):
        return today.isoformat()
    if lower in ("yesterday", "posted yesterday"):
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r'(\d+)\+?\s+days?\s+ago', lower)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r'(\d+)\+?\s+weeks?\s+ago', lower)
    if m:
        return (today - timedelta(days=int(m.group(1)) * 7)).isoformat()
    m = re.search(r'(\d+)\s+hours?\s+ago', lower)
    if m:
        return today.isoformat()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return ''

def extract_posted_from_text(text):
    """Best-effort date extraction from job-card text; returns YYYY-MM-DD."""
    if not text:
        return ''
    compact = " ".join(str(text).split())
    # Strip deadline/expiry/closing dates so they don't get returned as post dates
    # e.g. "Application deadline: 2026-05-22" or "Apply by: May 22, 2026"
    clean = re.sub(
        r'\b(?:application\s+)?(?:deadline|closing date?|expir\w*|valid through|apply by)[:\s]+[^\|•\n]{3,40}',
        ' ', compact, flags=re.I,
    )
    patterns = [
        (compact, r'\b(?:posted|posting date|date posted|published)[:\s-]+'
         r'(today|yesterday|\d+\s+(?:hours?|days?|weeks?)\s+ago|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b'),
        (compact, r'\b(today|yesterday|\d+\s+(?:hours?|days?|weeks?)\s+ago)\b'),
        (clean,   r'\b(\d{4}-\d{2}-\d{2})\b'),
        (clean,   r'\b([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b'),
    ]
    for src, pattern in patterns:
        m = re.search(pattern, src, re.I)
        if m:
            parsed = _parse_posted(m.group(1))
            if parsed:
                return parsed
    return ''

def extract_posted_from_html(html):
    """Best-effort posting date extraction from a job detail HTML page."""
    if not html:
        return ''
    try:
        soup = BeautifulSoup(html, "lxml")
        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text("", strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            parsed = _posted_from_json(data)
            if parsed:
                return parsed
        for attr in ("datePosted", "article:published_time", "pubdate", "publishdate"):
            node = soup.select_one(f'meta[property="{attr}"], meta[name="{attr}"]')
            if node:
                parsed = _parse_posted(node.get("content") or "")
                if parsed:
                    return parsed
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = str(html)
    return extract_posted_from_text(text)

def _posted_from_json(data):
    """Find a likely posting/start date in an ATS detail JSON payload."""
    preferred = (
        "postedOn", "postedDate", "datePosted", "postingDate", "publishedDate",
        "publishDate", "firstPublished", "first_published", "createdAt",
        "created_at", "startDate", "jobPostingStartDate",
    )
    blocked = ("end", "close", "closing", "deadline", "expire", "expiration")

    def walk(obj, depth=0):
        if depth > 6:
            return ""
        if isinstance(obj, dict):
            for key in preferred:
                if key in obj:
                    parsed = _parse_posted(obj.get(key))
                    if parsed:
                        return parsed
            for key, value in obj.items():
                kl = str(key).lower()
                if any(x in kl for x in blocked):
                    continue
                if any(x in kl for x in ("posted", "posting", "published", "created", "start")):
                    parsed = _parse_posted(value)
                    if parsed:
                        return parsed
            for key, value in obj.items():
                if any(x in str(key).lower() for x in blocked):
                    continue
                parsed = walk(value, depth + 1)
                if parsed:
                    return parsed
        elif isinstance(obj, list):
            for value in obj[:20]:
                parsed = walk(value, depth + 1)
                if parsed:
                    return parsed
        return ""

    return walk(data)

# ── URL helpers ───────────────────────────────────────────────────────────────

def has_include(url, pattern):
    return pattern.lower() in url.lower()

def get_param_title(url, param):
    if not param: return ""
    pname = param.lstrip("&?").rstrip("=")
    qs    = parse_qs(urlparse(url).query)
    if pname in qs:
        return unquote(qs[pname][0]).replace("+", " ").strip()
    marker = param if param.startswith(("&","?")) else f"&{param}"
    idx    = url.lower().find(marker.lower())
    if idx != -1:
        val = url[idx+len(marker):].split("&")[0].split("#")[0]
        return unquote(val).replace("+", " ").replace("-", " ").strip()
    return ""

def make_paged_url(base, pg_cfg, idx):
    param = pg_cfg.get("param", "page")
    step  = pg_cfg.get("step")
    start = int(pg_cfg.get("start", 1))
    val   = start + (idx * int(step)) if step else start + idx
    fi    = base.find("#")
    frag  = base[fi:] if fi != -1 else ""
    base  = base[:fi]  if fi != -1 else base
    parts = list(urlparse(base))
    qs    = parse_qs(parts[4], keep_blank_values=True)
    qs[param] = [str(val)]
    parts[4]  = urlencode(qs, doseq=True)
    return urlunparse(parts) + frag
