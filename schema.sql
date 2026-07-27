-- db/schema.sql
--
-- Single table by design. When this moves to SQLAlchemy in v2, this maps
-- directly onto one declarative model (e.g. class Instance(Base)) with
-- these columns as its attributes - no restructuring needed.

CREATE TABLE IF NOT EXISTS instances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,   -- e.g. "lockedshields-team01"
    challenge   TEXT NOT NULL,          -- challenge/template identifier
    vmid        INTEGER NOT NULL UNIQUE,
    node        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',  -- active | destroyed
    created_at  INTEGER NOT NULL,       -- unix epoch
    expires_at  INTEGER NOT NULL,       -- unix epoch
    destroyed_at INTEGER                -- unix epoch, NULL until destroyed
);

CREATE INDEX IF NOT EXISTS idx_instances_status ON instances(status);
CREATE INDEX IF NOT EXISTS idx_instances_expires_at ON instances(expires_at);
