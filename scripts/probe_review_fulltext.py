# -*- coding: utf-8 -*-
"""阶段 R"skill 优先"全文可达性探测。

只用**元数据 API**（Crossref / Unpaywall / PMC ID Converter / PMC OA /
Europe PMC fullTextXML 可用性），不抓取任何 PDF 正文 —— 这样既符合
「CDP 强制抓取禁令」（禁令只针对内容抓取，元数据 API 仍允许），
又能为后续决策提供依据。

输出 .work_cdp/fulltext_probe.json
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
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
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)

UA = "flow1-stageR-fulltext-probe/1.0 (mailto:researcher@example.org)"
EMAIL = "researcher@example.org"


def get(url, timeout=30, accept=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()


def jget(url, timeout=30):
    st, body = get(url, timeout)
    if st != 200:
        return None, st, body[:200].decode("utf-8", "replace")
    try:
        return json.loads(body.decode("utf-8", "replace")), st, ""
    except Exception as e:  # noqa: BLE001
        return None, st, f"json-error {e}"


def unpaywall(doi):
    q = urllib.parse.urlencode({"email": EMAIL})
    return jget(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?{q}")


def idconv(doi):
    q = urllib.parse.urlencode({"ids": doi, "format": "json", "tool": "flow1", "email": EMAIL})
    return jget(f"https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?{q}")


def pmc_oa(pmcid):
    st, body = get(f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}")
    txt = body.decode("utf-8", "replace")
    if st != 200:
        return {"http": st, "error": txt[:200]}
    if "<error" in txt:
        import re
        m = re.search(r'code="([^"]+)"', txt)
        return {"http": st, "error_code": m.group(1) if m else "unknown"}
    import re
    m = re.search(r'format="([^"]+)"[^>]*href="([^"]+)"', txt)
    lic = re.search(r'license="([^"]*)"', txt)
    return {"http": st, "in_oa_subset": True, "format": m.group(1) if m else None,
            "href": m.group(2) if m else None, "license": lic.group(1) if lic else None}


def epmc_doi(doi):
    q = urllib.parse.urlencode({"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 1})
    return jget(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{q}")


def main():
    tasks = sorted(glob.glob("outputs/03_领域概貌/tasks/*.json"))
    out = []
    for t in tasks:
        d = json.load(open(t, encoding="utf-8"))
        doi = d["doi"]
        rec = {"slug": os.path.basename(t)[:-5], "doi": doi, "title": d["title"], "task_is_oa": d.get("is_oa")}
        print("=" * 78)
        print(f"{doi}  {d['title'][:58]}")

        # 1) Unpaywall
        u, st, err = unpaywall(doi)
        if u:
            best = u.get("best_oa_location") or {}
            rec["unpaywall"] = {
                "is_oa": u.get("is_oa"), "oa_status": u.get("oa_status"),
                "pdf": best.get("url_for_pdf"), "landing": best.get("url_for_landing_page"),
                "host": best.get("host_type"), "version": best.get("version"),
                "license": best.get("license"),
                "n_locations": len(u.get("oa_locations") or []),
            }
            print(f"  unpaywall : is_oa={u.get('is_oa')} status={u.get('oa_status')} pdf={'Y' if best.get('url_for_pdf') else 'N'}")
        else:
            rec["unpaywall"] = {"error": f"http {st} {err}"}
            print(f"  unpaywall : FAIL http={st} {err}")

        # 2) PMC ID converter
        ic, st2, err2 = idconv(doi)
        pmcid = pmid = None
        if ic and ic.get("records"):
            r0 = ic["records"][0]
            pmcid = r0.get("pmcid")
            pmid = r0.get("pmid")
        rec["pmcid"] = pmcid
        rec["pmid"] = pmid
        print(f"  pmc idconv: pmcid={pmcid} pmid={pmid}")

        # 3) PMC OA subset
        if pmcid:
            rec["pmc_oa"] = pmc_oa(pmcid)
            print(f"  pmc oa    : {rec['pmc_oa']}")
        else:
            rec["pmc_oa"] = None

        # 4) Europe PMC core (has pmcid / fulltext flags)
        e, st3, err3 = epmc_doi(doi)
        if e and e.get("resultList", {}).get("result"):
            rr = e["resultList"]["result"][0]
            rec["epmc"] = {
                "pmcid": rr.get("pmcid"), "pmid": rr.get("pmid"),
                "isOpenAccess": rr.get("isOpenAccess"),
                "inEPMC": rr.get("inEPMC"), "hasTextMinedTerms": rr.get("hasTextMinedTerms"),
                "hasPDF": rr.get("hasPDF"), "citedByCount": rr.get("citedByCount"),
                "pubType": rr.get("pubTypeList", {}).get("pubType"),
            }
            print(f"  epmc      : pmcid={rr.get('pmcid')} isOA={rr.get('isOpenAccess')} inEPMC={rr.get('inEPMC')} hasPDF={rr.get('hasPDF')} cited={rr.get('citedByCount')}")
        else:
            rec["epmc"] = {"error": f"http {st3} {err3}"}
            print(f"  epmc      : FAIL http={st3}")
        out.append(rec)
        time.sleep(0.4)

    json.dump(out, open(".work_cdp/fulltext_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n写出 .work_cdp/fulltext_probe.json")


if __name__ == "__main__":
    main()
