# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 0：准备阶段（Prepare Stage）—— 各项选择确认，决定后续阶段是否执行
#
# 为什么要有这一步
#   旧流程从阶段 A 直接开跑，把"要不要翻译""要不要做 PPT""要不要反向查参考文献"
#   这类**只有用户能决定**的问题留到执行中途才问，结果是：
#     · 跑了 40 分钟全文下载，才发现用户只要一份 Markdown 列表；
#     · 反向参考文献网络是解读报告质量的关键输入，却常常没人想到要做。
#   本脚本把这些问题**前置到第 0 步**，一次性收齐，落盘成单一事实源
#   `.work_cdp/prepare_plan.json`，后续每个阶段开头只需问它一句。
#
# 本脚本**只读写本地文件**，不发起任何网络请求。
#
# 用法：
#   # ① 非交互（agent 在对话里收齐答案后调用；推荐）
#   uv run python 流程1/scripts/prepare_stage.py \
#       --set output_format=both --set reverse_refs=yes --set translate=no --set make_ppt=yes
#
#   # ② 从答案文件读
#   uv run python 流程1/scripts/prepare_stage.py --answers=answer.json
#
#   # ③ 全默认（保守：只出 Markdown 列表 + 解读，不翻译、不做 PPT）
#   uv run python 流程1/scripts/prepare_stage.py --defaults
#
#   # ④ 查看当前计划
#   uv run python 流程1/scripts/prepare_stage.py --show
#
#   # ⑤ 阶段闸门（供 run_all.sh / agent 判定某阶段是否执行）
#   uv run python 流程1/scripts/prepare_stage.py --check translate   # 0=执行 3=跳过
#   uv run python 流程1/scripts/prepare_stage.py --check-all         # 打印全部闸门状态
#
#   # ⑥ ★ v9：解读档位查询（参数，不是开关 —— 所以不能用 --check 问）
#   uv run python 流程1/scripts/prepare_stage.py --tier H    # 作者论文档位
#   uv run python 流程1/scripts/prepare_stage.py --tier H2   # 综述网络档位
#   uv run python 流程1/scripts/prepare_stage.py --tier R2   # 领域概貌档位
#   #     stdout 只有档位本身（L1|L2|L3），便于 `T=$(… --tier H)` 捕获
#
#   # ⑦ ★ v9：门控齐全性自检
#   uv run python 流程1/scripts/prepare_stage.py --audit
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import sys

def _fix_streams() -> None:
    """UTF-8 兜底（**必须幂等**）。

    ★ 为什么不能无条件 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`：
      本流程的脚本之间会互相 import（refs_bibliography → refs_backfill，
      refs_build → refs_prepare）。每 import 一次就重包一层，中间那层 wrapper
      被 GC 时会**关掉底层 buffer**，表现为后续所有 print 抛
      "ValueError: I/O operation on closed file" —— 而且只在特定 import 顺序下复现。
      这里与 `logutil._fix_stdout()` 用同一套判据：已是 UTF-8 就不再包。
    """
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
except Exception:                      # 单文件运行时也要能用
    def get(name):                     # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)
        return _L()

    def journal(*a, **k):               # noqa: D103
        pass

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WD / ".work_cdp"
PLAN = CDPDIR / "prepare_plan.json"

# ══════════════════════════════════════════════════════════════
# 选项定义 —— 单一事实源。新增选项只需在这里加一行。
#
#   key            选项 ID（写进 prepare_plan.json）
#   question       向用户提出的问题（中文，直接可读）
#   kind           bool | enum
#   values         enum 的候选值
#   default        默认值（保守优先：不产生额外抓取、不产生额外交付物）
#   gates         该选项控制哪些阶段的执行
#   gate_rule      该选项取值 → 阶段是否执行
#   note           选项说明（为什么要问这个）
# ══════════════════════════════════════════════════════════════
OPTIONS = [
    {
        "key": "output_format",
        "question": "输出文件格式选择：PDF、Markdown，还是两者都要？",
        "kind": "enum",
        "values": ["markdown", "pdf", "both"],
        "default": "markdown",
        # ★ v13.1：本项**不再是闸门**，改为纯参数。
        #   改前 gates=["F","G"] 且 gate_rule 把 markdown 映射成 F=skip，
        #   与它自己的 note、translate 的 note、_f_mode="html-only"、
        #   _translate_output="markdown"、render() 的提示语**五处**相互矛盾：
        #   translate=yes + markdown 会**跳过阶段 F**，用户要了翻译却什么都拿不到。
        #   现改由 `translate` 单独判定阶段 F 是否执行，本项只决定
        #   「顺带渲染 PDF 与否」（经 `_f_mode` 表达）。详见 §9.16。
        "gates": [],
        "gate_rule": {},
        "note": ("markdown = 只出 .md 交付物（论文列表 / 解读 / 综述），不渲染 PDF；"
                 "pdf = 额外渲染中译 PDF；both = 两者都要。"
                 "★ 只影响**产物格式**，不影响是否下载原文 PDF —— 原文 PDF 是解读的输入，一律要下。"
                 "★ 也**不决定阶段 F 是否执行**：那由 `translate` 单独判定。"
                 "本项只在阶段 F 执行时决定「要不要顺带渲染 PDF」"
                 "（translate=yes 且 output_format=markdown → 产出中译 Markdown，不渲染 PDF）。"),
    },
    {
        "key": "translate",
        "question": "是否进行翻译环节（全文中文翻译 → PDF）？",
        "kind": "bool",
        "default": False,
        "gates": ["F"],
        "gate_rule": {True: {"F": "run"}, False: {"F": "skip"}},
        "note": ("翻译是逐篇人工级工作量（素材链 → HTML → PDF → CJK 校验），"
                 "且只在确实要中文精读时才值得做。"
                 "★ 与 output_format 的关系：translate=no 时阶段 F 整体跳过；"
                 "translate=yes 而 output_format=markdown 时，产出中译 Markdown 而不渲染 PDF。"),
    },
    {
        "key": "reverse_refs",
        "question": "是否反向查找相关参考文献，形成该研究方向整体综述网络？",
        "kind": "bool",
        "default": True,
        "gates": ["D2", "G2", "H2"],
        "gate_rule": {True: {"D2": "run", "G2": "run", "H2": "run"},
                      False: {"D2": "skip", "G2": "skip", "H2": "skip"}},
        "note": ("反向查找 = 取作者每篇论文的**参考文献**，倒推出这位作者站在谁的肩上，"
                 "从而还原整个研究方向的全景（而不只是这位作者自己的产出）。"
                 "★ 强烈建议开启：这是把「作者论文分析」升级为「研究方向综述」的关键一步，"
                 "且全程只走元数据 API（OpenAlex），零风控风险、不额外下载任何 PDF。"),
    },
    {
        "key": "make_graphs",
        "question": "是否绘制知识图谱（overview / 词云 / 方向聚类）？",
        "kind": "bool",
        "default": True,
        "gates": ["K"],
        "gate_rule": {True: {"K": "run"}, False: {"K": "skip"}},
        "note": ("三张图：① overview（作者为中心、阶段→方向→综述、Louvain 社区上色、"
                 "记忆连接）；② 词云（剔除小鼠等误导词，聚焦方法与实验范式）；"
                 "③ 方向聚类（近 3 年热点）。"
                 "★ 位置固定在**所有搜集工作完成之后、PPT 制作之前**（阶段 H2 之后、"
                 "阶段 P 之前）—— 图要吃的正是「最终」产物，早画必然过期。"
                 "★ SVG 优先、失败自动退 PNG，背景透明。"
                 "★ 只有 `make_ppt` 或 `make_graphs` 至少一个开启时才需要跑本阶段。"),
    },
    {
        "key": "graph_recent_years",
        "question": "方向聚类看最近几年（默认 3 年）？",
        "kind": "enum",
        "values": ["2", "3", "5"],
        "default": "3",
        "gates": [],
        "gate_rule": {},
        "note": "「近 3 年最新论文与热点」的窗口。样本不足时脚本**如实留空**，不拿旧论文冒充最新进展。",
    },
    {
        "key": "make_ppt",
        "question": "是否制作相关 PPT（把文献亮点解读 + 综述结果做成演示文稿）？",
        "kind": "bool",
        "default": False,
        "gates": ["P"],
        "gate_rule": {True: {"P": "run"}, False: {"P": "skip"}},
        "note": ("PPT 材料固定取自本流程的最终执行结果 —— 《<作者名>_论文解读.md》的文献亮点"
                 "与《<作者名>_研究方向综述.md》的 review 结果，不需要另行准备素材。"),
    },
    {
        "key": "refs_bibliography",
        "question": "是否输出「有代表性文献」的参考文献列表（GB/T 7714）？",
        "kind": "bool",
        "default": True,
        "gates": ["G2"],
        "gate_rule": {True: {"G2": "run"}, False: {"G2": "skip"}},
        "note": ("代表性文献 = 发表于 Nature/Science/Cell/PNAS 等核心期刊，"
                 "或「本人为第一/通讯作者」，或「中科院一区 / IF>5.0」。"
                 "★ 与 reverse_refs 的关系：reverse_refs 关掉时本项**自动关闭**（没有参考文献可取）。"),
    },
    {
        "key": "curate_review",
        "question": "是否对反向查得的文献做去重与研究方向相关性整理，并据此产出 review？",
        "kind": "bool",
        "default": True,
        "gates": ["H2"],
        "gate_rule": {True: {"H2": "run"}, False: {"H2": "skip"}},
        "note": ("去重 + 剔除与作者主体研究方向无关的论文，形成最终文献列表，"
                 "再据此产出研究方向综述（review）。"
                 "★ 与 reverse_refs 的关系：reverse_refs 关掉时本项**自动关闭**。"),
    },
    {
        "key": "undergrad_prelude",
        "question": "是否执行本科科研辅助前置链路（阶段 N 大方向确定 → S 匹配细分专业 → R 平行检索综述 → R2 领域概貌）？",
        "kind": "bool",
        "default": True,
        "gates": ["N", "S", "R", "R2"],
        "gate_rule": {True: {"N": "run", "S": "run", "R": "run", "R2": "run"},
                      False: {"N": "skip", "S": "skip", "R": "skip", "R2": "skip"}},
        "note": ("★ 前置链路与原有链路的关系：本科入门场景通常**只走 N→S→R→R2** 就到交付"
                 "（领域概貌 + 导师候选 + 综述清单）；只有用户已锁定某位导师、想深挖其成果时，"
                 "才需要再接阶段 0 之后的原有链路。"
                 "★ 默认开启：这是 v8 起为本科生设定的默认入口。"
                 "★ 关掉它并不会关闭原有链路（A→…→I）；两条链路相互独立。"),
    },
    {
        "key": "interpret_tier",
        "question": "论文解读档位（阶段 H，作者本人的论文）：L1 精简 / L2 详细 / L3 完整？",
        "kind": "enum",
        "values": ["L1", "L2", "L3"],
        "default": "L2",
        "gates": [],
        "gate_rule": {},
        "note": ("三档只差**细节量**，不差**语气强度** —— 同一个发现三档的动词强度一致"
                 "（原文说 suggest，三档都写「提示」）。"
                 "L1 = 一句话定位 + 主要成就 + 关键位点与结论数字（2–5 个），"
                 "不含误差项 / 样本量 / 图表引用 / 实验步骤，适合表格；"
                 "L2 = 另加误差项、样本量、有限实验过程与排除性证据，适合逐篇卡片；"
                 "L3 = 另加完整论证链、机制细节与逐条局限，适合综述与代表作。"
                 "★ 默认 L2：阶段 H 的产物《<作者名>_论文解读.md》是**逐篇卡片**，"
                 "正是 L2 的目标形态。要恢复到与旧版等深的全量解读，选 L3。"
                 "★ 规范性规则见 skill：`paper-interpretation-writing`"
                 "（档位规范、句式库、跨 agent 数据契约）。"),
    },
    {
        "key": "review_interpret_tier",
        "question": "综述解读档位（阶段 H2 研究方向综述 / 阶段 R2 领域概貌）：L1 / L2 / L3？",
        "kind": "enum",
        "values": ["L1", "L2", "L3"],
        "default": "L3",
        "gates": [],
        "gate_rule": {},
        "note": ("★ 默认 L3：阶段 H2 的《<作者名>_研究方向综述.md》与阶段 R2 的《领域概貌.md》"
                 "都是**独立成篇**的综述类交付物，需要完整论证链、排除性证据与逐条局限。"
                 "综述读者要据此判断一篇文献在网络中的位置与证据强度 —— "
                 "低档会丢掉排除性证据与作者语气限定，而那正是综述最需要的部分。"
                 "★ 阶段 R2 的读者是没有科研经验的本科生，L3 还要额外解释术语、"
                 "给出推荐阅读顺序（见 subagent-driven-review § 7.2）。"
                 "★ 规范性规则见 skill：`paper-interpretation-writing`。"),
    },
    {
        "key": "ppt_pages",
        "question": "PPT 目标页数（简化 Strategist 用，无需交互确认）？",
        "kind": "enum",
        "values": ["short", "standard", "long"],
        "default": "standard",
        "gates": [],
        "gate_rule": {},
        "note": "short ≈ 10–14 页（组会/快报）；standard ≈ 16–22 页（常规汇报）；long ≈ 24–32 页（答辩/大综述）。",
    },
]

BY_KEY = {o["key"]: o for o in OPTIONS}

# ★ 物理执行顺序：准备阶段在最前（它产出闸门表），随后是前置链路，
#   再接原有链路。A→I 的相对顺序与编号语义**完全未变**（v9 只在前面插入了
#   前置链路与准备阶段），因此既有脚本与文档的引用不受影响。
VALID_STAGES = ["0", "N", "S", "R", "R2",
                "A", "B", "C", "D", "D2", "E", "F", "G", "G2", "H", "H2",
                "K", "P", "I"]

STAGE_LABEL = {
    "0": "准备阶段（各项选择确认 → prepare_plan.json）",
    "N": "大方向确定（拆解输入 + 匹配关键词群）",
    "S": "匹配细分专业（抓导师名单 → 归类 → 等用户选择）",
    "R": "平行检索优质综述（按方向选库 + 综述优先）",
    "R2": "领域概貌解读（面向本科生）",
    "A": "作者定位与工作目录",
    "B": "启动 CDP 浏览器",
    "C": "抓取 ORCID 全部成果",
    "D": "Crossref 核实 + 同名消歧",
    "D2": "反向查找参考文献（综述网络 + 高质量文献列表）",
    "E": "全文获取（统一走 CDP）",
    "F": "翻译与 PDF 导出",
    "G": "生成论文列表（GB/T 7714）",
    "G2": "代表性文献的参考文献列表",
    "H": "论文解读（并行 subagent）",
    "H2": "去重与相关性整理 → 最终列表 + 研究方向综述",
    "K": "知识图谱（overview / 词云 / 方向聚类）",
    "P": "PPT 生成（整合 ppt-master）",
    "I": "收尾",
}

# ★ 解读档位的查询表：阶段 → 决定它档位的选项 key。
#   两个档位是**参数**而非开关（gates 为空），所以不能用 --check 问，
#   要用 --tier 问。H 用作者档位；H2 与 R2 都是综述类，共用综述档位。
TIER_STAGE_KEYS = {
    "H": "interpret_tier",
    "H2": "review_interpret_tier",
    "R2": "review_interpret_tier",
    # 阶段 P 的素材取自《论文解读》与《研究方向综述》，两个档位都会用到；
    # 单独查询时返回作者档位（PPT 的文献亮点主要来自它）。
    "P": "interpret_tier",
}

TIER_VALUES = ("L1", "L2", "L3")

BOOL_TRUE = {"1", "true", "yes", "y", "on", "是", "要", "需要", "开启", "做"}
BOOL_FALSE = {"0", "false", "no", "n", "off", "否", "不要", "不需要", "关闭", "不做"}


def _arg(name, default=None):
    """取参数值。**同时支持 `--check=D2` 与 `--check D2` 两种写法。**

    ★ 为什么两种都要支持：`--check D2` 是几乎所有人第一反应的写法。
      只认 `=` 形式时，`--check D2` 会被解析成「--check 无值（True）」+「D2 被忽略」，
      然后拿 True 去查阶段表 → 报「未知阶段」并退出 2。
      这不会崩溃，但会把调用方（run_all.sh / 自检 / agent）引到错误结论上。
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
        if a == f"--{name}":
            # 裸开关：下一个 token 若不是另一个 --flag，就当作它的值
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
            return True
    return default


def _arg_multi(name):
    """收集可重复参数。**同时支持 `--set k=v` 与 `--set=k=v`。**

    ★ 为什么必须两种都认：只认 `=` 形式时，`--set output_format=pdf`
      会**被整个忽略**（既不报错也不生效），随后 `build_plan` 退化成全默认值 ——
      用户明确给的选择被静默丢弃，是比崩溃严重得多的失败模式。
      实测就是自检脚本用空格写法调用时，选项全部落回默认。
    """
    argv = sys.argv[1:]
    out = []
    for i, a in enumerate(argv):
        if a.startswith(f"--{name}="):
            out.append(a.split("=", 1)[1])
        elif a == f"--{name}" and i + 1 < len(argv) and "=" in argv[i + 1]:
            out.append(argv[i + 1])
    return out


def coerce(key: str, raw):
    """把命令行/答案文件的原始值转成规范取值；非法值抛 ValueError。"""
    o = BY_KEY[key]
    if o["kind"] == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in BOOL_TRUE:
            return True
        if s in BOOL_FALSE:
            return False
        raise ValueError(f"{key}: 无法识别的布尔值 {raw!r}（可用 yes/no、true/false、是/否）")
    vals = o["values"]
    s = str(raw).strip()
    # ★ 大小写不敏感匹配，但返回**规范写法**。解读档位的取值是 L1/L2/L3，
    #   而 --set interpret_tier=L3 里的 "L3" 若按 .lower() 比对就永远匹配不上
    #   'L3'（枚举里就是大写），用户会收到"取值不在列表内"这种莫名其妙的话。
    #   返回规范写法还顺带统一了 l3/L3 两种输入落到同一取值。
    for v in vals:
        if s.lower() == str(v).lower():
            return v
    raise ValueError(f"{key}: 取值 {raw!r} 不在 {vals} 内")


def resolve_gates(plan: dict) -> dict:
    """把选项解析成每个阶段 run/skip —— 后续所有阶段只认这个结果。"""
    gate = {s: "run" for s in VALID_STAGES}
    hard_off = {}          # 阶段 → 被谁关掉（用于解释原因）

    for o in OPTIONS:
        if not o["gates"]:
            continue
        val = plan.get(o["key"], o["default"])
        rule = o["gate_rule"].get(val)
        if rule is None:
            continue
        for stage, act in rule.items():
            if act == "skip" and gate.get(stage) != "skip":
                gate[stage] = "skip"
                hard_off[stage] = o["key"]

    # ── 依赖收敛：上游关掉时下游必须一起关，且要说明**是因为谁** ──
    # ★ 图谱关掉不强制关闭 PPT（PPT 只用文字素材也能做），但要能解释
    #   "为什么 PPT 里没有图" —— 写进 gate_reasons，别让人以为是漏跑了。
    if plan.get("make_graphs") is False and plan.get("make_ppt") is True:
        plan["_ppt_note"] = "make_graphs=no → 阶段 P 不会带知识图谱，只做文字版面"

    if gate.get("D2") == "skip":
        for s in ("G2", "H2"):
            if gate.get(s) != "skip":
                gate[s] = "skip"
                hard_off[s] = "reverse_refs（上游阶段 D2 未执行）"
    if plan.get("translate") is False and gate.get("F") != "skip":
        gate["F"] = "skip"
        hard_off["F"] = "translate"

    # ── 阶段 F 的形态：过滤掉「PDF 渲染」而不是整个阶段 ──
    fmt = plan.get("output_format", "markdown")
    if gate.get("F") == "run":
        plan["_f_mode"] = "html+pdf" if fmt in ("pdf", "both") else "html-only"
    else:
        plan["_f_mode"] = "off"

    return gate, hard_off


def build_plan(sets: dict, defaults: bool = False) -> dict:
    plan = {}
    for o in OPTIONS:
        plan[o["key"]] = o["default"] if defaults else o["default"]
    for k, v in sets.items():
        if k not in BY_KEY:
            raise KeyError(f"未知选项 {k!r}；可用：{', '.join(BY_KEY)}")
        plan[k] = coerce(k, v)

    # 输出格式对阶段 F 的隐含约束（写进计划，让阶段 F 有据可依）
    if plan["output_format"] == "markdown" and plan["translate"]:
        plan["_translate_output"] = "markdown"
    elif plan["translate"]:
        plan["_translate_output"] = "html+pdf"
    else:
        plan["_translate_output"] = "off"

    gate, hard_off = resolve_gates(plan)
    plan["_gates"] = gate
    plan["_gate_reasons"] = hard_off
    plan["_stages_order"] = VALID_STAGES
    plan["_schema"] = 1
    return plan


def save(plan: dict) -> None:
    CDPDIR.mkdir(parents=True, exist_ok=True)
    tmp = PLAN.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PLAN)          # 原子替换：中途失败不会留下半截计划


def load() -> dict | None:
    if not PLAN.exists():
        return None
    try:
        return json.loads(PLAN.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ prepare_plan.json 解析失败：{e}", file=sys.stderr)
        return None


def gate_audit() -> list[str]:
    """门控齐全性自检 —— 每个阶段都有标签、每个闸门都指向真实阶段。

    ★ 为什么要机器自检：阶段 N/S/R/R2 在 v8 加入时**没有同步进闸门表**，
      于是 `--check N` 返回 2（未知阶段），而 run_all.sh 的 `gate()` 用
      `if gate N; then` 判定 —— 2 与 3 一样落进 else 分支，该阶段被**静默当成跳过**。
      这类缺口靠人记得是不行的，必须能被一条命令查出来。
    """
    problems = []
    for s in VALID_STAGES:
        if s not in STAGE_LABEL:
            problems.append(f"阶段 {s} 缺 STAGE_LABEL 标签")
    for o in OPTIONS:
        rule_stages = {s for rule in (o["gate_rule"] or {}).values() for s in rule}
        for s in o["gates"]:
            if s not in VALID_STAGES:
                problems.append(f"选项 {o['key']} 的 gates 引用了未知阶段 {s}")
            elif o["gate_rule"] and s not in rule_stages:
                problems.append(
                    f"选项 {o['key']} 声明管阶段 {s}，但 gate_rule 未覆盖它"
                )
        for s in rule_stages:
            if s not in VALID_STAGES:
                problems.append(f"选项 {o['key']} 的 gate_rule 引用了未知阶段 {s}")
    for s in TIER_STAGE_KEYS:
        if s not in VALID_STAGES:
            problems.append(f"TIER_STAGE_KEYS 引用了未知阶段 {s}")
    return problems


def ungated_stages() -> list[str]:
    """没有任何选项管辖、恒定 run 的阶段。属信息项，不是错误。"""
    controlled = {s for o in OPTIONS if o["gates"] for s in o["gates"]}
    return [s for s in VALID_STAGES if s not in controlled]


#: 门控表里**已知的语义矛盾**，固化在此以便每次 --audit 都看得见。
#: ★ 只记录、不自动修：改它会变动既有行为，属于另一件事，须单独决策。
#:   加进来是因为「文档说 A、规则做 B」这类问题人眼扫一遍表格是发现不了的。
#:
#: 当前为空 —— 原第 1 条（`output_format=markdown` 关掉阶段 F，与自身 note 及
#: `_f_mode="html-only"`/`_translate_output="markdown"` 矛盾）已由用户拍板、
#: 于 v13.1 修复：该选项不再是闸门，阶段 F 改由 `translate` 单独判定。
#: 记录见 SKILL.md §9.16。**以后新增条目请连同「为什么不能自动修」一起写清。**
AUDIT_NOTES: list[str] = []


def resolve_tier(plan: dict | None, stage: str) -> tuple[str, str, bool]:
    """(档位, 选项 key, 是否为缺省回退) —— 供 --tier 查询与各阶段脚本复用。"""
    key = TIER_STAGE_KEYS[stage]
    default = BY_KEY[key]["default"]
    if not plan:
        return default, key, True
    value = plan.get(key, default)
    if value not in TIER_VALUES:
        return default, key, True
    return value, key, False


def render(plan: dict) -> str:
    L = []
    L.append("准备阶段计划（.work_cdp/prepare_plan.json）")
    L.append("─" * 66)
    for o in OPTIONS:
        v = plan.get(o["key"], o["default"])
        shown = {True: "是", False: "否"}.get(v, str(v))
        L.append(f"  · {o['question']}")
        L.append(f"      → {shown}")
    L.append("")
    L.append("阶段闸门（run = 执行 / skip = 跳过）")
    L.append("─" * 66)
    gate = plan.get("_gates") or {}
    reasons = plan.get("_gate_reasons") or {}
    for s in VALID_STAGES:
        st = gate.get(s, "run")
        mark = "✅ run " if st == "run" else "⏭  skip"
        why = f"   ← 由 {reasons[s]} 关闭" if s in reasons else ""
        L.append(f"  {mark}  阶段 {s:<3} {STAGE_LABEL[s]}{why}")

    # ── 解读档位（参数，不是开关）──────────────────────────────
    L.append("")
    L.append("解读档位（参数；用 `--tier <阶段>` 查询，用 `--set <key>=L1|L2|L3` 改）")
    L.append("─" * 66)
    for stage, key in TIER_STAGE_KEYS.items():
        if stage == "P":
            continue                     # P 只是消费方，不单独列
        o = BY_KEY[key]
        v = plan.get(key, o["default"])
        L.append(f"  · 阶段 {stage:<3} → {v}　（{o['question']}）")
    L.append(f"      H  = 作者论文卡片；H2 / R2 = 综述类交付物，共用 review_interpret_tier。")
    L.append("      ★ 三档只差细节量，语气强度一致；规范见 skill：paper-interpretation-writing。")

    ungated = ungated_stages()
    if ungated:
        L.append("")
        L.append(f"  ℹ 恒定执行（无选项管辖）：{'、'.join(ungated)}")

    if plan.get("_f_mode") == "html-only":
        L.append("")
        L.append("  ℹ 阶段 F 已开启但 output_format=markdown → 只产出中译 Markdown，不渲染 PDF。")
    return "\n".join(L)


def main() -> int:
    # ★ --tier 必须**在 get("prepare") 之前**处理：logutil.get() 会往 stdout
    #   打一行「=== prepare 启动 … ===」，而 --tier 的约定是 stdout 只有档位本身
    #   （调用方写 `T=$(… --tier H)`）。日志行混进去会让捕获到 "INFO … L2"，
    #   下游再拿它去比对 "L2" 就永远不相等 —— 而且是静默失败。
    tier_stage = _arg("tier")
    if tier_stage:
        if tier_stage not in TIER_STAGE_KEYS:
            print(f"✗ 未知阶段 {tier_stage!r}；可查询：{', '.join(TIER_STAGE_KEYS)}",
                  file=sys.stderr)
            return 2
        value, key, fallback = resolve_tier(load(), tier_stage)
        print(value)                     # stdout 只有这一样东西
        if fallback:
            print(f"ℹ 尚无 prepare_plan.json 或取值非法 → 阶段 {tier_stage} 用**默认档位** "
                  f"{value}（选项 {key}）。建议先跑阶段 0 确认。", file=sys.stderr)
        return 0

    log = get("prepare")

    if _arg("show"):
        plan = load()
        if not plan:
            print("✗ 尚未生成 prepare_plan.json；先跑 --defaults 或 --set …")
            return 2
        print(render(plan))
        return 0

    if _arg("check-all"):
        plan = load()
        if not plan:
            print("✗ 尚未生成 prepare_plan.json；先跑阶段 0。")
            return 2
        gate = plan.get("_gates") or {}
        for s in VALID_STAGES:
            print(f"{s}\t{gate.get(s, 'run')}\t{STAGE_LABEL[s]}")
        print("---interpret_tier---")
        for stage, key in TIER_STAGE_KEYS.items():
            if stage == "P":
                continue
            value, _, fallback = resolve_tier(plan, stage)
            note = "（缺省回退）" if fallback else ""
            print(f"{stage}\t{key}\t{value}{note}")
        return 0

    if _arg("audit"):
        problems = gate_audit()
        print("门控齐全性自检（prepare_stage.gate_audit）")
        print("─" * 66)
        print(f"  阶段数：{len(VALID_STAGES)}　选项数：{len(OPTIONS)}")
        print(f"  无选项管辖、恒定执行：{'、'.join(ungated_stages()) or '（无）'}")
        print(f"  解读档位查询表：{', '.join(f'{k}→{v}' for k, v in TIER_STAGE_KEYS.items())}")
        if problems:
            print("\n✗ 发现缺口：")
            for p in problems:
                print(f"  · {p}")
            return 1
        print("\n✅ 门控齐全：每个阶段都有标签，每个闸门都指向真实阶段。")
        if AUDIT_NOTES:
            print(f"\n⚠️ 已知语义矛盾 {len(AUDIT_NOTES)} 条（只记录，不自动修）：")
            for note in AUDIT_NOTES:
                print(f"  · {note}")
        return 0

    chk = _arg("check")
    if chk:
        plan = load()
        if not plan:
            print(f"✗ 尚未生成 prepare_plan.json → 视为**跳过**阶段 {chk}（保守）。", file=sys.stderr)
            return 3
        st = (plan.get("_gates") or {}).get(chk)
        if st is None:
            print(f"✗ 未知阶段 {chk!r}；可用：{', '.join(VALID_STAGES)}", file=sys.stderr)
            return 2
        if st == "skip":
            r = (plan.get("_gate_reasons") or {}).get(chk, "准备阶段选项")
            print(f"⏭  阶段 {chk}（{STAGE_LABEL[chk]}）已按准备阶段选择跳过 —— 原因：{r}")
            return 3
        print(f"✅ 阶段 {chk}（{STAGE_LABEL[chk]}）已启用")
        return 0

    sets = {}
    for kv in _arg_multi("set"):
        if "=" not in kv:
            print(f"✗ --set 需要 k=v 形式，收到 {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        sets[k.strip()] = v.strip()

    ans = _arg("answers")
    if ans:
        p = pathlib.Path(ans)
        if not p.exists():
            print(f"✗ 答案文件不存在：{p}", file=sys.stderr)
            return 2
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"✗ 答案文件解析失败：{e}", file=sys.stderr)
            return 2
        for k, v in (data or {}).items():
            if str(k).startswith("_"):
                continue
            sets.setdefault(k, v)

    defaults = bool(_arg("defaults")) or not sets
    try:
        plan = build_plan(sets, defaults=defaults)
    except (KeyError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    save(plan)
    log.info(f"准备阶段计划已写入 {PLAN}")
    journal("0", "prepare.plan", **{o["key"]: plan[o["key"]] for o in OPTIONS})
    print(render(plan))
    print()
    print(f"→ 计划文件：{PLAN}")
    print("  后续每个阶段开头先跑：uv run python 流程1/scripts/prepare_stage.py --check <阶段>"
          "（退出码 0=执行 / 3=跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
