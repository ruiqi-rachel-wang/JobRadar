from .base import *

# ── TalentBrew / Jibe style pages ─────────────────────────────────────────────

def fetch_talentbrew(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    page_param = scrape.get("page_param", "pg")
    verify_ssl = bool(scrape.get("verify_ssl", True))
    jobs = {}
    for page in range(max_pages):
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
            jobs[jurl] = {"title": title, "url": jurl, "location": loc, "source": "talentbrew", "posted": posted}
        if len(jobs) == before:
            break
    return list(jobs.values()), None
