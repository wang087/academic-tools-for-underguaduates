# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 生成《全文清单.md》（阶段 I）
#
# 逐篇列出：全文状态、来源途径、页数、大小、署名位次、未获取原因，
# 并按阶段统计覆盖率；末尾附「页数合理性核查」。
#
# 输入（均在 <WORKDIR>/.work_cdp/）：
#   crossref_all.json —— 题录总表 {doi: 记录}（fetch_crossref_all.py 的产物）
#   master.json       —— 同上的历史形状 [记录, …]（两者都能读，优先前者）
#   pdf_index.json    —— DOI → 实际文件名 登记表（store.register_pdf 写入）
#   fulltext_state.json（可选）—— download_all_fulltexts 的逐篇状态
#   periods.json      （可选）—— 阶段分组 [["标签","说明",起,止], …]
#
# 产出：<WORKDIR>/<作者名>_paper_Exports/全文清单.md
#
# 用法：
#   uv run --with pypdf python inventory_build.py [--src 目录]
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import store                                        # noqa: E402
from logutil import get as log_get, workdir as _wd   # noqa: E402


def _records(container):
    """统一 {doi: 记录} 与 [记录, …] 两种顶层形状 → 记录列表。"""
    if isinstance(container, dict):
        return [v for v in container.values() if isinstance(v, dict)]
    if isinstance(container, list):
        return [r for r in container if isinstance(r, dict)]
    return []


def pages_of(p: pathlib.Path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(p)).pages)
    except Exception:
        return None


def main() -> int:
    wd = _wd()
    cdp = wd / ".work_cdp"
    src = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else cdp
    log = log_get("inventory_build", wd=wd)

    # 题录总表：优先 crossref_all.json（本流程**实际产出**的总表），
    # 回退 master.json（早期案例的历史名 —— 本目录任何脚本都不会生成它，
    #   旧版只认这个文件名，于是全新工作目录跑到这一步必然硬失败）。
    cfile = next((p for p in (src / "crossref_all.json", src / "master.json")
                  if p.exists()), None)
    if cfile is None:
        print(f"[X] 找不到题录总表：{src}/crossref_all.json（或 master.json）")
        print("  请先运行 fetch_crossref_all.py 产出 crossref_all.json。")
        return 1
    master = _records(json.loads(cfile.read_text(encoding="utf-8")))
    if not master:
        print(f"[X] {cfile.name} 里没有可用记录")
        return 1
    idx = json.loads((src / "pdf_index.json").read_text(encoding="utf-8")) \
        if (src / "pdf_index.json").exists() else {}
    state = json.loads((src / "fulltext_state.json").read_text(encoding="utf-8")) \
        if (src / "fulltext_state.json").exists() else {}
    exports = store.exports_dir(wd)

    # 未获取原因：优先用 fulltext_state 的判定，其次给出通用解释
    def reason_for(doi, rec):
        st = (state.get(doi) or {})
        s = st.get("state")
        if s == "challenge":
            return "遇站点人机验证（Cloudflare 等），宽限期内未自动放行；未做自动化绕过"
        if s == "blocked":
            return "站点封禁文案，按流程全局中止"
        if s == "paywall":
            return "付费墙，本机无该刊订阅授权"
        if doi.startswith("10.2139/"):
            return "SSRN：中国大陆 IP 段网络层不可达（detect.CN_INACCESSIBLE）"
        return "未获取（详见 .work_cdp/logs/ 对应日志）"

    rows = []
    for rec in master:
        doi = (rec.get("doi") or "").lower()
        name = idx.get(doi)
        p = exports / name if name else None
        exists = bool(p and p.exists())
        rows.append({
            "doi": doi, "year": int(rec["year"]) if str(rec.get("year", "")).isdigit() else 0,
            "journal": rec.get("journal") or rec.get("journal_abbr") or "",
            "title": rec.get("title") or "",
            "pos": rec.get("author_position"), "n": rec.get("n_authors"),
            "file": name, "pages": pages_of(p) if exists else None,
            "size": (p.stat().st_size // 1024) if exists else None,
            "state": (state.get(doi) or {}).get("state"),
            "phase": rec.get("phase") or "",
            "reason": reason_for(doi, rec),
        })
    rows.sort(key=lambda x: (x["year"], x["journal"]))

    got = [r for r in rows if r["pages"]]
    # 作者名不硬编码、也不假定「工作目录名 == 作者名」：
    # 优先 PAPER_AUTHOR，其次 PAPER_EXPORTS_NAME，最后回退工作目录名。
    author = (os.environ.get("PAPER_AUTHOR", "").strip()
              or os.environ.get("PAPER_EXPORTS_NAME", "").strip()
              or wd.name)
    L = [f"# {author} 论文全文获取清单\n",
         f"> 统计：共 {len(rows)} 篇 ｜ **已获全文 {len(got)} 篇** ｜ 未获取 "
         f"{len(rows) - len(got)} 篇（覆盖率 {len(got) / max(len(rows), 1) * 100:.0f}%）",
         "> 获取途径：CDP + 真实 Edge（多实例），遵守 **CDP 强制抓取禁令** 与 **鼠标自动化禁令**",
         f"> 文件命名：`{exports.name}/<年份>_<论文题名>.pdf`"
         "（见 `.work_cdp/pdf_index.json` 登记表）\n", "---\n"]

    # 阶段分组：与 build_list.py **同一数据源**（.work_cdp/periods.json）。
    # 旧版在此写死「2011–2018 早期 / 2022–2023 博后期 / 2025– 独立期」——
    #   那是上一案例的履历，换作者后分组标题直接是错的（违反 [版本变动.md](版本变动.md) 去硬编码）。
    periods = []
    pj = src / "periods.json"
    if pj.exists():
        try:
            periods = [(str(a), str(b), int(c), int(d))
                       for a, b, c, d in json.loads(pj.read_text(encoding="utf-8"))]
        except Exception as e:
            log.warn(f"periods.json 解析失败，改为不分组：{str(e)[:80]}")

    def bucket(r):
        if r["phase"]:
            return r["phase"]
        y = r["year"]
        if periods and y:
            for label, _desc, lo, hi in periods:
                if lo <= y <= hi:
                    return label
        return "全部成果（未配置阶段分组）"

    groups = {}
    for r in rows:
        groups.setdefault(bucket(r), []).append(r)
    for phase in sorted(groups, key=lambda k: min(x["year"] for x in groups[k])):
        grp = groups[phase]
        L.append(f"## {phase}\n")
        L.append("| # | 年份 | 刊物 | 论文题名 | 署名 | 页数 | 大小 | 全文 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(grp, 1):
            p, n = r["pos"], r["n"]
            postag = ("第一作者" if p == 1 else
                      "末位作者" if p and p == n else
                      f"第{p}/{n}" if p else "—")
            L.append(f"| {i} | {r['year']} | {r['journal'][:28]} | {r['title'][:64]} | "
                     f"{postag} | {r['pages'] or '—'} | "
                     f"{str(r['size']) + ' KB' if r['size'] else '—'} | "
                     f"{'[OK]' if r['pages'] else '[X]'} |")
        L.append("")

    miss = [r for r in rows if not r["pages"]]
    L.append("---\n\n## 未获取全文的论文及原因\n")
    if not miss:
        L.append("无。\n")
    else:
        for r in miss:
            L.append(f"- **{r['title'][:80]}**（{r['journal']}, {r['year']}）")
            L.append(f"  - DOI：`{r['doi']}`")
            L.append(f"  - 原因：{r['reason']}")
            L.append("  - 可补途径：机构 VPN / 图书馆文献传递，或人工在 Edge 中另存。")
        L.append("")

    L.append("---\n\n## 页数合理性核查\n")
    L.append("正文一般 ≥6 页；页数偏少者须排查是否为补充材料 / 图注页。\n")
    short = [r for r in got if r["pages"] and r["pages"] <= 3]
    if short:
        L.append("| 论文 | 页数 | 判断 |")
        L.append("|---|---|---|")
        for r in short:
            verdict = ("Correction / Letter 类短件（正常）"
                       if any(k in r["title"] for k in ("Correction", "Erratum", "Letter"))
                       else "**需人工确认**是否为补充材料")
            L.append(f"| {r['title'][:60]} | {r['pages']} | {verdict} |")
        L.append("")
    else:
        L.append("全部文件页数 ≥4，未见异常短件。\n")

    out = exports / "全文清单.md"
    out.write_text("\n".join(L), encoding="utf-8")
    log.ok(f"已生成 {out}")
    print(f"   共 {len(rows)} 篇 | 已获全文 {len(got)} 篇 | 未获取 {len(rows) - len(got)} 篇")
    for r in miss:
        print(f"   [X] {r['year']} {r['journal'][:24]} — {r['title'][:56]}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
