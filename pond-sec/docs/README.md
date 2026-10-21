# Pond Sec

The web application of The Pond: registration and approval, the challenge
catalogue, VM sessions with a browser console, flag scoring, leaderboards, and
the staff console. It drives Proxmox through `provisioner.py` at the repo root,
and keeps everything in one SQLite database through the models in `db/`.

This file used to be the main runbook. That content has moved into the
documentation set under [`docs/`](../../docs/), and the
[root README](../../README.md) is the index. This file stays here because the
app's own warnings point at it by path.

| You want to… | Read |
|---|---|
| Understand the system | [System overview](../../docs/architecture/system-overview.md) |
| Start it | [Launch guide](../../docs/user-guides/launch-guide.md) |
| Build it from scratch | [Build from scratch](../../docs/setup/build-from-scratch.md) |
| Run it as staff | [Admin guide](../../docs/user-guides/pond-sec-admin-guide.md) |
| Change the code | [Code map](../../docs/architecture/pond-sec-code-map.md) |
| Know what's broken | [Known issues](../../docs/known-issues.md) |

---

## Least-privilege Proxmox token

If you got here from a start-up warning such as `PROXMOX_TOKEN_ID is not set`,
the app has no Proxmox credentials, and every launch will fail until it does.

The app uses its own Proxmox user, `pond@pve`, through a privilege-separated
token, `pond@pve!launcher`, created by `playbooks/pond_least_privilege.yml`. It
refuses `@pam` tokens.

- **Creating the token:** phase 7 of
  [build from scratch](../../docs/setup/build-from-scratch.md).
- **Giving the app the secret:** step 4 of the
  [launch guide](../../docs/user-guides/launch-guide.md) (`PROXMOX_TOKEN_ID`,
  `PROXMOX_TOKEN_SECRET`, `PROXMOX_CA_BUNDLE`).
- **What the token may and may not hold, and why:** §2.3 of the
  [network reference](../../docs/networking/pond-proxmox-vmid-network-reference.md),
  and "The app refuses root and over-privileged Proxmox tokens" in the
  [design decisions](../../docs/architecture/pond-sec-design-decisions.md).
- **Rotating it:** "The Proxmox token" in the
  [admin guide](../../docs/user-guides/pond-sec-admin-guide.md).

Before every launch the app reads the token's effective permissions and refuses
to launch if they go beyond what it needs (`app/proxmox.py`,
`_check_token_privileges()`). If launches fail with a privilege error after
someone has changed permissions on `pve`, re-run the playbook rather than adding
grants by hand.

### Token runbook

`pond-sec/tests/test_credentials.py` checks that this runbook is here, so keep
it in this file.

1. **Run the playbook** on `pve`, as root, from a directory with no
   `ansible.cfg`. The repo's `ansible.cfg` names a vault password file that
   doesn't exist on `pve` and would abort the run. Copy just the playbook across,
   then dry-run it, then run it for real:

   ```bash
   cd /root && ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml -e '{"pond_template_vmids":[10000,10001,10002]}' --check
   cd /root && ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml -e '{"pond_template_vmids":[10000,10001,10002]}'
   ```

   List every template VMID. If an old `/root/pond-launcher.token` exists, the
   playbook refuses to create a token until you `shred -u` it.

2. **Check what it granted**, on `pve`: `pveum user token permissions pond@pve launcher`
   and `pveum acl list | grep -i pond`.

3. **Probe the token from the controller.** Each of the first four must be
   refused. The secret is read without echo and reaches `curl` through a process
   substitution, so it never lands in shell history or `ps`:

   ```bash
   read -rs PVE_SECRET; C="--cacert $HOME/pve-root-ca.pem"
   H() { printf 'Authorization: PVEAPIToken=pond@pve!launcher=%s' "$PVE_SECRET"; }
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/qemu/300/config           # expect 403
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/qemu/300/status/current   # expect 403
   curl -s $C -H @<(H) https://10.1.21.151:8006/api2/json/access/users      # expect 403, or only pond@pve; a full user list means the token is too wide
   curl -s -o /dev/null -w '%{http_code}\n' $C -H @<(H) https://10.1.21.151:8006/api2/json/nodes/pve/execute    # expect 403 or 501
   curl -s $C -H @<(H) "https://10.1.21.151:8006/api2/json/cluster/resources?type=vm"   # only VMs in the pond pools
   unset PVE_SECRET
   ```

   Every probe is a read. Don't add write requests to this list.
   Sources: [Proxmox API tokens](https://pve.proxmox.com/wiki/Proxmox_VE_API#API_Tokens),
   [bash: Process Substitution](https://www.gnu.org/software/bash/manual/bash.html#Process-Substitution),
   [curl `-H @file`](https://curl.se/docs/manpage.html#-H).

4. **Give the app the secret** (launch guide, step 4), restart it, and launch and
   close one challenge.

5. **If an old `root@pam` token was ever used by the app,** check nothing still
   uses it before removing it. On `pve`:
   `zgrep -h 'root@pam!root' /var/log/pveproxy/access.log* | tail` (`zgrep`
   includes the rotated, compressed logs) and
   `grep -c 'root@pam!root' /var/log/pve/tasks/index`. Confirm with the owners of
   VMs 300–303 that their tooling doesn't use it, then
   `pveum user token remove root@pam root`. Treat its secret as exposed.

6. **Rotating:** `pveum user token remove pond@pve launcher`, `shred -u` the old
   token file, re-run step 1, update the app's secret, restart.

If launches fail with a 403 in the app log: a task-status read means set
`pond_grant_node_audit: true` in the playbook; a clone error naming a bridge
means a template NIC is on a bridge the token can't use (move it to `pondtpl`);
a clone error naming `/` and `Sys.Console` means the template still has a host
CD-ROM.

## Security assessment

[`SECURITY_ASSESSMENT.md`](SECURITY_ASSESSMENT.md) is the assessment of
29 Sep 2026, kept as it was written. The current status of each finding is in
[known issues](../../docs/known-issues.md) §6.
