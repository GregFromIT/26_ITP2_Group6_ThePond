"""
db/test_database.py

Relational integrity tests for The Pond database.

Usage:

    python -m db.test_database

This script creates temporary test records, checks relationships and
constraints, then removes the test data.
"""

import hashlib
import random
import uuid

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app import app
from db.orm import db

from db.user_models import (
    Role,
    User,
    UserCredential,
)

from db.challenge_models import Challenge

from db.VMs_models import (
    VMTemplate,
    ChallengeFlag,
)

from db.runtime_models import (
    ChallengeInstance,
    VMInstance,
    InstanceJob,
)

from db.scoring_models import (
    FlagSubmission,
    UserSolve,
)


TEST_NAMES = {
    1: "FIRST TEST",
    2: "SECOND TEST",
    3: "THIRD TEST",
    4: "FOURTH TEST",
    5: "FIFTH TEST",
    6: "SIXTH TEST",
    7: "SEVENTH TEST",
    8: "EIGHTH TEST",
    9: "NINTH TEST",
    10: "TENTH TEST",
    11: "ELEVENTH TEST",
    12: "TWELFTH TEST",
    13: "THIRTEENTH TEST",
    14: "FOURTEENTH TEST",
    15: "FIFTEENTH TEST",
    16: "SIXTEENTH TEST",
}


def start_test(number, name, description):
    print(
        f"{number}. {TEST_NAMES[number]} - "
        f"{name} - {description}"
    )


def passed_test(number):
    print(f"{TEST_NAMES[number]} [PASSED]")
    print()


def failed_test(number, error):
    print(f"{TEST_NAMES[number]} [FAILED]")
    print(f"Reason: {error}")
    print()


def cleanup_test_data(
    test_username,
    admin_username,
    challenge_title
):
    """
    Remove temporary records created by the test suite.
    """

    db.session.rollback()

    users = db.session.execute(
        db.select(User).where(
            User.username.in_([
                test_username,
                admin_username,
            ])
        )
    ).scalars().all()

    for user in users:
        db.session.delete(user)

    db.session.commit()

    challenges = db.session.execute(
        db.select(Challenge).where(
            Challenge.title == challenge_title
        )
    ).scalars().all()

    for challenge in challenges:
        db.session.delete(challenge)

    db.session.commit()


def run_tests():

    marker = uuid.uuid4().hex[:8]

    test_username = f"__dbtest_user_{marker}"
    admin_username = f"__dbtest_admin_{marker}"
    challenge_title = f"__dbtest_challenge_{marker}"

    base_template_vmid = random.randint(
        800000,
        850000
    )

    base_instance_vmid = random.randint(
        900000,
        950000
    )

    current_test = None

    with app.app_context():

        try:

            # ---------------------------------------------------------
            # TEST 1
            # ---------------------------------------------------------

            current_test = 1

            start_test(
                1,
                "SQLite Foreign Keys",
                "Confirm foreign key enforcement is enabled"
            )

            connection = db.session.connection()

            foreign_keys_enabled = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar()

            assert foreign_keys_enabled == 1

            passed_test(1)


            # ---------------------------------------------------------
            # TEST 2
            # ---------------------------------------------------------

            current_test = 2

            start_test(
                2,
                "Default Roles",
                "Confirm the user and sysadmin roles can be retrieved"
            )

            user_role = db.session.execute(
                db.select(Role).where(
                    Role.role_name == "user"
                )
            ).scalar_one()

            sysadmin_role = db.session.execute(
                db.select(Role).where(
                    Role.role_name == "sysadmin"
                )
            ).scalar_one()

            passed_test(2)


            # ---------------------------------------------------------
            # TEST 3
            # ---------------------------------------------------------

            current_test = 3

            start_test(
                3,
                "Users and Credentials",
                "Create test users with linked credentials"
            )

            admin = User(
                username=admin_username,
                display_name="Database Test Sysadmin",
                role_id=sysadmin_role.role_id
            )

            admin.credentials = UserCredential(
                password_hash=(
                    "TEST_ONLY_NOT_A_REAL_PASSWORD_HASH"
                )
            )

            test_user = User(
                username=test_username,
                display_name="Database Test User",
                role_id=user_role.role_id
            )

            test_user.credentials = UserCredential(
                password_hash=(
                    "TEST_ONLY_NOT_A_REAL_PASSWORD_HASH"
                )
            )

            db.session.add_all([
                admin,
                test_user,
            ])

            db.session.commit()

            passed_test(3)


            # ---------------------------------------------------------
            # TEST 4
            # ---------------------------------------------------------

            current_test = 4

            start_test(
                4,
                "User Roles",
                "Confirm users are linked to the correct roles"
            )

            assert test_user.role.role_name == "user"
            assert admin.role.role_name == "sysadmin"

            passed_test(4)


            # ---------------------------------------------------------
            # TEST 5
            # ---------------------------------------------------------

            current_test = 5

            start_test(
                5,
                "Challenge Creation",
                "Create a challenge linked to the test sysadmin"
            )

            challenge = Challenge(
                title=challenge_title,
                description=(
                    "Temporary relational database test"
                ),
                instructions="Test challenge only",
                category="database-test",
                difficulty="test",
                status="draft",
                time_limit_minutes=60,
                created_by_user_id=admin.user_id
            )

            db.session.add(challenge)
            db.session.commit()

            assert (
                challenge.created_by_user_id
                == admin.user_id
            )

            passed_test(5)


            # ---------------------------------------------------------
            # TEST 6
            # ---------------------------------------------------------

            current_test = 6

            start_test(
                6,
                "VM Templates",
                "Create multiple VM templates linked to one challenge"
            )

            attacker_template = VMTemplate(
                challenge_id=challenge.challenge_id,
                template_name=f"Test Kali {marker}",
                proxmox_template_vmid=base_template_vmid,
                proxmox_node="test-node",
                vm_role="attacker",
                cpu_cores=2,
                memory_mb=2048,
                boot_order=1,
                is_user_accessible=True
            )

            victim_template = VMTemplate(
                challenge_id=challenge.challenge_id,
                template_name=f"Test Victim {marker}",
                proxmox_template_vmid=(
                    base_template_vmid + 1
                ),
                proxmox_node="test-node",
                vm_role="victim",
                cpu_cores=2,
                memory_mb=2048,
                boot_order=2,
                is_user_accessible=True
            )

            db.session.add_all([
                attacker_template,
                victim_template,
            ])

            db.session.commit()

            assert len(challenge.vm_templates) == 2

            passed_test(6)


            # ---------------------------------------------------------
            # TEST 7
            # ---------------------------------------------------------

            current_test = 7

            start_test(
                7,
                "Challenge Flags",
                "Create a flag linked to a VM template"
            )

            plaintext_flag = (
                "POND{RELATIONAL_TEST}"
            )

            flag_hash = hashlib.sha256(
                plaintext_flag.encode()
            ).hexdigest()

            flag = ChallengeFlag(
                template_id=victim_template.template_id,
                flag_name="Relational Test Flag",
                flag_hash=flag_hash,
                points=50,
                sequence_number=1
            )

            db.session.add(flag)
            db.session.commit()

            assert (
                flag.vm_template.template_id
                == victim_template.template_id
            )

            passed_test(7)


            # ---------------------------------------------------------
            # TEST 8
            # ---------------------------------------------------------

            current_test = 8

            start_test(
                8,
                "Challenge Instance",
                "Create a running challenge linked to a user and challenge"
            )

            challenge_instance = ChallengeInstance(
                user_id=test_user.user_id,
                challenge_id=challenge.challenge_id,
                instance_mode="normal",
                scoring_enabled=True,
                status="running",
                network_identifier=(
                    f"test-network-{marker}"
                )
            )

            db.session.add(challenge_instance)
            db.session.commit()

            assert (
                challenge_instance.user_id
                == test_user.user_id
            )

            assert (
                challenge_instance.challenge_id
                == challenge.challenge_id
            )

            passed_test(8)


            # ---------------------------------------------------------
            # TEST 9
            # ---------------------------------------------------------

            current_test = 9

            start_test(
                9,
                "VM Instances",
                "Create running Proxmox VM records for a challenge instance"
            )

            attacker_instance = VMInstance(
                instance_id=(
                    challenge_instance.instance_id
                ),
                template_id=(
                    attacker_template.template_id
                ),
                proxmox_vmid=base_instance_vmid,
                proxmox_node="test-node",
                hostname=(
                    f"test-attacker-{marker}"
                ),
                status="running"
            )

            victim_instance = VMInstance(
                instance_id=(
                    challenge_instance.instance_id
                ),
                template_id=(
                    victim_template.template_id
                ),
                proxmox_vmid=(
                    base_instance_vmid + 1
                ),
                proxmox_node="test-node",
                hostname=(
                    f"test-victim-{marker}"
                ),
                status="running"
            )

            db.session.add_all([
                attacker_instance,
                victim_instance,
            ])

            db.session.commit()

            assert (
                len(challenge_instance.vm_instances)
                == 2
            )

            passed_test(9)


            # ---------------------------------------------------------
            # TEST 10
            # ---------------------------------------------------------

            current_test = 10

            start_test(
                10,
                "Instance Jobs",
                "Create a Python Instance Manager job linked to the challenge"
            )

            job = InstanceJob(
                instance_id=(
                    challenge_instance.instance_id
                ),
                requested_by_user_id=(
                    test_user.user_id
                ),
                action="launch",
                status="completed",
                attempt_count=1
            )

            db.session.add(job)
            db.session.commit()

            assert (
                job.challenge_instance.instance_id
                == challenge_instance.instance_id
            )

            passed_test(10)


            # ---------------------------------------------------------
            # TEST 11
            # ---------------------------------------------------------

            current_test = 11

            start_test(
                11,
                "Flag Submission",
                "Record a correct flag submission"
            )

            submission = FlagSubmission(
                user_id=test_user.user_id,
                instance_id=(
                    challenge_instance.instance_id
                ),
                vm_instance_id=(
                    victim_instance.vm_instance_id
                ),
                matched_flag_id=flag.flag_id,
                was_correct=True,
                was_already_solved=False,
                scoring_enabled=True
            )

            db.session.add(submission)
            db.session.commit()

            assert (
                submission.matched_flag_id
                == flag.flag_id
            )

            passed_test(11)


            # ---------------------------------------------------------
            # TEST 12
            # ---------------------------------------------------------

            current_test = 12

            start_test(
                12,
                "User Solve",
                "Award points for a correctly solved flag"
            )

            solve = UserSolve(
                user_id=test_user.user_id,
                flag_id=flag.flag_id,
                instance_id=(
                    challenge_instance.instance_id
                ),
                awarded_points=flag.points
            )

            db.session.add(solve)
            db.session.commit()

            assert solve.awarded_points == 50

            passed_test(12)


            # ---------------------------------------------------------
            # TEST 13
            # ---------------------------------------------------------

            current_test = 13

            start_test(
                13,
                "Duplicate Scoring",
                "Confirm the same user cannot score the same flag twice"
            )

            duplicate_solve = UserSolve(
                user_id=test_user.user_id,
                flag_id=flag.flag_id,
                instance_id=(
                    challenge_instance.instance_id
                ),
                awarded_points=flag.points
            )

            db.session.add(duplicate_solve)

            try:
                db.session.commit()

                raise AssertionError(
                    "Duplicate solve was incorrectly accepted"
                )

            except IntegrityError:
                db.session.rollback()

            passed_test(13)


            # ---------------------------------------------------------
            # TEST 14
            # ---------------------------------------------------------

            current_test = 14

            start_test(
                14,
                "Invalid User Role",
                "Confirm a user cannot reference a role that does not exist"
            )

            invalid_user = User(
                username=f"__invalid_user_{marker}",
                display_name="Invalid User",
                role_id=999999999
            )

            db.session.add(invalid_user)

            try:
                db.session.commit()

                raise AssertionError(
                    "Invalid role foreign key was accepted"
                )

            except IntegrityError:
                db.session.rollback()

            passed_test(14)


            # ---------------------------------------------------------
            # TEST 15
            # ---------------------------------------------------------

            current_test = 15

            start_test(
                15,
                "Invalid Challenge",
                "Confirm a VM template cannot reference a nonexistent challenge"
            )

            invalid_template = VMTemplate(
                challenge_id=999999999,
                template_name=(
                    f"Invalid Template {marker}"
                ),
                proxmox_template_vmid=(
                    base_template_vmid + 100
                ),
                proxmox_node="test-node",
                vm_role="invalid"
            )

            db.session.add(invalid_template)

            try:
                db.session.commit()

                raise AssertionError(
                    "Invalid challenge foreign key was accepted"
                )

            except IntegrityError:
                db.session.rollback()

            passed_test(15)


            # ---------------------------------------------------------
            # TEST 16
            # ---------------------------------------------------------

            current_test = 16

            start_test(
                16,
                "Credential Cascade",
                "Confirm deleting a user also deletes their credentials"
            )

            cascade_user = User(
                username=(
                    f"__cascade_user_{marker}"
                ),
                display_name="Cascade Test User",
                role_id=user_role.role_id
            )

            cascade_user.credentials = UserCredential(
                password_hash="TEST_ONLY"
            )

            db.session.add(cascade_user)
            db.session.commit()

            cascade_user_id = cascade_user.user_id

            assert db.session.get(
                UserCredential,
                cascade_user_id
            ) is not None

            db.session.execute(
                delete(User).where(
                    User.user_id
                    == cascade_user_id
                )
            )

            db.session.commit()

            assert db.session.get(
                UserCredential,
                cascade_user_id
            ) is None

            passed_test(16)

            print("RELATIONAL DATABASE TESTS [PASSED]")
            print()

        except Exception as error:

            db.session.rollback()

            if current_test is not None:
                failed_test(
                    current_test,
                    error
                )

            print(
                "RELATIONAL DATABASE TESTS [FAILED]"
            )

        finally:

            print(
                "CLEANUP - Removing temporary test data"
            )

            cleanup_test_data(
                test_username,
                admin_username,
                challenge_title
            )

            print("CLEANUP [COMPLETE]")
            print()


if __name__ == "__main__":
    run_tests()
