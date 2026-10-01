"""Compatibility entry point; prefer python3 -m db.init_database."""

from db.init_database import check_schema, initialise_database, main, seed_challenges_from_yaml


if __name__ == "__main__":
    main()
