# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 中译 HTML 生成器（阶段 F）
#
# 把「结构化中文内容 JSON」+「图形 JSON」拼成**带内联图**的 HTML，
# 再交给 render_html_pdf.py 渲染为 PDF。
#
# 为什么图要 base64 内联：headless Chrome 渲染本地 HTML 时，相对路径图片
# 常因 file:// 安全策略或工作目录变化而断链；内联后单文件自包含，最稳。
#
# ── 内容 JSON 结构 ───────────────────────────────────────────────
# {
#   "meta": {"title_zh","title_en","authors_zh","affiliation","journal",
#            "year","vol","issue","pages","doi","oa_note"},
#   "abstract_zh": "...",
#   "sections": [
#     {"level":2, "title":"1 引言", "blocks":[
#         {"t":"p",    "v":"段落中文译文"},
#         {"t":"h3",   "v":"2.1 小节标题"},
#         {"t":"fig",  "ref":"p3", "v":"图注中文译文"},   # ref 对应图形 JSON 的 label/ref
#         {"t":"note", "v":"译者注：……"}
#     ]}
#   ],
#   "refs_note": "..."
# }
#
# 图形 JSON：由 extract_figures.py / crop_by_images.py / render_pages.py 产出，
# 每项含 page / label / ref / mime / b64。
#
# 用法：
#   uv run python translation_build.py <content.json> <figures.json> <out.html>
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import html as _h
import io
import json
import pathlib
import sys


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与 render_html_pdf.py 等同名函数逐字一致）。

    为什么不能像旧版那样**无条件** `sys.stdout = io.TextIOWrapper(...)`：
      本模块会被阶段 F 的其它脚本 import。无条件替换会叠加第二层 wrapper，
      中间层被 GC 时把底层 buffer 一起关掉 —— 之后所有 print 抛
      "ValueError: I/O operation on closed file"，且只在特定 import 顺序下复现。
      先看编码、已是 UTF-8 就跳过，才不会叠加。
    """
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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── 日志（**文件型**：console=False）────────────────────────────
#   本脚本的 stdout 是阶段 F 的操作反馈与末行汇总，日志**不写控制台**，
#   只落 `.work_cdp/logs/translation_build_<时间戳>.log` 并追加操作流水。
try:
    from logutil import get as _get_log, journal as _journal  # type: ignore
except Exception:                      # 单文件运行时也要能用
    def _get_log(name, **kw):          # noqa: D103
        class _L:
            def info(self, *a, **k): pass
            def ok(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass
            def close(self): pass
        return _L()

    def _journal(*a, **k):             # noqa: D103
        pass

CSS = """
  @page { size: A4; margin: 1.8cm 2.0cm; }
  body { font-family: "Times New Roman","SimSun","Songti SC","Microsoft YaHei",serif;
         font-size: 10.5pt; line-height: 1.75; color:#111; }
  h1 { font-size: 16pt; line-height:1.5; margin: 0 0 10px; }
  h2 { font-size: 13pt; border-bottom: 1.5px solid #333; padding-bottom:4px; margin-top:22px; }
  h3 { font-size: 11.5pt; margin-top:16px; color:#1a1a1a; }
  .meta { color:#444; font-size:10pt; background:#fafafa; border:1px solid #e5e5e5;
          padding:10px 12px; margin-bottom:14px; }
  .abstract { background:#f2f2f2; border-left:4px solid #888; padding:10px 14px; }
  .figbox { margin: 14px 0; page-break-inside: avoid; }
  .figbox img { width:100%; max-width:100%; border:1px solid #ddd; }
  .figcap { background:#f7f7f7; border:1px solid #ddd; border-left:4px solid #999;
            padding:8px 12px; margin-top:6px; font-size:9.5pt; line-height:1.6; }
  .figcap b { color:#000; }
  .refnote { font-size:9pt; color:#555; margin-top:18px; border-top:1px solid #ddd; padding-top:8px; }
  .note { background:#fff8e1; border-left:3px solid #e0a800; padding:6px 10px;
          font-size:9.5pt; margin:8px 0; }
  sup, sub { font-size:70%; }
  table { border-collapse: collapse; width:100%; font-size:9.5pt; margin:8px 0; }
  th, td { border:1px solid #bbb; padding:4px 6px; }
"""


def esc(s) -> str:
    return _h.escape(str(s or ""))


def _run(log) -> int:
    content = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    figs_path = pathlib.Path(sys.argv[2])
    figs = json.loads(figs_path.read_text(encoding="utf-8")) if figs_path.exists() else []
    out = pathlib.Path(sys.argv[3])
    log.info(f"构建中译 HTML: {sys.argv[1]} + {figs_path.name} → {out}")

    # ref/label → 图；同时支持 "#N" 与 "p<页码>" 定位
    fmap = {}
    for i, f in enumerate(figs):
        for k in (f.get("label"), f.get("ref"), f"#{i + 1}", f"p{f.get('page')}"):
            if k:
                fmap.setdefault(str(k), f)

    m = content.get("meta", {}) or {}
    L = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
         f"<title>中文翻译 · {esc(m.get('title_en'))}</title>",
         f"<style>{CSS}</style></head><body>",
         f"<h1>{esc(m.get('title_zh'))}</h1>",
         "<div class='meta'>",
         f"<b>英文原题：</b>{esc(m.get('title_en'))}<br>",
         f"<b>作者：</b>{esc(m.get('authors_zh'))}<br>",
         f"<b>单位：</b>{esc(m.get('affiliation'))}<br>"]
    cite = esc(m.get("journal")) + ", " + esc(m.get("year"))
    if m.get("vol"):
        cite += ", " + esc(m.get("vol"))
    if m.get("issue"):
        cite += "(" + esc(m.get("issue")) + ")"
    if m.get("pages"):
        cite += ": " + esc(m.get("pages"))
    L.append(f"<b>期刊出处：</b>{cite}. DOI: {esc(m.get('doi'))}{esc(m.get('oa_note'))}")
    L.append("</div>")
    if content.get("abstract_zh"):
        L.append(f"<p class='abstract'><b>摘要（译）：</b>{esc(content['abstract_zh'])}</p>")

    nfig = 0
    for sec in content.get("sections", []) or []:
        lv = int(sec.get("level", 2))
        if sec.get("title"):
            L.append(f"<h{lv}>{esc(sec['title'])}</h{lv}>")
        for b in sec.get("blocks", []) or []:
            t = b.get("t", "p")
            if t == "h3":
                L.append(f"<h3>{esc(b.get('v'))}</h3>")
            elif t == "note":
                L.append(f"<div class='note'>{esc(b.get('v'))}</div>")
            elif t == "fig":
                ref = str(b.get("ref") or "")
                f = fmap.get(ref)
                cap = b.get("v") or (f or {}).get("caption") or ""
                if f:
                    nfig += 1
                    L.append("<div class='figbox'>")
                    L.append(f"<img src='data:{f.get('mime', 'image/jpeg')};base64,{f['b64']}'>")
                    L.append(f"<div class='figcap'><b>{esc(ref)}</b> {esc(cap)}</div></div>")
                else:
                    # 未抽取到图像的图（Extended Data / 补充图 / 未纳入的主图），
                    # 只保留中文图注——中译文档「有图注、无图」属正常呈现，
                    # 不加警示语以免读者误以为翻译缺失。
                    L.append(f"<div class='figcap'><b>{esc(ref)}</b> {esc(cap)}</div>")
            else:
                L.append(f"<p>{esc(b.get('v'))}</p>")

    L.append(f"<p class='refnote'>{esc(content.get('refs_note') or '')}</p>")
    L.append("</body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    size_kb = out.stat().st_size // 1024
    nsec = len(content.get("sections", []) or [])
    log.ok(f"[OK] {out.name}（{size_kb} KB）| 嵌入图 {nfig}/{len(figs)} | 章节 {nsec}")
    _journal("F", "translation.html", out=str(out), size_kb=size_kb,
             figures_used=nfig, figures_total=len(figs), sections=nsec)
    print(f"[OK] {out.name}（{size_kb} KB）| 嵌入图 {nfig}/{len(figs)} "
          f"| 章节 {nsec}")
    if figs and nfig == 0:
        log.warn("有图形素材但一张都没匹配上（fig ref 与图形 JSON 的 label/ref 不一致？）")
        print("[WARN] 有图形素材但一张都没匹配上——检查 blocks 里 fig 的 ref 是否与图形 JSON 的 "
              "label/ref 一致（或先用 #1 / p3 这类定位）。")
    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__ or "用法: uv run python translation_build.py "
                         "<content.json> <figures.json> <out.html>")
        return 1
    log = _get_log("translation_build", console=False)
    try:
        return _run(log)
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
