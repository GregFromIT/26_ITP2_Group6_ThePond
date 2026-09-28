"""VM image uploads.

SUPERSEDED — DO NOT BUILD ON THIS
---------------------------------
The group's submission pipeline replaces this module: `app/uploads.py` in their
plan covers uploading, status, resubmission and approval, backed by
`db/submission_*_models.py` and the `challenge_ingestion/` package, with
quarantine handled in `challenge_ingestion/storage.py`.

This file stays only because that pipeline does not exist yet and this is
currently the only way to get an image into the platform. Delete it, along with
`app/fetcher.py`, `app/templates/admin/uploads.html` and the upload checks in
`tests/test_flow.py`, once the replacement works end to end. See
docs/MIGRATION.md.

`app/fetcher.py` is worth keeping either way: if the new pipeline accepts a URL
it needs the same SSRF guards, and they are already written and tested.


Lets an administrator add a VM image either by choosing a file from their own
machine or by pasting a URL, and records it in a SQLAlchemy table so images can
be catalogued before they are turned into Proxmox templates.

Both routes end in the same place: the bytes on disk under a generated name,
with a size, a SHA-256 and a format check, and a row in `vm_uploads`. A URL
image additionally records where it came from in `source_url`. The fetching and
its guards live in app/fetcher.py.

KNOWN RISK, DEFERRED ON PURPOSE
-------------------------------
This module accepts an image and records it. It does not quarantine, scan or
sanitise anything — the group's decision is that quarantine and sanitisation
happen in Proxmox, not here, so nothing of that sort is built into the platform.

That is a real gap and it is being accepted knowingly for now rather than
overlooked. Written down so nobody later reads this file and assumes a check
exists somewhere:

  * an uploaded image is not scanned, and nothing verifies it is what it claims
    to be beyond an extension check and a look at the first few bytes.
  * an image fetched from a URL is downloaded and stored, but it is no more
    checked than an uploaded one.
  * the `status` column is a label staff set for each other. Nothing in the
    platform behaves differently based on it.

So anything reaching Proxmox from here should be treated as untrusted until
Proxmox has dealt with it. Before this is used by a real cohort, the handover
needs to say plainly whose job that is and what they do. It is on the to-do
list in docs/README.md.

What this module DOES still do, and what should not be quietly dropped later:
files are written outside the web root and never served back, the stored name is
generated rather than taken from the browser, there is a size cap, and every
upload is audited with a SHA-256 of the contents.

WHY THIS FILE USES SQLALCHEMY WHEN THE REST OF THE APP USES sqlite3
-------------------------------------------------------------------
The rest of Pond Sec talks to SQLite through `app/db.py`. The group's newer
database work (`db/orm.py`, `db/user_models.py` and the rest) is SQLAlchemy
against a separate `the_pond.db` bind, and this module follows that, because
uploads are new work and there is no reason to add to the old layer.

That does mean two database layers exist side by side for now. It is deliberate
and temporary. The migration note in `app/db.py` and the to-do list both cover
finishing the move.

ACCEPTED FORMATS
----------------
Disk image formats only: .vmdk, .vhd, .vhdx, .vdi, .qcow, .qcow2 and .raw (and
.img, which is the same thing under a different name). The list is ALLOWED_
EXTENSIONS below and is the only place to change it.

An image can also be registered by URL instead of uploaded. See `add_url()` —
the URL is recorded as a reference and the file is NOT fetched by this server,
which matters for a reason worth reading before anyone "finishes" that feature.

The extension check is a first line, not the whole story: the client picks the
filename, so it proves nothing on its own. `_sniff_format()` reads the first few
bytes and reports what the file actually looks like, and a mismatch is recorded
against the row for staff to look at rather than silently trusted.

Beyond the format check, the things that make file upload dangerous are handled
separately, and none of them depend on knowing the format.

  * files are written outside the web root, under UPLOAD_DIR, and the app never
    serves them back over HTTP. Nothing uploaded here can be requested by URL,
    which is what usually turns "arbitrary upload" into "arbitrary code
    execution".
  * the stored name is generated, never the browser-supplied one. A filename
    like `../../app/themes.py` or `shell.py` cannot escape the directory or land
    somewhere meaningful.
  * the original name is kept in the database as a label only.
  * a size cap applies, so one upload cannot fill the disk.
  * a SHA-256 of the contents is recorded, so the file can be checked later and
    duplicates spotted.
  * only administrators reach this at all, and every upload is audited by name.

When the group settles the format list, add the extension and content check in
`_allowed()` below. That is the only place that needs to change.
"""

import hashlib
import os
import secrets
from datetime import datetime, timezone

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
)
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.utils import secure_filename

from . import audit, fetcher
from .orm import db
from .roles import require

bp = Blueprint("uploads", __name__, url_prefix="/admin/uploads")

# 8 GB. A VM image is large by nature, so this is a "something has gone wrong"
# ceiling rather than a tight limit. Note it is enforced while streaming, not by
# MAX_CONTENT_LENGTH, which is set low for ordinary form posts.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 8 * 1024 * 1024 * 1024))
CHUNK = 1024 * 1024

# A label staff can set so the list is useful to look at. It is a note to each
# other, not a control — nothing in the platform behaves differently based on
# it, because quarantine and sanitisation happen in Proxmox rather than here.
STATUS_NEW = "new"              # uploaded or linked, nobody has said anything yet
STATUS_CHECKED = "checked"      # somebody has looked at it in Proxmox
STATUS_REJECTED = "rejected"    # not to be used, kept on the list for the record
STATUSES = (STATUS_NEW, STATUS_CHECKED, STATUS_REJECTED)


def utc_now():
    return datetime.now(timezone.utc)


class VMUpload(db.Model):
    """One uploaded VM image file.

    The row is the catalogue entry; the bytes live on disk under `stored_name`.
    Nothing here stores a path the browser supplied.
    """

    __bind_key__ = "pond"
    __tablename__ = "vm_uploads"

    upload_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # What the administrator called it, and what their browser called the file.
    # The original name is a label for humans and is never used to build a path.
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # How the image got here: 'file' means the bytes are on disk under
    # stored_name, 'url' means we hold a link and nothing else.
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="file")

    # Set for source='url'. The file is not downloaded — see add_url().
    source_url: Mapped[str] = mapped_column(String(2000), nullable=True)

    # What it is actually saved as on disk: generated, unguessable, no extension
    # taken from the client. Null for URL entries.
    stored_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=True)

    size_bytes: Mapped[int] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=True)

    # What the first bytes looked like, and whether that contradicts the name.
    # Null when there was no signature to read, which is normal for raw images.
    detected_format: Mapped[str] = mapped_column(String(20), nullable=True)
    format_mismatch: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    # Reported by the browser. Advisory only — never trust it for a decision.
    content_type: Mapped[str] = mapped_column(String(120), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=True)

    uploaded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by_username: Mapped[str] = mapped_column(String(50), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # See STATUSES above. A label for staff, not a control.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_NEW)

    # Who last set the status, when, and any note. Kept so that "who said this
    # was fine?" has an answer six months later.
    reviewed_by_username: Mapped[str] = mapped_column(String(50), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=True)

    @property
    def size_display(self) -> str:
        if self.size_bytes is None:
            return "—"
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


def upload_dir() -> str:
    """Where images are written. Outside the app package, never served."""
    configured = current_app.config.get("UPLOAD_DIR")
    if not configured:
        configured = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(configured, exist_ok=True)
    return configured


def path_for(record) -> str:
    """Where this record's bytes live, or None for a link-only entry."""
    if not record.stored_name:
        return None
    return os.path.join(upload_dir(), record.stored_name)


# The disk image formats the platform accepts. Add to this and nothing else
# needs changing — the upload form, the URL form and the error messages all
# read from here.
ALLOWED_EXTENSIONS = (
    ".vmdk",     # VMware
    ".vhd",      # Hyper-V / Azure, older
    ".vhdx",     # Hyper-V, current
    ".vdi",      # VirtualBox
    ".qcow",     # QEMU, original
    ".qcow2",    # QEMU, current — what Proxmox uses natively
    ".raw",      # flat image
    ".img",      # flat image under a different name
)

# First bytes of the formats that have a recognisable header, so a file can be
# checked against the name it arrived with. raw/img have no magic number by
# definition, and .vmdk descriptor files are plain text, so both come back as
# None rather than a mismatch.
MAGIC = {
    b"QFI\xfb": "qcow",             # qcow and qcow2 share this
    b"KDMV": "vmdk",                # VMware sparse extent
    b"conectix": "vhd",             # Hyper-V footer signature, also at offset 0
    b"vhdxfile": "vhdx",
    b"<<< Oracle VM VirtualBox Disk Image >>>": "vdi",
}
MAGIC_READ = 64


def _allowed(filename: str) -> tuple:
    """Extension check. Returns (ok, reason).

    Deliberately checks the name only. The name comes from the client, so this
    catches honest mistakes rather than a determined attacker — see
    `_sniff_format()` for the part that looks at the bytes.
    """
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        listed = ", ".join(ALLOWED_EXTENSIONS)
        return False, f"That file type is not accepted. Allowed: {listed}"
    return True, ""


def _sniff_format(path: str) -> str:
    """What the first bytes say the file is, or None if there is no signature.

    None is a normal answer, not a failure: a raw image has no header, and a
    .vmdk can be a plain-text descriptor. Only a positive mismatch — the bytes
    clearly say one format and the name says another — is worth flagging.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(MAGIC_READ)
    except OSError:
        return None

    for signature, name in MAGIC.items():
        if head.startswith(signature):
            return name
    return None


def _format_mismatch(filename: str, sniffed: str) -> bool:
    """True when the bytes clearly contradict the extension."""
    if sniffed is None:
        return False
    extension = os.path.splitext(filename.lower())[1].lstrip(".")
    if extension in ("raw", "img"):
        # A raw image is whatever it contains. If the bytes say qcow2, the name
        # is wrong, and that is worth flagging.
        return True
    if sniffed == "qcow":
        return extension not in ("qcow", "qcow2")
    return sniffed != extension


def _store_upload(stream) -> dict:
    """Stream bytes to disk, hashing as they go. Returns file facts.

    `stream` is anything with .read(n) — a Werkzeug upload or a streamed HTTP
    response body. Both paths share this so a downloaded image and an uploaded
    one are stored identically.

    Streamed in chunks rather than read whole, because a VM image will not fit
    comfortably in memory. The hash is computed on the way past so the file is
    only read once.

    Raises ValueError if it exceeds MAX_UPLOAD_BYTES. The partial file is removed
    before the error is raised, so a refused transfer leaves nothing behind.
    """
    stored_name = f"{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(16)}.upload"
    path = os.path.join(upload_dir(), stored_name)

    digest = hashlib.sha256()
    size = 0

    try:
        with open(path, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError(
                        f"That file is over the {MAX_UPLOAD_BYTES // (1024 ** 3)} GB limit."
                    )
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise

    if size == 0:
        os.unlink(path)
        raise ValueError("That file was empty.")

    # 0600: readable by the account running the app and nobody else on the box.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    return {"stored_name": stored_name, "size_bytes": size, "sha256": digest.hexdigest()}


# ------------------------------------------------------------------ routes

@bp.route("/")
@require("upload_vm_images")
def index():
    uploads = (
        db.session.execute(db.select(VMUpload).order_by(VMUpload.uploaded_at.desc()))
        .scalars()
        .all()
    )
    unchecked = sum(1 for row in uploads if row.status == STATUS_NEW)
    return render_template(
        "admin/uploads.html",
        uploads=uploads,
        unchecked=unchecked,
        max_gb=MAX_UPLOAD_BYTES // (1024 ** 3),
        upload_path=upload_dir(),
        allowed=ALLOWED_EXTENSIONS,
        statuses=STATUSES,
    )


@bp.route("/", methods=("POST",))
@require("upload_vm_images")
def upload():
    file_storage = request.files.get("vm_file")
    if file_storage is None or not file_storage.filename:
        flash("Choose a file first.", "error")
        return redirect(url_for("uploads.index"))

    # secure_filename() only sanitises the label we display. The path we write
    # to never comes from here.
    original = secure_filename(file_storage.filename) or "unnamed"
    display_name = (request.form.get("display_name", "").strip() or original)[:120]
    notes = request.form.get("notes", "").strip()[:2000]

    ok, reason = _allowed(original)
    if not ok:
        flash(reason, "error")
        return redirect(url_for("uploads.index"))

    try:
        stored = _store_upload(file_storage.stream)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("uploads.index"))
    except OSError as exc:
        current_app.logger.exception("VM upload failed")
        flash(f"Could not save that file: {exc}", "error")
        return redirect(url_for("uploads.index"))

    # Check what the bytes actually are now the file is on disk. A mismatch is
    # recorded rather than treated as fatal: the upload may be fine and the name
    # simply wrong, and an administrator is better placed to judge that than a
    # header check is.
    detected = _sniff_format(os.path.join(upload_dir(), stored["stored_name"]))
    mismatch = _format_mismatch(original, detected)

    record = VMUpload(
        display_name=display_name,
        original_filename=original[:255],
        source="file",
        stored_name=stored["stored_name"],
        size_bytes=stored["size_bytes"],
        sha256=stored["sha256"],
        detected_format=detected,
        format_mismatch=1 if mismatch else 0,
        content_type=(file_storage.mimetype or "")[:120],
        notes=notes or None,
        uploaded_by=g.user["user_id"],
        uploaded_by_username=g.user["username"],
    )
    db.session.add(record)
    db.session.commit()

    audit.record(
        audit.VM_UPLOADED,
        user_id=g.user["user_id"],
        username=g.user["username"],
        detail=f"{display_name} ({stored['size_bytes']} bytes, sha256 {stored['sha256'][:12]})",
    )
    if mismatch:
        flash(
            f"Uploaded {display_name}, but the file's contents look like "
            f"{detected}, which does not match its name. Worth checking before "
            f"anyone builds a template from it.",
            "info",
        )
    else:
        flash(f"Uploaded {display_name}.", "success")
    return redirect(url_for("uploads.index"))


@bp.route("/url", methods=("POST",))
@require("upload_vm_images")
def add_url():
    """Download a VM image from a URL and store it like any other upload.

    Same outcome as the file button: the bytes end up on disk under a generated
    name, with a size, a SHA-256 and a format check, and a row in the catalogue.
    The only difference is that `source_url` records where it came from.

    All the guarding lives in app/fetcher.py — read that before changing
    anything here. Making a server fetch a user-supplied URL is server-side
    request forgery, and this server sits next to the Proxmox cluster holding an
    API token, so the checks are not optional decoration.
    """
    raw_url = request.form.get("source_url", "").strip()[:2000]
    display_name = request.form.get("display_name", "").strip()[:120]
    notes = request.form.get("notes", "").strip()[:2000]

    if not raw_url:
        flash("Enter a URL first.", "error")
        return redirect(url_for("uploads.index"))

    allow_private = current_app.config.get("UPLOAD_FETCH_ALLOW_PRIVATE", False)

    try:
        response, final_url = fetcher.open_stream(raw_url, allow_private)
    except fetcher.FetchError as exc:
        flash(str(exc), "error")
        return redirect(url_for("uploads.index"))

    original = secure_filename(fetcher.filename_from(final_url, response)) or "download"

    ok, reason = _allowed(original)
    if not ok:
        response.close()
        flash(reason, "error")
        return redirect(url_for("uploads.index"))

    # Content-Length is the server's claim, not a guarantee, so it is used as an
    # early refusal only. The real limit is enforced while streaming.
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        response.close()
        flash(f"That file is over the {MAX_UPLOAD_BYTES // (1024 ** 3)} GB limit.", "error")
        return redirect(url_for("uploads.index"))

    try:
        stored = _store_upload(response.raw)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("uploads.index"))
    except Exception as exc:
        current_app.logger.exception("Fetching %s failed", raw_url[:200])
        flash(f"The download failed: {exc}", "error")
        return redirect(url_for("uploads.index"))
    finally:
        response.close()

    detected = _sniff_format(os.path.join(upload_dir(), stored["stored_name"]))
    mismatch = _format_mismatch(original, detected)

    record = VMUpload(
        display_name=(display_name or original)[:120],
        original_filename=original[:255],
        source="url",
        source_url=final_url[:2000],
        stored_name=stored["stored_name"],
        size_bytes=stored["size_bytes"],
        sha256=stored["sha256"],
        detected_format=detected,
        format_mismatch=1 if mismatch else 0,
        content_type=(response.headers.get("Content-Type", ""))[:120],
        notes=notes or None,
        uploaded_by=g.user["user_id"],
        uploaded_by_username=g.user["username"],
    )
    db.session.add(record)
    db.session.commit()

    audit.record(
        audit.VM_URL_ADDED,
        user_id=g.user["user_id"],
        username=g.user["username"],
        detail=f"{record.display_name} <- {final_url[:180]} "
               f"({stored['size_bytes']} bytes, sha256 {stored['sha256'][:12]})",
    )

    if mismatch:
        flash(
            f"Downloaded {record.display_name}, but the contents look like {detected}, "
            f"which does not match the filename.",
            "info",
        )
    else:
        flash(f"Downloaded {record.display_name}.", "success")
    return redirect(url_for("uploads.index"))


@bp.route("/<int:upload_id>/status", methods=("POST",))
@require("upload_vm_images")
def set_status(upload_id):
    """Set the label on an image, and record who set it.

    This is bookkeeping, not a control. Nothing in the platform behaves
    differently based on the status — quarantine and sanitisation are handled in
    Proxmox, so this is here for staff to tell each other what has been looked
    at.
    """
    record = db.session.get(VMUpload, upload_id)
    if record is None:
        abort(404)

    status = request.form.get("status", "")
    if status not in STATUSES:
        flash("That is not a status.", "error")
        return redirect(url_for("uploads.index"))

    notes = request.form.get("review_notes", "").strip()[:2000]
    previous = record.status

    record.status = status
    record.reviewed_by_username = g.user["username"]
    record.reviewed_at = utc_now()
    record.review_notes = notes or None
    db.session.commit()

    audit.record(audit.VM_UPLOAD_STATUS, user_id=g.user["user_id"],
                 username=g.user["username"],
                 detail=f"{record.display_name}: {previous} -> {status}"
                        + (f" ({notes[:120]})" if notes else ""))

    flash(f"{record.display_name} marked {status}.", "success")
    return redirect(url_for("uploads.index"))


@bp.route("/<int:upload_id>/delete", methods=("POST",))
@require("upload_vm_images")
def delete(upload_id):
    """Remove the file from disk and the row from the catalogue.

    The file goes first: a row with no file is a visible inconsistency somebody
    will notice and clean up, whereas a file with no row is invisible and sits
    on the disk forever.
    """
    record = db.session.get(VMUpload, upload_id)
    if record is None:
        abort(404)

    # A URL entry has no file, so there is nothing to unlink.
    path = path_for(record)
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError as exc:
            current_app.logger.exception("Could not delete upload %s", record.stored_name)
            flash(f"Could not delete the file: {exc}", "error")
            return redirect(url_for("uploads.index"))

    name = record.display_name
    db.session.delete(record)
    db.session.commit()
    audit.record(audit.VM_UPLOAD_DELETED, user_id=g.user["user_id"],
                 username=g.user["username"], detail=name)
    flash(f"Deleted {name}.", "info")
    return redirect(url_for("uploads.index"))


def lift_request_size_cap():
    """Raise the request size limit, for upload requests only.

    MAX_CONTENT_LENGTH is 64 KB app-wide, which is right for ordinary forms and
    would reject a VM image with a 413 before any view ran.

    This has to be an app-level hook registered BEFORE the CSRF check, not a
    blueprint hook. Blueprint hooks run after every app-level one, and the CSRF
    check reads request.form, which is the moment Werkzeug measures the body
    against the cap. By then it is too late to raise it.

    The limit that actually applies to an upload is MAX_UPLOAD_BYTES, enforced
    while streaming in _store_upload().
    """
    if request.method != "POST" or not request.path.startswith("/admin/uploads"):
        return None
    try:
        request.max_content_length = MAX_UPLOAD_BYTES
    except AttributeError:
        # Older Flask cannot set this per request. The streaming check still
        # applies, but MAX_CONTENT_BYTES would need raising app-wide.
        current_app.logger.warning(
            "This Flask version cannot raise the request size cap per request; "
            "large VM uploads will be rejected. Raise MAX_CONTENT_BYTES instead."
        )
    return None


def init_app(app):
    """Attach SQLAlchemy, create the uploads table, and lift the size cap.

    Bound to `the_pond.db` to match the group's newer model files, which all set
    __bind_key__ = "pond".

    Call this BEFORE csrf.init_app() in create_app — see lift_request_size_cap().
    """
    app.before_request(lift_request_size_cap)

    app.config.setdefault(
        "SQLALCHEMY_BINDS",
        {"pond": f"sqlite:///{os.path.join(app.instance_path, 'the_pond.db')}"},
    )
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)

    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key="pond")
