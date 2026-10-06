"""Read-only submission validation; returns findings without database writes.

Image inspection and approved-template verification are trusted service adapters,
not user-supplied callbacks. Missing/failing adapters block success. No uploaded
code is executed here. A successful report means ready for review, not publish.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import re

from jsonschema import Draft202012Validator

from . import requirements as policy
from .storage import CHUNK_BYTES, StorageError


@dataclass(frozen=True)
class Finding:
    error_code: str
    message: str
    field_path: str | None = None
    file_id: int | None = None
    severity: str = "error"


@dataclass(frozen=True)
class FileRecord:
    file_id: int
    submission_id: int
    logical_path: str
    file_role: str
    storage_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[Finding, ...]

    @property
    def passed(self):
        return not self.findings

    @property
    def suggested_status(self):
        if any(f.error_code in {"CHECK_UNAVAILABLE", "STORAGE_UNAVAILABLE", "INVALID_INVENTORY"}
               for f in self.findings):
            return "validation_failed"
        return "ready_for_review" if self.passed else "needs_changes"


class ManifestError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def _invalid_number(value):
    raise ManifestError("Only integer numbers are allowed")


def _strings(value, depth=0):
    if depth > 32:
        raise ManifestError("Manifest nesting is too deep")
    if isinstance(value, str):
        if any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 for c in value):
            raise ManifestError("Manifest contains unsupported characters")
    elif isinstance(value, dict):
        for key, item in value.items():
            _strings(key, depth + 1)
            _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _strings(item, depth + 1)


def parse_manifest(data):
    """Parse bounded UTF-8 JSON bytes; reject duplicate keys and noninteger numbers."""
    if not isinstance(data, bytes) or len(data) > policy.MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest must be UTF-8 JSON within the server size limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_invalid_number, parse_float=_invalid_number)
        _strings(value)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ManifestError("Invalid or unsupported JSON manifest") from error
    if not isinstance(value, dict):
        raise ManifestError("Manifest must be a JSON object")
    return value


def content_digest(manifest, files):
    """Fingerprint a validated manifest and inventory before freezing a revision.

    The uploader/worker must use this same canonicalization. Files are ordered
    by logical path. Server IDs, original names and validation state are excluded.
    This does not validate data, reserve files or protect against concurrent edits.
    """
    inventory = sorted(({
        "logical_path": f.logical_path, "file_role": f.file_role,
        "storage_key": f.storage_key, "size_bytes": f.size_bytes, "sha256": f.sha256,
    } for f in files), key=lambda item: item["logical_path"])
    value = json.dumps({"manifest": manifest, "files": inventory}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_submission(manifest_bytes, files, *, submission_id, challenge_type,
                        schema_version, expected_digest, storage,
                        verify_template=None, inspect_image=None):
    """Validate a frozen revision snapshot, without updating files or database.

    verify_template(vm) must return exactly True only for an authorized approved
    template compatible with the VM spec and not conflicting with live table
    uniqueness. inspect_image(handle, dependent_vms) must return exactly True
    only after isolated, bounded deep inspection (format, backing references,
    encryption, virtual size and security policy). False rejects content; other
    returns or exceptions indicate unavailable infrastructure. Adapters must set
    their own timeouts and must not boot images or run parsers on the web host.
    """
    issues = []
    def add(code, message, path=None, file_id=None):
        issues.append(Finding(code, message, path, file_id))
    try:
        manifest = parse_manifest(manifest_bytes)
    except ManifestError:
        return ValidationReport((Finding("INVALID_MANIFEST", "Provide a valid, bounded UTF-8 JSON object."),))

    validator = Draft202012Validator(policy.get_manifest_schema())
    for error in validator.iter_errors(manifest):
        # Do not echo schema error.message: it can include flag hashes or inputs.
        safe_path = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in error.absolute_path)
        if error.validator == "required":
            for key in error.validator_value:
                if key not in error.instance:
                    add("MISSING_FIELD", "A required field is missing.", safe_path.rstrip("/") + "/" + key)
        else:
            add("INVALID_FIELD", "Field does not meet the server's format or limits.", safe_path)
        if len(issues) >= 100:
            break
    if issues:
        return ValidationReport(tuple(issues))
    if (type(schema_version) is not int or manifest["schema_version"] != schema_version
            or manifest["challenge_type"] != challenge_type):
        add("SUBMISSION_MISMATCH", "Manifest type/version differs from the submission record.")

    roles = set()
    templates = set()
    for i, vm in enumerate(manifest["vms"]):
        if vm["role"] in roles:
            add("DUPLICATE_VM_ROLE", "VM roles must be unique.", f"/vms/{i}/role")
        roles.add(vm["role"])
        source = vm["source"]
        if source["kind"] == "approved_template":
            if source["reference"] in templates:
                add("DUPLICATE_TEMPLATE", "Use a distinct approved template for each VM.", f"/vms/{i}/source")
            templates.add(source["reference"])
    for i, rule in enumerate(manifest["network_rules"]):
        for field in ("from_role", "to_role"):
            if rule[field] not in roles:
                add("UNKNOWN_VM_ROLE", "Network rule refers to an undefined VM role.", f"/network_rules/{i}/{field}")
    flag_names, sequences, flag_hashes = set(), set(), set()
    for i, flag in enumerate(manifest["flags"]):
        if "vm_role" in flag and flag["vm_role"] not in roles:
            add("UNKNOWN_VM_ROLE", "Flag refers to an undefined VM role.", f"/flags/{i}/vm_role")
        name = (flag.get("vm_role"), flag["name"])
        seq = (flag.get("vm_role"), flag.get("sequence_number"))
        if name in flag_names or flag["flag_hash"] in flag_hashes or (seq[1] is not None and seq in sequences):
            add("DUPLICATE_FLAG", "Flag names/sequences within a VM and flag hashes within a challenge must be unique.", f"/flags/{i}")
        flag_names.add(name)
        flag_hashes.add(flag["flag_hash"])
        sequences.add(seq)

    files = tuple(files)
    if len(files) > policy.MAX_VMS:
        add("INVALID_INVENTORY", "Inventory contains more files than the package permits.")
        return ValidationReport(tuple(issues))
    paths, identifiers, keys = {}, set(), set()
    total = 0
    for record in files:
        if (not isinstance(record, FileRecord) or type(record.file_id) is not int or record.file_id <= 0
                or type(record.submission_id) is not int or record.submission_id != submission_id
                or not isinstance(record.logical_path, str) or not isinstance(record.file_role, str)
                or not isinstance(record.storage_key, str) or not re.fullmatch(r"[0-9a-f]{32}\.blob", record.storage_key)
                or type(record.size_bytes) is not int or record.size_bytes < 0
                or not isinstance(record.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", record.sha256)):
            add("INVALID_INVENTORY", "Inventory contains an invalid or foreign file record.")
            continue
        if record.logical_path in paths or record.file_id in identifiers or record.storage_key in keys:
            add("INVALID_INVENTORY", "Inventory contains duplicate paths, IDs or storage objects.")
        paths[record.logical_path] = record
        identifiers.add(record.file_id)
        keys.add(record.storage_key)
        total += record.size_bytes
    if any(f.error_code == "INVALID_INVENTORY" for f in issues):
        return ValidationReport(tuple(issues))
    if total > policy.MAX_TOTAL_IMAGE_BYTES:
        add("PACKAGE_TOO_LARGE", "Combined image size exceeds the server package limit.")
    requirements = policy.required_image_files(manifest)
    expected_paths = {r.logical_path for r in requirements}
    for requirement in requirements:
        if requirement.logical_path not in paths:
            add("MISSING_IMAGE", "Upload the VM image required by this path.", requirement.logical_path)
    for path, record in paths.items():
        if path not in expected_paths:
            add("UNEXPECTED_FILE", "File is not declared by this manifest.", file_id=record.file_id)
        if record.file_role != "image":
            add("WRONG_FILE_ROLE", "Declared VM images must have the image role.", file_id=record.file_id)
        if record.size_bytes == 0 or record.size_bytes > policy.MAX_IMAGE_BYTES:
            add("INVALID_FILE_SIZE", "Image is empty or exceeds the per-file size limit.", file_id=record.file_id)
    actual_digest = content_digest(manifest, files)
    if (not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            or not hmac.compare_digest(actual_digest, expected_digest)):
        add("CONTENT_CHANGED", "Submission no longer matches its frozen content digest.")
    if issues:
        return ValidationReport(tuple(issues))

    def adapter(callback, args, failure_code, path=None, file_id=None):
        if callback is None:
            add("CHECK_UNAVAILABLE", "Required verification service is not configured.", path, file_id)
            return
        try:
            result = callback(*args)
        except Exception:
            add("CHECK_UNAVAILABLE", "Required verification could not complete; retry after service recovery.", path, file_id)
            return
        if result is False:
            add(failure_code, "Source did not pass the server's approval or content checks.", path, file_id)
        elif result is not True:
            add("CHECK_UNAVAILABLE", "Verification service returned an invalid result.", path, file_id)

    for i, vm in enumerate(manifest["vms"]):
        if vm["source"]["kind"] == "approved_template":
            adapter(verify_template, (vm,), "TEMPLATE_REJECTED", f"/vms/{i}/source")
    for record in files:
        try:
            with storage.open(record.storage_key) as handle:
                digest, size = hashlib.sha256(), 0
                while True:
                    chunk = handle.read(min(CHUNK_BYTES, record.size_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > record.size_bytes:
                        break
                    digest.update(chunk)
                if size != record.size_bytes or not hmac.compare_digest(digest.hexdigest(), record.sha256):
                    add("FILE_CHANGED", "Stored file differs from the submitted size or hash.", file_id=record.file_id)
                    continue
                handle.seek(0)
                if handle.read(4) != b"QFI\xfb":
                    add("INVALID_IMAGE_FORMAT", "File does not have a QCOW2 header.", file_id=record.file_id)
                    continue
                handle.seek(0)
                dependent_vms = tuple(vm for vm in manifest["vms"] if vm["source"].get("path") == record.logical_path)
                adapter(inspect_image, (handle, dependent_vms), "IMAGE_REJECTED", file_id=record.file_id)
        except (OSError, StorageError):
            add("STORAGE_UNAVAILABLE", "Stored file could not be read safely; recover storage before retrying.", file_id=record.file_id)
    return ValidationReport(tuple(issues))
