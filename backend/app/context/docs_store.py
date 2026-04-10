from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class DocEntry:
    path: str          # filename, e.g. "net_revenue.md"
    title: str         # first H1 heading or filename
    content: str       # raw markdown body

    def snippet(self, max_chars: int = 240) -> str:
        # Return the first paragraph after the title, trimmed.
        body = self.content
        # Drop the leading title line if present.
        if body.startswith("# "):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.strip()
        if len(body) <= max_chars:
            return body
        return body[: max_chars - 1].rstrip() + "…"


class DocStore:
    """In-memory markdown doc store with token-overlap search.

    Mirrors the load-on-startup pattern used by TableDocManager. No DB,
    no embeddings — small enough to fit in memory and good enough for
    a directed lookup tool the LLM calls.
    """

    def __init__(self, docs_dir: Path):
        self._dir = docs_dir
        self._docs: Dict[str, DocEntry] = {}

    def load_all(self) -> None:
        if not self._dir.exists():
            return
        for path in sorted(self._dir.glob("*.md")):
            self._load_file(path)

    def _load_file(self, path: Path) -> None:
        content = path.read_text()
        title = path.stem.replace("_", " ").title()
        # First H1 wins as the title.
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        self._docs[path.name] = DocEntry(path=path.name, title=title, content=content)

    def all(self) -> List[DocEntry]:
        return list(self._docs.values())

    def search(self, query: str, limit: int = 3) -> List[DocEntry]:
        """Token-overlap ranking. Title hits weighted 3x, body hits 1x.

        Returns up to `limit` non-zero matches, highest score first.
        """
        tokens = _tokenize(query)
        if not tokens:
            return []

        scored: list[tuple[int, DocEntry]] = []
        for entry in self._docs.values():
            title_tokens = _tokenize(entry.title)
            body_tokens = _tokenize(entry.content)
            score = 0
            for tok in tokens:
                score += 3 * title_tokens.count(tok)
                score += body_tokens.count(tok)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def get(self, path: str) -> Optional[DocEntry]:
        return self._docs.get(path)
