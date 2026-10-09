# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 将 <作者>_Exports/ 下已下载的论文 PDF **重命名为其标题**
#
#   旧命名：<year>_<doi slug>.pdf      例：2025_10.1016_j.cell.2025.04.019.pdf
#   新命名：<year>_<论文标题>.pdf       例：2025_Near-infrared spatiotemporal color vision....pdf
#
# 题名来源优先级：crossref_all.json（权威题录） → fulltext_state.json（抓取时页面标题）
# 重命名后同步登记到 .work_cdp/pdf_index.json（DOI → 文件名），
# 保证 store.existing_pdf() 去重判断依然可靠（不会重复下载）。
#
# 用法：
#   uv run python rename_to_title.py --dry-run     # 预览将要发生的改名
#   uv run python rename_to_title.py               # 实际执行
#   uv run python rename_to_title.py --keep-dois   # 题名后保留 DOI 便于核对
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import io

from logutil import run_root as _run_root  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import store  # noqa: E402

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
EXPORTS = store.exports_dir(WD)

_YEAR_RE = re.compile(r"^(?:19|20)\d\d_(.+)$")


def norm_doi_key(name: str) -> str:
    """文件名剥掉年份前缀后，把 slug 还原成可比对的 DOI 形态。"""
    m = _YEAR_RE.match(name)
    return (m.group(1) if m else name)


def build_maps() -> tuple[dict, dict]:
    """(doi → {title, year}) 与 (doi_slug → doi) 两张表。"""
    info: dict[str, dict] = {}
    slug2doi: dict[str, str] = {}

    try:
        cr = json.loads((CDPDIR / "crossref_all.json").read_text(encoding="utf-8"))
    except Exception:
        cr = {}
    for doi, m in cr.items():
        if m.get("error"):
            continue
        info[doi.lower()] = {"title": m.get("title") or "", "year": m.get("year") or ""}
        slug2doi[store.doi_slug(doi).lower()] = doi.lower()

    # fulltext_state 补漏（crossref 缺题名时用抓取页标题）
    try:
        st = json.loads((CDPDIR / "fulltext_state.json").read_text(encoding="utf-8"))
    except Exception:
        st = {}
    for doi, m in st.items():
        d = doi.lower()
        if not (m or {}).get("pdf_ok"):
            continue
        cur = info.setdefault(d, {"title": "", "year": m.get("year") or ""})
        if not cur.get("title"):
            cur["title"] = m.get("title") or ""
        slug2doi.setdefault(store.doi_slug(doi).lower(), d)

    return info, slug2doi


def main() -> None:
    dry = "--dry-run" in sys.argv
    keep_doi = "--keep-dois" in sys.argv

    info, slug2doi = build_maps()
    idx = store.load_index(WD)

    pdfs = sorted(EXPORTS.glob("*.pdf"))
    renamed, skipped, unknown = [], [], []

    for p in pdfs:
        # ① 登记表已有该文件名 → 找回 DOI
        doi = next((d for d, n in idx.items() if n == p.name), None)
        # ② 文件名含 DOI slug → 找回 DOI
        if not doi:
            doi = slug2doi.get(norm_doi_key(p.stem).lower())
        # ③ DOI slug 出现在文件名任意位置（runs/前缀等）
        if not doi:
            low = p.stem.lower()
            doi = next((d for s, d in slug2doi.items() if s in low), None)

        if not doi or doi not in info:
            unknown.append(p.name)
            continue

        meta = info[doi]
        title = (meta.get("title") or "").strip()
        if not title:
            unknown.append(p.name)
            continue

        year = meta.get("year") or ""
        stem = store.stem_for(doi, year, title)
        if keep_doi:
            stem = f"{stem} [{store.doi_slug(doi)}]"
        target = EXPORTS / f"{stem}.pdf"

        if target.name == p.name:
            skipped.append(p.name)
            idx[doi] = p.name
            continue

        # 目标已存在（同一 DOI 的另一种命名）→ 删掉重复的旧文件，保留规范名
        if target.exists() and target.stat().st_size == p.stat().st_size:
            if not dry:
                p.unlink(missing_ok=True)
                idx[doi] = target.name
            renamed.append((p.name, target.name, "去重合并"))
            continue

        if dry:
            renamed.append((p.name, target.name, "预览"))
            continue

        try:
            p.rename(target)
            idx[doi] = target.name
            renamed.append((p.name, target.name, "已改"))
        except OSError as e:
            print(f"  [WARN] 改名失败 {p.name}: {e}")

    if not dry:
        store.save_index(idx, WD)

    print(f"== 题名重命名{'（预览）' if dry else ''} ==")
    for old, new, tag in renamed:
        print(f"  [{tag}] {old}\n         → {new}")
    if skipped:
        print(f"\n  已是题名命名，跳过 {len(skipped)} 个")
    if unknown:
        print(f"\n  [WARN] 无法匹配 DOI/题名，保持原样 {len(unknown)} 个：")
        for n in unknown:
            print(f"      {n}")
    print(f"\n合计：改名 {len(renamed)} | 跳过 {len(skipped)} | 未识别 {len(unknown)} "
          f"| 总计 {len(pdfs)}")
    if dry:
        print("（--dry-run 预览模式，未改动任何文件）")


if __name__ == "__main__":
    main()
