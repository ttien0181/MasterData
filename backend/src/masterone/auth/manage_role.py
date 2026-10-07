"""Assign one existing account a role, offline; dry run unless --apply is supplied."""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from masterone.auth.models import User
from masterone.auth.security import ROLES, normalized_email
from masterone.config import get_settings
from masterone.db.session import create_database


def change_role(db: Session, email: str, role: str, expected_role: str, *, apply: bool = False) -> dict:
    if role not in ROLES or expected_role not in ROLES:
        raise ValueError("Unknown role")
    user = db.scalar(select(User).where(User.email_normalized == normalized_email(email)))
    if user is None:
        raise ValueError("User does not exist")
    if user.role != expected_role:
        raise ValueError(f"Expected {expected_role}, found {user.role}; no changes made")
    result = {"user_id": user.id, "email": user.email, "from": user.role, "to": role, "applied": apply}
    if apply:
        # Compare-and-set also protects against a concurrent operator changing the role.
        count = db.query(User).filter_by(id=user.id, role=expected_role).update({"role": role})
        if count != 1:
            db.rollback()
            raise ValueError("Role changed concurrently; no changes made")
        db.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--expected-role", choices=ROLES, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine, factory = create_database(get_settings().active_mdm_database_url)
    try:
        with factory() as db:
            try:
                print(change_role(db, args.user_email, args.role, args.expected_role, apply=args.apply))
            except ValueError as exc:
                parser.error(str(exc))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
