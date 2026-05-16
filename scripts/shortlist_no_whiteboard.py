import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse


US_RE = re.compile(
    r"\b(USA?|United States|San Francisco|New York|Austin|Seattle|Boston|Chicago|"
    r"Denver|Atlanta|Los Angeles|Portland|Washington|DC|California|Texas|"
    r"Mountain View|Palo Alto|San Jose|San Mateo|Redwood City|Oakland|Raleigh|"
    r"Nashville|Philadelphia|Minneapolis|Boulder|Dallas|Houston|Pittsburgh|"
    r"Salt Lake|Provo|Arlington|Madison|Columbus|CA|NY|WA|MA|IL|CO|GA|FL|VA|NC|OR|TX)\b",
    re.I,
)
REMOTE_RE = re.compile(r"\bRemote\b", re.I)


GOOD_TERMS = {
    "AI": 24,
    "Analytics": 14,
    "API": 12,
    "Cloud": 18,
    "Data": 16,
    "Developer": 10,
    "DevOps": 14,
    "Engineering": 18,
    "Finance": 10,
    "Health": 12,
    "Infrastructure": 18,
    "Labs": 8,
    "Platform": 14,
    "Security": 16,
    "Software": 18,
    "Tech": 10,
}

POPULAR = {
    "Airtable", "Automattic", "Buffer", "Calendly", "CircleCI", "Close",
    "Contentful", "ConvertKit", "DuckDuckGo", "GitLab", "Glassdoor",
    "Heroku", "Hotjar", "LaunchDarkly", "Mattermost", "Mozilla",
    "Mux", "Netlify", "New Relic", "NerdWallet", "Octopus Energy",
    "Omada Health", "Pager", "Qonto", "Remote", "Salesloft", "Segment",
    "Slack", "Sourcegraph", "Splice", "Square", "ThoughtWorks", "Toggl",
    "TrainHeroic", "TrainingPeaks", "UserTesting", "Wikimedia Foundation",
}


def normalize_name(name):
    value = (name or "").lower()
    value = re.sub(r"&", " and ", value)
    value = re.sub(r"\b(inc|inc\.|llc|ltd|corp|corporation|company|co)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_readme(path):
    rows = []
    entry_re = re.compile(r"- \[([^\]]+)\]\(([^)]+)\) \| ([^|]*) \| (.*)")
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        match = entry_re.match(raw.strip())
        if match:
            rows.append({
                "name": match.group(1).strip(),
                "url": match.group(2).strip(),
                "locations": match.group(3).strip(),
                "process": match.group(4).strip(),
            })
    return rows


def parse_no_wb_json(path):
    rows = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        locations = item.get("locations") or []
        if isinstance(locations, list):
            locations_text = " / ".join(str(value) for value in locations if value)
        else:
            locations_text = str(locations)
        rows.append({
            "id": item.get("id"),
            "name": (item.get("name") or "").strip(),
            "url": (item.get("careersUrl") or "").strip(),
            "locations": locations_text,
            "employment_type": item.get("employmentType") or "",
            "process": item.get("interviewProcess") or "",
            "source": "No Whiteboard Jobs",
            "source_url": "https://www.no-wb.org/",
        })
    return rows


def score(row):
    name = row["name"]
    url = row["url"]
    loc = row["locations"]
    process = row["process"]
    text = f"{name} {url} {loc} {process}"
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    value = 0
    if US_RE.search(loc):
        value += 70
    if REMOTE_RE.search(loc):
        value += 25
    if name in POPULAR:
        value += 70
    if any(ats in url.lower() for ats in ("greenhouse", "lever", "ashby", "workday", "workable", "icims", "smartrecruiters", "bamboohr")):
        value += 45
    for term, points in GOOD_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", text, re.I):
            value += points
    if any(word in process.lower() for word in ("pair", "take home", "technical", "system design", "discussion")):
        value += 10
    if any(word in name.lower() for word in ("agency", "consulting", "studio")):
        value -= 10
    if any(word in host for word in ("angel.co", "wellfound.com", "linkedin.com")):
        value -= 30
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="app/data/no_wb_companies_raw.json")
    parser.add_argument("--source-format", choices=["no-wb-json", "hww-readme"], default="no-wb-json")
    parser.add_argument("--original", default="/tmp/original_companies.json")
    parser.add_argument("--current", default="app/companies.json")
    parser.add_argument("--output", default="app/data/no_whiteboard_shortlist_200.json")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--us-target", type=float, default=0.70)
    parser.add_argument("--include-already-added", action="store_true")
    args = parser.parse_args()

    original = json.loads(Path(args.original).read_text(encoding="utf-8"))
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    original_keys = {normalize_name(c.get("name")) for c in original}
    current_keys = {normalize_name(c.get("name")) for c in current}
    rows = parse_no_wb_json(args.source) if args.source_format == "no-wb-json" else parse_readme(args.source)

    candidates = []
    for row in rows:
        key = normalize_name(row["name"])
        if not key or key in original_keys:
            continue
        if key in current_keys and not args.include_already_added:
            continue
        row["score"] = score(row)
        row["us_explicit"] = bool(US_RE.search(row["locations"]))
        row["remote"] = bool(REMOTE_RE.search(row["locations"]))
        row["already_added"] = key in current_keys
        candidates.append(row)

    candidates.sort(key=lambda row: (row["us_explicit"], row["score"], row["remote"], row["name"].lower()), reverse=True)
    us_target = min(args.limit, int(args.limit * args.us_target + 0.999))
    us = [row for row in candidates if row["us_explicit"]]
    rest = [row for row in candidates if not row["us_explicit"]]
    selected = us[:us_target] + rest[: max(0, args.limit - min(len(us), us_target))]
    if len(selected) < args.limit:
        selected.extend([row for row in us[us_target:] if row not in selected][: args.limit - len(selected)])
    selected.sort(key=lambda row: (row["score"], row["us_explicit"], row["remote"], row["name"].lower()), reverse=True)
    selected = selected[:args.limit]

    Path(args.output).write_text(json.dumps(selected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print({
        "candidates": len(candidates),
        "selected": len(selected),
        "us_explicit": sum(1 for row in selected if row["us_explicit"]),
        "remote": sum(1 for row in selected if row["remote"]),
        "already_added": sum(1 for row in selected if row["already_added"]),
        "output": args.output,
    })
    for row in selected[:30]:
        print(f"{row['score']:>3} {'US' if row['us_explicit'] else '--'} {'R' if row['remote'] else '-'} {row['name']} — {row['url']}")


if __name__ == "__main__":
    main()
