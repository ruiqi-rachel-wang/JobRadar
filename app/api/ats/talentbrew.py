from .base import *

# ── TalentBrew / Jibe style pages ─────────────────────────────────────────────

def fetch_talentbrew(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    page_param = scrape.get("page_param", "pg")
    verify_ssl = bool(scrape.get("verify_ssl", True))
    fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
    refresh_experience = bool(scrape.get("refresh_experience"))
    max_desc = int(scrape.get("max_desc_details", 300))
    detail_workers = int(scrape.get("detail_workers", 4))
    jobs = {}

    def _jsonld_values(data):
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                yield item
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)

    def _jsonld_location(value):
        locs = []
        values = value if isinstance(value, list) else [value]
        for loc in values:
            raw = ""
            if isinstance(loc, dict):
                address = loc.get("address") or {}
                if isinstance(address, dict):
                    parts = [
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    ]
                    raw = ", ".join(str(p) for p in parts if p)
                raw = raw or loc.get("name") or ""
            elif isinstance(loc, str):
                raw = loc
            candidate = normalize_location(norm(raw))
            if candidate and candidate != "Multiple Locations" and candidate.lower() not in {x.lower() for x in locs}:
                locs.append(candidate)
        return "; ".join(locs)

    def _detail_location_from_text(text):
        locs = []
        patterns = [
            r'\b(?:Location|Locations|Job Location|Job Locations|Work Location)\s*[:\-]\s*(.+?)(?=\s+(?:Job ID|Req|Posted|Apply|Share|Overview|Responsibilities|Qualifications|$))',
            r'\bSame job available in\s+\d+\s+locations?\s+(.+?)(?=\s+(?:Apply|Share|Back|$))',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if not m:
                continue
            for part in re.split(r'\s*[;|•]\s*|\s{2,}', m.group(1)):
                candidate = normalize_location(norm(part))
                if candidate and candidate != "Multiple Locations" and candidate.lower() not in {x.lower() for x in locs}:
                    locs.append(candidate)
            if locs:
                return "; ".join(locs)
        return ""

    def _fetch_detail(job):
        try:
            r = requests.get(job["url"], headers=HEADERS, timeout=20, verify=verify_ssl)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            posted = ""
            loc = ""
            desc = ""
            for raw in soup.find_all("script", {"type": re.compile(r"ld\+json", re.I)}):
                try:
                    data = json.loads(raw.get_text(strip=True))
                except Exception:
                    continue
                for obj in _jsonld_values(data):
                    if not isinstance(obj, dict):
                        continue
                    posted = posted or _parse_posted(obj.get("datePosted") or obj.get("postedDate") or "")
                    loc = loc or _jsonld_location(obj.get("jobLocation"))
                    if not desc and obj.get("description"):
                        desc = obj.get("description") or ""
            full_text = norm(soup.get_text(" ", strip=True))
            loc = loc or _detail_location_from_text(full_text)
            posted = posted or extract_posted_from_text(full_text)
            if not desc:
                for sel in ('.job-description', '#job-description', '[class*="job-description"]',
                            '[class*="jobDescription"]', 'main', 'article'):
                    el = soup.select_one(sel)
                    if el:
                        raw_html = str(el)
                        if len(normalize_description(raw_html) or "") > 150:
                            desc = raw_html
                            break
            return job, {
                "location": loc,
                "posted": posted,
                "description": normalize_description(desc),
                "description_raw": desc,
            }
        except Exception:
            return job, {"location": "", "posted": "", "description": ""}
    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return list(jobs.values()), "company scan deadline reached (partial results)"
        purl = list_url if page == 0 else make_paged_url(list_url, {"param": page_param, "start": page + 1}, 0)
        try:
            r = requests.get(purl, headers=HEADERS, timeout=25, verify=verify_ssl)
            r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)
        soup = BeautifulSoup(r.text, "lxml")
        before = len(jobs)
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            text = norm(a.get_text(" ", strip=True))
            if not text:
                continue
            if not re.search(r"/job/|/jobs/", href, re.I):
                continue
            if re.search(r"career areas|saved jobs|sign in|search jobs|talent community|events|login\b|^apply\b|^view job$", text, re.I):
                continue
            # Skip "Apply Now" / "View Job" duplicate links that share the job URL
            if text.lower() in {"apply now", "apply", "view job", "view details", "learn more"}:
                continue
            jurl = urljoin(r.url, href)
            # Don't overwrite a previously found entry for this URL (avoid "Apply Now" duplicates)
            if jurl in jobs:
                continue
            # TalentBrew job URLs have pattern /job/{loc}/{title}/{org_id}/{job_id}
            # Nav links have fewer segments — require at least job_id (numeric) at the end
            path_check = urlparse(jurl).path.strip('/').split('/')
            if path_check and path_check[0] == 'job' and len(path_check) < 4:
                continue

            # TalentBrew URL pattern: /job/{location-slug}/{title-slug}/{org_id}/{job_id}
            # The <a> text often has "{City, Country} {Title}" concatenated.
            # Extract a clean title from the URL slug when possible.
            path_parts = urlparse(jurl).path.strip('/').split('/')
            title = text
            loc_from_url = ""
            if path_parts and path_parts[0] == 'job' and len(path_parts) >= 3:
                title_slug = path_parts[2]
                title_from_url = re.sub(r'-+', ' ', title_slug).strip().title()
                if title_from_url and len(title_from_url) >= 5:
                    title = title_from_url
                if len(path_parts) >= 2:
                    loc_slug = path_parts[1]
                    loc_from_url = normalize_location(norm(re.sub(r'-+', ' ', loc_slug).strip()))

            row = a.find_parent("li") or a.find_parent("tr") or a.find_parent("div")
            parent_text = norm(row.get_text(" ", strip=True)) if row else text
            loc_text = parent_text.replace(text, " ", 1)
            loc = extract_location_from_text(loc_text)
            # TalentBrew anchor text often embeds "{Title} {City, ST} {metadata}".
            # When loc_text is empty (anchor == parent), strip the title from the
            # anchor text and extract location from the remainder.
            if not loc and not loc_text.strip() and title:
                # Build a flexible pattern that matches title words ignoring punctuation
                title_words = title.split()
                t_pat = r'[\s,.-]*'.join(re.escape(w) for w in title_words)
                m_title = re.search(t_pat, text, re.I)
                if m_title:
                    remainder = norm(text[m_title.end():])
                    loc = extract_location_from_text(remainder)
            loc = loc or loc_from_url
            posted = extract_posted_from_text(parent_text)
            ats_id = path_parts[-1] if path_parts and path_parts[-1].isdigit() else ""
            jobs[jurl] = {
                "title": title,
                "url": jurl,
                "ats_id": ats_id,
                "location": loc,
                "location_raw": loc_text or loc_from_url,
                "locations_raw": [loc] if loc else [],
                "source": "talentbrew",
                "posted": posted,
                "posted_raw": posted,
            }
        if len(jobs) == before:
            break
    detail_targets = []
    if fetch_details:
        for job in jobs.values():
            jid = make_id(company, job.get("url", ""), job.get("title", ""))
            existing = seen.get(jid) if isinstance(seen, dict) else None
            existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
            needs_location = not job.get("location") or re.search(r'\+\s*\d+\s+more|\bmultiple locations?\b', job.get("location") or "", re.I)
            if refresh_experience or needs_location or not job.get("posted") or not existing_has_desc:
                detail_targets.append(job)
    if detail_targets[:max_desc]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
            for job, detail in ex.map(_fetch_detail, detail_targets[:max_desc]):
                if detail.get("location"):
                    job["location"] = detail["location"]
                    job["locations_raw"] = [
                        part.strip() for part in detail["location"].split(";") if part.strip()
                    ]
                if detail.get("posted") and not job.get("posted"):
                    job["posted"] = detail["posted"]
                if detail.get("description"):
                    job["description"] = detail["description"]
                    job["description_raw"] = detail.get("description_raw")
    return list(jobs.values()), None
