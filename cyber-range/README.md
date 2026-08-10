# Cyber Range

Flask + SQLite orchestrator for a cyber teaching range. Students register, pick
a theme, launch a challenge, and the platform clones a Proxmox VM, times the
session, grades flags and keeps the leaderboards.

**Vocabulary.** A *theme* is a subject area (networking, forensics, web) holding
six *challenges*; each challenge is one exercise backed by its own VM. An
*instance* is one student's live run at one challenge.

Everything in the outline is wired end to end. The one thing faked by default is
Proxmox itself: `PROXMOX_BACKEND=simulate` invents a vmid and console URL so the
whole flow can be demonstrated without a cluster. Switch it to `api` and the same
code path clones and starts real VMs.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install Flask                       # or: pip install -r requirements.txt

flask --app wsgi init-db                # build the schema
flask --app wsgi seed-db                # 3 themes, 18 challenges, 6 VM templates, demo users
flask --app wsgi run --debug            # http://127.0.0.1:5000
```

On Windows the only differences are the venv activation line
(`.venv\Scripts\activate`) and, for a production run, `waitress-serve
--listen=127.0.0.1:8000 wsgi:app` instead of gunicorn — `pip install -r
requirements.txt` picks the right server automatically. Everything else,
including the `flask --app wsgi` commands, is identical on Linux, macOS and
Windows.

Demo accounts all use the password `rootroot`: `bpt` is the system
administrator, `vstergiou` is a moderator, and `mbates`, `lhardie`, `gthomas`
and `demo` are students.

That password is 8 characters, below the platform's own 12-character minimum. It
works only because seeding writes the hash directly, while the length rules are
enforced in the registration and reset forms — nobody could choose it through
the web interface. It is a demo convenience, not an example to follow: delete or
re-password every seeded account before a real cohort uses the platform. Seeded flags follow the pattern
`flag{challenge_one_entry}`, `flag{challenge_one_bonus}`,
`flag{challenge_two_entry}` and so on.

There is no email anywhere in the platform. If a demo account gets locked out or
you forget a password, sign in as `bpt` and issue a temporary one from the staff
console.

Checks: `python -m tests.test_flow` — 47 assertions across registration, lockout,
reset, launch, scoring, access control, CSRF, throttling and headers. No pytest
required.

For anything beyond the lab, set `RANGE_ENV=production` and a real
`FLASK_SECRET_KEY`. The app refuses to start in production without one.

## What is where

```
wsgi.py               entry point
app/__init__.py       application factory, template filters
app/config.py         every setting, all env-overridable
app/schema.sql        tables + the two leaderboard views
app/db.py             connection handling, init-db / seed-db CLI
app/security.py       hashing, lockout arithmetic, temporary passwords
app/csrf.py           per-session CSRF tokens, enforced on every unsafe method
app/throttle.py       sliding-window rate limits, stored in SQLite
app/audit.py          security event log
app/auth.py           register, login, logout, forgot, reset
app/dashboard.py      landing page, dashboard
app/themes.py         theme list, challenges, launch, timer, flags, teardown
                      (a running challenge works inline — no session page)
app/scoring.py        flag grading, leaderboards, per-challenge matrix
app/proxmox.py        simulate | api backends behind one interface
app/seed.py           demo content
```

## Preview without installing anything

`preview/cyber-range-preview.html` is a single self-contained file showing all
twelve screens with a switcher across the top. No Python, no server, no network —
double-click it, or drop it in a report or an email. Nothing in it is clickable.

Regenerate it after any template or CSS change:

```bash
python tools/build_preview.py
```

It renders the real templates through Flask's test client, so it cannot drift
from the actual interface the way a hand-built mockup would.

## Documentation

`docs/CODE_MAP.md` explains what every Python file does, in plain language, for
someone picking the codebase up cold — the request lifecycle, each file's job,
and a "where to look when something breaks" table. Read that before the table
below if you are new to the project.

## Where to add things

Every module opens with a docstring covering what it does and what to change
when extending it. The short version:

| You want to | Go to |
|---|---|
| Add a theme, challenge or flag | `app/seed.py` — data only, no code change |
| Add a page | new blueprint + register it in `app/__init__.py` |
| Add a registration field | `schema.sql`, `auth.register`, `register.html` |
| Add a setting | `app/config.py` and `.env.example` |
| Add a rate limit | `LIMITS` at the top of `app/throttle.py` |
| Add a leaderboard | a view in `schema.sql`, a function in `app/scoring.py` |
| Support another hypervisor | `app/proxmox.py` — nothing else imports it |
| Change the schema | read the migration warning at the top of `app/db.py` first |

Two rules worth stating once: every `<form method="post">` needs the CSRF hidden
field or it will be rejected, and every route that touches a session must go
through `_owned_instance()`. Run `python -m tests.test_flow` before committing.

## How the outline maps to the schema

| Outline | Table | Notes |
|---|---|---|
| User | `user` | Name, uni year, username, points, lockout state, role. No email column |
| Password manager | `password_manager` | Separate table, one row per user. Only `security.py` reads it |
| Challenges (now *themes*) | `theme` | Name, category, summary, weighting |
| (now *challenges*) | `challenge` | Six per theme, each mapped to a VM template |
| VMs | `vm` | Template catalogue: node, template vmid, cores, memory |
| Active VMs | `active_vm` | One row per clone, with its vmid, console URL and state |
| Running challenges | `running_instance` | The user-facing session: access key, start, end, duration, status |
| Challenge points | `challenge_points` | One row per flag: hashed flag and its points |
| UCP | `user_challenge_points` | The award ledger. `UNIQUE(user_id, flag_id)` is the lock |
| (attempts) | `flag_submission` | Every submission, right or wrong — the "flags played" column |

Two design points worth defending in the report:

- **Flags are stored as SHA-256, not plaintext.** A dump of `challenge_points`
  gives away nothing. Submissions are lower-cased and stripped before hashing so
  a trailing space never fails a correct answer.
- **The double-award lock is a database constraint, not application logic.**
  `UNIQUE(user_id, flag_id)` cannot be raced or bypassed by a second tab.

`user.points` is the raw sum of awarded points and drives the overall
leaderboard, exactly as the outline described. Theme weighting is applied on
read in `leaderboard_theme` and in the per-challenge matrix, so changing a
weighting does not require rescoring anyone.

## Roles

Three levels, defined in one matrix in `app/roles.py`:

| | Student | Moderator | Administrator |
|---|---|---|---|
| Take challenges | yes | yes | yes |
| View accounts and sessions | — | yes | yes |
| Unlock / lock an account | — | yes | yes |
| Issue a temporary password | — | yes | yes |
| Force-close a session, free its VM | — | yes | yes |
| Read the audit log | — | yes | yes |
| Grant or remove moderator/admin | — | — | yes |
| Appears on the leaderboards | yes | yes | **no** |

Registration always creates a student. The first administrator is promoted from
the command line, which is the one privilege escalation that cannot be done over
the web:

```bash
flask --app wsgi set-role bpt admin
```

Administrators are excluded from every leaderboard and have no rank on their own
dashboard: they run the platform rather than compete on it, and a staff account
in the rankings reads as the assessor playing against the assessed. Moderators
do compete — they are typically students helping run a class, and hiding them
would cost them their own results. The rule lives in the two leaderboard views
in `schema.sql` and in `theme_challenge_matrix()` / `user_stats()`; all four must
agree, so grep for `role != 'admin'` if it ever changes.

Rules the console enforces, each for a specific failure it prevents:

- **Nobody can act on an equal or senior account.** One moderator cannot lock,
  unlock or reset another, and neither can touch an administrator.
- **An admin can step down, but not if they are the last one.** Handover is
  normal; leaving nobody able to grant access is not. Recovery from that would
  need shell access.
- **Password recovery is staff-issued and in person.** With no email address on
  file there is no self-service reset. A moderator or administrator issues a
  temporary password, which appears once on their screen, is never stored
  readable or written to the log, and forces a change at next sign-in. The
  consequence, which belongs in the report: staff can effectively take over any
  account below their rank. That is inherent to staff-held recovery, and it is
  why the action is audited by name and why nobody can use it sideways or
  upwards.
- **Locking takes effect immediately**, including on a session already open.
- **Every role change, lock, unlock and staff-closed session is audited** with
  the name of the staff member who did it.

Deliberately absent: any power to edit scores or delete flag awards. Scores are
the evidence of what a student did, and the award ledger is what makes
double-claiming impossible. A cheating case should be a documented, deliberate
database action with a reason recorded, not a button a tired moderator can
misfire. Raise it with the client before adding one.

## Account rules

Three failed sign-ins lock the account for 15 minutes (`MAX_LOGIN_ATTEMPTS`,
`LOCKOUT_MINUTES`; set the latter to 0 for admin-unlock-only). A successful
password reset clears the lock and kills any live session for that account.
Reset tokens are random 32-byte values, stored only as a hash, single-use, and
dead after 24 hours. Sessions expire after an hour idle.

A taken username is reported plainly, because usernames are printed on the
leaderboards and hiding them would protect nothing. There are no email addresses
to enumerate.

## Hardening

| Control | Where |
|---|---|
| Parameterised SQL everywhere | all queries go through `db.query` / `db.execute` |
| Output escaping | Jinja autoescape, no `\|safe` anywhere |
| CSRF tokens on every unsafe request | `app/csrf.py`, compared with `compare_digest` |
| CSP with `script-src 'self'` (no inline script) | `security_headers` in `app/__init__.py` |
| `X-Frame-Options`, nosniff, Referrer-Policy, Permissions-Policy, HSTS | same |
| Secret key from env, generated per-instance in dev, mandatory in prod | `resolve_secret_key` |
| Secure + HttpOnly + SameSite=Strict cookies | `app/config.py` |
| Rate limits on login, registration and flag submission | `app/throttle.py` |
| Per-source throttle in front of per-account lockout | `auth.login` |
| Session cleared and CSRF rotated on login, logout and password change | `app/auth.py` |
| Request size cap and per-field length caps | `MAX_CONTENT_LENGTH`, `auth.field` |
| Ownership checks on every session route | `challenges._owned_instance` |
| Console URL never rendered into the page | `challenges.console` |
| Audit log of auth and VM events | `app/audit.py` |
| Error pages instead of tracebacks | `register_error_handlers` |

The rate limits all live in one dictionary at the top of `app/throttle.py` so
they can be reviewed in one place. Counters are stored in SQLite rather than
process memory, so several gunicorn workers share one allowance.

Threat model note worth putting in the report: the users of this platform are
being actively taught to attack things, and the machines they are given are
deliberately vulnerable. The orchestrator should be treated as sitting on a
hostile network, not a trusted one.

## Connecting real Proxmox

The project cluster is already the default. Only the token secret is missing,
because it is the one value that must never be in the repo:

```bash
pip install proxmoxer requests
export PROXMOX_BACKEND=api
export PROXMOX_TOKEN_SECRET='...'          # from Proxmox, not from git
```

For reference, the defaults now baked into `app/config.py`:

| Setting | Value |
|---|---|
| `PROXMOX_HOST` | `10.1.21.151` |
| `PROXMOX_NODE` | `pve` |
| `PROXMOX_TOKEN_ID` | `root@pam!root` |
| `PROXMOX_STORAGE` | `local-lvm` (full clones only) |
| `PROXMOX_FULL_CLONE` | `0` — linked clones by default |

Two things about that configuration to decide on rather than inherit:

- **`root@pam` is far more access than this app needs.** A token under root can
  do anything on the cluster, so a flaw in the orchestrator — or anyone who
  reads the secret off the server — owns every VM, not just the teaching ones.
  Create a dedicated user instead and give its token only `VM.Clone`,
  `VM.Config.*`, `VM.PowerMgmt`, `VM.Audit` and `VM.Allocate` on the template
  pool. Tick **Privilege Separation** when creating the token, or it silently
  inherits the user's full rights.
- **Certificate verification is off by default** for this cluster, because a
  host reached by IP will have a self-signed certificate. That is a reasonable
  lab trade-off and a bad habit to carry into anything else — with it off,
  anything on the path between the app and `10.1.21.151` can impersonate the
  hypervisor and harvest the API token. Installing the cluster CA on the app
  host and setting `PROXMOX_VERIFY_SSL=1` closes it.

Then set each challenge's `vm_id` to a `vm` row whose `template_vmid` is a real
template. `clone_and_start` takes the next cluster id, makes a linked clone,
starts it and hands back a noVNC console URL; `stop_and_destroy` stops and deletes
it when the session closes. Give the API token only what it needs:
`VM.Clone`, `VM.Config.*`, `VM.PowerMgmt`, `VM.Audit`, `VM.Allocate` on the
template pool.

## Still open

These are known gaps, not oversights. They are the next security work package.

- **Per-user network isolation.** Every clone still lands on the same bridge.
  Until each session gets its own VLAN or SDN zone, students can reach each
  other's machines — and a compromised challenge VM has a path to the
  orchestrator. This is the most serious item on the list.
- **Console authentication.** `/challenges/session/<id>/console` checks that the
  session is yours before redirecting, and the URL is no longer rendered into the
  page, but the student still has no Proxmox credential. A ticket-issuing proxy
  (or Proxmox's own `/access/ticket` with a short-lived per-session user) is the
  real fix.
- **Idle reaping.** A session left open holds its VM. A scheduled job should close
  sessions past a time limit and free the clone. The idle *session* timeout is in;
  the VM teardown side is not.
- **Admin interface.** Themes, challenges, VMs and flags are seeded from
  `app/seed.py`; there is no web UI for staff, and no role separation beyond the
  unused `user.is_admin` column.
- **No independent testing.** The 47 checks are ours. Nothing here has been
  through a scanner (ZAP, Burp) or a review by anyone outside the team, which is
  what would let you make a claim about this in the report.
- **Recovery depends on staff being reachable.** Removing email removed a whole
  class of attack — reset-link interception, mail spoofing, address enumeration,
  mail-bombing — and removed the platform's only way to contact a user. A student
  locked out at 11pm before an assessment has no path until someone answers.
- **Backups and log retention.** The audit log grows without bound and nothing
  backs up the SQLite file.
