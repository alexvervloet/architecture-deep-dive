"""
ch02/stores.py: two places to keep a conversation, and the routing that
decides whether it matters.

The chapter is not really "dict versus database". It is: *does a request need
to reach a particular machine to be answered correctly?* A conversation stored
in a process is a hidden routing requirement, and the app never states it, so
nothing enforces it and nothing warns when it stops holding.

Three combinations are compared, because two would be a strawman. Sticky
routing is a real fix that real teams ship, and it works, right up until it
does not.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from app.determinism import draw

# The in-process store. Module-level, so each OS process that imports this file
# gets its own, which is the entire mechanism this chapter is about.
_MEMORY: dict[str, list[dict]] = {}


@dataclass
class Turn:
    role: str
    content: str


class InProcessStore:
    """A dict. Fast, obvious, and invisible to every other process."""

    name = "in-process dict"

    def load(self, session_id: str) -> list[Turn]:
        return [Turn(**t) for t in _MEMORY.get(session_id, [])]

    def append(self, session_id: str, turns: list[Turn]) -> None:
        _MEMORY.setdefault(session_id, []).extend({"role": t.role, "content": t.content} for t in turns)

    def close(self) -> None:
        pass


class SqliteStore:
    """A file. Slower per turn, and reachable from anywhere.

    SQLite rather than Postgres or Redis on purpose: the point of the chapter
    is *where the state lives*, and the cheapest possible shared store makes
    that point without dragging a service into the measurement. The latency
    number it produces is therefore a floor, and the ADR says so.
    """

    name = "sqlite (shared)"

    @staticmethod
    def initialize(path: str) -> None:
        """Create the database once, from one process, before any worker starts.

        This is not tidiness. `PRAGMA journal_mode=WAL` needs exclusive access
        to the file, and it does not honour `busy_timeout` on that path: when
        several freshly started workers all try to set it on the same new
        database, one wins and the rest get "database is locked" immediately.
        It is a race, so it passed for several runs before failing.
        """
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers + one writer
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns ("
                "  session_id TEXT NOT NULL,"
                "  seq        INTEGER NOT NULL,"
                "  role       TEXT NOT NULL,"
                "  content    TEXT NOT NULL,"
                "  PRIMARY KEY (session_id, seq))"
            )
            conn.commit()
        finally:
            conn.close()

    def __init__(self, path: str) -> None:
        self.path = path
        # Workers open an already-initialized database: no DDL, no journal-mode
        # change, and a busy timeout so ordinary write contention waits instead
        # of failing.
        self.conn = sqlite3.connect(path, timeout=30.0)
        self.conn.execute("PRAGMA busy_timeout=30000")

    def load(self, session_id: str) -> list[Turn]:
        rows = self.conn.execute(
            "SELECT role, content FROM turns WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
        return [Turn(role=r, content=c) for r, c in rows]

    def append(self, session_id: str, turns: list[Turn]) -> None:
        cur = self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM turns WHERE session_id = ?", (session_id,)
        )
        next_seq = cur.fetchone()[0] + 1
        self.conn.executemany(
            "INSERT INTO turns (session_id, seq, role, content) VALUES (?, ?, ?, ?)",
            [(session_id, next_seq + i, t.role, t.content) for i, t in enumerate(turns)],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def make_store(kind: str, db_path: str) -> InProcessStore | SqliteStore:
    if kind == "memory":
        return InProcessStore()
    if kind == "sqlite":
        return SqliteStore(db_path)
    raise ValueError(f"unknown store {kind!r}")


# --- Routing ----------------------------------------------------------------


def route(policy: str, session_id: str, request_index: int, workers: int, seed: int) -> int:
    """Which worker serves this request.

    `spread`  a load balancer that knows nothing about sessions. Modelled as a
              deterministic pseudo-random pick, which is not literally what
              least-connections does, but shares the only property that
              matters here: it is not session-aware, so a follow-up lands on
              the same worker with probability 1/workers.

    `sticky`  route by session, the standard fix for in-process state. Note the
              hash is blake2b via `draw`, not Python's `hash()`, whose string
              hashing is salted per process and would give every worker a
              different opinion about where a session belongs. That bug would
              have looked exactly like the one this chapter is measuring.
    """
    if policy == "spread":
        return int(draw(seed, "dispatch", request_index) * workers)
    if policy == "sticky":
        return int(draw(0, "sticky", session_id) * workers)
    raise ValueError(f"unknown routing policy {policy!r}")


# --- The three designs under test -------------------------------------------


@dataclass(frozen=True)
class Design:
    label: str
    store: str
    routing: str
    blurb: str


DESIGNS = (
    Design(
        "memory + spread",
        "memory",
        "spread",
        "the prototype shape: a dict, behind an ordinary load balancer",
    ),
    Design(
        "memory + sticky",
        "memory",
        "sticky",
        "the standard fix: keep the dict, pin each session to one worker",
    ),
    Design(
        "sqlite + spread",
        "sqlite",
        "spread",
        "move the state out; any worker can serve any request",
    ),
)


def db_path_for(tag: str) -> str:
    directory = os.environ.get("CH02_DB_DIR", "/tmp")
    return os.path.join(directory, f"ch02-{tag}.db")
