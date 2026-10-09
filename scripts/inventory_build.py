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
    """统一三种顶层形状 → 记录列表，并**保证每条记录带 doi**。

      ① `{doi: 记录}`   —— `crossref_all.json` 的实际形态：**doi 只在键上，
         记录体里没有 doi**。原来直接丢弃键名，于是每篇的 doi 都是空串，
         `pdf_index` / `fulltext_state` / 三层通道产物**一条也匹配不上**，
         清单会把「已拿到全文」整批报成「未获取」。
      ② `{"records": […]}` / `{…: […]}` —— paginate 与阶段 R 的产物形态。
      ③ `[记录, …]`。
    """
    if isinstance(container, dict):
        for key in ("records", "result", "items"):
            if isinstance(container.get(key), list):
                return [r for r in container[key] if isinstance(r, dict)]
        out = []
        for k, v in container.items():
            if isinstance(v, dict):
                rec = dict(v)
                # 键名像 DOI（含 `/`）才当 doi 用，避免把普通字段名误当标识
                if not rec.get("doi") and "/" in str(k):
                    rec["doi"] = k
                out.append(rec)
        return out
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

    # 题录总表：优先 crossref_all.json（作者链路的总表），回退 master.json（早期案例历史名）。
    # 入门链路（N→S→R→R2）没有 crossref_all.json，其总表是阶段 R 的
    #   `.work_cdp/reviews/reviews.json`（最终选定的综述清单）—— 只认前两者时，
    #   12 篇里仅有 6 篇被并入过 crossref_all.json，清单会漏报覆盖面。
    cfile = next((p for p in (src / "crossref_all.json", src / "master.json")
                  if p.exists()), None)
    master = _records(json.loads(cfile.read_text(encoding="utf-8"))) if cfile else []
    rfile = src / "reviews" / "reviews.json"
    if rfile.exists():
        seen = {(r.get("doi") or "").lower() for r in master}
        extra = [r for r in _records(json.loads(rfile.read_text(encoding="utf-8")))
                 if (r.get("doi") or "").lower() and (r.get("doi") or "").lower() not in seen]
        master += extra
    if not master:
        print(f"[X] 没有可用题录：既无 {src}/crossref_all.json（或 master.json），"
              f"也无 {rfile}")
        print("  作者链路请先跑 fetch_crossref_all.py；入门链路请先跑 reviews_prepare.py。")
        return 1
    idx = json.loads((src / "pdf_index.json").read_text(encoding="utf-8")) \
        if (src / "pdf_index.json").exists() else {}
    state = json.loads((src / "fulltext_state.json").read_text(encoding="utf-8")) \
        if (src / "fulltext_state.json").exists() else {}
    exports = store.exports_dir(wd)

    # ── 全文 ≠ 只有 PDF ──────────────────────────────────────────
    #   阶段 R①.5 的三条通道里只有第二层-A 会落 PDF，另两条拿到的是**网页正文**：
    #     第一层   EPMC fullTextXML        → reviews_layer1.json（has_body / chars）
    #     第二层-A 出版社落地页（CDP）      → fulltext_state.json（fulltext_chars / pdf_*）
    #     第二层-B PMC 仓库副本（CDP）      → reviews_layer2_repo.json（path / chars）
    #   只认 PDF 会把「正文已到手」的篇目报成「未获取」——
    #   实测 12 篇综述 12/12 有正文，旧判据却报「已获全文 0 篇 / 未获取 6 篇」。
    def _jlist(name):
        p = src / name
        if not p.exists():
            return []
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        return d if isinstance(d, list) else []

    layer1 = {(r.get("doi") or "").lower(): r for r in _jlist("reviews_layer1.json")
              if isinstance(r, dict) and r.get("has_body")}
    repo = {(r.get("doi") or "").lower(): r for r in _jlist("reviews_layer2_repo.json")
            if isinstance(r, dict) and r.get("path")}
    tasks_dir = src / "reviews" / "tasks"
    tasks = {}
    if tasks_dir.is_dir():
        for f in tasks_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(t, dict) and t.get("doi"):
                tasks[t["doi"].lower()] = t

    def fulltext_for(doi):
        """返回 (正文字符数, 来源通道)；四条候选取**字数最大**的那条，不少报也不虚报。"""
        cands = []
        l1 = layer1.get(doi)
        if l1:
            cands.append((int(l1.get("chars") or 0), "第一层 EPMC fullTextXML"))
        st = state.get(doi) or {}
        if st.get("fulltext_chars"):
            cands.append((int(st["fulltext_chars"]), "第二层-A 出版社落地页（经 CDP）"))
        rp = repo.get(doi)
        if rp:
            cands.append((int(rp.get("chars") or 0), "第二层-B PMC 仓库副本（经 CDP）"))
        t = tasks.get(doi) or {}
        if t.get("evidence_source") == "fulltext" and t.get("text"):
            cands.append((len(t["text"]), "任务包 text 字段"))
        cands = [c for c in cands if c[0] > 0]
        return max(cands) if cands else (0, "")

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
        chars, route = fulltext_for(doi)
        rows.append({
            "doi": doi, "year": int(rec["year"]) if str(rec.get("year", "")).isdigit() else 0,
            "journal": rec.get("journal") or rec.get("journal_abbr") or "",
            "title": rec.get("title") or "",
            "pos": rec.get("author_position"), "n": rec.get("n_authors"),
            "file": name, "pages": pages_of(p) if exists else None,
            "size": (p.stat().st_size // 1024) if exists else None,
            "text_chars": chars, "route": route,
            "state": (state.get(doi) or {}).get("state"),
            "phase": rec.get("phase") or "",
            "reason": reason_for(doi, rec),
        })
    rows.sort(key=lambda x: (x["year"], x["journal"]))

    got = [r for r in rows if r["pages"] or r["text_chars"]]
    # 作者名不硬编码、也不假定「工作目录名 == 作者名」：
    # 优先 PAPER_AUTHOR，其次 PAPER_EXPORTS_NAME，最后回退工作目录名。
    author = (os.environ.get("PAPER_AUTHOR", "").strip()
              or os.environ.get("PAPER_EXPORTS_NAME", "").strip()
              or wd.name)
    n_pdf = sum(1 for r in rows if r["pages"])
    n_text = sum(1 for r in rows if r["text_chars"])
    kind = "综述" if (tasks or layer1 or repo) else "论文"
    # 抬头：没有作者语境（入门链路 N→S→R→R2）时，标题不该显示成日期目录名。
    #   目录名仍按上面的兜底（`store._exports_candidates()` 的既有行为），
    #   标题改用 topic_plan 的学部 + 用户选定方向。
    title_owner = author
    if not (os.environ.get("PAPER_AUTHOR", "").strip()
            or os.environ.get("PAPER_EXPORTS_NAME", "").strip()):
        tp = src / "topic_plan.json"
        if tp.exists():
            try:
                _p = json.loads(tp.read_text(encoding="utf-8"))
                title_owner = " ".join(x for x in (((_p.get("unit") or "").strip()),
                                                   ((_p.get("direction_chosen") or "").strip())) if x)
            except Exception:
                pass
        title_owner = title_owner or author
    L = [f"# {title_owner} {kind}全文获取清单\n",
         f"> 统计：共 {len(rows)} 篇 ｜ **拿到全文 {len(got)} 篇**"
         f"（其中正文 {n_text} 篇、PDF {n_pdf} 件）｜ 未获取 "
         f"{len(rows) - len(got)} 篇（覆盖率 {len(got) / max(len(rows), 1) * 100:.0f}%）",
         "> 获取途径：§5.1 **分层获取策略** —— 摘要/题录走第一层直连，"
         "**原文全文一律经 CDP + 真实 Edge**（多实例），遵守 CDP 强制抓取与鼠标自动化两条禁令",
         f"> 文件命名：`{exports.name}/<年份>_<论文题名>.pdf`"
         "（见 `.work_cdp/pdf_index.json` 登记表）；"
         "网页正文落在 `.work_cdp/fulltext_xml/`、`.work_cdp/fulltext/` 或任务包的 `fulltext_path`\n",
         "---\n"]

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
        L.append("| # | 年份 | 刊物 | 论文题名 | 署名 | 页数 | 大小 | 正文 | 全文 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(grp, 1):
            p, n = r["pos"], r["n"]
            postag = ("第一作者" if p == 1 else
                      "末位作者" if p and p == n else
                      f"第{p}/{n}" if p else "—")
            full_tag = ("[OK] PDF" if r["pages"] else
                        "[OK] 正文" if r["text_chars"] else "[X]")
            L.append(f"| {i} | {r['year']} | {r['journal'][:28]} | {r['title'][:64]} | "
                     f"{postag} | {r['pages'] or '—'} | "
                     f"{str(r['size']) + ' KB' if r['size'] else '—'} | "
                     f"{str(r['text_chars']) + ' 字' if r['text_chars'] else '—'} | "
                     f"{full_tag} |")
        L.append("")

    miss = [r for r in rows if not (r["pages"] or r["text_chars"])]
    routes = {}
    for r in got:
        key = r["route"] or ("第二层-A 出版社落地页（经 CDP，PDF）" if r["pages"] else "未标注")
        routes[key] = routes.get(key, 0) + 1
    L.append("---\n\n## 全文来源通道\n")
    L.append("| 通道 | 篇数 |")
    L.append("|---|---|")
    for k, v in sorted(routes.items(), key=lambda kv: -kv[1]):
        L.append(f"| {k} | {v} |")
    L.append("")

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
    with_pdf = [r for r in got if r["pages"]]
    if not with_pdf:
        L.append("本次**没有 PDF 落盘**，全文以网页正文形式取得 —— 无页数可核。\n")
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
        if n_pdf:
            L.append(f"已落盘的 {n_pdf} 件 PDF 页数均 ≥4，未见异常短件。\n")
        else:
            L.append("本次**没有 PDF 落盘** —— 全文均以网页正文取得，无页数可核。\n")

    out = exports / "全文清单.md"
    out.write_text("\n".join(L), encoding="utf-8")
    log.ok(f"已生成 {out}")
    print(f"   共 {len(rows)} 篇 | 拿到全文 {len(got)} 篇"
          f"（正文 {n_text} 篇 / PDF {n_pdf} 件）| 未获取 {len(rows) - len(got)} 篇")
    for r in miss:
        print(f"   [X] {r['year']} {r['journal'][:24]} — {r['title'][:56]}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
