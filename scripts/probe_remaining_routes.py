# -*- coding: utf-8 -*-
# [诊断脚本] 非流程环节 —— 不参与任何阶段的正常执行。
#   `完整链路.md` / `docs/阶段R全文获取模块.md` / 《阶段R成果检查报告》/《流程1_核验检查报告_20261007》把它当"当时怎么定位原文获取缺陷"的取证脚本引用；
#   保留是为了"结论可重跑、可回查"，不是流程依赖。改流程逻辑时不要顺手改它。
"""复核剩余 6 篇的**其他第一层合法通道**（不抓出版社正文）。

目的：CDP 抓出版社被 CAPTCHA 挡住时，先确认是否还有
**白名单内的结构化开放获取接口**可走 —— 这比硬闯出版社风控更合规也更稳。

判据仍按 templates/直连白名单.json：
  www.ebi.ac.uk  -> abstract+oa-fulltext-xml (oa_only)
  其余白名单域名 -> 仅 metadata/abstract
所以本脚本**只查可用性与链接**，不下载任何出版社内容。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n), None
    _s = getattr(sys, _n, None)
    _b = getattr(_s, "buffer", None)
    if _b is not None and "utf8" not in (getattr(_s, "encoding", "") or "").lower().replace("-", ""):
        try:
            setattr(sys, _n, io.TextIOWrapper(_b, encoding="utf-8", errors="replace"))
        except Exception:
            pass

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
UA = "flow1-coverage/1.0 (mailto:researcher@example.org)"
EMAIL = "researcher@example.org"

REMAIN = [
    "10.1016/j.cell.2014.03.011",
    "10.1016/j.immuni.2017.04.008",
    "10.1016/j.immuni.2019.03.025",
    "10.1016/j.jaci.2009.12.980",
    "10.1037/a0035302",
    "10.4049/jimmunol.0802740",
]


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()


def jget(url, timeout=30):
    st, b = get(url, timeout)
    if st != 200:
        return None
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except Exception:
        return None


print("=" * 78)
print("剩余 6 篇 · 第一层合法通道复核")
print("=" * 78)

rows = []
for doi in REMAIN:
    rec = {"doi": doi}
    print(f"\n{doi}")
    # 1) EPMC core：确认 PMCID / inEPMC / isOpenAccess / 是否有 PDF 标记
    q = urllib.parse.urlencode({"query": f'DOI:"{doi}"', "format": "json",
                                "resultType": "core", "pageSize": 1})
    js = jget(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{q}")
    r0 = (js or {}).get("resultList", {}).get("result", [{}])
    r0 = r0[0] if r0 else {}
    rec.update({"pmcid": r0.get("pmcid"), "pmid": r0.get("pmid"),
                "inEPMC": r0.get("inEPMC"), "isOpenAccess": r0.get("isOpenAccess"),
                "hasPDF": r0.get("hasPDF"), "isFreeToRead": r0.get("isFreeToRead"),
                "citedBy": r0.get("citedByCount")})
    print(f"  EPMC: pmcid={rec['pmcid']} inEPMC={rec['inEPMC']} isOA={rec['isOpenAccess']} "
          f"hasPDF={rec['hasPDF']} freeToRead={rec['isFreeToRead']}")

    # 2) EPMC fullTextXML 再确认（可能 OA 状态变化）
    ft = False
    if rec["pmcid"] and rec["inEPMC"] == "Y" and rec["isOpenAccess"] == "Y":
        st, b = get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{rec['pmcid']}/fullTextXML")
        txt = b.decode("utf-8", "replace")
        ft = st == 200 and "<body" in txt
    rec["epmc_fulltext"] = ft
    print(f"  EPMC fullTextXML 可用: {ft}")

    # 3) Unpaywall：是否有**其他**开放获取副本位置（仅查元数据，不下载）
    u = jget(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={EMAIL}")
    if u:
        locs = u.get("oa_locations") or []
        rec["oa_status"] = u.get("oa_status")
        rec["n_locations"] = len(locs)
        rec["hosts"] = sorted({(l.get("host_type") or "?") for l in locs})
        rec["repos"] = [l.get("url_for_landing_page") or l.get("url") for l in locs
                        if l.get("host_type") == "repository"][:3]
        print(f"  Unpaywall: status={rec['oa_status']} locations={rec['n_locations']} hosts={rec['hosts']}")
        for rp in rec["repos"]:
            print(f"     repo: {rp}")
    else:
        rec["oa_status"] = None
        print("  Unpaywall: 查询失败")

    # 4) OpenAlex：best_oa_location（同样是元数据）
    oa = jget(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}")
    if oa:
        bol = oa.get("best_oa_location") or {}
        rec["openalex_oa"] = bool(oa.get("open_access", {}).get("is_oa"))
        rec["openalex_pdf"] = bol.get("pdf_url")
        rec["openalex_landing"] = bol.get("landing_page_url")
        print(f"  OpenAlex: is_oa={rec['openalex_oa']} pdf={'Y' if rec['openalex_pdf'] else 'N'}")
    rows.append(rec)
    time.sleep(0.4)

json.dump(rows, open(".work_cdp/remaining_routes.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print("\n" + "=" * 78)
print("汇总：可用于补救的**第一层**通道")
print("=" * 78)
n = 0
for r in rows:
    routes = []
    if r.get("epmc_fulltext"):
        routes.append("EPMC fullTextXML")
    if r.get("openalex_pdf"):
        routes.append("OpenAlex pdf_url")
    if r.get("repos"):
        routes.append(f"仓库副本×{len(r['repos'])}")
    if routes:
        n += 1
        print(f"  {r['doi']:34} -> {', '.join(routes)}")
    else:
        print(f"  {r['doi']:34} -> 无第一层全文通道（须 CDP）")
print(f"\n  有第一层候选：{n}/{len(rows)}")
print("  结果 → .work_cdp/remaining_routes.json")
