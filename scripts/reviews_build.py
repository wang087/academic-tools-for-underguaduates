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
#   uv run python 流程1/scripts/reviews_build.py
#
#   # ② 自定义目录 / 输出文件名
#   uv run python 流程1/scripts/reviews_build.py \
#       --dir .work_cdp/reviews --out .work_cdp/reviews/领域概貌.md
#
#   # ③ 只看状态（有几张卡、缺哪几篇）
#   uv run python 流程1/scripts/reviews_build.py --status
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
    r"placeholder|待补|待填|TODO|TBD|\.\.\.$|^无$|^N/?A$", re.I)


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

    # ── 这个领域在解决什么问题 ──
    L.append("## 这个领域在解决什么问题")
    L.append("")
    probs = []
    for c in cards:
        p = (c.get("problem_addressed") or "").strip()
        if p and p not in probs:
            probs.append(p)
    for p in probs[:8]:
        L.append(f"- {p}")
    if not probs:
        L.append("-（解读卡未提供）")
    L.append("")

    # ── 主要研究方向与流派（按出现篇数排序）──
    L.append("## 主要研究方向与流派")
    L.append("")
    dirs: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for c in cards:
        for d in (c.get("main_directions") or []):
            if isinstance(d, dict) and d.get("name"):
                dirs[str(d["name"]).strip()].append(
                    (_as_text(d.get("idea")), _as_text(d.get("conclusion")),
                     (c.get("title") or "")))
            elif isinstance(d, str) and d.strip():
                dirs[d.strip()].append(("", "", c.get("title") or ""))

    if dirs:
        # 只列**每个方向一次**，取最长的 idea/conclusion 作代表（信息量最大），
        #   并汇总所有来源综述。早期版本对每条 (方向,论文) 组合都出一行，
        #   12 篇卡会摊成 30+ 行且"出现篇数"恒为 1（不同论文措辞不同，永不合并）。
        L.append("> 同一方向被多篇综述提到时合并为一行；「出现篇数」≥2 说明该方向被反复讨论。")
        L.append("")
        L.append("| 方向 | 核心思路 | 代表综述 | 出现篇数 |")
        L.append("|---|---|---|---|")
        rows = []
        for name, items in dirs.items():
            idea = max((i for i, _, _ in items), key=len, default="")
            titles = []
            for _, _, t in items:
                if t and t not in titles:
                    titles.append(t)
            rows.append((len(items), name, idea, titles))
        # 出现篇数降序；同篇数按方向名排序，便于查找
        for n, name, idea, titles in sorted(rows, key=lambda r: (-r[0], r[1])):
            shown = "; ".join(t[:32] for t in titles[:2])
            if len(titles) > 2:
                shown += f" 等 {len(titles)} 篇"
            L.append(f"| {name} | {idea[:110]} | {shown} | {n} |")
        L.append("")
        L.append(f"> 共 {len(rows)} 个方向。**方向名由各综述自行表述，措辞不同但含义相近的"
                 f"不会被自动合并** —— 需要合并请人工归并。")
    else:
        L.append("-（解读卡未提供 main_directions）")
    L.append("")

    # ── 共识 vs 争议（本科生选题最该看）──
    L.append("## 目前的一致结论 vs 仍有争议的地方")
    L.append("")
    L.append("### 一致结论（多篇综述都这么认为）")
    L.append("")
    cons = Counter()
    for c in cards:
        for x in (c.get("consensus") or []):
            k = _as_text(x)
            if k:
                cons[k] += 1
    if cons:
        for k, v in cons.most_common(14):
            mark = " " if v >= 2 else ""
            L.append(f"- {k}{mark}")
        L.append("")
        L.append("> = 两篇以上综述都提到，可信度更高。")
    else:
        L.append("-（解读卡未提供 consensus）")
    L.append("")

    L.append("### 仍有争议 / 未解决")
    L.append("")
    contro = []
    for c in cards:
        for x in (c.get("controversies") or []):
            k = _as_text(x)
            if k and k not in contro:
                contro.append(k)
    if contro:
        for k in contro[:14]:
            L.append(f"- {k}")
        L.append("")
        L.append("> [WARN] 争议点**原样并列**，没有替你判定谁对。"
                 "想找课题，这里往往是最好的切入口。")
    else:
        L.append("-（解读卡未提供 controversies）")
    L.append("")

    # ── 关键概念速查（按出现篇数排序）──
    L.append("## 关键概念速查")
    L.append("")
    concepts: dict[str, dict] = {}
    freq = Counter()
    # 合并键的选取顺序很关键：**先用中文名**。
    #   早期版本用英文名做键，于是同一个概念因英文写法不同而分裂成多行 ——
    #   实测「肿瘤免疫微环境」出现了 3 行（TIME / Tumor immune microenvironment /
    #   TME），"出现篇数"被拆成 2+3+3 而不是应有的合并计数。
    #   中文名在各 subagent 之间一致性明显更高，故以其为首选键。
    for c in cards:
        seen_in_card = set()
        for k in (c.get("key_concepts") or []):
            if isinstance(k, dict) and (k.get("cn") or k.get("en")):
                key = (_norm_key(k.get("cn")) or _norm_key(k.get("en")))
                if not key:
                    continue
                # 同一篇综述内的重复只计一次（避免"出现篇数"虚高）
                if key not in seen_in_card:
                    freq[key] += 1
                    seen_in_card.add(key)
                old = concepts.get(key)
                if old is None or len(str(k.get("plain") or "")) > len(str(old.get("plain") or "")):
                    merged = dict(k)
                    # 保留首次见到的英文名，避免被空值覆盖
                    if not merged.get("en") and old:
                        merged["en"] = old.get("en", "")
                    concepts[key] = merged
            elif isinstance(k, str) and k.strip():
                key = _norm_key(k)
                if key not in seen_in_card:
                    freq[key] += 1
                    seen_in_card.add(key)
                concepts.setdefault(key, {"cn": k.strip(), "en": "", "plain": ""})
    if concepts:
        L.append("| 术语 | 英文 | 解释 | 出现篇数 |")
        L.append("|---|---|---|---|")
        # 按出现篇数降序：反复出现的才是必懂的
        for key, n in freq.most_common():
            k = concepts[key]
            L.append(f"| {_as_text(k.get('cn'))} | {_as_text(k.get('en'))} | "
                     f"{_as_text(k.get('plain'))[:130]} | {n} |")
        L.append("")
        L.append("> 「出现篇数」= 有多少篇综述提到该术语。**≥2 的属于领域核心概念**，"
                 "优先掌握；=1 的多是该篇综述的专门术语。")
    else:
        L.append("-（解读卡未提供 key_concepts）")
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

    # ── 选题切入点 ──
    L.append("## 可能的选题切入点")
    L.append("")
    oq = []
    for c in cards:
        for q in (c.get("open_questions") or []):
            k = _as_text(q)
            if k and k not in oq:
                oq.append(k)
    if oq:
        for q in oq[:10]:
            L.append(f"- {q}")
        L.append("")
        L.append("> 这些是综述里**仍未解决**的问题，适合作为大创/研究计划的起点。")
        if len(oq) > 10:
            L.append(f"> （另有 {len(oq) - 10} 条见各篇解读卡）")
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
