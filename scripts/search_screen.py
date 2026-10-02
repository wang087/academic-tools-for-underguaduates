# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R②：两轮筛选 + PRISMA 计数 + 来源质量分级
#
# 设计参照 academic-research-skills-3.22.2：
#   · deep-research/agents/bibliography_agent.md
#       Step 3 纳入/排除标准表、Step 4 两轮筛选（题录 → 全text）、
#       Step 4.6 分布偏斜告警（阈值 ≥70% 触发 DISTRIBUTIONAL_SKEW_ADVISORY）
#   · deep-research/references/source_quality_hierarchy.md
#       证据金字塔 7 级 + 掠夺性出版红旗清单
#   · deep-research/references/systematic_review_protocol.md（PRISMA 流程）
#
# ★ 关于"自动筛选能做到什么程度"的诚实说明：
#   本脚本做的是**基于关键词的可复核预筛**，不是同行评审级别的判断。
#   · Pass 1 用题名+摘要与检索词的词面重叠打分，阈值可调，**保留命中词**
#     以便人工复核为什么被纳入/排除；
#   · 质量分级只依据**元数据**（文献类型/年份/是否 OA/DOI 是否齐全），
#     无法判断方法学质量 —— 报告里会明确标注这个局限；
#   · 掠夺性红旗只在**元数据可判**的范围内检查（本脚本不做期刊黑名单匹配）。
#   最终纳入与否应由人复核，脚本的价值在于把 150 条收敛到值得看的几十条，
#   并留下可追溯的计数。
#
# 用法：
#   uv run python 流程1/scripts/search_screen.py --raw outputs/07_检索/search_raw.json
#   uv run python 流程1/scripts/search_screen.py --raw … --threshold 0.25
#   uv run python 流程1/scripts/search_screen.py --raw … --no-backfill
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

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
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 (academic-use)")

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# ── 证据金字塔（source_quality_hierarchy.md §Evidence Pyramid）──
EVIDENCE_LEVELS = {
    "I": "系统综述 / Meta 分析",
    "II": "随机对照试验（RCT）",
    "III": "非随机对照研究",
    "IV": "病例对照 / 队列研究",
    "V": "描述性研究的系统综述",
    "VI": "单篇描述性 / 质性研究",
    "VII": "专家意见 / 委员会报告",
}

# 从**元数据**能判定的证据级别（判不了的一律归 VII 并在报告中说明）
def grade_evidence(rec: dict) -> tuple[str, str]:
    """按元数据给出证据级别。返回 (级别, 依据说明)。

    ⛔ 局限：仅凭题录无法判断方法学质量。综述/meta 分析定为 I 是
      **依据文献类型的惯例做法**，不代表该综述本身质量合格
      （source_quality_hierarchy.md 明确提醒："garbage in, garbage out"）。
    """
    t = (rec.get("type") or "").lower()
    ti = (rec.get("title") or "").lower()
    if "meta-analysis" in t or "meta-analysis" in ti:
        return "I", "题录标注为 meta 分析"
    if "systematic" in t or "systematic review" in ti:
        return "I", "题录标注为系统综述"
    if "review" in t or re.search(r"\breview\b|综述", ti):
        return "I", "题录标注为综述（按惯例列 I 级，质量需另评）"
    if "randomized" in t or "randomised" in t or "rct" in t:
        return "II", "题录标注为随机对照试验"
    if "clinical trial" in t:
        return "II", "题录标注为临床试验"
    if "cohort" in t or "case-control" in t or "observational" in t:
        return "IV", "题录标注为观察性研究"
    if "journal-article" in t or "article" in t:
        return "VI", "期刊论文（元数据未标明设计类型）"
    return "VII", "元数据不足以判定设计类型"


def predatory_flags(rec: dict) -> list[str]:
    """**元数据可判**的掠夺性出版红旗。

    ⛔ 诚实边界：source_quality_hierarchy.md 的红旗清单有 12 项，其中
      "邮件骚扰作者""72 小时录用""编委会造假"等**无法从题录判断**。
      本函数只检查能从元数据机械判定的 4 项，其余一律不猜。
    """
    flags = []
    if not rec.get("doi"):
        flags.append("无 DOI（可能未进入主流索引）")
    if not rec.get("journal"):
        flags.append("无期刊名")
    if not rec.get("year"):
        flags.append("无出版年")
    # 有 DOI 但格式不规范
    doi = rec.get("doi") or ""
    if doi and not re.match(r"^10\.\d{4,9}/", doi):
        flags.append(f"DOI 格式可疑：{doi[:40]}")
    return flags


def backfill_abstracts(recs: list[dict], log, limit: int = 0) -> dict:
    """给缺摘要的记录补摘要（查 Europe PMC，按 DOI 优先、题名兜底）。

    ★ 为什么需要：`paginate.py` 请求 Europe PMC 时用 `resultType=lite`，
      **不含摘要**（实测 149 条里仅 46 条有摘要，全部来自 OpenAlex/Crossref）。
      而两轮筛选的 Pass 1 正是靠题名+摘要，缺摘要会让主源失效。
      这里用 resultType=core 按 DOI 单篇补，是最省的补法。
    """
    todo = [r for r in recs if not r.get("abstract")]
    if limit:
        todo = todo[:limit]
    if not todo:
        return {"requested": 0, "filled": 0}

    log.info(f"  补摘要：{len(todo)} 条缺摘要，逐条查 Europe PMC …")
    filled = 0
    for i, r in enumerate(todo, 1):
        q = None
        if r.get("doi"):
            q = f'DOI:"{r["doi"]}"'
        elif r.get("title"):
            t = re.sub(r'["\[\]]', " ", r["title"])[:180]
            q = f'TITLE:"{t}"'
        if not q:
            continue
        url = (f"{EPMC}?query={urllib.parse.quote(q)}&format=json"
               f"&pageSize=1&resultType=core")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            time.sleep(0.3)
            continue
        hits = ((data.get("resultList") or {}).get("result") or [])
        if hits:
            ab = hits[0].get("abstractText") or ""
            if ab:
                import html as _h
                ab = re.sub(r"<[^>]{0,80}>", " ", _h.unescape(ab))
                r["abstract"] = re.sub(r"\s+", " ", ab).strip()
                r["abstract_source"] = "europepmc:core"
                filled += 1
        if i % 25 == 0:
            log.info(f"    [{i}/{len(todo)}] 已补 {filled}")
        time.sleep(0.25)          # 站点礼貌间隔
    return {"requested": len(todo), "filled": filled}


# ── 相关性打分 ──
_STOP = set("""a an the of in on for to with and or by from as at is are was were be been
this that these those it its their there here which who whom whose what when where how
we our you your they them he she his her not no nor but if then than so such can could
may might will would shall should do does did done have has had having also more most
other some any all both each few many much own same very s t just don now use used using
study studies research results show shows shown found indicate indicates suggest suggests
""".split())


def _tokens(s: str) -> list[str]:
    """切词：保留英文词与中文双字，去停用词。"""
    s = (s or "").lower()
    out = []
    for w in re.findall(r"[a-z][a-z\-]{2,}", s):
        if w not in _STOP and len(w) > 2:
            out.append(w)
    # 中文：按 2-gram 切（无分词器时的稳妥做法）
    cn = re.findall(r"[\u4e00-\u9fff]+", s)
    for seg in cn:
        for i in range(len(seg) - 1):
            out.append(seg[i:i + 2])
    return out


def relevance(rec: dict, anchors: set[str]) -> tuple[float, list[str]]:
    """算题名+摘要与检索锚点（主题词+限定词）的词面重叠率。

    返回 (分数, 命中词)。分数 = 命中的锚点数 / 锚点总数，
    题名命中权重加倍（题名相关通常比摘要提及更可靠）。
    """
    if not anchors:
        return 1.0, []
    ti = set(_tokens(rec.get("title") or ""))
    ab = set(_tokens(rec.get("abstract") or ""))
    hit = [a for a in anchors if a in ti or a in ab]
    ti_hit = [a for a in anchors if a in ti]
    # 加权：题名命中算 1.0，仅摘要命中算 0.6
    score = (len(ti_hit) * 1.0 + (len(hit) - len(ti_hit)) * 0.6) / len(anchors)
    return round(min(score, 1.0), 4), sorted(hit)


def screen(recs: list[dict], plan: dict, threshold: float, log) -> dict:
    """两轮筛选 + PRISMA 计数。

    Pass 1：题名+摘要相关性（阈值可调）
    Pass 2：可得性与完整性（有无摘要/DOI/年份；是否满足 OA 要求）
    """
    ui = plan.get("user_input", {})
    anchors = set(_tokens(ui.get("topic_en") or ""))
    for t in (ui.get("terms_en") or []):
        anchors |= set(_tokens(t))
    doc = ui.get("doc_types") or "any"
    from_y = ui.get("from_year")
    to_y = ui.get("through_year")
    oa_only = bool(ui.get("oa_only"))

    prisma = collections.OrderedDict()
    prisma["identified"] = len(recs)

    # ── 硬条件过滤（等同 PRISMA 的 limits）──
    step = []
    drop_year = drop_doc = drop_oa = 0
    for r in recs:
        y = r.get("year") or ""
        if from_y and y.isdigit() and int(y) < from_y:
            drop_year += 1
            continue
        if to_y and y.isdigit() and int(y) > to_y:
            drop_year += 1
            continue
        if doc == "review" and not r.get("is_review"):
            drop_doc += 1
            continue
        if oa_only and not r.get("oa"):
            drop_oa += 1
            continue
        step.append(r)
    prisma["after_limits"] = len(step)
    prisma["excluded_year"] = drop_year
    prisma["excluded_doctype"] = drop_doc
    prisma["excluded_oa"] = drop_oa

    # ── Pass 1：题录相关性 ──
    pass1, excl1 = [], []
    for r in step:
        sc, hits = relevance(r, anchors)
        r["relevance_score"] = sc
        r["relevance_hits"] = hits
        if sc >= threshold:
            pass1.append(r)
        else:
            r["exclude_reason"] = (
                f"Pass1 相关性不足（{sc:.2f} < {threshold}）")
            excl1.append(r)
    prisma["pass1_included"] = len(pass1)
    prisma["pass1_excluded"] = len(excl1)

    # ── Pass 2：可得性/完整性 ──
    pass2, excl2 = [], []
    for r in pass1:
        prob = []
        if not r.get("title"):
            prob.append("无题名")
        if not r.get("year"):
            prob.append("无出版年")
        if not r.get("abstract"):
            prob.append("无摘要（未能补到）")
        if not r.get("url") and not r.get("doi"):
            prob.append("无链接")
        r["completeness_issues"] = prob
        # Pass 2 只排除**根本无法定位**的（其余带问题保留，交人工判断）
        if "无题名" in prob or "无链接" in prob:
            r["exclude_reason"] = "Pass2 无法定位（" + "、".join(prob) + "）"
            excl2.append(r)
        else:
            pass2.append(r)
    prisma["pass2_included"] = len(pass2)
    prisma["pass2_excluded"] = len(excl2)

    # ── 分级 ──
    for r in pass2:
        lvl, why = grade_evidence(r)
        r["evidence_level"] = lvl
        r["evidence_basis"] = why
        r["predatory_flags"] = predatory_flags(r)

    return {
        "prisma": prisma,
        "included": pass2,
        "excluded": excl1 + excl2,
        "anchors": sorted(anchors),
    }


def skew_advisory(included: list[dict], log) -> list[dict]:
    """分布偏斜告警（bibliography_agent §Step 4.6）。

    阈值：单一取值占已知值的 ≥70% 时告警。分母用 **known_N**（该维度有值的条数），
    不是总数 —— 否则缺失值多时会误报。
    """
    out = []
    dims = {
        "年份分布": lambda r: r.get("year") or "",
        "文献类型分布": lambda r: r.get("type") or "",
        "期刊分布": lambda r: r.get("journal") or "",
        "证据级别分布": lambda r: r.get("evidence_level") or "",
    }
    for name, fn in dims.items():
        vals = [fn(r) for r in included if fn(r)]
        if len(vals) < 4:
            continue                      # 样本太少不评估
        c = collections.Counter(vals)
        top, n = c.most_common(1)[0]
        if n / len(vals) >= 0.70:
            out.append({
                "dimension": name,
                "concentration": f"{top} = {n}/{len(vals)} ({n/len(vals)*100:.0f}%)",
                "advisory": "这是覆盖分布信号，不是缺陷。"
                            "请判断该检索范围是否本就该如此；"
                            "如需更广，可放宽年份 / 增加数据源 / 换关键词。",
            })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="search_screen.py",
        description="阶段 R②：两轮筛选 + PRISMA 计数 + 来源质量分级",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  search_screen.py --raw outputs/07_检索/search_raw.json\n"
                "  search_screen.py --raw … --threshold 0.25 --no-backfill\n"))
    ap.add_argument("--raw", required=True, help="search_raw.json 路径")
    ap.add_argument("--plan", help="search_plan.json（默认与 raw 同目录）")
    ap.add_argument("--threshold", type=float, default=0.20,
                    help="Pass1 相关性阈值（0-1，默认 0.20）")
    ap.add_argument("--out", help="输出目录，默认与 raw 同目录")
    ap.add_argument("--no-backfill", action="store_true",
                    help="跳过摘要补齐（会显著降低 Pass1 质量）")
    ap.add_argument("--backfill-limit", type=int, default=0,
                    help="最多补多少条摘要（0=全部）")
    args = ap.parse_args(argv)

    log = get("screen")
    rp = pathlib.Path(args.raw)
    if not rp.is_absolute():
        rp = WD / rp
    if not rp.exists():
        raise SystemExit(f"✗ 找不到 {rp}；先跑 search_run.py")
    payload = json.loads(rp.read_text(encoding="utf-8"))
    recs = payload.get("records") or []

    d = pathlib.Path(args.out) if args.out else rp.parent
    if not d.is_absolute():
        d = WD / d
    d.mkdir(parents=True, exist_ok=True)

    pp = pathlib.Path(args.plan) if args.plan else (rp.parent / "search_plan.json")
    if not pp.is_absolute():
        pp = WD / pp
    plan = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
    if not plan:
        log.warn("⚠ 未找到检索计划，将用 raw 里的 user_input 兜底")

    t0 = time.time()
    log.info(f"开始筛选：{len(recs)} 条，阈值 {args.threshold}")

    bf = {"requested": 0, "filled": 0}
    if not args.no_backfill:
        bf = backfill_abstracts(recs, log, limit=args.backfill_limit)
        log.info(f"  摘要补齐：{bf['filled']}/{bf['requested']}")

    res = screen(recs, plan or {"user_input": payload.get("user_input", {})},
                 args.threshold, log)
    advisories = skew_advisory(res["included"], log)
    elapsed = time.time() - t0

    out = {
        "schema_version": "flow1-search-screened/1.0",
        "screened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "plan_sha256": payload.get("plan_sha256", ""),
        "user_input": plan.get("user_input") or payload.get("user_input", {}),
        "threshold": args.threshold,
        "abstract_backfill": bf,
        "anchors": res["anchors"],
        "prisma": res["prisma"],
        "skew_advisories": advisories,
        "included": res["included"],
        "excluded": res["excluded"],
        "per_source": payload.get("per_source", {}),
        "failures": payload.get("failures", []),
    }
    op = d / "search_screened.json"
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    pm = res["prisma"]
    print()
    print("═" * 68)
    print("✓ 筛选完成（PRISMA 计数）")
    print("═" * 68)
    print(f"  ① 各源识别记录            {pm['identified']:>5}")
    print(f"  ② 硬条件过滤后            {pm['after_limits']:>5}"
          f"   （年份 -{pm['excluded_year']} / 类型 -{pm['excluded_doctype']}"
          f" / 非 OA -{pm['excluded_oa']}）")
    print(f"  ③ Pass1 相关性通过        {pm['pass1_included']:>5}"
          f"   （排除 {pm['pass1_excluded']}）")
    print(f"  ④ Pass2 可得性通过        {pm['pass2_included']:>5}"
          f"   （排除 {pm['pass2_excluded']}）")
    print()
    inc = res["included"]
    if inc:
        lv = collections.Counter(r.get("evidence_level") for r in inc)
        print("  纳入文献的证据级别分布：")
        for k in sorted(lv):
            print(f"    {k:4} {EVIDENCE_LEVELS.get(k,'—'):28} {lv[k]:>4}")
        yrs = collections.Counter(r.get("year") for r in inc if r.get("year"))
        if yrs:
            top = sorted(yrs)[:3]
            print(f"  年份范围：{min(yrs)} ~ {max(yrs)}"
                  f"　（{len(yrs)} 个年份）")
        flagged = [r for r in inc if r.get("predatory_flags")]
        print(f"  带元数据红旗：{len(flagged)} 条"
              + ("（详见 JSON）" if flagged else ""))
    if advisories:
        print()
        print("  ⚠ 分布偏斜告警：")
        for a in advisories:
            print(f"    · {a['dimension']}：{a['concentration']}")
    print()
    print(f"  耗时 {elapsed:.1f} 秒")
    print(f"→ {op}")
    print()
    print("  下一步：search_report.py 生成 PRISMA 报告")
    print("═" * 68)
    journal("R2", "search.screen", identified=pm["identified"],
            included=pm["pass2_included"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
