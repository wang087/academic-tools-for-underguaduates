# -*- coding: utf-8 -*-
"""Command line interface.

Conventions:

* human-readable progress goes to stderr, machine-readable JSON to stdout;
* exit code 0 = success, 2 = bad input / backend error, 3 = search under target;
* the browser is deliberately NOT closed at the end of a command. Repeated
  start/stop cycles and page reloads are what trigger bot defences.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import cdp as cdpmod
from . import fulltext as fulltext_mod
from . import journals as journal_mod
from . import query as query_mod
from . import records as recmod
from . import report as report_mod
from . import search as search_mod
from . import taxonomy as taxonomy_mod
from .backends import BackendUnavailable
from .runtime import Runtime
from .util import HttpError, Log, ensure_dir, read_json, setup_stdout, write_json, write_text

DEFAULT_TAXONOMY = pathlib.Path(__file__).resolve().parents[2] / "assets" / "direction_taxonomy.json"


def _json_print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _log(args) -> Log:
    work = pathlib.Path(args.workdir)
    return Log(path=work / ".work_lq" / "lq.jsonl", verbose=not getattr(args, "quiet", False))


def _runtime(args) -> Runtime:
    return Runtime(
        workdir=args.workdir,
        log=_log(args),
        mailto=args.mailto,
        port=args.port,
        min_nav_interval=args.min_nav_interval,
        use_browser=not args.no_browser,
        cache=not args.no_cache,
    )


def _plan(path: str) -> dict:
    payload = read_json(path)
    if "concepts" not in payload:
        raise ValueError("plan JSON has no 'concepts' array")
    return payload


def _taxonomy(path: str | None) -> dict:
    return taxonomy_mod.load(path or DEFAULT_TAXONOMY)


def _print_table(rows: list[list[str]], header: list[str]) -> None:
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(60, max(widths[i], len(str(cell))))
    def line(values):
        return " | ".join(str(v)[: widths[i]].ljust(widths[i]) for i, v in enumerate(values))
    print(line(header))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(line(row))


# ----------------------------------------------------------------- commands
def cmd_doctor(args) -> int:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    add("python", sys.version_info >= (3, 9), sys.version.split()[0])
    add("taxonomy", DEFAULT_TAXONOMY.exists(), str(DEFAULT_TAXONOMY))
    try:
        tax = _taxonomy(None)
        leaves = taxonomy_mod.leaves(tax)
        add("taxonomy_parse", True, f"{len(leaves)} leaves")
    except Exception as exc:
        add("taxonomy_parse", False, repr(exc))
    table = journal_mod.load_all(args.journal_table)
    add("journal_table", table.stats().get("entries", 0) > 0, table.stats())
    add("scopus_api_key", bool(__import__("os").environ.get("SCOPUS_API_KEY") or __import__("os").environ.get("ELSEVIER_API_KEY")), "env SCOPUS_API_KEY")
    add("wos_api_key", bool(__import__("os").environ.get("WOS_API_KEY") or __import__("os").environ.get("CLARIVATE_API_KEY")), "env WOS_API_KEY")
    add("browser_binary", bool(cdpmod.find_browser()), cdpmod.find_browser() or "not found")
    add("cdp_port", cdpmod.is_alive(args.port), f"127.0.0.1:{args.port}")
    critical = {"python", "taxonomy", "taxonomy_parse", "journal_table", "browser_binary"}
    optional = {"scopus_api_key", "wos_api_key", "cdp_port"}
    _json_print(
        {
            "ok": all(c["ok"] for c in checks if c["check"] in critical),
            "critical_failures": [c["check"] for c in checks if c["check"] in critical and not c["ok"]],
            "optional_missing": [c["check"] for c in checks if c["check"] in optional and not c["ok"]],
            "checks": checks,
        }
    )
    return 0


def cmd_directions_match(args) -> int:
    tax = _taxonomy(args.taxonomy)
    plan = taxonomy_mod.build_plan(args.input, tax, year_from=args.year_from)
    if args.out:
        write_json(args.out, plan)
    if args.json or not args.out:
        _json_print(plan)
    else:
        print(f"[OK] {plan['direction'].get('path')} -> {len(plan.get('english_keywords') or [])} keywords")
    return 0 if plan.get("direction", {}).get("matched") else 3


def cmd_directions_list(args) -> int:
    tax = _taxonomy(args.taxonomy)
    rows = taxonomy_mod.list_tree(tax, args.contains)
    if args.json:
        _json_print(rows)
        return 0
    _print_table(
        [[" > ".join(r["path"]), r["en"], "leaf" if r["leaf"] else "", str(r["keywords"])] for r in rows],
        ["path", "en", "leaf", "keywords"],
    )
    return 0


def cmd_query_build(args) -> int:
    plan = _plan(args.plan)
    engines = ["scopus", "wos"] if args.engine == "both" else [args.engine]
    builds = {}
    for engine in engines:
        opts = query_mod.BuildOptions(
            engine=engine,
            with_synonyms=not args.no_synonyms,
            with_broader=args.with_broader,
            with_narrower=args.with_narrower,
            wildcards=args.wildcards,
            phrase_mode=args.phrase_mode,
            proximity=args.proximity,
            include_filters=not args.no_filters,
            year_from=args.year_from,
        )
        built = query_mod.build(plan, opts)
        lint = query_mod.lint(built["query"], engine)
        built["lint"] = lint
        builds[engine] = built

    payload = {"schema_version": "litquery-queries/1.0", "plan": str(args.plan), "builds": builds}
    if args.out:
        out = pathlib.Path(args.out)
        write_json(out, payload)
        for engine, built in builds.items():
            write_text(out.with_name(out.stem + "_" + engine + ".txt"), built["query"] + "\n")
    _json_print(payload if args.json else {e: {"query": b["query"], "sha256": b["sha256"], "lint_ok": b["lint"]["ok"], "errors": b["lint"]["errors"]} for e, b in builds.items()})
    return 0 if all(b["lint"]["ok"] for b in builds.values()) else 2


def cmd_query_lint(args) -> int:
    if args.file:
        text = pathlib.Path(args.file).read_text(encoding="utf-8-sig").strip()
    else:
        text = args.query or ""
    result = query_mod.lint(text, args.engine)
    _json_print(result)
    return 0 if result["ok"] else 2


def cmd_query_scaffold(args) -> int:
    concepts = []
    for idx, spec in enumerate(args.concept or [], 1):
        if "=" not in spec:
            raise ValueError(f"--concept must look like label=term1|term2, got {spec!r}")
        label, terms = spec.split("=", 1)
        concepts.append(
            {
                "id": f"c{idx}",
                "label": label.strip(),
                "role": "core" if idx == 1 else "context",
                "terms": [t.strip() for t in terms.split("|") if t.strip()],
                "synonyms": [],
                "broader": [],
                "narrower": [],
                "proximity": args.proximity,
                "exact": True,
            }
        )
    for spec in args.synonym or []:
        if "=" not in spec:
            continue
        label, terms = spec.split("=", 1)
        for concept in concepts:
            if concept["label"].lower() == label.strip().lower():
                concept["synonyms"].extend(t.strip() for t in terms.split("|") if t.strip())
    plan = {
        "schema_version": "litquery-plan/1.0",
        "topic": {"zh": args.topic_zh, "en": args.topic_en},
        "english_keywords": [c["terms"][0] for c in concepts if c["terms"]],
        "concepts": concepts,
        "exclude": args.exclude or [],
        "filters": {
            "year_from": args.year_from,
            "year_to": None,
            "document_types": args.document_types or [],
            "languages": ["English"],
        },
        "notes": ["scaffold: concepts were supplied by the caller; verify synonyms before searching"],
    }
    write_json(args.out, plan)
    print(json.dumps({"ok": True, "out": args.out, "concepts": len(concepts)}, ensure_ascii=False))
    return 0


def cmd_reviews_discover(args) -> int:
    plan = _plan(args.plan)
    runtime = _runtime(args)
    try:
        if args.source == "openalex":
            options = search_mod.DiscoverOptions(
                engine="openalex",
                top_percent=args.top_percent,
                limit=args.limit,
                min_year=args.min_year,
                max_age_years=args.max_age,
                min_citations=args.min_citations,
                rank_policy=args.rank_policy,
                accept_q1=args.accept_q1,
                journal_table=args.journal_table,
                max_records=args.candidates,
                log=_log(args),
            )
            result = search_mod.openalex_discover(plan, runtime, options)
        else:
            options = search_mod.DiscoverOptions(
                engine=args.source,
                min_candidates=args.min_candidates,
                max_attempts=args.max_attempts,
                max_records=args.candidates,
                pages=args.pages,
                sort=args.sort,
                top_percent=args.top_percent,
                limit=args.limit,
                min_year=args.min_year,
                max_age_years=args.max_age,
                min_citations=args.min_citations,
                rank_policy=args.rank_policy,
                accept_q1=args.accept_q1,
                refresh_counts=args.refresh,
                journal_table=args.journal_table,
                browser_budget=args.browser_budget,
                require_abstract=not args.allow_no_abstract,
                log=_log(args),
            )
            result = search_mod.discover(plan, runtime, options)
    finally:
        runtime.detach()

    write_json(args.out, result)
    rows = []
    for i, record in enumerate(result.get("selected") or [], 1):
        first, corr = recmod.first_and_corresponding(record)
        rows.append([str(i), str(record.get("year") or ""), (record.get("title") or "")[:70], (record.get("journal") or "")[:32], str(record.get("cited_by") or 0), first[:24]])
    if rows:
        _print_table(rows, ["#", "year", "title", "journal", "cited", "first"])
    print(
        json.dumps(
            {"status": result.get("status"), "selected": len(result.get("selected") or []), "candidates": result.get("candidates"), "out": args.out},
            ensure_ascii=False,
        )
    )
    if not result.get("selected"):
        return 3
    return 0


def cmd_reviews_fetch(args) -> int:
    payload = read_json(args.input)
    selected = payload.get("selected") if isinstance(payload, dict) else payload
    if not selected:
        raise ValueError("input has no 'selected' reviews")
    runtime = _runtime(args)
    try:
        manifest = fulltext_mod.fetch_reviews(selected, runtime, args.outdir, allow_browser=not args.no_browser, log=_log(args))
    finally:
        runtime.detach()
    print(json.dumps({"count": manifest["count"], "status_counts": manifest["status_counts"], "outdir": args.outdir}, ensure_ascii=False))
    return 0


def cmd_reviews_digest_init(args) -> int:
    payload = read_json(args.input)
    selected = payload.get("selected") if isinstance(payload, dict) else payload
    tasks = report_mod.digest_tasks(selected or [])
    write_json(args.out, tasks)
    print(json.dumps({"out": args.out, "reviews": len(tasks["reviews"])}, ensure_ascii=False))
    return 0


def cmd_reviews_digest_check(args) -> int:
    tasks = read_json(args.tasks)
    filled = read_json(args.filled)
    result = report_mod.digest_check(tasks, filled)
    if args.out:
        write_json(args.out, result)
    _json_print(result)
    return 0 if result["ok"] else 2


def cmd_reviews_report(args) -> int:
    discover = read_json(args.discover)
    digest = read_json(args.digest) if args.digest else {}
    text = report_mod.reviews_report(discover, digest)
    write_text(args.out, text)
    print(json.dumps({"out": args.out, "selected": len(discover.get("selected") or []), "chars": len(text)}, ensure_ascii=False))
    return 0


def cmd_search_run(args) -> int:
    plan = _plan(args.plan)
    runtime = _runtime(args)
    try:
        run = search_mod.run(
            plan,
            runtime,
            search_mod.RunOptions(
                engine=args.source,
                min_results=args.min,
                max_attempts=args.max_attempts,
                max_records=args.max_records,
                pages=args.pages,
                sort=args.sort,
                refresh_counts=args.refresh,
                fetch=not args.count_only,
                fallback=args.fallback,
                enrich=not args.no_enrich,
                enrich_limit=args.enrich_limit,
                browser_budget=args.browser_budget,
                relevance_filter=not args.no_relevance_filter,
                min_concept_mentions=args.min_concept_mentions,
                log=_log(args),
            ),
        )
    finally:
        runtime.detach()

    write_json(args.out, run)
    if args.md:
        write_text(args.md, report_mod.list_report(run))
    chosen = run.get("chosen") or {}
    print(
        json.dumps(
            {
                "status": run.get("status"),
                "engine": run.get("engine"),
                "count": chosen.get("count"),
                "records": len(run.get("records") or []),
                "out": args.out,
                "md": args.md,
            },
            ensure_ascii=False,
        )
    )
    return 0 if run.get("status") == "ok" else 3


def cmd_list_build(args) -> int:
    run = read_json(args.input)
    text = report_mod.list_report(run)
    write_text(args.out, text)
    print(json.dumps({"out": args.out, "records": len(run.get("records") or []), "chars": len(text)}, ensure_ascii=False))
    return 0


def cmd_journals_lookup(args) -> int:
    table = journal_mod.load_all(args.table)
    entry, how = table.lookup(title=args.journal, issn=args.issn or "")
    rank = table.rank(title=args.journal, issn=args.issn or "", top_percent=args.top_percent, accept_q1=args.accept_q1)
    _json_print({"matched": bool(entry), "match": how, "entry": entry, "rank": rank})
    return 0 if entry else 3


def cmd_journals_import(args) -> int:
    result = journal_mod.import_csv(args.csv, args.format, args.out)
    print(json.dumps({k: v for k, v in result.items() if k != "table"}, ensure_ascii=False))
    return 0


def cmd_journals_stats(args) -> int:
    table = journal_mod.load_all(args.table)
    _json_print(table.stats())
    return 0


def cmd_browser(args) -> int:
    if args.action == "status":
        alive = cdpmod.is_alive(args.port)
        info = {}
        if alive:
            try:
                info = cdpmod.version(args.port)
            except Exception:
                info = {}
        _json_print({"alive": alive, "port": args.port, "browser": info.get("Browser")})
        return 0 if alive else 3
    if args.action == "start":
        browser = cdpmod.Browser(port=args.port, workdir=args.workdir, log=_log(args))
        browser.ensure()
        _json_print({"started": True, "port": args.port, "profile": str(browser.profile)})
        return 0
    if args.action == "stop":
        _json_print({"closed": cdpmod.quit_browser(args.port), "port": args.port})
        return 0
    return 2


def cmd_web_probe(args) -> int:
    runtime = _runtime(args)
    try:
        page = runtime.cdp()
        if args.engine == "scopus":
            state = runtime.scopus().open_query(args.query)
            records = runtime.scopus().rows()
        else:
            state = runtime.wos().open_query(args.query)
            records = runtime.wos().rows()
        snapshot = {}
        if args.outdir:
            from .web import save_snapshot

            snapshot = save_snapshot(page, args.outdir, args.engine)
    finally:
        runtime.detach()
    _json_print({"state": state.as_dict(), "records": len(records), "sample": records[:2], "snapshot": snapshot})
    return 0


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lq", description="Scopus / Web of Science literature workflow")
    parser.add_argument("--version", action="version", version="litquery 1.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=".")
    common.add_argument("--port", type=int, default=9222)
    common.add_argument("--no-browser", action="store_true")
    common.add_argument("--mailto", default=None)
    common.add_argument("--min-nav-interval", type=float, default=5.0)
    common.add_argument("--no-cache", action="store_true")
    common.add_argument("--quiet", action="store_true")

    p = sub.add_parser("doctor", parents=[common], help="environment and data checks")
    p.add_argument("--journal-table", default=None)
    p.set_defaults(func=cmd_doctor)

    d = sub.add_parser("directions", help="direction taxonomy")
    dsub = d.add_subparsers(dest="action", required=True)
    dm = dsub.add_parser("match", parents=[common])
    dm.add_argument("--input", required=True)
    dm.add_argument("--taxonomy", default=None)
    dm.add_argument("--year-from", type=int, default=None)
    dm.add_argument("--out", default=None)
    dm.add_argument("--json", action="store_true")
    dm.set_defaults(func=cmd_directions_match)
    dl = dsub.add_parser("list", parents=[common])
    dl.add_argument("--contains", default=None)
    dl.add_argument("--taxonomy", default=None)
    dl.add_argument("--json", action="store_true")
    dl.set_defaults(func=cmd_directions_list)

    q = sub.add_parser("query", help="build and lint queries")
    qsub = q.add_subparsers(dest="action", required=True)
    qb = qsub.add_parser("build", parents=[common])
    qb.add_argument("--plan", required=True)
    qb.add_argument("--engine", choices=["scopus", "wos", "both"], default="both")
    qb.add_argument("--out", default=None)
    qb.add_argument("--json", action="store_true")
    qb.add_argument("--no-synonyms", action="store_true")
    qb.add_argument("--with-broader", action="store_true")
    qb.add_argument("--with-narrower", action="store_true")
    qb.add_argument("--wildcards", action="store_true")
    qb.add_argument("--phrase-mode", choices=["exact", "loose", "proximity"], default="exact")
    qb.add_argument("--proximity", type=int, default=3)
    qb.add_argument("--no-filters", action="store_true")
    qb.add_argument("--year-from", type=int, default=None)
    qb.set_defaults(func=cmd_query_build)

    ql = qsub.add_parser("lint", parents=[common])
    ql.add_argument("--engine", choices=["scopus", "wos"], default="scopus")
    ql.add_argument("--query", default="")
    ql.add_argument("--file", default=None)
    ql.set_defaults(func=cmd_query_lint)

    qs = qsub.add_parser("scaffold", parents=[common])
    qs.add_argument("--topic-zh", required=True)
    qs.add_argument("--topic-en", default="")
    qs.add_argument("--concept", action="append", default=[])
    qs.add_argument("--synonym", action="append", default=[])
    qs.add_argument("--exclude", action="append", default=[])
    qs.add_argument("--year-from", type=int, default=None)
    qs.add_argument("--document-types", action="append", default=[])
    qs.add_argument("--proximity", type=int, default=3)
    qs.add_argument("--out", required=True)
    qs.set_defaults(func=cmd_query_scaffold)

    r = sub.add_parser("reviews", help="review discovery, fetching, digest")
    rsub = r.add_subparsers(dest="action", required=True)
    rd = rsub.add_parser("discover", parents=[common])
    rd.add_argument("--plan", required=True)
    rd.add_argument("--source", default="scopus", choices=["scopus", "scopus-api", "wos", "wos-api", "openalex"])
    rd.add_argument("--top-percent", type=float, default=20.0)
    rd.add_argument("--limit", type=int, default=8)
    rd.add_argument("--min-year", type=int, default=None)
    rd.add_argument("--max-age", type=int, default=6)
    rd.add_argument("--min-citations", type=int, default=20)
    rd.add_argument("--rank-policy", choices=["strict", "balanced", "off"], default="strict")
    rd.add_argument("--accept-q1", action="store_true")
    rd.add_argument("--candidates", type=int, default=30)
    rd.add_argument("--min-candidates", type=int, default=30)
    rd.add_argument("--max-attempts", type=int, default=3)
    rd.add_argument("--pages", type=int, default=3)
    rd.add_argument("--sort", choices=["cited", "relevance", "date"], default="cited")
    rd.add_argument("--journal-table", default=None)
    rd.add_argument("--browser-budget", type=int, default=8)
    rd.add_argument("--allow-no-abstract", action="store_true")
    rd.add_argument("--refresh", action="store_true")
    rd.add_argument("--out", required=True)
    rd.set_defaults(func=cmd_reviews_discover)

    rf = rsub.add_parser("fetch", parents=[common])
    rf.add_argument("--input", required=True)
    rf.add_argument("--outdir", default="fulltext")
    rf.set_defaults(func=cmd_reviews_fetch)

    rdi = rsub.add_parser("digest-init", parents=[common])
    rdi.add_argument("--input", required=True)
    rdi.add_argument("--out", required=True)
    rdi.set_defaults(func=cmd_reviews_digest_init)

    rdc = rsub.add_parser("digest-check", parents=[common])
    rdc.add_argument("--tasks", required=True)
    rdc.add_argument("--filled", required=True)
    rdc.add_argument("--out", default=None)
    rdc.set_defaults(func=cmd_reviews_digest_check)

    rr = rsub.add_parser("report", parents=[common])
    rr.add_argument("--discover", required=True)
    rr.add_argument("--digest", default=None)
    rr.add_argument("--out", required=True)
    rr.set_defaults(func=cmd_reviews_report)

    s = sub.add_parser("search", help="run a refined-topic search")
    ssub = s.add_subparsers(dest="action", required=True)
    sr = ssub.add_parser("run", parents=[common])
    sr.add_argument("--plan", required=True)
    sr.add_argument("--source", default="scopus", choices=["scopus", "scopus-api", "wos", "wos-api", "openalex"])
    sr.add_argument("--min", type=int, default=15)
    sr.add_argument("--max-attempts", type=int, default=4)
    sr.add_argument("--max-records", type=int, default=30)
    sr.add_argument("--pages", type=int, default=2)
    sr.add_argument("--sort", choices=["cited", "relevance", "date"], default="cited")
    sr.add_argument("--refresh", action="store_true")
    sr.add_argument("--count-only", action="store_true")
    sr.add_argument("--fallback", default="openalex")
    sr.add_argument("--no-enrich", action="store_true")
    sr.add_argument("--enrich-limit", type=int, default=12)
    sr.add_argument("--browser-budget", type=int, default=8)
    sr.add_argument("--no-relevance-filter", action="store_true")
    sr.add_argument("--min-concept-mentions", type=int, default=2)
    sr.add_argument("--out", required=True)
    sr.add_argument("--md", default=None)
    sr.set_defaults(func=cmd_search_run)

    l = sub.add_parser("list", help="build the final literature list")
    lsub = l.add_subparsers(dest="action", required=True)
    lb = lsub.add_parser("build", parents=[common])
    lb.add_argument("--input", required=True)
    lb.add_argument("--out", required=True)
    lb.set_defaults(func=cmd_list_build)

    j = sub.add_parser("journals", help="journal ranking table")
    jsub = j.add_subparsers(dest="action", required=True)
    jl = jsub.add_parser("lookup", parents=[common])
    jl.add_argument("--journal", required=True)
    jl.add_argument("--issn", default=None)
    jl.add_argument("--table", default=None)
    jl.add_argument("--top-percent", type=float, default=20.0)
    jl.add_argument("--accept-q1", action="store_true")
    jl.set_defaults(func=cmd_journals_lookup)
    ji = jsub.add_parser("import", parents=[common])
    ji.add_argument("--csv", required=True)
    ji.add_argument("--format", choices=["jcr", "cas", "sjr"], required=True)
    ji.add_argument("--out", default="journal_tiers.json")
    ji.set_defaults(func=cmd_journals_import)
    js = jsub.add_parser("stats", parents=[common])
    js.add_argument("--table", default=None)
    js.set_defaults(func=cmd_journals_stats)

    b = sub.add_parser("browser", help="persistent browser session")
    bsub = b.add_subparsers(dest="action", required=True)
    for action in ("start", "status", "stop"):
        bp = bsub.add_parser(action, parents=[common])
        bp.set_defaults(func=cmd_browser, action=action)

    w = sub.add_parser("web", help="diagnostics")
    wsub = w.add_subparsers(dest="action", required=True)
    wp = wsub.add_parser("probe", parents=[common])
    wp.add_argument("--engine", choices=["scopus", "wos"], required=True)
    wp.add_argument("--query", required=True)
    wp.add_argument("--outdir", default=None)
    wp.set_defaults(func=cmd_web_probe)

    return parser


def main(argv=None) -> int:
    setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BackendUnavailable as exc:
        print(json.dumps({"error": "backend_unavailable", "message": str(exc)}, ensure_ascii=False))
        return 2
    except HttpError as exc:
        print(json.dumps({"error": "http", "status": exc.status, "message": str(exc)[:300]}, ensure_ascii=False))
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)[:300]}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
