# Redduck update — test results

Tested 2 October 2026 against a source copy of `ThePondNewVers.zip`.

```text
python -m pytest tests -q
351 passed, 594 warnings in 30.40s
```

The supplied baseline had 329 passing root-suite tests. This update adds 22 tests
(16 container workflow/CLI tests and 6 migration tests). Existing publication
assertions were updated for challenge-owned flags and template assignments.
Warnings concern deprecated `datetime.utcnow()` calls; broad datetime cleanup is
outside this feature.

Covered:

- Two challenges sharing one Redduck template while using distinct Docker keys,
  attempt operation identities, flags and scores; renaming a title preserves lookup identity.
- Ordinary VM launch through assignments; offline launch and completion without
  Proxmox or Docker calls; owner-only flag/close actions; sequential repeat launch.
- Missing adapter preventing clone creation; lost start response followed by
  cleanup using the operation key; failed cleanup retaining identity for retry.
- Registration reusing the existing template and rejecting conflicting metadata;
  cleanup CLI refusing active attempts and recovering closed attempts.
- Container/offline manifest intake, validation, administrator review and metadata
  publication without a VM import adapter; malformed definition rejection.
- Migration using synthetic data shaped from the supplied models: accounts,
  password hash, score, flag IDs and runtime relationships preserved; read-only
  check; backup preservation; repeated apply; rollback after rebuild failure;
  refusal of unknown columns and broken foreign keys.
- Existing root tests for accounts/approval, upload, validation, publication,
  notification and related database behavior remain passing. Account code and
  seeding behavior were not redesigned.

Tests used temporary databases and simulated Proxmox/Docker operations. No real
Docker server, file delivery, guest networking, Proxmox clone or cleanup was
tested. No live schema migration, user seeding or deployment was performed.
These results do not certify unrelated integration changes or concurrent external
service behavior. Existing broad integration problems remain your teammate's work.

See `CONTAINER_LAB_SETUP.md` for deployment checks and
`DOCKER_ADAPTER_CONTRACT.md` for the remaining external interface.
