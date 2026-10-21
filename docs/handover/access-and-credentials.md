# Access and credentials inventory

Every account, key and secret The Pond depends on: what it's for, where it
lives, who holds it, and how to replace it. **No secret goes in this file.** It
records where each one is, not what it is.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. Items marked "from the code" were found by reading the repo;
the rest are site facts to confirm.

> **Before you fill in "Held by":** this repository is public (see the
> [handover checklist](handover-checklist.md) 2.1). Who holds what, and the BMC
> and logins listed here, tell an attacker where to aim. If the repo stays
> public, keep the filled-in copy in the client's own records and leave this
> one blank.

---

## How to use this

- **At handover**, fill in "Held by" for every row, then rotate every row marked
  **Rotate at handover**. Outgoing members' copies then stop working.
- **When someone leaves the team** later, rotate whatever they held.
- **If a secret leaks**, rotate it first and investigate second. Charter risk
  R10 also requires telling the client.

---

## A. Code and documentation

| # | What | Where | Used for | Held by | Rotating / transferring |
|---|---|---|---|---|---|
| A1 | GitHub repository `GregFromIT/26_ITP2_Group6_ThePond` | GitHub, owned by the personal account `GregFromIT` | All code and these docs | `<FILL IN>` owner and collaborators | Transfer to an account the client controls ([GitHub: transferring a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository)). Then remove outgoing collaborators |
| A2 | Ansible vault password `~/.vault_pass` | Named by the repo's `ansible.cfg` | Only the legacy playbooks (`create_instance.yml`, `destroy_instance.yml`). The live playbook doesn't use it | `<FILL IN>`, if it still exists | Retire it with the legacy playbooks |

## B. Machines

| # | What | Where | Used for | Held by | Rotating |
|---|---|---|---|---|---|
| B1 | Proxmox `root` (Linux PAM) password | `pve`, `10.1.21.151` | Web UI login (realm **Linux PAM**), the node shell, running the token playbook | `<FILL IN>` | **Rotate at handover.** `passwd` on `pve`. Also used by anything that logs in as `root` over SSH with a password |
| B2 | Server BMC (out-of-band management) | `10.1.20.201`, on the lab VLAN (known issue N6) | Powering the server on and off remotely, its console | `<FILL IN>` | **Rotate at handover**, in the BMC's own interface. `<FILL IN: make and model>` |
| B3 | Controller VM login | VM 9999, `10.1.21.248` | Running and updating the web app | `<FILL IN>` username and holders | **Rotate at handover** |
| B4 | Docker host login | VM 9997 | Updating containers and handouts | `<FILL IN>` | **Rotate at handover** |
| B5 | Network equipment (UniFi gateway and switches) | Site network owner | Gateway rules G1–G5 (network reference §7.5) | The site network owner, `<FILL IN contact>` | Not ours to rotate. Record who confirmed the rules |

## C. Between the controller and Proxmox

From the code. These are what the web app and the seeder use.

| # | What | Where it's stored | Used by | Held by | Rotating |
|---|---|---|---|---|---|
| C1 | API token `pond@pve!launcher` (user `pond@pve`) | Secret in `/root/pond-launcher.token` on `pve` (mode 0600), read at each start by the launch guide's commands (see DECISION 4.2 in the checklist) | The web app (`PROXMOX_TOKEN_SECRET`) and the seeder (`THEPOND_PROXMOX_TOKEN_SECRET`) | Anyone with B1, or a shell on the controller while the app runs | **Rotate at handover.** [`pond-sec/docs/README.md`](../../pond-sec/docs/README.md), token runbook step 6 |
| C2 | Root SSH key, controller → `pve` | Private key in the controller's `~/.ssh/` (`id_ed25519`); public key in `/root/.ssh/authorized_keys` on `pve` | The app's `virt-customize` runs (static IPs, Red Duck handouts), and reading C1 | Anyone with B3 | **Rotate at handover.** Make a new key (`ssh-keygen -t ed25519`), add it with `ssh-copy-id root@10.1.21.151`, check `ssh -o BatchMode=yes root@10.1.21.151 true`, then delete the old line from `authorized_keys`. Restrict the new one with `from=` and a forced command (network reference §7.3). [ssh-keygen(1)](https://man.openbsd.org/ssh-keygen.1), [sshd(8) AUTHORIZED_KEYS](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT) |
| C3 | `pve`'s SSH host key, pinned on the controller | The controller's `~/.ssh/known_hosts` | The app refuses to SSH to `pve` without it (`RejectPolicy`) | Not secret | Changes only if `pve` is reinstalled. Then `ssh-keygen -R 10.1.21.151`, connect once and check the fingerprint against `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on `pve` |
| C4 | Proxmox CA certificate | `/etc/pve/pve-root-ca.pem` on `pve`; copied to `~/pve-root-ca.pem` on the controller | TLS verification of every API call and the console relay (`PROXMOX_CA_BUNDLE`) | Not secret | Re-copy only if the CA is regenerated. Renewing the node certificate doesn't change it ([Proxmox: Certificate Management](https://pve.proxmox.com/wiki/Certificate_Management)) |
| C5 | Importer token for `import_challenge.py` | **Doesn't exist yet** (known issue C4) | Would be: importing images and creating templates | — | Create a separate `@pve` token with only those rights. Never reuse C1 or a `@pam` token |
| C6 | Old token `root@pam!root` | Should no longer exist | Was the app's token before the scoped one. Its secret was in `.env` and `group_vars/secrets.txt` on team machines (never in git: checked by searching the history for token-shaped values) | — | Confirm it's gone (checklist 2.4). If not: the token runbook, step 5 |

## D. The web app

From the code.

| # | What | Where | Used for | Held by | Rotating |
|---|---|---|---|---|---|
| D1 | Flask secret key | `FLASK_SECRET_KEY` (required in production), otherwise generated into `pond-sec/instance/secret_key` (`app/__init__.py`, `resolve_secret_key()`) | Signs session cookies and CSRF tokens | Anyone with B3 | **Rotate at handover.** Set a new `FLASK_SECRET_KEY`, or delete the file and restart. Everyone is signed out once. Generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"` ([Python: secrets](https://docs.python.org/3/library/secrets.html)) |
| D2 | The database `the_pond.db` | Repo root on the controller, unless `POND_SETTINGS` moves it | Holds every account's scrypt password hash, flag hashes and the audit log | Anyone with B3, and anyone with a backup | Not rotated. Protect the file and its backups like a password store |
| D3 | Pond Sec `sysadmin` accounts | In D2 | Approving accounts, roles, publishing | `<FILL IN>`. Today they're team accounts (`db/seed_accounts.py`) | Create the incoming team's account (checklist 1.8), then demote or lock ours. If every admin is lost: `flask --app wsgi set-role <username> admin` on the controller (admin guide) |
| D4 | `POND_SETTINGS` file | Outside the repo, path set in the environment (example: `deployment/pond-settings.example.py`) | Trusted Python config; can hold integration credentials | Anyone with B3 | It's code: restrict who can write it |

## E. Content

| # | What | Where | Exposure | Action |
|---|---|---|---|---|
| E1 | Challenge flags | Plaintext in `vars/challenges/*.yml` | Public while the repo is public (known issue C6) | Checklist 2.7 |
| E2 | `the_pond.db.bak` | Tracked in git, and in its history | Early accounts' password hashes, flag hashes | Checklist 2.2, 2.3 |
| E3 | Team members' names | `db/seed_accounts.py` | Public | Checklist 2.6 |

---

## Not found anywhere

Things a site like this usually has, which we couldn't find. If they exist,
add them above:

- a password manager or shared vault for the team;
- a backup location, and whoever has access to it (see the
  [maintenance guide](../operations/maintenance-and-restore.md));
- any external accounts (domain names, cloud services, mailing lists). As far as
  the code shows, the system uses none.
