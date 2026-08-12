# Pond Sec — what each Python file does

Notes for whoever picks this up next. Every file has a docstring at the top with
more detail; this is just the map that tells you which one to open.

Vocabulary first, because we renamed things partway through and the old words
are still in some of our meeting notes:

- **theme** — a subject area like networking or forensics. Holds six challenges.
- **challenge** — one exercise inside a theme, with its own VM.
- **instance** — one student's live run at one challenge.
- **flag** — a token proving a student got somewhere. Worth points.

---

## How a request moves through the app

Every page load follows the same path, and knowing it makes the rest of this
easier to follow:

1. `wsgi.py` hands the request to the app object.
2. `app/__init__.py` runs three checks, in this order: force HTTPS, check the
   CSRF token, load the signed-in user.
3. One of the blueprints handles it: `auth.py`, `dashboard.py`, `themes.py` or
   `admin.py`.
4. That blueprint asks the support modules for whatever it needs. `db.py` for
   data, `scoring.py` for points, `roles.py` for permissions, `proxmox.py` for a
   VM.
5. A template in `app/templates/` renders it, and `security_headers()` back in
   `app/__init__.py` stamps the response on the way out.

Imports only go one way. Blueprints use the support modules, and the support
modules never import blueprints. There is one exception, commented where it
happens: `admin.py` imports `themes._close` inside a function so staff can close
a student's session without us writing the teardown logic twice.

---

## Starting up

### `wsgi.py` (27 lines)
The object a web server imports. Creates the app and does nothing else. The
comments cover running it in development, under gunicorn on Linux and under
waitress on Windows, plus two warnings worth reading before you deploy: never
run with `--debug` on a network, and set `TRUSTED_PROXIES` if there's anything
in front of it.

### `app/__init__.py` (278 lines)
The assembly point, and probably the file to read first. `create_app()` wires
everything else together, so it doubles as a map of the codebase.

It also holds the things that apply to every request no matter where it came
from:

- `resolve_secret_key()` reads the key from the environment, generates a
  development one if there isn't any, and refuses to start in production without
  it. There's no hard-coded default anywhere.
- `force_https()` redirects plain HTTP in production.
- `security_headers()` adds the CSP, framing, sniffing and HSTS headers.
- `session_is_idle()` signs people out after an hour untouched.
- `register_error_handlers()` gives us friendly error pages, and is why a crash
  never shows a stack trace in the browser.
- `register_filters()` defines the `|stamp` and `|duration` template filters so
  a timestamp looks the same on every page.

New blueprints get registered here and nowhere else.

### `app/config.py` (96 lines)
Every setting in one place, each one reading an environment variable with a
sensible default. Covers session and cookie policy, lockout rules, password
length, request size caps and all the Proxmox connection details.

Setting `RANGE_ENV=production` tightens a group of defaults at once: secure-only
cookies, HTTPS enforced, TLS verified, and no startup without a real secret key.

Add settings here and document them in `.env.example`. Read them with
`current_app.config[...]` rather than `os.environ` directly, otherwise tests
can't override them.

---

## Data

### `app/db.py` (131 lines)
The only file that opens a database connection. It gives everything else two
functions, `query()` for reads and `execute()` for writes, both of which take
the values separately from the SQL. That separation is what stops SQL injection,
and it's why nothing else in the codebase builds a query with string formatting.

Also provides the `flask --app wsgi init-db` and `seed-db` commands.

Read the migration warning at the top before you change the schema. `init-db`
drops and rebuilds everything, which is fine now and destructive once there are
real scores.

### `app/schema.sql`
Not Python, but read it early. Tables, constraints and the two leaderboard
views. The constraints are doing real work here: `UNIQUE(user_id, flag_id)` is
what makes claiming a flag twice impossible, and the `CHECK` on `role` stops a
typo creating an account with permissions nobody ever wrote.

### `app/seed.py` (226 lines)
Demo content: three themes, eighteen challenges, six VM templates, the flags and
the demo accounts. This is where challenge content lives until somebody builds a
staff interface for it, so adding a theme means editing this file rather than
writing code.

`_fabricate_scores()` invents leaderboard history so the boards aren't empty in
a demo. That call needs deleting before a real class uses the platform.

---

## Security bits

Four small files, and the ones a marker is most likely to look at.

### `app/security.py` (177 lines)
Passwords and lockouts. The only file that reads or writes the password table.

- `set_password()` and `password_matches()` handle hashing, via Werkzeug's
  scrypt.
- `password_problems()` holds the rules a new password has to pass. It returns
  a list rather than raising, so the user sees everything wrong at once instead
  of fixing one thing per attempt.
- `issue_temporary_password()` is the staff recovery path. Generates three words
  and a number, returns it once in the clear, and flags the account so the
  student has to change it. It's never stored readable and never logged.
- `register_failure()`, `lockout_remaining()` and `clear_lockout()` are the
  three-strikes lockout.

### `app/csrf.py` (66 lines)
One token per session, required on every POST, compared with
`secrets.compare_digest` so the check can't be timed. We wrote it by hand rather
than pulling in a library, partly so the mechanism is visible in the code.

Every `<form method="post">` needs the hidden `_csrf` field or the submission
comes back as a 400.

### `app/throttle.py` (106 lines)
Rate limiting. Every limit is in one dictionary at the top of the file so the
whole policy reads in one screen: sign-in attempts per source and per account,
registrations, password changes, and flag guesses per challenge.

Counters live in SQLite rather than in memory, because several gunicorn workers
sharing a dict would each get their own full allowance.

### `app/roles.py` (151 lines)
Who's allowed to do what. Three roles (student, moderator, administrator) and
one `PERMISSIONS` matrix saying which roles hold which capability.

- `can()` is used in templates to hide controls.
- `require()` is the decorator that actually blocks the request.
- `outranks()` stops staff acting on accounts at their own level or above.

Use both `require()` and `can()`. Hiding a button is only politeness; the
decorator is the thing doing the access control.

---

## Pages

### `app/auth.py` (306 lines)
Register, sign in, sign out, change password. This decides who somebody is; the
rules it applies live in `security.py` and the limits in `throttle.py`.

Two things in here look like bugs and aren't, so read the comments before
changing them. A taken username is reported plainly, because usernames are
public on the leaderboards anyway. And there's no forgotten-password page at
all, because with no email stored, recovery is staff-issued and in person.

Also holds `login_required`, which most other views use, and
`force_password_change`, which pins an account with a temporary password to the
change-password page until it sets its own.

### `app/dashboard.py` (75 lines)
The landing page and the signed-in dashboard. Deliberately thin: it asks
`scoring.py` for the numbers and hands them to a template. Anything that
calculates a score belongs in `scoring.py`, so the dashboard and the theme pages
can't end up disagreeing.

### `app/themes.py` (394 lines)
The biggest file and the core of the platform. Themes, challenges, VM instances
and flag submission.

A session goes like this. `launch()` clones a VM and writes the instance row.
The challenge's tile on the theme page turns into a working panel with a timer,
a console link and a flag box. `flag()` grades submissions and closes the
challenge automatically once the last flag is captured. `close()` stops the
clock and tears the VM down. There's no separate session page; it all happens
inline on the theme page.

`_owned_instance()` is the important one. It 404s on somebody else's instance,
and it's the only thing standing between a student and another student's
machine. Any new route that touches an instance has to call it.

`_close()` deliberately records the result before it asks Proxmox to destroy
anything, so a problem with the hypervisor can't cost a student their score.

### `app/admin.py` (355 lines)
The staff console at `/admin`. Account list, account detail, unlock, lock, issue
a temporary password, change a role, view live sessions, force-close a session,
read the audit log.

Every route is guarded by `require()`, and every one that changes something also
calls `_may_act_on()`, which refuses actions against equal or senior accounts.

It also provides `flask --app wsgi set-role <username> <role>`, which is how the
first administrator gets created. That's the one privilege escalation you can't
do through the web, on purpose.

---

## Calculations and outside systems

### `app/scoring.py` (260 lines)
Flag grading and all the leaderboards.

- `submit_flag()` hashes the submission, matches it against the flags for that
  challenge, logs the attempt either way, then writes the award and the points
  total in a single transaction.
- `challenge_progress()` gives flags captured against flags available.
- `overall_leaderboard()`, `theme_leaderboard()`, `theme_challenge_matrix()` and
  `user_stats()` are the four boards. All of them exclude administrators.
- `_ranked()` attaches the ranks and shares them on ties, so ranking behaviour
  only needs changing in one spot.

### `app/proxmox.py` (163 lines)
The hypervisor adapter, and the only file that knows Proxmox exists.

Two backends behind one interface. `simulate` invents a vmid and a console URL
so the platform demos without a cluster, and `api` clones and starts real VMs.
Switching between them is a config change, not a rewrite, because no view
imports `proxmoxer`.

### `app/audit.py` (58 lines)
The security event log: sign-ins, failures, lockouts, role changes, temporary
passwords, instance launches and closes. One `record()` function and a list of
event-name constants.

Write failures are swallowed on purpose. An audit problem should never be the
reason a student can't sign in.

---

## Not part of the running app

### `tests/test_flow.py` (498 lines)
91 checks covering registration, lockout, staff password resets, CSRF, rate
limits, security headers, the VM lifecycle, scoring, roles and access control.
No pytest. Run it with `python -m tests.test_flow` and it prints a line per
check.

Run it before every commit. A few of the checks are there because a
reasonable-looking change would quietly break something: that a temporary
password is never stored readable, that the console URL never ends up in the
HTML, that a second student gets a 404 on somebody else's session. If one of
those fails, the fix is almost never the test.

### `tools/build_preview.py` (338 lines)
Builds `preview/pond-sec-preview.html`, the one-file preview of all twelve
screens that needs nothing installed. It renders the real templates through
Flask's test client, so it can't drift away from the actual interface like a
hand-drawn mockup would. Re-run it after any template or CSS change.

---

## Where to look when something breaks

| Symptom | Start here |
|---|---|
| "That form expired" on submit | `csrf.py`, the form is missing its `_csrf` field |
| A 403 for someone who should have access | `roles.py`, check the `PERMISSIONS` matrix |
| Scores look wrong | `scoring.py`, then the views in `schema.sql` |
| A challenge won't launch | `proxmox.py`, then the `vm` rows in `seed.py` |
| A student can't sign in | `security.py` for the lockout, `throttle.py` for the limits |
| Something changed and nobody knows who | the audit log at `/admin/audit` |
| A page renders but data is missing | the view that renders it, not the template |

---

## Still to sort out

Kept here as well as in `README.md` so it doesn't get lost:

- [ ] The schema isn't final. Agree it before anyone has scores worth keeping,
      because `init-db` drops everything and we have no migrations yet.
- [ ] The Proxmox connection is still on `simulate`. The template vmids in
      `seed.py` are placeholders and need replacing with real ones from `pve`.
- [ ] `app/seed.py` is the only way to add challenge content. No staff UI for it.
- [ ] Seeded flags are shared across challenges. Fine for a demo, no good for
      assessment.
