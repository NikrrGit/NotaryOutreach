-- Track explicit sends and prevent resending an ambiguous SMTP attempt.
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY NOT NULL,
    draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id),
    recipient TEXT NOT NULL,
    sender TEXT NOT NULL,
    message_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed', 'unknown')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
PRAGMA user_version = 2;
COMMIT;
