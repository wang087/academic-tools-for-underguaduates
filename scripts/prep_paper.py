# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 中译素材准备：原文 PDF → 按页分节的纯文本 + 图注页索引（阶段 F 前置）
#
# 为什么要按页分节：翻译必须逐段对照原文，且译文需要保留原文章节层级。
# 按页切分后每段都有明确出处，便于核对「是否漏译」「数值是否抄错」。
#
# 用法：
#   uv run --with pymupdf python prep_paper.py <pdf> [--out 目录] [--key 名]
#   默认输出 <WORKDIR>/.work_cdp/md/<key>.txt（key 缺省取 PDF 文件名主干）
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from logutil import get as get_log, workdir as _wd   # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    pdf = pathlib.Path(argv[0]).resolve()
    if not pdf.exists():
        print(f"✗ 找不到 PDF: {pdf}")
        return 1

    try:
        import fitz                      # PyMuPDF
    except ImportError:
        print("✗ 需要 PyMuPDF：uv run --with pymupdf python prep_paper.py ...")
        return 1

    wd = _wd()
    outdir = wd / ".work_cdp" / "md"
    key = pdf.stem
    for i, a in enumerate(argv[1:]):
        if a.startswith("--out="):
            outdir = pathlib.Path(a.split("=", 1)[1]).resolve()
        elif a == "--out" and i + 2 < len(argv):
            outdir = pathlib.Path(argv[i + 2]).resolve()
        elif a.startswith("--key="):
            key = a.split("=", 1)[1]
    outdir.mkdir(parents=True, exist_ok=True)
    log = get_log("prep_paper", wd=wd)

    try:
        doc = fitz.open(str(pdf))
    except Exception as e:
        log.error(f"无法打开 PDF {pdf.name}: {type(e).__name__}: {e}")
        log.close()
        return 1
    lines = [f"# 源文件: {pdf.name}", f"# 总页数: {len(doc)}", ""]
    fig_pages = []
    for pi, page in enumerate(doc, 1):
        t = page.get_text("text") or ""
        lines.append(f"\n{'=' * 78}\n===== 第 {pi} 页 =====\n{'=' * 78}")
        lines.append(t.rstrip())
        for m in re.finditer(r"(Extended Data Fig\.?\s*\d+|Supplementary Fig\.?\s*\d+|Fig\.?\s*\d+)\s*\|", t):
            fig_pages.append((pi, m.group(1)))

    p = outdir / f"{key}.txt"
    p.write_text("\n".join(lines), encoding="utf-8")

    chars = sum(len(l) for l in lines)
    if chars == 0 or not any(l.strip() for l in lines[3:]):
        log.error(f"{key}: PDF 未提取到可用文本（可能是扫描件或字体映射损坏）")
        log.close()
        return 2
    log.ok(f"{key}: {len(doc)} 页 | 文本 {p.stat().st_size // 1024} KB | 约 {chars} 字 → {p.name}")
    if fig_pages:
        seen = []
        for pi, lab in fig_pages:
            if not seen or seen[-1][1] != lab:
                seen.append((pi, lab))
        log.info("图注所在页： " + ", ".join(f"{lab}@p{pi}" for pi, lab in seen[:24]))
        print("\n提示：图注所在页可用于定位主图 —— 图通常在**图注页的前一页**，")
        print("      用 crop_by_images.py 对该页取「位图并集」即可裁出图-only 图像。")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
