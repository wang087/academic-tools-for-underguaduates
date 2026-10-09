# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 主图裁剪：按页面内**栅格图像**的联合包围盒裁出「图-only」区域（阶段 F）
#
# 为什么这样做：Nature/Cell 系主图是**多张位图拼版**，页面上的 drawings
# 绝大多数是文字与线条（坐标轴标签、标尺），直接按 drawings 聚类会把
# 正文误判为图区。而位图 bbox 的并集恰好覆盖整幅主图，且不含正文文字，
# 因此是「既不缺图、也不混入正文」的稳妥切法。
#
# 用法：
#   uv run --with pymupdf python crop_by_images.py <pdf> <out.json> <页码,逗号分隔> [dpi] [pad]
#   页码取「图注页的前一页」（见 prep_paper.py 输出的图注页索引）
#
# 产出的 JSON 每项含 page / w / h / rect / mime / b64，可直接喂给
# translation_build.py 内联进中译 HTML。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import base64
import io
import json
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__ or "用法: uv run --with pymupdf python crop_by_images.py "
                         "<pdf> <out.json> <页码,逗号分隔> [dpi] [pad]")
        return 1
    try:
        import fitz
    except ImportError:
        print("[X] 需要 PyMuPDF：uv run --with pymupdf python crop_by_images.py ...")
        return 1

    src = pathlib.Path(sys.argv[1]).resolve()
    out = pathlib.Path(sys.argv[2]).resolve()
    pages = [int(x) - 1 for x in sys.argv[3].split(",") if x.strip()]
    dpi = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    pad = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0

    doc = fitz.open(str(src))
    results = []
    for pi in pages:
        if pi < 0 or pi >= len(doc):
            continue
        page = doc[pi]
        W, H = page.rect.width, page.rect.height
        boxes = []
        for im in page.get_image_info():
            b = fitz.Rect(*im["bbox"])
            if b.width < 18 or b.height < 18:          # 图标
                continue
            if b.width > W * 0.98 and b.height < 30:   # 页眉页脚细长横幅
                continue
            boxes.append(b)
        if not boxes:
            print(f"  第{pi + 1}页: 无位图，跳过（该页可能是纯矢量图，改用 render_pages.py 整页渲染）")
            continue
        u = boxes[0]
        for b in boxes[1:]:
            u |= b
        u = fitz.Rect(max(0, u.x0 - pad), max(0, u.y0 - pad),
                      min(W, u.x1 + pad), min(H, u.y1 + pad))
        if u.width < 80 or u.height < 80:
            print(f"  第{pi + 1}页: 位图区域过小 ({u.width:.0f}x{u.height:.0f})，跳过")
            continue
        pix = page.get_pixmap(clip=u, dpi=dpi, colorspace=fitz.csRGB)
        data = pix.tobytes("jpeg", jpg_quality=90)
        results.append({"page": pi + 1, "n_images": len(boxes),
                        "w": pix.width, "h": pix.height,
                        "rect": [round(v, 1) for v in u], "bytes": len(data),
                        "mime": "image/jpeg",
                        "label": f"主图-page{pi + 1}", "ref": f"p{pi + 1}",
                        "b64": base64.b64encode(data).decode("ascii")})
        print(f"  第{pi + 1}页: 位图 {len(boxes)} 个 → 裁剪 {pix.width}x{pix.height} "
              f"{len(data) // 1024} KB  rect={[round(v) for v in u]}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {len(results)} 图 → {out.name}（{out.stat().st_size // 1024} KB）")
    if results:
        print("   在内容 JSON 里用 ref=\"" + results[0]["ref"] + "\" 这类值引用对应图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
