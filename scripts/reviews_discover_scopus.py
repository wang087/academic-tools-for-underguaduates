# -*- coding: utf-8 -*-
"""阶段 R 可选增强：用 Scopus / Web of Science 发现权威综述。

本脚本**不替换**原有 `search_plan.py → search_run.py → reviews_prepare.py` 链路，
只把外部 skill `skills/scopus-wos-lit` 的综述发现能力产出为阶段 R 认识的
`reviews.json`（`{"records": [...]}`），随后可直接交给：

    python 流程1/scripts/reviews_prepare.py --input .work_cdp/reviews/reviews_scopus.json

设计约束：
  * 默认不启用；只有显式调用本脚本才会访问 Scopus/WoS/OpenAlex。
  * 输出字段与 `reviews_prepare.py` 的 `norm_record()` 对齐。
  * 不修改 `.work_cdp/reviews/reviews.json`，除非显式 `--merge-into`。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
FLOW_ROOT = HERE.parent
SKILL_ROOT = FLOW_ROOT / "skills" / "scopus-wos-lit"
SKILL_SCRIPTS = SKILL_ROOT / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            stream.flush()
        except Exception:
            pass
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

from litquery import records as recmod  # noqa: E402
from litquery import search as search_mod  # noqa: E402
from litquery import taxonomy as taxonomy_mod  # noqa: E402
from litquery.backends import BackendUnavailable  # noqa: E402
from litquery.runtime import Runtime  # noqa: E402
from litquery.util import Log, ensure_dir, read_json  # noqa: E402


def _default_workdir() -> pathlib.Path:
    env = os.environ.get("PAPER_WORKDIR") or os.environ.get("FLOW_WORKDIR")
    return pathlib.Path(env).expanduser().resolve() if env else pathlib.Path.cwd()


def _load_records(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "included", "items", "reviews"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _to_flow_record(record: dict, source: str) -> dict:
    authors = "; ".join(a.get("name", "") for a in (record.get("authors") or []) if a.get("name"))
    selection = record.get("selection") or {}
    reasons = selection.get("reasons") or []
    return {
        "title": record.get("title") or "",
        "authors": authors,
        "journal": record.get("journal") or "",
        "year": record.get("year") or "",
        "doi": recmod.normalize_doi(record.get("doi")),
        "type": "review",
        "citations": int(record.get("cited_by") or 0),
        "oa_url": record.get("oa_url") or "",
        "is_oa": bool(record.get("oa_url")),
        "abstract": record.get("abstract") or "",
        "platform": source,
        "why_selected": "; ".join(reasons[:3]) if reasons else "Scopus/OpenAlex 综述筛选",
        "first_author": record.get("first_author") or "",
        "corresponding_authors": record.get("corresponding_authors") or [],
        "journal_rank": record.get("journal_rank") or {},
    }


def _merge(existing: list[dict], new: list[dict]) -> list[dict]:
    out = list(existing)
    index = {}
    for i, record in enumerate(out):
        key = (recmod.normalize_doi(record.get("doi")) or record.get("title", "")).lower()
        index[key] = i
    for record in new:
        key = (recmod.normalize_doi(record.get("doi")) or record.get("title", "")).lower()
        if key in index:
            current = out[index[key]]
            if not current.get("abstract") and record.get("abstract"):
                current["abstract"] = record["abstract"]
            if not current.get("oa_url") and record.get("oa_url"):
                current["oa_url"] = record["oa_url"]
            if not current.get("why_selected") and record.get("why_selected"):
                current["why_selected"] = record["why_selected"]
            continue
        index[key] = len(out)
        out.append(record)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="阶段 R 可选增强：Scopus/WoS/OpenAlex 综述发现 → reviews.json"
    )
    ap.add_argument("--direction", help="研究方向（中文或英文），例如 神经科学 / immunology")
    ap.add_argument("--plan", help="已有 litquery plan.json；提供后忽略 --direction")
    ap.add_argument("--source", default="scopus",
                    choices=["scopus", "wos", "openalex", "scopus-api", "wos-api"])
    ap.add_argument("--limit", type=int, default=8, help="输出综述篇数（默认 8）")
    ap.add_argument("--candidates", type=int, default=30, help="候选记录上限")
    ap.add_argument("--pages", type=int, default=3, help="Scopus 结果页数（每页 10 条）")
    ap.add_argument("--min-year", type=int, default=2018)
    ap.add_argument("--min-citations", type=int, default=10)
    ap.add_argument("--rank-policy", default="off",
                    choices=["strict", "balanced", "off"],
                    help="默认 off：与阶段 R 的‘综述优先’一致；要严格前 20%% 用 balanced/strict")
    ap.add_argument("--sort", default="cited", choices=["cited", "relevance", "date"])
    ap.add_argument("--browser-budget", type=int, default=4,
                    help="允许打开多少个 Scopus 记录页补摘要")
    ap.add_argument("--max-per-journal", type=int, default=3,
                    help="同一期刊最多入选几篇（默认 3，避免单一顶刊占满）")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--out", default=".work_cdp/reviews/reviews_scopus.json")
    ap.add_argument("--merge-into", default="",
                    help="与已有 reviews.json 合并（按 DOI 去重）")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    if not args.plan and not args.direction:
        ap.error("需要 --direction 或 --plan")

    workdir = pathlib.Path(args.workdir).expanduser().resolve() if args.workdir else _default_workdir()
    runtime_root = ensure_dir(workdir / ".work_cdp" / "external")
    log = Log(path=runtime_root / "reviews_discover.jsonl", verbose=True)

    if args.plan:
        plan = read_json(pathlib.Path(args.plan))
    else:
        tax = taxonomy_mod.load(taxonomy_mod.default_taxonomy_path())
        plan = taxonomy_mod.build_plan(args.direction, tax, year_from=args.min_year)
        if not plan.get("direction", {}).get("matched"):
            print("[WARN] 方向未命中内置词表，将依赖 agent 补全的 concepts；请检查 plan。",
                  file=sys.stderr)

    runtime = Runtime(
        workdir=runtime_root,
        log=log,
        port=args.port,
        min_nav_interval=5.0,
        use_browser=not args.no_browser,
        cache=True,
    )
    options = search_mod.DiscoverOptions(
        engine=args.source,
        min_candidates=max(10, args.candidates // 2),
        max_attempts=2,
        max_records=args.candidates,
        pages=args.pages,
        sort=args.sort,
        top_percent=20.0,
        limit=args.limit,
        min_year=args.min_year,
        max_age_years=8,
        min_citations=args.min_citations,
        rank_policy=args.rank_policy,
        browser_budget=args.browser_budget,
        require_abstract=False,
        max_per_journal=args.max_per_journal,
        log=log,
    )
    try:
        if args.source == "openalex":
            result = search_mod.openalex_discover(plan, runtime, options)
        else:
            result = search_mod.discover(plan, runtime, options)
    except BackendUnavailable as exc:
        print(f"[X] 数据源不可用：{exc}", file=sys.stderr)
        print("    可改用 --source openalex，或在浏览器完成登录后重试。", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"[X] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        runtime.detach()

    selected = result.get("selected") or []
    records = [_to_flow_record(record, args.source) for record in selected]
    out_path = pathlib.Path(args.out)
    if not out_path.is_absolute():
        out_path = workdir / out_path
    ensure_dir(out_path.parent)
    payload = {
        "schema_version": "flow1-reviews/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": args.source,
        "direction": (plan.get("direction") or {}).get("zh") or (plan.get("topic") or {}).get("zh") or "",
        "records": records,
        "meta": {
            "total": result.get("total"),
            "candidates": result.get("candidates"),
            "status": result.get("status"),
            "stats": result.get("stats"),
        },
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.merge_into:
        merge_path = pathlib.Path(args.merge_into)
        if not merge_path.is_absolute():
            merge_path = workdir / merge_path
        existing = _load_records(merge_path)
        base_payload = {}
        if merge_path.exists():
            try:
                raw = json.loads(merge_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    base_payload = raw
            except Exception:
                base_payload = {}
        merged = _merge(existing, records)
        base_payload["records"] = merged
        merge_path.write_text(
            json.dumps(base_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 合并 {len(records)} 条 → {merge_path}（共 {len(merged)} 条）")
    print(f"[OK] 输出 {len(records)} 条 → {out_path}")
    print(f"     状态 {result.get('status')}；候选 {result.get('candidates')}；源 {args.source}")
    if not records:
        print("[X] 没有综述入选：检查方向词、--rank-policy、--min-citations 或改用 --source openalex。",
              file=sys.stderr)
        return 3
    print("下一步：python 流程1/scripts/reviews_prepare.py --input "
          f"{out_path.relative_to(workdir)} --fetch-abstracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
