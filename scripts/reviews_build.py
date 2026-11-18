# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R② ：领域概貌汇总（Reviews Build）
#
# 为什么需要本脚本
#   `interpret_build.py`（阶段 H）与 `refs_build.py`（阶段 H2）都是各自链路的
#   汇总器，绑定在各自的 manifest 与解读卡 schema 上。阶段 R2 的解读卡字段
#   不同（field_role / consensus / controversies / key_concepts），需要一个汇总器。
#
#   本脚本**只做机械合并**，不替 agent 做判断：
#     · 把 N 张卡按字段归并（多篇共现的概念 = 领域核心）
#     · 术语表按「出现篇数」排序（反复出现的才是必懂的）
#     · 共识与争议**原样并列**，不替用户挑一个当定论
#     · 缺卡的文献如实列出，不跳过、不假装齐全
#
# 用法：
#   # ① 汇总（subagent 写完全部 result.json 之后）
#   uv run python scripts/reviews_build.py
#
#   # ② 自定义目录 / 输出文件名
#   uv run python scripts/reviews_build.py \
#       --dir .work_cdp/reviews --out .work_cdp/reviews/领域概貌.md
#
#   # ③ 只看状态（有几张卡、缺哪几篇）
#   uv run python scripts/reviews_build.py --status
#
# 综述解读档位（`review_interpret_tier`）：只做「读档位 → 报告开头与末尾摘要显示」，
#   **不改任何既有渲染行为**（默认 L3，等于现状）。读取优先级：
#   task packet → manifest.json → .work_cdp/prepare_plan.json → 默认 "L3"。
#   档位是参数不是闸门，读不到只回退、不报错。语义见 skill：`paper-interpretation-writing`。
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import sys
from collections import Counter, defaultdict

from logutil import run_root as _run_root  # noqa: E402

# ── UTF-8 兜底（幂等）──


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

# 推荐阅读顺序用的角色排序（越靠前越适合入门先读）
ROLE_ORDER = ["入门综述", "奠基性综述", "争议梳理", "方法综述", "最新进展"]


# ══════════════════════════════════════════════════════════════
# 综述解读档位（阶段 R2 用 `review_interpret_tier`，与阶段 H2 共用同一个键）
#
# 档位是**参数不是闸门**：读不到就回退默认 L3，绝不中断汇总。
# 本脚本**只负责显示档位**（报告开头一行 + 末尾摘要），不改任何既有渲染逻辑 ——
#   档位改变的是细节量，而 L1/L2 的差异化渲染属于后续批次。
# 自包含一份助手而不 import 别的脚本（见 _fix_streams 注释里的 import 陷阱）。
# ══════════════════════════════════════════════════════════════
TIER_KEY = "review_interpret_tier"
TIER_VALUES = ("L1", "L2", "L3")
DEFAULT_TIER = "L3"                         # 综述类交付物的默认档位
TIER_LABELS = {"L1": "精简", "L2": "详细", "L3": "完整"}
PLAN_PATH = WD / ".work_cdp" / "prepare_plan.json"


def normalize_tier(raw) -> str:
    """把任意输入规范化成 L1/L2/L3（大小写不敏感）；无法识别返回空串。"""
    s = str(raw or "").strip().upper()
    return s if s in TIER_VALUES else ""


def plan_tier(plan_path: pathlib.Path | None = None) -> tuple[str, str]:
    """读阶段 0 的 `review_interpret_tier`（返回 (档位, 失败原因)；原因空=成功）。"""
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


def resolve_tier(task_files: list[pathlib.Path], man: dict | None) -> tuple[str, str]:
    """档位优先级：**task packet → manifest → prepare_plan.json → 默认 L3**。

    返回 (档位, 来源说明)。任务包是 reviews_prepare 逐条落盘的事实，
    比 manifest 更贴近「这篇综述当时按什么档位解读」；两者都没有才回头看阶段 0 计划。
    """
    for p in task_files or []:
        # 本脚本没有 load_json 助手（读 JSON 一律就地 json.loads）—— 读不出来就跳过，
        #   档位是参数不是闸门，单个任务包损坏不该让汇总失败。
        try:
            packet = json.loads(pathlib.Path(p).read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        got = normalize_tier(packet.get("interpret_tier"))
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


# 占位/截断污染的特征串。并行 subagent 在高并发下可能触发生成长度上限，
# 于是用 "placeholder" / "待补" / 空串等填充字段 —— 这种卡**必须拒收**，
# 否则《领域概貌》会把占位符当成真实结论展示给本科生。
_PLACEHOLDER = re.compile(
    r"placeholder|待补|待填|\bTODO\b|\bTBD\b|\.\.\.$|^无$|^N/?A$", re.I)


def _bad_text(s) -> bool:
    t = str(s or "").strip()
    if len(t) < 2:
        return True
    return bool(_PLACEHOLDER.search(t))


def validate_card(c: dict) -> list[str]:
    """检查一张解读卡是否可用，返回问题清单（空 = 通过）。

    为什么需要：高并发下 subagent 可能产出**结构合法但内容是占位符**的卡
      （实测出现 `"conclusion": "placeholder"`、`"consensus": ["placeholder"]`）。
      schema 校验只管字段类型，管不了内容是不是真话 —— 所以这里补一层。
    """
    problems: list[str] = []
    if _bad_text(c.get("one_sentence")):
        problems.append("one_sentence 为空/占位")
    if _bad_text(c.get("problem_addressed")):
        problems.append("problem_addressed 为空/占位")

    dirs = [d for d in (c.get("main_directions") or []) if isinstance(d, dict)]
    good_dirs = [d for d in dirs
                 if not _bad_text(d.get("name"))
                 and not _bad_text(d.get("idea"))
                 and not _bad_text(d.get("conclusion"))]
    if not good_dirs:
        problems.append("main_directions 无有效条目")
    elif len(good_dirs) < len(dirs):
        problems.append(f"main_directions 有 {len(dirs)-len(good_dirs)} 条含占位/空值")

    def arr_ok(key):
        items = [x for x in (c.get(key) or []) if not _bad_text(x)]
        return items

    if not arr_ok("consensus"):
        problems.append("consensus 无有效条目")
    concepts = [k for k in (c.get("key_concepts") or [])
                if isinstance(k, dict)
                and not _bad_text(k.get("cn"))
                and not _bad_text(k.get("plain"))]
    if not concepts:
        problems.append("key_concepts 无有效条目")
    if not arr_ok("open_questions"):
        problems.append("open_questions 无有效条目")
    return problems


def _as_text(x) -> str:
    """把解读卡里的任意值渲染成一行可读文本。

    为什么需要：subagent 对同一字段可能给出**不同形状** ——
      `controversies` 有时是字符串数组，有时是 `{issue, positions}` 或
      `{point, status}` 这样的对象。早期版本直接 f-string 插值，于是
      《领域概貌》里出现了 `{'issue': '…', 'positions': '…'}` 这种
      Python repr 泄漏（实测 6 处）。这里做一次归一。
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        # 常见键名按语义拼接；未知键名则原样列出
        parts = []
        for k in ("issue", "point", "question", "topic", "detail",
                  "positions", "status", "note", "explanation"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        if parts:
            return " —— ".join(parts)
        # 兜底：把值按顺序拼起来，绝不泄漏 Python repr
        vals = [str(v).strip() for v in x.values()
                if isinstance(v, (str, int, float)) and str(v).strip()]
        return " —— ".join(vals)
    if isinstance(x, (list, tuple)):
        return "；".join(_as_text(i) for i in x if _as_text(i))
    return str(x).strip()


def _norm_key(s: str) -> str:
    """把术语/方向名归一成可比较的键（去空白、标点、大小写）。"""
    return re.sub(r"[\s\-_/（）()·,，。、：:；;]+", "", str(s or "").lower())


# ══════════════════════════════════════════════════════════════
# 语义聚类 + 综合底稿（dossier）
#
# 旧版用**精确字符串**去重/计数：跨综述措辞不同永不合并，方向/共识/概念
#   全是单篇 dump（出现篇数恒 1）＝"生硬拼凑"。这里改为按**显著词重叠**聚类，
#   把含义相近的跨篇表述合并、计跨篇支持数，产出供综述综合 subagent 使用的
#   「综合底稿」（参照 paper-spine：写证据绑定的综合论证，而非拼接/模板壳）。
#   聚类是启发式（供底稿），最终 prose 由综合 subagent 依底稿撰写并再归并。
# ══════════════════════════════════════════════════════════════
_STOP = set("""的 了 是 在 和 与 及 或 等 对 对于 通过 用 使用 基于 一种 一个 我们 本文 该文
综述 文章 方法 结果 结论 研究 分析 可以 可能 需要 进行 提出 认为 表明 显示 以及 并 并且
而 但 但是 仍 没有 未 不 更 最 很 非常 相关 主要 核心 重要 关键 问题 任务 领域 方向
技术 模型 网络 学习 深度 机器 数据 作者 原文 文中 指出 强调 讨论 介绍 概述 总结""".split())


def _sig_tokens(text: str) -> set:
    """显著词集合：拉丁词(≥3字母) + 中文二元组，去停用词。用于跨篇语义重叠。"""
    s = str(text or "")
    lat = set(re.findall(r"[a-z]{3,}", s.lower()))
    cjk = re.findall(r"[一-鿿]", s)
    grams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return (lat | grams) - _STOP


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster(texts: list[str], threshold: float = 0.3) -> list[list[int]]:
    """贪心聚类：返回成员下标组。含义相近（显著词 Jaccard≥threshold）归一组。"""
    clusters: list[dict] = []
    for i, t in enumerate(texts):
        tok = _sig_tokens(t)
        best, best_s = None, 0.0
        for cl in clusters:
            s = _jaccard(tok, cl["tok"])
            if s > best_s:
                best, best_s = cl, s
        if best is not None and best_s >= threshold:
            best["members"].append(i)
            best["tok"] |= tok
        else:
            clusters.append({"tok": tok, "members": [i]})
    return [cl["members"] for cl in clusters]


_GAP_NOISE = ("阅读覆盖", "已分段", "未读部分", "无法核对", "参考文献编号", "图只剩",
              "由全文", "抽取而来", "没有给出具体", "未展开", "还需另外")


def build_dossier(cards: list[dict]) -> dict:
    """把解读卡聚合成「综合底稿」：合并方向/共识/争议/问题/概念/缺口，计跨篇支持。"""
    titles = [str(c.get("title") or "") for c in cards]

    # 方向：name+idea 聚类
    d_items = []  # (name, idea, conclusion, title_idx)
    for ci, c in enumerate(cards):
        for d in (c.get("main_directions") or []):
            if isinstance(d, dict) and d.get("name"):
                d_items.append((str(d["name"]).strip(), _as_text(d.get("idea")),
                                _as_text(d.get("conclusion")), ci))
            elif isinstance(d, str) and d.strip():
                d_items.append((d.strip(), "", "", ci))
    directions = []
    if d_items:
        for mem in _cluster([f"{n} {i}" for n, i, _, _ in d_items]):
            names = Counter(d_items[i][0] for i in mem)
            idea = max((d_items[i][1] for i in mem), key=len, default="")
            concl = max((d_items[i][2] for i in mem), key=len, default="")
            src = sorted({titles[d_items[i][3]] for i in mem})
            directions.append({"name": names.most_common(1)[0][0], "idea": idea,
                               "conclusion": concl, "support": len(src), "sources": src})
        directions.sort(key=lambda x: (-x["support"], x["name"]))

    def _cluster_field(field: str) -> list[dict]:
        items = []  # (text, title_idx)
        for ci, c in enumerate(cards):
            for x in (c.get(field) or []):
                t = _as_text(x)
                if t:
                    items.append((t, ci))
        out = []
        if items:
            for mem in _cluster([t for t, _ in items]):
                text = max((items[i][0] for i in mem), key=len)
                src = sorted({titles[items[i][1]] for i in mem})
                out.append({"text": text, "support": len(src), "sources": src})
            out.sort(key=lambda x: (-x["support"], x["text"]))
        return out

    consensus = _cluster_field("consensus")
    controversies = _cluster_field("controversies")
    problems = _cluster_field("problem_addressed")

    # 概念：中文键合并、跨篇计数；core≥2 / single=1
    concepts: dict[str, dict] = {}
    freq: Counter = Counter()
    for c in cards:
        seen = set()
        for k in (c.get("key_concepts") or []):
            if isinstance(k, dict) and (k.get("cn") or k.get("en")):
                key = _norm_key(k.get("cn")) or _norm_key(k.get("en"))
            elif isinstance(k, str) and k.strip():
                key = _norm_key(k)
                k = {"cn": k.strip(), "en": "", "plain": ""}
            else:
                continue
            if not key or key in seen:
                continue
            seen.add(key)
            freq[key] += 1
            old = concepts.get(key)
            if old is None or len(str(k.get("plain") or "")) > len(str(old.get("plain") or "")):
                concepts[key] = dict(k)
    core = [{"cn": concepts[k].get("cn", ""), "en": concepts[k].get("en", ""),
             "plain": concepts[k].get("plain", ""), "support": n}
            for k, n in freq.most_common() if n >= 2]
    single = [{"cn": concepts[k].get("cn", ""), "en": concepts[k].get("en", "")}
              for k, n in freq.most_common() if n == 1]

    # 缺口：open_questions 聚类 + 过滤单篇"阅读覆盖/取证限制"噪声
    gaps = [g for g in _cluster_field("open_questions")
            if not any(w in g["text"] for w in _GAP_NOISE)]

    return {"schema": 1, "n_reviews": len(cards), "directions": directions,
            "consensus": consensus, "controversies": controversies,
            "problems": problems, "concepts_core": core, "concepts_single": single,
            "gaps": gaps,
            "papers": [{"title": c.get("title"), "year": c.get("year"),
                        "journal": c.get("journal"), "doi": c.get("doi"),
                        "field_role": c.get("field_role"), "is_oa": c.get("is_oa")}
                       for c in cards]}


def load_cards(d: pathlib.Path, strict: bool = True) -> tuple[list[dict], list[str], list[tuple[str, list[str]]]]:
    """读全部解读卡。

    返回 (可用卡, 读不出来的文件名, [(文件名, 问题清单)])。
    strict=True 时，占位/截断的卡会被**拒收并单独列出**，不进入汇总。
    """
    cards, bad, rejected = [], [], []
    rdir = d / "results"
    if not rdir.is_dir():
        return [], [], []
    for p in sorted(rdir.glob("*.json")):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            bad.append(p.name)
            continue
        if not isinstance(c, dict) or not c.get("title"):
            bad.append(p.name)
            continue
        c["_file"] = p.name
        if strict:
            probs = validate_card(c)
            if probs:
                rejected.append((p.name, probs))
                continue
        cards.append(c)
    return cards, bad, rejected


def render(cards: list[dict], manifest: dict | None, topic: str = "",
           tier: str = DEFAULT_TIER) -> str:
    """把解读卡渲染成面向本科生的《领域概貌.md》。

    `tier` = 综述解读档位；**只在报告开头多写一行档位说明**，
    既有渲染逻辑与档位无关（L1/L2 的差异化渲染属于后续批次）。
    """
    tier = normalize_tier(tier) or DEFAULT_TIER
    n = len(cards)
    dossier = build_dossier(cards)
    L: list[str] = []
    title = topic or "领域概貌"
    L.append(f"# {title} · 领域概貌（面向本科生）")
    L.append("")
    L.append(f"> 本文件由 {n} 篇综述的解读卡自动汇总。"
             "**共识与争议原样并列**，不替你下判断。")
    L.append(f"> {tier_banner(tier)}")
    L.append("")

    # ── 一句话总结：取第一篇的 one_sentence 或并列 ──
    L.append("## 一句话总结")
    L.append("")
    for c in cards[:3]:
        s = (c.get("one_sentence") or "").strip()
        if s:
            L.append(f"- {s}")
    if not any((c.get("one_sentence") or "").strip() for c in cards[:3]):
        L.append("-（解读卡未提供 one_sentence）")
    L.append("")

    # ── 这个领域在解决什么问题（跨篇聚类合并，非逐卡拼接）──
    L.append("## 这个领域在解决什么问题")
    L.append("")
    if dossier["problems"]:
        for p in dossier["problems"][:8]:
            sup = f"（{p['support']} 篇综述共同关注）" if p["support"] >= 2 else ""
            L.append(f"- {p['text']}{sup}")
    else:
        L.append("-（解读卡未提供）")
    L.append("")

    # ── 主要研究方向与流派（语义聚类合并，跨篇支持数）──
    L.append("## 主要研究方向与流派")
    L.append("")
    if dossier["directions"]:
        L.append("> 含义相近的方向已按显著词重叠**自动聚类合并**；「出现篇数」= 支持该方向的综述数，≥2 为反复讨论的主干方向。")
        L.append("")
        L.append("| 方向 | 核心思路 | 代表综述 | 出现篇数 |")
        L.append("|---|---|---|---|")
        for d in dossier["directions"]:
            shown = "; ".join(t[:32] for t in d["sources"][:2])
            if len(d["sources"]) > 2:
                shown += f" 等 {len(d['sources'])} 篇"
            L.append(f"| {d['name']} | {d['idea'][:110]} | {shown} | {d['support']} |")
        L.append("")
        L.append(f"> 共 {len(dossier['directions'])} 个合并后方向（原始表述 {sum(len(c.get('main_directions') or []) for c in cards)} 条）。聚类为启发式，终稿由综述综合步再归并。")
    else:
        L.append("-（解读卡未提供 main_directions）")
    L.append("")

    # ── 共识 vs 争议（本科生选题最该看）──
    L.append("## 目前的一致结论 vs 仍有争议的地方")
    L.append("")
    L.append("### 一致结论（多篇综述都这么认为）")
    L.append("")
    if dossier["consensus"]:
        for x in dossier["consensus"][:14]:
            mark = f"（{x['support']} 篇支持）" if x["support"] >= 2 else ""
            L.append(f"- {x['text']}{mark}")
        L.append("")
        L.append("> 括号内为支持该结论的综述数；≥2 可信度更高。含义相近表述已聚类合并。")
    else:
        L.append("-（解读卡未提供 consensus）")
    L.append("")

    L.append("### 仍有争议 / 未解决")
    L.append("")
    if dossier["controversies"]:
        for x in dossier["controversies"][:14]:
            L.append(f"- {x['text']}")
        L.append("")
        L.append("> [WARN] 争议点**原样并列**，没有替你判定谁对。"
                 "想找课题，这里往往是最好的切入口。")
    else:
        L.append("-（解读卡未提供 controversies）")
    L.append("")

    # ── 关键概念速查（仅跨篇核心概念入主表；单篇术语移附录）──
    L.append("## 关键概念速查")
    L.append("")
    if dossier["concepts_core"]:
        L.append("| 术语 | 英文 | 解释 | 出现篇数 |")
        L.append("|---|---|---|---|")
        for k in dossier["concepts_core"]:
            L.append(f"| {_as_text(k['cn'])} | {_as_text(k['en'])} | "
                     f"{_as_text(k['plain'])[:130]} | {k['support']} |")
        L.append("")
        L.append("> 主表只列 **≥2 篇综述共同提到**的领域核心概念，优先掌握。")
    else:
        L.append("-（无跨篇核心概念；解读卡 key_concepts 重叠不足）")
    if dossier["concepts_single"]:
        L.append("")
        L.append(f"<details><summary>单篇专门术语（{len(dossier['concepts_single'])} 个，点击展开）</summary>")
        L.append("")
        L.append("、".join(_as_text(k["cn"]) or _as_text(k["en"])
                          for k in dossier["concepts_single"]))
        L.append("")
        L.append("</details>")
    L.append("")

    # ── 推荐阅读顺序 ──
    L.append("## 推荐阅读顺序")
    L.append("")
    def role_rank(c: dict) -> int:
        r = (c.get("field_role") or "").strip()
        return ROLE_ORDER.index(r) if r in ROLE_ORDER else len(ROLE_ORDER)

    ordered = sorted(cards, key=role_rank)
    for i, c in enumerate(ordered, 1):
        role = c.get("field_role") or "未标注"
        oa = "，**有开放获取全文**" if c.get("is_oa") else ""
        doi = c.get("doi") or ""
        link = f" https://doi.org/{doi}" if doi else ""
        L.append(f"{i}. **{c.get('title','')}**（{c.get('year','')}, "
                 f"{c.get('journal','')}）—— 定位：{role}{oa}。{link}")
    L.append("")
    L.append("> 排序依据：入门综述 → 奠基性 → 争议梳理 → 方法 → 最新进展。"
             "本科生建议**按顺序读**，不要一上来就啃方法综述。")
    L.append("")

    # ── 选题切入点（跨篇聚类 + 过滤单篇取证噪声）──
    L.append("## 可能的选题切入点")
    L.append("")
    if dossier["gaps"]:
        for g in dossier["gaps"][:10]:
            sup = f"（{g['support']} 篇综述共同指出）" if g["support"] >= 2 else ""
            L.append(f"- {g['text']}{sup}")
        L.append("")
        L.append("> 这些是综述里**仍未解决**的问题，适合作为大创/研究计划的起点。")
        if len(dossier["gaps"]) > 10:
            L.append(f"> （另有 {len(dossier['gaps']) - 10} 条见综合底稿 synthesis_dossier.json）")
    else:
        L.append("-（解读卡未提供 open_questions）")
    L.append("")

    # ── 附录：文献清单与证据层级 ──
    L.append("---")
    L.append("")
    L.append("## 附录：本概貌依据的文献")
    L.append("")
    L.append("| # | 题录 | 类型 | 年份 | 开放获取 | 证据层级 |")
    L.append("|---|---|---|---|---|---|")
    for i, c in enumerate(cards, 1):
        oa = "[OK]" if c.get("is_oa") else "—"
        ev = c.get("evidence")
        ev = ev.get("source", "—") if isinstance(ev, dict) else _as_text(ev)
        title = _as_text(c.get("title"))[:60].replace("|", "／")
        L.append(f"| {i} | {title} | "
                 f"{_as_text(c.get('field_role'))[:12] or '—'} | "
                 f"{_as_text(c.get('year'))} | {oa} | {ev or '—'} |")
    L.append("")
    L.append("> [WARN] **证据层级说明**：`abstract` = 仅依据摘要解读；"
             "`metadata-only` = 连摘要都没有，仅据题名与期刊；`fulltext` = 读到全文。"
             "本概貌如有多数为摘要级，请在引用前核对原文。")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="reviews_build.py",
        description="阶段 R②：把并行 subagent 的解读卡汇总为《领域概貌.md》。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=".work_cdp/reviews", help="阶段 R 的工作目录")
    ap.add_argument("--out", help="输出文件名（默认 <dir>/领域概貌.md）")
    ap.add_argument("--topic", default="", help="领域名（用于标题，可不填）")
    ap.add_argument("--status", action="store_true", help="只看进度，不生成报告")
    ap.add_argument("--allow-placeholder", action="store_true",
                    help="放宽校验：允许内容含 placeholder/空值的卡进入报告"
                         "（默认拒收这类卡，因为它们通常意味着生成被截断）")
    args = ap.parse_args(argv)

    log = get("reviews")
    d = pathlib.Path(args.dir)
    if not d.is_absolute():
        d = WD / d
    if not d.is_dir():
        print(f"[X] 目录不存在：{d}；先跑 reviews_prepare.py", file=sys.stderr)
        return 2

    gate = None
    gate_path = d / "gate_report.json"
    if gate_path.exists():
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
        except Exception:
            gate = {"verdict": "invalid", "exit_code": 4}
    if isinstance(gate, dict) and gate.get("verdict") != "complete":
        print(f"[X] 统一质量门未通过：{gate.get('verdict', 'unknown')}", file=sys.stderr)
        print("  请先运行：uv run python scripts/subagent_gate.py --stage R2", file=sys.stderr)
        return int(gate.get("exit_code") or 4)

    tasks = sorted((d / "tasks").glob("*.json")) if (d / "tasks").is_dir() else []
    cards, bad, rejected = load_cards(d, strict=not args.allow_placeholder)

    if args.status:
        print(f"任务包：{len(tasks)}    可用解读卡：{len(cards)}")
        done = {c["_file"] for c in cards}
        missing = [t.name for t in tasks if t.name not in done]
        if missing:
            print(f"\n尚未产出**可用**解读卡（{len(missing)}）：")
            for m in missing[:20]:
                print(f"  · {m}")
        if rejected:
            print(f"\n[WARN] 内容为占位/截断而**拒收**的卡（{len(rejected)}）：")
            for name, probs in rejected[:10]:
                print(f"  · {name}")
                print(f"      {', '.join(probs)}")
            print("\n  → 这些卡结构合法但内容是 placeholder/空值，")
            print("     多半是并行 subagent 触发了生成长度上限。")
            print("     建议**降低并发度**（每批 3–4 个）重跑这些篇目。")
        if bad:
            print(f"\n[WARN] 读不出来的文件（{len(bad)}）：")
            for b in bad[:10]:
                print(f"  · {b}")
        return 0

    if not cards:
        print("[X] 还没有**可用**的解读卡；先并行分派 subagent 生成 results/*.json",
              file=sys.stderr)
        if rejected:
            print(f"  （有 {len(rejected)} 张卡因占位/截断被拒收，见 --status）",
                  file=sys.stderr)
        return 2

    mf = None
    mp = d / "manifest.json"
    if mp.exists():
        try:
            mf = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass

    # ── 综述解读档位：task packet → manifest → prepare_plan.json → L3 ──
    tier, tier_src = resolve_tier(tasks, mf)

    text = render(cards, mf, topic=args.topic, tier=tier)
    outp = pathlib.Path(args.out) if args.out else (d / "领域概貌.md")
    if not outp.is_absolute():
        outp = WD / outp
    outp.parent.mkdir(parents=True, exist_ok=True)   # 允许输出到尚不存在的目录

    # ── 综合底稿 + 综述综合步任务（paper-spine 式：综合论证而非拼接）──
    dossier = build_dossier(cards)
    dos_p = d / "synthesis_dossier.json"
    dos_p.write_text(json.dumps(dossier, ensure_ascii=False, indent=1), encoding="utf-8")
    syn_p = d / "synthesis_task.json"
    syn_p.write_text(json.dumps({
        "stage": "R2-synthesis",
        "instruction": "读 synthesis_dossier.json 与全部解读卡，按 subagent-driven-review/"
                       "references/r2-synthesis.md 的综述写法撰写终稿《领域概貌.md》："
                       "立 spine→跨源综合共识→合并方向/概念→有界不确定性→引综述，禁止逐卡拼接。",
        "dossier": str(dos_p),
        "prompt": "subagent-driven-review/references/r2-synthesis.md",
        "output": str(outp),
        "n_reviews": len(cards),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    from vosviewer_export import export_stage
    keyword_export = export_stage(WD, 'R2', outdir=d / 'vosviewer', stage_dir=d)
    text += ('\n\n## 完整术语与 VOSviewer 导出\n'
             + f'完整术语节点 {keyword_export["terms"]} 个，共现边 {keyword_export["links"]} 条。'
             + 'core+method+reagent 全三层并集、min_occurrences=1（保留孤立词）；每文献二值计数；'
             + '不预设聚类；证据与剔除原因见 '+str(d/'vosviewer')+'。\n')
    text += ('\n## 综述综合步（待执行）\n'
             + f'本文件为**合并底稿渲染版**。终稿应由一个综述综合 subagent 依 '
             + f'`synthesis_dossier.json`（{len(dossier["directions"])} 合并方向 / '
             + f'{len(dossier["consensus"])} 共识 / {len(dossier["concepts_core"])} 核心概念）'
             + '按 `subagent-driven-review/references/r2-synthesis.md` 撰写（综合共识，非拼接）。\n')
    outp.write_text(text, encoding="utf-8")

    n_task = len(tasks)
    log.info(f"[OK] 汇总完成：{len(cards)} 张卡 → {outp}")
    if n_task and len(cards) < n_task:
        log.warn(f"[WARN] 任务包 {n_task} 个，但只有 {len(cards)} 张可用卡 —— "
                 f"报告基于**部分文献**，请在交付时如实说明")
    if rejected:
        log.warn(f"[WARN] {len(rejected)} 张卡因内容占位/截断被拒收（未进入报告）："
                 f"{[n for n, _ in rejected][:5]}")
        log.warn("   多半是并行 subagent 触发了生成长度上限 → 降低并发度（每批 3–4 个）重跑")
    if bad:
        log.warn(f"[WARN] {len(bad)} 个结果文件无法解析，已跳过：{bad[:5]}")
    journal("R2", "reviews.build", cards=len(cards), tasks=n_task,
            rejected=len(rejected), interpret_tier=tier)
    print(f"解读档位：{tier}（{TIER_LABELS.get(tier, '')}；来源：{tier_src}）")
    print(f"→ {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
