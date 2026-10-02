"""Red Duck handout pre-download: command building and SSH injection, no real hypervisor."""
import shlex
from types import SimpleNamespace

import pytest

import provisioner
from test_upload_routes import env
from app import themes


def test_command_only_for_handout_template(env):
    app, _, _ = env
    with app.app_context():
        kali = SimpleNamespace(template_name=app.config["POND_HANDOUT_TEMPLATE"])
        other = SimpleNamespace(template_name="metasploitable2")
        ch = SimpleNamespace(title="dockjmp")
        assert themes._handout_command(other, ch) is None
        cmd = themes._handout_command(kali, ch)
        assert "-P /home/kali/Desktop/dockjmp http://10.1.30.10:8000/dockjmp/" in cmd
        assert cmd.endswith("chown -R kali:kali /home/kali/Desktop")


def test_command_quotes_title(env):
    app, _, _ = env
    with app.app_context():
        kali = SimpleNamespace(template_name=app.config["POND_HANDOUT_TEMPLATE"])
        cmd = themes._handout_command(kali, SimpleNamespace(title="x; rm -rf /"))
        assert shlex.quote("/home/kali/Desktop/x; rm -rf /") in cmd
        assert " rm -rf /;" not in cmd


def test_disabled_when_template_setting_empty(env):
    app, _, _ = env
    with app.app_context():
        app.config["POND_HANDOUT_TEMPLATE"] = ""
        assert themes._handout_command(SimpleNamespace(template_name=""), SimpleNamespace(title="a")) is None


def test_firstboot_requires_proxmox_host():
    with pytest.raises(ValueError):
        provisioner.clone_and_start(None, 10001, "pve", instance_id=1, template_id=1,
                                    firstboot_command="true")


def test_inject_runs_virt_customize_over_ssh(monkeypatch):
    ran = []

    class Chan:
        def recv_exit_status(self): return 0

    class SSH:
        def exec_command(self, cmd):
            ran.append(cmd)
            return None, SimpleNamespace(channel=Chan()), SimpleNamespace(read=lambda: b"")
        def close(self): ran.append("closed")

    monkeypatch.setattr(provisioner, "_find_disk_volid", lambda c, n, v: "vm-19001-disk-0")
    monkeypatch.setattr(provisioner, "_ssh_client", lambda host: SSH())
    provisioner.inject_firstboot_command(None, "pve", 19001, "echo hi; id", "10.1.21.151")
    assert ran == ["virt-customize -a /dev/pve/vm-19001-disk-0 --firstboot-command 'echo hi; id'", "closed"]


def test_inject_refuses_protected_vmid():
    with pytest.raises(provisioner.ProxmoxError):
        provisioner.inject_firstboot_command(None, "pve", 301, "true", "10.1.21.151")