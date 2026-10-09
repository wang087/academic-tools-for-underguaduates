# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# ORCID 身份核验与成果列表探测（阶段 A）
#
# 为什么单独一支：`orcid_extract.py` 走浏览器抓 ORCID **网页**（权威成果集，
# 适合翻页去重）；本脚本走 **公共 API**，一次拿到「姓名 + 任职 + 教育 + 关键词
# + 全部 works（含 DOI）」的结构化记录，用于**同名消歧前的身份核验**：
# 把 ORCID 的任职/教育时间线与作者主页履历逐条比对，吻合才认定同一人。
#
# 本脚本属「元数据 API」类，不受「CDP 强制抓取禁令」约束
#   （该禁令针对的是自编脚本从 HTTP 直链抓取目标内容，见 README「CDP 强制抓取禁令」）。
#
# 用法：
#   uv run python orcid_probe.py <ORCID> [--out 目录]
#   uv run python orcid_probe.py 0000-0000-0000-0000
#
# 可移植配置：PAPER_WORKDIR（默认当前目录）
# 产出：<WORKDIR>/.work_cdp/orcid_probe.json
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from logutil import get as get_log, workdir as _wd   # noqa: E402

UA = ("AuthorPaperAnalysis/1.0 (mailto:author-paper-analysis@example.org)")
API = "https://pub.orcid.org/v3.0"


def norm_orcid(s: str) -> str:
    """接受裸 iD 或 orcid.org URL，规范为 0000-0000-0000-0000。"""
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", s or "")
    return m.group(1) if m else (s or "").strip()


def fetch(url: str, accept="application/json", tries: int = 4, timeout: int = 45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    for a in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 404 = ORCID 不存在，无需重试
            if e.code == 404:
                raise
            if a == tries - 1:
                raise
        except Exception:
            if a == tries - 1:
                raise
        time.sleep(2 * (a + 1))


def ymd(d) -> str:
    if not d:
        return "?"
    y = (d.get("year") or {}).get("value") or "?"
    m = (d.get("month") or {}).get("value")
    return f"{y}-{m}" if m else str(y)


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    if not argv:
        print(__doc__ or "用法: uv run python orcid_probe.py <ORCID> [--out 目录]")
        return 1
    orcid = norm_orcid(argv[0])
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid):
        print(f"[X] ORCID 格式不合法: {argv[0]!r}")
        return 1

    wd = _wd()
    outdir = wd / ".work_cdp"
    for a in argv[1:]:
        if a.startswith("--out="):
            outdir = pathlib.Path(a.split("=", 1)[1]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    log = get_log("orcid_probe", wd=wd)

    result = {"orcid": orcid, "employments": [], "educations": [], "keywords": [],
              "biography": "", "works": [], "name": "", "errors": []}

    log.info(f"ORCID 记录核验 https://orcid.org/{orcid}")
    try:
        rec = fetch(f"{API}/{orcid}/record")
        person = rec.get("person", {}) or {}
        nm = person.get("name") or {}
        gn = ((nm.get("given-names") or {}) or {}).get("value", "")
        fn = ((nm.get("family-name") or {}) or {}).get("value", "")
        result["name"] = f"{gn} {fn}".strip()
        result["biography"] = ((person.get("biography") or {}) or {}).get("content", "") or ""
        result["keywords"] = [k.get("content") for k in
                              ((person.get("keywords") or {}) or {}).get("keyword", []) or []]

        acts = rec.get("activities-summary", {}) or {}
        for key, tag in (("employments", "employment-summary"),
                         ("educations", "education-summary")):
            for grp in ((acts.get(key) or {}) or {}).get("affiliation-group", []) or []:
                for s in grp.get("summaries", []) or []:
                    e = s.get(tag) or {}
                    org = ((e.get("organization") or {}) or {}).get("name", "")
                    row = {"org": org, "role": e.get("role-title") or "",
                           "dept": e.get("department-name") or "",
                           "start": ymd(e.get("start-date")), "end": ymd(e.get("end-date"))}
                    result["employments" if key == "employments" else "educations"].append(row)
    except urllib.error.HTTPError as e:
        msg = f"ORCID record HTTP {e.code}（iD 可能不存在）"
        result["errors"].append(msg)
        log.warn(msg)
    except Exception as e:
        msg = f"ORCID record 获取失败: {type(e).__name__}: {e}"
        result["errors"].append(msg)
        log.warn(msg)

    try:
        w = fetch(f"{API}/{orcid}/works")
        for grp in (w.get("group", []) or []):
            s = (grp.get("work-summary") or [{}])[0]
            ids = {e.get("external-id-type"): e.get("external-id-value")
                   for e in ((s.get("external-ids") or {}).get("external-id", []) or [])}
            result["works"].append({
                "title": (((s.get("title") or {}) or {}).get("title") or {}).get("value", ""),
                "journal": ((s.get("journal-title") or {}) or {}).get("value", ""),
                "year": (((s.get("publication-date") or {}) or {}).get("year") or {}).get("value", ""),
                "doi": ids.get("doi"), "type": s.get("type"),
            })
    except Exception as e:
        msg = f"ORCID works 获取失败: {type(e).__name__}: {e}"
        result["errors"].append(msg)
        log.warn(msg)

    p = outdir / "orcid_probe.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n姓名: {result['name'] or '(未公开)'}")
    if result["biography"]:
        print(f"简介: {result['biography'][:300]}")
    for k in result["keywords"]:
        print(f"  关键词: {k}")
    print("\n任职:")
    for e in result["employments"]:
        print(f"  {e['org']} | {e['role']} | {e['start']} – {e['end']}")
    print("教育:")
    for e in result["educations"]:
        print(f"  {e['org']} | {e['role']} | {e['start']} – {e['end']}")
    print(f"\nORCID 收录成果组: {len(result['works'])}")
    for w in result["works"]:
        print(f"  [{w['year']}] {w['title'][:88]}")
        print(f"        {w['journal']} | DOI={w['doi']} | {w['type']}")
    if result["errors"]:
        print("\n[WARN] 错误:")
        for e in result["errors"]:
            print(f"  - {e}")
    log.ok(f"写入 {p}")
    log.info("提示：请把上方任职/教育时间线与作者主页履历逐条比对，吻合方可认定同一人。")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
