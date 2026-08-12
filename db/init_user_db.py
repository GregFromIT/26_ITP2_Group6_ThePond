"""
Create and initialise user.db.

Usage:

    python3 -m db.init_user_db
"""

from app import app
from db.orm import db
from db.user_models import Role


def initialise_user_database():
    with app.app_context():

        # Create the tables belonging only to the "users" bind.
        db.create_all(bind_key="users")

        print("User database tables created.")

        # Seed the system roles if they don't already exist.
        existing_roles = db.session.execute(
            db.select(Role)
        ).scalars().all()

        if not existing_roles:

            roles = [
                Role(
                    role_name="user",
                    role_level=1,
                    description="Standard teaching environment user"
                ),
                Role(
                    role_name="manager",
                    role_level=2,
                    description=(
                        "User with challenge demo and "
                        "password reset permissions"
                    )
                ),
                Role(
                    role_name="sysadmin",
                    role_level=3,
                    description="Full system administrator"
                )
            ]

            db.session.add_all(roles)
            db.session.commit()

            print("Default roles created.")

        else:
            print("Default roles already exist.")

        print("user.db initialisation complete.")


if __name__ == "__main__":
    initialise_user_database()
