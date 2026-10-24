-- AetherFlow Suite v1.0 — SQLite Schema
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS environments (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  source TEXT CHECK(source IN ('agent','static')),
  capabilities_json TEXT,
  last_heartbeat TIMESTAMP,
  status TEXT CHECK(status IN ('active','offline','maintenance'))
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  priority INTEGER DEFAULT 5 CHECK(priority BETWEEN 0 AND 10),
  status TEXT NOT NULL,
  environment TEXT REFERENCES environments(id),
  input_path TEXT,
  output_paths TEXT,
  error_type TEXT,
  retry_count INTEGER DEFAULT 0,
  checkpoint_data TEXT,
  cloud_consent INTEGER DEFAULT 0 CHECK(cloud_consent IN (0,1)),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  api_key_encrypted TEXT,
  quota_limit INTEGER,
  quota_used INTEGER DEFAULT 0,
  quota_reset_at TIMESTAMP,
  enabled INTEGER DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_accounts_provider ON accounts(provider);

CREATE TABLE IF NOT EXISTS provider_quotas (
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  limit_value INTEGER,
  current_value INTEGER DEFAULT 0,
  reset_at TIMESTAMP,
  PRIMARY KEY (provider, metric)
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON job_events(created_at);

-- Retention cleanup should be performed by a scheduled maintenance task.
-- DELETE FROM job_events WHERE created_at < datetime('now', '-30 days');
