# -*- coding: utf-8 -*-
"""Runtime wiring: one HTTP client, one persistent browser session, caches.

Design constraints learned from live probing (2026-10):

* Chromium cannot start inside a confined sandbox (named-pipe creation is
  blocked); the browser must be launched by a normal user process or an
  unconfined command.
* Starting/stopping the browser and reloading result pages repeatedly is the
  fastest way to trigger bot defences. Therefore: reuse a live browser on the
  debugging port, keep one tab, and enforce a minimum interval between
  navigations.
* Hit counts are expensive to obtain, so they are cached on disk keyed by
  engine + query SHA-256.
"""
from __future__ import annotations

import json
import pathlib
import time

from . import backends
from .backends.crossref import Crossref
from .backends.europepmc import EuropePMC
from .backends.openalex import OpenAlex
from .backends.scopus_api import ScopusAPI
from .backends.wos_api import WosAPI
from .cdp import CDP, Browser, CDPUnavailable
from .util import HttpClient, Log, ensure_dir, read_json, write_json
from .web import ScopusWeb, WosWeb, count_from_text, detect_state

ENGINE_ALIASES = {
    "scopus": "scopus-web",
    "scopus-web": "scopus-web",
    "scopus-api": "scopus-api",
    "wos": "wos-web",
    "wos-web": "wos-web",
    "wos-api": "wos-api",
    "openalex": "openalex",
    "crossref": "crossref",
}


def normalize_engine(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in ENGINE_ALIASES:
        raise ValueError(f"unknown source: {name!r} (use scopus, scopus-api, wos, wos-api, openalex)")
    return ENGINE_ALIASES[key]


class Runtime:
    def __init__(
        self,
        workdir=".",
        log: Log | None = None,
        mailto: str | None = None,
        port: int = 9222,
        min_nav_interval: float = 5.0,
        use_browser: bool = True,
        cache: bool = True,
    ):
        self.workdir = pathlib.Path(workdir).expanduser().resolve()
        self.log = log or Log(verbose=True)
        self.mailto = mailto
        self.min_nav_interval = float(min_nav_interval)
        self.use_browser = use_browser
        self.cache = cache
        self.http = HttpClient(
            cache_dir=(ensure_dir(self.workdir / ".work_lq" / "http_cache") if cache else None),
            log=self.log,
            min_interval=0.4,
        )
        self.openalex = OpenAlex(self.http, mailto=mailto)
        self.crossref = Crossref(self.http, mailto=mailto)
        self.europepmc = EuropePMC(self.http)
        self.scopus_api = ScopusAPI(self.http)
        self.wos_api = WosAPI(self.http)
        self.browser: Browser | None = None
        self._cdp: CDP | None = None
        self._scopus: ScopusWeb | None = None
        self._wos: WosWeb | None = None
        self.port = port
        self._last_nav = 0.0
        self._count_cache_path = self.workdir / ".work_lq" / "count_cache.json"
        self._count_cache = read_json(self._count_cache_path, default={}) if cache else {}

    # -- browser -------------------------------------------------------
    def cdp(self) -> CDP:
        if self._cdp is not None:
            return self._cdp
        if not self.use_browser:
            raise backends.BackendUnavailable("browser disabled (--no-browser)")
        self.browser = Browser(port=self.port, workdir=self.workdir, log=self.log)
        try:
            self.browser.ensure()
        except CDPUnavailable as exc:
            raise backends.BackendUnavailable(
                f"cannot start browser: {exc}. If the message mentions 'mojo' or 'Access is denied', "
                "launch the browser outside the sandbox (lq browser start from a normal terminal) "
                "and rerun with --browser-port."
            ) from exc
        self._cdp = self.browser.open("about:blank")
        self._cdp.min_nav_interval = self.min_nav_interval
        return self._cdp

    def scopus(self) -> ScopusWeb:
        if self._scopus is None:
            self._scopus = ScopusWeb(self.cdp(), workdir=self.workdir, log=self.log)
        return self._scopus

    def wos(self) -> WosWeb:
        if self._wos is None:
            self._wos = WosWeb(self.cdp(), workdir=self.workdir, log=self.log)
        return self._wos

    def detach(self) -> None:
        """Leave the browser running; only close the WebSocket."""
        if self._cdp is not None:
            self._cdp.close()
            self._cdp = None

    # -- counts --------------------------------------------------------
    def _cache_key(self, engine: str, query: str) -> str:
        import hashlib

        return f"{engine}:" + hashlib.sha256(query.encode("utf-8")).hexdigest()

    def cached_count(self, engine: str, query: str):
        entry = self._count_cache.get(self._cache_key(engine, query))
        if entry:
            return entry
        return None

    def save_count(self, engine: str, query: str, count: int | None, state: dict) -> None:
        if not self.cache:
            return
        self._count_cache[self._cache_key(engine, query)] = {
            "engine": engine,
            "query": query,
            "count": count,
            "state": state,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            write_json(self._count_cache_path, self._count_cache)
        except Exception:
            pass

    def count(self, engine: str, query: str, refresh: bool = False) -> dict:
        engine = normalize_engine(engine)
        if not refresh:
            hit = self.cached_count(engine, query)
            if hit and hit.get("count") is not None:
                return {"count": hit["count"], "state": hit.get("state", {}), "cached": True}

        if engine == "scopus-web":
            page = self.scopus().open_query(query)
            result = page.as_dict()
        elif engine == "wos-web":
            page = self.wos().open_query(query)
            result = page.as_dict()
        elif engine == "scopus-api":
            result = {"count": self.scopus_api.count(query), "state": {"engine": "scopus-api"}, "records_available": True}
        elif engine == "wos-api":
            result = {"count": self.wos_api.count(query), "state": {"engine": "wos-api"}, "records_available": True}
        elif engine == "openalex":
            anchor = plan_anchor(query)
            res = self.openalex.search(anchor=anchor, max_records=1)
            result = {"count": res.total, "state": {"engine": "openalex", "anchor": anchor}, "records_available": True}
        else:
            raise ValueError(f"cannot count with {engine}")
        state_obj = result.get("state")
        page_state = state_obj.get("state") if isinstance(state_obj, dict) else state_obj
        if page_state in ("login", "challenge"):
            raise backends.BackendUnavailable(
                f"{engine} requires interactive access ({page_state}). "
                "Sign in once in the browser window, or use --source openalex / an API key."
            )
        result["count"] = result.get("count")
        result["cached"] = False
        self.save_count(engine, query, result.get("count"), result.get("state") or {})
        return result

    # -- records -------------------------------------------------------
    def fetch_records(
        self,
        engine: str,
        query: str,
        max_records: int = 20,
        pages: int = 2,
        assume_type: str | None = None,
        sort: str = "cited",
    ) -> dict:
        engine = normalize_engine(engine)
        records: list[dict] = []
        total = None
        meta: dict = {}
        if engine == "scopus-web":
            scopus = self.scopus()
            if scopus.is_loaded(query, 1):
                state = scopus.last_state
            else:
                state = scopus.open_query(query, page=1, limit=10, sort=sort)
            total = state.count if state.count is not None else total
            if not state.records_available:
                meta["records_available"] = False
            else:
                records.extend(scopus.rows(assume_type=assume_type))
                for _ in range(max(0, pages - 1)):
                    if len(records) >= max_records:
                        break
                    if not scopus.next_page():
                        break
                    records.extend(scopus.rows(assume_type=assume_type))
                meta["records_available"] = bool(records)
        elif engine == "wos-web":
            wos = self.wos()
            state = wos.last_state if wos.is_loaded(query) else wos.open_query(query)
            total = state.count
            records = wos.rows()
            meta["records_available"] = bool(records)
            if not records and (state.count or 0) > 0:
                meta["note"] = (
                    "WoS shows a hit count but result records stay empty until an entitled user signs in; "
                    "use --source wos-api or --fallback openalex for records."
                )
        elif engine == "scopus-api":
            res = self.scopus_api.search(query, count=min(200, max_records), start=0)
            total = res.total
            records = res.records
        elif engine == "wos-api":
            res = self.wos_api.search(query, limit=min(50, max_records), page=1)
            total = res.total
            records = res.records
        elif engine == "openalex":
            anchor = plan_anchor(query)
            res = self.openalex.search(anchor=anchor, max_records=max_records)
            total = res.total
            records = res.records
            meta["anchor"] = anchor
        return {"records": records[:max_records], "total": total, "meta": meta}

    def close(self) -> None:
        self.detach()


def plan_anchor(text_or_plan) -> str:
    """Turn a query string (or plan) into an OpenAlex-friendly OR anchor."""
    if isinstance(text_or_plan, dict):
        from .backends.openalex import anchor_from_plan

        return anchor_from_plan(text_or_plan)
    text = str(text_or_plan or "")
    # Strip field tags / boolean operators and keep the first meaningful phrase.
    text = text.replace("(", " ").replace(")", " ").replace("{", " ").replace("}", " ")
    text = text.replace('"', " ")
    tokens = [t for t in text.split() if t.upper() not in ("AND", "OR", "NOT") and not t.upper().endswith("=")]
    return "|".join(tokens[:10])
