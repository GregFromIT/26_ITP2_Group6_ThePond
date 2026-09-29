"""Authorized draft intake with serialized local-storage quota accounting.

The private root lock serializes intake mutations across processes on this host.
It is held during streaming, but no database writer lock is held while copying
bytes. This deliberately supports one upload at a time, not distributed intake.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
import stat

from jsonschema import Draft202012Validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db import AuditLog, ChallengeSubmission, SubmissionFile, SubmissionJob, User
from . import requirements as policy
from .validation import FileRecord, ManifestError, content_digest, parse_manifest


class IntakeError(ValueError):
    pass


class IntakeBusy(IntakeError):
    pass


class IntakeService:
    def __init__(self, engine, storage, *, user_bytes=80 * 1024**3, total_bytes=200 * 1024**3):
        if engine.dialect.name != "sqlite":
            raise ValueError("Intake currently requires SQLite")
        for limit in (user_bytes, total_bytes):
            if type(limit) is not int or limit <= 0:
                raise ValueError("Storage quotas must be positive integers")
        self.engine, self.storage = engine, storage
        self.user_bytes, self.total_bytes = user_bytes, total_bytes

    @contextmanager
    def _lock(self):
        # Import only when intake is used so other web pages still load on
        # hosts without POSIX upload support.
        import fcntl
        fd = os.open(".intake.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                     0o600, dir_fd=self.storage._directory())
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise IntakeError("Unsafe intake lock configuration")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise IntakeBusy("Another upload is in progress. Try again shortly.") from error
            yield
        finally:
            os.close(fd)

    @staticmethod
    def actor(session, user_id):
        user = session.get(User, user_id)
        if user is None or not user.is_active or user.role.role_name not in {"manager", "sysadmin"}:
            raise PermissionError("Staff access required")
        return user

    @classmethod
    def owned(cls, session, user_id, submission_id):
        user = cls.actor(session, user_id)
        row = session.get(ChallengeSubmission, submission_id)
        if row is None or (row.uploaded_by_user_id != user.user_id and user.role.role_name != "sysadmin"):
            raise LookupError("Submission not found")
        return row

    @staticmethod
    def manifest(data):
        try:
            value = parse_manifest(data)
        except ManifestError as error:
            raise IntakeError("Provide a valid UTF-8 challenge JSON file within the size limit.") from error
        if not Draft202012Validator(policy.get_manifest_schema()).is_valid(value):
            raise IntakeError("Challenge definition does not match the required format. Check the example manifests.")
        return value

    @staticmethod
    def _draft(row):
        if row.status != "draft":
            raise IntakeError("This revision is frozen. Create a correction after validation finishes.")

    def _quota(self, session, owner, submission_id, additional):
        base = select(func.coalesce(func.sum(SubmissionFile.size_bytes), 0))
        total = session.scalar(base)
        personal = session.scalar(base.join(ChallengeSubmission).where(ChallengeSubmission.uploaded_by_user_id == owner))
        submission = session.scalar(base.where(SubmissionFile.submission_id == submission_id)) if submission_id else 0
        if (additional < 0 or total + additional > self.total_bytes or personal + additional > self.user_bytes
                or submission + additional > policy.MAX_TOTAL_IMAGE_BYTES):
            raise IntakeError("Upload would exceed a submission or storage quota.")
        disk = os.fstatvfs(self.storage._directory())
        if disk.f_bavail * disk.f_frsize < additional + 512 * 1024**2:
            raise IntakeError("Insufficient free quarantine storage.")

    def _draft_capacity(self, session, owner):
        count = session.scalar(select(func.count()).select_from(ChallengeSubmission).where(
            ChallengeSubmission.uploaded_by_user_id == owner, ChallengeSubmission.status == "draft"))
        if count >= 20:
            raise IntakeError("You already have 20 drafts. Finish or ask an administrator to retire existing drafts.")

    def create(self, user_id, manifest_bytes):
        manifest = self.manifest(manifest_bytes)
        with self._lock(), Session(self.engine) as session:
            self.actor(session, user_id)
            self._draft_capacity(session, user_id)
            row = ChallengeSubmission(uploaded_by_user_id=user_id, title=manifest["title"],
                challenge_type=manifest["challenge_type"], schema_version=manifest["schema_version"], manifest_json=manifest)
            session.add(row)
            session.flush()
            identifier = row.submission_id
            session.add(AuditLog(actor_user_id=user_id, action="submission_draft", target_type="submission", target_id=identifier))
            session.commit()
            return identifier

    def update_manifest(self, user_id, submission_id, manifest_bytes):
        manifest = self.manifest(manifest_bytes)
        with self._lock(), Session(self.engine) as session:
            row = self.owned(session, user_id, submission_id)
            self._draft(row)
            row.manifest_json, row.title = manifest, manifest["title"]
            row.challenge_type, row.schema_version = manifest["challenge_type"], manifest["schema_version"]
            session.commit()

    def upload(self, user_id, submission_id, logical_path, original_filename, stream, declared_size):
        if type(declared_size) is not int or not 0 < declared_size <= policy.MAX_IMAGE_BYTES:
            raise IntakeError("A valid image Content-Length within the server limit is required.")
        if not isinstance(original_filename, str) or not 1 <= len(original_filename) <= 255 or any(ord(c) < 32 for c in original_filename):
            raise IntakeError("Invalid original filename.")
        with self._lock():
            with Session(self.engine) as session:
                row = self.owned(session, user_id, submission_id)
                self._draft(row)
                wanted = {r.logical_path for r in policy.required_image_files(row.manifest_json)}
                if logical_path not in wanted:
                    raise IntakeError("This image path is not required by the challenge definition.")
                if session.scalar(select(SubmissionFile.file_id).where(SubmissionFile.submission_id == submission_id,
                                                                         SubmissionFile.logical_path == logical_path)):
                    raise IntakeError("Remove the existing draft file before uploading its replacement.")
                self._quota(session, row.uploaded_by_user_id, submission_id, declared_size)
            stored = self.storage.save(stream, remaining_bytes=declared_size)
            try:
                if stored.size_bytes != declared_size:
                    raise IntakeError("Upload was incomplete. Please retry.")
                with Session(self.engine) as session:
                    row = self.owned(session, user_id, submission_id)
                    self._draft(row)
                    session.add(SubmissionFile(submission_id=submission_id, logical_path=logical_path,
                        original_filename=original_filename, file_role="image", storage_key=stored.storage_key,
                        size_bytes=stored.size_bytes, sha256=stored.sha256))
                    session.commit()
            except BaseException:
                self.storage.delete(stored.storage_key)
                raise

    def remove_file(self, user_id, submission_id, file_id):
        with self._lock(), Session(self.engine) as session:
            row = self.owned(session, user_id, submission_id)
            self._draft(row)
            file = session.scalar(select(SubmissionFile).where(SubmissionFile.file_id == file_id,
                                                              SubmissionFile.submission_id == submission_id))
            if file is None:
                raise LookupError("File not found")
            key = file.storage_key
            session.delete(file)
            session.commit()
            self.storage.delete(key)

    def submit(self, user_id, submission_id):
        with self._lock(), Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self.owned(session, user_id, submission_id)
            key = f"validate-submission:{submission_id}:initial"
            existing = session.scalar(select(SubmissionJob).where(SubmissionJob.idempotency_key == key))
            if existing:
                return existing.job_id
            self._draft(row)
            files = tuple(FileRecord(f.file_id, f.submission_id, f.logical_path, f.file_role, f.storage_key,
                                    f.size_bytes, f.sha256) for f in session.scalars(
                                    select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)))
            row.content_digest = content_digest(row.manifest_json, files)
            row.submitted_at = datetime.now(timezone.utc)
            row.status = "validating"
            job = SubmissionJob(submission_id=submission_id, requested_by_user_id=user_id,
                                action="validate", idempotency_key=key)
            session.add(job)
            session.flush()
            identifier = job.job_id
            session.add(AuditLog(actor_user_id=user_id, action="submission_queued", target_type="submission", target_id=submission_id))
            session.commit()
            return identifier

    def revise(self, user_id, submission_id):
        copied = []
        with self._lock():
            try:
                with Session(self.engine) as session:
                    old = self.owned(session, user_id, submission_id)
                    if old.status not in {"needs_changes", "validation_failed", "rejected"}:
                        raise IntakeError("Only a submission requiring correction can be revised.")
                    self._draft_capacity(session, user_id)
                    files = list(session.scalars(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)))
                    self._quota(session, user_id, None, sum(f.size_bytes for f in files))
                    manifest = old.manifest_json
                for file in files:
                    stored = self.storage.copy(file.storage_key, remaining_bytes=file.size_bytes)
                    copied.append((file, stored))
                    if stored.size_bytes != file.size_bytes or stored.sha256 != file.sha256:
                        raise IntakeError("An existing file changed. Administrator recovery is required.")
                with Session(self.engine) as session:
                    self.owned(session, user_id, submission_id)
                    row = ChallengeSubmission(uploaded_by_user_id=user_id, title=manifest["title"],
                        challenge_type=manifest["challenge_type"], schema_version=manifest["schema_version"],
                        manifest_json=manifest, supersedes_submission_id=submission_id)
                    session.add(row)
                    session.flush()
                    identifier = row.submission_id
                    for old_file, stored in copied:
                        session.add(SubmissionFile(submission_id=identifier, file_role=old_file.file_role,
                            logical_path=old_file.logical_path, original_filename=old_file.original_filename,
                            storage_key=stored.storage_key, size_bytes=stored.size_bytes, sha256=stored.sha256))
                    session.add(AuditLog(actor_user_id=user_id, action="submission_revision", target_type="submission", target_id=identifier))
                    session.commit()
                return identifier
            except BaseException:
                for _, stored in copied:
                    self.storage.delete(stored.storage_key)
                raise
