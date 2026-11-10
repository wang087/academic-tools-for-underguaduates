# -*- coding: utf-8 -*-
"""Web of Science Starter API backend.

Requires a Clarivate API key:
    setx WOS_API_KEY "your-key"

Docs: https://developer.clarivate.com/apis/wos-starter
The Starter API returns metadata (title, source, authors, DOI, citations) but
not abstracts; abstracts are enriched from OpenAlex / Crossref / Scopus.
"""
from __future__ import annotations

import os

from .. import records as recmod
from ..util import HttpClient, HttpError, parse_number, qs
from . import BackendUnavailable, SearchResult

BASE = "https://api.clarivate.com/apis/wos-starter/v1/documents"

SORT_MAP = {"cited": "TC+", "date": "PY+", "relevance": ""}


def credentials() -> dict:
    return {
        "api_key": os.environ.get("WOS_API_KEY") or os.environ.get("CLARIVATE_API_KEY") or "",
    }


class WosAPI:
    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.key = api_key or credentials()["api_key"]
        self.http = http

    def available(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        if not self.key:
            raise BackendUnavailable(
                "Web of Science API key missing. Set WOS_API_KEY, or use --source wos-web with a logged-in browser."
            )
        return {"X-ApiKey": self.key, "Accept": "application/json"}

    def search(
        self,
        query: str,
        limit: int = 50,
        page: int = 1,
        sort: str = "cited",
        count_only: bool = False,
    ) -> SearchResult:
        params = {
            "q": query,
            "db": "WOS",
            "limit": "1" if count_only else str(max(1, min(50, limit))),
            "page": str(max(1, page)),
        }
        sort_value = SORT_MAP.get(sort, sort)
        if sort_value:
            params["sort"] = sort_value
        url = qs(BASE, params)
        try:
            payload = self.http.get_json(url, headers=self._headers(), use_cache=False)
        except HttpError as exc:
            if exc.status == 401:
                raise BackendUnavailable("Web of Science rejected the API key (401). Check WOS_API_KEY.") from exc
            if exc.status == 429:
                raise BackendUnavailable("Web of Science rate limit hit (429). Wait and retry.") from exc
            if exc.status in (400, 422) and params.get("sort"):
                params.pop("sort", None)
                url = qs(BASE, params)
                payload = self.http.get_json(url, headers=self._headers(), use_cache=False)
            else:
                raise
        hits = payload.get("hits") or []
        metadata = payload.get("metadata") or {}
        total = parse_number(metadata.get("total"))
        return SearchResult(
            engine="wos-api",
            query=query,
            total=total,
            records=[] if count_only else [recmod.from_wos(h) for h in hits],
            url=url,
            meta={"page": page, "limit": len(hits), "api_total": metadata.get("total")},
        )

    def count(self, query: str) -> int | None:
        return self.search(query, limit=1, count_only=True).total
