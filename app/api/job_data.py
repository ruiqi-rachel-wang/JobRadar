import json
import shutil
from collections import Counter
from datetime import datetime

from .common import COMPANIES_FILE, DEFAULT_COMPANIES_FILE
from .database import IS_POSTGRES, add_column_if_missing, connect_db, initialize_postgres_schema, placeholders
from .search import keyword_groups, keyword_matches
from .taxonomy import normalize_categories, normalize_tags
from .utils import extract_experience, extract_keywords, location_matches, make_id, normalize_location, _parse_posted


def _text_or_json(value):
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _backfill_has_description_once(conn):
    """One-time migration flag for the expensive description scan."""
    row = conn.execute(
        "SELECT value FROM app_state WHERE key='has_description_backfilled'"
    ).fetchone()
    value = row[0] if row else ""
    if value == "1":
        return
    conn.execute(
        "UPDATE jobs SET has_description = 1 "
        "WHERE has_description = 0 AND COALESCE(description, '') != ''"
    )
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES ('has_description_backfilled', '1') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )

def init_db():
    with connect_db() as conn:
        if IS_POSTGRES:
            initialize_postgres_schema(conn)
            for column_sql in (
                "has_description INTEGER DEFAULT 0",
                "keywords TEXT DEFAULT '[]'",
                "ats_id TEXT",
                "requisition_id TEXT",
                "apply_url TEXT",
                "location_raw TEXT",
                "locations JSONB",
                "country_iso TEXT",
                "remote_mode TEXT",
                "posted_at TIMESTAMPTZ",
                "posted_raw TEXT",
                "description_raw TEXT",
                "employment_type_raw TEXT",
                "department TEXT",
                "team TEXT",
                "raw_data JSONB",
            ):
                add_column_if_missing(conn, "jobs", column_sql)
            add_column_if_missing(conn, "scan_runs", "scheduled_for TEXT DEFAULT ''")
            add_column_if_missing(conn, "scan_runs", "send_alerts_after INTEGER DEFAULT 0")
            _backfill_has_description_once(conn)
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                url        TEXT DEFAULT '',
                company    TEXT NOT NULL,
                ats_id     TEXT,
                requisition_id TEXT,
                apply_url  TEXT,
                location   TEXT DEFAULT '',
                location_raw TEXT,
                locations  TEXT,
                country_iso TEXT,
                remote_mode TEXT,
                source     TEXT DEFAULT '',
                categories TEXT DEFAULT '[]',
                first_seen TEXT NOT NULL,
                posted     TEXT,
                posted_at  TEXT,
                posted_raw TEXT,
                last_seen_at TEXT DEFAULT '',
                is_open    INTEGER DEFAULT 1,
                exp_min    REAL,
                exp_max    REAL,
                exp_label  TEXT DEFAULT '',
                exp_source TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                total_companies INTEGER DEFAULT 0,
                companies_scanned INTEGER DEFAULT 0,
                total_jobs_found INTEGER DEFAULT 0,
                new_jobs_found INTEGER DEFAULT 0,
                updated_jobs_found INTEGER DEFAULT 0,
                failed_companies INTEGER DEFAULT 0,
                error_message TEXT,
                scheduled_for TEXT DEFAULT '',
                send_alerts_after INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_company_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_run_id INTEGER,
                company_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                jobs_found INTEGER DEFAULT 0,
                new_jobs_found INTEGER DEFAULT 0,
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                error_message TEXT
            )
        """)
        for column_sql in (
            "posted TEXT DEFAULT ''",
            "last_seen_at TEXT DEFAULT ''",
            "is_open INTEGER DEFAULT 1",
            "exp_min REAL",
            "exp_max REAL",
            "exp_label TEXT DEFAULT ''",
            "exp_source TEXT DEFAULT ''",
            "description TEXT DEFAULT ''",
            "description_raw TEXT",
            "has_description INTEGER DEFAULT 0",
            "skills TEXT DEFAULT '[]'",
            "keywords TEXT DEFAULT '[]'",
            "employment_type TEXT",
            "employment_type_raw TEXT",
            "ats_id TEXT",
            "requisition_id TEXT",
            "apply_url TEXT",
            "location_raw TEXT",
            "locations TEXT",
            "country_iso TEXT",
            "remote_mode TEXT",
            "posted_at TEXT",
            "posted_raw TEXT",
            "department TEXT",
            "team TEXT",
            "raw_data TEXT",
        ):
            add_column_if_missing(conn, "jobs", column_sql)
        for column_sql in (
            "scheduled_for TEXT DEFAULT ''",
            "send_alerts_after INTEGER DEFAULT 0",
        ):
            add_column_if_missing(conn, "scan_runs", column_sql)
        _backfill_has_description_once(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_open ON jobs(is_open)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company_open ON jobs(company, is_open)")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_open_first_seen
            ON jobs(is_open, first_seen DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company_open_first_seen
            ON jobs(company, is_open, first_seen DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT    NOT NULL DEFAULT 'other',
                message      TEXT    NOT NULL,
                url          TEXT    DEFAULT '',
                submitted_at TEXT    DEFAULT (datetime('now')),
                status       TEXT    DEFAULT 'new'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                email         TEXT    DEFAULT '',
                password_hash TEXT    NOT NULL,
                role          TEXT    DEFAULT 'user',
                email_alerts_enabled INTEGER DEFAULT 1,
                created_at    TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_apply_list (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                job_id        TEXT    NOT NULL,
                stage         TEXT    DEFAULT 'to-apply',
                added_at      TEXT    DEFAULT (datetime('now')),
                job_title     TEXT    DEFAULT '',
                job_company   TEXT    DEFAULT '',
                job_location  TEXT    DEFAULT '',
                job_url       TEXT    DEFAULT '',
                job_exp_label TEXT    DEFAULT '',
                job_posted    TEXT    DEFAULT '',
                UNIQUE(user_id, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_saved_searches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                kw         TEXT    DEFAULT '',
                loc        TEXT    DEFAULT '',
                exp_yrs    TEXT    DEFAULT '',
                since      TEXT    DEFAULT 'any',
                category   TEXT    DEFAULT 'All',
                tag        TEXT    DEFAULT '',
                email_alert_enabled INTEGER DEFAULT 0,
                alert_cursor_at TEXT DEFAULT '',
                last_alert_sent_at TEXT DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        for column_sql in (
            "email_alerts_enabled INTEGER DEFAULT 1",
        ):
            add_column_if_missing(conn, "users", column_sql)
        for column_sql in (
            "email_alert_enabled INTEGER DEFAULT 0",
            "tag TEXT DEFAULT ''",
            "alert_cursor_at TEXT DEFAULT ''",
            "last_alert_sent_at TEXT DEFAULT ''",
        ):
            add_column_if_missing(conn, "user_saved_searches", column_sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apply_user ON user_apply_list(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_searches_user ON user_saved_searches(user_id)")

def upsert_jobs(job_list):
    if not job_list:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = (
        "id", "title", "url", "company", "ats_id", "requisition_id",
        "apply_url", "location", "location_raw", "locations", "country_iso",
        "remote_mode", "source", "categories", "first_seen", "posted",
        "posted_at", "posted_raw", "last_seen_at", "is_open", "exp_min",
        "exp_max", "exp_label", "exp_source", "description",
        "description_raw", "has_description", "skills", "keywords",
        "employment_type", "employment_type_raw", "department", "team",
        "raw_data",
    )
    with connect_db() as conn:
        conn.executemany(
            f"INSERT INTO jobs ({', '.join(columns)}) "
            f"VALUES ({placeholders(len(columns))}) "
            "ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, "
            "url=CASE WHEN COALESCE(excluded.url, '') != '' THEN excluded.url ELSE jobs.url END, "
            "ats_id=COALESCE(excluded.ats_id, jobs.ats_id), "
            "requisition_id=COALESCE(excluded.requisition_id, jobs.requisition_id), "
            "apply_url=COALESCE(excluded.apply_url, jobs.apply_url), "
            "location=CASE WHEN COALESCE(excluded.location, '') != '' THEN excluded.location ELSE jobs.location END, "
            "location_raw=COALESCE(excluded.location_raw, jobs.location_raw), "
            "locations=CASE WHEN COALESCE(excluded.locations, '[]') != '[]' THEN excluded.locations ELSE jobs.locations END, "
            "country_iso=COALESCE(excluded.country_iso, jobs.country_iso), "
            "remote_mode=excluded.remote_mode, "
            "source=excluded.source, "
            "categories=excluded.categories, "
            # first_seen intentionally excluded — never overwrite the original discovery date
            # A stable job URL can be refreshed or re-published by an ATS. Do
            # not let that make an already-known job look newly posted. Keep
            # the first valid source date; if it was initially missing, only
            # accept a later-discovered date that is not after first_seen.
            "posted=CASE "
            "WHEN COALESCE(jobs.posted, '') != '' THEN jobs.posted "
            "WHEN COALESCE(excluded.posted, '') != '' AND excluded.posted <= substr(jobs.first_seen,1,10) "
            "THEN excluded.posted "
            "ELSE jobs.posted END, "
            "posted_at=COALESCE(jobs.posted_at, excluded.posted_at), "
            "posted_raw=COALESCE(excluded.posted_raw, jobs.posted_raw), "
            "last_seen_at=excluded.last_seen_at, "
            "is_open=excluded.is_open, "
            "exp_min=CASE WHEN excluded.exp_source IS NOT NULL THEN excluded.exp_min WHEN jobs.exp_source IN ('title','company_peer') THEN NULL ELSE jobs.exp_min END, "
            "exp_max=CASE WHEN excluded.exp_source IS NOT NULL THEN excluded.exp_max WHEN jobs.exp_source IN ('title','company_peer') THEN NULL ELSE jobs.exp_max END, "
            "exp_label=CASE WHEN excluded.exp_source IS NOT NULL THEN excluded.exp_label WHEN jobs.exp_source IN ('title','company_peer') THEN NULL ELSE jobs.exp_label END, "
            "exp_source=CASE WHEN excluded.exp_source IS NOT NULL THEN excluded.exp_source WHEN jobs.exp_source IN ('title','company_peer') THEN NULL ELSE jobs.exp_source END, "
            "description=CASE WHEN COALESCE(excluded.description, '') != '' THEN excluded.description ELSE jobs.description END, "
            "description_raw=CASE WHEN COALESCE(excluded.description_raw, '') != '' THEN excluded.description_raw ELSE jobs.description_raw END, "
            "has_description=CASE WHEN excluded.has_description != 0 THEN excluded.has_description ELSE jobs.has_description END, "
            "skills=CASE WHEN excluded.skills != '[]' THEN excluded.skills ELSE jobs.skills END, "
            "keywords=CASE WHEN excluded.keywords != '[]' THEN excluded.keywords ELSE jobs.keywords END, "
            "employment_type=excluded.employment_type, "
            "employment_type_raw=COALESCE(excluded.employment_type_raw, jobs.employment_type_raw), "
            "department=COALESCE(excluded.department, jobs.department), "
            "team=COALESCE(excluded.team, jobs.team), "
            "raw_data=COALESCE(excluded.raw_data, jobs.raw_data)",
            [
                (
                    j["id"], j["title"], j.get("url") or "", j["company"],
                    j.get("ats_id"), j.get("requisition_id"), j.get("apply_url"),
                    j.get("location"), j.get("location_raw"),
                    json.dumps(j.get("locations") or [], ensure_ascii=False),
                    j.get("country_iso"), j.get("remote_mode"),
                    j.get("source") or "",
                    json.dumps(j.get("categories") or [], ensure_ascii=False),
                    j["first_seen"], j.get("posted"), j.get("posted_at"),
                    j.get("posted_raw"), now_str, True, j.get("exp_min"),
                    j.get("exp_max"), j.get("exp_label"), j.get("exp_source"),
                    j.get("description"), j.get("description_raw"),
                    int(bool((j.get("description") or "").strip())),
                    json.dumps(j.get("skills") or [], ensure_ascii=False),
                    json.dumps(
                        j.get("keywords")
                        or extract_keywords(
                            j.get("title", ""),
                            j.get("description", ""),
                            j.get("skills") or [],
                        ),
                        ensure_ascii=False,
                    ),
                    j.get("employment_type"), _text_or_json(j.get("employment_type_raw")),
                    j.get("department"), j.get("team"),
                    json.dumps(j.get("raw_data"), ensure_ascii=False)
                    if j.get("raw_data") is not None else None,
                )
                for j in job_list
            ],
        )
        # Do not maintain the legacy jobs_fts table. No application query
        # reads it (search is served from jobs/the seen cache), while
        # incrementally updating its unindexed job_id column made large scans
        # repeatedly walk a multi-gigabyte derived index. Existing deployments
        # may retain the table for rollback compatibility; it is intentionally
        # left untouched and can be removed in a separate maintenance window.

def get_app_state(key, default=""):
    with connect_db() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def set_app_state(key, value):
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

def delete_app_state(key):
    with connect_db() as conn:
        conn.execute("DELETE FROM app_state WHERE key=?", (key,))

def delete_expired_jobs(company_name, current_ids, allow_empty=False):
    """Mark jobs closed when they do not appear in a company's latest scan.
    Empty results are non-destructive unless the caller explicitly verified
    that the source reliably represents a company with no current openings.
    Uses a diff approach to avoid SQLite's 999 bind-variable limit."""
    if not current_ids and not allow_empty:
        return
    current_set = set(current_ids)
    with connect_db() as conn:
        existing = {row[0] for row in conn.execute(
            "SELECT id FROM jobs WHERE company=?", (company_name,)
        )}
        to_delete = list(existing - current_set)
        if not to_delete:
            return
        # Delete in batches of 500 to stay well under SQLite's 999-variable limit
        for i in range(0, len(to_delete), 500):
            batch = to_delete[i:i + 500]
            conn.execute(
                f"UPDATE jobs SET is_open=0 WHERE id IN ({placeholders(len(batch))})",
                batch,
            )
# ── Companies ─────────────────────────────────────────────────────────────────

def load_companies():
    path = COMPANIES_FILE
    if not path.exists() and DEFAULT_COMPANIES_FILE.exists() and path != DEFAULT_COMPANIES_FILE:
        shutil.copyfile(DEFAULT_COMPANIES_FILE, path)
    if not path.exists():
        print("⚠  companies.json not found"); return []
    companies = json.loads(path.read_text(encoding="utf-8"))
    for company in companies:
        company["categories"] = normalize_categories(company.get("categories"))
        tags = list(company.get("tags") or [])
        if (company.get("interview") or {}).get("no_whiteboard") is True:
            tags.append("Interview Friendly")
        company["tags"] = normalize_tags(tags)
    modes = Counter((c.get("scrape") or {}).get("mode","html") for c in companies)
    print(f"✓ {len(companies)} companies — " +
          " | ".join(f"{m}:{n}" for m,n in sorted(modes.items())))
    return companies

COMPANIES = load_companies()
_COMPANIES_MTIME = COMPANIES_FILE.stat().st_mtime if COMPANIES_FILE.exists() else 0

def current_companies():
    """Return companies.json, reloading only when the file changes."""
    global COMPANIES, _COMPANIES_MTIME
    try:
        mtime = COMPANIES_FILE.stat().st_mtime
    except FileNotFoundError:
        mtime = 0
    if mtime != _COMPANIES_MTIME:
        COMPANIES = load_companies()
        _COMPANIES_MTIME = mtime
    return COMPANIES

def add_or_update_company(name, scrape, categories=None):
    """Persist a detected company config to companies.json."""
    name = (name or "").strip()
    if not name:
        raise ValueError("company name required")
    if not isinstance(scrape, dict) or not scrape.get("mode"):
        raise ValueError("valid scrape config required")

    path = COMPANIES_FILE
    if not path.exists() and DEFAULT_COMPANIES_FILE.exists() and path != DEFAULT_COMPANIES_FILE:
        shutil.copyfile(DEFAULT_COMPANIES_FILE, path)
    companies = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    categories = normalize_categories(categories)
    updated = False
    for company in companies:
        if (company.get("name") or "").strip().lower() == name.lower():
            company["name"] = name
            company["categories"] = normalize_categories(company.get("categories") or categories)
            company["scrape"] = scrape
            updated = True
            break
    if not updated:
        companies.append({"name": name, "categories": categories, "tags": ["Needs Review"], "scrape": scrape})
    companies.sort(key=lambda c: (c.get("name") or "").lower())
    path.write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    current_companies()
    return updated

def load_seen():
    """Return known open jobs keyed by stable job id. The database is the source of truth."""
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(
                "SELECT id, company, url, title, ats_id, location, location_raw, "
                "locations, country_iso, remote_mode, source, first_seen, posted, "
                "posted_at, exp_min, exp_max, exp_label, exp_source, skills, "
                "keywords, has_description "
                "FROM jobs WHERE is_open = 1"
            ).fetchall()
        seen = {}
        for r in rows:
            entry = {
                "_stored_id": r["id"],
                "first_seen": r["first_seen"],
                "company":    r["company"],
                "title":      r["title"],
                "url":        r["url"],
                "ats_id":     r["ats_id"],
                "location":   r["location"],
                "location_raw": r["location_raw"],
                "locations":  json.loads(r["locations"] or "[]"),
                "country_iso": r["country_iso"],
                "remote_mode": r["remote_mode"],
                "source":     r["source"] or "",
                "posted":     r["posted"],
                "posted_at":  r["posted_at"],
                "exp_min":    r["exp_min"],
                "exp_max":    r["exp_max"],
                "exp_label":  r["exp_label"],
                "exp_source": r["exp_source"],
                "skills":     json.loads(r["skills"] or "[]"),
                "keywords":   json.loads(r["keywords"] or "[]"),
                "has_description": bool(r["has_description"]),
            }
            stable_id = make_id(
                r["company"], r["url"], r["title"], r["source"], r["ats_id"]
            )
            legacy_id = make_id(r["company"], r["url"], r["title"])
            for key in {r["id"], stable_id, legacy_id}:
                seen[key] = entry
        return seen
    except Exception as e:
        print(f"⚠  load_seen failed: {e}")
        return {}

def normalize_existing_data():
    """Clean rows/files written before location/date normalization was consistent."""
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute("SELECT id, location, posted FROM jobs").fetchall()
            updates = []
            for row in rows:
                loc = normalize_location(row["location"] or "")
                posted = _parse_posted(row["posted"] or "")
                if loc != (row["location"] or "") or posted != (row["posted"] or ""):
                    updates.append((loc, posted, row["id"]))
            if updates:
                conn.executemany("UPDATE jobs SET location=?, posted=? WHERE id=?", updates)
    except Exception:
        pass

# Do not run this automatically on import. With a large jobs DB it makes local
# startup and Render cold starts feel broken. New scan results are normalized
# before save; run normalize_existing_data() manually only after changing
# normalization rules.

def company_categories_map():
    return {c.get("name"): c.get("categories", []) for c in current_companies()}

def jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today):
    """Fallback job query from the in-memory seen dict when the DB returns nothing."""
    cats_by_company = company_categories_map()
    company_set = set(companies or [])
    groups = keyword_groups(keyword)
    jobs = []
    emitted = set()
    for jid, item in load_seen().items():
        if not isinstance(item, dict):
            continue
        stored_id = item.get("_stored_id") or jid
        if stored_id in emitted:
            continue
        emitted.add(stored_id)
        if item.get("is_open") is False:
            continue
        title      = item.get("title", "")
        company    = item.get("company", "")
        first_seen = item.get("first_seen", "")
        posted     = item.get("posted", "")
        cats       = cats_by_company.get(company, [])
        if company_set and company not in company_set:
            continue
        loc = item.get("location", "")
        if groups and not keyword_matches(title, company, item.get("keywords") or [], groups):
            continue
        if location and not location_matches(location, loc):
            continue
        if since and (posted or first_seen) < since:
            continue
        if category and category not in cats:
            continue
        fs = first_seen[:10]
        jobs.append({
            "id":         stored_id,
            "title":      title,
            "url":        item.get("url", ""),
            "company":    company,
            "location":   loc,
            "location_raw": item.get("location_raw"),
            "locations":  item.get("locations") or [],
            "country_iso": item.get("country_iso"),
            "remote_mode": item.get("remote_mode"),
            "source":     item.get("source", "seen"),
            "categories": cats,
            "first_seen": first_seen,
            "posted":     _parse_posted(posted) or None,
            "posted_at":  item.get("posted_at"),
            "exp_min":    item.get("exp_min"),
            "exp_max":    item.get("exp_max"),
            "exp_label":  item.get("exp_label"),
            "exp_source": item.get("exp_source"),
            "skills":     item.get("skills") or [],
            "is_new":     fs == today,
        })
    jobs.sort(key=lambda j: j.get("first_seen", ""), reverse=True)
    return jobs[offset:offset + limit], len(jobs)

def backfill_experience_from_titles():
    """Legacy no-op: titles are not evidence of a numeric requirement."""
    return 0
