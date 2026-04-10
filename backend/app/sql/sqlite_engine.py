from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import List, Optional, Union

import aiosqlite

from .engine import ColumnInfo, QueryResult, TableInfo


class SQLiteEngine:
    def __init__(self, db_path: Union[str, Path]):
        self._db_path = str(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA query_only = ON")
        await self._db.execute("PRAGMA foreign_keys = ON")

    async def execute_query(
        self,
        sql: str,
        timeout_seconds: float = 30.0,
        max_rows: int = 500,
    ) -> QueryResult:
        if self._db is None:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        start = time.perf_counter()
        try:
            cursor = await asyncio.wait_for(
                self._db.execute(sql),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Query timed out after {timeout_seconds}s")

        rows_raw = await cursor.fetchmany(max_rows + 1)
        elapsed = (time.perf_counter() - start) * 1000

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        truncated = len(rows_raw) > max_rows
        rows = [list(r) for r in rows_raw[:max_rows]]

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_time_ms=round(elapsed, 2),
        )

    async def get_tables(self) -> List[TableInfo]:
        if self._db is None:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        rows = await cursor.fetchall()
        tables = []
        for row in rows:
            count_cursor = await self._db.execute(f'SELECT COUNT(*) FROM "{row[0]}"')
            count_row = await count_cursor.fetchone()
            tables.append(
                TableInfo(
                    name=row[0],
                    type=row[1],
                    row_count=count_row[0] if count_row else None,
                )
            )
        return tables

    async def get_columns(self, table_name: str) -> List[ColumnInfo]:
        if self._db is None:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        cursor = await self._db.execute(f'PRAGMA table_info("{table_name}")')
        rows = await cursor.fetchall()
        return [
            ColumnInfo(
                name=row[1],
                data_type=row[2] or "TEXT",
                nullable=not row[3],
                is_primary_key=bool(row[5]),
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
