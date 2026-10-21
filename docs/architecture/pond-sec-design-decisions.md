# Design decisions

Why the system is the way it is, and what was turned down. Each entry says what
was decided, what else was considered, and what it costs. Read it before you
"fix" something that looks odd: several odd-looking things are deliberate.

Updated to `main` at `04998a1` with `fix/seeder-theme-flagformat` (`3dc6afc`)
merged. Decisions that no longer apply are listed at the
end under "Superseded".

---

## Platform

### Proxmox VE, QEMU VMs only

**Decided:** every challenge runs as a QEMU VM on Proxmox VE. No LXC.

**Why:** challenge content (Kali, Metasploitable, VulnHub boxes) ships as VM
images, and several challenges rely on kernel behaviour that a shared-kernel
container wouldn't isolate. Proxmox has a full REST API, so the whole lifecycle
can be driven from Python.

**What it costs:** every Proxmox call uses the `/qemu` endpoints, and
`_resolve_template_vmid()` only searches QEMU VMs. An LXC template would need
its own code path.

### Flask and SQLite, not Django

**Decided:** Flask, SQLAlchemy models, one SQLite file.

**Also considered:** Django, which brings auth, sessions, CSRF, an admin site
and migrations.

**Why not:** it would have been a mid-semester rewrite, and much of what Django
gives for free (CSRF, lockout, access control) is exactly what the course
assessed. Hand-written versions are visible and defensible.

**What it costs:** no migration tool. `db.init_database` adds missing tables but
won't change existing ones, so a schema change needs a one-off script (see the
[data model](../database/pond-sec-data-model.md)). SQLite is fine at class size;
"database is locked" under load is the first sign of outgrowing it.

### Linked clones by default

**Decided:** `PROXMOX_FULL_CLONE=0`: clones share their template's disk.

**Why:** near-instant and small, which suits sessions that last an hour.

**What it costs:** a template can't be deleted while linked clones exist, and
shouldn't be edited while sessions are running. Proxmox rejects a `storage`
argument on a linked clone, so it's only sent for full clones.

### The provisioner writes into clone disks over root SSH

**Decided:** static IPs and first-boot commands are written into a stopped
clone's disk with `virt-customize`, run on `pve` over SSH as root
(`provisioner.inject_instance_network()`, `inject_firstboot_command()`).

**Also considered:** cloud-init, which would need every template rebuilt with it;
or baking addresses into templates, which breaks once a template is shared.

**Why:** no template changes are needed, and the Proxmox API has no way to
write files into a guest disk.

**What it costs:** the SSH key is a stronger credential than the scoped API
token. It's the real privilege boundary between the controller and the
hypervisor (known issue N7). Templates that take a static IP must use
`ifupdown` with interface `eth0`.

---

## Security and accounts

### No email anywhere

**Decided:** no email addresses, reset links or mail server. Password recovery
is a temporary password that staff issue and hand over in person.

**Also considered:** emailed reset links. That was built, then taken out.

**Why:** it removes reset-link interception, mail spoofing, address
enumeration and mail-bombing in one go, and means the platform holds no contact
data it has no use for.

**What it costs:** recovery depends on staff being reachable. And staff can take
over any account below their rank, because a temporary password is a full
reset. That's inherent to staff-held recovery. It's audited, and can't be used
on an equal or senior account.

### Registration needs approval, and an unapproved account is told so

**Decided:** a new account starts as `pending` and can reach only a waiting page
until an admin approves it. Signing in with the right password says plainly
that approval is what's missing.

**Why:** if a refused sign-in looked like a wrong password, people would retry
into the three-strikes lockout. Approval sits with admins because it decides
who gets onto the platform at all, the same weight as granting a role.

**What it costs:** someone has to watch the queue. The staff console shows a
count. Confirming the password on an inert account is a small information leak,
accepted.

### Per-source throttle in front of the lockout

**Decided:** sign-ins are rate limited per IP *and* per account, in front of the
three-failures lockout.

**Why:** usernames are public on the leaderboards, so a lockout on its own lets
anyone lock out the whole class with three guesses each.

### Rate-limit counters in the database

**Decided:** `throttle_events` rows, not an in-memory dict.

**Why:** with several workers, an in-memory counter gives each one a full
allowance, and resets on every restart.

**What it costs:** a write per throttled attempt, and a table nothing prunes yet.

### Hand-written CSRF instead of Flask-WTF

**Decided:** `app/csrf.py`, about sixty lines: one token per session, required
on every POST, compared in constant time.

**Why:** one fewer dependency, and the mechanism is visible in the code.

**What it costs:** every form needs the hidden `_csrf` field added by hand.
Forgetting it shows up as a 400 error at runtime.

### The app refuses root and over-privileged Proxmox tokens

**Decided:** the app runs as its own user `pond@pve`, through a
privilege-separated token created by `playbooks/pond_least_privilege.yml`. It
refuses `@pam` tokens, verifies TLS, never touches the protected VMIDs, and
before every launch reads its own effective permissions, refusing to launch if
they exceed an allowlist (`proxmox._check_token_privileges()`).

**Also considered:** documenting least privilege only (the root token stayed
because nothing stopped it), or a start-up check only (misses a token widened
later).

**Why:** a root token turns any app bug into control of every VM on the server,
including other projects'.

**What it costs:** a new privilege means editing both the playbook and
`allowed_privileges()`. `pond-sec/tests/test_credentials.py` checks the two
agree. A too-wide token blocks launches until fixed; teardown is never blocked,
so running VMs are never orphaned.

### The console is relayed, never redirected

**Decided:** the browser opens a WebSocket to Pond Sec, which relays it to
Proxmox's `vncwebsocket` using the app's token and a one-time VNC ticket
(`themes.console_relay()`).

**Also considered:** redirecting the student to Proxmox's own console, or giving
each student a short-lived Proxmox user.

**Why:** students never learn Proxmox's address or hold a Proxmox credential,
and Proxmox's port 8006 never needs to be reachable from student machines.

**What it costs:** the web app carries every console's traffic. Each open
console holds a worker thread, so production needs gunicorn `--threads` (known
issue L12).

---

## Scoring

### Flags stored as hashes

**Decided:** the database keeps SHA-256 of the trimmed, lower-cased flag, never
the plaintext.

**Why:** a flag proves something only if it's secret. This platform teaches
people to dump databases.

**What it costs:** staff can't look a flag up in the database. The plaintext
lives in the challenge's YAML file (known issue C6). Flags are case-insensitive,
and changing the normalisation invalidates every stored hash.

### Double awards prevented by a database constraint

**Decided:** `UNIQUE (user_id, flag_id)` on `user_solves`.

**Why:** a check in Python leaves a gap between read and write, so two tabs can
both pass it. The constraint can't be raced.

### Scores computed on read

**Decided:** there's no points column on users. Every leaderboard sums
`user_solves.awarded_points` when it's read.

**Why:** a total that's computed can never drift from the ledger it comes from.

**What it costs:** an aggregation per page load. Fine at class size.

### Admins excluded from leaderboards; moderators compete

**Why:** an admin mid-table looks odd when that person runs the platform.
Moderators are usually students helping out, and hiding them would cost them
their own results. The rule is `_EXCLUDED_ROLE = "sysadmin"` in `scoring.py`.

### No score editing, from any role

**Decided:** nothing in the app edits a score, deletes a solve or deletes an
account.

**Why:** scores are the record of what a student did. A cheating case should be
a deliberate, recorded database action, not a button.

---

## Challenges

### Challenges are files, seeded into the database

**Decided:** each challenge is a YAML file in `vars/challenges/`, loaded by
`python3 -m db.init_database --seed-challenges`. Templates are referenced by
name and resolved to a VMID when seeded.

**Why:** challenges live in version control with the code. Adding one needs no
code change.

**What it costs:** the seeder only inserts, so changes to a loaded challenge
need SQL (known issue C1). Flags are in plain text in the repository.

### Themes are a free-text category

**Decided:** there's no theme table. A theme is whatever
`challenges.category` says. Any number of challenges per theme, and no weighting.

**Also considered:** the original design: six challenges per theme, with a
per-theme score weighting.

**Why:** the six-per-theme rule didn't fit real CTF releases, and nothing needed
weighting. `scoring.category_challenge_matrix()` still returns a `score` and a
`raw_total` separately, in case weighting comes back.

### One template serves many challenges

**Decided:** `vm_templates` holds each Proxmox template once.
`challenge_templates` assigns templates to challenges, with a per-challenge role,
boot order and static IP.

**Why:** one Red Duck template serves every Docker-hosted challenge, and the 47
Red Duck challenges shouldn't need 47 copies of Kali.

### Every launch isolated; Red Duck by firewall (20 Oct 2026, `4476c02`)

**Decided:** every VM launch gets its own SDN VNet with no gateway, except a
lone Red Duck. A lone Red Duck stays on `pondnc`, where the Docker host is, with
a VM firewall that drops all inbound traffic and allows outbound only to the
Docker host (TCP), DNS and listed internet ports, never to a private range.
Every clone's NICs and firewall are set before it boots, and the app refuses to
launch at all if the datacenter firewall is off or the zone isn't Simple, rather
than launch without isolation.

**Also considered:** the 8 Oct design, isolating multi-VM sessions only and
accepting a shared segment for everything else (see Superseded); and giving each
Red Duck its own VNet with a second NIC or a router VM to reach the Docker host,
which the 8 Oct network reference called design work rather than a
one-line change.

**Why:** the 8 Oct acceptance never covered single-VM targets like Metasploitable
2, which sat on VLAN 20 next to the server's management devices, and Red Ducks
could reach each other. A firewall per Red Duck closes the second gap without
changing how Docker-hosted challenges work.

**What it costs:**
- a single-VM target has nothing on its network to attack it from, so it can
  only be worked from its console (known issue N8);
- a Red Duck in a multi-VM session can't reach the Docker host (N9);
- the lone-Red-Duck test relies on the template's name (N10);
- Red Duck reaches the Docker host over TCP only, so UDP challenges need a code
  change;
- Red Ducks still share one L2 segment, so forged ARP is still possible
  (network reference §5.2).

### Docker-hosted challenges run on a shared, hand-run host

**Decided:** BSides-style challenges run as long-lived containers on one Docker
host, published on `pondnc`. Students reach them from a Red Duck clone, which
pulls its handouts on first boot.

**Also considered:** the `container_lab` type, where Pond would ask a Docker
server to start and stop a container per attempt (`CONTAINER_LAB_ADAPTER`).

**Why:** the shared host works now, needs no server-side integration, and suits
challenges designed for a shared event. The `container_lab` interface is in place
in case a per-attempt server is built.

**What it costs:** a student who crashes a container affects everyone using it.

### Static flags

**Decided:** each challenge has one fixed flag.

**Why:** simple, and enough for a club setting. `generate_flag()` in
`db/store.py` is written for per-session flags, but the flag would have to be
written into each clone.

**What it costs:** a student can share a flag.

---

## Interface

### No separate session page

**Decided:** a running challenge works inline on its own tile on the theme page.

**Why:** the client asked for it.

**What it costs:** a narrower working area.

### Tiles don't name the VM

**Decided:** the template name never appears on a challenge tile.

**Why:** a name like `forensics-w10` is a spoiler, and shows students our
infrastructure naming.

---

## Superseded

These decisions appeared in earlier versions of this file and no longer describe
the code:

| Old decision | What replaced it |
|---|---|
| `init-db` drops and rebuilds the schema from `schema.sql` | SQLAlchemy models, and `db.init_database`, which never drops anything |
| Points denormalised onto the user row | Scores computed from `user_solves` on read |
| Theme weighting applied on read | No weighting; themes are a category string |
| Proxmox adapter with `simulate` and `api` backends | One real backend; the simulator was removed. Tests use `tests/fake_proxmox.py` |
| VM uploads using a separate SQLAlchemy database, accepting eight image formats, fetching pasted URLs (`app/fetcher.py`) | The submission pipeline in `challenge_ingestion/`: one database, `.qcow2` only, private quarantine, no URL fetching |
| "Quarantine and sanitisation happen in Proxmox" | The pipeline's own quarantine, plus site-supplied verification and inspection integrations |
| Root Proxmox token on the "still open" list | Done: see "The app refuses root and over-privileged Proxmox tokens" |
| Isolation for multi-VM sessions only, with a shared segment for single-VM sessions accepted (8 Oct 2026) | "Every launch isolated; Red Duck by firewall" |
| A gateway (`PROXMOX_LAB_GATEWAY`) written into single-VM clones on the lab VLAN | No clone is on the lab VLAN any more; the setting is unused (known issue L15) |
| User-visible "category" wording on the theme page | "Theme" everywhere a student or staff member can see it (`aca7e40`); the code and database still say `category` |
