#!/usr/bin/env python3
"""Test double: the retired SQLite task backend, kept only so behaviour tests
can run the task-store contract against a temporary SQLite database.

The SQLite task backend was retired on 2026-10-09; production code uses
``ApiTaskStore`` only and must not import this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Safety: a test that imports this double must never reach the live Tasks API. Point the client
# switch at a file that does not exist, so any unpatched open_task_store() raises instead of calling out.
os.environ.pop("SOLO_EMPIRE_TASKS_BACKEND", None)
os.environ.pop("EMPIRE_TASKS_API", None)
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))

from recurring_step import next_due_dates  # noqa: E402
from task_store import (  # noqa: E402
    LINK_ATTRIBUTES,
    LINK_TABLES,
    _check_fields,
    _check_filters,
    _date,
    _link_order,
    _record,
    link_relation,
    tags_list,
)


class SqliteTaskStore:
    """The legacy backend: the same SQL the writers used, in one place."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _rows(self, where: str, params: tuple) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            f"SELECT * FROM tasks WHERE {where} ORDER BY id", params
        )
        names = [d[0] for d in cursor.description]
        records = []
        for row in cursor.fetchall():
            values = dict(zip(names, row, strict=False))
            records.append(
                _record(
                    {
                        **values,
                        "number": values["id"],
                        "parent_number": values.get("parent_task_id"),
                        "parent_recurring_number": values.get("parent_recurring_id"),
                    }
                )
            )
        return records

    def get(self, number: int) -> dict[str, Any] | None:
        rows = self._rows("id = ?", (number,))
        return rows[0] if rows else None

    def find_external(self, source: str, external_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "external_source = ? AND external_id = ?", (source, str(external_id))
        )
        return rows[0] if rows else None

    @staticmethod
    def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        _check_filters(filters)
        f = filters
        clauses, params = ["1 = 1"], []

        def add(clause: str, *values: Any) -> None:
            clauses.append(clause)
            params.extend(values)

        if not f.get("include_archived"):
            add("archived_at IS NULL")
        if f.get("status"):
            add(f"status IN ({', '.join('?' * len(f['status']))})", *f["status"])
        if f.get("external_source"):
            add("external_source = ?", f["external_source"])
        if f.get("no_external_source"):
            add("external_source IS NULL")
        if f.get("project") is not None:
            add("project = ?", f["project"])
        if f.get("category") is not None:
            add("category = ?", f["category"])
        if f.get("priority_max") is not None:
            add("priority <= ?", f["priority_max"])
        if f.get("due_from"):
            add("due_date IS NOT NULL AND substr(due_date, 1, 10) >= ?", f["due_from"])
        if f.get("due_before"):
            add("due_date IS NOT NULL AND substr(due_date, 1, 10) < ?", f["due_before"])
        if f.get("completed_between"):
            add(
                "completed_at IS NOT NULL AND date(completed_at, 'localtime') BETWEEN ? AND ?",
                *f["completed_between"],
            )
        if f.get("created_between"):
            add("date(created_at, 'localtime') BETWEEN ? AND ?", *f["created_between"])
        if f.get("reminder_due_before"):
            add(
                "reminder_at IS NOT NULL AND datetime(reminder_at) <= datetime(?) AND reminder_sent = 0",
                f["reminder_due_before"],
            )
        if f.get("missing_calendar_event"):
            add("calendar_event_id IS NULL")
        if f.get("numbers") is not None:
            add(
                f"id IN ({', '.join('?' * len(f['numbers'])) or 'NULL'})", *f["numbers"]
            )
        if f.get("parent") is not None:
            add("parent_task_id = ?", f["parent"])
        if f.get("q"):
            needle = (
                "%"
                + f["q"]
                .lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "%"
            )
            add(
                "(lower(title) LIKE ? ESCAPE '\\' OR lower(coalesce(description, '')) LIKE ? ESCAPE '\\')",
                needle,
                needle,
            )
        return " AND ".join(clauses), params

    def list(self, **filters) -> list[dict[str, Any]]:
        where, params = self._where(filters)
        return self._rows(where, tuple(params))

    def stats(self, *, group_by=None, **filters) -> dict[str, Any]:
        where, params = self._where(filters)
        total = self.conn.execute(
            f"SELECT count(*) FROM tasks WHERE {where}", params
        ).fetchone()[0]
        groups: dict[Any, int] = {}
        if group_by:
            if group_by not in ("status", "priority", "project", "category"):
                raise ValueError(f"cannot group by {group_by!r}")
            for key, count in self.conn.execute(
                f"SELECT {group_by}, count(*) FROM tasks WHERE {where} GROUP BY {group_by}",
                params,
            ):
                groups[
                    str(key) if key is not None and group_by == "priority" else key
                ] = count
        return {"total": total, "groups": groups}

    def links(
        self, *, task=None, target_type=None, target_id=None
    ) -> list[dict[str, Any]]:
        records = []
        for kind, (table, column, _) in LINK_TABLES.items():
            if target_type and kind != target_type:
                continue
            clauses, params = ["1 = 1"], []
            if task is not None:
                clauses.append("task_id = ?")
                params.append(task)
            if target_id is not None:
                clauses.append(f"{column} = ?")
                params.append(target_id)
            cursor = self.conn.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY task_id, id",
                params,
            )
            names = [d[0] for d in cursor.description]
            for row in cursor.fetchall():
                values = dict(zip(names, row, strict=False))
                attributes = {
                    k: values[k] for k in LINK_ATTRIBUTES[kind] if k in values
                }
                records.append(
                    {
                        "task": values["task_id"],
                        "target_type": kind,
                        "target_id": values[column],
                        "relation": link_relation(kind, attributes),
                        "attributes": attributes,
                    }
                )
        return sorted(records, key=_link_order)

    def link(
        self, task, target_type, target_id, attributes=None, *, update=False
    ) -> str:
        table, column, _ = LINK_TABLES[target_type]
        attributes = dict(attributes or {})
        unknown = set(attributes) - LINK_ATTRIBUTES[target_type]
        if unknown:
            raise ValueError(
                f"unknown {target_type} link attributes: {sorted(unknown)}"
            )
        columns = ["task_id", column, *attributes]
        cursor = self.conn.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            [task, target_id, *attributes.values()],
        )
        if cursor.rowcount:
            return "created"
        if not update or not attributes:
            return "unchanged"
        # Skill links are unique per source registry; never touch another registry's row.
        scope, scope_params = "", []
        if target_type == "skill" and attributes.get("source_registry"):
            scope, scope_params = (
                " AND source_registry = ?",
                [attributes["source_registry"]],
            )
        current = self.conn.execute(
            f"SELECT {', '.join(attributes)} FROM {table} WHERE task_id = ? AND {column} = ?{scope}",
            (task, target_id, *scope_params),
        ).fetchone()
        if current is not None and list(current) == list(attributes.values()):
            return "unchanged"
        stamp = ", updated_at = CURRENT_TIMESTAMP" if target_type != "project" else ""
        self.conn.execute(
            f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in attributes)}{stamp}"
            f" WHERE task_id = ? AND {column} = ?{scope}",
            [*attributes.values(), task, target_id, *scope_params],
        )
        return "updated"

    def unlink(self, task, target_type, target_id, *, source_registry=None) -> bool:
        """Withdraw a link (generated suggestions only; tasks themselves are never deleted)."""
        table, column, _ = LINK_TABLES[target_type]
        scope = (
            " AND source_registry = ?"
            if target_type == "skill" and source_registry
            else ""
        )
        params = (task, target_id, source_registry) if scope else (task, target_id)
        return (
            self.conn.execute(
                f"DELETE FROM {table} WHERE task_id = ? AND {column} = ?{scope}", params
            ).rowcount
            > 0
        )

    def assignments(
        self, *, task=None, assignee_type=None, assignee_id=None, assignment_type=None
    ):
        clauses, params = ["1 = 1"], []
        for column, value in (
            ("task_id", task),
            ("assignee_type", assignee_type),
            ("assignee_id", assignee_id),
            ("assignment_type", assignment_type),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        rows = self.conn.execute(
            "SELECT task_id, assignee_type, assignee_id, assignee_name, assignment_type FROM task_assignments"
            f" WHERE {' AND '.join(clauses)} ORDER BY task_id, id",
            params,
        ).fetchall()
        return [
            {
                "task": r[0],
                "assignee_type": r[1] or "user",
                "assignee_id": r[2],
                "assignee_name": r[3],
                "assignment_type": r[4] or "assigned",
            }
            for r in rows
        ]

    def assign(
        self,
        task,
        assignee_type,
        assignee_id,
        assignee_name=None,
        assignment_type="assigned",
    ) -> str:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO task_assignments (task_id, assignee_type, assignee_id, assignee_name, assignment_type)"
            " VALUES (?, ?, ?, ?, ?)",
            (task, assignee_type, assignee_id, assignee_name, assignment_type),
        )
        return "created" if cursor.rowcount else "unchanged"

    def role_suggestions(
        self,
        *,
        task=None,
        role_id=None,
        source_registry=None,
        review_status=None,
        confidence=None,
    ):
        clauses, params = ["1 = 1"], []
        for column, value in (
            ("task_id", task),
            ("role_id", role_id),
            ("source_registry", source_registry),
            ("review_status", review_status),
            ("confidence", confidence),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        rows = self.conn.execute(
            "SELECT task_id, role_id, confidence, match_reason, review_status, source_registry, updated_at"
            f" FROM task_role_suggestions WHERE {' AND '.join(clauses)} ORDER BY task_id, role_id, id",
            params,
        ).fetchall()
        return [
            {
                "task": r[0],
                "role_id": r[1],
                "confidence": r[2],
                "match_reason": r[3],
                "review_status": r[4],
                "source_registry": r[5],
                "updated_at": r[6],
            }
            for r in rows
        ]

    def suggest_role(
        self,
        task,
        role_id,
        source_registry,
        confidence,
        match_reason,
        review_status=None,
    ) -> str:
        current = self.conn.execute(
            "SELECT confidence, match_reason, review_status FROM task_role_suggestions"
            " WHERE task_id = ? AND role_id = ? AND source_registry = ?",
            (task, role_id, source_registry),
        ).fetchone()
        wanted = (
            confidence,
            match_reason,
            review_status or (current[2] if current else "suggested"),
        )
        if current is None:
            self.conn.execute(
                "INSERT INTO task_role_suggestions (task_id, role_id, confidence, match_reason, review_status,"
                " source_registry) VALUES (?, ?, ?, ?, ?, ?)",
                (task, role_id, *wanted, source_registry),
            )
            return "created"
        if tuple(current) == wanted:
            return "unchanged"
        self.conn.execute(
            "UPDATE task_role_suggestions SET confidence = ?, match_reason = ?, review_status = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE task_id = ? AND role_id = ? AND source_registry = ?",
            (*wanted, task, role_id, source_registry),
        )
        return "updated"

    def review_role(
        self, task, role_id, source_registry, review_status, expected_status=None
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE task_role_suggestions SET review_status = ?, updated_at = CURRENT_TIMESTAMP"
            " WHERE task_id = ? AND role_id = ? AND source_registry = ? AND review_status <> ?"
            " AND (? IS NULL OR review_status = ?)",
            (
                review_status,
                task,
                role_id,
                source_registry,
                review_status,
                expected_status,
                expected_status,
            ),
        )
        return cursor.rowcount > 0

    def withdraw_role(self, task, role_id, source_registry, only_status=None) -> bool:
        return (
            self.conn.execute(
                "DELETE FROM task_role_suggestions WHERE task_id = ? AND role_id = ? AND source_registry = ?"
                " AND (? IS NULL OR review_status = ?)",
                (task, role_id, source_registry, only_status, only_status),
            ).rowcount
            > 0
        )

    def dependencies(self, *, task=None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT task_id, depends_on_task_id, coalesce(dependency_type, 'finish_to_start') FROM task_dependencies"
            " WHERE (? IS NULL OR task_id = ?) ORDER BY task_id, depends_on_task_id",
            (task, task),
        ).fetchall()
        return [
            {"task": r[0], "depends_on": r[1], "dependency_type": r[2]} for r in rows
        ]

    def add_dependency(
        self, task, depends_on, dependency_type="finish_to_start"
    ) -> str:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id, dependency_type) VALUES (?, ?, ?)",
            (task, depends_on, dependency_type),
        )
        return "created" if cursor.rowcount else "unchanged"

    def task_types(self, *, include_inactive=False) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM task_types WHERE (? OR is_active = 1) ORDER BY sort_order, name",
            (1 if include_inactive else 0,),
        )
        names = [d[0] for d in cursor.description]
        types = []
        for row in cursor.fetchall():
            values = dict(zip(names, row, strict=False))
            types.append(
                {
                    "id": values["id"],
                    "name": values["name"],
                    "description": values["description"],
                    "icon": values["icon"],
                    "color": values["color"],
                    "default_status": values["default_status"] or "todo",
                    "allowed_statuses": tags_list(values["allowed_statuses"]),
                    "is_active": bool(
                        values["is_active"] if values["is_active"] is not None else 1
                    ),
                    "sort_order": values["sort_order"] or 0,
                }
            )
        return types

    def _values(self, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            k: (json.dumps(v) if k == "tags" and isinstance(v, list) else v)
            for k, v in fields.items()
        }

    def create(self, fields, *, legacy=None) -> int:
        """``legacy`` may carry SQLite-only columns, including explicit created_at/updated_at.

        Without them the column defaults (CURRENT_TIMESTAMP) apply, as for the
        writers' own INSERTs that omitted the timestamps.
        """
        _check_fields(fields)
        values = {"status": "todo", **self._values(fields), **(legacy or {})}
        cursor = self.conn.execute(
            f"INSERT INTO tasks ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )
        return int(cursor.lastrowid)

    def upsert_external(
        self, source, external_id, fields, *, legacy=None
    ) -> tuple[str, int]:
        _check_fields(fields)
        existing = self.find_external(source, external_id)
        if existing is None:
            number = self.create(
                fields,
                legacy={
                    "external_source": source,
                    "external_id": str(external_id),
                    **(legacy or {}),
                },
            )
            return "created", number
        changes = {
            k: v
            for k, v in fields.items()
            if (
                tags_list(v) != existing["tags"]
                if k == "tags"
                else (
                    _date(v) != existing[k] if k == "due_date" else existing.get(k) != v
                )
            )
        }
        if not changes:
            return "unchanged", existing["id"]
        self.update(existing["id"], changes, legacy=legacy)
        return "updated", existing["id"]

    def update(self, number, fields, *, legacy=None) -> None:
        _check_fields(fields)
        values = {**self._values(fields), **(legacy or {})}
        if values.get("status") == "done":
            self.complete(
                number,
                legacy={k: v for k, v in values.items() if k != "status"} or None,
            )
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        stamp = "" if "updated_at" in values else ", updated_at = datetime('now')"
        self.conn.execute(
            f"UPDATE tasks SET {assignments}{stamp} WHERE id = ?",
            [*values.values(), number],
        )

    def complete(self, number, *, legacy=None) -> None:
        extra = dict(legacy or {})
        completed = (
            "?"
            if "completed_at" in extra
            else "COALESCE(completed_at, datetime('now'))"
        )
        params = [extra.pop("completed_at")] if "completed_at" in extra else []
        stamp = "" if "updated_at" in extra else ", updated_at = datetime('now')"
        assignments = "".join(f", {column} = ?" for column in extra)
        self.conn.execute(
            f"UPDATE tasks SET status = 'done', completed_at = {completed}{stamp}{assignments}"
            " WHERE id = ?",
            [*params, *extra.values(), number],
        )

    def archive(self, number, reason) -> None:
        self.conn.execute(
            "UPDATE tasks SET archived_at = COALESCE(archived_at, datetime('now')), archive_reason = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (reason, number),
        )

    def unarchive(self, number) -> None:
        self.conn.execute(
            "UPDATE tasks SET archived_at = NULL, archive_reason = NULL, updated_at = datetime('now')"
            " WHERE id = ?",
            (number,),
        )

    def record_reminder_delivered(self, number, reminder_at, channel) -> bool:
        cursor = self.conn.execute(
            "UPDATE tasks SET reminder_sent = 1 WHERE id = ? AND reminder_sent = 0 AND reminder_at = ?",
            (number, reminder_at),
        )
        return cursor.rowcount > 0

    def generate_occurrences(self, number, until) -> list[int]:
        parent = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (number,)
        ).fetchone()
        if parent is None:
            return []
        names = [
            d[0] for d in self.conn.execute("SELECT * FROM tasks LIMIT 0").description
        ]
        parent = dict(zip(names, parent, strict=False))
        existing = self.conn.execute(
            "SELECT recurrence_occurrence, due_date, completed_at FROM tasks WHERE parent_recurring_id = ?"
            " ORDER BY recurrence_occurrence",
            (number,),
        ).fetchall()
        created = []
        for occurrence, due in next_due_dates(
            parent, existing, date.fromisoformat(until)
        ):
            title = (
                parent["title"]
                if parent["title"].startswith(f"[R{occurrence}]")
                else f"[R{occurrence}] {parent['title']}"
            )
            # Same columns as the SQLite-era recurring_gen.py instances.
            cursor = self.conn.execute(
                "INSERT INTO tasks (title, description, status, priority, project, tags, category, due_date,"
                " recurrence_rule, is_recurring_instance, parent_recurring_id, recurrence_occurrence,"
                " estimated_minutes, created_at, updated_at)"
                " VALUES (?, ?, 'todo', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (
                    title,
                    parent["description"] or "",
                    parent["priority"] if parent["priority"] is not None else 2,
                    parent["project"],
                    parent["tags"],
                    parent["category"],
                    due.isoformat(),
                    parent["recurrence_rule"],
                    number,
                    occurrence,
                    parent["estimated_minutes"],
                ),
            )
            created.append(int(cursor.lastrowid))
        return created


_DEFAULT_CONNECTION: dict[str, Any] = {"conn": None}


def use_connection(conn: Any) -> Any:
    """Set the connection the double uses when production code opens the store with ``None``; returns the old one."""
    previous = _DEFAULT_CONNECTION["conn"]
    _DEFAULT_CONNECTION["conn"] = conn
    return previous


def sqlite_opener(conn: Any, automation: str, **_kwargs: Any) -> SqliteTaskStore:
    """Stand-in for ``open_task_store``: the double over the caller's connection.

    Production code opens the store with ``None`` (tasks live in the API); the double then uses the
    connection set by :func:`use_connection`, else the test database named by SOLO_EMPIRE_DB_PATH.
    """
    if conn is None:
        conn = _DEFAULT_CONNECTION["conn"]
    if conn is None and os.environ.get("SOLO_EMPIRE_DB_PATH"):
        # Autocommit: each write is visible at once and holds no lock, as an API call would.
        conn = sqlite3.connect(os.environ["SOLO_EMPIRE_DB_PATH"], isolation_level=None)
        conn.row_factory = sqlite3.Row
    return SqliteTaskStore(conn)


def uses_sqlite_store(*modules: Any):
    """Decorator: run a test with ``open_task_store`` of each module replaced by :func:`sqlite_opener`."""
    from functools import wraps
    from unittest.mock import patch

    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            patchers = [
                patch.object(module, "open_task_store", sqlite_opener)
                for module in modules
            ]
            for patcher in patchers:
                patcher.start()
            try:
                return fn(*args, **kwargs)
            finally:
                for patcher in reversed(patchers):
                    patcher.stop()

        return wrapper

    return decorate


_DATABASE_DIR = Path(__file__).resolve().parents[2] / "database"
_LEGACY_TASK_TABLES = Path(__file__).resolve().parent / "legacy_task_tables.sql"


def fixture_db(path: Any = ":memory:", *, tasks: bool = True) -> sqlite3.Connection:
    """Open a SQLite fixture: schema.sql, then (by default) the retired task tables."""
    conn = sqlite3.connect(str(path))
    conn.executescript((_DATABASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    if tasks:
        conn.executescript(_LEGACY_TASK_TABLES.read_text(encoding="utf-8"))
    return conn


def apply_legacy_task_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_LEGACY_TASK_TABLES.read_text(encoding="utf-8"))


__all__ = [
    "SqliteTaskStore",
    "apply_legacy_task_tables",
    "fixture_db",
    "sqlite_opener",
    "uses_sqlite_store",
]
