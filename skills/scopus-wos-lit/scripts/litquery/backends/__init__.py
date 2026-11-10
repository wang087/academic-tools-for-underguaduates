# -*- coding: utf-8 -*-
"""Backend registry and the common search-result shape."""
from __future__ import annotations

from dataclasses import dataclass, field


class BackendUnavailable(RuntimeError):
    """Raised when a backend needs credentials or a browser that is not there."""


@dataclass
class SearchResult:
    engine: str
    query: str
    total: int | None
    records: list[dict] = field(default_factory=list)
    url: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "engine": self.engine,
            "query": self.query,
            "total": self.total,
            "returned": len(self.records),
            "url": self.url,
            "meta": self.meta,
            "records": self.records,
        }
