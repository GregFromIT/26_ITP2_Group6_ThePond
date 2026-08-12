"""
db/init_database.py

Creates and initialises the SQLAlchemy database for The Pond.

Usage:

    python -m db.init_database
"""

from db.database_app import app
from db.orm import db

# Import every model so SQLAlchemy knows about every table.
from db.user_models import Role, User, UserCredential
from db.challenge_models import Challenge
from db.VMs_models import VMTemplate, ChallengeFlag
from db.runtime_models import ChallengeInstance, VMInstance, InstanceJob
from db.scoring_models import FlagSubmission, UserSolve
from db.audit_models import AuditLog


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

        print("The Pond database initialisation complete.")


if __name__ == "__main__":
    initialise_database()
