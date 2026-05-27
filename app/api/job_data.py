import json
import shutil
from collections import Counter
from datetime import datetime

from .common import COMPANIES_FILE, DEFAULT_COMPANIES_FILE
from .database import IS_POSTGRES, add_column_if_missing, connect_db, initialize_postgres_schema, placeholders
from .utils import extract_experience, location_matches, make_id, normalize_location, _parse_posted

def init_db():
    with connect_db() as conn:
        if IS_POSTGRES:
            initialize_postgres_schema(conn)
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                url        TEXT DEFAULT '',
                company    TEXT NOT NULL,
                location   TEXT DEFAULT '',
                source     TEXT DEFAULT '',
                categories TEXT DEFAULT '[]',
                first_seen TEXT NOT NULL,
                posted     TEXT DEFAULT '',
                last_seen_at TEXT DEFAULT '',
                is_open    INTEGER DEFAULT 1,
                exp_min    INTEGER,
                exp_max    INTEGER,
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
                error_message TEXT
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
            "exp_min INTEGER",
            "exp_max INTEGER",
            "exp_label TEXT DEFAULT ''",
            "exp_source TEXT DEFAULT ''",
            "description TEXT DEFAULT ''",
            "skills TEXT DEFAULT '[]'",
            "employment_type TEXT DEFAULT ''",
        ):
            add_column_if_missing(conn, "jobs", column_sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_open ON jobs(is_open)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company_open ON jobs(company, is_open)")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_open_sort_date
            ON jobs(is_open, COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company_open_sort_date
            ON jobs(company, is_open, COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_open_posted_sort
            ON jobs(is_open, CASE WHEN posted != '' THEN 0 ELSE 1 END, posted DESC, substr(first_seen,1,10) DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_company_open_posted_sort
            ON jobs(company, is_open, CASE WHEN posted != '' THEN 0 ELSE 1 END, posted DESC, substr(first_seen,1,10) DESC)
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
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apply_user ON user_apply_list(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_searches_user ON user_saved_searches(user_id)")

def upsert_jobs(job_list):
    if not job_list:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with connect_db() as conn:
        conn.executemany(
            "INSERT INTO jobs "
            "(id, title, url, company, location, source, categories, first_seen, posted, last_seen_at, is_open, exp_min, exp_max, exp_label, exp_source, description, skills, employment_type) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, "
            "location=CASE WHEN excluded.location != '' THEN excluded.location ELSE jobs.location END, "
            "source=excluded.source, "
            "categories=excluded.categories, "
            # first_seen intentionally excluded — never overwrite the original discovery date
            "posted=CASE WHEN excluded.posted != '' THEN excluded.posted ELSE jobs.posted END, "
            "last_seen_at=excluded.last_seen_at, "
            "is_open=excluded.is_open, "
            "exp_min=CASE WHEN excluded.exp_min IS NOT NULL THEN excluded.exp_min ELSE jobs.exp_min END, "
            "exp_max=CASE WHEN excluded.exp_max IS NOT NULL THEN excluded.exp_max ELSE jobs.exp_max END, "
            "exp_label=CASE WHEN excluded.exp_label != '' THEN excluded.exp_label ELSE jobs.exp_label END, "
            "exp_source=CASE WHEN excluded.exp_source != '' THEN excluded.exp_source ELSE jobs.exp_source END, "
            "description=CASE WHEN excluded.description != '' THEN excluded.description ELSE jobs.description END, "
            "skills=CASE WHEN excluded.skills != '[]' THEN excluded.skills ELSE jobs.skills END, "
            "employment_type=CASE WHEN excluded.employment_type != '' THEN excluded.employment_type ELSE jobs.employment_type END",
            [(j["id"], j["title"], j.get("url",""), j["company"],
              j.get("location",""), j.get("source",""),
              json.dumps(j.get("categories",[])), j["first_seen"],
              j.get("posted",""), now_str, True, j.get("exp_min"), j.get("exp_max"),
              j.get("exp_label",""), j.get("exp_source",""),
              j.get("description",""), json.dumps(j.get("skills",[])),
              j.get("employment_type",""))
             for j in job_list],
        )

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

def delete_expired_jobs(company_name, current_ids):
    """Mark jobs closed when they do not appear in a company's latest scan.
    Only runs when the scan returned results — empty scan means error, not expiry.
    Uses a diff approach to avoid SQLite's 999 bind-variable limit."""
    if not current_ids:
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
    categories = categories or ["Detected"]
    updated = False
    for company in companies:
        if (company.get("name") or "").strip().lower() == name.lower():
            company["name"] = name
            company["categories"] = company.get("categories") or categories
            company["scrape"] = scrape
            updated = True
            break
    if not updated:
        companies.append({"name": name, "categories": categories, "scrape": scrape})
    companies.sort(key=lambda c: (c.get("name") or "").lower())
    path.write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    current_companies()
    return updated

def load_seen():
    """Return known open jobs keyed by stable job id. The database is the source of truth."""
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(
                "SELECT company, url, title, location, source, first_seen, posted, "
                "exp_min, exp_max, exp_label, exp_source, skills FROM jobs WHERE is_open = 1"
            ).fetchall()
        return {
            make_id(r["company"], r["url"], r["title"]): {
                "first_seen": r["first_seen"],
                "company":    r["company"],
                "title":      r["title"],
                "url":        r["url"],
                "location":   r["location"] or "",
                "source":     r["source"] or "",
                "posted":     r["posted"] or "",
                "exp_min":    r["exp_min"],
                "exp_max":    r["exp_max"],
                "exp_label":  r["exp_label"] or "",
                "exp_source": r["exp_source"] or "",
                "skills":     json.loads(r["skills"] or "[]"),
            }
            for r in rows
        }
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
    jobs = []
    for jid, item in load_seen().items():
        if not isinstance(item, dict):
            continue
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
        if keyword and keyword not in f"{title} {company}".lower():
            continue
        if location and not location_matches(location, loc):
            continue
        if since and (posted or first_seen) < since:
            continue
        if category and category not in cats:
            continue
        fs = first_seen[:10]
        jobs.append({
            "id":         jid,
            "title":      title,
            "url":        item.get("url", ""),
            "company":    company,
            "location":   loc,
            "source":     item.get("source", "seen"),
            "categories": cats,
            "first_seen": first_seen,
            "posted":     _parse_posted(posted),
            "exp_min":    item.get("exp_min"),
            "exp_max":    item.get("exp_max"),
            "exp_label":  item.get("exp_label", ""),
            "exp_source": item.get("exp_source", ""),
            "skills":     item.get("skills") or [],
            "is_new":     fs == today,
        })
    jobs.sort(key=lambda j: j.get("first_seen", ""), reverse=True)
    return jobs[offset:offset + limit], len(jobs)

def backfill_experience_from_titles():
    """Fill missing experience estimates from titles for existing DB rows."""
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(
                "SELECT id, title FROM jobs WHERE exp_min IS NULL AND exp_max IS NULL"
            ).fetchall()
            updates = []
            for row in rows:
                exp = extract_experience(row["title"] or "")
                if exp.get("label"):
                    updates.append((exp["min"], exp["max"], exp["label"], exp["source"], row["id"]))
            if updates:
                conn.executemany(
                    "UPDATE jobs SET exp_min=?, exp_max=?, exp_label=?, exp_source=? WHERE id=?",
                    updates,
                )
            return len(updates)
    except Exception:
        return 0
