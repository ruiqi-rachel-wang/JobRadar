"""Persistent scan run and per-company status storage."""
from datetime import datetime

try:
    from .api.database import IS_POSTGRES, connect_db
except ImportError:
    from api.database import IS_POSTGRES, connect_db


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_scan_run(scan_type, total_companies, scheduled_for="", send_alerts_after=False):
    with connect_db() as conn:
        if IS_POSTGRES:
            row = conn.execute(
                "INSERT INTO scan_runs "
                "(scan_type, total_companies, started_at, scheduled_for, send_alerts_after) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                (scan_type, total_companies, _now(), scheduled_for or "", int(bool(send_alerts_after))),
            ).fetchone()
            return row[0]
        cur = conn.execute(
            "INSERT INTO scan_runs "
            "(scan_type, total_companies, started_at, scheduled_for, send_alerts_after) "
            "VALUES (?, ?, ?, ?, ?)",
            (scan_type, total_companies, _now(), scheduled_for or "", int(bool(send_alerts_after))),
        )
        return cur.lastrowid


def finish_scan_run(scan_run_id, status, total_jobs_found=0, new_jobs_found=0,
                    updated_jobs_found=0, failed_companies=0, companies_scanned=None,
                    error_message=None):
    scanned_sql = ", companies_scanned=?" if companies_scanned is not None else ""
    scanned_params = (companies_scanned,) if companies_scanned is not None else ()
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_runs SET status=?, finished_at=?, total_jobs_found=?, "
            "new_jobs_found=?, updated_jobs_found=?, failed_companies=?, error_message=? "
            f"{scanned_sql} "
            "WHERE id=?",
            (
                status, _now(), total_jobs_found, new_jobs_found,
                updated_jobs_found, failed_companies, error_message,
                *scanned_params,
                scan_run_id,
            ),
        )


def update_scan_counts(scan_run_id, companies_scanned):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_runs SET companies_scanned=? WHERE id=?",
            (companies_scanned, scan_run_id),
        )


def scan_run_totals(scan_run_id):
    with connect_db(rows=True) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(CASE WHEN status NOT IN ('pending', 'scanning') THEN 1 END) AS companies_scanned,
                COALESCE(SUM(jobs_found), 0) AS total_jobs_found,
                COALESCE(SUM(new_jobs_found), 0) AS new_jobs_found,
                COUNT(CASE WHEN status='failed' THEN 1 END) AS failed_companies
            FROM scan_company_status
            WHERE scan_run_id=?
            """,
            (scan_run_id,),
        ).fetchone()
    total_jobs = row["total_jobs_found"] or 0
    new_jobs = row["new_jobs_found"] or 0
    return {
        "companies_scanned": row["companies_scanned"] or 0,
        "total_jobs_found": total_jobs,
        "new_jobs_found": new_jobs,
        "updated_jobs_found": max(0, total_jobs - new_jobs),
        "failed_companies": row["failed_companies"] or 0,
    }


def finish_scan_run_from_statuses(scan_run_id, status, error_message=None):
    totals = scan_run_totals(scan_run_id)
    finish_scan_run(scan_run_id, status=status, error_message=error_message, **totals)


def running_scan():
    with connect_db(rows=True) as conn:
        return conn.execute(
            "SELECT * FROM scan_runs WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def scan_company_status_map(scan_run_id):
    with connect_db(rows=True) as conn:
        rows = conn.execute(
            "SELECT * FROM scan_company_status WHERE scan_run_id=?",
            (scan_run_id,),
        ).fetchall()
    return {row["company_name"]: dict(row) for row in rows}


def reset_unfinished_company_statuses(scan_run_id):
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE scan_company_status
            SET status='pending', started_at='', finished_at='', error_message=NULL
            WHERE scan_run_id=? AND status IN ('pending', 'scanning')
            """,
            (scan_run_id,),
        )


def abandon_running_scans():
    """Mark interrupted scans abandoned while preserving completed company totals."""
    with connect_db(rows=True) as conn:
        rows = conn.execute("SELECT id FROM scan_runs WHERE status='running'").fetchall()
    for row in rows:
        finish_scan_run_from_statuses(row["id"], status="abandoned")


def repair_abandoned_scan_totals():
    """Backfill totals for abandoned runs created before aggregate finalization."""
    with connect_db(rows=True) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM scan_runs
            WHERE status='abandoned'
              AND EXISTS (
                  SELECT 1 FROM scan_company_status
                  WHERE scan_run_id=scan_runs.id
                    AND status NOT IN ('pending', 'scanning')
              )
            """
        ).fetchall()
        for row in rows:
            scan_run_id = row["id"]
            totals = conn.execute(
                """
                SELECT
                    COUNT(CASE WHEN status NOT IN ('pending', 'scanning') THEN 1 END) AS companies_scanned,
                    COALESCE(SUM(jobs_found), 0) AS total_jobs_found,
                    COALESCE(SUM(new_jobs_found), 0) AS new_jobs_found,
                    COUNT(CASE WHEN status='failed' THEN 1 END) AS failed_companies
                FROM scan_company_status
                WHERE scan_run_id=?
                """,
                (scan_run_id,),
            ).fetchone()
            conn.execute(
                """
                UPDATE scan_runs
                SET companies_scanned=?,
                    total_jobs_found=?,
                    new_jobs_found=?,
                    updated_jobs_found=?,
                    failed_companies=?
                WHERE id=?
                """,
                (
                    totals["companies_scanned"] or 0,
                    totals["total_jobs_found"] or 0,
                    totals["new_jobs_found"] or 0,
                    max(0, (totals["total_jobs_found"] or 0) - (totals["new_jobs_found"] or 0)),
                    totals["failed_companies"] or 0,
                    scan_run_id,
                ),
            )


def create_company_scan_status(scan_run_id, company_name):
    with connect_db() as conn:
        if IS_POSTGRES:
            row = conn.execute(
                "INSERT INTO scan_company_status "
                "(scan_run_id, company_name, status) "
                "VALUES (?, ?, 'pending') RETURNING id",
                (scan_run_id, company_name),
            ).fetchone()
            return row[0]
        cur = conn.execute(
            "INSERT INTO scan_company_status "
            "(scan_run_id, company_name, status) VALUES (?, ?, 'pending')",
            (scan_run_id, company_name),
        )
        return cur.lastrowid


def create_company_scan_statuses(scan_run_id, company_names):
    """Create many pending company rows in one connection and transaction.

    Opening a database connection for every company is especially expensive
    when Postgres is remote. Keep the same row-per-company schema and returned
    IDs, but pay the connection/commit cost once for the whole scan.
    """
    status_ids = {}
    with connect_db() as conn:
        for company_name in company_names:
            if IS_POSTGRES:
                row = conn.execute(
                    "INSERT INTO scan_company_status "
                    "(scan_run_id, company_name, status) "
                    "VALUES (?, ?, 'pending') RETURNING id",
                    (scan_run_id, company_name),
                ).fetchone()
                status_ids[company_name] = row[0]
            else:
                cur = conn.execute(
                    "INSERT INTO scan_company_status "
                    "(scan_run_id, company_name, status) VALUES (?, ?, 'pending')",
                    (scan_run_id, company_name),
                )
                status_ids[company_name] = cur.lastrowid
    return status_ids


def start_company_scan_status(status_id):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_company_status SET status='scanning', started_at=? WHERE id=?",
            (_now(), status_id),
        )


def finish_company_scan_status(status_id, status, jobs_found=0, new_jobs_found=0,
                               error_message=None):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_company_status SET status=?, jobs_found=?, new_jobs_found=?, "
            "finished_at=?, error_message=? WHERE id=?",
            (status, jobs_found, new_jobs_found, _now(), error_message, status_id),
        )


def latest_scan():
    with connect_db(rows=True) as conn:
        return conn.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()


def scan_companies(scan_run_id):
    with connect_db(rows=True) as conn:
        return conn.execute(
            "SELECT * FROM scan_company_status WHERE scan_run_id=? "
            "ORDER BY started_at ASC, id ASC",
            (scan_run_id,),
        ).fetchall()
