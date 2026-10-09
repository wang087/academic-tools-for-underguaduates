# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H ①：为并行 subagent 组装**自包含**的「单篇解读任务包」
#
# 为什么需要这一步：
#   解读一篇论文要通读全文（1–4 万字符）。把全文直接粘进 subagent 提示词会
#   撑爆主 agent 的上下文；让 subagent 自己去翻 PDF 又要它解析文件、失败率高。
#   折中做法就是这里：**预先**把「题录 + 摘要 + 已抽取的正文文本」固化成
#   一个 JSON 任务包，subagent 拿到即可开读，不必碰网络、不必解析 PDF。
#
# 本脚本**只读本地文件**，不发起任何网络请求（含元数据 API）——
#   摘要取自既有产物，全文取自主流程已下载的 PDF。符合 CDP 强制抓取禁令。
#
# 输入（均在 <WORKDIR>/.work_cdp/）：
#   crossref_all.json（或 master.json）—— 题录总表
#   abstracts_openalex.json（可选）      —— 摘要语料
#   disambiguated.json（可选）           —— 同名他人排除名单（scope.py）
#   .work_cdp/pdf_index.json（可选）     —— DOI → 文件名 登记表
#   prepare_plan.json（可选）            —— 阶段 0 计划；读其中的 interpret_tier（解读档位）
# 全文 PDF：<WORKDIR>/<作者名>_paper_Exports/*.pdf
#
# 产出：
#   <WORKDIR>/.work_cdp/interpret/tasks/<NN>_<doi-slug>.json   单篇任务包（含 interpret_tier）
#   <WORKDIR>/.work_cdp/interpret/manifest.json                任务清单 + 统计（含 interpret_tier）
#   <WORKDIR>/.work_cdp/interpret/paper_cards/                 paper-card 引擎的 16 节卡（仅该引擎）
#
# 解读档位（L1 精简 / L2 详细 / L3 完整）：
#   档位是**参数**，不是闸门 —— 读不到就用默认 L2，绝不因此中断。
#   优先级：命令行 --tier → prepare_plan.json 的 interpret_tier → 默认 L2。
#   档位语义见 skill：paper-interpretation-writing（三档只差细节量，不差语气强度）。
#
# 解读引擎（legacy / paper-card）：
#   引擎也是**参数**，不是闸门 —— 读不到就用默认 legacy。
#   优先级：命令行 --engine → prepare_plan.json 的 interpret_engine → 默认 legacy。
#   paper-card 引擎把已并入的 nature-paper-card 接进阶段 H：subagent 在原有
#     11 字段卡之外**额外**产出 16 节 Paper Card，并用 nature_bridge.py 的
#     check 子命令（结构审计 + 数字回查原文）作为交付质量门。
#   **只增不减**：11 字段卡照旧产出，interpret_build.py 等下游零改动。
#   接线权威见 流程1/nature-integration/SKILL.md。
#
# 用法：
#   uv run --with pypdf python interpret_prepare.py
#     --tier=L1|L2|L3        解读档位（覆盖 prepare_plan.json；缺省走计划文件）
#     --engine=legacy|paper-card  解读引擎（同上）
#     --max-chars=60000      单篇正文截断上限（超出即截断并标注）
#     --only=<DOI,DOI>       只准备指定 DOI
#     --no-text              只出题录与摘要（不读 PDF）
#     --limit=N              只准备前 N 篇（按年份升序）
#     --help                 显示帮助
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import sys

from logutil import run_root as _run_root  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import scope                                       # noqa: E402
import store                                       # noqa: E402
import extract_figures                             # noqa: E402

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
OUTDIR = CDPDIR / "interpret"
TASKDIR = OUTDIR / "tasks"
RESDIR = OUTDIR / "results"
FULLDIR = OUTDIR / "fulltext"        # 超长论文的完整文本（任务包只带「头+尾」）
# 阶段 E 顺带落盘的「落地页正文」（detect.py 在下载 PDF 的同时取出并写盘）。
#   [WARN] 必须在此回退读取，否则**PDF 下载失败但正文已拿到**的篇目会被
#   误判成「仅摘要」——实测刘北明 25 篇里 4 篇如此，其中 2 篇各有 7 万/10 万字
#   完整正文（jneurosci 的 PDF 端点被 Cloudflare 限流，但文章页早已取到）。
CAPTURED_FULLTEXT = CDPDIR / "fulltext"
# 受控标签词表：子流程手册 §6.5 要求**每个任务包都嵌入**，
#   否则并行 subagent 各造各的标签（machine-learning / machine learning / ML /
#   机器学习），汇总时被拆成 4 条弱信号，「范式分析」直接失真。
#   refs_prepare.py（阶段 H2）早已按此实现，本脚本（阶段 H）此前漏了 —— 此处补齐。
TAG_VOCAB_PATH = CDPDIR / "tag_vocabulary.json"
TAG_VOCAB_TPL = pathlib.Path(__file__).resolve().parent.parent / "templates" / "方法范式标签词表.json"
FIGDIR = pathlib.Path(os.environ.get("PAPER_INTERPRET_FIGURES_DIR", str(OUTDIR / "figures")))
# paper-card 引擎的落盘位置：16 节卡片本体 + 它的 source_bundle / 审计报告。
#   与 results/ 分开存放，理由是**产物形态不同**：
#     results/<slug>.json   机器读的 11 字段卡（所有下游消费者只认它）
#     paper_cards/<slug>.*  人读的 16 节深读卡 + 可复核的审计证据
#   混放会让「按文件名取卡」的下游把 16 节 Markdown 当成 JSON 卡读崩。
PCARDDIR = OUTDIR / "paper_cards"
PLAN_PATH = CDPDIR / "prepare_plan.json"     # 阶段 0 的单一事实源：档位从这里读

# ── 解读档位（interpret_tier）────────────────────────────────
#   三档只差**细节量**（L1 ⊂ L2 ⊂ L3），不差语气强度；规范见
#     skill：paper-interpretation-writing/references/tier-specs.md。
#   档位是**参数**不是闸门：计划文件缺失或取值非法一律回退默认 L2 并说明一句，
#     绝不因此中断整个阶段 H。只有命令行**显式**给错才报错退出（那是拼写错误，
#     静默接受会让人以为改档生效了）。
TIER_VALUES = ("L1", "L2", "L3")
DEFAULT_TIER = "L2"
TIER_LABEL = {"L1": "精简", "L2": "详细", "L3": "完整"}

# ── 解读引擎（interpret_engine）────────────────────────────────
#   legacy     = 原有形态：subagent 只产出一张 11 字段解读卡（默认，零破坏）
#   paper-card = 额外产出 16 节 Paper Card（已并入的 nature-paper-card），
#                并把它的「图表清单 / 三态定位 / 溯源标签 / 批判性分析」
#                一并带进阶段 H。**仍然产出**那张 11 字段卡 ——
#                interpret_build.py / ppt_brief.py / 知识图谱都只认它，
#                这是"不同时改动所有下游"的关键。
#   引擎是**参数**不是闸门：读不到就用默认 legacy，绝不因此中断阶段 H。
#   优先级：命令行 --engine → prepare_plan.json 的 interpret_engine → 默认 legacy。
ENGINE_VALUES = ("legacy", "paper-card")
DEFAULT_ENGINE = "legacy"
ENGINE_LABEL = {"legacy": "原有解读卡", "paper-card": "16 节 Paper Card"}

USAGE = """阶段 H ①：为并行 subagent 组装单篇解读任务包（只读本地文件，不联网）

用法：
  python 流程1/scripts/interpret_prepare.py [选项]

选项：
  --tier=L1|L2|L3  解读档位（覆盖 prepare_plan.json 里的 interpret_tier）
                   L1 精简 / L2 详细（默认） / L3 完整
  --engine=legacy|paper-card
                   解读引擎（覆盖 prepare_plan.json 里的 interpret_engine）
                   legacy = 只产出 11 字段解读卡（默认，形态不变）
                   paper-card = **额外**产出 16 节 Paper Card（已并入的
                     nature-paper-card），并下发审计与数字回查的路径与质量门；
                     11 字段卡照旧产出，下游脚本无需改动
  --max-chars=N    单篇正文截断上限（默认 60000）
  --only=DOI,DOI   只准备指定 DOI
  --no-text        只出题录与摘要（不读 PDF）
  --limit=N        只准备前 N 篇（按年份升序）
  -h, --help       显示本帮助并退出

档位优先级：--tier → <WORKDIR>/.work_cdp/prepare_plan.json 的 interpret_tier → 默认 L2
引擎优先级：--engine → <WORKDIR>/.work_cdp/prepare_plan.json 的 interpret_engine → 默认 legacy

产出：
  <WORKDIR>/.work_cdp/interpret/tasks/*.json    单篇任务包（带 interpret_tier 与 interpret_engine）
  <WORKDIR>/.work_cdp/interpret/manifest.json   任务清单 + 统计（含两者）
  <WORKDIR>/.work_cdp/interpret/paper_cards/    paper-card 引擎的 16 节卡与审计证据（仅该引擎）
"""


def _arg(name: str, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def _tier_arg():
    """取 `--tier` 的原始值；未给返回 None。

    两种写法都认（`--tier L3` 与 `--tier=L3`）：只认 `=` 形式时，
      `--tier L3` 会被静默忽略、档位退回计划文件 —— 用户以为改了档其实没改。
      这与 prepare_stage.py `_arg()` 的取舍一致（见那里的注释）。
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a.startswith("--tier="):
            return a.split("=", 1)[1]
        if a == "--tier":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
            return ""            # 裸 --tier 无值 → 交给 resolve_tier 报错
    return None


def _norm_tier(v):
    """规范化为 L1/L2/L3；非法返回 None。

    大小写不敏感但**返回规范写法**：`--tier l2` 落到 "L2"，
      否则下游按 "L2" 比对时永远不相等（prepare_stage.coerce 同款处理）。
    """
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    return s if s in TIER_VALUES else None


def _engine_arg():
    """取 `--engine` 的原始值；未给返回 None。

    两种写法都认（`--engine paper-card` 与 `--engine=paper-card`），
      理由与 `_tier_arg()` 完全相同：只认 `=` 形式时，用户以为换了引擎其实没换。
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a.startswith("--engine="):
            return a.split("=", 1)[1]
        if a == "--engine":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
            return ""            # 裸 --engine 无值 → 交给 resolve_engine 报错
    return None


def _norm_engine(v):
    """规范化为 legacy / paper-card；非法返回 None。

    大小写与连字符都容忍（paper_card / PAPER-CARD 都落到 "paper-card"），
      避免 `--engine paper_card` 被静默回退成 legacy。
    """
    if not isinstance(v, str):
        return None
    s = v.strip().lower().replace("_", "-")
    return s if s in ENGINE_VALUES else None


def resolve_engine(cli_raw):
    """返回 (引擎, 来源)。来源 ∈ cli | plan | default | invalid-cli。"""
    if cli_raw is not None:
        e = _norm_engine(cli_raw)
        if e:
            return e, "cli"
        return "", "invalid-cli"

    plan = load_plan()
    if plan is None:
        raw = None
        why = f"未读到可用的 {PLAN_PATH}（缺失或无法解析）"
    else:
        raw = plan.get("interpret_engine")
        if raw is None:
            why = f"{PLAN_PATH} 里没有 interpret_engine 字段"
        else:
            why = f"{PLAN_PATH} 的 interpret_engine 取值非法：{raw!r}"
    e = _norm_engine(raw)
    if e:
        return e, "plan"
    print(f"[INFO] {why} → 解读引擎回退**默认 {DEFAULT_ENGINE}**"
          f"（{ENGINE_LABEL[DEFAULT_ENGINE]}）。"
          f"可用 --engine legacy|paper-card 指定，"
          f"或在阶段 0 用 `--set interpret_engine=paper-card` 确认。", file=sys.stderr)
    return DEFAULT_ENGINE, "default"


def load_plan():
    """读 prepare_plan.json；缺失或无法解析返回 None（不报错 —— 它只是可选输入）。"""
    if not PLAN_PATH.exists():
        return None
    try:
        d = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def resolve_tier(cli_raw) -> tuple[str, str]:
    """返回 (档位, 来源)。来源 ∈ cli | plan | default | invalid-cli。

    优先级：命令行 --tier → prepare_plan.json 的 interpret_tier → 默认 L2。
    """
    if cli_raw is not None:
        t = _norm_tier(cli_raw)
        if t:
            return t, "cli"
        return "", "invalid-cli"

    plan = load_plan()
    if plan is None:
        raw = None
        why = f"未读到可用的 {PLAN_PATH}（缺失或无法解析）"
    else:
        raw = plan.get("interpret_tier")
        if raw is None:
            why = f"{PLAN_PATH} 里没有 interpret_tier 字段"
        else:
            why = f"{PLAN_PATH} 的 interpret_tier 取值非法：{raw!r}"
    t = _norm_tier(raw)
    if t:
        return t, "plan"
    print(f"[INFO] {why} → 解读档位回退**默认 {DEFAULT_TIER}**（{TIER_LABEL[DEFAULT_TIER]}）。"
          f"可用 --tier L1|L2|L3 指定，或先跑阶段 0 确认。", file=sys.stderr)
    return DEFAULT_TIER, "default"


def load_records() -> dict:
    """题录总表 → {doi: rec}（兼容 {doi:rec} 与 [rec,…] 两种形状）。"""
    for n in ("crossref_all.json", "master.json"):
        p = CDPDIR / n
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {str(k).lower(): v for k, v in d.items() if isinstance(v, dict)}
        if isinstance(d, list):
            return {str(r.get("doi", "")).lower(): r
                    for r in d if isinstance(r, dict) and r.get("doi")}
    return {}


def load_abstracts() -> dict:
    """摘要语料 → {doi: 摘要文本}。形状不确定，做多种容错。"""
    p = CDPDIR / "abstracts_openalex.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            k = str(k).lower()
            if isinstance(v, str):
                out[k] = v
            elif isinstance(v, dict):
                out[k] = (v.get("abstract") or v.get("abstractText")
                          or v.get("text") or "")
    return {k: v for k, v in out.items() if v}


def pdf_text(path: pathlib.Path, max_chars: int):
    """抽取 PDF 正文并在后端失败时回退 PyMuPDF。

    返回 `(packet, full, pages, truncated, omitted, status, backend, errors)`。
    """
    errors = []
    backend = "pypdf"
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path), strict=False)
        n = len(r.pages)
        buf = []
        for i, pg in enumerate(r.pages, 1):
            try:
                t = (pg.extract_text() or "").strip()
            except Exception as e:
                errors.append(f"pypdf:p{i}:{type(e).__name__}")
                t = ""
            if t:
                buf.append(f"\n\n===== [p.{i}] =====\n{t}")
        full = "".join(buf)
    except Exception as e:
        errors.append(f"pypdf:{type(e).__name__}:{str(e)[:80]}")
        n, full = 0, ""

    # 扫描件、字体映射损坏或 pypdf 空结果：使用 PyMuPDF 重新提取。
    if not full.strip() or full.count("") > max(20, len(full) // 100):
        try:
            import fitz
            doc = fitz.open(str(path))
            n = len(doc)
            pages = []
            for i, page in enumerate(doc, 1):
                try:
                    t = (page.get_text("text") or "").strip()
                except Exception as e:
                    errors.append(f"fitz:p{i}:{type(e).__name__}")
                    t = ""
                if t:
                    pages.append(f"\n\n===== [p.{i}] =====\n{t}")
            candidate = "".join(pages)
            if len(candidate) > len(full):
                full, backend = candidate, "pymupdf"
        except Exception as e:
            errors.append(f"fitz:{type(e).__name__}:{str(e)[:80]}")
    status = "ok" if full.strip() else ("failed" if errors else "empty")
    if len(full) <= max_chars:
        return full, full, n, False, 0, status, backend, errors
    text, omitted = head_tail(full, max_chars)
    return text, full, n, True, omitted, status, backend, errors


def head_tail(full: str, max_chars: int, sidecar_note: str = ""):
    """超出上限时**保留开头与结尾**，中间以省略标记衔接。返回 (text, omitted)。

    为什么不能只截开头：论文最关键的内容 —— **结论、量化数据、
      作者自述局限与展望** —— 全部落在结尾的 Results / Discussion /
      Conclusion。旧版从头部硬截，恰好把最该读的部分丢掉。
      实测 2026-09-18：13 篇里 5 篇被截断，解读卡只能对前 6 万字符负责，
      而它们的结论段根本没进入任务包。
    为什么不干脆放大上限：任务包要整体塞进 subagent 的上下文，
      无限放大会挤掉其它内容、拉低解读质量。头 60% + 尾 40% 是
      「既读到主旨、也读到结论」的折中；被略去的中间段多为方法与
      中间结果，且完整文本另有 sidecar 可查（见下）。
    """
    head_n = int(max_chars * 0.6)
    tail_n = max(0, max_chars - head_n)
    omitted = max(0, len(full) - head_n - tail_n)
    marker = ("\n\n===== [中间省略 {n} 字符{src}；"
              "如需该部分请读取任务包中的 fulltext_path 指向的完整文本] =====\n\n").format(
        n=omitted, src=("；完整文本：" + sidecar_note) if sidecar_note else "")
    return full[:head_n] + marker + (full[-tail_n:] if tail_n else ""), omitted


def gbt_authors(authors) -> str:
    """GB/T 7714：>3 位取前 3 位 + et al."""
    names = []
    for a in authors or []:
        if isinstance(a, dict):
            n = a.get("name") or ", ".join(
                x for x in (a.get("family"), a.get("given")) if x)
        else:
            n = str(a)
        if n:
            names.append(n)
    if not names:
        return ""
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + ", et al."


def load_captured_fulltext(doi: str, title: str) -> str:
    """回退读取阶段 E 已落盘的落地页正文。

    detect.py 在下载 PDF 的同时就把 `document.body.innerText` 落到了
    `.work_cdp/fulltext/<年>_<slug>.txt`（零额外请求）。但 PDF 端点失败的篇目
    （限流 / not-pdf-body）在旧版会**整篇降级为「仅摘要」**，
    尽管那份正文其实已经在磁盘上。

    返回正文（已剥掉文件头部的 `# source:` 元信息行），无则返回空串。
    """
    if not CAPTURED_FULLTEXT.exists():
        return ""
    stem = store.doi_slug(doi) if doi else store.doi_slug(title)[:60]
    if not stem:
        return ""
    hits = sorted(CAPTURED_FULLTEXT.glob(f"*{stem}*.txt"))
    if not hits:
        return ""
    try:
        raw = hits[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # 去掉 detect.py 写的 `# key: value` 头与分隔线
    body = "\n".join(l for l in raw.splitlines()
                     if not l.startswith("# ") and set(l.strip()) != {"-"})
    return body.strip()


def my_position(authors, tokens) -> str:
    if not authors or not tokens:
        return ""
    names = []
    for a in authors or []:
        if isinstance(a, dict):
            names.append((a.get("name") or "").lower())
        else:
            names.append(str(a).lower())
    hits = [i for i, n in enumerate(names) if any(t in n for t in tokens)]
    if not hits:
        return ""
    i, n = hits[0], len(names)
    if i == 0:
        return "第一作者"
    if i == n - 1:
        return "末位作者（通常为通讯作者）"
    return f"第{i + 1}位作者"


def paper_card_block(stem: str, tier: str) -> dict:
    """paper-card 引擎下发给 subagent 的**自包含**路径与产出契约。

    为什么要把路径算好塞进任务包：subagent 不该自己去猜
    `vendor/nature-paper-card/...` 在哪。路径解析只有一个权威 ——
    `nature_bridge.py`（本脚本与它同目录，直接 import，不复制逻辑）。
    """
    try:
        import nature_bridge
        p = nature_bridge.paths()
    except Exception as e:                                   # pragma: no cover
        print(f"[WARN] 无法加载 nature_bridge（{type(e).__name__}: {e}）；"
              f"paper-card 引擎将缺少脚本路径，subagent 需自行定位。",
              file=sys.stderr)
        p = {}

    card = PCARDDIR / f"{stem}.md"
    return {
        "engine": "paper-card",
        "tier": tier,
        # 已并入的**单个**技能（流程内，不是外部依赖）
        "skill_dir": p.get("paper_card_root"),
        "skill_md": (str(pathlib.Path(p["paper_card_root"]) / "SKILL.md")
                     if p.get("paper_card_root") else None),
        "card_schema": p.get("card_schema"),
        "prepare_script": p.get("prepare_paper"),
        "audit_script": p.get("audit_paper_card"),
        # 本流程的适配层：路径解析 + 审计 + 内容级校验，三合一的入口
        "bridge_script": str(_HERE / "nature_bridge.py"),
        # 本流程内的解读写作模块（内容级校验器所在，含档位规范）
        "interpretation_skill": p.get("interpretation_skill"),
        "content_check_script": p.get("check_interpretation"),
        # 产出（agent 必须两样都写，缺一不可）
        "card_path": str(card),
        "source_bundle_path": str(PCARDDIR / f"{stem}.source_bundle.json"),
        "audit_report_path": str(PCARDDIR / f"{stem}.audit.json"),
        "rendered_pages_dir": str(PCARDDIR / f"{stem}.pages"),
        "locator_modes": ["page-grounded", "structure-grounded", "source-limited"],
        # 质量门写成**可直接复制执行**的一条命令（路径已解析好）。
        #   不写 "<bridge_script>" 这类占位符：占位符等于把拼路径的活推给
        #   subagent，而它拼错的后果是「审计没跑」却不报错。
        "quality_gate": (
            f'python "{_HERE / "nature_bridge.py"}" check '
            f'--card "{card}" --bundle "{PCARDDIR / f"{stem}.source_bundle.json"}" '
            f'--locator-mode <实际模式> '
            f'--report "{PCARDDIR / f"{stem}.audit.json"}"；'
            "退出码非 0 即为 error（结构不合规 / 数字在原文中找不到），"
            "必须修复后重跑；无 --bundle 时（source-limited）改用 --source 指向原文摘录。"
        ),
        "dual_output": (
            "本引擎要求**两样都产出**："
            "① <card_path>：16 节 Paper Card（人读的深读本体）；"
            "② result_path 指向的那份 11 字段 JSON 解读卡（机器读，"
            "下游 interpret_build.py / ppt_brief.py / 知识图谱只认它）。"
            "两者必须口径一致：JSON 卡里的数字与结论不得与 Paper Card 冲突。"
        ),
    }


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0

    max_chars = int(_arg("max-chars", "60000") or 60000)
    only = [d.strip().lower() for d in (_arg("only", "") or "").split(",") if d.strip()]
    no_text = "--no-text" in sys.argv
    limit = int(_arg("limit", "0") or 0)

    # 档位在**任何文件检查之前**解析：它只影响任务包内容，不该被题录缺失拦住。
    tier, tier_src = resolve_tier(_tier_arg())
    if tier_src == "invalid-cli":
        print(f"[X] --tier 取值非法：{_tier_arg()!r}；可用：{'、'.join(TIER_VALUES)}"
              f"（L1 精简 / L2 详细 / L3 完整）", file=sys.stderr)
        return 2

    # 引擎同理，也提前解析：它是**参数**，不该被题录缺失拦住。
    engine, engine_src = resolve_engine(_engine_arg())
    if engine_src == "invalid-cli":
        print(f"[X] --engine 取值非法：{_engine_arg()!r}；可用："
              f"{'、'.join(ENGINE_VALUES)}（legacy = 原有解读卡；"
              f"paper-card = 额外产出 16 节 Paper Card）", file=sys.stderr)
        return 2
    if engine == "paper-card":
        # 引擎可用性**前置检查**：路径都不通就该在组包前报错，
        #   而不是等 subagent 跑到一半才发现没有脚本可调。
        _pc = paper_card_block("__probe__", tier)
        _missing = [k for k in ("skill_md", "prepare_script", "audit_script",
                                "bridge_script", "content_check_script")
                    if not _pc.get(k)]
        if _missing:
            print(f"[X] --engine=paper-card 不可用：解析不到 {', '.join(_missing)}。"
                  f"请确认 流程1/vendor/nature-paper-card 与 "
                  f"流程1/skills/paper-interpretation-writing 均已就位，"
                  f"或改用 --engine=legacy。", file=sys.stderr)
            return 2

    if not (CDPDIR / "crossref_all.json").exists() and not (CDPDIR / "master.json").exists():
        print("[X] 找不到题录总表 .work_cdp/crossref_all.json")
        print("  请先运行 fetch_crossref_all.py。")
        return 2

    recs = load_records()
    abstracts = load_abstracts()
    exports = store.exports_dir(WD)

    # 同名他人：与 build_list.py 共用 scope.py 的**肯定性判定**排除名单
    exc = scope.excluded_dois(CDPDIR, total=len(recs)) or set()
    if exc:
        recs = {d: r for d, r in recs.items() if d not in exc}
        print(f"[INFO] 已排除同名他人论文 {len(exc)} 条（见 disambiguated.json）")

    tokens = [t.strip().lower() for t in
              os.environ.get("PAPER_NAME_TOKENS", "").split("|") if t.strip()]
    if not tokens:
        sn = os.environ.get("PAPER_SURNAME", "").strip().lower()
        gn = os.environ.get("PAPER_GIVEN", "").strip().lower()
        if sn and gn:
            tokens = [f"{sn}, {gn}", f"{gn} {sn}"]

    # 缺年份要先报警：题录一旦缺 year，会排到 9999 而改变整体顺序。
    #   实测 2026-09-18：一次 Crossref 重采有 2 条失败未补，year 为空 →
    #   顺序整体错位 → 旧版「序号_DOI」命名下 13 份解读卡有 9 份对不上号。
    #   （文件名改成 DOI 命名后已不会错位，但缺年份本身仍是题录缺陷，需修。）
    _noyear = [d for d, r in recs.items() if not r.get("year")]
    if _noyear:
        print(f"[WARN] {len(_noyear)} 条题录**缺发表年份**，会影响排序与阶段分组。"
              f"建议先跑 retry_crossref.py 补全：{', '.join(_noyear[:3])}"
              f"{' …' if len(_noyear) > 3 else ''}")

    # 按发表年份升序 —— 解读报告要求「依照发表先后顺序串联要点」
    items = sorted(recs.items(), key=lambda kv: (
        int(kv[1].get("year") or 9999), str(kv[1].get("title") or "")))
    if only:
        items = [(d, r) for d, r in items if d in set(only)]
    if limit > 0:
        items = items[:limit]

    TASKDIR.mkdir(parents=True, exist_ok=True)
    RESDIR.mkdir(parents=True, exist_ok=True)

    # 受控标签词表（子流程手册 §6.5）：优先工作目录，缺省回退内置模板
    def _read_vocab(p: pathlib.Path) -> dict:
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return {}

    vocab = _read_vocab(TAG_VOCAB_PATH) or _read_vocab(TAG_VOCAB_TPL) or {}
    vocab_block = {
        "direction_tags_vocabulary": vocab.get("direction_tags_vocabulary") or [],
        "paradigm_tags_vocabulary": vocab.get("paradigm_tags_vocabulary") or [],
    }
    if vocab_block["direction_tags_vocabulary"]:
        print(f"[OK] 标签词表已嵌入：方向 {len(vocab_block['direction_tags_vocabulary'])} 词"
              f" / 范式 {len(vocab_block['paradigm_tags_vocabulary'])} 词")
    else:
        print("[WARN] 未找到标签词表 → 任务包不含 tag_vocabulary，"
              "subagent 自造标签将导致汇总时「标准漂移」（见子流程手册 §6.5）")

    manifest, n_full, n_abs, seq = [], 0, 0, 0
    for doi, rec in items:
        seq += 1
        year = rec.get("year") or ""
        title = rec.get("title") or ""
        # 文件名**只由 DOI 决定**（稳定、与排序无关）；序号只写在 JSON 内部。
        #   旧版用「序号_DOI」命名，一旦排序发生变化 —— 例如某条 Crossref 记录
        #   暂时缺年份而被排到末尾 —— 同一个文件名就会指向**另一篇论文**。
        #   实测 2026-09-18：重跑一次 prepare 后，13 份已产出的解读卡里有 9 份
        #   与 manifest 对不上号；轻则整片报「缺卡」，重则**把 A 的解读卡
        #   当成 B 的结果**（build 是按文件名取卡的）。改用 DOI 命名后，
        #   重跑只会覆盖同名文件，永远不会错位。
        stem = store.doi_slug(doi) if doi else store.doi_slug(title)[:60]
        taskp = TASKDIR / f"{stem}.json"
        resp = RESDIR / f"{stem}.json"

        text, full, pages, trunc, omitted = "", "", 0, False, 0
        extraction_status, extraction_backend, extraction_errors = "not-run", "", []
        pdfname, fullpath = "", ""
        figure_dir, figures_manifest = "", []
        if not no_text:
            p = store.existing_pdf(doi, year, WD, title)
            if p is not None:
                pdfname = p.name
                (text, full, pages, trunc, omitted, extraction_status,
                 extraction_backend, extraction_errors) = pdf_text(p, max_chars)
                # 将候选数据图落盘为真实文件，任务包只携带元数据，避免 base64 膨胀。
                try:
                    target = FIGDIR / stem
                    figures_manifest = extract_figures.save_records(
                        extract_figures.extract(p), target)
                    figure_dir = str(target) if figures_manifest else ""
                except Exception as e:
                    print(f"[WARN] 图片抽取失败 {doi}: {str(e)[:100]}")
                if trunc:
                    # 完整文本另存 sidecar：任务包只带「头 + 尾」，
                    #   被略去的中间段仍可查，subagent 必要时分段读取。
                    FULLDIR.mkdir(parents=True, exist_ok=True)
                    fp = FULLDIR / f"{stem}.txt"
                    fp.write_text(full, encoding="utf-8")
                    fullpath = str(fp)

            # ── 回退：PDF 取不到时，用阶段 E 已落盘的落地页正文 ──
            #   只在前述 PDF 路径**没拿到正文**时启用，绝不覆盖 PDF 提取结果
            #   （PDF 才是权威全文；落地页正文可能与排版有差异，故标注来源）。
            if not text.strip():
                cap = load_captured_fulltext(doi, title)
                if len(cap) >= 1500:
                    text = cap[:max_chars]
                    full = cap
                    trunc = len(cap) > max_chars
                    extraction_status = "ok"
                    extraction_backend = "landing-page-text"
                    if trunc:
                        FULLDIR.mkdir(parents=True, exist_ok=True)
                        fp = FULLDIR / f"{stem}.txt"
                        fp.write_text(full, encoding="utf-8")
                        fullpath = str(fp)

        abstract = abstracts.get(doi, "") or rec.get("abstract") or ""
        src = "fulltext" if text.strip() else "abstract"
        if src == "fulltext":
            n_full += 1
        else:
            n_abs += 1

        # ── 正文**始终**另存为纯文本 sidecar ─────────────────────────────
        #   为什么必须无条件写：任务包里的 `text` 是 **JSON 字符串**，
        #   整篇正文落在**单行**上（实测 4 万字符/行）。而 read 类工具对
        #   **超长单行**会截断显示，subagent 因此只能看到开头 ~2000 字符，
        #   既读不到结论也读不到局限 —— 实测 8 个 subagent 全部因此失败。
        #   sidecar 是**纯文本且保留真实换行**，可正常逐行读取。
        #   故：只要拿得到正文，就写 sidecar 并回填 fulltext_path。
        if text.strip():
            try:
                FULLDIR.mkdir(parents=True, exist_ok=True)
                fp = FULLDIR / f"{stem}.txt"
                fp.write_text(full if len(full) >= len(text) else text,
                              encoding="utf-8")
                fullpath = str(fp)
            except OSError as e:
                print(f"[WARN] sidecar 写入失败 {doi}: {str(e)[:80]}")

        packet = {
            "seq": seq,
            # 档位随任务包**一起下发**：subagent 不必再去问阶段 0，
            #   下游 interpret_build.py 也据此选渲染档位（见其 resolve_tier）。
            "interpret_tier": tier,
            # 解读引擎同样随包下发（v17）：legacy 只出 11 字段卡；
            #   paper-card 额外产出 16 节 Paper Card，并把审计/内容级校验
            #   的路径与质量门一并交给 subagent（见 paper_card 块）。
            "interpret_engine": engine,
            "doi": doi,
            "title": title,
            "journal": rec.get("journal") or rec.get("short_journal") or "",
            "year": year,
            "authors": gbt_authors(rec.get("authors")),
            "my_position": my_position(rec.get("authors"), tokens),
            "abstract": abstract,
            "pdf": pdfname,
            "pages": pages,
            "evidence_source": src,
            "text_extraction_status": extraction_status,
            "text_extraction_backend": extraction_backend,
            "text_extraction_errors": extraction_errors,
            "text_chars": len(text),
            "text_truncated": trunc,
            "text_omitted_chars": omitted,
            "fulltext_chars": len(full),
            # 完整文本路径（仅在被截断时非空）；任务包已含头+尾，此处供按需深读
            "fulltext_path": fullpath,
            "figure_dir": figure_dir,
            "figures_manifest": figures_manifest,
            "figure_count": len(figures_manifest),
            "text": text,
            # 受控标签词表（§6.5）：direction_tags / paradigm_tags 必须从这里取词，
            #   确无合适词才自造并加 `自定义:` 前缀 —— 否则并行 subagent 各造各的，
            #   汇总时同一概念被拆成多条弱信号（machine-learning / ML / 机器学习）。
            "tag_vocabulary": vocab_block,
            # 供 subagent 直接写结果用（绝对路径，避免它自己拼错）
            "result_path": str(resp),
        }
        if engine == "paper-card":
            # 只在启用时下发：legacy 任务包保持**逐字节可比**，
            #   历史卡与历史提示词不受影响。
            PCARDDIR.mkdir(parents=True, exist_ok=True)
            packet["paper_card"] = paper_card_block(stem, tier)
        taskp.write_text(json.dumps(packet, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest.append({
            "seq": seq, "doi": doi, "year": year, "title": title[:90],
            # v7：补 slug 字段，与 refs_prepare.py 的 manifest 对齐。
            #   下游（自检、人工核对、跨阶段脚本）一律按**文件名 stem** 定位产物，
            #   而不是去解析 task/result 的完整路径 —— 路径会随工作目录变化，stem 不会。
            "slug": stem,
            "task": str(taskp), "result": str(resp),
            "evidence_source": src,
            "text_extraction_status": extraction_status,
            "text_extraction_backend": extraction_backend,
            "text_extraction_errors": extraction_errors,
            "text_chars": len(text),          # 任务包内的**正文**长度（不含摘要）
            "abstract_chars": len(abstract),  # 摘要长度
            "text_truncated": trunc, "text_omitted_chars": omitted,
            "fulltext_chars": len(full), "fulltext_path": fullpath,
            "pages": pages,
            "figure_dir": figure_dir,
            "figures_manifest": figures_manifest,
            "figure_count": len(figures_manifest),
            # 受控标签词表：subagent 的 direction_tags / paradigm_tags
            #   必须从这里取词（§6.5）；确无合适词才自造并加 `自定义:` 前缀。
            "tag_vocabulary": vocab_block,
            # 引擎随清单一起记：任务包被清理后仍能看出本轮用的是哪种形态。
            "interpret_engine": engine,
        })

    # ── 陈旧文件检查：tasks/ 与 results/ 里不属于本轮的文件 ──────────
    #   不静默忽略。尤其 results/ 里的「孤儿卡」意味着上一轮的解读成果
    #     与当前题录对不上 —— 必须让人看见，而不是让它悄悄错位。
    cur_tasks = {pathlib.Path(m["task"]).name for m in manifest}
    cur_results = {pathlib.Path(m["result"]).name for m in manifest}
    stale_tasks = sorted(p.name for p in TASKDIR.glob("*.json")
                         if p.name not in cur_tasks)
    orphan_results = sorted(p.name for p in RESDIR.glob("*.json")
                            if p.name not in cur_results)

    (OUTDIR / "manifest.json").write_text(
        json.dumps({"workdir": str(WD), "exports": str(exports),
                    "max_chars": max_chars, "count": len(manifest),
                    # 档位写进清单顶层：任务包被清理或换版时，
                    #   汇总脚本仍能从这里读到本轮用的档位（回退链的第二级）。
                    "interpret_tier": tier,
                    "interpret_engine": engine,
                    "fulltext": n_full, "abstract_only": n_abs,
                    "tasks": manifest}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    n_trunc = sum(1 for m in manifest if m["text_truncated"])
    # 「无米下锅」= 任务包里**既没有正文、也没有摘要**。
    #   注意不能只看 text_chars：那是**正文**长度，没有全文的篇目天然为 0，
    #   但它可能带着完整的摘要 —— 只判 text_chars 会误报一片「摘要也为空」。
    n_empty = sum(1 for m in manifest
                  if not m["text_chars"] and not m["abstract_chars"])
    print(f"\n已生成 {len(manifest)} 个任务包 → {TASKDIR}")
    print(f"   解读档位：{tier}（{TIER_LABEL[tier]}）")
    if tier_src == "cli":
        print("   （来源：命令行 --tier 覆盖了阶段 0 的选择）")
    elif tier_src == "default":
        print(f"   （来源：默认值 —— 未读到阶段 0 的档位；"
              f"建议在阶段 0 用 `--set interpret_tier={tier}` 确认）")
    print(f"   解读引擎：{engine}（{ENGINE_LABEL[engine]}）")
    if engine_src == "cli":
        print("   （来源：命令行 --engine 覆盖了阶段 0 的选择）")
    elif engine_src == "default":
        print("   （来源：默认值 —— 未读到阶段 0 的引擎选择；"
              "要 16 节深读卡请在阶段 0 用 "
              "`--set interpret_engine=paper-card` 确认）")
    if engine == "paper-card":
        print(f"   [INFO] paper-card 引擎：subagent 需**额外**产出 16 节卡片 → {PCARDDIR}")
        print("          并在交付前跑 nature_bridge.py check（审计 + 数字回查原文）；"
              "提示词模板见 流程1/subagent-driven-review/SKILL.md § 7.3")
        print("          该引擎**只增不减**：11 字段 JSON 卡照旧产出，下游不受影响。")
    print(f"   全文级 {n_full} 篇 ｜ 仅摘要 {n_abs} 篇")
    if n_trunc:
        print(f"   [INFO] {n_trunc} 篇正文超长：任务包**保留开头 60% + 结尾 40%**"
              f"（结论/局限都在结尾，不再被截掉），中间省略部分另存 → {FULLDIR}")
    if n_abs:
        print("   [WARN] 仅摘要的篇目在最终报告中会被如实标注；"
              "如需全文请回主流程 §4 阶段 E 用 CDP 补下。")
    if n_empty:
        print(f"   [WARN] {n_empty} 篇**摘要也为空**（既无全文、又无摘要语料）→ "
              f"subagent 将无米下锅。建议先跑 fetch_openalex_abstracts.py 补摘要。")
    if stale_tasks:
        print(f"   [INFO] 清理陈旧任务包 {len(stale_tasks)} 个（不属于本轮题录）："
              f"{', '.join(stale_tasks[:4])}{' …' if len(stale_tasks) > 4 else ''}")
        for n in stale_tasks:
            try:
                (TASKDIR / n).unlink()
            except Exception:
                pass
    if orphan_results:
        print(f"   [WARN] results/ 里有 {len(orphan_results)} 份**孤儿解读卡**"
              f"（文件名与本轮任务对不上，不会被汇总采用）："
              f"{', '.join(orphan_results[:4])}{' …' if len(orphan_results) > 4 else ''}")
        print("      多为上一轮命名规则不同所致。若内容仍有效，"
              "按 DOI 改名即可复用；否则删除后重新分派。")
    print(f"   清单：{OUTDIR / 'manifest.json'}")
    print("\n下一步：同一条消息内并行分派 4–6 个 subagent，"
          "每个只读自己的 task.json、只写自己的 result.json")
    print("（提示词模板见 流程1/subagent-driven-review/SKILL.md § 7）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
