#!/usr/bin/env python3
"""方向 → 数据库路由：查表 + 自检（**零网络**）。

阶段 R 选库的单一入口。表在 `templates/方向数据库路由表.json`。

    # 查某方向该跑哪些库（供阶段 R 直接消费）
    python route_databases.py --domain 机器学习与深度学习
    python route_databases.py --domain 生物信息学与基因组学 --json

    # 查表里有哪些方向
    python route_databases.py --list-domains

    # 自检（CI 用）：表结构与「研究方法与关键词总表.md」的 24 领域一致性
    python route_databases.py --self-test

设计要点（实测教训，见 SKILL §阶段 R）：
  ⛔ paginate.py **没有 --sort 开关** —— 按被引排序的语法必须写进 query 里
    （Europe PMC 用 `AND (SORT_CITED:y)`），本脚本会把它拼好给你。
  ⛔ arXiv **每 3 秒 1 个请求**，必须串行；state 字段会标出 rate_limit。
  ⛔ 全文一律走 CDP（SKILL §5.1）；本脚本只产出**元数据/摘要**层的检索计划。
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import sys


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
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

HERE = pathlib.Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent                      # 流程1/
TABLE = SKILL_ROOT / "templates" / "方向数据库路由表.json"
# 「研究方法与关键词总表.md」在**工作文件夹**（流程1/ 的上一级）
TOTALS = SKILL_ROOT.parent / "研究方法与关键词总表.md"

EXIT_OK, EXIT_USAGE, EXIT_SELFTEST = 0, 2, 3


def load_table(path: pathlib.Path = TABLE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def domains_from_totals(path: pathlib.Path = TOTALS) -> list[str]:
    """从总表抽取 `## N. 领域名` 得到权威领域清单。"""
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+\d+\.\s*(.+?)\s*$", line)
        if m:
            names.append(m.group(1))
    return names


# ── 每个库的检索式模板 ────────────────────────────────────────────
# ⛔ 各库的**语法互不通用**：PUB_TYPE:"Review" 是 Europe PMC 语法，
#    喂给 arXiv 会被当成普通词、喂给 OpenAlex 会被忽略。
#    故 review_filter 按**库类型**分别取，而不是把路由表里的串原样透传。
DEFAULT_REVIEW_FILTER = {
    "europepmc": '(PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")',
    "arxiv": '(all:"review" OR all:"survey")',
    "openalex": "type:review",
    "crossref": "",
    "biorxiv": "",
    "medrxiv": "",
}


def review_clause(api_family: str, spec_filter: str) -> str:
    """为某个库选取**该库语法**下的综述过滤子句。

    路由表里的 review_filter 是**按方向**给的（决定"什么算综述"），
    这里再按**库**翻译成对应语法；库不认识该语法时退回库级默认值。
    """
    if api_family == "europepmc":
        # 方向若给了欧洲 PMC 语法就用它，否则用默认
        if spec_filter and "PUB_TYPE" in spec_filter:
            return spec_filter
        return DEFAULT_REVIEW_FILTER["europepmc"]
    if api_family == "arxiv":
        return DEFAULT_REVIEW_FILTER["arxiv"]
    if api_family == "openalex":
        if spec_filter and spec_filter.startswith("type:"):
            return spec_filter
        return DEFAULT_REVIEW_FILTER["openalex"]
    return ""


def build_plan(db_key: str, db: dict, terms: list[str], review_filter: str) -> dict:
    """把一个库拼成可直接喂给脚本/CLI 的检索计划。"""
    joined = " OR ".join(f'"{t}"' for t in terms) if terms else "<主题词>"
    api = db.get("api", "")

    if api == "europepmc":
        rf = review_clause("europepmc", review_filter)
        query = (f"({joined}) AND ({rf}) AND (SORT_CITED:y)"
                 if db.get("citation_sort") else f"({joined}) AND ({rf})")
        return {
            "db": db_key, "api": "europepmc", "method": "paginate.py",
            "cmd": (f"python skills/paper-lookup/scripts/paginate.py --api europepmc "
                    f"--query '{query}' --max-records 12"),
            "query": query,
            "review_clause": rf,
            "citation_sorted": bool(db.get("citation_sort")),
            "delay_seconds": db.get("delay_seconds"),
            "content_level": db.get("content_level"),
            "note": "SORT_CITED:y 写在 query 内 —— paginate.py 没有 --sort 开关。",
        }

    if api == "arxiv_atom.py":
        rf = review_clause("arxiv", review_filter)
        term0 = terms[0] if terms else "<主题词>"
        arxiv_q = f'all:"{term0}" AND {rf}' if rf else f'all:"{term0}"'
        enc = (arxiv_q.replace('"', "%22").replace(" ", "+")
               .replace("(", "%28").replace(")", "%29").replace(":", "%3A"))
        url = (f"https://export.arxiv.org/api/query?"
               f"search_query={enc}&max_results=12")
        # ★ v12.1（2026-10-02）：取数由 curl 改为 Python（stdlib urllib）。见 SKILL §9.13。
        #   ① curl 走系统 TLS 栈（Schannel/SSPI）：在低完整性令牌的受限环境里
        #      `AcquireCredentialsHandle` 失败（SEC_E_NO_CREDENTIALS），整条命令直接不可用；
        #      Python 自带 OpenSSL、不经 SSPI，实测同一环境返回 200。
        #   ② 取到**文件**再由 arxiv_atom.py 读该文件 —— **不用管道、不用 shell 重定向**：
        #      PowerShell 在原生程序之间传递文本会按控制台编码解码再编码，
        #      非 ASCII 作者名会被破坏；而 _common.read_input 对文件路径是
        #      `read_bytes()` + 显式 UTF-8 解码，字节安全、与 locale 无关。
        fetch = (
            "python -c \"import urllib.request,pathlib;"
            "p=pathlib.Path('.work_cdp/arxiv_query.xml');"
            "p.parent.mkdir(parents=True,exist_ok=True);"
            f"p.write_bytes(urllib.request.urlopen(urllib.request.Request('{url}',"
            "headers={'User-Agent':'scientific-agent-skills/2.65'}),timeout=30).read())\""
        )
        return {
            "db": db_key, "api": "arxiv", "method": "arxiv_atom.py",
            "cmd": (f"{fetch} && python skills/paper-lookup/scripts/arxiv_atom.py "
                    f".work_cdp/arxiv_query.xml"),
            "query": arxiv_q,
            "review_clause": rf,
            "citation_sorted": False,
            "rate_limit_seconds": 3,
            "content_level": db.get("content_level"),
            "note": ("⛔ 限流每 3 秒 1 请求，**串行**；退出码 5 表示被限流。"
                     "取数用 Python（**不是 curl**），Atom XML 落 `.work_cdp/arxiv_query.xml`。"),
        }

    if api in ("biorxiv", "medrxiv"):
        return {
            "db": db_key, "api": api, "method": "paginate.py",
            "cmd": (f"python skills/paper-lookup/scripts/paginate.py --api {api} "
                    f"--query 2024-01-01/2026-12-31 --max-records 100"),
            "query": "日期区间（**不能按关键词检索**）",
            "citation_sorted": False,
            "delay_seconds": db.get("delay_seconds"),
            "content_level": db.get("content_level"),
            "note": "query 是日期区间；关键词需取回后**本地筛**。",
        }

    if api == "openalex":
        term0 = terms[0] if terms else "<主题词>"
        rf = review_clause("openalex", review_filter)
        qs = f"search={term0}&sort=cited_by_count:desc"
        if rf:
            qs = f"search={term0}&filter={rf}&sort=cited_by_count:desc"
        return {
            "db": db_key, "api": "openalex", "method": "paginate.py",
            "cmd": (f"python skills/paper-lookup/scripts/paginate.py --api openalex "
                    f"--query '{qs}' --max-records 12"),
            "query": qs,
            "review_clause": rf,
            "citation_sorted": True,
            "delay_seconds": db.get("delay_seconds"),
            "content_level": db.get("content_level"),
            "note": "query 是**原始参数串**，不是查询语法。",
        }

    if api == "crossref":
        term0 = terms[0] if terms else "<标题>"
        return {
            "db": db_key, "api": "crossref", "method": "paginate.py",
            "cmd": (f"python skills/paper-lookup/scripts/paginate.py --api crossref "
                    f"--query 'query.bibliographic={term0}'"),
            "query": "query.bibliographic=…",
            "citation_sorted": False,
            "content_level": db.get("content_level"),
            "note": "**核实**用途（补全卷期页、校验 DOI），不做主题发现。",
        }

    return {"db": db_key, "api": api, "method": "?", "note": "未知库类型，请检查路由表"}


def route(table: dict, domain: str, terms: list[str] | None = None) -> dict:
    """先按**方法领域**查；未命中再按**学部方向**映射；仍未命中才走兜底。"""
    terms = terms or []
    domains = table.get("domains", {})
    dbs = table.get("databases", {})
    fmap = table.get("faculty_direction_map", {})

    resolved_via, mapped_from, mapped_note = "domain", None, ""

    if domain in domains:
        spec = domains[domain]
        matched, used_fallback = True, False
    elif domain in fmap:
        # 学部方向 → 方法领域（可能多个）：取并集
        entry = fmap[domain]
        subs = [d for d in entry.get("domains", []) if d in domains]
        prim, sec, skip = [], [], []
        for sd in subs:
            s = domains[sd]
            for k in s.get("primary", []):
                if k not in prim:
                    prim.append(k)
            for k in s.get("secondary", []):
                if k not in sec and k not in prim:
                    sec.append(k)
            for k in s.get("skip", []):
                if k not in skip:
                    skip.append(k)
        # 已被 primary/secondary 选中的库，不再出现在 skip 里
        skip = [k for k in skip if k not in prim and k not in sec]
        spec = {
            "primary": prim or list(table.get("fallback", {}).get("order", ["europepmc"])),
            "secondary": sec,
            "skip": skip,
            "review_filter": domains[subs[0]].get("review_filter", 'PUB_TYPE:"Review"') if subs else 'PUB_TYPE:"Review"',
            "reason": entry.get("note", ""),
        }
        matched, used_fallback = True, False
        resolved_via, mapped_from = "faculty_direction", subs
        mapped_note = entry.get("note", "")
    else:
        fb = table.get("fallback", {})
        spec = {
            "primary": list(fb.get("order", ["europepmc"])),
            "secondary": [],
            "skip": [],
            "review_filter": 'PUB_TYPE:"Review"',
            "reason": f"**该方向未登记路由** —— 按兜底策略 ({' → '.join(fb.get('order', []))}) 执行。",
        }
        matched, used_fallback = False, True
        resolved_via = "fallback"

    rf = spec.get("review_filter", 'PUB_TYPE:"Review"')
    primary = [build_plan(k, dbs.get(k, {}), terms, rf) for k in spec.get("primary", [])]
    secondary = [build_plan(k, dbs.get(k, {}), terms, rf) for k in spec.get("secondary", [])]

    plan = {
        "domain": domain,
        "resolved_via": resolved_via,
        "mapped_to_domains": mapped_from or [],
        "matched": matched,
        "used_fallback": used_fallback,
        "reason": spec.get("reason", ""),
        "primary": primary,
        "secondary": secondary,
        "skip": spec.get("skip", []),
        "skip_reasons": {
            k: dbs.get(k, {}).get("strength", "") for k in spec.get("skip", [])
        },
        "rules": table.get("fallback", {}).get("rule", []),
        "honesty_rules": table.get("honesty_rules", []),
    }
    return plan


def print_plan(plan: dict) -> None:
    print(f"方向：{plan['domain']}")
    if plan.get("resolved_via") == "faculty_direction":
        print(f"  → 学部方向，映射到方法领域：{'、'.join(plan['mapped_to_domains'])}")
    elif plan["used_fallback"]:
        print("  ⚠ 未登记路由（既非方法领域也非学部方向）—— 使用兜底库")
    if plan["reason"]:
        print(f"  理由：{plan['reason']}")
    print()
    print("  【主力库（必跑）】")
    for p in plan["primary"]:
        print(f"    · {p['db']}  ({p.get('api')})")
        if p.get("citation_sorted"):
            print("        可按被引排序 ✓")
        if p.get("rate_limit_seconds"):
            print(f"        ⛔ 限流：每 {p['rate_limit_seconds']} 秒 1 请求，串行")
        print(f"        $ {p.get('cmd','')}")
    if plan["secondary"]:
        print()
        print("  【补充库（**主力结果 < 6 篇时**才跑）】")
        for p in plan["secondary"]:
            print(f"    · {p['db']}  ({p.get('api')})")
            print(f"        $ {p.get('cmd','')}")
    if plan["skip"]:
        print()
        print("  【明确不跑（避免无谓请求）】")
        for k in plan["skip"]:
            print(f"    · {k}")
    print()
    print("  提示：")
    for r in plan["honesty_rules"]:
        print(f"    - {r}")


def self_test() -> int:
    print("═══ 方向数据库路由表 · 自检 ═══")
    problems: list[str] = []

    if not TABLE.exists():
        print(f"✗ 路由表不存在：{TABLE}")
        return EXIT_SELFTEST
    table = load_table()

    dbs = table.get("databases", {})
    domains = table.get("domains", {})
    print(f"  库 {len(dbs)} 个 / 方向 {len(domains)} 个")

    # ① 每个方向引用的库都必须在 databases 里定义
    for dname, spec in domains.items():
        for slot in ("primary", "secondary", "skip"):
            for k in spec.get(slot, []):
                if k not in dbs:
                    problems.append(f"[{dname}] {slot} 引用了未定义的库: {k}")

    # ② primary 不得为空
    for dname, spec in domains.items():
        if not spec.get("primary"):
            problems.append(f"[{dname}] primary 为空 —— 该方向不会检索任何库")

    # ③ 同一库不得同时出现在 primary 与 skip
    for dname, spec in domains.items():
        clash = set(spec.get("primary", [])) & set(spec.get("skip", []))
        if clash:
            problems.append(f"[{dname}] 同一库既 primary 又 skip: {sorted(clash)}")

    # ④ 与「研究方法与关键词总表.md」的 24 领域对账
    totals = domains_from_totals()
    if totals:
        print(f"  总表领域 {len(totals)} 个")
        missing = [d for d in totals if d not in domains]
        extra = [d for d in domains if d not in totals]
        if missing:
            problems.append(f"总表有、路由表缺（将走兜底）: {missing}")
        if extra:
            problems.append(f"路由表有、总表无（疑为笔误）: {extra}")
        if not missing and not extra:
            print("  ✓ 与总表 24 个领域**逐项一致**")
    else:
        print(f"  ! 未找到总表（{TOTALS}），跳过领域一致性对账")

    # ⑤ 声称可按被引排序的库，必须给出排序语法或说明
    for k, d in dbs.items():
        if d.get("citation_sort") and not (d.get("sort_syntax") or d.get("api") == "europepmc"):
            problems.append(f"[库 {k}] citation_sort=true 但未给 sort_syntax")

    # ⑥ arXiv 必须带限流标注
    if "arxiv" in dbs and not dbs["arxiv"].get("rate_limit_seconds"):
        problems.append("[库 arxiv] 缺少 rate_limit_seconds（限流是硬约束）")

    # ⑦ 兜底策略必须非空
    if not table.get("fallback", {}).get("order"):
        problems.append("fallback.order 为空 —— 未登记路由的方向将无法检索")

    # ⑧ 学部方向映射：每个映射到的方法领域都必须存在
    fmap = table.get("faculty_direction_map", {})
    real_faculty = [k for k in fmap if not k.startswith("_")]
    for fname in real_faculty:
        entry = fmap[fname]
        subs = entry.get("domains", [])
        if not subs:
            problems.append(f"[学部方向 {fname}] 未映射任何方法领域")
        for sd in subs:
            if sd not in domains:
                problems.append(f"[学部方向 {fname}] 映射到未定义的方法领域: {sd}")

    # ⑨ 学部方向应与 templates/导师方向规则.json 的 12 个方向一致
    rules_path = SKILL_ROOT / "templates" / "导师方向规则.json"
    if rules_path.exists():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        real_rules = [k for k in rules if not k.startswith("_")]
        miss = [d for d in real_rules if d not in fmap]
        extra = [d for d in real_faculty if d not in real_rules]
        if miss:
            problems.append(f"导师方向规则有、映射表缺（将走兜底）: {miss}")
        if extra:
            problems.append(f"映射表有、导师方向规则无（疑为笔误）: {extra}")
        if not miss and not extra:
            print(f"  ✓ 学部方向 {len(real_rules)} 个与 导师方向规则.json **逐项一致**")
    else:
        print(f"  ! 未找到导师方向规则.json，跳过学部方向对账")

    print()
    if problems:
        print(f"✗ 自检失败，{len(problems)} 项问题：")
        for p in problems:
            print(f"    - {p}")
        return EXIT_SELFTEST
    print("✓ 自检全部通过")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(
        description="方向 → 数据库路由（阶段 R 选库单一入口，零网络）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  route_databases.py --list-domains\n"
            "  route_databases.py --domain 机器学习与深度学习\n"
            "  route_databases.py --domain 医学影像与数字病理 --terms 'segmentation' 'MRI' --json\n"
            "  route_databases.py --self-test\n"
        ),
    )
    ap.add_argument("--domain", help="方向名（见 --list-domains；未登记则走兜底）")
    ap.add_argument("--terms", nargs="*", default=[], help="主题词（拼进检索式）")
    ap.add_argument("--list-domains", action="store_true", help="列出全部已登记方向")
    ap.add_argument("--list-databases", action="store_true", help="列出全部库及其强项")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供 agent 消费）")
    ap.add_argument("--self-test", action="store_true", help="自检路由表（零网络）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    table = load_table()

    if args.list_domains:
        print("已登记的 24 个方向 → 主力库：\n")
        for d, spec in table.get("domains", {}).items():
            print(f"  {d}")
            print(f"      主力: {', '.join(spec.get('primary', [])) or '—'}"
                  f"   补充: {', '.join(spec.get('secondary', [])) or '—'}"
                  f"   不跑: {', '.join(spec.get('skip', [])) or '—'}")
        return EXIT_OK

    if args.list_databases:
        print("可用库：\n")
        for k, d in table.get("databases", {}).items():
            cs = "可按被引排序" if d.get("citation_sort") else "不能按被引排序"
            rl = f"，限流 {d['rate_limit_seconds']}s" if d.get("rate_limit_seconds") else ""
            print(f"  {k:12} [{d.get('content_level','?')}] {cs}{rl}")
            print(f"      {d.get('strength','')}")
        return EXIT_OK

    if not args.domain:
        ap.print_help()
        return EXIT_USAGE

    plan = route(table, args.domain, args.terms)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_plan(plan)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
