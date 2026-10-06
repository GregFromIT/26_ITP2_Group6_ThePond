# Challenge uploads — step 6: database initialization

Requires all five model updates and their imports from steps 1–5.

## Apply

Replace these files with the supplied versions:

- `db/init_database.py`: canonical initializer using the web app factory and its
  configured `pond` database binding.
- `db/init_databases.py`: compatibility wrapper around the canonical initializer.

Add `tests/test_database_initialization.py`. No model changes or additional
imports in `db/__init__.py` are needed in this step.

The original singular initializer seeded challenges automatically. It now only
does so with `--seed-challenges`. The plural entry point has the same behaviour,
preventing the two commands from drifting apart. Existing application and
provisioner database configuration files were not changed.

## Initialize on your server

Use the repository root, the web server's Python environment and the same
environment settings as the web server. Back up the existing database using
your normal SQLite backup procedure before a schema change.

Inspect first:

```bash
python3 -m db.init_database --check
```

The command prints the selected database location. Confirm it matches the web
application database. Missing upload tables are expected before applying this
update; a missing table causes a nonzero exit status. A missing database file
is reported without creating it.

Create missing registered tables and default roles, then verify:

```bash
python3 -m db.init_database
python3 -m db.init_database --check
```

Normal initialization:

- Adds missing SQLAlchemy model tables, including the five upload tables.
- Adds missing `user`, `manager` and `sysadmin` roles without overwriting them.
- Preserves existing accounts, challenges, submissions and other records.
- Does not seed challenges, create accounts or contact Proxmox.
- Can be run again; existing compatible tables and roles are retained.

It creates all registered Pond model tables, not exclusively the five upload
tables. It does not initialize the separate legacy `instances` table from
`db/schema.sql`; that remains the legacy store's responsibility.

`--check` performs no table creation or application-data writes. Normal app
startup still occurs, including development instance/secret-key setup, and the
existing database connection hooks can configure SQLite WAL settings. This is
not a filesystem-forensic read-only mode. Production requires the same secret
key configuration as the web server.

## Schema conflicts

Before adding tables, the initializer checks existing registered tables for
missing columns. On existing upload tables it also compares declared foreign
keys, uniqueness constraints, checks and indexes, including the partial index
that prevents concurrent jobs for one submission. Conflicting default role
definitions also stop initialization before tables are added.

If it reports an existing-table mismatch, stop and review a migration. This
command does not drop/rebuild tables, add missing columns to old tables or repair
missing indexes in existing tables. Equivalent hand-written check expressions
may still be flagged for review. It is not a complete schema diff: column types,
nullability, defaults and constraints on older tables are not all compared.
Unexpected operational failures during table creation can leave partial schema
progress; resolve the cause and rerun. Do not delete tables to silence a check.

## Optional legacy challenge seeding

Only when explicitly desired and Proxmox is configured:

```bash
python3 -m db.init_database --seed-challenges
```

This invokes the existing YAML challenge seeder after initialization. It uses
the existing provisioner configuration and can commit partial seeding progress.
It is not the new upload/publication workflow. `--check` and `--seed-challenges`
cannot be combined. No real Proxmox seeding was run during this update.

## Tests

```bash
python3 -m pytest tests/test_database_initialization.py tests/test_submission_models.py tests/test_submission_file_models.py tests/test_submission_job_models.py tests/test_submission_issue_models.py tests/test_notification_models.py -q
```

110 tests passed, including 14 initialization tests. They cover empty/existing
databases, repeated runs, preservation of accounts/challenges, absent database
files, check-only behaviour, schema/role conflicts, the compatibility wrapper,
explicit seeding dispatch, seeding failure and the actual web app factory with
a temporary configured database. Seeder calls were mocked; all databases were
temporary. Existing `datetime.utcnow()` warnings remain in the user model.

No deployed database has been opened or updated by this task. Run the commands
above in your deployment after applying these files.

Next: define the server-controlled challenge package requirements, which the
upload route and validation worker will use to decide what is required.
