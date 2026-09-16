#!/usr/bin/env python3
"""
test_inject_network.py - standalone check for inject_instance_network().

Bypasses clone_and_start() deliberately: that function requires a real
ChallengeInstance/VMTemplate row to satisfy VMInstance's NOT NULL foreign
keys (see decisions-and-learnings), which DC-1 doesn't have yet. This
script clones the DC-1 template directly via proxmoxer, calls
inject_instance_network() on the result, starts it, and leaves it running
for you to confirm from pve - same validation steps (tcpdump/ping/nmap)
used to originally debug VM 105.

This clones the template directly with no session VNet involved, landing
the result on the template's own flat/shared bridge - the same situation
as launch()'s single-VM path (themes.py's `len(templates) > 1` check),
NOT the isolated-VNet multi-VM path. That means a gateway is required for
the clone to be reachable from anywhere off its own L2 segment - confirmed
the hard way testing vmid 308 on 2026-09-16: omitting it let the guest
answer ARP fine (pure L2) while every ping got 100% loss, since replies
had nowhere to route to. Pass one via the third argument.

Run from the controller node. Deletes nothing automatically - clean up
the test VM yourself once you've confirmed it. Also worth checking
`qm list` before each run for stale VMs left over from a previous
attempt - two live clones both claiming the same static IP produced a
confusing partial-failure result (ARP replied, ping still failed) that
had nothing to do with this script or inject_instance_network() at all.

Usage:
    THEPOND_PROXMOX_TOKEN_SECRET=... THEPOND_PROXMOX_SSH_KEY=... \\
        python3 test_inject_network.py <dc1_template_vmid> <test_static_ip> [gateway]

Example:
    python3 test_inject_network.py 10002 10.1.20.99 10.1.20.1
"""
import sys

import provisioner


def main():
    if len(sys.argv) not in (3, 4):
        print(f"usage: {sys.argv[0]} <template_vmid> <static_ip> [gateway]")
        sys.exit(1)

    template_vmid = int(sys.argv[1])
    static_ip = sys.argv[2]
    gateway = sys.argv[3] if len(sys.argv) == 4 else None
    if gateway is None:
        print(
            "WARNING: no gateway given - fine if this template's bridge really is an "
            "isolated segment, but the flat/shared bridge case (the template's own "
            "default) needs one, or the clone will answer ARP but be unreachable for "
            "anything beyond that - exactly what happened testing vmid 308."
        )

    config = provisioner.load_config()
    client = provisioner.get_client(config)
    node = config["proxmox_node"]
    proxmox_host = config["proxmox_api_host"]

    test_vmid = provisioner.next_free_vmid(client, node)
    print(f"[1/4] cloning template {template_vmid} -> {test_vmid} ...")
    task = client.nodes(node).qemu(template_vmid).clone.post(
        newid=test_vmid, name="test-inject-network", full=1, storage=config["vm_storage"],
    )
    from proxmoxer.tools import Tasks
    Tasks.blocking_status(client, task)
    print("      clone complete, disk exists, VM is offline")

    gw_note = f" (gateway {gateway})" if gateway else " (no gateway)"
    print(f"[2/4] injecting static IP {static_ip}{gw_note} via virt-customize over SSH ...")
    provisioner.inject_instance_network(client, node, test_vmid, static_ip, proxmox_host, gateway=gateway)
    print("      injection call returned without raising - virt-customize reported success")

    print(f"[3/4] starting vmid {test_vmid} ...")
    client.nodes(node).qemu(test_vmid).status.start.post()
    print("      start command sent")

    print(f"[4/4] done. On pve, confirm with:")
    print(f"      tcpdump -i tap{test_vmid}i0 -n")
    print(f"      ping -c 3 {static_ip}")
    print(f"\nWhen you're satisfied, clean up manually:")
    print(f"      qm stop {test_vmid} && qm destroy {test_vmid}")


if __name__ == "__main__":
    main()
