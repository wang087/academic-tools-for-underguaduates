# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R③：PRISMA 报告
#
# 设计参照 academic-research-skills-3.22.2：
#   · deep-research/references/systematic_review_toolkit.md §2 PRISMA 2020
#     27 项清单 + 流程图
#   · deep-research/agents/bibliography_agent.md
#     §Search Documentation (PRISMA-style)、§Step 5 Annotated Bibliography
#   · deep-research/references/source_quality_hierarchy.md
#     证据金字塔与分级（含"分级只看适配度，不看绝对设计等级"的提醒）
#
# ★ 本报告**只陈述检索与筛选过程**，不下"这个领域是什么"的结论
#   —— 领域结论由阶段 R2（subagent 并行解读）产出。
#   报告的职责是让读者能判断：这份检索可不可信、漏没漏、能不能复现。
#
# 用法：
#   uv run python 流程1/scripts/search_report.py --screened outputs/07_检索/search_screened.json
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import pathlib
import sys
import time

# ── UTF-8 兜底 ──


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", "") or "").lower()
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


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()

EVIDENCE_LEVELS = {
    "I": "系统综述 / Meta 分析",
    "II": "随机对照试验（RCT）",
    "III": "非随机对照研究",
    "IV": "病例对照 / 队列研究",
    "V": "描述性研究的系统综述",
    "VI": "单篇描述性 / 质性研究",
    "VII": "专家意见 / 委员会报告",
}

SOURCE_LABELS = {
    "europepmc": "Europe PMC", "openalex": "OpenAlex",
    "crossref": "Crossref", "arxiv": "arXiv",
    "biorxiv": "bioRxiv", "medrxiv": "medRxiv",
}


def _cell(s: str, n: int = 60) -> str:
    """表格单元格：转义竖线、压空白、截断。"""
    t = (s or "").replace("|", "／").replace("\n", " ")
    t = " ".join(t.split())
    return t[:n - 1] + "…" if len(t) > n else t


def render(sd: dict, plan: dict | None, log) -> str:
    ui = sd.get("user_input") or {}
    pm = sd.get("prisma") or {}
    inc = sd.get("included") or []
    exc = sd.get("excluded") or []
    L: list[str] = []
    A = L.append

    A(f"# 检索报告 · {ui.get('topic_cn') or ui.get('topic_en','（未命名）')}")
    A("")
    A("> 本报告描述**检索与筛选过程**（PRISMA 风格），不含领域结论。")
    A("> 领域结论见《领域概貌》。")
    A("")
    A(f"- 检索时间：{sd.get('screened_at','—')}")
    A(f"- 计划哈希：`{sd.get('plan_sha256','—')[:32]}…`")
    A(f"- 相关性阈值：{sd.get('threshold','—')}")
    A("")
    A("---")
    A("")

    # ── ① 检索策略 ──
    A("## 一、检索策略")
    A("")
    A("| 项 | 内容 |")
    A("|---|---|")
    A(f"| 主题（中文） | {_cell(ui.get('topic_cn') or '—', 80)} |")
    A(f"| 主题（英文检索锚） | {_cell(ui.get('topic_en') or '—', 80)} |")
    terms = ui.get("terms_en") or []
    A(f"| 限定词（{len(terms)} 个） | {_cell('、'.join(terms) or '—', 80)} |")
    A(f"| 年份范围 | {ui.get('from_year') or '不限'} ~ {ui.get('through_year') or '不限'} |")
    dt = {"review": "综述", "research": "研究论文", "any": "不限"}.get(
        ui.get("doc_types") or "any", "—")
    A(f"| 文献类型 | {dt} |")
    A(f"| 语言 | 英文 |")
    A(f"| 开放获取 | {'只要 OA' if ui.get('oa_only') else '不限'} |")
    srcs = ui.get("sources") or []
    A(f"| 数据源 | {'、'.join(SOURCE_LABELS.get(s, s) for s in srcs)} |")
    A("")

    if plan and plan.get("eligibility"):
        A("## 二、纳入 / 排除标准")
        A("")
        A("| 维度 | 纳入 | 排除 |")
        A("|---|---|---|")
        for k, v in plan["eligibility"].items():
            A(f"| {k} | {_cell(v.get('include',''), 46)} | {_cell(v.get('exclude',''), 46)} |")
        A("")

    # ── ③ PRISMA 流程 ──
    A("## 三、PRISMA 流程与计数")
    A("")
    A("```")
    A(f"  各数据源识别记录                    n = {pm.get('identified',0)}")
    A("        │")
    A(f"        ├─ 排除：年份不符              n = {pm.get('excluded_year',0)}")
    A(f"        ├─ 排除：文献类型不符          n = {pm.get('excluded_doctype',0)}")
    A(f"        └─ 排除：非开放获取            n = {pm.get('excluded_oa',0)}")
    A("        ▼")
    A(f"  硬条件过滤后                        n = {pm.get('after_limits',0)}")
    A("        │")
    A(f"        ├─ Pass1 题录相关性筛选        n = {pm.get('pass1_excluded',0)} 排除")
    A("        ▼")
    A(f"  Pass1 通过                          n = {pm.get('pass1_included',0)}")
    A("        │")
    A(f"        ├─ Pass2 可得性筛选            n = {pm.get('pass2_excluded',0)} 排除")
    A("        ▼")
    A(f"  **最终纳入**                        n = {pm.get('pass2_included',0)}")
    A("```")
    A("")

    # 各源贡献
    ps = sd.get("per_source") or {}
    if ps:
        A("### 各数据源实际发送的检索式")
        A("")
        A("> ⚠️ 各源 query 格式**互不相同**（Europe PMC 收检索语法、"
          "OpenAlex/Crossref 收原始参数串），下表是**真正发出**的串。")
        A("")
        A("| 数据源 | 实际检索式 | 计划哈希 |")
        A("|---|---|---|")
        plan_q = {q.get("index_target"): q.get("query_sha256", "")
                  for q in (plan or {}).get("queries", [])}
        for s, st in ps.items():
            lbl = SOURCE_LABELS.get(s, s)
            aq = st.get("actual_query") or "（该源失败，未发送）"
            A(f"| {lbl} | `{_cell(aq, 110)}` | `{plan_q.get(s,'')[:12]}…` |")
        A("")

        A("### 各数据源贡献")
        A("")
        A("| 数据源 | 取回 | 该库命中总数 | 完整性 |")
        A("|---|---|---|---|")
        for s, st in ps.items():
            lbl = SOURCE_LABELS.get(s, s)
            tot = f"{st.get('expected_total'):,}" if st.get("expected_total") else "—"
            comp = ("完整" if st.get("complete") else "部分") \
                if st.get("status") == "ok" else "**失败**"
            A(f"| {lbl} | {st.get('retrieved',0)} | {tot} | {comp} |")
        A("")

    # 失败源
    fails = sd.get("failures") or []
    if fails:
        A("### ⚠️ 失败的数据源")
        A("")
        A("| 数据源 | 原因 |")
        A("|---|---|")
        for f in fails:
            A(f"| {SOURCE_LABELS.get(f.get('source'), f.get('source'))} | "
              f"{_cell(f.get('reason',''), 70)} |")
        A("")
        A("> 一个源失败不影响其余源的结果（graceful degradation）；")
        A("> 但**覆盖会因此不完整**，解读时需注意。")
        A("")

    # ── ④ 纳入文献 ──
    A(f"## 四、纳入文献（{len(inc)} 篇）")
    A("")
    if not inc:
        A("_无_")
        A("")
    else:
        A("| # | 年份 | 题名 | 期刊 | 证据级别 | 来源 | DOI |")
        A("|---|---|---|---|---|---|---|")
        for i, r in enumerate(sorted(inc, key=lambda x: (-(int(x["year"]) if (x.get("year") or "").isdigit() else 0), x.get("title") or "")), 1):
            doi = r.get("doi") or ""
            dl = f"[{doi[:26]}](https://doi.org/{doi})" if doi else "—"
            srcl = "/".join(SOURCE_LABELS.get(s, s) for s in (r.get("sources") or []))
            A(f"| {i} | {r.get('year') or '—'} | {_cell(r.get('title'), 72)} | "
              f"{_cell(r.get('journal'), 28)} | {r.get('evidence_level','—')} | "
              f"{_cell(srcl, 22)} | {dl} |")
        A("")

    # 证据级别分布
    if inc:
        lv = collections.Counter(r.get("evidence_level") for r in inc)
        A("### 证据级别分布")
        A("")
        A("| 级别 | 说明 | 篇数 |")
        A("|---|---|---|")
        for k in sorted(lv):
            A(f"| {k} | {EVIDENCE_LEVELS.get(k,'—')} | {lv[k]} |")
        A("")

    # 元数据红旗
    flagged = [r for r in inc if r.get("predatory_flags")]
    if flagged:
        A(f"### ⚠️ 元数据红旗（{len(flagged)} 篇）")
        A("")
        A("| 题名 | 红旗 |")
        A("|---|---|")
        for r in flagged[:25]:
            A(f"| {_cell(r.get('title'), 62)} | {_cell('、'.join(r['predatory_flags']), 50)} |")
        if len(flagged) > 25:
            A(f"| _（其余 {len(flagged)-25} 篇见 JSON）_ | |")
        A("")
        A("> ⛔ **这不是掠夺性期刊判定。** 仅依据可机械判定的元数据缺失"
          "（无 DOI / 无期刊名 / 无年份）。"
          "《来源质量分级》清单里的多数红旗（邮件骚扰、72 小时录用、编委会造假等）"
          "**无法从题录判断**，本流程不做猜测。")
        A("")

    # 偏斜告警
    adv = sd.get("skew_advisories") or []
    if adv:
        A("### 分布偏斜告警")
        A("")
        for a in adv:
            A(f"- **{a['dimension']}**：{a['concentration']}")
            A(f"  - {a['advisory']}")
        A("")
        A("> 这是覆盖分布信号，**不是缺陷**；"
          "如果检索范围本来就是刻意收窄的（例如只要综述），可以照常使用。")
        A("")

    # ── ⑤ 排除文献 ──
    if exc:
        A(f"## 五、排除文献及原因（{len(exc)} 篇）")
        A("")
        by_reason = collections.Counter(
            (r.get("exclude_reason") or "未标注").split("（")[0] for r in exc)
        A("| 排除原因 | 篇数 |")
        A("|---|---|")
        for k, v in by_reason.most_common():
            A(f"| {k} | {v} |")
        A("")
        A("<details>")
        A(f"<summary>展开查看被排除的 {len(exc)} 篇</summary>")
        A("")
        A("| 年份 | 题名 | 排除原因 |")
        A("|---|---|---|")
        for r in exc[:200]:
            A(f"| {r.get('year') or '—'} | {_cell(r.get('title'), 66)} | "
              f"{_cell(r.get('exclude_reason'), 44)} |")
        if len(exc) > 200:
            A(f"| | _（其余 {len(exc)-200} 篇见 JSON）_ | |")
        A("")
        A("</details>")
        A("")

    # ── ⑥ 局限 ──
    A("## 六、本检索的局限")
    A("")
    A("1. **各源结果均被 `--max-records` 截断**（见「各数据源贡献」表的"
      "「完整性」列，标「部分」即未取全）。**本报告不是该领域的穷尽检索。**")
    A("2. **Pass1 相关性是词面重叠打分，不是语义判断。** "
      "用词不同但含义相同的文献可能被漏掉；阈值见报告开头，"
      "调整阈值会改变纳入集。")
    A("3. **证据级别仅依据元数据**（文献类型标注），"
      "**不代表该文献本身质量合格**。"
      "《来源质量分级》提醒：「综述的质量取决于它纳入的研究」。")
    A("4. **摘要缺失会影响 Pass1**。缺摘要的记录只能靠题名打分，"
      f"本轮的摘要补齐情况：{sd.get('abstract_backfill',{}).get('filled',0)}"
      f"/{sd.get('abstract_backfill',{}).get('requested',0)}。")
    failn = len(sd.get("failures") or [])
    if failn:
        A(f"5. **有 {failn} 个数据源检索失败**，覆盖不完整（见上表）。")
    A("")
    A("---")
    A("")
    A("> **下一步**：把纳入文献交给阶段 R2（并行 subagent）逐篇解读，")
    A("> 产出解读卡后用 `reviews_build.py` 汇总成《领域概貌》。")
    A("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="search_report.py",
        description="阶段 R③：生成 PRISMA 风格检索报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  search_report.py --screened outputs/07_检索/search_screened.json\n")
    ap.add_argument("--screened", required=True, help="search_screened.json 路径")
    ap.add_argument("--plan", help="search_plan.json（默认同目录）")
    ap.add_argument("--out", help="输出目录，默认与 screened 同目录")
    ap.add_argument("--name", default="检索报告.md", help="输出文件名")
    args = ap.parse_args(argv)

    log = get("report")
    sp = pathlib.Path(args.screened)
    if not sp.is_absolute():
        sp = WD / sp
    if not sp.exists():
        raise SystemExit(f"✗ 找不到 {sp}；先跑 search_screen.py")
    sd = json.loads(sp.read_text(encoding="utf-8"))

    d = pathlib.Path(args.out) if args.out else sp.parent
    if not d.is_absolute():
        d = WD / d
    d.mkdir(parents=True, exist_ok=True)

    pp = pathlib.Path(args.plan) if args.plan else (sp.parent / "search_plan.json")
    if not pp.is_absolute():
        pp = WD / pp
    plan = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else None

    text = render(sd, plan, log)
    op = d / args.name
    op.write_text(text, encoding="utf-8")

    pm = sd.get("prisma") or {}
    print()
    print("═" * 68)
    print("✓ 检索报告已生成")
    print("═" * 68)
    print(f"  识别 {pm.get('identified',0)} → 硬条件后 {pm.get('after_limits',0)}"
          f" → Pass1 {pm.get('pass1_included',0)}"
          f" → **纳入 {pm.get('pass2_included',0)}**")
    print(f"  报告 {len(text.splitlines())} 行，{len(text)//1024} KB")
    print(f"→ {op}")
    print("═" * 68)
    journal("R3", "search.report", included=pm.get("pass2_included", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
