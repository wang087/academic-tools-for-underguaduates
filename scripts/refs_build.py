# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H2 ③：回收综述解读卡 → 生成《<作者名>_研究方向综述.md》（review 结果）
#
# 报告结构（固定）：
#   〇 数据来源与限制        —— 先说清楚覆盖度，避免读者高估
#   一 综述网络概览          —— 统计口径全部公开
#   二 研究方向分布          —— 受控标签归一后计数 + 未归一标签如实列出
#   三 方法范式分布
#   四 网络角色分布          —— 奠基 / 方法源头 / 共识综述 / 争议焦点 / 最新进展
#   五 逐篇解读卡            —— 与作者论文解读同一套四步规范
#   六 研究脉络（按年份）
#   七 关键结论汇总
#   八 研究缺口与展望        —— 全部来自 open_questions，不臆造
#   九 缺卡与限制申报        —— 缺卡 / 无摘要 / schema 不合规，逐条列出
#
# 诚实性优先：缺卡的篇目、仅摘要级的篇目全部**显式列出**，不静默略过。
# 本脚本**只读本地文件**，不发起任何网络请求。
#
# 综述解读档位（`review_interpret_tier`）：只做「读档位 → 报告开头与末尾摘要显示」，
#   **不改任何既有渲染行为**（默认 L3，等于现状）。读取优先级：
#   task packet → manifest.json → .work_cdp/prepare_plan.json → 默认 "L3"。
#   档位是参数不是闸门，读不到只回退、不报错。语义见 skill：`paper-interpretation-writing`。
#
# 输入（<WORKDIR>/.work_cdp/refs/）：
#   manifest.json     refs_prepare.py 产出
#   tasks/*.json      任务包（缺卡时也能列进「缺卡」清单）
#   results/*.json    subagent 产出的解读卡
#   final_list.json   阶段 H2 ① 产出（取相关性排序与统计）
#
# 产出：
#   <WORKDIR>/<作者名>_研究方向综述.md          review 结果（PPT 的素材之一）
#
# 用法：
#   uv run python 流程1/scripts/refs_build.py
#     --self-test       纯逻辑自检（不联网）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_ORCID
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys

from collections import Counter

from logutil import run_root as _run_root  # noqa: E402

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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
REFDIR = CDPDIR / "refs"
TASKDIR = REFDIR / "tasks"
RESDIR = REFDIR / "results"
TPL = _HERE.parent / "templates"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
ORCID = os.environ.get("PAPER_ORCID", "").strip()
AFFIL = os.environ.get("PAPER_AFFIL", "").strip()


# ══════════════════════════════════════════════════════════════
# 综述解读档位（阶段 H2 / R2 共用 `review_interpret_tier`）
#
# 档位是**参数不是闸门**：读不到就回退默认 L3，绝不中断汇总。
# 本脚本**只负责显示档位**（报告开头一行 + 末尾摘要），不改任何既有渲染逻辑 ——
#   档位改变的是细节量，而 L1/L2 的差异化渲染属于后续批次。
# 这里自包含一份助手而不 import refs_prepare：汇聚器与组装器的方向反转会带来
#   模块级副作用（见 _fix_streams 注释），几十行自包含更稳。
# ══════════════════════════════════════════════════════════════
TIER_KEY = "review_interpret_tier"
TIER_VALUES = ("L1", "L2", "L3")
DEFAULT_TIER = "L3"                         # 综述类交付物的默认档位
TIER_LABELS = {"L1": "精简", "L2": "详细", "L3": "完整"}
PLAN_PATH = CDPDIR / "prepare_plan.json"


def normalize_tier(raw) -> str:
    """把任意输入规范化成 L1/L2/L3（大小写不敏感）；无法识别返回空串。"""
    s = str(raw or "").strip().upper()
    return s if s in TIER_VALUES else ""


def plan_tier(plan_path: pathlib.Path | None = None) -> tuple[str, str]:
    """读阶段 0 的 `review_interpret_tier`（返回 (档位, 提示语)；提示语非空=发生了回退）。"""
    p = plan_path or PLAN_PATH
    if not p.exists():
        return DEFAULT_TIER, f"找不到 {p}"
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return DEFAULT_TIER, f"{p.name} 解析失败（{e}）"
    raw = plan.get(TIER_KEY) if isinstance(plan, dict) else None
    got = normalize_tier(raw)
    if not got:
        return DEFAULT_TIER, f"{p.name} 的 {TIER_KEY}={raw!r} 非法"
    return got, ""


def resolve_tier(tasks: list, man: dict | None) -> tuple[str, str]:
    """档位优先级：**task packet → manifest → prepare_plan.json → 默认 L3**。

    返回 (档位, 来源说明)。任务包是 prepare 阶段逐条落盘的事实，
    比 manifest 更贴近「这篇文献当时按什么档位解读」；两者都没有才回头看阶段 0 计划。
    """
    for t in tasks or []:
        got = normalize_tier((t.get("packet") or {}).get("interpret_tier"))
        if got:
            return got, "task packet"
    got = normalize_tier((man or {}).get("interpret_tier"))
    if got:
        return got, "manifest.json"
    got, why = plan_tier()
    if not why:
        return got, PLAN_PATH.name
    return got, f"默认回退（{why}）"


def tier_banner(tier: str) -> str:
    """报告开头那一行档位说明（**只是新增一行，不改任何既有内容**）。"""
    return (f"解读档位：**{tier} {TIER_LABELS.get(tier, '')}**"
            f"（综述类默认 L3；可在阶段 0 改档）")


# 与 refs_prepare.REQUIRED_FULL 保持一致（此处独立声明，避免 import 副作用）
REQUIRED = ("doi", "one_line", "problem_solved", "methods",
            "verified_conclusions", "key_20", "quantified_gains",
            "open_questions", "direction_tags", "paradigm_tags",
            "network_role", "relation_to_author", "evidence")

NETWORK_ROLES = ["奠基工作", "方法源头", "共识综述", "争议焦点", "最新进展"]


def load_json(p: pathlib.Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] {p.name} 解析失败：{e}", file=sys.stderr)
        return default


# 本脚本用 `--self-test` 这一轻量开关，没有 argparse —— 因此必须自己认 `--help`，
# 否则 `--help` 会被**静默忽略**并当成一次正常汇总去跑（实测退出码 2 且信息误导）。
USAGE = """\
阶段 H2 ③：回收综述解读卡 → 生成《<作者名>_研究方向综述.md》

用法：
  uv run python 流程1/scripts/refs_build.py [选项]

选项：
  --self-test   纯逻辑自检（不联网）
  -h, --help    显示本帮助

输入：<WORKDIR>/.work_cdp/refs/{manifest.json,tasks/,results/,final_list.json}
产出：<WORKDIR>/<作者名>_研究方向综述.md

解读档位：task packet → manifest.json → .work_cdp/prepare_plan.json → 默认 L3
可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_ORCID
"""


def as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    return [v]


def one_line(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def load_vocab() -> dict:
    p = CDPDIR / "tag_vocabulary.json"
    if not p.exists():
        p = TPL / "方法范式标签词表.json"
    return load_json(p, {}) or {}


def normalize_tag(tag: str, allowed: list[str], aliases: dict) -> tuple[str, bool]:
    t = str(tag or "").strip()
    if not t:
        return "", True
    if t in allowed:
        return t, True
    low = t.lower()
    if low in aliases:
        return aliases[low], True
    flat = re.sub(r"[\s\-_]+", "", low)
    for a in allowed:
        if re.sub(r"[\s\-_]+", "", a.lower()) == flat:
            return a, True
    for k, v in aliases.items():
        if re.sub(r"[\s\-_]+", "", str(k).lower()) == flat:
            return v, True
    return t, False


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0

    log = get("refs")
    man = load_json(REFDIR / "manifest.json")
    if not man:
        print("[X] 找不到 .work_cdp/refs/manifest.json")
        print("  请先运行：uv run python 流程1/scripts/refs_prepare.py")
        return 2

    tasks = []
    for t in man.get("tasks") or []:
        tp = pathlib.Path(t.get("task") or "")
        packet = load_json(tp, {}) if tp.exists() else {}
        rp = pathlib.Path(t.get("result") or "")
        card = load_json(rp, None) if rp.exists() else None
        tasks.append({"meta": t, "packet": packet, "card": card, "result_path": rp})

    cards = [t for t in tasks if isinstance(t["card"], dict)]
    missing = [t for t in tasks if not isinstance(t["card"], dict)]
    bad = [t for t in cards if any(k not in t["card"] for k in REQUIRED)]

    # ── 综述解读档位：task packet → manifest → prepare_plan.json → L3 ──
    tier, tier_src = resolve_tier(tasks, man)

    vocab = load_vocab()
    allowed_d = vocab.get("direction_tags_vocabulary") or []
    allowed_p = vocab.get("paradigm_tags_vocabulary") or []
    aliases = vocab.get("aliases") or {}
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}
    al_p = {str(k).lower(): v for k, v in (aliases.get("_范式别名") or {}).items()}

    dirs, parads, roles = Counter(), Counter(), Counter()
    unnorm = Counter()
    for t in cards:
        c = t["card"]
        for d in as_list(c.get("direction_tags")):
            n, ok = normalize_tag(d, allowed_d, al_d)
            if n:
                dirs[n] += 1
                if not ok:
                    unnorm[one_line(d)] += 1
        for p in as_list(c.get("paradigm_tags")):
            n, ok = normalize_tag(p, allowed_p, al_p)
            if n:
                parads[n] += 1
                if not ok:
                    unnorm[one_line(p)] += 1
        r = one_line(c.get("network_role"))
        if r:
            key = next((x for x in NETWORK_ROLES if x in r), r)
            roles[key] += 1

    final = load_json(REFDIR / "final_list.json", {}) or {}
    fstats = final.get("stats") or {}
    n_all, n_card = len(tasks), len(cards)
    # ── 证据层级统计（v7.2：综述可能有 OA 全文）─────────────────
    ev_of = lambda t: ((t["packet"].get("evidence") or {}).get("source") or "—")
    n_ft = len([t for t in tasks if ev_of(t) == "fulltext"])
    n_dabs = len([t for t in tasks if ev_of(t) == "detailed-abstract"])
    n_abs = len([t for t in tasks if ev_of(t) == "abstract"])
    n_meta = len([t for t in tasks if ev_of(t) == "metadata-only"])
    n_review = len([t for t in tasks if t["meta"].get("is_review")])

    # ── 按相关性排序（与 final_list 一致；缺则按被引）────────────
    order = {m["slug"]: i for i, m in enumerate(man.get("tasks") or [])}
    tasks_sorted = sorted(tasks, key=lambda t: order.get(t["meta"].get("slug"), 9999))

    L = []
    L.append(f"# {AUTHOR} 研究方向综述（Review）\n")
    L.append(f"> 作者：{AUTHOR}" + (f"（{AFFIL}）" if AFFIL else ""))
    if ORCID:
        L.append(f"> ORCID：https://orcid.org/{ORCID}")
    L.append(f"> 共 **{n_all}** 条文献 ｜ 已解读 **{n_card}** 条 ｜ 综述 **{n_review}** 条")
    L.append("> 生成方式：反向查找作者论文的参考文献 → 去重与相关性整理 → "
             "并行 subagent 逐条精读（流程见 `流程1/subagent-driven-review/SKILL.md`）；"
             "**全程无网络抓取**。\n")
    # 档位是**参数不是闸门**：显式写在报告开头，读者才知道这份综述的细节量档位
    #   （L1/L2/L3 只差细节量，不差语气强度）。渲染逻辑本身不随档位变化。
    L.append(f"> {tier_banner(tier)}\n")

    # ── 〇 数据来源与限制 ──────────────────────────────────────
    L.append("## 〇、数据来源与限制\n")
    L.append(f"- 文献来源：作者论文的**参考文献**（OpenAlex `referenced_works`），"
             f"经去重与研究方向相关性整理后保留 {fstats.get('最终保留', '—')} 条。")
    L.append(f"- **证据层级**（v7.2 起分级）：全文 **{n_ft}** 条 ｜ "
             f"详细摘要 **{n_dabs}** 条 ｜ 常规摘要 **{n_abs}** 条 ｜ "
             f"仅有题录 **{n_meta}** 条。")
    L.append("- **开放获取的综述**已通过 Europe PMC `fullTextXML` 取到**全文**"
             "（结构化开放获取接口，非抓出版社网页）→ 这部分结论的**置信度显著更高**；"
             "非 OA 的取**详细摘要**；两者都拿不到时只有题录，已如实标注。")
    L.append("- [WARN] 本阶段文献**不是作者本人的论文**，本流程只下载了作者自己的全文。"
             "**非全文级的结论不能等同于通读全文得到的结论** —— "
             "报告在每条解读卡上标注了证据来源，请按层级区分可信度。")
    L.append("- [WARN] 被引次数来自 OpenAlex `cited_by_count`，"
             "与 Google Scholar / Web of Science **口径不同、数值不同**，"
             "不可跨库直接比较。")
    L.append("")

    # ── 一 综述网络概览 ────────────────────────────────────────
    L.append("## 一、综述网络概览\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    for k in ("阶段 D2 高质量文献", "去重后", "最终保留", "被剔除", "其中综述"):
        if k in fstats:
            L.append(f"| {k} | {fstats[k]} |")
    L.append(f"| 本次解读任务 | {n_all} |")
    L.append(f"| 成功解读 | {n_card} |")
    L.append(f"| 缺卡 | {len(missing)} |")
    L.append(f"| schema 不合规 | {len(bad)} |")
    L.append("")
    L.append("> 「综述网络」的含义：一条边 = 作者的一篇论文引用了这条文献。"
             "被引用次数越多，说明它在作者研究脉络中越基础。\n")

    # ── 二 研究方向分布 ────────────────────────────────────────
    L.append("## 二、研究方向分布\n")
    if dirs:
        L.append("| 方向 | 篇数 |")
        L.append("|---|---|")
        for k, v in dirs.most_common():
            L.append(f"| {k} | {v} |")
    else:
        L.append("> 暂无方向标签。")
    L.append("")
    if unnorm:
        L.append(f"**未归一标签**（受控词表缺词 or 标注漂移，**已保留原值、未丢弃**）："
                 + "、".join(f"{k}({v})" for k, v in unnorm.most_common()))
        L.append("")

    # ── 三 方法范式分布 ────────────────────────────────────────
    L.append("## 三、方法范式分布（Paradigm）\n")
    if parads:
        L.append("| 范式 | 篇数 |")
        L.append("|---|---|")
        for k, v in parads.most_common():
            L.append(f"| {k} | {v} |")
    else:
        L.append("> 暂无范式标签。")
    L.append("")

    # ── 四 网络角色分布 ────────────────────────────────────────
    L.append("## 四、网络角色分布\n")
    L.append("> 角色刻画的是「这条文献在综述网络里干什么」，"
             "与「它属于哪个方向」是两个维度。\n")
    if roles:
        L.append("| 角色 | 篇数 |")
        L.append("|---|---|")
        for k, v in roles.most_common():
            L.append(f"| {k} | {v} |")
    else:
        L.append("> 暂无角色标注。")
    L.append("")

    # ── 五 逐篇解读卡 ──────────────────────────────────────────
    L.append("## 五、逐篇解读\n")
    L.append("> 规范与作者论文解读一致："
             "方法论 / 已验证结论 / 二八关键信息 / 量化提升数据，四步齐备。\n")
    if bad:
        L.append(f"> [WARN] 有 **{len(bad)}** 条解读卡 schema 不合规，"
                 "已在第九节列出，请**重派 subagent** 而不是手工修补。\n")
    for t in tasks_sorted:
        m, c = t["meta"], t["card"]
        idx = m.get("seq")
        title = m.get("title") or "（无题名）"
        tags = []
        if m.get("is_review"):
            tags.append("综述")
        if m.get("n_sources"):
            tags.append(f"被作者 {m['n_sources']} 篇引用")
        if m.get("cited_by_count"):
            tags.append(f"总被引 {m['cited_by_count']}")
        L.append(f"### {idx}. {title}\n")
        L.append(f"- 期刊：{m.get('venue') or '—'}（{m.get('year') or '—'}）"
                 f" ｜ DOI：{m.get('doi') or '—'}")
        L.append(f"- 相关性：{m.get('relevance')} ｜ " + " ｜ ".join(tags))
        if not isinstance(c, dict):
            L.append("- [STOP] **缺解读卡** —— 本条未产出 result.json。")
            L.append("")
            continue
        ev = (c.get("evidence") or {}).get("source") or "—"
        L.append(f"- 证据层级：**{ev}**")
        if c.get("network_role"):
            L.append(f"- 网络角色：{one_line(c.get('network_role'))}")
        if c.get("relation_to_author"):
            L.append(f"- 与作者研究的关系：{one_line(c.get('relation_to_author'))}")
        L.append("")
        if c.get("one_line"):
            L.append(f"**一句话定位**：{one_line(c['one_line'])}\n")
        if c.get("problem_solved"):
            L.append(f"**解决的问题**：{one_line(c['problem_solved'])}\n")
        for label, key in (("明确的方法论", "methods"),
                           ("经过验证的结论", "verified_conclusions"),
                           ("关键信息（二八原则）", "key_20"),
                           ("待验证的问题", "open_questions")):
            vals = as_list(c.get(key))
            if vals:
                L.append(f"**{label}**")
                for v in vals:
                    L.append(f"- {one_line(v)}")
                L.append("")
        qg = c.get("quantified_gains")
        if isinstance(qg, list) and qg:
            L.append("**量化提升数据**\n")
            L.append("| 指标 | 数值 | 条件/基线 |")
            L.append("|---|---|---|")
            for g in qg:
                if isinstance(g, dict):
                    L.append(f"| {one_line(g.get('metric'))} | "
                             f"{one_line(g.get('value'))} | {one_line(g.get('context'))} |")
                else:
                    L.append(f"| {one_line(g)} | | |")
            L.append("")
        elif isinstance(qg, list):
            L.append("**量化提升数据**：`[]`（原文无量化提升数据，未编造）\n")

    # ── 六 研究脉络 ────────────────────────────────────────────
    L.append("## 六、研究脉络（按年份）\n")
    by_year = {}
    for t in tasks_sorted:
        y = t["meta"].get("year") or 0
        by_year.setdefault(y, []).append(t)
    for y in sorted(by_year, reverse=True):
        L.append(f"### {y}\n")
        for t in by_year[y]:
            c = t["card"]
            note = one_line(c.get("one_line")) if isinstance(c, dict) else "（缺卡）"
            L.append(f"- **{t['meta'].get('title') or '（无题名）'}** —— {note}")
        L.append("")

    # ── 七 关键结论汇总 ────────────────────────────────────────
    L.append("## 七、关键结论汇总\n")
    L.append("> 全部来自各条解读卡的 `verified_conclusions`（有数据/实验支撑的结论），"
             "未做二次推断。\n")
    n = 0
    for t in tasks_sorted:
        c = t["card"]
        if not isinstance(c, dict):
            continue
        for v in as_list(c.get("verified_conclusions")):
            n += 1
            L.append(f"{n}. {one_line(v)}"
                     f" <span style=\"color:#888\">〔{t['meta'].get('title') or ''}〕</span>")
    if not n:
        L.append("> 暂无可汇总的已验证结论。")
    L.append("")

    # ── 八 研究缺口与展望 ──────────────────────────────────────
    L.append("## 八、研究缺口与展望\n")
    L.append("> 全部来自各条解读卡的 `open_questions`（作者自述局限 + 尚未解决之处），"
             "**本流程不自行发明研究缺口**。\n")
    m2 = 0
    for t in tasks_sorted:
        c = t["card"]
        if not isinstance(c, dict):
            continue
        for v in as_list(c.get("open_questions")):
            m2 += 1
            L.append(f"{m2}. {one_line(v)}"
                     f" <span style=\"color:#888\">〔{t['meta'].get('title') or ''}〕</span>")
    if not m2:
        L.append("> 暂无可汇总的待验证问题。")
    L.append("")

    # ── 九 缺卡与限制申报 ──────────────────────────────────────
    L.append("## 九、缺卡与限制申报\n")
    L.append(f"- 任务包 **{n_all}** 条，解读卡 **{n_card}** 条，"
             f"缺卡 **{len(missing)}** 条，schema 不合规 **{len(bad)}** 条。")
    if missing:
        L.append("\n**缺卡清单**（应重派 subagent，不应手工补）：\n")
        for t in missing:
            L.append(f"- [{t['meta'].get('seq')}] {t['meta'].get('title') or '（无题名）'}"
                     f" → 期望结果文件：`{t['result_path']}`")
    if bad:
        L.append("\n**schema 不合规清单**：\n")
        for t in bad:
            lack = [k for k in REQUIRED if k not in t["card"]]
            L.append(f"- [{t['meta'].get('seq')}] {t['meta'].get('title') or '（无题名）'}"
                     f" 缺少字段：{', '.join(lack)}")
    only_meta = [t for t in tasks
                 if (t["packet"].get("evidence") or {}).get("source") == "metadata-only"]
    if only_meta:
        L.append(f"\n**仅有题录、无摘要（{len(only_meta)} 条）** —— "
                 "这些条目的解读**只能依据题名与期刊**，可信度低于摘要级：\n")
        for t in only_meta:
            L.append(f"- [{t['meta'].get('seq')}] {t['meta'].get('title') or '（无题名）'}")
    if not (missing or bad or only_meta):
        L.append("\n无缺卡、无 schema 问题、无仅有题录的条目。")
    L.append("")

    md = "\n".join(L)
    # 单一交付文件（同 interpret_build：不再写 `_完整版.md` 别名）
    p = WD / f"{AUTHOR}_研究方向综述.md"
    p.write_text(md, encoding="utf-8")

    print(f"[OK] 综述报告：{p}")
    print(f"   任务 {n_all} / 解读卡 {n_card} / 缺卡 {len(missing)} / "
          f"schema 不合规 {len(bad)}")
    print(f"   解读档位：{tier}（{TIER_LABELS.get(tier, '')}；来源：{tier_src}）")
    if missing:
        print(f"   [WARN] 缺卡 {len(missing)} 条 —— 请**重派 subagent**，不要手工修补。")
    journal("H2", "refs.build", n_tasks=n_all, n_cards=n_card,
            n_missing=len(missing), n_bad=len(bad), interpret_tier=tier)
    log.info(f"[OK] 阶段 H2 ③（研究方向综述）完成 → {p} [解读档位 {tier}]")
    return 0


def self_test() -> int:
    allowed_d = ["单细胞与多组学", "计算与模拟"]
    al_d = {"scRNA": "单细胞与多组学"}
    assert normalize_tag("scRNA", allowed_d, al_d) == ("单细胞与多组学", True)
    assert normalize_tag("单细胞与多组学", allowed_d, al_d) == ("单细胞与多组学", True)
    got, ok = normalize_tag("自定义:冷冻电镜", allowed_d, al_d)
    assert (got, ok) == ("自定义:冷冻电镜", False)
    print("标签归一：通过")

    assert as_list(None) == [] and as_list("x") == ["x"] and as_list(["a", "b"]) == ["a", "b"]
    assert one_line(" a\n b ") == "a b"
    print("工具函数：通过")

    assert len(REQUIRED) == 13, REQUIRED
    assert "network_role" in REQUIRED and "relation_to_author" in REQUIRED
    print("schema 必需字段：通过（11 基础 + 2 综述网络）")

    # 档位：读取链与显示行都固化在自检里，避免以后被静默改掉
    assert normalize_tier("l3") == "L3" and normalize_tier("L9") == ""
    assert normalize_tier(None) == ""
    assert resolve_tier([{"packet": {"interpret_tier": "L1"}}],
                        {"interpret_tier": "L2"})[0] == "L1"
    assert resolve_tier([{"packet": {}}], {"interpret_tier": "L2"})[0] == "L2"
    assert tier_banner("L3") == "解读档位：**L3 完整**（综述类默认 L3；可在阶段 0 改档）"
    print("解读档位：通过（packet → manifest → plan → L3；显示行固定）")
    print("\n[OK] 自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
