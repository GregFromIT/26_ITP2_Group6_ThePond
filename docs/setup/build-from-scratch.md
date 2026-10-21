# Build The Pond from scratch

How to go from bare hardware to a working copy of The Pond, in order. Every
step uses our own values (addresses, VMIDs, names) as the worked example. Where
your site differs, change the value in every place it appears. The
[site values](#site-values) table at the end lists them all.

Written against `main` at `04998a1` with the branch
`fix/seeder-theme-flagformat` (`3dc6afc`) merged. On `main` alone, phase 9 fails and the app won't start
([known issues](../known-issues.md) B1, B2) until that merge is done.

**Who this is for:** whoever is building a new copy. It assumes you can
administer Linux, Proxmox and a managed network. It doesn't assume you've seen
the code.

**How long it takes:** one to two days. Most of that is building templates and
the Docker host.

---

## The order, and why

Each phase depends on the one before it, and the app's own checks refuse to
work if one is skipped. For example, the token playbook refuses to run until the
SDN zone exists and the templates are in place.

| Phase | Builds | Done when |
|---|---|---|
| 0 | Network prerequisites | VLANs and gateway rules confirmed |
| 1 | Proxmox host | Web UI loads, `apt update` works, `virt-customize` installed |
| 2 | Host bridges | `vmbr0` and `vmbr1` up |
| 3 | SDN zone and VNets | `pondz` (Simple) with `pondtpl` and `pondnc` |
| 4 | Proxmox firewall | Datacenter firewall on, management still reachable. Required: the app refuses to launch without it |
| 5 | Challenge templates | Red Duck and target templates exist, NICs on the right networks |
| 6 | Docker host | Containers answering on `10.1.30.10` |
| 7 | API token | `pond@pve!launcher` created with only its intended rights |
| 8 | Controller VM | App installed, settings in place, SSH key to `pve` |
| 9 | Database | Tables, roles, first accounts, challenges seeded |
| 10 | First launch | Acceptance tests pass |

The network design behind phases 0–4 (why each network exists, what is isolated
from what) is in the
[network reference](../networking/pond-proxmox-vmid-network-reference.md).
This guide gives the steps; the reference gives the reasons.

---

## Phase 0: network prerequisites

You need these from whoever runs the site network before you start:

- [ ] A switch port for the server's first NIC (`nic0`), as a trunk: VLAN 10
      untagged (management) and VLAN 20 tagged (lab).
- [ ] A switch port for the second NIC (`nic3`) on VLAN 20, the lab network.
- [ ] Addresses: `10.1.21.151` for the Proxmox host, `10.1.21.248` for the
      controller VM, gateways `10.1.21.1` (VLAN 10) and `10.1.20.1` (VLAN 20).
- [ ] The upstream gateway rules G1–G5 from §7.5 of the
      [network reference](../networking/pond-proxmox-vmid-network-reference.md).
      In short: nothing on the lab network may reach the management network or
      the controller, and students reach only the web app. Write down who
      confirmed each rule, and when.

## Phase 1: Proxmox host

1. Install Proxmox VE 9 on the server, following the
   [Proxmox installation guide](https://pve.proxmox.com/pve-docs/chapter-pve-installation.html).
   Use hostname `pve`, management IP `10.1.21.151/24` and gateway `10.1.21.1`.
   Use LVM-thin storage named `local-lvm` (the installer's default). The app
   assumes the volume group is called `pve`
   (`provisioner.inject_instance_network()`, `vg_name="pve"`).
2. Without a subscription, switch to the no-subscription repository, or every
   `apt update` returns 401. On Proxmox VE 9 the repositories are deb822
   `.sources` files:

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

   Source: [Proxmox wiki: Package Repositories](https://pve.proxmox.com/wiki/Package_Repositories).
   You can also do this in the web UI under **Datacenter → `pve` → Updates →
   Repositories**.
3. Install the tools the app runs on the host over SSH (`virt-customize`) and
   the ones the template importer uses:

   ```bash
   apt install libguestfs-tools unzip
   ```

   Sources: [virt-customize(1)](https://libguestfs.org/virt-customize.1.html),
   [Debian: libguestfs-tools](https://packages.debian.org/trixie/libguestfs-tools).

   **Don't install `qemu-utils`.** `qemu-img` already ships in `pve-qemu-kvm`;
   `qemu-utils` conflicts with it, and apt will offer to remove the whole
   Proxmox stack.

4. Install Ansible on the host for the token playbook in phase 7:
   `apt install ansible`.

## Phase 2: host bridges

`/etc/network/interfaces` on `pve`. Check your NIC names first with
`ip -br link` (Proxmox VE 9 can pin them as `nicN`):

```
auto lo
iface lo inet loopback

iface nic0 inet manual
iface nic3 inet manual

# VLAN 10 native (untagged) for the host; VLAN 20 available tagged
auto vmbr0
iface vmbr0 inet static
    address 10.1.21.151/24
    gateway 10.1.21.1
    bridge-ports nic0
    bridge-stp off
    bridge-fd 0
    bridge-vlan-aware yes
    bridge-vids 2-4094

# VLAN 20 lab, no host address
auto vmbr1
iface vmbr1 inet manual
    bridge-ports nic3
    bridge-stp off
    bridge-fd 0

source /etc/network/interfaces.d/*
```

Apply with `ifreload -a`. The last line is required: SDN writes its bridges to
`/etc/network/interfaces.d/sdn`. Keep `vmbr1` without an address, so the
hypervisor has no IP on the student network.

Sources: [Proxmox admin guide: Network Configuration](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html),
[Proxmox SDN](https://pve.proxmox.com/pve-docs/chapter-pvesdn.html).

> This layout was reconstructed from project notes. Before copying it onto a
> live host, diff it against that host's real file
> (network reference §4.1, "VERIFY").

## Phase 3: SDN zone and VNets

```bash
pvesh create /cluster/sdn/zones --zone pondz --type simple
pvesh create /cluster/sdn/vnets --vnet pondtpl --zone pondz
pvesh create /cluster/sdn/vnets --vnet pondnc --zone pondz
pvesh set /cluster/sdn
pvesh get /cluster/sdn/zones        # pondz must show type simple
ip -br link | grep -E 'pondtpl|pondnc'
```

| Name | Purpose |
|---|---|
| `pondz` | The Simple zone. The app creates one VNet per session in it (every launch except a lone Red Duck), and deletes it at close. The playbook, and the app before every launch, refuse to run unless it is type `simple` |
| `pondtpl` | A parking network for target template NICs, so cloning them needs no grant on a real bridge. Clones never stay on it: every target's NICs move to its session VNet |
| `pondnc` | The Red Duck network: the Docker host (`10.1.30.10`) and every Red Duck clone |

Create `pondnc` **without** a subnet. A subnet with a gateway would put the
hypervisor's own address on that network, next to every student's Red Duck
(network reference §7.4).

Source: [Proxmox API viewer: /cluster/sdn](https://pve.proxmox.com/pve-docs/api-viewer/#/cluster/sdn).
Never create or delete the per-session `s…` VNets by hand.

## Phase 4: Proxmox firewall

The per-VM rules the app writes do nothing unless the datacenter firewall is on.
The app checks before every launch and refuses with "the datacenter firewall is
disabled" if it's off (`provisioner.check_isolation_preconditions()`).

1. Open a **second** SSH session to `pve` and leave it open. Enabling the
   firewall blocks host traffic except management access from the local network.
2. If the controller isn't on `10.1.21.0/24`, add host rules admitting it on
   ports 8006 and 22 **before** you enable the firewall.
3. Enable it and confirm:

   ```bash
   pvesh set /cluster/firewall/options --enable 1
   pvesh get /cluster/firewall/options      # enable: 1
   ```

4. From the controller's address, confirm you can still reach
   `https://10.1.21.151:8006` and SSH. If you can't, use the second session to
   run `pvesh set /cluster/firewall/options --enable 0`.

Source: [Proxmox firewall](https://pve.proxmox.com/pve-docs/chapter-pve-firewall.html),
"Cluster Wide Setup".

## Phase 5: challenge templates

Templates are ordinary Proxmox VMs converted to templates. The app clones them
by **name**: the name in the challenge file must match the Proxmox VM name
exactly (`provisioner._resolve_template_vmid()`). Keep templates in VMIDs
10000–10999.

### 5.1 Rules every template must meet

The token playbook (phase 7) checks these and refuses to run otherwise:

- It's a template (`qm template <vmid>`).
- Every disk is on `local-lvm`.
- There's no host CD-ROM passthrough.
- Every NIC is on a VNet in `pondz` (`pondtpl` or `pondnc`), not on `vmbr0` or
  `vmbr1`. The exceptions are a bridge you deliberately list in
  `pond_template_bridges`, and the VNet in `pond_workstation_vnet` if you build a
  routed Red Duck network (network reference §5.4).
- It isn't in any pool other than `pond-templates` (the playbook adds it there).

The playbook doesn't check the next one, but launches fail without it:

- **Delete the CD/DVD drive entirely** (`qm set <vmid> --delete ide2`, or
  whichever slot it's in), not just its ISO. Before writing into a clone's disk,
  the app takes the *first* disk-like entry in the VM's config
  (`provisioner._find_disk_volid()`). A CD drive, even an empty one, can be
  picked instead of the real disk, and then every launch that needs a static IP
  or handouts fails (known issue L13).

If a challenge gives a VM a **static IP** (every multi-VM challenge does), the
app writes it as a Debian `ifupdown` file for interface `eth0` before first
boot (`provisioner.inject_instance_network()`). That template must therefore:
- name its NIC `eth0`; and
- bring the NIC up from `/etc/network/interfaces`, which means NetworkManager
  must not take it over.

> **VERIFY (Kali):** Kali uses NetworkManager by default. In September the `dc1`
> attacker came up with no address for exactly this reason. Before relying on a
> multi-VM challenge, confirm on the template that `eth0` gets its address from
> `/etc/network/interfaces`, for example by making `eth0` unmanaged in
> NetworkManager.

When you change a NIC, keep its MAC address, or Proxmox generates a new one:

```bash
qm config <vmid> | grep ^net       # e.g. net0: virtio=BC:24:11:AA:BB:CC,bridge=vmbr0
qm set <vmid> --net0 virtio=BC:24:11:AA:BB:CC,bridge=pondtpl
```

Source: [qm(1)](https://pve.proxmox.com/pve-docs/qm.1.html).

### 5.2 Red Duck (the Kali workstation), VMID 10001

Red Duck is the student's workstation for every Docker-hosted challenge, and the
attacker in multi-VM challenges.

1. Install Kali Linux from its ISO into a new VM, VMID `10001`. Create the user
   `kali`. Handouts are downloaded to `/home/kali/Desktop/<challenge>` on first
   boot (`POND_HANDOUT_USER`, default `kali`).
2. Make sure `wget`, `curl`, `nc` and `python3` are installed. The handout
   download uses `wget`.
3. On `pondnc`, Red Duck gets its address by DHCP from the Docker host
   (phase 6), so leave networking as DHCP for that NIC. The VM firewall the app
   sets on each clone allows DHCP. See also the 5.1 note about static IPs for
   multi-VM use.
4. Clean the machine so every clone is unique: delete the SSH host keys
   (`rm /etc/ssh/ssh_host_*`, regenerated on boot) and empty
   `/etc/machine-id` (`truncate -s0 /etc/machine-id`).
5. Shut it down, delete the CD/DVD drive (5.1), and set `net0` to
   `bridge=pondnc` (keeping the MAC).
6. Name it exactly **`Kali-attacker`**. That name is used in 49 challenge files,
   and `POND_HANDOUT_TEMPLATE` defaults to it. The name matters twice: clones of
   *this* template are the ones that get handouts, and a challenge whose only VM
   is this template is the one launch that stays on `pondnc` (behind its own
   firewall) instead of getting a session VNet. Under any other name, Red Duck
   can't reach the Docker host.
7. `qm template 10001`.

### 5.3 Target templates

Each vulnerable machine (Metasploitable 2, DC-1, Kioptrix and so on) becomes one
template. Two ways to build one:

**With the importer.** `import_challenge.py` downloads an image onto the
Proxmox host, extracts it, imports the disk, makes the template, and writes or
updates the challenge file. It runs from the controller (phase 8), is
interactive, and allocates the next free VMID in 10000–10999:

```bash
python3 import_challenge.py https://download.vulnhub.com/dc/DC-1.zip
```

It needs its own settings: `THEPOND_PROXMOX_HOST`, `THEPOND_PROXMOX_NODE`,
`THEPOND_PROXMOX_USER`, `THEPOND_PROXMOX_TOKEN_NAME`,
`THEPOND_PROXMOX_TOKEN_SECRET`, `THEPOND_PROXMOX_SSH_USER`,
`THEPOND_PROXMOX_SSH_KEY`, plus `THEPOND_PROXMOX_ALLOW_ROOT_SSH=1` for root SSH.
It reads them from the environment or a `.env` file (`import_challenge.py`,
`REQUIRED_ENV`).

> **DECISION:** the importer creates VMs and imports disks, which the app's
> `pond@pve!launcher` token isn't allowed to do, and it refuses `@pam` tokens.
> You need a second, `@pve` token with VM-creation rights on the template range.
> Nothing in the repo defines it (known issue C4).

> **FIX:** the importer creates the template with `net0` on `vmbr0` and no VLAN
> tag, which is the management network (`import_challenge.py` line 289). Move
> the NIC to `pondtpl` straight afterwards (known issue N5).

**By hand.** Upload or download the image onto `pve`, then use `qm importovf`
(OVA/OVF) or `qm disk import` (a single disk) into `local-lvm`, attach the disk,
set the boot order, and convert to a template. Commands:
[qm(1)](https://pve.proxmox.com/pve-docs/qm.1.html).

Either way, before templating:
- put every NIC on `pondtpl`. Every launch moves a target's NICs onto its own
  session VNet, so nothing ever runs on the template's bridge;
- name it to match the challenge file (for example `metasploitable2`, `DC-1`,
  `Kioptrix-Level-1`).

> **DECISION:** a target launched on its own (our `metasploitable2`) sits alone
> on its session VNet and can only be reached through its console. To make it
> attackable, pair it with `Kali-attacker` in the challenge file (known issue
> N8; [authoring guide](../challenges/authoring-guide.md)).

Our templates:

| VMID | Name | Used by |
|---|---|---|
| 10000 | `metasploitable2` | `metasploitable2` |
| 10001 | `Kali-attacker` | every Red Duck challenge; the attacker in `dc1` and `kioptrixlevel1` |
| 10002 | `DC-1` | `dc1` |
| (VERIFY) | `Kioptrix-Level-1` | `kioptrixlevel1` |

## Phase 6: Docker host, VMID 9997

The Docker host runs the containerised challenges that Red Duck clones connect
to. It also serves each challenge's handout files on port 8000, and hands out
DHCP addresses on `pondnc`.

Its configuration isn't in the repo yet. **Follow
[`deployment/docker-host/README.md`](../../deployment/docker-host/README.md)**,
which lists every file it needs and what each one does.

In outline:

1. Create a VM with two NICs: `net0` on `vmbr0` (management, so you can
   administer it and it can pull images), and `net1` on `pondnc`.
2. Install Ubuntu Server. Give the `pondnc` NIC the static address
   `10.1.30.10/24` with **no gateway**, so the default route stays on the
   management NIC.
3. Install Docker Engine from Docker's own repository
   ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
4. Run DHCP on the `pondnc` NIC only, handing out `10.1.30.100–250` with no
   gateway and no DNS. The app's Red Duck firewall lets clones reach this host on
   any TCP port; if you move it off `10.1.30.10`, set `POND_DOCKER_HOST_IP` and
   `POND_HANDOUT_BASE_URL` to match.
5. Block the containers from reaching the management network.
6. Build and run each challenge's container, published only on `10.1.30.10`, and
   copy each challenge's handout files into the file server's folder.
7. Set the VM to start on boot if you want it to: `qm set 9997 --onboot 1`.

Keep 9997 **out of** the `pond-clones` and `pond-templates` pools.

## Phase 7: the API token

On `pve`, as root. Copy just the playbook across from a checkout of the repo,
and run it from `/root`, not from the repo. The repo's `ansible.cfg` points at a
vault password file that doesn't exist on `pve` (playbook header, lines 10–25):

```bash
scp playbooks/pond_least_privilege.yml root@10.1.21.151:/root/
# then, on pve:
cd /root
ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml \
  -e '{"pond_template_vmids":[10000,10001,10002]}' --check     # dry run
ansible-playbook -i 'localhost,' -c local ./pond_least_privilege.yml \
  -e '{"pond_template_vmids":[10000,10001,10002]}'
```

List **every** template VMID in `pond_template_vmids`. The playbook then:
- checks the zone and every template against the phase 5 rules;
- creates the roles, the user `pond@pve`, and the pools `pond-clones` and
  `pond-templates`;
- puts the templates in `pond-templates`;
- creates the privilege-separated token `pond@pve!launcher`;
- grants exactly the rights in §2.3 of the network reference;
- if you set `pond_workstation_vnet` (only for a routed Red Duck network,
  network reference §5.4), also grants `SDN.Use` on that one VNet;
- reads the token's effective rights back and fails if anything forbidden
  slipped in.

The secret is written once to `/root/pond-launcher.token` (mode 0600). The
playbook won't create a new token while that file exists.

> **DECISION:** the playbook header says to copy the secret into the app's
> settings and then `shred -u` the file. Our launch procedure instead reads it
> from that file over SSH at every start. Pick one and make the
> [launch guide](../user-guides/launch-guide.md) match.

Verify:

```bash
pveum user token permissions pond@pve launcher
pveum acl list | grep -i pond
```

Adding a template later means re-running the playbook with its VMID added to the
list.

## Phase 8: controller VM, VMID 9999

The controller runs the web app. It needs to reach `pve` on 8006 (API) and 22
(SSH), and be reachable by students on the web app's port. Nothing on the lab
network or any VNet should be able to reach it (gateway rule G5).

1. Create the VM on `vmbr0` (management), address `10.1.21.248`, and install a
   Linux server distribution with Python 3.12 or later.

   > **VERIFY:** we used Ubuntu. The project's tests were last run on
   > Python 3.12 and 3.13.

2. Get the code and install it:

   ```bash
   git clone https://github.com/GregFromIT/26_ITP2_Group6_ThePond.git ~/26_ITP2_Group6_ThePond
   cd ~/26_ITP2_Group6_ThePond
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r pond-sec/requirements.txt
   pip install python-dotenv pytest
   ```

   > **FIX:** the last line is needed because these two are missing from the
   > requirements files (known issues B3, B4): `python-dotenv` for
   > `import_challenge.py`, `pytest` for the tests. The web app itself needs
   > only the requirements files.

   Sources: [Python: venv](https://docs.python.org/3/library/venv.html),
   [pip: requirements files](https://pip.pypa.io/en/stable/user_guide/#requirements-files).

3. Copy Proxmox's CA certificate, so the app can verify it's talking to the real
   server:

   ```bash
   scp root@10.1.21.151:/etc/pve/pve-root-ca.pem ~/pve-root-ca.pem
   ```

4. Create an SSH key and authorise it for root on `pve`. The app uses it to run
   `virt-customize` (static IPs and Red Duck handouts):

   ```bash
   ssh-keygen -t ed25519
   ssh-copy-id root@10.1.21.151
   ssh -o BatchMode=yes root@10.1.21.151 true && echo "key login OK"
   ```

   The first connection adds `pve` to `~/.ssh/known_hosts`. The app refuses to
   SSH to a host it doesn't already know (paramiko `RejectPolicy` in
   `provisioner._ssh_client()`).

   This key is more powerful than the API token. Restrict it in
   `/root/.ssh/authorized_keys` on `pve` with `from="10.1.21.248"` and a forced
   command that only runs `virt-customize` (network reference §7.3).

   Sources: [ssh-keygen(1)](https://man.openbsd.org/ssh-keygen.1),
   [ssh-copy-id(1)](https://manpages.ubuntu.com/manpages/noble/man1/ssh-copy-id.1.html),
   [sshd(8): AUTHORIZED_KEYS FILE FORMAT](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT).

5. Settings. The app reads environment variables (`pond-sec/app/config.py`).
   For anything that has to be a Python object or a path that mustn't sit in the
   environment, it also loads a trusted Python file named by `POND_SETTINGS`
   (`pond-sec/app/__init__.py`, `create_app()`). The variables a launch needs
   are in the [launch guide](../user-guides/launch-guide.md), and the full list
   is in the [admin guide](../user-guides/pond-sec-admin-guide.md#settings).

## Phase 9: database, accounts and challenges

All from the repo root on the controller, with the venv active.

1. **Create the database.** This makes `the_pond.db` at the repo root, with every
   table and the three roles:

   ```bash
   python3 -m db.init_database
   python3 -m db.init_database --check     # read-only confirmation
   ```

2. **First accounts.** Edit the `ACCOUNTS` list in `db/seed_accounts.py`. It
   still holds our team's accounts; replace them with yours. Each entry is
   `(username, display name, role)`, with role `user`, `manager` or `sysadmin`.
   Include at least one `sysadmin`. Then:

   ```bash
   python3 -m db.seed_accounts --check     # preview
   python3 -m db.seed_accounts             # create; prints each temporary password once
   ```

3. **Challenges.** Review `vars/challenges/`. Delete the ones you won't run, and
   `lockedshields.yml`, which is a test file (known issue C3). Fix
   `kioptrixlevel1.yml` (known issue B6) and the `dc1.yml` VM order (known issue
   C8). Then load them. The seeder connects with the token in
   `group_vars/all.yml` and its own setting names:

   ```bash
   export THEPOND_PROXMOX_TOKEN_SECRET="$(ssh root@10.1.21.151 cat /root/pond-launcher.token)"
   export THEPOND_PROXMOX_CA_BUNDLE=$HOME/pve-root-ca.pem
   python3 -m db.init_database --seed-challenges
   ```

   Every template named in a challenge file must exist in Proxmox by then, or
   seeding stops at that challenge.

How to write your own challenges is in the
[challenge authoring guide](../challenges/authoring-guide.md).

## Phase 10: first launch and acceptance

1. Start the app as described in the [launch guide](../user-guides/launch-guide.md),
   step 4.
2. Sign in as your `sysadmin` account. You'll be made to change the temporary
   password first.
3. Run the launch guide's step 5 checks: the site loads, the Docker host serves
   handouts, a Red Duck test launch succeeds and tears down cleanly. If every
   launch fails, read the app log for `launch failed for instance`; the most
   likely first-time cause is phase 4.
4. Run the network acceptance tests N1–N15 in §9 of the
   [network reference](../networking/pond-proxmox-vmid-network-reference.md).
   Record the result, the date and who ran each one.
5. Register a test student account, approve it, launch and solve one challenge
   of each shape (single-VM target, multi-VM, Red Duck), and check the
   leaderboard.

---

## Site values

Every value in this guide that's specific to our site. Change them all together.

| Value | Ours | Set in |
|---|---|---|
| Proxmox host IP | `10.1.21.151` | `PROXMOX_HOST` (app), `group_vars/all.yml` (seeder), `THEPOND_PROXMOX_HOST` (importer) |
| Proxmox node name | `pve` | `PROXMOX_NODE`, `group_vars/all.yml`, playbook `pond_node` |
| Storage | `local-lvm`, volume group `pve` | `PROXMOX_STORAGE`, playbook `pond_storages`; `vg_name` in `provisioner.py` |
| Management VLAN | VLAN 10, `10.1.21.0/24`, gateway `.1` | Switch, `/etc/network/interfaces` |
| Lab VLAN | VLAN 20, `10.1.20.0/24`, gateway `.1` | Switch. No challenge clone uses it since the merge; `PROXMOX_LAB_GATEWAY` is no longer read |
| Controller | VM 9999, `10.1.21.248`, port 5000 | Your launch command |
| Docker host | VM 9997, `10.1.30.10` on `pondnc` | `POND_HANDOUT_BASE_URL`, `POND_DOCKER_HOST_IP`; every Red Duck challenge's `instructions:` |
| Red Duck internet ports | `80,443` (but `pondnc` has no uplink, so no internet) | `POND_WORKSTATION_INTERNET_PORTS` |
| SDN zone | `pondz` | `PROXMOX_SDN_ZONE`, playbook `pond_sdn_zone`, `provisioner.VNET_ZONE` |
| Pools | `pond-clones`, `pond-templates` | `PROXMOX_POOL`, `PROXMOX_TEMPLATE_POOL`, playbook |
| Template VMIDs | 10000–10002 (range 10000–10999) | playbook `pond_template_vmids`; `import_challenge.py` |
| Clone VMIDs | 304–399 | `DEFAULT_VMID_RANGE` in `provisioner.py` (code, not a setting) |
| Protected VMIDs | 300–303 (another project's VMs on our server) | `PROTECTED_VMIDS` in `provisioner.py`, `PROXMOX_PROTECTED_VMIDS`, playbook `pond_protected_vmids` (all three must agree) |
| Red Duck template name | `Kali-attacker` | `POND_HANDOUT_TEMPLATE`; challenge files |
| Handout user | `kali` | `POND_HANDOUT_USER` |
| Token | `pond@pve!launcher` | `PROXMOX_TOKEN_ID`; `group_vars/all.yml`; playbook |

If you have no other project's VMs on your server, you can empty the protected
list, but all three places must still agree.
