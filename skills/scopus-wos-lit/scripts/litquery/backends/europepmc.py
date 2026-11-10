# -*- coding: utf-8 -*-
"""Europe PMC backend: abstracts and OA links for biomedical DOIs.

Europe PMC indexes PubMed/MEDLINE records, so it often has an abstract even when
OpenAlex does not (many subscription journals deposit abstracts with PubMed but
not with Crossref/OpenAlex). This is the cheapest way to give the screener real
text before spending browser budget on document pages.
"""
from __future__ import annotations

import re

from ..util import HttpClient, HttpError, qs
from . import SearchResult

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePMC:
    def __init__(self, http: HttpClient):
        self.http = http

    def by_doi(self, doi: str) -> dict:
        doi = (doi or "").strip()
        if not doi:
            return {}
        url = qs(BASE, {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": "1"})
        try:
            payload = self.http.get_json(url)
        except HttpError:
            return {}
        results = (payload.get("resultList") or {}).get("result") or []
        return results[0] if results else {}

    def abstracts(self, dois: list[str], chunk: int = 15) -> dict[str, str]:
        clean = [d for d in dict.fromkeys((d or "").strip() for d in dois) if d]
        out: dict[str, str] = {}
        for i in range(0, len(clean), chunk):
            group = clean[i : i + chunk]
            query = " OR ".join(f'DOI:"{d}"' for d in group)
            url = qs(BASE, {"query": query, "format": "json", "resultType": "core", "pageSize": str(len(group))})
            try:
                payload = self.http.get_json(url)
            except HttpError:
                continue
            for result in (payload.get("resultList") or {}).get("result") or []:
                doi = (result.get("doi") or "").lower()
                abstract = re.sub(r"<[^>]+>", " ", result.get("abstractText") or "").strip()
                if doi and abstract:
                    out[doi] = re.sub(r"\s+", " ", abstract)
        return out

    def search(self, query: str, page_size: int = 25) -> SearchResult:
        url = qs(BASE, {"query": query, "format": "json", "resultType": "core", "pageSize": str(page_size)})
        payload = self.http.get_json(url)
        results = (payload.get("resultList") or {}).get("result") or []
        return SearchResult(
            engine="europepmc",
            query=query,
            total=(payload.get("hitCount") if isinstance(payload.get("hitCount"), int) else None),
            records=[],
            url=url,
            meta={"returned": len(results)},
        )
