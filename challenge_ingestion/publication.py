"""Controlled publication orchestration; deployment supplies the VM adapter.

Required adapter methods (trusted Python code, never uploaded code):
 plan(manifest): read-only resource planning, one dict per VM role.
 prepare(manifest, journal, files, storage, checkpoint, guard): reconcile by operation_key,
   prepare only owned resources, checkpoint tasks/results; return exactly True.
 ready(manifest, journal, guard): isolated boot/network/resource readiness checks;
   return exactly True only after success and test-resource cleanup.
Plans contain role/node/vmid/template_name/owned. Import identity is frozen before
external mutations. No default or simulated production adapter is provided.
"""

import argparse
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import secrets
import sys
from threading import Event, Thread

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import (AuditLog, Challenge, ChallengeSubmission, NotificationOutbox,
                SubmissionJob, User, VMTemplate, ChallengeFlag, db)
from db.challenge_models import NetworkRule
from .validation import content_digest, validate_submission
from .worker import Claim, LeaseLost, ValidationWorker
from .storage import QuarantineStorage


class PublicationError(ValueError):
    pass


class PublicationWorker(ValidationWorker):
    def __init__(self, engine, storage, *, adapter=None, **kwargs):
        super().__init__(engine, storage, **kwargs)
        self.adapter = adapter

    def _owned(self, session, claim):
        job = session.get(SubmissionJob, claim.job_id)
        if (job is None or job.action != "publish" or job.status != "running"
                or job.submission_id != claim.submission_id or job.lease_token != claim.token
                or job.lease_expires_at is None or job.lease_expires_at <= self._now()):
            raise LeaseLost("Publication claim expired or was replaced")
        return job

    def _approval(self, session, submission):
        if submission.approved_by_user_id is None or submission.approved_at is None:
            raise PublicationError("Administrator approval is required.")
        approver = session.get(User, submission.approved_by_user_id)
        if approver is None or not approver.is_active or approver.role.role_name != "sysadmin":
            raise PublicationError("The approving administrator is no longer authorized.")
        files, manifest_bytes, snapshot = self._snapshot(session, submission)
        if content_digest(submission.manifest_json, files) != submission.content_digest:
            raise PublicationError("Approved content changed. A corrected revision and approval are required.")
        audit = session.scalar(select(AuditLog).where(AuditLog.action == "submission_review",
            AuditLog.target_type == "submission", AuditLog.target_id == submission.submission_id)
            .order_by(AuditLog.audit_id.desc()).limit(1))
        evidence = audit.details_json if audit else None
        if (not isinstance(evidence, dict) or evidence.get("decision") != "approve"
                or evidence.get("content_digest") != submission.content_digest
                or audit.actor_user_id != submission.approved_by_user_id):
            raise PublicationError("Approval evidence for this revision is missing.")
        return files, manifest_bytes, snapshot + (submission.approved_by_user_id, submission.approved_at)

    def enqueue(self, submission_id, *, actor_user_id, expected_digest, retry=False):
        """Called only by authenticated admin routes; returns the stable job ID."""
        if not all(callable(getattr(self.adapter, name, None)) for name in ("plan", "prepare", "ready")):
            raise PublicationError("The deployment publication adapter is not configured.")
        with self._write() as session:
            actor = session.get(User, actor_user_id)
            if actor is None or not actor.is_active or actor.role.role_name != "sysadmin":
                raise PermissionError("System administrator publication permission is required")
            row = session.get(ChallengeSubmission, submission_id)
            if row is None:
                raise LookupError("Submission not found")
            if row.content_digest != expected_digest:
                raise PublicationError("The submission changed. Refresh before publishing.")
            self._approval(session, row)
            key = f"publish-submission:{submission_id}"
            job = session.scalar(select(SubmissionJob).where(SubmissionJob.idempotency_key == key))
            if row.status in {"published", "importing"} and job is not None:
                return job.job_id
            if row.status == "import_failed":
                if not retry or job is None or job.status != "failed" or job.attempt_count >= job.max_attempts:
                    raise PublicationError("Review the failed import; retry requires reconciliation and available attempts.")
                # Reuse the same journal and operation identities. Never create a
                # new job that could forget resources from an uncertain attempt.
                job.status = "queued"
                job.available_at = self._now()
                job.completed_at = None
            elif row.status == "approved" and job is None:
                if session.scalar(select(SubmissionJob.job_id).where(SubmissionJob.submission_id == submission_id,
                        SubmissionJob.status.in_(("queued", "running", "retry_wait"))).limit(1)):
                    raise PublicationError("Another job is active for this submission.")
                job = SubmissionJob(submission_id=submission_id, requested_by_user_id=actor_user_id,
                    action="publish", idempotency_key=key, available_at=self._now())
                session.add(job)
            else:
                raise PublicationError("Only approved submissions can enter publication.")
            row.status = "importing"
            session.flush()
            identifier = job.job_id
            session.add(AuditLog(actor_user_id=actor_user_id, action="publication_queued", target_type="submission",
                target_id=submission_id, details_json={"job_id": identifier, "retry": retry}))
            return identifier

    def return_for_correction(self, submission_id, *, actor_user_id, expected_digest, note):
        """After operator reconciliation, retain recovery history and allow a revision."""
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise PublicationError("A correction note of 1–2000 characters is required.")
        with self._write() as session:
            actor = session.get(User, actor_user_id)
            if actor is None or not actor.is_active or actor.role.role_name != "sysadmin":
                raise PermissionError("System administrator permission is required")
            row = session.get(ChallengeSubmission, submission_id)
            if row is None:
                raise LookupError("Submission not found")
            if row.status != "import_failed" or row.content_digest != expected_digest:
                raise PublicationError("Only the current failed publication can be returned for correction.")
            job = session.scalar(select(SubmissionJob).where(
                SubmissionJob.idempotency_key == f"publish-submission:{submission_id}"))
            if job is None or job.status != "failed" or session.scalar(select(SubmissionJob.job_id).where(
                    SubmissionJob.submission_id == submission_id,
                    SubmissionJob.status.in_(("queued", "running", "retry_wait"))).limit(1)):
                raise PublicationError("Publication work must be stopped and reconciled first.")
            row.status = "rejected"
            row.approved_at = row.approved_by_user_id = None
            session.add(AuditLog(actor_user_id=actor_user_id, action="publication_returned", target_type="submission",
                target_id=submission_id, details_json={"job_id": job.job_id, "note": note.strip()}))
            session.add(NotificationOutbox(submission_id=submission_id, recipient_user_id=row.uploaded_by_user_id,
                event_type="rejected", deduplication_key=f"publication-returned:{submission_id}",
                message="Publication was returned for correction: " + note.strip()))

    def _fail(self, session, job, row, code):
        job.status = "failed"
        job.error_code = code
        job.error_message = "Publication stopped. Review recovery records before retrying."
        job.completed_at = self._now()
        job.locked_by = job.lease_token = job.lease_expires_at = None
        row.status = "import_failed"
        session.add(AuditLog(actor_user_id=None, action="publication_failed", target_type="submission",
            target_id=row.submission_id, details_json={"job_id": job.job_id, "attempt": job.attempt_count, "code": code}))
        session.add(NotificationOutbox(submission_id=row.submission_id, recipient_user_id=row.uploaded_by_user_id,
            event_type="import_failed", deduplication_key=f"publish:{job.job_id}:failed:{job.attempt_count}",
            message="Publication stopped. A system administrator must review the import before retrying."))

    def claim(self):
        with self._write() as session:
            now = self._now()
            for job in list(session.scalars(select(SubmissionJob).where(SubmissionJob.action == "publish",
                    SubmissionJob.status == "running", SubmissionJob.lease_expires_at <= now))):
                row = session.get(ChallengeSubmission, job.submission_id)
                if row.status == "importing":
                    self._fail(session, job, row, "LEASE_EXPIRED_RECONCILE")
            session.flush()
            job = session.scalar(select(SubmissionJob).where(SubmissionJob.action == "publish",
                SubmissionJob.status == "queued", SubmissionJob.available_at <= now)
                .order_by(SubmissionJob.job_id).limit(1))
            if job is None:
                return None
            row = session.get(ChallengeSubmission, job.submission_id)
            if row.status != "importing":
                job.status, job.completed_at = "cancelled", now
                return None
            if job.attempt_count >= job.max_attempts:
                self._fail(session, job, row, "ATTEMPT_LIMIT")
                return None
            job.status = "running"
            job.attempt_count += 1
            job.locked_by = self.worker_id
            job.lease_token = secrets.token_hex(32)
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.started_at = job.started_at or now
            return Claim(job.job_id, job.submission_id, job.lease_token)

    def guard(self, claim):
        with Session(self.engine) as session:
            job = self._owned(session, claim)
            if session.get(ChallengeSubmission, job.submission_id).status != "importing":
                raise LeaseLost("Submission left publication")

    def _plans(self, manifest, plans, job_id):
        if not isinstance(plans, list) or len(plans) != len(manifest["vms"]):
            raise PublicationError("Adapter returned an invalid resource plan.")
        sources = {vm["role"]: vm["source"]["kind"] for vm in manifest["vms"]}
        roles, vmids, result = set(), set(), []
        for plan in plans:
            if (not isinstance(plan, dict) or set(plan) != {"role", "node", "vmid", "template_name", "owned"}
                    or plan["role"] not in sources or plan["role"] in roles
                    or type(plan["vmid"]) is not int or not 100 <= plan["vmid"] <= 999999999
                    or plan["vmid"] in vmids or type(plan["owned"]) is not bool
                    or plan["owned"] != (sources[plan["role"]] == "image")
                    or any(not isinstance(plan[k], str) or not 1 <= len(plan[k]) <= 100 for k in ("node", "template_name"))):
                raise PublicationError("Adapter returned an unsafe or conflicting resource plan.")
            roles.add(plan["role"])
            vmids.add(plan["vmid"])
            result.append({**plan, "operation_key": f"publication:{job_id}:vm:{plan['role']}", "state": "planned", "task_id": None})
        return result

    def _journal(self, claim, manifest):
        with Session(self.engine) as session:
            job = self._owned(session, claim)
            existing = deepcopy(job.resource_inventory_json)
        if existing:
            fields = ("role", "node", "vmid", "template_name", "owned")
            checked = self._plans(manifest, [{k: p[k] for k in fields} for p in existing], claim.job_id)
            for prior, expected in zip(existing, checked):
                if (prior.get("operation_key") != expected["operation_key"]
                        or prior.get("state") not in {"planned", "reserved", "importing", "ready", "failed"}):
                    raise PublicationError("The recovery journal is invalid.")
            return existing
        planned = self._plans(manifest, self.adapter.plan(deepcopy(manifest)), claim.job_id)
        with self._write() as session:
            job = self._owned(session, claim)
            vmids = {p["vmid"] for p in planned}
            if session.scalar(select(VMTemplate.template_id).where(VMTemplate.proxmox_template_vmid.in_(vmids)).limit(1)):
                raise PublicationError("A planned template is already assigned to a live challenge.")
            for other in session.scalars(select(SubmissionJob).where(SubmissionJob.action == "publish", SubmissionJob.job_id != job.job_id)):
                if any(p.get("vmid") in vmids for p in other.resource_inventory_json):
                    raise PublicationError("A planned VM ID is reserved by another publication record.")
            job.resource_inventory_json = planned
        return planned

    def checkpoint(self, claim, role, *, state, task_id=None):
        if state not in {"reserved", "importing", "ready", "failed"} or (task_id is not None and (not isinstance(task_id, str) or len(task_id) > 255)):
            raise PublicationError("Invalid resource checkpoint")
        with self._write() as session:
            job = self._owned(session, claim)
            journal = deepcopy(job.resource_inventory_json)
            entry = next((p for p in journal if p["role"] == role), None)
            if entry is None:
                raise PublicationError("Unknown planned VM role")
            entry["state"] = state
            if task_id is not None:
                entry["task_id"] = task_id
            job.resource_inventory_json = journal

    def _commit_live(self, claim, identity, manifest):
        with self._write() as session:
            job = self._owned(session, claim)
            row = session.get(ChallengeSubmission, job.submission_id)
            if row.status != "importing" or self._approval(session, row)[2] != identity:
                raise PublicationError("Approval or content changed during publication.")
            plans = job.resource_inventory_json
            if len(plans) != len(manifest["vms"]) or any(p["state"] != "ready" for p in plans):
                raise PublicationError("Not every planned resource is ready.")
            if session.scalar(select(VMTemplate.template_id).where(VMTemplate.proxmox_template_vmid.in_([p["vmid"] for p in plans])).limit(1)):
                raise PublicationError("A template was assigned to another challenge during publication.")
            challenge = Challenge(title=manifest["title"], description=manifest["description"],
                instructions=manifest["instructions"], category=manifest["category"], difficulty=manifest["difficulty"],
                time_limit_minutes=manifest.get("time_limit_minutes"), created_by_user_id=row.uploaded_by_user_id, status="published")
            session.add(challenge)
            session.flush()
            specs = {vm["role"]: vm for vm in manifest["vms"]}
            templates = {}
            for plan in plans:
                spec = specs[plan["role"]]
                template = VMTemplate(challenge_id=challenge.challenge_id, template_name=plan["template_name"],
                    proxmox_template_vmid=plan["vmid"], proxmox_node=plan["node"], vm_role=plan["role"],
                    cpu_cores=spec["cpu_cores"], memory_mb=spec["memory_mb"], disk_gb=spec["disk_gb"],
                    boot_order=spec["boot_order"], is_user_accessible=spec["is_user_accessible"])
                session.add(template)
                templates[plan["role"]] = template
            session.flush()
            for flag in manifest["flags"]:
                session.add(ChallengeFlag(template_id=templates[flag["vm_role"]].template_id, flag_name=flag["name"],
                    flag_hash=flag["flag_hash"], points=flag["points"], sequence_number=flag.get("sequence_number")))
            for rule in manifest["network_rules"]:
                session.add(NetworkRule(challenge_id=challenge.challenge_id, **rule))
            row.published_challenge_id, row.published_at, row.status = challenge.challenge_id, self._now(), "published"
            job.status, job.completed_at = "succeeded", self._now()
            job.locked_by = job.lease_token = job.lease_expires_at = None
            job.error_code = job.error_message = None
            session.add(AuditLog(actor_user_id=job.requested_by_user_id, action="submission_published", target_type="submission",
                target_id=row.submission_id, details_json={"job_id": job.job_id, "challenge_id": challenge.challenge_id,
                "content_digest": row.content_digest}))
            session.add(NotificationOutbox(submission_id=row.submission_id, recipient_user_id=row.uploaded_by_user_id,
                event_type="published", deduplication_key=f"published:{row.submission_id}", message="Your challenge has been published."))

    def run_once(self):
        if not all(callable(getattr(self.adapter, name, None)) for name in ("plan", "prepare", "ready")):
            raise PublicationError("The deployment publication adapter is not configured.")
        claim = self.claim()
        if claim is None:
            return None
        stop, lost = Event(), Event()
        def heartbeat():
            while not stop.wait(self.lease_seconds / 3):
                try:
                    self.renew(claim)
                except Exception:
                    lost.set()
                    return
        thread = Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            with Session(self.engine) as session:
                job = self._owned(session, claim)
                row = session.get(ChallengeSubmission, job.submission_id)
                files, raw, identity = self._approval(session, row)
                args = dict(submission_id=row.submission_id, challenge_type=row.challenge_type,
                    schema_version=row.schema_version, expected_digest=row.content_digest)
            manifest = json.loads(raw)
            def revalidate():
                report = validate_submission(raw, files, storage=self.storage, verify_template=self.verify_template,
                    inspect_image=self.inspect_image, **args)
                if not report.passed:
                    raise PublicationError("Publication revalidation failed; administrator review is required.")
            revalidate()
            plans = self._journal(claim, manifest)
            guard = lambda: self.guard(claim)
            guard()
            checkpoint = lambda role, **values: self.checkpoint(claim, role, **values)
            if self.adapter.prepare(deepcopy(manifest), deepcopy(plans), tuple(files), self.storage, checkpoint, guard) is not True:
                raise PublicationError("Resource preparation did not complete successfully.")
            guard()
            with Session(self.engine) as session:
                plans = deepcopy(self._owned(session, claim).resource_inventory_json)
            if self.adapter.ready(deepcopy(manifest), plans, guard) is not True:
                raise PublicationError("Isolated readiness checks did not pass.")
            # Verify bytes again after external preparation/testing. A trusted
            # importer must use the inspected immutable object, never a path
            # resolved from uploaded metadata or an unverified external URL.
            revalidate()
            if lost.is_set():
                raise LeaseLost("Publication heartbeat failed")
            self._commit_live(claim, identity, manifest)
        except LeaseLost:
            raise
        except Exception:
            with self._write() as session:
                job = self._owned(session, claim)
                row = session.get(ChallengeSubmission, job.submission_id)
                if row.status != "importing":
                    raise LeaseLost("Submission left publication")
                self._fail(session, job, row, "PUBLICATION_STOPPED")
            # Record a safe failure; no automatic deletion of possibly shared resources.
        finally:
            stop.set()
            thread.join(timeout=self.lease_seconds)
        return claim.job_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process one approved publication job.")
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pond-sec"))
    from app import create_app
    app = create_app()
    with app.app_context(), QuarantineStorage(args.storage_root) as storage:
        worker = PublicationWorker(db.engines["pond"], storage, worker_id=args.worker_id,
            adapter=app.config.get("INGESTION_PUBLICATION_ADAPTER"),
            verify_template=app.config.get("INGESTION_VERIFY_TEMPLATE"), inspect_image=app.config.get("INGESTION_INSPECT_IMAGE"))
        result = worker.run_once()
        print("No eligible publication job." if result is None else f"Processed publication job {result}; inspect its recorded status.")


if __name__ == "__main__":
    main()
