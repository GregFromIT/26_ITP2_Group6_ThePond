# Challenge uploads — step 9: read-only validation

Requires steps 1–8, including `jsonschema` from `requirements-ingestion.txt`.

## Apply

Add `challenge_ingestion/validation.py` and `tests/test_submission_validation.py`
to the matching repository directories. No new dependencies, database tables
or imports in `db/__init__.py` are needed.

The validator returns structured findings. It does not write issues, update
statuses, notify users, import VMs or publish challenges. Worker integration is
the next step. No live database or external verification service was used here.

## Interface

`parse_manifest(data)` accepts bounded UTF-8 JSON bytes and rejects duplicate
keys, invalid JSON, noninteger numbers, unsupported characters and excessive
nesting. The input must then pass the server-owned v1 manifest schema.

`FileRecord` is an immutable snapshot of the persisted file inventory:
`file_id`, `submission_id`, `logical_path`, `file_role`, `storage_key`,
`size_bytes`, `sha256`. The worker must load it from the authorized submission's
database rows, not accept a browser-supplied inventory.

`content_digest(manifest, files)` calculates the canonical SHA-256 fingerprint
used when finalizing a revision. The upload service must first validate metadata
and inventory, finish uploads, then calculate and save this fingerprint while
freezing the revision. The helper does not itself validate/freeze anything.
Ordering manifest keys or inventory rows does not change the fingerprint;
metadata, logical paths, roles, storage keys, sizes and hashes do.

Call `validate_submission()` with manifest bytes, the inventory snapshot,
trusted submission ID/type/version, the previously stored digest, a
`QuarantineStorage` instance and the verification adapters described below.
Do not recalculate the expected digest just before validating changed data;
compare against the value stored when the submission was frozen.

## Checks implemented

- Bounded JSON parsing, schema requirements, field limits and unknown fields.
- Agreement between manifest type/version and submission record.
- Unique VM roles and template references; network/flag references to defined roles.
- Duplicate flag names/sequences within a VM and duplicate hashes in a challenge.
- Inventory ownership, valid keys/hash formats, duplicate IDs/paths/objects.
- Required images, unexpected files, image roles and per-file/package size limits.
- Agreement with the frozen content digest.
- Re-reading stored bytes to verify their size and SHA-256, with bounded reads.
- QCOW2 magic-header preflight, then mandatory deep-inspection adapter invocation.
- Mandatory approval-verification adapter invocation for existing template references.

Missing-file findings carry the expected logical path but no `file_id`. Existing
file findings carry its ID. Messages avoid echoing raw schema values or internal
exception diagnostics (which could contain secrets). Messages and field paths
still require safe escaping in the user interface.

## Verification adapters: required before real use

`verify_template(vm)` is a trusted service callback. It must return exactly
`True` only after checking the server-approved catalog, uploader authorization,
actual template existence, compatibility/resource requirements and live-template
uniqueness constraints. Different catalog aliases must not bypass uniqueness.

`inspect_image(handle, dependent_vms)` receives the open, rewound image and VM
definitions that reference it. It must perform deep inspection in a bounded,
isolated service: real format, backing/external data references, encryption,
virtual disk size, image integrity and teaching-image security policy. It must
use resource limits and timeouts. A four-byte magic-header match is not sufficient.
The callback must not run untrusted image parsers or boot images on the web host.

Both adapters return exactly `False` for rejected content, and exactly `True`
for completed successful verification. Missing adapters, exceptions or other
return values yield `CHECK_UNAVAILABLE` and block success. Do not install an
always-true adapter in production. Tests use fake adapters with dummy bytes;
they do not demonstrate that any real image is safe or bootable.

The concrete approved-template resolver and isolated image-inspection service
are not included in this step. Real submissions will remain blocked until the
relevant adapter is provided. Cluster quota, network enforcement, admin approval
and isolated boot/readiness tests also remain required before publication.

## Findings and status

`ValidationReport.findings` contains immutable `Finding` records with
`error_code`, `message`, `field_path`, `file_id` and `severity`. This first
validator emits blocking errors only. `passed` is true only when there are none.

| Suggested status | Meaning |
|---|---|
| `needs_changes` | Missing/invalid metadata, files, references or content |
| `validation_failed` | Invalid server inventory or unavailable storage/verification services |
| `ready_for_review` | All configured validation checks completed successfully |

These are recommendations for the worker, not automatic database updates or
publication authorization. Structural problems cause an early return before
expensive reads/inspection, so absence of a file-specific issue does not prove
that file was checked. Do not mark every file as passed based on issue absence
when the overall report failed.

The worker must hold the current job lease, freeze submitted content, and save
findings, submission status and notification events in a coordinated transaction.
Each persisted issue uses the job and submission IDs from the trusted job context.
Retries must deduplicate/replace findings within that job while retaining older
run history. Unexpected programming failures must mark the job failed, never pass
the submission. Recheck the digest and file integrity at publication to prevent
changes between validation and import.

## Tests

```bash
python3 -m pytest tests/test_submission_validation.py -q
```

38 validator tests passed. The complete suite for these updates passed 218 tests.
They cover malformed/oversized/ambiguous JSON, schema errors, missing images,
cross-submission inventory, references, changed bytes/digests, unavailable storage
and adapters, and safe findings. No real image scans, template lookups, VM boots,
notifications or live database writes occurred. Existing user-model timestamp
deprecation warnings remain.

Next: worker integration to claim validation jobs and persist findings/status
and notification events, with explicit configuration of verification adapters.
