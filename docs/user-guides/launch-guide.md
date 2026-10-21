# Launching The Pond

How to bring The Pond up from cold: power on the Proxmox server, start the two
supporting VMs, and launch the web app. Follow it top to bottom after any
power-off or reboot. For building a new system, see
[build from scratch](../setup/build-from-scratch.md); for stopping it cleanly,
see "Starting and stopping" in the [admin guide](pond-sec-admin-guide.md).

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged.

> **DRAFT.** Still to resolve before handover: every `<FILL IN>` and every
> **TO UPDATE** note.

---

## What you are starting

| Machine | Address | What it does | Starts on boot? |
|---|---|---|---|
| Proxmox server (`pve`) | `10.1.21.151` | The hypervisor. Everything else runs on it. | — |
| Docker host (VM `9997`) | `10.1.30.10` | Runs the container challenges and serves handout files on port 8000. | **No — start by hand** |
| Controller VM (VM `9999`) | `10.1.21.248` | Runs the Flask web app on port 5000. | **No — start by hand** |

Start them in that order. The web app talks to Proxmox on every launch. Red Duck
challenges also need the Docker host, so a missing Docker host only shows up
when a student clicks **Launch**.

You need:
- the Proxmox root password
- a login on the controller VM
- network access to VLAN 10 (`10.1.21.0/24`)

---

## 1. Turn on the Proxmox server

**If the server is already running and you only need a restart:** reboot it from
the web UI (step 2). Select node **`pve`** in the left-hand tree, then click
**Reboot** at the top right.

**If the server is off:** press the power button on the front of the physical
server. The web UI can't start a machine that's switched off, so the button is
the only way in this case.

- Server location: `<FILL IN — room / rack>`

Give it a few minutes to boot. It's ready when the web UI in step 2 loads.

---

## 2. Log in to the Proxmox web UI

1. Browse to **`https://10.1.21.151:8006`**.
2. Your browser will warn about the certificate. That's expected: Proxmox signs
   its certificate with its own CA, not a public one. Accept the warning and
   carry on.
3. Log in:

   | Field | Value |
   |---|---|
   | User name | `<FILL IN>` |
   | Password | `<FILL IN>` |
   | Realm | **Linux PAM standard authentication** |

   Realm matters. `root` is a Linux account on the server, so it only works
   under the **Linux PAM** realm, not **Proxmox VE authentication server**.

4. Dismiss the "No valid subscription" pop-up. It's informational; the
   no-subscription repository is in use on purpose.

Source: Proxmox VE Administration Guide, [Graphical User
Interface](https://pve.proxmox.com/pve-docs/chapter-pve-gui.html) and [User
Management → Authentication
Realms](https://pve.proxmox.com/pve-docs/chapter-pveum.html#pveum_authentication_realms).

---

## 3. Start the Docker host and the controller VM

Neither VM starts on boot, so start both by hand, Docker host first.

**From the web UI:** select the VM in the left-hand tree, then click **Start** at
the top right. Do this for:

1. **`9997`** (Docker host)
2. **`9999`** (controller)

**Or from the node shell** (select `pve` → **>_ Shell**):

```bash
qm start 9997
qm start 9999
qm list | grep -E '9997|9999'    # both should say "running"
```

Source: the [`qm` manual](https://pve.proxmox.com/pve-docs/qm.1.html), `qm start`
and `qm list`.

Wait about a minute for both to finish booting before step 4.

---

## 4. Launch the web app

You need two terminals:

| Terminal | Where it runs | Purpose |
|---|---|---|
| **Terminal 1** | Controller VM (`10.1.21.248`) | Runs the web app. Leave it open. |
| **Terminal 2** | Proxmox server (`10.1.21.151`) | Watches VMs being cloned while you test. |

### Terminal 1 — controller VM

SSH in (`ssh <FILL IN>@10.1.21.248`), or open the controller's console in the
web UI. Then:

```bash
cd ~/26_ITP2_Group6_ThePond
source .venv/bin/activate

export PROXMOX_TOKEN_ID='pond@pve!launcher'
export PROXMOX_TOKEN_SECRET="$(ssh root@10.1.21.151 cat /root/pond-launcher.token)"
export PROXMOX_CA_BUNDLE=$HOME/pve-root-ca.pem
export THEPOND_PROXMOX_SSH_USER=root
export THEPOND_PROXMOX_ALLOW_ROOT_SSH=1
export THEPOND_PROXMOX_SSH_KEY=$(ls ~/.ssh/id_ed25519 ~/.ssh/id_rsa 2>/dev/null | head -1)
echo "key=$THEPOND_PROXMOX_SSH_KEY secret=${#PROXMOX_TOKEN_SECRET} chars"

cd pond-sec && flask --app wsgi run --host 0.0.0.0 --port 5000
```

> **TO UPDATE:** confirm these are still current on the controller:
> - the repo path `~/26_ITP2_Group6_ThePond` and the venv at `.venv`;
> - the CA file at `~/pve-root-ca.pem` (`WSGI_Files/.env.example` uses
>   `/etc/pond/pve-root-ca.pem` instead, so pick one);
> - the token file `/root/pond-launcher.token` on `pve`. If it has been
>   shredded, this line needs replacing with wherever the secret now lives.

**Check the `echo` line before going further.** It should print:

```
key=/home/<user>/.ssh/id_ed25519 secret=36 chars
```

- `secret=36 chars` means the token secret loaded. Proxmox token secrets are
  UUIDs, which are 36 characters long.
- `secret=0 chars` means the `ssh` that reads the token file failed. Run
  `ssh -o BatchMode=yes root@10.1.21.151 true`. If it asks for a password or
  fails, the controller's SSH key isn't authorised on `pve`.
- An empty `key=` means there's no SSH key in `~/.ssh`. Every Red Duck and
  multi-VM launch will then fail with "could not be started"
  (`provisioner.py`, `_ssh_client()`). Single-VM challenges still work, because
  they never use SSH.

The app also refuses to SSH to a host it doesn't already know. `pve`'s host key
must be in the controller's `~/.ssh/known_hosts` (`provisioner.py`,
`RejectPolicy`). The `ssh root@10.1.21.151 cat …` line above adds it the first
time, when it asks "Are you sure you want to continue connecting?". Answer
`yes`.

What each line is for:

| Line | Why |
|---|---|
| `cd` / `source .venv/bin/activate` | The commands below run from the repo root, inside the project's virtualenv. |
| `PROXMOX_TOKEN_ID` / `PROXMOX_TOKEN_SECRET` | The least-privilege API token the app clones and destroys VMs with (`pond-sec/app/config.py`). The secret is read straight off `pve`, so it never sits in a file on the controller. |
| `PROXMOX_CA_BUNDLE` | Proxmox's own CA certificate, so the app verifies it's talking to the real server (`config.py`). |
| `THEPOND_PROXMOX_SSH_*` | SSH login the app uses to run `virt-customize` on `pve`. It's only needed for clones that get a static IP (multi-VM challenges) or a first-boot handout download (Red Duck) (`pond-sec/app/proxmox.py`, `clone_and_start()`). |
| `--host 0.0.0.0` | `flask run` only listens on `127.0.0.1` by default. Without this, no other machine can reach the site. |

Don't add `--debug`. The Werkzeug debugger hands an interactive Python console
to anyone who triggers an error (`pond-sec/wsgi.py`).

`flask run` is Flask's development server, and it's how The Pond has been run
so far. For a production deployment, run it under gunicorn with
`RANGE_ENV=production` and a `FLASK_SECRET_KEY`, behind a reverse proxy, with
`--threads` so open consoles don't use up every worker:

```bash
RANGE_ENV=production FLASK_SECRET_KEY=... gunicorn 'wsgi:app' -b 127.0.0.1:8000 --workers 2 --threads 50
```

The command comes from the `pond-sec/wsgi.py` docstring, with `--threads` added
following [flask-sock: Gunicorn](https://flask-sock.readthedocs.io/en/stable/web_servers.html):
each open WebSocket holds one thread (known issue L12). Set `TRUSTED_PROXIES` to
the number of proxies in front, or the rate limits can be bypassed.

> **DECISION:** whether to move to gunicorn and a reverse proxy before handover,
> and the right `--workers`/`--threads` numbers for your class size. Neither
> has been tested on the live system.

Sources: [Flask CLI — run](https://flask.palletsprojects.com/en/stable/cli/),
[Bash manual — Shell Parameter
Expansion](https://www.gnu.org/software/bash/manual/html_node/Shell-Parameter-Expansion.html)
(`${#var}` gives a variable's length), [`ssh_config` —
BatchMode](https://man.openbsd.org/ssh_config#BatchMode).

### Read the startup log

Flask's output in Terminal 1 should end with:

```
 * Running on http://10.1.21.248:5000
```

If you see any line starting `WARNING in __init__: Proxmox settings:`, one of the
exports didn't take. Press **Ctrl+C**, fix it, and run the block again. The app
starts anyway in development, but every launch will fail.

A clean log doesn't prove everything is set. The startup check only covers the
token ID, token secret, pool and CA file (`pond-sec/app/proxmox.py`,
`settings_problems()`). It doesn't check the SSH settings. A missing SSH setting
only shows up when a Red Duck or multi-VM launch fails, which is why the test
launch in step 5 uses a Red Duck challenge.

### Terminal 2 — Proxmox server

Open a shell on `pve` (`ssh root@10.1.21.151`, or **`pve` → >_ Shell** in the web
UI):

```bash
watch -n2 'qm list | grep -v " stopped"; pgrep -a virt-customize'
```

This refreshes every 2 seconds and shows:
- every VM that's running, which is where new clones appear; and
- any `virt-customize` process, which is the app preparing a clone before it boots.

Leave it running while you do the checks in step 5.

Before that, in a second shell on `pve`, confirm the datacenter firewall is on:

```bash
pvesh get /cluster/firewall/options     # must include: enable 1
```

If it doesn't say `enable` `1`, every launch will be refused. Turn it on with
`pvesh set /cluster/firewall/options --enable 1` (build guide, phase 4, has the
precautions). Source: [Proxmox firewall](https://pve.proxmox.com/pve-docs/chapter-pve-firewall.html).

Sources: [`watch(1)`](https://man7.org/linux/man-pages/man1/watch.1.html),
[`pgrep(1)`](https://man7.org/linux/man-pages/man1/pgrep.1.html).

---

## 5. Check it works

1. **Site loads.** Browse to **`http://10.1.21.248:5000`** from another machine.
2. **Docker host is serving handouts.** In a spare terminal on the controller:
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' http://10.1.30.10:8000/
   ```
   - Any HTTP code (200, 404, …) means the handout server is up.
   - `000` or "connection refused" means VM 9997 is up but its file server isn't.

   `-w '%{http_code}'` prints only the response status ([curl
   manual](https://curl.se/docs/manpage.html#-w)).
3. **No leftovers from before the restart.** Sign in with an admin account, open
   **Staff**, then **Manage sessions** (`/admin/sessions`).
   - **Running now:** any session that was open when the server went down still
     shows here, because its database row still says `running`. Its VM was shut
     down by the reboot. Click **Close** on each one. Until you do, that student
     can't launch anything else, because the platform only allows one challenge
     at a time.
   - **Machines without a session:** clones the database thinks are up, but that
     no session points at. Usually a teardown failed. The page has no button for
     these. Destroy the VM in Proxmox by hand (see "Clearing a leftover clone"
     below).

   > **TO UPDATE:** how is a "Machines without a session" row cleared from the
   > database once its VM is destroyed? Nothing in the code removes it, so it
   > stays listed. Add the step here, or note that it's expected to stay.

4. **One test launch, on a Red Duck challenge.** It's the only test that
   exercises the SSH settings, the first-boot handout download and the Docker
   host together. Launch one and watch Terminal 2:
   - a new VM appears;
   - `virt-customize` shows for a few seconds, then disappears;
   - the VM goes to `running`.

   Then open the console from the challenge tile and check the handout files are
   in `~/Desktop/<challenge name>`. Close the challenge, and check the VM
   disappears from Terminal 2.

   This also tests the Red Duck firewall: the handouts arrive only if it lets
   Red Duck through to the Docker host.

   Every other kind of launch gets its own session network, which this test
   doesn't cover. Launch a multi-VM challenge too if there's time, and check in
   Terminal 2 that its VMs appear and go.

If all four pass, The Pond is ready for students.

---

## Clearing a leftover clone

A launch can fail after its clone has already been created, for example when the
SSH step fails. The clone is then left on Proxmox, but **it doesn't appear on the
Sessions page**:
- the platform marks its record as deleted (`provisioner.py`, the `except` block
  in `clone_and_start()`); and
- the launch only tears down clones that finished starting (`themes.py`,
  `launch()`).

Left there, it holds a VMID that the next launch may try to use.

After any failed launch, list what's in the clone pool on `pve`:

```bash
pvesh get /pools/pond-clones --output-format json-pretty | grep -E '"vmid"|"name"|"status"'
```

Compare that list with **Running now** on the Sessions page. A VM in the pool that
no running session owns is a leftover. Destroy it:

```bash
qm stop <vmid>
qm destroy <vmid> --purge
```

**Never touch VMIDs `300–303`.** They belong to another project on this server.
Also leave alone the templates (`10000` and up), the Docker host (`9997`) and the
controller.

Sources: Proxmox VE API viewer,
[`/pools/{poolid}`](https://pve.proxmox.com/pve-docs/api-viewer/#/pools/{poolid});
the [`qm` manual](https://pve.proxmox.com/pve-docs/qm.1.html), `qm stop` and
`qm destroy`.

---

## If something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `https://10.1.21.151:8006` doesn't load | Server still booting, or off | Wait a few minutes; check the server's power light |
| Login fails with the right password | Wrong realm | Choose **Linux PAM standard authentication** |
| `secret=0 chars` | Controller can't SSH to `pve` as root | `ssh -o BatchMode=yes root@10.1.21.151 true` and fix the key |
| `Proxmox settings:` warning at startup | An export missed | Ctrl+C, rerun the Terminal 1 block |
| Site doesn't load from other machines | Flask started without `--host 0.0.0.0` | Restart Flask with it |
| Every launch says "could not be started" | Most often the datacenter firewall is off | Read Terminal 1 for `launch failed for instance`; the admin guide's "When a launch fails" lists the reasons. Then check for a leftover clone ("Clearing a leftover clone") |
| One launch says "could not be started" | Token, SSH or VMID problem | As above |
| Only Red Duck and multi-VM launches fail | SSH settings, or `pve`'s host key isn't known | Check `key=` in the `echo` line; run `ssh root@10.1.21.151 true` once and answer `yes` |
| Red Duck challenge has no files on the Desktop | Docker host down, or its file server is; or the Docker host's address isn't `POND_DOCKER_HOST_IP`, so Red Duck's firewall blocks it | Rerun the `curl` check in step 5; check the setting |
| Launch works but the console won't connect | TLS to Proxmox, or the WebSocket upgrade | Check `PROXMOX_CA_BUNDLE` points at the copied `pve-root-ca.pem`; in the browser's developer tools the console WebSocket should return status 101 |

When a launch fails, Terminal 1 prints the reason:

```
ERROR in themes: launch failed for instance <id>: <reason>
```

The Proxmox task log (`pve` → **Tasks** panel at the bottom of the web UI) also
shows failed clones, but not SSH failures.
