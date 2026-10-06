"""Register an existing template's metadata. Does not create or inspect a VM."""
import argparse
from pathlib import Path
import sys
from db import db, VMTemplate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--vmid', type=int, required=True)
    parser.add_argument('--node', required=True)
    args = parser.parse_args(argv)
    if not args.name.strip() or len(args.name) > 100 or not args.node.strip() or len(args.node) > 100 or not 100 <= args.vmid <= 999999999:
        parser.error('Supply a name/node of 1–100 characters and a valid template VMID.')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pond-sec'))
    from app import create_app
    app = create_app()
    with app.app_context():
        row = db.session.scalar(db.select(VMTemplate).filter_by(proxmox_template_vmid=args.vmid))
        if row is not None:
            if row.proxmox_node != args.node or row.template_name != args.name:
                parser.error('That VMID is already registered with different metadata.')
        else:
            row = VMTemplate(template_name=args.name, proxmox_template_vmid=args.vmid, proxmox_node=args.node)
            db.session.add(row)
            db.session.commit()
        print(f'Template ID: {row.template_id}. Use this as workstation_template_id.')


if __name__ == '__main__':
    main()
