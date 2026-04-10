from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class LineageNode:
    kind: str          # "metric" | "column" | "table"
    name: str          # canonical key as stored in the YAML
    data: Dict[str, Any]


class LineageStore:
    """In-memory lookup over lineage.yaml.

    Three sections — metrics, columns, tables — searched in that order.
    All lookups are case-insensitive on the subject.
    """

    def __init__(self, lineage_file: Path):
        self._file = lineage_file
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._columns: Dict[str, Dict[str, Any]] = {}
        self._tables: Dict[str, Dict[str, Any]] = {}

    def load(self) -> None:
        if not self._file.exists():
            return
        raw = yaml.safe_load(self._file.read_text()) or {}
        self._metrics = {k.lower(): v for k, v in (raw.get("metrics") or {}).items()}
        self._columns = {k.lower(): v for k, v in (raw.get("columns") or {}).items()}
        self._tables = {k.lower(): v for k, v in (raw.get("tables") or {}).items()}

    def get(self, subject: str) -> Optional[LineageNode]:
        if not subject:
            return None
        key = subject.strip().lower()
        if key in self._metrics:
            return LineageNode(kind="metric", name=key, data=self._metrics[key])
        if key in self._columns:
            return LineageNode(kind="column", name=key, data=self._columns[key])
        if key in self._tables:
            return LineageNode(kind="table", name=key, data=self._tables[key])
        return None

    def list_subjects(self) -> Dict[str, List[str]]:
        return {
            "metrics": sorted(self._metrics.keys()),
            "columns": sorted(self._columns.keys()),
            "tables": sorted(self._tables.keys()),
        }
