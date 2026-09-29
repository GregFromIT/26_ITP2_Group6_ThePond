"""Checks that every clone is isolated on its own network before it boots.

Run with:  python -m tests.test_isolation      (no pytest needed)

Same shape as test_flow.py: plain checks, one line each. The one difference is
that PROXMOX_BACKEND=api needs a hypervisor, so this drives app/proxmox.py
against FakeProxmox (tests/fake_proxmox.py) — an in-memory stand-in that
answers the handful of API paths the adapter uses and records every call, so
the checks can assert both the end state (where each NIC ended up) and the
order things happened in (nothing boots before it is isolated).

ADDING A CHECK: build a FakeProxmox, tweak its state or set .fail_on to make
one call raise, run _launch() or _destroy(), and assert on .vms / .vnets /
.calls.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, proxmox            # noqa: E402
from tests.fake_proxmox import FakeProxmox     # noqa: E402

checks = []


def check(label, condition):
    checks.append((label, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {label}")


# ----------------------------------------------------------------- helpers

# Same throwaway-storage pattern as test_flow.build(): nothing here touches a
# real database, and the adapter never reaches a network.
_scratch = tempfile.mkdtemp(prefix="pondsec-isolation-")
app = create_app({"DATABASE": os.path.join(_scratch, "range.sqlite"), "TESTING": True,
                  "SECRET_KEY": "test", "PROXMOX_BACKEND": "api",
                  "PROXMOX_TOKEN_ID": "pond@pve!launcher",
                  "PROXMOX_TOKEN_SECRET": "unused", "PROXMOX_TASK_TIMEOUT": 5,
                  "SQLALCHEMY_BINDS": {"pond": f"sqlite:///{_scratch}/pond.db"},
                  "UPLOAD_DIR": os.path.join(_scratch, "uploads")})


def _launch(fake):
    """Run clone_and_start against fake; return (clone, error)."""
    proxmox._connect = lambda: fake
    with app.app_context():
        try:
            return proxmox.clone_and_start(FakeProxmox.TEMPLATE, "pve", "student-c1"), None
        except proxmox.ProxmoxError as exc:
            return None, exc


def _destroy(fake, vmid):
    proxmox._connect = lambda: fake
    with app.app_context():
        try:
            proxmox.stop_and_destroy(vmid, "pve")
            return None
        except proxmox.ProxmoxError as exc:
            return exc


def _index(fake, method, suffix):
    for i, (m, path, _) in enumerate(fake.calls):
        if m == method and path.endswith(suffix):
            return i
    return None


def _nothing_left(fake):
    return set(fake.vms) == {FakeProxmox.TEMPLATE} and not fake.vnets and not fake.running


# ------------------------------------------------------------------ checks

print("\n-- a normal launch is isolated before it boots")
fake = FakeProxmox()
clone, err = _launch(fake)
vnet = "p2329"   # 9001 in hex
check("launch succeeds", clone is not None and err is None)
check("the clone gets its own VNet", fake.vnets == {vnet})
check("the VNet is created in the configured zone",
      any(m == "POST" and p == "cluster/sdn/vnets" and kw == {"vnet": vnet, "zone": "pondz"}
          for m, p, kw in fake.calls))
check("the VNet is created without a subnet or gateway",
      not any("subnets" in p for _, p, _ in fake.calls))
nics = {k: v for k, v in fake.vms[9001].items() if k.startswith("net")}
check("every NIC is moved, not just net0",
      set(nics) == {"net0", "net1"} and all(f"bridge={vnet}" in v.split(",") for v in nics.values()))
check("no NIC keeps a VLAN tag", not any("tag=" in v for v in nics.values()))
check("every NIC has the firewall on", all("firewall=1" in v.split(",") for v in nics.values()))
check("no NIC is left with firewall=0", not any("firewall=0" in v for v in nics.values()))
check("MAC addresses are preserved",
      nics["net0"].startswith("virtio=BC:24:11:00:00:01") and nics["net1"].startswith("e1000=BC:24:11:00:00:02"))
check("the VM firewall drops inbound by default",
      fake.firewall[9001] == {"enable": 1, "policy_in": "DROP"})
start = _index(fake, "POST", "qemu/9001/status/start")
check("the VM is started", start is not None and 9001 in fake.running)
check("the VM is started only after its NICs are moved",
      start > _index(fake, "PUT", "qemu/9001/config"))
check("the VM is started only after its firewall is on",
      start > _index(fake, "PUT", "qemu/9001/firewall/options"))
check("the NICs are moved only after the clone task finished",
      _index(fake, "GET", "tasks/UPID:clone:9001/status") < _index(fake, "PUT", "qemu/9001/config"))
check("the template itself is never modified",
      not any(m in ("PUT", "POST") and "qemu/100/" in p and not p.endswith("clone")
              for m, p, _ in fake.calls))

print("\n-- two students get two separate networks")
fake = FakeProxmox()
first, _ = _launch(fake)
fake.next_vmid = 9002
second, _ = _launch(fake)
check("each clone has a different VNet", fake.vnets == {"p2329", "p232a"})
check("the two clones share no bridge",
      not ({v for k, v in fake.vms[9001].items() if k.startswith("net")} &
           {v for k, v in fake.vms[9002].items() if k.startswith("net")}))

print("\n-- teardown removes the network too")
err = _destroy(fake, 9001)
check("teardown succeeds", err is None)
check("the VM is gone", 9001 not in fake.vms)
check("its VNet is gone", "p2329" not in fake.vnets)
check("the other student's VNet is untouched", "p232a" in fake.vnets and 9002 in fake.vms)
check("the VNet is removed only after the VM is deleted",
      _index(fake, "DELETE", "cluster/sdn/vnets/p2329") > _index(fake, "DELETE", "qemu/9001"))
fake = FakeProxmox()
fake.vms[8000] = {"net0": "virtio=AA,bridge=vmbr0"}   # launched before this change
check("a clone from before per-session networks still tears down", _destroy(fake, 8000) is None)

print("\n-- preconditions fail closed")
fake = FakeProxmox(zone_type="vlan")
clone, err = _launch(fake)
check("a non-Simple zone refuses the launch", clone is None and "Simple zone" in str(err))
check("...before anything is cloned", _index(fake, "POST", "clone") is None)
fake = FakeProxmox()
fake.zones = []
clone, err = _launch(fake)
check("a missing zone refuses the launch", clone is None and "Simple zone" in str(err))
fake = FakeProxmox(dc_firewall=0)
clone, err = _launch(fake)
check("a disabled datacenter firewall refuses the launch",
      clone is None and "datacenter firewall" in str(err))
check("...before anything is cloned", _index(fake, "POST", "clone") is None)
fake = FakeProxmox(nics={"scsi0": "local-lvm:vm-100-disk-0"})
clone, err = _launch(fake)
check("a template with no NIC refuses the launch", clone is None and "no network interface" in str(err))
check("...and leaves nothing behind", _nothing_left(fake))

print("\n-- any failure mid-launch cleans up and never boots")
for method, suffix, label in [
    ("POST", "cluster/sdn/vnets", "creating the VNet"),
    ("PUT", "cluster/sdn", "applying SDN"),
    ("PUT", "qemu/9001/config", "moving the NICs"),
    ("PUT", "qemu/9001/firewall/options", "enabling the firewall"),
    ("POST", "qemu/9001/status/start", "starting the VM"),
]:
    fake = FakeProxmox()
    fake.fail_on = (method, suffix)
    clone, err = _launch(fake)
    check(f"a failure {label} refuses the launch", clone is None and err is not None)
    check(f"a failure {label} leaves no VM, VNet or running machine", _nothing_left(fake))

fake = FakeProxmox()
fake.ignore_config_put = True     # Proxmox accepts the write but the NICs do not move
clone, err = _launch(fake)
check("a NIC that did not actually move is caught on read-back",
      clone is None and "outside its session network" in str(err))
check("...and that VM is never started", _index(fake, "POST", "status/start") is None)
check("...and is removed", _nothing_left(fake))

print("\n-- cleanup never touches what it did not create")
fake = FakeProxmox()
fake.vms[9001] = {"net0": "virtio=CC,bridge=p2329,firewall=1", "name": "other-student"}
fake.vnets.add("p2329")
fake.running.add(9001)
fake.fail_on = ("POST", "qemu/100/clone")   # vmid 9001 already taken by a racing launch
clone, err = _launch(fake)
check("a clone that loses the vmid race is refused", clone is None and err is not None)
check("the other student's VM survives", 9001 in fake.vms and 9001 in fake.running)
check("the other student's VNet survives", "p2329" in fake.vnets)

print("\n-- the VNet name always fits SDN's limit")
check("a normal vmid gives a valid name", proxmox._vnet_name(9001) == "p2329")
check("the largest vmid that fits still works", proxmox._vnet_name(16**7 - 1) == "pfffffff")
try:
    proxmox._vnet_name(16**7)
    too_big = False
except proxmox.ProxmoxError:
    too_big = True
check("a vmid too large to name is refused, not truncated", too_big)

passed = sum(1 for _, ok in checks if ok)
print(f"\n{passed}/{len(checks)} checks passed")
sys.exit(0 if passed == len(checks) else 1)
