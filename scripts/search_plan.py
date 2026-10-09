# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R⓪：检索计划（用户条件 → 结构化查询计划）
#
# 设计参照 academic-research-skills-3.22.2：
#   · deep-research/agents/bibliography_agent.md §"Search Strategy Framework"
#     （DATABASES / KEYWORDS / BOOLEAN / DATE RANGE / LANGUAGE / DOC TYPES 六要素）
#   · deep-research/references/systematic_review_protocol.md（PRISMA 流程）
#   · shared/query_plan.schema.json（查询计划的可追溯字段：query_id / construction /
#     language / date_filter / index_targets / query_sha256）
#
# 为什么要把「条件」变成「计划」文件：
#   PRISMA 要求检索**可复现**——同样的条件必须能重跑出同样的结果集。
#   把条件固化成带哈希的 JSON，才能事后核对「这份报告是用什么检索式查出来的」。
#
# 用法：
#   uv run python 流程1/scripts/search_plan.py \
#       --topic "肿瘤免疫微环境" --topic-en "tumor immune microenvironment" \
#       --terms-en "NK cell,macrophage,checkpoint" \
#       --from-year 2020 --doc-types review \
#       --sources europepmc,openalex,crossref,arxiv \
#       --out outputs/07_检索
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import time

from logutil import run_root as _run_root  # noqa: E402

# ── UTF-8 兜底 ──


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from logutil import get, journal  # type: ignore
except Exception:
    def get(name):                     # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)
        return _L()

    def journal(*a, **k):              # noqa: D103
        pass


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()

SCHEMA_VERSION = "flow1-search-plan/1.0"

# 各数据源的能力（照抄 bibliography_agent 的 DATABASES 要素，落到具体 API）
SOURCES = {
    "europepmc": {
        "label": "Europe PMC",
        "purpose": "生命科学与医学文献（含预印本）",
        "supports": ["boolean", "date", "doctype", "oa"],
        "max_page": 1000,
        "note": "零凭据；命中数会与分页数对账",
    },
    "openalex": {
        "label": "OpenAlex",
        "purpose": "跨学科文献图谱（含引用关系）",
        "supports": ["boolean", "date", "doctype", "oa"],
        "max_page": 200,
        "note": "零凭据；可选 OPENALEX_API_KEY 提升配额",
    },
    "crossref": {
        "label": "Crossref",
        "purpose": "DOI 注册库（覆盖最广的题录）",
        "supports": ["date", "doctype"],
        "max_page": 1000,
        "note": "零凭据；布尔检索能力弱，适合按题名/关键词粗查",
    },
    "arxiv": {
        "label": "arXiv",
        "purpose": "物理/数学/CS/量化生物预印本",
        "supports": ["boolean", "date"],
        "max_page": 2000,
        "note": "零凭据；**限速 1 请求/3 秒**，超限返回 exit 5",
    },
}

# 文献类型 → 各源的过滤表达式
DOCTYPE_MAP = {
    "review": {
        "europepmc": '(PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")',
        "openalex": "type:review",
        "crossref": "type:journal-article",
        "label": "综述",
    },
    "research": {
        "europepmc": 'NOT PUB_TYPE:"Review"',
        "openalex": "",
        "crossref": "type:journal-article",
        "label": "研究论文",
    },
    "any": {"europepmc": "", "openalex": "", "crossref": "", "label": "不限"},
}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _split_terms(s: str) -> list[str]:
    """把 "a,b；c、d" 拆成词表（去空、去重、保序）。"""
    if not s:
        return []
    out = []
    for t in re.split(r"[,，;；、|]", s):
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


def _quote_if_needed(t: str) -> str:
    """含空格/连字符的词加引号，避免被解析成两个词。"""
    t = t.strip()
    if not t:
        return t
    if re.search(r"[\s\-/]", t) and not (t.startswith('"') and t.endswith('"')):
        return f'"{t}"'
    return t


def build_query(topic_en: str, terms_en: list[str], logic: str = "OR") -> str:
    """把主题词 + 限定词拼成布尔检索式。

    结构：主题短语 AND (限定词1 OR 限定词2 …)
      主题短语是"必须命中"的锚，限定词用于收窄/扩展。
      若没有限定词，则只用主题短语。
    """
    topic = _quote_if_needed(topic_en) if topic_en else ""
    parts = [_quote_if_needed(t) for t in terms_en if t]
    if topic and parts:
        return f"{topic} AND ({f' {logic} '.join(parts)})"
    if topic:
        return topic
    return f" {logic} ".join(parts)


def build_plan(args) -> dict:
    topic_en = (args.topic_en or "").strip()
    terms_en = _split_terms(args.terms_en or "")
    sources = [s.strip() for s in (args.sources or "").split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise SystemExit(f"未知数据源 {unknown}；可选 {list(SOURCES)}")

    dt = DOCTYPE_MAP.get(args.doc_types, DOCTYPE_MAP["any"])
    core = build_query(topic_en, terms_en, args.logic)

    queries = []
    for i, src in enumerate(sources, 1):
        extra = dt.get(src, "")
        q = f"({core})" if extra and core else core
        if extra:
            q = f"{q} AND {extra}" if q else extra
        queries.append({
            "query_id": f"q{i}",
            "index_target": src,
            "index_label": SOURCES[src]["label"],
            "query_text": q,
            "construction": "topic_and_terms",
            "language": "en",
            "date_filter": {
                "from_year": args.from_year or None,
                "through_year": args.through_year or None,
            },
            "doc_types": args.doc_types,
            "oa_only": bool(args.oa_only),
            "max_records": args.max_records,
            "query_sha256": _sha(q),
        })

    plan = {
        "schema_version": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "created_by": "流程1/scripts/search_plan.py",
        # ── ① 用户给的检索条件（原始，不改写）──
        "user_input": {
            "topic_cn": args.topic or "",
            "topic_en": topic_en,
            "terms_en": terms_en,
            "from_year": args.from_year,
            "through_year": args.through_year,
            "doc_types": args.doc_types,
            "oa_only": bool(args.oa_only),
            "language": "en",
            "sources": sources,
            "max_records": args.max_records,
            "boolean_logic": args.logic,
        },
        # ── ② 编译出的检索式（每个源一条，含哈希）──
        "queries": queries,
        # ── ③ 数据源能力说明（PRISMA 要求记录检索了哪些库）──
        "provider_roster": [
            {"index_id": s, **SOURCES[s]} for s in sources
        ],
        # ── ④ 纳入/排除标准（bibliography_agent Step 3 的表）──
        "eligibility": {
            "relevance": {"include": "直接回应检索主题",
                          "exclude": "仅擦边或无关"},
            "quality": {"include": "同行评审 / 有正式编辑流程",
                        "exclude": "掠夺性期刊（见红旗清单）"},
            "currency": {"include": (f"{args.from_year} 年及以后"
                                     if args.from_year else "不限年份"),
                         "exclude": "过时（除非是该领域奠基性文献）"},
            "language": {"include": "英文", "exclude": "其他语种"},
            "availability": {"include": "开放获取优先",
                             "exclude": "仅摘要且无进一步获取途径"},
        },
        # ── ⑤ 检索可复现声明 ──
        "reproducibility": {
            "note": "同一 plan 文件重跑应得到同一结果集；"
                    "各源命中数以 paginate.py 的对账字段为准",
            "plan_sha256": "",
        },
    }
    plan["reproducibility"]["plan_sha256"] = _sha(
        json.dumps(plan["user_input"], ensure_ascii=False, sort_keys=True))
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="search_plan.py",
        description="阶段 R⓪：把用户的检索条件编译成**可复现**的结构化查询计划",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  search_plan.py --topic \"肿瘤免疫微环境\" \\\n"
                "      --topic-en \"tumor immune microenvironment\" \\\n"
                "      --terms-en \"NK cell,macrophage,checkpoint\" \\\n"
                "      --from-year 2020 --doc-types review \\\n"
                "      --out outputs/07_检索\n"))
    ap.add_argument("--topic", help="中文主题（仅记录，供报告用）")
    ap.add_argument("--topic-en", required=True, help="英文主题短语（检索锚点）")
    ap.add_argument("--terms-en", default="",
                    help="英文限定词，逗号分隔（同义词/下位概念）")
    ap.add_argument("--logic", choices=["OR", "AND"], default="OR",
                    help="限定词之间的连接符（默认 OR）")
    ap.add_argument("--from-year", type=int, help="起始年份")
    ap.add_argument("--through-year", type=int, help="截止年份")
    ap.add_argument("--doc-types", choices=list(DOCTYPE_MAP), default="any",
                    help="文献类型：review / research / any")
    ap.add_argument("--oa-only", action="store_true", help="只要开放获取")
    ap.add_argument("--sources", default="europepmc,openalex,crossref",
                    help=f"数据源，逗号分隔（可选 {list(SOURCES)}）")
    ap.add_argument("--max-records", type=int, default=50,
                    help="每个源最多取回多少条（默认 50）")
    ap.add_argument("--out", default=str(WD / "outputs" / "07_检索"),
                    help="输出目录（默认 <流程1>/<年月日>/outputs/07_检索）")
    args = ap.parse_args(argv)

    log = get("plan")
    plan = build_plan(args)

    d = pathlib.Path(args.out)
    if not d.is_absolute():
        d = WD / d
    d.mkdir(parents=True, exist_ok=True)
    p = d / "search_plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("═" * 66)
    print("[OK] 检索计划已生成")
    print("═" * 66)
    ui = plan["user_input"]
    print(f"  主题      {ui['topic_cn'] or '—'} / {ui['topic_en']}")
    print(f"  限定词    {len(ui['terms_en'])} 个：{'、'.join(ui['terms_en'][:6])}"
          + ("…" if len(ui["terms_en"]) > 6 else ""))
    print(f"  年份      {ui['from_year'] or '不限'} ~ {ui['through_year'] or '不限'}")
    print(f"  类型      {DOCTYPE_MAP[ui['doc_types']]['label']}"
          + ("（只要开放获取）" if ui["oa_only"] else ""))
    print(f"  数据源    {len(ui['sources'])} 个："
          + "、".join(SOURCES[s]["label"] for s in ui["sources"]))
    print()
    print("  各源检索式：")
    for q in plan["queries"]:
        txt = q["query_text"] or "（无过滤，按年份/类型）"
        print(f"    [{q['index_label']}] {txt[:110]}")
        print(f"        sha256 {q['query_sha256'][:16]}…")
    print()
    print(f"  计划哈希  {plan['reproducibility']['plan_sha256'][:32]}…")
    print(f"→ {p}")
    print()
    print("  下一步：search_run.py 执行检索")
    print("═" * 66)
    journal("R0", "search.plan", topic=ui["topic_en"],
            sources=len(ui["sources"]), sha=plan["reproducibility"]["plan_sha256"][:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
