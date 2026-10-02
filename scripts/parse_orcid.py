# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 可移植配置：设置环境变量 PAPER_WORKDIR 指向作者工作目录，
# 未设置时使用当前工作目录。所有中间数据存于 <WORKDIR>/.work_cdp/
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys
WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
AUTHOR = os.environ.get("PAPER_AUTHOR", WORKDIR.name)
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import re, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = str(CDPDIR / "orcid_works_parsed.json")

# ★ 输入文件名兼容：orcid_pages.py 产 orcid_works_all.txt（带 PAGE 分隔），
#   orcid_extract.py 产 orcid_page.txt。旧版只认前者，用另一个抓完就接不上。
SRC = None
for _name in ("orcid_works_all.txt", "orcid_page.txt"):
    if (CDPDIR / _name).exists():
        SRC = str(CDPDIR / _name)
        break
if SRC is None:
    sys.stderr.write(
        "✗ 找不到 ORCID 页面文本（orcid_works_all.txt / orcid_page.txt）。\n"
        "  请先运行 orcid_pages.py 或 orcid_extract.py。\n")
    sys.exit(2)
print("source:", SRC)

txt = open(SRC, encoding="utf-8").read()
pages = re.split(r"===== PAGE \d+ =====", txt)
recs, seen_doi = [], set()

DATE_RE = re.compile(r"^\d{4}(-\d{2})?(-\d{2})? \| ")
# 页面固定噪声词；**不含任何作者姓名**（ORCID 页会重复出现本人姓名行，
# 由环境变量 PAPER_AUTHOR_NAMES 提供，竖线分隔，例如 "<Given Family>|<Family GI>"）
SKIP = {"Show more detail", "Source", ":", "", "sort", "Sort", "Items per page:", "expand_more", "expand_less", "Preferred source (of 2)", "Preferred source (of 3)"}
SKIP |= {n.strip() for n in os.environ.get("PAPER_AUTHOR_NAMES", "").split("|") if n.strip()}

for page in pages:
    lines = page.splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.startswith("Works ("):
            start = i + 1
            break
    if start is None:
        continue
    i = start
    while i < len(lines):
        raw = lines[i].strip()
        if not raw:
            i += 1
            continue
        # 收集标题行
        title_lines = []
        j = i
        while j < len(lines):
            s = lines[j].strip()
            if not s:
                break
            if DATE_RE.match(s) or s.startswith("DOI:") or s.startswith("CONTRIBUTORS:") or s.startswith("Part of ISSN"):
                break
            if s in SKIP or s.startswith("Preferred source"):
                j += 1
                continue
            title_lines.append(s)
            j += 1
        # 跳过"书评/综述 UnknownSource"等无效条目
        date = ""
        if j < len(lines) and DATE_RE.match(lines[j].strip()):
            date = lines[j].strip()
            j += 1
        doi = ""
        if j < len(lines) and lines[j].startswith("DOI:"):
            doi = lines[j][4:].strip()
            j += 1
        contrib = []
        while j < len(lines):
            s = lines[j].strip()
            if s == "" or s.startswith("Part of ISSN") or s in SKIP or s.startswith("Preferred source"):
                j += 1
                continue
            if s.startswith("CONTRIBUTORS:"):
                contrib = [x.strip() for x in s[len("CONTRIBUTORS:"):].split(";") if x.strip()]
                j += 1
            break
        title = " ".join(title_lines).strip()
        if title and date and ("Journal article" in date or "Book" in date or "Review" in date or "Book review" in date or "Preprint" in date):
            key = doi or title[:60]
            if key not in seen_doi:
                seen_doi.add(key)
                m = re.match(r"^(\d{4})", date)
                recs.append({
                    "title": title,
                    "date": date,
                    "year": m.group(1) if m else "",
                    "doi": doi,
                    "contributors": contrib,
                })
        i = j if j > i else i + 1

# ── 兜底：页面文本解析不出成果时，回退到 ORCID 公共 API 的结果 ──────────
# ★ 为什么需要：ORCID 3.x 改版后成果面板**默认折叠**、卡片元素名也从
#   `app-work-statement` 变成 `app-work`，页面抓取可能得 0 条
#   （2026-09-18 用本流程实测复现）。而 orcid_probe.py 走的是公共 API，
#   与前端改版无关、始终可用 —— 不能因为站点改版就让整条链断掉。
# ★ 已知差异：API 结果**没有 CONTRIBUTORS 字段**，故本人署名位次改由
#   enrich_crossref_authors.py 从 Crossref 结构化作者回填（该步本就在流程里）。
if not recs:
    _probe = CDPDIR / "orcid_probe.json"
    if _probe.exists():
        try:
            _api = json.loads(_probe.read_text(encoding="utf-8"))
        except Exception:
            _api = {}
        for w in _api.get("works") or []:
            if not isinstance(w, dict) or not (w.get("title") or w.get("doi")):
                continue
            _y = str(w.get("year") or "")
            recs.append({
                "title": (w.get("title") or "").strip(),
                "date": _y,
                "year": _y,
                "doi": (w.get("doi") or "").strip(),
                "contributors": [],
                "_source": "orcid-api",
            })
        if recs:
            sys.stderr.write(
                f"⚠ 页面文本未解析出成果（ORCID 可能改版）→ 已回退 ORCID API："
                f"{len(recs)} 条（出处 orcid_probe.json）\n"
                f"  该结果不含 CONTRIBUTORS，署名位次将由 Crossref 回填。\n")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(recs, f, ensure_ascii=False, indent=1)
print("records:", len(recs))
for r in recs:
    print(f"{r['year']:6s} | {r['doi']:34s} | {r['title'][:70]}")
