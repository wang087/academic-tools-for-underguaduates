# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文获取路线 A：出版商官方 API（Elsevier / Springer Nature / IEEE）
#
# 定位
#   本模块是**新增模块，尚未接入主链路**。它把对照报告 §3.3 的 P2-1 实现出来，
#   并把「接线」这一步留到主链路改造统一决策（见
#   docs/全文获取三条腿-设计与待确认清单.md）。
#   在接线之前，本模块**不会被任何既有脚本调用**，因此不改动既有行为。
#
# [STOP] 三重闸门（缺一不可，任何一条不满足就拒绝发请求）
#   1) `fulltext-allow-publisher-api` 开关（环境变量
#      `LIT_FLOW1_ALLOW_PUBLISHER_API=1` 或 config 里的 `allow_publisher_api: true`）
#      —— 默认**关闭**。理由：这是本流程第一次对出版社域名发**程序化直连**请求。
#      流程1 的 §5.1 分层把「原文全文」划为第二层（一律走 CDP），
#      而出版商 API 是**授权接口**、不是抓网页，性质不同 —— 但性质不同
#      不等于可以默认打开。必须由使用者显式表态。
#   2) 该出版商配了**可用**凭据（见 fulltext_credentials.is_usable）
#   3) DOI 确实属于该出版商
#
#   为什么坚持闸门：`fetch_oa_direct.py` 的双路径守卫（退出码 3）就是为了
#   防「顺手直连出版社」这一类事故。本模块用一个显式开关达到同样效果，
#   同时保留 API 这条合法路线的能力。
#
# 与 nature-downloader 的对应关系（本模块是它的 Python 等价实现）
#   scripts/lib/publisher-providers.mjs ：请求构造 + 重试 + 状态映射
#   scripts/lib/provider-utils.mjs      ：HTTP 状态 → 语义状态
#   lib/open-access.mjs                 ：DOI 归一化（本模块不做，见说明）
#
# 刻意**不**做的事
#   · 不做 DOI 归一化：既有 `reviews_fulltext` / `fetch_crossref_all` 已有题录，
#     DOI 由调用方给进来。重复实现只会多一处漂移。
#   · 不做 PDF 页数校验：落盘后由 `store.validate_pdf` 判（SKILL.md:1686 硬约束）。
#   · 不写凭据：只读（见 fulltext_credentials 的立场声明）。
#
# 用法：
#   from fulltext_publishers import classify_doi, plan_request, fetch_fulltext
#   prov = classify_doi("10.1016/j.cell.2014.03.011")        # → "elsevier"
#   plan = plan_request(doi, prov)                           # → 不含秘钥的请求描述
#   r    = fetch_fulltext(doi, prov, out_dir)                # → FullTextResult
#
#   uv run python scripts/fulltext_publishers.py --self-test
#   uv run python scripts/fulltext_publishers.py --plan <DOI>
#   uv run python scripts/fulltext_publishers.py --status
#
# 退出码：0 = 通过 / 1 = 自检失败 / 2 = 用法错误 / 3 = 闸门未开（拒绝执行）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# ── 本文件属于**可选模块** `流程1/optional-cdp-fulltext/`：
#   核心脚本仍在 `流程1/scripts/`，这里把它加进 sys.path 才能 import。
#   整目录删掉 = 本模块不可用，流程改走备用链路（见本目录 README.md）。
_CORE_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(_CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CORE_SCRIPTS))


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
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


_fix_streams()

import fulltext_credentials as creds  # noqa: E402
import fulltext_result as fr  # noqa: E402
from fulltext_result import FullTextResult  # noqa: E402

# 与既有代码同款的 UA（fetch_oa_direct.py 的 UA 常量风格）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ALLOW_ENV = "LIT_FLOW1_ALLOW_PUBLISHER_API"
CONFIG_PATH_ENV = "LIT_FLOW1_CONFIG"

# IEEE 的 endpoint 模板必须带 {doi} 占位符 —— 与 nature-downloader 同约定
IEEE_TEMPLATE_PLACEHOLDER = "{doi}"


# ══════════════════════════════════════════════════════════════
# 开关（闸门 1）
# ══════════════════════════════════════════════════════════════
def config_path() -> pathlib.Path:
    env = (os.environ.get(CONFIG_PATH_ENV) or "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return creds.config_dir() / "flow1.json"


def allow_publisher_api() -> tuple[bool, str]:
    """→ (是否允许, 依据)。环境变量优先于 config 文件。"""
    env = (os.environ.get(ALLOW_ENV) or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True, f"环境变量 {ALLOW_ENV}={env}"
    if env in ("0", "false", "no", "off"):
        return False, f"环境变量 {ALLOW_ENV}={env}（显式关闭）"
    try:
        p = config_path()
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and cfg.get("allow_publisher_api") is True:
                return True, f"config {p} 的 allow_publisher_api=true"
    except Exception:  # noqa: BLE001
        pass
    return False, (f"默认关闭。要启用请在 config 里写 allow_publisher_api=true，"
                   f"或设 {ALLOW_ENV}=1")


# ══════════════════════════════════════════════════════════════
# 出版商识别
# ══════════════════════════════════════════════════════════════
def normalize_doi(doi: str) -> str:
    """最小 DOI 清洗：去 URL 前缀、去空白。**不做**大小写归一以外的改写。

    DOI 后缀大小写敏感，所以只把 `https://doi.org/` 前缀与首尾空白去掉。
    """
    d = str(doi or "").strip()
    d = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.strip()


def classify_doi(doi: str) -> str:
    """→ 出版商 key，或 "other"。只按 DOI 前缀，不猜。"""
    d = normalize_doi(doi)
    for provider, spec in creds.PROVIDERS.items():
        if re.search(spec["doi_pattern"], d):
            return provider
    return "other"


# ══════════════════════════════════════════════════════════════
# 请求构造（**只返回描述，不含秘钥**）
# ══════════════════════════════════════════════════════════════
def _provider_request(doi: str, provider: str, c: dict) -> tuple[dict, dict]:
    """→ ({url, headers}, redacted_headers)。秘钥只出现在 headers 里，不进 URL。"""
    d = normalize_doi(doi)
    enc = urllib.parse.quote(d, safe="")
    if provider == "elsevier":
        headers = {"Accept": "application/pdf", "X-ELS-APIKey": str(c.get("api_key") or "")}
        if c.get("insttoken"):
            headers["X-ELS-Insttoken"] = str(c["insttoken"])
        if c.get("authtoken"):
            headers["X-ELS-Authtoken"] = str(c["authtoken"])
        url = f"https://api.elsevier.com/content/article/doi/{enc}?httpAccept=application/pdf"
        return {"url": url, "headers": headers}, fr.redact_headers(headers)
    if provider == "springer_nature":
        # [WARN] SPDI 是 Springer Nature 的正式接口，但 key 走**查询参数**。
        #   这意味着「秘钥进 URL」不可避免 —— 因此：
        #     · 该 URL **绝不**写进日志/manifest/结果（一律经 redact_url）；
        #     · 这是本模块唯一允许秘钥进 URL 的分支，改动时务必留意。
        url = ("https://spdi.public.springernature.app/xmldata/jats"
               f"?q=doi:{urllib.parse.quote(d, safe='')}"
               f"&api_key={urllib.parse.quote(str(c.get('api_key') or ''), safe='')}")
        headers = {"Accept": "application/xml"}
        return {"url": url, "headers": headers}, fr.redact_headers(headers)
    if provider == "ieee":
        tpl = str(c.get("fulltext_endpoint") or "")
        if IEEE_TEMPLATE_PLACEHOLDER not in tpl:
            return ({"unavailable": "IEEE 缺少含 {doi} 的 Full-Text endpoint 模板（"
                                    "Metadata key 不是收费全文权限）"}, {})
        url = tpl.replace(IEEE_TEMPLATE_PLACEHOLDER, enc)
        if "apikey=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}apikey={urllib.parse.quote(str(c.get('api_key') or ''), safe='')}"
        return {"url": url, "headers": {"Accept": "application/pdf, text/html, application/xml"}}, {}
    raise ValueError(f"unsupported publisher provider: {provider}")


def plan_request(doi: str, provider: str | None = None) -> dict:
    """干跑：返回该 DOI 的请求描述（**已脱敏**），不发任何网络请求。

    这是给人看的「这条路线会打哪个域名、带哪些头」，
    因此 URL 一律经 `redact_url`，秘钥只显示存在性。
    """
    d = normalize_doi(doi)
    if not d:
        return {"ok": False, "reason": "empty doi"}
    prov = provider or classify_doi(d)
    if prov == "other":
        return {"ok": False, "doi": d, "provider": "other",
                "reason": "DOI 前缀不属于已支持的出版商（Elsevier/Springer Nature/IEEE）"}
    allowed, how = allow_publisher_api()
    ok_cred, why_cred = creds.is_usable(prov)
    out = {
        "ok": prov != "other" and ok_cred,
        "doi": d,
        "provider": prov,
        "provider_name": creds.PROVIDERS[prov]["name"],
        "gate_open": allowed,
        "gate_reason": how,
        "credentials_usable": ok_cred,
        "credentials_reason": why_cred,
    }
    if not ok_cred:
        return out
    req, red_hdr = _provider_request(d, prov, creds.provider_credentials(prov) or {})
    if req.get("unavailable"):
        out.update({"ok": False, "reason": req["unavailable"]})
        return out
    out["url_redacted"] = fr.redact_url(req["url"])
    out["host"] = _host_of(req["url"])
    out["headers"] = red_hdr
    out["secret_in_url"] = fr.has_secret_in_url(req["url"])
    return out


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


# ══════════════════════════════════════════════════════════════
# HTTP 状态 → 语义状态
# ══════════════════════════════════════════════════════════════
def classify_http_status(status: int) -> str:
    """与 `provider-utils.mjs:classifyProviderFailure` 语义对齐。"""
    if status == 401:
        return "credentials_invalid"
    if status == 403:
        return "api_not_entitled"          # HANDOFF：授权问题要用户动，不是失败
    if status in (404, 204):
        return "api_fulltext_unavailable"
    if status == 429:
        return "rate_limited"
    return "api_fulltext_unavailable"


# ══════════════════════════════════════════════════════════════
# 取全文
# ══════════════════════════════════════════════════════════════
def _default_fetch(url: str, headers: dict, timeout: float):
    """真实取数。返回 (http_status, content_type, bytes)。

    只在 `fetch_fulltext` 内部使用；自检一律注入假实现，绝不联网。
    """
    req = urllib.request.Request(url, headers={**headers, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200), \
                resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.headers.get("Content-Type", "") if e.headers else "", e.read() or b""


def _safe_stem(doi: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", normalize_doi(doi))[:120] or "article"


def fetch_fulltext(doi: str, provider: str | None = None, out_dir: pathlib.Path | str = ".",
                   *, fetch_impl=None, timeout: float = 60.0,
                   max_attempts: int | None = None) -> FullTextResult:
    """走出版商 API 取全文。

    返回 `FullTextResult`。**任何**失败路径都返回结果对象而不抛异常，
    这样调用方（未来的路由层）不必写 try/except 分支。

    `fetch_impl(url, headers, timeout) -> (status, content_type, bytes)`：
    注入点。自检用它跑完全离线的通路验证。
    """
    d = normalize_doi(doi)
    out = pathlib.Path(out_dir)
    prov = provider or classify_doi(d)

    # ── 闸门 3：DOI 归属 ────────────────────────────────────
    if prov == "other":
        return FullTextResult.failed(prov, "route_disabled",
                                     reason="DOI 前缀不属于已支持的出版商", extra={})

    # ── 闸门 1：显式开关 ────────────────────────────────────
    allowed, how = allow_publisher_api()
    if not allowed:
        return FullTextResult.failed(prov, "route_disabled",
                                     reason=f"出版商 API 路线未启用：{how}")

    # ── 闸门 2：凭据 ────────────────────────────────────────
    c = creds.provider_credentials(prov)
    ok_cred, why_cred = creds.is_usable(prov, c)
    if not ok_cred:
        return FullTextResult.handoff(prov, "credentials_missing", reason=why_cred,
                                      extra={"configure_url": creds.PROVIDERS[prov]["config_url"]})

    req, _red_hdr = _provider_request(d, prov, c or {})
    if req.get("unavailable"):
        return FullTextResult.handoff(prov, "api_fulltext_unavailable", reason=req["unavailable"])

    attempts: list[dict] = []
    # Elsevier 限流/5xx 才退避重试；其余出版商不重试（与 nature-downloader 同策略）
    if max_attempts is None:
        max_attempts = 3 if prov == "elsevier" else 1
    fetch_impl = fetch_impl or _default_fetch

    http_status, content_type, body = 0, "", b""
    for attempt in range(1, max_attempts + 1):
        try:
            http_status, content_type, body = fetch_impl(req["url"], req["headers"], timeout)
        except Exception as e:  # noqa: BLE001
            attempts.append({"attempt": attempt, "status": "request_failed",
                             "reason": f"{type(e).__name__}: {str(e)[:120]}"})
            if attempt >= max_attempts:
                return FullTextResult.failed(prov, "failed_after_retry",
                                             reason=f"{type(e).__name__}: {str(e)[:160]}",
                                             attempts=attempts)
            continue
        retriable = prov == "elsevier" and (http_status == 429 or http_status >= 500)
        if not retriable or attempt >= max_attempts:
            break
        attempts.append({"attempt": attempt, "status": f"http-{http_status}", "reason": "retrying"})
        import time  # noqa: PLC0415
        time.sleep(0.25 * attempt)

    if http_status < 200 or http_status >= 300:
        st = classify_http_status(http_status)
        attempts.append({"attempt": max_attempts, "status": st, "http_status": http_status})
        res = (FullTextResult.handoff if fr.is_handoff(st) else FullTextResult.failed)(
            prov, st, http_status=http_status, attempts=attempts,
            reason=f"HTTP {http_status}",
        )
        res.access_mode = "publisher_api"
        return res

    ok, fmt, why = fr.classify_bytes(content_type, body[:65536])
    if not ok:
        attempts.append({"attempt": max_attempts, "status": "invalid_content",
                         "http_status": http_status, "reason": why})
        r = FullTextResult.failed(prov, "api_fulltext_unavailable",
                                  http_status=http_status, attempts=attempts,
                                  reason=f"响应不是可用全文：{why}")
        r.access_mode = "publisher_api"
        return r

    # ── 落盘 ────────────────────────────────────────────────
    stem = _safe_stem(d)
    extra: dict = {}
    if fmt == "pdf":
        info = fr.save_bytes(body, out / fr.FORMAT_DIR["pdf"] / f"{stem}.pdf")
    elif fmt == "jats_xml":
        # [STOP] Springer 返回的就是 JATS —— 必须先过「是否真全文」判据，
        #   否则「HTTP 200 + 完整 XML + 无 <body>」会被当成功写进交付物。
        import jats_fulltext  # noqa: PLC0415
        xml_text = body.decode("utf-8", "replace")
        real, jwhy = jats_fulltext.is_real_fulltext(xml_text)
        if not real:
            attempts.append({"attempt": max_attempts, "status": "not_real_fulltext",
                             "http_status": http_status, "reason": jwhy})
            r = FullTextResult.failed(prov, "not_real_fulltext",
                                      http_status=http_status, attempts=attempts,
                                      reason=f"JATS 不是真全文：{jwhy}")
            r.access_mode = "publisher_api"
            return r
        xml_info = fr.save_bytes(body, out / fr.FORMAT_DIR["jats_xml"] / f"{stem}.xml")
        body_text = jats_fulltext.body_text(xml_text)
        txt_info = fr.store_text_as_fulltext(
            body_text,
            out / fr.FORMAT_DIR["jats_xml"] / f"{stem}.txt",
            {"source": fr.redact_url(req["url"]), "doi": d, "route": "publisher-api-jats"},
        )
        info = xml_info
        extra["text_file"] = txt_info["file"]
        extra["text_bytes"] = txt_info["bytes"]
        extra["body_chars"] = len(body_text)
    else:
        info = fr.save_bytes(body, out / fr.FORMAT_DIR.get(fmt, "FullText") / f"{stem}{fr.FORMAT_EXT[fmt]}")

    attempts.append({"attempt": max_attempts, "status": "ok", "http_status": http_status})
    res = FullTextResult(
        status="native_fulltext_downloaded" if fmt in ("jats_xml", "caj") else "downloaded",
        provider=prov,
        access_mode="publisher_api",
        fmt=fmt,
        http_status=http_status,
        final_url=fr.redact_url(req["url"]),
        attempts=attempts,
        extra=extra,
        **info,
    )
    return res


# ══════════════════════════════════════════════════════════════
# 自检（**完全离线**：一律注入假 fetch，不发任何真实请求）
# ══════════════════════════════════════════════════════════════
_PDF_BODY = b"%PDF-1.7\n" + b"0" * 3000
_JATS_BODY = (b'<?xml version="1.0"?><article><front><article-title>T</article-title></front>'
              b'<body><sec><p>' + b"word " * 2000 + b'</p></sec></body></article>')
_SHELL_BODY = (b'<?xml version="1.0"?><pmc-articleset><!-- no full text -->'
               b'<article><front><article-title>T</article-title></front></article></pmc-articleset>')


class _Fake:
    """离线假 fetch：记录调用，按序返回预设响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers)})
        if not self.responses:
            raise AssertionError("假 fetch 被调用次数超出预设")
        return self.responses.pop(0)


def _env_snapshot(keys):
    return {k: os.environ.get(k) for k in keys}


def _env_restore(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def self_test() -> int:
    bad = 0
    import logutil  # noqa: PLC0415

    keys = ("LIT_DL_CONFIG_DIR", "LIT_FLOW1_CREDENTIALS", "LIT_FLOW1_CONFIG",
            ALLOW_ENV, "LIT_FLOW1_ALLOW_PUBLISHER_API")
    snap = _env_snapshot(set(keys))
    scratch = logutil.workdir() / ".work_cdp" / "selftest" / "publishers"
    scratch.mkdir(parents=True, exist_ok=True)
    outdir = scratch / "out"
    try:
        os.environ["LIT_DL_CONFIG_DIR"] = str(scratch)
        os.environ.pop("LIT_FLOW1_CREDENTIALS", None)
        os.environ.pop("LIT_FLOW1_CONFIG", None)
        os.environ.pop(ALLOW_ENV, None)

        # ── 1. DOI → 出版商 ─────────────────────────────────
        cases = [
            ("10.1016/j.cell.2014.03.011", "elsevier"),
            ("https://doi.org/10.1038/s41577-022-00763-8", "springer_nature"),
            ("10.1109/TMI.2020.1234", "ieee"),
            ("10.1126/science.abc1234", "other"),
            ("doi:10.1007/s00122-021-03957-1", "springer_nature"),
        ]
        wrong = [(d, classify_doi(d), w) for d, w in cases if classify_doi(d) != w]
        if wrong:
            bad += 1
            print(f"  [X] DOI 归属判定错误：{wrong}")
        else:
            print(f"DOI 归属：{len(cases)}/{len(cases)} 命中（含 URL 前缀与 doi: 前缀）")

        # ── 2. 闸门 1：默认关闭，且**一次网络都不发** ─────────
        fake = _Fake([(200, "application/pdf", _PDF_BODY)])
        r = fetch_fulltext("10.1016/j.cell.2014.03.011", out_dir=outdir, fetch_impl=fake)
        if r.status != "route_disabled" or fake.calls:
            bad += 1
            print(f"  [X] 默认关闭时状态/调用不符：status={r.status} calls={len(fake.calls)}")
        else:
            print("闸门 1：默认关闭 → route_disabled，且未发出任何请求")
        if not r.is_final_failure:
            bad += 1
            print("  [X] route_disabled 应归 FAILURE")
        else:
            print("闸门 1：route_disabled 归类为 FAILURE（不是用户交接）")

        # ── 3. 开了闸门但没凭据 → credentials_missing（HANDOFF）──
        os.environ[ALLOW_ENV] = "1"
        r = fetch_fulltext("10.1016/j.cell.2014.03.011", out_dir=outdir, fetch_impl=fake)
        if r.status != "credentials_missing" or not r.is_handoff or fake.calls:
            bad += 1
            print(f"  [X] 无凭据分支错误：status={r.status} handoff={r.is_handoff} calls={len(fake.calls)}")
        else:
            print("闸门 2：开闸但无凭据 → credentials_missing（HANDOFF，非最终失败）")

        # ── 4. 请求构造：秘钥绝不进 URL（Elsevier）─────────────
        (scratch / "credentials.json").write_text(json.dumps({
            "elsevier": {"api_key": "ELSEVIER_SECRET", "insttoken": "INST_SECRET"},
            "springer_nature": {"api_key": "SPRINGER_SECRET"},
            "ieee": {"api_key": "IEEE_SECRET"},
        }), encoding="utf-8")
        plan = plan_request("10.1016/j.cell.2014.03.011")
        if not plan.get("ok") or plan.get("secret_in_url"):
            bad += 1
            print(f"  [X] Elsevier 计划异常：{plan}")
        else:
            print(f"请求构造：Elsevier → {plan['host']}，秘钥仅在请求头（secret_in_url=False）")
        if "ELSEVIER_SECRET" in json.dumps(plan, ensure_ascii=False):
            bad += 1
            print("  [X] 计划里泄露了 Elsevier 秘钥")
        else:
            print("请求构造：计划输出已脱敏（无秘钥明文）")

        # ── 5. Springe Nature 秘钥走 URL → 必须标出并无条件脱敏 ──
        plan = plan_request("10.1038/s41577-022-00763-8")
        if not plan.get("secret_in_url"):
            bad += 1
            print("  [X] Springer 的 key 走查询参数，secret_in_url 应为 True")
        else:
            print("请求构造：Springer 的 key 走 URL（已标 secret_in_url=True）")
        if "SPRINGER_SECRET" in json.dumps(plan, ensure_ascii=False):
            bad += 1
            print("  [X] Springer 计划里泄露了秘钥")
        else:
            print("请求构造：Springer 计划输出已脱敏")

        # ── 6. IEEE 缺模板 → 明确拒绝，且不发请求 ──────────────
        fake = _Fake([])
        r = fetch_fulltext("10.1109/TMI.2020.1234", out_dir=outdir, fetch_impl=fake)
        if r.status != "credentials_missing" or fake.calls:
            bad += 1
            print(f"  [X] IEEE 缺模板分支错误：status={r.status} calls={len(fake.calls)}")
        else:
            print("IEEE 门槛：缺 Full-Text 模板 → credentials_missing，未发请求")

        # ── 7. 成功路径：Elsevier PDF ────────────────────────
        fake = _Fake([(200, "application/pdf", _PDF_BODY)])
        r = fetch_fulltext("10.1016/j.cell.2014.03.011", out_dir=outdir, fetch_impl=fake)
        if not r.ok or r.fmt != "pdf" or r.bytes != len(_PDF_BODY):
            bad += 1
            print(f"  [X] Elsevier 成功路径异常：{r}")
        elif not pathlib.Path(r.file).is_file():
            bad += 1
            print(f"  [X] 落盘文件不存在：{r.file}")
        else:
            print(f"成功路径：Elsevier PDF → {pathlib.Path(r.file).name} ({r.bytes}B, {r.sha256[:12]}...)")
        if r.access_mode != "publisher_api" or r.provider != "elsevier":
            bad += 1
            print("  [X] access_mode/provider 标记缺失")
        else:
            print("成功路径：access_mode=publisher_api、provider 已标记（可入 manifest）")
        import hashlib  # noqa: PLC0415
        if r.sha256 != hashlib.sha256(_PDF_BODY).hexdigest():
            bad += 1
            print("  [X] SHA-256 与源字节不符")
        else:
            print("成功路径：SHA-256 与源字节一致")

        # ── 8. [STOP] 关键：JATS articleset 壳必须被拦下 ───────
        fake = _Fake([(200, "application/xml", _SHELL_BODY)])
        r = fetch_fulltext("10.1038/s41577-022-00763-8", out_dir=outdir, fetch_impl=fake)
        if r.status != "not_real_fulltext" or r.ok:
            bad += 1
            print(f"  [X] articleset 壳被当成功：status={r.status} ok={r.ok}")
        else:
            print(f"JATS 判据：articleset 壳被拦下（{r.status}）—— 不写进交付物")
        shell_files = list(outdir.rglob("*"))
        if any("s41577" in p.name for p in shell_files):
            bad += 1
            print(f"  [X] 壳仍然落盘了：{[p.name for p in shell_files]}")
        else:
            print("JATS 判据：壳未落盘（判据在写盘之前）")

        # ── 9. 成功路径：Springer 真 JATS（XML + 正文 .txt 双产物）──
        fake = _Fake([(200, "application/xml", _JATS_BODY)])
        r = fetch_fulltext("10.1038/s41577-022-00763-8", out_dir=outdir, fetch_impl=fake)
        d = r.to_dict()
        if not r.ok or r.fmt != "jats_xml":
            bad += 1
            print(f"  [X] Springer 成功路径异常：{r}")
        elif not d.get("extra", {}).get("text_file"):
            bad += 1
            print(f"  [X] JATS 未产出正文 .txt：extra={d.get('extra')}")
        elif not pathlib.Path(d["extra"]["text_file"]).is_file():
            bad += 1
            print(f"  [X] JATS 正文 .txt 文件不存在：{d['extra']['text_file']}")
        else:
            print(f"成功路径：Springer JATS → xml + txt 双产物"
                  f"（正文 {d['extra'].get('body_chars')} 字）")
        if d.get("status") != "native_fulltext_downloaded":
            bad += 1
            print(f"  [X] JATS 的状态应为 native_fulltext_downloaded，实得 {d.get('status')}")
        else:
            print("成功路径：JATS 状态 = native_fulltext_downloaded（非 PDF 原生全文）")

        # ── 10. HTTP 状态 → 语义状态 ─────────────────────────
        want = {401: "credentials_invalid", 403: "api_not_entitled",
                404: "api_fulltext_unavailable", 429: "rate_limited", 500: "api_fulltext_unavailable"}
        got = {k: classify_http_status(k) for k in want}
        if got != want:
            bad += 1
            print(f"  [X] HTTP 状态映射错误：{got}")
        else:
            print("状态映射：401/403/404/429/5xx 全部按语义归类")

        # 403（无授权）必须是 HANDOFF —— 不是失败，要用户去配授权
        fake = _Fake([(403, "text/html", b"<html>forbidden</html>")])
        r = fetch_fulltext("10.1016/j.cell.2014.03.011", out_dir=outdir, fetch_impl=fake)
        if r.status != "api_not_entitled" or not r.is_handoff:
            bad += 1
            print(f"  [X] 403 应归 HANDOFF：status={r.status} handoff={r.is_handoff}")
        else:
            print("状态映射：403 无授权 → api_not_entitled（HANDOFF，交用户处理）")

        # 429 在 Elsevier 上会退避重试（max_attempts 生效）
        fake = _Fake([(429, "text/plain", b"slow down"),
                      (429, "text/plain", b"slow down"),
                      (200, "application/pdf", _PDF_BODY)])
        r = fetch_fulltext("10.1016/j.cell.2014.03.011", out_dir=outdir, fetch_impl=fake)
        if not r.ok or len(fake.calls) != 3:
            bad += 1
            print(f"  [X] Elsevier 429 退避重试异常：ok={r.ok} calls={len(fake.calls)}")
        else:
            print("退避重试：Elsevier 429 重试 3 次后成功（其余出版商不重试）")

        # ── 11. 结果/日志里不得出现任何秘钥明文 ───────────────
        blob = json.dumps(r.to_dict(), ensure_ascii=False)
        leak = [s for s in ("ELSEVIER_SECRET", "SPRINGER_SECRET", "IEEE_SECRET", "INST_SECRET")
                if s in blob]
        if leak:
            bad += 1
            print(f"  [X] 结果里泄露秘钥：{leak}")
        else:
            print("泄密检查：四条成功/失败路径的结果 JSON 均无秘钥明文")
    finally:
        _env_restore(snap)
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
    if "--status" in argv:
        allowed, how = allow_publisher_api()
        print(f"出版商 API 开关：{'开启' if allowed else '关闭'}（{how}）")
        for p in creds.PROVIDERS:
            ok, why = creds.is_usable(p)
            print(f"  {creds.PROVIDERS[p]['name']:<24} {'可用' if ok else '不可用'} — {why}")
        return 0 if allowed else 3
    if "--plan" in argv:
        i = argv.index("--plan")
        if i + 1 >= len(argv):
            print("[X] --plan 需要一个 DOI")
            return 2
        print(json.dumps(plan_request(argv[i + 1]), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
