# Testing

How to run the tests, what they cover, what they don't, and how to test the live
system.

Results below are from a clean install of `main` at `04998a1` with
`fix/seeder-theme-flagformat` (`3dc6afc`) merged, on Python 3.13 (21 Oct 2026),
with this documentation in place.

---

## Setup

From the repo root, with the venv active:

```bash
pip install -r requirements.txt -r pond-sec/requirements.txt
pip install python-dotenv pytest
```

`python-dotenv` and `pytest` aren't in the requirements files yet (known issues
B3, B4).

The automated tests need no Proxmox, no network and no real database. They use
temporary SQLite files and a fake Proxmox (`tests/fake_proxmox.py`).

---

## The suites

| Suite | Command | Result |
|---|---|---|
| Main suite, `tests/` | `python3 -m pytest tests -q` | 369 passed |
| Credentials, `pond-sec/tests/test_credentials.py` | `python3 pond-sec/tests/test_credentials.py` | 121/121 checks pass |
| `pond-sec/tests/test_flow.py` | — | Broken: imports the deleted `app.db` |

Run the suite as **`python3 -m pytest tests`**, not bare `pytest`. From the
repo root, bare `pytest` also collects `pond-sec/tests/`, where
`test_credentials.py` is a script that calls `sys.exit()` when imported.
pytest stops with `INTERNALERROR … SystemExit: 0` and runs nothing.

Use `python3 -m pytest` rather than `pytest`, so the venv's interpreter is used
([pytest: Calling pytest through python -m pytest](https://docs.pytest.org/en/stable/how-to/usage.html#calling-pytest-through-python-m-pytest)).

On `main` alone (before the merge), the main suite fails to collect, because
`db/init_database.py` has an `IndentationError` (known issue B1).

`test_credentials.py` also reads documentation: it checks that
`pond-sec/docs/README.md` still holds the token runbook (the playbook command,
the `read -rs` probes, the `zgrep` check), and that the playbook header matches.
Edit those sections with the test open.

---

## What the main suite covers

| File | Covers |
|---|---|
| `test_account_approval.py` | Registration, the approval gate, approve and reject, the `set-role` command, `seed_accounts` |
| `test_database_initialization.py` | `db.init_database`: creating tables and roles, keeping existing data, `--check` |
| `test_container_labs.py` | The launch and close routes for VM, `container_lab` and offline challenges, with the Proxmox calls replaced by stubs, including which launches get a session VNet and which stay on `pondnc` with Red Duck's outbound rules; shared Red Duck templates; Docker adapter failure and recovery; flag scoring and ownership |
| `test_container_lab_migration.py` | `db.migrate_container_labs`: check, apply, backup, rollback, refusals |
| `test_handout_firstboot.py` | The Red Duck first-boot handout command |
| `test_network_isolation.py` | The isolation helpers in `provisioner.py`: every NIC moved and firewalled (and the read-back refusing when a change didn't take), the Simple-zone and datacenter-firewall checks, outbound rules written in order (and refused when out of order), inbound rules created enabled |
| `test_provisioner.py` | One test: the console ticket request (`get_console_ticket()`) |
| `test_store.py` | The legacy `db/store.py` |
| `test_challenge_requirements.py`, `test_submission_*.py`, `test_validation_worker.py`, `test_quarantine_storage.py`, `test_notification_*.py`, `test_ingestion_runtime.py`, `test_upload_routes.py` | The submission pipeline: manifest schema, storage, intake, validation, review, publication, notifications, the upload pages |

`pond-sec/tests/test_credentials.py` pins which tokens and TLS settings the
Proxmox adapter accepts, the protected-VM rules, and the per-launch privilege
check. It also feeds the playbook's grants through the app's own allowlist, so
the playbook and `proxmox.py` can't drift apart.

## What the tests don't cover

- **Parts of the provisioner:** VMID allocation (`claim_vmid()`), session VNet
  creation and teardown have no unit tests. The launch tests replace them with
  stubs.
- **Anything real:** a Proxmox clone, `virt-customize`, a VNet, the firewall,
  the console relay, the Docker host, a handout download.
- **Concurrency:** simultaneous flag submissions, simultaneous launches,
  lockout races (security assessment M3, L2).
- **The challenge seeder** (`seed_challenges_from_yaml()`) against real
  challenge files. That's how B1 and B5 reached `main`. Add a test that runs it
  against `vars/challenges/` with a stubbed `provisioner`.
- **What a launch does when an uncaught error happens:** a full VMID range or a
  refused network rule (known issues L14, L16).
- The student and staff pages, beyond what the approval, upload and
  container-lab tests touch.

---

## Testing the live system

Run these on the real hardware after a build, after any change to the network,
templates or token, and before a class.

1. **The launch guide's checks:** site loads, Docker host serves handouts, a Red
   Duck test launch works and tears down. See step 5 of the
   [launch guide](../user-guides/launch-guide.md).
2. **Network acceptance tests N1–N15:** §9 of the
   [network reference](../networking/pond-proxmox-vmid-network-reference.md).
   Record the result, the date and who ran each one.
3. **One challenge of each shape:** single-VM target, multi-VM, Red Duck. Launch, open
   the console, submit the flag, check the score, close, and check the VMs are
   gone. See §8 of the [authoring guide](../challenges/authoring-guide.md).
4. **The security assessment's live tests:** concurrent lockout, idle expiry,
   console ticket replay, reachability between sessions. Listed under
   "Outstanding live tests" in
   [`SECURITY_ASSESSMENT.md`](../../pond-sec/docs/SECURITY_ASSESSMENT.md).

While testing launches, watch `pve`:

```bash
watch -n2 'qm list | grep -v " stopped"; pgrep -a virt-customize'
```

[`watch(1)`](https://man7.org/linux/man-pages/man1/watch.1.html) refreshes every
2 seconds. The command shows running VMs, where new clones appear, and any
`virt-customize` run, which is the app preparing a clone's disk.

Earlier results, kept for history:
[`docs/archive/`](../archive/README.md) (`challenge-upload-test-results.md`,
`CONTAINER_LAB_TEST_RESULTS.md`, `novnc-console-testing.md`).
