# The Pond

A self-hosted capture-the-flag (CTF) training range on Proxmox. Students sign in
to a web app (**Pond Sec**), launch a challenge, get their own freshly cloned
virtual machines with a browser console, find flags, and score points on
leaderboards. Staff approve accounts, run classes and add challenges.

It runs on a single Proxmox VE 9 server, with a Flask web app on a controller VM
and an SQLite database.

Built in 2026 as a UNSW Canberra ZEIT3101 IT Project 2 capstone (Group 6).

---

## Contents

1. [How it works](#how-it-works)
2. [Setting up from a fresh Proxmox install](#setting-up-from-a-fresh-proxmox-install)
3. [Running it day to day](#running-it-day-to-day)
4. [Tests](#tests)
5. [Repository layout](#repository-layout)
6. [Documentation](#documentation)

---

## How it works

| Part | Where | What it does |
|---|---|---|
| **Pond Sec** (`pond-sec/`) | Controller VM | Flask web app: accounts, challenges, launch and close, flag submission, scoring, staff console, browser VM console (noVNC over a WebSocket relay) |
| **Provisioner** (`provisioner.py`) | Called by Pond Sec | Every Proxmox operation, through the REST API ([proxmoxer](https://proxmoxer.github.io/docs/)): clone, firewall, start, stop, destroy. Writes static IPs and handout downloads into a clone's disk before first boot with `virt-customize` over SSH |
| **Database** (`db/`) | `the_pond.db` on the controller | SQLAlchemy models; challenges are seeded from `vars/challenges/*.yml` |
| **Challenge templates** | Proxmox, VMIDs 10000-10999 | One template per machine. **Red Duck** (`Kali-attacker`, 10001) is the student workstation and the attacker in multi-VM challenges |
| **Docker host** | Proxmox VM 9997, `10.1.30.10` | Runs the netcat/web challenges as containers, serves handout files, and hands out DHCP on the Red Duck network |

**A launch, in short.** A student clicks *Launch*. Pond Sec creates a private
software-defined network (a VNet in the SDN zone `pondz`) for that session,
clones each of the challenge's templates into it (VMIDs 1001-2000), firewalls
each clone, writes its static IP, and boots it. A challenge whose only VM is Red
Duck stays on the shared `pondnc` network instead, behind its own firewall, so it
can reach the Docker host. Closing the session, or the session reaper, destroys
the clones and the VNet.

The full design, with the reasons for each network decision, is in the
[network reference](docs/networking/pond-proxmox-vmid-network-reference.md).

---

## Setting up from a fresh Proxmox install

This is the short path, from a server with nothing on it to a first launch. Each
step links to the detailed version in the
[build guide](docs/setup/build-from-scratch.md), which also covers
troubleshooting. Do the steps in order: the app's own checks refuse to run if one
is skipped.

The values shown are ours (`10.1.21.151` for Proxmox, `10.1.21.248` for the
controller, and so on). Every site-specific value is listed in the build guide's
[site values](docs/setup/build-from-scratch.md#site-values) table. Change them
together.

**Allow one to two days.** Most of it is building templates and the Docker host.

### What you need first

- A server for Proxmox VE 9 with two NICs.
- From your network team: a switch trunk for the first NIC (management VLAN
  untagged, lab VLAN tagged), a port for the second NIC on the lab VLAN,
  addresses for the Proxmox host and the controller, and the upstream gateway
  rules G1-G5 in §7.5 of the
  [network reference](docs/networking/pond-proxmox-vmid-network-reference.md).
  In short: nothing on the lab network may reach the management network or the
  controller, and students reach only the web app.
- A machine to administer from, with SSH.

### Step 1. Install Proxmox and the host tools

1. Install Proxmox VE 9 following the
   [Proxmox installation guide](https://pve.proxmox.com/pve-docs/chapter-pve-installation.html).
   Hostname `pve`, LVM-thin storage `local-lvm` (the installer default). The
   app assumes the volume group is called `pve`.
2. Without a subscription, switch to the no-subscription repository, or
   `apt update` fails with 401
   ([Proxmox wiki, Package Repositories](https://pve.proxmox.com/wiki/Package_Repositories)):

   ```bash
   echo 'Enabled: false' >> /etc/apt/sources.list.d/pve-enterprise.sources
   echo 'Enabled: false' >> /etc/apt/sources.list.d/ceph.sources
   cat > /etc/apt/sources.list.d/pve-no-subscription.sources <<'EOF'
   Types: deb
   URIs: http://download.proxmox.com/debian/pve
   Suites: trixie
   Components: pve-no-subscription
   Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
   EOF
   apt update
   ```

3. Install the tools the app and the token playbook use:

   ```bash
   apt install libguestfs-tools unzip ansible
   ```

   `libguestfs-tools` provides `virt-customize`
   ([virt-customize(1)](https://libguestfs.org/virt-customize.1.html)).
   **Don't install `qemu-utils`**: `qemu-img` already ships with Proxmox, and
   `qemu-utils` conflicts with it badly enough that apt offers to remove Proxmox.

Detail: [build guide, phase 1](docs/setup/build-from-scratch.md#phase-1-proxmox-host).

### Step 2. Networking on the host

1. Configure the two bridges in `/etc/network/interfaces`: `vmbr0` on the first
   NIC (VLAN-aware, carries the host's management address), and `vmbr1` on the
   second NIC with **no address**, so the hypervisor has no IP on the student
   network. The file is in
   [build guide, phase 2](docs/setup/build-from-scratch.md#phase-2-host-bridges).
   Keep the `source /etc/network/interfaces.d/*` line: SDN writes its bridges
   there. Apply with `ifreload -a`.
2. Create the SDN zone and the two fixed VNets
   ([Proxmox SDN](https://pve.proxmox.com/pve-docs/chapter-pvesdn.html)):

   ```bash
   pvesh create /cluster/sdn/zones --zone pondz --type simple
   pvesh create /cluster/sdn/vnets --vnet pondtpl --zone pondz
   pvesh create /cluster/sdn/vnets --vnet pondnc --zone pondz
   pvesh set /cluster/sdn
   pvesh get /cluster/sdn/zones        # pondz must show type simple
   ```

   `pondtpl` parks the target templates' NICs; `pondnc` is the Red Duck network
   that reaches the Docker host. Create `pondnc` **without a subnet**: a subnet
   with a gateway would give the hypervisor an address next to every student's
   workstation. The per-session VNets are made and deleted by the app; never
   create them by hand.
3. Turn on the datacenter firewall. The per-VM rules the app writes do nothing
   without it, and the app refuses every launch if it's off. **Keep a second SSH
   session open** while you do this, in case it locks you out:

   ```bash
   pvesh set /cluster/firewall/options --enable 1
   pvesh get /cluster/firewall/options      # enable: 1
   ```

   If your controller isn't on the host's own subnet, add rules admitting it on
   ports 8006 and 22 first
   ([Proxmox firewall](https://pve.proxmox.com/pve-docs/chapter-pve-firewall.html)).

Detail: [build guide, phases 2-4](docs/setup/build-from-scratch.md#phase-2-host-bridges).

### Step 3. Build the challenge templates

Templates are ordinary VMs converted with `qm template`. The app finds a template
by its Proxmox **name**, which must match the challenge file exactly. Keep
template VMIDs in 10000-10999.

Every template must:
- have all its disks on `local-lvm` and no host CD-ROM passthrough;
- have every NIC on `pondtpl` or `pondnc` (never `vmbr0` or `vmbr1`). When you
  move a NIC, keep its MAC address
  ([qm(1)](https://pve.proxmox.com/pve-docs/qm.1.html)):

  ```bash
  qm config <vmid> | grep ^net        # note the MAC
  qm set <vmid> --net0 virtio=<MAC>,bridge=pondtpl
  ```

- if a challenge gives it a static IP (every multi-VM challenge does): name its
  NIC `eth0` and bring it up from `/etc/network/interfaces`. The app writes a
  Debian `ifupdown` file before first boot. On Kali, stop NetworkManager
  managing `eth0`, or the address never applies.

**Red Duck (VMID 10001).** Install Kali, create the user `kali`, make sure
`wget`, `curl`, `nc` and `python3` are installed, and leave its NIC on DHCP.
Clean it so every clone is unique (`rm /etc/ssh/ssh_host_*`,
`truncate -s0 /etc/machine-id`), shut it down, set `net0` to `bridge=pondnc`,
name it exactly **`Kali-attacker`**, and run `qm template 10001`. The name
matters: clones of this template get handouts, and a challenge whose only VM is
`Kali-attacker` stays on `pondnc` where the Docker host is.

**Target machines** (Metasploitable 2, DC-1, Kioptrix and so on). Either import
them by hand (`qm importovf` or `qm disk import`, then `qm template`), or use
`import_challenge.py` from the controller once step 6 is done:

```bash
python3 import_challenge.py https://download.vulnhub.com/dc/DC-1.zip
```

The importer needs its own Proxmox token with VM-creation rights (the app's token
deliberately can't create VMs), and it puts the new template's NIC on `vmbr0`:
move it to `pondtpl` afterwards. Its settings are listed in
[build guide, phase 5.3](docs/setup/build-from-scratch.md#53-target-templates).

Detail: [build guide, phase 5](docs/setup/build-from-scratch.md#phase-5-challenge-templates).

### Step 4. Build the Docker host (VMID 9997)

1. Create a VM with two NICs: `net0` on `vmbr0` (management, for updates and
   image pulls) and `net1` on `pondnc`.
2. Install Ubuntu Server. Give the `pondnc` NIC `10.1.30.10/24` with **no
   gateway**.
3. Install Docker Engine from Docker's repository
   ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
4. Run DHCP on the `pondnc` NIC only, handing out `10.1.30.100-250` with no
   gateway and no DNS, so Red Duck clones get addresses.
5. Block the containers from reaching the management network.
6. Build and run each challenge's container, published only on `10.1.30.10`,
   and serve each challenge's handout files on port 8000.
   `tools/pond-deploy-sf24.sh` is the deploy script for the BSidesSF 2024 set.

Keep VM 9997 out of the `pond-clones` and `pond-templates` pools. Detail:
[`deployment/docker-host/README.md`](deployment/docker-host/README.md) and
[build guide, phase 6](docs/setup/build-from-scratch.md#phase-6-docker-host-vmid-9997).

### Step 5. Create the app's API token

The playbook creates the user `pond@pve`, the pools, the roles and a
least-privilege token `pond@pve!launcher`, checks every template against the
step 3 rules, and refuses to finish if the token ends up with any right it
shouldn't have.

First, set the protected VMIDs: VMs the app must never touch. Ours are
`300-303` (another project on our server) and `1000`. On a server with no other
VMs, empty the list. It lives in three places, which must agree:

| Where | Setting |
|---|---|
| `provisioner.py` | `PROTECTED_VMIDS` |
| `playbooks/pond_least_privilege.yml` | `pond_protected_vmids` |
| The app's environment | `PROXMOX_PROTECTED_VMIDS` (default `300-303,1000`) |

Then on `pve`, as root. Copy just the playbook across and run it from `/root`,
not from the repo (the repo's `ansible.cfg` expects a vault file that isn't
there). List **every** template VMID:

```bash
scp playbooks/pond_least_privilege.yml root@10.1.21.151:/root/
# on pve:
cd /root
ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml \
  -e '{"pond_template_vmids":[10000,10001,10002]}' --check     # dry run
ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml \
  -e '{"pond_template_vmids":[10000,10001,10002]}'
pveum user token permissions pond@pve launcher                 # verify
```

The token secret is written once to `/root/pond-launcher.token` (mode 0600).
Adding a template later means re-running the playbook with its VMID in the list.
Sources: [ansible-playbook](https://docs.ansible.com/ansible/latest/cli/ansible-playbook.html),
[pveum(1)](https://pve.proxmox.com/pve-docs/pveum.1.html). Detail:
[build guide, phase 7](docs/setup/build-from-scratch.md#phase-7-the-api-token).

### Step 6. Set up the controller VM (VMID 9999)

Create a VM on `vmbr0` (we use `10.1.21.248`) running Ubuntu Server with Python
3.12 or later. It must reach `pve` on ports 8006 and 22, and students must reach
it on the web app's port. Then:

```bash
git clone https://github.com/GregFromIT/26_ITP2_Group6_ThePond.git ~/26_ITP2_Group6_ThePond
cd ~/26_ITP2_Group6_ThePond
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r pond-sec/requirements.txt

# Proxmox's CA certificate, so the app can verify it's talking to the real host
scp root@10.1.21.151:/etc/pve/pve-root-ca.pem ~/pve-root-ca.pem

# SSH key for virt-customize (static IPs and handouts)
ssh-keygen -t ed25519
ssh-copy-id root@10.1.21.151
ssh -o BatchMode=yes root@10.1.21.151 true && echo "key login OK"
```

The first SSH connection adds `pve` to `~/.ssh/known_hosts`. The app refuses to
SSH to a host it doesn't already know.

This SSH key can do more than the API token. Restrict it in
`/root/.ssh/authorized_keys` on `pve` with `from="10.1.21.248"` and a forced
command that only runs `virt-customize`
([sshd(8), AUTHORIZED_KEYS FILE FORMAT](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT)).

Sources: [Python venv](https://docs.python.org/3/library/venv.html),
[ssh-copy-id(1)](https://manpages.ubuntu.com/manpages/noble/man1/ssh-copy-id.1.html).
Detail: [build guide, phase 8](docs/setup/build-from-scratch.md#phase-8-controller-vm-vmid-9999).

### Step 7. Create the database, accounts and challenges

From the repo root on the controller, with the venv active.

1. **Database.** Creates `the_pond.db` with every table and the three roles:

   ```bash
   python3 -m db.init_database
   python3 -m db.init_database --check      # read-only confirmation
   ```

2. **First accounts.** Edit the `ACCOUNTS` list in `db/seed_accounts.py`: each
   entry is `(username, display name, role)`, role `user`, `manager` or
   `sysadmin`. Include at least one `sysadmin`. Then:

   ```bash
   python3 -m db.seed_accounts --check      # preview
   python3 -m db.seed_accounts              # prints each temporary password once
   ```

3. **Challenges.** Review `vars/challenges/` and delete any you won't run. Every
   template a challenge names must already exist in Proxmox. Then load them:

   ```bash
   export THEPOND_PROXMOX_TOKEN_SECRET="$(ssh root@10.1.21.151 cat /root/pond-launcher.token)"
   export THEPOND_PROXMOX_CA_BUNDLE=$HOME/pve-root-ca.pem
   python3 -m db.init_database --seed-challenges
   ```

   The seeder only inserts: re-seeding won't update a challenge already in the
   database. How to write your own challenge files is in the
   [authoring guide](docs/challenges/authoring-guide.md).

Detail: [build guide, phase 9](docs/setup/build-from-scratch.md#phase-9-database-accounts-and-challenges).

### Step 8. First launch

1. Start the app as in [Running it day to day](#running-it-day-to-day), below.
2. Browse to `http://10.1.21.248:5000` and sign in as your `sysadmin`. You're
   made to change the temporary password first.
3. Launch one Red Duck challenge. On `pve`, watch the clone appear and boot:

   ```bash
   watch -n2 'qm list | grep -v " stopped"; pgrep -a virt-customize'
   ```

   Open its console, then close the session and check the clone is destroyed.
   If every launch fails, the app log shows `launch failed for instance`; the
   most likely first-time cause is the datacenter firewall (step 2.3).
4. Register a test student, approve it, and launch and solve one challenge of
   each shape: Red Duck, single-VM target, multi-VM.
5. Set up the session reaper (next section) so expired or dead sessions don't
   hold VMs forever.
6. Run the network acceptance tests N1-N15 in §9 of the
   [network reference](docs/networking/pond-proxmox-vmid-network-reference.md),
   and record who ran each and when.

Detail: [build guide, phase 10](docs/setup/build-from-scratch.md#phase-10-first-launch-and-acceptance).

---

## Running it day to day

### Starting up

After a power cut or reboot, the controller (9999) and the Docker host (9997)
don't start on their own unless you set `qm set <vmid> --onboot 1`.

1. Power on the Proxmox server and log in to `https://10.1.21.151:8006`.
2. Start VM 9997 (Docker host), then VM 9999 (controller).
3. On the controller:

   ```bash
   cd ~/26_ITP2_Group6_ThePond
   source .venv/bin/activate

   export PROXMOX_TOKEN_ID='pond@pve!launcher'
   export PROXMOX_TOKEN_SECRET="$(ssh root@10.1.21.151 cat /root/pond-launcher.token)"
   export PROXMOX_CA_BUNDLE=$HOME/pve-root-ca.pem
   export THEPOND_PROXMOX_SSH_USER=root
   export THEPOND_PROXMOX_ALLOW_ROOT_SSH=1
   export THEPOND_PROXMOX_SSH_KEY=$HOME/.ssh/id_ed25519
   echo "secret=${#PROXMOX_TOKEN_SECRET} chars"      # expect 36

   cd pond-sec && flask --app wsgi run --host 0.0.0.0 --port 5000
   ```

   `secret=0 chars` means the SSH read of the token failed: check
   `ssh -o BatchMode=yes root@10.1.21.151 true`. A line starting
   `WARNING in __init__: Proxmox settings:` means an export didn't take.

`flask run` only listens on `127.0.0.1` unless given `--host`
([Flask CLI](https://flask.palletsprojects.com/en/stable/cli/)). Never add
`--debug`: Werkzeug's debugger gives an interactive Python console to anyone who
triggers an error. For production, run under gunicorn with `RANGE_ENV=production`
and a `FLASK_SECRET_KEY`, behind a reverse proxy, with `--threads` so open
consoles don't use up every worker
([flask-sock, web servers](https://flask-sock.readthedocs.io/en/stable/web_servers.html)):

```bash
RANGE_ENV=production FLASK_SECRET_KEY=... gunicorn 'wsgi:app' -b 127.0.0.1:8000 --workers 2 --threads 50
```

Every setting, and what it does, is in the
[admin guide](docs/user-guides/pond-sec-admin-guide.md). The full start-up
procedure, with checks, is the [launch guide](docs/user-guides/launch-guide.md).

### The session reaper

`db/reap_sessions.py` closes sessions that are stuck launching for over 15
minutes, older than `POND_SESSION_TTL_HOURS` (default 3), or whose VM is missing
or stopped (for example after a reboot), and destroys their VMs. If Proxmox
can't be reached it closes nothing. Run it from cron on the controller, every 10
minutes and at boot ([crontab(5)](https://man7.org/linux/man-pages/man5/crontab.5.html)).
Cron starts with an almost empty environment, so put the exports above in a
root-only file and source it:

```cron
*/10 * * * * cd /home/<user>/26_ITP2_Group6_ThePond && . /root/pond.env && .venv/bin/python -m db.reap_sessions
@reboot      cd /home/<user>/26_ITP2_Group6_ThePond && . /root/pond.env && .venv/bin/python -m db.reap_sessions
```

### Backups

Back up the database while the app runs with SQLite's online backup, which is
safe mid-write where copying the file isn't
([SQLite CLI, .backup](https://sqlite.org/cli.html#special_commands_to_sqlite3_dot_commands_)):

```bash
sqlite3 the_pond.db ".backup 'the_pond-$(date +%F).db'"
```

Restoring, and updating Proxmox, templates and the controller, are in
[maintenance and restore](docs/operations/maintenance-and-restore.md).

---

## Tests

From the repo root, with the venv active:

```bash
python3 -m pytest tests                           # main suite
python3 pond-sec/tests/test_credentials.py        # Proxmox credential and privilege checks
python3 -m db.test_database                       # integrity checks, on an initialised database
```

None of these need a Proxmox host: they use stand-ins for every hypervisor call.
Testing against the live system is described in [testing](docs/testing/testing.md).

---

## Repository layout

**The running system.**

| Path | What it is |
|---|---|
| `pond-sec/` | The Pond Sec web app (Flask): `app/` code, templates and static files (including its own noVNC); `wsgi.py` entry point |
| `provisioner.py` | Every Proxmox operation: VMIDs, cloning, session networks, firewall rules, disk injection, teardown |
| `db/` | SQLAlchemy models; `init_database` (tables, roles, challenge seeding); `seed_accounts`; `reap_sessions` (cron); `migrate_one_active_session` (one-off index migration for databases made before 2 Nov 2026) |
| `challenge_ingestion/` | The web challenge-submission pipeline |
| `vars/challenges/` | One YAML file per challenge |
| `playbooks/pond_least_privilege.yml` | Creates the app's Proxmox token, roles and pools |
| `group_vars/all.yml`, `ansible.cfg`, `inventory/`, `requirements.yml` | Proxmox host and token name for the seeder; Ansible settings |
| `import_challenge.py` | Imports a VM image as a template and writes its challenge file |
| `tools/` | Generators for importing CTF releases, and the BSidesSF 2024 Docker deploy script |
| `deployment/` | Example settings file; the Docker host's configuration |
| `examples/challenge_uploads/` | Example submission manifests |
| `tests/` | The test suite |
| `requirements.txt`, `pond-sec/requirements.txt` | Python dependencies |

**Stale but working: not part of the running system.** These still run against
the current code (checked 2 Nov 2026), but nothing in Pond Sec uses them:

| Path | What it is |
|---|---|
| `db/store.py`, `db/schema.sql`, `tests/test_store.py` | The original raw-SQLite data layer, replaced by the SQLAlchemy models in `db/`; its tests pass |
| `db/test_database.py` | Standalone integrity test for the current models (see [Tests](#tests)) |
| `proxmoxerTest.py` | Manual smoke test: clones and destroys one VM (VMID 9999) on a live Proxmox host |
| `prov_net_test.py` | Manual test of static-IP injection (`inject_instance_network()`) on a live host |
| `playbooks/create_instance.yml`, `playbooks/destroy_instance.yml` | The Ansible VM lifecycle that `provisioner.py` replaced; valid, but nothing calls them |
| `templates/` | Pages for the old debugger app |
| `static/novnc` | Git submodule for the old debugger app. Pond Sec's console uses its own copy in `pond-sec/app/static/novnc/` |

**Legacy: no longer runs.** [`legacy/`](legacy/README.md) holds the original
debugger app and two scripts that import modules since removed.

---

## Documentation

| You are... | Read, in order |
|---|---|
| **New to the project** | [System overview](docs/architecture/system-overview.md) → [known issues](docs/known-issues.md) |
| **Building your own copy** | This README's setup guide → [build from scratch](docs/setup/build-from-scratch.md) → [network reference](docs/networking/pond-proxmox-vmid-network-reference.md) → [Docker host](deployment/docker-host/README.md) |
| **Adding challenges** | [Authoring guide](docs/challenges/authoring-guide.md) → [submission pipeline](docs/challenges/submission-pipeline.md) |
| **Running the system** | [Launch guide](docs/user-guides/launch-guide.md) → [admin guide](docs/user-guides/pond-sec-admin-guide.md) → [maintenance and restore](docs/operations/maintenance-and-restore.md) |
| **A student** | [Student guide](docs/user-guides/pond-sec-student-guide.md) |
| **Changing the code** | [Contributing](CONTRIBUTING.md) → [code map](docs/architecture/pond-sec-code-map.md) → [data model](docs/database/pond-sec-data-model.md) → [design decisions](docs/architecture/pond-sec-design-decisions.md) → [testing](docs/testing/testing.md) |
| **Taking over** | [Handover checklist](docs/handover/handover-checklist.md) → [access and credentials](docs/handover/access-and-credentials.md) |

Open defects, gaps and security findings, each with its status, are in
[known issues](docs/known-issues.md). The security assessment of 29 Sep 2026 is
[`pond-sec/docs/SECURITY_ASSESSMENT.md`](pond-sec/docs/SECURITY_ASSESSMENT.md).
Superseded documents are kept in [`docs/archive/`](docs/archive/README.md).