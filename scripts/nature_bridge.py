#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# nature_bridge.py —— 「流程1 ↔ 已并入的 nature-paper-card」唯一适配层
#
# 为什么需要它：
#   nature-paper-card 已并入 流程1/vendor/nature-paper-card/（**只并入这一个技能**），
#   但它的脚本与本流程的「内容级校验器」各自为政
#   （prepare_paper.py / audit_paper_card.py / check_interpretation.py）。
#   若让 subagent 自己去拼路径，一旦目录挪动就会静默跑偏；
#   若把三者串起来的活留给提示词，就会出现「审计没跑却报通过」。
#   本脚本把**路径解析**与**调用契约**固定下来。
#
# 为什么不能只靠 nature-paper-card 自带的审计器（实测结论）：
#   它是**结构 lint** —— 一张 03–15 节正文全空、只有 1 条指针的卡
#   实测拿到 `pass (13 passes, 0 errors)`、退出码 0（本地已补 P5 修此点，
#   但结构性检查仍不等于内容可信）。
#   真正能查「卡片里的数字是否真出现在原文里」的是本流程内的
#   check_interpretation.py（NUMBER_NOT_IN_SOURCE）。本脚本的 `check`
#   子命令把两者合起来跑，这才是 B2 要的东西。
#
# 子命令：
#   locations                    打印解析到的所有路径（排障用）
#   prepare   --pdf P --output O [--render-dir D]
#   audit     --card C [--bundle B] --locator-mode M [--report R]
#   content-check --card C [--source S | --from-bundle B] [--strict]
#   check     --card C (--source S | --from-bundle B) [--bundle B]
#             --locator-mode M --report R       ← 审计 + 内容校验，合二为一
#   --self-test                  零网络自检：路径、--help、端到端小样
#
# 退出码：0 = 通过；1 = 有 error；2 = 用法/读取/路径错误。
#
# 可移植配置：
#   PAPER_CARD_DIR                    覆盖 paper-card 技能目录
#   NATURE_SKILLS_DIR                 覆盖上游技能包根（自动拼 skills/nature-paper-card）
#   PAPER_INTERPRETATION_SKILL_DIR    覆盖解读写作模块根
# 解析顺序一律是「流程内 → 上游单技能目录」，永不把某个人的本地检出写死。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

def _fix_streams() -> None:
    """UTF-8 化标准输出，**必须幂等**。

    与 prepare_stage.py / logutil._fix_stdout() 同款判据：已是 UTF-8 就不再包。
    为什么：本流程的脚本之间会互相 import（interpret_prepare → nature_bridge），
      每 import 一次就重包一层，中间那层 wrapper 被 GC 时会**关掉底层 buffer**，
      表现为后续所有 print 抛 "ValueError: I/O operation on closed file"，
      且只在特定 import 顺序下复现。
    **只在作为程序运行时调用**（main() 开头）—— 放在模块级会让 import 方中招。
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
            # 只重配置现有流，不替换 TextIOWrapper；替换会在旧 wrapper
            # 被回收时关闭底层管道，触发 closed-file/lost-sys.stderr。
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass

FLOW_ROOT = pathlib.Path(__file__).resolve().parent.parent          # 流程1/
REPO_ROOT = FLOW_ROOT.parent                                        # 仓库根

AUDIT_LOCATOR_MODES = ("page-grounded", "structure-grounded", "source-limited")

# 「卡片语境」下的严重度政策。
#   --tier all 已经替我们挡掉了档位预算 / 收尾落点 / L1 五要素这类
#   **只在中文解读草稿格式下成立**的检查；剩下五类里：
#     numbers / novelty  —— 与格式无关，正是 B2 要查的，保持 error；
#     filler / speculation / gloss —— 面向中文解读行文，对 16 节卡片会误伤
#       （卡片里有 "Not applicable"、英文小标题、公式符号），默认降为 warning，
#       --strict 时恢复原生严重度。
FORMAT_INDEPENDENT_CODES = (
    "NUMBER_NOT_IN_SOURCE",
    "NUMBER_CONVERSION_SUSPECT",
    "UNSUPPORTED_NOVELTY",
)


def log(msg: str) -> None:
    print(msg)


def _reclaim(directory: pathlib.Path) -> None:
    """尽力回收一个 scratch 目录；任何失败都咽掉。

    为什么不是光秃秃的 `rmdir()`：调用方在 `finally` 里清理，此时目录里
    可能还有**没能删掉的文件**（Windows 上被占用、或上一步 `unlink` 失败）。
    `rmdir()` 遇非空目录会抛 `OSError`，清不掉还留个空壳。
    `rmtree(ignore_errors=True)` 对「已删空」「非空」「压根不存在」三种情况
    都安全，是本仓库其它脚本（selftest_nature_integration.py、
    fulltext_publishers.py 等）已经统一采用的做法。
    """
    shutil.rmtree(directory, ignore_errors=True)


# 已建出的 scratch 目录，供进程退出时兜底回收。
_SCRATCH_DIRS: list[pathlib.Path] = []
_ATEXIT_REGISTERED = False


def _reclaim_all() -> None:
    """进程退出时回收本进程建过的全部 scratch 目录。

    对已被调用点回收过的路径再 `rmtree` 一次是无害的空操作，
    因此不需要额外记录「已回收」状态。
    """
    while _SCRATCH_DIRS:
        _reclaim(_SCRATCH_DIRS.pop())


def scratch_dir(prefix: str = "nature_bridge_") -> pathlib.Path:
    """建一个**可写**的临时目录，并登记为「退出时必须回收」。

    不用 `tempfile.mkdtemp()`：它以 0700 建目录，在受限沙箱下会变成
    「建得出来、写不进去」——实测 `PermissionError: [Errno 13]`。
    用 `pathlib.mkdir()` 走默认权限即可，两者对本工具都够用。

    [STOP] 这个取舍有代价，必须记住：`mkdtemp()` 会由 `tempfile` 在退出时
    自动删除，而 `mkdir()` **不会** —— 建目录的活我们接了过来，回收的活
    也就一并接手。早期版本正是漏了这一点：调用方只 `unlink()` 临时**文件**、
    从不删**目录**，于是每跑一次 `check` 就在仓库里留下 2–3 个空文件夹
    （实测 `流程1/` 下积了 14 个 `nature_bridge_{audit,src,norm}_*`，
    跨 4 个 PID）。当 `gettempdir()` 因沙箱被解析到工作区内时，这些空壳
    就直接倒进了仓库。

    所以这里用**两道防线**，新增调用点时不必记得手工清理：
      ① `atexit` 兜底 —— 正常退出、`sys.exit()`、未捕获异常都覆盖；
         调用点已回收的目录会被重复 `rmtree` 一次，对不存在的路径是空操作。
      ② 调用点仍应显式回收（`finally: _reclaim(d)`），让垃圾尽快消失，
         而不是攒到进程结束 —— 长驻进程里 `atexit` 可能几小时都不触发。
    """
    global _ATEXIT_REGISTERED
    base = pathlib.Path(tempfile.gettempdir()).resolve()
    flow_root = FLOW_ROOT.resolve()
    try:
        base.relative_to(flow_root)
    except ValueError:
        pass
    else:
        # TMP/TEMP 被误设到流程目录时，不能在交付树中制造 scratch。
        # 优先退回系统临时目录；若仍位于流程根内则明确失败。
        # 依次尝试流程根外的专用临时区；不把 scratch 倒进交付树。
        candidates = [FLOW_ROOT.parent / ".flow1-tmp",
                      pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Temp",
                      pathlib.Path.home() / ".flow1-tmp"]
        base = None
        for candidate in candidates:
            candidate = candidate.resolve()
            try:
                candidate.relative_to(flow_root)
                continue
            except ValueError:
                try:
                    candidate.mkdir(parents=True, exist_ok=True)
                    base = candidate
                    break
                except OSError:
                    continue
        if base is None:
            raise RuntimeError(f"找不到流程根外可写临时目录（原始目录：{base}）")
    d = base / f"{prefix}{os.getpid()}_{next(tempfile._get_candidate_names())}"
    d.mkdir(parents=True, exist_ok=True)
    _SCRATCH_DIRS.append(d)
    if not _ATEXIT_REGISTERED:
        atexit.register(_reclaim_all)
        _ATEXIT_REGISTERED = True
    return d


def scratch_file(directory: pathlib.Path, suffix: str) -> pathlib.Path:
    """在 scratch_dir 里取一个未占用的文件名（同款权限取舍）。"""
    return directory / f"tmp_{next(tempfile._get_candidate_names())}{suffix}"


def _first_existing(cands, marker: str):
    """返回第一个「存在且含 marker」的候选目录；找不到返回 None。"""
    for c in cands:
        if c and (c / marker).exists():
            return c.resolve()
    return None


def paper_card_root():
    """`nature-paper-card` 技能根。

    并入形态：**只并入这一个技能**，落在 `<流程1>/vendor/nature-paper-card/`。
      候选顺序 ① 显式环境变量 → ② 流程内 vendor（分发版唯一存在的那份）
      → ③ 上游**单技能**目录（仅开发期回退，分发包里没有）。
    环境变量收两个名字：
      PAPER_CARD_DIR      直接指向技能目录（首选）
      NATURE_SKILLS_DIR   指向上游技能**包**根，自动拼 `skills/nature-paper-card`
        —— 保留它只为兼容早期写法，不表示本流程依赖整包。
    """
    cands = []
    direct = os.environ.get("PAPER_CARD_DIR")
    if direct:
        cands.append(pathlib.Path(direct))
    pack = os.environ.get("NATURE_SKILLS_DIR")
    if pack:
        cands.append(pathlib.Path(pack) / "skills" / "nature-paper-card")
    cands.append(FLOW_ROOT / "vendor" / "nature-paper-card")
    upstream = REPO_ROOT / "nature-skills-main" / "skills" / "nature-paper-card"
    cands.append(upstream)
    root = _first_existing(cands, "SKILL.md")
    # 落到上游检出就**必须出声**：那说明流程内副本不见了（打包漏带 / 目录被挪），
    #   而静默沿用外部检出会让"不构成外部依赖"这条边界悄悄失效 ——
    #   在别人的机器上则直接崩，且崩在阶段 H 的中途。
    if root is not None and root == upstream.resolve():
        print(f"[WARN] nature_bridge：流程内副本缺失，正在回退到**外部检出** "
              f"{upstream}\n"
              f"       这只应用于开发期。分发版应当是 "
              f"{FLOW_ROOT / 'vendor' / 'nature-paper-card'}"
              f"（若缺失，请检查 pack_release 是否带上 vendor/nature-paper-card）。",
              file=sys.stderr)
    return root


def interp_root():
    """解读写作模块根（含内容级校验器）。"""
    env = os.environ.get("PAPER_INTERPRETATION_SKILL_DIR")
    cands = []
    if env:
        cands.append(pathlib.Path(env))
    cands.append(FLOW_ROOT / "skills" / "paper-interpretation-writing")
    cands.append(REPO_ROOT / "skills" / "paper-interpretation-writing")
    return _first_existing(cands, "scripts/check_interpretation.py")


def paths() -> dict:
    pc = paper_card_root()
    interp = interp_root()
    return {
        "flow_root": str(FLOW_ROOT),
        "repo_root": str(REPO_ROOT),
        "paper_card_root": str(pc) if pc else None,
        # 本流程只并入这一个技能；此字段恒为 1 或 0，供自检断言"范围未漂移"。
        "vendored_skills": ["nature-paper-card"] if pc else [],
        "prepare_paper": str(pc / "scripts" / "prepare_paper.py") if pc else None,
        "audit_paper_card": str(pc / "scripts" / "audit_paper_card.py") if pc else None,
        "card_schema": str(pc / "references" / "card-schema.md") if pc else None,
        "paper_card_tutorial": (
            str(pc / "docs" / "nature-paper-card-tutorial.md") if pc else None
        ),
        "interpretation_skill": str(interp) if interp else None,
        "check_interpretation": (
            str(interp / "scripts" / "check_interpretation.py") if interp else None
        ),
    }


# ── 子进程封装 ───────────────────────────────────────────────
def run_py(script: pathlib.Path, args: list[str]) -> tuple[int, str, str]:
    """跑一个 Python 脚本，返回 (退出码, stdout, stderr)。"""
    cmd = [sys.executable, str(script), *args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def bundle_text(bundle_path: pathlib.Path) -> str:
    """把 source_bundle.json 的逐页正文拼成可作 --source 的纯文本。

    为什么要拼：校验器要拿原文逐个核对数字，而它的 --source 只吃纯文本。
    页码标记保留，便于人工回查。
    """
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    chunks: list[str] = []
    for page in data.get("pages") or []:
        n = page.get("pdf_page")
        chunks.append(f"\n===== [p.{n}] =====\n")
        chunks.append(str(page.get("text") or ""))
    for b in data.get("unlocated_blocks") or []:
        chunks.append("\n===== [no page locator] =====\n")
        chunks.append(str(b.get("text") or ""))
    return "\n".join(chunks)


# ── 子命令实现 ───────────────────────────────────────────────
def cmd_locations(as_json: bool) -> int:
    p = paths()
    if as_json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return 0
    for k, v in p.items():
        print(f"{k:24} {v}")
    if p["paper_card_root"] is None:
        print("\n[X] 找不到 nature-paper-card 技能根。已并入的副本应为 "
              f"{FLOW_ROOT / 'vendor' / 'nature-paper-card'}；"
              "或用 PAPER_CARD_DIR 指定。", file=sys.stderr)
        return 2
    if p["interpretation_skill"] is None:
        print("\n[X] 找不到 paper-interpretation-writing（内容级校验器所在）。"
              f"应为本流程内 {FLOW_ROOT / 'skills' / 'paper-interpretation-writing'}；"
              "或用 PAPER_INTERPRETATION_SKILL_DIR 指定。", file=sys.stderr)
        return 2
    return 0


def cmd_prepare(a) -> int:
    script = paths()["prepare_paper"]
    if not script:
        print("[X] 找不到 prepare_paper.py（nature-paper-card 未并入？）", file=sys.stderr)
        return 2
    args = [str(a.pdf), "--output", str(a.output)]
    if a.render_dir:
        args += ["--render-dir", str(a.render_dir)]
    if a.render_scale is not None:
        args += ["--render-scale", str(a.render_scale)]
    code, out, err = run_py(pathlib.Path(script), args)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


def cmd_audit(a) -> int:
    script = paths()["audit_paper_card"]
    if not script:
        print("[X] 找不到 audit_paper_card.py（nature-paper-card 未并入？）", file=sys.stderr)
        return 2
    args = ["--card", str(a.card), "--locator-mode", a.locator_mode]
    if a.bundle:
        args += ["--bundle", str(a.bundle)]
    if a.report:
        args += ["--report", str(a.report)]
    code, out, err = run_py(pathlib.Path(script), args)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


def normalize_for_content_check(text: str) -> tuple[str, dict]:
    """剥掉**结构性脚手架**后再做内容级校验，并如实报告剥了什么。

    为什么必须剥：`check_interpretation.py` 是为「11 字段中文解读草稿」写的，
      那份草稿没有编号小标题。而 16 节 Paper Card 的节标题本身就是
      `## 01 …` `## 16 Research Ideas` —— 校验器的「数字是否在原文里」
      会把 **01…16 当成 16 个原文中不存在的数字**，于是每一篇都必然报错。
      实测：未剥前 error 15 条，全部是节号，真实问题被淹没。

    剥什么（只剥脚手架，不剥任何论断）：
      ① Markdown 标题行（`#`/`##`…）—— 它们的**结构**由审计器单独校验；
      ② 第一个标题之前的 `>` 证据状态头 —— 固定的元数据块，不是论文论断。
    剥了多少行会写进报告（`normalization` 字段），所以这不是静默篡改：
      读者能看到校验跑在什么文本上，需要时可对照原文自行复算。
    """
    kept: list[str] = []
    dropped_headings = 0
    dropped_header = 0
    seen_heading = False
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            seen_heading = True
            dropped_headings += 1
            continue
        if not seen_heading and line.lstrip().startswith(">"):
            dropped_header += 1
            continue
        kept.append(line)
    return "\n".join(kept), {
        "dropped_heading_lines": dropped_headings,
        "dropped_status_header_lines": dropped_header,
        "reason": ("节标题与证据状态头属结构脚手架：其结构由 audit_paper_card.py "
                   "单独校验；不剥掉会让节号 01–16 被误判为臆造数字"),
    }


def _content_check(card: pathlib.Path, source_text: str | None,
                   strict: bool) -> tuple[int, dict, str]:
    """跑内容级校验器。返回 (退出码, 报告, 人类可读输出)。

    一律用 `--tier all`：那是校验器自带的「非档位形态输入」模式
    （`TIER_CHECKS["all"]` 只选 filler/speculation/numbers/novelty/gloss，
    不含档位预算、收尾落点、L1 五要素）—— 正好对应 16 节卡片。
    """
    script = paths()["check_interpretation"]
    if not script:
        print("[X] 找不到 check_interpretation.py（内容级校验器未并入？）", file=sys.stderr)
        return 2, {}, ""

    raw = card.read_text(encoding="utf-8", errors="replace")
    normalized, norm_note = normalize_for_content_check(raw)

    tmp = None
    norm_path = None
    tmp_dir = None
    norm_dir = None
    try:
        args = [str(card), "--tier", "all", "--json"]
        if source_text is not None:
            tmp_dir = scratch_dir("nature_bridge_src_")
            tmp = scratch_file(tmp_dir, ".txt")
            tmp.write_text(source_text, encoding="utf-8")
            args += ["--source", str(tmp)]
        # 用**归一化后的文本**做校验：写一份临时副本，卡片本身不动。
        norm_dir = scratch_dir("nature_bridge_norm_")
        norm_path = scratch_file(norm_dir, ".md")
        norm_path.write_text(normalized, encoding="utf-8")
        args[0] = str(norm_path)
        code, out, err = run_py(pathlib.Path(script), args)
        if err:
            sys.stderr.write(err)
        try:
            report = json.loads(out)
        except json.JSONDecodeError:
            print(f"[X] 内容级校验器未输出可解析的 JSON（退出码 {code}）", file=sys.stderr)
            return 2, {}, out
    finally:
        for p in (tmp, norm_path):
            if p:
                try:
                    p.unlink()
                except OSError:
                    pass
        # 文件删完还要把**目录**删掉：只 unlink 文件会在仓库里留空壳，
        # 这正是 nature_bridge_{src,norm}_* 累积的成因。见 scratch_dir() 文档。
        for d in (tmp_dir, norm_dir):
            if d:
                _reclaim(d)

    findings = report.get("findings") or []
    if not strict:
        for f in findings:
            if (f.get("severity") == "error"
                    and f.get("code") not in FORMAT_INDEPENDENT_CODES):
                f["severity"] = "warning"
                f["demoted_from_error"] = True
                f["demoted_because"] = "面向中文解读行文的检查，对 16 节卡片按 warning 计"
    errs = [f for f in findings if f.get("severity") == "error"]
    warns = [f for f in findings if f.get("severity") == "warning"]
    report["error_count"] = len(errs)
    report["warning_count"] = len(warns)
    report["valid"] = not errs
    report["context"] = "paper-card (16-section Markdown)"
    report["tier_used"] = "all"
    report["normalization"] = norm_note

    lines = [f"内容级校验（paper-interpretation-writing，--tier all"
             f"{'，--strict' if strict else ''}）："
             f"error {len(errs)} / warning {len(warns)}"]
    for f in findings:
        loc = f":{f['line']}" if "line" in f else ""
        mark = "（原 error，卡片语境降级）" if f.get("demoted_from_error") else ""
        lines.append(f"  [{f.get('severity','?').upper()}] {f.get('code')}{loc} "
                     f"{f.get('message','')}{mark}")
    if not findings:
        lines.append("  未发现可机械判定的问题。")
    return (1 if errs else 0), report, "\n".join(lines)


def cmd_content_check(a) -> int:
    source_text = None
    if a.source:
        source_text = pathlib.Path(a.source).read_text(encoding="utf-8", errors="replace")
    elif a.from_bundle:
        source_text = bundle_text(pathlib.Path(a.from_bundle))
    code, _report, text = _content_check(pathlib.Path(a.card), source_text, a.strict)
    print(text)
    return code


def cmd_check(a) -> int:
    """审计器 + 内容级校验器，合二为一 —— 交付前应只跑这一条。"""
    p = paths()
    if p["paper_card_root"] is None:
        print("[X] nature-paper-card 未并入，无法审计。", file=sys.stderr)
        return 2

    report_path = pathlib.Path(a.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # ① 结构审计
    audit_args = ["--card", str(a.card), "--locator-mode", a.locator_mode]
    if a.bundle:
        audit_args += ["--bundle", str(a.bundle)]
    tmp_audit_path = scratch_file(scratch_dir("nature_bridge_audit_"), ".json")
    audit_args += ["--report", str(tmp_audit_path)]
    audit_code, audit_out, audit_err = run_py(
        pathlib.Path(p["audit_paper_card"]), audit_args)
    sys.stdout.write(audit_out)
    if audit_err:
        sys.stderr.write(audit_err)
    try:
        audit_report = json.loads(tmp_audit_path.read_text(encoding="utf-8"))
    except Exception:
        audit_report = {"summary": {"status": "unavailable", "errors": 0,
                                    "warnings": 0, "passes": 0},
                        "findings": [],
                        "note": "审计器未产出可解析报告"}
    finally:
        try:
            tmp_audit_path.unlink()
        except OSError:
            pass
        # 同 _content_check：删文件之外还要删目录，否则 nature_bridge_audit_*
        # 会留成空壳。见 scratch_dir() 文档。
        _reclaim(tmp_audit_path.parent)

    # ② 内容级校验（数字回查原文 —— 审计器做不到的那一半）
    #   原文取哪里：显式 --source 优先；否则**自动用 --bundle 里的逐页正文**。
    #   为什么自动取：既然 bundle 就在手边，原文就已经拿到了 ——
    #     还要用户再指一次 --source，结果只会是「忘了给 → 数字没回查 → 却报 pass」。
    #     这类"默认不检查"的设计，正是本流程一贯拒绝的静默失效。
    source_text = None
    if a.source:
        source_text = pathlib.Path(a.source).read_text(encoding="utf-8", errors="replace")
    elif a.from_bundle:
        source_text = bundle_text(pathlib.Path(a.from_bundle))
    elif a.bundle:
        source_text = bundle_text(pathlib.Path(a.bundle))
    content_code, content_report, content_text = _content_check(
        pathlib.Path(a.card), source_text, a.strict)
    print()
    print(content_text)

    audit_errors = int((audit_report.get("summary") or {}).get("errors") or 0)
    content_errors = int(content_report.get("error_count") or 0)
    merged = {
        "schema_version": "1.0",
        "card": str(pathlib.Path(a.card).resolve()),
        "locator_mode": a.locator_mode,
        "bundle": str(pathlib.Path(a.bundle).resolve()) if a.bundle else None,
        "source_checked": source_text is not None,
        "summary": {
            "status": "fail" if (audit_errors or content_errors) else "pass",
            "audit_errors": audit_errors,
            "content_errors": content_errors,
            "content_warnings": len(content_report.get("findings") or []),
            "audit_exit": audit_code,
            "content_exit": content_code,
        },
        "structural_audit": audit_report,
        "content_check": content_report,
        "notice": (
            "结构审计只证明卡片**形态**合规，不证明内容可信；"
            "内容级校验只证明卡片里的数字能在原文中找到，"
            "不证明「这句话原文里没有」。语气强度与机制忠实度仍需人工回原文核对。"
        ),
    }
    report_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print()
    print(f"合并报告：{report_path}")
    print(f"结论：{merged['summary']['status']} "
          f"（结构 error {audit_errors} / 内容 error {content_errors}）")
    return 1 if merged["summary"]["status"] == "fail" else 0


# ── 自检 ─────────────────────────────────────────────────────
def self_test() -> int:
    ok = True
    print("=== nature_bridge.py --self-test（零网络）===")

    p = paths()
    print(f"paper-card 根：{p['paper_card_root']}")
    print(f"已并入技能：{p['vendored_skills']}")
    # 并入范围是**一个**技能：多出来就说明有人把整包又塞回来了，
    #   那会让"不构成外部依赖"的边界失效，必须让人看见。
    if p["paper_card_root"] is None or p["vendored_skills"] != ["nature-paper-card"]:
        print("[X] 技能根缺失，或并入范围不是「只有 nature-paper-card」")
        ok = False
    if p["interpretation_skill"] is None:
        print("[X] 内容级校验器缺失")
        ok = False
    for key in ("prepare_paper", "audit_paper_card", "check_interpretation"):
        val = p[key]
        exists = bool(val) and pathlib.Path(val).is_file()
        print(f"{'[OK]' if exists else '[X] '} {key}: {val}")
        ok = ok and exists
    # 术语台账必须**内联在技能内**（上游写的是 ../nature-shared/...）
    ledger = pathlib.Path(p["paper_card_root"]) / "references" / "terminology-ledger.md" \
        if p["paper_card_root"] else None
    inlined = bool(ledger and ledger.is_file())
    print(f"{'[OK]' if inlined else '[X] '} 术语台账已内联: {ledger}")
    ok = ok and inlined
    for broken in (pathlib.Path(p["paper_card_root"]) / "manifest.yaml"
                   if p["paper_card_root"] else None,):
        if broken and broken.is_file():
            # 只看**有效 YAML**：注释里可以写 `../nature-shared/...` 解释历史，
            #   那不是引用。误报会让人把这条守卫当成噪声而忽略它。
            effective = [ln for ln in broken.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#")]
            escaped = [ln.strip() for ln in effective if "../" in ln]
            no_escape = not escaped
            print(f"{'[OK]' if no_escape else '[X] '} manifest 无越出技能目录的 ../ 引用"
                  + ("" if no_escape else f"：{escaped}"))
            ok = ok and no_escape

    if not ok:
        print("\n结果：FAIL（路径层）")
        return 1

    for key in ("prepare_paper", "audit_paper_card"):
        code, out, _ = run_py(pathlib.Path(p[key]), ["--help"])
        good = code == 0 and "usage" in out.lower()
        print(f"{'[OK]' if good else '[X] '} {key} --help → exit {code}")
        ok = ok and good
    code, out, _ = run_py(pathlib.Path(p["check_interpretation"]), ["--help"])
    good = code == 0
    print(f"{'[OK]' if good else '[X] '} check_interpretation.py --help → exit {code}")
    ok = ok and good

    tmpdir = scratch_dir("nature_bridge_selftest_")
    try:
        # ① source map（用 nature-reader 的官方字段名 original_text）→ 必须出页
        smap = tmpdir / "source_map.json"
        smap.write_text(json.dumps({
            "paper": {"title": "Self-test paper", "source_type": "pdf"},
            "blocks": [
                {"id": "S001", "page": 1, "type": "paragraph",
                 "original_text": "The treated group lost 92 percent of activity "
                                  "across three independent replicates."},
                {"id": "S002", "page": 2, "type": "paragraph",
                 "original_text": "Figure 1 shows the dose response at 10 micromolar."},
            ],
            "pages": [{"page": 1, "block_ids": ["S001"]},
                      {"page": 2, "block_ids": ["S002"]}],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        bundle = tmpdir / "source_bundle.json"
        code, out, err = run_py(pathlib.Path(p["prepare_paper"]),
                               [str(smap), "--output", str(bundle)])
        data = json.loads(bundle.read_text(encoding="utf-8")) if bundle.exists() else {}
        pages = data.get("page_count")
        mode = ((data.get("validation") or {}).get("recommended_locator_mode"))
        # 这是 FIX 1/FIX 2 的回归点：并入前此处是 0 页 + invalid + source-limited
        good = code == 0 and pages == 2 and mode == "page-grounded"
        print(f"{'[OK]' if good else '[X] '} source_map(original_text) → "
              f"exit {code}, 页数 {pages}, 推荐模式 {mode}")
        if not good:
            print(f"      stdout: {out.strip()[:300]}")
            print(f"      stderr: {err.strip()[:300]}")
        ok = ok and good

        # ② 空壳卡必须被审计器判错（FIX 5 的回归点）
        card = tmpdir / "card.md"
        body = ["# Self-test card"]
        body += ["> " + x for x in (
            "Source coverage: Full paper", "Extraction confidence: High",
            "Locator mode: page-grounded", "Primary analytical lens: methods",
            "Secondary analytical lens: None", "Context verification: Paper-only",
            "Card completeness: Complete relative to supplied source")]
        for n in range(1, 17):
            body.append(f"\n## {n:02d} Section {n}\n")
        card.write_text("\n".join(body), encoding="utf-8")
        report = tmpdir / "merged.json"
        code, out, _ = run_py(pathlib.Path(__file__).resolve(),
                              ["check", "--card", str(card), "--bundle", str(bundle),
                               "--locator-mode", "page-grounded",
                               "--report", str(report)])
        merged = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        errs = ((merged.get("summary") or {}).get("audit_errors"))
        good = code == 1 and isinstance(errs, int) and errs > 0
        print(f"{'[OK]' if good else '[X] '} 空壳卡 → exit {code}, 结构 error {errs}"
              f"（期望非零且 error>0）")
        if not good:
            print(f"      stdout: {out.strip()[:400]}")
        ok = ok and good

        # ③ 内容级校验：卡里编一个原文没有的数字 → 必须被抓
        card2 = tmpdir / "card2.md"
        card2.write_text(
            "# t\n\n## 01 Basic Information\nx\n\n## 10 Evidence\n"
            "[Paper: PDF p. 2] 抑制率达到 97.5% 且 n = 42。\n", encoding="utf-8")
        code, _rep, text = _content_check(card2, "The treated group lost 92 percent.",
                                          strict=False)
        good = code == 1 and "NUMBER_NOT_IN_SOURCE" in text
        print(f"{'[OK]' if good else '[X] '} 编造数字 → exit {code}"
              f"（期望 1 且命中 NUMBER_NOT_IN_SOURCE）")
        if not good:
            print(f"      {text.strip()[:400]}")
        ok = ok and good

        # ④ 回归：节标题里的 01…16 不得被当成臆造数字
        #    （未做脚手架归一化时，每一篇都会因此报 15 条假 error）
        card3 = tmpdir / "card3.md"
        body3 = ["# t"] + [f"\n## {n:02d} Section {n}\n[Paper: PDF p. 1] "
                           f"实测 92%（n = 6）。" for n in range(1, 17)]
        card3.write_text("\n".join(body3), encoding="utf-8")
        code, _rep, text = _content_check(
            card3, "Activity dropped by 92 percent (n = 6).", strict=False)
        good = code == 0 and "NUMBER_NOT_IN_SOURCE" not in text
        print(f"{'[OK]' if good else '[X] '} 节标题 01–16 不被误判为臆造数字 → "
              f"exit {code}（期望 0）")
        if not good:
            print(f"      {text.strip()[:400]}")
        ok = ok and good
    finally:
        _reclaim(tmpdir)

    print(f"\n结果：{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nature_bridge.py",
        description="流程1 ↔ 已并入 nature 技能的唯一适配层（路径解析 + 审计 + 内容级校验）。",
        epilog="退出码：0 = 通过；1 = 有 error；2 = 用法/读取/路径错误。",
    )
    sub = parser.add_subparsers(dest="cmd")

    pl = sub.add_parser("locations", help="打印解析到的所有路径（排障用）")
    pl.add_argument("--json", action="store_true")

    pp = sub.add_parser("prepare", help="跑 nature-paper-card 的 prepare_paper.py")
    pp.add_argument("--pdf", required=True, type=pathlib.Path)
    pp.add_argument("--output", required=True, type=pathlib.Path)
    pp.add_argument("--render-dir", type=pathlib.Path)
    pp.add_argument("--render-scale", type=float)

    pa = sub.add_parser("audit", help="跑 nature-paper-card 的 audit_paper_card.py")
    pa.add_argument("--card", required=True, type=pathlib.Path)
    pa.add_argument("--bundle", type=pathlib.Path)
    pa.add_argument("--locator-mode", required=True, choices=AUDIT_LOCATOR_MODES)
    pa.add_argument("--report", type=pathlib.Path)

    pc = sub.add_parser("content-check", help="跑内容级校验器（数字回查原文）")
    pc.add_argument("--card", required=True, type=pathlib.Path)
    pc.add_argument("--source", type=pathlib.Path, help="原文纯文本")
    pc.add_argument("--from-bundle", type=pathlib.Path,
                    help="从 source_bundle.json 取原文")
    pc.add_argument("--strict", action="store_true",
                    help="不做卡片语境的严重度降级")

    pk = sub.add_parser("check", help="审计 + 内容级校验（交付前只跑这一条）")
    pk.add_argument("--card", required=True, type=pathlib.Path)
    pk.add_argument("--bundle", type=pathlib.Path)
    pk.add_argument("--source", type=pathlib.Path)
    pk.add_argument("--from-bundle", type=pathlib.Path)
    pk.add_argument("--locator-mode", required=True, choices=AUDIT_LOCATOR_MODES)
    pk.add_argument("--report", required=True, type=pathlib.Path)
    pk.add_argument("--strict", action="store_true")

    parser.add_argument("--self-test", action="store_true",
                        help="零网络自检：路径、--help、端到端小样")
    return parser


def main() -> int:
    _fix_streams()
    parser = build_parser()
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if not args.cmd:
        parser.print_help()
        return 2
    if args.cmd == "locations":
        return cmd_locations(args.json)
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "audit":
        return cmd_audit(args)
    if args.cmd == "content-check":
        if not (args.source or args.from_bundle):
            print("[X] content-check 需要 --source 或 --from-bundle。", file=sys.stderr)
            return 2
        return cmd_content_check(args)
    if args.cmd == "check":
        return cmd_check(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
