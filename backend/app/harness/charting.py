from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Any, Optional, Sequence

DEFAULT_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"]
TIME_LABEL_KEYWORDS = ("date", "month", "week", "year", "day", "time", "period")


@dataclass(frozen=True)
class ChartInference:
    label_col: str
    value_col: str
    label_idx: int
    value_idx: int
    is_time: bool


@dataclass(frozen=True)
class ChartValidationResult:
    inference: Optional[ChartInference] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ChartBuildResult:
    chart: Optional[dict[str, Any]] = None
    error: Optional[str] = None


def validate_chart_data(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> ChartValidationResult:
    column_names = [str(col) for col in columns]
    row_values = [list(row) for row in rows]

    if len(column_names) < 2:
        return ChartValidationResult(
            error=(
                "chart_query must return at least two columns: one label/date "
                "column and one numeric value column."
            )
        )

    if len(row_values) < 2:
        return ChartValidationResult(
            error=(
                f"chart_query returned {len(row_values)} row(s); group it by date, "
                "category, or another dimension so it returns at least 2 rows."
            )
        )

    complete_rows = [row for row in row_values if len(row) >= len(column_names)]
    if len(complete_rows) < 2:
        return ChartValidationResult(
            error="chart_query must return at least 2 complete chartable rows."
        )

    numeric_indexes = [
        idx
        for idx in range(len(column_names))
        if _is_numeric_column(complete_rows, idx)
    ]

    if not numeric_indexes:
        return ChartValidationResult(
            error="chart_query must include a numeric value column for the chart."
        )

    label_idx = next(
        (idx for idx in range(len(column_names)) if idx not in numeric_indexes),
        0,
    )
    value_idx = next(
        (idx for idx in numeric_indexes if idx != label_idx and idx > label_idx),
        None,
    )
    if value_idx is None:
        value_idx = next((idx for idx in numeric_indexes if idx != label_idx), None)

    if value_idx is None:
        return ChartValidationResult(
            error=(
                "chart_query must include a numeric value column separate from "
                "the label/date column."
            )
        )

    label_col = column_names[label_idx]
    value_col = column_names[value_idx]
    label_lower = label_col.lower()

    return ChartValidationResult(
        inference=ChartInference(
            label_col=label_col,
            value_col=value_col,
            label_idx=label_idx,
            value_idx=value_idx,
            is_time=any(keyword in label_lower for keyword in TIME_LABEL_KEYWORDS),
        )
    )


def build_auto_chart(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    chart_preferences: Any = None,
) -> ChartBuildResult:
    row_values = [list(row) for row in rows]
    validation = validate_chart_data(columns, row_values)
    if validation.error:
        return ChartBuildResult(error=validation.error)

    inference = validation.inference
    if inference is None:
        return ChartBuildResult(error="chart_query could not be interpreted.")

    data = []
    for row in row_values:
        if len(row) <= max(inference.label_idx, inference.value_idx):
            continue

        label = row[inference.label_idx]
        value = _coerce_number(row[inference.value_idx])
        if label is None or value is None:
            continue

        data.append({inference.label_col: str(label), inference.value_col: value})

    if len(data) < 2:
        return ChartBuildResult(
            error="chart_query did not produce at least 2 complete chartable rows."
        )

    chart_type = "line" if inference.is_time else "bar"

    return ChartBuildResult(
        chart={
            "chartType": chart_type,
            "title": f"{inference.value_col} by {inference.label_col}"
            .replace("_", " ")
            .title(),
            "data": data,
            "xAxis": inference.label_col,
            "yAxis": inference.value_col,
            "xLabel": inference.label_col.replace("_", " ").title(),
            "yLabel": inference.value_col.replace("_", " ").title(),
            "colors": _color_palette(chart_preferences),
        }
    )


def _is_numeric_column(rows: Sequence[Sequence[Any]], idx: int) -> bool:
    values = [row[idx] for row in rows if len(row) > idx and row[idx] is not None]
    return bool(values) and all(_coerce_number(value) is not None for value in values)


def _coerce_number(value: Any) -> Optional[int | float]:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        return int(number) if isinstance(value, int) else number

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        try:
            number = float(text)
        except ValueError:
            return None

        if not math.isfinite(number):
            return None
        if number.is_integer() and "." not in text and "e" not in text.lower():
            return int(number)
        return number

    return None


def _color_palette(chart_preferences: Any) -> list[str]:
    palette = None
    if isinstance(chart_preferences, dict):
        palette = chart_preferences.get("color_palette") or chart_preferences.get("colors")
    elif chart_preferences is not None:
        palette = getattr(chart_preferences, "color_palette", None)

    if isinstance(palette, list) and palette:
        return palette
    return DEFAULT_COLORS
