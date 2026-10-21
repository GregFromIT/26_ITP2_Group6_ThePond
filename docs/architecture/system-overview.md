# System overview

What The Pond is, what it's made of, and how a challenge runs from click to
teardown. Read this first. The other docs go deeper on each part.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. Open problems are listed in
[known issues](../known-issues.md).

---

## What it does

The Pond is a self-hosted capture-the-flag (CTF) training range, in the spirit
of Hack The Box or picoCTF, running on your own Proxmox server.

- A student registers, waits for an admin to approve the account, then picks a
  challenge.
- The platform clones a fresh virtual machine (or several) for that student,
  opens a browser console into it, and times the attempt.
- The student finds flags inside the machine and submits them through the web
  page. Points go on the leaderboards.
- When the student finishes or quits, the machines are destroyed.

The web application is called **Pond Sec**. "The Pond" is the whole system.

---

## Components

| Component | What it is | Where it runs | Code |
|---|---|---|---|
| **Proxmox server** (`pve`) | Proxmox VE 9 hypervisor. Holds the challenge templates and every student clone | Physical server, `10.1.21.151` | — |
| **Controller VM** | Runs the Pond Sec web app (Flask) and the database file | VM 9999 on `pve`, `10.1.21.248` | `pond-sec/`, `provisioner.py`, `db/` |
| **Database** | One SQLite file, `the_pond.db`: accounts, challenges, sessions, scores, audit log | On the controller, at the repo root by default | `db/*_models.py` |
| **Docker host** | Runs the containerised challenges that Red Duck clones attack, and serves their handout files over HTTP | VM 9997 on `pve`, `10.1.30.10` on the `pondnc` network | Not in the repo (see [`deployment/docker-host/`](../../deployment/docker-host/README.md)) |
| **Challenge templates** | Proxmox VM templates that sessions are cloned from | `pve`, VMIDs 10000+ | Defined in `vars/challenges/*.yml` |
| **Proxmox API token** | `pond@pve!launcher`, a least-privilege token the app uses for every Proxmox call | `pve` | `playbooks/pond_least_privilege.yml` |
| **Root SSH key** | Lets the app run `virt-customize` on `pve` to write settings into a clone's disk before it boots | Controller → `pve` | `provisioner.py` |
| **Submission pipeline** | Web upload, validation, review and publication of new challenges. Partly integrated (see [submission pipeline](../challenges/submission-pipeline.md)) | Controller | `challenge_ingestion/`, `pond-sec/app/uploads.py` |

---

## Roles

| Role in the app | Name in the database | Can |
|---|---|---|
| Student | `user` | Take challenges, appear on leaderboards |
| Moderator | `manager` | Class staff: unlock accounts, issue temporary passwords, close sessions, read the audit log, upload challenge submissions. Appears on leaderboards |
| System administrator | `sysadmin` | Everything a moderator can, plus approving registrations, changing roles, and reviewing and publishing submissions. Not on any leaderboard. In practice this person also runs the servers |

The full permission matrix is `PERMISSIONS` in `pond-sec/app/roles.py`.

---

## Challenge types

Every challenge is one YAML file in `vars/challenges/`, loaded into the database
by `python3 -m db.init_database --seed-challenges`. There are three working
shapes:

| Shape | VMs per session | What the student gets | Network | Examples |
|---|---|---|---|---|
| **Single-VM target** | 1 | The vulnerable machine itself, through its console only | A private network (SDN VNet) created for this session, with nothing else on it, so nothing can reach the VM over the network ([known issues](../known-issues.md) N8) | `metasploitable2` |
| **Multi-VM** | 2 or more | A Kali attacker console, plus one or more targets they attack from it | A private network (SDN VNet) created for this session only, with a firewall allowing just the ports in the challenge's `network_rules` | `dc1`, `kioptrixlevel1` |
| **Red Duck** | 1 | Red Duck, a Kali workstation, with the challenge's handout files on its Desktop. The challenge itself runs as a container on the Docker host | `pondnc`, shared with other Red Duck sessions and the Docker host. Each clone's own firewall lets it reach only the Docker host (TCP), not the other clones | 15 BSides Canberra 2025 and 32 BSidesSF 2024 challenges |

A Red Duck challenge is an ordinary single-VM challenge whose template is the Kali
image (`Kali-attacker`, VMID 10001). What makes it different is the first-boot
handout download, the container it talks to on the Docker host, and its network:
it's the only launch that doesn't get its own VNet, because it has to reach the
Docker host. The app recognises it by the template's name (`POND_HANDOUT_TEMPLATE`).

The database also knows two more execution types, `container_lab` and `offline`.
Neither has any challenges on the live system: `container_lab` needs a Docker
integration that was never built, and `offline` is for physical exercises. See
the [submission pipeline](../challenges/submission-pipeline.md).

---

## How a launch works

```
Student browser ──HTTPS──▶ Pond Sec (controller)
                              │
                              ├─ Proxmox API (8006, scoped token)
                              │     check datacenter firewall is on, zone is Simple
                              │     [not Red Duck] create session VNet
                              │     clone template → pool pond-clones
                              │     NICs → session VNet, firewall on (before boot)
                              │     start VM
                              │     [multi-VM] allow rules from network_rules
                              │
                              └─ SSH to pve as root (22)
                                    virt-customize: static IP, first-boot handout download
```

1. The student clicks **Launch**. The app checks the challenge is published and
   the student has no other session open, then writes a `challenge_instances`
   row with status `provisioning` (`themes.launch()`).
2. Before cloning, the app checks its own token holds nothing beyond what it
   needs (`proxmox._check_token_privileges()`), and that isolation can work: the
   SDN zone `pondz` is a Simple zone and the Proxmox datacenter firewall is on
   (`provisioner.check_isolation_preconditions()`). It refuses to launch if
   either check fails.
3. **Every launch except a lone Red Duck:** it creates a VNet
   `s<instance id in hex>` in `pondz` (`provisioner.create_session_vnet()`).
4. For each VM: it reserves a VMID in 304–399, makes a linked clone in pool
   `pond-clones`, writes settings into the stopped clone's disk over SSH if
   needed, moves every NIC onto the session VNet and turns on its firewall
   (inbound DROP; for Red Duck, also an outbound allowlist), then starts it
   (`provisioner.clone_and_start()`).
5. **Multi-VM only:** once all the VMs are up, it adds one inbound ACCEPT per
   `network_rules` entry.
6. The session becomes `running`. The tile on the theme page turns into a panel
   with a timer, a console button and a flag box.
7. **Console:** the app fetches a one-time VNC ticket and relays the WebSocket
   between the browser and Proxmox. The student never sees Proxmox's address or
   credentials (`themes.console()`, `themes.console_relay()`).
8. **Flags** are typed into the web page, normalised (trimmed, lower-cased),
   hashed with SHA-256 and compared with the stored hash (`scoring.submit_flag()`).
9. **Close:** capturing the last flag closes the session as **complete**. The
   close button records it as **abandoned**. Either way the result is saved
   first, then every VM is stopped and deleted, and the VNet with them
   (`themes._close()`).

If any step fails, the student sees "The environment could not be started", the
session is recorded as abandoned, and the reason goes to the app log as
`launch failed for instance <id>: …`. The exception is step 5: a refused
allow rule isn't caught ([known issues](../known-issues.md) L14).

Every network hop, with the exact API calls, is in §3.1 of the
[network reference](../networking/pond-proxmox-vmid-network-reference.md).

---

## Networks

| Network | Subnet | Used for |
|---|---|---|
| VLAN 10, management | `10.1.21.0/24` | Proxmox host, controller, admin access |
| VLAN 20, lab | `10.1.20.0/24`, gateway `10.1.20.1` | No challenge clones since every target got its own VNet. Other lab devices only |
| `pondnc` (SDN VNet in zone `pondz`) | `10.1.30.0/24` | Docker host (`10.1.30.10`) and Red Duck clones, each firewalled from the others |
| Session VNets `s<hex>` (zone `pondz`) | `10.10.10.0/24`, static IPs from the challenge file | One per session (except Red Duck), never shared |

Students should only ever reach the web app. Full detail, with the firewall
rules the upstream gateway must enforce, is in the
[network reference](../networking/pond-proxmox-vmid-network-reference.md).

---

## Where to go next

| You want to… | Read |
|---|---|
| Build a copy on your own hardware | [Build from scratch](../setup/build-from-scratch.md) |
| Start or stop the running system | [Launch guide](../user-guides/launch-guide.md) |
| Run classes, manage accounts, look after the server | [Admin guide](../user-guides/pond-sec-admin-guide.md) |
| Add your own challenges | [Challenge authoring guide](../challenges/authoring-guide.md) |
| Change the code | [Code map](pond-sec-code-map.md), [data model](../database/pond-sec-data-model.md), [design decisions](pond-sec-design-decisions.md), [testing](../testing/testing.md) |
| Know what's broken | [Known issues](../known-issues.md) |
