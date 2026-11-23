"""DailyOps TaskStore — SQLite-backed task store for daily operations.

Fields: id, title, description, status (todo/in_progress/blocked/done), created_at, updated_at, due_date
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

VALID_STATUSES = frozenset({"todo", "in_progress", "blocked", "done"})
VALID_SOURCES = frozenset({"user", "agent"})
DEFAULT_TIMEZONE = "Asia/Kolkata"


def get_today_in_tz(tz_name: str = DEFAULT_TIMEZONE) -> str:
    """Return today's date formatted as YYYY-MM-DD in the given timezone."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def task_due_date_in_tz(
    due_date_str: Optional[str], tz_name: str = DEFAULT_TIMEZONE
) -> Optional[str]:
    """Extract YYYY-MM-DD in the target timezone from a due_date string."""
    if not due_date_str or not due_date_str.strip():
        return None
    raw = due_date_str.strip()
    if len(raw) >= 10 and raw[:10].count("-") == 2:
        prefix = raw[:10]
        if "Z" in raw or "+" in raw[10:] or ("-" in raw[10:] and len(raw) > 13):
            try:
                from zoneinfo import ZoneInfo

                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                tz = ZoneInfo(tz_name)
                return dt.astimezone(tz).strftime("%Y-%m-%d")
            except Exception:
                pass
        return prefix
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(raw)
        tz = ZoneInfo(tz_name)
        if dt.tzinfo is not None:
            return dt.astimezone(tz).strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def resolve_daily_ops_db_path(db_path: str = "") -> str:
    """Resolve the SQLite database path for daily_ops tasks.

    Priority:
    1. Explicit db_path argument
    2. OPENJARVIS_DAILY_OPS_DB environment variable
    3. Default ~/.openjarvis/daily_ops.db
    """
    if db_path:
        return db_path
    env_path = os.environ.get("OPENJARVIS_DAILY_OPS_DB", "").strip()
    if env_path:
        return env_path
    from openjarvis.core.paths import get_config_dir

    return str(get_config_dir() / "daily_ops.db")


@dataclass
class Task:
    """A task in the daily_ops store."""

    id: int
    title: str
    description: str = ""
    status: str = "todo"
    created_at: str = ""
    updated_at: str = ""
    due_date: Optional[str] = None
    source: str = "user"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize task to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "due_date": self.due_date,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: tuple) -> Task:
        """Create a Task from a database row tuple."""
        source = row[7] if len(row) > 7 else "user"
        return cls(
            id=row[0],
            title=row[1],
            description=row[2] or "",
            status=row[3],
            created_at=row[4],
            updated_at=row[5],
            due_date=row[6],
            source=source,
        )


class DailyOpsTaskStore:
    """SQLite-backed persistent store for daily tasks."""

    def __init__(self, db_path: str = "") -> None:
        self._db_path = resolve_daily_ops_db_path(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._conn.commit()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'blocked', 'done')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                due_date TEXT,
                source TEXT NOT NULL DEFAULT 'user' CHECK(source IN ('user', 'agent'))
            );
        """)
        # Backward compatibility / migration if table existed without source column
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in cursor.fetchall()]
        if "source" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")

    @property
    def db_path(self) -> str:
        return self._db_path

    def add_task(
        self,
        title: str,
        description: str = "",
        status: str = "todo",
        due_date: Optional[str] = None,
        source: str = "user",
    ) -> Task:
        """Add a new task to the store."""
        title = title.strip()
        if not title:
            raise ValueError("Task title cannot be empty.")
        status = status.strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        src = source.strip().lower() if source else "user"
        if src not in VALID_SOURCES:
            src = "user"

        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (title, description, status, created_at, updated_at, due_date, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description.strip(),
                status,
                now,
                now,
                due_date.strip() if due_date else None,
                src,
            ),
        )
        self._conn.commit()
        task_id = cursor.lastrowid
        task = self.get_task(task_id)  # type: ignore[arg-type]
        if task is None:
            raise RuntimeError(f"Failed to retrieve newly created task {task_id}")
        return task

    def get_task(self, task_id: int) -> Optional[Task]:
        """Fetch a task by ID."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, title, description, status, created_at, updated_at, due_date, source
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        return Task.from_row(row) if row else None

    def list_tasks(
        self,
        filter_mode: str = "all",
        status: Optional[str] = None,
        date_str: Optional[str] = None,
        timezone_name: str = DEFAULT_TIMEZONE,
        source: Optional[str] = None,
    ) -> List[Task]:
        """List tasks with filtering.

        filter_mode:
            - 'all': returns all tasks
            - 'today': returns tasks due today in Asia/Kolkata, or active tasks (not done)
            - or a specific status name ('todo', 'in_progress', 'blocked', 'done')
        status:
            optional explicit status filter override
        date_str:
            target date in YYYY-MM-DD (defaults to current date in Asia/Kolkata for 'today')
        timezone_name:
            timezone for evaluating 'today' and due dates (default: Asia/Kolkata)
        source:
            optional filter by source ('user' or 'agent')
        """
        cursor = self._conn.cursor()
        query = (
            "SELECT id, title, description, status, created_at, updated_at, due_date, source FROM tasks"
        )
        conditions: List[str] = []
        params: List[Any] = []

        target_status = status.strip().lower() if status else None
        if not target_status and filter_mode in VALID_STATUSES:
            target_status = filter_mode

        if target_status:
            conditions.append("status = ?")
            params.append(target_status)

        if source and source.strip().lower() in VALID_SOURCES:
            conditions.append("source = ?")
            params.append(source.strip().lower())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        tasks = [Task.from_row(r) for r in rows]

        if filter_mode == "today":
            today = date_str or get_today_in_tz(timezone_name)
            filtered = []
            for t in tasks:
                due_tz = task_due_date_in_tz(t.due_date, tz_name=timezone_name)
                # Matches if:
                # 1. due date in timezone matches today (whether done or not)
                # 2. active task (not done) and due date is null or on/before today
                if due_tz == today:
                    filtered.append(t)
                elif t.status != "done" and (due_tz is None or due_tz <= today):
                    filtered.append(t)
            return filtered

        return tasks

    def update_task(
        self,
        task_id: int,
        status: Optional[str] = None,
        description: Optional[str] = None,
        title: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> Optional[Task]:
        """Update an existing task's fields and updated_at timestamp."""
        task = self.get_task(task_id)
        if task is None:
            return None

        updates: List[str] = []
        params: List[Any] = []

        if status is not None:
            st = status.strip().lower()
            if st not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{st}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
                )
            updates.append("status = ?")
            params.append(st)

        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())

        if title is not None:
            t = title.strip()
            if not t:
                raise ValueError("Task title cannot be empty.")
            updates.append("title = ?")
            params.append(t)

        if due_date is not None:
            dd = due_date.strip()
            updates.append("due_date = ?")
            params.append(dd if dd else None)

        if not updates:
            return task

        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated_at = ?")
        params.append(now)

        params.append(task_id)
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get_task(task_id)

    def complete_task(self, task_id: int) -> Optional[Task]:
        """Shortcut to mark a task status as 'done'."""
        return self.update_task(task_id, status="done")

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()


__all__ = [
    "DailyOpsTaskStore",
    "Task",
    "VALID_STATUSES",
    "VALID_SOURCES",
    "resolve_daily_ops_db_path",
    "get_today_in_tz",
    "task_due_date_in_tz",
]
