#!/usr/bin/env python3
"""Inspect every stored URL and HTTP-check oldest/newest jobs in each URL family.

The report deliberately distinguishes source membership, HTTP verification,
definite failures, and inconclusive browser/challenge responses.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

LOCAL = threading.local()
HOST_LOCK = threading.Lock()
HOST_LIMITS = {}
LANDING_PATHS = {"", "/", "/company", "/about", "/about-us", "/careers", "/jobs", "/careers/all-jobs"}
JOB_QUERY_KEYS = {"jobid", "job_id", "ashby_jid", "gh_jid", "reqid", "requisitionid", "job", "jid", "positionid"}


def normalize_title(value):
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def url_family(value):
    try:
        p = urlsplit(value)
        parts = [x for x in p.path.split("/") if x]
        markers = {"job", "jobs", "positions", "position", "openings", "job-details", "details", "requisitions", "vacancies"}
        # Hosted boards often use /employer/posting-uuid without a "jobs"
        # segment. Including the UUID would make every posting a new family.
        end = next((i + 1 for i, part in enumerate(parts) if part.lower() in markers), min(1, len(parts)))
        return (p.hostname or "") + "/" + "/".join(parts[:end])
    except ValueError:
        return "invalid"


def structural_issue(job):
    value = job.get("url") or ""
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname:
            return "invalid_url"
    except ValueError:
        return "invalid_url"
    keys = {k.lower() for k in parse_qs(p.query)}
    if p.path.rstrip("/") in LANDING_PATHS and not keys.intersection(JOB_QUERY_KEYS):
        return "generic_landing_url"
    if p.hostname.startswith(("api.", "boards-api.")):
        return "api_url_instead_of_job_page"
    if normalize_title(job.get("title")) in {
        "apply", "apply now", "view jobs", "view all jobs", "search jobs", "read more",
        "see details", "learn more", "career opportunities", "open positions", "careers",
        "privacy policy", "terms of use", "join our talent community",
    }:
        return "navigation_title"
    return None


def classify_response(requested, final, status, page_title, visible_text, expected_title):
    if status in {404, 410}:
        return "broken_http"
    if status in {401, 403, 429} or status >= 500:
        return "inconclusive_http"
    if status >= 400:
        return "broken_http"
    lower_title = (page_title or "").lower()
    if re.search(r"page not found|job not found|position not found|404\b|job (?:is )?no longer available", lower_title):
        return "broken_page"
    p = urlsplit(final)
    keys = {k.lower() for k in parse_qs(p.query)}
    if final != requested and p.path.rstrip("/") in LANDING_PATHS and not keys.intersection(JOB_QUERY_KEYS):
        return "redirected_to_landing"
    if re.search(r"just a moment|attention required|access denied|checking your browser|security check", lower_title):
        return "inconclusive_challenge"
    expected = normalize_title(expected_title)
    text = normalize_title(page_title + " " + visible_text)
    if expected and expected in text:
        return "verified_job_title"
    if re.search(r"\b(?:this (?:job|position|role) (?:is no longer available|has been (?:filled|closed))|"
                 r"the (?:job|position) you (?:are looking for|requested) (?:is|could)|"
                 r"sorry.{0,30}(?:job|position).{0,30}(?:no longer|not available))", visible_text, re.I):
        return "expired_page"
    return "reachable_unverified"


def check(job):
    url = job.get("url") or ""
    result = {k: job.get(k) for k in ("id", "company", "title", "url", "first_seen")}
    result["family"] = url_family(url)
    result["structural_issue"] = structural_issue(job)
    if not url.startswith(("https://", "http://")):
        result["status"] = "invalid_url"
        return result
    try:
        host = urlsplit(url).hostname or ""
        with HOST_LOCK:
            limit = HOST_LIMITS.setdefault(host, threading.Semaphore(4))
        session = getattr(LOCAL, "session", None)
        if session is None:
            session = LOCAL.session = requests.Session()
            session.max_redirects = 8
            session.headers.update({"User-Agent": "JobDropBot/0.1 (+https://github.com/ruiqi-rachel-wang/JobRadar)"})
        with limit, session.get(url, timeout=(8, 18), stream=True) as response:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= 2_000_000:
                    break
            html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(html, "lxml")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            for node in soup.select("script,style,noscript,template"):
                node.decompose()
            visible = soup.get_text(" ", strip=True)
            result.update({
                "http_status": response.status_code, "final_url": response.url,
                "page_title": title, "redirects": [r.url for r in response.history],
                "status": classify_response(url, response.url, response.status_code, title, visible, job.get("title") or ""),
            })
    except requests.TooManyRedirects as exc:
        result.update(status="redirect_loop", error=str(exc)[:400])
    except Exception as exc:
        result.update(status="inconclusive_network", error=f"{type(exc).__name__}: {exc}"[:400])
    result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


def inventory(jobs_path, directory):
    families, metrics, issues = {}, defaultdict(Counter), []
    total = 0
    with gzip.open(jobs_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            total += 1
            company = row["company"]
            metrics[company]["open_jobs"] += 1
            issue = structural_issue(row)
            if issue:
                metrics[company][issue] += 1
                issues.append({k: row.get(k) for k in ("id", "company", "title", "url") } | {"issue": issue})
            key = (company, url_family(row.get("url") or ""))
            group = families.setdefault(key, [])
            group.append(row)
            # Two newest and one oldest, in every company/URL family.
            group.sort(key=lambda x: x.get("first_seen") or "")
            if len(group) > 3:
                del group[1:-2]
    selected = {row["id"]: row for group in families.values() for row in group}
    (directory / "url_inventory.json").write_text(json.dumps({
        "stored_urls_inspected": total, "families": len(families), "http_selected": len(selected),
        "companies": {k: dict(v) for k, v in metrics.items()}, "structural_issues": issues,
    }, ensure_ascii=False))
    return list(selected.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    directory = Path(args.output)
    links = directory / "links"
    links.mkdir(parents=True, exist_ok=True)
    selected = inventory(args.jobs, directory)
    pending, counts = [], Counter()
    for row in selected:
        path = links / (hashlib.sha256((row["id"] + "|" + row["url"]).encode()).hexdigest()[:24] + ".json")
        if path.exists():
            counts[json.loads(path.read_text())["status"]] += 1
        else:
            pending.append((row, path))
    cached = len(selected) - len(pending)
    print(json.dumps({"selected": len(selected), "cached": cached, "pending": len(pending)}), flush=True)
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(check, row): path for row, path in pending}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            futures[future].write_text(json.dumps(result, ensure_ascii=False))
            counts[result["status"]] += 1
            if done % 100 == 0 or done == len(pending):
                print(json.dumps({"done": cached + done, "total": len(selected), "statuses": dict(counts),
                                  "elapsed_s": round(time.monotonic() - started, 1)}), flush=True)


if __name__ == "__main__":
    main()
