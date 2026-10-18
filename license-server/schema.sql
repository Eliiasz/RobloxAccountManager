-- ANane RAM license database (Cloudflare D1). Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS keys (
    key           TEXT PRIMARY KEY,
    type          TEXT NOT NULL CHECK (type IN ('30d', 'lifetime')),
    note          TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    hwid          TEXT,
    activated_at  INTEGER,
    expires_at    INTEGER,
    last_seen_at  INTEGER,
    last_version  TEXT NOT NULL DEFAULT '',
    revoked       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_failures (
    ip         TEXT PRIMARY KEY,
    count      INTEGER NOT NULL,
    window_at  INTEGER NOT NULL
);
