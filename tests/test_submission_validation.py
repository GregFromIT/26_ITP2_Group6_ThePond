"""Read-only validator tests; inspection adapters are fakes, never real scans."""

from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path

import pytest

from challenge_ingestion import validation as v
from challenge_ingestion.storage import QuarantineStorage


@pytest.fixture
def case(tmp_path):
    manifest = json.loads((Path(__file__).resolve().parents[1] / "examples/challenge_uploads/scored-vm-image.json").read_text())
    with QuarantineStorage(tmp_path / "quarantine") as storage:
        stored = storage.save(io.BytesIO(b"QFI\xfb" + b"test fixture, not a real disk"), remaining_bytes=1000)
        record = v.FileRecord(1, 1, "images/target.qcow2", "image", stored.storage_key, stored.size_bytes, stored.sha256)
        yield manifest, [record], storage


def validate(case, **overrides):
    manifest, files, storage = case
    args = dict(submission_id=1, challenge_type=manifest["challenge_type"], schema_version=1,
                expected_digest=v.content_digest(manifest, files), storage=storage,
                inspect_image=lambda handle, vms: True, verify_template=lambda vm: True)
    args.update(overrides)
    return v.validate_submission(json.dumps(manifest).encode(), files, **args)


def codes(report):
    return {f.error_code for f in report.findings}


def test_valid_image_passes_with_explicit_test_inspector(case):
    calls = []
    def inspector(handle, vms):
        calls.append(vms[0]["role"])
        assert handle.tell() == 0
        return True
    report = validate(case, inspect_image=inspector)
    assert report.passed and report.suggested_status == "ready_for_review"
    assert calls == ["target"]


def test_template_requires_verification_and_no_files(case):
    manifest, _, storage = case
    manifest["vms"][0]["source"] = {"kind": "approved_template", "reference": "approved"}
    case = (manifest, [], storage)
    assert validate(case).passed
    assert "CHECK_UNAVAILABLE" in codes(validate(case, verify_template=None))
    assert "TEMPLATE_REJECTED" in codes(validate(case, verify_template=lambda vm: False))


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}', b'{"a":NaN}',
    b'{"a":Infinity}', b'{"a":1.0}', b'[]', b'null', b'\xff', b'{"x":"\\u0000"}', b'{"x":"\\ud800"}', b'{'])
def test_invalid_json_rejected(raw):
    with pytest.raises(v.ManifestError):
        v.parse_manifest(raw)


def test_manifest_byte_limit_and_nesting(monkeypatch):
    monkeypatch.setattr(v.policy, "MAX_MANIFEST_BYTES", 10)
    with pytest.raises(v.ManifestError):
        v.parse_manifest(b'{"title":"too long"}')
    monkeypatch.setattr(v.policy, "MAX_MANIFEST_BYTES", 10000)
    with pytest.raises(v.ManifestError):
        v.parse_manifest(b'{"nested":' + b'[' * 40 + b'0' + b']' * 40 + b'}')


def test_missing_field_has_actionable_path_without_values(case):
    del case[0]["instructions"]
    report = validate(case)
    assert any(f.error_code == "MISSING_FIELD" and f.field_path == "/instructions" for f in report.findings)


def test_schema_findings_do_not_echo_secret_value(case):
    case[0]["flags"][0]["flag_hash"] = "secret-flag-value"
    report = validate(case)
    assert "INVALID_FIELD" in codes(report)
    assert "secret-flag-value" not in repr(report)


def test_submission_record_mismatch(case):
    assert "SUBMISSION_MISMATCH" in codes(validate(case, challenge_type="vm"))
    assert "SUBMISSION_MISMATCH" in codes(validate(case, schema_version=True))


def test_role_references_and_duplicate_vm_roles(case):
    case[0]["vms"].append(deepcopy(case[0]["vms"][0]))
    case[0]["network_rules"] = [{"from_role": "missing", "to_role": "target", "protocol": "tcp", "port": 80}]
    case[0]["flags"][0]["vm_role"] = "missing"
    report = validate(case)
    assert {"DUPLICATE_VM_ROLE", "UNKNOWN_VM_ROLE"} <= codes(report)


def test_duplicate_flags_rejected(case):
    case[0]["flags"].append(deepcopy(case[0]["flags"][0]))
    assert "DUPLICATE_FLAG" in codes(validate(case))


def test_missing_image_needs_no_file_id(case):
    report = validate((case[0], [], case[2]))
    issue = next(f for f in report.findings if f.error_code == "MISSING_IMAGE")
    assert issue.file_id is None and issue.field_path == "images/target.qcow2"


@pytest.mark.parametrize("changes", [{"submission_id": 2}, {"file_id": True},
    {"storage_key": "../outside"}, {"sha256": "wrong"}, {"size_bytes": -1}])
def test_bad_inventory_rejected_before_reads(case, changes):
    records = [replace(case[1][0], **changes)]
    report = validate((case[0], records, case[2]))
    assert "INVALID_INVENTORY" in codes(report)
    assert report.suggested_status == "validation_failed"


def test_duplicate_inventory_rejected(case):
    report = validate((case[0], case[1] * 2, case[2]))
    assert "INVALID_INVENTORY" in codes(report)


def test_unexpected_file_and_wrong_role(case):
    records = [replace(case[1][0], logical_path="extra.txt", file_role="instructions")]
    assert {"MISSING_IMAGE", "UNEXPECTED_FILE", "WRONG_FILE_ROLE"} <= codes(validate((case[0], records, case[2])))


def test_zero_size_and_package_limit(case, monkeypatch):
    records = [replace(case[1][0], size_bytes=0)]
    assert "INVALID_FILE_SIZE" in codes(validate((case[0], records, case[2])))
    monkeypatch.setattr(v.policy, "MAX_TOTAL_IMAGE_BYTES", 1)
    assert "PACKAGE_TOO_LARGE" in codes(validate(case))


def test_frozen_digest_mismatch_blocks_before_inspection(case):
    def never(*args):
        pytest.fail("Inspector should not run")
    assert "CONTENT_CHANGED" in codes(validate(case, expected_digest="0" * 64, inspect_image=never))


def test_stored_hash_and_size_checked(case):
    for changes in ({"sha256": "0" * 64}, {"size_bytes": case[1][0].size_bytes - 1},
                    {"size_bytes": case[1][0].size_bytes + 1}):
        files = [replace(case[1][0], **changes)]
        assert "FILE_CHANGED" in codes(validate((case[0], files, case[2])))


def test_missing_storage_object_is_technical_failure(case):
    case[2].delete(case[1][0].storage_key)
    report = validate(case)
    assert "STORAGE_UNAVAILABLE" in codes(report) and report.suggested_status == "validation_failed"


def test_renamed_nonimage_rejected(case):
    stored = case[2].save(io.BytesIO(b"not qcow2"), remaining_bytes=100)
    files = [replace(case[1][0], storage_key=stored.storage_key, size_bytes=stored.size_bytes, sha256=stored.sha256)]
    assert "INVALID_IMAGE_FORMAT" in codes(validate((case[0], files, case[2])))


@pytest.mark.parametrize("inspector", [None, lambda *_: None, lambda *_: 1])
def test_missing_or_invalid_inspection_fails_closed(case, inspector):
    report = validate(case, inspect_image=inspector)
    assert not report.passed and "CHECK_UNAVAILABLE" in codes(report)


def test_inspection_rejection_and_exception(case):
    assert "IMAGE_REJECTED" in codes(validate(case, inspect_image=lambda *_: False))
    def failure(*args):
        raise RuntimeError("secret diagnostic")
    report = validate(case, inspect_image=failure)
    assert "CHECK_UNAVAILABLE" in codes(report)
    assert "secret diagnostic" not in repr(report)


def test_digest_is_stable_and_tracks_inventory(case):
    manifest, files, _ = case
    reordered = dict(reversed(list(manifest.items())))
    assert v.content_digest(manifest, files) == v.content_digest(reordered, files)
    assert v.content_digest(manifest, files) != v.content_digest(manifest, [replace(files[0], size_bytes=999)])


def test_validation_does_not_mutate_input(case):
    before = deepcopy(case[0])
    validate(case)
    assert case[0] == before
