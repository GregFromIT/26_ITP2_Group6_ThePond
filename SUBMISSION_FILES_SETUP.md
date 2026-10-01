# Challenge uploads — step 2: file inventory

Requires step 1 (`ChallengeSubmission`) to be installed first.

## Apply this update

1. Add `db/submission_file_models.py` and `tests/test_submission_file_models.py`
   to the matching directories in your repository.
2. Add this line to `db/__init__.py`, retaining the step-one import:

   ```python
   from db.submission_file_models import SubmissionFile
   ```

The ZIP includes the updated `db/__init__.py` for the original supplied repo
plus step one. If you have other local edits, add the import rather than
overwriting that file. `db/submission_models.py` is unchanged in this step.

No live database changes have been applied. Model registration alone does not
create a table. We will integrate table creation after the models are complete;
do not run the old initializer just to apply this file, because it also seeds
challenges through Proxmox.

## What gets recorded

Each `SubmissionFile` row tracks one completed stored upload:

| Field | Purpose |
|---|---|
| `file_id` | File record identifier |
| `submission_id` | Submission this file belongs to |
| `file_role` | Purpose, such as image or instructions; the validator will enforce allowed roles |
| `logical_path` | Normalized location within the submitted package, such as `vms/target.qcow2` |
| `original_filename` | Original name for display, not for filesystem access |
| `storage_key` | Unique server-generated reference to the quarantined object |
| `size_bytes` | Actual stored size, calculated by the upload service |
| `sha256` | Lowercase SHA-256 digest calculated by the upload service |
| `detected_media_type` | Optional result from content inspection |
| `validation_status` | `pending`, `passed`, `failed` or `review_required` |
| `validated_at` | Timestamp set by the validator when it records results |
| `created_at` | UTC creation time |

The file bytes remain in restricted storage. The upload service must finish
writing the object and calculating size/hash before creating this record;
interrupted uploads will need storage cleanup. The model neither writes nor
deletes physical files and does not prove that a referenced object exists.

Files start `pending`. A matching name or well-formed hash does not mean that
content is safe or that the whole submission is complete. Scanning, path
normalization, allowed formats, size limits, timestamp/status coordination,
authorization and preventing edits to frozen revisions belong in the upcoming
application service.

Duplicate logical paths within a submission are rejected. Multiple files with
the same role are allowed (for example, several VM images). Different revisions
may use the same logical paths and hashes, but this schema requires distinct
storage keys. The storage service should make server-side copies for unchanged
files when assembling a revision. Shared immutable objects would require a
separate object-ownership design before enabling shared storage references.

Deleting a submission with file records is blocked, preserving its inventory.
Any future retention process must deliberately handle both records and objects.
The composite unique constraint on `(submission_id, file_id)` prepares the
issue model to reject file references belonging to another submission.

## Tests

Run from the repository root in your test environment:

```bash
python3 -m pytest tests/test_submission_models.py tests/test_submission_file_models.py -q
```

Verified: 31 tests passed (16 step-one tests and 15 file-inventory tests), using
Flask-SQLAlchemy 3.1.1 and SQLAlchemy 2.0.51. Only temporary in-memory databases
were used. Existing user defaults still emit `datetime.utcnow()` warnings.

Next: the issue and job models. Issues reference jobs, so their foreign-key
integration test will require both models to be registered.
