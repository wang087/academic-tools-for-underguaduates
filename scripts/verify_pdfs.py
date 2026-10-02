# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文 PDF 完整性校验（阶段 I）
#
# 逐篇跑 store.validate_pdf（%PDF 头 + 体积 + 页数规则），并把**页数**单独
# 列出供人工判断——流程要求「正文一般 ≥6 页；2 页件须排查是否为补充材料 /
# 图注页」。实测确有把 suppl_file/*.pdf（补充材料）当正文下载下来的情况。
#
# 用法：
#   uv run --with pypdf python verify_pdfs.py [目录]
#   目录缺省为 <WORKDIR>/<作者名>_Exports
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

import store                                        # noqa: E402
from logutil import get as get_log, workdir as _wd   # noqa: E402

# 正文页数低于此值时提示复核（但不判失败——Short Take / Letter / Correction
# 本身就是短件，流程允许「1 页且 ≥100 KB」）
SHORT_PAGES = 4


def main() -> int:
    wd = _wd()
    d = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else store.exports_dir(wd)
    log = get_log("verify_pdfs", wd=wd)

    pdfs = sorted(d.glob("*.pdf"))
    if not pdfs:
        print(f"✗ {d} 下没有 PDF")
        return 1

    bad, short = [], []
    print(f"{'文件':<74} {'页数':>6} {'大小':>10}  校验")
    print("-" * 110)
    for f in pdfs:
        ok, why = store.validate_pdf(f)
        match = re.search(r"pages=(\d+)", why)
        pages = match.group(1) if match else "?"
        n = int(pages) if match else None
        print(f"{f.name[:74]:<74} {pages:>6} {f.stat().st_size // 1024:>7} KB  "
              f"{'✅' if ok else '❌ ' + why}")
        if not ok:
            bad.append((f.name, why))
        elif n is not None and n < SHORT_PAGES and "Correction" not in f.name:
            short.append((f.name, n))

    print("-" * 110)
    print(f"合计 {len(pdfs)} 个 PDF | 校验不通过 {len(bad)} 个")
    if bad:
        for n, w in bad:
            log.warn(f"校验不通过: {n} ({w})")
    if short:
        print(f"\n⚠️ 页数偏少、建议人工确认是否为补充材料/图注页（{len(short)} 个）：")
        for n, k in short:
            print(f"   {n}  —— {k} 页")
        log.warn(f"{len(short)} 个文件页数 < {SHORT_PAGES}，需确认非补充材料")
    print("\n判读提示：Author Correction / Letter / Short Take 本身就是短件（正常）；")
    print("          其余「2 页却通过校验」的多半是 suppl_file（补充材料），")
    print("          应改名 【补充材料】… 保留并重抓正文。")
    log.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
