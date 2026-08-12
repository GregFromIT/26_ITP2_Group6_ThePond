# Pond Sec

A Flask + SQLite app that runs a cyber teaching range. A student registers,
picks a theme, launches a challenge, and the app clones a VM on Proxmox, times
the session, marks the flags they submit and updates the leaderboards.

Some vocabulary, since we renamed things partway through and the old words are
still in a few of our meeting notes. A **theme** is a subject area like
networking or forensics. Each theme holds six **challenges**, and a challenge is
one exercise with its own VM. An **instance** is one student's live run at one
challenge.

The whole flow works end to end. The one part that is faked by default is
Proxmox: with `PROXMOX_BACKEND=simulate` the app makes up a vmid and a console
URL so you can demo everything without a cluster. Set it to `api` and the same
code talks to the real thing.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install Flask                       # or: pip install -r requirements.txt

flask --app wsgi init-db                # build the schema
flask --app wsgi seed-db                # 3 themes, 18 challenges, 6 VM templates, demo users
flask --app wsgi run --debug            # http://127.0.0.1:5000
```

Windows is the same except for the venv line (`.venv\Scripts\activate`), and if
you ever run it properly you want `waitress-serve --listen=127.0.0.1:8000
wsgi:app` rather than gunicorn, which doesn't run on Windows at all.
`pip install -r requirements.txt` picks the right one for you. The
`flask --app wsgi` commands are identical everywhere.

Demo accounts all use the password `rootroot`. `bpt` is the system
administrator, `vstergiou` is a moderator, and `mbates`, `lhardie`, `gthomas`
and `demo` are students. Seeded flags follow the pattern
`flag{challenge_one_entry}`, `flag{challenge_one_bonus}`,
`flag{challenge_two_entry}` and so on.

Note that `rootroot` is 8 characters, which is under our own 12-character
minimum. It only works because seeding writes the password hash straight to the
database, while the length check lives in the registration form. Nobody could
actually choose it through the web interface. It is there so demos are quick,
and the seeded accounts need deleting or re-passwording before any real class
uses this.

There is no email anywhere in the app. If you lock yourself out of a demo
account, sign in as `bpt` and issue a temporary password from the staff console.

To run the tests: `python -m tests.test_flow`. That is 91 checks covering
registration, lockout, staff password resets, CSRF, rate limits, security
headers, launching and closing VMs, scoring, roles and access control. It
doesn't need pytest.

For anything outside the lab, set `RANGE_ENV=production` and a real
`FLASK_SECRET_KEY`. The app won't start in production without one.

## What is where

```
wsgi.py               entry point
app/__init__.py       application factory, request hooks, template filters
app/config.py         every setting, all overridable from the environment
app/schema.sql        tables plus the two leaderboard views
app/db.py             connection handling, init-db / seed-db commands
app/security.py       hashing, lockout rules, temporary passwords
app/csrf.py           per-session CSRF tokens
app/throttle.py       rate limits, counters kept in SQLite
app/roles.py          the permission matrix
app/audit.py          security event log
app/auth.py           register, login, logout, change password
app/dashboard.py      landing page and dashboard
app/themes.py         themes, challenges, launch, timer, flags, teardown
app/admin.py          staff console at /admin
app/scoring.py        flag grading, leaderboards, per-challenge matrix
app/proxmox.py        simulate and api backends behind one interface
app/seed.py           demo content
```

`CODE_MAP.md` in this folder goes through each of these in more detail. Start
there if you are new to the code. `DATA_MODEL.md` covers the tables,
`DECISIONS.md` covers why things are built the way they are, and
`USER_GUIDE.md` is the one to hand to students and staff.

## Preview without installing anything

`preview/pond-sec-preview.html` is one self-contained file with all twelve
screens and a switcher along the top. No Python, no server, nothing to install.
Double-click it, or attach it to a report. Nothing in it is clickable.

Rebuild it after changing a template or the CSS:

```bash
python tools/build_preview.py
```

It renders the actual templates through Flask's test client, so it can't drift
away from the real interface like a hand-drawn mockup would.

## Where to add things

Every module has a docstring at the top explaining what to change when you
extend it. Quick version:

| You want to | Go to |
|---|---|
| Add a theme, challenge or flag | `app/seed.py`, data only, no code change |
| Add a page | new blueprint, then register it in `app/__init__.py` |
| Add a registration field | `schema.sql`, `auth.register`, `register.html` |
| Add a setting | `app/config.py` and `.env.example` |
| Add a rate limit | `LIMITS` at the top of `app/throttle.py` |
| Add a leaderboard | a view in `schema.sql`, a function in `app/scoring.py` |
| Support another hypervisor | `app/proxmox.py`, nothing else imports it |
| Change the schema | read the migration warning at the top of `app/db.py` first |

Two things that are easy to forget: every `<form method="post">` needs the CSRF
hidden field or the submission gets rejected, and any route that touches a
session has to go through `_owned_instance()`. Run the tests before you commit.

## How the original outline maps to the schema

| Outline | Table | Notes |
|---|---|---|
| User | `user` | Name, uni year, username, points, lockout state, role. No email column |
| Password manager | `password_manager` | Separate table, one row per user. Only `security.py` touches it |
| Challenges (now *themes*) | `theme` | Name, category, summary, weighting |
| (now *challenges*) | `challenge` | Six per theme, each mapped to a VM template |
| VMs | `vm` | Template catalogue: node, template vmid, cores, memory |
| Active VMs | `active_vm` | One row per clone, with its vmid, console URL and state |
| Running challenges | `running_instance` | The session itself: access key, start, end, duration, status |
| Challenge points | `challenge_points` | One row per flag, with the hashed flag and its points |
| UCP | `user_challenge_points` | The award ledger. `UNIQUE(user_id, flag_id)` is the lock |
| (attempts) | `flag_submission` | Every submission, right or wrong. Feeds the "flags played" column |

Two decisions here we should be ready to explain if asked.

Flags are stored as SHA-256 rather than plaintext, so dumping
`challenge_points` gives you nothing useful. Submissions get lower-cased and
stripped before hashing, so a trailing space doesn't fail a correct answer.

The rule stopping a student claiming the same flag twice is a database
constraint, `UNIQUE(user_id, flag_id)`, not a check in Python. That means two
browser tabs racing each other can't get round it.

`user.points` is the plain sum of awarded points and is what the overall
leaderboard reads, the way the original outline described. Theme weighting gets
applied when a board is read, in `leaderboard_theme` and in the per-challenge
matrix, so changing a weighting doesn't mean rescoring anybody.

## Roles

Three levels, all defined in one matrix in `app/roles.py`:

| | Student | Moderator | Administrator |
|---|---|---|---|
| Take challenges | yes | yes | yes |
| View accounts and sessions | — | yes | yes |
| Unlock / lock an account | — | yes | yes |
| Issue a temporary password | — | yes | yes |
| Force-close a session, free its VM | — | yes | yes |
| Read the audit log | — | yes | yes |
| Grant or remove moderator/admin | — | — | yes |
| Appears on the leaderboards | yes | yes | no |

Registering always gives you a student account. The first administrator gets
promoted from the command line, which is the one bit of privilege escalation
you can't do through the web:

```bash
flask --app wsgi set-role bpt admin
```

Administrators don't show up on any leaderboard and have no rank on their own
dashboard. They run the platform rather than compete on it, and a staff account
sitting in the rankings looks odd when that same person is marking you.
Moderators do compete, because they are usually students helping run a class and
hiding them would cost them their own results. That rule is in the two
leaderboard views in `schema.sql` and in `theme_challenge_matrix()` and
`user_stats()`. All four have to agree, so grep for `role != 'admin'` if you
ever change it.

Other rules the console enforces:

- Nobody can act on an account at their own level or above. One moderator can't
  lock, unlock or reset another, and neither can touch an administrator.
- An admin can step down, but not if they're the last one left. Handing over is
  normal, leaving nobody able to grant access isn't, and getting out of that
  would need shell access to the server.
- Password recovery is staff-issued and done in person. There's no email on
  file so there's no self-service reset. A moderator or admin issues a temporary
  password, it shows once on their screen, it isn't stored readable or written
  to the log, and the student has to change it at next sign-in. Worth putting in
  the report: this does mean staff can effectively take over any account below
  their rank. That comes with staff-held recovery generally, which is why the
  action is audited by name and can't be used sideways or upwards.
- Locking someone out takes effect straight away, including on a session they
  already have open.
- Role changes, locks, unlocks and staff-closed sessions all get audited with
  the name of whoever did it.

There is no way to edit scores or delete flag awards, and we left that out on
purpose. The scores are the record of what a student actually did, and the award
ledger is what stops double-claiming. If a cheating case ever needs a score
changed it should be a deliberate database action with a reason written down,
not a button someone can hit by accident. Worth asking the client before adding
one.

## Account rules

Three failed sign-ins lock the account for 15 minutes (`MAX_LOGIN_ATTEMPTS` and
`LOCKOUT_MINUTES`; set the second to 0 if you'd rather locks only lift when an
admin clears them). A staff-issued temporary password also clears the lock.
Sessions expire after an hour idle.

If a username is taken we say so plainly, since usernames are printed on the
leaderboards anyway and hiding that would protect nothing. There are no email
addresses to enumerate.

## Hardening

| Control | Where |
|---|---|
| Parameterised SQL everywhere | everything goes through `db.query` / `db.execute` |
| Output escaping | Jinja autoescape, no `\|safe` anywhere |
| CSRF tokens on every unsafe request | `app/csrf.py`, compared with `compare_digest` |
| CSP with `script-src 'self'`, no inline script | `security_headers` in `app/__init__.py` |
| `X-Frame-Options`, nosniff, Referrer-Policy, Permissions-Policy, HSTS | same |
| Secret key from the environment, generated in dev, required in prod | `resolve_secret_key` |
| Secure + HttpOnly + SameSite=Strict cookies | `app/config.py` |
| Rate limits on login, registration and flag submission | `app/throttle.py` |
| Per-source throttle in front of the per-account lockout | `auth.login` |
| Session cleared and CSRF rotated on login, logout, password change | `app/auth.py` |
| Request size cap and per-field length caps | `MAX_CONTENT_LENGTH`, `auth.field` |
| Ownership checks on every session route | `themes._owned_instance` |
| Console URL never rendered into the page | `themes.console` |
| Audit log of auth and VM events | `app/audit.py` |
| Error pages instead of tracebacks | `register_error_handlers` |

All the rate limits sit in one dictionary at the top of `app/throttle.py` so
they can be reviewed together. The counters are in SQLite rather than in memory,
because with several gunicorn workers an in-memory dict would give each worker
its own full allowance.

One thing for the report: the people using this platform are being taught to
attack things, and the machines we hand them are deliberately vulnerable. The
orchestrator should be treated as sitting on a hostile network rather than a
friendly one.

## Connecting to the real Proxmox

Our cluster details are already the defaults. The only thing missing is the
token secret, because that's the one value that must never go in the repo.

```bash
pip install proxmoxer requests
export PROXMOX_BACKEND=api
export PROXMOX_TOKEN_SECRET='...'          # from Proxmox, not from git
```

The defaults in `app/config.py`:

| Setting | Value |
|---|---|
| `PROXMOX_HOST` | `10.1.21.151` |
| `PROXMOX_NODE` | `pve` |
| `PROXMOX_TOKEN_ID` | `root@pam!root` |
| `PROXMOX_STORAGE` | `local-lvm` (full clones only) |
| `PROXMOX_FULL_CLONE` | `0`, so linked clones by default |

Then point each challenge's `vm_id` at a `vm` row whose `template_vmid` is a
real template. `clone_and_start` grabs the next cluster id, makes a linked
clone, starts it and hands back a noVNC console URL. `stop_and_destroy` stops
and deletes it when the session closes.

Two things about the current setup we should decide on rather than just
inherit.

`root@pam` is a lot more access than this app needs. A token under root can do
anything on the cluster, so a bug in our code, or anyone who reads the secret
off the server, gets every VM and not just the teaching ones. Better to make a
dedicated user and give its token only `VM.Clone`, `VM.Config.*`,
`VM.PowerMgmt`, `VM.Audit` and `VM.Allocate` on the template pool. Tick
Privilege Separation when creating the token or it inherits the user's full
rights anyway.

Certificate verification is off by default, because reaching the host by IP
means a self-signed certificate that would fail verification. That's fine in the
lab and a bad habit anywhere else. With it off, anything sitting between the app
and `10.1.21.151` can pretend to be the hypervisor and collect the API token.
Installing the cluster CA on the app host and setting `PROXMOX_VERIFY_SSL=1`
fixes it.

## To do

Still outstanding, roughly in the order we think they matter.

- [ ] **Real VM templates.** The template vmids in `app/seed.py` (9101, 9201 and
      so on) are placeholders and nothing exists at those ids yet. They need
      building on `pve` and the real ids putting in before `PROXMOX_BACKEND=api`
      gets switched on, or every launch fails with "no such VM".
- [ ] **Finalise the schema.** Still being agreed with the group. Worth settling
      before anyone has scores worth keeping, because `init-db` drops and
      rebuilds everything and we have no migration tool. Either add one (Alembic
      works with SQLite) or agree an export/import step.
- [ ] **Per-user network isolation.** Every clone currently lands on the same
      bridge. Until each session gets its own VLAN or SDN zone, students can
      reach each other's machines, and a compromised challenge VM has a route to
      the orchestrator. This is the one that worries me most.
- [ ] **Console authentication.** `/themes/session/<id>/console` checks the
      session belongs to you before redirecting, and the URL isn't in the page
      source any more, but the student still has no Proxmox credential of their
      own. Proper fix is a ticket-issuing proxy, or Proxmox's `/access/ticket`
      with a short-lived per-session user.
- [ ] **Idle reaping.** A session left open keeps its VM. Needs a scheduled job
      to close sessions past a time limit and free the clone. The idle session
      timeout is done, the VM teardown side isn't.
- [ ] **Staff interface for content.** Themes, challenges, VMs and flags all
      come from `app/seed.py`. Staff have an account console but no way to add
      or edit challenges through the web.
- [ ] **Per-challenge flags.** Every challenge currently shares the same seeded
      flag values, which is fine for a demo and useless for assessment. They
      need to be unique, and ideally generated per VM, or the first student to
      solve one can hand the answer to everybody.
- [ ] **Independent testing.** The 91 checks are our own. Nothing has been
      through ZAP or Burp or looked at by anyone outside the group, which is
      what we'd need before claiming much about the security in the report.
- [ ] **Backups and log retention.** The audit log grows forever and nothing
      backs up the SQLite file.

One known consequence of dropping email, not really a to-do but worth
remembering: it removed a whole class of attack (reset-link interception, mail
spoofing, address enumeration, mail-bombing) and it removed our only way to
contact a user. A student locked out at 11pm the night before an assessment has
no way in until somebody answers them.
