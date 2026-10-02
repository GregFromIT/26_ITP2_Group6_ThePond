"""Docker-server lifecycle boundary; no file, image or readiness verification.

A trusted deployment adapter implements start(challenge_key, operation_key,
workstation) -> opaque session reference, and stop(operation_key, session_ref).
start/stop must be idempotent by operation_key. stop must reconcile a lost start
response even when session_ref is None. Secrets/endpoints belong in that adapter,
not in student responses. The Docker server owns files, services and their cleanup.
"""
import secrets
from flask import current_app
from db.orm import db


class ContainerLabError(RuntimeError):
    pass


def adapter():
    value = current_app.config.get("CONTAINER_LAB_ADAPTER")
    if not all(callable(getattr(value, name, None)) for name in ("start", "stop")):
        raise ContainerLabError("The Docker challenge server is not configured.")
    return value


def start(instance, workstation):
    service = adapter()
    if instance.docker_status == "active":
        return instance.docker_session_ref
    if instance.docker_status != "not_requested":
        raise ContainerLabError("An earlier Docker request needs reconciliation before retrying.")
    instance.docker_operation_key = "pond-" + secrets.token_hex(24)
    instance.docker_status = "requested"
    instance.docker_error = None
    db.session.commit()  # Identity survives an uncertain external request.
    try:
        reference = service.start(challenge_key=instance.docker_challenge_key,
                                  operation_key=instance.docker_operation_key,
                                  workstation=workstation)
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 255:
            raise ValueError("Invalid session reference")
        instance.docker_session_ref = reference
        instance.docker_status = "active"
        db.session.commit()
        return reference
    except Exception as error:
        db.session.rollback()
        instance.docker_status = "cleanup_pending"
        instance.docker_error = "Docker launch did not complete; cleanup or reconciliation is required."
        db.session.commit()
        raise ContainerLabError(instance.docker_error) from error


def stop(instance):
    """Return success; retain the operation identity on failure for operator retry."""
    if instance.docker_status in {"not_requested", "closed"}:
        return True
    instance.docker_status = "cleanup_pending"
    db.session.commit()
    try:
        if adapter().stop(operation_key=instance.docker_operation_key,
                          session_ref=instance.docker_session_ref) is not True:
            raise ValueError("Cleanup not acknowledged")
    except Exception:
        db.session.rollback()
        instance.docker_status = "cleanup_pending"
        instance.docker_error = "Docker cleanup is pending. Retry after service recovery."
        db.session.commit()
        current_app.logger.warning("Docker cleanup pending for attempt %s", instance.instance_id)
        return False
    instance.docker_status = "closed"
    instance.docker_error = None
    db.session.commit()
    return True
