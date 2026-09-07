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
        delete_app_state, delete_expired_jobs, get_app_state, load_seen,
        set_app_state, upsert_jobs,
    )
    from .api.utils import make_id
    from .scan_store import (
        create_company_scan_statuses, create_scan_run,
        finish_company_scan_status, finish_scan_run_from_statuses,
        reset_unfinished_company_statuses, scan_company_status_map,
        scan_run_totals, start_company_scan_status, update_scan_counts,
    )
except ImportError:
    from api.apis import _API_MODES, fetch_jobs
    from api.job_data import (
        delete_app_state, delete_expired_jobs, get_app_state, load_seen,
        set_app_state, upsert_jobs,
    )
    from api.utils import make_id
    from scan_store import (
        create_company_scan_statuses, create_scan_run,
        finish_company_scan_status, finish_scan_run_from_statuses,
        reset_unfinished_company_statuses, scan_company_status_map,
        scan_run_totals, start_company_scan_status, update_scan_counts,
    )

# Guards concurrent writes to the shared `seen` dict across threads.
_seen_lock = threading.Lock()

# Thread-pool sizes (env-overridable). Four lanes so one slow source type
# can't starve the rest:
#   api      — one-shot / lightly paginated JSON APIs (greenhouse, ashby, lever…)
#   workday  — heavy pagination, kept separate so it can't fill the API pool
#   html     — requests+BeautifulSoup page scrapes (no browser, cheap)
#   browser  — real Playwright browsers only; kept narrow for memory
_API_WORKERS     = int(os.environ.get("JOBRADAR_API_WORKERS", "24"))
_WORKDAY_WORKERS = int(os.environ.get("JOBRADAR_WORKDAY_WORKERS", "4"))
_HTML_WORKERS    = int(os.environ.get("JOBRADAR_HTML_WORKERS", "8"))
_BROWSER_WORKERS = int(os.environ.get("JOBRADAR_BROWSER_WORKERS", "2"))

# A single wedged company must not block the whole scheduler for days. Fresh
# scans should be quick; full scans get a longer budget for large ATS sources.
_FRESH_COMPANY_TIMEOUT_S = int(os.environ.get("JOBRADAR_FRESH_COMPANY_TIMEOUT_SECONDS", "1200"))
_FULL_COMPANY_TIMEOUT_S  = int(os.environ.get("JOBRADAR_FULL_COMPANY_TIMEOUT_SECONDS", "7200"))


# ── Public entry point ────────────────────────────────────────────────────────

def run_scan(companies, scan_type="fresh", progress=None, on_scan_run_id=None,
             should_cancel=None, resume_scan_run_id=None, timing=None,
             scheduled_for="", send_alerts_after=False):
    """Scan a list of companies and return a summary dict.

    Args:
        companies:  Company config dicts from companies.json.
        scan_type:  "fresh" | "full"  (see module docstring).
        progress:   Optional list to update in-place so Flask can stream
                    per-company status to the browser.

    Returns a dict with keys:
        scan_run_id, scan_type, status, progress, results,
        new_count, total_jobs, failed_companies, warning_companies, elapsed_s
    """
    t_start = time.monotonic()
    phase_started = t_start
    phase_raw = {}
    work_raw = {
        "status_start": 0.0,
        "fetch_enrich": 0.0,
        "prepare_rows": 0.0,
        "save_jobs": 0.0,
        "status_finish": 0.0,
        "progress_write": 0.0,
    }
    if timing is None:
        timing = {}
    timing.clear()
    timing.update({"phase": "select_companies", "steps_s": {}, "work_s": {}})

    def _finish_phase(name, next_phase=None):
        nonlocal phase_started
        now = time.monotonic()
        phase_raw[name] = now - phase_started
        phase_started = now
        timing["steps_s"] = {key: round(value, 3) for key, value in phase_raw.items()}
        timing["elapsed_s"] = round(now - t_start, 3)
        timing["phase"] = next_phase or name

    def _publish_work():
        total = sum(work_raw.values())
        timing["work_s"] = {key: round(value, 3) for key, value in work_raw.items()}
        timing["work_percent"] = {
            key: round((value / total * 100) if total else 0.0, 1)
            for key, value in work_raw.items()
        }

    companies   = [
        c for c in companies
        if c.get("scan_enabled", True) is not False
    ]
    is_fresh    = scan_type != "full"
    _finish_phase("select_companies", "load_seen")
    seen        = load_seen()
    seen_by_url = {
        (entry.get("company"), entry.get("url")): entry
        for entry in seen.values()
        if isinstance(entry, dict) and entry.get("company") and entry.get("url")
    }
    _finish_phase("load_seen", "create_scan_run")
    now_str     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        scan_run_id = create_scan_run(
            scan_type=scan_type,
            total_companies=len(companies),
            scheduled_for=scheduled_for,
            send_alerts_after=send_alerts_after,
        )
    if on_scan_run_id:
        on_scan_run_id(scan_run_id)
    _finish_phase("create_scan_run", "create_status_rows")

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
    status_ids = {
        i: resumed_statuses[company["name"]]["id"]
        for i, company in enumerate(companies)
        if company["name"] in resumed_statuses
    }
    missing_names = [
        company["name"] for company in companies
        if company["name"] not in resumed_statuses
    ]
    created_status_ids = create_company_scan_statuses(scan_run_id, missing_names)
    for i, company in enumerate(companies):
        if i not in status_ids:
            status_ids[i] = created_status_ids[company["name"]]
    _finish_phase("create_status_rows", "scan_companies")

    # ── Per-company helpers (closures so they share outer state) ──────────────

    def _fetch_one(i, company):
        """Call the appropriate ATS fetcher for one company."""
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        work = {}
        try:
            status_t0 = time.monotonic()
            try:
                start_company_scan_status(status_ids[i])
            except Exception as exc:
                # Bookkeeping write only — a transient SQLite lock here must
                # not fail the company's whole scan.
                logging.warning("scan: status write failed for %s: %s", company.get("name"), exc)
            work["status_start"] = time.monotonic() - status_t0
            fetch_t0 = time.monotonic()
            blocked_until = _blocked_until(company)
            if blocked_until and blocked_until > datetime.now():
                work["fetch_enrich"] = time.monotonic() - fetch_t0
                return (
                    i, company, [],
                    f"skipped after recent 403 until {blocked_until.strftime('%Y-%m-%d %H:%M:%S')}",
                    round(time.monotonic() - t0, 1),
                    work,
                )
            deadline = t0 + _company_timeout_s()
            raw, err = fetch_jobs(
                company,
                seen,
                scan_type=scan_type,
                deadline=deadline,
            )
            if err and _is_forbidden_error(err):
                _remember_forbidden(company)
            elif not err:
                _clear_forbidden(company)
            work["fetch_enrich"] = time.monotonic() - fetch_t0
            return i, company, raw or [], err, round(time.monotonic() - t0, 1), work
        except Exception as exc:
            work.setdefault("fetch_enrich", time.monotonic() - t0)
            return i, company, [], str(exc), round(time.monotonic() - t0, 1), work

    def _commit_one(i, company, raw, err, duration, work=None):
        """Persist results for one company and update shared state."""
        nonlocal total_jobs, total_new
        work = work or {}
        for key in ("status_start", "fetch_enrich"):
            work_raw[key] += work.get(key, 0.0)
        name      = company["name"]
        cats      = company.get("categories", [])
        new_count = 0
        rows      = []

        prepare_t0 = time.monotonic()
        with _seen_lock:
            for job in raw:
                stable_id = make_id(
                    name,
                    job.get("url", ""),
                    job.get("title", ""),
                    job.get("source", ""),
                    job.get("ats_id", ""),
                )
                legacy_id = make_id(
                    name, job.get("url", ""), job.get("title", "")
                )
                existing = _find_existing_seen_job(
                    seen,
                    seen_by_url,
                    stable_id=stable_id,
                    legacy_id=legacy_id,
                    company=name,
                    url=job.get("url"),
                )
                jid = (
                    existing.get("_stored_id")
                    if isinstance(existing, dict) and existing.get("_stored_id")
                    else stable_id
                )
                is_new = existing is None
                fts_changed = bool(
                    is_new
                    or (job.get("title") or "") != (existing.get("title") or "")
                    or (
                        job.get("location")
                        and job.get("location") != (existing.get("location") or "")
                    )
                )

                if is_new:
                    new_count += 1
                    seen[jid] = _make_seen_entry(job, name, now_str)
                    seen[jid]["_stored_id"] = jid
                else:
                    _refresh_seen_entry(existing, job)
                    seen[jid] = existing
                seen[stable_id] = seen[jid]
                seen[legacy_id] = seen[jid]
                if job.get("url"):
                    seen_by_url[(name, job.get("url"))] = seen[jid]

                rows.append({
                    **job,
                    "id":         jid,
                    "company":    name,
                    "categories": cats,
                    "is_new":     is_new,
                    "fts_changed": fts_changed,
                    "first_seen": seen[jid]["first_seen"],
                    # Prefer freshly scraped date; fall back to what we cached.
                    "posted":     job.get("posted") or seen[jid].get("posted", ""),
                })
        work_raw["prepare_rows"] += time.monotonic() - prepare_t0

        save_t0 = time.monotonic()
        if rows:
            upsert_jobs(rows)
        # Full scan only: soft-delete jobs that vanished from the source.
        # Never expire on an errored fetch — a mid-pagination failure returns
        # a partial list, and expiring against it would close every job on the
        # pages that were never reached. Empty results remain protected unless
        # this company has an explicitly verified authoritative empty source.
        allow_empty = bool(
            (company.get("scrape") or {}).get("allow_empty_full_scan")
        )
        preserve_unseen = bool(
            (company.get("scrape") or {}).get("preserve_unseen_jobs")
        )
        if _should_expire_jobs(
            is_fresh=is_fresh,
            err=err,
            raw=raw,
            allow_empty=allow_empty,
            preserve_unseen=preserve_unseen,
        ):
            delete_expired_jobs(
                name,
                [r["id"] for r in rows],
                allow_empty=allow_empty,
            )
        work_raw["save_jobs"] += time.monotonic() - save_t0

        unexplained_empty = _is_unexplained_empty_full_scan(
            is_fresh=is_fresh,
            raw=raw,
            err=err,
            allow_empty=allow_empty,
        )
        warning_message = (
            "Full scan returned no jobs; existing jobs were preserved."
            if unexplained_empty else None
        )
        entry = _make_summary(
            name,
            progress[i]["mode"],
            raw,
            err,
            duration,
            new_count,
            warning_message=warning_message,
        )
        progress[i].update(entry)
        summaries.append(dict(entry))
        total_jobs += len(raw)
        total_new += new_count

        status_t0 = time.monotonic()
        finish_company_scan_status(
            status_ids[i],
            status="failed" if err else "warning" if unexplained_empty else "success",
            jobs_found=len(raw),
            new_jobs_found=new_count,
            error_message=err or warning_message,
        )
        work_raw["status_finish"] += time.monotonic() - status_t0
        progress_t0 = time.monotonic()
        update_scan_counts(scan_run_id, len(summaries))
        work_raw["progress_write"] += time.monotonic() - progress_t0
        _publish_work()

    # ── Run fetches concurrently ──────────────────────────────────────────────

    def _mode(c):
        return (c.get("scrape") or {}).get("mode", "html")

    workday_cos = [(i, c) for i, c in enumerate(companies) if _mode(c) == "workday"]
    api_cos     = [(i, c) for i, c in enumerate(companies)
                   if _mode(c) in _API_MODES and _mode(c) != "workday"]
    # "html" companies are plain requests+BeautifulSoup — they don't need a
    # browser, so they get their own wide lane instead of clogging Playwright's.
    html_cos    = [(i, c) for i, c in enumerate(companies) if _mode(c) == "html"]
    browser_cos = [(i, c) for i, c in enumerate(companies)
                   if _mode(c) not in _API_MODES and _mode(c) != "html"]

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
        cancelled_before_start = fut.cancel()
        i = meta["index"]
        company = meta["company"]
        duration = round(time.monotonic() - meta["started"], 1)
        timeout = _company_timeout_s()
        err = f"timed out after {timeout}s"
        logging.warning("scan: %s timed out after %.1fs", company.get("name"), duration)
        try:
            _commit_one(i, company, [], err, duration, {"fetch_enrich": duration})
        except Exception as exc:
            logging.warning("scan: failed to record timeout for %s: %s", company.get("name"), exc)
        return cancelled_before_start

    def _run_bounded(api_ex, workday_ex, html_ex, pw_ex):
        """Keep only active workers queued so progress and memory stay honest."""
        futures = {}
        api_iter = iter(api_cos)
        workday_iter = iter(workday_cos)
        html_iter = iter(html_cos)
        pw_iter = iter(browser_cos)
        for _ in range(min(_API_WORKERS, len(api_cos))):
            _submit_next(api_ex, api_iter, futures)
        for _ in range(min(_WORKDAY_WORKERS, len(workday_cos))):
            _submit_next(workday_ex, workday_iter, futures)
        for _ in range(min(_HTML_WORKERS, len(html_cos))):
            _submit_next(html_ex, html_iter, futures)
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
                # A running thread cannot be killed safely. Retire this worker
                # slot instead of queuing a replacement behind it (which made
                # queued companies appear to time out before they even began).
                _mark_timed_out(fut, meta)
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
        # If hung workers exhausted a lane, report every never-submitted company
        # explicitly. Silently skipping them would make scan completeness look
        # better than it really was.
        for iterator in (api_iter, workday_iter, html_iter, pw_iter):
            for i, company in iterator:
                err = "not scanned because all workers in this source lane timed out"
                _commit_one(i, company, [], err, 0.0, {})
        return None

    try:
        api_ex     = concurrent.futures.ThreadPoolExecutor(max_workers=_API_WORKERS)
        workday_ex = concurrent.futures.ThreadPoolExecutor(max_workers=_WORKDAY_WORKERS)
        html_ex    = concurrent.futures.ThreadPoolExecutor(max_workers=_HTML_WORKERS)
        pw_ex      = concurrent.futures.ThreadPoolExecutor(max_workers=_BROWSER_WORKERS)
        try:
            cancelled = _run_bounded(api_ex, workday_ex, html_ex, pw_ex)
        finally:
            for ex in (api_ex, workday_ex, html_ex, pw_ex):
                ex.shutdown(wait=False, cancel_futures=True)

        _finish_phase("scan_companies", "finalize")
        if cancelled:
            status = "cancelled"
        else:
            totals = scan_run_totals(scan_run_id)
            status = (
                "partial_success"
                if totals["failed_companies"] or totals["warning_companies"]
                else "success"
            )

        finish_scan_run_from_statuses(scan_run_id, status=status)
        _finish_phase("finalize", "done")
        elapsed = round(time.monotonic() - t_start, 1)
        timing["total_s"] = round(time.monotonic() - t_start, 3)
        timing["steps_percent"] = {
            key: round((value / timing["total_s"] * 100) if timing["total_s"] else 0.0, 1)
            for key, value in phase_raw.items()
        }
        _publish_work()
        return {
            "scan_run_id":      scan_run_id,
            "scan_type":        scan_type,
            "status":           status,
            "progress":         progress,
            "results":          [],
            "new_count":        total_new,
            "total_jobs":       total_jobs,
            "failed_companies": scan_run_totals(scan_run_id)["failed_companies"],
            "warning_companies": scan_run_totals(scan_run_id)["warning_companies"],
            "elapsed_s":        elapsed,
            "timing":           timing,
        }

    except Exception as exc:
        finish_scan_run_from_statuses(scan_run_id, status="failed", error_message=str(exc))
        raise


# ── Private helpers ───────────────────────────────────────────────────────────

def _forbidden_source_key(company):
    scrape = company.get("scrape") or {}
    source_fields = (
        "mode", "list_url", "url", "board_token", "lever_slug",
        "ashby_slug", "workable_slug", "icims_slug", "company_id",
    )
    return "|".join(str(scrape.get(field) or "") for field in source_fields)


def _blocked_until(company):
    company_name = company["name"]
    try:
        raw = get_app_state(f"crawler_403_until:{company_name}")
        stored_source = get_app_state(f"crawler_403_source:{company_name}")
    except Exception as exc:
        # Backoff metadata is advisory. A busy SQLite writer must not prevent
        # an otherwise independent network fetch from running.
        logging.warning("scan: could not read 403 backoff for %s: %s", company_name, exc)
        return None
    if not raw:
        return None
    # A backoff belongs to the source that returned 403, not permanently to
    # the company. If its ATS/config changes, try the new source immediately.
    if not stored_source or stored_source != _forbidden_source_key(company):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _remember_forbidden(company):
    company_name = company["name"]
    count_key = f"crawler_403_count:{company_name}"
    try:
        count = int(get_app_state(count_key) or "0") + 1
    except (ValueError, TypeError):
        count = 1
    except Exception as exc:
        logging.warning("scan: could not read 403 count for %s: %s", company_name, exc)
        count = 1
    base_hours = int(os.environ.get("JOBRADAR_403_BACKOFF_HOURS", "2"))
    max_hours = int(os.environ.get("JOBRADAR_403_MAX_BACKOFF_HOURS", "48"))
    hours = min(max_hours, max(1, base_hours) * (2 ** min(count - 1, 6)))
    until = datetime.now() + timedelta(hours=hours)
    try:
        set_app_state(count_key, str(count))
        set_app_state(
            f"crawler_403_until:{company_name}",
            until.strftime("%Y-%m-%d %H:%M:%S"),
        )
        set_app_state(
            f"crawler_403_source:{company_name}",
            _forbidden_source_key(company),
        )
    except Exception as exc:
        logging.warning("scan: could not persist 403 backoff for %s: %s", company_name, exc)


def _clear_forbidden(company):
    company_name = company["name"]
    try:
        delete_app_state(f"crawler_403_until:{company_name}")
        delete_app_state(f"crawler_403_count:{company_name}")
        delete_app_state(f"crawler_403_source:{company_name}")
    except Exception as exc:
        # Clearing advisory state must never discard a successfully fetched
        # job list merely because another process currently owns SQLite's
        # single writer lock.
        logging.warning("scan: could not clear 403 backoff for %s: %s", company_name, exc)

def _is_forbidden_error(err):
    text = str(err or "").lower()
    return "403" in text or "forbidden" in text


def _make_seen_entry(job, company_name, now_str):
    """Build the initial cache entry for a newly discovered job."""
    return {
        "_stored_id":  job.get("id"),
        "first_seen":  now_str,
        "company":     company_name,
        "title":       job.get("title", ""),
        "url":         job.get("url", ""),
        "ats_id":      job.get("ats_id"),
        "location":    job.get("location"),
        "location_raw": job.get("location_raw"),
        "locations":   job.get("locations") or [],
        "country_iso": job.get("country_iso"),
        "remote_mode": job.get("remote_mode"),
        "source":      job.get("source", ""),
        "posted":      job.get("posted"),
        "posted_at":   job.get("posted_at"),
        "exp_min":     job.get("exp_min"),
        "exp_max":     job.get("exp_max"),
        "exp_label":   job.get("exp_label"),
        "exp_source":  job.get("exp_source"),
        "skills":      job.get("skills") or [],
        "keywords":    job.get("keywords") or [],
        "has_description": bool((job.get("description") or "").strip()),
    }


def _refresh_seen_entry(entry, job):
    """Overwrite stale cached fields with newer data from a re-scan."""
    if job.get("title"):     entry["title"] = job["title"]
    if job.get("url"):       entry["url"] = job["url"]
    if job.get("ats_id"):    entry["ats_id"] = job["ats_id"]
    if job.get("location"):  entry["location"] = job["location"]
    if job.get("location_raw") is not None: entry["location_raw"] = job["location_raw"]
    if job.get("locations"): entry["locations"] = job["locations"]
    if job.get("country_iso"): entry["country_iso"] = job["country_iso"]
    entry["remote_mode"] = job.get("remote_mode")
    if job.get("posted"):    entry["posted"] = job["posted"]
    if job.get("posted_at"): entry["posted_at"] = job["posted_at"]
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


def _find_existing_seen_job(
    seen, seen_by_url, *, stable_id, legacy_id, company, url
):
    """Match a corrected posting by URL when its title-derived ID changed."""
    return (
        seen.get(stable_id)
        or seen.get(legacy_id)
        or (seen_by_url.get((company, url)) if url else None)
    )


def _should_expire_jobs(*, is_fresh, err, raw, allow_empty, preserve_unseen):
    """Only expire missing rows when a source is known to be comprehensive."""
    return (
        not is_fresh
        and not err
        and not preserve_unseen
        and bool(raw or allow_empty)
    )


def _is_unexplained_empty_full_scan(*, is_fresh, raw, err, allow_empty):
    """Return whether a successful full scan produced an unverified empty result."""
    return not is_fresh and not err and not raw and not allow_empty


def _make_summary(name, mode, raw, err, duration, new_count, warning_message=None):
    """Build the per-company status dict used by the progress list and scan log."""
    entry = {
        "company":    name,
        "mode":       mode,
        "status":     "error" if err else "warning" if warning_message else "done",
        "count":      len(raw),
        "new":        new_count,
        "duration_s": duration,
    }
    if err:
        entry["error"] = err
    elif warning_message:
        entry["error"] = warning_message
    return entry
