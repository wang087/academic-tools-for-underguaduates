# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文获取路线 B：OA 候选瀑布（**只清点与排序，不下载**）
#
# 定位
#   本模块是**新增模块，尚未接入主链路**。它实现对照报告 §3.3 的 P2-2。
#
# [STOP] 为什么默认「只清点，不下载」
#   流程1 的 §5.1 分层把「原文全文（含 OA）」划为**第二层**，一律走 CDP；
#   `fetch_oa_direct.py` 已用双路径守卫（退出码 3）把纯 HTTP 直取正文堵死。
#   而「用 Unpaywall 判断哪篇有合法 OA 副本」属于**第一层**
#   （白名单里 `api.unpaywall.org` 是 `content_class: metadata`）。
#   所以本模块先把**判断**这一步做全、做可审计，
#   把「要不要真的发请求去取」留给显式决策 —— 这正是 P2-2 里
#   「必须先想清楚立场」那个待确认点。
#
#   因此：
#     · 默认 `fetch=False` —— 只产出候选清单与排序理由，一个字节都不下载。
#     · `fetch=True` 时，**每个候选仍要过 `white_list_policy.decide()`**：
#         允许直连的才直连；不允许的一律标 `browser`（交 CDP），
#         绝不因为「它是 OA」就绕过分层。
#
# 技术特点（对照 nature-downloader/lib/open-access.mjs）
#   1. DOI → PMCID 自动补全（Europe PMC search），补上阶段 R 曾经漏采的那一步
#   2. PMCID → PMC OA 文件清单（oa.fcgi），ftp:// 改写为 https://
#   3. Unpaywall：`best_oa_location` + 全部 `oa_locations`，保留
#      host_type / version / license —— 供后续合法性分类使用
#   4. 候选**排序**而非先到先得：PMC > Unpaywall > publisher_oa > repository > arXiv；
#      同级 PDF 优先于落地页
#   5. OA 判定**三态**：confirmed_oa / confirmed_closed / unknown
#      —— unknown **绝不**被当作「非 OA」（nature-downloader 的正确做法）
#
# 依赖：`$LIT_DL_CONFIG_DIR/settings.json` 或 `PAPER_EMAIL` 里的联系邮箱。
#   **没有邮箱就不查 Unpaywall** —— 用一个占位邮箱等于没有邮箱，
#   还会把无效流量算到别人的 polite pool 上。
#
# 用法：
#   from oa_candidates import resolve_ids, collect_candidates, rank_candidates
#   ids = resolve_ids("10.1016/j.cell.2014.03.011")     # → {pmcid, inEPMC, isOpenAccess}
#   cands, assessment = collect_candidates(ids, doi)    # 只清点，不下载
#   best = rank_candidates(cands)[0] if cands else None
#
#   uv run python scripts/oa_candidates.py --self-test
#   uv run python scripts/oa_candidates.py --doi 10.1038/s41577-022-00763-8   # 联网清点
#
# 退出码：0 = 通过/有候选；1 = 自检失败；2 = 用法错误；3 = 无候选
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

import fulltext_credentials as creds  # noqa: E402
import fulltext_result as fr  # noqa: E402
from fulltext_result import FullTextResult  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PMC_IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
PMC_OA = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
UNPAYWALL = "https://api.unpaywall.org/v2/"
ARXIV_API = "https://export.arxiv.org/api/query"

# 候选来源优先级：数字越小越优先（与 nature-downloader 的 sourceRank 同序）
SOURCE_RANK = {"pmc": 0, "unpaywall": 1, "publisher_oa": 2, "repository": 3, "arxiv": 4}


# ══════════════════════════════════════════════════════════════
# 标识符归一化与解析
# ══════════════════════════════════════════════════════════════
def normalize_doi(doi: str) -> str:
    d = str(doi or "").strip()
    d = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.strip()


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "")).strip().lower()


def exact_title_match(candidate: str, expected: str) -> bool:
    """精确题名匹配（归一化后全等）。

    [STOP] 必须是全等，不能是子串或模糊相似 ——
    `--topic` 曾把 "Attention Is All You Need" 匹到无关商业文；
    题名是弱标识符，弱标识符只能靠**严格**匹配来兜底。
    """
    return bool(expected) and normalize_title(candidate) == normalize_title(expected)


def _http(url: str, timeout: float, accept: str = "application/json",
          fetch_impl=None) -> tuple[int, str, bytes]:
    if fetch_impl is not None:
        return fetch_impl(url, {"Accept": accept}, timeout)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200), \
                resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), (e.headers.get("Content-Type", "") if e.headers else ""), (e.read() or b"")
    except Exception:  # noqa: BLE001
        return 0, "", b""


def _json_or_none(url: str, timeout: float, fetch_impl=None) -> dict | None:
    st, _ct, body = _http(url, timeout, "application/json", fetch_impl)
    if st != 200 or not body:
        return None
    try:
        v = json.loads(body.decode("utf-8", "replace"))
        return v if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001
        return None


def resolve_ids(doi: str, fetch_impl=None, timeout: float = 30.0) -> dict:
    """DOI → {doi, pmcid, pmid, inEPMC, isOpenAccess, error}。

    两条路：Europe PMC search 优先，PMC ID Converter 兜底。
    这一步就是阶段 R 曾经**整批漏采**的那一步
    （见 docs/阶段R全文获取模块.md 一）。
    """
    d = normalize_doi(doi)
    rec: dict = {"doi": d, "pmcid": None, "pmid": None,
                 "inEPMC": "", "isOpenAccess": "", "error": ""}
    if not d:
        rec["error"] = "empty doi"
        return rec

    j = _json_or_none(f"{EPMC_SEARCH}?query=DOI:%22{urllib.parse.quote(d, safe='')}%22"
                      f"&format=json&resultType=core", timeout, fetch_impl)
    hit = ((j or {}).get("resultList") or {}).get("result") or []
    if hit and isinstance(hit[0], dict):
        r0 = hit[0]
        rec.update({
            "pmcid": r0.get("pmcid") or None,
            "pmid": r0.get("pmid") or None,
            "inEPMC": r0.get("inEPMC") or "",
            "isOpenAccess": r0.get("isOpenAccess") or "",
        })
        return rec

    j2 = _json_or_none(f"{PMC_IDCONV}?ids={urllib.parse.quote(d, safe='')}&format=json",
                       timeout, fetch_impl)
    recs = (j2 or {}).get("records") or []
    if recs and isinstance(recs[0], dict):
        rec["pmcid"] = recs[0].get("pmcid") or None
        rec["pmid"] = recs[0].get("pmid") or None
        return rec

    rec["error"] = "EPMC 与 PMC ID Converter 都没给出标识符"
    return rec


# ══════════════════════════════════════════════════════════════
# 候选发现
# ══════════════════════════════════════════════════════════════
def _https_for_ftp(url: str) -> str:
    """PMC 的 OA 清单给的是 ftp:// 链接，浏览器与 urllib 都更认 https。"""
    return re.sub(r"^ftp://ftp\.ncbi\.nlm\.nih\.gov/", "https://ftp.ncbi.nlm.nih.gov/",
                  str(url or ""), flags=re.I)


def parse_pmc_oa_xml(xml: str) -> list[dict]:
    """从 oa.fcgi 的 XML 抽 pdf 链接。

    [STOP] 只收 `format` 含 pdf 的 link —— 一份 OA 包里还有 tgz/pdf 混杂，
    把 tgz 当 PDF 存下来会得到一个体积达标但内容错的「成功」文件。
    """
    out = []
    for m in re.finditer(r"<link\b([^>]*)>", str(xml or ""), re.I):
        attrs = m.group(1)
        fmt = (re.search(r'\bformat=["\']([^"\']+)', attrs, re.I) or [None, ""])[1]
        href = (re.search(r'\bhref=["\']([^"\']+)', attrs, re.I) or [None, ""])[1]
        if not href or "pdf" not in fmt.lower():
            continue
        out.append({"source": "pmc", "resolver": "pmc-oa",
                    "url": _https_for_ftp(href), "format": "pdf",
                    "version": "publishedVersion", "license": "pmc-oa",
                    "host_type": "repository"})
    return out


def parse_unpaywall(record: dict | None) -> list[dict]:
    """Unpaywall 记录 → 候选列表（best + 全部 oa_locations，按 URL 去重）。"""
    if not isinstance(record, dict) or not record.get("is_oa"):
        return []
    locs = [record.get("best_oa_location")] + list(record.get("oa_locations") or [])
    seen, out = set(), []
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        url = loc.get("url_for_pdf") or loc.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        is_pub = str(loc.get("host_type") or "") == "publisher"
        out.append({
            "source": "publisher_oa" if is_pub else "repository",
            "resolver": "unpaywall",
            "url": url,
            "format": "pdf" if loc.get("url_for_pdf") else "landing",
            "host_type": loc.get("host_type") or "",
            "version": loc.get("version") or "",
            "license": loc.get("license") or record.get("license") or "",
            "is_best": loc is record.get("best_oa_location"),
        })
    return out


def find_pmc_candidates(pmcid: str, fetch_impl=None, timeout: float = 30.0) -> list[dict]:
    if not pmcid:
        return []
    st, _ct, body = _http(f"{PMC_OA}?id={urllib.parse.quote(str(pmcid), safe='')}",
                          timeout, "application/xml", fetch_impl)
    if st != 200 or not body:
        return []
    return parse_pmc_oa_xml(body.decode("utf-8", "replace"))


def find_arxiv_by_title(title: str, fetch_impl=None, timeout: float = 30.0) -> dict | None:
    """按**精确题名**找 arXiv 预印本（无 DOI 的会议/预印本用）。"""
    if not title:
        return None
    q = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "start": 0, "max_results": 5})
    st, _ct, body = _http(f"{ARXIV_API}?{q}", timeout, "application/atom+xml", fetch_impl)
    if st != 200 or not body:
        return None
    xml = body.decode("utf-8", "replace")
    for entry in re.findall(r"<entry>[\s\S]*?</entry>", xml):
        t = (re.search(r"<title>([\s\S]*?)</title>", entry) or [None, ""])[1]
        t = re.sub(r"\s+", " ", _unescape_xml(t)).strip()
        if not exact_title_match(t, title):
            continue
        raw_id = _unescape_xml((re.search(r"<id>([\s\S]*?)</id>", entry) or [None, ""])[1])
        aid = normalize_arxiv_id(raw_id)
        if not aid:
            continue
        return {"id": aid, "title": t, "url": f"https://arxiv.org/pdf/{aid}",
                "source": "arxiv", "resolver": "arxiv", "format": "pdf",
                "version": "submittedVersion", "license": "", "host_type": "repository"}
    return None


def _unescape_xml(text: str) -> str:
    return (str(text or "").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'"))


def normalize_arxiv_id(value: str) -> str:
    """`arxiv.org/abs/1706.03762v7` / `.../pdf/1706.03762.pdf` → `1706.03762v7`。"""
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/)?([a-z-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?",
                  str(value or ""), re.I)
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2) or ''}"


# ══════════════════════════════════════════════════════════════
# 排序与三态判定
# ══════════════════════════════════════════════════════════════
def rank_candidates(candidates: list[dict]) -> list[dict]:
    """排序：来源优先级 → PDF 优先 → 带 license 者优先。

    为什么不是「先到先得」：同一篇可能同时有 PMC 打包 PDF 与出版社落地页，
    落地页很可能是付费墙残页。排序把**结构化、可机器校验**的来源顶到前面。
    """
    return sorted(
        candidates or [],
        key=lambda c: (
            SOURCE_RANK.get(str(c.get("source")), 99),
            0 if c.get("format") == "pdf" else 1,
            0 if c.get("license") else 1,
            str(c.get("url") or ""),
        ),
    )


def assess_oa(pmcid: str | None, unpaywall_record: dict | None,
              license_hint: str = "") -> str:
    """三态结论：confirmed_oa / confirmed_closed / unknown。

    [STOP] `unknown` **绝不**等价于「非 OA」。
    查不到只说明「这次没查到」，不构成「这篇不开放」的证据；
    把 unknown 标成 closed 会让下游放弃一条本来存在的合法路线。
    """
    if pmcid or (isinstance(unpaywall_record, dict) and unpaywall_record.get("is_oa")) or license_hint:
        return "confirmed_oa"
    if isinstance(unpaywall_record, dict) and unpaywall_record.get("is_oa") is False:
        return "confirmed_closed"
    return "unknown"


def collect_candidates(doi: str, pmcid: str | None = None, title: str = "",
                       fetch_impl=None, timeout: float = 30.0,
                       want_arxiv: bool = False) -> tuple[list[dict], dict]:
    """清点该 DOI 的全部合法 OA 候选 → (已排序候选, 审计信息)。

    **本函数不下载任何内容**，只发查询类请求（EPMC / oa.fcgi / Unpaywall / arXiv）。
    """
    d = normalize_doi(doi)
    audit: dict = {
        "doi": d,
        "pmcid": pmcid or "",
        "unpaywall_checked": False,
        "unpaywall_error": "",
        "is_oa": None,
        "attempts": [],
    }
    cands: list[dict] = []

    # ① PMCID 已知则先取 PMC OA 清单（最结构化）
    if pmcid:
        pmc = find_pmc_candidates(pmcid, fetch_impl, timeout)
        audit["attempts"].append({"source": "pmc", "found": len(pmc)})
        cands.extend(pmc)

    # ② Unpaywall（需要真实联系邮箱）
    email = creds.contact_email()
    record = None
    if d and email:
        record = _json_or_none(f"{UNPAYWALL}{urllib.parse.quote(d, safe='')}"
                               f"?email={urllib.parse.quote(email, safe='')}",
                               timeout, fetch_impl)
        audit["unpaywall_checked"] = record is not None
        if record is None:
            audit["unpaywall_error"] = "查询失败或无响应"
        else:
            audit["is_oa"] = record.get("is_oa")
        up = parse_unpaywall(record)
        audit["attempts"].append({"source": "unpaywall", "found": len(up)})
        cands.extend(up)
    elif not email:
        audit["unpaywall_error"] = ("未配置联系邮箱 —— 按约定**不查** Unpaywall"
                                   "（占位邮箱等于没有邮箱）")
        audit["attempts"].append({"source": "unpaywall", "found": 0, "skipped": "no-email"})

    # ③ arXiv（只在显式需要且题名已知时）
    if want_arxiv and title:
        hit = find_arxiv_by_title(title, fetch_impl, timeout)
        audit["attempts"].append({"source": "arxiv", "found": 1 if hit else 0})
        if hit:
            cands.append(hit)

    # 去重（同 URL 只留优先来源的那条）
    dedup: dict[str, dict] = {}
    for c in rank_candidates(cands):
        url = str(c.get("url") or "")
        if url and url not in dedup:
            dedup[url] = c
    ordered = rank_candidates(list(dedup.values()))

    audit["assessment"] = assess_oa(pmcid, record)
    audit["candidate_count"] = len(ordered)
    return ordered, audit


# ══════════════════════════════════════════════════════════════
# 显式取回（默认不用；每个候选仍过分层判据）
# ══════════════════════════════════════════════════════════════
def fetch_candidate(candidate: dict, out_dir, *, allow_browser: bool = False,
                    is_oa: bool = True, fetch_impl=None, timeout: float = 60.0) -> FullTextResult:
    """取回**单个**候选。

    [STOP] 分层判据不因「它是 OA」而放宽：
      · 白名单允许直连 → 直连；
      · 不允许 → 返回 `route_disabled` 并标 `next=browser`（交 CDP）。
        `allow_browser=True` 只是**声明调用方会走 CDP**，本函数仍不代替 CDP，
        因为 CDP 需要已认证的浏览器会话，那是 download_all_fulltexts.py 的职责。

    `is_oa` 会原样交给 `white_list_policy.decide()` —— 对 `oa_only=true` 的源
    （如 EPMC）必须为 True 才放行。**默认 True 的正当性**：本函数的输入
    本身就是 OA 候选瀑布的产物，其证据链在 `collect_candidates` 里；
    若要更保守，调用方传 `is_oa=None` 即可让 `decide()` fail closed。
    """
    import white_list_policy as wlp  # noqa: PLC0415

    url = str((candidate or {}).get("url") or "")
    out = pathlib.Path(out_dir)
    src = str((candidate or {}).get("source") or "open_access")
    if not url:
        return FullTextResult.failed("open_access", "oa_not_found", reason="候选没有 URL")

    decision = wlp.decide(url, "fulltext", is_oa=is_oa)
    if not decision["allowed"]:
        return FullTextResult(
            status="route_disabled", provider="open_access", access_mode="",
            reason=f"分层策略不允许直连该候选：{decision['reason']}",
            attempts=[{"source": src, "url": url, "status": "route_denied",
                       "reason": decision["reason"]}],
            extra={"next": "browser",
                   "browser_route_confirmed": bool(allow_browser),
                   "policy": decision},
        )

    st, ct, body = _http(url, timeout, "application/pdf, text/html, application/xml", fetch_impl)
    if st < 200 or st >= 300:
        return FullTextResult.failed("open_access", "pdf_fetch_failed", http_status=st,
                                     reason=f"HTTP {st}",
                                     attempts=[{"source": src, "url": url, "status": f"http-{st}"}])
    ok, fmt, why = fr.classify_bytes(ct, body[:65536])
    if not ok:
        return FullTextResult.failed("open_access", "pdf_fetch_failed", http_status=st,
                                     reason=f"内容不是可用全文：{why}",
                                     attempts=[{"source": src, "url": url,
                                                "status": "invalid_content", "reason": why}])

    stem = re.sub(r"[^0-9A-Za-z._-]+", "_",
                  normalize_doi(str((candidate or {}).get("doi") or "")) or src)[:100] or src
    if fmt == "jats_xml":
        import jats_fulltext  # noqa: PLC0415
        text = body.decode("utf-8", "replace")
        real, jwhy = jats_fulltext.is_real_fulltext(text)
        if not real:
            return FullTextResult.failed("open_access", "not_real_fulltext", http_status=st,
                                         reason=f"JATS 不是真全文：{jwhy}")
        info = fr.save_bytes(body, out / fr.FORMAT_DIR["jats_xml"] / f"{stem}.xml")
        fr.store_text_as_fulltext(jats_fulltext.body_text(text),
                                  out / fr.FORMAT_DIR["jats_xml"] / f"{stem}.txt",
                                  {"source": url, "doi": candidate.get("doi", ""),
                                   "route": "oa-jats"})
        status = "native_fulltext_downloaded"
    else:
        info = fr.save_bytes(body, out / fr.FORMAT_DIR.get(fmt, "FullText") / f"{stem}{fr.FORMAT_EXT[fmt]}")
        status = "open_access_downloaded" if fmt == "pdf" else "full_text_html_available"

    return FullTextResult(
        status=status, provider="open_access", access_mode="open_access", fmt=fmt,
        http_status=st, final_url=url,
        attempts=[{"source": src, "url": url, "status": "ok", "resolver": candidate.get("resolver", "")}],
        extra={"oa_evidence": {k: v for k, v in candidate.items() if k != "url"},
               "license": candidate.get("license", ""), "version": candidate.get("version", "")},
        **info,
    )


# ══════════════════════════════════════════════════════════════
# 自检（**完全离线**：一律注入假 fetch）
# ══════════════════════════════════════════════════════════════
_OA_XML = ('<OA><records><record id="PMC1"><link format="pdf" '
           'href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/x.pdf" />'
           '<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/x.tar.gz" />'
           '</record></records></OA>')
_UNPAYWALL = {
    "is_oa": True,
    "best_oa_location": {"url_for_pdf": "https://www.ebi.ac.uk/europepmc/x.pdf",
                         "host_type": "repository", "version": "publishedVersion",
                         "license": "cc-by"},
    "oa_locations": [
        {"url": "https://www.ebi.ac.uk/europepmc/x.pdf", "host_type": "repository"},
        {"url_for_pdf": "https://publisher.example/a.pdf", "host_type": "publisher",
         "version": "publishedVersion", "license": "cc-by"},
        {"url": "https://repo.example/landing", "host_type": "repository"},
    ],
}


class _FakeRouter:
    """按 URL 关键词分派假响应；未命中即断言失败（防意外联网）。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"假 fetch 未预期地访问了：{url}")


def self_test() -> int:
    bad = 0
    import logutil  # noqa: PLC0415

    saved = {k: __import__("os").environ.get(k)
             for k in ("LIT_DL_CONFIG_DIR", "LIT_FLOW1_CREDENTIALS", "PAPER_EMAIL", "UNPAYWALL_EMAIL")}
    import os  # noqa: PLC0415

    scratch = logutil.workdir() / ".work_cdp" / "selftest" / "oa"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        os.environ["LIT_DL_CONFIG_DIR"] = str(scratch)
        os.environ.pop("LIT_FLOW1_CREDENTIALS", None)
        os.environ["PAPER_EMAIL"] = "tester@example.org"

        # ── 1. 标识符归一化 ─────────────────────────────────
        ids = [
            ("https://doi.org/10.1016/j.cell.2014.03.011", "10.1016/j.cell.2014.03.011"),
            ("doi:10.1038/s41577-022-00763-8", "10.1038/s41577-022-00763-8"),
            ("  10.1109/TMI.2020.1  ", "10.1109/TMI.2020.1"),
        ]
        wrong = [(a, normalize_doi(a), b) for a, b in ids if normalize_doi(a) != b]
        if wrong:
            bad += 1
            print(f"  [X] DOI 归一化错误：{wrong}")
        else:
            print(f"DOI 归一化：{len(ids)}/{len(ids)} 命中（DOI 后缀大小写**未**被改写）")

        ax = [("https://arxiv.org/abs/1706.03762v7", "1706.03762v7"),
              ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
              ("arXiv:1706.03762", "1706.03762")]
        wrong = [(a, normalize_arxiv_id(a), b) for a, b in ax if normalize_arxiv_id(a) != b]
        if wrong:
            bad += 1
            print(f"  [X] arXiv ID 归一化错误：{wrong}")
        else:
            print(f"arXiv ID 归一化：{len(ax)}/{len(ax)} 命中")

        # ── 2. 精确题名匹配必须严格 ─────────────────────────
        if not exact_title_match("Attention  Is All You Need", "attention is all you need"):
            bad += 1
            print("  [X] 归一化后应匹配（大小写/空白差异）")
        if exact_title_match("Attention Is All You Need (HBR reprint)", "Attention Is All You Need"):
            bad += 1
            print("  [X] 子串/近似题名被误判为匹配 —— 这会复现 --topic 误配事故")
        else:
            print("题名匹配：归一化后全等才算命中（近似/子串一律拒绝）")

        # ── 3. PMC OA 清单：只收 pdf，ftp 改 https ──────────
        pmc = parse_pmc_oa_xml(_OA_XML)
        if len(pmc) != 1 or not pmc[0]["url"].startswith("https://"):
            bad += 1
            print(f"  [X] PMC 清单解析错误（应收 1 条 pdf、且为 https）：{pmc}")
        else:
            print(f"PMC 清单：只收 pdf 链接（tgz 已排除），ftp → https 改写生效")

        # ── 4. Unpaywall 解析与去重 ─────────────────────────
        up = parse_unpaywall(_UNPAYWALL)
        if len(up) != 3:
            bad += 1
            print(f"  [X] Unpaywall 候选数错误：{len(up)}（期望 3，同 URL 已去重）")
        else:
            print(f"Unpaywall 解析：{len(up)} 个候选（best + oa_locations，同 URL 去重）")
        host_types = sorted({c["host_type"] for c in up})
        if host_types != ["publisher", "repository"]:
            bad += 1
            print(f"  [X] host_type 未保留：{host_types}")
        else:
            print(f"Unpaywall 解析：host_type/version/license 均已保留（{host_types}）")
        if not any(c["source"] == "publisher_oa" for c in up):
            bad += 1
            print("  [X] publisher 侧候选未被标为 publisher_oa")
        else:
            print("Unpaywall 解析：publisher 侧标 publisher_oa，其余标 repository")

        # ── 5. 排序：PMC > Unpaywall(publisher_oa) > repository ─
        mixed = up + pmc + [{"source": "arxiv", "url": "https://arxiv.org/pdf/x", "format": "pdf"}]
        order = [c["source"] for c in rank_candidates(mixed)]
        want_prefix = ["pmc", "publisher_oa", "repository", "arxiv"]
        if order[:1] != ["pmc"] or order[-1] != "arxiv":
            bad += 1
            print(f"  [X] 排序错误：{order}")
        else:
            print(f"候选排序：{order}（PMC 优先，arXiv 最后）")
        # 同级 PDF 优先于落地页
        same = rank_candidates([
            {"source": "unpaywall", "url": "https://a/landing", "format": "landing"},
            {"source": "unpaywall", "url": "https://a/x.pdf", "format": "pdf"},
        ])
        if same[0]["format"] != "pdf":
            bad += 1
            print(f"  [X] 同级未按 PDF 优先：{same}")
        else:
            print("候选排序：同级格式下 PDF 优先于落地页")

        # ── 6. 三态判定：unknown 绝不等于非 OA ───────────────
        cases = [
            ("PMCID 在", ("PMC1", None, ""), "confirmed_oa"),
            ("Unpaywall is_oa 真", (None, {"is_oa": True}, ""), "confirmed_oa"),
            ("license 提示", (None, None, "cc-by"), "confirmed_oa"),
            ("Unpaywall is_oa 假", (None, {"is_oa": False}, ""), "confirmed_closed"),
            ("什么都没查到", (None, None, ""), "unknown"),
            ("Unpaywall 查询失败", (None, None, ""), "unknown"),
        ]
        wrong = [(n, assess_oa(*a), w) for n, a, w in cases if assess_oa(*a) != w]
        if wrong:
            bad += 1
            print(f"  [X] 三态判定错误：{wrong}")
        else:
            print(f"三态判定：{len(cases)}/{len(cases)} 命中，unknown 未被当作非 OA")

        # ── 7. 无邮箱则不查 Unpaywall（且明确说明原因）─────────
        os.environ.pop("PAPER_EMAIL", None)
        os.environ.pop("UNPAYWALL_EMAIL", None)
        router = _FakeRouter({
            "europepmc": (200, "application/json",
                          json.dumps({"resultList": {"result": [{"pmcid": "PMC9"}]}}).encode()),
            "oa.fcgi": (200, "application/xml", _OA_XML.encode()),
        })
        cands, audit = collect_candidates("10.1/x", fetch_impl=router)
        if audit["unpaywall_checked"]:
            bad += 1
            print("  [X] 无邮箱时仍然查了 Unpaywall")
        elif not audit.get("unpaywall_error"):
            bad += 1
            print("  [X] 无邮箱被静默跳过（必须说明原因）")
        else:
            print(f"邮箱缺失：不查 Unpaywall，且原因写明（{audit['unpaywall_error'][:28]}...）")
        if not any("unpaywall" in u for u in router.calls):
            print("邮箱缺失：确实没有向 api.unpaywall.org 发请求")
        else:
            bad += 1
            print(f"  [X] 无邮箱仍访问了 Unpaywall：{router.calls}")

        # ── 8. 端到端清点（离线）不下载任何东西 ───────────────
        os.environ["PAPER_EMAIL"] = "tester@example.org"
        router = _FakeRouter({
            "europepmc/webservices/rest/search": (200, "application/json", json.dumps(
                {"resultList": {"result": [{"pmcid": "PMC1", "inEPMC": "Y", "isOpenAccess": "Y"}]}}
            ).encode()),
            "oa.fcgi": (200, "application/xml", _OA_XML.encode()),
            "api.unpaywall.org": (200, "application/json", json.dumps(_UNPAYWALL).encode()),
        })
        ids = resolve_ids("10.1016/j.cell.2014.03.011", fetch_impl=router)
        if ids.get("pmcid") != "PMC1" or ids.get("isOpenAccess") != "Y":
            bad += 1
            print(f"  [X] resolve_ids 结果错误：{ids}")
        else:
            print(f"resolve_ids：补出 PMCID={ids['pmcid']}，inEPMC={ids['inEPMC']}，"
                  f"isOpenAccess={ids['isOpenAccess']}")
        cands, audit = collect_candidates(ids["doi"], pmcid=ids.get("pmcid"), fetch_impl=router)
        if not cands:
            bad += 1
            print(f"  [X] 清点未得到候选：{audit}")
        else:
            print(f"清点：{len(cands)} 个候选，排序首位 = {cands[0]['source']}"
                  f"（{cands[0]['format']}）")
        if audit["assessment"] != "confirmed_oa":
            bad += 1
            print(f"  [X] 审计结论应为 confirmed_oa：{audit['assessment']}")
        else:
            print(f"清点：三态结论 = {audit['assessment']}，审计含 attempts 明细")
        # 关键：清点**不得**取任何正文（不能出现对 x.pdf 的请求）
        body_like = [u for u in router.calls if u.endswith((".pdf", ".xml")) and "oa.fcgi" not in u]
        if body_like:
            bad += 1
            print(f"  [X] 清点阶段就去取正文了（违反「只清点」约定）：{body_like}")
        else:
            print("只清点：全程未对任何正文 URL 发请求")

        # ── 9. 显式取回时，分层判据不被 OA 身份绕过 ───────────
        # 候选在出版社域名上（不在白名单）→ 必须 route_disabled + next=browser
        pub = [c for c in cands if "publisher.example" in str(c.get("url"))]
        if not pub:
            bad += 1
            print("  [X] 夹具里没有出版社侧候选，用例无法验证")
        else:
            r = fetch_candidate(pub[0], scratch / "out")
            d = r.to_dict()
            if r.status != "route_disabled" or d.get("extra", {}).get("next") != "browser":
                bad += 1
                print(f"  [X] 出版社侧 OA 候选未被分层拦下：{r} extra={d.get('extra')}")
            else:
                print("分层未被绕过：出版社域名的 OA 候选被拦为 route_disabled（next=browser）")
        # 白名单内的候选（ebi.ac.uk）→ 允许直连
        ok_c = [c for c in cands if "ebi.ac.uk" in str(c.get("url"))]
        if ok_c:
            router2 = _FakeRouter({"ebi.ac.uk": (200, "application/pdf", b"%PDF-1.7\n" + b"0" * 4000)})
            r = fetch_candidate(ok_c[0], scratch / "out", fetch_impl=router2)
            if not r.ok or r.access_mode != "open_access":
                bad += 1
                print(f"  [X] 白名单内候选取回失败：{r}")
            else:
                print(f"白名单内候选：取回成功并标记 access_mode=open_access（{r.bytes}B）")
        else:
            print("白名单内候选：夹具未含 ebi.ac.uk 候选，跳过该子用例")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            import shutil  # noqa: PLC0415
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    doi = ""
    title = ""
    for i, a in enumerate(argv):
        if a == "--doi" and i + 1 < len(argv):
            doi = argv[i + 1]
        elif a.startswith("--doi="):
            doi = a.split("=", 1)[1]
        elif a == "--title" and i + 1 < len(argv):
            title = argv[i + 1]
        elif a.startswith("--title="):
            title = a.split("=", 1)[1]
    if not doi and not title:
        print(__doc__)
        return 2
    ids = resolve_ids(doi) if doi else {"doi": "", "pmcid": None}
    cands, audit = collect_candidates(doi, pmcid=ids.get("pmcid"), title=title,
                                      want_arxiv=bool(title and not doi))
    print(json.dumps({"ids": ids, "audit": audit, "candidates": cands},
                     ensure_ascii=False, indent=2))
    return 0 if cands else 3


if __name__ == "__main__":
    raise SystemExit(main())
