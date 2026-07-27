# The Pond - Ansible / Proxmox project structure

## Layout

```
ansible.cfg              Project defaults (inventory path, vault password file)
inventory/hosts.yml       localhost = control target (we call the Proxmox API, not SSH)
group_vars/all.yml        Shared, non-secret config: node name, storage, reserved VMIDs
group_vars/vault.yml      NOT COMMITTED - copy from vault.yml.example, fill in, encrypt
vars/challenges/*.yml     One file per challenge: template name/vmid, VMID range, pool size
playbooks/site.yml        Clone + start ONE instance from an existing template
playbooks/build_pool.yml  Fill a pool of idle instances ahead of demand
playbooks/sweep_expired.yml   Cron job: stop + release sessions past TTL
playbooks/release_reset.yml   Roll a released slot back to a clean snapshot, mark available
scripts/schema.sql         SQLite schema: pool_slots + sessions tables
scripts/sessions_db.py     CLI wrapper - the ONLY place SQL lives, called via ansible.builtin.command
session_manager.py         (existing) admin CLI: init-db, list, assign, release
templates/flag.j2          Per-session flag template, for a future post-clone flag-drop task
```

## First-time setup

```bash
cp group_vars/vault.yml.example group_vars/vault.yml
ansible-vault encrypt group_vars/vault.yml
# fill in vault_proxmox_api_token_secret, vault_root_password before encrypting

python3 scripts/sessions_db.py init-db
```

## Everyday commands

```bash
# Fill/top-up the LockedShields pool
ansible-playbook playbooks/build_pool.yml -e @vars/challenges/lockedshields.yml

# Sweep expired sessions (also runnable from cron)
ansible-playbook playbooks/sweep_expired.yml

# Reset a released slot back to available
ansible-playbook playbooks/release_reset.yml -e vmid=304
```

## Known open items (see project memory / architecture doc)

- Proxmox host address 10.1.21.151 falls outside both defined VLAN subnets - unresolved.
- Template vmid 300 is labeled "LockedShieldsTemplate" in the Proxmox GUI but is
  UNRELATED to this project (belongs to a separate cluster project). The real
  LockedShields template is vmid 1000 ("v1Template"). Recommend renaming 1000
  in the GUI to remove the ambiguity.
- release_reset.yml assumes a VM snapshot named "clean" exists on each pool
  template/instance - this snapshot is not yet created as part of build_pool.yml
  and needs to be added (either baked into the template before first clone,
  or taken automatically as the last step of build_pool.yml).
