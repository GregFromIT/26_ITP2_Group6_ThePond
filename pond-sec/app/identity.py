"""ORM-backed identity layer, replacing the raw sqlite user/password_manager
tables. security.py and auth.py get exactly the row shape they always had -
a dict with the old field names - backed by db/user_models.py's real tables.
"""

from datetime import datetime

from db.orm import db
from db.user_models import Role, User, UserCredential
from db.scoring_models import UserSolve

ROLE_NAME_TO_APP = {"user": "student", "manager": "moderator", "sysadmin": "admin"}
ROLE_NAME_TO_DB = {v: k for k, v in ROLE_NAME_TO_APP.items()}


def _row_from_user(user: User) -> dict:
    cred = user.credentials
    points = (
        db.session.query(db.func.coalesce(db.func.sum(UserSolve.awarded_points), 0))
        .filter(UserSolve.user_id == user.user_id)
        .scalar()
    )
    return {
        "user_id": user.user_id,
        "username": user.username,
        "name": user.display_name,
        "uni_year": None,
        "points": points,
        "failed_attempts": cred.failed_login_count if cred else 0,
        "locked_until": cred.locked_until.strftime("%Y-%m-%d %H:%M:%S") if cred and cred.locked_until else None,
        "must_change_password": 1 if (cred and cred.must_change_password) else 0,
        "role": ROLE_NAME_TO_APP.get(user.role.role_name, "student") if user.role else "student",
        "role_set_at": None,
        "role_set_by": None,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def get_user_row(user_id: int) -> dict | None:
    user = db.session.get(User, user_id)
    return _row_from_user(user) if user else None


def get_user_row_by_username(username: str) -> dict | None:
    user = db.session.execute(
        db.select(User).filter(db.func.lower(User.username) == username.lower())
    ).scalar_one_or_none()
    return _row_from_user(user) if user else None


def username_taken(username: str) -> bool:
    return get_user_row_by_username(username) is not None


def create_user(name: str, uni_year: str, username: str) -> int:
    role = db.session.execute(db.select(Role).filter_by(role_name="user")).scalar_one_or_none()
    if role is None:
        raise RuntimeError("no 'user' role found - run `python -m db.init_database` first.")
    user = User(username=username, display_name=name, role_id=role.role_id)
    db.session.add(user)
    db.session.commit()
    return user.user_id


def touch_last_login(user_id: int):
    user = db.session.get(User, user_id)
    user.last_login_at = datetime.utcnow()
    db.session.commit()