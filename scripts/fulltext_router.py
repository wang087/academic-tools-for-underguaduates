# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# fulltext_router.py —— 全文获取**副链路**的路由编排层
#
# 为什么需要它
#   主链路（download_all_fulltexts.py）只有一条腿：CDP 浏览器。
#   CDP 被风控、端口失联、或遇到中文文献（CNKI）时，主链路能给出的
#   只有「失败」两个字 —— 它不知道这篇还有没有别的合法路线。
#   本模块补的正是这一层**判断**：一篇论文取不到时，先问清楚
#   「它属于谁、有没有 OA 副本、有没有授权 API、下一步该交给谁」，
#   再决定是自己取、还是如实交接。
#
#   判据链移植自 nature-downloader 的 `routeDoiDownload`
#   （scripts/batch_download.mjs:500-660 + lib/routing.mjs），
#   但**只移植编排层**：Crossref 元数据 →（中文硬分流）CNKI → 出版商 API
#   → OA 瀑布 → 浏览器 → API 兜底需显式授权。
#   真正干活的部分一律委托给流程1 自己的模块，不引入 Node 运行时，
#   也不复制任何 .mjs：
#     出版商 API   → fulltext_publishers.fetch_fulltext()
#     OA 候选/取回 → oa_candidates.collect_candidates() / fetch_candidate()
#     分层判据     → white_list_policy.decide()（由上面两个模块内部调用）
#     节流与熔断   → fetch_pacing.PacerGuard（跨轮持久化）
#     结果与状态码 → fulltext_result.FullTextResult
#     凭据（只读） → fulltext_credentials
#
# 定位（挂载点）
#   副链路，挂在**阶段 R①.5（reviews_fulltext.py）**，作为 CDP 的备用通道：
#   主链路取不到全文的那些 DOI 才进本模块。它**不是**主链路的替代品，
#   也不改变主链路任何行为 —— `--fallback` 不开，本模块一次都不会被调用。
#
# [STOP] 不变量一：只在 CDP 失败之后启用
#   `route_doi(..., cdp_failed=...)` 的 `cdp_failed` 不为 True 时，
#   本模块**直接拒绝**（状态 `route_disabled`，CLI 退出码 3），一个请求都不发。
#   这是「副链路」这个定位的唯一执行点：把判据写在调用方（reviews_fulltext）
#   等于没写 —— 换个调用方就绕过去了。
#
# [STOP] 不变量二：直连放宽的边界是白名单，不是「它是 OA」
#   §5.1 分层把原文全文（含 OA）划为第二层，一律走 CDP。本模块经用户显式
#   决策获得的放宽是**有条件的**：仅 CDP 失败后 + 仅白名单内。
#   第二个条件不由本模块自己判 —— 每个候选都交给
#   `oa_candidates.fetch_candidate()`，它内部逐个过 `white_list_policy.decide()`，
#   白名单外一律返回 `route_disabled` + `next=browser`。
#   本模块只负责把这类结果**如实汇总成交接单**，绝不因为「反正失败了」
#   就换个域名再试一次。
#
#   实际覆盖面（照实说，不夸大）：当前 templates/直连白名单.json 里
#   `content_class` 能覆盖 `fulltext` 的只有 `www.ebi.ac.uk`
#   （abstract+oa-fulltext-xml，且 oa_only=true）—— 也就是 EPMC 的
#   OA 全文 XML。PMC 打包 PDF（ftp.ncbi.nlm.nih.gov）、出版社落地页
#   都不在白名单内，一律清点后交接给 CDP。**要扩大覆盖面只能改白名单**
#   （那是 §5.1 的收录判据，不是本模块的开关）。
#
# [STOP] 不变量三：不代填、不代登录、不代持凭据
#   CNKI 分支与出版商 API 分支都**不会**替用户登录或填写任何凭据。
#   凭据只读（fulltext_credentials 的立场声明）；缺凭据时返回
#   HANDOFF（`credentials_missing` / `api_fallback_confirmation_required`）
#   并给出用户自己该跑的命令，而不是静默跳过、也不是假装取到。
#
# 刻意**不**做的事
#   · 不自己发 Crossref 请求：题录由调用方给进来（reviews_fulltext 的
#     tasks/*.json 里已有 title/pmcid）。缺题名时按 nature 的同款降级
#     处理 —— `title=doi` + `metadata_error`，并如实标注，不猜。
#   · 不驱动浏览器：CDP 是主链路的职责。本模块遇到「只能走浏览器」
#     的候选就产出交接单（`browser_handoff`），带上已排好序的 URL。
#   · 不做 PDF → 文本：R①.5 要的是能进任务包的**正文文本**。
#     取到 PDF/HTML 但没有文本时，如实记录 `text=None`，
#     由既有的文本化流程处理，绝不把 PDF 字节当文本塞进任务包
#     （backfill 是按字数择优的，塞进去会**盖住**真正的全文）。
#
# 用法：
#   # 干跑：只打印路由判定，零网络
#   uv run python scripts/fulltext_router.py --plan 10.1016/j.cell.2014.03.011
#
#   # 单篇（必须声明 CDP 已失败，否则退出码 3）
#   uv run python scripts/fulltext_router.py --doi 10.1038/s41577-022-00763-8 \
#       --title "..." --cdp-failed --out <目录>
#
#   # 批量：JSON 数组 [{doi,title,pmcid,cdp_failed}, ...]
#   uv run python scripts/fulltext_router.py --from-json gap.json --out <目录> --json
#
#   uv run python scripts/fulltext_router.py --self-test
#
# 可移植配置：
#   PAPER_WORKDIR                   工作目录（缺省用 logutil.workdir()）
#   LIT_FLOW1_ALLOW_PUBLISHER_API   出版商 API 闸门（转交 fulltext_publishers）
#   LIT_DL_CONFIG_DIR               凭据/联系邮箱目录（只读）
#
# 退出码：0 = 通过（成功或全部如实交接）；1 = 自检失败 / 有最终失败；
#         2 = 用法错误；3 = 守卫拒绝（CDP 未失败就调用副链路 / mode=off）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import sys
import urllib.parse

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

import fetch_pacing as fp          # noqa: E402
import fulltext_credentials as creds  # noqa: E402
import fulltext_publishers as fpb  # noqa: E402
import fulltext_result as fr       # noqa: E402
import oa_candidates as oac        # noqa: E402
from fulltext_result import FullTextResult  # noqa: E402

try:
    import logutil                 # noqa: E402
except Exception:                  # noqa: BLE001
    logutil = None


# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════
# 与 nature-downloader/lib/cnki.mjs 逐条对齐（DEFAULT_CNKI_URL / CNKI_HOST_RE /
# CHINESE_RE）。移植而非重写：这两个正则一旦漂移，中文文献就会被送进
# 英文出版社的 OA 瀑布里空转一圈，最后报「取不到」。
DEFAULT_CNKI_URL = "https://kns.cnki.net/kns8s/defaultresult/index"
CNKI_HOST_RE = re.compile(r"(^|\.)(?:cnki\.net|cnki\.com\.cn)$", re.I)
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")

# 阶段 0 的取舍项（prepare_stage.py 的 OPTIONS 里同名 key）
PLAN_KEY = "fulltext_fallback"
MODES = ("off", "oa", "oa+api")
DEFAULT_MODE = "oa"

STAGE = "R"


def _workdir() -> pathlib.Path:
    env = (os.environ.get("PAPER_WORKDIR") or "").strip()
    if env:
        return pathlib.Path(env).resolve()
    if logutil is not None:
        try:
            return logutil.workdir()
        except Exception:  # noqa: BLE001
            pass
    return _HERE.parent


def _journal(action: str, **fields) -> None:
    if logutil is None:
        return
    try:
        logutil.journal(STAGE, action, **fields)
    except Exception:  # noqa: BLE001
        pass


def _log():
    if logutil is None:
        return None
    try:
        return logutil.get("fulltext_router")
    except Exception:  # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════
# 中文硬分流判据（nature lib/routing.mjs:24-29 的等价实现）
# ══════════════════════════════════════════════════════════════
def looks_chinese(text: str) -> bool:
    """题名里含 CJK 字符即算中文文献。

    为什么这么宽：判错的代价不对称。把中文文献误判成英文 → 它会在
    Unpaywall / PMC 里空转一圈后报「无 OA 副本」，用户看到的结论是
    「这篇取不到」，而真相是「该走 CNKI」。反之把英文误判成中文，
    用户会在交接单里看见一条明确的 CNKI 提示，一眼就能纠正。
    """
    return bool(CHINESE_RE.search(str(text or "")))


def is_cnki_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return bool(host and CNKI_HOST_RE.search(host))


def cnki_search_url(query: str, base: str = DEFAULT_CNKI_URL) -> str:
    """题名 → CNKI 检索页 URL（只构造 URL，不访问）。"""
    u = urllib.parse.urlparse(base)
    q = urllib.parse.parse_qsl(u.query)
    q.append(("kw", str(query or "")))
    return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))


def is_chinese_literature(article: dict) -> bool:
    """四条判据任一命中即为中文文献（顺序与 nature 一致）。"""
    a = article or {}
    if a.get("cnki"):
        return True
    if is_cnki_url(a.get("source_url") or a.get("url") or ""):
        return True
    lang = str(a.get("language") or "")
    if re.match(r"^(?:zh|zho|chi)(?:[-_]|$)", lang, re.I):
        return True
    return looks_chinese(a.get("title") or "")


# ══════════════════════════════════════════════════════════════
# 元数据降级（nature batch_download.mjs:521-523）
# ══════════════════════════════════════════════════════════════
def normalize_article(article: dict) -> dict:
    """补齐路由需要的字段；缺题名时**如实降级**而不是猜。

    nature 在 Crossref 查不到时降级成 `{title: doi, metadata_error}` —— 
    本模块不自己查 Crossref（题录由调用方给），但保留同款的降级语义：
    下游看到 `metadata_error` 就知道「这条路由是在缺题名的前提下做的」，
    中文硬分流那一判据因此失效，结论要打折看待。
    """
    a = dict(article or {})
    doi = oac.normalize_doi(str(a.get("doi") or ""))
    a["doi"] = doi
    title = str(a.get("title") or "").strip()
    if not title:
        a["title"] = doi
        a["metadata_error"] = "题名缺失 → 以 DOI 充当题名；中文硬分流判据不可用"
    return a


# ══════════════════════════════════════════════════════════════
# 路由判定（纯函数，零网络）—— nature lib/routing.mjs:39-49
# ══════════════════════════════════════════════════════════════
def choose_route(article: dict, *, mode: str | None = None) -> dict:
    """→ {provider, mode, reason, next}。**不发任何请求。**

    与 nature `chooseRoute` 的对应关系：
      isChineseLiterature        → cnki / handoff
      hasPublisherCredentials    → <publisher> / fetch
      article.isOa === true      → open_access / fetch
      publisher !== "other"      → <publisher> / confirm（API 兜底需显式授权）
      其余                        → web_access；在流程1 里那就是 CDP 本身 → cdp
    """
    a = normalize_article(article)
    m = (mode or plan_mode()[0]).lower()

    if is_chinese_literature(a):
        return {"provider": "cnki", "mode": "title_search",
                "reason": "chinese_literature", "next": "handoff",
                "search_url": cnki_search_url(a.get("title") or a.get("doi") or "")}

    prov = fpb.classify_doi(a.get("doi") or "")
    gate_open, gate_why = fpb.allow_publisher_api()
    cred_ok, cred_why = creds.is_usable(prov) if prov != "other" else (False, "")

    if prov != "other" and m == "oa+api" and gate_open and cred_ok:
        return {"provider": prov, "mode": "publisher_api",
                "reason": "publisher_api_credentials_available", "next": "fetch"}

    if m in ("oa", "oa+api"):
        return {"provider": "open_access", "mode": "oa_waterfall",
                "reason": "oa_waterfall_enabled", "next": "fetch"}

    if prov != "other":
        # API 兜底：闸门没开或凭据不可用 —— 需用户显式授权，不能替他决定
        return {"provider": prov, "mode": "publisher_api",
                "reason": "api_fallback_confirmation_required", "next": "confirm",
                "gate_open": gate_open, "gate_reason": gate_why,
                "credentials_usable": cred_ok, "credentials_reason": cred_why}

    return {"provider": "cdp", "mode": "browser",
            "reason": "other_publisher", "next": "cdp"}


def fallback_after_oa(article: dict, *, mode: str | None = None) -> dict:
    """OA 瀑布也没成之后的兜底判定（nature batch_download.mjs:584-641）。

    为什么不复用 `choose_route`：那个函数在 mode=oa/oa+api 时**总是**回答
    「走 OA 瀑布」—— 而这里的前提恰恰是 OA 瀑布已经走完了。拿它当兜底
    会让 `confirm`（API 需显式授权）这一支永远走不到，等于把 nature 的
    最后一步静默删掉。

    nature 的语义是：`chooseRoute` 的结果只有 `web_access` 才真的驱动浏览器，
    其余一律 `api_fallback_confirmation_required`。在流程1 里 `web_access`
    就是主链路的 CDP，而本模块的前提是**CDP 已经失败过**，所以那一支
    映射成「没有别的合法路线了」，而不是「再交给 CDP 一次」。
    """
    a = normalize_article(article)
    prov = fpb.classify_doi(a.get("doi") or "")
    gate_open, gate_why = fpb.allow_publisher_api()
    cred_ok, cred_why = creds.is_usable(prov) if prov != "other" else (False, "")
    if prov != "other":
        return {"provider": prov, "mode": "publisher_api",
                "reason": "api_fallback_confirmation_required", "next": "confirm",
                "gate_open": gate_open, "gate_reason": gate_why,
                "credentials_usable": cred_ok, "credentials_reason": cred_why}
    return {"provider": "none", "mode": "",
            "reason": "other_publisher_no_remaining_route", "next": "none"}


# ══════════════════════════════════════════════════════════════
# 阶段 0 的取舍
# ══════════════════════════════════════════════════════════════
def plan_mode(plan_path: pathlib.Path | None = None) -> tuple[str, str]:
    """读阶段 0 的 `fulltext_fallback`。→ (mode, 提示语)。

    提示语非空表示发生了**回退**（调用方负责打到 stderr）。
    回退一律退到 DEFAULT_MODE 而不是 `off`：与 reviews_prepare.plan_tier
    同款取舍 —— 找不到计划文件说明阶段 0 没跑，那该出声，
    但不该顺手把用户已经批准过的能力也一并关掉。
    """
    p = plan_path or (_workdir() / ".work_cdp" / "prepare_plan.json")
    if not p.exists():
        return DEFAULT_MODE, (f"[WARN] 找不到 {p}（阶段 0 未跑？）→ "
                              f"副链路模式回退默认 {DEFAULT_MODE}")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return DEFAULT_MODE, f"[WARN] {p.name} 解析失败（{e}）→ 回退默认 {DEFAULT_MODE}"
    raw = plan.get(PLAN_KEY) if isinstance(plan, dict) else None
    got = str(raw or "").strip().lower()
    if got not in MODES:
        return DEFAULT_MODE, (f"[WARN] {p.name} 的 {PLAN_KEY}={raw!r} 非法"
                              f"（应为 {'/'.join(MODES)}）→ 回退默认 {DEFAULT_MODE}")
    return got, ""


# ══════════════════════════════════════════════════════════════
# 产物取值：只认**文本**，不把 PDF 字节当正文
# ══════════════════════════════════════════════════════════════
def text_path_of(res: FullTextResult):
    """→ 可直接进任务包的正文 .txt 路径；没有就 None。

    JATS 路线的 .txt 由 `store_text_as_fulltext` 落在 .xml 旁边：
      · fulltext_publishers 把它写进 `extra["text_file"]`；
      · oa_candidates.fetch_candidate 没写进结果，但落盘路径是同名同目录
        的固定约定，故按 `.xml → .txt` 推。
    """
    if not res.ok or not res.file:
        return None
    p = pathlib.Path(res.file)
    if p.suffix.lower() == ".txt":
        return p if p.is_file() else None
    t = (res.extra or {}).get("text_file")
    cand = pathlib.Path(t) if t else p.with_suffix(".txt")
    return cand if cand.is_file() else None


def pdf_path_of(res: FullTextResult):
    if not res.ok or not res.file:
        return None
    p = pathlib.Path(res.file)
    return p if (p.suffix.lower() == ".pdf" and p.is_file()) else None


# ══════════════════════════════════════════════════════════════
# 主路由：routeDoiDownload 的等价实现
# ══════════════════════════════════════════════════════════════
def route_doi(article: dict, *, out_dir, mode: str | None = None,
              cdp_failed: bool | None = None, fetch_impl=None, guard=None,
              timeout: float = 60.0, want_arxiv: bool = False) -> FullTextResult:
    """一篇论文的完整路由。返回 `FullTextResult`，**任何**失败都返回结果对象。

    `fetch_impl(url, headers, timeout) -> (status, content_type, bytes)`：
    注入点，自检用它跑完全离线的通路验证。
    `cdp_failed` 必须显式给出 —— 不接受默认值，理由见不变量一。
    """
    out = pathlib.Path(out_dir)
    a = normalize_article(article)
    doi = a.get("doi") or ""
    m = (mode or plan_mode()[0]).lower()
    attempts: list[dict] = []

    # ── 不变量一：CDP 没失败就不该来这儿 ─────────────────────
    if cdp_failed is None:
        return FullTextResult.failed(
            "router", "route_disabled", reason="调用方未声明 CDP 是否已失败",
            extra={"invariant": "cdp_failed 必须显式传 True/False，不接受默认值"})
    if not cdp_failed:
        return FullTextResult.failed(
            "router", "route_disabled",
            reason="副链路只在 CDP 失败之后启用（主链路已有结果时不得绕过它）",
            extra={"invariant": "cdp_failed=False"})
    if m == "off":
        return FullTextResult.failed(
            "router", "route_disabled",
            reason=f"阶段 0 的 {PLAN_KEY}=off —— 副链路已被用户关闭")

    # ── ① 中文硬分流 ────────────────────────────────────────
    if is_chinese_literature(a):
        url = cnki_search_url(a.get("title") or doi)
        _journal("router.cnki_handoff", doi=doi, title=a.get("title", "")[:80])
        return FullTextResult.handoff(
            "cnki", "api_fallback_confirmation_required",
            reason="中文文献：CNKI 需机构 Web Access + 已认证会话，本流程不代登录",
            attempts=[{"source": "cnki", "url": url, "status": "handoff"}],
            extra={"next": "handoff", "search_url": url, "mode": "title_search",
                   "needs": "机构 Web Access + 已认证浏览器（CDP 主链路驱动）",
                   "remediation": "uv run python scripts/preflight_fallback.py "
                                  "查看 CNKI 路线是否具备前置"})

    # ── ② 出版商 API（api-first）────────────────────────────
    #   nature :550 —— `useApiFirst` 成立时先打 API，成功即返回，
    #   **不再判 OA**，并把这一事实记进 oa_status（否则下游会以为
    #   「这篇没有 OA 副本」，而真相是「根本没查」）。
    api_first_res = None
    if m == "oa+api":
        pre = choose_route(a, mode=m)
        if pre["reason"] == "publisher_api_credentials_available":
            site = pre["provider"]
            if guard is not None and guard.blocked(site):
                attempts.append({"source": site, "status": "site_blocked",
                                 "reason": "站点已熔断（跨轮累计）"})
            else:
                if guard is not None:
                    guard.pace(site)
                api_first_res = fpb.fetch_fulltext(doi, pre["provider"], out,
                                                   fetch_impl=fetch_impl, timeout=timeout)
                if guard is not None:
                    guard.record(site, api_first_res.ok, api_first_res.status)
                attempts.append({"source": site, "status": api_first_res.status,
                                 "reason": api_first_res.reason[:120]})
                if api_first_res.ok:
                    api_first_res.extra["oa_status"] = "not_checked_api_first"
                    api_first_res.extra["router"] = {"route": pre, "mode": m}
                    _journal("router.api_success", doi=doi, provider=site,
                             status=api_first_res.status)
                    return api_first_res

    # ── ③ OA 瀑布 ───────────────────────────────────────────
    oa_res: FullTextResult | None = None
    if m in ("oa", "oa+api"):
        ids = oac.resolve_ids(doi, fetch_impl, timeout=min(timeout, 30.0))
        pmcid = a.get("pmcid") or ids.get("pmcid")
        cands, audit = oac.collect_candidates(
            doi, pmcid, a.get("title") or "", fetch_impl, timeout=timeout,
            want_arxiv=want_arxiv)
        attempts.append({"source": "oa_inventory", "status": audit.get("assessment"),
                         "found": len(cands), "pmcid": pmcid or "",
                         "unpaywall_error": audit.get("unpaywall_error", "")})

        handoff_urls: list[dict] = []
        for c in cands:
            url = str(c.get("url") or "")
            site = fp.host_of_url(url)
            if guard is not None and guard.blocked(site):
                attempts.append({"source": c.get("source"), "url": url,
                                 "status": "site_blocked"})
                handoff_urls.append({"url": url, "source": c.get("source"),
                                     "why": "site_blocked"})
                continue
            if guard is not None:
                # 单线程串行调用，站点级串行由这里的顺序天然保证；
                # 若将来并发化，record() 必须挪进调用方的站点锁内（不变量②）。
                guard.pace(site)
            r = oac.fetch_candidate(c, out, allow_browser=True,
                                    is_oa=(audit.get("assessment") == "confirmed_oa"),
                                    fetch_impl=fetch_impl, timeout=timeout)
            if guard is not None:
                guard.record(site, r.ok, r.status)
            attempts.append({"source": c.get("source"), "url": url, "status": r.status,
                             "reason": (r.reason or "")[:120]})
            if r.ok:
                r.extra["oa_status"] = audit.get("assessment")
                r.extra["router"] = {"mode": m, "candidates": len(cands),
                                     "api_first": bool(api_first_res)}
                _journal("router.oa_success", doi=doi, status=r.status,
                         source=c.get("source"))
                return r
            if (r.extra or {}).get("next") == "browser":
                handoff_urls.append({"url": url, "source": c.get("source"),
                                     "why": r.reason[:120]})

        if handoff_urls:
            # 有合法候选、但都不在直连白名单内 → 交接给 CDP，附上已排好序的 URL。
            # 这不是失败：路线存在，只是「谁来取」的问题。
            oa_res = FullTextResult.handoff(
                "open_access", "browser_handoff",
                reason=f"{len(handoff_urls)} 个 OA 候选不在直连白名单内 → 交 CDP",
                attempts=attempts,
                extra={"next": "cdp", "oa_status": audit.get("assessment"),
                       "candidates": handoff_urls,
                       "policy": "§5.1 第二层：原文全文一律走 CDP"})
        elif not cands:
            st = ("oa_resolution_inconclusive"
                  if audit.get("assessment") == "unknown" else "oa_not_found")
            oa_res = FullTextResult.failed(
                "open_access", st, attempts=attempts,
                reason=audit.get("unpaywall_error") or "无合法 OA 候选",
                extra={"oa_status": audit.get("assessment")})
        else:
            oa_res = FullTextResult.failed(
                "open_access", "pdf_fetch_failed", attempts=attempts,
                reason="全部 OA 候选取回失败",
                extra={"oa_status": audit.get("assessment")})

    # ── ④ 兜底：API 需显式授权 / 如实认输 ────────────────────
    post = fallback_after_oa(a, mode=m)

    # 有具体候选 URL 的交接单比「去开 API 闸门」更可执行 —— 那些 URL 是
    # 主链路没试过的（PMC 仓库副本、出版社 OA 落地页），值得单独摆出来。
    if oa_res is not None and oa_res.status == "browser_handoff":
        if post["next"] == "confirm":
            oa_res.extra["api_fallback"] = _confirmation(post)
        oa_res.extra["route"] = post
        return oa_res

    if post["next"] == "confirm":
        _journal("router.confirm_required", doi=doi, provider=post["provider"])
        return FullTextResult.handoff(
            post["provider"], "api_fallback_confirmation_required",
            reason=("出版商 API 是最后一条路，但需用户显式授权："
                    f"闸门 {post.get('gate_reason', '')}；凭据 {post.get('credentials_reason', '')}"),
            attempts=attempts,
            extra={"next": "confirm", "confirmation": _confirmation(post),
                   "oa_result": (oa_res.to_dict() if oa_res else None)})

    base = oa_res or FullTextResult.failed(
        "router", "route_disabled", reason=f"{PLAN_KEY}={m} 下没有可走的路线")
    base.attempts = list(base.attempts or []) + [
        {"source": "router", "status": post["reason"], "provider": post["provider"]}]
    base.extra = dict(base.extra or {})
    # CDP 已经失败过（不变量一），所以这里**不**写 next="cdp" —— 那会是空话。
    base.extra["next"] = "none"
    base.extra["route"] = post
    return base


def _confirmation(post: dict) -> dict:
    """把「用户该做什么」写清楚 —— 不含任何秘钥，只给开关名与命令。"""
    prov = post.get("provider") or ""
    return {
        "env": fpb.ALLOW_ENV,
        "config": str(fpb.config_path()),
        "config_key": "allow_publisher_api",
        "gate_open": post.get("gate_open"),
        "gate_reason": post.get("gate_reason", ""),
        "credentials_usable": post.get("credentials_usable"),
        "credentials_reason": post.get("credentials_reason", ""),
        "configure_url": (creds.PROVIDERS.get(prov) or {}).get("config_url", ""),
        "plan_key": f"阶段 0 把 {PLAN_KEY} 设为 oa+api",
    }


def route_many(articles: list[dict], *, out_dir, mode: str | None = None,
               fetch_impl=None, guard=None, timeout: float = 60.0,
               want_arxiv: bool = False, log=None) -> list[dict]:
    """批量路由 → 记录列表（可直接写进 reviews_fallback.json）。

    记录里的 `path` **只**在有正文文本时才给出 —— 见「刻意不做的事」。
    """
    lg = log or _log()
    if guard is None:
        guard = fp.load_guard(log=lg)
    recs: list[dict] = []
    for a in articles:
        res = route_doi(a, out_dir=out_dir, mode=mode,
                        cdp_failed=bool(a.get("cdp_failed")),
                        fetch_impl=fetch_impl, guard=guard, timeout=timeout,
                        want_arxiv=want_arxiv)
        txt = text_path_of(res)
        pdf = pdf_path_of(res)
        chars = 0
        if txt:
            try:
                chars = len(txt.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                chars = 0
        rec = {
            "doi": oac.normalize_doi(str(a.get("doi") or "")),
            "title": str(a.get("title") or "")[:200],
            "path": str(txt) if txt else None,
            "chars": chars,
            "pdf": str(pdf) if pdf else None,
            "status": res.status,
            "kind": res.kind,
            "provider": res.provider,
            "next": (res.extra or {}).get("next", ""),
            "reason": res.reason,
            "oa_status": (res.extra or {}).get("oa_status", ""),
            "candidates": (res.extra or {}).get("candidates", []),
            "attempts": res.attempts,
        }
        recs.append(rec)
        if lg is not None:
            try:
                (lg.info if res.ok else lg.warn)(
                    f"  [{res.kind[:4]}] {rec['doi'] or '?':34} {res.status}"
                    + (f" → {rec['chars']:,} 字" if chars else ""))
            except Exception:  # noqa: BLE001
                pass
        _journal("router.route", doi=rec["doi"], status=res.status, kind=res.kind,
                 provider=res.provider, chars=chars)
    # 跨轮持久化在这里落盘，不依赖调用方记得调 —— 忘了就等于熔断计数每轮清零，
    # 那正是 fetch_pacing 要解决的问题（见该模块头部）。
    try:
        guard.save()
    except Exception:  # noqa: BLE001
        pass
    return recs


# ══════════════════════════════════════════════════════════════
# 自检（**完全离线**：一律注入假 fetch）
# ══════════════════════════════════════════════════════════════
_JATS = ('<?xml version="1.0"?><article><front><article-title>T</article-title>'
         '</front><body><sec><p>' + "word " * 1500 + '</p></sec></body></article>')
_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 400 + b"\n%%EOF\n"
_EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML"
_UNPAYWALL_OK = {
    "is_oa": True,
    "best_oa_location": {"url_for_pdf": _EPMC, "host_type": "repository",
                         "version": "publishedVersion", "license": "cc-by"},
    "oa_locations": [{"url": _EPMC, "host_type": "repository"},
                     {"url_for_pdf": "https://www.sciencedirect.com/x.pdf",
                      "host_type": "publisher", "license": "cc-by"}],
}


class _NoNet:
    """任何请求都算断言失败 —— 用来证明「守卫确实拦住了」。"""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        raise AssertionError(f"不该发请求：{url}")


class _Fake:
    """按 URL 关键词分派假响应；未命中返回 404（不抛，便于测失败路径）。"""

    def __init__(self, routes: dict, record: list | None = None):
        self.routes = routes
        self.record = record if record is not None else []

    def __call__(self, url, headers, timeout):
        self.record.append(url)
        for key, val in self.routes.items():
            if key in url:
                return val
        return 404, "text/plain", b"not found"


def _env_snapshot(keys) -> dict:
    return {k: os.environ.get(k) for k in keys}


def _env_restore(snap: dict) -> None:
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def self_test() -> int:
    bad = 0

    def chk(cond: bool, label: str, detail: str = "") -> None:
        nonlocal bad
        if not cond:
            bad += 1
        print(f"  {'[OK]' if cond else '[X] '} {label}" + (f" —— {detail}" if detail and not cond else ""))

    print("=== fulltext_router.py --self-test（零网络）===")
    print("\n① 中文硬分流判据（nature lib/routing.mjs:24-29 的等价实现）")
    chk(looks_chinese("基于深度学习的图像分割综述"), "中文题名 → looks_chinese")
    chk(not looks_chinese("A survey of deep learning"), "英文题名 → 非中文")
    chk(is_cnki_url("https://kns.cnki.net/kns8s/x"), "kns.cnki.net → CNKI URL")
    chk(is_cnki_url("https://www.cnki.com.cn/a"), "cnki.com.cn → CNKI URL")
    chk(not is_cnki_url("https://notcnki.net.example/a"), "后缀边界：notcnki.net.example 不算")
    chk(is_chinese_literature({"title": "中文题名", "doi": "10.1/x"}), "中文题名 → 硬分流")
    chk(is_chinese_literature({"title": "English", "language": "zh-CN"}), "language=zh-CN → 硬分流")
    chk(is_chinese_literature({"title": "English", "cnki": {"url": "x"}}), "带 cnki 字段 → 硬分流")
    chk(not is_chinese_literature({"title": "English", "language": "en"}), "英文 → 不硬分流")
    su = cnki_search_url("深度学习")
    chk("kw=" in su and "kns.cnki.net" in su, "cnki_search_url 带上 kw= 查询参数", f"实际：{su}")

    print("\n② 元数据降级（不猜题名，如实标注）")
    a = normalize_article({"doi": "https://doi.org/10.1016/j.cell.2014.03.011"})
    chk(a["doi"] == "10.1016/j.cell.2014.03.011", "DOI 归一化（剥掉 https://doi.org/）")
    chk(a["title"] == a["doi"] and "metadata_error" in a, "缺题名 → title=doi + metadata_error")

    print("\n③ 不变量一：CDP 未失败 → 守卫拒绝，且**一个请求都不发**")
    nonet = _NoNet()
    r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                  out_dir=pathlib.Path("."), mode="oa+api", cdp_failed=False,
                  fetch_impl=nonet)
    chk(r.status == "route_disabled" and r.kind == "FAILURE", "cdp_failed=False → route_disabled")
    chk(not nonet.calls, "守卫拦在发请求之前", f"实际发了 {nonet.calls}")
    r2 = route_doi({"doi": "10.1/x", "title": "t"}, out_dir=pathlib.Path("."),
                   mode="oa", cdp_failed=None, fetch_impl=nonet)
    chk(r2.status == "route_disabled" and "cdp_failed" in (r2.extra or {}).get("invariant", ""),
        "cdp_failed 未显式给出 → 同样拒绝（不接受默认值）")
    r3 = route_doi({"doi": "10.1/x", "title": "t"}, out_dir=pathlib.Path("."),
                   mode="off", cdp_failed=True, fetch_impl=nonet)
    chk(r3.status == "route_disabled" and PLAN_KEY in r3.reason, "mode=off → 拒绝")

    print("\n④ 中文硬分流 → HANDOFF（不代登录、不代取）")
    r = route_doi({"doi": "10.1/cnki", "title": "基于深度学习的综述"},
                  out_dir=pathlib.Path("."), mode="oa+api", cdp_failed=True,
                  fetch_impl=nonet)
    chk(r.kind == "HANDOFF" and r.provider == "cnki", "→ HANDOFF/cnki")
    chk("kns.cnki.net" in (r.extra or {}).get("search_url", ""), "交接单里带 CNKI 检索 URL")
    chk(not nonet.calls, "硬分流路径不发任何请求", f"实际发了 {nonet.calls}")

    print("\n⑤ 出版商 API 成功 → **跳过 OA**，并如实记录「没查过 OA」")
    import tempfile  # noqa: PLC0415
    tmpcfg = pathlib.Path(tempfile.gettempdir()) / f"fr_cfg_{os.getpid()}"
    tmpcfg.mkdir(parents=True, exist_ok=True)
    (tmpcfg / "credentials.json").write_text(
        json.dumps({"elsevier": {"api_key": "SELFTEST-KEY"}}), encoding="utf-8")
    (tmpcfg / "settings.json").write_text(
        json.dumps({"open_access": {"contact_email": "selftest@example.org"}}),
        encoding="utf-8")
    snap = _env_snapshot(["LIT_DL_CONFIG_DIR", "LIT_FLOW1_ALLOW_PUBLISHER_API",
                          "LIT_FLOW1_CONFIG", "PAPER_WORKDIR",
                          "PAPER_EMAIL", "UNPAYWALL_EMAIL"])
    outdir = tmpcfg / "out"
    try:
        os.environ["LIT_DL_CONFIG_DIR"] = str(tmpcfg)
        os.environ["LIT_FLOW1_ALLOW_PUBLISHER_API"] = "1"
        os.environ["LIT_FLOW1_CONFIG"] = str(tmpcfg / "flow1.json")
        os.environ["PAPER_WORKDIR"] = str(tmpcfg)
        # 联系邮箱只从 settings.json 读 —— 若外部环境里已有 PAPER_EMAIL，
        #   ⑥⑦ 两节的 Unpaywall 假响应就不会被走到，断言会变成假绿。
        os.environ.pop("PAPER_EMAIL", None)
        os.environ.pop("UNPAYWALL_EMAIL", None)

        calls: list[str] = []
        fake = _Fake({"api.elsevier.com": (200, "application/pdf", _PDF)}, calls)
        r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                      out_dir=outdir, mode="oa+api", cdp_failed=True, fetch_impl=fake)
        chk(r.ok and r.provider == "elsevier", "API 路线成功", f"实得 {r}")
        chk((r.extra or {}).get("oa_status") == "not_checked_api_first",
            "oa_status=not_checked_api_first（nature :560-567 同款语义）")
        chk(not any("unpaywall" in c for c in calls),
            "成功后**不再**查 Unpaywall", f"实际请求 {calls}")
        chk(pdf_path_of(r) is not None, "PDF 已落盘且可被 pdf_path_of 认出来")
        chk(text_path_of(r) is None, "PDF 没有正文文本 → text_path_of 返回 None（不拿字节冒充正文）")

        print("\n⑥ API 闸门关闭 → 走 OA 瀑布；白名单内候选才直连")
        os.environ["LIT_FLOW1_ALLOW_PUBLISHER_API"] = "0"
        calls2: list[str] = []
        fake2 = _Fake({
            "europepmc/webservices/rest/search": (
                200, "application/json",
                json.dumps({"resultList": {"result": [
                    {"pmcid": "PMC1", "pmid": "1", "inEPMC": "Y", "isOpenAccess": "Y"}]}}).encode()),
            "unpaywall": (200, "application/json", json.dumps(_UNPAYWALL_OK).encode()),
            "europepmc/webservices/rest/PMC1/fullTextXML": (
                200, "application/xml", _JATS.encode("utf-8")),
        }, calls2)
        r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                      out_dir=outdir, mode="oa+api", cdp_failed=True, fetch_impl=fake2)
        chk(r.ok, "OA 瀑布取回成功", f"实得 {r}")
        chk(not any("api.elsevier.com" in c for c in calls2),
            "闸门关闭时不打出版商 API", f"实际请求 {calls2}")
        txt = text_path_of(r)
        chk(txt is not None and txt.is_file(), "JATS → 产出可进任务包的 .txt")
        if txt:
            n = len(txt.read_text(encoding="utf-8", errors="replace"))
            chk(n > 1000, f".txt 正文长度 {n:,} 字（>1000）")

        print("\n⑦ 白名单外的候选 → 交接单（browser_handoff），绝不换域名再试")
        up_no_epmc = dict(_UNPAYWALL_OK)
        up_no_epmc["best_oa_location"] = {
            "url_for_pdf": "https://www.sciencedirect.com/x.pdf",
            "host_type": "publisher", "license": "cc-by"}
        up_no_epmc["oa_locations"] = [
            {"url_for_pdf": "https://www.sciencedirect.com/x.pdf", "host_type": "publisher"}]
        calls3: list[str] = []
        fake3 = _Fake({
            "europepmc/webservices/rest/search": (
                200, "application/json",
                json.dumps({"resultList": {"result": []}}).encode()),
            "unpaywall": (200, "application/json", json.dumps(up_no_epmc).encode()),
        }, calls3)
        r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                      out_dir=outdir, mode="oa", cdp_failed=True, fetch_impl=fake3)
        chk(r.status == "browser_handoff" and r.kind == "HANDOFF",
            "→ HANDOFF/browser_handoff", f"实得 {r}")
        chk((r.extra or {}).get("next") == "cdp", "next=cdp（交回主链路）")
        cand_urls = [c["url"] for c in (r.extra or {}).get("candidates") or []]
        chk(any("sciencedirect.com" in u for u in cand_urls),
            "交接单里带上了已排序的候选 URL", f"实得 {cand_urls}")
        chk(not any("sciencedirect.com" in c for c in calls3),
            "**没有**对白名单外域名发过请求", f"实际请求 {calls3}")

        print("\n⑧ API 兜底需显式授权（nature :618-641 的等价语义）")
        os.environ.pop("LIT_FLOW1_ALLOW_PUBLISHER_API", None)
        (tmpcfg / "credentials.json").write_text("{}", encoding="utf-8")
        # OA 查询全 404（EPMC / Unpaywall 都探不到）→ 瀑布无候选 → 落到兜底。
        #   这里**不能**用 _NoNet：OA 瀑布发查询类请求是合法的（第一层），
        #   用「一发请求就断言失败」的桩会把正常通路误判成违规。
        dead = _Fake({}, [])
        r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                      out_dir=outdir, mode="oa", cdp_failed=True, fetch_impl=dead)
        chk(r.status == "api_fallback_confirmation_required" and r.kind == "HANDOFF",
            "OA 走空 + 出版商 API 未授权 → HANDOFF/api_fallback_confirmation_required",
            f"实得 {r}")
        conf = (r.extra or {}).get("confirmation") or {}
        chk(conf.get("env") == fpb.ALLOW_ENV and conf.get("config_key") == "allow_publisher_api",
            "交接单给出开关名与 config 键（用户能照着开）", f"实得 {conf}")
        chk(conf.get("configure_url", "").startswith("https://"),
            "交接单给出该出版商的申请页", f"实得 {conf.get('configure_url')}")
        chk(not any("api.elsevier.com" in c for c in dead.record),
            "未授权时**没有**偷打出版商 API", f"实际请求 {dead.record}")

        dead2 = _Fake({}, [])
        r = route_doi({"doi": "10.9999/unknown-prefix", "title": "Unknown publisher"},
                      out_dir=outdir, mode="oa", cdp_failed=True, fetch_impl=dead2)
        chk(r.status in ("oa_not_found", "oa_resolution_inconclusive")
            and (r.extra or {}).get("next") == "none",
            "非受支持出版商 + OA 走空 → 如实认输（next=none，不谎称还能交回 CDP）",
            f"实得 {r.status} / next={(r.extra or {}).get('next')}")

        r = route_doi({"doi": "10.1016/j.cell.2014.03.011", "title": "Cell paper"},
                      out_dir=outdir, mode="off", cdp_failed=True, fetch_impl=_NoNet())
        chk(r.status == "route_disabled", "mode=off → 一律拒绝")

        print("\n⑨ 纯判定 choose_route / fallback_after_oa（零网络）")
        chk(choose_route({"doi": "10.1/x", "title": "中文"}, mode="oa")["provider"] == "cnki",
            "choose_route：中文 → cnki")
        chk(choose_route({"doi": "10.9999/unknown", "title": "English"},
                         mode="oa")["provider"] == "open_access",
            "choose_route：mode=oa → open_access 瀑布")
        chk(choose_route({"doi": "10.9999/unknown", "title": "English"},
                         mode="off")["next"] == "cdp",
            "choose_route：mode=off + 未知出版商 → 交回 CDP")
        chk(fallback_after_oa({"doi": "10.1016/j.x", "title": "English"},
                              mode="oa")["next"] == "confirm",
            "fallback_after_oa：受支持出版商 → 需显式授权")
        chk(fallback_after_oa({"doi": "10.9999/x", "title": "English"},
                              mode="oa")["next"] == "none",
            "fallback_after_oa：其它出版商 → 没有剩余路线")

        print("\n⑩ plan_mode 回退（找不到计划文件要出声，但不顺手关能力）")
        m, note = plan_mode(tmpcfg / "nope.json")
        chk(m == DEFAULT_MODE and "[WARN]" in note, "缺文件 → 回退默认 + [WARN]")
        pj = tmpcfg / "prepare_plan.json"
        pj.write_text(json.dumps({PLAN_KEY: "oa+api"}), encoding="utf-8")
        chk(plan_mode(pj) == ("oa+api", ""), "读到合法值")
        pj.write_text(json.dumps({PLAN_KEY: "bogus"}), encoding="utf-8")
        m, note = plan_mode(pj)
        chk(m == DEFAULT_MODE and "非法" in note, "非法值 → 回退默认并说明")
    finally:
        _env_restore(snap)
        import shutil  # noqa: PLC0415
        shutil.rmtree(tmpcfg, ignore_errors=True)

    print(f"\n结果：{'PASS' if not bad else f'FAIL（{bad} 项）'}")
    return 0 if not bad else 1


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fulltext_router.py",
        description="全文获取副链路的路由编排层（CDP 失败后才启用）。",
        epilog="退出码：0 = 通过；1 = 自检失败/有最终失败；2 = 用法错误；3 = 守卫拒绝。")
    ap.add_argument("--plan", metavar="DOI", nargs="*", default=None,
                    help="干跑：只打印路由判定，零网络（可给多个 DOI）")
    ap.add_argument("--doi", help="单篇路由的 DOI")
    ap.add_argument("--title", default="", help="题名（中文硬分流判据要用）")
    ap.add_argument("--pmcid", default="", help="已知 PMCID（可省一次 EPMC 查询）")
    ap.add_argument("--cdp-failed", action="store_true",
                    help="声明主链路 CDP 已对这篇失败 —— **不给就拒绝执行**（不变量一）")
    ap.add_argument("--from-json", type=pathlib.Path,
                    help='批量：JSON 数组 [{"doi","title","pmcid","cdp_failed"}, ...]')
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="产物目录（默认 <workdir>/.work_cdp/fallback）")
    ap.add_argument("--report", type=pathlib.Path, default=None,
                    help="把记录写成 JSON（默认 <workdir>/.work_cdp/reviews_fallback.json）")
    ap.add_argument("--mode", choices=list(MODES), default=None,
                    help=f"覆盖阶段 0 的 {PLAN_KEY}（缺省读 prepare_plan.json）")
    ap.add_argument("--want-arxiv", action="store_true",
                    help="OA 瀑布里加查 arXiv（按题名匹配）")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--site-fail-limit", type=int, default=2,
                    help="站点连续受阻几次后熔断（跨轮持久化；默认 2）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--self-test", action="store_true", help="零网络自检")
    return ap


def _load_articles(a) -> list[dict]:
    if a.from_json:
        try:
            data = json.loads(a.from_json.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[X] --from-json 读取失败：{e}", file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(data, list):
            print("[X] --from-json 的内容必须是 JSON 数组", file=sys.stderr)
            raise SystemExit(2)
        return [dict(x) for x in data if isinstance(x, dict)]
    if a.doi:
        return [{"doi": a.doi, "title": a.title, "pmcid": a.pmcid,
                 "cdp_failed": bool(a.cdp_failed)}]
    return []


def main() -> int:
    _fix_streams()
    ap = build_parser()
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    mode, note = (a.mode, "") if a.mode else plan_mode()
    if note:
        print(note, file=sys.stderr)

    # ── 干跑 ────────────────────────────────────────────────
    if a.plan is not None:
        dois = a.plan or ([a.doi] if a.doi else [])
        if not dois:
            print("[X] --plan 需要至少一个 DOI", file=sys.stderr)
            return 2
        rows = []
        for d in dois:
            art = normalize_article({"doi": d, "title": a.title if len(dois) == 1 else ""})
            r = choose_route(art, mode=mode)
            rows.append({"doi": art["doi"], **{k: v for k, v in r.items() if k != "provider"},
                         "provider": r["provider"],
                         "metadata_error": art.get("metadata_error", "")})
        if a.json:
            print(json.dumps({"mode": mode, "routes": rows}, ensure_ascii=False, indent=1))
        else:
            print(f"\n副链路路由判定（干跑，零网络）· mode={mode}")
            print("-" * 74)
            for r in rows:
                print(f"  {r['doi'] or '?':34} → {r['provider']:<14} "
                      f"[{r['next']}] {r['reason']}")
                if r.get("search_url"):
                    print(f"      CNKI 检索页：{r['search_url']}")
                if r.get("metadata_error"):
                    print(f"      [WARN] {r['metadata_error']}")
            print("-" * 74)
            print("  干跑只回答「该走哪条路」；真的取要加 --cdp-failed。")
        return 0

    articles = _load_articles(a)
    if not articles:
        print("[X] 需要 --doi 或 --from-json（或用 --plan 干跑）", file=sys.stderr)
        return 2
    if not all(x.get("cdp_failed") for x in articles):
        n = sum(1 for x in articles if not x.get("cdp_failed"))
        print(f"[STOP] {n} 篇未声明 --cdp-failed / cdp_failed=true。\n"
              f"       副链路只在主链路 CDP 失败之后启用（不变量一）。\n"
              f"       确认这些 DOI 确实已被 download_all_fulltexts.py 试过且失败后，"
              f"再显式声明。", file=sys.stderr)
        return 3
    if mode == "off":
        print(f"[STOP] 阶段 0 的 {PLAN_KEY}=off —— 副链路已被关闭。\n"
              f"       要启用请在阶段 0 把它设为 oa 或 oa+api。", file=sys.stderr)
        return 3

    wd = _workdir()
    out = a.out or (wd / ".work_cdp" / "fallback")
    out.mkdir(parents=True, exist_ok=True)
    report = a.report or (wd / ".work_cdp" / "reviews_fallback.json")

    lg = _log()
    guard = fp.load_guard(wd=wd, limit=max(1, a.site_fail_limit), log=lg)
    recs = route_many(articles, out_dir=out, mode=mode, guard=guard,
                      timeout=a.timeout, want_arxiv=a.want_arxiv, log=lg)

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")

    n_ok = sum(1 for r in recs if r["kind"] == "SUCCESS")
    n_ho = sum(1 for r in recs if r["kind"] == "HANDOFF")
    n_fa = sum(1 for r in recs if r["kind"] == "FAILURE")
    if not a.json:
        print(f"\n副链路：{len(recs)} 篇 · 成功 {n_ok} · 交接 {n_ho} · 失败 {n_fa}")
        for r in recs:
            if r["kind"] != "SUCCESS":
                print(f"  [{r['kind'][:4]}] {r['doi'] or '?':34} {r['status']}"
                      f"{' → ' + r['next'] if r['next'] else ''}")
                if r["reason"]:
                    print(f"         {r['reason'][:110]}")
        print(f"记录：{report}")
        if n_ho:
            print("[INFO] 「交接」不是失败：路线存在，只是本模块不越权去取。"
                  "按 next 字段交回 CDP 或请用户授权。")
    else:
        print(json.dumps({"mode": mode, "summary": {"total": len(recs), "success": n_ok,
                                                    "handoff": n_ho, "failure": n_fa},
                          "report": str(report), "records": recs},
                         ensure_ascii=False, indent=1))
    return 1 if n_fa else 0


if __name__ == "__main__":
    sys.exit(main())
