-- pool_slots: instances cloned ahead of demand, sitting idle until checked out.
CREATE TABLE IF NOT EXISTS pool_slots (
    vmid        INTEGER PRIMARY KEY,
    challenge   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'available',  -- available | in_use | resetting
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pool_status ON pool_slots(challenge, status);

-- sessions: a pool_slot checked out to a specific team/user, with its own
-- per-session flag and expiry.
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    challenge    TEXT NOT NULL,
    vmid         INTEGER NOT NULL UNIQUE REFERENCES pool_slots(vmid),
    ip_address   TEXT,
    flag         TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'  -- active | expired | destroyed
);

CREATE INDEX IF NOT EXISTS idx_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_session_status ON sessions(status);
