# Contributing

How to change The Pond's code without breaking the running system: set up a
copy to work on, follow the rules the code depends on, test, and get the change
reviewed and merged.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. The [code map](docs/architecture/pond-sec-code-map.md) says
what each file does; this says how to change them.

GitHub shows this file to anyone opening a pull request
([GitHub: contributor guidelines](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors)).

---

## 1. The rules we worked to

From the team charter (Appendix A.3 and §4.6). They're Group 6's rules. **The
next team should confirm or replace them (DECISION):**

- **Feature branches.** Never commit straight to `main`. The prefixes in use are
  `fix/…`, `feature/…` and `docs/…`.
- **A second person reviews every merge to `main`.** That includes changes to
  `playbooks/pond_least_privilege.yml`, which runs as root on the hypervisor.
- **Only working code is merged:** both test suites pass (§4).
- **Descriptive commit messages,** and a version increment on merge to `main`
  (semantic versioning, as tracked in `Version Plans.xlsx`).
- **Anything that changes isolation, security, infrastructure or scope** is a
  team decision, and may need the client's approval (charter §3.4, change
  control). The 20 Oct "isolate every launch" change is an example.
- **AI-assisted work:** whoever commits it must be able to explain, test and
  maintain it (charter A.3, AI Usage Policy).

Nothing enforces these: `main` has no branch protection that we know of, and
there's no CI. Turning on branch protection, requiring a review, would make the
first two automatic
([GitHub: protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)).

---

## 2. A copy to work on

### 2.1 Never against the live database

The app keeps its database at the repo root by default (`the_pond.db`). If you
work in the controller's checkout, **a plain `flask run` uses the live
database.** Always point a working copy at its own file with a settings file.
Both the app and the `db.*` scripts read it, because they all start through
`create_app()`:

```bash
cat > ~/pond-dev-settings.py <<'EOF'
SQLALCHEMY_BINDS = {"pond": "sqlite:////home/<you>/pond-dev.db"}
EOF
export POND_SETTINGS=~/pond-dev-settings.py
```

`POND_SETTINGS` is loaded with `app.config.from_pyfile()`
(`pond-sec/app/__init__.py`;
[Flask: configuring from Python files](https://flask.palletsprojects.com/en/stable/config/#configuring-from-python-files)).
Four slashes in the URL means an absolute path
([SQLAlchemy: SQLite URLs](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#connect-strings)).

### 2.2 Set it up

On any Linux or macOS machine with Python 3.12 or later. No Proxmox needed:

```bash
git clone https://github.com/GregFromIT/26_ITP2_Group6_ThePond.git
cd 26_ITP2_Group6_ThePond
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r pond-sec/requirements.txt
pip install python-dotenv pytest
export POND_SETTINGS=~/pond-dev-settings.py     # §2.1

python3 -m db.init_database                      # tables and roles
python3 -m db.seed_accounts                      # the accounts in ACCOUNTS; prints temporary passwords
```

### 2.3 Challenges without Proxmox

The real seeder looks every template up on Proxmox. To load the challenge files
into a working copy anyway, stand in for those three calls. Save this as
`~/pond_dev_seed.py`, **outside the repo**, and run it from the repo root with
`PYTHONPATH=.:pond-sec python3 ~/pond_dev_seed.py`:

```python
"""Seed vars/challenges/ into a working copy without Proxmox.
Fake template VMIDs from 10000 up, one per template name."""
import provisioner
from db import init_database as init

ids = {}
provisioner.load_config = lambda: {"proxmox_node": "pve"}
provisioner.get_client = lambda cfg: None
provisioner._resolve_template_vmid = lambda client, node, name: ids.setdefault(name, 10000 + len(ids))

app = init.create_app()
with app.app_context():
    init.seed_challenges_from_yaml()
```

It replaces the three `provisioner` functions the seeder calls (`db/init_database.py`,
`seed_challenges_from_yaml()`). No test does this yet; turning it into one would
cover a gap noted in [testing](docs/testing/testing.md).
**Never run it with `POND_SETTINGS` unset:** it would write fake VMIDs into the
live database.

### 2.4 Run it

```bash
cd pond-sec && flask --app wsgi run --port 5000
```

You'll get `Proxmox settings:` warnings, and that's expected. Without Proxmox you
can register, approve, sign in, browse themes, use the staff console and see the
leaderboards. **Launching** fails with "could not be started", by design: the
app fails closed when it has no token. Testing launches needs the real system,
or the stubs the tests use (§4).

Don't add `--debug` on any machine others can reach: the Werkzeug debugger runs
any Python a visitor types ([Werkzeug: debugger](https://werkzeug.palletsprojects.com/en/stable/debug/)).

---

## 3. Rules the code depends on

Each of these is enforced by nothing but people knowing it. Break one and the
system still runs, but stops being safe or correct.

| Rule | Why | Where |
|---|---|---|
| A new route follows `@bp.route` → `@login_required` → `_owned_instance()` (for anything touching a session) → `throttle` if it can be hammered → `audit.record()` if it changes state | `_owned_instance()` is the only thing stopping a student reaching another student's VM. It returns 404, not 403, so session IDs can't be probed | `themes.py` docstring |
| Every POST form includes the hidden `_csrf` field | Every POST is refused without it | `app/csrf.py` |
| Permissions are checked with `roles.require()` in the view, not by hiding buttons | Hiding UI isn't access control. Known issue W4 is what happens when a route forgets | `app/roles.py` |
| Only `app/proxmox.py` and `provisioner.py` talk to Proxmox, and Proxmox errors become `ProxmoxError` | `launch()` only catches `ProxmoxError`: anything else gives the student a server error and a stuck session (known issue L16) | `app/proxmox.py` |
| Anything acting on a VMID calls `_refuse_protected()` first | VMIDs 300–303 belong to another project | `provisioner.py`; `test_credentials.py` checks it |
| A new Proxmox call that needs a new privilege changes **both** `allowed_privileges()` in `app/proxmox.py` **and** the playbook | The app refuses to launch if the token holds anything the allowlist doesn't, and fails if it lacks what it needs. `test_credentials.py` checks the two agree | `app/proxmox.py`, `playbooks/pond_least_privilege.yml` |
| "Now" comes from `db.orm.utcnow_naive()` | SQLite returns times without a time zone. Comparing one with an aware time raises `TypeError` | `db/orm.py` |
| A new column needs a one-off migration | `db.init_database` creates missing tables but refuses to alter existing ones | `db/oneoff_add_account_approval.py` is the example |
| Scores are never edited, from any role | They're the record of what a student did | `roles.py`, note under `PERMISSIONS` |
| Isolation changes are made in the launch path, before the VM boots | A rule applied after boot leaves a window, and an error after boot isn't handled (L14) | `provisioner.clone_and_start()` |

---

## 4. Before you open a pull request

From the repo root, with the venv active:

```bash
python3 -m pytest tests -q                    # expect: all pass (369 at 3dc6afc)
python3 pond-sec/tests/test_credentials.py    # expect: N/N checks passed (121 at 3dc6afc)
```

Run pytest as `python3 -m pytest tests`, not bare `pytest`: see
[testing](docs/testing/testing.md) for why, and for what each suite covers.

Optional, but it catches mistakes the tests don't:

```bash
pip install pyflakes && python3 -m pyflakes provisioner.py pond-sec/app db
```

([pyflakes](https://pypi.org/project/pyflakes/)). It lists unused imports and
undefined names. Some unused imports are already there (known issues §7).

**`test_credentials.py` reads documentation too.** It checks that
`pond-sec/docs/README.md` still holds the token runbook (the playbook command,
the `read -rs` probes, the `zgrep` check) and that the playbook header still
says how to run it. Edit those with the test open.

### Don't commit

- secrets of any kind: tokens, passwords, keys, `.env` files;
- databases or backups (`*.db`, `*.bak`; `the_pond.db.bak` is an existing
  mistake, known issues §7);
- `__pycache__` and `.pyc` files. Two are tracked by mistake and show as
  modified after every run: `git checkout -- db/__pycache__/` before you
  commit, or `git rm --cached` them once (checklist 2.8);
- real flags, while the repository is public (known issue C6).

---

## 5. Update the docs with the code

If your change makes a document wrong, fix the document in the same pull
request. Where each kind of change is documented:

| You changed | Update |
|---|---|
| A setting in `config.py` | Admin guide, "Settings"; the launch guide if it's needed to start |
| Launch, isolation or the firewall | [Network reference](docs/networking/pond-proxmox-vmid-network-reference.md) §3.1, §5, §6; [system overview](docs/architecture/system-overview.md) |
| A model or table | Re-generate the column reference in the [data model](docs/database/pond-sec-data-model.md) |
| A page students see | [Student guide](docs/user-guides/pond-sec-student-guide.md) |
| A staff page or role | [Admin guide](docs/user-guides/pond-sec-admin-guide.md) |
| The challenge file format or seeder | [Authoring guide](docs/challenges/authoring-guide.md) |
| The token's privileges | Network reference §2.3; `pond-sec/docs/README.md` |
| A bug fixed or found | [Known issues](docs/known-issues.md): mark it Fixed with the commit, or add it |
| A design choice | [Design decisions](docs/architecture/pond-sec-design-decisions.md) |

Each document says at the top which commit it was checked against. Update that
line when you re-check it.
