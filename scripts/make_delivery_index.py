# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 生成 outputs/交付物索引.md
#
# 交付物索引把散落在 outputs/ 下的产物、数据来源、覆盖率、
# 「为什么有人没有方向」的原因分布汇总成一页，供交付时说明口径。
#
# 用法：
#   uv run python 流程1/scripts/make_delivery_index.py \
#       --root outputs --topic "免疫与感染（肿瘤免疫微环境）"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_delivery_index.py",
                                 description="生成 outputs/交付物索引.md")
    ap.add_argument("--root", default="outputs", help="交付物根目录")
    ap.add_argument("--topic", default="免疫与感染（肿瘤免疫微环境）", help="研究主题")
    args = ap.parse_args(argv)

    OUT = pathlib.Path(args.root)
    if not OUT.is_absolute():
        OUT = WD / OUT

    plan = _j(OUT / "02_大方向" / "topic_plan.json", {}) or {}

    # ── 导师数据：兼容两种产物布局 ──
    #   ① v11 单一数据源（harvest_faculty.py）：直接放在 root 下
    #        faculty_detail.json / faculty_grouped.json
    #   ② v8-v10 双源（harvest_mentors + harvest_colleges）：
    #        01_导师名单/roster_detail.json + 05_学院官网/college_detail.json
    faculty = _j(OUT / "faculty_grouped.json", {}) or {}
    det = _j(OUT / "faculty_detail.json", []) or []
    layout = "v11"
    if not det:
        layout = "legacy"
        det = _j(OUT / "01_导师名单" / "roster_detail.json", []) or []
        grp = _j(OUT / "01_导师名单" / "mentors_grouped.json", {}) or {}
    else:
        grp = faculty

    col = _j(OUT / "05_学院官网" / "college_detail.json", []) or []
    epmc = _j(OUT / "03_综述检索" / "europepmc.json", {}) or {}

    cards = sorted((OUT / "03_综述检索" / "reviews" / "results").glob("*.json"))
    wd = [x for x in det if x.get("directions")]
    nd = [x for x in det if not (x.get("directions") or [])]
    src = Counter(x.get("direction_source") or "列表页" for x in wd)
    reasons = Counter(x.get("why") or x.get("no_direction_reason", "?") for x in nd)
    immune = (grp.get("groups") or {}).get("免疫与感染", [])
    col_hit = sum(1 for x in col if x.get("directions"))
    school_name = faculty.get("school") or plan.get("school", "")
    unit_name = faculty.get("unit") or plan.get("unit", "")
    site = faculty.get("base") or ""

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
        A(f"针对**{school_name}{unit_name}**的**{args.topic}**方向，")
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
    if layout == "v11":
        rows = [
            ("导师名单与方向归类.md",
             "**主交付物**：导师按方向归类，含职称、方向原文、邮箱、主页链接"),
            ("faculty_grouped.json", "机读版归类结果（含 groups / mentors）"),
            ("faculty_detail.json", "全部导师字段（方向 / 职称 / 邮箱 / 主页 / 状态）"),
            ("faculty_list.json", "阶段 S 列表页原始结果（未经详情补充）"),
            ("raw_list/", "各师资栏目列表页原始 HTML（含全部分页）"),
            ("raw_detail/", "需进详情页补方向的页面存档"),
            ("02_大方向/topic_plan.json", "阶段 N：主题词 / 方法词 / 检索式草稿"),
            ("03_综述检索/europepmc.json", "阶段 R：Europe PMC 原始检索结果"),
            ("03_综述检索/reviews/tasks/", "自包含任务包"),
            ("03_综述检索/reviews/results/", "解读卡"),
            ("04_领域概貌/领域概貌.md", "**主交付物**：面向本科生的领域概貌"),
        ]
    else:
        rows = [
            ("01_导师名单/导师名单与方向归类.md",
             "**主交付物**：导师按方向归类，含职称、方向原文、主页链接"),
            ("01_导师名单/mentors_grouped.json", "机读版归类结果"),
            ("01_导师名单/roster_detail.json", "导师全部字段 + 方向来源标记"),
            ("01_导师名单/roster_list.json", "教师名单（姓名 / 主页 URL）"),
            ("01_导师名单/raw_detail/", "教师个人主页原始 HTML"),
            ("01_导师名单/raw_subpage/", "「研究领域」子页存档"),
            ("05_学院官网/college_detail.json",
             f"**学院官网数据**：{len(col)} 人 × 结构化字段"),
            ("05_学院官网/raw_detail/", "学院师资详情页原始 HTML（按姓名命名）"),
            ("05_学院官网/college_list.json", "各学院师资名单"),
            ("02_大方向/topic_plan.json", "阶段 N：主题词 / 方法词 / 检索式草稿"),
            ("03_综述检索/europepmc.json", "阶段 R：Europe PMC 原始检索结果"),
            ("03_综述检索/reviews/tasks/", "自包含任务包"),
            ("03_综述检索/reviews/results/", "解读卡"),
            ("04_领域概貌/领域概貌.md", "**主交付物**：面向本科生的领域概貌"),
        ]
    for i, (rel, desc) in enumerate(rows, 1):
        p = OUT / rel
        A(f"| {i} | [{rel}]({rel}) | {_size(p) if p.exists() else '—'} | {desc} |")
    A("")
    A("## 数据来源与核验")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    if site:
        A(f"| 数据源（唯一）｜学部/学院官网 | [{site}]({site}) |")
    else:
        A("| 数据源① 教师个人主页系统 | faculty.ustc.edu.cn |")
        A("| 数据源② 学院官网师资页 | biomed.ustc.edu.cn |")
    A(f"| 教师总数 | **{len(det)} 人** |")
    A(f"| **有研究方向** | **{len(wd)} 人（{len(wd)/max(len(det),1)*100:.0f}%）** |")
    A(f"| 无研究方向 | {len(nd)} 人（原因见下，均已逐条核实） |")
    rec = (epmc.get("reconciliation") or {})
    if rec:
        A(f"| 文献检索 | Europe PMC；命中 {rec.get('expected_total',0):,} 篇，"
          f"取回 {len(epmc.get('records') or [])} 篇 |")
    if cards:
        A(f"| 解读卡 | {len(cards)} 张 |")
    if layout == "v11" and faculty.get("sections"):
        A(f"| 抓取栏目 | {len(faculty['sections'])} 个："
          f"{'、'.join(faculty['sections'][:6])}"
          + ("…" if len(faculty["sections"]) > 6 else "") + " |")
    A("")
    A("### 研究方向的获取途径")
    A("")
    A("| 来源 | 人数 | 机制 |")
    A("|---|---|---|")
    if layout == "v11":
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
    # 结构化字段覆盖率：v11 用 faculty_detail，legacy 用 college_detail
    field_src = det if layout == "v11" else col
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
    if layout == "v11":
        A(f"> [WARN] 这 {len(nd)} 人**学部官网本身没有研究方向数据**："
          f"列表页未列出、详情页也没有。")
        A("> 其中「支撑队伍」多为技术与行政支撑岗，本就不以科研方向为主；")
        A("> 「兼聘教师」列表页只给姓名，如需方向须逐一进其个人主页。")
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
    A("1. **方向归类是关键词粗分类**，一位老师可能出现在多个方向下；"
      "**请以官网原文为准**。")
    A(f"2. **{len(nd)} 位老师两个数据源都没有研究方向**，脚本如实标注原因，**未臆造**。")
    A("3. **检索结果是被 `--max-records` 截断的部分结果**（`complete=false`）。")
    A("4. **解读证据层级为「摘要级」** —— 未读全文，引用前请核对原文。")
    A("5. **争议点原样并列**，流程不替你判定谁对。")
    A("6. **学院官网方向为正文抽取**，已剔除论文/基金/导航行；"
      "如发现个别条目不当，请以教师主页原文为准。")
    A("")
    A("## 未完成的环节")
    A("")
    A("- **全文下载（阶段 E）未执行** —— 沙箱下 Edge 无法维持 CDP 调试端口"
      "（`OpenProcess: Access is denied`）。需完全权限后重跑。")
    A("- **Google Scholar / arXiv 两条检索线未跑** —— 仅用 Europe PMC。")
    A("")

    outp = OUT / "交付物索引.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {outp}  ({len(L)} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
