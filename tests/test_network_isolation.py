"""Isolation helpers in provisioner.py, driven against tests/fake_proxmox.py
or a Mock where the fake has no handler (firewall rules)."""
from unittest.mock import Mock

import pytest

import provisioner
from fake_proxmox import FakeProxmox

VMID = FakeProxmox.TEMPLATE   # net0 has tag=20, net1 has firewall=0


def nics(fake):
    return {k: v for k, v in fake.vms[VMID].items() if k.startswith("net")}


def test_inbound_rule_is_created_enabled():
    client = Mock()
    provisioner.apply_network_rule(client, "pve", 9001, "10.10.10.10", 22)
    kwargs = client.nodes.return_value.qemu.return_value.firewall.rules.post.call_args.kwargs
    assert kwargs["enable"] == 1 and kwargs["type"] == "in"

def test_every_nic_moves_to_the_session_vnet_with_firewall_on():
    fake = FakeProxmox()
    provisioner._firewall_nics(fake, "pve", VMID, "s2a")
    for value in nics(fake).values():
        parts = value.split(",")
        assert "bridge=s2a" in parts and "firewall=1" in parts
        assert not any(p.startswith(("tag=", "trunks=", "firewall=0")) for p in parts)
    assert nics(fake)["net0"].startswith("virtio=BC:24:11:00:00:01")   # MAC kept
    assert nics(fake)["net1"].startswith("e1000=BC:24:11:00:00:02")


def test_shared_bridge_keeps_its_bridge_but_gets_firewall():
    fake = FakeProxmox(nics={"net0": "virtio=BC:24:11:00:00:01,bridge=pondnc"})
    provisioner._firewall_nics(fake, "pve", VMID, None)
    assert nics(fake)["net0"] == "virtio=BC:24:11:00:00:01,bridge=pondnc,firewall=1"


def test_rewrite_that_did_not_stick_refuses():
    fake = FakeProxmox()
    fake.ignore_config_put = True          # the fake's switch for "PUT silently ignored"
    with pytest.raises(provisioner.ProxmoxError, match="not isolated"):
        provisioner._firewall_nics(fake, "pve", VMID, "s2a")


def test_preconditions_refuse_non_simple_zone_and_disabled_dc_firewall():
    with pytest.raises(provisioner.ProxmoxError, match="Simple zone"):
        provisioner.check_isolation_preconditions(FakeProxmox(zone_type="vlan"))
    with pytest.raises(provisioner.ProxmoxError, match="datacenter firewall"):
        provisioner.check_isolation_preconditions(FakeProxmox(dc_firewall=0))
    provisioner.check_isolation_preconditions(FakeProxmox())   # passes

class Rules:
    """POST inserts at the top, like Proxmox's unshift; append=True models the
    opposite to prove the read-back catches a wrong order."""
    def __init__(self, append=False):
        self.rules, self.append = [], append
    def post(self, **kw):
        self.rules.append(kw) if self.append else self.rules.insert(0, kw)
    def get(self):
        return [dict(r, pos=i) for i, r in enumerate(self.rules)]


EGRESS = [{"action": "ACCEPT", "proto": "tcp", "dest": "10.1.30.10"},
          {"action": "DROP", "dest": "10.0.0.0/8"},
          {"action": "ACCEPT", "proto": "tcp", "dport": "80,443"}]


def egress_client(rules):
    client = Mock()
    vm = client.nodes.return_value.qemu.return_value
    vm.firewall.rules = rules
    return client, vm


def test_egress_rules_land_in_the_given_order_then_policy_out_drop():
    rules = Rules()
    client, vm = egress_client(rules)
    provisioner.enable_vm_firewall(client, "pve", 9001, egress_rules=EGRESS)
    assert [(r["action"], r.get("dest"), r.get("dport")) for r in rules.get()] == \
        [("ACCEPT", "10.1.30.10", None), ("DROP", "10.0.0.0/8", None), ("ACCEPT", None, "80,443")]
    assert all(r["type"] == "out" and r["enable"] == 1 for r in rules.rules)
    vm.firewall.options.put.assert_called_once_with(
        enable=1, policy_in="DROP", policy_out="DROP", dhcp=1)


def test_egress_rules_out_of_order_refuse_before_enabling():
    client, vm = egress_client(Rules(append=True))
    with pytest.raises(provisioner.ProxmoxError, match="out of order"):
        provisioner.enable_vm_firewall(client, "pve", 9001, egress_rules=EGRESS)
    vm.firewall.options.put.assert_not_called()


def test_default_firewall_is_inbound_drop_only():
    client = Mock()
    provisioner.enable_vm_firewall(client, "pve", 9001)
    client.nodes.return_value.qemu.return_value.firewall.options.put.assert_called_once_with(
        enable=1, policy_in="DROP")