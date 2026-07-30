"""
provisioner.py

Direct Proxmox REST calls via proxmoxer - replaces the ansible-playbook
shell-outs in playbooks/create_instance.yml and playbooks/destroy_instance.yml.
Same clone+start / stop+delete lifecycle, called directly from cli.py instead
of shelling out to `ansible-playbook`.

Connection settings (node/host/user/token id) come from group_vars/all.yml,
same values the playbooks used via Jinja vars. The token secret is never
committed - set it via the THEPOND_PROXMOX_TOKEN_SECRET env var (this replaces
what group_vars/vault.yml held for Ansible).
"""
import os
from pathlib import Path

import yaml
from proxmoxer import ProxmoxAPI
from proxmoxer.tools import Tasks

PROJECT_ROOT = Path(__file__).parent
GROUP_VARS_PATH = PROJECT_ROOT / "group_vars" / "all.yml"

TOKEN_SECRET_ENV = "THEPOND_PROXMOX_TOKEN_SECRET"


def load_config() -> dict:
    with open(GROUP_VARS_PATH) as f:
        return yaml.safe_load(f)


def get_client(config: dict) -> ProxmoxAPI:
    token_secret = os.environ.get(TOKEN_SECRET_ENV)
    if not token_secret:
        raise RuntimeError(
            f"Set {TOKEN_SECRET_ENV} to the Proxmox API token secret "
            "(see group_vars/vault.yml.example for where this used to live)."
        )
    return ProxmoxAPI(
        config["proxmox_api_host"],
        user=config["proxmox_api_user"],
        token_name=config["proxmox_api_token_id"],
        token_value=token_secret,
        verify_ssl=False,
    )


def _resolve_template_vmid(client: ProxmoxAPI, node: str, template_name: str) -> int:
    """proxmox_kvm's `clone:` param takes a template name; the REST clone
    endpoint needs the template's VMID, so resolve name -> vmid first."""
    for vm in client.nodes(node).qemu.get():
        if vm.get("name") == template_name:
            return vm["vmid"]
    raise RuntimeError(f"no template named {template_name!r} found on node {node!r}")


def create_instance(
    client: ProxmoxAPI,
    node: str,
    vm_template: str,
    vm_name: str,
    vmid: int,
    storage: str,
    timeout: int = 120,
) -> None:
    """Clone `vm_template` to `vmid` and start it. Mirrors
    playbooks/create_instance.yml's clone + start tasks."""
    template_vmid = _resolve_template_vmid(client, node, vm_template)

    task = client.nodes(node).qemu(template_vmid).clone.post(
        newid=vmid, name=vm_name, full=1, storage=storage
    )
    Tasks.blocking_status(client, task, timeout=timeout)

    client.nodes(node).qemu(vmid).status.start.post()


def destroy_instance(client: ProxmoxAPI, node: str, vmid: int) -> None:
    """Force-stop then delete a VM. Mirrors
    playbooks/destroy_instance.yml's stop (force) + delete tasks."""
    client.nodes(node).qemu(vmid).status.stop.post()
    client.nodes(node).qemu(vmid).delete()
