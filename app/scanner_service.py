"""
Scan orchestration — two modes, one codebase.

  fresh (default)
      Collects only new jobs. Stops per-company as soon as a full page of
      already-known jobs is encountered. Never marks jobs as closed.
      Run this frequently (e.g. every 2–4 h).

  full
      Comprehensive audit. Scans every page regardless of seen status,
      updates changed fields (location, exp, skills…) and soft-deletes jobs
      that disappeared from the source (is_open=0). Run once a day.

Both modes share the same fetchers, enrichment pipeline, and DB writes.
"""
import concurrent.futures
import logging
import os
import threading
import time
from datetime import datetime
from datetime import timedelta

try:
    from .api.apis import _API_MODES, fetch_jobs
    from .api.job_data import (
        delete_expired_jobs, get_app_state, load_seen, set_app_state, upsert_jobs,
    )
    from .api.utils import make_id
    from .scan_store import (
        create_company_scan_status, create_scan_run,
        finish_company_scan_status, finish_scan_run_from_statuses,
        reset_unfinished_company_statuses, scan_company_status_map,
        scan_run_totals, start_company_scan_status, update_scan_counts,
    )
except ImportError:
    from api.apis import _API_MODES, fetch_jobs
    from api.job_data import (
        delete_expired_jobs, get_app_state, load_seen, set_app_state, upsert_jobs,
    )
    from api.utils import make_id
    from scan_store import (
        create_company_scan_status, create_scan_run,
        finish_company_scan_status, finish_scan_run_from_statuses,
        reset_unfinished_company_statuses, scan_company_status_map,
        scan_run_totals, start_company_scan_status, update_scan_counts,
    )

# Guards concurrent writes to the shared `seen` dict across threads.
_seen_lock = threading.Lock()

# Thread-pool sizes. Workday gets its own small lane because full scans may
# fetch thousands of detail pages for experience extraction; letting those fill
# the general API pool makes the whole scan look frozen.
_API_WORKERS     = 12
_WORKDAY_WORKERS = 2
_BROWSER_WORKERS = 2

# A single wedged company must not block the whole scheduler for days. Fresh
# scans should be quick; full scans get a longer budget for large ATS sources.
_FRESH_COMPANY_TIMEOUT_S = int(os.environ.get("JOBRADAR_FRESH_COMPANY_TIMEOUT_SECONDS", "1200"))
_FULL_COMPANY_TIMEOUT_S  = int(os.environ.get("JOBRADAR_FULL_COMPANY_TIMEOUT_SECONDS", "7200"))


# ── Public entry point ────────────────────────────────────────────────────────

def run_scan(companies, scan_type="fresh", progress=None, on_scan_run_id=None,
             should_cancel=None, resume_scan_run_id=None):
    """Scan a list of companies and return a summary dict.

    Args:
        companies:  Company config dicts from companies.json.
        scan_type:  "fresh" | "full"  (see module docstring).
        progress:   Optional list to update in-place so Flask can stream
                    per-company status to the browser.

    Returns a dict with keys:
        scan_run_id, scan_type, status, progress, results,
        new_count, total_jobs, failed_companies, elapsed_s
    """
    companies   = [
        c for c in companies
        if c.get("scan_enabled", True) is not False or c.get("policy")
    ]
    is_fresh    = scan_type != "full"
    seen        = load_seen()
    now_str     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t_start     = time.monotonic()
    summaries   = []        # per-company summary dicts
    total_jobs  = 0
    total_new   = 0

    resumed_statuses = {}
    if resume_scan_run_id:
        scan_run_id = resume_scan_run_id
        reset_unfinished_company_statuses(scan_run_id)
        resumed_statuses = scan_company_status_map(scan_run_id)
        companies = [
            c for c in companies
            if (resumed_statuses.get(c["name"]) or {}).get("status") in {"pending", "scanning"}
        ]
    else:
        scan_run_id = create_scan_run(scan_type=scan_type, total_companies=len(companies))
    if on_scan_run_id:
        on_scan_run_id(scan_run_id)

    if progress is None:
        progress = [
            {
                "company": c["name"],
                "mode":    (c.get("scrape") or {}).get("mode", "html"),
                "status":  "pending",
                "count":   0,
                "new":     0,
            }
            for c in companies
        ]

    # Pre-create DB status rows so the polling endpoint can return them immediately.
    status_ids = {}
    for i, company in enumerate(companies):
        row = resumed_statuses.get(company["name"])
        status_ids[i] = row["id"] if row else create_company_scan_status(scan_run_id, company["name"])

    # ── Per-company helpers (closures so they share outer state) ──────────────

    def _fetch_one(i, company):
        """Call the appropriate ATS fetcher for one company."""
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        try:
            start_company_scan_status(status_ids[i])
            blocked_until = _blocked_until(company["name"])
            if blocked_until and blocked_until > datetime.now():
                return (
                    i, company, [],
                    f"skipped after recent 403 until {blocked_until.strftime('%Y-%m-%d %H:%M:%S')}",
                    round(time.monotonic() - t0, 1),
                )
            raw, err = fetch_jobs(company, seen, scan_type=scan_type)
            if err and _is_forbidden_error(err):
                _remember_forbidden(company["name"])
            return i, company, raw or [], err, round(time.monotonic() - t0, 1)
        except Exception as exc:
            return i, company, [], str(exc), round(time.monotonic() - t0, 1)

    def _commit_one(i, company, raw, err, duration):
        """Persist results for one company and update shared state."""
        nonlocal total_jobs, total_new
        name      = company["name"]
        cats      = company.get("categories", [])
        new_count = 0
        rows      = []

        with _seen_lock:
            for job in raw:
                jid    = make_id(name, job.get("url", ""), job.get("title", ""))
                is_new = jid not in seen

                if is_new:
                    new_count += 1
                    seen[jid] = _make_seen_entry(job, name, now_str)
                else:
                    _refresh_seen_entry(seen[jid], job)

                rows.append({
                    **job,
                    "id":         jid,
                    "company":    name,
                    "categories": cats,
                    "is_new":     is_new,
                    "first_seen": seen[jid]["first_seen"],
                    # Prefer freshly scraped date; fall back to what we cached.
                    "posted":     job.get("posted") or seen[jid].get("posted", ""),
                })

        if rows:
            upsert_jobs(rows)
            # Full scan only: soft-delete jobs that vanished from the source.
            if not is_fresh and raw:
                delete_expired_jobs(name, [r["id"] for r in rows])

        entry = _make_summary(name, progress[i]["mode"], raw, err, duration, new_count)
        progress[i].update(entry)
        summaries.append(dict(entry))
        total_jobs += len(raw)
        total_new += new_count

        finish_company_scan_status(
            status_ids[i],
            status="success" if not err else "failed",
            jobs_found=len(raw),
            new_jobs_found=new_count,
            error_message=err,
        )
        update_scan_counts(scan_run_id, len(summaries))

    # ── Run fetches concurrently ──────────────────────────────────────────────

    workday_cos = [(i, c) for i, c in enumerate(companies)
                   if (c.get("scrape") or {}).get("mode", "html") == "workday"]
    api_cos     = [(i, c) for i, c in enumerate(companies)
                   if (c.get("scrape") or {}).get("mode", "html") in _API_MODES
                   and (c.get("scrape") or {}).get("mode", "html") != "workday"]
    browser_cos = [(i, c) for i, c in enumerate(companies)
                   if (c.get("scrape") or {}).get("mode", "html") not in _API_MODES]

    def _cancel_requested():
        return bool(should_cancel and should_cancel())

    def _mark_cancelled():
        for i, _company in enumerate(companies):
            if progress[i].get("status") in {"pending", "scanning"}:
                progress[i]["status"] = "cancelled"
                finish_company_scan_status(
                    status_ids[i],
                    status="cancelled",
                    error_message="scan cancelled",
                )

    def _company_timeout_s():
        return _FULL_COMPANY_TIMEOUT_S if scan_type == "full" else _FRESH_COMPANY_TIMEOUT_S

    def _submit_next(executor, iterator, futures):
        try:
            i, c = next(iterator)
        except StopIteration:
            return
        futures[executor.submit(_fetch_one, i, c)] = {
            "executor": executor,
            "iterator": iterator,
            "index": i,
            "company": c,
            "started": time.monotonic(),
        }

    def _mark_timed_out(fut, meta):
        fut.cancel()
        i = meta["index"]
        company = meta["company"]
        duration = round(time.monotonic() - meta["started"], 1)
        timeout = _company_timeout_s()
        err = f"timed out after {timeout}s"
        logging.warning("scan: %s timed out after %.1fs", company.get("name"), duration)
        try:
            _commit_one(i, company, [], err, duration)
        except Exception as exc:
            logging.warning("scan: failed to record timeout for %s: %s", company.get("name"), exc)

    def _run_bounded(api_ex, workday_ex, pw_ex):
        """Keep only active workers queued so progress and memory stay honest."""
        futures = {}
        api_iter = iter(api_cos)
        workday_iter = iter(workday_cos)
        pw_iter = iter(browser_cos)
        for _ in range(min(_API_WORKERS, len(api_cos))):
            _submit_next(api_ex, api_iter, futures)
        for _ in range(min(_WORKDAY_WORKERS, len(workday_cos))):
            _submit_next(workday_ex, workday_iter, futures)
        for _ in range(min(_BROWSER_WORKERS, len(browser_cos))):
            _submit_next(pw_ex, pw_iter, futures)

        while futures:
            if _cancel_requested():
                for fut in futures:
                    fut.cancel()
                _mark_cancelled()
                return "cancelled"
            timeout_s = _company_timeout_s()
            for fut, meta in list(futures.items()):
                if fut.done():
                    continue
                if time.monotonic() - meta["started"] <= timeout_s:
                    continue
                futures.pop(fut)
                _mark_timed_out(fut, meta)
                if not _cancel_requested():
                    _submit_next(meta["executor"], meta["iterator"], futures)
            done, _pending = concurrent.futures.wait(
                futures,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                continue
            for fut in done:
                meta = futures.pop(fut)
                if fut.cancelled():
                    continue
                try:
                    _commit_one(*fut.result())
                except Exception as exc:
                    # One company's failure (e.g. a transient DB lock) must never
                    # abort the whole scan — log it and keep going.
                    logging.warning("scan: skipped a company after commit error: %s", exc)
                if not _cancel_requested():
                    _submit_next(meta["executor"], meta["iterator"], futures)
        return None

    try:
        api_ex     = concurrent.futures.ThreadPoolExecutor(max_workers=_API_WORKERS)
        workday_ex = concurrent.futures.ThreadPoolExecutor(max_workers=_WORKDAY_WORKERS)
        pw_ex      = concurrent.futures.ThreadPoolExecutor(max_workers=_BROWSER_WORKERS)
        try:
            cancelled = _run_bounded(api_ex, workday_ex, pw_ex)
        finally:
            for ex in (api_ex, workday_ex, pw_ex):
                ex.shutdown(wait=False, cancel_futures=True)

        elapsed = round(time.monotonic() - t_start, 1)
        if cancelled:
            status = "cancelled"
        else:
            totals = scan_run_totals(scan_run_id)
            status = "partial_success" if totals["failed_companies"] else "success"

        finish_scan_run_from_statuses(scan_run_id, status=status)
        return {
            "scan_run_id":      scan_run_id,
            "scan_type":        scan_type,
            "status":           status,
            "progress":         progress,
            "results":          [],
            "new_count":        total_new,
            "total_jobs":       total_jobs,
            "failed_companies": scan_run_totals(scan_run_id)["failed_companies"],
            "elapsed_s":        elapsed,
        }

    except Exception as exc:
        finish_scan_run_from_statuses(scan_run_id, status="failed", error_message=str(exc))
        raise


# ── Private helpers ───────────────────────────────────────────────────────────

def _blocked_until(company_name):
    raw = get_app_state(f"crawler_403_until:{company_name}")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _remember_forbidden(company_name):
    hours = int(__import__("os").environ.get("JOBRADAR_403_BACKOFF_HOURS", "168"))
    until = datetime.now() + timedelta(hours=max(1, hours))
    set_app_state(f"crawler_403_until:{company_name}", until.strftime("%Y-%m-%d %H:%M:%S"))

def _is_forbidden_error(err):
    text = str(err or "").lower()
    return "403" in text or "forbidden" in text


def _make_seen_entry(job, company_name, now_str):
    """Build the initial cache entry for a newly discovered job."""
    return {
        "first_seen":  now_str,
        "company":     company_name,
        "title":       job.get("title", ""),
        "url":         job.get("url", ""),
        "location":    job.get("location", ""),
        "source":      job.get("source", ""),
        "posted":      job.get("posted", ""),
        "exp_min":     job.get("exp_min"),
        "exp_max":     job.get("exp_max"),
        "exp_label":   job.get("exp_label", ""),
        "exp_source":  job.get("exp_source", ""),
        "skills":      job.get("skills") or [],
        "keywords":    job.get("keywords") or [],
        "has_description": bool((job.get("description") or "").strip()),
    }


def _refresh_seen_entry(entry, job):
    """Overwrite stale cached fields with newer data from a re-scan."""
    if job.get("location"):  entry["location"]  = job["location"]
    if job.get("posted"):    entry["posted"]    = job["posted"]
    if job.get("source"):    entry["source"]    = job["source"]
    if job.get("exp_label"):
        entry.update({
            "exp_min":    job.get("exp_min"),
            "exp_max":    job.get("exp_max"),
            "exp_label":  job.get("exp_label", ""),
            "exp_source": job.get("exp_source", ""),
        })
    if job.get("skills"):
        entry["skills"] = job["skills"]
    if job.get("keywords"):
        entry["keywords"] = job["keywords"]
    if job.get("description"):
        entry["has_description"] = True


def _make_summary(name, mode, raw, err, duration, new_count):
    """Build the per-company status dict used by the progress list and scan log."""
    entry = {
        "company":    name,
        "mode":       mode,
        "status":     "error" if err else "done",
        "count":      len(raw),
        "new":        new_count,
        "duration_s": duration,
    }
    if err:
        entry["error"] = err
    return entry
