import json
import shutil
import sqlite3
from collections import Counter

from .common import COMPANIES_FILE, DB_FILE, DEFAULT_COMPANIES_FILE, SEEN_FILE
from .utils import make_id, normalize_location, _parse_posted

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
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
                posted     TEXT DEFAULT ''
            )
        """)
        # Migrate existing DBs that don't have the posted column
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN posted TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()

init_db()

def upsert_jobs(job_list):
    if not job_list:
        return
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            "INSERT INTO jobs "
            "(id, title, url, company, location, source, categories, first_seen, posted) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, "
            "location=CASE WHEN excluded.location != '' THEN excluded.location ELSE jobs.location END, "
            "source=excluded.source, "
            "categories=excluded.categories, "
            # first_seen intentionally excluded — never overwrite the original discovery date
            "posted=CASE WHEN excluded.posted != '' THEN excluded.posted ELSE jobs.posted END",
            [(j["id"], j["title"], j.get("url",""), j["company"],
              j.get("location",""), j.get("source",""),
              json.dumps(j.get("categories",[])), j["first_seen"],
              j.get("posted",""))
             for j in job_list],
        )

def delete_expired_jobs(company_name, current_ids):
    """Remove DB entries for a company that didn't appear in the latest scan.
    Only runs when the scan returned results — empty scan means error, not expiry.
    Uses a diff approach to avoid SQLite's 999 bind-variable limit."""
    if not current_ids:
        return
    current_set = set(current_ids)
    with sqlite3.connect(DB_FILE) as conn:
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
                f"DELETE FROM jobs WHERE id IN ({','.join('?' * len(batch))})",
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

def current_companies():
    """Reload companies.json so config edits show up without stale process state."""
    global COMPANIES
    COMPANIES = load_companies()
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

# ── Seen-jobs store ───────────────────────────────────────────────────────────

def rebuild_seen_from_db():
    """Rebuild seen_jobs.json from the DB so IDs stay in sync with any URL/title changes."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT company, url, title, location, source, first_seen, posted FROM jobs"
            ).fetchall()
        seen = {
            make_id(r["company"], r["url"], r["title"]): {
                "first_seen": r["first_seen"],
                "company":    r["company"],
                "title":      r["title"],
                "url":        r["url"],
                "location":   r["location"] or "",
                "source":     r["source"] or "",
                "posted":     r["posted"] or "",
            }
            for r in rows
        }
        SEEN_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False))
        return seen
    except Exception as e:
        print(f"⚠  rebuild_seen_from_db failed: {e}")
        return load_seen_raw()

def load_seen_raw():
    if SEEN_FILE.exists():
        try:   return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except: return {}
    return {}

def load_seen():
    """Always rebuild from DB to keep IDs consistent with current data."""
    return rebuild_seen_from_db()

def save_seen(seen):
    # seen_jobs.json is rebuilt from DB after every scan via rebuild_seen_from_db()
    pass

def normalize_existing_data():
    """Clean rows/files written before location/date normalization was consistent."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, location, posted FROM jobs").fetchall()
            updates = []
            for row in rows:
                loc = normalize_location(row["location"] or "")
                posted = _parse_posted(row["posted"] or "")
                if loc != (row["location"] or "") or posted != (row["posted"] or ""):
                    updates.append((loc, posted, row["id"]))
            if updates:
                conn.executemany("UPDATE jobs SET location=?, posted=? WHERE id=?", updates)
                conn.commit()
    except Exception:
        pass

    try:
        seen = load_seen()
        changed = False
        for item in seen.values():
            if not isinstance(item, dict):
                continue
            loc = normalize_location(item.get("location", ""))
            posted = _parse_posted(item.get("posted", ""))
            if loc != item.get("location", ""):
                item["location"] = loc
                changed = True
            if posted != item.get("posted", ""):
                item["posted"] = posted
                changed = True
        if changed:
            save_seen(seen)
    except Exception:
        pass

# Do not run this automatically on import. With a large jobs DB / seen file it
# makes local startup and Render cold starts feel broken. New scan results are
# normalized before save; run normalize_existing_data() manually only after
# changing normalization rules.

def company_categories_map():
    return {c.get("name"): c.get("categories", []) for c in current_companies()}
