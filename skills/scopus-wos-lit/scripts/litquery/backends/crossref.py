# -*- coding: utf-8 -*-
"""Crossref backend: metadata, abstracts when the publisher deposited them, and
DOI validation. No credential is required; a polite-pool mailto is recommended.
"""
from __future__ import annotations

import re
import time

from .. import records as recmod
from ..util import HttpClient, HttpError, qs
from . import SearchResult

BASE = "https://api.crossref.org/works"


class Crossref:
    def __init__(self, http: HttpClient, mailto: str | None = None):
        self.http = http
        self.mailto = mailto

    def _url(self, base: str, params: dict) -> str:
        if self.mailto:
            params = dict(params)
            params["mailto"] = self.mailto
        return qs(base, params)

    def by_doi(self, doi: str) -> dict | None:
        d = recmod.normalize_doi(doi)
        if not d:
            return None
        url = self._url(BASE + "/" + d, {})
        try:
            payload = self.http.get_json(url)
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise
        message = payload.get("message") or {}
        return recmod.from_crossref(message) if message else None

    def search(
        self,
        query: str,
        rows: int = 50,
        offset: int = 0,
        from_year: int | None = None,
        to_year: int | None = None,
        doc_type: str = "journal-article",
        sort: str = "is-referenced-by-count",
    ) -> SearchResult:
        filters = []
        if doc_type:
            filters.append(f"type:{doc_type}")
        if from_year:
            filters.append(f"from-pub-date:{int(from_year)}-01-01")
        if to_year:
            filters.append(f"until-pub-date:{int(to_year)}-12-31")
        url = self._url(
            BASE,
            {
                "query.bibliographic": query,
                "rows": str(min(1000, max(1, rows))),
                "offset": str(max(0, offset)),
                "filter": ",".join(filters) or None,
                "sort": sort,
                "order": "desc",
                "select": "DOI,title,author,container-title,ISSN,publisher,issued,published-print,"
                "published-online,volume,issue,page,is-referenced-by-count,type,abstract,URL",
            },
        )
        payload = self.http.get_json(url)
        message = payload.get("message") or {}
        items = message.get("items") or []
        return SearchResult(
            engine="crossref",
            query=query,
            total=message.get("total-results"),
            records=[recmod.from_crossref(item) for item in items],
            url=url,
            meta={"filters": filters, "sort": sort},
        )

    def by_title(self, title: str, rows: int = 3) -> list[dict]:
        """Bibliographic title search; caller must verify similarity/year."""
        clean = re.sub(r"\s+", " ", (title or "")).strip()[:250]
        if len(clean) < 8:
            return []
        url = self._url(
            BASE,
            {
                "query.bibliographic": clean,
                "rows": str(max(1, rows)),
                "select": "DOI,title,author,container-title,ISSN,publisher,issued,published-print,"
                "published-online,volume,issue,page,is-referenced-by-count,type,abstract,URL",
            },
        )
        try:
            time.sleep(0.25)
            payload = self.http.get_json(url, retries=1)
        except HttpError:
            return []
        items = ((payload.get("message") or {}).get("items")) or []
        return [recmod.from_crossref(item) for item in items]

    def validate(self, doi: str) -> dict:
        record = self.by_doi(doi)
        return {"doi": recmod.normalize_doi(doi), "valid": bool(record), "record": record}
