# Maintenance and restore

Keeping The Pond healthy between classes: backups and how to restore them,
updating Proxmox, the templates, the controller and the Docker host, and the
housekeeping that stops leftovers building up. It's for the system
administrator.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. The database backup and restore commands in §2–§3 were run
and checked on a copy. Nothing in §4–§7 has been done on the live system yet.

> **DECISION:** every frequency in §1 is a suggestion. Nothing was ever agreed.

---

## 1. Routine

| When | Do | Why | Section |
|---|---|---|---|
| Before every class | The launch guide's step 5 checks, including the datacenter firewall | A firewall left off blocks every launch | [Launch guide](../user-guides/launch-guide.md) |
| After every class | Close leftover sessions; clear leftover clones and VNets | Nothing reaps idle sessions (known issue L3), and leftovers hold VMIDs | §6 |
| Weekly while classes run | Back up the database | It's the only record of accounts and scores | §2 |
| Monthly | Proxmox, controller and Docker host updates | Security fixes | §4, §5 |
| Each term | Refresh the Red Duck template; restore a backup to prove it works | Kali goes stale quickly; an untested backup is unproven | §5.1, §3 |
| When someone leaves | Rotate what they held | | [Access inventory](../handover/access-and-credentials.md) |

---

## 2. Backups

### What to back up

| What | Where | Why |
|---|---|---|
| `the_pond.db` | Repo root on the controller, unless `POND_SETTINGS` moves it | Accounts, scores, sessions, challenges, audit log: all platform state |
| The `POND_SETTINGS` file, if you use one, and the environment you start the app with | Outside the repo | Without them the app starts with the wrong settings. Keep secrets out of the copy |
| `pond-sec/instance/secret_key` (only if `FLASK_SECRET_KEY` isn't set) | Controller | Losing it signs everyone out, nothing worse |
| The templates (VMIDs 10000+), the Docker host (9997) and the controller (9999) | `pve` | They can't be rebuilt from the repo alone. §2.2 |

The repo itself is on GitHub, so it doesn't need backing up here, but the
controller's checkout may hold uncommitted changes. `git status` before relying
on GitHub.

### 2.1 The database

From the repo root on the controller. Safe while the app is running:

```bash
python3 -c "import sqlite3; s=sqlite3.connect('the_pond.db'); d=sqlite3.connect('the_pond-$(date +%F).db'); s.backup(d); d.close(); s.close()"
```

Don't use `cp` while the app runs. SQLite can be part-way through a write, and
a plain copy can catch it half-done. `Connection.backup()` uses SQLite's own
backup API, which copies a consistent snapshot
([Python: `sqlite3.Connection.backup`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup);
[SQLite: Online Backup API](https://www.sqlite.org/backup.html)).

Then move the copy off the controller. A backup on the same VM is lost with the
VM.

> **DECISION:** where backups go and who can read them. A backup holds every
> password hash, so it needs the same protection as the database.

### 2.2 Whole VMs

Proxmox's own backup tool, `vzdump`, backs up a VM or template to a storage
that allows backups:

```bash
vzdump 10001 --storage <backup storage> --mode stop --compress zstd
```

- `--mode stop` shuts a running VM down for a consistent copy. Templates are
  never running. For the controller, use `--mode snapshot` to keep it up.
- `<backup storage>` must be a Proxmox storage with content type "VZDump backup
  file". `local-lvm` can't hold backups.

Restore with `qmrestore <file> <vmid>`. Sources:
[Proxmox: Backup and Restore](https://pve.proxmox.com/pve-docs/chapter-vzdump.html),
[vzdump(1)](https://pve.proxmox.com/pve-docs/vzdump.1.html),
[qmrestore(1)](https://pve.proxmox.com/pve-docs/qmrestore.1.html).

> **VERIFY:** whether `pve` has any backup storage. `pvesm status --content backup`
> lists the storages that can take backups ([pvesm(1)](https://pve.proxmox.com/pve-docs/pvesm.1.html)).
> If there's none, backups of the templates and VMs need an NFS share, a second
> disk, or a Proxmox Backup Server.

Never back up, restore over, or schedule anything for VMIDs 300–303. They
belong to another project.

---

## 3. Restoring the database

Use this after a corrupted database, a bad manual SQL change, or a lost
controller. Tested on a copy.

1. **Stop the web app** (Ctrl+C in its terminal). Restoring under a running app
   can leave it holding the old file.
2. **Move the current file aside, never delete it**, with any SQLite side files
   that sit next to it. A leftover `-wal` file belongs to the *old* database;
   SQLite would try to apply it to the restored one. A database and its WAL
   file only make sense together
   ([SQLite: Write-Ahead Logging](https://www.sqlite.org/wal.html)):

   ```bash
   mkdir -p replaced
   mv the_pond.db replaced/the_pond.db.$(date +%F-%H%M)
   for f in the_pond.db-wal the_pond.db-shm the_pond.db-journal; do [ -e "$f" ] && mv "$f" replaced/; done
   cp <backup file> the_pond.db
   ```

3. **Check the restored file:**

   ```bash
   python3 -c "import sqlite3; c=sqlite3.connect('the_pond.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('PRAGMA foreign_key_check').fetchall())"
   python3 -m db.init_database --check
   ```

   The first should print `ok []`
   ([SQLite: `integrity_check`, `foreign_key_check`](https://www.sqlite.org/pragma.html#pragma_integrity_check)).
   The second confirms the tables and columns match the code. If the backup is
   older than a model change, `--check` reports the missing column; apply that
   change's one-off migration (the admin guide, "The database").
4. **Start the app** (launch guide, step 4) and sign in.
5. **Reconcile with Proxmox.** The backup doesn't know what happened on `pve`
   since it was taken:
   - sessions that were open in the backup show under **Running now**, with no
     VM behind them. Close them;
   - clones made after the backup are on `pve` but not in the database. Find them
     with the launch guide's "Clearing a leftover clone" and destroy them;
   - scores and accounts since the backup are gone. Tell the students affected.

**Restoring onto a new controller:** build it as in phase 8 of
[build from scratch](../setup/build-from-scratch.md), then do steps 2–5 above
before running `db.init_database` or `db.seed_accounts`. Those would create a
fresh, empty database.

---

## 4. Proxmox updates

On `pve`, as root:

```bash
apt update
apt full-upgrade
```

Use `full-upgrade` (or `dist-upgrade`), **not** `apt upgrade`. Proxmox updates
often add or remove packages, which `upgrade` won't do, and a half-applied
update can break the host
([Proxmox admin guide: System Software Updates](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#system_software_updates)).

- If apt offers to **remove `proxmox-ve`**, answer no and stop. It's usually a
  `qemu-utils` conflict (build guide, phase 1) or a repository mistake.
- Do it with no sessions open: a reboot after a kernel update stops every clone.
- After any reboot, run the launch guide in full: the controller and Docker host
  don't start on their own, open sessions go stale, and the firewall check
  matters.

**Major versions** (for example 9 to 10) have their own upgrade guide on the
Proxmox wiki, with a checker script. Follow it exactly, and after the upgrade
re-run the network acceptance tests (network reference §9): SDN and firewall
behaviour are what the isolation depends on.

---

## 5. Templates, controller and Docker host

### 5.1 Refreshing the Red Duck template

A template can't be booted, and its linked clones depend on it, so you can't
update it in place. Instead, build a new one beside it, then point the database
at it. On `pve`, with **10003** as the free VMID in this example:

1. **Full-clone the template** into an ordinary VM:

   ```bash
   qm clone 10001 10003 --name Kali-attacker-new --full
   ```

2. **Give it internet for the update.** `pondnc` has no route out. Add a second
   NIC on a network that does, temporarily. On ours that's the management
   bridge:

   ```bash
   qm set 10003 --net1 virtio,bridge=vmbr0
   qm start 10003
   ```

   > **DECISION:** a VM on `vmbr0` with no tag is on the management network.
   > If your site has a separate network with internet access for this, use it.

3. **Update it** from its console, then clean it as in build guide phase 5.2
   step 4 (SSH host keys, `machine-id`):

   ```bash
   sudo apt update && sudo apt full-upgrade -y
   ```

   ([Kali: Updating Kali](https://www.kali.org/docs/general-use/updating-kali/)).
   Check `eth0` still isn't managed by NetworkManager (build guide 5.1).
4. **Shut it down and remove the extra NIC**, or the token playbook rejects it:

   ```bash
   qm shutdown 10003
   qm set 10003 --delete net1
   qm config 10003 | grep -E '^(net|ide|sata|scsi)'    # net0 on pondnc only; no CD drive
   ```

5. **Swap the names and make it a template.** Two VMs with the same name means
   the seeder picks whichever comes first (known issue L11), so rename the old
   one first:

   ```bash
   qm set 10001 --name Kali-attacker-old
   qm set 10003 --name Kali-attacker
   qm template 10003
   ```

6. **Add it to the token's template pool:** re-run the playbook with 10003 in
   `pond_template_vmids` (build guide phase 7).
7. **Point the database at it.** The app clones the VMID stored in
   `vm_templates`, not the name. Back up the database, then:

   ```sql
   UPDATE vm_templates SET proxmox_template_vmid = 10003 WHERE template_name = 'Kali-attacker';
   ```

   Every challenge using Red Duck now launches from 10003. The seeder matches
   templates by VMID, so later seeds find this row.
8. **Test** one Red Duck launch and one multi-VM launch.
9. **Retire the old template** once no session is still running on a clone of
   it: remove 10001 from `pond_template_vmids`, re-run the playbook, then
   `qm destroy 10001`.

The same steps work for any template. Sources: [qm(1)](https://pve.proxmox.com/pve-docs/qm.1.html)
(`clone --full`, `set --delete`, `template`, `destroy`).

### 5.2 The controller

- **Operating system:** the distribution's normal updates (on Ubuntu,
  `sudo apt update && sudo apt upgrade`). Restart the app afterwards.
- **Python packages:** most versions are pinned in `requirements.txt` and
  `pond-sec/requirements.txt`. To update them, make a fresh venv, change the
  pins, install, and run both test suites ([testing](../testing/testing.md))
  before using it for real. `pip list --outdated` shows what's behind
  ([pip list](https://pip.pypa.io/en/stable/cli/pip_list/)), and `pip-audit`
  checks for known vulnerabilities ([pip-audit](https://pypi.org/project/pip-audit/)).
- **Code updates:** `git pull` in the checkout, then `python3 -m db.init_database --check`.
  If it reports a schema difference, the change needs its one-off migration
  before the app starts.

### 5.3 The Docker host

- **Operating system and Docker:** normal Ubuntu updates. Containers run with
  `--restart unless-stopped`, so they come back after a reboot, but check them:
  `sudo docker ps`.
- **Challenge containers:** rebuilt by the deploy scripts
  ([Docker host README](../../deployment/docker-host/README.md)).
- After any change, from a Red Duck clone: `nc -zv 10.1.30.10 8000` and one
  challenge port.

---

## 6. Housekeeping

| Leftover | How to find it | How to clear it |
|---|---|---|
| Stale sessions | **Manage sessions → Running now** after a reboot or crash | **Close** |
| Leftover clones | `pvesh get /pools/pond-clones --output-format json-pretty`, compared with **Running now** | Launch guide, "Clearing a leftover clone" |
| Leftover session VNets | `pvesh get /cluster/sdn/vnets` on `pve`: any `s…` VNet with no running session | `pvesh delete /cluster/sdn/vnets/<vnet>` then `pvesh set /cluster/sdn`, only after its clones are destroyed ([Proxmox API viewer: /cluster/sdn](https://pve.proxmox.com/pve-docs/api-viewer/#/cluster/sdn)) |
| "Machines without a session" rows | Manage sessions | No supported way yet (known issue L4) |
| Old backups | Wherever you keep them | Your retention decision (§2.1) |

Leftover clones and VNets are created by failed launches (known issues L2,
L14, L16).

---

## 7. Certificates

The app trusts Proxmox's own CA (`pve-root-ca.pem`), not the node certificate,
so renewing the node certificate needs nothing on the controller. Check when the
node certificate expires:

```bash
openssl x509 -in /etc/pve/nodes/pve/pve-ssl.pem -noout -enddate
```

([openssl-x509(1)](https://docs.openssl.org/master/man1/openssl-x509/);
[Proxmox: Certificate Management](https://pve.proxmox.com/wiki/Certificate_Management)).
If the CA itself is ever regenerated, copy the new `pve-root-ca.pem` to the
controller (build guide phase 8, step 3).
