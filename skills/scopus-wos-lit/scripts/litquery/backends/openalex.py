# -*- coding: utf-8 -*-
"""OpenAlex backend.

OpenAlex needs no credentials and is the fallback that keeps the pipeline
running when Scopus / Web of Science are unavailable. It is NOT a substitute for
a subscription database when the user explicitly asks for Scopus/WoS: callers
must label the provenance and say which fields were enriched.

Boolean support in OpenAlex filters is limited to ``|`` (OR), ``+`` (AND) and
``!`` (NOT) inside a single filter value; complex nesting is not supported. The
builder therefore sends a simplified anchor (core concept terms OR-ed) and the
local keyword screen does the fine-grained work.
"""
from __future__ import annotations

import re
import urllib.parse

from .. import records as recmod
from ..util import HttpClient, HttpError, qs
from . import BackendUnavailable, SearchResult

BASE = "https://api.openalex.org/works"
SOURCE = "https://api.openalex.org/sources"

SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "display_name",
        "publication_year",
        "cited_by_count",
        "type",
        "authorships",
        "primary_location",
        "best_oa_location",
        "open_access",
        "abstract_inverted_index",
        "language",
    ]
)


def _or_join(terms: list[str], limit: int = 10) -> str:
    cleaned = []
    for term in terms:
        t = re.sub(r"[\"{}()]", "", str(term)).strip()
        if not t or t.lower() in {"and", "or", "not"}:
            continue
        # OpenAlex filter values are comma/pipe delimited; spaces are allowed but
        # a literal comma would split the filter, so drop it.
        t = t.replace(",", " ")
        cleaned.append(t)
    return "|".join(cleaned[:limit])


def anchor_from_plan(plan: dict) -> str:
    """Build a recall-oriented OR anchor from the plan's core concept."""
    concepts = plan.get("concepts") or []
    core = next((c for c in concepts if (c.get("role") or "core") == "core"), concepts[0] if concepts else None)
    if not core:
        return ""
    terms = list(core.get("terms") or []) + list(core.get("synonyms") or [])
    return _or_join(terms)


class OpenAlex:
    def __init__(self, http: HttpClient, mailto: str | None = None):
        self.http = http
        self.mailto = mailto
        self.disabled = False
        self.disabled_reason = ""

    def _check(self) -> bool:
        return not self.disabled

    def _handle_error(self, exc: HttpError) -> None:
        if exc.status in (403, 429) or "429" in str(exc):
            self.disabled = True
            self.disabled_reason = f"HTTP {exc.status}: rate limited"

    def _params(self, **kwargs) -> str:
        params = {"mailto": self.mailto} if self.mailto else {}
        params.update(kwargs)
        return qs(BASE, params)

    def search(
        self,
        anchor: str = "",
        filters: str = "",
        per_page: int = 100,
        max_records: int = 200,
        sort: str = "cited_by_count:desc",
        cursor: str = "*",
        title_only: bool = False,
    ) -> SearchResult:
        if not self._check():
            return SearchResult(
                engine="openalex", query="", total=0, records=[], meta={"disabled": self.disabled_reason}
            )
        parts = [p for p in (filters or "").split(",") if p]
        if anchor:
            parts.append(("title.search:" if title_only else "title_and_abstract.search:") + anchor)
        filter_str = ",".join(parts)
        collected: list[dict] = []
        total = None
        seen_cursor = set()
        url = ""
        while len(collected) < max_records:
            url = self._params(
                filter=filter_str or None,
                per_page=str(max(1, min(200, per_page))),
                cursor=cursor,
                select=SELECT_FIELDS,
                sort=sort or None,
            )
            payload = None
            try:
                payload = self.http.get_json(url, retries=0)
            except HttpError as exc:
                self._handle_error(exc)
                break
            if payload is None:
                break
            total = payload.get("meta", {}).get("count", total)
            results = payload.get("results") or []
            if not results:
                break
            for work in results:
                collected.append(recmod.from_openalex(work))
                if len(collected) >= max_records:
                    break
            cursor = payload.get("meta", {}).get("next_cursor")
            if not cursor or cursor in seen_cursor:
                break
            seen_cursor.add(cursor)
        return SearchResult(
            engine="openalex",
            query=filter_str,
            total=total,
            records=collected,
            url=url,
            meta={"anchor": anchor, "filters": filter_str, "sort": sort},
        )

    def by_doi(self, doi: str) -> dict | None:
        if not self._check():
            return None
        d = recmod.normalize_doi(doi)
        if not d:
            return None
        url = self._params2(f"https://api.openalex.org/works/https://doi.org/{d}", {"select": SELECT_FIELDS})
        try:
            payload = self.http.get_json(url, retries=0)
        except HttpError as exc:
            if exc.status == 404:
                return None
            self._handle_error(exc)
            return None
        return recmod.from_openalex(payload)

    def by_dois(self, dois: list[str], chunk: int = 40) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not self._check():
            return out
        clean = [recmod.normalize_doi(d) for d in dois if d]
        clean = [d for d in dict.fromkeys(clean) if d]
        for i in range(0, len(clean), chunk):
            if not self._check():
                break
            group = clean[i : i + chunk]
            expr = "|".join(f"doi:{d}" for d in group)
            url = self._params(filter=expr, per_page=str(len(group)), select=SELECT_FIELDS)
            try:
                payload = self.http.get_json(url, retries=0)
            except HttpError as exc:
                self._handle_error(exc)
                continue
            for work in payload.get("results") or []:
                record = recmod.from_openalex(work)
                if record.get("doi"):
                    out[record["doi"]] = record
        return out

    def by_title(self, title: str, per_page: int = 3) -> dict | None:
        """Best-effort DOI resolution for a record that only has a title.

        Title search is fuzzy by nature, so the caller MUST verify similarity
        before accepting the returned DOI.
        """
        clean = re.sub(r"[\"{}()]", " ", title or "").strip()[:250]
        if len(clean) < 8:
            return None
        if not self._check():
            return None
        url = self._params(
            search=clean,
            per_page=str(per_page),
            select=SELECT_FIELDS,
        )
        try:
            payload = self.http.get_json(url, retries=0)
        except HttpError as exc:
            self._handle_error(exc)
            return None
        results = payload.get("results") or []
        if not results:
            return None
        return recmod.from_openalex(results[0])

    @staticmethod
    def _params2(base: str, params: dict) -> str:
        return qs(base, params)

    def journal_metrics(self, issn: str) -> dict:
        issn = re.sub(r"[^0-9Xx]", "", issn or "")
        if not issn:
            return {}
        url = qs(SOURCE, {"filter": f"issn:{issn}", "per_page": "1"})
        try:
            payload = self.http.get_json(url)
        except HttpError:
            return {}
        results = payload.get("results") or []
        if not results:
            return {}
        source = results[0]
        stats = source.get("summary_stats") or {}
        return {
            "openalex_source_id": source.get("id"),
            "display_name": source.get("display_name"),
            "issn_l": source.get("issn_l"),
            "works_count": source.get("works_count"),
            "cited_by_count": source.get("cited_by_count"),
            "two_year_mean_citedness": stats.get("2yr_mean_citedness"),
            "h_index": stats.get("h_index"),
            "i10_index": stats.get("i10_index"),
        }


def available() -> bool:
    return True
