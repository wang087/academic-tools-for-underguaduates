# -*- coding: utf-8 -*-
"""测试：Europe PMC fullTextXML + PMC eFetch 能否取到全文正文。

关键判据（照 paper-lookup/references/pmc.md 的 eFetch 陷阱）：
**必须确认存在 <body>**，否则只是元数据，不能声称"拿到全文"。
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

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.dirname(HERE))

UA = "flow1-stageR-fulltext/1.0 (mailto:researcher@example.org)"
probe = {r["slug"]: r for r in json.load(open(".work_cdp/fulltext_probe.json", encoding="utf-8"))}


def get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()


def body_stats(xml: str):
    """返回 (has_body, n_sec, n_p, text_chars)。"""
    has_body = "<body" in xml
    if not has_body:
        return False, 0, 0, 0
    body = xml.split("<body", 1)[1]
    n_sec = body.count("<sec")
    n_p = body.count("<p>") + body.count("<p ")
    txt = re.sub(r"<[^>]+>", " ", body)
    txt = re.sub(r"\s+", " ", txt)
    return True, n_sec, n_p, len(txt)


def try_epmc_fulltext(pmcid):
    st, body = get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")
    txt = body.decode("utf-8", "replace")
    if st != 200:
        return {"http": st, "ok": False}
    hb, ns, np_, nc = body_stats(txt)
    return {"http": st, "ok": hb, "secs": ns, "paras": np_, "chars": nc, "bytes": len(body)}


def try_pmc_efetch(pmcid):
    num = pmcid.replace("PMC", "")
    st, body = get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={num}&retmode=xml")
    txt = body.decode("utf-8", "replace")
    if st != 200:
        return {"http": st, "ok": False}
    hb, ns, np_, nc = body_stats(txt)
    restricted = "does not allow downloading of the full text" in txt
    return {"http": st, "ok": hb, "secs": ns, "paras": np_, "chars": nc, "bytes": len(body),
            "publisher_restricted": restricted}


def try_epmc_pdf(pmcid):
    st, body = get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextPDF", timeout=90)
    return {"http": st, "bytes": len(body), "is_pdf": body[:5] == b"%PDF-"}


rows = []
for slug, rec in sorted(probe.items()):
    pmcid = rec.get("pmcid")
    print("=" * 78)
    print(f"{rec['doi']}  {rec['title'][:55]}")
    row = {"slug": slug, "doi": rec["doi"], "pmcid": pmcid}
    if not pmcid:
        print("  无 PMCID，跳过")
        row["verdict"] = "no-pmcid"
        rows.append(row)
        continue
    e = try_epmc_fulltext(pmcid)
    row["epmc_fulltextXML"] = e
    print(f"  EPMC fullTextXML : http={e['http']} body={e.get('ok')} secs={e.get('secs')} paras={e.get('paras')} chars={e.get('chars')}")
    time.sleep(0.5)
    p = try_pmc_efetch(pmcid)
    row["pmc_efetch"] = p
    print(f"  PMC eFetch       : http={p['http']} body={p.get('ok')} secs={p.get('secs')} paras={p.get('paras')} chars={p.get('chars')} restricted={p.get('publisher_restricted')}")
    time.sleep(0.5)
    pdf = try_epmc_pdf(pmcid)
    row["epmc_fullTextPDF"] = pdf
    print(f"  EPMC fullTextPDF : http={pdf['http']} bytes={pdf['bytes']} is_pdf={pdf['is_pdf']}")
    if e.get("ok"):
        row["verdict"] = "skill-fulltext-xml"
    elif pdf["is_pdf"]:
        row["verdict"] = "skill-fulltext-pdf"
    else:
        row["verdict"] = "needs-cdp"
    time.sleep(0.5)
    rows.append(row)

json.dump(rows, open(".work_cdp/fulltext_attempt.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\n" + "=" * 78)
print("判决汇总：")
for r in rows:
    print(f"  {r['verdict']:24} {r['doi']}")
print("\n写出 .work_cdp/fulltext_attempt.json")
