# -*- coding: utf-8 -*-
"""Search orchestration: query escalation, hit-count checks, record retrieval.

Two entry points:

* ``run``      - a precise topic search (the user's refined research question).
                 It widens the query step by step until at least ``min_results``
                 hits are found, and reports honestly when it cannot.
* ``discover`` - broad review discovery for a research direction. It screens
                 candidates by journal tier, citations and currency.

The escalation ladder is explicit and logged; no attempt is hidden. A count of
``None`` (page not parsed / blocked) is never treated as zero.
"""
from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass

from . import journals as journal_mod
from . import query as query_mod
from . import records as recmod
from . import screen as screen_mod
from .runtime import Runtime, normalize_engine
from .util import Log, norm_space
from .web import looks_like_abstract


@dataclass
class RunOptions:
    engine: str = "scopus"
    min_results: int = 15
    max_attempts: int = 4
    max_records: int = 30
    pages: int = 2
    sort: str = "cited"
    refresh_counts: bool = False
    fetch: bool = True
    fallback: str | None = "openalex"
    enrich: bool = True
    enrich_limit: int = 12
    browser_budget: int = 8
    relevance_filter: bool = True
    min_concept_mentions: int = 2
    log: Log | None = None


@dataclass
class DiscoverOptions:
    engine: str = "scopus"
    min_candidates: int = 30
    max_attempts: int = 3
    max_records: int = 30
    pages: int = 3
    sort: str = "cited"
    top_percent: float = 20.0
    limit: int = 8
    min_year: int | None = None
    max_age_years: int = 6
    min_citations: int = 20
    rank_policy: str = "strict"
    accept_q1: bool = False
    refresh_counts: bool = False
    fallback: str | None = "openalex"
    journal_table: str | None = None
    now_year: int | None = None
    browser_budget: int = 8
    require_abstract: bool = True
    max_per_journal: int = 2
    log: Log | None = None


def _copy_plan(plan: dict, year_shift: int = 0) -> dict:
    import copy

    clone = copy.deepcopy(plan)
    if year_shift:
        filters = clone.setdefault("filters", {})
        base = filters.get("year_from")
        if base:
            filters["year_from"] = max(1900, int(base) + int(year_shift))
    return clone


def _variant_options(engine: str, variant: dict) -> tuple[query_mod.BuildOptions, dict]:
    raw = dict(variant.get("opts") or {})
    year_shift = int(raw.pop("year_shift", 0) or 0)
    drop_roles = raw.pop("drop_roles", None)
    raw.pop("browser_budget", None)
    opts = query_mod.BuildOptions(engine=engine, **raw)
    if drop_roles:
        opts.drop_roles = list(drop_roles)
    return opts, {"year_shift": year_shift}


def _query_engine(engine: str) -> str:
    if engine.startswith("scopus"):
        return "scopus"
    if engine.startswith("wos"):
        return "wos"
    return "scopus"


def build_attempts(plan: dict, engine: str, max_attempts: int = 4) -> list[dict]:
    out = []
    engine_for_query = _query_engine(engine)
    for variant in query_mod.attempt_variants()[: max(1, max_attempts)]:
        opts, extra = _variant_options(engine_for_query, variant)
        out.append(
            {
                "label": variant["label"],
                "rationale": variant["rationale"],
                "opts": opts,
                "year_shift": extra["year_shift"],
                "plan": _copy_plan(plan, extra["year_shift"]),
            }
        )
    return out


def run(plan: dict, runtime: Runtime, options: RunOptions | None = None) -> dict:
    opts = options or RunOptions()
    log = opts.log or runtime.log
    engine = normalize_engine(opts.engine)
    attempts: list[dict] = []
    chosen = None

    for attempt in build_attempts(plan, engine, opts.max_attempts):
        entry = {"label": attempt["label"], "rationale": attempt["rationale"], "year_shift": attempt["year_shift"]}
        try:
            built = query_mod.build(attempt["plan"], attempt["opts"])
        except ValueError as exc:
            entry["error"] = str(exc)
            attempts.append(entry)
            continue
        lint = query_mod.lint(built["query"], "wos" if engine.startswith("wos") else "scopus")
        entry.update(
            {
                "query": built["query"],
                "sha256": built["sha256"],
                "stats": built["stats"],
                "lint_ok": lint["ok"],
                "lint_errors": lint["errors"],
                "lint_warnings": [w["code"] for w in lint["warnings"]],
            }
        )
        if not lint["ok"]:
            entry["error"] = "lint_failed"
            attempts.append(entry)
            log.warn(f"{attempt['label']}: query lint failed ({len(lint['errors'])} errors), skipped")
            continue
        result = runtime.count(engine, built["query"], refresh=opts.refresh_counts)
        entry["count"] = result.get("count")
        entry["count_state"] = result.get("state")
        entry["cached"] = result.get("cached")
        attempts.append(entry)
        log.info(f"{attempt['label']}: {entry['count']} hits" if entry.get("count") is not None else f"{attempt['label']}: count unavailable")
        if entry.get("count") is not None and entry["count"] >= opts.min_results:
            chosen = entry
            break

    status = "ok"
    if chosen is None:
        valid = [a for a in attempts if a.get("count") is not None]
        chosen = max(valid, key=lambda a: a["count"]) if valid else None
        status = "insufficient" if chosen else "failed"
        if status == "insufficient":
            log.warn(
                f"still only {chosen['count']} hits after {len(attempts)} attempts (target {opts.min_results}); "
                "report this to the user instead of pretending coverage"
            )

    payload = {
        "status": status,
        "engine": engine,
        "target_min": opts.min_results,
        "attempts": attempts,
        "chosen": chosen,
        "records": [],
        "records_meta": {},
        "notes": [],
    }
    if not chosen or not opts.fetch:
        return payload

    fetched = runtime.fetch_records(engine, chosen["query"], max_records=opts.max_records, pages=opts.pages, sort=opts.sort)
    records = fetched.get("records") or []
    meta = fetched.get("meta") or {}
    payload["records_meta"] = {
        "source_total": fetched.get("total"),
        "returned": len(records),
        **meta,
    }
    if len(records) < 3 and opts.fallback and engine in ("wos-web",):
        payload["notes"].append(f"record list unavailable from {engine}; falling back to {opts.fallback} for records")
        fb = runtime.fetch_records(opts.fallback, chosen["query"], max_records=opts.max_records, pages=1)
        records = fb.get("records") or []
        payload["records_meta"]["fallback"] = opts.fallback
        payload["records_meta"]["fallback_total"] = fb.get("total")

    if opts.enrich and records:
        records = enrich_records(records, runtime, limit=opts.enrich_limit, browser_budget=opts.browser_budget, log=log)

    excluded = []
    if opts.relevance_filter and records:
        kept = []
        for record in records:
            evidence = concept_evidence(record, plan, opts.min_concept_mentions)
            record["relevance"] = evidence
            if evidence["ok"]:
                kept.append(record)
            else:
                excluded.append(
                    {
                        "title": record.get("title"),
                        "doi": record.get("doi"),
                        "relevance": evidence,
                        "reason": "passing mention only",
                    }
                )
        records = kept
        if len(records) < opts.min_results:
            payload["notes"].append(
                f"relevance filter left {len(records)} records (< target {opts.min_results}); "
                "fetch more pages or relax with --no-relevance-filter"
            )
            payload["status"] = "insufficient"

    payload["records"] = records
    payload["excluded"] = excluded[:50]
    payload["stats"] = recmod.stats(records)
    return payload


def _normalize_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _title_match(left: str, right: str) -> float:
    a, b = _normalize_title(left), _normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def enrich_records(
    records: list[dict],
    runtime: Runtime,
    limit: int = 12,
    browser_budget: int = 8,
    log: Log | None = None,
) -> list[dict]:
    """Fill DOI / abstract / full author list / corresponding author.

    Order of preference:
      1. DOI already present -> batch OpenAlex lookup (cheap, no browser);
      2. title -> OpenAlex search with a strict similarity check;
      3. Scopus document page (browser) for the remaining budget.
    """
    log = log or runtime.log
    records = recmod.dedupe(records)
    unresolved = []

    dois = [r.get("doi") for r in records if r.get("doi")]
    if dois:
        found = runtime.openalex.by_dois(dois)
        for i, record in enumerate(records):
            if record.get("doi") and record["doi"] in found:
                records[i] = recmod.merge(record, found[record["doi"]])

    for i, record in enumerate(records):
        if record.get("doi"):
            continue
        title = record.get("title") or ""
        candidates = []
        oa = runtime.openalex.by_title(title)
        if oa:
            candidates.append(oa)
        candidates.extend(runtime.crossref.by_title(title, rows=3))
        best = None
        best_score = 0.0
        for candidate in candidates:
            score = _title_match(title, candidate.get("title") or "")
            year_ok = (
                not record.get("year")
                or not candidate.get("year")
                or abs(int(record["year"]) - int(candidate["year"])) <= 1
            )
            if score >= 0.9 and year_ok and score > best_score:
                best = candidate
                best_score = score
        if not best:
            unresolved.append(i)
            continue
        record["doi"] = best.get("doi")
        record["doi_source"] = "title-match"
        records[i] = recmod.merge(record, best)

    # Europe PMC often has abstracts that OpenAlex lacks (PubMed deposits).
    missing_dois = [r.get("doi") for r in records if r.get("doi") and not r.get("abstract")]
    if missing_dois:
        abstracts = runtime.europepmc.abstracts(missing_dois)
        for i, record in enumerate(records):
            doi = (record.get("doi") or "").lower()
            if doi in abstracts and not record.get("abstract"):
                record["abstract"] = abstracts[doi]
                record["abstract_source"] = "europepmc"

    if browser_budget > 0:
        used = 0
        for i in unresolved:
            if used >= browser_budget:
                break
            record = records[i]
            scopus_id = record.get("scopus_id")
            if not scopus_id:
                continue
            try:
                page = runtime.scopus().record_page(scopus_id)
            except Exception as exc:  # network or browser issue: do not abort the batch
                log.warn(f"record page failed for {scopus_id}: {type(exc).__name__}")
                continue
            used += 1
            if page.get("doi"):
                record["doi"] = page["doi"]
                record["doi_source"] = "scopus-record-page"
            if page.get("abstract") and not record.get("abstract"):
                record["abstract"] = page["abstract"]
                record["abstract_source"] = "scopus-record-page"
            if page.get("title") and not record.get("title"):
                record["title"] = page["title"]
            records[i] = recmod._finalize(record)

    # Second pass: newly resolved DOIs unlock corresponding-author data.
    new_dois = [r.get("doi") for r in records if r.get("doi") and not r.get("authors")]
    if new_dois:
        found = runtime.openalex.by_dois(new_dois)
        for i, record in enumerate(records):
            if record.get("doi") in found:
                records[i] = recmod.merge(record, found[record["doi"]])

    for i, record in enumerate(records):
        records[i] = recmod._finalize(record)
        if not records[i].get("abstract") and records[i].get("doi"):
            try:
                abstract = runtime.scopus_api.abstract(doi=records[i]["doi"]).get("abstract") if runtime.scopus_api.available() else ""
            except Exception:
                abstract = ""
            if abstract:
                records[i]["abstract"] = abstract
                records[i]["abstract_source"] = "scopus-abstract-api"
    return records


def _authors_from_europepmc(result: dict) -> list[dict]:
    authors = []
    for i, author in enumerate(((result.get("authorList") or {}).get("author")) or []):
        name = author.get("fullName") or author.get("collectiveName") or ""
        if not name:
            continue
        authors.append(
            recmod._author(
                name=name,
                given=author.get("firstName") or "",
                family=author.get("lastName") or "",
                is_first=(i == 0),
                source="europepmc",
            )
        )
    return authors


def backfill_selected_abstracts(selected: list[dict], runtime: Runtime, budget: int = 8, log: Log | None = None) -> list[dict]:
    """Fetch abstracts for the *selected* reviews only.

    Order: Europe PMC (cheap, no browser) -> Scopus document page (budgeted).
    A fragment that merely follows the word "Abstract" is rejected by
    ``looks_like_abstract``; otherwise a chemical list can be mistaken for prose.
    """
    log = log or runtime.log
    used = 0
    for record in selected:
        if not looks_like_abstract(record.get("abstract") or ""):
            record["abstract"] = ""
            record["abstract_source"] = ""
        if record.get("abstract"):
            continue

        if record.get("doi"):
            try:
                epmc = runtime.europepmc.by_doi(record["doi"])
                abstract = re.sub(r"<[^>]+>", " ", epmc.get("abstractText") or "").strip()
                if looks_like_abstract(abstract):
                    record["abstract"] = abstract
                    record["abstract_source"] = "europepmc"
                epmc_authors = _authors_from_europepmc(epmc)
                if len(epmc_authors) > len(record.get("authors") or []):
                    record["authors"] = epmc_authors
            except Exception:
                pass

        if used >= budget or not record.get("scopus_id"):
            recmod._finalize(record)
            continue
        try:
            page = runtime.scopus().record_page(record["scopus_id"])
        except Exception as exc:
            log.warn(f"abstract backfill failed for {record.get('scopus_id')}: {type(exc).__name__}")
            continue
        used += 1
        if page.get("abstract"):
            record["abstract"] = page["abstract"]
            record["abstract_source"] = "scopus-record-page"
        if page.get("doi") and not record.get("doi"):
            record["doi"] = page["doi"]
            record["doi_source"] = "scopus-record-page"
        recmod._finalize(record)
    return selected


def discover(plan: dict, runtime: Runtime, options: DiscoverOptions | None = None) -> dict:
    opts = options or DiscoverOptions()
    log = opts.log or runtime.log
    engine = normalize_engine(opts.engine)
    table = journal_mod.load_all(opts.journal_table)

    attempts: list[dict] = []
    chosen = None
    for attempt in build_attempts(plan, engine, opts.max_attempts):
        entry = {"label": attempt["label"], "rationale": attempt["rationale"]}
        try:
            built = query_mod.build(attempt["plan"], attempt["opts"])
        except ValueError as exc:
            entry["error"] = str(exc)
            attempts.append(entry)
            continue
        entry.update({"query": built["query"], "sha256": built["sha256"]})
        result = runtime.count(engine, built["query"], refresh=opts.refresh_counts)
        entry["count"] = result.get("count")
        entry["cached"] = result.get("cached")
        attempts.append(entry)
        log.info(f"discover {attempt['label']}: {entry['count']} hits")
        if entry.get("count") is not None and entry["count"] >= opts.min_candidates:
            chosen = entry
            break

    if chosen is None:
        valid = [a for a in attempts if a.get("count") is not None]
        chosen = max(valid, key=lambda a: a["count"]) if valid else None

    notes = []
    records: list[dict] = []
    total = None
    if chosen:
        fetched = runtime.fetch_records(
            engine,
            chosen["query"],
            max_records=opts.max_records,
            pages=opts.pages,
            assume_type="review" if engine == "scopus-web" else None,
            sort=opts.sort,
        )
        records = fetched.get("records") or []
        total = fetched.get("total")
        notes.extend([v for v in [fetched.get("meta", {}).get("note")] if v])
        if not records and opts.fallback:
            notes.append(f"no record rows from {engine}; using {opts.fallback} for candidate records")
            fb = runtime.fetch_records(opts.fallback, chosen["query"], max_records=opts.max_records, pages=1)
            records = fb.get("records") or []
            total = fb.get("total")

    if records:
        records = enrich_records(records, runtime, limit=opts.max_records, browser_budget=opts.browser_budget, log=log)

    selection = screen_mod.select(
        records,
        table,
        screen_mod.SelectOptions(
            top_percent=opts.top_percent,
            min_year=opts.min_year,
            max_age_years=opts.max_age_years,
            min_citations=opts.min_citations,
            rank_policy=opts.rank_policy,
            accept_q1=opts.accept_q1,
            limit=opts.limit,
            max_per_journal=opts.max_per_journal,
            now_year=opts.now_year,
            require_topic_terms=_core_terms(plan),
            scope_terms=[str(x) for x in (plan.get("scope_terms") or [])],
        ),
    )
    selected = backfill_selected_abstracts(
        selection["selected"], runtime, budget=min(8, max(1, opts.limit)), log=log
    )
    rejected = list(selection["rejected"])
    if opts.require_abstract:
        kept = []
        dropped = []
        for record in selected:
            if looks_like_abstract(record.get("abstract") or ""):
                kept.append(record)
            else:
                record.setdefault("selection", {}).setdefault("reject_reasons", []).append("abstract_unavailable")
                record["selection"]["selected"] = False
                dropped.append(record)
        if dropped:
            selection["stats"].setdefault("warnings", []).append(
                f"{len(dropped)} selected reviews had no usable abstract and were excluded; "
                "use --allow-no-abstract to keep them and report the gap"
            )
            selection["stats"].setdefault("rejected_by_reason", {})["abstract_unavailable"] = len(dropped)
            rejected = dropped + rejected
        selected = kept
        selection["stats"]["selected"] = len(selected)

    return {
        "status": "ok" if selected else "no_selection",
        "engine": engine,
        "attempts": attempts,
        "chosen": chosen,
        "total": total,
        "candidates": len(records),
        "selected": selected,
        "rejected": rejected,
        "stats": selection["stats"],
        "journal_table": table.stats(),
        "notes": notes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _core_terms(plan: dict, limit: int = 12) -> list[str]:
    for concept in plan.get("concepts") or []:
        if (concept.get("role") or "core").lower() == "core":
            return [str(t) for t in (concept.get("terms") or [])][:limit]
    return [str(t) for t in (plan.get("english_keywords") or [])][:limit]


def concept_evidence(record: dict, plan: dict, min_mentions: int = 2) -> dict:
    """Check that each core concept is actually discussed, not merely matched.

    A broad ``OR`` block can be satisfied by one passing mention in an abstract;
    citation sorting then floats tangential mega-reviews to the top. Requiring a
    title hit or >= N mentions per core block removes most of that drift.
    """
    title = (record.get("title") or "").lower()
    body = title + " " + (record.get("abstract") or "").lower()
    has_abstract = bool(record.get("abstract"))
    blocks = []
    ok_all = True
    for concept in plan.get("concepts") or []:
        if (concept.get("role") or "core").lower() != "core":
            continue
        terms = [str(t) for t in (concept.get("terms") or []) + (concept.get("synonyms") or [])]
        title_hit = False
        mentions = 0
        distinct = 0
        for term in terms:
            t = term.strip().lower()
            if not t:
                continue
            if " " in t:
                n = body.count(t)
                if t in title:
                    title_hit = True
            else:
                pattern = r"\b" + re.escape(t.rstrip("*")) + r"\w*"
                n = len(re.findall(pattern, body))
                if re.search(pattern, title):
                    title_hit = True
            if n:
                distinct += 1
                mentions += n
        block_ok = title_hit or mentions >= min_mentions
        if has_abstract and not block_ok:
            ok_all = False
        blocks.append(
            {
                "label": concept.get("label"),
                "title_hit": title_hit,
                "distinct": distinct,
                "mentions": mentions,
                "ok": block_ok,
            }
        )
    return {"ok": ok_all if has_abstract else True, "has_abstract": has_abstract, "blocks": blocks}


def openalex_filters(plan: dict, doctype: str = "review") -> str:
    filters = []
    if doctype:
        filters.append("type:review" if doctype == "review" else f"type:{doctype}")
    yf = (plan.get("filters") or {}).get("year_from")
    if yf:
        filters.append(f"from_publication_date:{int(yf)}-01-01")
    return ",".join(filters)


def openalex_discover(plan: dict, runtime: Runtime, options: DiscoverOptions | None = None) -> dict:
    """Discovery that does not use Scopus/WoS at all (no subscription needed)."""
    opts = options or DiscoverOptions(engine="openalex")
    log = opts.log or runtime.log
    table = journal_mod.load_all(opts.journal_table)
    from .backends.openalex import anchor_from_plan

    anchor = anchor_from_plan(plan)
    filters = openalex_filters(plan, "review")
    result = runtime.openalex.search(anchor=anchor, filters=filters, max_records=max(opts.max_records, 50))
    records = enrich_records(result.records, runtime, limit=opts.max_records, browser_budget=0, log=log) if result.records else []
    selection = screen_mod.select(
        records,
        table,
        screen_mod.SelectOptions(
            top_percent=opts.top_percent,
            min_year=opts.min_year,
            max_age_years=opts.max_age_years,
            min_citations=opts.min_citations,
            rank_policy=opts.rank_policy,
            accept_q1=opts.accept_q1,
            limit=opts.limit,
            max_per_journal=opts.max_per_journal,
            now_year=opts.now_year,
            require_topic_terms=_core_terms(plan),
            scope_terms=[str(x) for x in (plan.get("scope_terms") or [])],
        ),
    )
    selected = backfill_selected_abstracts(selection["selected"], runtime, budget=0, log=log)
    if opts.require_abstract:
        selected = [r for r in selected if looks_like_abstract(r.get("abstract") or "")]
        selection["stats"]["selected"] = len(selected)
    return {
        "status": "ok" if selected else "no_selection",
        "engine": "openalex",
        "anchor": anchor,
        "filters": filters,
        "total": result.total,
        "candidates": len(records),
        "selected": selected,
        "rejected": selection["rejected"],
        "stats": selection["stats"],
        "journal_table": table.stats(),
        "notes": ["OpenAlex discovery: ranking is still decided by the bundled/imported journal table, not by Scopus/WoS."],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
