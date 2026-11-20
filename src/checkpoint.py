"""SQLite checkpoints, session isolation, and idempotent simulated operation ledger."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from .state import AgentState, create_state, utc_now


class VersionConflict(RuntimeError):
    """Two writers attempted to update the same checkpoint version."""


class SQLiteCheckpointer:
    """A checkpoint is saved after every graph transition and before HITL pause."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock_guard = Lock()
        self._session_locks: dict[str, RLock] = {}
        self._initialize()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "thread_id TEXT PRIMARY KEY, version INTEGER NOT NULL, "
                "state_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS simulated_operations ("
                "idempotency_key TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                "name TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.commit()

    def lock_for(self, thread_id: str) -> RLock:
        with self._lock_guard:
            if thread_id not in self._session_locks:
                self._session_locks[thread_id] = RLock()
            return self._session_locks[thread_id]

    def load(self, thread_id: str) -> tuple[AgentState, int]:
        with self._connection() as db:
            record = db.execute(
                "SELECT state_json, version FROM checkpoints WHERE thread_id=?", (thread_id,)
            ).fetchone()
        if record is None:
            return create_state(thread_id), 0
        return json.loads(record["state_json"]), int(record["version"])

    def save(self, state: AgentState, expected_version: int) -> int:
        version = expected_version + 1
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if expected_version == 0:
                try:
                    db.execute(
                        "INSERT INTO checkpoints(thread_id, version, state_json, updated_at) "
                        "VALUES(?,?,?,?)",
                        (state["thread_id"], version, json.dumps(state), utc_now()),
                    )
                except sqlite3.IntegrityError as exc:
                    raise VersionConflict("Session was created concurrently") from exc
            else:
                updated = db.execute(
                    "UPDATE checkpoints SET version=?,state_json=?,updated_at=? "
                    "WHERE thread_id=? AND version=?",
                    (version, json.dumps(state), utc_now(), state["thread_id"], expected_version),
                )
                if updated.rowcount != 1:
                    raise VersionConflict("Concurrent checkpoint writer detected")
            db.commit()
        return version

    def execute_simulated_once(
        self,
        idempotency_key: str,
        thread_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Unique ledger entry prevents duplicate mutations on replay/resume."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT result_json FROM simulated_operations WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return json.loads(existing["result_json"])
            result = {
                "ok": True,
                "simulation": True,
                "tool": tool_name,
                "arguments": arguments,
                "operation_id": f"SIM-{idempotency_key[:12].upper()}",
                "message": "Simulated action recorded; no external system or funds were changed.",
            }
            db.execute(
                "INSERT INTO simulated_operations "
                "(idempotency_key,thread_id,name,result_json,created_at) VALUES(?,?,?,?,?)",
                (idempotency_key, thread_id, tool_name, json.dumps(result), utc_now()),
            )
            db.commit()
            return result

    def operation_count(self, thread_id: str | None = None) -> int:
        with self._connection() as db:
            if thread_id is None:
                return int(db.execute("SELECT COUNT(*) FROM simulated_operations").fetchone()[0])
            return int(db.execute(
                "SELECT COUNT(*) FROM simulated_operations WHERE thread_id=?", (thread_id,)
            ).fetchone()[0])
