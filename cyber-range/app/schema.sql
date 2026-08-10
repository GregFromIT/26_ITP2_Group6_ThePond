-- Cyber Range schema (SQLite)
--
-- VOCABULARY (renamed 2026-08; the old names are gone entirely)
--   theme      a subject area: networking, forensics, web. Holds six challenges.
--   challenge  one exercise inside a theme, backed by its own VM. Gets harder
--              as the number goes up.
--   flag       a token proving a student reached something, worth points.
--
-- Mirrors the original DB outline: user / password manager / themes /
-- challenges / VMs / active VMs / running instances / challenge points / UCP.

PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS leaderboard_theme;
DROP VIEW  IF EXISTS leaderboard_overall;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS throttle_event;
DROP TABLE IF EXISTS flag_submission;
DROP TABLE IF EXISTS user_challenge_points;
DROP TABLE IF EXISTS challenge_points;
DROP TABLE IF EXISTS running_instance;
DROP TABLE IF EXISTS active_vm;
DROP TABLE IF EXISTS challenge;
DROP TABLE IF EXISTS vm;
DROP TABLE IF EXISTS theme;
DROP TABLE IF EXISTS password_manager;
DROP TABLE IF EXISTS user;

-- ---------------------------------------------------------------- identity

CREATE TABLE user (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    uni_year        TEXT    NOT NULL,
    username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    points          INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,                      -- NULL = not locked out
    -- Set when staff issue a temporary password. While it is 1 the user is
    -- redirected to the change-password page and can reach nothing else.
    must_change_password INTEGER NOT NULL DEFAULT 0,
    -- Access level. See app/roles.py for what each one may do.
    role            TEXT    NOT NULL DEFAULT 'student'
                    CHECK (role IN ('student', 'moderator', 'admin')),
    role_set_at     TEXT,
    role_set_by     INTEGER REFERENCES user(user_id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login_at   TEXT
);
CREATE INDEX idx_user_role ON user(role);

-- Credentials live in their own table so a read on `user` never exposes a
-- hash. Nothing outside app/security.py should touch this table.
CREATE TABLE password_manager (
    password_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL UNIQUE REFERENCES user(user_id) ON DELETE CASCADE,
    password_hash TEXT    NOT NULL,
    algorithm     TEXT    NOT NULL DEFAULT 'scrypt',
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- There is no password_reset table and no email column anywhere. Password
-- recovery is entirely staff-driven: a moderator or administrator issues a
-- temporary password in person. See app/admin.py.

-- ------------------------------------------------------------- theme tree

CREATE TABLE theme (
    theme_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    category   TEXT    NOT NULL,             -- networking, forensics, web ...
    summary    TEXT    NOT NULL,
    weighting  REAL    NOT NULL DEFAULT 1.0, -- applied to the challenge total
    tile_image TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE vm (
    vm_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    proxmox_node  TEXT    NOT NULL,
    template_vmid INTEGER NOT NULL,            -- template cloned on launch
    cores         INTEGER NOT NULL DEFAULT 2,
    memory_mb     INTEGER NOT NULL DEFAULT 2048,
    notes         TEXT
);

CREATE TABLE challenge (
    challenge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id         INTEGER NOT NULL REFERENCES theme(theme_id) ON DELETE CASCADE,
    challenge_number INTEGER NOT NULL CHECK (challenge_number BETWEEN 1 AND 6),
    name             TEXT    NOT NULL,
    brief            TEXT    NOT NULL,
    difficulty       TEXT    NOT NULL DEFAULT 'Entry',
    vm_id            INTEGER REFERENCES vm(vm_id),
    tile_image       TEXT,
    UNIQUE (theme_id, challenge_number)
);

-- ------------------------------------------------------------- vm runtime

CREATE TABLE active_vm (
    active_vm_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vm_id        INTEGER NOT NULL REFERENCES vm(vm_id),
    proxmox_vmid INTEGER,                      -- vmid of the clone
    node         TEXT,
    console_url  TEXT,
    status       TEXT    NOT NULL DEFAULT 'provisioning',
                 -- provisioning | running | stopped | error
    started_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    stopped_at   TEXT
);

-- One student working one challenge. The only thing the user is ever handed;
-- it sits between them and the VM.
CREATE TABLE running_instance (
    instance_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES user(user_id) ON DELETE CASCADE,
    theme_id         INTEGER NOT NULL REFERENCES theme(theme_id),
    challenge_id     INTEGER NOT NULL REFERENCES challenge(challenge_id),
    active_vm_id     INTEGER REFERENCES active_vm(active_vm_id),
    access_key       TEXT    NOT NULL UNIQUE,  -- per-instance handle
    started_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at         TEXT,
    duration_seconds INTEGER,
    status           TEXT    NOT NULL DEFAULT 'in_progress'
                     -- in_progress | complete | abandoned
);
CREATE INDEX idx_instance_user ON running_instance(user_id, status);

-- ----------------------------------------------------------- scoring path

-- One row per flag. Only the hash is stored, so a dump of this table does not
-- hand out answers.
CREATE TABLE challenge_points (
    flag_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER NOT NULL REFERENCES challenge(challenge_id) ON DELETE CASCADE,
    theme_id     INTEGER NOT NULL REFERENCES theme(theme_id),
    label        TEXT    NOT NULL,
    flag_hash    TEXT    NOT NULL,
    points       INTEGER NOT NULL CHECK (points > 0)
);
CREATE INDEX idx_flag_challenge ON challenge_points(challenge_id);

-- The award ledger. UNIQUE(user_id, flag_id) is the lock that stops a user
-- claiming the same flag twice.
CREATE TABLE user_challenge_points (
    ucp_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES user(user_id) ON DELETE CASCADE,
    flag_id        INTEGER NOT NULL REFERENCES challenge_points(flag_id),
    theme_id       INTEGER NOT NULL REFERENCES theme(theme_id),
    challenge_id   INTEGER NOT NULL REFERENCES challenge(challenge_id),
    points_awarded INTEGER NOT NULL,
    awarded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, flag_id)
);
CREATE INDEX idx_ucp_user ON user_challenge_points(user_id);

-- Every attempt, right or wrong. Feeds the "flags played" column.
CREATE TABLE flag_submission (
    submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES user(user_id) ON DELETE CASCADE,
    challenge_id  INTEGER NOT NULL REFERENCES challenge(challenge_id),
    instance_id   INTEGER REFERENCES running_instance(instance_id),
    submitted_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    was_correct   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_submission_user ON flag_submission(user_id);

-- ------------------------------------------------------- abuse controls

CREATE TABLE throttle_event (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket      TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_throttle_bucket ON throttle_event(bucket, occurred_at);

CREATE TABLE audit_log (
    audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    event       TEXT NOT NULL,
    user_id     INTEGER,
    username    TEXT,
    source_ip   TEXT,
    detail      TEXT
);
CREATE INDEX idx_audit_time ON audit_log(occurred_at);
CREATE INDEX idx_audit_event ON audit_log(event, occurred_at);

-- ------------------------------------------------------------ leaderboards
--
-- SCOREBOARD ELIGIBILITY: administrators are excluded from every board. They
-- run the platform rather than compete on it. Moderators DO compete.
-- The rule lives here and in scoring.py — grep for role != 'admin'.

CREATE VIEW leaderboard_overall AS
SELECT
    u.user_id,
    u.username,
    u.points AS score,
    (SELECT MAX(awarded_at) FROM user_challenge_points x WHERE x.user_id = u.user_id) AS last_solved,
    (SELECT COUNT(*)        FROM user_challenge_points x WHERE x.user_id = u.user_id) AS solved_count,
    (SELECT COUNT(*)        FROM flag_submission       x WHERE x.user_id = u.user_id) AS flags_played
FROM user u
WHERE u.role != 'admin';

CREATE VIEW leaderboard_theme AS
SELECT
    t.theme_id,
    u.user_id,
    u.username,
    CAST(COALESCE(SUM(ucp.points_awarded), 0) * t.weighting AS INTEGER) AS score,
    MAX(ucp.awarded_at)                                                 AS last_solved,
    COUNT(ucp.ucp_id)                                                   AS solved_count,
    (SELECT COUNT(*) FROM flag_submission fs
       JOIN challenge c ON c.challenge_id = fs.challenge_id
      WHERE fs.user_id = u.user_id AND c.theme_id = t.theme_id)         AS flags_played
FROM theme t
CROSS JOIN user u
LEFT JOIN user_challenge_points ucp
       ON ucp.user_id = u.user_id AND ucp.theme_id = t.theme_id
WHERE u.role != 'admin'
GROUP BY t.theme_id, u.user_id;
