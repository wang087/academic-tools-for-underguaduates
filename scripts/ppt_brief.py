# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 P：PPT 生成 —— ppt-master 整合 + **简化 Strategist Phase**
#
# 材料来源（用户指定，不需要另行准备）：
#   ① 《<作者名>_论文解读.md》     → 该作者文献亮点解读
#   ② 《<作者名>_研究方向综述.md》  → 阶段 H2 产出的 review 结果
#
# 本脚本做三件事：
#   ① 把上面两份材料**结构化**成 ppt-master 的 source（Markdown）；
#   ② 调 ppt-master 的 project_manager.py 初始化项目并导入 source；
#   ③ **直接生成 `design_spec.md` 与 `spec_lock.md`** —— 这就是被简化掉的
#      「Strategist Phase」：原流程需要 Step 3 备选模板候选 + Step 4 两阶段
#      阻塞式确认（Confirm UI / 聊天确认）+ 人工撰写 spec/lock；
#      在本流程里，材料已由上游流水线定型、版面走自由设计，
#      因此候选准备与确认环节**没有可确认的内容**，可以直接确定性生成。
#
# ❗ 简化掉的和**没有**简化掉的：
#   · 简化掉：Step 3（模板候选准备）、Step 4 的两阶段用户确认、策略师的角色切换与
#     能力模块装载、§VII/§VIII 的图像资源规划（本流程不出图）。
#   · **没有**简化掉：Step 5 之后的全部环节 —— 页面 SVG 仍由主 agent **手写**、
#     仍要过 `svg_quality_checker.py`、仍需 Step 7 的串行导出。
#     页面的视觉设计与内容编排依旧由 agent 负责，只是**决策依据从"用户确认"
#     换成了"上游流水线的确定产物"**。
#
# 用法：
#   uv run python 流程1/scripts/ppt_brief.py
#     --ppt-dir=<ppt-master 仓库根>   默认自动探测（$PPT_MASTER_DIR 或 <本仓>/ppt-master）
#     --project-name=<项目名>         默认 <作者名>_文献汇报
#     --title=<PPT 标题>              默认「<作者名> 研究工作汇报」
#     --pages=short|standard|long     默认读准备阶段计划，再默认 standard
#     --canvas=ppt169                 画布格式（默认 PPT 16:9）
#     --lang=zh-CN                    内容语言
#     --dry-run                       只打印将要写什么，不落盘、不调用 ppt-master
#     --no-graphs                     不接入阶段 K 的图谱与方向综述整理
#     --self-test                     纯逻辑自检（不联网、不落盘）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_AFFIL / PAPER_ORCID
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import subprocess
import sys

from collections import Counter

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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from logutil import get, journal  # type: ignore
except Exception:
    class _L:
        def info(self, *a, **k):
            print(*a)

        def warn(self, *a, **k):
            print(*a, file=sys.stderr)

    def get(_n):            # noqa: D103
        return _L()

    def journal(*a, **k):   # noqa: D103
        pass

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WD / ".work_cdp"
REFDIR = CDPDIR / "refs"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
AFFIL = os.environ.get("PAPER_AFFIL", "").strip()
ORCID = os.environ.get("PAPER_ORCID", "").strip()

CANVAS = {
    "ppt169": ("PPT 16:9", "0 0 1280 720"),
    "ppt43": ("PPT 4:3", "0 0 1024 768"),
}

PAGE_BUDGET = {"short": (10, 14), "standard": (16, 22), "long": (24, 32)}

# 日期不参与逻辑，只写进 §I；用文件系统时间而不是当前时间，便于复现
def today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _argval(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def load_json(p: pathlib.Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠ {p.name} 解析失败：{e}", file=sys.stderr)
        return default


def one_line(v, n: int = 0) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return (s[:n - 1] + "…") if (n and len(s) > n) else s


def find_ppt_dir(explicit: str = "") -> pathlib.Path | None:
    """定位 ppt-master 仓库根（含 skills/ppt-master/SKILL.md 的那个目录）。

    查找顺序（前者优先）：
      ① `--ppt-dir=` 显式指定
      ② `PPT_MASTER_DIR` 环境变量
      ③ **本流程目录内的随包副本 `<流程>/vendor/ppt-master`** —— 打包发行版
         （如 `流程1/`）自带依赖时走这里，保证脱离原仓库也能跑
      ④ 同级仓库目录 `<仓库根>/ppt-master`
    """
    cands = []
    if explicit:
        cands.append(pathlib.Path(explicit))
    env = os.environ.get("PPT_MASTER_DIR", "").strip()
    if env:
        cands.append(pathlib.Path(env))
    # ③ 随包副本（_HERE.parent = 流程1/ 或 流程1/）
    cands.append(_HERE.parent / "vendor" / "ppt-master")
    # ④ 同级仓库目录。★ 上一级是 流程1/，再上一级才是仓库根
    #    （写成 parents[1].parent 会跑到用户主目录去找同名的 ppt-master，
    #      实测确实误命中过 C:\Users\<用户名>\ppt-master，必须钉死）
    cands.append(_HERE.parents[1] / "ppt-master")
    for c in cands:
        try:
            c = c.resolve()
        except Exception:
            continue
        if (c / "skills" / "ppt-master" / "SKILL.md").exists():
            return c
    return None


# ══════════════════════════════════════════════════════════════
# 材料抽取
# ══════════════════════════════════════════════════════════════
def collect_highlights() -> list[dict]:
    """作者论文亮点：优先读解读卡（结构化），缺则从 md 报告粗略提取。"""
    out = []
    rdir = CDPDIR / "interpret" / "results"
    man = load_json(CDPDIR / "interpret" / "manifest.json", {}) or {}
    meta = {t.get("slug"): t for t in (man.get("tasks") or [])}
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if not isinstance(c, dict):
                continue
            m = meta.get(p.stem) or {}
            out.append({
                "title": m.get("title") or one_line(c.get("one_line"), 60),
                "year": m.get("year"),
                "venue": m.get("venue") or "",
                "one_line": one_line(c.get("one_line")),
                "key_20": [one_line(x) for x in (c.get("key_20") or [])][:3],
                "gains": [g for g in (c.get("quantified_gains") or []) if isinstance(g, dict)][:2],
                "direction_tags": c.get("direction_tags") or [],
                "is_review_source": False,
            })
    out.sort(key=lambda x: -(x.get("year") or 0))
    return out


def collect_review() -> dict:
    """review 结果：读 stage H2 的结构化产物，缺则退回 md 报告。"""
    final = load_json(REFDIR / "final_list.json", {}) or {}
    man = load_json(REFDIR / "manifest.json", {}) or {}
    meta = {t.get("slug"): t for t in (man.get("tasks") or [])}
    cards = []
    rdir = REFDIR / "results"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if isinstance(c, dict):
                c["_meta"] = meta.get(p.stem) or {}
                cards.append(c)

    dirs, roles, parads = Counter(), Counter(), Counter()
    for c in cards:
        for d in (c.get("direction_tags") or []):
            dirs[str(d)] += 1
        for p in (c.get("paradigm_tags") or []):
            parads[str(p)] += 1
        if c.get("network_role"):
            roles[one_line(c["network_role"])] += 1

    return {
        "stats": final.get("stats") or {},
        "profile_tags": list((final.get("profile") or {}).get("direction_tags") or {}),
        "cards": cards,
        "directions": dirs.most_common(),
        "roles": roles.most_common(),
        "paradigms": parads.most_common(),
        "foundation": [c for c in cards
                       if "奠基" in one_line(c.get("network_role"))][:3],
        "method_src": [c for c in cards
                       if "方法源头" in one_line(c.get("network_role"))][:3],
        "consensus": [c for c in cards
                      if "共识" in one_line(c.get("network_role"))][:3],
        "gaps": [one_line(x) for c in cards for x in (c.get("open_questions") or [])][:6],
        "conclusions": [one_line(x) for c in cards
                        for x in (c.get("verified_conclusions") or [])][:6],
    }


def png_size(p: pathlib.Path) -> tuple[int, int]:
    """读 PNG 宽高（只解析文件头，不引入 Pillow 依赖）。"""
    try:
        import struct
        with open(p, "rb") as f:
            f.read(16)
            w, h = struct.unpack(">II", f.read(8))
        return int(w), int(h)
    except Exception:
        return 0, 0


GRAPH_TITLES = {
    "overview": ("作者研究工作知识图谱", "以作者为中心；阶段→方向→最紧密综述；Louvain 社区上色；虚线为记忆连接"),
    "词云": ("研究方法与实验范式词云", "颜色区分研究方向；已剔除模型生物等误导性常见词"),
    "wordcloud": ("研究方法与实验范式词云", "颜色区分研究方向；已剔除模型生物等误导性常见词"),
    "方向聚类": ("各方向近年论文与热点聚类", "点大小=被引次数；颜色=热点簇"),
    "directions": ("各方向近年论文与热点聚类", "点大小=被引次数；颜色=热点簇"),
}


def collect_graphs() -> list[dict]:
    """收集阶段 K 产出的图谱。

    ★ PPT 里用 **PNG**（不是 SVG）：PPTX 的 `<image>` 内嵌对 SVG 的支持随
      PowerPoint 版本而异，栅格图最稳。阶段 K 默认只出 SVG（矢量、可无损缩放），
      所以要进 PPT 就得**同时**出 PNG —— 这里检测不到 PNG 时明确提示怎么补，
      而不是悄悄少放一张图。
    """
    gdir = CDPDIR / "graph"
    if not gdir.is_dir():
        return []
    out = []
    for p in sorted(gdir.glob("*.png")):
        key = next((k for k in GRAPH_TITLES if k in p.stem), "")
        title, desc = GRAPH_TITLES.get(key, (p.stem, ""))
        w, h = png_size(p)
        out.append({"path": p, "name": p.name, "title": title, "desc": desc,
                    "w": w, "h": h})
    if not out and list(gdir.glob("*.svg")):
        print("ℹ 阶段 K 只产出了 SVG（矢量），PPT 需要 PNG → "
              "请补跑：uv run --with matplotlib --with networkx "
              "python 流程1/scripts/knowledge_graph.py --also-png")
    return out


def collect_direction_review() -> dict:
    """方向综述整理：每个方向下「最紧密的综述」+ 关键结论/缺口。

    数据源与阶段 K 一致（`refs/final_list.json` + `refs/results/*.json`），
    保证 PPT 上的方向口径与图谱、与《研究方向综述》报告**同源**。
    """
    final = load_json(REFDIR / "final_list.json", {}) or {}
    man = load_json(REFDIR / "manifest.json", {}) or {}
    meta = {t.get("slug"): t for t in (man.get("tasks") or [])}
    vocab = load_json(CDPDIR / "tag_vocabulary.json", {})         or load_json(TPL.parent / "templates" / "方法范式标签词表.json", {}) or {}
    allowed_d = vocab.get("direction_tags_vocabulary") or []
    aliases = (vocab.get("aliases") or {})
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}

    def norm(t):
        t = str(t or "").strip()
        if not t:
            return ""
        if t in allowed_d:
            return t
        if t.lower() in al_d:
            return al_d[t.lower()]
        return t

    by_dir = {}
    rdir = REFDIR / "results"
    cards = {}
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if isinstance(c, dict):
                cards[p.stem] = c
    for x in (final.get("items") or []):
        c = cards.get(str(x.get("id") or "")) or {}
        tags = [norm(t) for t in (c.get("direction_tags") or [])]
        tags = [t for t in tags if t]
        if not tags:
            continue
        d = tags[0]
        e = by_dir.setdefault(d, {"reviews": [], "conclusions": [], "gaps": []})
        if x.get("is_review"):
            e["reviews"].append({
                "title": x.get("title") or "",
                "venue": x.get("venue") or "",
                "year": x.get("year"),
                "cited": x.get("cited_by_count") or 0,
                "role": (c.get("network_role") or ""),
                "one_line": (c.get("one_line") or ""),
                "rel": ((x.get("_score") or {}).get("relevance") or 0),
            })
        for v in (c.get("verified_conclusions") or [])[:2]:
            e["conclusions"].append(str(v))
        for v in (c.get("open_questions") or [])[:2]:
            e["gaps"].append(str(v))
    for d, e in by_dir.items():
        e["reviews"].sort(key=lambda r: (-r["rel"], r["cited"]))
    return by_dir


def build_source_md(highlights, review, title, graphs=None, dirmap=None) -> str:
    """给 ppt-master 的 source（Markdown）。它会被 Step 1/2 原样导入。"""
    L = [f"# {title} —— 汇报材料", ""]
    L.append(f"> 作者：{AUTHOR}" + (f"（{AFFIL}）" if AFFIL else ""))
    if ORCID:
        L.append(f"> ORCID：{ORCID}")
    L.append("> 本材料由《流程》作者论文分析流水线自动汇编，"
             "内容取自《{}_论文解读.md》与《{}_研究方向综述.md》。".format(AUTHOR, AUTHOR))
    L.append("")

    L.append("## 一、作者文献亮点")
    L.append("")
    if highlights:
        for i, h in enumerate(highlights[:12], 1):
            L.append(f"### {i}. {h['title']}")
            L.append(f"- 发表：{h.get('venue') or '—'}（{h.get('year') or '—'}）")
            if h["one_line"]:
                L.append(f"- 定位：{h['one_line']}")
            for k in h["key_20"]:
                L.append(f"- 关键：{k}")
            for g in h["gains"]:
                L.append(f"- 量化：{g.get('metric', '')} = {g.get('value', '')}"
                         f"（{g.get('context', '')}）")
            L.append("")
    else:
        L.append("> ⚠ 未找到阶段 H 的解读卡（`.work_cdp/interpret/results/`）。"
                 "请先跑阶段 H，否则本材料只有综述部分。")
        L.append("")

    L.append("## 二、研究方向综述（review）")
    L.append("")
    st = review["stats"]
    if st:
        L.append("### 2.1 综述网络规模")
        L.append("")
        L.append("| 指标 | 数值 |")
        L.append("|---|---|")
        for k, v in st.items():
            L.append(f"| {k} | {v} |")
        L.append("")
    if review["directions"]:
        L.append("### 2.2 研究方向分布")
        L.append("")
        for k, v in review["directions"]:
            L.append(f"- {k}：{v} 条")
        L.append("")
    if review["roles"]:
        L.append("### 2.3 网络角色分布")
        L.append("")
        for k, v in review["roles"]:
            L.append(f"- {k}：{v} 条")
        L.append("")
    for label, key in (("奠基工作", "foundation"), ("方法源头", "method_src"),
                       ("共识综述", "consensus")):
        group = review[key]
        if group:
            L.append(f"### 2.4 {label}")
            L.append("")
            for c in group:
                t = one_line(c.get("_meta", {}).get("title") or c.get("doi"), 80)
                L.append(f"- **{t}** —— {one_line(c.get('one_line'), 120)}")
            L.append("")
    if review["conclusions"]:
        L.append("### 2.5 关键结论")
        L.append("")
        for c in review["conclusions"]:
            L.append(f"- {c}")
        L.append("")
    if review["gaps"]:
        L.append("### 2.6 研究缺口与展望")
        L.append("")
        for g in review["gaps"]:
            L.append(f"- {g}")
        L.append("")
    if not review["cards"]:
        L.append("> ⚠ 未找到阶段 H2 的综述解读卡（`.work_cdp/refs/results/`）。"
                 "请先跑阶段 D2 → H2 ① ②。")
        L.append("")

    if dirmap:
        L.append("## 三、方向综述整理")
        L.append("")
        L.append("> 与《`<作者名>_研究方向综述.md`》**同源**"
                 "（`refs/final_list.json` + `refs/results/*.json`），"
                 "保证 PPT 与报告的方向口径一致。")
        L.append("")
        for i, (d, e) in enumerate(sorted(dirmap.items(),
                                          key=lambda kv: -len(kv[1]["reviews"]))[:8], 1):
            L.append(f"### 3.{i} 方向：{d}")
            L.append("")
            revs = e["reviews"][:4]
            if revs:
                L.append("**该方向最紧密的综述**")
                L.append("")
                for r in revs:
                    tag = f"（{r['role']}）" if r.get("role") else ""
                    L.append(f"- {r['title']} —— {r['venue']} {r['year']}{tag}"
                             + (f"；{r['one_line']}" if r.get("one_line") else ""))
                L.append("")
            if e["conclusions"]:
                L.append("**已确认的关键结论**")
                L.append("")
                for c in e["conclusions"][:3]:
                    L.append(f"- {one_line(c, 120)}")
                L.append("")
            if e["gaps"]:
                L.append("**尚未解决的问题**")
                L.append("")
                for g in e["gaps"][:3]:
                    L.append(f"- {one_line(g, 120)}")
                L.append("")

    if graphs:
        L.append("## 四、知识图谱（阶段 K 产出）")
        L.append("")
        L.append("| 图 | 文件 | 说明 |")
        L.append("|---|---|---|")
        for g in graphs:
            L.append(f"| {g['title']} | `{g['name']}` | {g['desc']} |")
        L.append("")
        L.append("> 图为**透明背景**、高分辨率，可直接置于页面；"
                 "版式上建议整幅铺放并叠加少量文字，不要缩到读不清。")
        L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
# 简化 Strategist：确定性生成 design_spec.md
# ══════════════════════════════════════════════════════════════
PALETTE = [
    ("primary", "#1B3A6B", "主色，深蓝：标题、主导航、页眉"),
    ("accent", "#E4572E", "强调色，橙红：关键数字、结论、对比"),
    ("secondary", "#2E86AB", "辅助色，中蓝：次级信息、图表主序列"),
    ("muted", "#6B7A8F", "弱化色：注释、来源、页码"),
    ("surface", "#F5F7FA", "底色：内容块背景"),
    ("text", "#1A1A1A", "正文文字"),
    ("background", "#FFFFFF", "页面底色"),
]


def build_slides(highlights, review, pages_budget, title="",
                 graphs=None, dirmap=None) -> list[tuple[str, list[str]]]:
    """按素材实际能支撑的量生成页面清单。

    ★ 为什么页数要**由内容倒推**而不是直接写预算上限：
      ppt-master 的硬契约是 §I `Page Count` **必须等于** §IX 里实际的 Slide 数。
      先写 `Page Count = 14` 再生成 10 页（或反过来），会让 agent 按 14 页去排，
      最后要么缺 4 页，要么自己编 4 页 —— 后者正是"为凑数编内容"。
      素材不够就**如实少几页**，并在 stdout 说明差距，不擅自补内容。
    """
    n_low, n_high = pages_budget
    # 固定页：封面 / 地图 / 综述规模 / 缺口 / 结论 = 5；其余额度分给亮点页
    FIXED = 5
    n_h = max(1, min(len(highlights), n_high - FIXED - 2)) if highlights else 0

    p1, p2, p3, p4 = [], [], [], []

    p1.append("\n".join([
        "#### Slide @ - 封面", "",
        "- **Audience move**: 未知 → 知道这是谁的研究、讲什么",
        "- **Layout**: 全幅留白，标题居左偏上，作者信息居下",
        f"- **Title**: {title}", "",
        "- **Core message**: 本次汇报回答三个问题：这位作者做了什么、"
        "站在谁的肩上、还留下什么没解决",
        "- **Content**: 标题、作者、单位、汇报日期、数据来源说明"
        "（《论文解读》+《研究方向综述》）", "",
    ]))
    p1.append("\n".join([
        "#### Slide @ - 汇报地图", "",
        "- **Audience move**: 不知结构 → 知道后面几页会讲什么",
        "- **Layout**: 三栏并列",
        "- **Title**: 汇报结构", "",
        "- **Core message**: 亮点 → 网络 → 缺口，三步走",
        "- **Content**: ① 作者文献亮点；② 研究方向综述网络；③ 研究缺口与展望", "",
    ]))

    if highlights:
        for h in highlights[:n_h]:
            blk = [
                f"#### Slide @ - 亮点：{one_line(h['title'], 40)}", "",
                "- **Audience move**: 不了解该工作 → 记住它解决了什么问题",
                "- **Layout**: 标题 + 左侧要点 + 右侧量化数字块",
                f"- **Title**: {one_line(h['title'], 60)}",
            ]
            if h["one_line"]:
                blk.append(f"- **Core message**: {h['one_line']}")
            blk.append("- **Content**: "
                       + "；".join(h["key_20"] or ["（详见《论文解读》报告）"]))
            for g in h["gains"]:
                blk.append(f"- **量化**: {g.get('metric', '')} = {g.get('value', '')}"
                           f"（{g.get('context', '')}）")
            blk.append(f"- **来源**: {h.get('venue') or '—'} {h.get('year') or ''}")
            blk.append("")
            p2.append("\n".join(blk))
    else:
        p2.append("\n".join([
            "#### Slide @ - 亮点（素材缺失）", "",
            "- **Audience move**: —",
            "- **Layout**: 单栏提示",
            "- **Title**: 文献亮点", "",
            "- **Core message**: 本页需要《论文解读》报告；当前未找到该素材",
            "- **Content**: 请先执行阶段 H 生成解读卡后重新生成本 Spec", "",
        ]))

    st = review["stats"]
    p3.append("\n".join([
        "#### Slide @ - 综述网络规模", "",
        "- **Audience move**: 不知这个方向有多大 → 对规模有数量级认识",
        "- **Layout**: 指标卡横排",
        "- **Title**: 反向查得的综述网络", "",
        f"- **Core message**: 该作者的论文共同指向 "
        f"{st.get('最终保留', '—')} 条高质量文献，构成这个方向的知识底座",
        "- **Content**: " + "；".join(f"{k} {v}" for k, v in list(st.items())[:6]), "",
    ]))
    if review["directions"]:
        p3.append("\n".join([
            "#### Slide @ - 研究方向分布", "",
            "- **Audience move**: 说不清方向构成 → 能说出前 3 条主线",
            "- **Layout**: 横向条形 + 说明",
            "- **Title**: 主要研究方向", "",
            "- **Core message**: " + "；".join(
                f"{k} {v} 条" for k, v in review["directions"][:5]),
            "- **Content**: " + "；".join(f"{k}（{v}）" for k, v in review["directions"]),
            "",
        ]))
    if review["roles"]:
        p3.append("\n".join([
            "#### Slide @ - 网络角色", "",
            "- **Audience move**: 只见清单不见结构 → 理解每条文献的角色",
            "- **Layout**: 分栏列表",
            "- **Title**: 这些文献在网络里扮演什么角色", "",
            "- **Core message**: 奠基、方法源头、共识综述三类构成网络骨架",
            "- **Content**: " + "；".join(f"{k} {v} 条" for k, v in review["roles"]),
            "",
        ]))
    if review["conclusions"]:
        p3.append("\n".join([
            "#### Slide @ - 关键结论", "",
            "- **Audience move**: 不知共识是什么 → 记住 3 条被反复验证的结论",
            "- **Layout**: 编号列表",
            "- **Title**: 这个方向已经确认了什么", "",
            "- **Core message**: 以下结论来自多条文献的独立验证",
            "- **Content**: " + "；".join(review["conclusions"]), "",
        ]))

    p4.append("\n".join([
        "#### Slide @ - 研究缺口", "",
        "- **Audience move**: 以为方向已经做完 → 看到明确的未解决问题",
        "- **Layout**: 编号列表，缺口用强调色标注",
        "- **Title**: 还没解决的问题", "",
        "- **Core message**: 以下缺口全部来自文献作者自述，非本流程推断",
        "- **Content**: " + ("；".join(review["gaps"]) if review["gaps"]
                            else "（未收集到自述局限）"), "",
    ]))
    p4.append("\n".join([
        "#### Slide @ - 结论页", "",
        "- **Audience move**: 听完 → 带走一句话",
        "- **Layout**: 单句居中 + 数据来源脚注",
        "- **Title**: 小结",
        "- **Core message**: " + (tagline_or(review) + " —— 已有可量化推进，"
                                 "仍有关键缺口待补"),
        "- **Content**: 三条要点回扣 + 数据来源与限制声明", "",
    ]))

    # ── Part 5：知识图谱（有图才出页）────────────────────────
    p5 = []
    for g in (graphs or []):
        blk = [
            f"#### Slide @ - 图谱：{g['title']}", "",
            "- **Audience move**: 只见文字清单 → 一眼看到全局结构",
            "- **Layout**: 整幅铺放图谱，角落叠一条标题与一句导语",
            f"- **Title**: {g['title']}",
            f"- **Core message**: {g['desc']}",
            f"- **Images**: `{g['name']}` —— 整幅铺放（no-crop），"
            "四周留安全边距，**不要缩小到读不清**",
            "",
        ]
        p5.append("\n".join(blk))
    if dirmap:
        top = sorted(dirmap.items(), key=lambda kv: -len(kv[1]["reviews"]))[:4]
        blk = [
            "#### Slide @ - 方向综述整理", "",
            "- **Audience move**: 知道有哪些方向 → 知道**每个方向读哪几篇**",
            "- **Layout**: 每个方向一行：方向名 + 最紧密综述 + 一句结论",
            "- **Title**: 各方向的入门阅读顺序", "",
            "- **Core message**: 想进这个方向，先读这几篇综述",
        ]
        for d, e in top:
            r = (e["reviews"] or [{}])[0]
            blk.append(f"- **{d}**：{one_line(r.get('title') or '—', 60)}"
                       f"（{r.get('venue') or '—'} {r.get('year') or ''}）")
        blk.append("")
        p5.append("\n".join(blk))

    parts = [("Part 1: 开场与定位", p1), ("Part 2: 作者文献亮点", p2),
             ("Part 3: 研究方向综述网络", p3), ("Part 4: 缺口与展望", p4)]
    if p5:
        parts.append(("Part 5: 知识图谱与方向综述整理", p5))

    # ── 统一编号：把 @ 换成真实序号，保证 §I Page Count 与实际一致 ──
    n = 0
    out = []
    for part_title, blocks in parts:
        nb = []
        for b in blocks:
            n += 1
            nb.append(b.replace("#### Slide @", f"#### Slide {n:02d}", 1))
        out.append((part_title, nb))
    return out


def tagline_or(review) -> str:
    return "、".join([k for k, _ in review["directions"]][:3]) or "研究方向"


def gen_design_spec(title, canvas_key, parts, source_rel, highlights,
                    review, audience, graphs=None) -> str:
    fmt, vb = CANVAS[canvas_key]
    n_pages = sum(len(b) for _, b in parts)
    tagline = tagline_or(review)

    L = []
    L.append("<!-- ppt-master-schema: design-spec/v1 -->")
    L.append(f"# {title} - Design Spec")
    L.append("")
    L.append("## I. Project Information")
    L.append("")
    L.append("| Item | Value |")
    L.append("| --- | --- |")
    L.append(f"| Project Name | {title} |")
    L.append(f"| Canvas Format | {fmt} ({vb.split()[-2]}×{vb.split()[-1]}) |")
    # ★ 必须等于 §IX 里实际的 Slide 数（ppt-master 的硬契约），
    #   所以取 build_slides 的真实产出量，而不是页数预算的上限。
    L.append(f"| Page Count | {n_pages} |")
    L.append("| Primary Language | zh-CN |")
    L.append(f"| Target Audience | {audience} |")
    L.append("| Communication Intent | 报告 + 说服：说明该研究方向的脉络与该作者的贡献 |")
    L.append("| Desired Audience Outcome | 听众能说出该方向的 3 条主线、"
             "2 个奠基工作与 1 个明确的研究缺口 |")
    L.append("| Core Message / Ask / Action | 该作者的工作处在"
             f"「{tagline}」的脉络中，并已在若干具体问题上给出可量化推进 |")
    L.append("| Delivery Context | 现场口头汇报（投影），讲述者配合幻灯片讲解 |")
    L.append("| Artifact Afterlife | 汇报后作为 PDF 分发给课题组；SVG 源留作后续复用 |")
    L.append("| Reading Mode | presentation |")
    L.append("| Content Strategy | 以《论文解读》的亮点与《研究方向综述》的网络结构为唯一素材，"
             "不引入外部新事实 |")
    L.append("| Design Style | 学术简报：克制配色、大字号、每页一个主张 |")
    L.append("| AI Image Acquisition Path | not applicable |")
    L.append("| Generation Mode | continuous |")
    L.append("| Spec Refinement | disabled |")
    L.append("| Speaker Notes | enabled — 由阶段 P 默认开启（讲稿化汇报场景） |")
    L.append("| Custom Animations | disabled — 工作流默认 |")
    L.append("| Narration Audio | disabled — 工作流默认 |")
    L.append(f"| Created Date | {today()} |")
    L.append("")
    L.append("## II. Canvas Specification")
    L.append("")
    L.append("| Property | Value |")
    L.append("| --- | --- |")
    L.append(f"| Format | {fmt} |")
    L.append(f"| Dimensions | {vb.split()[-2]} × {vb.split()[-1]} |")
    L.append(f"| viewBox | `{vb}` |")
    L.append("| Margins | 64 px 四周安全边距 |")
    L.append("| Content Area | 0 0 1280 720 内，左右各留 64 |")
    L.append("")
    L.append("## III. Visual Theme")
    L.append("")
    L.append("### Theme Style")
    L.append("")
    L.append("学术简报风格：白底、深蓝主色、单一强调色；每页一个核心主张，")
    L.append("数据用表格与数字块承载，**不使用装饰性插图**（本流程不产图）。")
    L.append("")
    L.append("### Color Scheme")
    L.append("")
    L.append("| Role | Hex | Usage |")
    L.append("| --- | --- | --- |")
    for role, hexv, use in PALETTE:
        L.append(f"| {role} | `{hexv}` | {use} |")
    L.append("")
    L.append("## IV. Typography System")
    L.append("")
    L.append("### Font Plan")
    L.append("")
    L.append("| Role | Font Family |")
    L.append("| --- | --- |")
    L.append("| Title | Source Han Sans SC, Microsoft YaHei, sans-serif |")
    L.append("| Body | Source Han Sans SC, Microsoft YaHei, sans-serif |")
    L.append("")
    L.append("### Font Size Hierarchy")
    L.append("")
    L.append("| Role | Size (px) |")
    L.append("| --- | --- |")
    L.append("| Cover Title | 56 |")
    L.append("| Section Title | 36 |")
    L.append("| Page Title | 32 |")
    L.append("| Body | 24 |")
    L.append("| Caption | 18 |")
    L.append("")
    L.append("## V. Layout Principles")
    L.append("")
    L.append("### Deck-wide Direction")
    L.append("")
    L.append("- 统一页眉：左侧章节名，右侧页码；页脚不重复作者信息。")
    L.append("- 内容区采用 12 列网格；每页最多 2 个视觉层级（一个主张 + 一组支撑）。")
    L.append("- 数字与量化结论**加大加粗并使用强调色**，这是本套材料最有说服力的部分。")
    L.append("- 引用文献时用 `第一作者 等, 期刊 年份` 的短式，完整著录见交付的 Markdown 报告。")
    L.append("")
    L.append("## VI. Icon Usage Specification")
    L.append("")
    L.append("- **Library**: none")
    L.append("- **Inventory**: 无（本套材料不使用图标，避免与数据争夺注意力）")
    L.append("")
    L.append("## VII. Visualization Reference List")
    L.append("")
    L.append("| Page | Family | Template | Usage |")
    L.append("| --- | --- | --- | --- |")
    L.append("")
    L.append("## VIII. Image Resource List")
    L.append("")
    L.append("| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | "
             "Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    # ★ 阶段 K 的图谱是**用户已有文件**：Acquire Via=user、Status=Existing，
    #   因此 Step 5 不会去"获取"它们（也就不会触发任何抓取）。
    for g in (graphs or []):
        w, h = g.get("w") or 0, g.get("h") or 0
        ratio = f"{w}:{h}" if w and h else "—"
        dim = f"{w}x{h}" if w and h else "—"
        L.append(f"| {g['name']} | {dim} | {ratio} | {g['title']} | "
                 f"Diagram | 整幅铺放，四周留安全边距；角落叠标题与一句导语 | "
                 f"no-crop | user | Existing | 阶段 K 知识图谱（{g['desc']}） | "
                 f"keep-as-is | full-page-visual |")
    # ── §IX：由 build_slides 统一产出，**页数就是实际块数** ─────────
    L.append("## IX. Content Outline")
    L.append("")
    for part_title, blocks in parts:
        L.append(f"### {part_title}")
        L.append("")
        L.extend(blocks)
    # ★ 这一行必须与 for 同级（4 空格）。曾因缩进多了一级而落进循环体，
    #   导致 §X 被重复写出 4 次 —— ppt-master 的 validate 直接报
    #   "section 'speaker_notes' appears more than once"。肉眼看不出来，
    #   是端到端自检把它挡下来的。
    L.append("## X. Speaker Notes Requirements")
    L.append("")
    L.append("- **Generation**: enabled")
    L.append("- **Filename**: match each SVG filename under `notes/`")
    L.append("- **Content**: 每页一段口语化讲稿，"
             "量化数字必须与正文一致；引用文献时念出「第一作者 等, 期刊 年份」")
    L.append("- **Total duration**: 12 分钟")
    L.append("- **Notes style**: formal")
    L.append("- **Presentation purpose**: report")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"> **素材来源**：`{source_rel}`（本 Spec 的全部事实均取自该文件，"
             "不引入外部新事实）。")
    L.append("> **生成方式**：由《流程》阶段 P 的 `ppt_brief.py` **确定性生成**，"
             "跳过了 ppt-master 默认的 Step 3 模板候选与 Step 4 两阶段确认"
             "（详见 `流程1/ppt-integration/SKILL.md`）。")
    L.append("")
    return "\n".join(L)


def gen_spec_lock(title, canvas_key, n_pages, audience) -> str:
    fmt, vb = CANVAS[canvas_key]
    L = []
    L.append("<!-- ppt-master-schema: spec-lock/v1 -->")
    L.append("# Execution Lock")
    L.append("")
    L.append("## canvas")
    L.append(f"- viewBox: {vb}")
    L.append(f"- format: {fmt}")
    L.append("")
    L.append("## communication")
    L.append("- primary_language: zh-CN")
    L.append(f"- audience: {audience}")
    L.append("- objective: 报告该作者的研究工作及其所在方向的综述网络")
    L.append(f"- core_message: {title} —— 在明确的学术脉络中给出可量化推进，"
             "并留有清晰缺口")
    L.append("- consumption_mode: presentation")
    L.append("")
    L.append("## mode")
    L.append("- mode: briefing")
    L.append("")
    L.append("## visual_style")
    L.append("- visual_style: custom")
    L.append("- visual_style_behavior: 学术简报：白底、深蓝主色、单一橙红强调色；"
             "每页一个核心主张；不使用装饰性插图")
    L.append("")
    L.append("## colors")
    for role, hexv, _ in PALETTE:
        L.append(f"- {role}: {hexv}")
    L.append("")
    L.append("## typography")
    L.append("- font_family: Source Han Sans SC, Microsoft YaHei, sans-serif")
    L.append("- title_family: Source Han Sans SC, Microsoft YaHei, sans-serif")
    L.append("- body_family: Source Han Sans SC, Microsoft YaHei, sans-serif")
    L.append("- title: 32")
    L.append("- body: 24")
    L.append("- caption: 18")
    L.append("")
    L.append("## icons")
    L.append("- library: none")
    L.append("- inventory: none")
    L.append("")
    L.append("## page_rhythm")
    for i in range(1, n_pages + 1):
        # 封面/章节页/结论页留白，其余常规密度
        rhythm = "breathing" if i in (1, 2, n_pages) else "anchor"
        L.append(f"- P{i:02d}: {rhythm}")
    L.append("")
    L.append("## pptx_structure")
    L.append("- mode: flat")
    L.append("")
    L.append("## forbidden")
    L.append("- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, "
             "`textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / "
             "event attributes, `<iframe>`")
    L.append("- HTML named entities in text; write typography as raw Unicode and "
             "escape XML reserved characters")
    L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    fmt, vb = CANVAS["ppt169"]
    assert fmt == "PPT 16:9" and vb == "0 0 1280 720"
    assert CANVAS["ppt43"][1] == "0 0 1024 768"
    print("画布表：通过")

    review = {"stats": {"最终保留": 12}, "profile_tags": [], "cards": [],
              "directions": [("分子与化学", 5), ("材料与结构", 3)],
              "roles": [("奠基工作", 2)], "paradigms": [],
              "foundation": [], "method_src": [], "consensus": [],
              "gaps": ["缺口 A"], "conclusions": ["结论 A"]}
    hl = [{"title": "Paper X", "year": 2024, "venue": "Nature",
           "one_line": "定位", "key_20": ["要点"], "gains": [], "direction_tags": []}]
    parts = build_slides(hl, review, PAGE_BUDGET["short"], title="测试标题")
    n_pages = sum(len(b) for _, b in parts)
    ds = gen_design_spec("测试标题", "ppt169", parts, "sources/x.md",
                         hl, review, "课题组")
    # ★ 硬契约：§I Page Count 必须等于 §IX 里实际的 Slide 数
    declared = int(re.search(r"\|\s*Page Count\s*\|\s*(\d+)\s*\|", ds).group(1))
    actual = len(re.findall(r"^#### Slide \d+", ds, re.M))
    assert declared == actual == n_pages, (declared, actual, n_pages)
    nums = [int(x) for x in re.findall(r"^#### Slide (\d+)", ds, re.M)]
    assert nums == list(range(1, n_pages + 1)), f"编号不连续：{nums}"
    print(f"页数自洽：通过（Page Count = §IX 实际 = {n_pages} 页，编号 1..{n_pages} 连续）")
    # ppt-master 的 heading-sections-v1 解析器先剥掉 "## " 再匹配 schema 里的
    # `^I\. Project Information$`，所以这里断言到**剥壳后**的标题形态。
    for pat in ["^## I\\. Project Information$", "^## II\\. Canvas Specification$",
                "^## III\\. Visual Theme$", "^## IV\\. Typography System$",
                "^## V\\. Layout Principles$", "^## VI\\. Icon Usage Specification$",
                "^## VII\\. Visualization Reference List$",
                "^## VIII\\. Image Resource List$",
                "^## IX\\. Content Outline$", "^## X\\. Speaker Notes Requirements$"]:
        assert re.search(pat, ds, re.M), pat
    assert ds.startswith("<!-- ppt-master-schema: design-spec/v1 -->")
    assert "[fill" not in ds and "]" not in ds.split("## I.")[1].split("## II.")[0], \
        "§I 表内出现未解析占位符"
    print("design_spec 骨架：通过（I–X 章节齐备，无未解析占位符）")

    lock = gen_spec_lock("测试标题", "ppt169", 12, "课题组")
    assert lock.splitlines()[0] == "<!-- ppt-master-schema: spec-lock/v1 -->"
    assert lock.splitlines()[1] == "# Execution Lock"
    for sec in ("canvas", "communication", "mode", "visual_style", "colors",
                "typography", "icons", "page_rhythm", "pptx_structure", "forbidden"):
        assert re.search(rf"^## {sec}$", lock, re.M), sec
    assert re.search(r"^- P01: ", lock, re.M) and re.search(r"^- P12: ", lock, re.M)
    assert "P13" not in lock
    # 只有 ## 段与 - 数据行（forbidden 段是列表项）
    for ln in lock.splitlines():
        if not ln.strip() or ln.startswith("<!--"):
            continue
        assert ln.startswith(("# ", "## ", "- ")), f"非法行：{ln!r}"
    print("spec_lock 骨架：通过（必需段齐备、P01–P12、只含 ## 段与 - 数据行）")

    assert PAGE_BUDGET["short"][1] == 14 and PAGE_BUDGET["long"][0] == 24
    print("页数预算：通过")
    print("\n✅ 自检全部通过")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("ppt")
    dry = "--dry-run" in sys.argv
    plan = load_json(CDPDIR / "prepare_plan.json", {}) or {}
    pages = _argval("pages") or plan.get("ppt_pages") or "standard"
    if pages not in PAGE_BUDGET:
        print(f"✗ --pages 取值 {pages!r} 不在 {list(PAGE_BUDGET)} 内")
        return 2
    canvas = _argval("canvas", "ppt169")
    if canvas not in CANVAS:
        print(f"✗ --canvas 取值 {canvas!r} 不在 {list(CANVAS)} 内")
        return 2
    title = _argval("title") or f"{AUTHOR} 研究工作汇报"
    project = _argval("project-name") or f"{AUTHOR}_文献汇报"
    lang = _argval("lang", "zh-CN")
    audience = _argval("audience") or "课题组老师与同学（同方向，熟悉基本概念）"

    ppt_dir = find_ppt_dir(_argval("ppt-dir", "") or "")
    if ppt_dir is None:
        print("✗ 找不到 ppt-master 仓库根（应包含 skills/ppt-master/SKILL.md）")
        print("  用 --ppt-dir=<路径> 显式指定，或设 PPT_MASTER_DIR 环境变量。")
        return 2

    highlights = collect_highlights()
    review = collect_review()
    if not highlights and not review["cards"]:
        print("✗ 两类素材都为空：既没有阶段 H 的解读卡，也没有阶段 H2 的综述卡。")
        print("  请先跑阶段 H 与阶段 H2，再执行本阶段。")
        return 2
    print(f"素材：论文亮点 {len(highlights)} 条；综述解读卡 {len(review['cards'])} 条")

    # ── v7.2：接入阶段 K 的图谱 + 方向综述整理 ──────────────────
    graphs = [] if "--no-graphs" in sys.argv else collect_graphs()
    dirmap = {} if "--no-graphs" in sys.argv else collect_direction_review()
    if graphs:
        print(f"图谱：{len(graphs)} 张（"
              + "、".join(g["name"] for g in graphs) + "）")
    print(f"方向综述整理：{len(dirmap)} 个方向"
          + ("（无 review 卡 → 需先跑阶段 H2 ② ③）" if not dirmap else ""))

    source_md = build_source_md(highlights, review, title,
                                graphs=graphs, dirmap=dirmap)
    n_low, n_high = PAGE_BUDGET[pages]
    parts = build_slides(highlights, review, PAGE_BUDGET[pages], title=title,
                         graphs=graphs, dirmap=dirmap)
    n_pages = sum(len(b) for _, b in parts)
    spec = gen_design_spec(title, canvas, parts,
                           f"sources/{AUTHOR}_汇报材料.md",
                           highlights, review, audience, graphs=graphs)
    lock = gen_spec_lock(title, canvas, n_pages, audience)
    if n_pages < n_low:
        print(f"ℹ 素材只支撑 {n_pages} 页（{pages} 档预算 {n_low}–{n_high} 页）—— "
              f"**按实际页数写 Spec**，不补内容凑页数。"
              f"想要更多页请先补阶段 H/H2 的素材。")

    if dry:
        print("\n--- 将要写入 ---")
        print(f"  ppt-master 根：{ppt_dir}")
        print(f"  项目名：{project}（画布 {canvas}，预算 {n_low}–{n_high} 页，"
              f"Spec 里写实际 {n_pages} 页）")
        print(f"  sources/{AUTHOR}_汇报材料.md  （{len(source_md)} 字符）")
        print(f"  design_spec.md                （{len(spec)} 字符）")
        print(f"  spec_lock.md                  （{len(lock)} 字符）")
        print("  + 调 project_manager.py init / import-sources")
        return 0

    # ── ① 初始化 ppt-master 项目 ──────────────────────────────
    pm = ppt_dir / "skills" / "ppt-master" / "scripts" / "project_manager.py"
    if not pm.exists():
        print(f"✗ 找不到 {pm}")
        return 2
    init = run([sys.executable, str(pm), "init", project, "--format", canvas],
               cwd=ppt_dir)
    if init.returncode != 0:
        print("✗ project_manager.py init 失败：")
        print(init.stdout[-2000:] or init.stderr[-2000:])
        return 1
    proj_path = parse_project_path(init.stdout)
    if not proj_path:
        # 兜底：取 projects/ 下最新目录
        cands = sorted((ppt_dir / "projects").glob(f"{project}*"),
                       key=lambda p: p.stat().st_mtime)
        proj_path = cands[-1] if cands else None
    if not proj_path or not proj_path.is_dir():
        print("✗ 无法确定新建项目路径；init 输出：")
        print(init.stdout[-2000:])
        return 1
    print(f"✓ ppt-master 项目：{proj_path}")

    # ── ② 写入 source 与 spec/lock ────────────────────────────
    (proj_path / "sources").mkdir(exist_ok=True)
    # ★ 图谱复制进项目 images/：ppt-master 的 `<image href>` 从项目内取图，
    #   不复制过去就会指向项目外路径（导出的 PPTX 里变成断链）。
    if graphs:
        imgdir = proj_path / "images"
        imgdir.mkdir(exist_ok=True)
        import shutil as _sh
        for g in graphs:
            _sh.copy2(g["path"], imgdir / g["name"])
        print(f"✓ 已复制 {len(graphs)} 张图谱 → {imgdir}")
    src = proj_path / "sources" / f"{AUTHOR}_汇报材料.md"
    src.write_text(source_md, encoding="utf-8")
    (proj_path / "design_spec.md").write_text(spec, encoding="utf-8")
    (proj_path / "spec_lock.md").write_text(lock, encoding="utf-8")
    print(f"✓ 已写入 sources/{src.name} / design_spec.md / spec_lock.md")

    # ── ③ 导入 source（让 ppt-master 的 intake 也认账）──────────
    imp = run([sys.executable, str(pm), "import-sources", str(proj_path), str(src)],
              cwd=ppt_dir)
    if imp.returncode != 0:
        print("⚠ import-sources 未成功（不影响手写 SVG 路径，但请知悉）：")
        print((imp.stdout or imp.stderr)[-1200:])

    # ── ④ 校验（ppt-master 自己的校验器，不是我方自说自话）──────
    val = run([sys.executable, str(pm), "validate", str(proj_path)], cwd=ppt_dir)
    ok = val.returncode == 0
    print(("✓" if ok else "⚠") + " project_manager.py validate："
          + ("通过" if ok else "未通过"))
    if not ok:
        print((val.stdout or val.stderr)[-1500:])

    sk = ppt_dir / "skills" / "ppt-master" / "SKILL.md"
    print("\n下一步（阶段 P 剩余部分，由 agent 按 ppt-master 流程执行）：")
    print(f"  1) 读 {sk}")
    print(f"  2) **跳过 Step 3 / Step 4 的两阶段确认** —— design_spec.md 与 "
          f"spec_lock.md 已生成，等价于 Stage 1 + Stage 2 已确认（简化 Strategist）")
    if graphs:
        print(f"  3) Step 5 图像获取：**跳过** —— §VIII 里的 {len(graphs)} 张图谱是"
              f" `Acquire Via: user` / `Status: Existing`（已在项目 images/ 内，无需获取）")
    else:
        print(f"  3) Step 5 图像获取：跳过（§VIII 无数据行；如需图谱先跑阶段 K）")
    print(f"  4) Step 6 **手写** {proj_path / 'svg_output'} 下的每页 SVG")
    print(f"  5) Step 7 串行导出：finalize_svg.py → svg_to_pptx.py（先跑 "
          f"svg_quality_checker.py 到 0 error）")
    print(f"\n项目路径：{proj_path}")
    journal("P", "ppt.brief", project=str(proj_path), canvas=canvas,
            n_pages=n_pages, n_highlights=len(highlights),
            n_review_cards=len(review["cards"]), validate_ok=ok, lang=lang)
    log.info(f"✓ 阶段 P（材料与 Spec 生成）完成 → {proj_path}")
    return 0


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def parse_project_path(stdout: str) -> pathlib.Path | None:
    m = re.search(r"Project created:\s*(.+)", stdout or "")
    if not m:
        m = re.search(r"Project initialized:\s*(.+)", stdout or "")
    if not m:
        return None
    p = pathlib.Path(m.group(1).strip())
    return p if p.is_dir() else None


if __name__ == "__main__":
    raise SystemExit(main())
