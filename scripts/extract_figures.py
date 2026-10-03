# -*- coding: utf-8 -*-
"""从 PDF 抽取内嵌位图；既支持兼容 JSON，也支持落地真实图片文件。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import sys


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
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


def extract(pdf: pathlib.Path, min_px: int = 120, max_px: int = 1600) -> list[dict]:
    from pypdf import PdfReader
    rd = PdfReader(str(pdf))
    figs, seen = [], set()
    # ★ 记录「因缺依赖而跳过的图像」。pypdf 的 page.images 需要 **Pillow**；
    #   缺 Pillow 时它抛 ImportError，若不记账就会与「PDF 本来没图」混淆。
    #   必须在循环**之前**初始化（否则空结果分支会 NameError）。
    _import_errors: list[str] = []
    for pno, page in enumerate(rd.pages, 1):
        try:
            imgs = list(page.images)
        except ImportError as e:
            # ★ 这里的 ImportError 就是「缺 Pillow」：pypdf 的 page.images
            #   需要 Pillow 才能解码。**必须记账**，否则外部只会看到
            #   「0 张图」，与「PDF 本来没图」无法区分。
            _import_errors.append(f"page{pno}: {type(e).__name__}: {e}")
            continue
        except Exception:
            continue
        for image in imgs:
            try:
                data, name = image.data, image.name
            except Exception:
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            ext = pathlib.Path(name).suffix.lower().lstrip(".") or "bin"
            known = {"png", "jpg", "jpeg", "gif", "bmp", "tiff", "jp2", "jpx", "jbig2"}
            if ext not in known:
                ext = "bin"
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "jp2": "image/jp2", "jpx": "image/jpx",
                    "jbig2": "image/jbig2"}.get(ext, f"image/{ext}")
            w = h = None
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    w, h = im.size
                    if min_px and (w < min_px or h < min_px):
                        continue
                    if max_px and max(w, h) > max_px:
                        scale = max_px / float(max(w, h))
                        im = im.convert("RGB").resize(
                            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=88, optimize=True)
                        data, mime, ext = buf.getvalue(), "image/jpeg", "jpg"
                        w, h = im.size
            except ImportError as e:
                # ★ 不能静默吞掉：pypdf 的 page.images
                #   缺 Pillow 时抛 ImportError，吞掉后外部只看到
                #   「0 张图」，无法区分「PDF 真没图」与
                #   「依赖没装」。这里计数并在结尾报出。
                _import_errors.append(f"{type(e).__name__}: {e}")
            except Exception:
                pass
            seen.add(digest)
            figs.append({"page": pno, "name": name, "w": w, "h": h,
                         "bytes": len(data), "mime": mime,
                         "label": f"图-page{pno}", "ref": f"p{pno}",
                         "ext": ext, "data": data})
    if not figs and _import_errors:
        # 空结果 + 有导入错误 = 依赖缺失，不是 PDF 没图
        raise RuntimeError(
            "抽取为空且存在导入错误（多半是缺 Pillow）："
            + "; ".join(sorted(set(_import_errors))[:2]))
    return sorted(figs, key=lambda f: (f["page"], -(f["w"] or 0) * (f["h"] or 0)))


def save_records(records: list[dict], out_dir: pathlib.Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, rec in enumerate(records, 1):
        suffix = rec.get("ext") or "png"
        path = out_dir / f"p{rec['page']:03d}_img{i:03d}.{suffix}"
        path.write_bytes(rec["data"])
        manifest.append({k: rec.get(k) for k in
                         ("page", "name", "w", "h", "bytes", "mime", "label", "ref")} |
                        {"path": str(path)})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: extract_figures.py PDF OUT.json [min_px] [max_px]")
        return 1
    src, out = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
    min_px = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    max_px = int(sys.argv[4]) if len(sys.argv) > 4 else 1600
    try:
        records = extract(src, min_px, max_px)
    except ImportError:
        print("✗ 需要 pypdf：uv run --with pypdf python extract_figures.py ...")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [{k: v for k, v in r.items() if k != "data"} |
               {"b64": base64.b64encode(r["data"]).decode("ascii")} for r in records]
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {src.name} → {out.name}；抽取图像 {len(records)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
