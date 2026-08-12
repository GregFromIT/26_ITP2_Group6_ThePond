# Data model

What each table holds, how they join up, and what the constraints are actually
stopping. The schema itself is `app/schema.sql` and it's commented, but this is
the version to read first.

Bear in mind the schema isn't finalised yet. See the to-do list at the bottom.

## The shape of it

```
                    password_manager        (one row per user, hashes only)
                           |
                         user  ─────────────────────────┐
                           |                            |
              running_instance                 user_challenge_points
              /       |        \                        |
          theme   challenge   active_vm         challenge_points
                      |            |                    |
                      └──── vm ────┘              (belongs to a challenge)

              flag_submission  (every attempt, right or wrong)
              audit_log        (standalone, nothing joins to it)
              throttle_event   (standalone, rate limit counters)
```

Two tables sit outside the joins on purpose. `audit_log` is append-only history
and nothing should ever join to it in application code, and `throttle_event` is
scratch data for rate limiting that gets pruned.

## Table by table

### `user`
Name, uni year, username, running points total, lockout state and role.

There is no email column. Nothing in the platform sends mail, so an address
would just be personal data we hold with no purpose. Password recovery is
staff-issued instead.

`points` is a running total rather than something calculated each time. It gets
incremented in the same transaction that writes the award. That's a denormalised
figure, which means it *could* drift out of step with the ledger, and the reason
we accepted that is the overall leaderboard becomes `SELECT points FROM user
ORDER BY points DESC` instead of an aggregate over every award every time
somebody loads the dashboard. If you ever suspect drift, this recalculates it:

```sql
UPDATE user SET points = (
  SELECT COALESCE(SUM(points_awarded), 0)
  FROM user_challenge_points WHERE user_id = user.user_id
);
```

`role` has a `CHECK` constraint limiting it to student, moderator or admin. That
exists so a typo in a future bit of code can't create an account holding a role
nobody ever wrote permissions for.

### `password_manager`
One row per user, holding only the hash and the algorithm. It's a separate table
so that reading `user` never brings a password hash along with it, and so
there's exactly one file in the codebase (`app/security.py`) touching
credentials at all.

### `theme`
A subject area: networking, forensics, web. Name, category, summary and a
`weighting` multiplier.

The weighting is applied when a leaderboard is read, not stored into anyone's
score. Change a theme's weighting and every board updates on the next page load,
with nobody needing to be rescored.

### `challenge`
Six per theme, enforced by `CHECK (challenge_number BETWEEN 1 AND 6)` plus
`UNIQUE (theme_id, challenge_number)`. Each one points at a `vm` row.

Fair warning: the "six" assumption is baked into the per-challenge scoreboard,
which has six hard-coded columns in `scoring.theme_challenge_matrix()` and in
`theme_detail.html`. If challenges-per-theme ever becomes variable, those two
places need rewriting to build columns dynamically.

### `vm`
The catalogue of Proxmox templates: node, template vmid, cores, memory. Nothing
here is a running machine, it's the list of what *can* be cloned.

### `active_vm`
One row per clone that's been made, with its Proxmox vmid, node, console URL and
state (provisioning, running, stopped, error).

Rows are kept after the VM is destroyed rather than deleted, so `/admin/sessions`
can show clones that failed to tear down properly.

### `running_instance`
One student's live run at one challenge. Holds the start time, end time,
duration and status (in_progress, complete, abandoned), plus an `access_key`
which is the per-instance handle.

This is the only thing a student is ever handed. They never get a direct
reference to a VM.

Only one row per user should be `in_progress` at a time. That is enforced in
`themes.launch()` rather than by a constraint, which is a gap worth knowing
about: a partial unique index would be the stronger version.

### `challenge_points`
One row per flag: which challenge it belongs to, a label, the points, and the
flag itself as a SHA-256 hash.

Storing the hash rather than the plaintext means dumping this table gives you
nothing usable. Submissions get stripped and lower-cased before hashing, so a
trailing space doesn't fail a correct answer. Change that normalisation and
every stored hash becomes wrong, so treat it like a schema change.

### `user_challenge_points`
The award ledger. One row each time a student first captures a flag.

`UNIQUE (user_id, flag_id)` is the important part of the whole schema. It is
what makes claiming the same flag twice impossible, and because it's a database
constraint rather than an `if` in Python, two browser tabs submitting at the
same instant can't get round it. The second one raises rather than paying out.

### `flag_submission`
Every attempt, correct or not. This is what the "flags played" column on the
leaderboards counts, and it's also the trail for spotting somebody brute-forcing
a flag.

### `audit_log`
Sign-ins, failures, lockouts, role changes, temporary passwords, instance
launches and closes. Append-only. Nothing in the platform edits or deletes rows
here.

It stores the raw source IP, which is fine because nothing renders these rows
back into a page except the staff console.

### `throttle_event`
Rate limit counters, one row per attempt, bucketed by action and key. In the
database rather than in memory so several gunicorn workers share one allowance
instead of each getting a full one.

Nothing prunes this yet. `throttle.prune()` exists but nothing calls it.

## The two views

`leaderboard_overall` and `leaderboard_theme` both filter `role != 'admin'`, so
administrators never appear on a board. Moderators do, because they're usually
students helping run a class.

That filter is repeated in `scoring.theme_challenge_matrix()` and
`scoring.user_stats()`, because SQLite views can't share a predicate. All four
have to agree. Grep for `role != 'admin'` before changing eligibility.

## Still to sort out

- [ ] **The schema isn't final.** Still being agreed in the group.
- [ ] **No migrations.** `flask --app wsgi init-db` drops and rebuilds
      everything. That's fine while the only data is seeded, and destroys real
      scores the moment there are any. Either add Alembic (it works with SQLite)
      or agree an export/import step, and do it before the first real class.
- [ ] **One-instance-per-user isn't a constraint.** Enforced in application code
      only. A partial unique index on `(user_id)` where `status = 'in_progress'`
      would make it structural.
- [ ] **Nothing prunes `throttle_event` or `audit_log`.** Both grow forever.
- [ ] **No backup story** for the SQLite file at all.
- [ ] **Scale.** SQLite is fine for a cohort. The first sign of outgrowing it is
      "database is locked" under load, and the fix is PostgreSQL. `app/db.py` is
      the only file that would need changing.
