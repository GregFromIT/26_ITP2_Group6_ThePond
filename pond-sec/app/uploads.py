"""Authenticated challenge intake/status and recipient inbox pages."""

from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from db import AuditLog, ChallengeSubmission, SubmissionFile, SubmissionIssue, SubmissionJob, User, db
from challenge_ingestion.intake import IntakeBusy, IntakeError, IntakeService
from challenge_ingestion.notifications import NotificationService
from challenge_ingestion.publication import PublicationError, PublicationWorker
from challenge_ingestion.review import ReviewError, ReviewService
from challenge_ingestion.requirements import MAX_IMAGE_BYTES, MAX_MANIFEST_BYTES, required_image_files
from challenge_ingestion.storage import QuarantineStorage, StorageError
from .auth import login_required
from .roles import can, require

bp = Blueprint("uploads", __name__)


@bp.before_request
def active_account():
    if g.get("user"):
        user = db.session.get(User, g.user["user_id"])
        if user is None or not user.is_active:
            abort(403)


def init_app(app):
    for setting in ("UPLOAD_MAX_IMAGE_BYTES", "UPLOAD_USER_QUOTA_BYTES", "UPLOAD_TOTAL_QUOTA_BYTES"):
        if type(app.config[setting]) is not int or app.config[setting] <= 0:
            raise ValueError(f"{setting} must be a positive integer")
    # Register BEFORE CSRF parsing; never allow multipart image bodies to spool
    # before authorization. Raw upload requests send the CSRF token in a header.
    @app.before_request
    def intake_request_limits():
        if request.endpoint == "uploads.upload_file":
            if request.mimetype != "application/octet-stream":
                abort(415)
            if request.content_length is None:
                abort(411)
            request.max_content_length = min(app.config["UPLOAD_MAX_IMAGE_BYTES"], MAX_IMAGE_BYTES)
        elif request.endpoint in {"uploads.create", "uploads.edit_manifest"}:
            request.max_content_length = MAX_MANIFEST_BYTES + 16 * 1024


@contextmanager
def intake():
    root = current_app.config.get("UPLOAD_QUARANTINE_ROOT")
    if not root:
        abort(503, description="Challenge upload storage has not been configured.")
    if Path(root).resolve().is_relative_to(Path(current_app.static_folder).resolve()):
        abort(503, description="Quarantine must be outside the public static directory.")
    with QuarantineStorage(root) as storage:
        yield IntakeService(db.engines["pond"], storage,
            user_bytes=current_app.config["UPLOAD_USER_QUOTA_BYTES"],
            total_bytes=current_app.config["UPLOAD_TOTAL_QUOTA_BYTES"])


@bp.errorhandler(IntakeError)
@bp.errorhandler(StorageError)
@bp.errorhandler(ReviewError)
@bp.errorhandler(PublicationError)
def intake_error(error):
    status = 409 if isinstance(error, IntakeBusy) else 400
    if request.endpoint == "uploads.upload_file":
        return jsonify(error=str(error)), status
    return render_template("admin/upload_error.html", message=str(error)), status


@bp.errorhandler(PermissionError)
def denied(error):
    return "Access denied", 403


@bp.errorhandler(LookupError)
def missing(error):
    return "Not found", 404


def owned(submission_id):
    return IntakeService.owned(db.session, g.user["user_id"], submission_id)


def manifest_upload():
    file = request.files.get("manifest")
    if file is None:
        raise IntakeError("Choose a challenge definition JSON file.")
    return file.stream.read(MAX_MANIFEST_BYTES + 1)


@bp.get("/admin/challenge-submissions")
@require("upload_challenges")
def index():
    query = select(ChallengeSubmission)
    if not can("review_challenges"):
        query = query.where(ChallengeSubmission.uploaded_by_user_id == g.user["user_id"])
    before = request.args.get("before", type=int)
    if before:
        query = query.where(ChallengeSubmission.submission_id < before)
    rows = list(db.session.scalars(query.order_by(ChallengeSubmission.submission_id.desc()).limit(50)))
    return render_template("admin/submissions.html", submissions=rows)


@bp.get("/admin/challenge-submissions/new")
@require("upload_challenges")
def new():
    return render_template("admin/submission_upload.html")


@bp.post("/admin/challenge-submissions")
@require("upload_challenges")
def create():
    with intake() as service:
        identifier = service.create(g.user["user_id"], manifest_upload())
    return redirect(url_for("uploads.detail", submission_id=identifier), code=303)


@bp.get("/admin/challenge-submissions/<int:submission_id>")
@require("upload_challenges")
def detail(submission_id):
    row = owned(submission_id)
    files = list(db.session.scalars(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)))
    jobs = list(db.session.scalars(select(SubmissionJob).where(SubmissionJob.submission_id == submission_id)
                                  .order_by(SubmissionJob.job_id.desc()).limit(20)))
    issues = list(db.session.scalars(select(SubmissionIssue).where(SubmissionIssue.submission_id == submission_id)
                                    .order_by(SubmissionIssue.issue_id.desc()).limit(100)))
    required = required_image_files(row.manifest_json)
    review_job = db.session.scalar(select(SubmissionJob).where(SubmissionJob.submission_id == submission_id,
        SubmissionJob.action == "validate").order_by(SubmissionJob.job_id.desc()).limit(1))
    reviews = list(db.session.scalars(select(AuditLog).where(AuditLog.action == "submission_review",
        AuditLog.target_type == "submission", AuditLog.target_id == submission_id)
        .order_by(AuditLog.audit_id.desc()).limit(20)))
    return render_template("admin/submission_detail.html", submission=row, files=files, jobs=jobs,
                           issues=issues, required=required, present={f.logical_path for f in files},
                           review_job=review_job, reviews=reviews,
                           publication_configured=current_app.config.get("INGESTION_PUBLICATION_ADAPTER") is not None,
                           upload_limit=min(current_app.config["UPLOAD_MAX_IMAGE_BYTES"], MAX_IMAGE_BYTES))


@bp.get("/admin/challenge-submissions/<int:submission_id>/status")
@require("upload_challenges")
def status(submission_id):
    row = owned(submission_id)
    return jsonify(submission_id=row.submission_id, status=row.status)


@bp.post("/admin/challenge-submissions/<int:submission_id>/manifest")
@require("upload_challenges")
def edit_manifest(submission_id):
    with intake() as service:
        service.update_manifest(g.user["user_id"], submission_id, manifest_upload())
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/files")
@require("upload_challenges")
def upload_file(submission_id):
    # Permission + active-account checks run before any request.stream read.
    with intake() as service:
        service.upload(g.user["user_id"], submission_id, request.args.get("path", ""),
            unquote(request.headers.get("X-Upload-Filename", "")), request.stream, request.content_length)
    return jsonify(saved=True), 201


@bp.post("/admin/challenge-submissions/<int:submission_id>/files/<int:file_id>/remove")
@require("upload_challenges")
def remove_file(submission_id, file_id):
    with intake() as service:
        service.remove_file(g.user["user_id"], submission_id, file_id)
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/submit")
@require("upload_challenges")
def submit(submission_id):
    with intake() as service:
        service.submit(g.user["user_id"], submission_id)
    flash("Submission queued for validation.", "info")
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/revise")
@require("upload_challenges")
def revise(submission_id):
    with intake() as service:
        identifier = service.revise(g.user["user_id"], submission_id)
    return redirect(url_for("uploads.detail", submission_id=identifier), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/review")
@require("review_challenges")
def review(submission_id):
    changed = ReviewService(db.engines["pond"]).decide(submission_id, actor_user_id=g.user["user_id"],
        decision=request.form.get("decision"), expected_digest=request.form.get("content_digest"),
        expected_job_id=request.form.get("job_id", type=int), note=request.form.get("note", ""))
    flash("Review decision saved." if changed else "That decision was already recorded.", "info")
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)


@bp.get("/notifications")
@login_required
def inbox():
    service = NotificationService(db.engines["pond"])
    before = request.args.get("before", type=int)
    if before is not None and before < 1:
        abort(400)
    return render_template("notifications.html", notices=service.inbox(actor_user_id=g.user["user_id"], before_id=before),
                           unread=service.unread_count(actor_user_id=g.user["user_id"]))


@bp.post("/notifications/<int:notification_id>/read")
@login_required
def read_notification(notification_id):
    if not NotificationService(db.engines["pond"]).mark_read(notification_id, actor_user_id=g.user["user_id"]):
        abort(404)
    return redirect(url_for("uploads.inbox"), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/publish")
@require("review_challenges")
def publish(submission_id):
    retry = request.form.get("retry") == "yes"
    if retry and request.form.get("reconciled") != "yes":
        raise PublicationError("Confirm the previous worker has stopped and its resources have been reconciled.")
    with intake() as service:
        worker = PublicationWorker(db.engines["pond"], service.storage, worker_id="web-enqueue",
            adapter=current_app.config.get("INGESTION_PUBLICATION_ADAPTER"))
        worker.enqueue(submission_id, actor_user_id=g.user["user_id"],
            expected_digest=request.form.get("content_digest"), retry=retry)
    flash("Publication queued. Refresh this page to check progress.", "info")
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)


@bp.post("/admin/challenge-submissions/<int:submission_id>/return-for-correction")
@require("review_challenges")
def return_for_correction(submission_id):
    if request.form.get("reconciled") != "yes":
        raise PublicationError("Confirm the previous worker has stopped and its resources have been reconciled.")
    with intake() as service:
        PublicationWorker(db.engines["pond"], service.storage, worker_id="web-return").return_for_correction(
            submission_id, actor_user_id=g.user["user_id"],
            expected_digest=request.form.get("content_digest"), note=request.form.get("note", ""))
    flash("Returned for correction. Recovery records have been retained.", "info")
    return redirect(url_for("uploads.detail", submission_id=submission_id), code=303)
