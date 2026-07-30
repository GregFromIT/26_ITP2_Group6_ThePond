"""
proxmoxerTest.py

Manual smoke test for provisioner.py against a real Proxmox host: clones a
template, starts it, waits for confirmation, then tears it down. Doesn't
touch db/store.py - just exercises the proxmoxer calls in isolation before
trusting cli.py to drive them.

Requires THEPOND_PROXMOX_TOKEN_SECRET to be set (see provisioner.py).
"""
import provisioner

if __name__ == "__main__":
    config = provisioner.load_config()
    client = provisioner.get_client(config)

    node = config["proxmox_node"]
    TEMPLATE_NAME = "v1Template"
    TEST_NAME = "smoketest-instance"
    TEST_VMID = 9999

    print(f"cloning {TEST_NAME} (vmid {TEST_VMID}) from template {TEMPLATE_NAME} on node {node}...")
    provisioner.create_instance(
        client,
        node=node,
        vm_template=TEMPLATE_NAME,
        vm_name=TEST_NAME,
        vmid=TEST_VMID,
        storage=config["vm_storage"],
    )
    print("created and started.")

    input("Press Enter to destroy the test instance...")

    print(f"destroying vmid {TEST_VMID}...")
    provisioner.destroy_instance(client, node=node, vmid=TEST_VMID)
    print("destroyed.")
