# scrapers/playwright_scraper.py
from __future__ import annotations
import concurrent.futures
import json
import re
import urllib.request
from urllib.parse import urljoin, urlparse, parse_qs, unquote, urlencode, urlunparse

try:
    from ..utils import _parse_posted
except ImportError:
    from api.utils import _parse_posted

try:
    from job_identity import make_job_id
except ModuleNotFoundError:
    from ...job_identity import make_job_id

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def norm(s: str) -> str:
    return " ".join((s or "").split())

def _make_id(company: str, url: str, title: str) -> str:
    return make_job_id(company, url, title)

def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def _parse_id_from_url(url: str) -> str:
    try:
        p = urlparse(url)
        qs = parse_qs(p.query)
        for k in ["id", "jobId", "jobID", "reqId", "requisitionId", "gh_jid"]:
            if k in qs and qs[k]:
                return (qs[k][0] or "").strip()
        seg = p.path.rstrip("/").split("/")[-1]
        if re.fullmatch(r"[A-Za-z0-9_-]{4,}", seg):
            return seg
    except Exception:
        pass
    return ""

# FIX 2: ID filter — returns True if the URL contains a numeric/alphanumeric ID
# after the include prefix. Filters out pure nav links like /careers/about-us
def _url_has_id(url: str) -> bool:
    """Return True if url contains an ID-like token (3+ digits somewhere in path or query)."""
    return bool(re.search(r'(?:[=/_-])([A-Za-z]{0,6}\d{3,}|\d{3,}[A-Za-z]{0,6})(?:[&/?#]|$)', url))

_JOB_PATH_RE = re.compile(
    r'/(?:job|jobs|careers?|career-opportunities|openings?|positions?|roles?|requisition|apply)(?:/|[-_?#=]|$)',
    re.I,
)

_BAD_TITLE_RE = re.compile(
    r'^(?:about|account|all jobs|benefits|blog|business areas|candidate home|company|'
    r'contact|contacts|customer stories|downloads|events|financial calendar|home|'
    r'investor relations?|learn more|legal|locations?|login|media|news|privacy|'
    r'reasons to invest|saved jobs|search|search jobs|service|sign in|sitemap|'
    r'terms|view all jobs|webinars)$',
    re.I,
)

def _looks_like_job_url(url: str) -> bool:
    """Keep browser fallback links tied to jobs, not ordinary site navigation."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    path = p.path or ""
    if _JOB_PATH_RE.search(path):
        return True
    qs = parse_qs(p.query)
    return any(k in qs and qs[k] for k in ("id", "jobId", "jobID", "reqId", "requisitionId", "gh_jid"))

def _clean_title(title: str) -> str:
    title = norm(title)
    if not title:
        return ""
    # Some card text arrives as "TitleLocation·Country·Level"; keep the title-ish
    # first segment instead of rendering the whole concatenated card.
    if "·" in title:
        title = title.split("·", 1)[0].strip()
    title = re.sub(r'\s+(?:Apply|Save Job|Posted|Posting Date)\b.*$', '', title, flags=re.I).strip()
    return norm(title)

def _is_bad_title(title: str) -> bool:
    title_l = norm(title).strip().lower()
    if not title_l or len(title_l) < 4:
        return True
    if _BAD_TITLE_RE.match(title_l):
        return True
    return False

def _title_from_url_or_text(url: str, text: str) -> str:
    bad_texts = {
        "read full job description", "read job description", "view job",
        "view job details", "view details", "apply", "apply now",
        "learn more", "see details", "details",
    }
    for raw in (text or "").splitlines():
        line = norm(raw)
        line_l = line.lower()
        if not line or line_l in bad_texts:
            continue
        if re.search(r"^(posted|company priority|hybrid|remote|on-site|apply)\b", line_l):
            continue
        return line

    t = norm(text)
    t_l = (t or "").strip().lower()
    if t and len(t) >= 4 and t_l not in bad_texts:
        return t
    try:
        p = urlparse(url)
        qs = parse_qs(p.query)
        for k in ["title", "jobTitle", "job_title", "position", "postingTitle", "name"]:
            if k in qs and qs[k]:
                return unquote(qs[k][0]).replace("+", " ").strip()
    except Exception:
        pass
    seg = urlparse(url).path.rstrip("/").split("/")[-1]
    seg = re.sub(r"[-_]+", " ", seg).strip()
    seg = re.sub(r"\d+", "", seg).strip()
    return seg

def _title_from_context(ctx_text: str) -> str:
    if not ctx_text:
        return ""
    for raw in ctx_text.splitlines():
        line = norm(raw)
        if not line or len(line) < 4:
            continue
        if re.search(r"^(posting date|location|apply|save job|posted)\b", line, re.I):
            continue
        if re.search(r"\b(on-site|hybrid|remote)\b", line, re.I):
            continue
        return line
    return ""

# FIX 1: Location extraction from surrounding text
def _extract_location(ctx_text: str) -> str:
    """
    Try to extract a location from surrounding card text.
    Handles:
      - Pipe-separated: "REQ7401 | London | Posted Today" → "London"
      - "Location: New York, NY"
      - "New York, NY" or "San Francisco, CA" patterns
      - Workday city labels
    """
    if not ctx_text:
        return ""
    text = " ".join(ctx_text.split())

    # Pipe-separated (Citadel/Salesforce): "REQ#### | City | Posted ..."
    parts = [p.strip() for p in re.split(r'\s*[|│•]\s*', text) if p.strip()]
    for part in parts:
        if len(part) > 60:          # too long to be just a location segment
            continue
        if re.search(r'Posted|Today|Yesterday|\d+\s+(?:hours?|days?|weeks?)|Apply|Save|Req(?:uisition)?|Job ID', part, re.I):
            continue
        if re.search(r'\b(remote|multiple locations?|[A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*[A-Z]{2})\b', part, re.I):
            return part

    # "Location: ..." label
    loc_label = re.search(
        r'(?:Location|Office|City|Work Location)[:\s-]+([^|•;\n]+?)(?=\s+(?:Posted|Posting Date|Date Posted|Apply|Job ID|Req)\b|$)',
        text,
        re.I,
    )
    if loc_label:
        return loc_label.group(1).strip(" ,-")

    # "City, ST" pattern — require 2-letter uppercase state code to avoid matching
    # job title fragments like "Director, Legal" or "Sr. Manager, Financial".
    # A full state name ("California") is too ambiguous; require "CA" / "TX" etc.
    _UNLIKELY_CITY = re.compile(
        r'^(?:Sr\.?|Jr\.?|Senior|Junior|Manager|Director|Engineer|Analyst|'
        r'Specialist|Associate|Executive|Principal|Lead|VP|President|Officer|'
        r'Consultant|Head|Chief|General|Operations|Order|Management|Product|'
        r'Project|Program|Business|Global|Regional|Production|System|Systems|'
        r'Financial|Legal|Quantitative|Counsel|Support|ALM|NET|BCG|Inverto)\b',
        re.I,
    )
    city_state = re.search(
        r'\b([A-Z][A-Za-z.-]+(?: [A-Z][A-Za-z.-]+){0,1}),\s*'
        r'([A-Z]{2}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})\b'
        r'(?:,\s*(United States(?: of America)?|USA|US|Canada|United Kingdom|UK))?\b',
        text,
    )
    if city_state:
        city_part = city_state.group(1)
        if not _UNLIKELY_CITY.match(city_part):
            candidate = city_state.group(0)
            if len(candidate) < 50:
                return candidate

    return ""

def _extract_posted(ctx_text: str) -> str:
    if not ctx_text:
        return ""
    text = " ".join(ctx_text.split())
    # Explicit "Posted:" label — most reliable
    m = re.search(
        r'\b(?:posted|posting date|date posted|published)[:\s-]+'
        r'(today|yesterday|\d+\s+(?:hours?|days?|weeks?)\s+ago|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b',
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r'\b(today|yesterday|\d+\s+(?:hours?|days?|weeks?)\s+ago)\b', text, re.I)
    if m:
        return m.group(1).strip()
    # Strip deadline/closing/expiry dates before falling back to bare date patterns
    # so we don't accidentally return "Application deadline: 2026-05-18" as the post date
    clean = re.sub(
        r'(?:application\s+)?(?:deadline|closing date|expir\w+|valid\s+through|apply\s+by)[:\s]+[\d/–-]+(?:-\d+)*',
        '', text, flags=re.I,
    )
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', clean)
    if m:
        return m.group(1)
    m = re.search(r'\b([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b', clean)
    if m:
        return m.group(1).strip()
    return ""

def _posted_from_detail_html(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "ignore")
    except Exception:
        return ""

    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                for key in ("datePosted", "postedDate", "postingDate", "publishedDate", "createdAt"):
                    if obj.get(key):
                        return str(obj.get(key))
                stack.extend(obj.values())
            elif isinstance(obj, list):
                stack.extend(obj)

    meta = re.search(
        r'<meta[^>]+(?:property|name)=["\'](?:datePosted|article:published_time|publishdate|pubdate)["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if meta:
        return meta.group(1).strip()
    text = re.sub(r"<[^>]+>", " ", html)
    return _extract_posted(text)

_PICK_SCROLL_EL = r"""
() => {
  const els = [document.scrollingElement, ...document.querySelectorAll('*')].filter(Boolean);
  let best = document.scrollingElement;
  let bestDelta = 0;
  for (const el of els) {
    const style = window.getComputedStyle(el);
    const canScroll = (style.overflowY === 'auto' || style.overflowY === 'scroll');
    const delta = el.scrollHeight - el.clientHeight;
    if (delta > bestDelta && (canScroll || el === document.scrollingElement)) {
      bestDelta = delta;
      best = el;
    }
  }
  window.__jobradar_scroll_el = best;
  return { delta: bestDelta, tag: best?.tagName || null, id: best?.id || null };
}
"""

_SCROLL_STEP = r"""
() => {
  const el = window.__jobradar_scroll_el || document.scrollingElement;
  if (!el) return { ok:false };
  const before = el.scrollTop;
  el.scrollTop = el.scrollHeight;
  const after = el.scrollTop;
  return { ok:true, before, after, height: el.scrollHeight, client: el.clientHeight };
}
"""

# FIX 1: Extended to capture parentText (up to 400 chars of surrounding card text)
_EXTRACT_CANDIDATES = r"""
() => {
  const out = [];

  function parentText(el) {
    // Walk up to find a card-like container with meaningful text
    let cur = el;
    for (let i = 0; i < 5; i++) {
      cur = cur?.parentElement;
      if (!cur) break;
      const t = (cur.innerText || cur.textContent || '').trim();
      if (t.length > 20 && t.length < 600) return t.slice(0, 400);
    }
    return '';
  }

  function push(url, text, el) {
    if (!url) return;
    out.push({
      url: String(url),
      text: (text || '').trim().slice(0, 200),
      ctx: parentText(el || document.body)   // ← surrounding card text for location
    });
  }

  // 1) <a href>
  document.querySelectorAll('a[href]').forEach(a =>
    push(a.getAttribute('href'), a.textContent, a));

  // 2) data-* urls
  document.querySelectorAll('[data-href],[data-url],[data-link]').forEach(el =>
    push(el.getAttribute('data-href') || el.getAttribute('data-url') || el.getAttribute('data-link'),
         el.textContent, el));

  // 3) role=link
  document.querySelectorAll('[role="link"]').forEach(el =>
    push(el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url'),
         el.textContent, el));

  // 4) onclick
  document.querySelectorAll('[onclick]').forEach(el => {
    const s = el.getAttribute('onclick') || '';
    const m =
      s.match(/(?:window\.open|location\.href|document\.location)\s*\(\s*['"]([^'"]+)['"]/) ||
      s.match(/(?:window\.open|location\.href|document\.location)\s*=\s*['"]([^'"]+)['"]/);
    if (m && m[1]) push(m[1], el.textContent, el);
  });

  return out;
}
"""

def _collect_all(page, list_url: str, scroll_rounds: int, try_load_more: bool = False) -> list[dict]:
    try:
        page.evaluate(_PICK_SCROLL_EL)
    except Exception:
        pass

    seen = set()
    items = []
    stable = 0
    last_n = 0
    load_more_exhausted = False

    for i in range(scroll_rounds):
        cands = page.evaluate(_EXTRACT_CANDIDATES) or []
        for c in cands:
            u = (c.get("url") or "").strip()
            if not u:
                continue
            abs_u = urljoin(list_url, u)
            if abs_u in seen:
                continue
            seen.add(abs_u)
            items.append({"url": abs_u, "text": c.get("text") or "", "ctx": c.get("ctx") or ""})

        if i >= 8 and len(items) == last_n:
            stable += 1
        else:
            stable = 0
        last_n = len(items)

        if stable >= 3 and try_load_more and not load_more_exhausted:
            if _click_load_more_if_present(page):
                stable = 0
                continue
            else:
                load_more_exhausted = True

        if stable >= 5:
            break

        try:
            page.evaluate(_SCROLL_STEP)
        except Exception:
            page.mouse.wheel(0, 3000)
        page.wait_for_timeout(400)

        # Try load-more after every scroll to catch buttons that appear at the bottom
        if try_load_more and not load_more_exhausted and (i % 3 == 2):
            _click_load_more_if_present(page)

    return items

def _validate_one(page, url: str) -> tuple[bool, str]:
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        status = resp.status if resp else 0
        page.wait_for_timeout(800)
        title = (page.title() or "").strip().lower()
        body  = (page.evaluate("() => document.body ? document.body.innerText.slice(0, 800) : ''") or "").lower()
        bad = any(x in title for x in ["privacy", "cookies", "access denied"]) or \
              any(x in body  for x in ["privacy", "cookie", "access denied", "forbidden"])
        ok = (status == 0 or 200 <= status < 500) and (len(body) > 200) and not bad
        return ok, f"status={status} title={title[:80]!r}"
    except Exception as e:
        return False, str(e)

def _make_paged_url(base: str, param: str, val: int) -> str:
    fi = base.find("#")
    frag = base[fi:] if fi != -1 else ""
    base2 = base[:fi] if fi != -1 else base
    parts = list(urlparse(base2))
    qs = parse_qs(parts[4], keep_blank_values=True)
    qs[param] = [str(val)]
    parts[4] = urlencode(qs, doseq=True)
    return urlunparse(parts) + frag

_NEXT_SELECTORS = [
    "a[rel='next']", "a[aria-label*='Next' i]", "button[aria-label*='Next' i]",
    "a:has-text('Next')", "button:has-text('Next')", "li.next a",
    ".pagination .next a", "[class*=next] a", "[class*=next] button",
]

_LOAD_MORE_SELECTORS = [
    "button:has-text('Load More')", "button:has-text('Load more')", "button:has-text('load more')",
    "button:has-text('Show More')", "button:has-text('Show more')",
    "button:has-text('See More')",  "button:has-text('See more')",
    "button:has-text('View More')", "button:has-text('View more')",
    "a:has-text('Load More')",      "a:has-text('Show More')",
    "[class*='load-more']", "[class*='loadMore']", "[class*='load_more']",
    "[data-ui='load-more-button']", "[data-testid*='load-more' i]",
    "button[aria-label*='Load more' i]", "button[aria-label*='Show more' i]",
]

def _click_load_more_if_present(page) -> bool:
    for sel in _LOAD_MORE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=500):
                continue
            aria_disabled = (loc.get_attribute("aria-disabled") or "").lower()
            if aria_disabled in ("true", "disabled"):
                continue
            try:
                if loc.is_disabled(timeout=300):
                    continue
            except Exception:
                pass
            loc.scroll_into_view_if_needed(timeout=1000)
            loc.click(timeout=2000)
            page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False

def _click_next_if_present(page) -> bool:
    cur_url = page.url
    for sel in _NEXT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=800):
                continue
            aria_disabled = (loc.get_attribute("aria-disabled") or "").lower()
            if aria_disabled in ("true", "disabled"):
                continue
            try:
                if loc.is_disabled(timeout=400):
                    continue
            except Exception:
                pass
            loc.click(timeout=2000)
            page.wait_for_timeout(1200)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            return True
        except Exception:
            continue
    return False

def fetch_playwright(scrape: dict, seen: dict, company: str):
    """
    Universal Playwright scraper.

    `include` is a PREFIX — URLs must start with it.
    FIX 1: Extracts location from surrounding card text (ctx).
    FIX 2: If include ends with '=' or '/' (implying an ID follows),
            filters out URLs that don't have a numeric/alphanumeric ID after the prefix.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], (
            "playwright not installed — run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )

    list_url         = (scrape.get("list_url") or "").strip()
    include          = (scrape.get("prefix", scrape.get("include", ""))  or "").strip()
    scroll_rounds    = int(scrape.get("scroll_rounds", 40))
    same_origin_only = bool(scrape.get("same_origin_only", False))
    validate_prefix = bool(scrape.get("validate_prefix", True))
    do_sort          = bool(scrape.get("sort", False))
    stop_on_seen     = bool(scrape.get("stop_on_seen", False))
    fetch_posted_details = bool(scrape.get("fetch_posted_details", False))
    posted_detail_limit = int(scrape.get("max_posted_details", 100000))
    url_exclude      = [s.lower() for s in (scrape.get("url_exclude") or [])]

    pg        = scrape.get("pagination") or {"type": "none"}
    pg_type   = (pg.get("type") or "none").strip()
    max_pages = int(pg.get("hard_max_pages") or scrape.get("hard_max_pages") or 1000)

    # load_more pagination type is just "none" + try_load_more=True
    try_load_more = bool(scrape.get("try_load_more", False)) or (pg_type == "load_more")
    if pg_type == "load_more":
        pg_type = "none"

    if not list_url: return [], "no list_url"
    if not include:  return [], "no prefix set (must be job detail URL prefix)"

    list_origin = _origin(list_url)
    include_l   = include.lower()

    # ID filter: if include ends with = or / (implying an ID follows),
    # reject URLs where nothing meaningful comes after the include substring.
    needs_id = bool(scrape.get("require_id", True)) and include.endswith(("=", "/"))

    seen_abs_links: set[str] = set()
    seen_job_urls:  set[str] = set()
    seen_snapshot = list((seen or {}).values())
    known_urls = {
        (v.get("url") or "").strip()
        for v in seen_snapshot
        if isinstance(v, dict) and v.get("url")
    }
    jobs_out: list[dict] = []

    def process_one_page(page) -> tuple[int, bool]:
        nonlocal jobs_out
        cands = _collect_all(page, list_url, scroll_rounds, try_load_more=try_load_more)

        if same_origin_only:
            cands = [c for c in cands if (c["url"] or "").startswith(list_origin)]

        new_added = 0
        hit_seen = False

        for c in cands:
            u = (c.get("url") or "").strip()
            if not u or u in seen_abs_links:
                continue
            seen_abs_links.add(u)

            if include_l not in u.lower():
                continue

            if url_exclude and any(ex in u.lower() for ex in url_exclude):
                continue

            if not _looks_like_job_url(u):
                continue

            # ID filter: if include ends with = or /, the part after it must contain digits
            if needs_id:
                idx = u.lower().find(include_l)
                suffix = u[idx + len(include):]
                if not re.search(r'\d{2,}', suffix):
                    continue  # no job ID → skip nav/generic links

            if u in seen_job_urls:
                continue
            seen_job_urls.add(u)

            title = _title_from_url_or_text(u, c.get("text", ""))
            if not title or title.lower() == "unknown title":
                title = _title_from_context(c.get("ctx", ""))
            title = norm(title)
            if not title or len(title) < 4:
                title = norm(_title_from_context(c.get("ctx", "")) or _title_from_url_or_text(u, "")) or "Unknown Title"
            title = _clean_title(title)
            title_regex = scrape.get("title_regex")
            if title_regex:
                m = re.search(title_regex, title)
                if m:
                    title = norm(m.group(1))
            if _is_bad_title(title):
                continue

            # Use anchor text + card context for location; fall back to title itself
            _loc_combined = " ".join(filter(None, [c.get("text", ""), c.get("ctx", "")]))
            location = _extract_location(_loc_combined) or _extract_location(title)
            posted = _extract_posted(c.get("ctx", ""))
            jobs_out.append({"title": title, "url": u, "location": location, "source": "playwright", "posted": posted})
            new_added += 1
            is_known = _make_id(company, u, title) in seen or u in known_urls
            if do_sort and stop_on_seen and seen and is_known:
                hit_seen = True
                break

        # Fallback: try jobdetails patterns if nothing matched prefix
        if new_added == 0 and not hit_seen:
            for c in cands:
                u = (c.get("url") or "").strip()
                if not u or u in seen_job_urls:
                    continue
                ul = u.lower()
                if "jobdetails?id=" in ul or "jobdetails?jobid=" in ul or "jobdetails?reqid=" in ul:
                    seen_job_urls.add(u)
                    title    = _title_from_url_or_text(u, c.get("text", ""))
                    title    = norm(title or _title_from_context(c.get("ctx", ""))) or "Unknown Title"
                    title    = _clean_title(title)
                    title_regex = scrape.get("title_regex")
                    if title_regex:
                        m = re.search(title_regex, title)
                        if m:
                            title = norm(m.group(1))
                    if _is_bad_title(title):
                        continue
                    _loc_combined = " ".join(filter(None, [c.get("text", ""), c.get("ctx", "")]))
                    location = _extract_location(_loc_combined) or _extract_location(title)
                    posted   = _extract_posted(c.get("ctx", ""))
                    jobs_out.append({"title": title, "url": u, "location": location, "source": "playwright", "posted": posted})
                    new_added += 1
                    is_known = _make_id(company, u, title) in seen or u in known_urls
                    if do_sort and stop_on_seen and seen and is_known:
                        hit_seen = True
                        break

        return new_added, hit_seen

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=UA,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()

            empty_streak = 0

            if pg_type == "none":
                page.goto(list_url, wait_until="commit", timeout=15_000)
                page.wait_for_timeout(1200)
                added, hit_seen = process_one_page(page)
                if added == 0:
                    empty_streak += 1

            elif pg_type == "query_page":
                param = (pg.get("param") or "page").strip()
                start = int(pg.get("start", 1))
                step  = int(pg.get("step", 1))
                for i in range(max_pages):
                    val  = start + i * step
                    purl = _make_paged_url(list_url, param, val)
                    page.goto(purl, wait_until="commit", timeout=15_000)
                    page.wait_for_timeout(1200)
                    added, hit_seen = process_one_page(page)
                    if added == 0:
                        empty_streak += 1
                    else:
                        empty_streak = 0
                    if hit_seen:
                        break
                    if empty_streak >= 2:
                        break

            elif pg_type == "path_page":
                # URL path pagination: base/page/2/, base/page/3/, etc.
                segment = (pg.get("segment") or "page").strip()
                start   = int(pg.get("start", 1))
                step    = int(pg.get("step", 1))
                base    = list_url.rstrip("/")
                for i in range(max_pages):
                    val  = start + i * step
                    purl = f"{base}/{segment}/{val}/" if i > 0 else list_url
                    page.goto(purl, wait_until="commit", timeout=15_000)
                    page.wait_for_timeout(1200)
                    added, hit_seen = process_one_page(page)
                    if added == 0:
                        empty_streak += 1
                    else:
                        empty_streak = 0
                    if hit_seen:
                        break
                    if empty_streak >= 2:
                        break

            elif pg_type == "next_link":
                page.goto(list_url, wait_until="commit", timeout=15_000)
                page.wait_for_timeout(1200)
                for _ in range(max_pages):
                    added, hit_seen = process_one_page(page)
                    if added == 0:
                        empty_streak += 1
                    else:
                        empty_streak = 0
                    if hit_seen:
                        break
                    if empty_streak >= 2:
                        break
                    if not _click_next_if_present(page):
                        break
                    page.wait_for_timeout(1200)

            else:
                browser.close()
                return [], f"unknown pagination type: {pg_type}"

            if validate_prefix and jobs_out:
                ok, info = _validate_one(page, jobs_out[0]["url"])
                if not ok:
                    browser.close()
                    return [], f"[{company}] include base seems wrong or blocked. test={jobs_out[0]['url']} ({info})"

            browser.close()
            if fetch_posted_details and jobs_out:
                targets = []
                for job in jobs_out:
                    if not job.get("posted") and isinstance(seen, dict):
                        existing = seen.get(_make_id(company, job.get("url") or "", job.get("title") or ""))
                        if isinstance(existing, dict):
                            job["posted"] = _parse_posted(existing.get("posted") or "")
                    if not job.get("posted") and len(targets) < posted_detail_limit:
                        targets.append(job)

                def _fetch_posted(job):
                    return job, _posted_from_detail_html(job.get("url") or "")

                if targets:
                    detail_workers = int(scrape.get("detail_workers", 6))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                        for job, posted in ex.map(_fetch_posted, targets):
                            if posted:
                                job["posted"] = posted
            return jobs_out, None

    except Exception as e:
        return [], str(e)
