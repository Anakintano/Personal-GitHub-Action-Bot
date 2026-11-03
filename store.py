import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from analyze import Analysis


@contextmanager
def connect():
    path = Path(os.getenv("SQLITE_PATH", "explainer.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        yield db


def initialize() -> None:
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                repo TEXT NOT NULL,
                event TEXT NOT NULL,
                charged_day TEXT,
                trimmed_log TEXT,
                category TEXT,
                body TEXT,
                done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (repo, event)
            );
            CREATE INDEX IF NOT EXISTS analyses_daily ON analyses(repo, charged_day);
            CREATE TABLE IF NOT EXISTS inbox (
                event TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS latest_success (
                repo TEXT NOT NULL,
                pr INTEGER NOT NULL,
                workflow INTEGER NOT NULL,
                run INTEGER NOT NULL,
                attempt INTEGER NOT NULL,
                PRIMARY KEY (repo, pr, workflow)
            );
        """)


def enqueue(event: str, payload: str) -> None:
    with connect() as db, db:
        db.execute("INSERT OR IGNORE INTO inbox VALUES (?, ?)", (event, payload))


def pending() -> list[tuple[str, str]]:
    with connect() as db:
        return [
            tuple(row)
            for row in db.execute("SELECT event, payload FROM inbox ORDER BY rowid")
        ]


def acknowledge(event: str) -> None:
    with connect() as db, db:
        db.execute("DELETE FROM inbox WHERE event = ?", (event,))


def record_success(
    db: sqlite3.Connection, key: tuple[str, int, int], run: int, attempt: int
) -> None:
    with db:
        db.execute(
            """
            INSERT INTO latest_success VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr, workflow) DO UPDATE SET run = excluded.run, attempt = excluded.attempt
            WHERE (excluded.run, excluded.attempt) > (latest_success.run, latest_success.attempt)
        """,
            (*key, run, attempt),
        )


def has_newer_success(
    db: sqlite3.Connection, key: tuple[str, int, int], run: int, attempt: int
) -> bool:
    return (
        db.execute(
            """
        SELECT 1 FROM latest_success WHERE repo = ? AND pr = ? AND workflow = ?
        AND (run, attempt) > (?, ?)
    """,
            (*key, run, attempt),
        ).fetchone()
        is not None
    )


def lookup(db: sqlite3.Connection, repo: str, event: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM analyses WHERE repo = ? AND event = ?", (repo, event)
    ).fetchone()


def reserve(db: sqlite3.Connection, repo: str, event: str, *, failure: bool) -> bool:
    """Record this attempt and charge its UTC daily quota before analysis."""
    day = datetime.now(UTC).date().isoformat() if failure else None
    with db:
        db.execute("BEGIN IMMEDIATE")
        previous = lookup(db, repo, event)
        if previous is not None and (
            previous["body"] is not None or previous["charged_day"] == day
        ):
            return True
        if day is not None:
            count = db.execute(
                "SELECT COUNT(*) FROM analyses WHERE repo = ? AND charged_day = ?",
                (repo, day),
            ).fetchone()[0]
            if count >= int(os.getenv("DAILY_ANALYSIS_LIMIT", "20")):
                return False
        db.execute(
            """
            INSERT INTO analyses(repo, event, charged_day) VALUES (?, ?, ?)
            ON CONFLICT(repo, event) DO UPDATE SET charged_day = excluded.charged_day
        """,
            (repo, event, day),
        )
    return True


def save(db: sqlite3.Connection, repo: str, event: str, result: Analysis) -> None:
    with db:
        db.execute(
            "UPDATE analyses SET trimmed_log = ?, category = ?, body = ? WHERE repo = ? AND event = ?",
            (result.trimmed_log, result.category, result.body, repo, event),
        )


def finish(db: sqlite3.Connection, repo: str, event: str) -> None:
    with db:
        db.execute(
            "UPDATE analyses SET done = 1 WHERE repo = ? AND event = ?", (repo, event)
        )
