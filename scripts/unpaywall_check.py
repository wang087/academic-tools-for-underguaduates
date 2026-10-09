# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 可移植配置：设置环境变量 PAPER_WORKDIR 指向作者工作目录，
# 未设置时使用当前工作目录。所有中间数据存于 <WORKDIR>/.work_cdp/
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys

from logutil import run_root as _run_root  # noqa: E402
WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
AUTHOR = os.environ.get("PAPER_AUTHOR", WORKDIR.name)
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import json, urllib.request, urllib.parse, sys, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DOIS = sorted(json.load(open(str(CDPDIR / "crossref_all.json"), encoding="utf-8")).keys())

def check(doi):
    url = "https://api.unpaywall.org/v2/" + urllib.parse.quote(doi) + "?email=research@example.org"
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0 (mailto:research@example.org)"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        best = d.get("best_oa_location") or {}
        return {"doi": doi, "is_oa": bool(d.get("is_oa")),
                "host": best.get("host_type"), "url_pdf": best.get("url_for_pdf"),
                "landing": best.get("url"), "version": best.get("version")}
    except Exception as e:
        return {"doi": doi, "is_oa": False, "error": str(e)[:60]}

out = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(check, d): d for d in DOIS}
    for f in as_completed(futs):
        r = f.result()
        out[r["doi"]] = r

with open(str(CDPDIR / "unpaywall.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

oa = [d for d, r in out.items() if r.get("is_oa")]
print("total:", len(out), "| OA:", len(oa))
for d in sorted(oa):
    r = out[d]
    print(" -", d, "|", r.get("host"), "|", (r.get("url_pdf") or "")[:80])
