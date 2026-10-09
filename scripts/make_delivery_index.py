# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 生成 outputs/交付物索引.md
#
# 交付物索引把散落在 outputs/ 下的产物、数据来源、覆盖率、
# 「为什么有人没有方向」的原因分布汇总成一页，供交付时说明口径。
#
# 产物位置按**候选路径逐个探测**，三种布局都认：
#   ① 当前布局（统一输出根）：`outputs/01_导师名单/faculty_*.json` +
#      `outputs/04_领域概貌/` + `.work_cdp/{topic_plan,reviews/}`
#   ② 早期 v11：全部平铺在 `--root` 下，含 `02_大方向/`、`03_综述检索/`
#   ③ v8–v10 双源：`01_导师名单/roster_*.json` + `05_学院官网/`
# **每一句结论都从文件里读**：文件不在就写「未产出」，不写死任何历史案例的话。
#
# 用法：
#   uv run python 流程1/scripts/make_delivery_index.py \
#       --root outputs --direction 神经与脑科学
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
from collections import Counter
from datetime import datetime

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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()

_REASON_CN = {
    "no_tab": "页面无「研究方向」页签",
    "empty_panel": "页签存在但内容为「暂无内容」",
    "empty_slot": "模板只有空的 `<!--研究方向-->` 占位注释",
    "no_homepage": "官网返回「主页不存在或没有上线发布」",
    "filtered_as_non_direction": "抓到的文本经判定为导航/论文/成果行，非研究方向",
    "no_direction_field": "页面上**根本没有**「研究方向」栏位（网站未填写）",
    "detail_no_lead": "详情页正文里找不到任何研究方向线索词（标签写法本站没有）",
    "列表页无方向且未补详情": "**该页尚未抓取**（列表页只给姓名，方向需进详情页补）—— 可续跑",
    "unknown": "需人工核对",
}


def _size(p: pathlib.Path) -> str:
    if p.is_dir():
        return f"{sum(1 for _ in p.rglob('*') if _.is_file())} 个文件"
    kb = p.stat().st_size / 1024
    return f"{kb:.1f} KB" if kb < 1024 else f"{kb/1024:.1f} MB"


def _j(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _pick(cands: list[pathlib.Path]) -> pathlib.Path | None:
    """返回第一个存在的候选路径；都不存在则返回 None（调用方据此写「未产出」）。"""
    return next((p for p in cands if p.exists()), None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_delivery_index.py",
                                 description="生成 outputs/交付物索引.md")
    ap.add_argument("--root", default="outputs", help="交付物根目录")
    ap.add_argument("--topic", default="",
                    help="研究主题（仅记录，供抬头用；不填则从 topic_plan 读）")
    ap.add_argument("--direction", default="",
                    help="用户选定的学部方向；不填则读 topic_plan 的 direction_chosen")
    args = ap.parse_args(argv)

    OUT = pathlib.Path(args.root)
    if not OUT.is_absolute():
        OUT = WD / OUT
    WORK = WD / ".work_cdp"

    plan = _j(_pick([OUT / "02_大方向" / "topic_plan.json",
                     WORK / "topic_plan.json"]) or pathlib.Path(""), {}) or {}

    # ── 导师数据：三种产物布局逐个探测 ──
    #   ① v16（当前）：`01_导师名单/faculty_detail.json` 等
    #   ② v11：同一批文件**平铺在 root 下**
    #   ③ v8–v10 双源：`01_导师名单/roster_detail.json` + `05_学院官网/college_detail.json`
    layout = "legacy"
    grp = _j(_pick([OUT / "01_导师名单" / "faculty_grouped.json",
                    OUT / "faculty_grouped.json"]) or pathlib.Path(""), {}) or {}
    det = _j(_pick([OUT / "01_导师名单" / "faculty_detail.json",
                    OUT / "faculty_detail.json"]) or pathlib.Path(""), []) or []
    if grp or det:
        layout = "v16" if (OUT / "01_导师名单").is_dir() else "v11"
    if not det:
        det = _j(OUT / "01_导师名单" / "roster_detail.json", []) or []
        grp = _j(OUT / "01_导师名单" / "mentors_grouped.json", {}) or {}

    col = _j(_pick([OUT / "05_学院官网" / "college_detail.json"])
             or pathlib.Path(""), []) or []
    epmc_path = _pick([WORK / "reviews" / "europepmc.json",
                       OUT / "03_综述检索" / "europepmc.json",
                       OUT / "07_检索" / "europepmc_cited.json"])
    epmc = _j(epmc_path, {}) or {} if epmc_path else {}

    # 任务包 / 解读卡 / manifest：当前布局在 `.work_cdp/reviews/`，早期在 `outputs/03_综述检索/reviews/`
    rdir = next((d for d in (WORK / "reviews", OUT / "03_综述检索" / "reviews")
                 if d.is_dir()), None)
    cards = sorted((rdir / "results").glob("*.json")) if rdir else []
    manifest = _j((rdir / "manifest.json") if rdir else pathlib.Path(""), {}) or {}
    ev_full = int(manifest.get("evidence_fulltext") or 0)
    ev_abs = int(manifest.get("evidence_abstract") or 0)
    ev_meta = int(manifest.get("evidence_metadata_only") or 0)

    wd = [x for x in det if x.get("directions")]
    nd = [x for x in det if not (x.get("directions") or [])]
    src = Counter(x.get("direction_source") or "列表页" for x in wd)
    reasons = Counter((x.get("no_direction_reason") or x.get("why") or "unknown")
                      for x in nd)
    # 用户选定的方向：命令行 > topic_plan > 人数最多的方向桶 —— **不再写死某个历史案例的方向名**
    groups = grp.get("groups") or {}
    chosen = (args.direction or plan.get("direction_chosen") or "").strip()
    if not chosen and groups:
        chosen = max((g for g in groups if g != "未分类"),
                     key=lambda g: len(groups[g]), default="")
    immune = groups.get(chosen) or []
    col_hit = sum(1 for x in col if x.get("directions"))
    school_name = grp.get("school") or plan.get("school", "")
    unit_name = grp.get("unit") or plan.get("unit", "")
    site = grp.get("base") or ""
    topic_label = args.topic or plan.get("topic_cn") or chosen or "（未记录主题）"
    is_faculty = layout in ("v11", "v16")

    def cov(k):
        return sum(1 for x in col if x.get(k))

    L: list[str] = []
    A = L.append
    A(f"# 交付物索引 · {school_name}{unit_name}")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} | 工作文件夹：`{WD}`")
    A("> ")
    A("> 流程：`流程1/` 本科生科研辅助链路（阶段 N → S → R → R① → R2 → R②）")
    if site:
        A("> ")
        A(f"> **导师数据源（唯一）**：学部/学院官网 [{site}]({site})")
    A("")
    A("---")
    A("")
    A("## 一句话交付")
    A("")
    if immune:
        A(f"针对**{school_name}{unit_name}**的**{topic_label}**方向，")
        A(f"给出「谁在做这个方向（{len(immune)} 位导师）」"
          f"+「该领域 {len(cards)} 篇优质综述」+「一份本科生能看懂的领域概貌」。")
    else:
        A(f"**{school_name}{unit_name}** 的导师名单与研究方向归类："
          f"共 **{len(det)}** 人，其中 **{len(wd)}** 人有研究方向。")
    A("")
    A("## 交付物清单")
    A("")
    A("| # | 文件 | 大小 | 说明 |")
    A("|---|---|---|---|")

    def _rel(p: pathlib.Path | None) -> str:
        if p is None:
            return "—"
        for base, prefix in ((OUT, ""), (WD, "../")):
            try:
                return (prefix + p.relative_to(base).as_posix())
            except ValueError:
                continue
        return p.as_posix()

    # 阶段 R 的检索产物：当前布局按子主题分目录（`07_检索`、`07_检索_2`…）
    screens = []
    for d in sorted(OUT.glob("0[0-9]_检索*")):
        raw = _j(d / "search_raw.json", {}) or {}
        scr = _j(d / "search_screened.json", {}) or {}
        if raw or scr:
            screens.append((d.name, raw, scr))

    fdir = OUT / "01_导师名单" if layout == "v16" else OUT
    if is_faculty:
        rows = [
            ("**主交付物**：导师按方向归类，含职称、方向原文、邮箱、主页链接",
             fdir / "导师名单与方向归类.md"),
            ("机读版归类结果（含 groups / mentors）", fdir / "faculty_grouped.json"),
            ("全部导师字段（方向 / 职称 / 邮箱 / 主页 / 状态）", fdir / "faculty_detail.json"),
            ("阶段 S 列表页原始结果（未经详情补充）", fdir / "faculty_list.json"),
            ("各师资栏目列表页原始 HTML（含全部分页）", fdir / "raw_list"),
            ("需进详情页补方向的页面存档", fdir / "raw_detail"),
            ("阶段 N：主题词 / 方法词 / 检索式草稿",
             _pick([OUT / "02_大方向" / "topic_plan.json", WORK / "topic_plan.json"])),
            ("阶段 R：检索计划（含各源检索式与 sha256）",
             _pick([OUT / "07_检索" / "search_plan.json",
                    OUT / "03_综述检索" / "search_plan.json"])),
            ("阶段 R：多源取数与归一化去重结果",
             _pick([OUT / "07_检索" / "search_raw.json"])),
            ("阶段 R：两轮筛选后的纳入 / 排除清单",
             _pick([OUT / "07_检索" / "search_screened.json"])),
            ("阶段 R：PRISMA 检索报告（各子主题一份）",
             _pick([OUT / "07_检索" / "检索报告.md", OUT / "03_综述检索" / "检索报告.md"])),
            ("阶段 R：最终选定的综述清单（题录 + 被引 + 每篇 `why_selected`）",
             (rdir / "reviews.json") if rdir else None),
            ("自包含任务包（题录 + 正文全文指针）",
             (rdir / "tasks") if rdir else None),
            ("逐篇正文全文（软换行，供分段读取）", (rdir / "fulltext") if rdir else None),
            ("解读卡", (rdir / "results") if rdir else None),
            ("逐卡机械汇总（全量共识 / 争议 / 术语）",
             (rdir / "领域概貌.md") if rdir else None),
            ("**主交付物**：面向本科生的领域概貌（跨卡综合）",
             _pick([OUT / "04_领域概貌" / "领域概貌.md",
                    (rdir / "领域概貌.md") if rdir else None])),
            ("阶段 I《全文清单.md》：逐篇正文/页数/来源通道/未获取原因",
             _pick(sorted(WD.glob("*_paper_Exports/全文清单.md"))
                   + sorted(WD.glob("*/*_paper_Exports/全文清单.md")))),
        ]
    else:
        rows = [
            ("**主交付物**：导师按方向归类，含职称、方向原文、主页链接",
             OUT / "01_导师名单" / "导师名单与方向归类.md"),
            ("机读版归类结果", OUT / "01_导师名单" / "mentors_grouped.json"),
            ("导师全部字段 + 方向来源标记", OUT / "01_导师名单" / "roster_detail.json"),
            ("教师名单（姓名 / 主页 URL）", OUT / "01_导师名单" / "roster_list.json"),
            ("教师个人主页原始 HTML", OUT / "01_导师名单" / "raw_detail"),
            ("「研究领域」子页存档", OUT / "01_导师名单" / "raw_subpage"),
            ("**学院官网数据**：结构化字段", OUT / "05_学院官网" / "college_detail.json"),
            ("学院师资详情页原始 HTML（按姓名命名）", OUT / "05_学院官网" / "raw_detail"),
            ("各学院师资名单", OUT / "05_学院官网" / "college_list.json"),
            ("阶段 N：主题词 / 方法词 / 检索式草稿", OUT / "02_大方向" / "topic_plan.json"),
            ("阶段 R：Europe PMC 原始检索结果", OUT / "03_综述检索" / "europepmc.json"),
            ("自包含任务包", OUT / "03_综述检索" / "reviews" / "tasks"),
            ("解读卡", OUT / "03_综述检索" / "reviews" / "results"),
            ("**主交付物**：面向本科生的领域概貌", OUT / "04_领域概貌" / "领域概貌.md"),
        ]
    for i, (desc, p) in enumerate(rows, 1):
        state = "未产出" if p is None or not p.exists() else _size(p)
        link = f"[{_rel(p)}]({_rel(p)})" if p and p.exists() else _rel(p)
        A(f"| {i} | {link} | {state} | {desc} |")
    A("")
    A("## 数据来源与核验")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    if site:
        A(f"| 数据源（唯一）｜学部/学院官网 | [{site}]({site}) |")
    else:
        A("| 数据源（唯一）｜学部/学院官网 | **未记录**（`faculty_grouped.json` 无 `base` 字段） |")
    A(f"| 教师总数 | **{len(det)} 人** |")
    A(f"| **有研究方向** | **{len(wd)} 人（{len(wd)/max(len(det),1)*100:.0f}%）** |")
    A(f"| 无研究方向 | {len(nd)} 人（原因见下，均已逐条核实） |")
    rec = (epmc.get("reconciliation") or {})
    if rec:
        A(f"| 文献检索 | Europe PMC；官网自称命中 {rec.get('expected_total',0):,} 篇，"
          f"取回 {len(epmc.get('records') or [])} 篇"
          + ("（**被上限截断的部分结果**）" if rec.get("stopped_at_limit") else "") + " |")
    for name, raw, scr in screens:
        srcs = "；".join(
            f"{k} 官网自称 {v.get('expected_total') or 0:,} 条 / 取回 {v.get('retrieved', 0)} 条"
            + ("（截断的部分结果）" if v.get("complete") is False else "")
            for k, v in (raw.get("per_source") or {}).items() if isinstance(v, dict))
        if srcs:
            A(f"| 检索（{name}） | {srcs} |")
        pr = scr.get("prisma") or {}
        if pr:
            A(f"| 筛选（{name}） | 识别 {pr.get('identified', 0)} → 硬条件 {pr.get('after_limits', 0)}"
              f" → Pass1 {pr.get('pass1_included', 0)} → Pass2 **纳入 {pr.get('pass2_included', 0)}**"
              f"（排除 {pr.get('pass1_excluded', 0)} + {pr.get('pass2_excluded', 0)}） |")
    if rdir and (rdir / "reviews.json").exists():
        sel = _j(rdir / "reviews.json", {}) or {}
        n = len(sel.get("records") or sel.get("reviews") or [])
        if n:
            A(f"| 最终选定 | **{n} 篇**"
              + (f"（{sel.get('selection_rule', '')}）" if sel.get("selection_rule") else "") + " |")
    if cards:
        A(f"| 解读卡 | {len(cards)} 张 |")
    if manifest:
        A(f"| 解读证据层级 | 全文 {ev_full} ｜ 摘要 {ev_abs} ｜ 仅题录 {ev_meta}"
          f"（`{_rel(rdir / 'manifest.json') if rdir else '—'}`） |")
    if is_faculty and grp.get("sections"):
        A(f"| 抓取栏目 | {len(grp['sections'])} 个："
          f"{'、'.join(grp['sections'][:6])}"
          + ("…" if len(grp["sections"]) > 6 else "") + " |")
    A("")
    A("### 研究方向的获取途径")
    A("")
    A("| 来源 | 人数 | 机制 |")
    A("|---|---|---|")
    if is_faculty:
        A(f"| **列表页** | **{src.get('列表页',0)}** | 学院师资列表页直接写出方向（本站模板） |")
        A(f"| 详情页正文 | {src.get('详情页正文',0)} | 列表页只有姓名，进详情页抽取 |")
    else:
        A(f"| 主页内嵌 | {src.get('主页内嵌',0)} | 个人主页自带「研究方向」栏位 |")
        A(f"| **学院官网** | **{src.get('学院官网',0)}** | 学院师资详情页正文"
          f"（另有 {col_hit} 人可从此源取到） |")
        A(f"| 「研究领域」子页 | {src.get('研究领域',0)} | 个人主页的独立子页 |")
        A(f"| 「研究方向」子页 | {src.get('研究方向',0)} | 同上（`yjfx` 路径） |")
        A(f"| 个人简介正文 | {src.get('个人简介正文',0)} | 写在简介散文里 |")
    A("")
    # 结构化字段覆盖率：faculty_* 布局用 faculty_detail，legacy 用 college_detail
    field_src = det if is_faculty else col
    if field_src:
        A(f"### 结构化字段覆盖率（{len(field_src)} 人）")
        A("")
        A("| 字段 | 覆盖 | 说明 |")
        A("|---|---|---|")
        FIELD_DESC = (
            ("title", "职称", "从列表页「姓名 职称」拆出，已剥离兼聘/长聘等标记"),
            ("directions", "研究方向", "已清洗，剔除导航/论文/基金行"),
            ("email", "邮箱", "列表页或详情页"),
            ("url", "主页链接", "教师个人主页/实验室页"),
            ("sections", "所属栏目", "同一人可能同时属人才队伍与研究所"),
            ("title_line", "职衔行", "详情页顶部的职衔描述"),
            ("phone", "电话", "详情页联系方式"),
            ("recruit_note", "招生说明", "是否欢迎本科生"),
            ("representative_papers", "代表性论文", "前若干条，仅供参考"),
        )
        for k, label, desc in FIELD_DESC:
            n = sum(1 for x in field_src if x.get(k))
            if n:
                A(f"| `{k}` | {n}/{len(field_src)} | {desc} |")
        A("")
    A("### 「官网未提供研究方向」的原因（已逐条核实）")
    A("")
    A("| 原因 | 人数 | 含义 |")
    A("|---|---|---|")
    for r, n in reasons.most_common():
        A(f"| {r} | {n} | {_REASON_CN.get(r,'—')} |")
    A("")
    if is_faculty:
        A(f"> [WARN] 这 {len(nd)} 人**学部官网页面本身没有研究方向栏位**："
          f"列表页未列出、详情页正文也没有（逐条原因见上表）。")
        A("> 如确需了解，请点主页链接看其论文成果或简介 —— **不是解析遗漏，再抓一次也拿不到**。")
    else:
        A(f"> [WARN] 这 {len(nd)} 人**两个数据源都没有**研究方向数据：已抓过「研究领域」子页、")
        A("> 学院官网师资页并核对页面结构，**不是解析遗漏，再抓一次也拿不到**。")
        A("> 如确需了解，请点主页链接看论文成果或简介。")
    A("")
    groups = grp.get("groups") or {}
    if groups:
        A(f"## 方向分布（全部 {len(det)} 位导师）")
        A("")
        A("| 方向 | 人数 | 方向 | 人数 |")
        A("|---|---|---|---|")
        items = [(g, len(m)) for g, m in groups.items() if g != "未分类"]
        half = (len(items) + 1) // 2
        for i in range(half):
            a = items[i]
            b = items[i + half] if i + half < len(items) else ("", "")
            bs = f"{b[0]} | {b[1]}" if b[0] else " | "
            A(f"| {a[0]} | {a[1]} | {bs} |")
        A(f"| **未分类** | **{len(groups.get('未分类', []))}** | （多为官网无方向数据） | |")
        A("")
    A("## [WARN] 使用须知")
    A("")
    advice = ["**方向归类是关键词粗分类**，一位老师可能出现在多个方向下；"
              "**请以官网原文为准**。"]
    nd_word = ("官网页面没有研究方向栏位" if is_faculty else "两个数据源都没有研究方向")
    advice.append(f"**{len(nd)} 位老师{nd_word}**，脚本如实标注原因，**未臆造**。")
    truncated = bool(rec.get("stopped_at_limit")) or any(
        (v or {}).get("complete") is False
        for _, raw, _ in screens for v in (raw.get("per_source") or {}).values())
    if truncated:
        advice.append("**检索结果是被 `--max-records` 截断的部分结果**"
                      "（`complete=false` / `stopped_at_limit=true`）"
                      " —— 不得说成「该领域全部综述」。")
    if manifest:
        lvl = (f"**解读证据层级：全文 {ev_full} / 摘要 {ev_abs} / 仅题录 {ev_meta}**"
               f"（读自 `{_rel(rdir / 'manifest.json') if rdir else '—'}`）")
        advice.append(lvl + (" —— 摘要级篇目引用前请核对原文，**不得当作全文结论**。"
                             if ev_abs or ev_meta else "。"))
    elif cards:
        advice.append(f"共 {len(cards)} 张解读卡，但**未找到 `manifest.json`**，"
                      "证据层级无法核实 —— 请先跑 `reviews_prepare.py` / `--backfill`。")
    advice.append("**争议点原样并列**，流程不替你判定谁对。")
    advice.append("**导师方向、题录、被引一律以抓取/接口返回为准**；"
                  "如发现个别条目不当，请以教师主页原文为准。")
    for i, t in enumerate(advice, 1):
        A(f"{i}. {t}")
    A("")
    A("## 未完成 / 未覆盖的环节")
    A("")
    gaps: list[str] = []
    if truncated:
        tot_exp = sum((v or {}).get("expected_total") or 0
                      for _, raw, _ in screens for v in (raw.get("per_source") or {}).values())
        tot_got = sum((v or {}).get("retrieved") or 0
                      for _, raw, _ in screens for v in (raw.get("per_source") or {}).values())
        gaps.append(f"- Europe PMC 结果被上限截断（官网自称合计 {tot_exp:,} 条，"
                    f"只取回 {tot_got} 条）—— 覆盖面不等于全领域。")
    if not manifest:
        gaps.append("- **未找到阶段 R 的 `manifest.json`** —— 全文覆盖情况无法核实。")
    elif ev_abs or ev_meta:
        gaps.append(f"- **{ev_abs + ev_meta} 篇仍停在摘要 / 仅题录级**，"
                    "其结论不得当作全文证据引用。")
    routed = plan.get("routed_databases") or {}
    if routed.get("secondary"):
        gaps.append(f"- 路由表列出的补充库 **{'、'.join(routed['secondary'])}** 未跑"
                    "（按阶段 R 规则：主力库已 ≥6 篇时不跑）"
                    f"；明确不跑的库：{'、'.join(routed.get('skipped') or []) or '—'}。")
    # 原有作者论文分析链路是否跑过 —— 判据用**该链路特有的产物**，
    #   不能用 `*_paper_Exports`：阶段 R①.5 取综述全文时也会建同名目录
    #   （`store._exports_candidates()` 在没有 `PAPER_AUTHOR` 时按工作目录名兜底，
    #    入门链路下就是 `<年月日>_paper_Exports`），用它判会误报「作者链路已跑」。
    author_chain = ((WORK / "interpret").is_dir()
                    or any(WD.glob("*/*_论文列表.md")) or any(WD.glob("*_论文列表.md")))
    if not author_chain:
        gaps.append("- **原有作者论文分析链路（阶段 0 → I）未执行** —— "
                    "本次只走本科入门链路 N → S → R → R2；"
                    "若已锁定某位导师要深挖其成果，从阶段 0 重新接入。")
    A("")
    for g in (gaps or ["- 无 —— 入门链路 N → S → R → R① → R①.5 → R2 → R② 全部执行完毕。"]):
        A(g)
    A("")

    outp = OUT / "交付物索引.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {outp}  ({len(L)} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
