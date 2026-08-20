"""
db/init_database.py

Creates and initialises the SQLAlchemy database for The Pond.

Usage:

    python -m db.init_database
"""
import hashlib
import yaml
from pathlib import Path
from db.throttle_models import ThrottleEvent
from db.database_app import app
from db.orm import db

# Import every model so SQLAlchemy knows about every table.
from db.user_models import Role, User, UserCredential
from db.challenge_models import Challenge
from db.VMs_models import VMTemplate, ChallengeFlag
from db.runtime_models import ChallengeInstance, VMInstance, InstanceJob
from db.scoring_models import FlagSubmission, UserSolve
from db.audit_models import AuditLog

CHALLENGES_DIR = Path(__file__).parent.parent / "vars" / "challenges"
# DEFAULT_FLAG_POINTS = 100

def _hash_flag(flag: str) -> str:
        return hashlib.sha256(flag.strip().lower().encode()).hexdigest()

def seed_challenges_from_yaml():
       import provisioner

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
                   )

                   db.session.add(template)
                   db.session.commit()
                   print(f"    + vm_template: {template_name} (vmid {template_vmid}, role={role})")

               flag_plain = cfg.get("flag")
               if flag_plain:
                   flag_hash = _hash_flag(flag_plain)
                   existing_flag = db.session.execute(
                       db.select(ChallengeFlag).filter_by(template_id=template.template_id, flag_hash=flag_hash)
                   ).scalar_one_or_none()
                   if existing_flag is None:
                       db.session.add(ChallengeFlag(
                           template_id=template.template_id,
                           flag_name=f"{slug}-flag",
                           flag_hash=flag_hash,
                           points=cfg.get("points"), #, DEFAULT_FLAG_POINTS),
                       ))
                       db.session.commit()
                       print(f"    + flag for {template_name}")


def initialise_database():
    with app.app_context():

        print("Creating The Pond database tables...")

        db.create_all(bind_key="pond")

        print("Database tables created.")

        # Seed the three system roles if they do not already exist.
        role_names = {
            role.role_name
            for role in db.session.execute(
                db.select(Role)
            ).scalars().all()
        }

        default_roles = [
            (
                "user",
                1,
                "Standard user"
            ),
            (
                "manager",
                2,
                "User with demo mode and password reset permissions"
            ),
            (
                "sysadmin",
                3,
                "Full system administrator"
            ),
        ]

        roles_added = False

        for role_name, role_level, description in default_roles:
            if role_name not in role_names:
                db.session.add(
                    Role(
                        role_name=role_name,
                        role_level=role_level,
                        description=description
                    )
                )

                roles_added = True

        if roles_added:
            db.session.commit()
            print("Default roles created.")
        else:
            print("Default roles already exist.")

        print("Seeding challenges from vars/challenges/*.yml...")
        seed_challenges_from_yaml()
        
        print("The Pond database initialisation complete.")

if __name__ == "__main__":
    initialise_database()
