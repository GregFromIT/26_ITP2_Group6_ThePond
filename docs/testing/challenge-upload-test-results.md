# Verification results

Verified on 2026-09-29 using Python 3.12, temporary SQLite databases, Flask test
clients, private temporary upload directories and test-only VM adapters.

- Upload/ingestion model, storage, requirements, validation, worker, notification,
  HTTP route, review, publication and runtime suites: **312 passed**.
- Existing `tests/test_store.py` and `tests/test_provisioner.py`: **3 passed**.
- Upload browser JavaScript syntax check: passed.

Total: **315 passing tests**. Existing SQLAlchemy user-model timestamp defaults
produce Python `datetime.utcnow()` deprecation warnings (443 in the ingestion
suite). No live database migration, real image scan, Proxmox import/boot, external
message, or production deployment was performed.

The integration test covers missing files, creation of a corrected revision,
file upload, validation, administrator approval, publication queue, all relevant
live-table writes and notification delivery. The VM adapter is a deterministic
fake; it verifies orchestration, not hypervisor security or readiness.

Reproduce the tests from the repository root after installing the application,
ingestion and test dependencies:

```bash
python -m pytest tests -q
```

The legacy `pond-sec/tests/test_flow.py` suite is not included: it references the
old `app.db`/`app.seed` interfaces absent from the supplied app. This report does
not claim that legacy suite passes.
