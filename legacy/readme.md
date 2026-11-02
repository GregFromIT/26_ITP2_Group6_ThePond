# Legacy

Code from earlier versions of The Pond that **no longer runs**. Kept as a
record of how the project developed; nothing in the running system uses it.

| File | What it was | Why it doesn't run |
|---|---|---|
| `debugger-app.py` | The single-file Flask debugger used before Pond Sec (V1-V2) | Imports `cli.py`, which was deleted |
| `db.test_database.py` | An early copy of the database integrity test | Imports `app`, which no longer exists. The working version is `db/test_database.py` |
| `init_user_db.py` | User-table setup for the old app | Imports `app`, which no longer exists |

Stale code that still works is listed in the main README, under *Repository
layout*.