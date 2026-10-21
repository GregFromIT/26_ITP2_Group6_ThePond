# Code map

What each file does, and where to look when something breaks. Every Python file
starts with a docstring that goes into more detail; this map tells you which one
to open.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. Line counts are approximate.

---

## Vocabulary

The code was renamed partway through, so old names survive in identifiers:

| In the UI | In the code | Meaning |
|---|---|---|
| Theme | `theme_id`, `themes.py`, `_theme_dict()` | A group of challenges. Stored as the free-text `challenges.category`; there is no theme table |
| Challenge | `Challenge` | One exercise, backed by one or more VM templates |
| Session | `ChallengeInstance`, "instance" | One student's run at one challenge |
| Clone | `VMInstance` | One VM cloned for a session |
| Template | `VMTemplate` | A Proxmox template that clones come from; reusable across challenges via `ChallengeTemplate` |
| Moderator / System administrator | `moderator` / `admin` in the app; `manager` / `sysadmin` in the database | Mapped in `identity.py`, `ROLE_NAME_TO_APP` |

---

## How a request moves through the app

1. `pond-sec/wsgi.py` creates the app and hands it to the web server.
2. `app/__init__.py` runs the request hooks, in this order: force HTTPS → check
   the CSRF token → load the signed-in user → hold unapproved accounts on the
   waiting page → pin accounts with a temporary password to the change-password
   page.
3. A blueprint handles it: `auth`, `dashboard`, `themes`, `admin` or `uploads`.
4. The blueprint calls the support modules: `scoring` for points, `roles` for
   permissions, `proxmox` for VMs, `identity` and `security` for accounts, and the
   SQLAlchemy models in `db/` for data.
5. A template in `app/templates/` renders the page, and `security_headers()`
   adds the response headers on the way out.

Imports go one way: blueprints use the support modules, and support modules
don't import blueprints. The one exception is `admin.close_session()`, which
imports `themes._close()` inside the function, so staff closing a session uses
the same teardown as a student.

---

## The web app: `pond-sec/`

### Start-up

| File | Lines | What it does |
|---|---|---|
| `wsgi.py` | 29 | Creates the app. Its docstring has the development, gunicorn and Windows (waitress) commands. Never run with `--debug` on a network |
| `app/__init__.py` | 318 | `create_app()`: loads `Config`, then the `POND_SETTINGS` file, sets up the database bind (default `the_pond.db` at the repo root), registers blueprints and hooks. Also the secret key (`resolve_secret_key()`), the start-up Proxmox settings check, security headers, the idle timeout, error pages and the `|stamp` / `|duration` template filters |
| `app/config.py` | 176 | Every setting, each read from an environment variable with a default. `RANGE_ENV=production` tightens the defaults (secure cookies, HTTPS, no start without a secret key). Secrets have no defaults |

### Accounts and security

| File | Lines | What it does |
|---|---|---|
| `app/auth.py` | 351 | `/register`, `/login`, `/logout`, `/change-password`, `/pending`. The `login_required` decorator. The approval gate and the forced password change |
| `app/identity.py` | 70 | The bridge between the SQLAlchemy `User`/`Role` models and the dict-shaped `g.user` the templates expect. Maps database role names to app role names |
| `app/security.py` | 161 | Password hashing (Werkzeug scrypt), password rules, lockout counting, temporary passwords. The only code that reads or writes `user_credentials` |
| `app/roles.py` | 158 | The three roles and the `PERMISSIONS` matrix. `require()` guards a view; `can()` hides UI; `outranks()` compares ranks. Hiding a button isn't access control: `require()` is |
| `app/csrf.py` | 66 | One token per session, required on every POST, compared with `secrets.compare_digest`. Every form needs the hidden `_csrf` field |
| `app/throttle.py` | 99 | Rate limits, all in the `LIMITS` dict at the top. Counters live in the database (`throttle_events`), so several workers share one allowance |
| `app/audit.py` | 69 | `record()` writes an `audit_logs` row; the event names are constants at the top. A write failure never breaks the request |

### Pages

| File | Lines | What it does |
|---|---|---|
| `app/dashboard.py` | 102 | `/` (landing page) and `/dashboard`. Asks `scoring` for every number |
| `app/themes.py` | 740 | **The core.** Theme list and theme page; `launch()` (which decides whether a launch gets a session VNet or is a lone Red Duck, and builds Red Duck's outbound rules in `_workstation_egress()`), `flag()`, `close()`, `timer()`; the console page and the WebSocket relay to Proxmox. `_owned_instance()` returns 404 for anyone else's session, and every session route must call it. `_close()` records the result *before* destroying VMs, so a hypervisor failure can't cost a student their score |
| `app/admin.py` | 498 | The staff console at `/admin`: accounts, approvals, unlock/lock, temporary passwords, roles, sessions, audit log. Also the `flask --app wsgi set-role` CLI command |
| `app/uploads.py` | 248 | Challenge submission pages under `/admin/challenge-submissions`, and the `/notifications` inbox. Thin over `challenge_ingestion/` |

### Calculations and outside systems

| File | Lines | What it does |
|---|---|---|
| `app/scoring.py` | 314 | `submit_flag()` grades a flag; `challenge_progress()`; the overall and per-theme leaderboards; the per-challenge matrix; `user_stats()`. Admins are excluded from every board |
| `app/proxmox.py` | 406 | The hypervisor adapter: the only file in `app/` that knows Proxmox exists. Checks settings (`settings_problems()`), refuses `@pam` tokens and protected VMIDs, checks the token's effective rights before every launch (`_check_token_privileges()` against `allowed_privileges()`, cached `PROXMOX_PRIVILEGE_CHECK_TTL` seconds), checks isolation can work (`provisioner.check_isolation_preconditions()`, every clone), and calls `provisioner.py` for the actual work. Turns Proxmox errors into `ProxmoxError` |
| `app/container_lab.py` | 73 | The boundary to an external Docker server for `container_lab` challenges. Calls `CONTAINER_LAB_ADAPTER.start()` / `.stop()`. No adapter exists yet |

### Templates and static files

- `app/templates/`: `base.html` (navigation) and one template per page.
  `theme_detail.html` is the theme page with the challenge tiles and the live
  session panel. `admin/` holds the staff console pages.
- `app/static/js/session.js` keeps the session timer (polling `/timer` every
  30 s), `console.js` runs noVNC against the relay, and `uploads.js` streams VM image files
  on a submission's page. Scripts are external files because the content
  security policy forbids inline script.
- `app/static/novnc/`: the vendored noVNC client.

---

## Provisioning: repo root

| File | Lines | What it does |
|---|---|---|
| `provisioner.py` | 638 | Every Proxmox operation. `get_client()`; `claim_vmid()` (VMID allocation, locked by a partial unique index); `clone_and_start()`; `create_session_vnet()` / `destroy_session_vnet()`; `check_isolation_preconditions()` (Simple zone, datacenter firewall on); `_firewall_nics()` (every NIC onto the VNet with `firewall=1`, read back); `enable_vm_firewall()` (inbound DROP, plus outbound rules when given, order verified) / `apply_network_rule()`; `inject_instance_network()` / `inject_firstboot_command()` (writing into a stopped clone's disk with `virt-customize` over SSH); `stop_and_destroy()`; `get_console_ticket()`. Holds `DEFAULT_VMID_RANGE`, `PROTECTED_VMIDS` and `VNET_ZONE` |
| `import_challenge.py` | 416 | Interactive: downloads a VM image onto `pve`, extracts it, imports the disk, makes a template in 10000–10999, and writes the challenge YAML. Uses its own `THEPOND_PROXMOX_*` settings |
| `group_vars/all.yml` | — | Proxmox host, node and token name, read by the seeder through `provisioner.load_config()` |
| `playbooks/pond_least_privilege.yml` | — | Creates the app's Proxmox user, token, roles, pools and ACLs. Run on `pve` |

## Database: `db/`

| File | What it does |
|---|---|
| `orm.py` | The shared `SQLAlchemy` object; foreign-key enforcement for SQLite (off by default in SQLite, switched on for every connection here); `utcnow_naive()`, the one way the code gets "now" (UTC, without a time zone, to match what SQLite returns) |
| `user_models.py`, `challenge_models.py`, `VMs_models.py`, `challenge_template_models.py`, `runtime_models.py`, `scoring_models.py`, `audit_models.py`, `throttle_models.py` | The live tables (see the [data model](../database/pond-sec-data-model.md)) |
| `submission_models.py`, `submission_file_models.py`, `submission_job_models.py`, `submission_issue_models.py`, `notification_models.py` | The submission pipeline's tables |
| `__init__.py` | Imports every model except `throttle_models`, so `from db import …` works |
| `init_database.py` | `python3 -m db.init_database [--check \| --seed-challenges]`: creates missing tables and the three roles, checks the schema, and seeds challenges from `vars/challenges/*.yml` |
| `seed_accounts.py` | `python3 -m db.seed_accounts [--check] [--debug] [--reset]`: creates the accounts in its `ACCOUNTS` list |
| `register_template.py`, `cleanup_docker_session.py`, `migrate_container_labs.py` | `container_lab` support (see [submission pipeline](../challenges/submission-pipeline.md)) |
| `oneoff_add_account_approval.py` | A completed one-off migration; the example to copy for the next one |
| `database_app.py` | A minimal Flask app with the default bind, used by `provisioner.py` when it runs outside the web app |
| `store.py`, `schema.sql`, `init_user_db.py`, `init_databases.py`, `test_database.py` | Legacy (pre-SQLAlchemy MVP), or thin compatibility shims |

## Challenge submissions: `challenge_ingestion/`

| File | What it does |
|---|---|
| `requirements.py` | The manifest JSON schema and the fixed limits |
| `storage.py` | Private quarantine storage on local disk |
| `intake.py` | Draft creation, file upload, quota accounting |
| `validation.py` | Read-only validation; calls the site's template verifier and image inspector |
| `worker.py` | Lease-based validation worker |
| `review.py` | Admin approve, reject, return for correction |
| `publication.py` | Publishes an approved revision; calls the site's publication adapter for VMs |
| `notifications.py` | In-app notification delivery and inbox |
| `runtime.py` | `python3 -m challenge_ingestion.runtime`: runs one round of the workers |

## Content and tools

| Path | What it is |
|---|---|
| `vars/challenges/*.yml` | One file per challenge (see the [authoring guide](../challenges/authoring-guide.md)) |
| `tools/gen_bsides_challenges.py`, `tools/gen_bsidessf24_challenges.py` | Generate challenge files (and a Docker deploy script) from an upstream CTF release |
| `tools/pond-deploy-sf24.sh` | Generated deploy script for the BSidesSF 2024 containers, run on the Docker host |
| `examples/challenge_uploads/*.json` | Example submission manifests |
| `deployment/pond-settings.example.py` | Example `POND_SETTINGS` file |
| `deployment/docker-host/` | The Docker host's configuration |
| `WSGI_Files/.env.example` | Example environment settings (partly stale, known issue W8) |

## Tests

| Path | Run with | Covers |
|---|---|---|
| `tests/` | `python3 -m pytest tests` | The live suite: accounts and approval, container labs and their migration, the submission pipeline, provisioner, network isolation, handouts, store |
| `pond-sec/tests/test_credentials.py` | `python3 pond-sec/tests/test_credentials.py` | Token, TLS, protected-VM and privilege-check rules, plus a check that the playbook matches the app's allowlist. A script, not a pytest file |
| `pond-sec/tests/test_flow.py` | — | Broken: written for the deleted raw-SQLite app |

More detail is in [testing](../testing/testing.md).

## Legacy, not part of the running system

`debugger-app.py` with `templates/` and the `static/novnc` submodule (the
original single-file debugger app); `proxmoxerTest.py`; `prov_net_test.py`;
`db.test_database.py`; `playbooks/create_instance.yml` and
`playbooks/destroy_instance.yml` with `inventory/` and `requirements.yml`;
`pond-sec/tools/build_preview.py` and `pond-sec/preview/` (broken).
[Known issues](../known-issues.md) §7 says what to do with each.

---

## Where to look when something breaks

| Symptom | Start here |
|---|---|
| "That form expired" on submit | `csrf.py`: the form is missing its `_csrf` field, or the session expired |
| A 403 for someone who should have access | `roles.py`, `PERMISSIONS` |
| Someone can sign in but sees only "Waiting for approval" | `auth.gate_unapproved_accounts()`: approve them at `/admin/approvals` |
| Every launch fails | The app log's `launch failed for instance` line. Then: the start-up log for `Proxmox settings:` warnings → `proxmox.settings_problems()`; `_check_token_privileges()`; `provisioner.check_isolation_preconditions()` (datacenter firewall off?); the Proxmox task log |
| Only multi-VM or Red Duck launches fail | SSH to `pve`: `provisioner._ssh_client()` (key, user, known host) |
| A launch gives a server error instead of "could not be started" | An exception `launch()` doesn't catch: a full VMID range (`RuntimeError`, known issue L16) or a refused network rule (L14). The traceback is in the app log |
| Red Duck can't reach something it should | `themes._workstation_egress()` and `POND_DOCKER_HOST_IP`; on `pve`, `pvesh get /nodes/pve/qemu/<vmid>/firewall/rules` |
| The console page opens but stays blank | `themes.console_relay()`; the browser's developer tools should show the WebSocket upgrade with status 101 |
| A correct flag gives a 500 error | Duplicate flag rows left by a pre-merge seed (known issue B5) |
| Scores look wrong | `scoring.py` |
| A student can't sign in | `security.py` for the lockout, `throttle.py` for rate limits |
| Something changed and nobody knows who | `/admin/audit` |
