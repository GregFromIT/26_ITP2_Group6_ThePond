#!/usr/bin/env python3
"""
import_challenge.py — Interactive installer: take a URL to a VM image,
import it into Proxmox as a template, and write/update the matching
vars/challenges/<challenge>.yml.

    python3 import_challenge.py https://download.vulnhub.com/dc/DC-1.zip

Everything else is prompted for, with derived defaults you can accept
by pressing enter.

Design notes:

  - Two transports. VM create / config / template-convert are real REST
    endpoints -> proxmoxer. `qm disk import` has no REST equivalent, and
    neither does archive extraction, so those run over SSH on the host
    -> paramiko. Same split already used in provisioner.py.

  - No qemu-img convert step. `qm disk import` converts the source to
    whatever format the target storage needs, so importing a .vmdk
    straight to local-lvm (LVM-thin, block-backed) is handled by qm
    itself. A separate convert pass would just write a full-size
    intermediate file for nothing.
    https://forum.proxmox.com/threads/usage-of-qcow2-disk-in-proxmox.140399/

  - Download + extract happen ON the Proxmox host, not the control node.
    Avoids pulling a multi-GB image down and pushing it straight back,
    and qemu-img/qm are only present on the host anyway.

  - VMID is allocated by querying live Proxmox state, not the app DB.
    VMIDs exist on the hypervisor outside this project's knowledge
    (301-303, 300) and allocating from DB state alone collides with them.

References:
    qm(1) — disk import, template, set:  https://pve.proxmox.com/pve-docs/qm.1.html
    PVE API viewer:                      https://pve.proxmox.com/pve-docs/api-viewer/
    proxmoxer:                           https://proxmoxer.github.io/docs/2.0/
    paramiko SSHClient:                  https://docs.paramiko.org/en/stable/api/client.html
    OVA = tar archive (DMTF OVF spec):   https://www.dmtf.org/standards/ovf
"""

import ipaddress
import os
import re
import sys
from urllib.parse import unquote, urlparse

import paramiko
import yaml
from dotenv import load_dotenv
from proxmoxer import ProxmoxAPI

load_dotenv()

REQUIRED_ENV = [
    "THEPOND_PROXMOX_HOST",
    "THEPOND_PROXMOX_NODE",
    "THEPOND_PROXMOX_USER",
    "THEPOND_PROXMOX_TOKEN_NAME",
    "THEPOND_PROXMOX_TOKEN_SECRET",
    "THEPOND_PROXMOX_SSH_USER",
    "THEPOND_PROXMOX_SSH_KEY",
]

WORK_DIR = "/var/lib/vz/template/import"
TEMPLATE_VMID_START = 10000          # project convention: templates live 10000+
TEMPLATE_VMID_END = 10999
DISK_EXTS = ("vmdk", "qcow2", "qcow", "img", "raw", "vdi", "vhd", "vhdx")
ARCHIVE_TOOLS = {                    # ext -> (extract cmd template, apt package)
    "ova":  ("tar -xf {src} -C {dst}", None),        # OVA is a tar per OVF spec
    "tar":  ("tar -xf {src} -C {dst}", None),
    "zip":  ("unzip -o {src} -d {dst}", "unzip"),
    "7z":   ("7z x -y -o{dst} {src}", "p7zip-full"),
    "rar":  ("bsdtar -xf {src} -C {dst}", "libarchive-tools"),   # libarchive reads RAR4/5
    "gz":   ("tar -xzf {src} -C {dst}", None),
    "bz2":  ("tar -xjf {src} -C {dst}", None),
    "xz":   ("tar -xJf {src} -C {dst}", None),
}


# ---------- prompting ----------

def prompt(question: str, default=None, cast=str):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = input(f"{question}{suffix}: ").strip()
        if not raw:
            if default is None:
                print("  Required.")
                continue
            return default
        try:
            return cast(raw)
        except ValueError:
            print(f"  Not a valid {cast.__name__}.")


def prompt_choice(question: str, options: list) -> int:
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        raw = input(f"{question} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("  Pick one of the listed numbers.")


# ---------- connections ----------

def load_config() -> dict:
    cfg = {k: os.environ.get(k) for k in REQUIRED_ENV}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")
    return cfg


# H3: TLS is verified (THEPOND_PROXMOX_CA_BUNDLE for a self-signed cluster) and SSH
# host keys must already be known - nothing is trusted on first sight. This
# remains a root-capable admin tool; that is a documented residual risk.
def connect_api(cfg: dict) -> ProxmoxAPI:
    user = cfg["THEPOND_PROXMOX_USER"].strip()
    realm = user.rpartition("@")[2].strip().lower() if "@" in user else ""
    if not realm or realm == "pam":
        # A realm-less user is refused too: Proxmox would read it as @pam.
        sys.exit("Refusing a @pam API token (H3); use a scoped @pve token.")
    return ProxmoxAPI(
        cfg["THEPOND_PROXMOX_HOST"],
        user=user,
        token_name=cfg["THEPOND_PROXMOX_TOKEN_NAME"],
        token_value=cfg["THEPOND_PROXMOX_TOKEN_SECRET"],
        verify_ssl=os.environ.get("THEPOND_PROXMOX_CA_BUNDLE")
        or os.environ.get("THEPOND_PROXMOX_VERIFY_SSL", "1") == "1",
    )


def connect_ssh(cfg: dict) -> paramiko.SSHClient:
    if (cfg["THEPOND_PROXMOX_SSH_USER"].strip() == "root"
            and os.environ.get("THEPOND_PROXMOX_ALLOW_ROOT_SSH") != "1"):
        sys.exit("Root SSH to the hypervisor is refused (H3). Use a restricted account with a "
                 "forced-command wrapper, or set THEPOND_PROXMOX_ALLOW_ROOT_SSH=1 knowingly.")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = os.environ.get("THEPOND_PROXMOX_KNOWN_HOSTS")
    if known_hosts:
        client.load_host_keys(known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        hostname=cfg["THEPOND_PROXMOX_HOST"],
        username=cfg["THEPOND_PROXMOX_SSH_USER"],
        key_filename=cfg["THEPOND_PROXMOX_SSH_KEY"],
    )
    return client


def run_remote(ssh: paramiko.SSHClient, command: str, echo: bool = True) -> str:
    if echo:
        print(f"[pve] {command}")
    _, stdout, stderr = ssh.exec_command(command)
    status = stdout.channel.recv_exit_status()   # blocks until the command finishes
    out = stdout.read().decode()
    if status != 0:
        sys.exit(f"Remote command failed (exit {status}): {stderr.read().decode()}")
    return out


# ---------- URL handling ----------

def derive_names(url: str) -> tuple:
    """(suggested vm name, suggested challenge slug) from the URL filename."""
    filename = unquote(os.path.basename(urlparse(url).path))
    stem = re.split(r"\.(?:ova|tar|zip|7z|rar|gz|bz2|xz|vmdk|qcow2|img|raw)$", filename,
                    flags=re.IGNORECASE)[0]
    stem = re.sub(r"\.(tar)$", "", stem, flags=re.IGNORECASE)   # e.g. foo.tar.gz
    slug = re.sub(r"[^a-z0-9]+", "", stem.lower())
    return dns_safe(stem), slug


def dns_safe(name: str) -> str:
    """Proxmox validates VM names as DNS names (RFC 1123): letters, digits,
    hyphens, dots. Underscores and spaces are rejected with a 400."""
    return re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-.")


def archive_kind(path: str):
    """Return the ARCHIVE_TOOLS key for this filename, or None if it's a bare
    disk image."""
    lower = path.lower()
    if lower.endswith((".tar.gz", ".tgz")):
        return "gz"
    if lower.endswith((".tar.bz2", ".tbz2")):
        return "bz2"
    if lower.endswith(".tar.xz"):
        return "xz"
    ext = lower.rsplit(".", 1)[-1]
    return ext if ext in ARCHIVE_TOOLS else None


def download(ssh: paramiko.SSHClient, url: str, slug: str) -> str:
    dest_dir = f"{WORK_DIR}/{slug}"
    filename = unquote(os.path.basename(urlparse(url).path)) or "download.bin"
    dest = f"{dest_dir}/{filename}"
    run_remote(ssh, f"mkdir -p {dest_dir}")
    # -f: fail on HTTP errors rather than saving an error page as the image
    # -L: follow redirects, normal for mirrors and release assets
    # -C -: resume a partial download instead of restarting a multi-GB pull
    run_remote(ssh, f"curl -fL -C - '{url}' -o '{dest}'")
    return dest


def extract(ssh: paramiko.SSHClient, archive_path: str) -> str:
    """Extract in place; returns the directory to search for disk images."""
    kind = archive_kind(archive_path)
    if kind is None:
        return os.path.dirname(archive_path)

    cmd_template, apt_pkg = ARCHIVE_TOOLS[kind]
    if apt_pkg:
        tool = cmd_template.split()[0]
        if not run_remote(ssh, f"command -v {tool} || true", echo=False).strip():
            sys.exit(
                f"'{tool}' is not installed on the Proxmox host.\n"
                f"  apt install {apt_pkg}\n"
                f"  (needs the no-subscription repo configured, or apt 401s on "
                f"the enterprise repo)"
            )

    out_dir = f"{os.path.dirname(archive_path)}/extracted"
    run_remote(ssh, f"mkdir -p {out_dir}")
    run_remote(ssh, cmd_template.format(src=f"'{archive_path}'", dst=f"'{out_dir}'"))

    # VulnHub usually ships zip -> ova -> vmdk. An OVA is a tar, so unpack
    # one level further in place; find_disks() then sees the vmdk.
    nested = run_remote(ssh, f"find '{out_dir}' -type f -iname '*.ova'", echo=False)
    for ova in nested.strip().splitlines():
        run_remote(ssh, f"tar -xf '{ova}' -C '{out_dir}'")
    return out_dir


def find_disks(ssh: paramiko.SSHClient, search_dir: str) -> list:
    """List (size_bytes, path) for every disk image under search_dir."""
    name_tests = " -o ".join(f"-iname '*.{e}'" for e in DISK_EXTS)
    out = run_remote(
        ssh, f"find '{search_dir}' -type f \\( {name_tests} \\) -printf '%s\\t%p\\n'",
        echo=False,
    )
    disks = []
    for line in out.strip().splitlines():
        size, path = line.split("\t", 1)
        disks.append((int(size), path))
    return sorted(disks, reverse=True)


def choose_disk(disks: list) -> str:
    if not disks:
        sys.exit("No disk image found in the download. Check the URL contents.")
    if len(disks) == 1:
        print(f"Disk: {disks[0][1]} ({disks[0][0] / 1e9:.2f} GB)")
        return disks[0][1]

    print("\nMultiple disk images found — pick the bootable OS disk.")
    print("Multi-disk VMs will need the rest attached manually afterwards.")
    labels = [f"{p}  ({s / 1e9:.2f} GB)" for s, p in disks]
    return disks[prompt_choice("Which disk", labels)][1]


# ---------- Proxmox ----------

def next_free_vmid(api: ProxmoxAPI, start: int, end: int) -> int:
    """Scan live cluster state. /cluster/resources?type=vm covers QEMU and LXC
    in one call, so VMIDs this project didn't create still block allocation."""
    used = {int(r["vmid"]) for r in api.cluster.resources.get(type="vm")}
    for vmid in range(start, end + 1):
        if vmid not in used:
            return vmid
    sys.exit(f"No free VMID in {start}-{end}.")


def create_template(api: ProxmoxAPI, ssh: paramiko.SSHClient, node: str,
                    vmid: int, name: str, storage: str, disk_path: str,
                    memory: int, cores: int, legacy: bool) -> None:
    # Legacy = guests too old for virtio (2.4 kernels, most pre-2012 VulnHub
    # images). IDE and e1000 are fully emulated, so no guest drivers needed.
    bus = "ide0" if legacy else "scsi0"
    nic = "e1000" if legacy else "virtio"

    api.nodes(node).qemu.create(
        vmid=vmid, name=name, memory=memory, cores=cores,
        net0=f"{nic},bridge=vmbr0", ostype="l26",
    )
    # CLI-only; no REST equivalent. `qm importdisk` is an alias for this.
    run_remote(ssh, f"qm disk import {vmid} '{disk_path}' {storage}")

    disk_cfg = {bus: f"{storage}:vm-{vmid}-disk-0", "boot": f"order={bus}"}
    if not legacy:
        disk_cfg["scsihw"] = "virtio-scsi-pci"
    api.nodes(node).qemu(vmid).config.set(**disk_cfg)
    api.nodes(node).qemu(vmid).template.post()


# ---------- challenge yml ----------

def load_challenge(path: str, slug: str) -> dict:
    if os.path.exists(path):
        data = yaml.safe_load(open(path)) or {}
        data.setdefault("challenge", slug)
        data.setdefault("vm_templates", [])
        data.setdefault("network_rules", [])
        return data
    return {"challenge": slug, "vm_templates": [], "network_rules": []}


def suggest_ip(data: dict) -> str:
    """Next free host address in the subnet the challenge already uses."""
    existing = [vm["static_ip"] for vm in data["vm_templates"] if vm.get("static_ip")]
    if not existing:
        return "10.10.10.10"
    highest = max(ipaddress.ip_address(ip) for ip in existing)
    return str(highest + 10)


def prompt_network_rules(data: dict, roles: list) -> None:
    print("\nNetwork rules (blank port to finish).")
    while True:
        port = prompt("  Port", default="", cast=str)
        if not port:
            break
        rule = {
            "from": prompt("  From role", default=roles[0]),
            "to": prompt("  To role", default=roles[-1]),
            "port": int(port),
            "protocol": prompt("  Protocol", default="tcp"),
        }
        if rule not in data["network_rules"]:
            data["network_rules"].append(rule)


def write_challenge(path: str, data: dict) -> None:
    order = ["challenge", "vm_templates", "network_rules", "vmid_range_start",
             "vmid_range_end", "default_ttl_hours", "flag", "points"]
    ordered = {k: data[k] for k in order if k in data}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(ordered, f, default_flow_style=False, sort_keys=False)


# ---------- main ----------

def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else prompt("Image URL")
    default_name, default_slug = derive_names(url)

    cfg = load_config()
    node = cfg["THEPOND_PROXMOX_NODE"]
    api = connect_api(cfg)
    ssh = connect_ssh(cfg)

    try:
        print()
        slug = prompt("Challenge slug", default=default_slug)
        yaml_dir = prompt("Challenge yml directory", default="vars/challenges")
        yaml_path = os.path.join(yaml_dir, f"{slug}.yml")
        data = load_challenge(yaml_path, slug)
        if data["vm_templates"]:
            print(f"  (existing {slug}.yml: "
                  f"{', '.join(vm['role'] for vm in data['vm_templates'])})")

        name = prompt("Template name", default=default_name)
        if dns_safe(name) != name:
            name = dns_safe(name)
            print(f"  Proxmox needs a DNS-safe name — using '{name}'")
        role = prompt("Role in this challenge", default="target")
        storage = prompt("Storage", default="local-lvm")
        memory = prompt("Memory (MB)", default=1024, cast=int)
        cores = prompt("Cores", default=1, cast=int)
        legacy = prompt("Legacy hardware - IDE disk, e1000 NIC (y/n)",
                        default="n").lower().startswith("y")

        vmid = next_free_vmid(api, TEMPLATE_VMID_START, TEMPLATE_VMID_END)
        vmid = prompt("Template VMID", default=vmid, cast=int)

        print()
        archive = download(ssh, url, slug)
        search_dir = extract(ssh, archive)
        disk = choose_disk(find_disks(ssh, search_dir))

        print()
        create_template(api, ssh, node, vmid, name, storage, disk, memory, cores, legacy)
        print(f"\nTemplate {vmid} ({name}) created.")

        static_ip = prompt("Static IP", default=suggest_ip(data))
        data["vm_templates"] = [v for v in data["vm_templates"] if v.get("role") != role]
        data["vm_templates"].append(
            {"name": name, "role": role, "static_ip": static_ip}
        )

        roles = [v["role"] for v in data["vm_templates"]]
        prompt_network_rules(data, roles)

        data["vmid_range_start"] = prompt("Session VMID range start",
                                          default=data.get("vmid_range_start", 1301), cast=int)
        data["vmid_range_end"] = prompt("Session VMID range end",
                                        default=data.get("vmid_range_end", 1399), cast=int)
        data["default_ttl_hours"] = prompt("Default TTL (hours)",
                                           default=data.get("default_ttl_hours", 3), cast=int)
        data["flag"] = prompt("Flag", default=data.get("flag", f"pond{{{slug}_root}}"))
        data["points"] = prompt("Points", default=data.get("points", 100), cast=int)

        write_challenge(yaml_path, data)
        print(f"\nWrote {yaml_path}")
        print(f"Cleanup when done:  rm -rf {WORK_DIR}/{slug}")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()