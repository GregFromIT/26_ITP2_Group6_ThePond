# Migrating onto the group's SQLAlchemy schema

Four decisions have been made:

1. The group's `app/uploads.py` (submission pipeline) replaces the VM image
   upload built here.
2. `the_pond.db` owns challenges. The tables in `cyber_range.sqlite` are
   superseded.
3. `the_pond.db` owns users. Same.
4. Quarantine lives in `challenge_ingestion/storage.py`, not in Proxmox and not
   in this app.
5. The validation worker runs as a separate process.

This document is the map between the two schemas, what each change costs, and
the questions still open. It is not a migration script — several of the
decisions below are the group's to make, not something to guess at.

## How big this is

Seven modules read the old tables directly: `admin.py`, `auth.py`,
`dashboard.py`, `scoring.py`, `security.py`, `seed.py`, `themes.py`. Between
them there are roughly 90 hand-written SQL statements going through
`db.query()` and `db.execute()`, all of which become SQLAlchemy queries against
models that live in `db/`.

That is a rewrite of the data layer, not a patch. It should be staged, and each
stage should keep `tests/test_flow.py` passing before the next one starts.
Doing it in one pass across a working system with no migrations tooling is how a
project loses a week.

## Table mapping

| Here (`cyber_range.sqlite`) | Group (`the_pond.db`) | Notes |
|---|---|---|
| `user` | `users` + `roles` | Identity splits from role |
| `password_manager` | `user_credentials` | Near-exact match, see below |
| `challenge` | `challenges` | |
| `theme` | — | **No equivalent. See open question 1** |
| `vm` | `vm_templates` | Theirs carries more: node, snapshot, role, boot order, network |
| `challenge_points` | `challenge_flags` | Both store a hash and points |
| `running_instance` | `challenge_instances` | |
| `active_vm` | `vm_instances` | |
| `flag_submission` | `flag_submissions` | Theirs requires `instance_id`; ours allows null |
| `user_challenge_points` | `user_solves` | Both are the award ledger |
| `audit_log` | `audit_logs` | |
| `throttle_event` | — | **No equivalent. Needs a new model** |

## What maps cleanly

`user_credentials` is a better fit than expected. It already has
`password_hash`, `must_change_password`, `failed_login_count` and
`locked_until` — which is exactly the lockout and temporary-password state built
here, under different names. The rules in `app/security.py` port across almost
unchanged; only the column names move.

`user_solves` and `flag_submissions` are the same shape as the award ledger and
the attempt log. `user_solves` has `awarded_points` per solve, which is what
`user_challenge_points.points_awarded` holds.

Roles map one to one, once renamed:

| Here | Group | `role_level` |
|---|---|---|
| `student` | `user` | 1 |
| `moderator` | `manager` | 2 |
| `admin` | `sysadmin` | 3 |

Their `manager` description — "challenge demo and password reset permissions" —
is the moderator role as built. The rename touches `app/roles.py`, every
template using `can()`, the seed data, the tests and the docs, but it is
mechanical.

## What does not map, and needs a decision

### 1. Themes have nowhere to go

This platform is two levels: a **theme** holds six **challenges**. The group's
schema is one level: `challenges`, each with a `category` string.

That affects more than a table name. Theme weighting, the per-theme
leaderboards and the six-column per-challenge scoreboard all assume the
grouping exists. Three options:

- Treat `challenges.category` as the theme. Cheapest, but weighting has nowhere
  to live and the boards become per-category aggregates computed on read.
- Add a `themes` table to `db/` and a `theme_id` on `challenges`. Keeps the
  feature, but it is a change to the group's schema and somebody has to own it.
- Drop themes. Honest if nobody wants them, but it is a visible feature of the
  platform and the client has seen it.

**This is the biggest open question and it blocks the scoring migration.**

### 2. Account approval has no columns

`users` has `is_active`, which is not the same thing: approval is a one-time
gate on a new registration, `is_active` is an enable/disable switch staff use
later. Conflating them means a suspended account and an unapproved one look
identical, and "approve" would silently also mean "un-suspend".

Needs `approval_status`, `approved_at` and `approved_by` on `db/user_models.py`
— their file. Worth raising with whoever owns it rather than editing it here.

### 3. Scores are computed, not stored

Here, `user.points` is a running total and the leaderboard is
`SELECT points FROM user ORDER BY points DESC` — which is what the original
outline asked for. The group's schema has no `points` column; the total comes
from summing `user_solves.awarded_points`.

Theirs is the more correct design and the drift problem disappears with it. But
`app/scoring.py` is written around the stored total, so all four leaderboard
functions change. Worth doing, worth not underestimating.

### 4. `uni_year` does not exist

Registration collects name, year of study and username. `users` has `username`
and `display_name`, no year. Either add a column, or drop the field from
registration. The client asked for it, so dropping it should be their call.

### 5. Rate limiting has no table

`throttle_event` has no counterpart. It is self-contained and easy — a new model
in `db/` with `bucket` and `occurred_at`, matching the current table. The limits
themselves do not change.

## Suggested staging

Each stage ends with the test suite green.

1. **Repo layout and one `db` instance.** Decide where `db/` sits relative to
   `pond-sec/` (open question 4 from the earlier list). `app/orm.py` already
   defers to `db.orm` when importable, so this stage is mostly about imports and
   the `SQLALCHEMY_BINDS` configuration.
2. **Users and auth.** Move `auth.py`, `security.py` and the role checks onto
   `users`, `roles` and `user_credentials`. Nothing else can move first, because
   everything holds a `user_id`.
3. **Challenges, VMs and flags.** `themes.py` and `seed.py`. Blocked on the
   themes decision.
4. **Scoring.** `scoring.py` onto `user_solves` and `flag_submissions`. Blocked
   on stage 3.
5. **Retire the local upload feature.** Remove `app/uploads.py` and
   `app/fetcher.py` once the group's submission pipeline is working. Not before
   — see below.

## Sequencing the upload handover

The group's `uploads.py` replaces the one here, but it does not exist yet. The
local one works, is tested and is the only way to get an image into the platform
today, so deleting it now would leave a hole for however long the replacement
takes.

Suggested order: build the group's pipeline, prove it end to end, then delete
`app/uploads.py`, `app/fetcher.py`, `app/templates/admin/uploads.html` and the
upload tests in one commit. The SSRF guards in `app/fetcher.py` are worth
keeping regardless — if the new pipeline accepts a URL, it needs them, and they
are already written and tested.

## What has already been done for this

`app/orm.py` imports `db.orm` when the `db/` package is importable and only
defines its own SQLAlchemy instance when it is not. Two instances in one process
is a confusing failure — models register against whichever one declared them, so
`create_all()` builds half the tables and cross-boundary relationships fail to
resolve with no error explaining why. That file is a bridge for the merge, not
something to keep: once the trees are together, delete it and import `db.orm`
directly.
