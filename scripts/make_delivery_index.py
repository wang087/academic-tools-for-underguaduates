# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 生成《交付物索引.md》——**两条链路统一写到同一个日期根下**
#
# 交付物索引把散落在输出根里的产物、数据来源、覆盖率、证据层级、
# 「为什么有人没有方向」的原因分布汇总成一页，供交付时说明口径。
#
# 两条链路（产物**同处一个日期根**）：
#   本科入门链路（阶段 N→S→R→R2）→ `<日期根>/outputs/…`
#   作者论文链路（阶段 0→A…I）   → `<日期根>/<作者名>/…`
# 索引落 `<日期根>/交付物索引.md`；只跑了一条链路时，就只出那一条的索引
# （**不会**再拿另一条链路的布局去探测，从而写出一屏「未产出」）。
#
# 产物位置按**候选路径逐个探测**，入门链路三种历史布局都认：
#   ① 当前布局：`outputs/01_导师名单/faculty_*.json` +
#      `outputs/04_领域概貌/` + `.work_cdp/{topic_plan,reviews/}`
#   ② 早期 v11：全部平铺在 `--root` 下，含 `02_大方向/`、`03_综述检索/`
#   ③ v8–v10 双源：`01_导师名单/roster_*.json` + `05_学院官网/`
# **每一句结论都从文件里读**：文件不在就写「未产出」，不写死任何历史案例的话。
#
# 用法：
#   uv run python 流程1/scripts/make_delivery_index.py            # 自动判别链路
#   uv run python 流程1/scripts/make_delivery_index.py --direction 神经与脑科学
#   uv run python 流程1/scripts/make_delivery_index.py --paper-only    # 只出作者链路
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
    """返回第一个存在的候选路径；都不存在则返回 None（调用方据此写「未产出」）。

    **容忍 None 元素**：调用方常用 `(x if cond else None)` 这种写法把候选串起来
    （如 `rdir` 不存在时它就是 None）。不过滤就会在该分支上抛
    `AttributeError: 'NoneType' object has no attribute 'exists'` ——
    即"没有综述目录"这种**正常**情况反而把整个脚本崩掉。
    """
    return next((p for p in cands if p is not None and p.exists()), None)


# ══════════════════════════════════════════════════════════════
# 链路判别与「同一日期目录」定位
#
# 为什么要分模式：交付索引原先**只**认本科入门链路（阶段 N→S→R→R2）的
#   布局（`outputs/01_导师名单/`、`outputs/04_领域概貌/`）。作者论文链路
#   （阶段 0→I）的产物根本不在那些目录里，于是整篇索引全写「未产出」，
#   同目录下真实存在的解读卡、综述卡、PDF 一个都没列 —— 而且**退出码仍是 0**，
#   调用方看不出这是一份答非所问的交付物（取证见 实测结果汇总.md#i-1）。
#
# 两条链路现在统一到**同一个日期根**下：
#   本科入门链路 → `<日期根>/outputs/…`
#   作者论文链路 → `<日期根>/<作者名>/…`（未设 PAPER_AUTHOR 时即 `<日期根>/…`）
# 索引写到 `<日期根>/交付物索引.md`，一份覆盖两条链路。
# ══════════════════════════════════════════════════════════════


def _mentor_root(wd: pathlib.Path) -> pathlib.Path:
    """本科入门链路的产物根（优先规范布局，兼容历史调用方式）。"""
    for c in (wd / "outputs", wd, wd.parent / "outputs"):
        if (c / "01_导师名单").is_dir() or (c / "faculty_grouped.json").exists() \
                or (c / "mentors_grouped.json").exists():
            return c
    return wd / "outputs"


def _has_mentor(out: pathlib.Path) -> bool:
    return ((out / "01_导师名单").is_dir()
            or (out / "faculty_grouped.json").exists()
            or (out / "mentors_grouped.json").exists())


def _find_author_dirs(wd: pathlib.Path) -> list[pathlib.Path]:
    """作者论文链路的产物目录：哪一个含 `*_论文列表.md` / `*_论文解读.md`。

    两级都找：`PAPER_WORKDIR` 直接设成作者目录（`<日期>/<作者>`）时命中第一级；
    设成日期目录（`<日期>`，作者再下一级）时命中第二级。
    """
    pats = ("*_论文列表.md", "*_论文解读.md", "*_研究方向综述.md", "*_知识图谱.md")
    out: list[pathlib.Path] = []
    for d in (wd, *sorted(p for p in wd.iterdir() if p.is_dir())):
        if any(any(d.glob(pat)) for pat in pats):
            out.append(d)
    return out


def _author_of(d: pathlib.Path) -> str:
    """从产物目录名或文件名推断作者名。"""
    for p in sorted(d.glob("*_论文列表.md")) + sorted(d.glob("*_论文解读.md")):
        return p.name.split("_论文")[0]
    return d.name


def _render_paper_section(wd: pathlib.Path, author_dir: pathlib.Path,
                          topic: str = "") -> tuple[list[str], dict]:
    """作者论文链路（阶段 0 → I）的交付索引段。

    **所有数字都从产物里读**：manifest 缺就写「未核实」，卡里没有就写 0 ——
    不写死任何历史案例的结论。
    """
    author = _author_of(author_dir)
    work = author_dir / ".work_cdp"
    L: list[str] = []
    A = L.append

    def _rel(p: pathlib.Path | None) -> str:
        if p is None:
            return "—"
        for base, prefix in ((wd, ""), (author_dir, "")):
            try:
                return prefix + p.relative_to(base).as_posix()
            except ValueError:
                continue
        return p.as_posix()

    def _row(desc: str, p: pathlib.Path | None) -> str:
        if p is None or not p.exists():
            return f"| [{_rel(p)}]({_rel(p)}) | 未产出 | {desc} |"
        return f"| [{_rel(p)}]({_rel(p)}) | {_size(p)} | {desc} |"

    plan = _j(work / "prepare_plan.json", {}) or {}
    # 全文目录：先认 `PAPER_EXPORTS_DIR`（流程显式覆盖点，允许在工作目录之外），
    #   再按规范名/历史名/工作目录名兜底探测（与 store.exports_dir 同一套候选）。
    exp_c: list[pathlib.Path] = []
    _env_exp = os.environ.get("PAPER_EXPORTS_DIR", "").strip()
    if _env_exp:
        _p = pathlib.Path(_env_exp)
        exp_c.append(_p if _p.is_absolute() else author_dir / _p)
    exp_c += [author_dir / f"{author}_paper_Exports",
              author_dir / f"{author}_Exports",
              author_dir / f"{author_dir.name}_paper_Exports",
              author_dir / f"{wd.name}_paper_Exports"]
    exp_c += [d for d in sorted(author_dir.glob("*_Exports")) if d.is_dir()]
    exp = next((d for d in exp_c if d.is_dir()), None)
    interp = work / "interpret"
    refs = work / "refs"
    man_i = _j(interp / "manifest.json", {}) or {}
    cards = sorted((interp / "results").glob("*.json")) if (interp / "results").is_dir() else []
    man_r = _j(refs / "manifest.json", {}) or {}
    rcards = sorted((refs / "results").glob("*.json")) if (refs / "results").is_dir() else []
    final_list = _j(refs / "final_list.json", {}) or {}
    dropped = _j(refs / "dropped.json", {}) or {}

    A(f"## 作者论文分析链路（阶段 0 → I）· {author}")
    A("")
    A("| # | 文件 | 大小 | 说明 |")
    A("|---|---|---|---|")
    rows = [
        ("**主交付物**：论文列表（GB/T 7714-2015 + DOI + 原文链接）",
         _pick(sorted(author_dir.glob("*_论文列表.md")))),
        ("**主交付物**：论文解读（时间线 / 逐篇四步 / 关键词 / 方向 / 范式）",
         _pick(sorted(author_dir.glob("*_论文解读.md")))),
        ("**主交付物**：研究方向综述（阶段 H2③，单一文件）",
         _pick(sorted(author_dir.glob("*_研究方向综述.md")))),
        ("阶段 D2：反向查得的高质量文献列表", _pick(sorted(author_dir.glob("*_高质量文献列表.md")))),
        ("阶段 G2：代表性文献的参考文献（GB/T 7714）",
         _pick(sorted(author_dir.glob("*_代表性文献参考文献.md")))),
        ("阶段 H2①：去重 + 相关性整理后的终表",
         _pick(sorted(author_dir.glob("*_研究方向文献终表.md")))),
        ("阶段 K：知识图谱说明 + 聚类解读 + 剔除记录",
         _pick(sorted(author_dir.glob("*_知识图谱.md")))),
        ("阶段 P：PPT 项目（`make_ppt=yes` 时；`.pptx` 在 exports/ 下）",
         _pick(sorted(author_dir.glob("*_汇报PPT*")))),
        ("阶段 I《全文清单.md》：逐篇状态 / 来源通道 / 页数 / 未获取原因",
         (exp / "全文清单.md") if exp else None),
        ("原文全文 PDF 目录（题名命名；历史名 `<作者名>_Exports` 亦沿用）", exp),
        ("阶段 H 自包含任务包（题录 + 正文；subagent 唯一输入）",
         (interp / "tasks") if (interp / "tasks").is_dir() else None),
        ("阶段 H 解读卡（一 task 一 result）", (interp / "results") if cards else None),
        ("阶段 H2 自包含综述任务包", (refs / "tasks") if (refs / "tasks").is_dir() else None),
        ("阶段 H2 综述解读卡", (refs / "results") if rcards else None),
        ("阶段 K 图件（SVG 优先，PNG 回退）+ graph_data.json",
         (work / "graph") if (work / "graph").is_dir() else None),
        ("阶段 0：准备计划 + 阶段闸门（单一事实源）",
         (work / "prepare_plan.json") if (work / "prepare_plan.json").exists() else None),
    ]
    for i, (desc, p) in enumerate(rows, 1):
        A(_row(desc, p))
    A("")

    # ── 数据核验：证据层级**读 interpret manifest**（不是 reviews manifest）──
    A("## 数据核验（作者链路）")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    n_tasks = int(man_i.get("count") or len(man_i.get("tasks") or []) or 0)
    n_full = int(man_i.get("fulltext") or 0)
    n_abs = int(man_i.get("abstract_only") or 0)
    A(f"| 论文解读 | 任务包 **{n_tasks}** ｜ 解读卡 **{len(cards)}** ｜ 缺卡 "
      f"**{max(0, n_tasks - len(cards))}** |")
    if n_tasks:
        A(f"| 解读证据层级 | 全文 **{n_full}** ｜ 仅摘要 **{n_abs}**"
          + ("（`interpret/manifest.json`）|" if man_i else "（manifest 缺失，按任务包计）|"))
    else:
        A("| 解读证据层级 | **未核实**（未找到 `interpret/manifest.json`）|")
    A(f"| 解读档位 | `{man_i.get('interpret_tier') or plan.get('interpret_tier') or '—'}`"
      f" ｜ 引擎 `{man_i.get('interpret_engine') or plan.get('interpret_engine') or '—'}` |")
    n_rt = int(man_r.get("count") or len(man_r.get("tasks") or []) or 0)
    A(f"| 研究方向综述 | 任务包 **{n_rt}** ｜ 综述卡 **{len(rcards)}** ｜ 缺卡 "
      f"**{max(0, n_rt - len(rcards))}** |")
    kept = final_list.get("kept") or final_list.get("final")
    kept_n = len(kept) if isinstance(kept, list) else (final_list.get("stats") or {}).get("最终保留")
    drop_n = len(dropped.get("dropped") or dropped.get("items") or []) if isinstance(dropped, dict) else None
    A(f"| H2① 相关性整理 | 最终保留 **{kept_n if kept_n is not None else '未核实'}**"
      f" ｜ 剔除 **{drop_n if drop_n is not None else '未核实'}**（剔除项已逐条留档）|")
    if exp:
        pdfs = sorted(exp.glob("*.pdf"))
        A(f"| 原文全文 | PDF **{len(pdfs)}** 件"
          + (f"（`{_rel(exp)}`）|" if pdfs else "（**未见 PDF**）|"))
    A("")

    # ── 如实申报限制 ──
    A("## 限制申报")
    A("")
    notes: list[str] = []
    if n_abs:
        notes.append(f"**{n_abs} 篇解读停在摘要级**（未取得原文全文）"
                     "—— 其结论不得当作全文结论引用。")
    if n_tasks and len(cards) < n_tasks:
        notes.append(f"**缺卡 {n_tasks - len(cards)} 篇**："
                     f"`interpret/manifest.json` 与 `results/` 数量不符，需重派后再交付。")
    if n_rt and len(rcards) < n_rt:
        notes.append(f"**综述缺卡 {n_rt - len(rcards)} 条**。")
    if not man_i:
        notes.append("未找到 `interpret/manifest.json` —— 解读证据层级**无法核实**。")
    notes.append("**同名消歧**只排除肯定性判定（`disambiguated.json`）；"
                 "语料缺作者/单位字段的条目标为「数据缺失、无法判定」，**不当作他人论文丢弃**。")
    notes.append("**参考文献（阶段 H2）通常只有摘要级**：本流程只下载作者本人的全文，"
                 "综述卡的 `evidence.source` 已如实标注，引用前请核对原文。")
    for i, t in enumerate(notes, 1):
        A(f"{i}. {t}")
    A("")
    stats = {"author": author, "n_tasks": n_tasks, "n_cards": len(cards),
             "n_full": n_full, "n_abs": n_abs, "n_rt": n_rt, "n_rcards": len(rcards)}
    return L, stats


def _paper_index(wd: pathlib.Path, author_dir: pathlib.Path, topic: str = "") -> list[str]:
    """**只**含作者链路的交付索引（入门链路未跑时用）。"""
    sec, stats = _render_paper_section(wd, author_dir, topic)
    L: list[str] = []
    A = L.append
    A(f"# 交付物索引 · {stats['author']}")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} | 工作文件夹：`{wd}`")
    A("> ")
    A("> 流程：`流程1/` 作者论文分析链路（阶段 0 → A → … → I）")
    A("> ")
    A(f"> 本次**未执行**本科科研辅助前置链路（阶段 N/S/R/R2）"
      f"—— 如需入门链路交付物，请从阶段 N 重新接入。")
    A("")
    A("---")
    A("")
    A("## 一句话交付")
    A("")
    A(f"作者 **{stats['author']}**：**{stats['n_tasks']}** 篇论文的题录核实与逐篇解读"
      f"（解读卡 {stats['n_cards']} 张，其中全文级 {stats['n_full']}）"
      f"＋ **{stats['n_rt']}** 条参考文献的方向综述网络"
      f"（综述卡 {stats['n_rcards']} 张）。")
    A("")
    A("---")
    A("")
    L.extend(sec)
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="make_delivery_index.py",
        description="生成《交付物索引.md》——两条链路（本科入门 / 作者论文）"
                    "统一写到**同一个日期根**下")
    ap.add_argument("--root", default="",
                    help="本科入门链路的产物根；缺省自动探测 "
                         "<日期根>/outputs → <日期根> → 仓库级 outputs/")
    ap.add_argument("--topic", default="",
                    help="研究主题（仅记录，供抬头用；不填则从 topic_plan 读）")
    ap.add_argument("--direction", default="",
                    help="用户选定的学部方向；不填则读 topic_plan 的 direction_chosen")
    ap.add_argument("--paper-only", action="store_true",
                    help="只出作者论文链路的索引（入门链路未跑时自动如此）")
    ap.add_argument("--mentor-only", action="store_true",
                    help="只出入门链路的索引（作者链路未跑时自动如此）")
    args = ap.parse_args(argv)

    # ── 链路判别：两条链路的产物现在都在**同一个日期根**下 ──
    #   入门链路 → <日期根>/outputs/…      作者链路 → <日期根>/<作者名>/…
    OUT = (pathlib.Path(args.root) if args.root else _mentor_root(WD))
    if not OUT.is_absolute():
        OUT = WD / OUT
    WORK = WD / ".work_cdp"
    author_dirs = _find_author_dirs(WD)
    mentor_here = _has_mentor(OUT)

    # 作者链路独有：直接出作者索引，**不再**渲染一屏「未产出」的入门布局
    #   （那正是缺陷 I-1：索引答非所问，而退出码仍是 0）
    if author_dirs and (args.paper_only or not mentor_here):
        L = _paper_index(WD, author_dirs[0], args.topic)
        # 文件名与产出物规范一致：作者链路用 `<作者名>_交付物索引.md`
        outp = WD / f"{_author_of(author_dirs[0])}_交付物索引.md"
        outp.write_text("\n".join(L), encoding="utf-8")
        print(f"→ {outp}  ({len(L)} 行)  [作者论文链路]")
        return 0
    if not mentor_here:
        # 两条链路的产物都没找到：如实说明，不再产出一份「全是未产出」的文件
        print(f"[X] 未找到任何链路的产物：")
        print(f"    入门链路根 {OUT}（应含 01_导师名单/ 或 faculty_grouped.json）")
        print(f"    作者链路    {WD}（应含 *_论文列表.md / *_论文解读.md）")
        print("    先跑阶段 S/R（入门链路）或阶段 0→I（作者链路），再执行本脚本。")
        return 2

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

    # ── 两条链路都跑过：把作者链路段**并进同一份索引** ──
    #   用户要求「两份输出在同一（日期命名的）文件夹下」—— 因此索引统一写到
    #   <日期根>/交付物索引.md，一份覆盖两条链路，而不是各写各的。
    if author_dirs and not args.mentor_only:
        A("---")
        A("")
        sec, _ = _render_paper_section(WD, author_dirs[0], topic_label)
        L.extend(sec)

    outp = ((WD / f"{_author_of(author_dirs[0])}_交付物索引.md") if author_dirs
            else (OUT / "交付物索引.md"))
    outp.write_text("\n".join(L), encoding="utf-8")
    chains = "入门链路" + ("＋作者论文链路" if author_dirs else "")
    print(f"→ {outp}  ({len(L)} 行)  [{chains}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
