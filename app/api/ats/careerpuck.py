from .base import *
import html

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
        desc_html = html.unescape(j.get("content") or "")
        desc = normalize_description(desc_html)
        jobs.append({
            "title": title,
            "url": jurl,
            "ats_id": j.get("id") or j.get("_id") or j.get("permalink"),
            "apply_url": j.get("applicationUrl") or jurl,
            "location": loc,
            "location_raw": raw_loc,
            "locations_raw": [raw_loc] if raw_loc else [],
            "remote_mode": j.get("workplaceType"),
            "source": "careerpuck",
            "posted": posted,
            "posted_at": j.get("postedAt") or j.get("createdAt"),
            "posted_raw": j.get("postedAt") or j.get("createdAt"),
            "description": desc,
            "description_raw": desc_html,
            "employment_type_raw": j.get("employmentType"),
            "department": j.get("department"),
            "raw_data": {
                key: j.get(key)
                for key in ("workplaceType", "location", "employmentType", "department")
                if j.get(key) is not None
            },
        })

    return jobs, None
