"""Assign explicitly selected legacy imports to an existing account, offline."""

import argparse

from sqlalchemy import select

from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import ImportBatch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--import-id", action="append", required=True, help="Repeat for each legacy import")
    parser.add_argument("--apply", action="store_true", help="Write changes; default is dry run")
    args = parser.parse_args()
    engine, factory = create_database(get_settings().mdm_database_url)
    with factory() as db:
        user = db.scalar(select(User).where(User.email_normalized == args.user_email.strip().casefold()))
        if not user:
            parser.error("User does not exist")
        batches = []
        for import_id in set(args.import_id):
            batch = db.get(ImportBatch, import_id)
            if batch is None or batch.owner_user_id is not None:
                parser.error(f"Import {import_id} does not exist or is already owned")
            batches.append(batch)
        print(f"{'Applying' if args.apply else 'Dry run'}: claim {len(batches)} import(s) for {user.email}")
        for batch in batches:
            print(batch.id)
            if args.apply:
                batch.owner_user_id = user.id
        if args.apply:
            db.commit()
    engine.dispose()


if __name__ == "__main__":
    main()
