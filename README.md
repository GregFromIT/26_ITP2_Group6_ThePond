# The Pond

A self-hosted capture-the-flag (CTF) training range on Proxmox. Students sign in
to a web app (**Pond Sec**), launch a challenge, get a fresh virtual machine with
a browser console, find flags, and score points on leaderboards. Staff approve
accounts, run classes and add challenges. It runs on a single Proxmox VE 9
server, with a Flask web app and an SQLite database.

Built in 2026 as a UNSW Canberra ZEIT3101 IT Project 2 capstone (Group 6).

> **These documents describe `main` (`04998a1`) with the branch
> `fix/seeder-theme-flagformat` (`3dc6afc`) merged.** Until that merge is done,
> `main` itself can't create a database or start the web app, and only multi-VM
> sessions are network-isolated. See [known issues](docs/known-issues.md) §1.

---

## Start here

| You are… | Read, in order |
|---|---|
| **New to the project** | [System overview](docs/architecture/system-overview.md) → [known issues](docs/known-issues.md) |
| **Building your own copy** on your own hardware | [System overview](docs/architecture/system-overview.md) → [build from scratch](docs/setup/build-from-scratch.md) → [network reference](docs/networking/pond-proxmox-vmid-network-reference.md) → [Docker host](deployment/docker-host/README.md) |
| **Adding your own challenges** | [Challenge authoring guide](docs/challenges/authoring-guide.md) → [submission pipeline](docs/challenges/submission-pipeline.md) |
| **Running the system** day to day | [Launch guide](docs/user-guides/launch-guide.md) → [admin guide](docs/user-guides/pond-sec-admin-guide.md) → [maintenance and restore](docs/operations/maintenance-and-restore.md) |
| **A student** | [Student guide](docs/user-guides/pond-sec-student-guide.md) |
| **Handing over, or taking over** | [Handover checklist](docs/handover/handover-checklist.md) → [access and credentials](docs/handover/access-and-credentials.md) |
| **Changing the code** | [Contributing](CONTRIBUTING.md) → [code map](docs/architecture/pond-sec-code-map.md) → [data model](docs/database/pond-sec-data-model.md) → [design decisions](docs/architecture/pond-sec-design-decisions.md) → [testing](docs/testing/testing.md) |

---

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/architecture/system-overview.md`](docs/architecture/system-overview.md) | The components, roles, challenge shapes, how a launch works, the networks |
| [`docs/setup/build-from-scratch.md`](docs/setup/build-from-scratch.md) | Bare hardware to first launch, in ten phases, with a table of every site-specific value |
| [`docs/networking/pond-proxmox-vmid-network-reference.md`](docs/networking/pond-proxmox-vmid-network-reference.md) | Proxmox host, VMID ranges, token permissions, every network hop, isolation, firewall, gateway rules, acceptance tests |
| [`deployment/docker-host/README.md`](deployment/docker-host/README.md) | The Docker host that serves the Red Duck challenges (its config files still need copying in) |
| [`docs/user-guides/launch-guide.md`](docs/user-guides/launch-guide.md) | Power on, start the VMs, start the app, check it works, clear leftovers |
| [`docs/user-guides/pond-sec-admin-guide.md`](docs/user-guides/pond-sec-admin-guide.md) | The staff console for moderators and admins; the server for the system administrator; every setting |
| [`docs/user-guides/pond-sec-student-guide.md`](docs/user-guides/pond-sec-student-guide.md) | Using the platform as a student |
| [`docs/challenges/authoring-guide.md`](docs/challenges/authoring-guide.md) | Writing, loading, changing and testing challenges |
| [`docs/challenges/submission-pipeline.md`](docs/challenges/submission-pipeline.md) | The web upload route: what works, and the interfaces a site must supply |
| [`docs/architecture/pond-sec-code-map.md`](docs/architecture/pond-sec-code-map.md) | Every live file, and where to look when something breaks |
| [`docs/database/pond-sec-data-model.md`](docs/database/pond-sec-data-model.md) | All 20 tables, generated from the models |
| [`docs/architecture/pond-sec-design-decisions.md`](docs/architecture/pond-sec-design-decisions.md) | Why things are the way they are |
| [`docs/operations/maintenance-and-restore.md`](docs/operations/maintenance-and-restore.md) | Backups and restoring them; updating Proxmox, templates, controller and Docker host; housekeeping |
| [`docs/handover/handover-checklist.md`](docs/handover/handover-checklist.md) | What the outgoing team must finish before handover |
| [`docs/handover/access-and-credentials.md`](docs/handover/access-and-credentials.md) | Every account, key and secret: where it lives, who holds it, how to rotate it (no secrets in it) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Working on the code safely: a separate database, the rules the code depends on, checks before merging |
| [`docs/testing/testing.md`](docs/testing/testing.md) | Running the tests; testing the live system |
| [`docs/known-issues.md`](docs/known-issues.md) | Every open defect, gap and security finding, with status |
| [`pond-sec/docs/SECURITY_ASSESSMENT.md`](pond-sec/docs/SECURITY_ASSESSMENT.md) | The security assessment of 29 Sep 2026 (a dated record) |
| [`docs/diagrams/`](docs/diagrams/) | Workflow, database and use-case diagrams. Not re-checked against `main`: the scoring diagram still shows a points total on the user table, which no longer exists |
| [`docs/archive/`](docs/archive/README.md) | Superseded documents, kept for history |

---

## Repository layout

**Live: the running system.**

| Path | What it is |
|---|---|
| `pond-sec/` | The Pond Sec web app (Flask): `app/` code, templates and static files; `wsgi.py` entry point |
| `provisioner.py` | Every Proxmox operation: cloning, VMIDs, session networks, firewall rules, disk injection, teardown |
| `db/` | SQLAlchemy models, `init_database` (tables and challenge seeding), `seed_accounts` |
| `challenge_ingestion/` | The challenge submission pipeline |
| `vars/challenges/` | One YAML file per challenge |
| `playbooks/pond_least_privilege.yml` | Creates the app's Proxmox token, roles and pools |
| `group_vars/all.yml` | Proxmox host and token name, used by the seeder |
| `import_challenge.py` | Imports a VM image as a template and writes its challenge file |
| `tools/` | Generators for importing CTF releases, and the BSidesSF 2024 Docker deploy script |
| `deployment/` | Example settings file; the Docker host's configuration |
| `examples/challenge_uploads/` | Example submission manifests |
| `tests/`, `pond-sec/tests/test_credentials.py` | The test suites |
| `requirements.txt`, `pond-sec/requirements.txt` | Python dependencies (`python-dotenv` and `pytest` still missing; known issues B3, B4) |

**Legacy: not part of the running system.** `debugger-app.py`, `templates/`,
`static/novnc` (submodule), `proxmoxerTest.py`, `prov_net_test.py`,
`db.test_database.py`, `db/store.py`, `db/schema.sql`,
`playbooks/create_instance.yml`, `playbooks/destroy_instance.yml`,
`inventory/`, `requirements.yml`, `pond-sec/tests/test_flow.py`,
`pond-sec/tools/`, `pond-sec/preview/`. Files that shouldn't be in the
repository at all (`the_pond.db.bak`, `odn`, `.provisioner.py.swp`) are
listed in [known issues](docs/known-issues.md) §7.

---

## Quick reference

```bash
# Install (controller, repo root)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r pond-sec/requirements.txt
pip install python-dotenv pytest               # missing from the requirements files

# Database
python3 -m db.init_database                    # tables and roles
python3 -m db.seed_accounts                    # first accounts (edit ACCOUNTS first)
python3 -m db.init_database --seed-challenges  # challenges (needs Proxmox; see the build guide)

# Run (development server; see the launch guide for the settings it needs)
cd pond-sec && flask --app wsgi run --host 0.0.0.0 --port 5000

# Tests
python3 -m pytest tests -q
python3 pond-sec/tests/test_credentials.py
```

Each of these is explained, with its prerequisites, in the
[build guide](docs/setup/build-from-scratch.md) and the
[launch guide](docs/user-guides/launch-guide.md).
