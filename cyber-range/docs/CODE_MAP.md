# What each Python file does

A plain-language tour of the codebase, written for someone picking it up who
did not write it. Every file also has a docstring at the top saying the same
thing in more detail; this is the map that tells you which one to open.

Vocabulary, because the names changed once and the old ones still appear in
older meeting notes:

- **theme** — a subject area such as networking or forensics. Holds six challenges.
- **challenge** — one exercise inside a theme, backed by its own VM.
- **instance** — one student's live run at one challenge.
- **flag** — a token proving a student got somewhere. Worth points.

---

## The shape of a request

Every page load takes the same path. Knowing it makes the rest of this document
easier to follow:

1. **`wsgi.py`** hands the request to the app object.
2. **`app/__init__.py`** runs three checks in order: force HTTPS, verify the CSRF
   token, load the signed-in user.
3. A **blueprint** (`auth.py`, `dashboard.py`, `themes.py`, `admin.py`) handles it.
4. That blueprint asks the **support modules** for what it needs — `db.py` for
   data, `scoring.py` for points, `roles.py` for permissions, `proxmox.py` for a
   VM.
5. A template in `app/templates/` renders the answer, and
   `security_headers()` in `app/__init__.py` stamps the response on the way out.

Dependencies run one way: blueprints use support modules, and support modules
never import blueprints. The one deliberate exception is documented where it
happens (`admin.py` imports `themes._close` inside a function, so staff can
close a student's session without duplicating the teardown logic).

---

## Starting up

### `wsgi.py` — 27 lines
The object a web server imports. Creates the app and nothing else. Its comments
cover how to run it in development, under gunicorn on Linux, and under waitress
on Windows, plus two warnings worth reading before deploying: never run with
`--debug` on a network, and set `TRUSTED_PROXIES` if anything sits in front.

### `app/__init__.py` — 278 lines
The assembly point, and the file to read first. `create_app()` wires every other
module together, so this is the map of the codebase in code form.

Also holds the things that apply to *every* request, wherever they came from:

- `resolve_secret_key()` — reads the key from the environment, generates a
  development one if there isn't one, and refuses to start in production
  without it. There is no hard-coded default anywhere.
- `force_https()` — redirects plain HTTP in production.
- `security_headers()` — the Content-Security-Policy, framing, sniffing and
  HSTS headers added to every response.
- `session_is_idle()` — signs people out after an hour untouched.
- `register_error_handlers()` — friendly error pages, and the reason a crash
  never shows a stack trace to a browser.
- `register_filters()` — the `|stamp` and `|duration` template filters, so a
  timestamp looks the same on every page.

**Add a blueprint here.** Nowhere else needs to know about it.

### `app/config.py` — 96 lines
Every setting in one place, each reading an environment variable with a
sensible default. Covers session and cookie policy, the lockout rules, password
length, request size caps and all the Proxmox connection details.

`RANGE_ENV=production` tightens the defaults as a group: secure-only cookies,
HTTPS enforced, TLS verified, and no start-up without a real secret key.

**Add a setting here and document it in `.env.example`.** Read it with
`current_app.config[...]`, never `os.environ` directly, or tests can't override it.

---

## Data

### `app/db.py` — 131 lines
The only file that opens a database connection. Gives everything else two
functions: `query()` for reads and `execute()` for writes, both taking values
separately from the SQL. That separation is what stops SQL injection, and it is
why nothing else in the codebase builds a query with string formatting.

Also provides the `flask --app wsgi init-db` and `seed-db` commands.

**Read the migration warning at the top before changing the schema.** `init-db`
drops and rebuilds everything — fine now, destructive once real scores exist.

### `app/schema.sql` — not Python, but read it early
The tables, the constraints and the two leaderboard views. The constraints are
doing real work: `UNIQUE(user_id, flag_id)` is what makes claiming a flag twice
impossible, and the `CHECK` on `role` is what stops a typo creating an account
with permissions nobody wrote.

### `app/seed.py` — 226 lines
Demo content: three themes, eighteen challenges, six VM templates, flags and
the demo accounts. This is where challenges live until someone builds a staff
interface for them, so **adding a theme is editing this file, not writing code.**

`_fabricate_scores()` invents leaderboard history so the boards aren't empty in
a demo. Delete that call before a real cohort uses the platform.

---

## Security support

These four are small, and they are where a marker will look.

### `app/security.py` — 177 lines
Passwords and lockouts. The only file that reads or writes the password table.

- `set_password()` / `password_matches()` — hashing via Werkzeug's scrypt.
- `password_problems()` — the rules a new password must pass, returned as a
  list so the user sees every problem at once instead of one per attempt.
- `issue_temporary_password()` — the staff recovery path. Generates three words
  and a number, returns it once in the clear, and flags the account to force a
  change. It is never stored readable and never logged.
- `register_failure()` / `lockout_remaining()` / `clear_lockout()` — the
  three-strikes lockout.

### `app/csrf.py` — 66 lines
One token per session, required on every POST, compared with
`secrets.compare_digest` so the check can't be timed. Written by hand rather
than pulled from a library so the mechanism is visible in the code.

**Every `<form method="post">` needs the hidden `_csrf` field** or the
submission is rejected with a 400.

### `app/throttle.py` — 106 lines
Rate limiting. Every limit is in one dictionary at the top of the file, so the
whole policy reads in one screen: sign-in attempts per source and per account,
registrations, password changes, flag guesses per challenge.

Counters live in SQLite rather than memory, because several gunicorn workers
sharing a dict would each get the full allowance.

### `app/roles.py` — 151 lines
Who is allowed to do what. Three roles — student, moderator, administrator —
and one `PERMISSIONS` matrix listing which roles hold which capability.

- `can()` — used in templates to hide controls.
- `require()` — the decorator that actually blocks the request.
- `outranks()` — stops staff acting on accounts at their own level or above.

**Use both `require()` and `can()`.** Hiding a button is courtesy; the decorator
is the access control.

---

## Pages

### `app/auth.py` — 306 lines
Register, sign in, sign out, change password. Decides *who someone is*; the
rules it enforces live in `security.py` and the limits in `throttle.py`.

Two things here look like bugs and are not, so read the comments before
changing them: a taken username is reported plainly (usernames are public on
the leaderboards), and there is no forgotten-password page at all — with no
email address stored, recovery is staff-issued and in person.

Also holds `login_required`, the decorator most other views use, and
`force_password_change`, which pins an account with a temporary password to the
change-password page until it sets its own.

### `app/dashboard.py` — 75 lines
The landing page and the signed-in dashboard. Deliberately thin: it asks
`scoring.py` for numbers and hands them to a template. Anything that computes a
score belongs in `scoring.py` so the dashboard and the theme pages can't disagree.

### `app/themes.py` — 394 lines
The biggest file and the core of the platform. Themes, challenges, VM
instances and flag submission.

A session runs like this: `launch()` clones a VM and writes the instance row;
the challenge's tile on the theme page becomes a working panel with a timer,
console link and flag box; `flag()` grades submissions and closes the challenge
automatically when the last flag is captured; `close()` stops the clock and
tears the VM down. There is **no separate session page** — everything happens
inline on the theme page.

`_owned_instance()` is the important one: it 404s on someone else's instance,
and it is the only thing standing between a student and another student's
machine. Any new route that touches an instance must call it.

`_close()` deliberately records the result *before* asking Proxmox to destroy
anything, so a hypervisor problem can never cost a student their score.

### `app/admin.py` — 355 lines
The staff console at `/admin`: account list, account detail, unlock, lock,
issue a temporary password, change a role, view live sessions, force-close a
session, read the audit log.

Every route is guarded by `require()`, and every state-changing one also calls
`_may_act_on()`, which refuses actions against equal or senior accounts.

Also provides `flask --app wsgi set-role <username> <role>`, the command-line
bootstrap for the first administrator — the one privilege escalation that
cannot be performed over the web.

---

## Calculations and outside systems

### `app/scoring.py` — 260 lines
Flag grading and every leaderboard.

- `submit_flag()` — hashes the submission, matches it against the flags for
  *that* challenge, logs the attempt either way, then writes the award and the
  points total in one transaction.
- `challenge_progress()` — flags captured versus available.
- `overall_leaderboard()`, `theme_leaderboard()`, `theme_challenge_matrix()`,
  `user_stats()` — the four boards. All exclude administrators, who run the
  platform rather than compete on it.
- `_ranked()` — attaches ranks, sharing on ties, so ranking behaviour only
  needs changing in one place.

### `app/proxmox.py` — 163 lines
The hypervisor adapter, and the only file that knows Proxmox exists.

Two backends behind one interface: `simulate` invents a vmid and a console URL
so the whole platform demos without a cluster, and `api` clones and starts real
VMs. Switching is a config change, not a rewrite — no view imports `proxmoxer`.

### `app/audit.py` — 58 lines
The security event log: sign-ins, failures, lockouts, role changes, temporary
passwords, instance launches and closes. One `record()` function and a list of
event-name constants.

Write failures are swallowed on purpose — an audit problem must never be the
reason a student can't sign in.

---

## Not part of the running app

### `tests/test_flow.py` — 498 lines
91 checks covering registration, lockout, staff password reset, CSRF, rate
limits, security headers, the VM lifecycle, scoring, roles and access control.
No pytest — run it with `python -m tests.test_flow` and it prints a line per
check.

**Run it before every commit.** Several checks exist precisely because a
reasonable-looking change would quietly break security: that a temporary
password is never stored readable, that the console URL never appears in the
HTML, that a second student gets a 404 on someone else's session. If one of
those fails, the fix is almost never the test.

### `tools/build_preview.py` — 311 lines
Builds `preview/cyber-range-preview.html`, the single self-contained file
showing all twelve screens with no install required. It renders the real
templates through Flask's test client, so it can't drift from the actual
interface the way a hand-built mockup would. Re-run it after any template or
CSS change.

---

## Where to look when something breaks

| Symptom | Start here |
|---|---|
| "That form expired" on submit | `csrf.py` — the form is missing its `_csrf` field |
| A 403 for someone who should have access | `roles.py` — check the `PERMISSIONS` matrix |
| Scores look wrong | `scoring.py`, then the views in `schema.sql` |
| A challenge won't launch | `proxmox.py`, then the `vm` rows in `seed.py` |
| A student can't sign in | `security.py` for the lockout, `throttle.py` for the limits |
| Something changed and nobody knows who | the audit log at `/admin/audit` |
| A page renders but is missing data | the view that renders it, not the template |
