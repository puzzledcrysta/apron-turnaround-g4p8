import sqlite3
from contextlib import contextmanager
from pathlib import Path
from app import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, source BLOB NOT NULL, book BLOB NOT NULL,
  result TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
  decision TEXT NOT NULL, note TEXT NOT NULL, changed TEXT NOT NULL,
  PRIMARY KEY(run_id,version)
);
CREATE TABLE IF NOT EXISTS settlements (
  id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL REFERENCES runs(id),
  status TEXT NOT NULL, created TEXT NOT NULL, closed TEXT
);
CREATE TABLE IF NOT EXISTS adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, settlement_id TEXT NOT NULL REFERENCES settlements(id),
  key TEXT NOT NULL, base_revision INTEGER NOT NULL, candidate_revision INTEGER NOT NULL,
  candidate_run_id TEXT NOT NULL REFERENCES runs(id), status TEXT NOT NULL,
  base_amount TEXT, new_amount TEXT NOT NULL, minute_delta TEXT NOT NULL,
  book_amount TEXT, book_delta TEXT, base_line INTEGER, new_line INTEGER,
  kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', changed TEXT NOT NULL,
  UNIQUE(settlement_id,key,candidate_revision)
);
CREATE TABLE IF NOT EXISTS settlement_versions (
  settlement_id TEXT NOT NULL REFERENCES settlements(id), version INTEGER NOT NULL,
  snapshot TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(settlement_id,version)
);
"""


@contextmanager
def connection():
    path = Path(settings.DB)
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path,timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()
