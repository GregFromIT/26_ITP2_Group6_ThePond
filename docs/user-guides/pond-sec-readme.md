# Pond Sec

Flask + SQLite orchestrator for a cyber teaching range. Students register, pick
a theme, launch a challenge, and the app clones a Proxmox VM, times the session,
grades flags and updates the leaderboards.

```bash
pip install Flask
flask --app wsgi init-db
flask --app wsgi seed-db
flask --app wsgi run --debug        # http://127.0.0.1:5000, sign in as bpt / rootroot
```

## Documentation

All of it lives in `docs/`:

| | |
|---|---|
| [`pond-sec/docs/README.md`](../../pond-sec/docs/README.md) | The main one. Setup, roles, schema, hardening, Proxmox, and the to-do list |
| [`pond-sec-code-map.md`](../architecture/pond-sec-code-map.md) | What each Python file does, and where to look when something breaks |
| [`pond-sec-data-model.md`](../database/pond-sec-data-model.md) | The tables, how they relate, and what each constraint is stopping |
| [`pond-sec-user-guide.md`](pond-sec-user-guide.md) | How to use the platform, for students and for staff |
| [`pond-sec-design-decisions.md`](../architecture/pond-sec-design-decisions.md) | Why the design is the way it is, with the alternatives we rejected |

`preview/pond-sec-preview.html` opens in a browser with no install and shows
every screen.

Tests: `python -m tests.test_flow`. No pytest needed.
