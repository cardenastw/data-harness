from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class ChartPreferences:
    default_type: str = "bar"
    color_palette: List[str] = field(
        default_factory=lambda: ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"]
    )
    guidelines: str = ""


@dataclass
class MetricDefinition:
    name: str
    definition: str
    sql_hint: str = ""


@dataclass
class ContextConfig:
    id: str
    name: str
    description: str
    system_prompt: str
    metrics: List[MetricDefinition] = field(default_factory=list)
    chart_preferences: ChartPreferences = field(default_factory=ChartPreferences)
    visible_tables: List[str] = field(default_factory=list)


class ContextManager:
    def __init__(self, contexts_dir: Path):
        self._dir = contexts_dir
        self._contexts: Dict[str, ContextConfig] = {}

    def load_all(self) -> None:
        if not self._dir.exists():
            return
        for path in sorted(self._dir.glob("*.yaml")):
            self._load_file(path)

    def _load_file(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text())
        if not raw:
            return

        metrics = [
            MetricDefinition(
                name=m["name"],
                definition=m["definition"],
                sql_hint=m.get("sql_hint", ""),
            )
            for m in raw.get("metrics", [])
        ]

        cp_raw = raw.get("chart_preferences", {})
        chart_prefs = ChartPreferences(
            default_type=cp_raw.get("default_type", "bar"),
            color_palette=cp_raw.get("color_palette", ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"]),
            guidelines=cp_raw.get("guidelines", ""),
        )

        config = ContextConfig(
            id=raw["id"],
            name=raw["name"],
            description=raw.get("description", ""),
            system_prompt=raw.get("system_prompt", ""),
            metrics=metrics,
            chart_preferences=chart_prefs,
            visible_tables=raw.get("visible_tables", []),
        )
        self._contexts[config.id] = config

    def get(self, context_id: str) -> Optional[ContextConfig]:
        return self._contexts.get(context_id)

    def list_all(self) -> List[ContextConfig]:
        return list(self._contexts.values())
