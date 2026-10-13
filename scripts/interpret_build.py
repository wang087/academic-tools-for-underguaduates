# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H ④：回收 subagent 的解读卡 → 生成《<作者名>_论文解读.md》
#
# 报告结构（固定，对应主流程 SKILL §4 阶段 H 的四项要求）：
#   ① 时间线要点串联（按发表先后）
#   ② 逐篇解读（方法论 / 已验证结论 / 二八关键信息 / 量化提升 / 待验证问题）
#   ③ 关键词统计（语义词族合并计数 + 单一条目频次）
#   ④ 主要研究方向归纳
#   ⑤ 研究方法范式（paradigm）分析
#   ⑥ 缺卡与限制（如实申报）
#
# 解读档位（interpret_tier）：只决定**逐篇卡片的细节量**，不改动报告结构。
#   优先级：任务包 tasks/*.json → manifest.json → prepare_plan.json → 默认 L2。
#     L1 精简：定位 + 问题 + 关键信息 + 关键数字表（≤5 行，且**不列图表出处**）；
#     L2 详细：完整四步卡片（默认档；渲染与引入档位前**逐字节一致**）；
#     L3 完整：L2 + 把 open_questions 明示为「作者自述局限与待验证问题」
#              + 卡片末尾一行证据强度提示。
#   三档只差细节量，不差语气强度；规范见 skill：paper-interpretation-writing
#   （references/tier-specs.md）。
#
# 诚实性优先：缺卡的篇目、仅摘要级的篇目、被截断的正文，全部**显式列出**，
#   不静默略过。宁可报告看起来「不完整」，也不能让读者以为覆盖了全部。
#
# 本脚本**只读本地文件**，不发起任何网络请求。
#
# 输入（均在 <WORKDIR>/.work_cdp/interpret/）：
#   manifest.json      —— interpret_prepare.py 产出（顶层含 interpret_tier）
#   tasks/*.json       —— 单篇任务包（取题录与档位；缺卡时也能列进「缺卡」清单）
#   results/*.json     —— subagent 产出的解读卡（schema 见子流程手册 § 6）
# 另读 <WORKDIR>/.work_cdp/crossref_all.json（关键词语料的标题/摘要）
# 与 <WORKDIR>/.work_cdp/prepare_plan.json（档位回退链的第三级）
#
# 产出：
#   <WORKDIR>/<作者名>_论文解读.md          交付物（单一文件，不再写 _完整版 别名）
#
# 用法：
#   uv run python interpret_build.py
#     --families=<families.json>   语义词族合并表（可选；缺省只出单条目频次）
#     --help                       显示帮助
#
# 可移植配置：PAPER_WORKDIR
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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
INDIR = CDPDIR / "interpret"
TASKDIR = INDIR / "tasks"
RESDIR = INDIR / "results"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
ORCID = os.environ.get("PAPER_ORCID", "").strip()
AFFIL = os.environ.get("PAPER_AFFIL", "").strip()

PLAN_PATH = CDPDIR / "prepare_plan.json"     # 档位回退链的第三级

# ── 解读档位（interpret_tier）────────────────────────────────
#   三档只差**细节量**（L1 ⊂ L2 ⊂ L3），不差语气强度；规范见
#     skill：paper-interpretation-writing/references/tier-specs.md。
#   档位是**参数**不是闸门：三级都读不到就回退默认 L2，绝不因此中断汇总。
TIER_VALUES = ("L1", "L2", "L3")
DEFAULT_TIER = "L2"
TIER_LABEL = {"L1": "精简", "L2": "详细", "L3": "完整"}

# 解读引擎（v17）：与 interpret_prepare.py / prepare_stage.py 三处同名同值。
#   legacy     = 只有 11 字段解读卡（默认；历史报告逐字节不变）
#   paper-card = 另有 16 节 Paper Card + 结构审计 + 数字回查原文
ENGINE_VALUES = ("legacy", "paper-card")
DEFAULT_ENGINE = "legacy"
ENGINE_LABEL = {"legacy": "原有解读卡", "paper-card": "16 节 Paper Card"}

USAGE = """阶段 H ④：回收解读卡 → 生成《<作者名>_论文解读.md》（只读本地文件，不联网）

用法：
  python 流程1/scripts/interpret_build.py [选项]

选项：
  --families=<json>  语义词族合并表（缺省只出单条目频次）
  -h, --help         显示本帮助并退出

解读档位（不改报告结构，只改逐篇卡片的细节量）：
  优先级：任务包 tasks/*.json 的 interpret_tier
        → manifest.json 的 interpret_tier
        → prepare_plan.json 的 interpret_tier
        → 默认 L2（详细）
  L1 精简：定位 + 本文有效解决了什么问题 + 关键信息 + 关键数字表（≤5 行）
  L2 详细：完整四步卡片（默认档）
  L3 完整：L2 + 明示「作者自述局限与待验证问题」+ 末尾一行证据强度提示
"""


REQUIRED = ("doi", "one_line", "problem_solved", "methods",
            "verified_conclusions", "key_20", "quantified_gains",
            "open_questions", "direction_tags", "paradigm_tags", "evidence")

# 英文停用词（仅用于关键词统计，不参与任何判定）
STOP = set("""
a an the and or but if then than that this these those of in on at to for from by with without
as is are was were be been being it its their there here we our us they them he she his her
not no nor so such can could may might must shall should will would do does did done have has had
using used use uses study studies paper article results result method methods show shown showed
demonstrate demonstrated reveal revealed report reported present presented propose proposed
new novel high low increase increased decrease decreased effect effects analysis data
however moreover furthermore therefore thus also both each other more most less least
between among during after before under over into out up down about above below
one two three all any own per via see fig tab figref day days ago yet nor
""".split())


def arg(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def load_json(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def as_list(v):
    """容忍 subagent 把数组写成字符串 / null。"""
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "", {})]
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [v]


def one_line(v) -> str:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return re.sub(r"\s+", " ", str(v or "")).strip()


def tokenize(text: str) -> list[str]:
    """英文单词（≥3 字符、含数字、去停用词）+ 中文 2-gram。

    为什么门槛是 3 而不是 4，且必须允许数字：
      生物医学语料里 **CD8 / CD4 / MYC / BAF / TNF / IPS / CAR / DNA / RNA /
      Th17 / IL17** 全是核心词。旧实现用 `[a-z][a-z\\-]{3,}`（≥4 字符且不含数字），
      把它们**整类丢掉** —— 词族表里 cd8/myc/baf 恒为 0，看起来像「该概念没出现」，
      实际是分词器根本没产出这些 token。实测 2026-09-18 郭傲案例暴露。
    """
    out = []
    t = (text or "").lower()
    for w in re.findall(r"[a-z][a-z0-9\-]{2,}", t):
        w = w.strip("-")
        if len(w) >= 3 and w not in STOP:
            out.append(w)
    for run in re.findall(r"[一-鿿]{2,}", t):
        for i in range(len(run) - 1):
            out.append(run[i:i + 2])
    return out


# ══════════════════════════════════════════════════════════════
# 解读档位：解析 + 按档渲染逐篇卡片
# ══════════════════════════════════════════════════════════════

def _norm_tier(v):
    """规范化为 L1/L2/L3；非法返回 None（大小写不敏感，返回规范写法）。"""
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    return s if s in TIER_VALUES else None


def resolve_tier(tasks, man) -> tuple[str, str]:
    """返回 (档位, 来源)。来源 ∈ packet | manifest | plan | default。

    优先级（与任务书一致）：
      任务包 tasks/*.json 的 interpret_tier
      → manifest.json 顶层的 interpret_tier
      → prepare_plan.json 的 interpret_tier
      → 默认 L2

    每一级都做合法性校验而非「有就用」：某一级写了非法值（例如 "L9"）
      就继续往下找，最后仍落到默认 L2 并说明一句 —— 档位是参数不是闸门，
      不能因为某个文件写坏了一个字段就让整份报告出不来。
    """
    for t in tasks:
        v = _norm_tier((t.get("packet") or {}).get("interpret_tier"))
        if v:
            return v, "packet"
    v = _norm_tier((man or {}).get("interpret_tier"))
    if v:
        return v, "manifest"
    plan = load_json(PLAN_PATH, None)
    v = _norm_tier((plan or {}).get("interpret_tier") if isinstance(plan, dict) else None)
    if v:
        return v, "plan"
    print(f"[INFO] 未读到解读档位（任务包 / manifest / {PLAN_PATH} 均无有效 interpret_tier）"
          f"→ 回退**默认 {DEFAULT_TIER}**（{TIER_LABEL[DEFAULT_TIER]}）。", file=sys.stderr)
    return DEFAULT_TIER, "default"


def _norm_engine(v):
    """规范化为 legacy / paper-card；非法返回 None（同 _norm_tier 的取舍）。"""
    if not isinstance(v, str):
        return None
    s = v.strip().lower().replace("_", "-")
    return s if s in ENGINE_VALUES else None


def resolve_engine(tasks, man) -> tuple[str, str]:
    """返回 (引擎, 来源)。来源 ∈ packet | manifest | plan | default。

    优先级与 resolve_tier 逐级同构：任务包 → manifest → prepare_plan.json → 默认 legacy。
    引擎只影响报告里**申报什么**（是否需列出 16 节卡与审计状态），
      不影响 11 字段卡的渲染 —— 所以读不到就静默回退，绝不中断汇总。
    """
    for t in tasks:
        v = _norm_engine((t.get("packet") or {}).get("interpret_engine"))
        if v:
            return v, "packet"
    v = _norm_engine((man or {}).get("interpret_engine"))
    if v:
        return v, "manifest"
    plan = load_json(PLAN_PATH, None)
    v = _norm_engine((plan or {}).get("interpret_engine") if isinstance(plan, dict) else None)
    if v:
        return v, "plan"
    return DEFAULT_ENGINE, "default"


def paper_card_status(tasks) -> list[dict]:
    """逐篇核对 paper-card 引擎的两份产物是否齐备、审计是否通过。

    为什么要 build 侧来查：subagent 是并行且**可能失败**的。
      只报「16 节卡产出了」而不报「它的审计没跑 / 没过」，
      等于把「未验证」当成「已验证」交付 —— 正是本流程一贯拒绝的做法。
      这里只做**事实申报**，不阻断（阻断在交付前的 nature_bridge.py check）。
    """
    rows = []
    for t in tasks:
        m = t["meta"]
        pc = (t.get("packet") or {}).get("paper_card") or {}
        card = pathlib.Path(pc["card_path"]) if pc.get("card_path") else None
        audit = (pathlib.Path(pc["audit_report_path"])
                 if pc.get("audit_report_path") else None)
        row = {"seq": m.get("seq"), "title": one_line(m.get("title"))[:60],
               "slug": m.get("slug"), "card": bool(card and card.exists()),
               "audit": None, "audit_errors": None, "content_errors": None}
        if audit and audit.exists():
            rep = load_json(audit, None)
            if isinstance(rep, dict):
                summ = rep.get("summary") or {}
                row["audit"] = summ.get("status")
                row["audit_errors"] = summ.get("audit_errors")
                row["content_errors"] = summ.get("content_errors")
        rows.append(row)
    return rows


def _card_body(L: list, c: dict, oq_head: str = "待验证的问题 / 局限") -> None:
    """L2 档卡片正文（四步格式）。

    这个函数是**逐字节基线**：L2 调用时不得改动任何一行输出，
      否则默认档的报告会因本次改动而变化。L3 只通过 `oq_head` 换标题、
      再在末尾追加证据强度提示（见 _card_l3）。
    """
    if c.get("one_line"):
        L.append(f"> **定位**：{one_line(c['one_line'])}\n")
    if c.get("problem_solved"):
        L.append(f"**本文有效解决了什么问题**：{one_line(c['problem_solved'])}\n")

    for head, key in (("1. 明确的方法论", "methods"),
                      ("2. 经过验证的结论", "verified_conclusions"),
                      ("3. 关键信息（二八原则）", "key_20")):
        items = as_list(c.get(key))
        L.append(f"**{head}**\n")
        if items:
            for it in items:
                L.append(f"- {one_line(it)}")
        else:
            L.append("- （该字段为空）")
        L.append("")

    gains = [g for g in as_list(c.get("quantified_gains")) if isinstance(g, dict)]
    L.append("**4. 优化 / 提升数据**\n")
    if gains:
        L.append("| 指标 | 提升 / 结果 | 条件 / 基线 | 出处 |")
        L.append("|---|---|---|---|")
        for g in gains:
            L.append(f"| {one_line(g.get('metric')) or '—'} "
                     f"| {one_line(g.get('value')) or '—'} "
                     f"| {one_line(g.get('context')) or '—'} "
                     f"| {one_line(g.get('source')) or '—'} |")
    else:
        L.append("- （原文未给出可量化的提升数据）")
    L.append("")

    oq = as_list(c.get("open_questions"))
    if oq:
        L.append(f"**{oq_head}**\n")
        for it in oq:
            L.append(f"- {one_line(it)}")
        L.append("")
    tags = as_list(c.get("direction_tags")) + as_list(c.get("paradigm_tags"))
    if tags:
        L.append("`" + "` `".join(one_line(x) for x in tags) + "`\n")


def _card_l1(L: list, c: dict) -> None:
    """L1 精简档：只留「定位 + 问题 + 关键信息 + 关键数字表」。

    L1 的「不含」是**硬约束**（tier-specs.md）：不写误差项、样本量、
      **图表引用**、实验步骤、机制链，也不写作者自述局限。
      所以这里的数字表**不带「出处」列** —— 那一列装的正是「图 1a / 表 2」
      这类图表引用，在 L1 属于禁写项。
    跳过四节：1. 明确的方法论、2. 经过验证的结论、4. 优化 / 提升数据
      （由下面的关键数字表替代）、待验证的问题 / 局限。
    quantified_gains 为空时写一句说明，**不留空表** —— 空表会让读者
      以为「这篇没有数字」，而这可能只是抓取缺失。
    """
    if c.get("one_line"):
        L.append(f"> **定位**：{one_line(c['one_line'])}\n")
    if c.get("problem_solved"):
        L.append(f"**本文有效解决了什么问题**：{one_line(c['problem_solved'])}\n")

    items = as_list(c.get("key_20"))
    L.append("**关键信息（二八原则）**\n")
    if items:
        for it in items:
            L.append(f"- {one_line(it)}")
    else:
        L.append("- （该字段为空）")
    L.append("")

    gains = [g for g in as_list(c.get("quantified_gains")) if isinstance(g, dict)][:5]
    L.append("**关键数字**\n")
    if gains:
        L.append("| 指标 | 提升 / 结果 | 条件 / 基线 |")
        L.append("|---|---|---|")
        for g in gains:
            L.append(f"| {one_line(g.get('metric')) or '—'} "
                     f"| {one_line(g.get('value')) or '—'} "
                     f"| {one_line(g.get('context')) or '—'} |")
    else:
        L.append("- （解读卡未提供可量化的关键数字，故本档不列数字表；"
                 "如需完整数据请按 L2 / L3 档重跑）")
    L.append("")


def eff_evidence(t: dict) -> str:
    """本篇**实际**的证据等级：以解读卡自述为准，任务包标注只作回退。

    [WARN] 为什么不能只信任务包：任务包的 `evidence_source` 来自下载与抽取记录，
      落地页残页（`text_extraction_backend = "landing-page-text"`）也会被标成
      `fulltext` —— 实测有 34,926 字符的「摘要 + 93 条参考文献」被这样标上去。
      subagent 读了实际文本会如实降级为 `abstract`，若本脚本仍按任务包统计，
      报告就会高报覆盖面、逐篇也会印成「解读依据：全文」
      （取证见 `流程1/实测结果汇总.md#h-2`）。
    """
    ev = (t.get("card") or {}).get("evidence") if isinstance(t.get("card"), dict) else None
    src = ""
    if isinstance(ev, dict):
        src = str(ev.get("source") or "").strip().lower()
    elif isinstance(ev, str):
        src = ev.strip().lower()
    if src in ("fulltext", "abstract", "metadata-only"):
        return src
    return str(t["meta"].get("evidence_source") or "abstract").strip().lower()


def _strength_line(c: dict, m: dict) -> str:
    """L3 档卡片末尾的**证据强度**一行。

    有 strength_notes / evidence 字段就**引用卡里的事实**（那是 subagent
      读原文时留下的），不要用本脚本临时编一句话盖过去；
      两者都没有才退回到按 evidence_source 的说明。
    原文的自我限定必须跟到解读里（tier-specs.md「常见错误」）：
      所以仅摘要级的卡片要明确写清「未通读全文」，不能让人当成全文结论。
    """
    notes = as_list(c.get("strength_notes"))
    if notes:
        return "> 证据强度：" + "；".join(one_line(x) for x in notes)
    ev = c.get("evidence")
    if isinstance(ev, dict):
        bits = [f"{k}={one_line(v)}" for k, v in ev.items()
                if v not in (None, "", [], {})]
        if bits:
            return "> 证据强度（卡内 evidence 字段）：" + "；".join(bits)
    elif isinstance(ev, str) and ev.strip():
        return f"> 证据强度（卡内 evidence 字段）：{one_line(ev)}"

    src = m.get("evidence_source")
    if src == "fulltext":
        line = ("> 证据强度：基于**全文**精读；排除性证据与作者自述局限均取自原文，"
                "作者语气未作升级。")
        if m.get("text_truncated"):
            line += "（正文超长，任务包保留开头 60% + 结尾 40%，中段另有全文可查）"
        return line
    return ("> 证据强度：**仅基于摘要**，未通读全文；机制细节、样本量与误差项可能缺失，"
            "引用前请核对原文。")


def _card_l3(L: list, c: dict, m: dict) -> None:
    """L3 完整档：L2 全部内容 + 明示自述局限 + 末尾一行证据强度提示。"""
    _card_body(L, c, oq_head="作者自述局限与待验证问题")
    L.append(_strength_line(c, m) + "\n")


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0

    man = load_json(INDIR / "manifest.json")
    if not man:
        print("[X] 找不到 .work_cdp/interpret/manifest.json")
        print("  请先运行：uv run --with pypdf python 流程1/scripts/interpret_prepare.py")
        return 2

    tasks = []
    for t in man.get("tasks") or []:
        tp = pathlib.Path(t.get("task") or "")
        pkt = load_json(tp, {}) if tp.exists() else {}
        rp = pathlib.Path(t.get("result") or "")
        card = load_json(rp, None) if rp.exists() else None
        tasks.append({"meta": t, "packet": pkt, "card": card,
                      "result_path": rp})

    # 档位在渲染之前定下来，并随报告开头一起写出去 ——
    #   读者要能一眼看到「这份报告按哪一档写的」，否则 L1 的省略会被
    #   误读成「这篇论文没有局限/没有方法」。
    tier, tier_src = resolve_tier(tasks, man)
    if tier_src in ("packet", "manifest", "plan"):
        tier_qualifier = "阶段 0 确认；可在阶段 0 改档"
    else:
        tier_qualifier = (f"未读到阶段 0 档位，按默认 {DEFAULT_TIER} 渲染；"
                          f"可在阶段 0 设置 interpret_tier")

    # 引擎同理：报告开头必须写清本轮按哪种形态产出，
    #   否则读者分不清「没有 16 节卡」是没做还是做了没通过。
    engine, engine_src = resolve_engine(tasks, man)
    pc_rows = paper_card_status(tasks) if engine == "paper-card" else []

    cards = [t for t in tasks if isinstance(t["card"], dict)]
    missing = [t for t in tasks if not isinstance(t["card"], dict)]
    bad = [t for t in cards if any(k not in t["card"] for k in REQUIRED)]

    # ── 关键词语料：题录 + 摘要 + 解读卡文字 ────────────────────
    cr = load_json(CDPDIR / "crossref_all.json", {}) or {}
    corpus_parts = []
    for t in tasks:
        m, pkt = t["meta"], t["packet"]
        corpus_parts.append(str(m.get("title") or ""))
        corpus_parts.append(str(pkt.get("abstract") or ""))
        c = t["card"]
        if isinstance(c, dict):
            corpus_parts.append(" ".join(map(str, as_list(c.get("methods")))))
            corpus_parts.append(" ".join(map(str, as_list(c.get("key_20")))))
            corpus_parts.append(" ".join(map(str, as_list(c.get("direction_tags")))))
            corpus_parts.append(" ".join(map(str, as_list(c.get("paradigm_tags")))))
    if isinstance(cr, dict):
        for r in cr.values():
            if isinstance(r, dict):
                corpus_parts.append(str(r.get("title") or ""))
                corpus_parts.append(str(r.get("abstract") or ""))
    freq = Counter(tokenize(" ".join(corpus_parts)))

    # 语义词族（可选配置；**没有配置就只出单条目** —— 不臆造合并关系）
    fam_path = arg("families") or str(CDPDIR / "keyword_families.json")
    families = load_json(fam_path, None)
    fam_rows = []
    # 词族成员必须是**单个 token**：英文单词（不含空格/连字符以外的短语）或中文词。
    #   写成 "t cell" / "cell cycle" 这类**短语**永远匹配不到 ——
    #   分词只产出英文单词与中文 2-gram。这类成员会在表里恒显示 0，
    #   让人误以为「该概念没出现」。这里显式检出并剔除，同时在报告里说明。
    bad_members = []

    def _usable(m: str) -> bool:
        m = str(m).strip().lower()
        if not m:
            return False
        if " " in m:                       # 短语：分词器不产出
            bad_members.append(m)
            return False
        return True

    if isinstance(families, dict):
        for label, members in families.items():
            # 键名以 _ 开头的是模板里的说明/注释，不是词族
            if str(label).startswith("_") or not isinstance(members, list):
                continue
            members = [m for m in members if _usable(m)]
            if not members:
                continue
            n = sum(freq.get(str(m).lower(), 0) for m in members)
            if n:
                fam_rows.append((label, n, sorted(
                    ((str(m).lower(), freq.get(str(m).lower(), 0)) for m in members),
                    key=lambda x: -x[1])))

    dirs = Counter()
    parads = Counter()
    for t in cards:
        for d in as_list(t["card"].get("direction_tags")):
            dirs[one_line(d)] += 1
        for p in as_list(t["card"].get("paradigm_tags")):
            parads[one_line(p)] += 1

    # ══ 组装报告 ═══════════════════════════════════════════════
    n_all, n_card = len(tasks), len(cards)
    # 证据等级**以卡片自述为准**（见 eff_evidence）：任务包会把落地页残页标成 fulltext。
    n_full = len([t for t in tasks if eff_evidence(t) == "fulltext"])
    n_landing = len([t for t in tasks
                     if eff_evidence(t) != "fulltext"
                     and t["packet"].get("text_extraction_backend") == "landing-page-text"])
    L = []
    L.append(f"# {AUTHOR} 论文解读\n")
    L.append(f"> 作者：{AUTHOR}" + (f"（{AFFIL}）" if AFFIL else ""))
    L.append(f"> ORCID：{f'https://orcid.org/{ORCID}' if ORCID else '（未提供）'}"
             f" ｜ 共 **{n_all}** 篇 ｜ 已解读 **{n_card}** 篇")
    L.append("> 解读方式：并行 subagent 逐篇精读（流程见 "
             "`流程1/subagent-driven-review/SKILL.md`）；"
             + ("全文取自主流程经 CDP 下载的本地 PDF，**全程无网络抓取**。"
                if n_full == n_all else
                f"其中 {n_full} 篇的全文取自主流程经 CDP 下载的本地 PDF，"
                f"其余 {n_all - n_full} 篇**无全文**，按摘要级解读；"
                "**全程无网络抓取**。") + "\n")
    L.append(f"> 解读引擎：**{engine}**（{ENGINE_LABEL[engine]}，来源：{engine_src}）"
             + ("；16 节 Paper Card 的产出与审计状态见文末「缺卡清单与后续处理」"
                if engine == "paper-card" else "") + "\n")

    # ── 数据来源与限制（先说清楚，避免读者高估覆盖度）──────────
    L.append("## 〇、数据来源与限制\n")
    L.append(f"- 有全文可精读：**{n_full}** 篇；仅摘要级解读：**{n_all - n_full}** 篇"
             f"（以各卡 `evidence.source` 为准，非任务包标注）")
    if n_landing:
        # 落地页残页：字符数看着像全文，实际只有摘要 + 参考文献 + 页面导航。
        #   单独列一行，别让读者以为「有几千字符就是读到了正文」。
        _ld = "、".join(str(t["meta"].get("seq")) for t in tasks
                       if eff_evidence(t) != "fulltext"
                       and t["packet"].get("text_extraction_backend") == "landing-page-text")
        L.append(f"- [WARN] 其中 **{n_landing}** 篇（编号 {_ld}）取到的是**出版社落地页文本**"
                 f"（摘要 + 参考文献 + 页面导航，非正文；无 `[p.N]` 页标记与图号），"
                 f"按**摘要级**计入，不得当作全文结论")
    trunc = [t for t in tasks if t["meta"].get("text_truncated")]
    if trunc:
        # 措辞要与机制一致：任务包**保留开头 60% + 结尾 40%**（结论/局限在结尾），
        #   被省略的中间段另有 fulltext 可分段补读 —— 所以不能说「解读以截断部分为限」，
        #   那会低报解读的实际覆盖度。这里据实报出「有几篇确认补读了中段」。
        def _supplemented(t):
            c = t.get("card") or {}
            blob = " ".join(str(x) for x in (c.get("open_questions") or []))
            return ("省略" in blob) or ("截断" in blob)
        n_supp = len([t for t in trunc if _supplemented(t)])
        L.append(f"- 正文超长：{len(trunc)} 篇 —— 任务包保留**开头 60% + 结尾 40%**"
                 f"（结论、量化数据、自述局限都在结尾，未被丢弃）；"
                 f"中间省略段另存 `.work_cdp/interpret/fulltext/` 供按需补读，"
                 f"其中 **{n_supp} 篇已补读中段**")
    if missing:
        L.append(f"- [WARN] **未产出解读卡：{len(missing)} 篇**（详见文末「缺卡清单」）")
    if bad:
        L.append(f"- [WARN] 解读卡字段不全：{len(bad)} 篇（已在正文中尽量呈现现有字段）")
    if engine == "paper-card":
        n_pc = len([r for r in pc_rows if r["card"]])
        n_audited = len([r for r in pc_rows if r["audit"]])
        n_pc_fail = len([r for r in pc_rows
                         if r["audit"] and r["audit"] != "pass"])
        L.append(f"- 深读卡（16 节）：已产出 **{n_pc}/{n_all}** 篇 ｜ "
                 f"已附审计报告 **{n_audited}** 篇"
                 + (f" ｜ [WARN] 其中 **{n_pc_fail}** 篇审计非 pass" if n_pc_fail else ""))
        if n_pc < n_all or n_audited < n_all:
            L.append("- [WARN] **未验证 ≠ 已验证**：缺深读卡或审计报告的篇目，"
                     "其结论只由 11 字段卡支撑，未经过数字回查原文。逐篇状态见第六节。")
    if not (missing or bad or trunc or (n_all - n_full)
            or (engine == "paper-card" and (n_pc < n_all or n_audited < n_all))):
        L.append("- 全部篇目均基于全文完整解读，无缺项。")
    L.append("")

    # ── ① 时间线要点串联 ─────────────────────────────────────
    L.append("---\n\n## 一、时间线要点串联（按发表先后）\n")
    cur_year = None
    for t in tasks:
        y = str(t["meta"].get("year") or "—")
        if y != cur_year:
            cur_year = y
            L.append(f"\n### {y}\n")
        c = t["card"] if isinstance(t["card"], dict) else {}
        pos = t["packet"].get("my_position") or ""
        title = one_line(t["meta"].get("title"))[:110]
        line = one_line(c.get("one_line")) or "（未解读）"
        L.append(f"- **[{t['meta'].get('seq')}] {title}**"
                 + (f" *{pos}*" if pos else ""))
        L.append(f"  - {line}")
        for g in as_list(c.get("quantified_gains"))[:2]:
            if isinstance(g, dict) and g.get("value"):
                L.append(f"  - [CHART] {one_line(g.get('metric'))}：{one_line(g.get('value'))}"
                         + (f"（{one_line(g.get('context'))}）" if g.get("context") else ""))
    L.append("")

    # ── ② 逐篇解读（四步格式；卡片细节量由档位决定）──────────
    L.append("---\n\n## 二、逐篇解读\n")
    # 只插入**一行**：末尾不写 "\n"，否则会多出一个空行；
    #   卡片自身以 "\n### " 开头，正好把它与正文隔开一个空行。
    L.append(f"> 解读档位：**{tier} {TIER_LABEL[tier]}**（{tier_qualifier}）")
    for t in tasks:
        m = t["meta"]
        c = t["card"] if isinstance(t["card"], dict) else {}
        pk = t["packet"]
        L.append(f"\n### [{m.get('seq')}] {one_line(m.get('title'))}\n")
        meta_bits = [one_line(m.get("journal")), str(m.get("year") or "")]
        if pk.get("pages"):
            meta_bits.append(f"{pk['pages']} 页")
        if pk.get("my_position"):
            meta_bits.append(pk["my_position"])
        L.append(f"`DOI: {m.get('doi')}` ｜ " + " ｜ ".join(x for x in meta_bits if x))
        src = eff_evidence(t)
        L.append(f" ｜ 解读依据：**{'全文' if src == 'fulltext' else '仅摘要'}**"
                 + ("（正文已截断）" if m.get("text_truncated") else "") + "\n")
        if not c:
            L.append("> [WARN] 本篇**未产出解读卡**，以下信息取自题录。"
                     "请按子流程手册 § 5③ 重派 subagent。\n")
            if pk.get("abstract"):
                L.append(f"**摘要（原文）**：{one_line(pk['abstract'])[:600]}\n")
            continue

        # 分档渲染。L2 是默认档，走 _card_body（与引入档位前逐字节一致）；
        #   L1 / L3 是新增形态，各自只改自己那一档的输出。
        if tier == "L1":
            _card_l1(L, c)
        elif tier == "L3":
            _card_l3(L, c, m)
        else:
            _card_body(L, c)

    # ── ③ 关键词统计 ────────────────────────────────────────
    L.append("---\n\n## 三、关键词统计\n")
    L.append("> 语料 = 全部题名 + 摘要 + 解读卡的方法/关键信息/标签。\n")
    if fam_rows:
        L.append("### 3.1 语义词族合并计数\n")
        L.append("| 语义词族 | 合计频次 | 成员（各自频次） |")
        L.append("|---|---|---|")
        for label, n, members in sorted(fam_rows, key=lambda x: -x[1]):
            # 只列出真正出现过的成员；0 频次的略去，避免表格被噪声淹没
            hit = [(m, c) for m, c in members if c]
            mem = "、".join(f"{m}({c})" for m, c in hit) if hit else "（无命中）"
            miss = [m for m, c in members if not c]
            if miss:
                mem += f" *未命中：{'、'.join(miss)}*"
            L.append(f"| **{label}** | {n} | {mem} |")
        L.append("")
        if bad_members:
            L.append(f"> [WARN] 词族表里有 {len(bad_members)} 个**短语型成员**已忽略"
                     f"（分词只产出单个英文词与中文 2-gram，短语永远匹配不到）："
                     f"{'、'.join(sorted(set(bad_members))[:12])}"
                     f"{' …' if len(set(bad_members)) > 12 else ''}")
            L.append("> 如需统计短语，请把词拆成单个词，或在 `interpret_build.py` 里扩展分词器。")
            L.append("")
    else:
        L.append("### 3.1 语义词族合并计数\n")
        L.append(f"> 未提供语义词族表（`{fam_path}`）→ 跳过合并计数。\n")
        L.append("> 如需合并同义/近义写法（如 *CO2 capture* / *carbon dioxide capture*），")
        L.append("> 可参考 `流程1/templates/关键词词族模板.json` 建立后重跑：")
        L.append("> `uv run python interpret_build.py --families=<你的词族表.json>`\n")

    L.append("### 3.2 单一条目频次（Top 40）\n")
    L.append("| # | 关键词 | 频次 |")
    L.append("|---|---|---|")
    for i, (w, n) in enumerate(freq.most_common(40), 1):
        L.append(f"| {i} | {w} | {n} |")
    L.append("")

    # ── ④ 主要研究方向 ──────────────────────────────────────
    L.append("---\n\n## 四、主要研究方向归纳\n")
    if dirs:
        for i, (d, n) in enumerate(dirs.most_common(), 1):
            L.append(f"{i}. **{d}** —— 在 {n} 篇中被标为该方向")
    else:
        L.append("（解读卡未提供 `direction_tags`）")
    L.append("")

    # ── ⑤ 方法范式 ──────────────────────────────────────────
    L.append("---\n\n## 五、研究方法范式（Paradigm）分析\n")
    if parads:
        L.append("| 方法范式 | 篇数 |")
        L.append("|---|---|")
        for p, n in parads.most_common():
            L.append(f"| {p} | {n} |")
        L.append("")
        L.append("> 范式标签由各篇解读卡汇总而来；同一篇可归入多个范式。")
    else:
        L.append("（解读卡未提供 `paradigm_tags`）")
    L.append("")

    # ── ⑥ 缺卡清单（如实申报）───────────────────────────────
    L.append("---\n\n## 六、缺卡清单与后续处理\n")
    if not missing:
        L.append("无。全部篇目均已产出解读卡。\n")
    else:
        L.append(f"以下 **{len(missing)}** 篇未产出解读卡，**未纳入**上文的统计与归纳：\n")
        L.append("| # | 年份 | 题名 | DOI | 原因 |")
        L.append("|---|---|---|---|---|")
        for t in missing:
            m = t["meta"]
            why = ("缺全文（仅摘要）" if m.get("evidence_source") != "fulltext"
                   else "subagent 未产出结果文件")
            L.append(f"| {m.get('seq')} | {m.get('year')} | "
                     f"{one_line(m.get('title'))[:60]} | `{m.get('doi')}` | {why} |")
        L.append("")
        L.append("> 处理：按子流程手册 § 5② 重新分派 subagent，"
                 "或回主流程 §4 阶段 E 用 CDP 补下全文后重跑 `interpret_prepare.py`。\n")

    # ── ⑥b 深读卡与审计状态（仅 paper-card 引擎；如实申报，不粉饰）──
    if engine == "paper-card":
        L.append("### 深读卡（16 节 Paper Card）与审计状态\n")
        L.append("> 审计 = 结构合规（节序 / 头部 / 定位模式 / 指针）**加上**"
                 "数字回查原文（`NUMBER_NOT_IN_SOURCE`）。"
                 "`未跑` 表示该篇未经数字核验，其数值应视为**未验证**。\n")
        L.append("| # | 年份 | 题名 | 16 节卡 | 审计状态 | 结构 error | 内容 error |")
        L.append("|---|---|---|---|---|---|---|")
        for r in pc_rows:
            if r["audit"] is None:
                status = "未跑"
            elif r["audit"] == "pass":
                status = "pass"
            else:
                status = f"**{r['audit']}**"
            L.append(f"| {r['seq']} | — | {r['title']} | "
                     f"{'已产出' if r['card'] else '**[缺]**'} | {status} | "
                     f"{r['audit_errors'] if r['audit_errors'] is not None else '—'} | "
                     f"{r['content_errors'] if r['content_errors'] is not None else '—'} |")
        L.append("")
        n_pc = len([r for r in pc_rows if r["card"]])
        n_pass = len([r for r in pc_rows if r["audit"] == "pass"])
        L.append(f"合计：深读卡 **{n_pc}/{len(pc_rows)}** 篇；审计 pass **{n_pass}** 篇。"
                 "未 pass 的篇目须修复后重跑 "
                 "`uv run python 流程1/scripts/nature_bridge.py check …`。\n")

    L.append("---\n")
    L.append("*本报告由并行 subagent 逐篇解读后汇总；全文取自经 CDP 下载的本地 PDF，"
             "解读过程未发起任何网络抓取。数值与结论以原文为准。*\n")

    body = "\n".join(L)
    # 单一交付文件。本报告本身就是完整版（含缺卡清单与限制申报，无删减），
    #   不再另写 `_完整版.md` 别名 —— 两份内容逐字节相同，只会让交付目录
    #   出现两个一样的文件，人工核对时还要先确认哪个是正的。
    p = WD / f"{AUTHOR}_论文解读.md"
    p.write_text(body, encoding="utf-8")
    print("saved:", p)
    print(f"解读档位：{tier}（{TIER_LABEL[tier]}）｜来源：{tier_src}")
    print(f"解读引擎：{engine}（{ENGINE_LABEL[engine]}）｜来源：{engine_src}")
    print(f"已解读 {n_card}/{n_all} 篇 ｜ 全文级 {n_full} 篇 ｜ 缺卡 {len(missing)} 篇")
    if engine == "paper-card":
        n_pc = len([r for r in pc_rows if r["card"]])
        n_audited = len([r for r in pc_rows if r["audit"]])
        n_pass = len([r for r in pc_rows if r["audit"] == "pass"])
        print(f"深读卡 {n_pc}/{n_all} 篇 ｜ 已审计 {n_audited} 篇 ｜ 审计 pass {n_pass} 篇")
        if n_audited < n_all:
            print("[WARN] 有篇目未经数字回查原文 —— 已在报告第六节逐篇申报")
    if missing:
        print("[WARN] 缺卡清单已写入报告第六节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
