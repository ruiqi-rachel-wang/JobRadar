CREATE TABLE IF NOT EXISTS companies (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    career_url TEXT,
    ats_source TEXT,
    category TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- JobRadar already uses a stable text hash as jobs.id throughout the app.
-- Keep that primary key during the Postgres migration so scan dedupe and
-- existing API payloads continue to work unchanged.
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    company_id BIGINT REFERENCES companies(id) ON DELETE SET NULL,

    title TEXT NOT NULL,
    url TEXT DEFAULT '',
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT DEFAULT '',
    categories TEXT DEFAULT '[]',

    first_seen TEXT NOT NULL,
    posted TEXT DEFAULT '',
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    is_open BOOLEAN DEFAULT TRUE,

    exp_min REAL,
    exp_max REAL,
    exp_label TEXT DEFAULT '',
    exp_source TEXT DEFAULT '',
    description TEXT DEFAULT '',
    has_description INTEGER DEFAULT 0,
    skills TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    employment_type TEXT DEFAULT '',

    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_is_open ON jobs(is_open);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location);

CREATE TABLE IF NOT EXISTS scan_runs (
    id BIGSERIAL PRIMARY KEY,
    scan_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    total_companies INTEGER DEFAULT 0,
    companies_scanned INTEGER DEFAULT 0,
    total_jobs_found INTEGER DEFAULT 0,
    new_jobs_found INTEGER DEFAULT 0,
    updated_jobs_found INTEGER DEFAULT 0,
    failed_companies INTEGER DEFAULT 0,
    error_message TEXT,
    scheduled_for TEXT DEFAULT '',
    send_alerts_after INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_company_status (
    id BIGSERIAL PRIMARY KEY,
    scan_run_id BIGINT REFERENCES scan_runs(id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    jobs_found INTEGER DEFAULT 0,
    new_jobs_found INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_company_status_run ON scan_company_status(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_scan_company_status_company ON scan_company_status(company_name);

CREATE TABLE IF NOT EXISTS user_job_status (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default_user',
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'other',
    message TEXT NOT NULL,
    url TEXT DEFAULT '',
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status);

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    plan_status TEXT DEFAULT 'free',
    plan_started_at TIMESTAMPTZ,
    plan_current_period_end TIMESTAMPTZ,
    stripe_customer_id TEXT DEFAULT '',
    stripe_subscription_id TEXT DEFAULT '',
    last_login_at TIMESTAMPTZ,
    last_active_at TIMESTAMPTZ,
    email_alerts_enabled INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_apply_list (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    stage TEXT DEFAULT 'to-apply',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    job_title TEXT DEFAULT '',
    job_company TEXT DEFAULT '',
    job_location TEXT DEFAULT '',
    job_url TEXT DEFAULT '',
    job_exp_label TEXT DEFAULT '',
    job_posted TEXT DEFAULT '',
    UNIQUE(user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_apply_user ON user_apply_list(user_id);

CREATE TABLE IF NOT EXISTS user_saved_searches (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kw TEXT DEFAULT '',
    loc TEXT DEFAULT '',
    exp_yrs TEXT DEFAULT '',
    since TEXT DEFAULT 'any',
    category TEXT DEFAULT 'All',
    tag TEXT DEFAULT '',
    email_alert_enabled INTEGER DEFAULT 0,
    alert_cursor_at TEXT DEFAULT '',
    last_alert_sent_at TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_searches_user ON user_saved_searches(user_id);
