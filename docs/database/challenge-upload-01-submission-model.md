# Challenge uploads — step 1: submission model

This update adds the submission record only. It does not yet provide an upload
form, file inventory, validation worker, notifications or publication service.
No existing database has been modified.

## Files in this update

- `db/submission_models.py`: the `ChallengeSubmission` model for the new
  `challenge_submissions` table in the existing `pond` binding.
- `db/__init__.py`: one new import registers the model with SQLAlchemy.
- `tests/test_submission_models.py`: 16 database integration test cases.

Copy the new model and test into the matching directories of the current repo.
For `db/__init__.py`, add this import (or use the supplied version if your file
still matches the attached repository):

```python
from db.submission_models import ChallengeSubmission
```

Importing the model registers metadata but does not create a database table.
We will settle the initializer and apply table creation after the remaining
models are ready. Do not run the older initializer solely for this update:
it also attempts Proxmox challenge seeding.

## What this model does

Stores draft metadata, uploader identity, lifecycle status, submission digest,
approval, publication link and timestamps. A revised upload links to its
predecessor through `supersedes_submission_id`; it does not erase it.

The database enforces allowed statuses, a JSON-object manifest, a valid digest
shape, consistent approval and publication fields, foreign keys, and a unique
published-challenge link. Explicit user relationships distinguish uploader from
approver. Referenced users and published challenges cannot be deleted while
these records reference them; deactivate users instead.

The future application service must enforce permissions, freeze submitted
content, calculate digests, validate files, enforce allowed status transitions,
prevent revision cycles, and publish only after all checks. A digest format
check cannot verify the digest's contents. This model does not scan anything.

When editing a draft, assign a replacement `manifest_json` dictionary; nested
in-place JSON edits are not tracked automatically. Store only metadata here,
not uploaded image bytes. SQLAlchemy writes `updated_at` on model updates;
raw SQL updates must set it explicitly. Use UTC throughout (SQLite does not
retain timezone offsets in its SQLAlchemy datetime representation).

## Testing

From the repository root, in an environment with its database dependencies
and pytest installed:

```bash
python3 -m pytest tests/test_submission_models.py -q
```

Verified: 16 tests passed using Flask 3.1.3, Flask-SQLAlchemy 3.1.1 and
SQLAlchemy 2.0.51. Tests create only temporary in-memory databases and import
the existing models to check compatibility. Existing user model defaults
emit `datetime.utcnow()` deprecation warnings; they are unrelated to this
update. Web routes and Proxmox operations have not been tested in this step.

Next file: `db/submission_file_models.py`.
