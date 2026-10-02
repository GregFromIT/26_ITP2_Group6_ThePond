"""Server-owned version-one package policy and manifest schema.

This defines requirements only. JSON Schema checks shape and bounds; later
validation must check references, file content, permissions, policy and VM
readiness. An uploader cannot supply a replacement schema or upload limits.
"""

from copy import deepcopy
from dataclasses import dataclass


SCHEMA_VERSION = 1
CHALLENGE_TYPES = ("vm", "scored_vm", "container_lab", "offline")
# Initial operator policy, not a statement of available Proxmox capacity.
MAX_VMS = 8
MAX_CPU_CORES_PER_VM = 8
MAX_MEMORY_MB_PER_VM = 16384
MAX_DISK_GB_PER_VM = 128
MAX_IMAGE_BYTES = 20 * 1024**3
MAX_TOTAL_IMAGE_BYTES = 40 * 1024**3
MAX_MANIFEST_BYTES = 256 * 1024
ALLOWED_IMAGE_SUFFIXES = (".qcow2",)


def _object(properties, required):
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _string(maximum):
    return {"type": "string", "minLength": 1, "maxLength": maximum, "pattern": r"\S"}


def _integer(minimum, maximum):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


_ROLE = {"type": "string", "maxLength": 50, "pattern": r"^[a-z][a-z0-9_-]*(?![\s\S])"}
_SOURCE = {
    "oneOf": [
        _object({"kind": {"const": "approved_template"}, "reference": _string(100)},
                ["kind", "reference"]),
        _object({"kind": {"const": "image"}, "path": {
            "type": "string", "maxLength": 240,
            # Portable package-relative image paths; no user-selected host paths.
            "pattern": r"^images/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.qcow2(?![\s\S])",
        }}, ["kind", "path"]),
    ]
}
_VM = _object({
    "role": _ROLE, "name": _string(100), "source": _SOURCE,
    "cpu_cores": _integer(1, MAX_CPU_CORES_PER_VM),
    "memory_mb": _integer(128, MAX_MEMORY_MB_PER_VM),
    "disk_gb": _integer(1, MAX_DISK_GB_PER_VM),
    "boot_order": _integer(1, MAX_VMS),
    "is_user_accessible": {"type": "boolean"},
}, ["role", "name", "source", "cpu_cores", "memory_mb", "disk_gb", "boot_order", "is_user_accessible"])
_NETWORK_RULE = _object({
    "from_role": _ROLE, "to_role": _ROLE,
    "protocol": {"enum": ["tcp", "udp"]}, "port": _integer(1, 65535),
}, ["from_role", "to_role", "protocol", "port"])
_FLAG = _object({
    "vm_role": _ROLE, "name": _string(100),
    "flag_hash": {"type": "string", "pattern": r"^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64},
    "points": _integer(1, 100000), "sequence_number": _integer(1, 1000),
}, ["vm_role", "name", "flag_hash", "points"])
_MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    **_object({
        "schema_version": {"const": SCHEMA_VERSION, "type": "integer"},
        "challenge_type": {"enum": list(CHALLENGE_TYPES)},
        "docker_challenge_key": {"type": "string", "maxLength": 160, "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*(?![\s\S])"},
        "workstation_template_id": _integer(1, 2147483647),
        "title": _string(100), "description": _string(10000),
        "instructions": _string(20000), "category": _string(50),
        "difficulty": {"enum": ["beginner", "intermediate", "advanced"]},
        "time_limit_minutes": _integer(1, 1440),
        "vms": {"type": "array", "minItems": 0, "maxItems": MAX_VMS, "items": _VM},
        "network_rules": {"type": "array", "maxItems": 128, "uniqueItems": True, "items": _NETWORK_RULE},
        "flags": {"type": "array", "maxItems": 100, "items": {**deepcopy(_FLAG), "required": ["name", "flag_hash", "points"]}},
    }, ["schema_version", "challenge_type", "title", "description", "instructions",
        "category", "difficulty", "vms", "network_rules", "flags"]),
    "allOf": [
        {"if": {"properties": {"challenge_type": {"enum": ["vm", "scored_vm"]}}},
         "then": {"properties": {"vms": {"minItems": 1}, "flags": {"items": {"required": ["vm_role"]}}}},
         "else": {"properties": {"vms": {"maxItems": 0}, "network_rules": {"maxItems": 0},
                                 "flags": {"items": {"not": {"required": ["vm_role"]}}}}}},
        {"if": {"properties": {"challenge_type": {"const": "scored_vm"}}},
         "then": {"properties": {"flags": {"minItems": 1}}}},
        {"if": {"properties": {"challenge_type": {"const": "vm"}}},
         "then": {"properties": {"flags": {"maxItems": 0}}}},
        {"if": {"properties": {"challenge_type": {"const": "container_lab"}}},
         "then": {"required": ["docker_challenge_key", "workstation_template_id"]},
         "else": {"not": {"anyOf": [{"required": ["docker_challenge_key"]},
                                     {"required": ["workstation_template_id"]}]}}},
    ],
}


def get_manifest_schema(schema_version=SCHEMA_VERSION):
    """Return an independent schema copy; reject unsupported versions."""
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ValueError("Unsupported challenge schema version")
    return deepcopy(_MANIFEST_SCHEMA)


@dataclass(frozen=True)
class FileRequirement:
    logical_path: str
    file_role: str
    required_by_vm_roles: tuple[str, ...]
    allowed_suffixes: tuple[str, ...] = ALLOWED_IMAGE_SUFFIXES
    max_bytes: int = MAX_IMAGE_BYTES


def required_image_files(validated_manifest):
    """Derive image inventory only AFTER schema and reference validation.

    This helper does not validate input or touch storage. A shared image path
    yields one requirement with all dependent VM roles. Approved template
    references require verification in the server's approved-template catalog,
    not an image upload. Return immutable requirements for the form/validator.
    """
    if (type(validated_manifest.get("schema_version")) is not int
            or validated_manifest["schema_version"] != SCHEMA_VERSION
            or validated_manifest.get("challenge_type") not in CHALLENGE_TYPES):
        raise ValueError("Unsupported challenge type or schema version")
    paths = {}
    for vm in validated_manifest["vms"]:
        if vm["source"]["kind"] == "image":
            paths.setdefault(vm["source"]["path"], []).append(vm["role"])
    return tuple(FileRequirement(path, "image", tuple(roles)) for path, roles in sorted(paths.items()))


def execution_type(manifest):
    """After schema validation; scored_vm remains a supported v1 manifest name."""
    return "vm" if manifest["challenge_type"] in ("vm", "scored_vm") else manifest["challenge_type"]
