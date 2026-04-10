from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class ColumnDoc:
    description: str = ""
    values: List[str] = field(default_factory=list)


@dataclass
class TableDoc:
    name: str
    description: str = ""
    columns: Dict[str, ColumnDoc] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


class TableDocManager:
    def __init__(self, tables_dir: Path):
        self._dir = tables_dir
        self._docs: Dict[str, TableDoc] = {}

    def load_all(self) -> None:
        if not self._dir.exists():
            return
        for path in sorted(self._dir.glob("*.yaml")):
            self._load_file(path)

    def _load_file(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text())
        if not raw:
            return

        columns: Dict[str, ColumnDoc] = {}
        for col_name, col_raw in (raw.get("columns") or {}).items():
            if isinstance(col_raw, dict):
                columns[col_name] = ColumnDoc(
                    description=col_raw.get("description", ""),
                    values=col_raw.get("values", []),
                )

        notes_raw = raw.get("notes") or []
        notes: List[str] = []
        for entry in notes_raw:
            if isinstance(entry, str):
                notes.append(entry)
            elif isinstance(entry, dict):
                topic = entry.get("topic", "").strip()
                guidance = entry.get("guidance", "").strip()
                if topic and guidance:
                    notes.append(f"{topic}: {guidance}")
                elif guidance:
                    notes.append(guidance)

        doc = TableDoc(
            name=raw["name"],
            description=raw.get("description", ""),
            columns=columns,
            notes=notes,
        )
        self._docs[doc.name] = doc

    def get(self, table_name: str) -> Optional[TableDoc]:
        return self._docs.get(table_name)
