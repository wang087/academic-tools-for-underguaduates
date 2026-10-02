# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 页面状态判定（ok / challenge / blocked / paywall / not-ready）
# —— 全流程唯一判定源
#
# ★ 为什么单独抽出来：旧脚本用「文本长度 < 6000 就当 CAPTCHA」这种启发式，
#   导致大量**误触发**——页面只是还没渲染完、或本身就是短页面（勘误、
#   简讯、PDB 条目），脚本就弹窗并空等 15 分钟 → 表现为「死机」。
#
# ★ 修复后的判定原则（三条铁律）：
#   1) **绝不**仅凭文本长度判定 challenge。
#   2) challenge / block 必须命中**精确标记**；且正文类标记只在
#      「页面很短（< SHORT_PAGE）且标记出现在页面开头」时才算数
#      —— 真正的拦截页是短小的插入页；正文/摘要里出现
#      "blocked by"、"forbidden"、"access denied" 属正常科研用语。
#   3) 只要能确认「这是一篇正常论文页」（有 citation_* 元数据 / 有摘要 /
#      正文足够长且非短小拦截页），**一律判 ok**，不受零散敏感词影响。
#
# 关键词来源：2026-09 实测 ScienceDirect / RSC / Wiley / Springer /
#             OUP / IUCr / ACS 的拦截页与 Cloudflare 挑战页文案。
#
# 用法：
#   from detect import judge, OK, CHALLENGE, BLOCKED, site_of
#   r = judge(cdp_session)          # → {"state": "ok", "title": ..., ...}
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import re

# ── 状态常量 ────────────────────────────────────────────────
OK = "ok"                 # 正常论文页（或至少可读正文）
CHALLENGE = "challenge"   # 人机验证 / Cloudflare 挑战 —— 可人工过一次
BLOCKED = "blocked"       # 真实封禁文案 —— 必须全局停止
NOT_READY = "not-ready"   # 页面尚未渲染完 / 空白 —— 应重试而非判挑战
PAYWALL = "paywall"       # 正常页面但无全文权限
ERROR = "error"           # 网络/导航异常

# 短页面阈值：拦截页/挑战页几乎都很短
SHORT_PAGE = 4500


# ══════════════════════════════════════════════════════════════
# 一、页内探针：一次 Runtime.evaluate 拿全判定所需信息
#     （标记匹配在页面内完成，只回传命中项，避免大文本往返）
# ══════════════════════════════════════════════════════════════
PROBE_JS = r"""
(() => {
  const T = (document.body && document.body.innerText) || '';
  const LOW = T.toLowerCase();
  const HEAD = LOW.slice(0, 2500);
  const TLOW = (document.title || '').toLowerCase();

  // ★ v8：把**完整正文**一并回传（上限见 MAX_FULL），供"边下 PDF 边解读"复用。
  //   为什么顺带取：正文此刻已在内存里（T 就是它），多回传一份几乎零成本；
  //   若等下载完再取，就得为每篇重新导航一次 —— 那才是真正的浪费。
  //   默认只回传前 FULL_CAP 字符，避免普通判定调用被大文本拖慢。
  const FULL_CAP = window.__PROBE_FULL_CAP__ || 0;
  const full = FULL_CAP > 0 ? T.slice(0, FULL_CAP) : '';

  const meta = n => { const e = document.querySelector('meta[name="'+n+'"]'); return e ? (e.content||'') : ''; };
  const citPdf = meta('citation_pdf_url'), citTitle = meta('citation_title');

  // ── 主文档真实 HTTP 状态（Chromium 109+ 的 Performance API）──
  let navStatus = 0;
  try {
    const e = performance.getEntriesByType('navigation')[0];
    if (e && e.responseStatus) navStatus = e.responseStatus;
  } catch (err) {}

  // ── 挑战 / 反爬组件指纹（iframe / 脚本 / DOM 特征）────────
  let botVendor = '';
  try {
    const ifr = [...document.querySelectorAll('iframe')].map(f => f.src || '').join(' ');
    const scr = [...document.querySelectorAll('script')].map(s => s.src || '').join(' ');
    const all = (ifr + ' ' + scr).toLowerCase();
    const html = document.documentElement ? document.documentElement.innerHTML.slice(0, 40000).toLowerCase() : '';
    const VEND = [["datadome","datadome"],["captcha-delivery","datadome"],
      ["px-captcha","perimeterx"],["perimeterx","perimeterx"],
      ["_incapsula_resource","imperva"],["incapsula","imperva"],
      ["challenges.cloudflare.com","cloudflare-turnstile"],["cf_chl_opt","cloudflare"],
      ["hcaptcha","hcaptcha"],["recaptcha","recaptcha"],["geetest","geetest"]];
    for (const [k, v] of VEND) { if (all.includes(k)) { botVendor = v; break; } }
    if (!botVendor) {
      for (const [k, v] of VEND) { if (html.includes(k)) { botVendor = v; break; } }
    }
  } catch (err) {}

  // ── 挑战页标记（保留原始大小写以回传）────────────────
  const CHAL = ["just a moment","attention required","verifying you are human",
    "verify you are human","checking your browser before accessing","checking your browser",
    "enable javascript and cookies to continue","are you a robot","are you a human",
    "captcha challenge","complete the captcha","human verification",
    "cf-challenge","challenge-platform","cf_chl_opt","cf-mitigated",
    "ddos protection by","please wait while we verify","press and hold",
    "pardon our interruption","datadome","captcha-delivery","px-captcha",
    "_incapsula_resource","request unsuccessful","incapsula incident",
    "reference #18","reference #9","akamai",
    "人机验证","请完成安全验证","安全验证","滑动验证","拖动滑块","正在验证","请稍候",
    "访问验证","请完成验证","验证您是真人","异常流量"];
  // ── 封禁精确短语（全文匹配安全：不可能出现在科研正文）──
  const BLK = ["此地址已被封禁","您的地址已被封禁","地址已被封禁","您已被阻止访问",
    "访问已被限制","已被列入黑名单","您的访问已被限制","your ip has been blocked",
    "your ip address has been blocked","you have been blocked","has blocked your access",
    "unusual traffic from your network","your request has been blocked",
    "access to this page has been denied","请求已被拦截","error 1020","error 1015",
    "错误代码 1020","错误代码 1015","您的请求已被拦截","访问请求被拒绝"];
  // ── 模糊封禁词（仅短页面开头才算数）──────────────────
  const AMB = ["access denied","request blocked","forbidden","blocked by","has been suspended"];
  // ── 软拦截 / 限流（**站点级**降温即可，不触发全局中止）──
  //    Cloudflare 的 403/429 错误页常带 Ray ID，属单次请求被 WAF 规则拦截，
  //    不是 IP 封禁；按 CHALLENGE 处理 → 站点熔断跳过本轮，绝不全局停摆。
  const SOFT = ["ray id","too many requests","rate limit","请求过于频繁","访问频繁",
    "429 too many requests"];

  // 中文标记用原文匹配（小写化不影响中文），英文用小写文本
  const hitFull = arr => arr.filter(m => { const k = m.toLowerCase();
      return LOW.includes(k) || T.includes(m); });
  const hitHead = arr => arr.filter(m => { const k = m.toLowerCase();
      return HEAD.includes(k) || HEAD.includes(m); });

  const chalTitleHit = CHAL.filter(m => TLOW.includes(m.toLowerCase()));
  const chalHeadHit  = hitHead(CHAL);
  const blkFullHit   = hitFull(BLK);
  const ambHeadHit   = hitHead(AMB);
  const softHeadHit  = hitHead(SOFT);

  const sub = /Subscribed|Access through your institution|View full text|Download PDF|Download full-text/i.test(T);
  const pay = /Get access|Purchase PDF|Buy article|Add to cart|Rent this article|Purchase access|Choose a subscription/i.test(T);

  const links = [];
  for (const a of document.querySelectorAll('a')) {
    const h = a.href || '';
    if (/\.pdf(\?|$)/i.test(h) || /pdfdirect/i.test(h) || /\/pdf\//i.test(h) ||
        /article-pdf/i.test(h) || /\/content\/pdf\//i.test(h) || /full\.pdf/i.test(h)) links.push(h);
  }

  let abs = '';
  const sels = ['section#abstract','div#abstract','#abstract','section.abstract','div.abstract',
                '[data-testid="abstract"]','.article-section__abstract','#Abs1-content',
                '.abstractSection','div.abstract.author','#abstracts','.c-article-section__content',
                'section[aria-labelledby*="abstract"]','div.abstract-content','#abstract-content'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el) { const t = (el.innerText||'').trim(); if (t.length > 120) { abs = t; break; } }
  }
  if (!abs) { const i = T.search(/Abstract|ABSTRACT/); if (i >= 0) abs = T.slice(i, i + 2200); }

  return JSON.stringify({
    title: document.title || '',
    url: location.href || '',
    readyState: document.readyState || '',
    textlen: T.length,
    head: T.slice(0, 1200),
    hasMeta: !!(citPdf || citTitle),
    metaPdf: citPdf,
    citeTitle: citTitle,
    journal: meta('citation_journal_title'),
    subscribed: sub,
    paywalled: pay,
    navStatus: navStatus,
    botVendor: botVendor,
    links: [...new Set(links)].slice(0, 8),
    abstract: abs.slice(0, 2600),
    fulltext: full,
    fulltext_len: T.length,
    hits: { chalTitle: chalTitleHit, chalHead: chalHeadHit,
            blkFull: blkFullHit, ambHead: ambHeadHit, softHead: softHeadHit }
  });
})()
"""


def probe(c, timeout: int = 30) -> dict:
    """对已连接的 CDP 会话执行探针。失败返回 {"error": ...}。"""
    try:
        raw = c.eval(PROBE_JS, timeout=timeout)
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(d, dict):
            return {"error": "probe-bad-shape"}
        d.setdefault("hits", {})
        return d
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:90]}"}


# ══════════════════════════════════════════════════════════════
# 二、判定（纯函数，便于单测）
# ══════════════════════════════════════════════════════════════
def classify(d: dict, http_status: int | None = None, nav_error: str = "") -> dict:
    """由探针结果判定页面状态。返回 {state, reason, markers, textlen}。"""
    if nav_error or d.get("error"):
        msg = nav_error or d.get("error", "")
        return {"state": ERROR, "reason": str(msg)[:140], "markers": [], "textlen": 0}

    hits = d.get("hits") or {}
    tlen = int(d.get("textlen") or 0)
    has_meta = bool(d.get("hasMeta"))
    abstract = d.get("abstract") or ""
    chal_title = hits.get("chalTitle") or []
    chal_head = hits.get("chalHead") or []
    blk_full = hits.get("blkFull") or []
    amb_head = hits.get("ambHead") or []
    soft_head = hits.get("softHead") or []
    # 主文档真实 HTTP 状态（探针抓不到时用调用方传入的）
    try:
        hs = int(d.get("navStatus") or 0) or (http_status or 0)
    except Exception:
        hs = http_status or 0
    vendor = d.get("botVendor") or ""

    # ── 1) 正常论文页优先放行（出版社元数据最可信）────────
    if has_meta:
        return {"state": OK, "reason": "citation-meta", "markers": [], "textlen": tlen}
    # ── 2) 封禁精确短语（全文安全匹配）────────────────────
    if blk_full:
        return {"state": BLOCKED, "reason": "block-phrase",
                "markers": blk_full, "textlen": tlen}
    # ── 3) HTTP 403/451 + 短页 + 模糊封禁词 → 封禁 ────────
    if hs in (403, 451) and tlen < SHORT_PAGE and amb_head:
        return {"state": BLOCKED, "reason": f"http-{hs}",
                "markers": amb_head, "textlen": tlen}
    # ── 4) 模糊封禁词：仅短页面开头（拦截页特征）──────────
    if tlen < SHORT_PAGE and amb_head:
        return {"state": BLOCKED, "reason": "block-head",
                "markers": amb_head, "textlen": tlen}
    # ── 5) 挑战页（精确短语；标题命中即算）─────────────────
    if chal_title:
        return {"state": CHALLENGE, "reason": "title",
                "markers": chal_title, "textlen": tlen}
    if tlen < SHORT_PAGE and chal_head:
        return {"state": CHALLENGE, "reason": "short-body",
                "markers": chal_head, "textlen": tlen}
    # ── 5b) 反爬组件指纹 + 短页 → 挑战（DataDome/PX/Imperva 等纯 JS 挑战页）──
    if vendor and tlen < SHORT_PAGE:
        return {"state": CHALLENGE, "reason": f"vendor:{vendor}",
                "markers": [vendor], "textlen": tlen}
    # ── 5c) HTTP 429/503 + 短页 → 限流挑战（按挑战处理，触达站点熔断）──
    if hs in (429, 503) and tlen < SHORT_PAGE:
        return {"state": CHALLENGE, "reason": f"http-{hs}",
                "markers": soft_head or amb_head, "textlen": tlen}
    # ── 5d) 软拦截（Ray ID / 限流话术，WAF 单次拦截）→ CHALLENGE，
    #        只让该站点本轮熔断，**不**触发全局中止 ──────────────
    if tlen < SHORT_PAGE and soft_head:
        return {"state": CHALLENGE, "reason": f"soft-block:{hs or 'waf'}",
                "markers": soft_head, "textlen": tlen}
    # ── 6) 正常页面但无全文权限 ───────────────────────────
    if d.get("paywalled") and not d.get("subscribed"):
        return {"state": PAYWALL, "reason": "paywall-ui", "markers": [], "textlen": tlen}
    # ── 7) 长正文且无挑战标题 → 正常页（科研用语不再误判）──
    if tlen >= SHORT_PAGE:
        return {"state": OK, "reason": "long-body", "markers": [], "textlen": tlen}
    if abstract and len(abstract) > 300 and tlen >= 1200:
        return {"state": OK, "reason": "has-abstract", "markers": [], "textlen": tlen}
    # ── 8) 空页 / 未渲染完 ────────────────────────────────
    if tlen < 400:
        return {"state": NOT_READY, "reason": f"tiny({tlen})",
                "markers": [], "textlen": tlen}
    return {"state": OK, "reason": "default", "markers": [], "textlen": tlen}


def judge(c, timeout: int = 30, http_status: int | None = None,
          fulltext_cap: int = 0) -> dict:
    """探针 + 判定，一步到位。返回判定结果并附带页面信息字段。

    fulltext_cap > 0 时，额外回传正文前 N 个字符（`r["fulltext"]`），
    供"边下载 PDF 边取正文解读"复用 —— 正文此刻已在页面内存里，
    顺带回传几乎零成本；为 0（默认）时行为与旧版完全一致。
    """
    if fulltext_cap > 0:
        try:
            # 探针在页面上下文里读这个全局变量决定回传多少正文
            c.eval(f"window.__PROBE_FULL_CAP__={int(fulltext_cap)}")
        except Exception:
            pass
    d = probe(c, timeout=timeout)
    r = classify(d, http_status=http_status)
    for k in ("title", "url", "metaPdf", "citeTitle", "journal",
              "subscribed", "paywalled", "links", "abstract", "readyState",
              "navStatus", "botVendor"):
        if k in d:
            r[k] = d[k]
    # 正文只在调用方明确要求时带回，避免普通判定被大文本拖慢
    if fulltext_cap > 0:
        r["fulltext"] = d.get("fulltext") or ""
        r["fulltext_len"] = d.get("fulltext_len") or 0
    # ★ 判定失败（挑战页/封禁页）时正文无意义，清掉以免污染解读素材
    if r.get("state") in (CHALLENGE, BLOCKED, NOT_READY, ERROR):
        r["fulltext"] = ""
    return r


# ══════════════════════════════════════════════════════════════
# 三、站点归并（用于站点级熔断，避免反复冲击同一出版社）
# ══════════════════════════════════════════════════════════════
_SITE_GROUPS = (
    "sciencedirect.com", "cell.com", "onlinelibrary.wiley.com", "academic.oup.com",
    "link.springer.com", "nature.com", "pubs.acs.org", "pubs.rsc.org",
    "journals.iucr.org", "tandfonline.com", "jbc.org", "pnas.org", "mdpi.com",
    "biorxiv.org", "preprints.org", "frontiersin.org", "europepmc.org",
)


def site_of(url: str) -> str:
    m = re.match(r"^https?://([^/]+)", url or "")
    host = (m.group(1) if m else "").lower()
    for pre in ("www.", "wwws.", "m.", "dx."):
        if host.startswith(pre):
            host = host[len(pre):]
    for g in _SITE_GROUPS:
        if host == g or host.endswith("." + g):
            return g
    return host or "unknown"


# ── DOI 前缀 → 出版社站点（落地前就能定站点，用于并发节流与熔断）──
#   注：doi.org 会把所有 DOI 都显示成同一个 host，若用落地 URL 前就
#   取 site_of()，全部论文会塌缩成 "doi.org" 一个站点，导致节流失效。
_DOI_PREFIX_SITE = {
    "10.1016": "elsevier",       # ScienceDirect / Cell Press
    "10.1074": "jbc.org",        # JBC（Elsevier 托管）
    "10.1038": "nature.com",
    "10.1007": "link.springer.com",
    "10.1186": "link.springer.com",
    "10.1021": "pubs.acs.org",
    "10.1039": "pubs.rsc.org",
    "10.1107": "journals.iucr.org",
    "10.1093": "academic.oup.com",
    "10.1002": "wiley",
    "10.1042": "wiley",
    "10.1111": "wiley",
    "10.15252": "embopress.org",
    "10.1073": "pnas.org",
    "10.1126": "science.org",
    "10.1128": "asm.org",
    "10.1371": "plos.org",
    "10.3390": "mdpi.com",
    "10.1101": "biorxiv.org",
    "10.21203": "researchsquare.com",
    "10.20944": "preprints.org",
    "10.2139": "ssrn.com",
    "10.4161": "tandfonline.com",
    "10.1080": "tandfonline.com",
    "10.1360": "sciengine.com",
    "10.52396": "justc.ustc.edu.cn",
    "10.2210": "rcsb.org",
}


def site_of_doi(doi: str) -> str:
    """由 DOI 前缀推断出版社站点组（并发节流 / 熔断的键）。"""
    p = (doi or "").split("/", 1)[0].lower()
    return _DOI_PREFIX_SITE.get(p, p or "unknown")


# ── 已知在**中国大陆 IP 段不可达**的站点 ────────────────────
# 这些站点不是「被风控」，而是网络层面根本连不上（或需境外网络）。
# 反复重试既无意义，也会浪费配额，因此在选取待办时直接跳过。
#
# ★ 实测（2026-09，中国科大校园网 202.141.176.10）：
#   · ssrn.com —— 国内 IP 无法访问（用户确认）
CN_INACCESSIBLE = {
    "ssrn.com": "SSRN 国内 IP 无法访问（网络层不可达，非风控）",
}


def is_cn_inaccessible(doi: str) -> str | None:
    """返回不可达原因；可正常尝试则返回 None。"""
    return CN_INACCESSIBLE.get(site_of_doi(doi))
