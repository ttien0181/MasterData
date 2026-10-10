"""Delete staged upload parts in R2 that no draft references, offline. Dry run unless --apply is given."""

import argparse

from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.upload_sessions import sweep_orphan_parts
from masterone.storage.r2 import get_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete objects; default only counts them")
    args = parser.parse_args()
    settings = get_settings()
    store = get_store(settings)
    if store is None:
        parser.error("Cloudflare R2 is not configured")
    engine, factory = create_database(settings.active_mdm_database_url)
    with factory() as db:
        count = sweep_orphan_parts(db, store, dry_run=not args.apply)
        print(f"{'Deleted' if args.apply else 'Dry run: would delete'} {count} orphan part object(s)")
    engine.dispose()


if __name__ == "__main__":
    main()
