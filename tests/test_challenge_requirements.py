"""Contract tests; no file scanning, uploads or Proxmox calls."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from challenge_ingestion.requirements import get_manifest_schema, required_image_files

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "challenge_uploads"


@pytest.fixture
def manifest():
    return json.loads((EXAMPLES / "vm-template.json").read_text())


def errors(manifest):
    return list(Draft202012Validator(get_manifest_schema()).iter_errors(manifest))


def test_schema_is_valid_and_examples_match():
    Draft202012Validator.check_schema(get_manifest_schema())
    for path in EXAMPLES.glob("*.json"):
        assert not errors(json.loads(path.read_text())), path.name


@pytest.mark.parametrize("field", ["title", "instructions", "vms", "network_rules", "flags", "schema_version"])
def test_required_fields(manifest, field):
    del manifest[field]
    assert errors(manifest)


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("schema_version", True),
    ("challenge_type", "custom"), ("instructions", "   "), ("title", "x" * 101),
    ("difficulty", "unknown"), ("time_limit_minutes", 0), ("vms", [])])
def test_bad_metadata_rejected(manifest, field, value):
    manifest[field] = value
    assert errors(manifest)


@pytest.mark.parametrize("field,value", [("cpu_cores", 0), ("cpu_cores", 9),
    ("cpu_cores", True), ("memory_mb", 16385), ("disk_gb", 129), ("is_user_accessible", "yes")])
def test_vm_resource_bounds(manifest, field, value):
    manifest["vms"][0][field] = value
    assert errors(manifest)


@pytest.mark.parametrize("path", ["../target.qcow2", "/images/target.qcow2", "images/../target.qcow2",
    "images/target.iso", "images/target.qcow2\n", "images/target.qcow2.exe", "images\\target.qcow2"])
def test_image_path_policy(manifest, path):
    manifest["vms"][0]["source"] = {"kind": "image", "path": path}
    assert errors(manifest)


def test_upload_cannot_override_policy_or_supply_server_fields(manifest):
    for field in ("approved_by_user_id", "status", "max_image_bytes", "required_files"):
        bad = deepcopy(manifest)
        bad[field] = 1
        assert errors(bad)


def test_source_is_exactly_one_supported_kind(manifest):
    manifest["vms"][0]["source"] = {"kind": "image", "path": "images/target.qcow2", "reference": "template"}
    assert errors(manifest)


def test_scored_challenge_needs_flags(manifest):
    manifest["challenge_type"] = "scored_vm"
    assert errors(manifest)
    manifest["flags"] = [{"vm_role": "target", "name": "Root", "flag_hash": "a" * 64, "points": 100}]
    assert not errors(manifest)
    manifest["challenge_type"] = "vm"
    assert errors(manifest)


def test_template_requires_no_image_and_shared_image_is_deduplicated(manifest):
    assert required_image_files(manifest) == ()
    manifest["vms"][0]["source"] = {"kind": "image", "path": "images/target.qcow2"}
    second = deepcopy(manifest["vms"][0])
    second["role"] = "second"
    manifest["vms"].append(second)
    result = required_image_files(manifest)
    assert len(result) == 1
    assert result[0].required_by_vm_roles == ("target", "second")


def test_schema_copy_cannot_mutate_server_policy():
    schema = get_manifest_schema()
    schema["properties"]["vms"]["maxItems"] = 999
    assert get_manifest_schema()["properties"]["vms"]["maxItems"] == 8


@pytest.mark.parametrize("version", [0, 2, "1", True])
def test_unsupported_schema_version_rejected(version):
    with pytest.raises(ValueError):
        get_manifest_schema(version)
