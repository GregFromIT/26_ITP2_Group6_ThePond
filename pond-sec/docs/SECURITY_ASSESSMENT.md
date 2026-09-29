# The Pond — Security Assessment (2026-09-29)

Authorised, in-scope assessment of The Pond CTF platform, for the Deliverable #11
acceptance-test evidence table (QA owner: Gareth).

## Scope and method

- **In scope:** 10.1.21.0/24 (management VLAN), 10.1.20.0/24 (lab/participant VLAN),
  `pond-sec/` Flask app, `db/` ORM layer, and `provisioner.py` (called by the app).
- **Assessor host:** 10.1.21.248 (VLAN 10).
- **Methods used:**
  - A static review of the application and provisioning code.
  - A non-intrusive TCP-connect port sweep of both in-scope /24s, covering ports
    22, 80, 443, 3128, 5000, 5001, 8000, 8006 and 5900–5902.
  - HTTP banner collection from the web hosts that sweep found.
- **Not performed:**
  - Live exploitation of the app.
  - Any Proxmox API or SSH authentication.
  - Any change to VMs, including VMIDs 300–303.
  - The Pond was not deployed on any in-scope host at test time: nothing was
    listening on 5000, 5001 or 8000.
- **Severity:** assessor's High/Medium/Low. Re-rate against the contract's L×I
  risk-register scale before submission.
- **NFR column:** refers to the items under client contract §4.4 as numbered in the
  test brief (1–8). Replace with exact clause IDs.

**Status legend:**
- **Confirmed**: established from code or network evidence.
- **Code-only**: follows from the code but has not been reproduced live.
- **Not verified**: needs a live test (see the last section).

## Summary

| # | Finding | NFR | Sev | Status |
|---|---|---|---|---|
| H1 | Challenge clones not isolated (no VNet, no firewall, template's shared lab bridge) | 2 | High → Low (residual) | **Remediated in code** — live verification pending |
| H2 | Server BMC and camera NVR on the participant VLAN; gateway routes between VLAN 10 and VLAN 20 | 2, 6 | High | Confirmed |
| H3 | Proxmox access uses the `root@pam` token and root SSH, without host-key or TLS verification | 5 | High | Code-fixed; live steps pending |
| M1 | Secrets and answers committed to git (DB backup with hashes, plaintext flags, swap file, zip) | 5 | Medium | Confirmed |
| M2 | Cross-challenge flag mapping error (kioptrix flag scores on dc1) | 4 | Medium | Confirmed |
| M3 | Lockout/throttle counters are non-atomic; a successful login clears the per-IP throttle | 3 | Medium | Code-only |
| M4 | Idle timeout defeated by the unconditional 30 s timer poll | 3 | Medium | Code-only |
| M5 | No server-side session revocation (logout, password change and reset don't invalidate cookies) | 3, 8 | Medium | Code-only |
| M6 | Console ticket passed in the WebSocket query string; `port` not validated | 7 | Medium | Code-only |
| L1 | `change_password` throttle defined but never applied | 3 | Low | Code-only |
| L2 | The one-running-instance-per-user check is racy; non-Proxmox errors leave ghost `running` instances | 2 | Low | Code-only |
| L3 | Temporary password stored in a flash message (signed, unencrypted session cookie) | 5 | Low | Code-only |
| L4 | Raw Proxmox exception text shown to students | 5 | Low | Code-only |

---

## High

### H1 — Challenge clones are not network-isolated

- **NFR:** 2 (Challenge isolation)
- **Severity:** High before the fix; Low residual after (see re-evaluation)
- **Status:** Remediated in code; live verification pending

**Codebase note.** Commit `9b29c31` (28 Sep) replaced the provisioner-backed app
with a self-contained `app/proxmox.py`. The original finding was written
against the old code, where only single-VM challenges were exposed. In
`9b29c31` it got broader: `_api_clone()` cloned the template and started it
straight away, with no VNet, no firewall and no read-back, so **every** clone
booted on the template's shared lab bridge (VLAN 20).

- **What was tested:** whether each launched instance boots on its own
  router-less network with deny-by-default inbound filtering, and whether any
  failure can leave a VM running on the shared lab bridge.
- **How:** code review of `app/proxmox.py` and `app/themes.py:launch`, then a
  new test suite, `tests/test_isolation.py`. It drives the real adapter
  (`PROXMOX_BACKEND=api`) against an in-memory fake Proxmox API that records
  every call.

**Fix** (`app/proxmox.py`, `app/config.py`):

1. **Preconditions, fail-closed.** Before anything is cloned, the launch is
   refused unless:
   - the SDN zone `PROXMOX_SDN_ZONE` (default `pondz`) exists and is type
     `simple`;
   - the datacenter firewall is enabled. Without it, VM firewall rules are
     silently ignored.
2. **Clone, then wait.** The clone task is awaited. Previously the VM was
   started without waiting.
3. **Per-instance VNet.** A new VNet `p<hex vmid>` is created in the zone,
   with no subnet and so no gateway, and SDN is applied.
4. **All NICs moved.** Every `netN` (not just `net0`) is moved onto the
   VNet:
   - VLAN `tag=` and `trunks=` are stripped;
   - `firewall=1` is set;
   - MAC addresses are preserved.
5. **Firewall on.** The VM firewall is enabled with `policy_in=DROP`.
6. **Read-back before boot.** The VM config and firewall state are re-read.
   The VM is started only if every NIC is confirmed on the VNet with the
   firewall on.
7. **Fail-closed cleanup.** Any failure destroys the clone and VNet this call
   created, and the launch is refused. Cleanup never touches a VM or VNet it
   did not create, so losing the `nextid` race can't delete another
   student's VM.
8. **Teardown.** Stop, wait, delete, wait, then remove the VNet. Clones from
   before this change, which have no VNet, still tear down cleanly.

**Evidence:**

| Check | Result |
|---|---|
| `python -m tests.test_isolation` (fixed code) | **49/49 pass** |
| Same test against the pre-fix `proxmox.py` from `9b29c31` | **Fails.** No VNet, NICs left on `vmbr0,tag=20`, no firewall, then a crash on the missing firewall config. This proves the test detects the vulnerability. |
| `python -m tests.test_flow` (regression) | **158/158 pass**, before and after |

The isolation test covers:
- VNet per clone, in the configured zone, with no subnet;
- every NIC moved, with no VLAN tag and the firewall on;
- inbound DROP;
- start only after isolation, and NICs moved only after the clone task
  finishes;
- the template is never modified;
- two students get disjoint networks;
- teardown ordering, and that other students' resources are untouched;
- all preconditions fail closed;
- a failure at each step (VNet create, SDN apply, NIC move, firewall enable,
  start) leaves nothing behind;
- a NIC write that silently doesn't take effect is caught on read-back and
  never boots;
- losing the vmid race doesn't delete the other student's VM;
- VNet name length limits.

**Re-evaluation: is the risk gone?**

For VMs launched through the Pond web app, the **code-level risk is closed.**
A clone can no longer boot on the shared lab bridge. Whatever breaks, it
either boots isolated or it doesn't boot. **Residual risk: Low**, for these
reasons:

- **Not yet verified on the live cluster.** The fix enforces its
  preconditions, but the environment still has to meet them. Acceptance
  steps:
  1. Confirm `pondz` exists as a Simple zone: `pvesh get /cluster/sdn/zones`.
  2. Confirm the datacenter firewall is enabled:
     `pvesh get /cluster/firewall/options`.
  3. Launch the same challenge from two demo accounts. On the host, check
     both clones' `netN` lines show different `bridge=p…` values with
     `firewall=1` and no `tag=`.
  4. From inside one clone, confirm it cannot reach:
     - the other clone;
     - 10.1.20.1 and 10.1.20.201;
     - 10.1.21.0/24, including 10.1.21.151.
  5. Close both sessions and confirm the `p…` VNets are gone.
- **The token needs more rights.** It now needs `SDN.Allocate` and `SDN.Use`
  on the zone, plus `Sys.Audit` to read the datacenter firewall. Fold these
  into the least-privilege role in H3; don't solve it by keeping root.
- **Other launch paths still use the flat network:** root-level
  `provisioner.py` (isolates multi-VM sessions only), `debugger-app.py`,
  `proxmoxerTest.py` and `playbooks/create_instance.yml`. The web app doesn't
  use them. Retire them, or route them through `app/proxmox.py`.
- **Outbound is not filtered** (`policy_out` is left at ACCEPT). This is
  acceptable because nothing else is on the VNet and it has no gateway. It
  will need a rule set if multi-VM challenges return to this codebase: they
  should share one session VNet, with per-role inbound rules.
- **Console.** In `9b29c31`, the console redirects the student to the
  Proxmox UI on 10.1.21.151:8006. That means participants need a network path
  to the hypervisor's management interface. That is tracked under M6/H2, not
  here, but it undercuts NFR 2 until a ticketing proxy is back.

### H2 — Out-of-band management on the participant VLAN; inter-VLAN routing open

- **NFR:** 2 (Challenge isolation), 6 (Network exposure)
- **Severity:** High
- **What was tested:** what is reachable across the two in-scope VLANs.
- **How:** a TCP-connect sweep and HTTP banner grab from 10.1.21.248.
- **Evidence** (sweep and banner output from 2026-09-29 12:39–12:40):
  - `10.1.20.201`: **Lenovo XClarity Controller** (server BMC) on 80/443/22.
  - `10.1.20.253`: **UniFi Protect** on 80/443.
  - `10.1.20.1` and `10.1.21.1`: UniFi OS gateway. From VLAN 10, VLAN 20 hosts
    were reachable on 22/80/443, so inter-VLAN routing is permitted in at least
    one direction.
  - `10.1.21.151`: Proxmox API on 8006, SSH on 22 and spiceproxy on 3128, all on
    VLAN 10.
- **Impact:** any VM on the flat lab segment (see H1) is L2/L3-adjacent to the
  physical server's BMC. The BMC offers power, console and firmware control below
  the hypervisor. If the gateway allows VLAN 20 → VLAN 10, the Proxmox API and the
  Pond app become reachable from participant VMs too.
- **Not verified:** reachability *from* a participant VM toward VLAN 10 and
  10.1.20.201. That needs a launched test instance.
- **Suggested fix:**
  - Move the BMC and the NVR to a dedicated management/OOB VLAN.
  - On the UniFi gateway, deny VLAN 20 → any RFC1918 destination, keeping only
    what the challenges genuinely need.
  - Restrict VLAN 10 → VLAN 20 to the Proxmox host and admin workstations.
  - Record the intended exposure for NFR 6.

### H3 — Over-privileged Proxmox credentials and unverified channels

- **NFR:** 5 (Credential/secret handling)
- **Severity:** High
- **What was tested:** the credentials the app uses to drive Proxmox, and how the
  channel to the hypervisor is protected.
- **How:** code review.
- **Evidence:**
  - `pond-sec/app/config.py:79` and `group_vars/all.yml`: the token ID is
    `root@pam!root`.
  - `provisioner.py:37,323`: network injection SSHes to the Proxmox host as
    `root`.
  - `provisioner.py:323`: `paramiko.AutoAddPolicy()`, so no host-key pinning.
  - `provisioner.py:86,94`: `verify_ssl=False` is hard-coded.
  - `themes.py:463`: the console relay disables certificate checks unless
    `PROXMOX_VERIFY_SSL` is set.
  - `themes.py:453-460`: the relay attaches the root token to every console
    WebSocket.
- **What the app actually needs:**
  - clone, configure, power and destroy VMs within the clone VMID range and pool;
  - `VM.Console` on those VMs (not needed by the current code: `themes.console()`
    only redirects to the console URL, so the token does not hold it);
  - `SDN.Allocate` and `SDN.Use` on the `pondz` zone;
  - `Datastore.AllocateSpace` on the clone storage;
  - `VM.Audit` on the templates.
  - Anything beyond that is excess: other VMs including 300–303, storage, users,
    ACLs, node shell, and cluster config.
- **Not measured:** the token's effective permissions and whether privilege
  separation is on. The test rules exclude real credentials. Run this on the host:
  `pveum user token list root@pam` (read the privsep column) and
  `pveum user token permissions root@pam root`.
- **Impact:** anyone who obtains the app's token or SSH key, or can intercept the
  unverified TLS or SSH traffic on VLAN 10, gets full control of the hypervisor.
- **Suggested fix:**
  - Create a dedicated `pond@pve` user with a privsep token and a custom role
    limited to the list above.
  - Scope it with ACLs on a resource pool for clones and templates, plus the SDN
    zone.
  - Replace root SSH with a restricted user and forced-command wrapper for
    `virt-customize`, or move the network injection to cloud-init.
  - Pin the host key, install the cluster CA, and default
    `PROXMOX_VERIFY_SSL=1`.

#### Remediation (code, 2026-09-29)

- `app/config.py`: no default token ID; `PROXMOX_VERIFY_SSL` defaults to on;
  new `PROXMOX_CA_BUNDLE`, `PROXMOX_POOL`, `PROXMOX_TEMPLATE_POOL`,
  `PROXMOX_PROTECTED_VMIDS` and `PROXMOX_PRIVILEGE_CHECK_TTL`.
- `app/proxmox.py`: `@pam` tokens, malformed IDs, a missing secret or pool, a
  missing CA file and (in production) TLS off are refused before any connection.
  Every clone is created in `PROXMOX_POOL`; VMs 300-303 are never cloned,
  started, deleted or accepted as a new vmid. Before each launch the adapter reads
  `GET /access/permissions` and refuses if the token holds anything outside an
  allowlist (cached only on a pass; teardown skips it so VMs are never orphaned).
- `app/__init__.py`: production refuses to start with settings the adapter would
  refuse.
- `playbooks/pond_least_privilege.yml`: creates `pond@pve`, a privilege-separated
  `launcher` token, custom roles and ACLs scoped to two pools and the
  `pondz` zone; refuses protected VMs as templates; verifies the result. Not yet
  run against the cluster.
- `provisioner.py`, `import_challenge.py`: TLS verified, host keys pinned
  (`RejectPolicy`), `@pam` tokens and default root SSH refused in `provisioner.py`,
  protected vmids guarded. `ansible.cfg` now checks host keys; `group_vars/all.yml`
  names `pond@pve`.
- Tests: `python -m tests.test_credentials` (110 checks) plus the shared
  `tests/fake_proxmox.py`. Baseline proof numbers: TBD (evaluation step).

#### Residual risk

After the code and the operator steps, the web-app token can only: create, start,
stop, reconfigure the networking of and delete VMs in `pond-clones`; clone (and so
read the disks of) templates in `pond-templates`, including challenge flags;
create and delete VNets and subnets in `pondz` and apply pending SDN config
cluster-wide (`SDN.Allocate` on `/sdn`, no propagate). A stolen token could add a
subnet with a gateway and SNAT to a session VNet to give a clone a route out
(follow-up: have `_isolate` assert the VNet has no subnets). It can also read
datacenter-level config (`Sys.Audit` on `/`) and consume space on `local-lvm`
(denial of service).

It cannot touch VMs 300-303 or any other VM, users, ACLs, tokens, the node shell,
node networking, power or storage definitions.

Remaining gaps:

- the secret is still in the app host's environment (bounded; mitigate with token
  expiry);
- development can still switch TLS off explicitly;
- the legacy `provisioner.py` and `import_challenge.py` can still SSH as root with
  an explicit `THEPOND_PROXMOX_ALLOW_ROOT_SSH=1` (host keys are now pinned);
- `debugger-app.py` still disables TLS on its WebSocket (out of scope);
- the self-check trusts the hypervisor's report, acceptable given verified TLS;
- the student console path is tracked under M6/H2.

Rating: code risk Low. Overall H3 stays High (open) until the playbook has run,
its verification output is attached, and `root@pam!root` is removed; then Low.

---

## Medium

### M1 — Secrets and answers committed to git

- **NFR:** 5
- **Status:** Confirmed via `git ls-files`.
- **Evidence:** these files are tracked:
  - `the_pond.db.bak`: all 7 accounts' usernames, roles and scrypt hashes, plus
    the flag hashes. `*.db` is ignored, but `.bak` isn't.
  - `vars/challenges/*.yml`: plaintext flags for all 7 challenges.
  - `.provisioner.py.swp`
  - `cyber-range.zip`: an older app snapshot.
- **Checked:** the Proxmox token secret does not appear anywhere in git history.
- **Fix:**
  - `git rm --cached` these files and add `*.bak`, `*.swp` and `*.zip` to
    `.gitignore`.
  - Purge them from history with `git filter-repo`.
  - Rotate every flag, and move flags out of the repo (Ansible Vault or an
    env-injected file).
  - Force a password reset for the seeded accounts.

### M2 — Cross-challenge flag mapping

- **NFR:** 4
- **Status:** Confirmed from the `challenge_flags` table.
- **Evidence:** `flag_id=6` (`kioptrixlevel1-flag`) has `template_id=3`, which
  belongs to the dc1 challenge (`challenge_id=3`). `submit_flag` scopes by
  challenge through the template, so the kioptrix flag is accepted on dc1.
- **Fix:**
  - Delete or re-point flag 6.
  - Have `bulk_import.py`/`import_challenge.py` refuse to attach a flag to a
    template from a different challenge.
  - Add a test for that check.

### M3 — Lockout and throttle counters are not atomic; a successful login resets the per-IP throttle

- **NFR:** 3
- **Status:** Code-only.
- **Evidence:**
  - `security.py:137-152`: `register_failure` reads `failed_login_count`,
    increments it in Python, and writes it back.
  - `throttle.py:308-314`: `hit()` runs `check()` and then `record()` in two
    separate steps.
  - Concurrent requests can therefore each observe the pre-increment value.
  - `auth.py:234`: a successful login calls `throttle.clear("login_ip", source)`,
    so a client that also holds a valid account can reset its own per-IP budget.
- **Mitigating factors:**
  - The lockout is per account row, so changing the username's case doesn't bypass
    it (`identity.py:500` compares lower-cased).
  - `TRUSTED_PROXIES=0` by default, so a spoofed `X-Forwarded-For` is ignored.
- **Live test needed:** send concurrent wrong-password requests against a demo
  account and count accepted attempts before the lock. Confirm the lock lasts
  15 minutes.
- **Fix:**
  - Use an atomic `UPDATE … SET failed_login_count = failed_login_count + 1 …
    RETURNING`, or `BEGIN IMMEDIATE`.
  - Perform the throttle check-and-insert in one transaction.
  - On success, clear only `login_user`.

### M4 — The idle timeout can be kept alive by the page itself

- **NFR:** 3
- **Status:** Code-only.
- **What works:**
  - The idle check is server-side: `__init__.py:session_is_idle`, driven by the
    signed `_seen` timestamp, so client-side tampering doesn't get around it.
- **What doesn't:**
  - `static/js/session.js` calls `setInterval(sync, 30000)` unconditionally.
  - Every poll to `/themes/session/<id>/timer` refreshes `_seen`.
  - A forgotten tab with a live challenge never times out.
  - Its `running` flag compares against `"in_progress"`, but the server returns
    `"running"`.
- **Fix:**
  - Don't refresh `_seen` on the timer endpoint, for example with a
    `passive_endpoints` set checked in `session_is_idle`.
  - Fix the status string comparison.

### M5 — No server-side session revocation

- **NFR:** 3, 8
- **Status:** Code-only.
- **Evidence:**
  - Sessions are Flask client-side signed cookies.
  - `logout` (`auth.py:255`) only clears the browser's copy.
  - A copy captured earlier stays valid, because every request re-signs it.
  - A password change or staff reset doesn't invalidate existing cookies. A locked
    account is the only exception (`auth.py:84`).
  - Session fixation is **not** an issue: login calls `session.clear()` and
    rotates the CSRF token.
- **Fix:**
  - Add a `session_version` column on `UserCredential`, stored in the session and
    compared in `load_logged_in_user`.
  - Bump it on logout, password change, temp-password issue and role change.
  - Add an absolute session lifetime.

### M6 — Console ticket handling

- **NFR:** 7
- **Status:** Code-only. Replay has not been tested.
- **Evidence:**
  - `console.js` puts the Proxmox VNC ticket in the WebSocket URL query
    (`?ticket=…&port=…`), so it's written to web and proxy access logs.
  - `themes.py:449-458`: `port` is interpolated into the upstream URL without
    validation.
  - The ticket is issued server-side, and the relay re-checks instance ownership
    (`_owned_instance`), so another user's ticket can't be used against your
    instance ID.
  - Single-use behaviour depends on Proxmox's `vncproxy` listener.
  - The relay socket doesn't close on logout or idle timeout.
- **Live test needed:**
  - Re-open the WebSocket with a used ticket after the first console closes.
  - Re-open it after `/logout`.
  - Re-open it after the instance is closed.
- **Fix:**
  - Store the ticket and port server-side, keyed to the Flask session and
    instance, with a short TTL, delete-on-use and no query parameters.
  - Validate `port` as an integer in 5900–5999.
  - Close relays when the session ends.

---

## Low

- **L1:** `LIMITS["change_password"]` (`throttle.py:266`) is never used.
  `/change-password` checks the current password with no rate limit. **Fix:** call
  `throttle.hit("change_password", user_id)`.
- **L2:**
  - `themes.py:310-335`: the "one running instance per user" rule is a
    check-then-insert, so concurrent launches can create more than one.
  - Only `ProxmoxError` is caught, so e.g. a missing token secret raises
    `RuntimeError` and leaves a `running` instance with no VM.
  - **Fix:** add a partial unique index on `(user_id) WHERE status='running'` and
    catch `Exception` in the rollback path.
- **L3:**
  - `admin.py:227`: the temporary password is flashed.
  - Flask stores flashes in the signed but unencrypted session cookie, which
    contradicts "never stored".
  - **Fix:** render it directly in the response and set `Cache-Control: no-store`.
- **L4:** `themes.py:377,420` flash raw Proxmox exception text to students.
  **Fix:** show a generic message and log the details server-side.

---

## Checks that passed on inspection (code-only)

| Area | NFR | Result |
|---|---|---|
| Role-based access | 1 | Every `/admin` route has a `@require(...)` guard. Moderators can't act on equal or senior accounts (`_may_act_on`). Only admins can change roles. Instance routes use `_owned_instance()` and return 404 for another user's instance. Only 4 blueprints exist (auth, dashboard, themes, admin); security, scoring, audit, throttle and proxmox are helper modules with no routes. |
| Temp-password flow | 3 | The `force_password_change` hook pins the user to `/change-password`, which requires the current (temporary) password. `set_password` clears `must_change_password`. |
| Flag comparison | 4 | Flags are normalised (strip + lower), hashed with SHA-256, and matched in SQL. There is no byte-wise compare in Python, so timing leakage isn't practically exploitable. The rate limit is keyed per user + challenge, not per session or IP. |
| Solve uniqueness | 4 | `uq_user_flag_solve UNIQUE(user_id, flag_id)` exists in the live DB. A race should produce an IntegrityError (HTTP 500), not a double award. Needs a live test. |
| SQL injection | 8 | All app queries go through SQLAlchemy expressions. No raw SQL string interpolation was found in `pond-sec/app/` or `db/*_models.py`. |
| CSRF | 8 | A per-session token is enforced on every unsafe method with `compare_digest`, with no exemptions. Cookies are HttpOnly and SameSite=Strict. |
| CSP | 8 | `script-src 'self'` with no inline scripts or event handlers in any template, and no `|safe`. `style-src 'unsafe-inline'` is accepted for the progress bars. |
| Bind address | 6 | `0.0.0.0` applies only when `wsgi.py` is run directly. The documented gunicorn command binds `127.0.0.1:8000`. Confirm the deployed command. |

## Outstanding live tests

1. **NFR 3:** concurrent lockout; a 15-minute lock with auto-expiry; 60-minute idle
   expiry; temp-password pinning.
2. **NFR 4:** concurrent flag submissions (the rate-limit race and the solve race).
3. **NFR 7:** ticket replay after close, after logout and after instance teardown.
4. **NFR 2:** from inside a launched single-VM and a multi-VM instance, test
   reachability to another participant's VM, 10.1.21.0/24, 10.1.21.151, and
   10.1.20.201. Also confirm the `pondz` zone type is `simple`.
5. **NFR 5:** effective permissions of `root@pam!root` (commands in H3).
