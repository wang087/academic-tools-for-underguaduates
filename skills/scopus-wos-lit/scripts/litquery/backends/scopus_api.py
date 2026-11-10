# -*- coding: utf-8 -*-
"""Scopus Search + Abstract Retrieval API backend.

Requires a Scopus API key (free for subscribers):
    setx SCOPUS_API_KEY "your-key"
Optional institutional token:
    setx SCOPUS_INST_TOKEN "your-insttoken"

Docs: https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl
      https://dev.elsevier.com/documentation/AbstractRetrievalAPI.wadl

Quota: 20,000 requests/week and ~2 requests/second for the standard key. The
client paces itself and treats HTTP 429 as a hard stop for the current call.
"""
from __future__ import annotations

import os
import time
import urllib.parse

from .. import records as recmod
from ..util import HttpClient, HttpError, parse_number, qs
from . import BackendUnavailable, SearchResult

SEARCH = "https://api.elsevier.com/content/search/scopus"
ABSTRACT = "https://api.elsevier.com/content/abstract"
DOI_LINK = "https://doi.org/"

SORT_MAP = {
    "cited": "-cited-by",
    "date": "-coverDate",
    "relevance": "",
}


def credentials() -> dict:
    return {
        "api_key": os.environ.get("SCOPUS_API_KEY") or os.environ.get("ELSEVIER_API_KEY") or "",
        "inst_token": os.environ.get("SCOPUS_INST_TOKEN") or os.environ.get("ELSEVIER_INST_TOKEN") or "",
    }


class ScopusAPI:
    def __init__(self, http: HttpClient, api_key: str | None = None, inst_token: str | None = None):
        creds = credentials()
        self.key = api_key or creds["api_key"]
        self.inst_token = inst_token or creds["inst_token"]
        self.http = http
        self._last_call = 0.0

    def available(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        if not self.key:
            raise BackendUnavailable(
                "Scopus API key missing. Set SCOPUS_API_KEY, or use --source scopus-web with a logged-in browser."
            )
        headers = {"X-ELS-APIKey": self.key, "Accept": "application/json"}
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        return headers

    def _pace(self) -> None:
        gap = 0.55 - (time.time() - self._last_call)
        if gap > 0:
            time.sleep(gap)
        self._last_call = time.time()

    def _get(self, url: str):
        self._pace()
        try:
            return self.http.get_json(url, headers=self._headers(), use_cache=False)
        except HttpError as exc:
            if exc.status == 401:
                raise BackendUnavailable("Scopus rejected the API key (401). Check SCOPUS_API_KEY and entitlement.") from exc
            if exc.status == 429:
                raise BackendUnavailable("Scopus rate limit hit (429). Wait and retry, or reduce --max-records.") from exc
            raise

    def search(
        self,
        query: str,
        count: int = 25,
        start: int = 0,
        sort: str = "cited",
        count_only: bool = False,
    ) -> SearchResult:
        params = {
            "query": query,
            "count": "1" if count_only else str(max(1, min(200, count))),
            "start": str(max(0, start)),
            "sort": SORT_MAP.get(sort, sort) or None,
            "httpAccept": "application/json",
        }
        url = qs(SEARCH, params)
        try:
            payload = self._get(url)
        except HttpError as exc:
            if exc.status in (400, 500) and params.get("sort"):
                params.pop("sort", None)
                url = qs(SEARCH, params)
                payload = self._get(url)
            else:
                raise
        root = payload.get("search-results") or {}
        total = parse_number(root.get("opensearch:totalResults"))
        entries = root.get("entry") or []
        clean = []
        for entry in entries:
            if isinstance(entry, dict) and "error" in entry:
                continue
            clean.append(entry)
        return SearchResult(
            engine="scopus-api",
            query=query,
            total=total,
            records=[] if count_only else [recmod.from_scopus(e) for e in clean],
            url=url,
            meta={
                "start": start,
                "count": len(clean),
                "items_per_page": root.get("opensearch:itemsPerPage"),
                "query_echo": (root.get("Query") or {}).get("query") if isinstance(root.get("Query"), dict) else None,
            },
        )

    def count(self, query: str) -> int | None:
        return self.search(query, count=1, count_only=True).total

    def abstract(self, doi: str = "", eid: str = "") -> dict:
        if doi:
            url = qs(f"{ABSTRACT}/doi/{urllib.parse.quote(recmod.normalize_doi(doi), safe='')}", {"httpAccept": "application/json"})
        elif eid:
            url = qs(f"{ABSTRACT}/eid/{urllib.parse.quote(eid, safe='')}", {"httpAccept": "application/json"})
        else:
            return {}
        try:
            payload = self._get(url)
        except HttpError as exc:
            if exc.status == 404:
                return {}
            raise
        status = payload.get("abstracts-retrieval-response") or {}
        coredata = status.get("coredata") or {}
        item = status.get("item") or {}
        abstract = coredata.get("dc:description") or ""
        if not abstract:
            bib = (((item.get("bibrecord") or {}).get("head") or {}).get("abstracts") or "")
            abstract = bib
        authors = []
        for author in ((item.get("bibrecord") or {}).get("head") or {}).get("author-group") or []:
            if isinstance(author, dict) and author.get("author"):
                for entry in author["author"] or []:
                    if entry.get("@seq"):
                        authors.append(
                            {
                                "seq": entry.get("@seq"),
                                "name": (entry.get("ce:indexed-name") or entry.get("ce:surname") or "").strip(),
                                "given": entry.get("ce:given-name") or "",
                                "surname": entry.get("ce:surname") or "",
                            }
                        )
        return {
            "abstract": abstract,
            "cited_by": parse_number(coredata.get("citedby-count")),
            "authors": authors,
            "title": coredata.get("dc:title"),
            "journal": coredata.get("prism:publicationName"),
            "doi": coredata.get("prism:doi"),
            "raw": status if not abstract else {},
        }

    def enrich(self, record: dict) -> dict:
        if record.get("abstract"):
            return record
        data = self.abstract(doi=record.get("doi") or "")
        if data.get("abstract"):
            record = dict(record)
            record["abstract"] = data["abstract"]
            record["abstract_source"] = "scopus-abstract-api"
            if data.get("cited_by"):
                record["cited_by"] = max(int(record.get("cited_by") or 0), int(data["cited_by"]))
        return record
