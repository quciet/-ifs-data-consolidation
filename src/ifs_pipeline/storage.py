"""Filesystem, provenance, and SQLite primitives."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class PipelineError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:10]


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def q(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise PipelineError(f"Invalid SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise PipelineError(f"Database does not exist: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def snapshot(source, destination):
    """SQLite backup includes committed WAL data and never opens source writable."""
    destination = Path(destination)
    if destination.exists():
        raise PipelineError(f"Snapshot already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as src:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn, table):
    if table not in tables(conn):
        raise PipelineError(f"Missing table: {table}")
    return [dict(row) for row in conn.execute(f"PRAGMA table_info({q(table)})")]


def records(conn, table):
    columns(conn, table)
    return [dict(r) for r in conn.execute(f"SELECT * FROM {q(table)}")]


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise PipelineError(f"Path escapes repository: {relative}")
    return path


def resource_path(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


def check_idle_database(path):
    # Baseline pair must be stable while copied. No live database intake in v1.
    if Path(str(path) + "-wal").exists() or Path(str(path) + "-journal").exists():
        raise PipelineError(f"Close/checkpoint the database before intake or consolidation: {path}")
