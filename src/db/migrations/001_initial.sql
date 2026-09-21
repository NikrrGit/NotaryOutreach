-- Initial shared schema for Notary and VC outreach.
-- Enable foreign_keys on EVERY connection, including connections opened after
-- this migration. Application-generated stable IDs make replay-safe writes
-- possible; storage must use targeted upserts, never INSERT OR REPLACE.
PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    target_type TEXT NOT NULL CHECK (target_type IN ('notary', 'vc')),
    location TEXT NOT NULL CHECK (length(trim(location)) > 0),
    target_count INTEGER NOT NULL CHECK (target_count BETWEEN 1 AND 100),
    company_type TEXT CHECK (company_type IN ('UG', 'GmbH')),
    startup_description TEXT,
    industry TEXT,
    funding_stage TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'manual_review', 'ready_for_review', 'failed', 'completed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (target_type != 'notary' OR company_type IS NOT NULL),
    CHECK (target_type != 'vc' OR (
        company_type IS NULL
        AND startup_description IS NOT NULL AND length(trim(startup_description)) > 0
        AND industry IS NOT NULL AND length(trim(industry)) > 0
        AND funding_stage IS NOT NULL AND length(trim(funding_stage)) > 0
    )),
    UNIQUE (id, target_type)
);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    job_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('notary', 'vc')),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    organization TEXT,
    city TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (CASE WHEN json_valid(metadata_json) THEN json_type(metadata_json) = 'object' ELSE 0 END),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (job_id, target_type) REFERENCES jobs (id, target_type)
);

-- Eligibility is tri-state: 1 = eligible, 0 = ineligible, NULL = unknown.
-- Store each verification attempt separately to retain evidence history.
CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    candidate_id TEXT NOT NULL REFERENCES candidates (id),
    eligible INTEGER CHECK (eligible IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    evidence TEXT,
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (eligible IS NULL OR (
        evidence IS NOT NULL AND length(trim(evidence)) > 0
        AND source_url IS NOT NULL AND length(trim(source_url)) > 0
    ))
);

-- New or edited draft versions receive new IDs so evaluations and approvals
-- remain attached to the exact version reviewed.
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    candidate_id TEXT NOT NULL REFERENCES candidates (id),
    subject TEXT NOT NULL CHECK (length(trim(subject)) > 0),
    body TEXT NOT NULL CHECK (length(trim(body)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    draft_id TEXT NOT NULL REFERENCES drafts (id),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    -- Normalized score; NULL supports evaluators that return no numeric score.
    score REAL CHECK (score BETWEEN 0.0 AND 1.0),
    reasoning TEXT,
    issues_json TEXT NOT NULL DEFAULT '[]'
        CHECK (CASE WHEN json_valid(issues_json) THEN json_type(issues_json) = 'array' ELSE 0 END),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- A decision has one value, rather than potentially contradictory booleans.
-- Evaluation passing never implies human approval. No sending state exists.
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    draft_id TEXT NOT NULL REFERENCES drafts (id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'edited')),
    final_subject TEXT NOT NULL CHECK (length(trim(final_subject)) > 0),
    final_body TEXT NOT NULL CHECK (length(trim(final_body)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates (job_id);
CREATE INDEX IF NOT EXISTS idx_verifications_candidate ON verifications (candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_candidate ON drafts (candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evaluations_draft ON evaluations (draft_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_draft ON reviews (draft_id, created_at);

-- Storage updates updated_at explicitly when changing a mutable record.
PRAGMA user_version = 1;
COMMIT;
