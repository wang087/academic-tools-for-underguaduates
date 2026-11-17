# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# opendataloader-pdf：可选轻量解析层（确定性、带 bbox，需 Java）
#
# 定位（分层共存，见 SKILL.md「可选解析层」节）
#   主链路仍是 fitz/pypdf（prep_paper.py / extract_parallel.py）。本脚本是
#   **可选增强**，不替换主链路：数字版 PDF 上它约 0.7s/篇、输出确定性 Markdown
#   与逐元素 bounding-box JSON（利于溯源），可作交叉校验或备选引擎。
#   本地模式**无 OCR**；扫描件 / 复杂表格 / 公式需其 hybrid 模式（另装 Python
#   AI 栈），那不在本可选层范围内 —— 深解析请走 MinerU（optional_layers.py）。
#
# 资产（已完整解压整合进流程，不走 zip 运行时解压）
#   随包副本在 vendor/opendataloader/（jar + LICENSE + NOTICE + THIRD_PARTY）。
#   jar 发现优先级：
#     ① 环境变量 OPENDATALOADER_JAR（显式覆盖，换版本/换机用）
#     ② vendor/opendataloader/*.jar（随包默认）
#   本脚本是核心侧访问该模块的**唯一 adapter**（类比 nature_bridge.py）。
#
# 本脚本**只本地处理**，不发起网络请求。
#
# 用法：
#   uv run python 流程1/scripts/opendataloader_extract.py <pdf> [<pdf> ...] \
#       [--out 目录] [--format markdown,json] [--image-output off]
#   uv run python 流程1/scripts/opendataloader_extract.py --detect      # 可用性探测
#   uv run python 流程1/scripts/opendataloader_extract.py --self-test   # 零网络自检
#
# 默认输出：<WORKDIR>/.work_cdp/odl/<pdf主干>.md / .json
# 退出码：0=成功；2=用法/环境错误（缺 Java 或 jar）；1=解析失败；--detect 3=不可用。
#
# 可移植配置：PAPER_WORKDIR、OPENDATALOADER_JAR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import sys


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
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8",
                                                errors="replace", line_buffering=True))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

FLOW_ROOT = _HERE.parent
MODULE_DIR = FLOW_ROOT / "vendor" / "opendataloader"
JAR_GLOB = "opendataloader-pdf-cli-*.jar"


def _workdir() -> pathlib.Path:
    env = (os.environ.get("PAPER_WORKDIR") or "").strip()
    if env:
        return pathlib.Path(env).resolve()
    try:
        from logutil import workdir
        return workdir()
    except Exception:
        import time
        d = FLOW_ROOT / time.strftime("%Y%m%d")
        author = (os.environ.get("PAPER_AUTHOR") or "").strip()
        return (d / author if author else d).resolve()


def java_exe() -> str | None:
    return shutil.which("java")


def module_dir() -> pathlib.Path:
    return MODULE_DIR


def find_jar(wd: pathlib.Path | None = None) -> pathlib.Path | None:
    """jar 发现：env 显式覆盖 > vendor/opendataloader/*.jar。无 → None。"""
    env = (os.environ.get("OPENDATALOADER_JAR") or "").strip()
    if env:
        p = pathlib.Path(env)
        if p.is_file():
            return p.resolve()
    if MODULE_DIR.is_dir():
        hits = sorted(MODULE_DIR.glob(JAR_GLOB))
        if hits:
            return hits[-1].resolve()
    return None


def detect(wd: pathlib.Path | None = None) -> dict:
    jar = find_jar(wd)
    java = java_exe()
    return {
        "jar": str(jar) if jar else "",
        "java": java or "",
        "module_dir": str(MODULE_DIR),
        "available": bool(java) and bool(jar),
    }


def available(wd: pathlib.Path | None = None) -> bool:
    return detect(wd)["available"]


def describe(wd: pathlib.Path | None = None) -> str:
    d = detect(wd)
    if d["available"]:
        return f"opendataloader 可选层：可用（Java={d['java']}；jar={d['jar']}）"
    miss = []
    if not d["java"]:
        miss.append("缺 Java 11+")
    if not d["jar"]:
        miss.append(f"缺 jar（{MODULE_DIR} 无 *.jar，或设 OPENDATALOADER_JAR）")
    return "opendataloader 可选层：不可用（" + "；".join(miss) + "）→ 主链路 fitz 不受影响"


def build_cmd(jar: pathlib.Path, pdfs: list[pathlib.Path], outdir: pathlib.Path,
              fmt: str = "markdown,json", image_output: str = "off") -> list[str]:
    return [java_exe() or "java", "-jar", str(jar),
            "-f", fmt, "--image-output", image_output,
            "-q", "-o", str(outdir), *[str(p) for p in pdfs]]


def extract(pdfs: list[pathlib.Path], outdir: pathlib.Path | None = None,
            fmt: str = "markdown,json", image_output: str = "off",
            wd: pathlib.Path | None = None) -> int:
    java = java_exe()
    if not java:
        print("[X] 缺 Java 11+：opendataloader 需要 JRE。主链路 fitz 不受影响。",
              file=sys.stderr)
        return 2
    jar = find_jar(wd)
    if not jar:
        print(f"[X] 找不到 opendataloader jar：{MODULE_DIR} 下无 *.jar，"
              "或设 OPENDATALOADER_JAR 显式指定。", file=sys.stderr)
        return 2
    outdir = outdir or ((wd or _workdir()) / ".work_cdp" / "odl")
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(jar, pdfs, outdir, fmt, image_output)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        print("[X] opendataloader 解析超时", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"[X] opendataloader 退出码 {r.returncode}\n{(r.stderr or '')[-800:]}",
              file=sys.stderr)
        return 1
    produced = sorted(outdir.glob("*"))
    print(f"[OK] opendataloader 解析 {len(pdfs)} 篇 → {outdir}")
    for p in produced:
        if p.suffix in (".md", ".json", ".html", ".txt"):
            print(f"  · {p.name}（{p.stat().st_size} 字节）")
    try:
        from logutil import journal
        journal("E", "opendataloader.extract", n=len(pdfs), out=str(outdir), format=fmt)
    except Exception:
        pass
    return 0


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
        old_jar = os.environ.pop("OPENDATALOADER_JAR", None)
        try:
            d = detect(wd)
            ck("detect 字段齐全", all(k in d for k in ("jar", "java", "available", "module_dir")))
            ck("available 与 java+jar 一致",
               d["available"] == (bool(d["java"]) and bool(d["jar"])))

            # 显式 env jar 优先
            fake = wd / "opendataloader-pdf-cli-9.9.9.jar"
            fake.write_bytes(b"PK\x03\x04fake")
            os.environ["OPENDATALOADER_JAR"] = str(fake)
            ck("env jar 被优先发现", find_jar(wd) == fake.resolve())
            cmd = build_cmd(fake, [wd / "a.pdf"], wd / "out")
            ck("build_cmd 含 jar 与输出目录", str(fake) in cmd and "-o" in cmd)
            os.environ.pop("OPENDATALOADER_JAR", None)

            # 随包 vendor jar（本机已整合则实测发现；零网络）
            vjar = find_jar(wd)
            if vjar:
                ck("发现随包 vendor jar", vjar.is_file() and "vendor" in str(vjar), str(vjar))
                ck("vendor 目录含 LICENSE/NOTICE（合规）",
                   (MODULE_DIR / "LICENSE").is_file() and (MODULE_DIR / "NOTICE").is_file())
                ck("vendor 目录含 THIRD_PARTY 声明",
                   (MODULE_DIR / "THIRD_PARTY").is_dir())
            else:
                print("  [--] 本机无 vendor/opendataloader jar，跳过随包发现实测")
        finally:
            if old_jar is not None:
                os.environ["OPENDATALOADER_JAR"] = old_jar

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--detect" in argv:
        import json
        if _arg("json"):
            print(json.dumps(detect(), ensure_ascii=False, indent=2))
        else:
            print(describe())
        return 0 if available() else 3

    # 收集 PDF 位置参数；剔除带值开关（--out/--format/--image-output）吞掉的值
    opts_with_val = {"--out", "--format", "--image-output"}
    cleaned, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in opts_with_val:
            skip = True
            continue
        if a.startswith("--"):
            continue
        cleaned.append(pathlib.Path(a))
    pdfs = cleaned
    if not pdfs:
        print("用法: opendataloader_extract.py <pdf> [...] [--out 目录] "
              "[--format markdown,json] [--image-output off]｜--detect｜--self-test",
              file=sys.stderr)
        return 2
    missing = [p for p in pdfs if not p.is_file()]
    if missing:
        print(f"[X] 找不到 PDF：{missing}", file=sys.stderr)
        return 2
    out = _arg("out")
    outdir = pathlib.Path(out).resolve() if out and out is not True else None
    fmt = _arg("format", "markdown,json") or "markdown,json"
    img = _arg("image-output", "off") or "off"
    return extract(pdfs, outdir, str(fmt), str(img))


if __name__ == "__main__":
    raise SystemExit(main())