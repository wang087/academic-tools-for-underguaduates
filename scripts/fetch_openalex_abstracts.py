# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 用 OpenAlex 批量补全摘要（重建 abstract_inverted_index）
# 用途：出版社反爬（Cloudflare）拿不到摘要时的主力补充手段
# 用法: python fetch_openalex_abstracts.py
# 可移植配置：环境变量 PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys, json, io, urllib.request, urllib.parse, time
from concurrent.futures import ThreadPoolExecutor, as_completed

from logutil import run_root as _run_root  # noqa: E402

WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = CDPDIR / "abstracts_openalex.json"
CR = json.load(open(CDPDIR / "crossref_all.json", encoding="utf-8"))
DOIS = sorted(CR.keys())

# 已通过 CDP 抓到的摘要（优先保留）
try:
    CDP_ABS = json.load(open(CDPDIR / "abstracts.json", encoding="utf-8"))
except Exception:
    CDP_ABS = {}


def rebuild(inv):
    """OpenAlex abstract_inverted_index -> 原文"""
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[k] for k in sorted(pos))


def fetch(doi):
    url = "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi) + "?select=id,doi,title,abstract_inverted_index,publication_year,primary_location,biblio"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ResearchAgent/2.0 (mailto:research@example.org)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        return {
            "doi": doi,
            "title": d.get("title") or "",
            "year": d.get("publication_year"),
            "abstract": rebuild(d.get("abstract_inverted_index")),
            "source": "openalex",
        }
    except Exception as e:
        return {"doi": doi, "abstract": "", "error": str(e)[:60], "source": "openalex"}


results = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(fetch, d): d for d in DOIS}
    for f in as_completed(futs):
        r = f.result()
        results[r["doi"]] = r

# v7.1：先读回本文件里已有的结果，避免**覆盖别的源**。
#   起因：fetch_direct.py（第一层：S2 / Europe PMC / Crossref / PubMed / OpenAlex）
#   与本脚本写同一个产物文件。若本脚本无条件重写，用户先跑 fetch_direct 再跑本脚本时，
#   非 OpenAlex 的命中会被**静默抹掉** —— 表现为"摘要覆盖率莫名下降"。
try:
    PREV = json.load(open(OUT, encoding="utf-8"))
except Exception:
    PREV = {}

# 合并优先级：CDP 抓到 > 其它直连源（fetch_direct 的产物）> OpenAlex > 空
merged = {}
for d in DOIS:
    cdp_txt = ((CDP_ABS.get(d) or {}).get("abstract") or "").strip()
    prev = (PREV.get(d) or {})
    prev_src = prev.get("source") or ""
    prev_txt = (prev.get("abstract") or "").strip()
    oa_txt = (results.get(d, {}).get("abstract") or "").strip()
    if cdp_txt and len(cdp_txt) > 200 and "请稍候" not in cdp_txt:
        merged[d] = {"abstract": cdp_txt, "source": "cdp-publisher", "title": (CDP_ABS[d] or {}).get("title", "")}
    elif prev_txt and prev_src not in ("", "none", "openalex"):
        # 别的直连源已经拿到，保留它，别降级成 OpenAlex
        merged[d] = {"abstract": prev_txt, "source": prev_src,
                     "title": prev.get("title") or ""}
    elif oa_txt:
        merged[d] = {"abstract": oa_txt, "source": "openalex", "title": results[d].get("title", "")}
    elif prev_txt:
        merged[d] = {"abstract": prev_txt, "source": prev_src or "cached",
                     "title": prev.get("title") or ""}
    else:
        merged[d] = {"abstract": "", "source": "none", "title": (CR.get(d) or {}).get("title", "")}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=1)

have = sum(1 for v in merged.values() if v["abstract"])
print(f"total={len(merged)}  with_abstract={have}  missing={len(merged)-have}")
print("\n-- 仍缺摘要 --")
for d, v in sorted(merged.items()):
    if not v["abstract"]:
        print(" ", d, "|", (v.get("title") or "")[:60])
