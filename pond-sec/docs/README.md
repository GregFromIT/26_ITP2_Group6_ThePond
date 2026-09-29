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

Our cluster host and node are the defaults. The token is not: there is no
default token ID and no default secret, because a default that works is a
default nobody replaces, and the last one was a root token (H3). Create The
Pond's own token first (see "Least-privilege Proxmox token" below).

```bash
pip install proxmoxer requests
export PROXMOX_BACKEND=api
export PROXMOX_TOKEN_ID='pond@pve!launcher'
export PROXMOX_TOKEN_SECRET='...'          # from the playbook, not from git
export PROXMOX_CA_BUNDLE=/etc/pond/pve-root-ca.pem
```

The defaults in `app/config.py`:

| Setting | Value |
|---|---|
| `PROXMOX_HOST` | `10.1.21.151` |
| `PROXMOX_NODE` | `pve` |
| `PROXMOX_TOKEN_ID` | (none; required, `user@realm!tokenname`, `@pam` refused) |
| `PROXMOX_VERIFY_SSL` | `1` (0 is refused in production) |
| `PROXMOX_CA_BUNDLE` | (none) copy of `/etc/pve/pve-root-ca.pem`; wins over `PROXMOX_VERIFY_SSL=0` |
| `PROXMOX_POOL` | `pond-clones`, the pool every clone is created in |
| `PROXMOX_TEMPLATE_POOL` | `pond-templates`, the pool holding the templates |
| `PROXMOX_TEMPLATE_BRIDGES` | (none) local bridges the token may hold `SDN.Use` on; must match `pond_template_bridges` in the playbook |
| `PROXMOX_TEMPLATE_STORAGES` | `local-lvm`, storages (besides `PROXMOX_STORAGE`) the token may hold `Datastore.AllocateSpace` on; must match `pond_storages` |
| `PROXMOX_PROTECTED_VMIDS` | `300-303`, other projects' VMs, never cloned or deleted |
| `PROXMOX_PRIVILEGE_CHECK_TTL` | `300` seconds a passed self-check is trusted per worker |
| `PROXMOX_STORAGE` | `local-lvm` (full clones only) |
| `PROXMOX_FULL_CLONE` | `0`, so linked clones by default |

Then point each challenge's `vm_id` at a `vm` row whose `template_vmid` is a
real template. `clone_and_start` grabs the next cluster id, makes a linked
clone, starts it and hands back a noVNC console URL. `stop_and_destroy` stops
and deletes it when the session closes.

### Least-privilege Proxmox token

The app runs as its own user, `pond@pve`, through a privilege-separated token,
`pond@pve!launcher`. It is created by `playbooks/pond_least_privilege.yml`, which
is run on the Proxmox node as root and never touches VMs 300-303.

What each call in `app/proxmox.py` needs:

| # | Call | Privilege | Granted by |
|---|---|---|---|
| 1 | `GET /cluster/nextid` | none | |
| 2 | `POST /nodes/{n}/qemu/{tpl}/clone` (with `pool=`) | `VM.Clone` on the template; `VM.Allocate` on the clone pool; `Datastore.AllocateSpace` on the storage; `SDN.Use` on the template's bridge; `Sys.Console` on `/` only if the template has a physical CD-ROM | `/pool/pond-templates` PondTemplates; `/pool/pond-clones` PondClones; `/storage/local-lvm` PondStorage; the template's bridge PondBridge |
| 3 | `GET /nodes/{n}/tasks/{upid}/status` | none for own tasks, else `Sys.Audit` on `/nodes/{n}` | optional `pond_grant_node_audit` |
| 4 | `GET /cluster/sdn/zones` | filtered by `SDN.Audit`/`SDN.Allocate` | `/sdn/zones/pondz` PondSDN |
| 5 | `GET /cluster/firewall/options` | `Sys.Audit` on `/` | `/` PondAudit (no propagate) |
| 6 | `POST /cluster/sdn/vnets` | `SDN.Allocate` on the zone | PondSDN |
| 7 | `PUT /cluster/sdn` (apply) | `SDN.Allocate` on `/sdn` | `/sdn` PondSDNApply (no propagate; medium confidence, see 403 notes) |
| 8 | `GET qemu/{id}/config` | `VM.Audit` | PondClones via the pool |
| 9 | `PUT config netN` | `VM.Config.Network`, plus `SDN.Use` on the new VNet | PondClones; PondSDN |
| 10 | `PUT`/`GET firewall/options` | `VM.Config.Network` / `VM.Audit` | PondClones |
| 11 | `POST status/start`, `status/stop` | `VM.PowerMgmt` | PondClones |
| 12 | `DELETE qemu/{id}` | `VM.Allocate` | PondClones |
| 13 | `GET`/`DELETE /cluster/sdn/vnets` | filtered `SDN.Audit`/`SDN.Allocate` | PondSDN |
| 14 | `GET /access/permissions` | none | |

Roles: PondClones (`VM.Allocate`, `VM.Audit`, `VM.Config.Network`,
`VM.PowerMgmt`), PondTemplates (`VM.Audit`, `VM.Clone`), PondStorage
(`Datastore.AllocateSpace`), PondSDN (`SDN.Allocate`, `SDN.Audit`, `SDN.Use`),
PondSDNApply (`SDN.Allocate`), PondAudit (`Sys.Audit`), PondBridge (`SDN.Use`,
only if `pond_template_bridges` is set). Every ACL is granted to both the user
and the token, because a privilege-separated token gets the intersection of the
two. `VM.Console` is not needed: the console route only redirects to the noVNC
URL. If a console relay returns, add it to the playbook and to
`_VM_PRIVS_CLONES` together.

Never granted: anything on `/vms` or `/vms/300-303`, `/access`, `/nodes` (except
the optional audit), the storage root, the `localnetwork` zone root, the pool
root, and any of `Permissions.Modify`, `Sys.Modify`, `Sys.Console`,
`User.Modify`, `Pool.Allocate`, `Datastore.Allocate`, `VM.Config.Disk`.

What the app refuses (all fail closed):

- a `@pam` token, a malformed token ID, a missing secret or an empty pool: at
  connect time, and at start-up in production;
- TLS verification off in production, or a `PROXMOX_CA_BUNDLE` that does not
  exist. In development an explicit `PROXMOX_VERIFY_SSL=0` works but logs a
  warning on every connect;
- cloning, starting or deleting a VM in `PROXMOX_PROTECTED_VMIDS`, or accepting
  a `nextid` inside that range;
- launching when the token holds anything beyond the allowlist. Before every
  launch (cached for `PROXMOX_PRIVILEGE_CHECK_TTL` seconds after a pass, never
  after a failure) the app reads `GET /access/permissions` and compares it with
  `allowed_privileges()`. It also reads `GET /cluster/resources?type=vm` (covered by
  the `VM.Audit` the token already holds) so a `/vms/<id>` grant is only accepted
  on a VM that is in the clone pool (clone rights) or the template pool (template
  rights); a VM in neither pool gets nothing. Bridges, storages and the node are
  limited to the configured ones. Students see a generic error; the offending paths go to
  the log. Teardown deliberately skips this check so a widened token cannot
  orphan a running VM.

Operator runbook:

1. Before, for the evidence (on the Proxmox host): `pveum user token list root@pam`
   and `pveum user token permissions root@pam root`.
2. Certificate. `openssl x509 -in /etc/pve/nodes/pve/pve-ssl.pem -noout -ext subjectAltName`
   must show `IP Address:10.1.21.151`. If not, set `PROXMOX_HOST` to a DNS name
   that is in the SAN and add it to `/etc/hosts` on the app host. If a custom or
   ACME certificate is served through `pveproxy-ssl.pem`, use the system CA store
   instead: `PROXMOX_VERIFY_SSL=1` and no bundle. Otherwise copy
   `/etc/pve/pve-root-ca.pem` to `/etc/pond/pve-root-ca.pem` on the app host
   (mode 0644) and check it:
   `openssl s_client -connect 10.1.21.151:8006 -CAfile /etc/pond/pve-root-ca.pem -verify_ip 10.1.21.151 </dev/null 2>/dev/null | grep 'Verify return code'`
   should say `0 (ok)`.
3. Run the playbook on the node, `--check` first and then for real (see the
   header of `playbooks/pond_least_privilege.yml` for the exact commands). Install
   Ansible on the node and copy just that file over; the repo is not needed. The
   repo's `ansible.cfg` names a vault password file that will not exist there, so
   run it from a directory with no `ansible.cfg`, for example
   `cd /root && ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml -e '{"pond_template_vmids":[9101,9201]}' --check`
   and then again without `--check`. From the repo root it would pick up the
   repo's `ansible.cfg`, whose `vault_password_file` would abort the run. If an old
   `/root/pond-launcher.token` exists the playbook refuses to create a token until
   you `shred -u` it.
4. Verify: `pveum user token permissions pond@pve launcher` and
   `pveum acl list | grep -i pond`, then probe from the app host with the new
   token (the first three should be refused). The secret is read without
   echo and reaches curl through a process substitution, so it never lands in shell
   history or `ps`:

   ```bash
   read -rs PVE_SECRET; C='--cacert /etc/pond/pve-root-ca.pem'
   H() { printf 'Authorization: PVEAPIToken=pond@pve!launcher=%s' "$PVE_SECRET"; }
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/qemu/300/config   # expect 403
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/qemu/300/status/current   # expect 403 (read-only probe of a protected VM)
   curl -s $C -H @<(H) https://10.1.21.151:8006/api2/json/access/users   # expect 403 or an empty/own-user-only list; a full user list means the token is too wide
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/execute   # expect 403/501
   curl -s $C -H @<(H) "https://10.1.21.151:8006/api2/json/cluster/resources?type=vm"   # only the pond pools' VMs
   unset PVE_SECRET
   ```
5. Cut over. On the app host set `PROXMOX_BACKEND=api`,
   `PROXMOX_TOKEN_ID=pond@pve!launcher`, `PROXMOX_TOKEN_SECRET=<contents of
   /root/pond-launcher.token>` and `PROXMOX_CA_BUNDLE=/etc/pond/pve-root-ca.pem`,
   restart, and launch and close one challenge (this also completes the H1
   acceptance). Then `shred -u /root/pond-launcher.token`.
6. Revoke root, only after the cut-over works. Look for remaining users of the
   old token (`zgrep -h 'root@pam!root' /var/log/pveproxy/access.log* | tail`, which includes rotated logs;
   `grep -c 'root@pam!root' /var/log/pve/tasks/index`), CONFIRM WITH THE OWNERS
   OF VMs 300-303 that their tooling does not use it, then
   `pveum user token remove root@pam root` and check with
   `pveum user token list root@pam`. Remove the old secret from `.env` and
   `group_vars/secrets.txt` on every host (history rewrite is tracked under M1).
7. Rotation: `pveum user token remove pond@pve launcher`, rerun the playbook,
   update the environment, restart.

If launches fail with a 403 in the log: a task-status read means set
`pond_grant_node_audit: true`; the SDN apply means report the path (the grant on
`/sdn` is the least certain row above); a clone error naming a bridge means the
template's bridge is not covered (see the to-do about a placeholder VNet); a clone
error naming `/` and `Sys.Console` means the template has a host CD-ROM.

Optional: `pvesh set /cluster/options --next-id lower=9000,upper=9999` keeps clone
vmids away from other projects. It is datacenter-wide, so agree it with the other
project first.

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
- [ ] **Move template NICs to a placeholder VNet in `pondz`.** Give each template's
      NICs a VNet with no subnet (for example `pondtpl`) so cloning needs no
      grant on any local bridge and `pond_template_bridges` can stay empty.
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
