from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationResult:
    is_safe: bool
    reason: Optional[str] = None


class SQLSafetyValidator:
    FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
        (re.compile(r"\b(INSERT)\b", re.IGNORECASE), "INSERT statements are not allowed"),
        (re.compile(r"\b(UPDATE)\b", re.IGNORECASE), "UPDATE statements are not allowed"),
        (re.compile(r"\b(DELETE)\b", re.IGNORECASE), "DELETE statements are not allowed"),
        (re.compile(r"\b(DROP)\b", re.IGNORECASE), "DROP statements are not allowed"),
        (re.compile(r"\b(ALTER)\b", re.IGNORECASE), "ALTER statements are not allowed"),
        (re.compile(r"\b(CREATE)\b", re.IGNORECASE), "CREATE statements are not allowed"),
        (re.compile(r"\b(TRUNCATE)\b", re.IGNORECASE), "TRUNCATE statements are not allowed"),
        (re.compile(r"\b(GRANT)\b", re.IGNORECASE), "GRANT statements are not allowed"),
        (re.compile(r"\b(REVOKE)\b", re.IGNORECASE), "REVOKE statements are not allowed"),
        (re.compile(r"\b(ATTACH)\b", re.IGNORECASE), "ATTACH statements are not allowed"),
        (re.compile(r"\b(DETACH)\b", re.IGNORECASE), "DETACH statements are not allowed"),
        (re.compile(r"\b(PRAGMA)\b", re.IGNORECASE), "PRAGMA statements are not allowed"),
        (re.compile(r"\b(EXEC|EXECUTE)\b", re.IGNORECASE), "EXEC statements are not allowed"),
        (re.compile(r"\b(REPLACE)\b", re.IGNORECASE), "REPLACE statements are not allowed"),
    ]

    def validate(self, sql: str) -> ValidationResult:
        stripped = sql.strip()
        if not stripped:
            return ValidationResult(is_safe=False, reason="Empty query")

        # Remove string literals to avoid false positives on keywords inside strings
        cleaned = re.sub(r"'[^']*'", "''", stripped)
        cleaned = re.sub(r'"[^"]*"', '""', cleaned)

        # Check for multiple statements (semicolon not at end)
        without_trailing = cleaned.rstrip(";").strip()
        if ";" in without_trailing:
            return ValidationResult(
                is_safe=False,
                reason="Multiple statements are not allowed",
            )

        # Must start with SELECT or WITH (CTEs)
        if not re.match(r"^\s*(SELECT|WITH)\b", cleaned, re.IGNORECASE):
            return ValidationResult(
                is_safe=False,
                reason="Only SELECT queries are allowed",
            )

        for pattern, reason in self.FORBIDDEN_PATTERNS:
            if pattern.search(cleaned):
                return ValidationResult(is_safe=False, reason=reason)

        return ValidationResult(is_safe=True)
