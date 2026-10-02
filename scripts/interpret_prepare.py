# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H ①：为并行 subagent 组装**自包含**的「单篇解读任务包」
#
# 为什么需要这一步：
#   解读一篇论文要通读全文（1–4 万字符）。把全文直接粘进 subagent 提示词会
#   撑爆主 agent 的上下文；让 subagent 自己去翻 PDF 又要它解析文件、失败率高。
#   折中做法就是这里：**预先**把「题录 + 摘要 + 已抽取的正文文本」固化成
#   一个 JSON 任务包，subagent 拿到即可开读，不必碰网络、不必解析 PDF。
#
# ★ 本脚本**只读本地文件**，不发起任何网络请求（含元数据 API）——
#   摘要取自既有产物，全文取自主流程已下载的 PDF。符合 CDP 强制抓取禁令。
#
# 输入（均在 <WORKDIR>/.work_cdp/）：
#   crossref_all.json（或 master.json）—— 题录总表
#   abstracts_openalex.json（可选）      —— 摘要语料
#   disambiguated.json（可选）           —— 同名他人排除名单（scope.py）
#   .work_cdp/pdf_index.json（可选）     —— DOI → 文件名 登记表
# 全文 PDF：<WORKDIR>/<作者名>_paper_Exports/*.pdf
#
# 产出：
#   <WORKDIR>/.work_cdp/interpret/tasks/<NN>_<doi-slug>.json   单篇任务包
#   <WORKDIR>/.work_cdp/interpret/manifest.json                任务清单 + 统计
#
# 用法：
#   uv run --with pypdf python interpret_prepare.py
#     --max-chars=60000      单篇正文截断上限（超出即截断并标注）
#     --only=<DOI,DOI>       只准备指定 DOI
#     --no-text              只出题录与摘要（不读 PDF）
#     --limit=N              只准备前 N 篇（按年份升序）
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
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import scope                                       # noqa: E402
import store                                       # noqa: E402
import extract_figures                             # noqa: E402

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WD / ".work_cdp"
OUTDIR = CDPDIR / "interpret"
TASKDIR = OUTDIR / "tasks"
RESDIR = OUTDIR / "results"
FULLDIR = OUTDIR / "fulltext"        # 超长论文的完整文本（任务包只带「头+尾」）
FIGDIR = pathlib.Path(os.environ.get("PAPER_INTERPRET_FIGURES_DIR", str(OUTDIR / "figures")))


def _arg(name: str, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def load_records() -> dict:
    """题录总表 → {doi: rec}（兼容 {doi:rec} 与 [rec,…] 两种形状）。"""
    for n in ("crossref_all.json", "master.json"):
        p = CDPDIR / n
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {str(k).lower(): v for k, v in d.items() if isinstance(v, dict)}
        if isinstance(d, list):
            return {str(r.get("doi", "")).lower(): r
                    for r in d if isinstance(r, dict) and r.get("doi")}
    return {}


def load_abstracts() -> dict:
    """摘要语料 → {doi: 摘要文本}。形状不确定，做多种容错。"""
    p = CDPDIR / "abstracts_openalex.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            k = str(k).lower()
            if isinstance(v, str):
                out[k] = v
            elif isinstance(v, dict):
                out[k] = (v.get("abstract") or v.get("abstractText")
                          or v.get("text") or "")
    return {k: v for k, v in out.items() if v}


def pdf_text(path: pathlib.Path, max_chars: int):
    """抽取 PDF 正文并在后端失败时回退 PyMuPDF。

    返回 `(packet, full, pages, truncated, omitted, status, backend, errors)`。
    """
    errors = []
    backend = "pypdf"
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path), strict=False)
        n = len(r.pages)
        buf = []
        for i, pg in enumerate(r.pages, 1):
            try:
                t = (pg.extract_text() or "").strip()
            except Exception as e:
                errors.append(f"pypdf:p{i}:{type(e).__name__}")
                t = ""
            if t:
                buf.append(f"\n\n===== [p.{i}] =====\n{t}")
        full = "".join(buf)
    except Exception as e:
        errors.append(f"pypdf:{type(e).__name__}:{str(e)[:80]}")
        n, full = 0, ""

    # 扫描件、字体映射损坏或 pypdf 空结果：使用 PyMuPDF 重新提取。
    if not full.strip() or full.count("�") > max(20, len(full) // 100):
        try:
            import fitz
            doc = fitz.open(str(path))
            n = len(doc)
            pages = []
            for i, page in enumerate(doc, 1):
                try:
                    t = (page.get_text("text") or "").strip()
                except Exception as e:
                    errors.append(f"fitz:p{i}:{type(e).__name__}")
                    t = ""
                if t:
                    pages.append(f"\n\n===== [p.{i}] =====\n{t}")
            candidate = "".join(pages)
            if len(candidate) > len(full):
                full, backend = candidate, "pymupdf"
        except Exception as e:
            errors.append(f"fitz:{type(e).__name__}:{str(e)[:80]}")
    status = "ok" if full.strip() else ("failed" if errors else "empty")
    if len(full) <= max_chars:
        return full, full, n, False, 0, status, backend, errors
    text, omitted = head_tail(full, max_chars)
    return text, full, n, True, omitted, status, backend, errors


def head_tail(full: str, max_chars: int, sidecar_note: str = ""):
    """超出上限时**保留开头与结尾**，中间以省略标记衔接。返回 (text, omitted)。

    ★ 为什么不能只截开头：论文最关键的内容 —— **结论、量化数据、
      作者自述局限与展望** —— 全部落在结尾的 Results / Discussion /
      Conclusion。旧版从头部硬截，恰好把最该读的部分丢掉。
      实测 2026-09-18：13 篇里 5 篇被截断，解读卡只能对前 6 万字符负责，
      而它们的结论段根本没进入任务包。
    ★ 为什么不干脆放大上限：任务包要整体塞进 subagent 的上下文，
      无限放大会挤掉其它内容、拉低解读质量。头 60% + 尾 40% 是
      「既读到主旨、也读到结论」的折中；被略去的中间段多为方法与
      中间结果，且完整文本另有 sidecar 可查（见下）。
    """
    head_n = int(max_chars * 0.6)
    tail_n = max(0, max_chars - head_n)
    omitted = max(0, len(full) - head_n - tail_n)
    marker = ("\n\n===== [中间省略 {n} 字符{src}；"
              "如需该部分请读取任务包中的 fulltext_path 指向的完整文本] =====\n\n").format(
        n=omitted, src=("；完整文本：" + sidecar_note) if sidecar_note else "")
    return full[:head_n] + marker + (full[-tail_n:] if tail_n else ""), omitted


def gbt_authors(authors) -> str:
    """GB/T 7714：>3 位取前 3 位 + et al."""
    names = []
    for a in authors or []:
        if isinstance(a, dict):
            n = a.get("name") or ", ".join(
                x for x in (a.get("family"), a.get("given")) if x)
        else:
            n = str(a)
        if n:
            names.append(n)
    if not names:
        return ""
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + ", et al."


def my_position(authors, tokens) -> str:
    if not authors or not tokens:
        return ""
    names = []
    for a in authors or []:
        if isinstance(a, dict):
            names.append((a.get("name") or "").lower())
        else:
            names.append(str(a).lower())
    hits = [i for i, n in enumerate(names) if any(t in n for t in tokens)]
    if not hits:
        return ""
    i, n = hits[0], len(names)
    if i == 0:
        return "第一作者"
    if i == n - 1:
        return "末位作者（通常为通讯作者）"
    return f"第{i + 1}位作者"


def main() -> int:
    max_chars = int(_arg("max-chars", "60000") or 60000)
    only = [d.strip().lower() for d in (_arg("only", "") or "").split(",") if d.strip()]
    no_text = "--no-text" in sys.argv
    limit = int(_arg("limit", "0") or 0)

    if not (CDPDIR / "crossref_all.json").exists() and not (CDPDIR / "master.json").exists():
        print("✗ 找不到题录总表 .work_cdp/crossref_all.json")
        print("  请先运行 fetch_crossref_all.py。")
        return 2

    recs = load_records()
    abstracts = load_abstracts()
    exports = store.exports_dir(WD)

    # 同名他人：与 build_list.py 共用 scope.py 的**肯定性判定**排除名单
    exc = scope.excluded_dois(CDPDIR, total=len(recs)) or set()
    if exc:
        recs = {d: r for d, r in recs.items() if d not in exc}
        print(f"ℹ 已排除同名他人论文 {len(exc)} 条（见 disambiguated.json）")

    tokens = [t.strip().lower() for t in
              os.environ.get("PAPER_NAME_TOKENS", "").split("|") if t.strip()]
    if not tokens:
        sn = os.environ.get("PAPER_SURNAME", "").strip().lower()
        gn = os.environ.get("PAPER_GIVEN", "").strip().lower()
        if sn and gn:
            tokens = [f"{sn}, {gn}", f"{gn} {sn}"]

    # ★ 缺年份要先报警：题录一旦缺 year，会排到 9999 而改变整体顺序。
    #   实测 2026-09-18：一次 Crossref 重采有 2 条失败未补，year 为空 →
    #   顺序整体错位 → 旧版「序号_DOI」命名下 13 份解读卡有 9 份对不上号。
    #   （文件名改成 DOI 命名后已不会错位，但缺年份本身仍是题录缺陷，需修。）
    _noyear = [d for d, r in recs.items() if not r.get("year")]
    if _noyear:
        print(f"⚠️ {len(_noyear)} 条题录**缺发表年份**，会影响排序与阶段分组。"
              f"建议先跑 retry_crossref.py 补全：{', '.join(_noyear[:3])}"
              f"{' …' if len(_noyear) > 3 else ''}")

    # 按发表年份升序 —— 解读报告要求「依照发表先后顺序串联要点」
    items = sorted(recs.items(), key=lambda kv: (
        int(kv[1].get("year") or 9999), str(kv[1].get("title") or "")))
    if only:
        items = [(d, r) for d, r in items if d in set(only)]
    if limit > 0:
        items = items[:limit]

    TASKDIR.mkdir(parents=True, exist_ok=True)
    RESDIR.mkdir(parents=True, exist_ok=True)

    manifest, n_full, n_abs, seq = [], 0, 0, 0
    for doi, rec in items:
        seq += 1
        year = rec.get("year") or ""
        title = rec.get("title") or ""
        # ★ 文件名**只由 DOI 决定**（稳定、与排序无关）；序号只写在 JSON 内部。
        #   旧版用「序号_DOI」命名，一旦排序发生变化 —— 例如某条 Crossref 记录
        #   暂时缺年份而被排到末尾 —— 同一个文件名就会指向**另一篇论文**。
        #   实测 2026-09-18：重跑一次 prepare 后，13 份已产出的解读卡里有 9 份
        #   与 manifest 对不上号；轻则整片报「缺卡」，重则**把 A 的解读卡
        #   当成 B 的结果**（build 是按文件名取卡的）。改用 DOI 命名后，
        #   重跑只会覆盖同名文件，永远不会错位。
        stem = store.doi_slug(doi) if doi else store.doi_slug(title)[:60]
        taskp = TASKDIR / f"{stem}.json"
        resp = RESDIR / f"{stem}.json"

        text, full, pages, trunc, omitted = "", "", 0, False, 0
        extraction_status, extraction_backend, extraction_errors = "not-run", "", []
        pdfname, fullpath = "", ""
        figure_dir, figures_manifest = "", []
        if not no_text:
            p = store.existing_pdf(doi, year, WD, title)
            if p is not None:
                pdfname = p.name
                (text, full, pages, trunc, omitted, extraction_status,
                 extraction_backend, extraction_errors) = pdf_text(p, max_chars)
                # 将候选数据图落盘为真实文件，任务包只携带元数据，避免 base64 膨胀。
                try:
                    target = FIGDIR / stem
                    figures_manifest = extract_figures.save_records(
                        extract_figures.extract(p), target)
                    figure_dir = str(target) if figures_manifest else ""
                except Exception as e:
                    print(f"⚠️ 图片抽取失败 {doi}: {str(e)[:100]}")
                if trunc:
                    # ★ 完整文本另存 sidecar：任务包只带「头 + 尾」，
                    #   被略去的中间段仍可查，subagent 必要时分段读取。
                    FULLDIR.mkdir(parents=True, exist_ok=True)
                    fp = FULLDIR / f"{stem}.txt"
                    fp.write_text(full, encoding="utf-8")
                    fullpath = str(fp)

        abstract = abstracts.get(doi, "") or rec.get("abstract") or ""
        src = "fulltext" if text.strip() else "abstract"
        if src == "fulltext":
            n_full += 1
        else:
            n_abs += 1

        packet = {
            "seq": seq,
            "doi": doi,
            "title": title,
            "journal": rec.get("journal") or rec.get("short_journal") or "",
            "year": year,
            "authors": gbt_authors(rec.get("authors")),
            "my_position": my_position(rec.get("authors"), tokens),
            "abstract": abstract,
            "pdf": pdfname,
            "pages": pages,
            "evidence_source": src,
            "text_extraction_status": extraction_status,
            "text_extraction_backend": extraction_backend,
            "text_extraction_errors": extraction_errors,
            "text_chars": len(text),
            "text_truncated": trunc,
            "text_omitted_chars": omitted,
            "fulltext_chars": len(full),
            # 完整文本路径（仅在被截断时非空）；任务包已含头+尾，此处供按需深读
            "fulltext_path": fullpath,
            "figure_dir": figure_dir,
            "figures_manifest": figures_manifest,
            "figure_count": len(figures_manifest),
            "text": text,
            # 供 subagent 直接写结果用（绝对路径，避免它自己拼错）
            "result_path": str(resp),
        }
        taskp.write_text(json.dumps(packet, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest.append({
            "seq": seq, "doi": doi, "year": year, "title": title[:90],
            # ★ v7：补 slug 字段，与 refs_prepare.py 的 manifest 对齐。
            #   下游（自检、人工核对、跨阶段脚本）一律按**文件名 stem** 定位产物，
            #   而不是去解析 task/result 的完整路径 —— 路径会随工作目录变化，stem 不会。
            "slug": stem,
            "task": str(taskp), "result": str(resp),
            "evidence_source": src,
            "text_extraction_status": extraction_status,
            "text_extraction_backend": extraction_backend,
            "text_extraction_errors": extraction_errors,
            "text_chars": len(text),          # 任务包内的**正文**长度（不含摘要）
            "abstract_chars": len(abstract),  # 摘要长度
            "text_truncated": trunc, "text_omitted_chars": omitted,
            "fulltext_chars": len(full), "fulltext_path": fullpath,
            "pages": pages,
            "figure_dir": figure_dir,
            "figures_manifest": figures_manifest,
            "figure_count": len(figures_manifest),
        })

    # ── 陈旧文件检查：tasks/ 与 results/ 里不属于本轮的文件 ──────────
    #   ★ 不静默忽略。尤其 results/ 里的「孤儿卡」意味着上一轮的解读成果
    #     与当前题录对不上 —— 必须让人看见，而不是让它悄悄错位。
    cur_tasks = {pathlib.Path(m["task"]).name for m in manifest}
    cur_results = {pathlib.Path(m["result"]).name for m in manifest}
    stale_tasks = sorted(p.name for p in TASKDIR.glob("*.json")
                         if p.name not in cur_tasks)
    orphan_results = sorted(p.name for p in RESDIR.glob("*.json")
                            if p.name not in cur_results)

    (OUTDIR / "manifest.json").write_text(
        json.dumps({"workdir": str(WD), "exports": str(exports),
                    "max_chars": max_chars, "count": len(manifest),
                    "fulltext": n_full, "abstract_only": n_abs,
                    "tasks": manifest}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    n_trunc = sum(1 for m in manifest if m["text_truncated"])
    # ★ 「无米下锅」= 任务包里**既没有正文、也没有摘要**。
    #   注意不能只看 text_chars：那是**正文**长度，没有全文的篇目天然为 0，
    #   但它可能带着完整的摘要 —— 只判 text_chars 会误报一片「摘要也为空」。
    n_empty = sum(1 for m in manifest
                  if not m["text_chars"] and not m["abstract_chars"])
    print(f"\n✅ 已生成 {len(manifest)} 个任务包 → {TASKDIR}")
    print(f"   全文级 {n_full} 篇 ｜ 仅摘要 {n_abs} 篇")
    if n_trunc:
        print(f"   ℹ {n_trunc} 篇正文超长：任务包**保留开头 60% + 结尾 40%**"
              f"（结论/局限都在结尾，不再被截掉），中间省略部分另存 → {FULLDIR}")
    if n_abs:
        print("   ⚠️ 仅摘要的篇目在最终报告中会被如实标注；"
              "如需全文请回主流程 §4 阶段 E 用 CDP 补下。")
    if n_empty:
        print(f"   ⚠️ {n_empty} 篇**摘要也为空**（既无全文、又无摘要语料）→ "
              f"subagent 将无米下锅。建议先跑 fetch_openalex_abstracts.py 补摘要。")
    if stale_tasks:
        print(f"   ℹ 清理陈旧任务包 {len(stale_tasks)} 个（不属于本轮题录）："
              f"{', '.join(stale_tasks[:4])}{' …' if len(stale_tasks) > 4 else ''}")
        for n in stale_tasks:
            try:
                (TASKDIR / n).unlink()
            except Exception:
                pass
    if orphan_results:
        print(f"   ⚠️ results/ 里有 {len(orphan_results)} 份**孤儿解读卡**"
              f"（文件名与本轮任务对不上，不会被汇总采用）："
              f"{', '.join(orphan_results[:4])}{' …' if len(orphan_results) > 4 else ''}")
        print("      多为上一轮命名规则不同所致。若内容仍有效，"
              "按 DOI 改名即可复用；否则删除后重新分派。")
    print(f"   清单：{OUTDIR / 'manifest.json'}")
    print("\n下一步：同一条消息内并行分派 4–6 个 subagent，"
          "每个只读自己的 task.json、只写自己的 result.json")
    print("（提示词模板见 流程1/subagent-driven-review/SKILL.md § 7）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
