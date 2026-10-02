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
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from logutil import get as get_log, journal          # noqa: E402

LOG = get_log("fetch_crossref_all", wd=WORKDIR)

dois = [l.strip() for l in open(str(CDPDIR / "dois.txt"), encoding="utf-8") if l.strip()]
OUT = str(CDPDIR / "crossref_all.json")
LOG.info(f"待核实 DOI {len(dois)} 个（来源 {CDPDIR / 'dois.txt'}）")
journal("D", "crossref.fetch.start", dois=len(dois), script="fetch_crossref_all.py")

def strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)

def fetch(doi):
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0 (mailto:research@example.org)", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            m = json.load(r)["message"]
        dp = (m.get("issued") or {}).get("date-parts") or [[None]]
        yr = dp[0][0] if dp and dp[0] else ""
        authors = []
        for a in m.get("author", []):
            fam = strip_tags(a.get("family", ""))
            giv = strip_tags(a.get("given", ""))
            # ★ 采集**逐作者单位**：同名消歧最强的判据是「署名单位指纹」，
            #   而此前根本没把 affiliation 取下来 —— 没有单位数据，
            #   disambiguate.py 的单位判据就永远无从施加（实测 2026-09-18 暴露：
            #   郭傲案例里 13 篇全部落到「数据缺失、无法判定」）。
            #   注意：不是所有出版社都向 Crossref 存缴单位，缺是常态，故用空列表兜底。
            affs = []
            for x in (a.get("affiliation") or []):
                if isinstance(x, dict):
                    nm = strip_tags(x.get("name", ""))
                    if nm:
                        affs.append(nm)
            authors.append({"family": fam, "given": giv,
                            "name": (fam + ", " + giv).strip(", "),
                            "affiliation": affs})
        return {
            "doi": doi,
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
    except Exception as e:
        return {"doi": doi, "error": str(e)}

results = {}
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(fetch, d): d for d in dois}
    for f in as_completed(futs):
        r = f.result()
        results[r["doi"]] = r

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)

ok = sum(1 for r in results.values() if "error" not in r)
err = [r["doi"] for r in results.values() if "error" in r]
LOG.info(f"Crossref 核实：成功 {ok}/{len(results)}")
if err:
    LOG.warn(f"失败 {len(err)} 条（交给 retry_crossref.py 串行退避重试）：{err}")
# 抽查
for k in list(results)[:3]:
    r = results[k]
    LOG.info(f" - {r.get('year')} {r.get('doi')} | {(r.get('title') or '')[:50]} | "
             f"{r.get('journal')} {r.get('vol')} {r.get('page')}")
journal("D", "crossref.fetch.done", total=len(results), ok=ok,
        failed=len(err), failed_dois=err, out=str(CDPDIR / "crossref_all.json"))
LOG.close()
