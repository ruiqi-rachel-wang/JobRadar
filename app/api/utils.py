import json
import html as html_lib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

try:
    from job_identity import make_job_id
except ModuleNotFoundError:
    from ..job_identity import make_job_id

def make_id(company, url, title, source="", ats_id=""):
    return make_job_id(company, url, title, source=source, ats_id=ats_id)

def norm(s):
    return " ".join((s or "").split())


def normalize_description(value):
    """Return structure-preserving Markdown-ish text from HTML or plain text.

    Raw source content is stored separately by the caller. This function only
    creates the canonical description used for display and enrichment. It
    intentionally preserves headings, paragraphs, and list boundaries instead
    of flattening the entire posting into one line.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Several ATS providers (including Darwinbox) return a complete HTML
    # document encoded as entities. Decode before deciding whether the value
    # is HTML; raw source content remains untouched in description_raw.
    text = html_lib.unescape(text)
    if "<" in text and re.search(r"<[a-zA-Z][^>]*>", text):
        try:
            soup = BeautifulSoup(text, "lxml")
            for node in soup.select("script, style, noscript, template"):
                node.decompose()
            for br in soup.find_all("br"):
                br.replace_with("\n")
            for heading in soup.find_all(re.compile(r"^h[1-6]$")):
                level = int(heading.name[1])
                heading.insert_before("\n\n" + ("#" * level) + " ")
                heading.insert_after("\n\n")
            for item in soup.find_all("li"):
                item.insert_before("\n- ")
                item.insert_after("\n")
            for block in soup.find_all(
                ["p", "div", "section", "article", "ul", "ol", "table", "tr"]
            ):
                block.insert_after("\n")
            text = soup.get_text("", strip=False)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)

    text = html_lib.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    blank = False
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if lines and not blank:
                lines.append("")
            blank = True
            continue
        blank = False
        if line.startswith("- "):
            line = "- " + line[2:].strip()
        lines.append(line)
    normalized = "\n".join(lines).strip()
    normalized = re.sub(r"(?m)(^- .+)\n\n(?=- )", r"\1\n", normalized)
    return normalized[:100_000] or None


def normalize_datetime_utc(value):
    """Preserve a source timestamp as an ISO-8601 UTC string.

    Date-only values are represented at midnight UTC. Relative labels such as
    ``2 days ago`` are deliberately not promoted to an exact timestamp.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) > 10_000_000_000:
            seconds /= 1000.0
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    else:
        raw = str(value).strip()
        if not raw or re.search(r"\b(?:ago|today|yesterday)\b", raw, re.I):
            return None
        if re.fullmatch(r"-?\d{10,16}(?:\.\d+)?", raw):
            seconds = float(raw)
            if abs(seconds) > 10_000_000_000:
                seconds /= 1000.0
            try:
                parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        else:
            parsed = None
        candidate = raw.replace("Z", "+00:00")
        try:
            parsed = parsed or datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in (
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%B %d, %Y",
                "%b %d, %Y",
                "%B %d %Y",
                "%b %d %Y",
                "%d-%B-%Y",
                "%d-%b-%Y",
            ):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.date() > datetime.now(timezone.utc).date():
        return None
    return parsed.isoformat().replace("+00:00", "Z")

def extract_experience(title="", text="", allow_title_fallback=False):
    """Return an experience range only when the posting states numeric years.

    ``allow_title_fallback`` remains in the signature for compatibility with
    older callers, but titles are intentionally never converted into invented
    year requirements.
    """
    title = norm(title)
    text = norm(text)

    def parse_year(value):
        value = float(value)
        return int(value) if value.is_integer() else value

    def fmt_year(value):
        if value is None:
            return ""
        value = float(value)
        return str(int(value)) if value.is_integer() else str(value).rstrip("0").rstrip(".")

    def result(lo, hi, label, source):
        lo = parse_year(lo) if lo is not None else None
        hi = parse_year(hi) if hi is not None else None
        if hi is not None and lo is not None and hi < lo:
            hi = lo
        return {"min": lo, "max": hi, "label": label, "source": source}

    word_years = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8",
        "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
        "thirteen": "13", "fourteen": "14", "fifteen": "15",
        "sixteen": "16", "seventeen": "17", "eighteen": "18",
        "nineteen": "19", "twenty": "20",
    }
    requirement = text.lower().replace("'", "'")
    for word, value in word_years.items():
        requirement = re.sub(rf"\b{word}\b", value, requirement)

    _REQ_SENTENCE_RE = re.compile(
        r'\b(?:experience|require[sd]?|must have|qualif\w+|proficien\w+|background|'
        r'expertise|knowledge|skill\w*|familiar\w*|working with|hands.on)\b',
        re.I,
    )
    _COMPANY_HISTORY_RE = re.compile(
        r'\b(?:founded|established|headquarter\w*|pioneer\w*)\b',
        re.I,
    )

    def _requirement_sentences(section):
        """Return only sentences that look like job requirements, not company history."""
        sentences = re.split(r'(?<=[.!?;])\s+', section)
        # If fewer than 3 sentences, just return the whole section unchanged
        if len(sentences) < 3:
            return section
        req_sents = []
        for s in sentences:
            if _REQ_SENTENCE_RE.search(s) and not _COMPANY_HISTORY_RE.search(s):
                req_sents.append(s)
        return " ".join(req_sents) if req_sents else section

    def text_matches(section):
        # Filter to requirement-context sentences before pattern matching
        filtered = _requirement_sentences(section)
        year_num = r'(\d{1,2}(?:\.\d)?)'
        range_pattern = re.compile(
            year_num + r'\s*(?:[-–—]\s*|to\s+)' + year_num + r'\s*(?:years?|yrs?)'
            r'(?:\s+[^.;:]{0,100}?\bexperience\b)?'
        )
        minimum_patterns = [
            re.compile(
                year_num + r'\s*\+\s*(?:years?|yrs?)'
                r'(?:\s+[^.;:]{0,100}?\bexperience\b)?'
            ),
            # "more than/at least/minimum X years" — require "experience" within 60 chars
            re.compile(
                r'(?:minimum|min\.?|at least|more than)\s+' + year_num + r'\s*(?:years?|yrs?)'
                r'[\s’\'-]*[^.;:]{0,60}?\bexperience\b'
            ),
            re.compile(
                year_num + r'\s*(?:or more|plus)\s*(?:years?|yrs?)'
                r'(?:\s+[^.;:]{0,100}?\bexperience\b)?'
            ),
            re.compile(
                r'(?<![-\d.])' + year_num + r'\s*(?:years?|yrs?)\s+[^.;:]{0,100}?\bexperience\b'
            ),
        ]
        matches = []
        range_spans = []
        for m in range_pattern.finditer(filtered):
            lo, hi = parse_year(m.group(1)), parse_year(m.group(2))
            if lo > 18 or hi > 18:   # implausible job requirement
                continue
            matches.append(result(lo, hi, f"{fmt_year(lo)}-{fmt_year(hi)} years", "text"))
            range_spans.append(m.span())
        for pattern in minimum_patterns:
            for m in pattern.finditer(filtered):
                if any(start <= m.start() < end for start, end in range_spans):
                    continue
                lo = parse_year(m.group(1))
                if lo > 18:          # implausible job requirement
                    continue
                matches.append(result(lo, None, f"{fmt_year(lo)}+ years", "text"))
        return matches

    baseline = re.split(
        r'\b(?:preferred qualifications?|preferred|desired skills?|nice to have)\b',
        requirement,
        maxsplit=1,
    )[0]
    pref_re = re.compile(r'\b(?:preferred|desired|nice to have|bonus|plus)\b', re.I)
    non_preferred_clauses = [
        clause.strip()
        for clause in re.split(r'[;]|(?<=[.!?])\s+', requirement)
        if clause.strip() and not pref_re.search(clause)
    ]
    required_scope = " ".join(non_preferred_clauses) if non_preferred_clauses else baseline
    required_matches = text_matches(required_scope)
    all_matches = text_matches(requirement)
    if required_matches:
        # Required minimums set the floor. Preferred ranges may widen the ceiling,
        # but should not raise the floor:
        #   "Minimum 1 year required; 3-10 preferred" -> "1-10 years".
        lo_values = [m["min"] for m in required_matches if m.get("min") is not None]
        # When a role requires both an overall minimum and a smaller specialty
        # minimum, the overall qualification floor is the larger number:
        # "7+ years engineering, including 2+ years with AI" -> 7+ years.
        lo = max(lo_values) if lo_values else None
        finite_maxes = [
            m["max"] for m in all_matches
            if m.get("max") is not None and (lo is None or m["max"] >= lo)
        ]
        if lo is not None and finite_maxes:
            hi = max(finite_maxes)
            return result(lo, hi, f"{fmt_year(lo)}-{fmt_year(hi)} years", "text")
        return max(required_matches, key=lambda x: (x["min"] or -1, x["max"] or -1))
    matches = all_matches
    if matches:
        return max(matches, key=lambda x: (x["min"] or -1, x["max"] or -1))

    flight_hours = re.search(
        r'\b(?:logged?|minimum|at least)\s+([\d,]{3,6})'
        r'[^.;:]{0,80}?\bhours?\b',
        requirement,
        re.I,
    )
    if flight_hours:
        hours = flight_hours.group(1)
        return result(None, None, f"{hours} flight hours", "text")

    # A text source with no numeric minimum is still marked as inspected so
    # detail fetchers can cache that work, but the UI must show "Not listed".
    if text:
        return result(None, None, "", "text")

    return {"min": None, "max": None, "label": "", "source": ""}

# ── Skills extraction ─────────────────────────────────────────────────────────

_TECH_SKILLS = [
    # Languages
    "Python","Java","JavaScript","TypeScript","Go","Rust","C++","C#","Swift","Kotlin",
    "Scala","Ruby","PHP","R","MATLAB","Bash","Shell","Perl","Haskell","Elixir",
    # Web / Frontend
    "React","Vue","Angular","Next.js","Svelte","HTML","CSS","Tailwind","GraphQL",
    "REST","WebSockets","Node.js","Express","Django","FastAPI","Flask","Spring",
    "Rails","Laravel","ASP.NET",
    # Data / ML
    "SQL","PostgreSQL","MySQL","SQLite","NoSQL","MongoDB","Redis","Elasticsearch",
    "Kafka","Spark","Hadoop","Airflow","dbt","Snowflake","BigQuery","Redshift",
    "Pandas","NumPy","PyTorch","TensorFlow","Keras","scikit-learn","HuggingFace",
    "LLM","RAG","OpenAI","Langchain","LangChain","MLflow","Databricks",
    # Cloud / DevOps
    "AWS","GCP","Azure","Kubernetes","Docker","Terraform","Ansible","CI/CD",
    "GitHub Actions","Jenkins","Linux","Nginx","Prometheus","Grafana","ArgoCD",
    # Tools / Misc
    "Git","Jira","Figma","Salesforce","Tableau","PowerBI","Excel","Looker",
]
_SKILL_RE = {
    s: re.compile(
        rf'(?<![A-Za-z0-9.#]){re.escape(s)}(?![A-Za-z0-9.#])',
        re.I if s == s.lower() else 0,
    )
    for s in _TECH_SKILLS
}
# Normalise display name (e.g. "langchain" → "LangChain", "next.js" → "Next.js")
_SKILL_CANONICAL = {s.lower(): s for s in _TECH_SKILLS}

_REQ_SECTION_RE = re.compile(
    r'(?:required|requirements?|must.have|qualifications?|you.(?:must|need|have)|'
    r'what.you.(?:bring|need|have)|basic.qualifications?|minimum.qualifications?)'
    r'[:\s]+(.*?)(?=\n\n|\Z)',
    re.I | re.S,
)
_PREF_SECTION_RE = re.compile(
    r'(?:preferred|nice.to.have|bonus|desired|advantageous|plus)'
    r'[:\s]+(.*?)(?=\n\n|\Z)',
    re.I | re.S,
)

def extract_skills(title="", description=""):
    """Return a list of canonical tech skill names found in required sections."""
    # Extract only the required section if a structure is present
    req_text = title
    if description:
        pref_m = _PREF_SECTION_RE.search(description)
        pref_span = pref_m.span() if pref_m else None

        req_m = _REQ_SECTION_RE.search(description)
        if req_m:
            req_body = req_m.group(1)
            # Exclude anything past the preferred section
            if pref_m and pref_m.start() > req_m.start():
                req_body = req_body[:pref_m.start() - req_m.start(0)]
            req_text = title + " " + req_body
        else:
            # No clear structure — use full description but remove preferred section
            req_text = title + " " + description
            if pref_span:
                req_text = req_text[:pref_span[0]]

    found = []
    seen_lower = set()
    for skill in _TECH_SKILLS:
        pattern = _SKILL_RE[skill]
        if pattern.search(req_text):
            key = skill.lower()
            if key not in seen_lower:
                found.append(_SKILL_CANONICAL.get(key, skill))
                seen_lower.add(key)
    return found

_KEYWORD_STOP_WORDS = {
    "about", "across", "after", "also", "and", "are", "around", "because",
    "been", "being", "benefits", "best", "build", "business", "candidate",
    "company", "customer", "customers", "day", "description", "duties",
    "equal", "every", "experience", "full", "great", "have", "help", "here",
    "including", "job", "join", "like", "make", "more", "must", "need",
    "new", "our", "own", "people", "position", "preferred", "provide",
    "qualifications", "required", "requirements", "responsibilities", "role",
    "skills", "team", "teams", "that", "the", "their", "this", "time",
    "using", "what", "when", "where", "will", "with", "work", "working",
    "world", "you", "your",
}

_TITLE_KEYWORD_STOP_WORDS = _KEYWORD_STOP_WORDS | {
    "associate", "contract", "full", "hybrid", "intern", "internship",
    "junior", "lead", "manager", "part", "principal", "remote", "senior",
    "staff", "temporary",
}

def extract_keywords(title="", description="", skills=None, max_keywords=12):
    """Return broad job keywords even when no strict skills are detected."""
    keywords = []
    seen = set()

    def add(value):
        value = norm(value).strip(" -_/.,:;()[]{}")
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        keywords.append(value)

    for skill in skills or []:
        add(skill)
        if len(keywords) >= max_keywords:
            return keywords[:max_keywords]

    title_tokens = [
        t for t in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", title or "")
        if t.lower() not in _TITLE_KEYWORD_STOP_WORDS
    ]
    for token in title_tokens[:6]:
        add(token)
        if len(keywords) >= max_keywords:
            return keywords[:max_keywords]

    text = norm(description or "")
    words = [
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", text)
        if w.lower() not in _KEYWORD_STOP_WORDS and not w.isdigit()
    ]
    for word, _count in Counter(words).most_common(30):
        add(word.title() if word.isupper() else word)
        if len(keywords) >= max_keywords:
            break
    return keywords[:max_keywords]

def experience_match_score(user_years, exp_min, exp_max):
    """Higher is better. Negative means a known requirement is too high."""
    if user_years is None:
        return 0
    try:
        y = float(user_years)
    except (TypeError, ValueError):
        return 0
    if exp_min is None and exp_max is None:
        return 0
    lo = float(exp_min) if exp_min is not None else 0.0
    hi = float(exp_max) if exp_max is not None else None
    if lo > y + 1:
        return -100
    if hi is not None and lo <= y <= hi:
        return 100
    if hi is None and y >= lo:
        gap = y - lo
        if gap <= 3:
            return 90
        if gap <= 7:
            return 60
        if gap <= 12:
            return 35
        return 15
    if hi is not None and y < lo:
        return max(0, 70 - int((lo - y) * 20))
    if hi is not None and y > hi:
        return max(10, 60 - int((y - hi) * 10))
    return 20

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
    'czech republic':'Czech Republic','czechia':'Czech Republic','portugal':'Portugal',
    'argentina':'Argentina','chile':'Chile','colombia':'Colombia','costa rica':'Costa Rica',
    'peru':'Peru','uruguay':'Uruguay','venezuela':'Venezuela','bermuda':'Bermuda',
    'cayman islands':'Cayman Islands','puerto rico':'Puerto Rico',
    'south africa':'South Africa','morocco':'Morocco','egypt':'Egypt','kenya':'Kenya',
    'ghana':'Ghana','nigeria':'Nigeria','uganda':'Uganda','mauritius':'Mauritius',
    'indonesia':'Indonesia','malaysia':'Malaysia','philippines':'Philippines',
    'thailand':'Thailand','vietnam':'Vietnam','pakistan':'Pakistan','bangladesh':'Bangladesh',
    'sri lanka':'Sri Lanka','turkey':'Türkiye','türkiye':'Türkiye',
    'greece':'Greece','hungary':'Hungary','romania':'Romania','bulgaria':'Bulgaria',
    'serbia':'Serbia','croatia':'Croatia','slovakia':'Slovakia','slovenia':'Slovenia',
    'lithuania':'Lithuania','latvia':'Latvia','estonia':'Estonia','ukraine':'Ukraine',
    'luxembourg':'Luxembourg','iceland':'Iceland','qatar':'Qatar','kuwait':'Kuwait',
    'bahrain':'Bahrain','oman':'Oman','jordan':'Jordan','lebanon':'Lebanon',
    'worldwide':'Worldwide',
    'armenia':'Armenia','azerbaijan':'Azerbaijan',
    'kazakhstan':'Kazakhstan','uzbekistan':'Uzbekistan','nepal':'Nepal',
    'cambodia':'Cambodia','laos':'Laos','myanmar':'Myanmar','mongolia':'Mongolia',
    'cyprus':'Cyprus','malta':'Malta','moldova':'Moldova','north macedonia':'North Macedonia',
    'bosnia and herzegovina':'Bosnia and Herzegovina','albania':'Albania',
    'ecuador':'Ecuador','panama':'Panama','guatemala':'Guatemala','honduras':'Honduras',
    'dominican republic':'Dominican Republic','jamaica':'Jamaica','bahamas':'Bahamas',
    'macao':'Macao','macau':'Macao',
}

_COUNTRY_CODES = {
    'AE':'UAE','AR':'Argentina','AU':'Australia','AT':'Austria','BE':'Belgium','BR':'Brazil',
    'CA':'Canada','CH':'Switzerland','CL':'Chile','CN':'China','CO':'Colombia','CZ':'Czech Republic',
    'DE':'Germany','DK':'Denmark','ES':'Spain','FI':'Finland','FR':'France',
    'GB':'UK','HK':'Hong Kong','IE':'Ireland','IL':'Israel','IN':'India','IT':'Italy',
    'ID':'Indonesia','JP':'Japan','KR':'South Korea','MY':'Malaysia','MX':'Mexico',
    'NL':'Netherlands','NO':'Norway','NZ':'New Zealand','PH':'Philippines','PL':'Poland',
    'PT':'Portugal','SE':'Sweden','SG':'Singapore','TH':'Thailand','TW':'Taiwan',
    'UK':'UK','US':'US','VN':'Vietnam','ZA':'South Africa',
    'AM':'Armenia','AZ':'Azerbaijan','GE':'Georgia','RO':'Romania','BG':'Bulgaria',
    'HR':'Croatia','RS':'Serbia','SK':'Slovakia','SI':'Slovenia','LT':'Lithuania',
    'LV':'Latvia','EE':'Estonia','UA':'Ukraine','LU':'Luxembourg','IS':'Iceland',
    'GR':'Greece','HU':'Hungary','TR':'Türkiye','PK':'Pakistan','BD':'Bangladesh',
    'LK':'Sri Lanka','NG':'Nigeria','KE':'Kenya','GH':'Ghana','UG':'Uganda',
    'MA':'Morocco','EG':'Egypt','QA':'Qatar','KW':'Kuwait','BH':'Bahrain',
    'OM':'Oman','JO':'Jordan','LB':'Lebanon','CR':'Costa Rica','PE':'Peru',
    'UY':'Uruguay','VE':'Venezuela','BM':'Bermuda','KY':'Cayman Islands','PR':'Puerto Rico',
}

_CITY_DEFAULTS = {
    'atlanta': ('Atlanta','GA','US'), 'austin': ('Austin','TX','US'), 'boston': ('Boston','MA','US'),
    'chicago': ('Chicago','IL','US'), 'dallas': ('Dallas','TX','US'), 'denver': ('Denver','CO','US'),
    'detroit': ('Detroit','MI','US'), 'houston': ('Houston','TX','US'), 'los angeles': ('Los Angeles','CA','US'),
    'miami': ('Miami','FL','US'), 'nashville': ('Nashville','TN','US'), 'new york': ('New York','NY','US'),
    'nyc': ('New York','NY','US'), 'new york city': ('New York','NY','US'),
    'palo alto': ('Palo Alto','CA','US'), 'philadelphia': ('Philadelphia','PA','US'),
    'phoenix': ('Phoenix','AZ','US'), 'portland': ('Portland','OR','US'), 'raleigh': ('Raleigh','NC','US'),
    'redmond': ('Redmond','WA','US'), 'san diego': ('San Diego','CA','US'), 'san francisco': ('San Francisco','CA','US'),
    'san jose': ('San Jose','CA','US'), 'santa clara': ('Santa Clara','CA','US'), 'seattle': ('Seattle','WA','US'),
    'washington': ('Washington','DC','US'), 'washington dc': ('Washington','DC','US'),
    'washington d.c.': ('Washington','DC','US'), 'washington d.c': ('Washington','DC','US'),
    'london': ('London','','UK'), 'toronto': ('Toronto','ON','Canada'), 'vancouver': ('Vancouver','BC','Canada'),
    'montreal': ('Montreal','QC','Canada'), 'singapore': ('Singapore','','Singapore'),
    'pune': ('Pune','','India'), 'pune city': ('Pune','','India'), 'bengaluru': ('Bengaluru','','India'),
    'bangalore': ('Bengaluru','','India'), 'hyderabad': ('Hyderabad','','India'), 'mumbai': ('Mumbai','','India'),
    'gurugram': ('Gurugram','','India'), 'gurgaon': ('Gurugram','','India'), 'chennai': ('Chennai','','India'),
    'salt lake city': ('Salt Lake City','UT','US'), 'richardson': ('Richardson','TX','US'),
    'albany': ('Albany','NY','US'), 'warsaw': ('Warsaw','','Poland'),
    'the hague': ('The Hague','','Netherlands'), 'stockholm': ('Stockholm','','Sweden'),
    'sydney': ('Sydney','','Australia'), 'shenzhen': ('Shenzhen','','China'),
    'amsterdam': ('Amsterdam','','Netherlands'), 'barcelona': ('Barcelona','','Spain'),
    'beijing': ('Beijing','','China'), 'berlin': ('Berlin','','Germany'),
    'brussels': ('Brussels','','Belgium'), 'dubai': ('Dubai','','UAE'),
    'dublin': ('Dublin','','Ireland'), 'frankfurt': ('Frankfurt','','Germany'),
    'hong kong': ('Hong Kong','','Hong Kong'), 'lisbon': ('Lisbon','','Portugal'),
    'madrid': ('Madrid','','Spain'), 'melbourne': ('Melbourne','','Australia'),
    'mexico city': ('Mexico City','','Mexico'), 'munich': ('Munich','','Germany'),
    'paris': ('Paris','','France'), 'prague': ('Prague','','Czech Republic'),
    'rome': ('Rome','','Italy'), 'sao paulo': ('Sao Paulo','','Brazil'),
    'são paulo': ('São Paulo','','Brazil'),
    'cyberjaya': ('Cyberjaya','','Malaysia'), 'jakarta': ('Jakarta','','Indonesia'),
    'makati': ('Makati','','Philippines'),
    'seoul': ('Seoul','','South Korea'), 'shanghai': ('Shanghai','','China'),
    'taipei': ('Taipei','','Taiwan'), 'tokyo': ('Tokyo','','Japan'),
    'zurich': ('Zurich','','Switzerland'),
    'tbilisi': ('Tbilisi','','Georgia'),
    'bucharest': ('Bucharest','','Romania'), 'budapest': ('Budapest','','Hungary'),
    'milan': ('Milan','','Italy'), 'tel aviv': ('Tel Aviv','','Israel'),
    'redondo beach': ('Redondo Beach','CA','US'), 'mountain view': ('Mountain View','CA','US'),
    'bellevue': ('Bellevue','WA','US'), 'guadalajara': ('Guadalajara','','Mexico'),
    'abu dhabi': ('Abu Dhabi','','UAE'), 'rehovot': ('Rehovot','','Israel'),
    'colombo': ('Colombo','','Sri Lanka'), 'phnom penh': ('Phnom Penh','','Cambodia'),
    'kuala lumpur': ('Kuala Lumpur','','Malaysia'), 'tainan': ('Tainan','','Taiwan'),
    'hsinchu': ('Hsinchu','','Taiwan'), 'yerevan': ('Yerevan','','Armenia'),
}

# A city plus a local region is not enough for the generic two-part parser to
# infer a country safely (for example, "Paris, Texas" must not become France).
# Keep only unambiguous pairs observed in official job feeds here.
_CITY_REGION_COUNTRY_DEFAULTS = {
    ('hyderabad', 'telangana'): ('Hyderabad', 'Telangana', 'India'),
    ('noida', 'uttar pradesh'): ('Noida', 'Uttar Pradesh', 'India'),
}

_ISRAEL_CITY_NAMES = {
    'beer sheva', 'caesarea', 'haifa', 'herzliya', 'kibbutz shefayim',
    'kiryat gat', 'migdal haemek', "migdal ha'emek", 'ness ziona',
    'petah tikva', 'petah-tikva', 'raanana', 'ramat gan', 'rehovot',
    'tel aviv', 'tel aviv yafo', 'tel-aviv', 'tel-aviv-yafo',
    'tzrifin', 'yavne', 'yokneam',
}

_LOCATION_NOISE = re.compile(
    r'\b(posted|posting date|date posted|apply|save job|view job|job id|requisition|req\d+)\b',
    re.I,
)

_JOB_TITLE_LOCATION_NOISE = re.compile(
    r'\b(software|engineer|developer|architect|manager|director|analyst|'
    r'specialist|consultant|associate|banking|wealth|infrastructure|industrials|'
    r'product|program|project|pricing|incentives|'
    r'membership|consumer|senior|staff|principal|lead|intern)\b',
    re.I,
)

def _title_case_city(s):
    return " ".join(w if w.isupper() else w.capitalize() for w in (s or "").split())

def _country_code(raw):
    raw = (raw or "").strip()
    return _COUNTRY_ALIASES.get(raw.lower()) or _COUNTRY_CODES.get(raw.upper())


def _location_separator_is_multi(raw):
    parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
    if len(parts) < 2:
        return False
    if any(re.search(r"\b(?:remote|hybrid|on-?site|in[- ]office)\b", p, re.I) for p in parts):
        return True
    return all("," in p for p in parts)


@lru_cache(maxsize=8192)
def normalize_location(loc):
    if not loc:
        return ''
    loc = ' '.join(loc.split())
    loc = re.sub(r'(?:(?<=,)|^)\s*UNAVAILABLE\s*(?:,|$)', '', loc, flags=re.I).strip(' ,')
    if not loc:
        return ''
    # Workday also advertises country/state/city with full state names.
    # Parse the state before treating a leading USA token as a country-city
    # pair ("USA - Texas - Lubbock" must retain TX).
    us_address = re.fullmatch(r'(?:USA|US|United States(?: of America)?)\s*-\s*([A-Za-z ]+?)\s*-\s*([^;|│/]+)', loc, re.I)
    if us_address:
        state = _US_STATE_NAMES.get(us_address.group(1).strip().lower())
        if state:
            return f"{_title_case_city(us_address.group(2).strip())}, {state}, US"
    remote_state = re.fullmatch(r'Remote,\s*([A-Z]{2}),\s*US', loc, re.I)
    if remote_state and remote_state.group(1).upper() in _US_STATES:
        return f"Remote, {remote_state.group(1).upper()}, US"
    # A slash often separates complete alternatives, not address components.
    # Preserve each alternative before the normal city/state/country parser can
    # mistake a trailing state code such as CA for the country Canada.
    if "/" in loc and _location_separator_is_multi(loc):
        values = []
        seen_values = set()
        for part in re.split(r"\s*/\s*", loc):
            normalized = normalize_location(part)
            if normalized and normalized.lower() not in seen_values:
                values.append(normalized)
                seen_values.add(normalized.lower())
        return "; ".join(values)
    loc = re.sub(r'^(?:location|office|city|work location|job location)s?[:\s-]+', '', loc, flags=re.I).strip()
    loc = re.sub(r'\s*\([^)]*(?:hybrid|onsite|on-site|remote)[^)]*\)\s*', ' ', loc, flags=re.I).strip()
    loc = re.sub(r'\s*,?\s*(?:hybrid|onsite|on-site)\s*$', '', loc, flags=re.I).strip()
    loc = loc.replace('United States - Remote', 'Remote, US')
    loc = re.sub(r'\s*[•|│]\s*', ', ', loc)
    loc = re.sub(r'\s*/\s*', ', ', loc)
    loc = re.sub(r'\s*,\s*', ', ', loc).strip(' ,-')
    # Repair malformed Workday labels such as "Philadelphia (, PA, US; 90)".
    loc = re.sub(r'\s*\(\s*,', ',', loc)
    loc = re.sub(r'[);]\s*\d{1,4}\)?\s*$', '', loc).strip(' ,')
    # Reject raw requisition IDs like "R 104778", "REQ-1234", "WD12345" early
    if re.fullmatch(r'[A-Z]{0,4}[\s-]?\d{4,8}', loc):
        return ''
    # Strip trailing US zip code ("West Milford, NJ 07480" → "West Milford, NJ")
    loc = re.sub(r',?\s*\b\d{5}(?:-\d{4})?\s*$', '', loc).strip(' ,')
    # Strip trailing store/facility numbers like "Braselton, GA 3498" or "City, ST 123"
    loc = re.sub(r',?\s*\b\d{3,6}\s*$', '', loc).strip(' ,')
    if ";" in loc:
        normalized_parts = []
        seen_parts = set()
        for part in [p.strip() for p in loc.split(";") if p.strip()]:
            cleaned = normalize_location(part)
            if cleaned and cleaned.lower() not in seen_parts:
                normalized_parts.append(cleaned)
                seen_parts.add(cleaned.lower())
        if len(normalized_parts) > 1:
            # Workday often repeats the same office as
            # "Bucharest, address; Bucharest, address, Romania". Keep the
            # country-qualified version and discard its unqualified duplicate.
            qualified = []
            for part in normalized_parts:
                part_l = part.lower()
                if any(
                    other != part
                    and (
                        other.lower().startswith(part_l + ",")
                        or part_l.startswith(other.lower() + ",")
                    )
                    for other in normalized_parts
                ):
                    if any(other != part and other.lower().startswith(part_l + ",") for other in normalized_parts):
                        continue
                qualified.append(part)
            normalized_parts = qualified
            normalized_parts = [
                p for p in normalized_parts
                if "," in p or not any(other != p and other.endswith(f", {p}") for other in normalized_parts)
            ]
        return "; ".join(normalized_parts)
    lower = loc.lower()

    if not loc:
        return ''

    if lower in _US_STATE_NAMES:
        return f"{_US_STATE_NAMES[lower]}, US"
    if re.fullmatch(r'[A-Z]{2}', loc) and loc.upper() in _US_STATES:
        return f"{loc.upper()}, US"
    if lower in _CANADA_PROVINCE_NAMES:
        return f"{_CANADA_PROVINCE_NAMES[lower]}, Canada"
    if lower in {"puerto rico", "pr"}:
        return "Puerto Rico, US"

    # ATS requisition IDs and internal tracking IDs are common false positives
    # from Workday/Playwright cards. They should never be shown as locations.
    if re.fullmatch(r'(?:[A-Z]{2,6}-[A-Z]\d[\w-]*|ATCI[-\w]*|(?:REQ|JR|WD|EB|R|S)[-_]?\d[\w-]*|\d{5,})', loc, re.I):
        return ''

    # Reject title fragments that generic HTML/Playwright extractors sometimes
    # mistake for locations, e.g. "Software Engineer, Consumer Pricing".
    first_piece = lower.split(",", 1)[0].strip()
    last_piece = lower.rsplit(",", 1)[-1].strip()
    has_geo_signal = (
        first_piece in _CITY_DEFAULTS
        or last_piece in _COUNTRY_ALIASES
        or last_piece in _US_STATE_NAMES
        or any(code in _US_STATES or code in _COUNTRY_CODES for code in re.findall(r'\b[A-Z]{2}\b', loc))
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

    # "Italy-Milan" / "Germany - Berlin"
    m_country_city = re.match(r'^([A-Za-z .]+?)\s*-\s*(.+)$', loc)
    if m_country_city:
        state_code = m_country_city.group(1).upper()
        city_text = m_country_city.group(2).strip()
        city_key = re.sub(r'\s+', ' ', city_text.replace("-", " ").lower()).strip()
        if state_code == "IL" and city_key in _ISRAEL_CITY_NAMES:
            return normalize_location(f"{_title_case_city(city_text.replace('-', ' '))}, Israel")
        if state_code in _US_STATES:
            return normalize_location(f"{city_text}, {state_code}, US")
        country = _country_code(m_country_city.group(1))
        if country:
            return normalize_location(f"{city_text}, {country}")

    # "New York - United States of America" / "London - United Kingdom".
    # Check the right-hand country before treating a left-hand US state name
    # as the state in a state-city pair.
    m_city_country = re.match(r"^(.+?)\s+-\s+([A-Za-z .]+)$", loc)
    if m_city_country:
        country = _country_code(m_city_country.group(2))
        if country:
            return normalize_location(f"{m_city_country.group(1)}, {country}")

    # "Massachusetts - Boston" / "California - Pleasanton"
    m_state_city = re.match(r'^([A-Za-z ]+?)\s*-\s*(.+)$', loc)
    if m_state_city:
        state = _US_STATE_NAMES.get(m_state_city.group(1).strip().lower())
        if state:
            return normalize_location(f"{m_state_city.group(2)}, {state}, US")

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
    m_remote = re.match(r'remote\s*(?:-|,|\(|/|\s)?\s*([^)]+)?\)?$', loc, re.I)
    if m_remote:
        country = _country_code(m_remote.group(1) or "")
        return f"Remote, {country}" if country else 'Remote'

    area = lower.replace('greater ', '').removesuffix(' area').strip()
    if area in _CITY_DEFAULTS:
        city, state, country = _CITY_DEFAULTS[area]
        if city.lower() == country.lower():
            return country
        return ", ".join(x for x in (city, state, country) if x)

    # Corporate Workday locations sometimes append a street/building to a
    # well-known US city token without a comma ("NYC - 1211 Ave ...").
    city_address = re.match(
        r"^(nyc|new york city|washington d\.?c\.?)\s*-\s*.+$",
        lower,
    )
    if city_address:
        key = (
            "washington dc"
            if city_address.group(1).startswith("washington")
            else city_address.group(1)
        )
        city, state, country = _CITY_DEFAULTS[key]
        return f"{city}, {state}, {country}"

    # Strip US country suffix and re-add normalized ", US"
    m = re.search(r',?\s*(united states of america|united states|usa|u\.s\.a\.|u\.s\.)\s*$', lower)
    if m:
        base = loc[:m.start()].strip().rstrip(',').strip()
        return normalize_location(base + ', US') if base else 'US'

    parts = [p.strip() for p in loc.split(',') if p.strip()]
    country_parts = [_country_code(part) for part in parts]
    if len(parts) > 1 and all(country_parts) and len(set(country_parts)) == 1:
        return country_parts[0]
    if len(parts) >= 3 and re.fullmatch(r'\d{4,6}(?:-\d{4})?', parts[-1]):
        parts = parts[:-1]
    # Workday frequently returns a facility/address followed by city and state,
    # for example:
    #   "Main Campus - 777 Bannock Street, Denver, CO 80204"
    # After removing the ZIP, CO must remain Colorado -- not become the ISO
    # country code for Colombia. The penultimate component guard preserves
    # explicit international triples such as "Bogotá, DC, CO".
    if (
        len(parts) >= 3
        and parts[-1].upper() in _US_STATES
        and not re.fullmatch(r"[A-Z]{2}", parts[-2])
    ):
        return f"{_title_case_city(parts[-2])}, {parts[-1].upper()}, US"
    if len(parts) >= 3:
        city, region, country_raw = parts[0], parts[1], parts[-1]
        country = _country_code(country_raw) or country_raw
        state = _US_STATE_NAMES.get(region.lower(), region.upper() if region.upper() in _US_STATES else region)
        if country == "Canada":
            state = _CANADA_PROVINCE_NAMES.get(region.lower(), region.upper() if region.upper() in _CANADA_PROVINCES else region)
        return ", ".join(x for x in (_title_case_city(city), state, country) if x)

    if len(parts) == 2:
        first, second = parts
        city_region = _CITY_REGION_COUNTRY_DEFAULTS.get(
            (first.lower(), second.lower())
        )
        if city_region:
            return ", ".join(city_region)
        first_country = _country_code(first)
        if first_country:
            if second.lower() == first_country.lower():
                return first_country
            return ", ".join(x for x in (_title_case_city(second), first_country) if x)
        state = _US_STATE_NAMES.get(second.lower()) or (second if second in _US_STATES else None)
        if state:
            return f"{_title_case_city(first)}, {state}, US"
        province = _CANADA_PROVINCE_NAMES.get(second.lower()) or (second if second in _CANADA_PROVINCES else None)
        if province:
            return f"{_title_case_city(first)}, {province}, Canada"
        country = _country_code(second)
        if country:
            if first.lower() == country.lower():
                return country
            city_key = first.lower()
            if city_key in _CITY_DEFAULTS and country == _CITY_DEFAULTS[city_key][2]:
                city, state, _ = _CITY_DEFAULTS[city_key]
                return ", ".join(x for x in (city, state, country) if x)
            return ", ".join(x for x in (_title_case_city(first), country) if x)
        if first.lower() in _CITY_DEFAULTS and re.search(r'\b(campus|office|center|centre|sez|tower|building|comm(er)?zone|park)\b', second, re.I):
            city, state, country = _CITY_DEFAULTS[first.lower()]
            return ", ".join(x for x in (city, state, country) if x)

    m2 = re.search(r'^(.*?)[,\s]+([A-Z]{2})\s*$', loc)
    if m2 and m2.group(2) in _US_STATES:
        return f"{_title_case_city(m2.group(1).strip())}, {m2.group(2)}, US"

    if lower in _CITY_DEFAULTS:
        city, state, country = _CITY_DEFAULTS[lower]
        if city.lower() == country.lower():
            return country
        return ", ".join(x for x in (city, state, country) if x)

    country = _country_code(loc)
    if country:
        return country

    return loc


_COUNTRY_ISO_BY_DISPLAY = {
    display.lower(): code
    for code, display in _COUNTRY_CODES.items()
}
_COUNTRY_ISO_BY_DISPLAY.update({
    "us": "US",
    "uk": "GB",
    "uae": "AE",
    "türkiye": "TR",
    "puerto rico": "PR",
})


def normalize_remote_mode(value, location_text=""):
    """Return ``remote``, ``hybrid``, ``onsite``, or ``None`` when unknown."""
    raw = " ".join(str(v) for v in (value, location_text) if v).strip().lower()
    if not raw:
        return None
    if re.search(r"\bhybrid\b", raw):
        return "hybrid"
    if re.search(r"\b(?:fully\s+remote|remote|work\s+from\s+home|wfh|distributed)\b", raw):
        return "remote"
    if re.search(r"\b(?:on-?site|in[- ]office|office[- ]based)\b", raw):
        return "onsite"
    return None


def _country_iso_from_normalized(location):
    if not location:
        return None
    if location == "US" or location.endswith(", US"):
        return "US"
    if location == "UK" or location.endswith(", UK"):
        return "GB"
    tail = location.rsplit(",", 1)[-1].strip().lower()
    return _COUNTRY_ISO_BY_DISPLAY.get(tail)


def _structured_location(raw, normalized, remote_mode):
    city = region = country = None
    country_iso = _country_iso_from_normalized(normalized)
    if normalized and not normalized.startswith("Remote"):
        parts = [part.strip() for part in normalized.split(",") if part.strip()]
        if country_iso and parts:
            country = parts[-1]
            if len(parts) >= 3:
                city, region = parts[0], parts[1]
            elif len(parts) == 2:
                first = parts[0]
                if (
                    (country_iso == "US" and first in _US_STATES)
                    or (country_iso == "CA" and first in _CANADA_PROVINCES)
                ):
                    region = first
                elif first.lower() != country.lower():
                    city = first
        elif len(parts) >= 2:
            city, region = parts[0], parts[1]
        elif parts and normalized not in {"Multiple Locations"}:
            city = parts[0]
    return {
        "raw": raw or None,
        "display": normalized or None,
        "city": city,
        "region": region,
        "country": country,
        "country_iso": country_iso,
        "remote_mode": remote_mode,
    }


def normalize_locations(raw_value, remote_mode=None):
    """Build canonical display and structured locations without losing raw data."""
    raw_items = []
    if isinstance(raw_value, list):
        for value in raw_value:
            if isinstance(value, dict):
                value = (
                    value.get("raw")
                    or value.get("display")
                    or value.get("name")
                    or value.get("label")
                    or ", ".join(
                        str(value.get(key))
                        for key in ("city", "region", "state", "country")
                        if value.get(key)
                    )
                )
            if value:
                raw_items.append(norm(str(value)))
    elif isinstance(raw_value, dict):
        assembled = ", ".join(
            str(raw_value.get(key))
            for key in ("city", "region", "state", "country")
            if raw_value.get(key)
        )
        if assembled:
            raw_items.append(norm(assembled))
    elif raw_value:
        raw_text = norm(str(raw_value))
        if "/" in raw_text and _location_separator_is_multi(raw_text):
            raw_items.extend(p.strip() for p in re.split(r"\s*/\s*", raw_text) if p.strip())
        else:
            raw_items.extend(
                p.strip()
                for p in re.split(r"\s*(?:;|\||│)\s*", raw_text)
                if p.strip()
            )

    output = []
    seen_display = set()
    explicit_mode = normalize_remote_mode(remote_mode)
    for raw in raw_items:
        display = normalize_location(raw)
        if not display:
            continue
        for display_part in [p.strip() for p in display.split(";") if p.strip()]:
            key = display_part.lower()
            if key in seen_display:
                continue
            seen_display.add(key)
            mode = explicit_mode or normalize_remote_mode(None, raw)
            output.append(_structured_location(raw, display_part, mode))

    # Providers sometimes include a "Multiple Locations" label alongside
    # the actual office list. Keep the explicit places rather than counting
    # that label as an additional office; retain it when nothing is resolved.
    if any(item["display"] != "Multiple Locations" for item in output):
        output = [item for item in output if item["display"] != "Multiple Locations"]

    top_mode = explicit_mode
    if top_mode is None:
        modes = {item["remote_mode"] for item in output if item["remote_mode"]}
        top_mode = next(iter(modes)) if len(modes) == 1 else None
    country_values = {
        item["country_iso"] for item in output if item["country_iso"]
    }
    country_iso = next(iter(country_values)) if len(country_values) == 1 else None
    display = "; ".join(
        item["display"] for item in output if item["display"]
    ) or None
    return {
        "location": display,
        "locations": output,
        "country_iso": country_iso,
        "remote_mode": top_mode,
    }

def extract_location_from_text(text):
    """Best-effort location extraction from job-card text."""
    if not text:
        return ''
    compact = " ".join(str(text).split())

    label = re.search(
        r'\b(?:location|office|city|work location|job location)s?[:\s-]+'
        r'([^|•\n;]+?)(?=\s+(?:posted|posting date|date posted|job id|requisition|apply|'
        r'travel|work location type|brands?|job type|function|experience level|description|location)\b|$)',
        compact,
        re.I,
    )
    if label:
        raw_candidate = label.group(1).strip(" ,-")
        # A location is a short field.  If an unfamiliar careers page appends
        # its description without a known label, fail closed instead of
        # rendering the description as the location.
        candidate = normalize_location(raw_candidate) if len(raw_candidate) <= 120 else ""
        if candidate and not _LOCATION_NOISE.search(candidate):
            return candidate

    # Pipe/bullet-separated segments: only treat segments short enough to be a single location.
    parts = [p.strip() for p in re.split(r'\s*[|│•]\s*', compact) if p.strip()]
    for part in parts:
        if len(part) > 80:          # too long to be just a location segment
            continue
        if _LOCATION_NOISE.search(part):
            continue
        normalized = normalize_location(part)
        if normalized and (normalized != part or normalized in ("Remote", "Multiple Locations")):
            # Reject if the city portion (before first comma) is more than 3 words —
            # that usually means non-location text leaked in (e.g. "New York Research Early Careers, NY, US")
            city_part = normalized.split(",")[0].strip() if "," in normalized else normalized
            if len(city_part.split()) > 2:
                continue
            return normalized

    # "Remote" / "Remote, US" — check before city_region which can't detect it
    if re.search(r'\bremote\b', compact, re.I):
        # Confirm it's not just "not remote" / "non-remote"
        if not re.search(r'\b(?:not|non-?|in-?)\b.{0,20}\bremote\b', compact, re.I):
            country_m = re.search(r'\bremote[,\s-]+(US|USA|United States|United Kingdom|UK|Canada|India|Australia)\b', compact, re.I)
            return normalize_location("Remote, " + country_m.group(1) if country_m else "Remote")

    # Require 2-letter state code to avoid matching job title words as city names
    # (e.g. "Sr. Specialist, Securities" → false match without this restriction).
    _UNLIKELY_CITY_RE = re.compile(
        r'^(?:Sr\.?|Jr\.?|Senior|Junior|Manager|Director|Engineer|Engineering|Analyst|'
        r'Specialist|Associate|Executive|Principal|Lead|VP|President|Officer|'
        r'Consultant|Head|Chief|General|Operations|Order|Management|Product|'
        r'Project|Program|Business|Global|Regional|Production|System|Systems|'
        r'Financial|Legal|Quantitative|Counsel|Support|ALM|NET|BCG|Risk|Compliance|'
        r'Treasury|Investment|Banking|Markets|Governance|Planning|Audit|Procurement|'
        r'Divisional|Corporate|Capital|Wealth|Trading|Pricing|Security|Infrastructure|'
        r'Software|Hardware|Data|Platform|Cloud|Services|Solutions|Technology|Technologies)\b',
        re.I,
    )
    # Try all city_region matches (not just first) so job-title words don't block real cities
    for city_region in re.finditer(
        r'\b([A-Z][A-Za-z.-]+(?:\s+[A-Z][A-Za-z.-]+){0,1}),\s*'
        r'([A-Z]{2}|United\s+(?:Kingdom|States(?:\s+of\s+America)?)|[A-Z][a-z]+)\b',
        compact,
    ):
        city_part = city_region.group(1)
        state_part = city_region.group(2)
        if _UNLIKELY_CITY_RE.match(city_part):
            continue
        # Reject if the state part is a job-title word (not a real 2-letter state code)
        if not re.fullmatch(r'[A-Z]{2}', state_part) and _UNLIKELY_CITY_RE.match(state_part):
            continue
        candidate = city_region.group(0)
        if len(candidate) < 50:
            return normalize_location(candidate)

    for city in sorted(_CITY_DEFAULTS, key=len, reverse=True):
        # Use \b at word start; allow end-of-word boundary OR uppercase continuation
        # so "DallasDallas" (location concatenated in title) still matches "Dallas".
        if re.search(rf'\b{re.escape(city)}(?:\b|(?=[A-Z]))', compact, re.I):
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
    padded = f" {hay.replace(', ', ' ')} "
    return any(f" {a} " in padded for a in aliases)

def _parse_posted(raw):
    """Parse a date string to ISO YYYY-MM-DD; return '' on failure."""
    if not raw:
        return ''
    raw = str(raw).strip()
    if not raw:
        return ''
    today = datetime.now().date()   # local time — matches what the user sees

    def _not_future(value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return ''
        return value if parsed <= today else ''

    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        return _not_future(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', raw)
    if m:
        return _not_future(f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    lower = raw.lower()
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
    for fmt in (
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%d-%B-%Y", "%d-%b-%Y",
    ):
        try:
            return _not_future(datetime.strptime(raw, fmt).date().isoformat())
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

_EMP_TYPE_MAP = [
    (r'\binternship\b|\bintern\b(?! review)',        'Internship'),
    (r'\bcontract(?:or|ing)?\b|\bfreelance\b',       'Contract'),
    (r'\bpart[- ]time\b',                            'Part-time'),
    (r'\bfull[- ]time\b|\bpermanent\b',              'Full-time'),
    (r'\btemporary\b|\btemp\b',                      'Temporary'),
]


def normalize_employment_type(value):
    """Map provider labels to the public display vocabulary."""
    if value is None:
        return None
    raw = norm(str(value))
    if not raw:
        return None
    canonical = raw.upper().replace("-", "_").replace(" ", "_")
    enum_map = {
        "FULL_TIME": "Full-time",
        "FULLTIME": "Full-time",
        "PERMANENT": "Full-time",
        "REGULAR": "Full-time",
        "PART_TIME": "Part-time",
        "PARTTIME": "Part-time",
        "CONTRACT": "Contract",
        "CONTRACTOR": "Contract",
        "FREELANCE": "Contract",
        "INTERN": "Internship",
        "INTERNSHIP": "Internship",
        "TEMP": "Temporary",
        "TEMPORARY": "Temporary",
        "SEASONAL": "Temporary",
    }
    if canonical in enum_map:
        return enum_map[canonical]
    lower = raw.lower()
    for pattern, label in _EMP_TYPE_MAP:
        if re.fullmatch(rf".*(?:{pattern}).*", lower, re.I):
            return label
    return None


def extract_employment_type(title, desc):
    """Return normalized employment type from title+description.

    Checks explicit title and labelled description signals. Missing evidence is
    returned as ``None`` rather than silently inventing a full-time value.
    """
    # Title is the most reliable signal — check it first for non-default types
    desc_l = (desc or "").lower()
    # Benefits/legal boilerplate often says part-time workers may receive fewer
    # benefits; that does not mean the role itself is part-time.
    desc_l = re.sub(
        r'\bsome of the above benefits may not apply to part[- ]time employees[^.]*\.',
        ' ',
        desc_l,
        flags=re.I,
    )
    _NON_DEFAULT = _EMP_TYPE_MAP[:-2]   # Internship, Contract, Part-time
    for pattern, label in _NON_DEFAULT:
        if re.search(pattern, (title or "").lower()):
            return label
    labelled = re.search(
        r"\b(?:job|employment|position|time|schedule)\s*type\s*:?\s*"
        r"(full[- ]time|part[- ]time|contract(?:or)?|intern(?:ship)?|temporary|seasonal)\b",
        desc_l,
    )
    if labelled:
        return normalize_employment_type(labelled.group(1))
    return None
