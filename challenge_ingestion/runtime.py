"""Bounded ingestion worker runner using the same trusted config as the web app."""
import argparse
import json
from pathlib import Path
import sys
from .notifications import NotificationService
from .publication import PublicationWorker
from .storage import QuarantineStorage
from .worker import ValidationWorker
from db import db


def check_configuration(config):
    """Read-only readiness report; never contacts the hypervisor or claims a job."""
    adapter = config.get('INGESTION_PUBLICATION_ADAPTER')
    return {
        'quarantine_configured': bool(config.get('UPLOAD_QUARANTINE_ROOT')),
        'template_verifier_configured': callable(config.get('INGESTION_VERIFY_TEMPLATE')),
        'image_inspector_configured': callable(config.get('INGESTION_INSPECT_IMAGE')),
        'publication_adapter_configured': all(callable(getattr(adapter, name, None))
                                              for name in ('plan', 'prepare', 'ready')),
    }


def process_once(app, *, worker_id, mode='all'):
    """At most one validation, one publication, and one notification batch.

    Use separate processes with mode=validate/publish/notify for slow VM tasks.
    Missing adapters are never substituted with successful checks.
    """
    if mode not in {'all', 'validate', 'publish', 'notify'}:
        raise ValueError('Unknown worker mode')
    results = {}
    with app.app_context():
        engine = db.engines['pond']
        if mode != 'notify':
            root = app.config.get('UPLOAD_QUARANTINE_ROOT')
            if not root:
                raise ValueError('UPLOAD_QUARANTINE_ROOT is required')
            with QuarantineStorage(root) as storage:
                kwargs = dict(worker_id=worker_id,
                    verify_template=app.config.get('INGESTION_VERIFY_TEMPLATE'),
                    inspect_image=app.config.get('INGESTION_INSPECT_IMAGE'))
                if mode in {'all', 'validate'}:
                    results['validation_job'] = ValidationWorker(engine, storage, **kwargs).run_once()
                if mode in {'all', 'publish'}:
                    if check_configuration(app.config)['publication_adapter_configured']:
                        results['publication_job'] = PublicationWorker(engine, storage,
                            adapter=app.config['INGESTION_PUBLICATION_ADAPTER'], **kwargs).run_once()
                    elif mode == 'publish':
                        raise ValueError('Publication adapter is not configured')
                    else:
                        results['publication_disabled'] = True
        if mode in {'all', 'notify'}:
            delivery = NotificationService(engine).deliver_pending(limit=100)
            results['notifications_sent'] = delivery.sent
            results['notifications_failed'] = delivery.failed
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Report configuration only; do no work')
    parser.add_argument('--worker-id', default='ingestion-worker')
    parser.add_argument('--mode', choices=['all','validate','publish','notify'], default='all')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'pond-sec'))
    from app import create_app
    app = create_app()
    result = check_configuration(app.config) if args.check else process_once(app, worker_id=args.worker_id, mode=args.mode)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
