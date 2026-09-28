"""Entry point — the object a WSGI server imports.

Development:  flask --app wsgi run --debug          (Linux, macOS, Windows)
Production:   RANGE_ENV=production FLASK_SECRET_KEY=... \
              gunicorn 'wsgi:app' -b 127.0.0.1:8000 --workers 4
Windows:      waitress-serve --listen=127.0.0.1:8000 wsgi:app
              (gunicorn will not run on Windows; it imports fcntl)

Never run with --debug on anything reachable from a network: Werkzeug's debugger
offers an interactive Python console on any traceback, which is remote code
execution by design.

Behind nginx or Apache, set TRUSTED_PROXIES to the number of proxies in front of
this app, or every rate limit in throttle.py can be bypassed with a forged
X-Forwarded-For header.

Multiple workers share the SQLite file. That is fine at cohort scale, but the
first symptom of outgrowing it is "database is locked" under load — the fix is
PostgreSQL, and db.py is the only module that would need to change.
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
