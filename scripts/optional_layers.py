# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 可选解析层：首跑探测 + 安装决策记录（MinerU / opendataloader）
#
# 为什么要有这一步
#   流程的 PDF 解析按「分层共存」组织（见 SKILL.md「可选解析层」节）：
#     · 主链路：fitz/pypdf（prep_paper.py / extract_parallel.py），数字版 PDF 已够用；
#     · 可选轻量层：opendataloader（24MB jar，需 Java，确定性、带 bbox）；
#     · 可选深解析层：MinerU（扫描件 OCR / 复杂表格 / 公式，重达数 GB）。
#   MinerU 不是必装项。是否安装属于「只有用户能定」的环境级选择，
#   按文件头第 8 条「增加类操作必须分项确认」，应在**首跑**时单独问一句，
#   并如实告知体积、损益与建议，而不是替用户默认装上几个 GB。
#
# 首跑判据（用户指定，三者同时成立才触发询问）
#   ① 工作目录无产物目录（*_paper_Exports / *_Exports / outputs）；
#   ② 工作目录无临时文件目录（.work_cdp/）；
#   ③ 环境无 MinerU（PATH 无 mineru、当前解释器 import 不到、未设 MINERU_HOME）。
#   一旦记录过决定（yes/no/defer），后续运行不再重复追问。
#
# 本脚本**只读探测 + 本地落盘**，不发起任何网络请求，不替用户安装任何东西。
# 脚本是记录器，不是提问器：probe 给出判据与建议，由 agent 在对话里问用户，
# 再用 record 落盘答复（与 prepare_stage.py 同款分工）。
#
# 用法：
#   uv run python 流程1/scripts/optional_layers.py probe          # 首跑探测 + 建议
#   uv run python 流程1/scripts/optional_layers.py probe --json   # 机读
#   uv run python 流程1/scripts/optional_layers.py record --mineru-install=no
#   uv run python 流程1/scripts/optional_layers.py record --mineru-install=yes --note="要跑扫描件"
#   uv run python 流程1/scripts/optional_layers.py status         # 当前可用性 + 已记录决定
#   uv run python 流程1/scripts/optional_layers.py --self-test    # 零网络自检
#
# 退出码：probe/status/self-test 0=正常；record 0=已落盘、2=用法/取值错误。
#   probe 的 JSON 里 `should_ask_mineru_install` 标明是否需要向用户发问。
#
# 可移植配置：PAPER_WORKDIR、PAPER_AUTHOR、MINERU_HOME、OPENDATALOADER_JAR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import time


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；判据与 logutil._fix_stdout 一致）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8",
                                                errors="replace", line_buffering=True))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

FLOW_ROOT = _HERE.parent
DECISION_SCHEMA = 1
DECISION_NAME = "optional_layers.json"
VALID_DECISIONS = ("yes", "no", "defer")

# ── MinerU 安装告知（如实申报；体积为 MinerU 4.x 的量级估计，非本机实测承诺）──
# 数字版 PDF 的解析质量对比证据见 实测结果汇总.md「可选解析层」节。
# 这里只给「该不该装」的判据，不把某次运行的实测数写死成保证。
MINERU_ADVISORY = {
    "size_total": "全量约 6 GB 量级：模型 ~2.9G（onnx/torch/VLM-GGUF 三套）"
                  "+ 代码及专属依赖 ~0.3G + 共享 torch 运行时 ~3G",
    "size_marginal": "若 torch 已因小模型项目装好，MinerU 边际占用约 3 GB（主要是模型）",
    "size_reducible": "onnx 与 torch 两套模型同构冗余，核实当前后端后可省约 0.8–2 GB；"
                      "VLM-GGUF（~1.2G）仅深解析档需要",
    "pros": [
        "扫描件 / 图片型 PDF 可 OCR（数字版解析器做不到）",
        "复杂 / 无边框表格、公式（LaTeX）结构化更好",
        "脚注 / 表格 / 页眉分离，正文字符量通常比 fitz 多约 20–27%",
        "输出规范 Markdown 标题层级",
    ],
    "cons": [
        "重：数 GB 磁盘 + 常驻解析服务器，首启预载约 1–2 分钟",
        "慢：数字版 PDF 约 5s/篇，fitz 为毫秒级、opendataloader 约 0.7s/篇",
        "mineru_llama_cpp 在含中文的路径上有编码 bug，需 ASCII junction 变通",
        "数字版 PDF 上相对 fitz 的质量增益有限（字符量略增，符号保真三者一致）",
    ],
    "recommendation": "数字版 PDF 为主 → 不必装（fitz 主链 + opendataloader 可选层已够）；"
                      "确有扫描件 / 复杂表格 / 公式深解析需求 → 再装，并先解决中文路径问题",
}


def _default_workdir() -> pathlib.Path:
    """与 logutil.run_root 同口径，但**不创建目录**（探测要保持首跑现场）。"""
    d = FLOW_ROOT / time.strftime("%Y%m%d")
    author = (os.environ.get("PAPER_AUTHOR") or "").strip()
    if author:
        d = d / author
    return d


def probe_workdir() -> pathlib.Path:
    env = (os.environ.get("PAPER_WORKDIR") or "").strip()
    return pathlib.Path(env).resolve() if env else _default_workdir().resolve()


def decision_path(wd: pathlib.Path | None = None) -> pathlib.Path:
    return (wd or probe_workdir()) / ".work_cdp" / DECISION_NAME


def first_run_state(wd: pathlib.Path) -> dict:
    """①无产物目录 ②无临时目录 —— 两者皆无才算首跑（③环境无 MinerU 另判）。"""
    has_temp = (wd / ".work_cdp").exists()
    has_products = (
        any(wd.glob("*_paper_Exports"))
        or any(wd.glob("*_Exports"))
        or (wd / "outputs").exists()
    )
    return {
        "workdir": str(wd),
        "has_temp_dir": has_temp,
        "has_product_dir": has_products,
        "first_run": (not has_temp) and (not has_products),
    }


def detect_mineru() -> dict:
    """环境是否有 MinerU：PATH CLI / 当前解释器可 import / MINERU_HOME 三者任一。"""
    exe = shutil.which("mineru")
    try:
        importable = importlib.util.find_spec("mineru") is not None
    except (ImportError, ValueError):
        importable = False
    home = (os.environ.get("MINERU_HOME") or "").strip()
    home_exists = bool(home) and pathlib.Path(home).exists()
    return {
        "cli_on_path": exe or "",
        "importable": importable,
        "mineru_home": home,
        "mineru_home_exists": home_exists,
        "available": bool(exe) or importable or home_exists,
    }


def detect_opendataloader() -> dict:
    try:
        import opendataloader_extract as odl
        d = odl.detect()
        return {
            "jar": d.get("jar", ""),
            "java": d.get("java", ""),
            "module_dir": d.get("module_dir", ""),
            "available": d.get("available", False),
        }
    except Exception as e:
        return {"jar": "", "java": "", "available": False, "error": str(e)}


def load_decision(wd: pathlib.Path | None = None) -> dict | None:
    p = decision_path(wd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def should_ask_mineru(state: dict, mineru: dict, decision: dict | None) -> bool:
    """首跑 + 环境无 MinerU + 尚未记录过决定 → 需要向用户发问。"""
    if not state["first_run"] or mineru["available"]:
        return False
    prior = (decision or {}).get("mineru_install", {})
    return prior.get("decision") not in VALID_DECISIONS


def build_probe(wd: pathlib.Path | None = None) -> dict:
    wd = wd or probe_workdir()
    state = first_run_state(wd)
    mineru = detect_mineru()
    odl = detect_opendataloader()
    decision = load_decision(wd)
    return {
        "first_run": state,
        "mineru": mineru,
        "opendataloader": odl,
        "recorded_decision": decision,
        "should_ask_mineru_install": should_ask_mineru(state, mineru, decision),
        "mineru_advisory": MINERU_ADVISORY,
    }


def render_probe(p: dict) -> str:
    L = []
    fr = p["first_run"]
    L.append("可选解析层 · 首跑探测")
    L.append("─" * 66)
    L.append(f"  工作目录：{fr['workdir']}")
    L.append(f"  产物目录：{'有' if fr['has_product_dir'] else '无'}"
             f"｜临时目录(.work_cdp)：{'有' if fr['has_temp_dir'] else '无'}"
             f"｜首跑：{'是' if fr['first_run'] else '否'}")
    m = p["mineru"]
    L.append(f"  MinerU：{'可用' if m['available'] else '未安装'}"
             f"（PATH={'有' if m['cli_on_path'] else '无'}，"
             f"import={'可' if m['importable'] else '否'}，"
             f"MINERU_HOME={'设' if m['mineru_home'] else '未设'}）")
    o = p["opendataloader"]
    L.append(f"  opendataloader：{'可用' if o['available'] else '不可用'}"
             f"（jar={'在位' if o.get('jar') else '无'}，Java={'有' if o['java'] else '无'}）")
    prior = (p.get("recorded_decision") or {}).get("mineru_install", {})
    if prior.get("decision") in VALID_DECISIONS:
        L.append(f"  已记录 MinerU 安装决定：{prior['decision']}"
                 f"（{prior.get('ts', '')}）→ 不再追问")
    L.append("")
    if p["should_ask_mineru_install"]:
        L.append("  [ASK] 触发首跑询问：请就「是否安装 MinerU」单独问用户一句，")
        L.append("        如实告知以下体积 / 损益 / 建议，再用 record 落盘答复。")
        a = p["mineru_advisory"]
        L.append(f"        体积：{a['size_total']}")
        L.append(f"        边际：{a['size_marginal']}")
        L.append(f"        可省：{a['size_reducible']}")
        L.append("        收益：" + "；".join(a["pros"]))
        L.append("        代价：" + "；".join(a["cons"]))
        L.append(f"        建议：{a['recommendation']}")
        L.append("        落盘：optional_layers.py record --mineru-install=yes|no|defer")
    else:
        L.append("  [OK] 无需首跑询问（非首跑 / 已装 MinerU / 已记录决定）。")
    return "\n".join(L)


def record(decision: str, note: str = "", wd: pathlib.Path | None = None) -> pathlib.Path:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"--mineru-install 取值 {decision!r} 非法（可用 yes/no/defer）")
    wd = wd or probe_workdir()
    p = decision_path(wd)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_decision(wd) or {"_schema": DECISION_SCHEMA}
    data["_schema"] = DECISION_SCHEMA
    data["mineru_install"] = {
        "decision": decision,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "explicit",
        "note": note,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        from logutil import journal
        journal("0", "optional_layers.mineru_decision", decision=decision, note=note)
    except Exception:
        pass
    return p


def _arg(name, default=None):
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
        if a == f"--{name}":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
            return True
    return default


def self_test() -> int:
    import tempfile
    bad = 0

    def ck(name, ok, detail=""):
        nonlocal bad
        print(f"  {'[OK]' if ok else '[X]'} {name}" + (f"  —— {detail}" if detail else ""))
        if not ok:
            bad += 1

    with tempfile.TemporaryDirectory() as td:
        wd = pathlib.Path(td)
        st = first_run_state(wd)
        ck("空目录判为首跑", st["first_run"] and not st["has_temp_dir"]
           and not st["has_product_dir"])

        (wd / ".work_cdp").mkdir()
        ck("有 .work_cdp 即非首跑", not first_run_state(wd)["first_run"])
        shutil.rmtree(wd / ".work_cdp")

        (wd / "某作者_paper_Exports").mkdir()
        ck("有产物目录即非首跑", not first_run_state(wd)["first_run"])
        shutil.rmtree(wd / "某作者_paper_Exports")

        (wd / "outputs").mkdir()
        ck("有 outputs 即非首跑", not first_run_state(wd)["first_run"])
        shutil.rmtree(wd / "outputs")

        # should_ask 真值表
        fresh = {"first_run": True}
        no_mineru = {"available": False}
        ck("首跑+无MinerU+无决定 → 询问",
           should_ask_mineru(fresh, no_mineru, None))
        ck("已装MinerU → 不询问",
           not should_ask_mineru(fresh, {"available": True}, None))
        ck("非首跑 → 不询问",
           not should_ask_mineru({"first_run": False}, no_mineru, None))
        for d in VALID_DECISIONS:
            ck(f"已记录 {d} → 不追问",
               not should_ask_mineru(fresh, no_mineru, {"mineru_install": {"decision": d}}))

        # record / load 往返
        p = record("no", note="数字版为主", wd=wd)
        ck("record 落盘存在", p.exists())
        got = load_decision(wd)
        ck("load 回读一致", (got or {}).get("mineru_install", {}).get("decision") == "no")
        ck("落盘后不再询问",
           not should_ask_mineru(fresh, no_mineru, got))
        try:
            record("maybe", wd=wd)
            ck("非法取值应报错", False)
        except ValueError:
            ck("非法取值应报错", True)

    a = MINERU_ADVISORY
    ck("告知含体积/损益/建议",
       all(k in a for k in ("size_total", "size_marginal", "pros", "cons", "recommendation"))
       and len(a["pros"]) > 0 and len(a["cons"]) > 0)

    m = detect_mineru()
    ck("detect_mineru 字段齐全",
       all(k in m for k in ("available", "importable", "cli_on_path", "mineru_home")))
    o = detect_opendataloader()
    ck("detect_opendataloader 字段齐全", all(k in o for k in ("available", "jar", "java")))

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv and not argv[0].startswith("--") else "probe"

    if "--self-test" in argv:
        return self_test()

    if cmd == "probe":
        p = build_probe()
        if _arg("json"):
            print(json.dumps(p, ensure_ascii=False, indent=2))
        else:
            print(render_probe(p))
        return 0

    if cmd == "status":
        p = build_probe()
        print(render_probe(p))
        return 0

    if cmd == "record":
        val = _arg("mineru-install")
        if not val or val is True:
            print("[X] record 需要 --mineru-install=yes|no|defer", file=sys.stderr)
            return 2
        note = _arg("note", "") or ""
        try:
            p = record(str(val), note=str(note))
        except ValueError as e:
            print(f"[X] {e}", file=sys.stderr)
            return 2
        print(f"[OK] MinerU 安装决定已落盘：{val} → {p}")
        return 0

    print(f"[X] 未知命令 {cmd!r}（可用 probe/status/record/--self-test）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())