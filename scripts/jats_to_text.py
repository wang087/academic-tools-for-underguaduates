# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# JATS XML → 结构化纯文本（供中译使用）
#
# 用途：Europe PMC 的开放获取全文以 JATS XML 提供
# （https://www.ebi.ac.uk/europepmc/webservices/rest/<PMCID>/fullTextXML）。
# 当出版社 PDF 受反爬阻拦、而该文在 PMC 有开放获取全文时，这条路径可拿到
# **完整正文（含章节层级、图注、表注）**，用于生成中译。
#
# 注意：这是「开放获取全文的元数据/正文接口」，不是绕过付费墙；
# 仅对 inEPMC=Y 的条目有效，非 OA 文章会返回 404。
#
# 用法：
#   uv run python jats_to_text.py <file.xml> [...]
#   输出同名 .txt（章节以 # 层级标记，便于逐段翻译）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

def _fix_streams() -> None:
    """UTF-8 兜底（**必须幂等**）。

    本模块会被别的脚本 `import`（如 refs_backfill.enrich_reviews 复用它的
      JATS 解析）。无条件重包 stdout 会让**上一层 wrapper 在 GC 时关掉底层 buffer**，
      调用方后续所有 print 直接抛 "ValueError: I/O operation on closed file" ——
      而且是"import 一个工具模块把主流程搞挂"这种最难查的形态。
      与 logutil._fix_stdout() 同判据：已是 UTF-8 就不再包。
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
            setattr(sys, name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()


def de_tags(s: str) -> str:
    """去残余标签 + 清理 xref 被剥离后留下的空括号/多余空格。"""
    s = re.sub(r"<[^>]{1,60}>", "", s or "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(\s*[,;]?\s*\)", "", s)        # ( ) / ( , )
    s = re.sub(r"\(\s*(,\s*)+\)", "", s)
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def node_text(el, drop=("xref", "fn", "table-wrap-foot")) -> str:
    parts = []
    if el.text:
        parts.append(el.text)
    for ch in el:
        tag = ch.tag.split("}")[-1]
        if tag in drop:
            if ch.tail:
                parts.append(ch.tail)
            continue
        parts.append(node_text(ch, drop))
        if ch.tail:
            parts.append(ch.tail)
    return "".join(parts)


def _local(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _child(el, name):
    return next((x for x in list(el) if _local(x) == name), None)


def _desc(el, name):
    return next((x for x in el.iter() if _local(x) == name), None)


def _descs(el, name):
    return [x for x in el.iter() if _local(x) == name]


def _walk(sec, acc, level):
    ttl = _child(sec, "title")
    title = de_tags(node_text(ttl)) if ttl is not None else ""
    paras = []
    for ch in sec:
        tag = ch.tag.split("}")[-1]
        if tag in ("title", "sec"):
            continue
        if tag in ("p", "list", "disp-quote", "statement"):
            txt = de_tags(node_text(ch))
            if txt:
                paras.append(txt)
        elif tag in ("fig", "table-wrap"):
            lab, cap = _child(ch, "label"), _child(ch, "caption")
            s = ((de_tags(node_text(lab)) if lab is not None else "") + " " +
                 (de_tags(node_text(cap)) if cap is not None else "")).strip()
            if s:
                paras.append(f"【图表】{s}")
    if title or paras:
        acc.append({"level": level, "title": title, "paras": paras})
    for ch in sec:
        if _local(ch) == "sec":
            _walk(ch, acc, level + 1)


def parse_xml(xml: str | bytes) -> dict:
    root = ET.fromstring(xml)
    return _parse_root(root)


def parse(path: pathlib.Path) -> dict:
    return _parse_root(ET.parse(str(path)).getroot())


def _parse_root(root) -> dict:
    art = _desc(root, "article-meta")
    out = {"sections": [], "title": "", "abstract": "", "authors": [], "journal": "",
           "year": "", "volume": "", "issue": "", "fpage": "", "doi": "", "refs": []}

    t = _desc(root, "article-title")
    out["title"] = de_tags(node_text(t)) if t is not None else ""
    jt = _desc(root, "journal-title")
    if jt is None:
        jt = _desc(root, "abbrev-journal-title")
    out["journal"] = de_tags(node_text(jt)) if jt is not None else ""

    if art is not None:
        for tag in ("volume", "issue", "fpage"):
            e = _desc(art, tag)
            if e is not None and e.text:
                out[tag] = e.text.strip()
        for pd in _descs(art, "pub-date"):
            y = _child(pd, "year")
            if y is not None and y.text:
                out["year"] = y.text.strip()
                break
        for aid in _descs(art, "article-id"):
            if aid.get("pub-id-type") == "doi" and aid.text:
                out["doi"] = aid.text.strip()
        for a in _descs(art, "contrib"):
            if a.get("contrib-type") not in (None, "author"):
                continue
            sn, gn = _desc(a, "surname"), _desc(a, "given-names")
            nm = " ".join(x.text.strip() for x in (gn, sn) if x is not None and x.text)
            if nm:
                out["authors"].append(nm)
        ab = _desc(art, "abstract")
        if ab is not None:
            # 摘要常带 <title>（Summary/Abstract），其文本会与正文粘连
            # （"SummaryThe recent..."）→ 取 title 后在正文前补空格
            atitle = _child(ab, "title")
            atxt = de_tags(node_text(atitle)) if atitle is not None else ""
            body_txt = de_tags(node_text(ab, drop=("xref", "title")))
            out["abstract"] = (f"{atxt} {body_txt}".strip() if atxt else body_txt)

    body = _desc(root, "body")
    if body is not None:
        for sec in body:
            if sec.tag.split("}")[-1] == "sec":
                _walk(sec, out["sections"], 1)

    rl = _desc(root, "ref-list")
    if rl is not None:
        for ref in [x for x in rl if _local(x) == "ref"]:
            out["refs"].append(de_tags(node_text(ref, drop=("xref",)))[:300])
    out["fpage"] = out.pop("fpage", "") or out.get("fpage", "")
    return out


def to_text(xml: str | bytes) -> str:
    """兼容旧调用方：从 XML 字符串返回结构化纯文本。"""
    d = parse_xml(xml)
    lines = [f"题名: {d['title']}", f"期刊: {d['journal']} {d['year']}",
             f"DOI: {d['doi']}", "", "摘要:", d["abstract"], "", "正文:"]
    for sec in d["sections"]:
        lines.append("#" * sec["level"] + " " + sec["title"])
        lines.extend(sec["paras"])
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__ or "用法: uv run python jats_to_text.py <file.xml> [...]")
        return 1
    rc = 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        if not p.exists():
            print(f"[X] 不存在: {p}")
            rc = 1
            continue
        d = parse(p)
        out = p.with_suffix(".txt")
        L = [f"题名: {d['title']}",
             f"期刊: {d['journal']} {d['year']};{d.get('volume','')}({d.get('issue','')}):{d.get('fpage','')}",
             f"DOI: {d['doi']}", f"作者: {', '.join(d['authors'])}", "",
             "摘要:", d["abstract"], "", "=" * 70, "正文:", "=" * 70]
        for s in d["sections"]:
            L.append("")
            L.append("#" * s["level"] + " " + s["title"])
            L.extend(s["paras"])
        out.write_text("\n".join(L), encoding="utf-8")
        nch = sum(len(x["paras"]) for x in d["sections"])
        nchar = sum(len("".join(x["paras"])) for x in d["sections"])
        print(f"[OK] {p.name} → {out.name} | 章节 {len(d['sections'])} | 段落 {nch} | "
              f"正文 {nchar} 字 | 参考文献 {len(d['refs'])}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
