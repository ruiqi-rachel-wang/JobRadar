from .base import *

# ── HTML ──────────────────────────────────────────────────────────────────────

def fetch_html(scrape, seen, company):
    """Universal HTML scraper for simple static career pages."""
    list_url    = scrape.get("list_url","")
    include     = scrape.get("include","")
    title_param = scrape.get("title_param","")
    stop_on_seen = bool(scrape.get("stop_on_seen", False))
    use_reader  = bool(scrape.get("reader", False))
    pg_cfg      = scrape.get("pagination") or {"type":"none"}
    pg_type     = pg_cfg.get("type","none")
    max_pages   = int(pg_cfg.get("hard_max_pages") or scrape.get("hard_max_pages") or 100)

    if not list_url: return [], "no list_url"
    if not include:  return [], "no include pattern"

    visited: set = set()

    def scrape_one(page_url):
        try:
            fetch_url = "https://r.jina.ai/" + page_url if use_reader else page_url
            r = requests.get(fetch_url, headers=HEADERS, timeout=25)
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
                    if abs_url in visited:
                        continue
                    visited.add(abs_url)
                    title = norm(re.sub(r"!\[[^\]]*\]", " ", title))
                    if not title or re.search(r"^(apply|view job|search jobs|saved jobs|learn more)$", title, re.I):
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
                        "location": extract_location_from_text(nearby_clean) or extract_location_from_text(nearby),
                        "source": "html",
                        "posted": extract_posted_from_text(nearby),
                    }
                if stop:
                    break
            return found, None, None, stop

        soup  = BeautifulSoup(r.text, "lxml")
        found = {}
        stop  = False

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#","javascript:","mailto:")):
                continue
            abs_url = urljoin(page_url, href)
            if not has_include(abs_url, include):
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
                seg   = urlparse(abs_url).path.rstrip("/").split("/")[-1]
                title = re.sub(r"[-_]"," ", seg).strip()
            if not title or len(title) < 4:
                continue
            _NAV = {"view all jobs", "view all", "all jobs", "apply", "view job",
                    "search jobs", "saved jobs", "learn more", "see all openings",
                    "see all jobs", "open positions", "view openings"}
            if title.lower().strip() in _NAV:
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

            # When parent_text is very large (multi-card container), skip it and use
            # the anchor-embedded location instead.
            loc_candidate = "" if len(parent_text) > 500 else extract_location_from_text(parent_text)
            # Don't use the job title itself as the location
            if loc_candidate and loc_candidate.lower() == title.lower():
                loc_candidate = ""
            # Prefer location extracted from the anchor tag (Freshteam-style embeds)
            if not loc_candidate and _loc_in_anchor:
                loc_candidate = _loc_in_anchor
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
            time.sleep(0.35)

    elif pg_type == "next_link":
        current = list_url
        for _ in range(max_pages):
            found, err, next_url, stop = scrape_one(current)
            if err and not first_err: first_err = err
            all_jobs.update(found)
            if stop or not next_url: break
            current = next_url
            time.sleep(0.35)

    return list(all_jobs.values()), first_err
