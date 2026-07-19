from .base import *
from urllib.parse import urlunparse

# ── HTML ──────────────────────────────────────────────────────────────────────

def fetch_html(scrape, seen, company):
    """Universal HTML scraper for simple static career pages."""
    list_url    = scrape.get("list_url","")
    include     = scrape.get("include","")
    title_param = scrape.get("title_param","")
    title_suffix_regex = scrape.get("title_suffix_regex", "")
    url_exclude = [str(s).lower() for s in (scrape.get("url_exclude") or [])]
    title_exclude = [str(s).lower() for s in (scrape.get("title_exclude") or [])]
    stop_on_seen = bool(scrape.get("stop_on_seen", False))
    use_reader  = bool(scrape.get("reader", False))
    verify_ssl  = bool(scrape.get("verify_ssl", True))
    headers     = dict(HEADERS)
    if scrape.get("user_agent"):
        headers["User-Agent"] = scrape["user_agent"]
    pg_cfg      = scrape.get("pagination") or {"type":"none"}
    pg_type     = pg_cfg.get("type","none")
    max_pages   = int(pg_cfg.get("hard_max_pages") or scrape.get("hard_max_pages") or 100)
    page_delay  = max(0.0, float(scrape.get("page_delay_s", 0.35)))
    require_job_path = bool(scrape.get("require_job_path", False))
    default_location = normalize_location(scrape.get("default_location") or "")
    expected_total = None

    if not list_url: return [], "no list_url"
    if not include:  return [], "no include pattern"

    visited: set = set()

    def extract_embedded_next_jobs(soup, page_url):
        """Decode job objects embedded in Next.js Flight script chunks.

        Some official career pages render job data into ``self.__next_f`` but
        do not emit ordinary anchors until client-side hydration. Reading that
        already-delivered data avoids browser automation and extra API calls.
        """
        if not scrape.get("embedded_next_jobs"):
            return {}
        found = {}
        decoder = json.JSONDecoder()
        marker = '"attributes":'
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            if "self.__next_f.push(" not in text or "ashby_jid" not in text:
                continue
            call_start = text.find("self.__next_f.push(") + len("self.__next_f.push(")
            call_end = text.rfind(")")
            if call_end <= call_start:
                continue
            try:
                payload = json.loads(text[call_start:call_end])
            except Exception:
                continue
            chunks = [value for value in payload if isinstance(value, str)] if isinstance(payload, list) else []
            for chunk in chunks:
                pos = 0
                while True:
                    pos = chunk.find(marker, pos)
                    if pos < 0:
                        break
                    obj_start = pos + len(marker)
                    try:
                        attrs, obj_end = decoder.raw_decode(chunk, obj_start)
                    except Exception:
                        pos = obj_start
                        continue
                    pos = obj_end
                    if not isinstance(attrs, dict) or not attrs.get("title"):
                        continue
                    slug = norm(attrs.get("slug") or "")
                    if "ashby_jid=" not in slug or attrs.get("is_listed") is False:
                        continue
                    job_url = urljoin(page_url, slug)
                    if not has_include(job_url, include) or job_url in visited:
                        continue
                    visited.add(job_url)
                    job = {
                        "title": norm(attrs.get("title") or ""),
                        "url": job_url,
                        "location": normalize_location(norm(attrs.get("location_name") or "")),
                        "source": "html",
                        "posted": _parse_posted(attrs.get("ashby_published_date") or ""),
                    }
                    description = attrs.get("full_job_description") or attrs.get("description")
                    if description:
                        job["description"] = description
                    if attrs.get("employment_type"):
                        job["employment_type"] = attrs["employment_type"]
                    found[job_url] = job
        return found

    def is_job_detail_url(url):
        if not require_job_path:
            return True
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        path = parsed.path.rstrip("/")
        if re.search(r'/(?:jobs?|openings?|positions?|requisitions?)/[^/]+$', path, re.I):
            return True
        if re.search(r'/(?:jobs?|openings?|positions?|requisitions?)/[^/]+/[^/]+$', path, re.I):
            return True
        if re.search(r'/careers?/[^/]+$', path, re.I):
            return True
        query = parse_qs(parsed.query)
        return any(
            query.get(key)
            for key in ("jobId", "jobID", "job_id", "reqId", "requisitionId", "gh_jid")
        )

    def scrape_one(page_url):
        nonlocal expected_total
        try:
            fetch_url = "https://r.jina.ai/" + page_url if use_reader else page_url
            r = requests.get(fetch_url, headers=headers, timeout=25, verify=verify_ssl)
            r.raise_for_status()
        except Exception as e:
            return {}, str(e), None, False

        if use_reader:
            found = {}
            stop = False
            for idx, line in enumerate(r.text.splitlines()):
                for title, abs_url in re.findall(r"\[([^\]]{4,180})\]\((https?://[^)\s]+)\)", line):
                    if not has_include(abs_url, include):
                        continue
                    if not is_job_detail_url(abs_url):
                        continue
                    if url_exclude and any(ex in abs_url.lower() for ex in url_exclude):
                        continue
                    if abs_url in visited:
                        continue
                    visited.add(abs_url)
                    title = norm(re.sub(r"!\[[^\]]*\]", " ", title))
                    title = re.sub(r'\s+(?:learn more|apply now|view job)$', '', title, flags=re.I).strip()
                    if not title or re.search(r"^(apply|apply now|view job|search jobs|saved jobs|learn more|read more|see details|job details)$", title, re.I):
                        continue
                    if title_exclude and any(ex in title.lower() for ex in title_exclude):
                        continue
                    jid = make_id(company, abs_url, title)
                    if stop_on_seen and jid in seen:
                        stop = True
                        break
                    nearby_lines = r.text.splitlines()[idx + 1:idx + 8]
                    # Strip markdown list bullets (* item) — they contain description
                    # text that looks like locations (e.g. "* Seattle, WA, and collaborate...")
                    nearby_clean = " ".join(
                        l for l in nearby_lines
                        if not re.match(r'^\s*\*\s+[A-Z].*,', l)  # skip "* City, ..." bullets
                    )
                    nearby = " ".join(nearby_lines)
                    found[abs_url] = {
                        "title": title,
                        "url": abs_url,
                        "location": (extract_location_from_text(nearby_clean)
                                     or extract_location_from_text(nearby)
                                     or extract_location_from_text(title)
                                     or default_location),
                        "source": "html",
                        "posted": extract_posted_from_text(nearby),
                    }
                if stop:
                    break
            return found, None, None, stop

        soup  = BeautifulSoup(r.text, "lxml")
        if scrape.get("verify_reported_total"):
            page_text = norm(soup.get_text(" ", strip=True))
            total_match = (
                re.search(
                    r"\bShowing\s+\d[\d,]*\s*-\s*\d[\d,]*\s+of\s+([\d,]+)\s+jobs\b",
                    page_text,
                    re.I,
                )
                or re.search(r"\b([\d,]+)\s+open positions?\b", page_text, re.I)
                or re.search(r"\b([\d,]+)\s+open roles?\b", page_text, re.I)
            )
            if total_match:
                expected_total = int(total_match.group(1).replace(",", ""))
        found = extract_embedded_next_jobs(soup, page_url)
        stop  = False

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#","javascript:","mailto:")):
                continue
            try:
                abs_url = urljoin(page_url, href)
            except ValueError:
                # A malformed third-party link must not fail every valid job on
                # the page (for example, an invalid ConvertKit form hostname).
                continue
            if not has_include(abs_url, include):
                continue
            if not is_job_detail_url(abs_url):
                continue
            if url_exclude and any(ex in abs_url.lower() for ex in url_exclude):
                continue
            if abs_url in visited:
                continue
            visited.add(abs_url)

            if title_param:
                title = get_param_title(abs_url, title_param)
            else:
                raw_anchor = norm(a.get_text(" ", strip=True))
                # Some ATS (e.g. Freshteam) embed "Job Title: ... Location: ..."
                # inside the <a> tag. Extract location before stripping labels.
                _loc_in_anchor = ""
                # Freshteam and similar ATS embed "Location:" inside a word (e.g.
                # "ExperientialLocation: Remote, New York") so skip the word boundary.
                _loc_m = re.search(r'Location:\s*([^\n]{3,120})', raw_anchor, re.I)
                if _loc_m:
                    loc_raw = _loc_m.group(1)
                    # Strip trailing embedded metadata (employment type, company info)
                    loc_raw = re.sub(
                        r'[\s)]+(?:Employment\s+Type|About\b|Full[- ]Time|Part[- ]Time|Contract|Intern|Temporary)\b.*$',
                        '', loc_raw, flags=re.I,
                    ).strip().strip("()")
                    _loc_in_anchor = normalize_location(norm(loc_raw)) if len(loc_raw) < 80 else ""
                title = re.sub(r'\s+(?:Job Title|Location|Department|Type)[:\s].*', '', raw_anchor, flags=re.I).strip()
                # Freshteam / BambooHR job cards embed the full description inside <a>.
                # If the anchor text is very long, use the URL slug as a clean title.
                if len(title) > 100:
                    seg = urlparse(abs_url).path.rstrip("/").split("/")[-1]
                    slug_title = re.sub(r"[-_]", " ", seg).strip().title()
                    if len(slug_title) >= 4:
                        title = slug_title
                    else:
                        title = title[:100].rsplit(" ", 1)[0]

            if not title or len(title) < 4:
                parts = [p for p in urlparse(abs_url).path.rstrip("/").split("/") if p]
                seg   = parts[-1] if parts else ""
                if re.fullmatch(r"\d+", seg or "") and len(parts) >= 2:
                    seg = parts[-2]
                title = re.sub(r"[-_]"," ", seg).strip()
            if not title or len(title) < 4:
                continue
            title = re.sub(r'\s+(?:learn more|apply now|view job)$', '', title, flags=re.I).strip()
            if title_suffix_regex:
                title = re.sub(title_suffix_regex, "", title, flags=re.I).strip()
            _NAV = {
                "view all jobs","view all","all jobs","apply","view job","search jobs",
                "saved jobs","learn more","see all openings","see all jobs","open positions",
                "view openings","view open jobs","careers","career","about","about us",
                "contact","contact us","benefits","diversity","home","search","login",
                "sign in","engineering","technologies","blog","news","events","media",
                "privacy","terms","legal","sitemap","locations","back to top","join us",
                "job listings","job openings","open roles","our culture","our values",
                "why work here","meet our team","early talent","student careers",
                "inclusive culture","citizenship","current openings","career opportunities",
                "career paths","facility operations","skip to content","manage cookies",
                "cookie settings","job search","no jobs found","find jobs","explore jobs",
                "company","workplace","services","explore","service","explore entry level",
                "explore our application steps","jobs per page","back to top page navigation",
                "unknown title","view all openings","see all","all openings",
                "read more","see details","job details",
            }
            t_lower = title.lower().strip()
            if t_lower in _NAV:
                continue
            # Also skip very generic single-word / logo / icon titles
            import re as _re2
            if _re2.match(r'^(?:engineering|about|contact|careers?|benefits?|diversity|'
                          r'technologies?|services?|explore|workplace|culture|citizenship|'
                          r'.*\blogo\b.*|.*\bicon\b.*|hrcc|about us|contact us)$', t_lower, _re2.I):
                continue
            if title_exclude and any(ex in title.lower() for ex in title_exclude):
                continue

            jid = make_id(company, abs_url, title)
            if stop_on_seen and jid in seen:
                stop = True; break

            parent_text = ""
            parent = a
            for _ in range(5):
                parent = parent.parent if parent else None
                if not parent:
                    break
                t = norm(parent.get_text(" ", strip=True))
                if len(t) > 20:
                    parent_text = t
                    if len(t) < 600:
                        break

            # Try parent text first (skip if huge multi-card container).
            # Also try the raw anchor text and the cleaned title for embedded locations.
            loc_candidate = (
                (extract_location_from_text(parent_text) if len(parent_text) <= 500 else "")
                or extract_location_from_text(raw_anchor)
                or extract_location_from_text(title)
            )
            # Don't use the job title itself as the location
            if loc_candidate and loc_candidate.lower() == title.lower():
                loc_candidate = ""
            # Prefer location extracted from the anchor tag (Freshteam-style embeds)
            if not loc_candidate and _loc_in_anchor:
                loc_candidate = _loc_in_anchor
            # Last resort: first 300 chars of large parent text
            if not loc_candidate and len(parent_text) > 500:
                loc_candidate = extract_location_from_text(parent_text[:300])
                if loc_candidate and loc_candidate.lower() == title.lower():
                    loc_candidate = ""
            if default_location and loc_candidate in ("", "Remote", "Multiple Locations"):
                loc_candidate = default_location
            found[abs_url] = {
                "title": title,
                "url": abs_url,
                "location": loc_candidate,
                "source": "html",
                "posted": extract_posted_from_text(parent_text),
            }

        next_url = None
        for sel in ["a[rel='next']","a.next","[aria-label='Next page']","li.next a",
                    ".pagination .next a","[class*=next] a"]:
            nxt = soup.select_one(sel)
            if nxt and nxt.get("href"):
                cand = urljoin(page_url, nxt["href"])
                if cand != page_url:
                    next_url = cand; break

        return found, None, next_url, stop

    all_jobs: dict = {}
    first_err      = None

    if pg_type == "none":
        found, err, _, _ = scrape_one(list_url)
        if err: first_err = err
        all_jobs.update(found)

    elif pg_type == "query_page":
        for idx in range(max_pages):
            purl = make_paged_url(list_url, pg_cfg, idx)
            found, err, _, stop = scrape_one(purl)
            if err and not first_err: first_err = err
            new = {k:v for k,v in found.items() if k not in all_jobs}
            all_jobs.update(new)
            if stop or (not new and not found): break
            time.sleep(page_delay)

    elif pg_type == "path_page":
        segment = (pg_cfg.get("segment") or "page").strip()
        start = int(pg_cfg.get("start", 1))
        step = int(pg_cfg.get("step", 1))
        trailing_slash = bool(pg_cfg.get("trailing_slash", True))
        base_parts = list(urlparse(list_url))
        base_path = base_parts[2].rstrip("/")
        for idx in range(max_pages):
            value = start + idx * step
            suffix = "/" if trailing_slash else ""
            if idx == 0:
                purl = list_url
            else:
                parts = list(base_parts)
                parts[2] = f"{base_path}/{segment}/{value}{suffix}"
                purl = urlunparse(parts)
            found, err, _, stop = scrape_one(purl)
            if err and not first_err:
                first_err = err
            new = {k: v for k, v in found.items() if k not in all_jobs}
            all_jobs.update(new)
            if stop or (not new and not found):
                break
            time.sleep(page_delay)

    elif pg_type == "next_link":
        current = list_url
        for _ in range(max_pages):
            found, err, next_url, stop = scrape_one(current)
            if err and not first_err: first_err = err
            all_jobs.update(found)
            if stop or not next_url: break
            current = next_url
            time.sleep(page_delay)

    if expected_total is not None and len(all_jobs) < expected_total:
        partial_err = f"partial listing: fetched {len(all_jobs)} of {expected_total} jobs"
        first_err = f"{first_err}; {partial_err}" if first_err else partial_err
    return list(all_jobs.values()), first_err
