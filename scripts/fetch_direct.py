# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 第一层直连获取（摘要 / 综述题录）—— 失败自动转 CDP
#
# 依据：SKILL §5.1「分层获取策略（v7.1）」
#   第一层｜允许直连 —— 元数据、**摘要**、**综述（题录/摘要级）**、
#                      `templates/直连白名单.json` 里已验证的开源数据库
#   第二层｜必须 CDP —— **原文全文**（PDF / HTML 正文）
#   回退：第一层尝试全部失败 → 转向 CDP
#
# 本脚本负责**第一层**，并把失败清单交给第二层：
#   · 只对白名单内的域名发请求（**表外一律拒绝，fail closed**）；
#   · 按 OpenAlex → Semantic Scholar → Europe PMC → Crossref → PubMed 顺序尝试，
#     任一源命中即停止（省下游压力）；
#   · 产物写 `.work_cdp/abstracts_openalex.json`（主流程既有契约，interpret_prepare 直接读）；
#   · 仍缺的 DOI 写 `.work_cdp/direct_failed.json`，并打印 CDP 回退命令。
#
# ★ 为什么要有这个脚本，而不是继续靠"agent 自己判断能不能直连"：
#   §5.1 的分层规则若只写在文档里，每次都要临场判断某个域名算不算
#   「已验证可行的开源数据库」—— 这正是原禁令"一刀切"想避免的风险。
#   白名单把这个判断**前置成配置**，脚本对表外域名直接拒绝。
#
# ★ 本脚本**绝不获取原文全文**：它只查询结构化元数据与摘要接口。
#   任何返回 HTML 页面、PDF 的路径都在第二层，不在这里。
#
# 用法：
#   uv run python 流程1/scripts/fetch_direct.py
#     --limit=0            只处理前 N 篇（0=全部）
#     --max-per-source=0   单源最多尝试多少篇（0=不限；用于压测节流）
#     --sources=openalex,s2,europepmc,crossref,pubmed
#     --offline            只读缓存，不联网（跑自检/复现用）
#     --run-cdp-fallback   仍有缺口时**直接调起** grab_abstracts.py --retry-failed
#                          （需要 CDP 实例在跑；默认只打印命令，不擅自启动浏览器）
#     --check-domain=URL   只做策略判定：这个 URL 允许直连吗？（不联网）
#     --self-test          纯逻辑自检（不联网）
#
# 可移植配置：PAPER_WORKDIR / PAPER_EMAIL
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ThreadPoolExecutor, as_completed

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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from logutil import get, journal  # type: ignore
except Exception:
    class _L:
        def info(self, *a, **k):
            print(*a)

        def warn(self, *a, **k):
            print(*a, file=sys.stderr)

    def get(_n):            # noqa: D103
        return _L()

    def journal(*a, **k):   # noqa: D103
        pass

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WD / ".work_cdp"
CACHEDIR = CDPDIR / "cache_direct"
TPL = _HERE.parent / "templates"
WHITELIST = TPL / "直连白名单.json"

OUT = CDPDIR / "abstracts_openalex.json"     # 主流程既有契约名（interpret_prepare 读它）
FAILED = CDPDIR / "direct_failed.json"
CDP_ABS = CDPDIR / "abstracts.json"          # 第二层（CDP）产物

EMAIL = os.environ.get("PAPER_EMAIL", "").strip() or "scientific-agent-skills@example.org"
UA = f"scientific-agent-skills/2.65 (mailto:{EMAIL})"
OFFLINE = "--offline" in sys.argv

DEFAULT_SOURCES = ["openalex", "s2", "europepmc", "crossref", "pubmed"]


def _argval(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def load_json(p: pathlib.Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════
# 白名单判定 —— fail closed
# ══════════════════════════════════════════════════════════════
def load_whitelist() -> list[dict]:
    d = load_json(WHITELIST, {}) or {}
    return [s for s in (d.get("sources") or []) if isinstance(s, dict)]


def domain_allowed(url: str, entries: list[dict]) -> tuple[bool, str]:
    """→ (是否允许直连, 命中的 domain 或拒绝原因)。

    ★ 匹配规则用**后缀边界**，不是 `in`：
      `evil-api.crossref.org.attacker.com` 里含 "api.crossref.org"，
      用子串匹配会放行 —— 必须是 host == domain 或 host 以 "." + domain 结尾。
    """
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False, "URL 无法解析"
    if not host:
        return False, "URL 没有主机名"
    for e in entries:
        d = str(e.get("domain") or "").lower().strip()
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True, d
    return False, f"{host} 不在直连白名单内"


def http_json(url: str, headers: dict | None = None, tries: int = 3):
    """带磁盘缓存的 GET（第一层允许直连，但同样要节流与退避）。"""
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    cp = CACHEDIR / f"{h}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cp.unlink(missing_ok=True)
    if OFFLINE:
        return None
    hd = {"User-Agent": UA, "Accept": "application/json"}
    hd.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hd)
            with urllib.request.urlopen(req, timeout=35) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            CACHEDIR.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.15)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.8 * (i + 1))
                continue
            return None          # 404/403：该源没有这篇，换下一个源
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    return None


def http_json_post(url: str, payload, headers: dict | None = None, tries: int = 3):
    import hashlib
    body = json.dumps(payload).encode("utf-8")
    h = hashlib.sha1((url + str(payload)).encode("utf-8")).hexdigest()[:20]
    cp = CACHEDIR / f"post_{h}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cp.unlink(missing_ok=True)
    if OFFLINE:
        return None
    hd = {"User-Agent": UA, "Accept": "application/json",
          "Content-Type": "application/json"}
    hd.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=hd, method="POST")
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            CACHEDIR.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.2)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (i + 1))
                continue
            return None
        except Exception:
            time.sleep(1.2 * (i + 1))
    return None


def strip_jats(s: str) -> str:
    """Crossref 的 abstract 是 JATS XML 片段，剥标签后才是纯文本。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = (s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
          .replace("&quot;", '"').replace("&#x2019;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def rebuild_inverted(inv) -> str:
    if not isinstance(inv, dict) or not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs or []:
            pos[int(i)] = w
    return " ".join(pos[k] for k in sorted(pos))


# ══════════════════════════════════════════════════════════════
# 各源实现（**只取摘要 / 题录，绝不取正文**）
# ══════════════════════════════════════════════════════════════
def src_openalex(doi: str):
    url = ("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi)
           + "?select=id,doi,title,abstract_inverted_index,publication_year")
    d = http_json(url)
    if not isinstance(d, dict):
        return None
    a = rebuild_inverted(d.get("abstract_inverted_index"))
    return {"abstract": a, "title": d.get("title") or ""} if a else None


def src_crossref(doi: str):
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    d = http_json(url)
    m = ((d or {}).get("message") or {}) if isinstance(d, dict) else {}
    a = strip_jats(m.get("abstract") or "")
    if not a:
        return None
    title = (m.get("title") or [""])[0]
    return {"abstract": a, "title": title}


def src_europepmc(doi: str):
    q = urllib.parse.quote(f'DOI:"{doi}"')
    url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}"
           f"&resultType=core&format=json&pageSize=1")
    d = http_json(url)
    res = (((d or {}).get("resultList") or {}).get("result") or []) \
        if isinstance(d, dict) else []
    if not res:
        return None
    r = res[0]
    a = strip_jats(r.get("abstractText") or "")
    if not a:
        return None
    return {"abstract": a, "title": r.get("title") or ""}


def src_pubmed(doi: str):
    q = urllib.parse.quote(f"{doi}[DOI]")
    u1 = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
          f"?db=pubmed&retmode=json&term={q}")
    d = http_json(u1)
    ids = (((d or {}).get("esearchresult") or {}).get("idlist") or []) \
        if isinstance(d, dict) else []
    if not ids:
        return None
    u2 = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
          f"?db=pubmed&retmode=xml&id={ids[0]}")
    try:
        req = urllib.request.Request(u2, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=35) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    # ★ 从 XML 里取 AbstractText 段，不引入 XML 依赖
    segs = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", xml, re.S)
    a = strip_jats(" ".join(segs))
    if not a:
        return None
    m = re.search(r"<ArticleTitle[^>]*>(.*?)</ArticleTitle>", xml, re.S)
    return {"abstract": a, "title": strip_jats(m.group(1)) if m else ""}


def src_s2_batch(dois: list[str]) -> dict:
    """Semantic Scholar 批量接口：一次 500 篇，优先用它以减少请求数。"""
    out = {}
    url = ("https://api.semanticscholar.org/graph/v1/paper/batch"
           "?fields=title,abstract,externalIds")
    for i in range(0, len(dois), 100):
        grp = dois[i:i + 100]
        d = http_json_post(url, {"ids": [f"DOI:{x}" for x in grp]})
        if not isinstance(d, list):
            continue
        for doi, item in zip(grp, d):
            if isinstance(item, dict) and (item.get("abstract") or "").strip():
                out[doi] = {"abstract": item["abstract"].strip(),
                            "title": item.get("title") or ""}
    return out


SOURCES = {
    "openalex": src_openalex,
    "crossref": src_crossref,
    "europepmc": src_europepmc,
    "pubmed": src_pubmed,
}


# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    entries = [{"domain": "api.crossref.org"}, {"domain": "www.ebi.ac.uk"},
               {"domain": "api.openalex.org"}]
    cases = [
        ("https://api.crossref.org/works/10.1/x", True),
        ("https://api.openalex.org/works/doi:10.1/x", True),
        ("https://www.ebi.ac.uk/europepmc/webservices/rest/search", True),
        # ★ 后缀边界陷阱：子串匹配会把这些全放行
        ("https://api.crossref.org.attacker.com/steal", False),
        ("https://evil-api.crossref.org.attacker.com/x", False),
        ("https://www.nature.com/articles/x", False),
        ("https://sciencedirect.com/science/article/x", False),
        ("https://api.semanticscholar.org/graph/v1/paper/batch", False),
    ]
    bad = 0
    for url, want in cases:
        got, why = domain_allowed(url, entries)
        if got != want:
            bad += 1
            print(f"  ✗ domain_allowed({url!r}) = {got}（{why}），期望 {want}")
    print(f"白名单边界判定：{len(cases) - bad}/{len(cases)} 通过")

    wl = load_whitelist()
    if wl:
        # 白名单里**不得**出现任何出版社内容站
        bad_dom = [s["domain"] for s in wl
                   if re.search(r"(nature|science|cell|wiley|elsevier|springer|"
                                r"acs\.|rsc\.|oup|tandfonline|sagepub|sciencedirect)",
                                str(s.get("domain") or ""), re.I)]
        ok = not bad_dom
        print(f"白名单不含出版社内容站：{'通过' if ok else '失败 → ' + str(bad_dom)}")
        bad += 0 if ok else 1

        # 每条都必须声明 content_class，且只能是这三种
        ALLOWED_CC = ("metadata", "abstract", "abstract+oa-fulltext-xml")
        badc = [s["domain"] for s in wl
                if s.get("content_class") not in ALLOWED_CC]
        ok2 = not badc
        print(f"白名单内容层级合法：{'通过' if ok2 else '失败 → ' + str(badc)}")
        bad += 0 if ok2 else 1

        # ★ 含全文的源**必须** oa_only：否则等于放开了"直连取非 OA 正文"，
        #   那正是 §5.1 第二层明令禁止的。
        bad_o = [s["domain"] for s in wl
                 if "oa-fulltext-xml" in str(s.get("content_class") or "")
                 and not s.get("oa_only")]
        ok3 = not bad_o
        print(f"含全文 XML 的源均为 oa_only：{'通过' if ok3 else '失败 → ' + str(bad_o)}")
        bad += 0 if ok3 else 1

    assert strip_jats("<jats:p>Hello <b>world</b></jats:p>") == "Hello world"
    assert rebuild_inverted({"a": [1], "b": [0]}) == "b a"
    print("文本清洗：通过")

    print("\n✅ 自检全部通过" if bad == 0 else "\n❌ 自检有失败项")
    return 0 if bad == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("direct")
    entries = load_whitelist()

    chk = _argval("check-domain")
    if chk:
        ok, why = domain_allowed(chk, entries)
        print(f"{'✅ 允许直连' if ok else '⛔ 禁止直连'}：{chk}")
        print(f"   理由：{why}")
        if not ok:
            print("   → 按 §5.1：原文全文走 CDP；其他内容请确认是否属于第一层。")
        return 0 if ok else 3

    limit = int(_argval("limit", "0") or 0)
    sources = [s.strip() for s in
               (_argval("sources", "") or ",".join(DEFAULT_SOURCES)).split(",")
               if s.strip()]
    unknown = [s for s in sources if s not in SOURCES and s != "s2"]
    if unknown:
        print(f"✗ 未知源 {unknown}；可用：{list(SOURCES)} + s2")
        return 2

    cr = load_json(CDPDIR / "crossref_all.json", {}) or {}
    dois = sorted(k.lower() for k in cr if k)
    if limit:
        dois = dois[:limit]
    if not dois:
        print("✗ 找不到题录 .work_cdp/crossref_all.json")
        return 2

    # 已有摘要：CDP（第二层）优先保留，其次第一层历史产物
    have = {}
    cdp = load_json(CDP_ABS, {}) or {}
    for k, v in (cdp.items() if isinstance(cdp, dict) else []):
        a = (v.get("abstract") if isinstance(v, dict) else v) or ""
        if str(a).strip():
            have[str(k).lower()] = {"abstract": str(a).strip(),
                                    "title": (v.get("title") if isinstance(v, dict)
                                              else "") or "",
                                    "source": "cdp"}
    prev = load_json(OUT, {}) or {}
    for k, v in (prev.items() if isinstance(prev, dict) else []):
        k = str(k).lower()
        if k in have:
            continue
        a = (v.get("abstract") if isinstance(v, dict) else v) or ""
        if str(a).strip():
            have[k] = {"abstract": str(a).strip(),
                       "title": (v.get("title") if isinstance(v, dict) else "") or "",
                       "source": (v.get("source") if isinstance(v, dict) else "") or "cached"}

    todo = [d for d in dois if d not in have]
    print(f"题录 {len(dois)} 篇｜已有摘要 {len(have)} 篇｜待补 {len(todo)} 篇")
    print(f"第一层源：{sources}"
          + ("（离线模式，只读缓存）" if OFFLINE else ""))

    hits = {}
    # ① 批量源优先（一次请求拿多篇，最省下游压力）
    if "s2" in sources:
        todo_s2 = [d for d in todo if d not in hits]
        if todo_s2:
            print(f"  · Semantic Scholar 批量（{len(todo_s2)} 篇）…")
            got = src_s2_batch(todo_s2)
            for d, v in got.items():
                v["source"] = "s2"
                hits[d] = v
            print(f"    命中 {len(got)}")

    # ② 逐篇源
    for s in sources:
        if s == "s2":
            continue
        rest = [d for d in todo if d not in hits]
        if not rest:
            break
        print(f"  · {s}（{len(rest)} 篇）…")
        n = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(SOURCES[s], d): d for d in rest}
            for f in as_completed(futs):
                d = futs[f]
                try:
                    v = f.result()
                except Exception:
                    v = None
                if v and v.get("abstract"):
                    v["source"] = s
                    hits[d] = v
                    n += 1
        print(f"    命中 {n}")

    have.update(hits)
    OUT.write_text(json.dumps(have, ensure_ascii=False, indent=2), encoding="utf-8")

    missing = [d for d in dois if d not in have]
    FAILED.write_text(json.dumps({
        "reason": "第一层（直连白名单）未取到摘要，需转第二层 CDP",
        "cdp_fallback_cmd": ("uv run --with websocket-client python "
                             "流程1/scripts/grab_abstracts.py --retry-failed"),
        "dois": missing,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    by_src = {}
    for d in dois:
        if d in have:
            by_src[have[d].get("source") or "?"] = \
                by_src.get(have[d].get("source") or "?", 0) + 1
    print(f"\n摘要覆盖：{len(have)}/{len(dois)}")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"产物：{OUT}")

    if missing:
        print(f"\n⚠ 仍有 {len(missing)} 篇在第一层取不到摘要 → **转第二层 CDP**：")
        print("   uv run --with websocket-client python 流程1/scripts/grab_abstracts.py "
              "--retry-failed")
        print(f"   缺口清单：{FAILED}")
        if "--run-cdp-fallback" in sys.argv:
            if not OFFLINE:
                print("   → 正在调起 CDP 回退 …")
                p = subprocess.run(
                    [sys.executable, str(_HERE / "grab_abstracts.py"), "--retry-failed"],
                    cwd=str(_HERE), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=3600)
                print((p.stdout or "")[-1500:] or (p.stderr or "")[-1500:])
                # CDP 抓到的落在 abstracts.json；再跑一次本脚本即可折进统一产物
                print("   → CDP 回退完成；重跑本脚本可把结果折进 "
                      f"{OUT.name}")
            else:
                print("   （--offline 模式，跳过实际调起）")
    else:
        print("\n✅ 第一层已覆盖全部摘要，无需动用 CDP。")

    journal("E", "abstracts.direct", total=len(dois), covered=len(have),
            missing=len(missing), sources=",".join(sources), offline=OFFLINE)
    log.info(f"✓ 第一层摘要获取完成：{len(have)}/{len(dois)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
