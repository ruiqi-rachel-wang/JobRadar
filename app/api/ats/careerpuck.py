from .base import *

# ── CareerPuck ────────────────────────────────────────────────────────────────

def fetch_careerpuck(scrape, seen, company):
    """
    CareerPuck public job-board API.
    Endpoint: GET https://api.careerpuck.com/v1/public/job-boards/{board_slug}
    Returns all jobs in one response (no pagination).
    """
    board_slug = scrape.get("board_slug", "")
    if not board_slug:
        # Derive from list_url: https://app.careerpuck.com/job-board/{slug}
        list_url = scrape.get("list_url", "")
        m = re.search(r"/job-board/([^/?#]+)", list_url)
        if m:
            board_slug = m.group(1)
    if not board_slug:
        return [], "no board_slug"

    try:
        r = requests.get(
            f"https://api.careerpuck.com/v1/public/job-boards/{board_slug}",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    raw_jobs = data.get("jobs") or []
    jobs = []
    for j in raw_jobs:
        if j.get("status") != "public":
            continue
        title = norm(j.get("title") or "")
        if not title:
            continue
        jurl = j.get("publicUrl") or j.get("publicShareableUrl") or ""
        if not jurl:
            permalink = j.get("permalink") or ""
            jurl = f"https://app.careerpuck.com/job-board/{board_slug}/job/{permalink}" if permalink else ""
        if not jurl:
            continue

        raw_loc = j.get("location") or ""
        # workplaceType: "remote" takes priority for remote roles
        if j.get("workplaceType") == "remote" and not raw_loc:
            raw_loc = "Remote"
        loc = normalize_location(norm(raw_loc))

        posted = _parse_posted(j.get("postedAt") or j.get("createdAt") or "")
        jobs.append({"title": title, "url": jurl, "location": loc,
                     "source": "careerpuck", "posted": posted})

    return jobs, None
