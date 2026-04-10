from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, runtime_checkable


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    truncated: bool = False
    execution_time_ms: float = 0.0


@dataclass
class TableInfo:
    name: str
    type: str  # "table" or "view"
    row_count: Optional[int] = None


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False


@runtime_checkable
class SQLEngine(Protocol):
    async def execute_query(
        self,
        sql: str,
        timeout_seconds: float = 30.0,
        max_rows: int = 500,
    ) -> QueryResult: ...

    async def get_tables(self) -> List[TableInfo]: ...

    async def get_columns(self, table_name: str) -> List[ColumnInfo]: ...

    async def close(self) -> None: ...
