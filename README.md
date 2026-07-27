# The Pond - MVP

Minimal CTF instance manager: clone a pre-built Proxmox template, track it
in SQLite with an expiry time, delete it when time's up (manually or via
a cron sweep). No pool/checkout model, no snapshots, no session tracking
beyond "this instance exists and expires at X."

## Structure

```
cli.py                    The one entrypoint you run
db/schema.sql              One table: instances
db/store.py                All SQL lives here, behind a class interface
playbooks/create_instance.yml   Clone + start (called by cli.py, not run directly)
playbooks/destroy_instance.yml  Stop + delete (called by cli.py, not run directly)
vars/challenges/*.yml       One file per challenge: template name, VMID range, TTL
group_vars/all.yml           Proxmox node/host/storage (non-secret)
group_vars/vault.yml.example Copy to vault.yml, fill in, then ansible-vault encrypt
```

## Setup

```bash
pip install pyyaml --break-system-packages
ansible-galaxy collection install community.general

cp group_vars/vault.yml.example group_vars/vault.yml
# fill in vault_proxmox_api_token_secret, then:
ansible-vault encrypt group_vars/vault.yml

python3 cli.py init-db
```

## Usage

```bash
# Create an instance for "team01" from the lockedshields challenge
python3 cli.py create --challenge lockedshields --name team01

# Same, with an explicit TTL and VMID override
python3 cli.py create --challenge lockedshields --name team02 --ttl-hours 6 --vmid 310

# List everything
python3 cli.py list
python3 cli.py list --status active

# Destroy one manually
python3 cli.py destroy --name lockedshields-team01

# Destroy everything past its expiry (run this from cron)
python3 cli.py sweep
```

## Cron example (sweep every 10 minutes)

```
*/10 * * * * cd /opt/thepond && python3 cli.py sweep >> /var/log/thepond-sweep.log 2>&1
```

## Adding a challenge

Drop a new file in `vars/challenges/<name>.yml`:

```yaml
challenge: "mychallenge"
vm_template: "MyChallengeTemplate"   # must already exist in Proxmox
vmid_range_start: 401
vmid_range_end: 449
default_ttl_hours: 3
```

Nothing else needs to change - `cli.py create --challenge mychallenge --name ...`
picks it up automatically.

## Deliberately not included (see project memory for the fuller design if needed)

- No pool/checkout model - every `create` clones fresh from the template.
  If you want faster handout later, that's a v2 addition, not this one.
- No snapshot/reset-and-reuse - `destroy` is a hard delete via `state: absent`.
- No per-instance flag generation - out of scope for this MVP; template
  content (including any flag) is whatever you baked into it manually.

## v2 scaffolding notes

`db/store.py`'s `InstanceStore` class is the only place SQL lives. Every
method (`create`, `get_by_vmid`, `get_by_name`, `list_all`, `list_expired`,
`mark_destroyed`) keeps the same name and signature when this moves to
SQLAlchemy - only the method bodies change from raw `sqlite3` calls to
`session.query(...)`. `cli.py` and the playbooks never see SQL directly,
so nothing outside `db/store.py` needs to change for that migration.

`next_free_vmid()` in `cli.py` currently checks only the local db, not live
Proxmox state - fine for a single operator, but worth reconciling against
`community.general.proxmox_vm_info` before this is used by more than one
person at once.
