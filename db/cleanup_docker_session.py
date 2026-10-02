"""Retry Docker cleanup for one closed attempt; never starts or inspects services."""
import argparse
from pathlib import Path
import sys
from db import db, ChallengeInstance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', required=True, type=int)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pond-sec'))
    from app import create_app, container_lab
    app = create_app()
    with app.app_context():
        row = db.session.get(ChallengeInstance, args.instance_id)
        if row is None:
            parser.error('Attempt does not exist.')
        if row.status in {'queued', 'provisioning', 'running'}:
            parser.error('Attempt is not closed. Reconcile a stopped launcher before changing its state.')
        if not container_lab.stop(row):
            parser.exit(1, 'Cleanup still pending; operation key and session reference retained.\n')
        print('Docker cleanup acknowledged (or no Docker session was requested).')


if __name__ == '__main__':
    main()
