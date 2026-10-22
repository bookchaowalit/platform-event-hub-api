#!/usr/bin/env python3
"""Test double: the retired portfolio tables behind the ``ApiCellStore`` interface (goals, projects, roles-skills).

The goals, projects and roles-skills data live in their cells since 2026-10-10;
production code uses ``cell_store.open_cell_store`` only and must not import
this module. It mirrors the kit API: list filters by equality (a list means any
of), ``null=[field]``, ``id``/``legacy_id`` lists, the resource's order, and
``legacy_id`` equal to ``id``. It does not enforce authorization.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

# Safety: a test that imports this double must never reach a live cell API.
for _key in (
    "EMPIRE_GOALS_API",
    "EMPIRE_PROJECTS_API",
    "EMPIRE_ROLES_SKILLS_API",
    "EMPIRE_OPERATING_API",
    "EMPIRE_INTAKE_API",
    "EMPIRE_AUTOMATION_API",
):
    os.environ.pop(_key, None)
os.environ["EMPIRE_CLIENT_CONFIG"] = "/nonexistent/solo-empire-test/client.env"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from cell_store import CellStoreError  # noqa: E402  (the error the production client raises)

LEGACY_DDL = Path(__file__).resolve().parent / "legacy_portfolio_tables.sql"
LEGACY_DDL_2 = (
    Path(__file__).resolve().parent / "legacy_operating_intake_automation_tables.sql"
)

# plural -> (cell, legacy table, id column, order column, bool columns)
RESOURCES: dict[str, tuple[str, str, str, str, tuple[str, ...]]] = {
    "goals": ("goals", "goals", "id", "id", ()),
    "goal-tasks": ("goals", "goal_tasks", "id", "sort_order", ()),
    "goal-metrics": ("goals", "goal_metrics", "id", "id", ()),
    "goal-portfolio-mappings": (
        "goals",
        "goal_portfolio_mappings",
        "goal_id",
        "goal_id",
        (),
    ),
    "productivity-goals": ("goals", "productivity_goals", "id", "id", ("is_active",)),
    "goal-project-links": ("goals", "goal_project_links", "id", "id", ()),
    "goal-context-links": ("goals", "goal_context_links", "id", "id", ()),
    "goal-task-project-links": ("goals", "goal_task_project_links", "id", "id", ()),
    "projects": ("projects", "projects", "id", "id", ()),
    "project-repos": ("projects", "project_repos", "id", "id", ("is_primary",)),
    "portfolio-contexts": ("projects", "portfolio_contexts", "id", "id", ()),
    "project-context-links": ("projects", "project_context_links", "id", "id", ()),
    "work-items": ("projects", "portfolio_work_items", "id", "id", ()),
    "work-item-task-links": ("projects", "work_item_task_links", "id", "id", ()),
    "work-item-goal-task-links": (
        "projects",
        "work_item_goal_task_links",
        "id",
        "id",
        (),
    ),
    "personal-roles": ("roles-skills", "personal_roles", "id", "id", ()),
    "personal-role-context-links": (
        "roles-skills",
        "personal_role_context_links",
        "id",
        "id",
        (),
    ),
    "personal-role-goal-links": (
        "roles-skills",
        "personal_role_goal_links",
        "id",
        "id",
        (),
    ),
    "personal-role-project-links": (
        "roles-skills",
        "personal_role_project_links",
        "id",
        "id",
        (),
    ),
    "personal-role-task-links": (
        "roles-skills",
        "personal_role_task_links",
        "id",
        "id",
        (),
    ),
    "role-goal-links": ("roles-skills", "role_goal_links", "id", "id", ()),
    "skill-categories": ("roles-skills", "skill_categories", "id", "sort_order", ()),
    "skills": ("roles-skills", "skills", "id", "id", ("revenue_relevant",)),
    "progress-log": ("roles-skills", "progress_log", "id", "id", ()),
    "role-path-steps": ("roles-skills", "role_path_steps", "id", "id", ()),
    "role-project-allocations": (
        "roles-skills",
        "role_project_allocations",
        "id",
        "id",
        (),
    ),
    "role-skill-links": ("roles-skills", "role_skill_links", "id", "id", ()),
    "cycles": ("operating", "operating_cycles", "id", "start_date", ()),
    "cycle-tasks": (
        "operating",
        "operating_cycle_tasks",
        "id",
        "id",
        ("evidence_required",),
    ),
    "cycle-goal-links": ("operating", "operating_cycle_goal_links", "id", "id", ()),
    "cycle-reviews": ("operating", "operating_cycle_reviews", "id", "id", ()),
    "kpis": ("operating", "operating_kpi_definitions", "id", "id", ()),
    "kpi-measurements": ("operating", "operating_kpi_measurements", "id", "id", ()),
    "sources": ("intake", "source_registry", "id", "id", ()),
    "intakes": ("intake", "intake_queue", "id", "id", ()),
    "intake-events": ("intake", "intake_events", "id", "id", ()),
    "cron-jobs": ("automation", "cron_jobs", "id", "id", ("is_active",)),
    "automation-rules": ("automation", "automation_rules", "id", "id", ("is_active",)),
    "workflow-pipelines": (
        "automation",
        "workflow_pipelines",
        "id",
        "id",
        ("is_active",),
    ),
    "automation-log": ("automation", "automation_log", "id", "id", ("success",)),
}


def apply_legacy_portfolio_tables(conn: sqlite3.Connection) -> None:
    """Create the legacy tables that are missing; a test's own minimal table keeps its columns.

    An index on a column such a minimal table lacks is skipped; any other DDL error is raised.
    """
    script = (
        LEGACY_DDL.read_text(encoding="utf-8")
        + "\n"
        + LEGACY_DDL_2.read_text(encoding="utf-8")
    )
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if not sqlite3.complete_statement(statement):
            continue
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if not (
                statement.lstrip().upper().startswith("CREATE")
                and "INDEX" in statement.upper()
                and "no such column" in str(exc)
            ):
                raise
        statement = ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class SqliteCellStore:
    """One cell over the legacy tables of ``conn`` (the same interface as ``ApiCellStore``)."""

    def __init__(self, conn: sqlite3.Connection, cell: str) -> None:
        self.cell = cell
        self.conn = conn
        self._versions: dict[
            tuple[str, int], int
        ] = {}  # the cells' row version, which the old tables lack
        self._keys: dict[tuple[str, str], int] = {}  # idempotency keys -> created id
        self.conn.row_factory = sqlite3.Row
        apply_legacy_portfolio_tables(self.conn)  # schema.sql no longer has them

    def _resource(self, plural: str) -> tuple[str, str, str, tuple[str, ...]]:
        if plural not in RESOURCES or RESOURCES[plural][0] != self.cell:
            raise CellStoreError(404, f"no resource {plural} in {self.cell}", self.cell)
        _, table, key, order, bools = RESOURCES[plural]
        return table, key, order, bools

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _out(
        self, row: sqlite3.Row | None, key: str, bools: tuple[str, ...], table: str = ""
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data.setdefault("id", data[key])
        data["legacy_id"] = data[key]
        data["version"] = data.get("version") or self._versions.get(
            (table, int(data[key])), 1
        )
        for name in bools:
            if name in data and data[name] is not None:
                data[name] = bool(data[name])
        return data

    def list(
        self,
        plural: str,
        *,
        order: str = "asc",
        limit: int | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        table, key, order_column, bools = self._resource(plural)
        columns = self._columns(table)
        where, params = ["1=1"], []
        nulls = filters.pop("null", None) or []
        for name, value in filters.items():
            if value is None:
                continue
            column = key if name in ("id", "legacy_id") else name
            if column not in columns:
                raise CellStoreError(422, f"unknown_filter:{name}", self.cell)
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            values = [int(v) if isinstance(v, bool) else v for v in values]
            where.append(f"{column} IN ({','.join('?' * len(values))})")
            params += values
        where.extend(
            f"{name} IS NULL" for name in ([nulls] if isinstance(nulls, str) else nulls)
        )
        direction = "DESC" if order == "desc" else "ASC"
        sort = (
            f"{order_column} {direction}, {key} {direction}"
            if order_column != key
            else f"{key} {direction}"
        )
        sql = f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY {sort}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._out(r, key, bools, table) for r in self.conn.execute(sql, params)]

    def get(self, plural: str, record_id: int) -> dict[str, Any] | None:
        table, key, _, bools = self._resource(plural)
        return self._out(
            self.conn.execute(
                f"SELECT * FROM {table} WHERE {key} = ?", (int(record_id),)
            ).fetchone(),
            key,
            bools,
            table,
        )

    def create(
        self, plural: str, fields: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        table, key, _, _bools = self._resource(plural)
        if (
            idempotency_key and (plural, idempotency_key) in self._keys
        ):  # a replay returns the first result
            return self.get(plural, self._keys[(plural, idempotency_key)])
        columns = self._columns(table)
        data = {
            k: (int(v) if isinstance(v, bool) else v)
            for k, v in fields.items()
            if k in columns and v is not None
        }
        if "legacy_id" in fields and fields["legacy_id"] is not None:
            data[key] = fields["legacy_id"]
        try:
            cursor = self.conn.execute(
                f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                list(data.values()),
            )
        except sqlite3.IntegrityError as exc:
            raise CellStoreError(
                409 if "UNIQUE" in str(exc) else 422, "conflict", self.cell
            ) from None
        self.conn.commit()
        new_id = data.get(key, cursor.lastrowid)
        if idempotency_key:
            self._keys[(plural, idempotency_key)] = int(new_id)
        return self.get(plural, new_id)

    def update(
        self,
        plural: str,
        record_id: int,
        fields: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        table, key, _, _ = self._resource(plural)
        before = self.get(plural, record_id)
        if before is None:
            raise CellStoreError(404, "not_found", self.cell)
        if expected_version is not None and before["version"] != expected_version:
            raise CellStoreError(409, "version_conflict", self.cell)
        columns = self._columns(table)
        data = {
            k: (int(v) if isinstance(v, bool) else v)
            for k, v in fields.items()
            if k in columns and k != key
        }
        if data:
            stamp = (
                ", updated_at = ?"
                if "updated_at" in columns and "updated_at" not in data
                else ""
            )
            self.conn.execute(
                f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in data)}{stamp} WHERE {key} = ?",
                [*data.values(), *([_now()] if stamp else []), int(record_id)],
            )
            self.conn.commit()
            self._versions[(table, int(record_id))] = before["version"] + 1
        return self.get(plural, record_id)

    def delete(self, plural: str, record_id: int) -> bool:
        table, key, _, _ = self._resource(plural)
        cursor = self.conn.execute(
            f"DELETE FROM {table} WHERE {key} = ?", (int(record_id),)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def stats(self, plural: str, group_by: str, **filters: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.list(plural, **filters):
            counts[str(row.get(group_by))] = counts.get(str(row.get(group_by)), 0) + 1
        return counts


def sqlite_cell_opener(conn: sqlite3.Connection):
    """A drop-in for ``open_cell_store`` that returns the double over ``conn`` (one per cell)."""
    stores: dict[str, SqliteCellStore] = {}

    def opener(cell: str, *_args, **_kwargs) -> SqliteCellStore:
        if cell not in stores:
            stores[cell] = SqliteCellStore(conn, cell)
        return stores[cell]

    return opener
