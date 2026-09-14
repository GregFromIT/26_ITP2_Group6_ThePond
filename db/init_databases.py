"""Initialise The Pond's tables and default roles using the web app configuration.

Save as db/init_database.py and run from the repository root with the web
server's virtual environment and environment settings:

    python3 -m db.init_database --check
    python3 -m db.init_database

Optional challenge seeding (requires configured Proxmox access):

    python3 -m db.init_database --seed-challenges

Existing tables and roles are preserved. This is not a schema migration:
missing columns on existing tables are reported, not repaired. Accounts are
created separately with python3 -m db.seed_accounts.
"""

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pond-sec"))

from app import create_app
from db.orm import db
from db.user_models import Role, User, UserCredential
from db.challenge_models import Challenge, NetworkRule
from db.VMs_models import VMTemplate, ChallengeFlag
from db.runtime_models import ChallengeInstance, VMInstance, InstanceJob
from db.scoring_models import FlagSubmission, UserSolve
from db.audit_models import AuditLog
from db.throttle_models import ThrottleEvent
from sqlalchemy import inspect

CHALLENGES_DIR = Path(__file__).parent.parent / "vars" / "challenges"

def _hash_flag(flag: str) -> str:
        return hashlib.sha256(flag.strip().lower().encode()).hexdigest()

def seed_challenges_from_yaml():
       import provisioner
       import yaml

       if not CHALLENGES_DIR.exists():
           print(f"no challenges dir at {CHALLENGES_DIR}, skipping challenge seeding.")
           return

       config = provisioner.load_config()
       client = provisioner.get_client(config)
       node = config["proxmox_node"]

       for path in sorted(CHALLENGES_DIR.glob("*.yml")):
           with open(path) as f:
               cfg = yaml.safe_load(f)
           slug = cfg["challenge"]

           challenge = db.session.execute(
               db.select(Challenge).filter_by(title=slug)
           ).scalar_one_or_none()
           if challenge is None:
               challenge = Challenge(
                   title=slug,
                   description=f"Seeded from vars/challenges/{path.name}",
                   instructions="See the challenge brief for connection details.",
                   category=cfg.get("category", "general"),
                   difficulty=cfg.get("difficulty"),
                   status="published",
               )
               db.session.add(challenge)
               db.session.commit()
               print(f"  + challenge: {slug}")

           template_specs = cfg.get("vm_templates") or [{"name": cfg["vm_template"], "role": "target"}]
           for spec in template_specs:
               template_name = spec["name"] if isinstance(spec, dict) else spec
               role = spec.get("role", "target") if isinstance(spec, dict) else "target"
               static_ip = spec.get("static_ip") if isinstance(spec, dict) else None
               template_vmid = provisioner._resolve_template_vmid(client, node, template_name)

               template = db.session.execute(
                   db.select(VMTemplate).filter_by(proxmox_template_vmid=template_vmid)
               ).scalar_one_or_none()
               if template is None:
                   template = VMTemplate(
                       challenge_id=challenge.challenge_id,
                       template_name=template_name,
                       proxmox_template_vmid=template_vmid,
                       proxmox_node=node,
                       vm_role=role,
                       static_ip=static_ip,
                   )
                   db.session.add(template)
                   db.session.commit()
                   print(f"    + vm_template: {template_name} (vmid {template_vmid}, role={role})")

               flag_plain = cfg.get("flag")
               if flag_plain:
                    if "points" not in cfg:
                        raise ValueError(
                            f"{path.name}: challenge '{slug}' has a flag but no 'points:' value. "
                            "Every flag must specify its own points explicitly in the YAML - "
                            "there is no default, so add one and re-run."
                        )
                    flag_hash = _hash_flag(flag_plain)
                    existing_flag = db.session.execute(
                        db.select(ChallengeFlag).filter_by(template_id=template.template_id, flag_hash=flag_hash)
                    ).scalar_one_or_none()
                    if existing_flag is None:
                        db.session.add(ChallengeFlag(
                            template_id=template.template_id,
                            flag_name=f"{slug}-flag",
                            flag_hash=flag_hash,
                            points=cfg["points"],
                        ))
                        db.session.commit()
                        print(f"    + flag for {template_name}")

           for rule in cfg.get("network_rules", []):
               existing_rule = db.session.execute(
                   db.select(NetworkRule).filter_by(
                       challenge_id=challenge.challenge_id,
                       from_role=rule["from"], to_role=rule["to"], port=rule["port"],
                   )
               ).scalar_one_or_none()
               if existing_rule is None:
                   db.session.add(NetworkRule(
                       challenge_id=challenge.challenge_id,
                       from_role=rule["from"],
                       to_role=rule["to"],
                       protocol=rule.get("protocol", "tcp"),
                       port=rule["port"],
                   ))
                   db.session.commit()
                   print(f"    + network_rule: {rule['from']} -> {rule['to']}:{rule['port']}")



DEFAULT_ROLES = [
    ("user", 1, "Standard user"),
    ("manager", 2, "User with demo mode and password reset permissions"),
    ("sysadmin", 3, "Full system administrator"),
]


def check_schema(engine):
    """Report missing tables and columns against registered model metadata."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    problems = []
    for table in db.metadatas["pond"].sorted_tables:
        if table.name not in existing:
            problems.append(f"Missing table: {table.name}")
            continue
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        for name in sorted(set(table.columns.keys()) - columns):
            problems.append(f"Missing column: {table.name}.{name}")
    return problems


def initialise_database(check_only=False, seed_challenges=False):
    """Run inside the web app's application context."""
    engine = db.engines["pond"]
    print(f"Database: {engine.url.render_as_string(hide_password=True)}", flush=True)
    if check_only and engine.dialect.name == "sqlite":
        filename = engine.url.database
        if not filename or filename == ":memory:" or not Path(filename).is_file():
            raise RuntimeError("Database file does not exist. Run without --check to initialise it.")

    if not check_only:
        print("Creating missing database tables...")
        db.create_all(bind_key="pond")

    problems = check_schema(engine)
    if problems:
        raise RuntimeError("\n".join(problems) +
                           "\nInitialisation adds missing tables but cannot migrate existing tables.")
    print("Verified all registered tables and expected column names, including throttle_events.")

    existing_roles = {
        role.role_name: role
        for role in db.session.execute(db.select(Role)).scalars()
    }
    for name, level, description in DEFAULT_ROLES:
        if name in existing_roles and existing_roles[name].role_level != level:
            raise RuntimeError(f"Role {name} has an unexpected level; review it before continuing.")
        for role in existing_roles.values():
            if role.role_level == level and role.role_name != name:
                raise RuntimeError(f"Role level {level} is already assigned to {role.role_name}.")

    missing = [spec for spec in DEFAULT_ROLES if spec[0] not in existing_roles]
    if check_only:
        if missing:
            raise RuntimeError("Missing roles: " + ", ".join(spec[0] for spec in missing))
        print("Check complete. All three default roles exist. No accounts or roles changed.")
        return

    try:
        for name, level, description in missing:
            db.session.add(Role(role_name=name, role_level=level, description=description))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    print(f"Default roles ready: user, manager, sysadmin ({len(missing)} added).")
    print("Database tables and roles initialised successfully.")

    if seed_challenges:
        print("Seeding challenges using Proxmox. This step may commit partial progress.")
        try:
            seed_challenges_from_yaml()
        except Exception:
            db.session.rollback()
            print("Challenge seeding failed; tables and roles remain initialised.", file=sys.stderr)
            raise
    print("Next: python3 -m db.seed_accounts --check")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Check tables, column names and roles without initialising them.")
    mode.add_argument("--seed-challenges", action="store_true",
                      help="Also run the existing YAML challenge seeder; requires Proxmox access.")
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        try:
            initialise_database(check_only=args.check, seed_challenges=args.seed_challenges)
        except (RuntimeError, ValueError) as error:
            parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
