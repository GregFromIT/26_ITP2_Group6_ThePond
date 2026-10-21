# Pond Sec: admin guide

How to run Pond Sec as staff. Part 1 is the web console, for moderators and
system administrators. Part 2 is the server, for the system administrator only.

> **DRAFT,** written from the code on `main` (`04998a1`) with
> `fix/seeder-theme-flagformat` (`3dc6afc`) merged. Two kinds of flag
> need resolving before this goes to anyone else:
> - **DECISION:** the code allows more than one reasonable approach, or the
>   behaviour may not be what the team intended. Someone has to choose.
> - **FIX:** the code is wrong or missing something. Fix the code, then update
>   this guide.

| Role in the app | Name in the database | Who |
|---|---|---|
| Student | `user` | Everyone who registers |
| Moderator | `manager` | Class staff |
| System administrator | `sysadmin` | Runs the platform and the server |

---

# Part 1: In the web console

Moderators and administrators get a **Staff** link in the top bar, leading to
the staff console at `/admin`.

## What each role can do

| Action | Moderator | Admin |
|---|:---:|:---:|
| See accounts, sessions and the audit log | ✓ | ✓ |
| Unlock or lock an account | ✓ | ✓ |
| Issue a temporary password | ✓ | ✓ |
| Close any student's session | ✓ | ✓ |
| Upload challenge submissions | ✓ | ✓ |
| Approve or reject new registrations | | ✓ |
| Review and publish challenge submissions | | ✓ |
| Change someone's role | | ✓ |

Source: `pond-sec/app/roles.py`, `PERMISSIONS`.

Moderators appear on the leaderboards; admins don't (`scoring.py`).

### Acting on other accounts

You can only unlock, lock, or issue a temporary password to an account that
ranks **below** yours. A moderator can't act on another moderator or on an admin,
and an admin can't act on another admin (`admin.py`, `_may_act_on()`).

Rejecting a registration only works on student accounts. Use the role controls
to change a staff account instead (`admin.py`, `reject()`).

> **FIX:** the role-change route (`admin.py`, `set_role()`) never calls
> `_may_act_on()`. The account page hides the role form unless you outrank the
> account (`admin/user_detail.html` line 65), so in normal use an admin can't
> change another admin's role, or their own. But a request sent straight to
> `/admin/users/<id>/role` would be accepted. `roles.py` itself says hiding a
> button "is not access control". Add the check to the route.

> **DECISION:** can an admin step down? The code disagrees with itself:
> - `roles.py` lines 33–34 say no.
> - The `set_role()` docstring says yes, as long as another admin remains.
> - The UI never shows the form on your own account, so in practice no.
>
> Pick one, make the code and the `roles.py` comment match, then update
> "Changing roles" below.

## The staff console

The front page shows counts for:
- live sessions, machines up and **stuck machines** (VMs in an error state);
- accounts, moderators, admins, locked accounts and accounts **awaiting
  approval**.

Click **Awaiting approval** to go to the approvals page. Below the counts are
lists of locked accounts, what's running now, and the latest audit events.

The buttons lead to **Manage sessions**, **Challenge submissions**, **All
accounts** and **Audit log**.

## Approving registrations (admins)

Every new account starts as pending. Until an admin decides, the student only
sees a "Waiting for approval" page (`auth.py`).

Open **Awaiting approval** (`/admin/approvals`). Pending accounts are listed
oldest first, so earlier registrations aren't stuck behind later ones. Click
**Approve** or **Reject**. The page also shows the last 10 decisions and who made
them.

A rejected account isn't deleted. Its username stays reserved, and you can
approve it later if the rejection was a mistake (`admin.py`, `reject()`).

## Helping someone who can't sign in

Open **All accounts**, search by username or name, and open the account.

- **Locked out after failed sign-ins:** click **Unlock this account**. They can
  try again straight away.
- **Forgotten password:** click **Issue a temporary password**. One appears on
  your screen, something like `lantern-ribbon-velvet-78`.

Read it to them, or hand it over on paper. **You won't see it again.** It isn't
stored anywhere readable and can't be looked up. If it gets lost, issue another.
At their next sign-in they're forced to set their own password, and issuing one
also clears any lockout (`security.py`, `issue_temporary_password()`).

Treat it like any password while it exists: don't send it over a channel you
wouldn't trust with one.

## Locking someone out

On the account page, enter a number of hours (default 24, between 1 and 8760)
and click **Lock this account**. It takes effect immediately, including on a
session they already have open: they're signed out on their next click
(`auth.py`, `load_logged_in_user()`).

## Changing roles (admins)

On the account page, under **Access level**, choose a role and click **Save
role**. The form only appears on accounts that rank below yours. Every change is
recorded in the audit log.

The last admin can never be demoted, whoever asks. Recovering from that would
need shell access to the server (`admin.py`, `set_role()`).

## Sessions and machines

**Manage sessions** (`/admin/sessions`) has two lists.

**Running now** lists every live session: who, which challenge, when it started
and which VM. Click **Close** on any that have been left open. That frees the
machine for the next class. A session you close goes down as **abandoned**, and
the student keeps every flag they'd already captured.

After a server reboot, sessions that were open before it also show here, because
their records still say "running". Close them, or that student can't launch
anything else.

**Machines without a session** lists clones the platform thinks are up but that
no session points at. Usually a teardown failed. There's no button for these: the
VM has to be destroyed in Proxmox (see Part 2, "Leftover clones").

> **FIX:** nothing removes a row from **Machines without a session** once its VM
> has been destroyed, so it stays listed. Add a clear action, or document the
> database step here.

## Challenge submissions

Under **Challenge submissions** (`/admin/challenge-submissions`):

1. **Moderators and admins** create a submission from a challenge definition
   (JSON file), add any VM images on the next page, and submit it.
2. A worker validates it. Workers aren't scheduled; the system administrator
   runs them.
3. **An admin** reviews it. Approving it **doesn't publish it**: publishing is a
   separate step on the same page. An admin can also return it for correction.

**Only offline challenges can come in this way.** VM submissions fail
validation, and Red Duck (`container_lab`) submissions publish but can't
launch, until site-specific integrations are written (known issue C2). Add VM and
Red Duck challenges with YAML files instead
([authoring guide](../challenges/authoring-guide.md)).

If this section shows an error saying upload storage hasn't been configured, the
server is missing `UPLOAD_QUARANTINE_ROOT` (see Part 2, "Settings").

The full detail (workers, the manifest format, the integrations) is in the
[submission pipeline](../challenges/submission-pipeline.md) doc.

## Notifications

The **Notifications** link in the top bar is where submission results land:
validation passed, changes needed, published, and so on. Each one goes to the
person who uploaded that submission (`challenge_ingestion/`, `NotificationOutbox`).

## The audit log

**Audit log** (`/admin/audit`) shows the latest 100 events, and you can filter
them by event type. It records:
- sign-ins, failed attempts, lockouts and throttled sign-ins;
- registrations, approvals, rejections and role changes;
- temporary passwords and password changes;
- session launches and closes;
- throttled flag guesses and rejected (CSRF) form submissions.

Event names are listed in `audit.py`.

It's read-only. Nothing in the platform can edit or delete an entry.

> **FIX:** when staff act on a student, the entry is stored against the
> **student's** account (`audit.py`, `record()` sets both the actor and the
> target to the same user). The staff member's name only appears in the
> free-text detail, for example "by lhardie". The log can't reliably answer
> "what did this staff member do?" Store the acting staff member as the actor.

## What nobody can do

There's no way to edit scores, delete flag awards or delete accounts, from any
role. That's deliberate: scores are the record of what a student did. If a
cheating case ever needs a score changed, raise it with the client first
(`roles.py`, the note under `PERMISSIONS`).

---

# Part 2: On the server (system administrator)

Building a new system from bare hardware is in
[build from scratch](../setup/build-from-scratch.md). This part covers running
and looking after an existing one.

## Starting and stopping

To start The Pond, follow the [launch guide](launch-guide.md).

To shut it down cleanly:
1. Close every session in **Manage sessions**, so each clone is destroyed and its
   record is closed.
2. Stop the web app with **Ctrl+C** in its terminal.
3. Shut down the controller VM (9999), the Docker host (9997), then `pve`.

> **DECISION:** this shutdown order is inferred from the code: open sessions
> survive a reboot as stale "running" records. Nobody has agreed it as the
> procedure yet. Confirm it.

## The database

The database is one SQLite file, `the_pond.db`, at the repo root by default
(`pond-sec/app/__init__.py`, `create_app()`). A `POND_SETTINGS` file can move
it, by setting `SQLALCHEMY_BINDS`. The `DATABASE_PATH` setting in
`WSGI_Files/.env.example` does nothing.

From the repo root:

```bash
python3 -m db.init_database --check     # read-only: tables, columns, roles
python3 -m db.init_database             # create missing tables and roles; never touches existing data
```

`init_database` adds missing *tables*, but refuses to change existing ones. A
model change that adds a column needs a one-off migration. See
`db/oneoff_add_account_approval.py` for the existing example. Table-by-table
detail is in the [data model](../database/pond-sec-data-model.md).

## Accounts from the command line

**First accounts.** Edit the `ACCOUNTS` list in `db/seed_accounts.py`. Each entry
is `(username, display name, role)`, where the role is `user`, `manager` or
`sysadmin`. Then, from the repo root:

```bash
python3 -m db.seed_accounts --check     # preview, changes nothing
python3 -m db.seed_accounts             # create them
```

New accounts are created already approved, and each one's temporary password is
printed **once**. Usernames that already exist are skipped. The other options
are:
- `--debug`: print each listed account's current state;
- `--reset`: issue every listed account a fresh temporary password. Comment out
  the accounts you don't want reset first.

**Changing a role, or getting back in.** If every admin account is ever lost,
this is the way back. From `pond-sec/`, with the same settings the app uses:

```bash
flask --app wsgi set-role <username> admin      # or: moderator, student
```

It also approves the account if it was still pending (`admin.py`,
`set_role_command()`). Because it needs shell access to the server, it can't be
done over the web.

## Challenges

How to write, load, change and test challenges is in the
[challenge authoring guide](../challenges/authoring-guide.md). Points that matter
for whoever runs the server:

- Loading needs the seeder's own settings names (`THEPOND_PROXMOX_TOKEN_SECRET`,
  `THEPOND_PROXMOX_CA_BUNDLE`), not the app's.
- **The seeder only adds.** It never updates a loaded challenge, and a changed
  flag is added as a second flag (known issue C1). Changes are SQL, listed in the
  authoring guide.
- A new template must be added to the token's template pool by re-running the
  playbook (below), or launching it fails.

> **FIX:** on a database seeded before the merge, `dc1` and `kioptrixlevel1`
> have a **duplicate flag row** (one per VM), and a correct flag on either one
> crashes instead of scoring. The seeder no longer does this, but existing rows
> must be removed by hand: the SQL is in [known issues](../known-issues.md) B5.

## The Proxmox token

The app uses its own limited token, `pond@pve!launcher`, created by
`playbooks/pond_least_privilege.yml`. How to run the playbook is in phase 7 of
[build from scratch](../setup/build-from-scratch.md). Re-run it the same way
whenever you add a template, listing every template VMID in
`pond_template_vmids`.

It writes the secret once to `/root/pond-launcher.token`, and won't create a
token while that file exists.

> **DECISION:** the playbook's header says to copy the secret into the app's
> environment and then `shred -u` the file. The launch guide reads the secret
> straight from that file at every start. Keep the file, or move the secret and
> shred it, then update both guides to match.

Every other credential, and how to rotate it, is in the
[access inventory](../handover/access-and-credentials.md).

**Rotating it:** `pveum user token remove pond@pve launcher` on `pve`, remove
the old `/root/pond-launcher.token`, re-run the playbook, then restart the app
with the new secret.

Never give the app a `root@pam` token. The app and `provisioner.py` refuse any
`@pam` token.

## Settings

The web app reads environment variables (`pond-sec/app/config.py`). Anything that
has to be a Python object (the submission and Docker integrations) goes in a
trusted Python file named by `POND_SETTINGS`, which can also override any
setting below. See `deployment/pond-settings.example.py`.

| Variable | Default | What it does |
|---|---|---|
| `PROXMOX_TOKEN_ID` | none | `pond@pve!launcher` |
| `PROXMOX_TOKEN_SECRET` | none | The token secret. `THEPOND_PROXMOX_TOKEN_SECRET` also works |
| `PROXMOX_CA_BUNDLE` | none | Path to Proxmox's CA certificate. Used for API calls and the console relay |
| `PROXMOX_VERIFY_SSL` | on | Only an explicit `0`/`no`/`off`/`false` turns verification off; refused in production |
| `PROXMOX_HOST` / `PROXMOX_NODE` | `10.1.21.151` / `pve` | Which Proxmox server |
| `PROXMOX_POOL` / `PROXMOX_TEMPLATE_POOL` | `pond-clones` / `pond-templates` | Where clones are created / where templates live |
| `PROXMOX_STORAGE`, `PROXMOX_FULL_CLONE` | `local-lvm`, `0` | Full clones only |
| `PROXMOX_PROTECTED_VMIDS` | `300-303` | VMs the app will never clone, start or delete |
| `PROXMOX_SDN_ZONE` | `pondz` | Zone for per-session networks |
| `PROXMOX_LAB_GATEWAY` | `10.1.20.1` | Nothing any more: launch stopped passing it when every target got its own network (known issue L15) |
| `PROXMOX_WORKSTATION_ZONE` | empty | Only for a future routed Red Duck network: the SDN zone holding it. The token check then accepts `SDN.Use` on that zone. Must match the playbook's `pond_workstation_vnet` (network reference §5.4) |
| `PROXMOX_TEMPLATE_BRIDGES`, `PROXMOX_TEMPLATE_STORAGES` | empty, `local-lvm` | Must match the playbook's `pond_template_bridges` / `pond_storages` |
| `PROXMOX_PRIVILEGE_CHECK_TTL` | `300` | Seconds a passed token check is trusted |
| `PROXMOX_TASK_TIMEOUT` | `300` | Seconds to wait for a clone, stop or delete |
| `POND_HANDOUT_TEMPLATE` / `POND_HANDOUT_BASE_URL` / `POND_HANDOUT_USER` | `Kali-attacker` / `http://10.1.30.10:8000` / `kali` | Which clones get handouts, from where, into whose Desktop. `POND_HANDOUT_TEMPLATE` also decides which single-VM launches stay on `pondnc` instead of getting their own network |
| `POND_DOCKER_HOST_IP` | `10.1.30.10` | The one private address a Red Duck clone may reach (any TCP port) |
| `POND_WORKSTATION_INTERNET_PORTS` | `80,443` | TCP ports Red Duck may use to the internet, in Proxmox's port syntax. Setting it also allows DNS. Empty means none. Has no effect until `pondnc` has a routed uplink |
| `UPLOAD_QUARANTINE_ROOT` | none | Storage for challenge submissions. Unset means the submissions pages don't work |
| `MAX_LOGIN_ATTEMPTS` / `LOCKOUT_MINUTES` | `3` / `15` | Sign-in lockout (`0` minutes means locks only lift when staff clear them) |
| `MIN_PASSWORD_LENGTH` | `12` | Password policy |
| `IDLE_TIMEOUT_MINUTES` / `SESSION_LIFETIME_MINUTES` | `60` / `720` | Idle sign-out / cookie lifetime |
| `RANGE_ENV` | `development` | `production` forces HTTPS and secure cookies, and refuses to start without `FLASK_SECRET_KEY` and verified TLS |
| `FLASK_SECRET_KEY` | none | Required in production. In development a key is generated into `pond-sec/instance/secret_key` |
| `TRUSTED_PROXIES` | `0` | Number of reverse proxies in front. Leave at `0` when there are none, or rate limits can be bypassed |

The SSH settings (`THEPOND_PROXMOX_SSH_USER`, `THEPOND_PROXMOX_SSH_KEY`,
`THEPOND_PROXMOX_ALLOW_ROOT_SSH`, and optionally `THEPOND_PROXMOX_KNOWN_HOSTS`)
are read by `provisioner.py`, not `config.py`. The seeder and
`import_challenge.py` have their own `THEPOND_PROXMOX_*` names (see the
[authoring guide](../challenges/authoring-guide.md) and phase 5 of the build
guide).

> **FIX:** `WSGI_Files/.env.example` still lists `PROXMOX_BACKEND`,
> `DATABASE_PATH` and `RESET_TOKEN_HOURS`, none of which does anything now.
> Remove them (known issue W8). Note too that the app doesn't read `.env` files
> itself: export the variables, or use a service manager's environment file.

## Backups

There's no backup mechanism. The whole platform state (accounts, scores,
sessions, challenges, the audit log) is the one database file.

Don't back it up with `cp` while the app is running. SQLite can hold recent
changes in side files (`-wal`, `-shm`) that a plain copy misses. Use SQLite's
own backup instead, from the repo root:

```bash
python3 -c "import sqlite3; s=sqlite3.connect('the_pond.db'); d=sqlite3.connect('the_pond-$(date +%F).db'); s.backup(d); d.close(); s.close()"
```

Source: [Python docs: `sqlite3.Connection.backup`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

How to restore one, what else to back up, and the routine upkeep (updates,
template refresh, housekeeping) are in
[maintenance and restore](../operations/maintenance-and-restore.md).

> **DECISION:** how often, where backups are kept, and who keeps them. A backup
> holds password hashes, so it needs the same care as the database.

> **FIX:** `the_pond.db.bak` is committed to the repository and holds account
> password hashes (known issues, §7).

## Leftover clones

A failed launch can leave a clone on Proxmox that the Sessions page never shows.
How to find and remove it is in the [launch guide](launch-guide.md), "Clearing a
leftover clone".

## When a launch fails

The student sees only "The environment could not be started". The reason goes to
the app's log: the terminal it's running in, or wherever its service manager
sends standard error. Look for one of:

```
ERROR in themes: launch failed for instance <id>: <reason>
ERROR in themes: session network for instance <id>: <reason>
```

Common reasons, and what to do:

| Reason contains | Cause | Fix |
|---|---|---|
| `the datacenter firewall is disabled` | Someone switched the Proxmox firewall off | Turn it back on (build guide, phase 4). Every launch fails until you do |
| `is missing or is not a Simple zone` | `pondz` was deleted or changed | Rebuild it (build guide, phase 3) |
| `over-privileged` (the offending paths are logged just above, by `proxmox`) | The token's rights were widened by hand | Re-run the token playbook |
| `is not isolated after rewrite`, `egress rules were written out of order` | Proxmox didn't store a NIC or firewall change as sent | Check the clone's config on `pve`, then clear the leftover clone |
| `Proxmox refused the clone` | Anything else during cloning, SSH or `virt-customize` | Read the rest of the line and the Proxmox task log |

A failed launch can still leave a clone behind (known issue L2): check, as
above.

If the student gets a server error page instead of the message, nothing was
logged by `themes`: look for a Python traceback in the log. The usual one is
`RuntimeError: no free VMIDs in range 301-399`, meaning all 96 clone numbers are
taken (known issue L16). Close sessions or clear leftover clones, then close the
stuck session on **Manage sessions**.

## Known limits

The full list is in [known issues](../known-issues.md). The ones that affect
running a class:

- **Nothing reaps idle sessions.** A session left open holds its VMs until
  someone closes it.
- **The Proxmox datacenter firewall must stay on.** If it's off, every launch
  is refused.
- **A single-VM target can only be reached through its console.** Every launch
  except a lone Red Duck gets a private network, and a target alone on one has
  nothing to attack it from (known issue N8).
- **Red Duck reaches the Docker host over TCP only,** and nothing else
  private. No internet until `pondnc` gets a routed uplink.
- **96 clone VMIDs** in total, across all sessions.
