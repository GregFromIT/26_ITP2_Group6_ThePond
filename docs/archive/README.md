# Archive

Documents that the code has overtaken, kept for history. **Don't follow them:**
commands, file names and behaviour in here may no longer exist. The current
documentation is indexed in the [root README](../../README.md).

Archived on 21 Oct 2026, when the documentation was rewritten against `main`
at `04998a1` with `fix/seeder-theme-flagformat` (`3dc6afc`) merged.

| File | What it was | Replaced by |
|---|---|---|
| `pond-sec-user-guide.md` | The original student and staff guide: six challenges per theme, theme weighting, `app/seed.py` | [Student guide](../user-guides/pond-sec-student-guide.md), [admin guide](../user-guides/pond-sec-admin-guide.md) |
| `pond-sec-readme.md` | The old app readme: `flask … init-db` / `seed-db`, demo login `bpt` / `rootroot`, `tests.test_flow` | [Root README](../../README.md), [build from scratch](../setup/build-from-scratch.md) |
| `pond-network-spec.md` | 7 Oct draft of the network specification | [Network reference](../networking/pond-proxmox-vmid-network-reference.md) (the 8 Oct successor) |
| `pond-infrastructure-documentation.docx` | V2-era infrastructure, provisioning and Git document (before session VNets and the scoped token) | [Network reference](../networking/pond-proxmox-vmid-network-reference.md), [build from scratch](../setup/build-from-scratch.md), [design decisions](../architecture/pond-sec-design-decisions.md) |
| `CONTAINER_LAB_SETUP.md`, `CONTAINER_LAB_TEST_RESULTS.md` | Installation notes and results for the 2 Oct container-lab patch | [Submission pipeline](../challenges/submission-pipeline.md) |
| `DOCKER_ADAPTER_CONTRACT.md` | The `CONTAINER_LAB_ADAPTER` interface | [Submission pipeline](../challenges/submission-pipeline.md), "For `container_lab` challenges" |
| `INGESTION_HANDOVER.md`, `challenge-upload-handover.md` | Review entry points for the upload pipeline. They point at files that were never added (`DATABASE_INITIALIZATION_SETUP.md`, `PUBLICATION_SETUP.md`, `TEST_RESULTS.md`) | [Submission pipeline](../challenges/submission-pipeline.md) |
| `challenge-upload-test-results.md` | Pipeline test results, 29 Sep 2026 | [Testing](../testing/testing.md) |
| `novnc-console-testing.md` | Pre-relay console checklist | [Testing](../testing/testing.md), [network reference](../networking/pond-proxmox-vmid-network-reference.md) §7.1 |
| `challenge-upload-steps/` (01–14) | Step-by-step notes written while the upload pipeline was built, each describing how to apply one change | [Submission pipeline](../challenges/submission-pipeline.md), [data model](../database/pond-sec-data-model.md) |
