# Known issues

Every open defect, gap and security finding in The Pond, in one place, with its
status. Checked against `main` at `04998a1` **with the branch
`fix/seeder-theme-flagformat` (`3dc6afc`) merged into it** (21 Oct 2026). Where
an item is marked fixed, the commit that fixed it is named; all of those commits
are on that branch.

> Until that branch is actually merged, everything marked **Fixed** below is
> still broken on `main` itself.

Read this before you run a class, before you change the code, and before you
build a copy of the system.

**Status key**

- **Open:** wrong or missing, with no fix anywhere yet.
- **Fixed:** fixed by the merge, in the commit named. Kept in the list so that
  anyone comparing with older notes can see what changed.
- **Accepted:** a known limitation that the team chose to live with.
- **Infra:** a property of the live servers or network, which the code can't
  show. Check it on the hosts.

---

## 1. Blocking: fix these first

These stop a fresh install from working. The first two were the reason `main`
couldn't be installed at `04998a1`; the merge fixes them.

| # | Problem | Effect | Status |
|---|---|---|---|
| B1 | `db/init_database.py` has an `IndentationError` at line 108, from commit `dd032e6` | `python3 -m db.init_database` fails in every mode, including `--check` and `--seed-challenges`, so no new database can be created and no challenge can be added. `pytest tests` also fails at collection | Fixed (`97ac461`) |
| B2 | `paramiko` is in neither `requirements.txt` nor `pond-sec/requirements.txt` | The web app won't start on a fresh install: `wsgi` → `app.proxmox` → `provisioner` → `import paramiko` | Fixed (`d23048a`). `requests` was added at the same time |
| B3 | `python-dotenv` is in no requirements file | `import_challenge.py` won't start | Open |
| B4 | `pytest` is in no requirements file | The test suite can't run until it's installed by hand | Open |
| B5 | Multi-VM challenges (`dc1`, `kioptrixlevel1`) were seeded with one flag row **per VM**, not per challenge | A correct flag raises `MultipleResultsFound` in `scoring.submit_flag()` and the student gets a 500 error. Verified against a fresh seed of `dc1.yml` | Fixed in the seeder (`97ac461`): a fresh seed of `dc1.yml` now gives one flag row. Rows already in a live database must still be deleted by hand, below |
| B6 | `vars/challenges/kioptrixlevel1.yml` gives **both** VMs `10.10.10.10`, and has a `target → target` rule | IP conflict inside the session; the attacker can't reliably reach the target | Open |

To remove the duplicate flag rows (B5) from an existing database, back it up
first (see "Backups" in the [admin guide](user-guides/pond-sec-admin-guide.md)),
then keep the lowest `flag_id` for each challenge:

```sql
DELETE FROM challenge_flags
WHERE flag_id NOT IN (SELECT MIN(flag_id) FROM challenge_flags GROUP BY challenge_id, flag_hash);
```

Check that no `user_solves` row points at a flag you're about to delete:
`SELECT * FROM user_solves WHERE flag_id NOT IN (SELECT MIN(flag_id) FROM challenge_flags GROUP BY challenge_id, flag_hash);`.
Nobody can have scored on a duplicate flag (scoring crashes before it awards
anything), so this should return nothing.

---

## 2. Isolation and networking

| # | Problem | Status |
|---|---|---|
| N1 | Only multi-VM sessions were isolated; single-VM sessions stayed on their template's shared bridge with no VM firewall | Fixed (`4476c02`). Every VM launch now gets its own session VNet, except a lone Red Duck, which stays on `pondnc` behind inbound DROP and an outbound allowlist (N11). See the [network reference](networking/pond-proxmox-vmid-network-reference.md) §5 |
| N2 | The app didn't check that the Proxmox **datacenter firewall** is on. If it's off, every per-VM rule is silently ignored | Fixed (`4476c02`). Every launch now calls `provisioner.check_isolation_preconditions()` and refuses unless `pondz` is a Simple zone and the datacenter firewall is enabled |
| N3 | Only `net0` was moved to the session VNet and firewalled | Fixed (`4476c02`). `provisioner._firewall_nics()` rewrites every `netN`, reads the config back, and refuses the launch if any NIC didn't take |
| N4 | A target can't open connections to its attacker (inbound DROP on the attacker, with no rule back), so reverse shells and callbacks fail | Open: decide whether to add `target → attacker` rules or require bind shells |
| N5 | `import_challenge.py` creates templates with `net0` on `vmbr0` with no VLAN tag, which is the **management** network (line 289). Every imported template must be moved by hand | Open |
| N6 | The server BMC (`10.1.20.201`) and camera NVR (`10.1.20.253`) are on the student VLAN, and the gateway routes VLAN 10 ↔ 20 (security assessment H2) | Infra: not re-checked since 29 Sep. Students' clones no longer sit on VLAN 20 (N1), so this now matters only for other devices on that VLAN |
| N7 | The real privilege boundary is the root SSH key from the controller to `pve` (for `virt-customize`), not the scoped API token | Accepted residual risk; restrict the key with `from=` and a forced `command=` (see the [network reference](networking/pond-proxmox-vmid-network-reference.md) §7.3) |
| N8 | **A single-VM target challenge has no network at all.** Its clone is alone on a session VNet with no gateway (`themes.launch()`), so the only way in is the VM's own console. Metasploitable 2 used to sit on VLAN 20, where students could attack it from elsewhere; now it can't be attacked over the network | Open. **DECISION:** turn each single-VM target into a two-VM challenge with a `Kali-attacker` (like `dc1`), or accept console-only. See the [authoring guide](challenges/authoring-guide.md) |
| N9 | In a multi-VM challenge the Red Duck attacker is on the session VNet, which has no route to the Docker host, so its first-boot handout download fails (the VM still boots and works) | Accepted: neither `dc1` nor `kioptrixlevel1` has handouts. Don't put handouts on a multi-VM challenge |
| N10 | Whether a launch is a "lone Red Duck" (stays on `pondnc`) is decided by **template name**: exactly one VM, named `POND_HANDOUT_TEMPLATE` (`Kali-attacker`), or `execution_type: container_lab`. A workstation template under any other name gets a session VNet and can't reach the Docker host | Accepted: keep the workstation template's name equal to `POND_HANDOUT_TEMPLATE` |
| N11 | A lone Red Duck still shares `pondnc` with every other Red Duck. Its firewall drops all inbound, and outbound allows only TCP to the Docker host (`POND_DOCKER_HOST_IP`), then, if `POND_WORKSTATION_INTERNET_PORTS` is set, DNS and those TCP ports to the internet; every private range is dropped (`themes._workstation_egress()`). `pondnc` has no routed uplink, so in practice there is no internet yet. Routing it out through a VLAN-backed zone is prepared (`PROXMOX_WORKSTATION_ZONE`, the playbook's `pond_workstation_vnet`) but not built | Accepted. Internet access for Red Duck is a site decision; see the [network reference](networking/pond-proxmox-vmid-network-reference.md) §5 |

The full network specification, with the build steps and acceptance tests, is
in the [network reference](networking/pond-proxmox-vmid-network-reference.md).

---

## 3. Launch, sessions and VMs

| # | Problem | Status |
|---|---|---|
| L1 | Launch errors were thrown away: `themes.launch()` showed a generic message and logged nothing | Fixed (`4476c02`, `3dc6afc`). The app log now gets `ERROR in themes: launch failed for instance <id>: <reason>`, or `session network for instance <id>: <reason>` if the session VNet couldn't be made. The student still sees only the generic message |
| L2 | **A failed launch can leave a hidden clone.** If a step fails after the clone exists (SSH, `virt-customize`, the NIC rewrite or the VM firewall), `provisioner.clone_and_start()` marks the record deleted but doesn't destroy the VM, and `launch()` only tears down clones that finished starting. The VM stays on Proxmox, invisible to the Sessions page, holding a VMID | Open; clean-up steps are in the [launch guide](user-guides/launch-guide.md) |
| L3 | **Nothing reaps idle sessions.** A session left open keeps its VMs until someone closes it. `default_ttl_hours` is in every challenge file but no code reads it | Open |
| L4 | Rows under "Machines without a session" never clear, even after the VM is destroyed | Open |
| L5 | After a server reboot, sessions that were open show as **Running now** with no VM behind them, and that student can't launch anything until the session is closed | Open (operational workaround in the launch guide) |
| L6 | The one-session-per-user rule is check-then-insert, so two simultaneous launches can both succeed (assessment L2) | Open |
| L7 | `vmid_range_start` / `vmid_range_end` in the challenge files and `PROXMOX_CLONE_POOL_START` in `config.py` are never read. Every clone comes from `DEFAULT_VMID_RANGE = (301, 399)` in `provisioner.py`, minus the protected 300–303 | Open: delete them, or wire them up |
| L8 | Capacity is 96 clone VMIDs (304–399) across all sessions | Accepted: widen `DEFAULT_VMID_RANGE` if needed |
| L9 | Protected VMIDs are defined in three places (`provisioner.PROTECTED_VMIDS`, `PROXMOX_PROTECTED_VMIDS`, the playbook's `pond_protected_vmids`); allocation only honours the hard-coded one | Open |
| L10 | A VM created by hand inside 304–399 is invisible to the scoped token, so its VMID keeps being picked and every launch fails while it's the lowest free number | Accepted: never create VMs in that range by hand |
| L11 | Templates are matched by **name** at seed time (`provisioner._resolve_template_vmid()`); two VMs with the same name means the first match wins | Open |
| L13 | `provisioner._find_disk_volid()` takes the first disk-like key in a VM's config (`ide`, `sata`, `scsi`, `virtio`). A CD drive (`ide2`) matches too, and is picked instead of the real disk whenever it comes first in the config Proxmox returns: an attached ISO gives `virt-customize` the wrong path, and an empty drive (`none,media=cdrom`, the web UI default) has no `:` and fails to parse. Either way, every launch needing a static IP or handouts fails | Open: delete CD drives from templates; skip `media=cdrom` in the code |
| L14 | In a multi-VM launch, the network rules (`apply_network_rule()`) are written after every VM has started, outside the launch's error handling. If Proxmox refuses one, the student gets a server error, the session stays `provisioning` with its VMs running, and that student can't launch again until the session is closed from the Sessions page | Open: move the rule loop inside the `try`, or before the VMs start |
| L15 | `PROXMOX_LAB_GATEWAY` is still a setting, but nothing reads it any more: since every target goes on a gateway-less session VNet, launch no longer passes a gateway | Open: remove it |
| L16 | When every clone VMID is taken, `provisioner.claim_vmid()` raises `RuntimeError`, not `ProxmoxError`, so `themes.launch()` doesn't catch it. The student gets a server error, the session stays `provisioning`, and in a multi-VM launch the VMs already cloned keep running. The same applies to any other non-Proxmox error during a launch | Open: catch it in `launch()`, or raise `ProxmoxError` |
| L12 | The production command in `pond-sec/wsgi.py` (`gunicorn … --workers 4`) has no `--threads`. Each open console WebSocket holds a whole worker, so four workers means at most four consoles at once ([flask-sock: Gunicorn](https://flask-sock.readthedocs.io/en/stable/web_servers.html)) | Open: add `--threads` |

---

## 4. Challenges and content

| # | Problem | Status |
|---|---|---|
| C1 | **The seeder only inserts.** For a challenge already in the database, re-seeding ignores changed text, points and IPs, but adds unseen roles, and **adds a changed flag as a second flag** while keeping the old one, so the challenge then needs both. There's no supported update path | Open |
| C2 | **The web submission pipeline can't produce a launchable VM or Red Duck challenge.** VM submissions fail validation with `CHECK_UNAVAILABLE` until three site integrations are written (`INGESTION_VERIFY_TEMPLATE`, `INGESTION_INSPECT_IMAGE`, `INGESTION_PUBLICATION_ADAPTER`). `container_lab` submissions publish, but can't launch without a `CONTAINER_LAB_ADAPTER`. None of the four is implemented | Open: see [submission pipeline](challenges/submission-pipeline.md) |
| C3 | `vars/challenges/lockedshields.yml` is a test challenge (flag `pond{test}`) pointing at a template named "VM 10000" | Open: remove before handover |
| C4 | `import_challenge.py` needs a Proxmox identity that can create VMs and import disks. The app's `pond@pve!launcher` token can't, `@pam` tokens are refused, and nothing documents which identity to use | Open: create and document a separate scoped admin token |
| C5 | The Docker host's configuration (the BSides Canberra 2025 deploy script, the handout file server, DHCP, the isolation firewall rule) is not in the repo | Open: see [`deployment/docker-host/`](../deployment/docker-host/README.md) |
| C6 | Challenge flags are plaintext in `vars/challenges/*.yml`, in a public repository | Open (assessment M1) |
| C7 | Flags are static per challenge. Per-session flag generation (`generate_flag()` in `db/store.py`) exists but isn't used | Accepted for now |
| C8 | **The console opens on the last VM cloned** (`themes._owned_instance()` takes the newest `vm_instances` row). VMs are cloned in `boot_order`, but the seeder gives every VM `boot_order` 1 and has no YAML key for it, so the order in the file decides. `dc1.yml` lists the attacker *first*, so a fresh seed opens the console on the DC-1 target. The live database was fixed by hand in September | Open: add a `boot_order` key to the seeder, or list the attacker last |

---

## 5. Web app

| # | Problem | Status |
|---|---|---|
| W1 | **Year of study** is required at registration but never stored (`identity.create_user()` drops it; there's no column). Pages that show it print the literal word "None": the student dashboard, the admin account list and the account page | Open |
| W2 | The flag box placeholder says `flag{...}`, which no challenge uses (`theme_detail.html` line 58) | Open |
| W3 | The challenge timer only updates every 30 seconds after the first sync: `session.js` compares the status to `"in_progress"`, but the server returns `"running"` | Open |
| W4 | `set_role()` doesn't call `_may_act_on()`. The UI hides the role form unless you outrank the account, but a request sent straight to the route is accepted, so one admin could demote another | Open |
| W5 | `roles.py` lines 33–34 say an admin can't demote themselves; `admin.set_role()`'s docstring says they can; the UI makes it impossible | Open: decide, then make all three agree |
| W6 | Audit entries for staff actions are stored against the **student's** account (`audit.record()` sets actor and target to the same user). The staff member's name is only in the free-text detail | Open |
| W7 | On the theme page the scoreboard was headed "Category scoreboard", but the navigation says "Themes" | Fixed (`aca7e40`): it's now "Theme scoreboard", and theme summaries say "in this theme". The admin submission page says "Theme:" |
| W8 | `WSGI_Files/.env.example` still lists `PROXMOX_BACKEND`, `DATABASE_PATH` and `RESET_TOKEN_HOURS`, none of which does anything | Open |

---

## 6. Security assessment findings (29 Sep 2026): current status

The [assessment](../pond-sec/docs/SECURITY_ASSESSMENT.md) is a dated record and
hasn't been edited. This is where each finding stands with the branch merged.

| Finding | Status |
|---|---|
| H1: clones not isolated | Fixed in code (`4476c02`): every VM launch is on its own session VNet, or, for a lone Red Duck, firewalled on `pondnc` (N1, N11), and the app refuses to launch with the datacenter firewall off (N2). The live acceptance tests (network reference §9) haven't been re-run since |
| H2: BMC and NVR on the student VLAN | Infra (N6) |
| H3: root token and unverified channels | Fixed in code: own scoped token, `@pam` refused, TLS verified (including the console relay since `dd032e6`), SSH host keys required. The root SSH key remains (N7) |
| M1: secrets and answers in git | Open: `the_pond.db.bak` (account password hashes and flag hashes) and `.provisioner.py.swp` are still tracked (`cyber-range.zip` is removed by the merge, but stays in history), and flags are plaintext (C6). The repository is public |
| M2: cross-challenge flag mapping | Fixed in code: flags now belong to a challenge (`challenge_flags.challenge_id`) and grading filters on it |
| M3: lockout and throttle counters not atomic; a successful login clears the per-IP throttle | Open |
| M4: idle timeout kept alive by the timer poll | Open (and see W3) |
| M5: no server-side session revocation | Open. Locking an account still takes effect on the next request |
| M6: console ticket in the WebSocket query string; `port` not validated | Open |
| L1: `change_password` throttle never applied | Open (not to be confused with known issue L1 above) |
| L2: one-session check is racy | Open (L6). Missing Proxmox settings now raise `ProxmoxError` and are handled |
| L3: temporary password stored in a flash message (signed, unencrypted cookie) | Open |
| L4: raw Proxmox errors shown to students | Partly fixed: launch and session-network failures show a generic message and log the detail; console errors (`themes.console()`) still show the exception text |

---

## 7. Repository hygiene

These files are in the repository but aren't part of the running system.
Remove them, or move them out, before handing over.

| File | What it is | Action |
|---|---|---|
| `the_pond.db.bak` | Database backup with account password hashes | Remove, add `*.bak` to `.gitignore`, purge from history; treat those passwords as exposed |
| `odn` | A coloured `git diff` output, added by mistake in `3dc6afc` (the earlier `n` and `cyber-range.zip` are removed by the merge) | Remove |
| `.provisioner.py.swp` | Editor swap file | Remove |
| `db/__pycache__/*.pyc` | Python bytecode, tracked before `.gitignore` covered it | `git rm --cached` |
| `debugger-app.py`, `templates/`, `static/novnc` (submodule) | The original single-file debugger app, kept for isolated testing | Keep or remove; not used by Pond Sec |
| `db/store.py`, `db/schema.sql`, `db/init_user_db.py`, `proxmoxerTest.py` | The pre-SQLAlchemy MVP data layer and its smoke test | Legacy; used only by `debugger-app.py` and `tests/test_store.py` |
| `db/test_database.py`, `db.test_database.py` | Two copies of an old standalone integrity test | Legacy |
| `prov_net_test.py` | Manual test of `inject_instance_network()` against a real VM | Manual tool; keep if useful |
| `playbooks/create_instance.yml`, `playbooks/destroy_instance.yml`, `inventory/`, `requirements.yml` | The Ansible layer the provisioner replaced | Legacy; only `playbooks/pond_least_privilege.yml` is live |
| `pond-sec/tests/test_flow.py`, `pond-sec/tools/build_preview.py`, `pond-sec/preview/` | Tests and a preview builder for the deleted raw-SQLite app; both import the deleted `app.db` | Broken; rewrite or remove |
| `CONTAINER_LAB_SCHEMA.sql`, `UPLOAD_SCHEMA.sql` | Reference DDL generated from the models | Reference only; the models are canonical |

### Also before handover

The full list of handover tasks is the
[handover checklist](handover/handover-checklist.md).


- **Changes on the live controller that aren't in git.** Work done on the
  controller on 20 Oct (a `POND_DIRECT_ENTRY` setting, the Docker host moving to
  `10.1.21.206`, and a fix for templates with a CD drive) is on no branch.
  Nothing in these documents describes it. On the controller, run `git status`
  and `git diff` in the checkout, and commit what should be kept.
- **The branch's own `docs/user-guides/launch-guide.md`** is an older draft. When
  the branch is merged together with these documents, keep this suite's version.
- **Unused imports** left by the merge (`datetime` in `admin.py`, `identity.py`
  and `themes.py`; `identity` in `admin.py`; `UserCredential` in `identity.py`),
  and a mis-indented comment above `POND_DOCKER_HOST_IP` in `config.py`.
  Harmless; `python3 -m pyflakes` lists them.
