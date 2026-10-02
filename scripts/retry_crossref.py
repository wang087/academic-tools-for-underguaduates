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

import json, urllib.request, urllib.parse, html, re, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = json.load(open(str(CDPDIR / "crossref_all.json"), encoding="utf-8"))
CACHE = str(CDPDIR / "crossref_all.json")

def strip_tags(s):
    if not s:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", s))

def fetch(doi):
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    req = urllib.request.Request(url, headers={
        "User-Agent": "ResearchAgent/2.0 (mailto:research@example.org; +86-000)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        m = json.load(r)["message"]
    dp = (m.get("issued") or {}).get("date-parts") or [[None]]
    yr = dp[0][0] if dp and dp[0] else ""
    authors = []
    for a in m.get("author", []):
        fam = strip_tags(a.get("family", ""))
        giv = strip_tags(a.get("given", ""))
        authors.append({"family": fam, "given": giv, "name": (fam + ", " + giv).strip(", ")})
    return {
        "doi": doi.lower(),
        "title": strip_tags((m.get("title") or [""])[0]),
        "journal": strip_tags((m.get("container-title") or [""])[0]),
        "short_journal": strip_tags((m.get("short-container-title") or [""])[0]) if m.get("short-container-title") else "",
        "year": yr,
        "vol": m.get("volume", ""),
        "issue": m.get("issue", ""),
        "page": m.get("page", ""),
        "article_number": m.get("article-number", ""),
        "type": m.get("type", ""),
        "authors": authors,
    }

fails = [d for d, r in DATA.items() if "error" in r]
print("to retry:", len(fails))
# 大写DOI转小写再试
normalized = []
for d in fails:
    ld = d.lower()
    normalized.append(ld)
    if ld != d:
        # 更新缓存中的键
        DATA[ld] = DATA.pop(d)

for d in normalized:
    if d in DATA and "error" not in DATA[d]:
        continue
    for attempt in range(3):
        try:
            r = fetch(d)
            DATA[d] = r
            print("OK", d)
            break
        except Exception as e:
            wait = 3 * (attempt + 1)
            print("retry", d, attempt, str(e)[:60], f"(wait {wait}s)")
            time.sleep(wait)

with open(CACHE, "w", encoding="utf-8") as f:
    json.dump(DATA, f, ensure_ascii=False, indent=1)
ok = sum(1 for r in DATA.values() if "error" not in r)
print(f"final ok={ok}/{len(DATA)}")
print("still failing:", [d for d, r in DATA.items() if "error" in r])
