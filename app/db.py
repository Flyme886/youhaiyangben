from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import settings


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = dict_factory
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                current_step TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                task_dir TEXT NOT NULL,
                input_filename TEXT NOT NULL,
                docx_count INTEGER NOT NULL DEFAULT 0,
                issue_count INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT NOT NULL DEFAULT ''
            )
            """
        )


def create_task(task_id: str, name: str, note: str, task_dir: Path, input_filename: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, name, note, status, current_step, created_at, task_dir, input_filename
            ) VALUES (?, ?, ?, 'queued', '等待执行', ?, ?, ?)
            """,
            (task_id, name, note, utc_now(), str(task_dir), input_filename),
        )


def list_tasks() -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()


def list_recoverable_tasks() -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY created_at ASC"
        ).fetchall()


def get_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    keys = list(fields)
    sql = ", ".join(f"{key} = ?" for key in keys)
    values = [fields[key] for key in keys]
    values.append(task_id)
    with connect() as conn:
        conn.execute(f"UPDATE tasks SET {sql} WHERE id = ?", values)


def parse_summary(task: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(task.get("summary_json") or "{}")
    except json.JSONDecodeError:
        return {}
