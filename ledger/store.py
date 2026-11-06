"""Transactional case state and immutable evidence snapshots."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATABASE = "case.sqlite3"
MAX_SOURCE_BYTES = 8 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def case_file(case, relative):
    if Path(relative).is_absolute():
        raise ValueError("Use a path relative to the case directory")
    path = (case / relative).resolve()
    if not path.is_relative_to(case) or path.name == DATABASE:
        raise ValueError("File is outside the case or reserved for state")
    return path


def digest(path):
    with path.open("rb") as handle:
        content = handle.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds 8 MiB")
    return hashlib.sha256(content).hexdigest(), content


def init(case, request):
    if not request.strip():
        raise ValueError("Request is empty")
    case.mkdir(parents=True, exist_ok=True)
    with transaction(case, create=True) as db:
        db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS evidence (sha TEXT PRIMARY KEY, content BLOB)")
        data = {
            "schema": 1,
            "request": request,
            "sources": {},
            "checkpoint": {"stage": "intake", "next": "Уточнить решение и ограничения"},
            "draft": None,
            "decisions": [],
        }
        if not db.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (dumps(data),)).rowcount:
            raise ValueError("Case already exists")


@contextmanager
def transaction(case, *, write=False, create=False):
    path = case / DATABASE
    if path.is_symlink():
        raise ValueError("Case database must be a regular file inside the case")
    mode = "rwc" if create else "rw"
    if not create and not path.is_file():
        raise ValueError("Case is not initialized. Use init first.")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True)) as db, db:
        db.execute("BEGIN IMMEDIATE" if write or create else "BEGIN")
        yield db


@contextmanager
def load(case, *, write=False):
    with transaction(case, write=write) as db:
        row = db.execute("SELECT data FROM state WHERE id = 1").fetchone()
        if row is None:
            raise ValueError("Case is not initialized")
        state = json.loads(row[0])
        if state["schema"] != 1:
            raise ValueError("Unsupported case schema")
        yield state, db
        if write:
            db.execute("UPDATE state SET data = ? WHERE id = 1", (dumps(state),))
