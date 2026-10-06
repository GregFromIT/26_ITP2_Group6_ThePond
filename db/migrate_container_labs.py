"""Update an existing Pond SQLite schema for shared templates/container labs.

No app startup, account seeding, network calls or data resets. Stop web/workers
before applying. --check is read-only. --apply requires a new backup path and
explicit confirmation that existing challenges are VM challenges.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable, CreateIndex
from db import db
from db.challenge_template_models import ChallengeTemplate

TABLES = ('challenges', 'vm_templates', 'challenge_flags', 'challenge_instances', 'vm_instances')
MOVED = {'challenge_id', 'vm_role', 'boot_order', 'static_ip', 'hostname_prefix',
         'network_name', 'is_user_accessible'}


class MigrationError(ValueError):
    pass


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _state(conn):
    available = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(TABLES).issubset(available):
        raise MigrationError('Required Pond tables are missing; use initialization for a fresh database.')
    markers = [('challenges', 'execution_type'), ('challenges', 'docker_challenge_key'),
               ('challenge_flags', 'challenge_id'), ('challenge_instances', 'docker_operation_key'),
               ('vm_instances', 'challenge_template_id')]
    present = [column in _columns(conn, table) for table, column in markers]
    if all(present) and 'challenge_templates' in available and 'challenge_id' not in _columns(conn, 'vm_templates'):
        for name in (*TABLES, 'challenge_templates'):
            expected = set(db.metadatas['pond'].tables[name].columns.keys())
            if _columns(conn, name) != expected:
                raise MigrationError(f'Unexpected schema for {name}; review separately.')
        return 'current'
    if any(present) or 'challenge_templates' in available:
        raise MigrationError('Partial/different migration detected; inspect it without overwriting data.')
    # Unknown columns might contain a teammate's work; never drop them silently.
    for name in TABLES:
        expected = set(db.metadatas['pond'].tables[name].columns.keys())
        if name == 'vm_templates':
            expected |= MOVED
        unknown = _columns(conn, name) - expected
        if unknown:
            raise MigrationError(f'Unrecognized columns in {name}: {sorted(unknown)}')
    if not MOVED.issubset(_columns(conn, 'vm_templates')):
        raise MigrationError('Unsupported original template schema.')
    return 'old'


def _integrity(conn):
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise MigrationError('Database integrity check failed.')
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise MigrationError('Foreign-key inconsistencies exist; resolve separately before migration.')


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def migrate(database, *, apply=False, backup=None, confirm_existing_vm=False):
    path = Path(database).resolve()
    if not path.is_file():
        raise MigrationError('Database does not exist; refusing to create an empty file.')
    mode = 'rw' if apply else 'ro'
    conn = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('BEGIN IMMEDIATE')
        else:
            conn.execute('BEGIN')
        _integrity(conn)
        state = _state(conn)
        counts = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in TABLES}
        if state == 'current' or not apply:
            conn.rollback()
            return {'state': state, 'rows': counts, 'changed': False}
        if counts['challenges'] and not confirm_existing_vm:
            raise MigrationError('Confirm existing challenges should be classified as vm before applying.')
        if backup is None:
            raise MigrationError('A new --backup path is required.')
        target = Path(backup).resolve()
        # Stop on custom views rather than risking SQLite rename rewriting them.
        views = list(conn.execute("SELECT name FROM sqlite_master WHERE type='view'"))
        if views:
            raise MigrationError('Custom views detected; review their dependencies before migration.')
        records = {name: [dict(row) for row in conn.execute(f'SELECT * FROM "{name}"')] for name in TABLES}
        templates = {row['template_id']: row for row in records['vm_templates']}
        attempts = {row['instance_id']: row for row in records['challenge_instances']}
        assignments = []
        for template_id, row in templates.items():
            assignments.append({k: row[k] for k in MOVED} | {'challenge_template_id': template_id, 'template_id': template_id})
        for row in records['challenges']:
            row.update(execution_type='vm', docker_challenge_key=None)
        for row in records['challenge_flags']:
            template = templates.get(row['template_id'])
            if template is None:
                raise MigrationError('A flag has no source template; cannot infer its challenge.')
            row['challenge_id'] = template['challenge_id']
        for row in records['vm_instances']:
            template = templates.get(row['template_id'])
            attempt = attempts.get(row['instance_id'])
            if template is None or attempt is None or template['challenge_id'] != attempt['challenge_id']:
                raise MigrationError('A VM is linked to a different challenge than its template; review separately.')
            row['challenge_template_id'] = template['template_id']
        for row in records['challenge_instances']:
            row.update(docker_challenge_key=None, docker_operation_key=None,
                       docker_session_ref=None, docker_status='not_requested', docker_error=None)
        for row in records['vm_templates']:
            for column in MOVED:
                del row[column]
        # Capture operator-created indexes/triggers rather than dropping them.
        extras = list(conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('index','trigger') "
                                   "AND sql IS NOT NULL AND tbl_name IN (?,?,?,?,?)", TABLES))
        # O_EXCL prevents overwriting the last good backup. A separate read
        # connection sees the same committed state while BEGIN IMMEDIATE blocks writers.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
                _integrity(destination)
        dialect = sqlite.dialect()
        meta = db.metadatas['pond']
        for name in TABLES:
            table = meta.tables[name]
            ddl = str(CreateTable(table).compile(dialect=dialect))
            temporary = '__pond_new_' + name
            ddl = ddl.replace('CREATE TABLE ' + name, 'CREATE TABLE ' + temporary, 1)
            conn.execute(ddl)
            _insert(conn, temporary, records[name])
            conn.execute(f'DROP TABLE "{name}"')
            conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{name}"')
        conn.execute(str(CreateTable(ChallengeTemplate.__table__).compile(dialect=dialect)))
        _insert(conn, 'challenge_templates', assignments)
        for name, sql in extras:
            conn.execute(sql)  # Unknown index/trigger incompatibility rolls back everything.
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for name in (*TABLES, 'challenge_templates'):
            for index in meta.tables[name].indexes:
                if index.name not in existing:
                    conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
        _integrity(conn)
        conn.commit()
        return {'state': 'current', 'rows': counts, 'assignments_created': len(assignments), 'changed': True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert(conn, table, rows):
    if not rows:
        return
    columns = list(rows[0])
    sql = f'INSERT INTO {_quote(table)} (' + ','.join(map(_quote, columns)) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
    conn.executemany(sql, [[row[c] for c in columns] for row in rows])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--confirm-existing-vm', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(migrate(args.database, apply=args.apply, backup=args.backup,
                                 confirm_existing_vm=args.confirm_existing_vm), sort_keys=True))
    except (MigrationError, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Migration stopped: {error}\n')


if __name__ == '__main__':
    main()
