# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 D2：反向查找参考文献（综述网络 + 高质量文献列表）
#
# 位置：紧接阶段 D（Crossref 核实 + 同名消歧）之后。
#       此时作者的成果集已经**确认无误**，正好用它去反向扩展。
#
# 做什么：
#   ① 取作者每篇论文的**参考文献**（OpenAlex `referenced_works`）；
#   ② 把这些参考文献的题录、被引次数、类型、期刊一次补齐；
#   ③ 按「高质量」定义筛出候选池（**v7.2 改版**）：
#        · **重点：近年发表于 CNS 及高水平刊物的「综述」**；
#        · 被引次数**不再是硬性要求**（默认阈值 0），只作为排序信号之一；
#        · 核心期刊 / 综述 / 近年 / 被引 / 共引 五项加权成分数，逐条可复核。
#   ④ **为综述取全文或详细摘要**，提高后续 review 的置信度：
#        · 开放获取 → Europe PMC `fullTextXML`（结构化、非爬网页）；
#        · 非 OA → 取**详细摘要**（OpenAlex 倒排重建，通常含 Objective/Method/Result）。
#   ④ 保留「哪几篇作者论文引用了它」的反向索引 —— 这就是综述网络的边。
#
# 为什么走 OpenAlex 而不是抓页面：
#   这是**元数据 API**，不属于流程 §5.1「CDP 强制抓取禁令」所指的「抓取论文内容」。
#   全程零浏览器、零风控、不下载任何 PDF。
#
# 输入（<WORKDIR>/.work_cdp/）：
#   crossref_all.json      题录总表（必需）
#   disambiguated.json     同名消歧结果（可选；经 scope.py 只排除肯定性判定）
#   refs/core_journals.json  自定义核心期刊表（可选，追加/覆盖内置表）
#
# 产出（<WORKDIR>/.work_cdp/refs/）：
#   source_refs.json       {作者论文 DOI: {openalex, title, year, refs:[W…]}}
#   refs_pool.json         {W…: {doi,title,year,venue,type,cited_by_count,…}}
#   high_quality.json      {items:[…], stats:{…}}   ← 下游 G2 / H2 的唯一输入
#   cache/                 HTTP 响应缓存（可断点续跑）
#
# 另产出交付物：<WORKDIR>/<作者名>_高质量文献列表.md（可 --no-report 关闭）
#
# 用法：
#   uv run python 流程1/scripts/refs_backfill.py
#     --min-citations=0       被引门槛（**v7.2 默认 0 = 不设门槛**；只作排序信号）
#     --recent-years=5        "近年"的窗口（默认 5 年）
#     --no-review-fulltext    跳过为综述取 OA 全文/详细摘要
#     --limit-sources=0       只处理前 N 篇作者论文（0=全部；调试用）
#     --top=0                 报告里最多列多少条（0=全部）
#     --no-report             不生成 md 交付物
#     --offline               **不联网**，只用 cache/ 里已有的响应（测试用）
#     --self-test             用内置夹具跑一遍纯逻辑自检（不联网）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_EMAIL（OpenAlex polite pool 用）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

def _fix_streams() -> None:
    """UTF-8 兜底（**必须幂等**）。

    ★ 为什么不能无条件 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`：
      本流程的脚本之间会互相 import（refs_bibliography → refs_backfill，
      refs_build → refs_prepare）。每 import 一次就重包一层，中间那层 wrapper
      被 GC 时会**关掉底层 buffer**，表现为后续所有 print 抛
      "ValueError: I/O operation on closed file" —— 而且只在特定 import 顺序下复现。
      这里与 `logutil._fix_stdout()` 用同一套判据：已是 UTF-8 就不再包。
    """
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
REFDIR = CDPDIR / "refs"
CACHEDIR = REFDIR / "cache"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
EMAIL = os.environ.get("PAPER_EMAIL", "").strip() or "scientific-agent-skills@example.org"
UA = f"scientific-agent-skills/2.65 (mailto:{EMAIL})"

OA = "https://api.openalex.org"

# ★ primary_location 必须一起取：阶段 G2 要靠它的 source.id 去查期刊影响因子，
#   复用同一份缓存可以省掉一整轮重复请求。
SEL_IDS = ("id,doi,title,publication_year,referenced_works,cited_by_count,type,"
           "primary_location")
SEL_REF = ("id,doi,title,publication_year,cited_by_count,type,primary_location,"
           "authorships,abstract_inverted_index")

# ══════════════════════════════════════════════════════════════
# 核心期刊表
#
# ★ 为什么用**显式清单**而不是前缀匹配：
#   "science" 前缀会命中 Science of the Total Environment（非核心）；
#   "cell"   前缀会命中 Cellulose / Cells / Cellular Signalling（非核心）。
#   前缀误判会让"高质量"定义失真，必须逐条列举。
#   Nature 家族用前缀（Nature 是自有品牌，前缀安全）。
# ══════════════════════════════════════════════════════════════
NATURE_PREFIX = "nature"
EXACT_CORE = {
    # Science 家族
    "science", "science advances", "science translational medicine",
    "science immunology", "science robotics", "science signaling",
    "science partner journal",
    # Cell 家族
    "cell", "cell reports", "cell reports medicine", "cell metabolism",
    "cell stem cell", "cell chemical biology", "cell systems", "cell research",
    "cell discovery", "cell host microbe", "cell host & microbe",
    "molecular cell", "developmental cell", "cancer cell", "cancer discovery",
    "immunity", "neuron", "current biology", "chem", "chemm",
    "molecular therapy", "structure", "med", "patterns", "stem cell reports",
    "the lancet", "lancet", "new england journal of medicine",
    "the new england journal of medicine", "jama", "bmj",
    "nature", "pnas",
}
PNAS_PREFIX = "proceedings of the national academy of sciences"

# v7.2：优先级评分权重（**全部公开在报告里**，可复核、可调参）
W_REVIEW = 40.0      # 是综述 —— 本版的核心关注点
W_CORE = 25.0        # 发表于 CNS 家族 / 其他核心期刊
W_RECENT = 20.0      # 近 N 年（每远一年减 4 分，减到 0 为止）
W_CITED = 15.0       # 被引次数（对数尺度，**不再是一道门槛**）
W_COCITE = 10.0      # 被作者本人引用过几篇（综述网络里的边）


def norm_venue(v: str) -> str:
    """期刊名归一：小写、去标点、压空白。"""
    s = (v or "").lower()
    s = s.replace("&amp;", "&")
    s = re.sub(r"[^\w\s&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


JOURNAL_FAMILY: dict[str, str] = {}


def load_core_journals() -> dict:
    """内置核心期刊表 + 用户自定义扩展。返回 {family: [normalized names]}。"""
    fam = {
        "Nature 家族": [],          # 前缀匹配
        "Science 家族": ["science", "science advances", "science translational medicine",
                         "science immunology", "science robotics", "science signaling"],
        "Cell 家族": ["cell", "cell reports", "cell reports medicine", "cell metabolism",
                      "cell stem cell", "cell chemical biology", "cell systems",
                      "cell research", "cell discovery", "cell host microbe",
                      "molecular cell", "developmental cell", "cancer cell",
                      "cancer discovery", "immunity", "neuron", "current biology",
                      "chem", "molecular therapy", "structure", "med", "patterns",
                      "stem cell reports"],
        "PNAS": ["proceedings of the national academy of sciences"],
        "NEJM/Lancet/JAMA/BMJ": ["new england journal of medicine",
                                 "the new england journal of medicine",
                                 "the lancet", "lancet", "jama", "bmj"],
    }
    extra = CDPDIR / "refs" / "core_journals.json"
    if extra.exists():
        try:
            data = json.loads(extra.read_text(encoding="utf-8"))
            for k, v in (data or {}).items():
                if str(k).startswith("_"):
                    continue
                fam[k] = [norm_venue(x) for x in (v or [])]
        except Exception as e:
            print(f"⚠ refs/core_journals.json 解析失败，忽略：{e}", file=sys.stderr)
    return {k: [norm_venue(x) for x in v] for k, v in fam.items()}


def core_family(venue: str, fam: dict) -> str:
    """返回命中的核心期刊家族名；未命中返回 ''。"""
    n = norm_venue(venue)
    if not n:
        return ""
    if n == NATURE_PREFIX or n.startswith(NATURE_PREFIX):
        # "nature" 前缀：Nature / Nature Communications / Nature Reviews … 都是自有品牌
        return "Nature 家族"
    if n.startswith(PNAS_PREFIX):
        return "PNAS"
    for k, names in fam.items():
        if k == "Nature 家族":
            continue
        if n in names:
            return k
    return ""


# ══════════════════════════════════════════════════════════════
# HTTP（带缓存 + 退避重试；--offline 时只读缓存）
# ══════════════════════════════════════════════════════════════
OFFLINE = "--offline" in sys.argv
_CALLS = {"n": 0}


def cache_path(url: str) -> pathlib.Path:
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return CACHEDIR / f"{h}.json"


def api(url: str, tries: int = 4):
    """GET + 磁盘缓存。离线模式下缓存未命中 → 返回 None（不报错）。"""
    cp = cache_path(url)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cp.unlink(missing_ok=True)   # 缓存损坏：删掉重取，别让它毒化后续
    if OFFLINE:
        return None
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            CACHEDIR.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            _CALLS["n"] += 1
            time.sleep(0.12)          # polite pool：约 8 req/s，留出余量
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (i + 1))
                continue
            if e.code == 404:
                return None
            break
        except Exception as e:        # 网络中断 / 超时
            last = e
            time.sleep(1.5 * (i + 1))
    print(f"⚠ 请求失败（已重试 {tries} 次）：{url}\n   {last}", file=sys.stderr)
    return None


def chunk(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def wid(x) -> str:
    return str(x or "").rsplit("/", 1)[-1]


def norm_doi(x) -> str:
    s = str(x or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", s)


def abstract_of(inv) -> str:
    """OpenAlex 倒排索引 → 摘要文本。"""
    if not isinstance(inv, dict) or not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs or []:
            pos[int(i)] = w
    return " ".join(pos[k] for k in sorted(pos))


def author_names(authships) -> list[str]:
    out = []
    for a in authships or []:
        n = ((a or {}).get("author") or {}).get("display_name")
        if n:
            out.append(n)
    return out


# ══════════════════════════════════════════════════════════════
def load_records() -> dict:
    """读题录总表；用 scope.py 排除同名他人（只排除肯定性判定）。"""
    p = CDPDIR / "crossref_all.json"
    if not p.exists():
        p = CDPDIR / "master.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    if "records" in data and isinstance(data["records"], list):
        data = {norm_doi(r.get("doi")): r for r in data["records"] if isinstance(r, dict)}
    try:
        from scope import excluded_dois  # type: ignore
        ex = excluded_dois(CDPDIR, len(data)) or set()
        if ex:
            data = {k: v for k, v in data.items() if norm_doi(k) not in ex}
            print(f"ℹ scope.py：已排除 {len(ex)} 篇同名他人论文")
    except Exception:
        pass          # scope 不可用不阻塞：宁可多留，不可漏收
    return {norm_doi(k): v for k, v in data.items() if isinstance(v, dict)}


def resolve_sources(recs: dict, limit: int = 0) -> dict:
    """作者论文 DOI → OpenAlex work（拿 referenced_works）。"""
    out = {}
    dois = [d for d in recs if d]
    if limit:
        dois = dois[:limit]
    for i, grp in enumerate(chunk(dois, 40), 1):
        f = "doi:" + "|".join(grp)
        url = f"{OA}/works?filter={urllib.parse.quote(f, safe=':|')}&per-page=50&select={SEL_IDS}"
        d = api(url)
        if not d:
            continue
        bydoi = {}
        for r in d.get("results") or []:
            bydoi[norm_doi(r.get("doi"))] = r
        for doi in grp:
            r = bydoi.get(doi)
            rec = recs.get(doi) or {}
            if r is None:
                out[doi] = {"openalex": "", "title": rec.get("title") or "",
                            "year": rec.get("year"), "refs": [], "missing": True}
                continue
            pl = r.get("primary_location") or {}
            src = pl.get("source") or {}
            out[doi] = {
                "openalex": wid(r.get("id")),
                "title": r.get("title") or rec.get("title") or "",
                "year": r.get("publication_year") or rec.get("year"),
                "refs": [wid(x) for x in (r.get("referenced_works") or [])],
                # ★ source_id / venue 供阶段 G2 查期刊影响因子，避免重复请求
                "source_id": wid(src.get("id")) if src.get("id") else "",
                "venue": src.get("display_name") or rec.get("journal") or "",
                "missing": False,
            }
        print(f"  解析作者论文 {min(i * 40, len(dois))}/{len(dois)}")
    return out


def fetch_pool(all_wids: list[str]) -> dict:
    """批量取参考文献题录。"""
    pool: dict[str, dict] = {}
    fam = load_core_journals()
    todo = [w for w in all_wids if w and w not in pool]
    total = len(todo)
    for i, grp in enumerate(chunk(todo, 50), 1):
        f = "openalex_id:" + "|".join(grp)
        url = f"{OA}/works?filter={urllib.parse.quote(f, safe=':|')}&per-page=50&select={SEL_REF}"
        d = api(url)
        if d:
            for r in d.get("results") or []:
                w = wid(r.get("id"))
                pl = r.get("primary_location") or {}
                src = pl.get("source") or {}
                venue = src.get("display_name") or ""
                pool[w] = {
                    "id": w,
                    "doi": norm_doi(r.get("doi")),
                    "title": (r.get("title") or "").strip(),
                    "year": r.get("publication_year"),
                    "venue": venue,
                    "type": (r.get("type") or "").strip(),
                    "cited_by_count": int(r.get("cited_by_count") or 0),
                    "authors": author_names(r.get("authorships")),
                    "abstract": abstract_of(r.get("abstract_inverted_index")),
                    "core_family": core_family(venue, fam),
                }
        if i % 4 == 0 or i * 50 >= total:
            print(f"  取参考文献题录 {min(i * 50, total)}/{total}")
    return pool


def is_review(rec: dict) -> bool:
    """OpenAlex 没有 review 类型；用 type + 题名/刊名的经验判据。

    ★ 明确标注为启发式：OpenAlex 的 `type` 只有 article/review 之外的粗分类，
      真正的综述标识来自期刊名（"… Reviews"）与题名（Review/A survey/…）。
      判据写在报告里，读者可自行复核。
    """
    t = (rec.get("type") or "").lower()
    if t in ("review", "review-article"):
        return True
    venue = (rec.get("venue") or "").lower()
    title = (rec.get("title") or "").lower()
    if re.search(r"\breviews?\b", venue):
        return True
    if re.match(r"^(a\s+)?(review|survey|overview|perspective|advances in|"
                r"progress in|recent advances|state of the art)\b", title):
        return True
    if re.search(r"\b(a|an)\s+(review|survey|overview)\b", title):
        return True
    return False


def priority_of(rec: dict, n_sources: int, recent_years: int, this_year: int):
    """v7.2 优先级评分。返回 (总分, 分量字典)。

    ★ 为什么把 200 次被引从"门槛"降为"信号"：
      被引阈值会把**近两年的高质量综述**整批挡在门外 —— 而它们正是本版要重点看的。
      新版把"是不是综述 / 是不是 CNS / 够不够新"放在前三位，被引只占 15 分，
      并且用**对数尺度**：1000 次与 100 次被引只差几分，不主导排序。
    """
    yr = rec.get("year")
    age = (this_year - int(yr)) if str(yr or "").isdigit() else 99
    par = {
        "综述": W_REVIEW if rec.get("is_review") else 0.0,
        "核心期刊": W_CORE if rec.get("core_family") else 0.0,
        "近期": (max(0.0, W_RECENT - 4.0 * max(0, age - 1))
                 if age <= recent_years else 0.0),
        "被引": (min(W_CITED, 5.0 * math.log10((rec.get("cited_by_count") or 0) + 1))
                 if (rec.get("cited_by_count") or 0) else 0.0),
        "共引": min(W_COCITE, 3.0 * n_sources),
    }
    return round(sum(par.values()), 1), {k: round(v, 1) for k, v in par.items()}


def europepmc_fulltext(doi: str) -> tuple[str, str]:
    """开放获取综述的**全文**（Europe PMC fullTextXML → 纯文本）。

    → (文本, 说明)。取不到返回 ("", 原因)。

    ★ 为什么这条路是允许的（§5.1 分层策略）：
      它不是"从出版社网页抓正文"，而是 **Europe PMC 的开放获取结构化接口**，
      与我们一直在用的 `jats_to_text.py` 是同一条路。**非 OA 一律返回空**，
      绝不拿它去绕付费墙 —— 那种情况走"详细摘要"。
    """
    q = urllib.parse.quote(f'DOI:"{doi}"')
    d = api(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}"
            f"&resultType=core&format=json&pageSize=1")
    res = (((d or {}).get("resultList") or {}).get("result") or []) \
        if isinstance(d, dict) else []
    if not res:
        return "", "Europe PMC 无记录"
    r = res[0]
    if str(r.get("isOpenAccess") or "").upper() != "Y":
        return "", "非开放获取"
    if str(r.get("inEPMC") or "").upper() != "Y":
        return "", "Europe PMC 无全文"
    pmcid = r.get("pmcid") or ""
    if not pmcid:
        return "", "无 PMCID"
    try:
        req = urllib.request.Request(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=45) as fh:
            xml = fh.read().decode("utf-8", "replace")
    except Exception as e:
        return "", f"全文下载失败：{type(e).__name__}"
    # 复用 jats_to_text 的解析（同一实现，避免两套口径）
    try:
        import jats_to_text as j2t                       # type: ignore
        txt = j2t.to_text(xml) if hasattr(j2t, "to_text") else ""
    except Exception:
        txt = ""
    if not txt:
        txt = re.sub(r"<[^>]+>", " ", xml)
        txt = re.sub(r"\s+", " ", txt).strip()
    return txt, (f"Europe PMC OA 全文（{pmcid}，{len(txt)} 字符）" if txt else "解析为空")


def enrich_reviews(quality: dict, pool: dict, flag: bool = True) -> dict:
    """为**综述**补全文或详细摘要，提高下游 review 的置信度。

    证据层级（写进每条记录，下游按它决定"这条结论有多可信"）：
      fulltext         —— 拿到 OA 全文（Europe PMC）
      detailed-abstract —— 摘要足够长（≥800 字符），通常含方法/结果
      abstract         —— 只有常规摘要
      metadata-only    —— 连摘要都没有（只能依据题录）
    """
    stats = {"fulltext": 0, "detailed-abstract": 0, "abstract": 0,
             "metadata-only": 0, "attempted": 0}
    if not flag:
        return stats
    ftdir = REFDIR / "fulltext"
    for it in quality.get("items") or []:
        if not it.get("is_review"):
            continue
        abs_txt = (it.get("abstract") or "").strip()
        level = ("detailed-abstract" if len(abs_txt) >= 800
                 else ("abstract" if abs_txt else "metadata-only"))
        it["evidence_level"] = level
        it["fulltext_path"] = ""
        if not OFFLINE and it.get("doi"):
            stats["attempted"] += 1
            txt, why = europepmc_fulltext(it["doi"])
            it["fulltext_note"] = why
            if txt and len(txt) > 3000:
                ftdir.mkdir(parents=True, exist_ok=True)
                fp = ftdir / f"{it.get('id') or wid(it['doi'])}.txt"
                fp.write_text(txt, encoding="utf-8")
                it["fulltext_path"] = str(fp)
                it["fulltext_chars"] = len(txt)
                it["evidence_level"] = "fulltext"
        stats[it["evidence_level"]] += 1
    return stats


def build_quality(pool: dict, sources: dict, min_cites: int,
                  recent_years: int = 5) -> dict:
    """候选池筛选 + 优先级排序（v7.2：综述优先，被引不再是门槛）。"""
    import datetime
    this_year = datetime.date.today().year

    cited_by_sources: dict[str, list[str]] = {}
    for doi, s_ in sources.items():
        for w in s_.get("refs") or []:
            cited_by_sources.setdefault(w, []).append(doi)

    items = []
    for w, rec in pool.items():
        srcs = cited_by_sources.get(w) or []
        if not srcs:
            continue                     # 网络里的边必须存在
        rev = is_review(rec)
        cited = rec["cited_by_count"]
        core = rec["core_family"]
        # ★ 必须把刚算出的 is_review **带进去**再评分：
        #   priority_of 读的是 rec["is_review"]，而该字段要到下面 rec.update() 才写回。
        #   直接传原始 rec 会让「综述」这一项恒为 0 分 —— 权重最高的 40 分静默失效，
        #   排序退化成"按被引排"，恰好是本次改版要摆脱的行为。
        prio, parts = priority_of({**rec, "is_review": rev}, len(srcs),
                                  recent_years, this_year)

        # ── 入选门槛（v7.2）────────────────────────────────────
        #   ★ 关键：**"近期"只影响排序，不能单独构成入选理由**。
        #     若让"近 5 年"单独放行，等于把近五年的参考文献**全部**收进来
        #     （实测 235 条 → 几乎全进），"高质量"就没有意义了。
        #   真正的门槛是：综述 / 核心期刊 / 高被引(仅在设了阈值时) / 强共引。
        enters = []
        if rev:
            enters.append("综述")
        if core:
            enters.append(f"核心期刊（{core}）")
        if min_cites and cited > min_cites:
            enters.append(f"被引 {cited} > {min_cites}")
        if len(srcs) >= 3:
            enters.append(f"被作者 {len(srcs)} 篇论文引用（网络核心）")
        if not enters:
            continue
        reasons = list(enters)
        if parts["近期"] > 0:
            reasons.append(f"近 {recent_years} 年内（{rec.get('year')}）")
        if cited and not (min_cites and cited > min_cites):
            reasons.append(f"被引 {cited}")
        rec = dict(rec)
        rec.update({
            "is_review": rev,
            "cited_by_sources": srcs,
            "n_sources": len(srcs),
            "reasons": reasons,
            "priority": prio,
            "priority_parts": parts,
            # ★ 排序：优先级 → 综述优先 → 共引 → 被引 → 年份新
            "rank": (-prio, 0 if rev else 1, -len(srcs), -cited,
                     -(rec.get("year") or 0)),
        })
        items.append(rec)

    items.sort(key=lambda r: r["rank"])
    n_rev = len([r for r in items if r["is_review"]])
    n_rev_recent = len([r for r in items
                        if r["is_review"] and r["priority_parts"]["近期"] > 0
                        and r["core_family"]])
    return {
        "min_citations": min_cites,
        "recent_years": recent_years,
        "items": items,
        "stats": {
            "作者论文数": len(sources),
            "作者论文无参考文献记录": len([s_ for s_ in sources.values()
                                          if not s_.get("refs")]),
            "参考文献条目总数": sum(len(s_.get("refs") or [])
                                    for s_ in sources.values()),
            "参考文献去重后": len(pool),
            "候选（高质量）文献数": len(items),
            "其中综述": n_rev,
            "近年核心期刊综述（重点）": n_rev_recent,
            "未取到题录": len([w for w, r in pool.items() if not r.get("title")]),
        },
    }


# ══════════════════════════════════════════════════════════════
# 报告
# ══════════════════════════════════════════════════════════════
def gbt(rec: dict) -> str:
    """GB/T 7714-2015 期刊论文著录（前 3 位作者 + et al.）。"""
    au = rec.get("authors") or []
    names = []
    for a in au[:3]:
        names.append(a)
    astr = ", ".join(names) + (", et al." if len(au) > 3 else "")
    # ★ 作者串以 "et al." 结尾时**不再补句点**，否则会出现 "et al.." 双句点
    #   （[版本变动.md](版本变动.md) ⑦ 修过同一个错，这里保持一致）
    sep = "" if astr.endswith(".") else "."
    title = (rec.get("title") or "").rstrip(".")
    venue = rec.get("venue") or ""
    year = rec.get("year") or ""
    s = f"{astr}{sep} {title}[J]. {venue}, {year}."
    if rec.get("doi"):
        s += f" DOI: {rec['doi']}. 原文链接：https://doi.org/{rec['doi']}"
    return re.sub(r"\s+", " ", s).strip()


def write_report(quality: dict, sources: dict, top: int = 0) -> pathlib.Path:
    items = quality["items"]
    shown = items[:top] if top else items
    L = []
    L.append(f"# {AUTHOR} · 反向查找参考文献（高质量文献列表）\n")
    L.append(f"> 数据来源：OpenAlex `referenced_works`（元数据 API，全程无浏览器、无 PDF 下载）")
    L.append(f"> 高质量判据：核心期刊（Nature / Science / Cell / PNAS 等） **或** "
             f"被引次数 > {quality['min_citations']}；**优先输出 Review**\n")

    st = quality["stats"]
    L.append("## 一、统计\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    for k, v in st.items():
        L.append(f"| {k} | {v} |")
    L.append("")

    n_rev = st["其中综述"]
    L.append("## 二、综述优先清单\n")
    L.append(f"共 **{len(items)}** 条高质量文献，其中综述 **{n_rev}** 条。")
    L.append("排序规则：**综述优先** → 被作者引用的篇数降序 → 总被引降序 → 年份新者优先。\n")
    L.append("| # | 类型 | 题名 | 期刊 | 年 | 被引 | 被作者引用 | 入选理由 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(shown, 1):
        t = (r.get("title") or "（无题名）").replace("|", "\\|")
        if len(t) > 90:
            t = t[:87] + "…"
        L.append(f"| {i} | {'综述' if r['is_review'] else '研究论文'} | {t} | "
                 f"{(r.get('venue') or '—')} | {r.get('year') or '—'} | "
                 f"{r['cited_by_count']} | {r['n_sources']} | {'；'.join(r['reasons'])} |")
    if top and len(items) > top:
        L.append(f"\n> ⚠ 表内仅列前 {top} 条（共 {len(items)} 条）；全量见 "
                 f"`.work_cdp/refs/high_quality.json`。**这里是有意截断，不是全部。**")
    L.append("")

    L.append("## 三、GB/T 7714 详细著录\n")
    for i, r in enumerate(shown, 1):
        L.append(f"{i}. {gbt(r)}")
    L.append("")

    L.append("## 四、反向索引（哪几篇作者论文引用了它）\n")
    L.append("> 这张表就是「综述网络」的边：一条边 = 作者的一篇论文引用了这条文献。\n")
    for i, r in enumerate(shown[:60], 1):
        t = (r.get("title") or "（无题名）")[:70]
        srcs = r.get("cited_by_sources") or []
        L.append(f"{i}. **{t}** ← {len(srcs)} 篇：" + "；".join(f"`{d}`" for d in srcs[:6])
                 + ("…" if len(srcs) > 6 else ""))
    L.append("")

    L.append("## 五、诚实性声明\n")
    L.append("- 高质量判据里的「核心期刊」用的是**显式清单**（见 `refs_backfill.py` 的 "
             "`load_core_journals()`），不用前缀匹配 —— `science` / `cell` 前缀会命中 "
             "Science of the Total Environment、Cellulose 这类非核心刊，必须逐条列举。")
    L.append("- 「综述」是**启发式判定**（OpenAlex 无 review 类型；判据 = type + 刊名含 "
             "Reviews + 题名以 Review/Survey/Overview… 开头），可能有漏判或误判，已逐条标出理由。")
    L.append("- 被引次数来自 OpenAlex `cited_by_count`，与 Google Scholar / Web of Science "
             "**口径不同、数值不同**，不要把这里的数字写进需要与其他数据库对齐的场合。")
    L.append("- 参考文献来自 OpenAlex 的存缴数据，**不完整**：作者论文中 "
             f"{st['作者论文无参考文献记录']} 篇在 OpenAlex 没有参考文献记录。"
             "缺记录不等于该文没有参考文献。")
    L.append("")

    p = WD / f"{AUTHOR}_高质量文献列表.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    """纯逻辑自检（不联网）：核心期刊判定 / 综述判定 / 筛选 / 排序 / 著录。"""
    fam = load_core_journals()
    cases = [
        ("Nature", "Nature 家族", True),
        ("Nature Communications", "Nature 家族", True),
        ("Nature Reviews Molecular Cell Biology", "Nature 家族", True),
        ("Science", "Science 家族", True),
        ("Science Advances", "Science 家族", True),
        ("Science of the Total Environment", "", False),      # ★ 前缀陷阱
        ("Cell", "Cell 家族", True),
        ("Cell Reports", "Cell 家族", True),
        ("Cellulose", "", False),                             # ★ 前缀陷阱
        ("Cells", "", False),                                 # ★ 前缀陷阱
        ("Cellular Signalling", "", False),                   # ★ 前缀陷阱
        ("Proceedings of the National Academy of Sciences", "PNAS", True),
        ("Journal of the American Chemical Society", "", False),
    ]
    bad = 0
    for venue, want_fam, want_hit in cases:
        got = core_family(venue, fam)
        ok = (got == want_fam) if want_hit else (got == "")
        if not ok:
            bad += 1
            print(f"  ✗ core_family({venue!r}) = {got!r}，期望 {want_fam!r}")
    print(f"核心期刊判定：{len(cases) - bad}/{len(cases)} 通过")

    rv = [
        ({"type": "article", "venue": "Nature Reviews Cancer", "title": "X"}, True),
        ({"type": "article", "venue": "Cell", "title": "A survey of X"}, True),
        ({"type": "article", "venue": "Cell", "title": "A novel method for X"}, False),
        ({"type": "review", "venue": "Foo", "title": "Y"}, True),
    ]
    bad2 = 0
    for rec, want in rv:
        got = is_review(rec)
        if got != want:
            bad2 += 1
            print(f"  ✗ is_review({rec['title']!r} @ {rec['venue']!r}) = {got}，期望 {want}")
    print(f"综述判定：{len(rv) - bad2}/{len(rv)} 通过")

    pool = {
        "W1": {"id": "W1", "doi": "10.1/a", "title": "Nature paper", "year": 2020,
               "venue": "Nature", "type": "article", "cited_by_count": 10,
               "authors": ["A B", "C D", "E F", "G H"], "abstract": "",
               "core_family": "Nature 家族"},
        "W2": {"id": "W2", "doi": "10.1/b", "title": "Review of X", "year": 2019,
               "venue": "Chem Soc Rev", "type": "article", "cited_by_count": 500,
               "authors": ["A B"], "abstract": "", "core_family": ""},
        "W3": {"id": "W3", "doi": "10.1/c", "title": "Nobody cites me", "year": 2021,
               "venue": "J Foo", "type": "article", "cited_by_count": 3,
               "authors": ["Z Z"], "abstract": "", "core_family": ""},
    }
    sources = {"10.1/src1": {"refs": ["W1", "W2", "W3"], "title": "src1", "year": 2022},
               "10.1/src2": {"refs": ["W2"], "title": "src2", "year": 2023}}
    q = build_quality(pool, sources, 0, recent_years=5)
    got_ids = [r["id"] for r in q["items"]]
    assert got_ids == ["W2", "W1"], f"排序错误：{got_ids}（综述 W2 应在最前）"
    assert q["stats"]["候选（高质量）文献数"] == 2, q["stats"]
    assert q["stats"]["其中综述"] == 1, q["stats"]
    # ★ v7.2 关键不变量：**被引 0 次但近年的核心期刊综述**也必须入选
    #   （旧版 200 次被引门槛会把它整条挡掉 —— 这正是本次改版要修的问题）
    fresh = {"id": "W9", "doi": "10.1/z", "title": "A review of X",
             "year": 2026, "venue": "Nature Reviews Chemistry", "type": "article",
             "cited_by_count": 0, "authors": ["A B"], "abstract": "",
             "core_family": "Nature 家族"}
    p2 = dict(pool); p2["W9"] = fresh
    q2 = build_quality(p2, {"10.1/s": {"refs": ["W9"]}}, 0, recent_years=5)
    ids2 = [r["id"] for r in q2["items"]]
    assert "W9" in ids2, "被引 0 的近年 CNS 综述被门槛挡掉了"
    assert q2["items"][0]["priority_parts"]["综述"] == W_REVIEW
    print("筛选与排序：通过（优先级排序；**被引 0 的近年 CNS 综述照样入选**）")

    s = gbt(pool["W1"])
    assert "et al." in s and "Nature" in s and "https://doi.org/10.1/a" in s, s
    assert "et al.." not in s, "出现 et al.. 双句点"
    print("GB/T 7714 著录：通过")
    print("\n✅ 自检全部通过" if bad == 0 and bad2 == 0 else "\n❌ 自检有失败项")
    return 0 if bad == 0 and bad2 == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("refs")
    min_cites = int(_argval("min-citations", "0") or 0)
    try:
        recent_years = int(_argval("recent-years", "5") or 5)
    except ValueError:
        print("✗ --recent-years 必须是整数")
        return 2
    no_review_ft = "--no-review-fulltext" in sys.argv
    limit = int(_argval("limit-sources", "0") or 0)
    top = int(_argval("top", "0") or 0)
    no_report = "--no-report" in sys.argv

    REFDIR.mkdir(parents=True, exist_ok=True)

    recs = load_records()
    if not recs:
        print("✗ 找不到题录总表 .work_cdp/crossref_all.json")
        print("  请先跑阶段 C/D：orcid_extract → fetch_crossref_all → disambiguate")
        return 2
    print(f"作者论文：{len(recs)} 篇" + ("（离线模式，只读缓存）" if OFFLINE else ""))

    sources = resolve_sources(recs, limit=limit)
    (REFDIR / "source_refs.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    all_wids = sorted({w for s in sources.values() for w in (s.get("refs") or [])})
    print(f"参考文献条目：{sum(len(s.get('refs') or []) for s in sources.values())} 条，"
          f"去重后 {len(all_wids)} 条")
    pool = fetch_pool(all_wids)
    (REFDIR / "refs_pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")

    quality = build_quality(pool, sources, min_cites, recent_years=recent_years)
    # ★ v7.2：为综述补 OA 全文 / 标注证据层级 —— 下游 review 的置信度靠它
    ev = enrich_reviews(quality, pool, flag=not no_review_ft)
    quality["evidence_stats"] = ev
    (REFDIR / "high_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n统计：")
    for k, v in quality["stats"].items():
        print(f"  {k}: {v}")

    if not no_report:
        p = write_report(quality, sources, top=top)
        print(f"\n✅ 交付物：{p}")

    journal("D2", "refs.backfill", n_sources=len(sources), n_refs=len(all_wids),
            n_quality=quality["stats"]["候选（高质量）文献数"],
            n_review=quality["stats"]["其中综述"],
            n_review_recent_core=quality["stats"]["近年核心期刊综述（重点）"],
            min_citations=min_cites, recent_years=recent_years,
            evidence=ev,
            offline=OFFLINE, api_calls=_CALLS["n"])
    log.info(f"✓ 阶段 D2 完成 → {REFDIR}")
    if quality["stats"]["作者论文无参考文献记录"]:
        print(f"⚠ {quality['stats']['作者论文无参考文献记录']} 篇作者论文在 OpenAlex "
              f"没有参考文献记录 —— 属正常（期刊未存缴），不是流程错误。")
    return 0


def _argval(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


if __name__ == "__main__":
    raise SystemExit(main())
