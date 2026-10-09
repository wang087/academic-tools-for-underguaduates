# -*- coding: utf-8 -*-
"""按 §5.1 分层策略，测量各篇综述在**第一层白名单**内的全文可获得性。

边界（依 templates/直连白名单.json）：
  www.ebi.ac.uk          content_class = "abstract+oa-fulltext-xml", oa_only=true
      -> fullTextXML **只在 inEPMC=Y 且 isOpenAccess=Y 时**可用
  eutils.ncbi.nlm.nih.gov content_class = "abstract"
      -> 只允许取摘要；eFetch 的 <body> 全文**越界**，不在此列

于是「skill 能合规拿到全文」= EPMC fullTextXML 有 <body>。
其余一律转 CDP（第二层）。
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n, None)
    _b = getattr(_s, "buffer", None)
    if _b is not None and "utf8" not in (getattr(_s, "encoding", "") or "").lower().replace("-", ""):
        try:
            setattr(sys, _n, io.TextIOWrapper(_b, encoding="utf-8", errors="replace"))
        except Exception:
            pass

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
UA = "flow1-stageR-layer1/1.0 (mailto:researcher@example.org)"


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()


probe = {r["slug"]: r for r in json.load(open(".work_cdp/fulltext_probe.json", encoding="utf-8"))}
rows = []
for slug, rec in sorted(probe.items()):
    ep = rec.get("epmc") or {}
    pmcid = rec.get("pmcid")
    in_epmc = ep.get("inEPMC")
    is_oa = ep.get("isOpenAccess")
    row = {"slug": slug, "doi": rec["doi"], "title": rec["title"], "pmcid": pmcid,
           "inEPMC": in_epmc, "isOpenAccess": is_oa,
           "unpaywall_status": (rec.get("unpaywall") or {}).get("oa_status")}
    layer1 = False
    chars = 0
    if pmcid and in_epmc == "Y" and is_oa == "Y":
        st, body = get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")
        if st == 200:
            txt = body.decode("utf-8", "replace")
            if "<body" in txt:
                layer1 = True
                b = txt.split("<body", 1)[1]
                chars = len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b)))
    row["layer1_fulltext"] = layer1
    row["layer1_chars"] = chars
    row["verdict"] = "skill(第一层)" if layer1 else "needs-cdp(第二层)"
    rows.append(row)
    print(f"{row['verdict']:20} {rec['doi']:34} inEPMC={in_epmc} isOA={is_oa} chars={chars}")

json.dump(rows, open(".work_cdp/layer_split.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
n1 = sum(1 for r in rows if r["layer1_fulltext"])
print(f"\n第一层可合规取到全文：{n1}/{len(rows)}")
print(f"需转 CDP（第二层）：{len(rows) - n1}/{len(rows)}")
for r in rows:
    if not r["layer1_fulltext"]:
        print(f"   -> CDP: {r['doi']}  ({r['title'][:52]})")
