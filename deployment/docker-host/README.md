# Docker host (VM 9997)

The Docker host runs the containerised challenges that students reach from
their Red Duck workstation, and serves each challenge's handout files. This
folder is where its configuration lives, so a copy of The Pond can be rebuilt
from the repository alone.

> **INCOMPLETE: the configuration files aren't here yet.** They exist only on the
> live Docker host. Copy each file in the table below off VM 9997 into this
> folder, under the name shown, before handover. Until then, a new team can't
> rebuild the Docker host.
>
> The descriptions below were reconstructed from the project history, not
> checked against the live machine. Where a copied file disagrees with them, the
> file is right: update this README.

---

## What it does

| Job | How | Address / port |
|---|---|---|
| Hosts challenges | One Docker container per hosted challenge, each published only on the `pondnc` address | `10.1.30.10:31301–31331` |
| Serves handouts | A static file server (`pond-files`) over `/opt/pond-files/<challenge>/` | `http://10.1.30.10:8000` |
| Gives Red Duck clones an address | DHCP on the `pondnc` NIC only: `10.1.30.100–250`, no gateway, no DNS | `pondnc` |
| Keeps containers off the management network | A firewall rule in Docker's `DOCKER-USER` chain, made permanent by a systemd unit | — |

The web app never talks to this host. A Red Duck clone downloads its handouts
from `pond-files` on first boot: the app writes a `wget` command into the
clone's disk (`pond-sec/app/themes.py`, `_handout_command()`). The student then
connects to the challenge's port from inside Red Duck. Both the address and the
port are written into each challenge's `instructions:`.

**What a Red Duck clone may send to this host.** Each Red Duck clone has its own
VM firewall, set by the app before it boots (`themes._workstation_egress()`;
[network reference](../../docs/networking/pond-proxmox-vmid-network-reference.md)
§5.2). Towards this host it allows **TCP on any port**, and nothing else:

- a challenge served over **UDP** can't be reached. Supporting one means adding
  a UDP rule in `_workstation_egress()`;
- `ping 10.1.30.10` from Red Duck fails, even when everything works. Test with
  `nc -zv 10.1.30.10 8000` instead;
- DHCP from this host still works (the firewall allows it).

The address the firewall allows is `POND_DOCKER_HOST_IP` (default `10.1.30.10`).
If you give this host a different `pondnc` address, change that setting and
`POND_HANDOUT_BASE_URL` together.

## The VM

| Setting | Value |
|---|---|
| VMID | 9997 (keep it out of the `pond-clones` and `pond-templates` pools) |
| OS | Ubuntu Server (VERIFY the version) |
| `net0` | `vmbr0`, management (VLAN 10), for administration and pulling images |
| `net1` | `pondnc`, static `10.1.30.10/24`, **no gateway** (interface `ens19` on ours) |

The `pondnc` address has no gateway on purpose. The default route stays on the
management NIC, so the host can still pull packages and images, while nothing
on `pondnc` gets routed anywhere through it.

---

## Files to copy in

| Copy from VM 9997 | Save here as | What it is |
|---|---|---|
| `/etc/netplan/60-pondnc.yaml` (or the `ens19` stanza of `/etc/network/interfaces`, whichever the host uses) | `netplan-60-pondnc.yaml` | Static `10.1.30.10/24` on the `pondnc` NIC, no gateway |
| `/etc/dnsmasq.d/pondnc.conf` | `dnsmasq-pondnc.conf` | DHCP for Red Duck clones |
| The `pond-files` service unit (find it with `systemctl list-units \| grep -i pond`) | `pond-files.service` | The handout file server on `:8000` |
| The firewall rule script and its unit (`DOCKER-USER` chain) | `docker-user-isolation.sh`, `docker-user-isolation.service` | Stops containers reaching `10.1.21.0/24` |
| `/opt/pond-deploy.sh` | `pond-deploy-cbr25.sh` | Builds and runs the 15 BSides Canberra 2025 containers and stages their handouts |
| Anything else in `/etc/systemd/system/` or `/opt/` that you added for The Pond | same name | — |

`tools/pond-deploy-sf24.sh` (BSidesSF 2024) is already in the repo, but
check it against the live copy: the deploy scripts were edited on the live
host after `main` was last updated.

When copying, **leave out anything secret**: keys, tokens, passwords.

### What the reconstructed versions look like

**DHCP**, `/etc/dnsmasq.d/pondnc.conf`:

```
interface=ens19
bind-interfaces
port=0
dhcp-range=10.1.30.100,10.1.30.250,12h
dhcp-option=3
```

- `port=0` turns DNS off, so dnsmasq only does DHCP.
- `dhcp-option=3` with no value sends no default gateway, so Red Duck clones get
  no route out of `pondnc`.
- `interface` and `bind-interfaces` keep it off the management NIC.

Source: [dnsmasq man page](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html).

**Static address**, netplan:

```yaml
network:
  version: 2
  ethernets:
    ens19:
      dhcp4: false
      addresses: [10.1.30.10/24]
```

Apply with `sudo netplan try`, which rolls back if you lose the connection.
Source: [Netplan docs](https://netplan.readthedocs.io/en/stable/).

**Isolation check.** From inside any challenge container, the Proxmox host
must be unreachable:

```bash
sudo docker exec <container> timeout 3 bash -c 'echo > /dev/tcp/10.1.21.151/8006' \
  && echo "REACHABLE - bad" || echo "blocked - good"
```

`/dev/tcp/<host>/<port>` is a bash feature that opens a TCP connection
([bash manual: Redirections](https://www.gnu.org/software/bash/manual/bash.html#Redirections)).
Docker's own guidance on the `DOCKER-USER` chain:
[Docker: packet filtering and firewalls](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

---

## Building it

1. Create the VM with the two NICs above, then install Ubuntu Server. During the
   install, configure only the management NIC.
2. Give the `pondnc` NIC its static address (netplan file above).
3. Install Docker Engine from Docker's repository:
   [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/).
4. `sudo apt install dnsmasq`, install `dnsmasq-pondnc.conf` as
   `/etc/dnsmasq.d/pondnc.conf`, then `sudo systemctl restart dnsmasq`.
5. Install and enable the isolation rule and its unit, then run the isolation
   check above once containers are up.
6. Create `/opt/pond-files`, install `pond-files.service`, then enable and start
   it.
7. Run the deploy scripts as root:
   - `pond-deploy-cbr25.sh` for BSides Canberra 2025;
   - `tools/pond-deploy-sf24.sh` for BSidesSF 2024. It clones
     `https://github.com/BSidesSF/ctf-2024-release` into `/opt/bsidessf24`.

   Each script builds one image per challenge, runs it with
   `--restart unless-stopped`, publishes it only on `10.1.30.10`, and copies
   the handouts into `/opt/pond-files/<challenge>/`.
8. Check from the controller: `curl -sS -o /dev/null -w '%{http_code}\n' http://10.1.30.10:8000/`
   should print an HTTP status code (200 or 404), not `000`.

Some BSides Canberra 2025 challenges are built on DownUnderCTF's `nsjail` image,
which needs `--privileged`. Bring one container up first and read
`docker logs` before deploying the rest.

## Adding a Docker-hosted challenge

See "Red Duck challenges" in the
[challenge authoring guide](../../docs/challenges/authoring-guide.md). In short:

1. Pick an unused host port after 31331.
2. Add a `docker run … -p 10.1.30.10:<host port>:<container port>` line, and a
   handout staging line, to a deploy script.
3. Write the challenge's YAML with the port in its `instructions:`.
4. Seed it.

`tools/gen_bsides_challenges.py` and `tools/gen_bsidessf24_challenges.py` show
how to generate both the YAML files and the deploy script from an upstream CTF
release.
