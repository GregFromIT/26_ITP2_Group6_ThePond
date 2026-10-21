# Challenge authoring guide

How to add your own challenges to The Pond: choose a shape, build what it runs
on, write its file, load it, and test it.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. The only working way to add a launchable challenge is a YAML
file plus the seeder, described here. The web upload route can't yet publish a
VM or Red Duck challenge (see [submission pipeline](submission-pipeline.md)).

---

## 1. Pick a shape

| Shape | Use it when | Student gets | Network |
|---|---|---|---|
| **Single-VM target** | The vulnerable machine *is* the challenge, and the student works on it from its own console (local privilege escalation, forensics, a locked-down shell) | The target's console | A private network per session with nothing else on it. **The target can't be reached over the network at all** |
| **Multi-VM** | Students attack one or more targets over the network from an attacker machine | A Kali console; the targets are reachable only on the ports you allow | A private network per session, with a firewall |
| **Red Duck** | The challenge is a network service, a web app, or just files. It runs in a container on the Docker host | Red Duck (Kali) with the handouts on its Desktop | `pondnc`, shared with other students, but each clone's firewall lets it reach only the Docker host |

Every shape is isolated from other students' sessions. The difference that
matters when you choose is the single-VM row: if students are meant to attack
the target over the network, it has to be multi-VM, with a Kali attacker.

---

## 2. The challenge file

Each challenge is one file, `vars/challenges/<name>.yml`. These are the keys the
seeder reads (`db/init_database.py`, `seed_challenges_from_yaml()`):

| Key | Required | Meaning |
|---|---|---|
| `challenge` | yes | The challenge's name. It's shown on the tile, used as the challenge's identity in the database, and names the handout folder (`~/Desktop/<challenge>` and `http://10.1.30.10:8000/<challenge>/`). Keep it short, lower-case, without spaces |
| `category` | no | The theme it appears under. Free text, for example `web`, `crypto`, `pwn`. Default `general` |
| `difficulty` | no | Shown on the tile, for example `easy`. Free text. Shown as "Entry" if missing |
| `description` | no | One or two sentences on the tile. Default "Seeded from vars/challenges/<file>" |
| `instructions` | no | The full briefing in the tile's **Instructions** section. Default "See the challenge brief for connection details." |
| `vm_templates` | yes | One entry per VM: `name` (the Proxmox template name, exactly), `role` (free text, for example `attacker`, `target`), optional `static_ip` |
| `network_rules` | multi-VM only | Each entry: `from` (role), `to` (role), `port`, `protocol` (`tcp` or `udp`) |
| `flag` | no | The flag in plain text. Hashed when seeded |
| `points` | if `flag` is set | What the flag is worth. The seeder stops with an error if a flag has no points |

These keys appear in many existing files but **do nothing**:
`vmid_range_start`, `vmid_range_end` and `default_ttl_hours` (known issue L7,
L3). Leave them out of new files.

### Writing the text

- **`description`:** what the challenge is, at a glance. No spoilers.
- **`instructions`:** everything the student needs to start: what to open, where
  the challenge is (address and port), where the files are, and **the flag
  format**. The flag box on the page says `flag{...}` whatever the format (known
  issue W2), so the instructions are the only place a student learns the real
  one.

A good pattern, taken from our Red Duck challenges:

```yaml
instructions: |
  Open the console to use your attacker machine.
  Connect to the challenge:  nc 10.1.30.10 31303
  Download:  curl -O http://10.1.30.10:8000/wordgame/wordgame.tar.gz
  Flag format: skbdg{...}
```

The `|` keeps line breaks; each line is shown as its own paragraph
(`theme_detail.html`).

### Flags and points

- One `flag:` per file gives one flag, worth `points:`. Capturing it completes
  the challenge.
- Flags are compared after trimming whitespace and lower-casing
  (`scoring.hash_flag()`), so **flags are case-insensitive**. Don't design a
  challenge whose answer depends on case.
- The database keeps only a SHA-256 hash, but the YAML file holds the flag in
  plain text, and the repository is public (known issue C6). Keep real flags out
  of a public repository.
- Points are copied onto each solve when it's awarded (`user_solves.awarded_points`).
  Changing a challenge's points later doesn't change anyone's existing score.

---

## 3. Single-VM target

1. Build the target as a template (see phase 5 of
   [build from scratch](../setup/build-from-scratch.md)). Name it, for example
   `mytarget`.
2. Put every NIC of the template on `pondtpl`. At launch, the app moves them all
   onto a new session network that holds only this VM, so the template's bridge
   doesn't matter beyond passing the token playbook's checks.
3. Plant the flag inside the template (for example in `/root/flag.txt`), then
   convert it to a template.
4. Write the file:

   ```yaml
   challenge: mytarget
   category: boot2root
   difficulty: easy
   description: |
     An old web server nobody patched.
   instructions: |
     Use the console to work on the machine.
     Flag format: pond{...}
   vm_templates:
     - name: mytarget
       role: target
   flag: "pond{example_flag}"
   points: 200
   ```

The student reaches this VM **only through its console**: there's nothing else
on its network. That suits a challenge solved from inside the machine. For one
meant to be attacked over the network, such as our `metasploitable2`, use the
multi-VM shape instead and add a `Kali-attacker` (known issue N8).

A `static_ip` on a single-VM target is written without a gateway, and does
nothing useful.

---

## 4. Multi-VM (attacker plus target)

The session gets its own network, `s<instance id in hex>`, with no router and no
DHCP. Every VM therefore needs a `static_ip`.

```yaml
challenge: mylab
category: boot2root
difficulty: medium
description: |
  A Drupal site with a known flaw.
instructions: |
  Your Kali machine is 10.10.10.10. The target is 10.10.10.20.
  Only ports 22 and 80 on the target are reachable.
  Flag format: pond{...}
vm_templates:
  - name: DC-1
    role: target
    static_ip: "10.10.10.20"
  - name: Kali-attacker
    role: attacker
    static_ip: "10.10.10.10"
network_rules:
  - {from: attacker, to: target, port: 80, protocol: tcp}
  - {from: attacker, to: target, port: 22, protocol: tcp}
flag: "pond{example_flag}"
points: 300
```

What happens at launch (`themes.launch()`, `provisioner.clone_and_start()`):

1. Each VM is cloned, its static IP is written into its disk as a Debian
   `ifupdown` config for `eth0`, every NIC is moved onto the session network,
   its firewall is switched on with inbound DROP, and it's started.
2. Once all are up, each `network_rules` entry becomes one inbound ACCEPT on the
   **`to`** VM, from the **`from`** VM's IP.

Rules for multi-VM files:

- **List the attacker last.** The console opens on the last VM cloned. VMs are
  cloned in `boot_order`, but the seeder gives them all the same `boot_order`,
  so the order in the file decides (known issue C8). Our own `dc1.yml` gets this
  wrong.
- **Give every VM a different IP** in `10.10.10.0/24`. The same addresses can be
  reused in every challenge, because each session is its own network. Our own
  `kioptrixlevel1.yml` gives both VMs the same IP (known issue B6).
- **Every template needs `eth0` managed by `ifupdown`.** For Kali, check that
  NetworkManager doesn't take `eth0` over (see the Kali note in phase 5 of the
  build guide).
- **Nothing can connect *to* the attacker.** Replies to the attacker's own
  connections come back, but a target can't open a new connection to it. Reverse
  shells and payload callbacks fail unless you add a rule from target to
  attacker on a listener port (known issue N4).
- A rule naming a role the challenge doesn't have, or whose `from` VM has no
  `static_ip`, is skipped without an error.
- Every NIC is moved onto the session network, but only `eth0` gets the static
  IP. A second NIC is harmless but has no address.
- **No handouts.** A Red Duck attacker inside a multi-VM session can't reach the
  Docker host, so files staged for it there never arrive (known issue N9). Put
  any files the student needs inside the template instead.

---

## 5. Red Duck challenges (Docker-hosted)

The challenge runs as a container on the Docker host (`10.1.30.10`). The
student gets a Red Duck clone (template `Kali-attacker`) with the handouts
already on its Desktop, and connects to the container from there.

1. **Put the container on the Docker host.** Pick the next unused host port
   (ours run 31301–31331). Publish it only on the `pondnc` address:

   ```bash
   docker build -t pond-<name> <path to the challenge's Dockerfile folder>
   docker run -d --name pond-<name> --restart unless-stopped \
     -p 10.1.30.10:<host port>:<container port> pond-<name>
   ```

   Putting the IP in `-p` publishes the port only on that address, not on the
   management NIC ([Docker: port publishing](https://docs.docker.com/engine/network/port-publishing/)).
   Add the lines to a deploy script, so the host can be rebuilt
   ([Docker host README](../../deployment/docker-host/README.md)).
   File-only challenges need no container.

2. **Stage the handouts** in `/opt/pond-files/<challenge>/` on the Docker host,
   where `<challenge>` is exactly the file's `challenge:` value. On first boot the
   Red Duck clone downloads that folder into `/home/kali/Desktop/<challenge>/`
   (`themes._handout_command()`, ten tries, three seconds apart). If the folder
   doesn't exist, the Desktop is just empty: nothing reports the failure.

3. **Write the file:**

   ```yaml
   challenge: mychallenge
   category: web
   difficulty: easy
   description: |
     A login page with a weakness.
   instructions: |
     Open the console to use your attacker machine.
     Open the challenge:  http://10.1.30.10:31332/
     Your files are in ~/Desktop/mychallenge
     Flag format: CTF{...}
   vm_templates:
     - name: Kali-attacker
       role: attacker
   flag: "CTF{example_flag}"
   points: 100
   ```

**List exactly one VM, the template named in `POND_HANDOUT_TEMPLATE`**
(default `Kali-attacker`). That's how the app recognises a Red Duck challenge.
It then gives the clone handouts, and keeps it on `pondnc`, where the Docker
host is, instead of a private session network. With a second VM, or any other
template name, the clone gets a private network and can't reach the Docker host
(known issue N10).

What Red Duck can reach (its firewall, set before boot; network reference §5.2):

- the Docker host (`POND_DOCKER_HOST_IP`) on **any TCP port**. UDP challenges
  won't work, and neither will `ping`;
- nothing else private: not other students, not the lab or management networks;
- DNS and TCP 80/443 to the internet, but only once `pondnc` has a routed
  uplink, which it doesn't have yet. Don't write a challenge that needs the
  internet.

**Importing a whole CTF release.** `tools/gen_bsides_challenges.py` (BSides
Canberra 2025) and `tools/gen_bsidessf24_challenges.py` (BSidesSF 2024) read
each challenge's metadata from an upstream release and write both the YAML files
and the Docker host deploy script. Copy one as the starting point for a new
release.

---

## 6. Load it

From the repo root on the controller, with the venv active. The seeder uses the
token in `group_vars/all.yml` and its own setting names:

```bash
export THEPOND_PROXMOX_TOKEN_SECRET="$(ssh root@10.1.21.151 cat /root/pond-launcher.token)"
export THEPOND_PROXMOX_CA_BUNDLE=$HOME/pve-root-ca.pem
python3 -m db.init_database --seed-challenges
```

New challenges are published immediately. If a template name doesn't match a
VM in Proxmox, seeding stops with `no template named …`. Challenges loaded
before that one stay loaded.

A new template must also be added to the token's template pool, or launching it
fails. Re-run the token playbook with its VMID added to `pond_template_vmids`
(phase 7 of the build guide).

---

## 7. Changing or removing a challenge

**The seeder only adds what's missing** (known issue C1). For a challenge that's
already loaded, re-seeding:

- changes nothing about the challenge itself: not its text, theme or difficulty;
- adds any VM whose `role` it hasn't seen, but doesn't change an existing role's
  template or `static_ip`;
- **adds a changed `flag` as a second flag, and keeps the old one.** The student
  then has to find both to complete the challenge. Don't change a flag in the
  file and re-seed.

Until the seeder can update, change the database directly.

**Back up `the_pond.db` first** (see "Backups" in the
[admin guide](../user-guides/pond-sec-admin-guide.md)), then use `sqlite3`, or
Python's `sqlite3` module, from the repo root.

| Change | SQL |
|---|---|
| Text | `UPDATE challenges SET description = '…', instructions = '…', difficulty = '…', category = '…' WHERE title = 'mychallenge';` |
| Hide from students | `UPDATE challenges SET status = 'draft' WHERE title = 'mychallenge';` (only `published` challenges are listed or launchable) |
| Points | `UPDATE challenge_flags SET points = 250 WHERE challenge_id = (SELECT challenge_id FROM challenges WHERE title = 'mychallenge');` (existing solves keep what they were awarded) |
| Flag | Set `flag_hash` to the new hash, computed exactly as the app does: `python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].strip().lower().encode()).hexdigest())" 'CTF{new_flag}'` |

Change the YAML file to match as well, so a fresh build gets the same result.

Hide a challenge rather than deleting it. Deleting is refused while any session
record points at the challenge (`challenge_instances.challenge_id` has no
cascade). Once those are gone, deleting it would also delete its flags and every
solve of them (`ON DELETE CASCADE` on `challenge_flags` and `user_solves`),
which changes students' scores.

---

## 8. Test it

1. Sign in as a test student account.
2. Open the challenge's theme. Check the tile's text and the **Instructions**.
3. Launch it. On `pve`, watch the clone appear:
   `watch -n2 'qm list | grep -v " stopped"; pgrep -a virt-customize'`.
4. Open the console. Check you land on the right VM, and that the address, ports
   and handouts are where the instructions say. For a multi-VM challenge, check
   from the attacker that the target answers on the allowed ports and nothing
   else (network reference §9, N7).
5. Solve it and submit the flag. Check the points.
6. Check the session closed itself, and the VMs are gone from `qm list`.

If the launch fails, the student sees only a generic message. The reason is in
the app's log, on a line starting `ERROR in themes: launch failed for instance`
(see the [admin guide](../user-guides/pond-sec-admin-guide.md) for where the log
is). Check the Proxmox task log too (`pve` → **Tasks** at the bottom of the web
UI), then look for a leftover clone ([launch guide](../user-guides/launch-guide.md), "Clearing a
leftover clone").
