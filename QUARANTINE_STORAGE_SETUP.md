# Challenge uploads — step 8: quarantine storage

Requires step 7's `challenge_ingestion` package and server policy constants.
This module stores bytes only. It does not enable an upload route or scan images.

## Apply

Add these files to the matching repository directories:

- `challenge_ingestion/storage.py`
- `tests/test_quarantine_storage.py`

There are no new dependencies, model imports, database changes or environment
settings to apply in this step. Configure the actual storage location when the
upload service is integrated. This implementation targets Linux/POSIX with
directory-descriptor operations, O_NOFOLLOW and directory fsync; it intentionally
has no weaker Windows fallback. Test local workflows on Linux/WSL if necessary.

## Storage contract

`QuarantineStorage(root)` accepts an absolute server-configured directory outside
the public web directory. The parent must already exist. It creates only the
final directory, with mode 0700; existing roots must be owned by the service user
with no group/other access. It rejects symlink path components and never changes
permissions on an existing directory. Restrict ancestor directory administration
to trusted operators. Mount policy, disk quotas and non-executable mounts are
deployment responsibilities; this module gives stored files mode 0600.

Use the storage object as a context manager to close its directory descriptor.
It retains that descriptor so a later directory rename cannot redirect an
in-progress upload to a different path. The store is private to trusted services;
it does not defend against a compromised process running as the same OS user.

### Operations

| Operation | Behaviour |
|---|---|
| `save(stream, remaining_bytes=..., max_bytes=...)` | Writes a binary stream in bounded chunks, enforces byte budget, calculates SHA-256 and returns a `StoredFile` |
| `open(storage_key)` | Opens a regular private object for reading; caller must authorize access first |
| `copy(storage_key, remaining_bytes=..., max_bytes=...)` | Creates a separately owned object for a revised submission, recalculating size/hash |
| `delete(storage_key)` | Explicit cleanup of one object; returns false if already absent |

`StoredFile` contains `storage_key`, `size_bytes` and `sha256`, suitable for the
corresponding `SubmissionFile` fields. Keys are generated on the server; user
filenames and logical package paths are never used as storage paths. The original
filename and logical path stay in database metadata. Never accept an arbitrary
storage key from a browser without checking its authorized database ownership.

Uploads use exclusive temporary files, fsync and an atomic no-overwrite hard-link
publication step within the same directory. Final keys are returned only after
the write completes. Ordinary read/write failures remove partial files; a final
name collision never overwrites or removes the pre-existing object. Symlink reads,
directories, FIFOs and multiply linked objects are rejected. This module does not
extract archives, execute files or expose direct filesystem paths to the client.

### Example for the upcoming upload service

```python
from challenge_ingestion.storage import QuarantineStorage

# All values below are service-controlled; authorize the request first.
with QuarantineStorage(configured_private_root) as storage:
    stored = storage.save(upload_stream, remaining_bytes=reserved_byte_budget)
    # Insert SubmissionFile using stored.storage_key, stored.size_bytes,
    # stored.sha256 and the separately validated metadata.
```

The service owns and closes `upload_stream`; storage does not close it.

## Required integration work

- Reserve submission-wide and user/system quota atomically before streaming.
  `remaining_bytes` is a previously reserved budget, not a client-provided value.
  This module enforces that budget for one operation but cannot aggregate concurrent
  requests or consult database totals. The 40 GiB package policy is not automatically
  enforced across independent calls here.
- `max_bytes` can tighten the server's 20 GiB per-image ceiling, not relax it.
  Request size, timeout and reverse-proxy limits must be configured separately.
- Complete the physical upload before creating its file row. If the database
  transaction fails, explicitly clean up the new unreferenced object.
- Recover from process termination/power loss with reconciliation. Crashes can
  leave `.part-*` files, unreferenced final objects or, during publication, both
  names for one inode. Reconcile against live jobs and committed references before
  deleting anything. There is no automatic orphan sweeper yet.
- Reserve quota before copying unchanged files to a revision. Copies consume
  separate storage and preserve old revisions; content hashes do not authorize
  sharing or deleting another submission's file.
- Only delete objects after permission/reference/retention checks. The method
  deliberately does not delete database rows or check those policies itself.
- Files may be empty or contain arbitrary bytes at this stage. The next validator
  must verify declared type, actual content, image backing-file references, format,
  hash consistency and required-file presence before approval or publication.
- Do not expose this directory as static content. Any download endpoint must
  authorize the recipient and use controlled response headers.

## Tests

```bash
python3 -m pytest tests/test_quarantine_storage.py -q
```

33 storage tests passed. The combined storage, requirements, initialization and
model suite passed 180 tests. Tests use temporary directories/databases and cover
permissions, hashing, streaming limits, partial cleanup, unsafe keys/symlinks,
special files, collisions, independent copies and simulated filesystem failures.
Existing user-model deprecation warnings remain. No deployment directory, real
upload or live database was created or changed.

Next: `challenge_ingestion/validation.py`, to check manifests and stored file
inventories and produce structured findings for `submission_issues`.
