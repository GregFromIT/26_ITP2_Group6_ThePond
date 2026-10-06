# Challenge uploads — step 7: package requirements

This step defines the version-one manifest contract and server-owned limits.
It makes no database changes and does not enable uploads or VM importing.

## Apply

Add the following new files/directories to the repository:

- `challenge_ingestion/__init__.py`
- `challenge_ingestion/requirements.py`
- `requirements-ingestion.txt`
- `tests/test_challenge_requirements.py`
- `examples/challenge_uploads/vm-template.json`
- `examples/challenge_uploads/scored-vm-image.json`

No changes to `db/__init__.py` are needed. Install the schema-validation dependency
in the application's Python environment, alongside existing requirements:

```bash
python3 -m pip install -r requirements-ingestion.txt
```

## Manifest format

Metadata may be entered in the future form or uploaded as `challenge.json`. Both
produce the same manifest object stored in `challenge_submissions.manifest_json`.
An extra physical manifest file is not required when the form supplies metadata.
These new manifests are not drop-in replacements for existing legacy YAML seeds.

| Content | Requirement |
|---|---|
| `schema_version` | Exactly integer `1` |
| `challenge_type` | `vm` for unscored exercises; `scored_vm` for scored exercises |
| `title`, `description`, `instructions` | Required nonblank text; instructions are inline |
| `category` | Required text |
| `difficulty` | `beginner`, `intermediate` or `advanced` |
| `time_limit_minutes` | Optional integer, 1–1440 |
| `vms` | 1–8 VM definitions |
| `network_rules` | Required list, possibly empty |
| `flags` | Required list: empty for `vm`; at least one flag for `scored_vm` |

Each VM defines `role`, `name`, `source`, `cpu_cores`, `memory_mb`, `disk_gb`,
`boot_order` and `is_user_accessible`. Only one source is accepted:

- `{"kind": "approved_template", "reference": "example-approved-template"}`:
  a server-owned catalog reference, not an arbitrary Proxmox node/VM ID. Requires
  no image upload. The approved-template catalog resolver is still to be built;
  the example reference is a placeholder, not an existing template.
- `{"kind": "image", "path": "images/target.qcow2"}`: requires that image in
  the uploaded inventory. Image import/inspection is not implemented yet. This
  source type must not be published until those services support it.

The first image format is QCOW2 only. A filename is just a preliminary check;
the validator must inspect actual format, backing-file references and image
properties in isolation. No archives, scripts, ISO or OVA imports are enabled
by this policy. Extra files not declared by the manifest should be rejected by
the upcoming validator. Shared image paths produce one upload requirement with
all dependent VM roles. Each revision still uses its own storage objects.

Network rules contain `from_role`, `to_role`, `protocol` (`tcp` or `udp`) and a
port. Empty rules are permitted to express no requested inter-VM connections.
This schema does not configure a firewall or guarantee isolation. Publication
must enforce a server-owned isolated network policy; submitted rules cannot
select host bridges, management networks or public exposure. If enforcement is
unavailable, publication must fail rather than claim isolation.

Flags specify `vm_role`, `name`, `flag_hash`, `points` and optional
`sequence_number`. Hashes follow the existing application's convention:
SHA-256 of `flag.strip().lower().encode()`. The image example uses the dummy flag
`pond{example_only}`. Do not use that flag in a real challenge. The future admin
form may accept a flag securely and hash it server-side before manifest storage.
Hashes and other answer material must not appear in student-visible manifests.

## Initial server policy

These are conservative editable deployment defaults, not measured cluster
capacity or guarantees that a VM can run:

| Limit | Initial value |
|---|---|
| VM count | 8 |
| CPU cores per VM | 1–8 |
| Memory per VM | 128–16384 MB |
| Disk capacity per VM | 1–128 GB |
| Uploaded image size | 20 GiB per image |
| Combined uploaded image size | 40 GiB |
| Manifest size | 256 KiB |
| Network rules | 128 |
| Flags | 100 |

CPU/memory/disk and list limits are in the manifest schema. Byte limits are
constants for the upcoming streaming upload and validation services to enforce.
They do not change Flask's current small request limit or reverse-proxy settings.
Declared disk capacity and uploaded byte count are different checks.

Administrators change policy in server code; submitted manifests cannot replace
it. Unknown fields, including approval/status fields and client-supplied limits,
are rejected. Changing the version-one contract after deployment should be a
deliberate version/migration decision.

## What the next validator must add

- Bounded JSON parsing, duplicate-key rejection, strict handling of numeric and
  control-character values, and agreement with the submission's type/version.
- Unique VM roles, valid flag/network role references, and duplicate flag checks.
- Server-approved template resolution and existing unique-template constraints.
- Actual file presence, format, content, size/hash checks and total byte limits.
- Cluster capacity/quota checks, image compatibility and isolated readiness tests.
- Flag/network mapping and any challenge-specific content requirements.
- Authorized workflow transitions and publication checks.

`get_manifest_schema()` returns a fresh copy for local validation; it does not
fetch the `$schema` URL. `required_image_files()` derives file requirements only
after schema/reference validation. It is not a validator and must not be called
on raw untrusted input. Policy requirements cannot be relaxed by the uploader.

## Verification

```bash
python3 -m pytest tests/test_challenge_requirements.py -q
```

37 requirement tests passed. The combined requirements, initialization and model
suite passed 147 tests. Existing user-model deprecation warnings remain. Examples
pass structural checks only; no VM images are bundled and no template lookups,
uploads or Proxmox operations were performed.

Next: `challenge_ingestion/storage.py` for bounded quarantine storage, followed
by the validator that reports problems through the issue model.
