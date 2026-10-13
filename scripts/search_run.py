# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R①：执行检索（多源并行 + 去重合并）
#
# 复用工作文件夹已有的 `skills/paper-lookup/scripts/paginate.py` 作为**取数层**
#   —— 它已实现分页 + 命中数对账（`reconciliation.complete`），
#      以及 europepmc / openalex / crossref / biorxiv / medrxiv / arxiv 六种源。
#   **不重写检索层**：参考包（academic-research-skills）的 *_client.py 是
#   为「已知 DOI/题名的核验」设计的，不适合主题检索。
#
# 本脚本负责参考包里 bibliography_agent 的这几步：
#   Step 2 Execute Search（逐源执行、记录命中数、记录检索日期）
#   Step 4.5 Deduplication（DOI 优先，题名归一化兜底）
#
# 用法：
#   uv run python 流程1/scripts/search_run.py --plan outputs/07_检索/search_plan.json
#   uv run python 流程1/scripts/search_run.py --plan … --sources europepmc
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from logutil import run_root as _run_root  # noqa: E402

# ── UTF-8 兜底 ──


def _fix_streams() -> None:
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
    def get(name):                     # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)
        return _L()

    def journal(*a, **k):              # noqa: D103
        pass


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()

# paginate.py 的位置：既有 skill 的取数层。它**不在本次输出目录下** ——
# `PAPER_WORKDIR` 缺省是 `流程1/<年月日>/`，而 skills/ 在流程根、包根或工作文件夹，
# 故按候选根目录逐个探测（与 `preflight_fallback.py` 同一判据）。
SKILL_ROOTS = [WD / "skills", WD.parent / "skills",
               pathlib.Path(__file__).resolve().parent.parent.parent / "skills"]
_PAGINATE_REL = pathlib.Path("paper-lookup") / "scripts" / "paginate.py"
PAGINATE = next((r / _PAGINATE_REL for r in SKILL_ROOTS if (r / _PAGINATE_REL).exists()),
                SKILL_ROOTS[0] / _PAGINATE_REL)

# paginate.py 实际支持的源（实测 --list-apis）：
#     biorxiv / crossref / europepmc / medrxiv / openalex
#   **不含 arxiv** —— arXiv 要用 skills/paper-lookup/scripts/arxiv_atom.py
#   对**已下载的 Atom XML** 做解析；即取数需另走 HTTP。
#   因此本脚本把 arxiv 标为「需 arxiv_atom.py 管道」，默认跳过而非假装成功。
PAGINATE_APIS = {"biorxiv", "crossref", "europepmc", "medrxiv", "openalex"}

# 各源 query 参数**格式不同**（实测 --list-apis），必须分别构造：
#   europepmc —— Europe PMC 检索语法（布尔可用）
#   openalex   —— 原始参数串，如 'search=crispr' 或 'filter=publication_year:2024'
#   crossref   —— 原始参数串，如 'query.bibliographic=attention'
#   biorxiv/medrxiv —— 日期区间（不接受主题词）
QUERY_STYLE = {
    "europepmc": "syntax",
    "openalex": "params",
    "crossref": "params",
    "biorxiv": "interval",
    "medrxiv": "interval",
}

# arXiv 官方限速：1 请求 / 3 秒。因此**必须串行**，不能与其他源抢占。
ARXIV_INTERVAL = 3.0

_failures: list[dict] = []


def _enc(s: str) -> str:
    """把参数值编码进 URL。

    为什么必须编码：paginate.py 把 --query **原样拼进 URL**，不做转义。
      实测 `search="tumor immune microenvironment"` 会抛
        http.client.InvalidURL: URL can't contain control characters（空格）
      —— openalex/crossref 因此直接 exit 1、整个源静默失败。

      注意：这里**只编码值，不编码结构字符** ——
        `=` `&` `,` `:` 在 openalex/crossref 的参数串里有语法含义，
        编码掉会把查询结构破坏（如 `filter=a,b` 变成 `filter=a%2Cb` 后
        OpenAlex 可能仍接受，但 `&search=` 被编码就彻底失效）。
      因此策略是：**先按结构切分，再只对每段的值做最小编码**。
    """
    # 空格 → +（OpenAlex/Crossref 都接受），其余保留
    return s.replace(" ", "+")


def build_source_query(src: str, q: dict) -> str:
    """把计划里的抽象检索式，翻译成**该源真正接受的 query 参数**。

    为什么必须分开翻译：paginate.py 的 --query 对各源含义完全不同
      （europepmc 收检索语法、openalex/crossref 收原始参数串、
      biorxiv/medrxiv 只收日期区间）。用同一条字符串喂所有源必然失败。

    另一条硬约束：paginate.py **不做 URL 编码**，含空格的查询会直接
      抛 InvalidURL 让整个源失败（实测 openalex/crossref 都栽在这里）。
      因此 openalex/crossref 的**参数值必须先把空格换成 +**。
    """
    ui = q.get("_user_input") or {}
    topic = (ui.get("topic_en") or "").strip()
    terms = ui.get("terms_en") or []
    y_from = (q.get("date_filter") or {}).get("from_year")
    y_to = (q.get("date_filter") or {}).get("through_year")
    doc = ui.get("doc_types") or "any"
    style = QUERY_STYLE.get(src, "syntax")

    # ── 日期区间源（biorxiv/medrxiv 只按日期检索）──
    if style == "interval":
        a = f"{y_from or 2020}-01-01"
        b = f"{y_to or time.gmtime().tm_year}-12-31"
        return f"{a}/{b}"

    # ── 主题词部分 ──
    def _q(t: str) -> str:
        t = (t or "").strip()
        return f'"{t}"' if re.search(r"[\s\-/]", t) and not t.startswith('"') else t

    core = ""
    if topic and terms:
        core = f"{_q(topic)} AND ({' OR '.join(_q(t) for t in terms)})"
    elif topic:
        core = _q(topic)
    elif terms:
        core = " OR ".join(_q(t) for t in terms)

    # ── 文献类型 ──
    dt = ""
    if doc == "review":
        dt = {"europepmc": '(PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")',
              "openalex": "type:review",
              "crossref": "type:journal-article"}.get(src, "")
    if ui.get("sort_cited") and src == "europepmc":
        # 实测坑：`(PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")` 与
        # `(SORT_CITED:y)` 同用会把结果池砍掉两个数量级（同一主题词池 343 → 4）。
        # 与 search_plan.py 同一判据：被引排序时用路由表 `europepmc_review_cited`
        # 的单一 Review。
        dt = '(PUB_TYPE:"Review")'

    # ── 按源拼最终 query ──
    if src == "europepmc":
        parts = [p for p in (core, dt) if p]
        if y_from or y_to:
            a = f"{y_from}-01-01" if y_from else "1800-01-01"
            b = f"{y_to}-12-31" if y_to else "2100-12-31"
            parts.append(f"(FIRST_PDATE:[{a} TO {b}])")
        if ui.get("oa_only"):
            parts.append("(OPEN_ACCESS:y)")
        if ui.get("sort_cited"):
            # paginate.py **没有 --sort 开关**，排序语法只能写进 query
            parts.append("(SORT_CITED:y)")
        return " AND ".join(f"({p})" for p in parts) if parts else "*"

    # openalex/crossref 的 search/query 参数**不接受布尔语法**，
    #   把 AND/OR/引号原样塞进去会查不到东西。退化成词袋更可靠。
    def _bag(s: str) -> str:
        s = re.sub(r'["()]', " ", s or "")
        s = re.sub(r"\b(?:AND|OR|NOT)\b", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    if src == "openalex":
        # OpenAlex 参数串：filter=...&search=...
        # 值里的空格必须换成 +（paginate 不编码，含空格会 InvalidURL）
        filters = []
        if y_from:
            filters.append(f"from_publication_date:{y_from}-01-01")
        if y_to:
            filters.append(f"to_publication_date:{y_to}-12-31")
        if doc == "review":
            filters.append("type:review")
        if ui.get("oa_only"):
            filters.append("is_oa:true")
        params = []
        if filters:
            params.append("filter=" + _enc(",".join(filters)))
        bag = _bag(core)
        if bag:
            params.append("search=" + _enc(bag))
        return "&".join(params) if params else "filter=type:review"

    if src == "crossref":
        params = []
        bag = _bag(core)
        if bag:
            params.append("query.bibliographic=" + _enc(bag))
        cf = []
        if y_from:
            cf.append(f"from-pub-date:{y_from}-01-01")
        if y_to:
            cf.append(f"until-pub-date:{y_to}-12-31")
        if cf:
            params.append("filter=" + _enc(",".join(cf)))
        return "&".join(params) if params else "query.bibliographic=review"

    return _enc(core)


def _run_source(q: dict, timeout: int = 240) -> list[dict] | None:
    """对单个源跑一次检索。返回记录列表；失败返回 None 并登记原因。

    关键设计：**一个源失败不影响其他源**（graceful degradation）。
      参考包 bibliography_agent 明确要求"一次瞬时失败只该丢掉该源的结果，
      不该中止整次检索"。
    """
    src = q["index_target"]
    year_from = (q.get("date_filter") or {}).get("from_year")
    year_to = (q.get("date_filter") or {}).get("through_year")
    max_rec = int(q.get("max_records") or 50)

    if not PAGINATE.exists():
        _failures.append({"source": src,
                          "reason": "缺少 paginate.py；已尝试 "
                                    + " | ".join(str(r / _PAGINATE_REL) for r in SKILL_ROOTS)})
        return None

    if src not in PAGINATE_APIS:
        _failures.append({
            "source": src,
            "reason": f"paginate.py 不支持该源（支持 {sorted(PAGINATE_APIS)}）；"
                      f"arXiv 需另走 arxiv_atom.py 管道",
        })
        return None

    query = build_source_query(src, q)
    cmd = [sys.executable, str(PAGINATE), "--api", src,
           "--query", query, "--max-records", str(max_rec)]

    # paginate.py 把结果以 UTF-8 JSON 写 stdout，但它**不**自己 reconfigure
    #   编码；在 GBK 控制台下若结果含非 GBK 字符（ä/ö/ą 等），子进程会抛
    #   UnicodeEncodeError 并 exit 1，整源失败。显式给子进程 UTF-8。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        _failures.append({"source": src, "reason": f"超时（{timeout}s）"})
        return None

    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    if r.returncode != 0:
        reason = {4: "命中数对账不足（reconciliation shortfall）",
                  5: "被限流"}.get(r.returncode, f"exit {r.returncode}")
        _failures.append({"source": src, "reason": reason,
                          "query": query[:160],
                          "stderr": err.strip()[-300:]})
        return None
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        _failures.append({"source": src, "reason": "输出不是合法 JSON",
                          "stdout_head": out[:200]})
        return None

    recs = payload.get("records") or []
    recon = payload.get("reconciliation") or {}
    for rec in recs:
        rec["_source"] = src
        rec["_query_id"] = q.get("query_id", "")
    if recs:
        recs[0]["_reconciliation"] = recon
    return recs


def _as_text(v) -> str:
    """把任意形状的字段值收敛成字符串。

    为什么需要：三个源的字段类型**完全不同**（实测）——
        europepmc  title=str   doi=str        pubYear=str
        openalex   title=str   doi=完整URL     publication_year=int
        crossref   title=**list**  DOI=大写键  issued/published=dict
      不归一化的话，去重时 `title.lower()` 会直接抛
      `AttributeError: 'list' object has no attribute 'lower'`（实测崩溃过）。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        # Crossref 的 title/container-title 是数组，取第一个非空元素
        for x in v:
            t = _as_text(x)
            if t:
                return t
        return ""
    if isinstance(v, dict):
        for k in ("value", "name", "title", "text", "URL", "url"):
            if v.get(k):
                return _as_text(v[k])
        return ""
    return str(v)


def _unescape(s: str) -> str:
    """还原 HTML/XML 实体并去标签。

    Europe PMC 与 Crossref 会返回转义文本（`&lt;i&gt;`）或内嵌 JATS 标签
      （`<jats:p>`）。不处理会一路带进报告，用户直接看到噪声（实测出现过）。
    """
    import html as _h
    t = _h.unescape(s or "")
    t = re.sub(r"<[^>]{0,80}>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _reconstruct_abstract(v) -> str:
    """把 OpenAlex 的 abstract_inverted_index 还原成正文。

    OpenAlex 为省流量把摘要存成 `{词: [位置...]}`，需要按位置重排。
    """
    if isinstance(v, str):
        return v
    if not isinstance(v, dict):
        return ""
    pos: dict[int, str] = {}
    for word, idxs in v.items():
        if isinstance(idxs, (list, tuple)):
            for i in idxs:
                if isinstance(i, int):
                    pos[i] = word
    if not pos:
        return ""
    return " ".join(pos[i] for i in sorted(pos))


def _norm_title(t: str) -> str:
    """题名归一化，用于去重（在**已归一化**的字符串上调用）。

    归一化规则（保守，避免把不同论文误判为重复）：
      转小写 → 去所有非字母数字字符。
      不按副标题切分，因为同名不同副标题常是不同论文。
    """
    t = (t or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", t)


def _norm_doi(d: str) -> str:
    """DOI 归一化：去掉 URL 前缀与大小写差异（Crossref 用大写 `DOI` 键）。"""
    d = _as_text(d).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.strip()


def _is_review(type_str: str) -> bool:
    """从文献类型串判断是否综述（各源用词不同）。"""
    t = (type_str or "").lower()
    return any(k in t for k in ("review", "meta-analysis", "systematic"))


def normalize_record(rec: dict, src: str) -> dict:
    """把**源原生记录**归一成统一形状。

    这是本脚本最关键的一层：没有它，去重、筛选、统计都要为每个源
      各写一套分支，且极易因字段类型差异崩溃（实测因 crossref 的
      title 是 list 而整套流程 exit 1）。
    """
    year = (_as_text(rec.get("pubYear")) or _as_text(rec.get("publication_year"))
            or _as_text(rec.get("year")))
    if not year:
        for k in ("issued", "published", "published-print", "published-online"):
            v = rec.get(k)
            if isinstance(v, dict):
                dp = v.get("date-parts")
                if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                    year = str(dp[0][0])
                    break
    m = re.search(r"(19|20)\d{2}", str(year))
    year = m.group(0) if m else ""

    oa = rec.get("isOpenAccess")
    if isinstance(oa, str):
        oa_bool = oa.strip().upper() == "Y"
    elif isinstance(oa, dict):
        oa_bool = bool(oa.get("is_oa"))
    elif isinstance(rec.get("open_access"), dict):
        oa_bool = bool(rec["open_access"].get("is_oa"))
    else:
        oa_bool = bool(oa) if oa is not None else False

    # 作者：europepmc 给字符串，openalex/crossref 给结构化数组
    au = _as_text(rec.get("authorString"))
    if not au:
        al = rec.get("authorships") or rec.get("author") or []
        names = []
        if isinstance(al, list):
            for a in al:
                if isinstance(a, dict):
                    nm = (_as_text(a.get("name"))
                          or f"{_as_text(a.get('given'))} {_as_text(a.get('family'))}".strip()
                          or _as_text(a.get("author")))
                    if nm:
                        names.append(nm.strip())
        au = ", ".join(names[:12]) + (" 等" if len(names) > 12 else "")

    abstract = (_reconstruct_abstract(rec.get("abstract_inverted_index"))
                or _as_text(rec.get("abstractText"))
                or _as_text(rec.get("abstract")))

    journal = (_as_text(rec.get("journalTitle"))
               or _as_text(rec.get("container-title"))
               or _as_text(rec.get("primary_location"))
               or _as_text(rec.get("journal")))

    url = (_as_text(rec.get("URL")) or _as_text(rec.get("url")))
    doi = _norm_doi(rec.get("doi") or rec.get("DOI"))
    if not url and doi:
        url = f"https://doi.org/{doi}"

    t = _as_text(rec.get("title") or rec.get("display_name") or rec.get("name"))
    typ = _as_text(rec.get("pubType") or rec.get("type"))

    return {
        "title": _unescape(t),
        "doi": doi,
        "year": year,
        "journal": _unescape(journal),
        "authors": _unescape(au),
        "abstract": _unescape(abstract),
        "type": typ,
        "is_review": _is_review(typ),
        "oa": oa_bool,
        "url": url,
        "source": src,
        "sources": [src],
    }


def dedupe(records: list[dict]) -> tuple[list[dict], dict]:
    """归一化 + DOI 优先、题名兜底的去重。

    返回 (保留的记录, 统计)。被合并的记录来源追加到 `sources` 以便溯源。
    """
    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    out: list[dict] = []
    merged = 0

    for raw in records:
        src = raw.get("_source") or "?"
        rec = normalize_record(raw, src)
        if not rec["title"] and not rec["doi"]:
            continue                      # 无题名也无 DOI 的记录无法使用

        doi = rec["doi"]
        ti = _norm_title(rec["title"])

        hit = None
        if doi and doi in by_doi:
            hit = by_doi[doi]
        elif ti and len(ti) >= 20 and ti in by_title:
            hit = by_title[ti]

        if hit is not None:
            merged += 1
            # 合并：补齐空字段，来源并入 sources
            for k, v in rec.items():
                if k == "sources":
                    continue
                if not hit.get(k) and v:
                    hit[k] = v
            for s in rec["sources"]:
                if s not in hit["sources"]:
                    hit["sources"].append(s)
            # 综述标记：任一说它是综述就算（宁可多收，筛选阶段再判）
            hit["is_review"] = hit["is_review"] or rec["is_review"]
            hit["oa"] = hit["oa"] or rec["oa"]
            continue

        out.append(rec)
        if doi:
            by_doi[doi] = rec
        if ti and len(ti) >= 20:
            by_title[ti] = rec

    return out, {"before": len(records), "after": len(out), "merged": merged}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="search_run.py",
        description="阶段 R①：按检索计划多源并行检索并去重",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  search_run.py --plan outputs/07_检索/search_plan.json\n"
                "  search_run.py --plan … --sources europepmc,openalex\n"))
    ap.add_argument("--plan", required=True, help="search_plan.json 路径")
    ap.add_argument("--sources", help="只跑指定源（逗号分隔），默认按计划全跑")
    ap.add_argument("--out", help="输出目录，默认与 plan 同目录")
    ap.add_argument("--timeout", type=int, default=240, help="单源超时秒数")
    args = ap.parse_args(argv)

    log = get("search")
    pp = pathlib.Path(args.plan)
    if not pp.is_absolute():
        pp = WD / pp
    if not pp.exists():
        raise SystemExit(f"[X] 找不到检索计划 {pp}；先跑 search_plan.py")
    plan = json.loads(pp.read_text(encoding="utf-8"))

    d = pathlib.Path(args.out) if args.out else pp.parent
    if not d.is_absolute():
        d = WD / d
    d.mkdir(parents=True, exist_ok=True)

    only = {s.strip() for s in (args.sources or "").split(",") if s.strip()}
    ui = plan.get("user_input", {})
    queries = []
    for q in plan["queries"]:
        if only and q["index_target"] not in only:
            continue
        q = dict(q)
        q["_user_input"] = ui          # 供 build_source_query 翻译用
        queries.append(q)
    if not queries:
        raise SystemExit("[X] 没有要执行的查询（检查 --sources）")

    log.info(f"开始检索：{len(queries)} 个源，每源最多 "
             f"{queries[0].get('max_records')} 条")

    # ── 并行执行（arXiv 串行，因为它有 1 请求/3 秒的硬限速）──
    results: dict[str, list[dict]] = {}
    parallel = [q for q in queries if q["index_target"] != "arxiv"]
    serial = [q for q in queries if q["index_target"] == "arxiv"]

    t0 = time.time()
    if parallel:
        with cf.ThreadPoolExecutor(max_workers=min(4, len(parallel))) as ex:
            futs = {ex.submit(_run_source, q, args.timeout): q for q in parallel}
            for fut in cf.as_completed(futs):
                q = futs[fut]
                try:
                    recs = fut.result()
                except Exception as e:
                    _failures.append({"source": q["index_target"],
                                      "reason": f"未捕获异常：{e}"})
                    recs = None
                if recs is not None:
                    results[q["index_target"]] = recs
                    log.info(f"  [{q['index_label']}] 取回 {len(recs)} 条")

    for q in serial:
        recs = _run_source(q, args.timeout)
        if recs is not None:
            results[q["index_target"]] = recs
            log.info(f"  [{q['index_label']}] 取回 {len(recs)} 条")
        time.sleep(ARXIV_INTERVAL)
    elapsed = time.time() - t0

    # ── 汇总与去重 ──
    all_recs: list[dict] = []
    per_source: dict[str, dict] = {}
    for q in queries:
        src = q["index_target"]
        recs = results.get(src)
        if recs is None:
            per_source[src] = {"retrieved": 0, "status": "failed"}
            continue
        recon = {}
        for r in recs:
            if r.get("_reconciliation"):
                recon = r["_reconciliation"]
                break
        per_source[src] = {
            "retrieved": len(recs),
            "status": "ok",
            "expected_total": recon.get("expected_total"),
            "complete": recon.get("complete"),
            # 记录**真正发给该 API 的查询串**（不是计划里的抽象式）。
            #   各源 query 格式完全不同，报告里必须显示实际值才有可复现性。
            "actual_query": build_source_query(src, q),
        }
        all_recs.extend(recs)

    kept, dstats = dedupe(all_recs)

    payload = {
        "schema_version": "flow1-search-result/1.0",
        "plan_file": str(pp.name),
        "plan_sha256": (plan.get("reproducibility") or {}).get("plan_sha256", ""),
        "searched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "user_input": plan.get("user_input", {}),
        "per_source": per_source,
        "failures": _failures,
        "dedupe": dstats,
        "records": kept,
    }
    op = d / "search_raw.json"
    op.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 控制台摘要 ──
    print()
    print("═" * 68)
    print("[OK] 检索完成")
    print("═" * 68)
    print(f"  {'数据源':<14}{'取回':>6}{'该库命中总数':>14}{'完整性':>10}")
    print("  " + "-" * 46)
    for src, st in per_source.items():
        label = next((q["index_label"] for q in queries
                      if q["index_target"] == src), src)
        total = f"{st['expected_total']:,}" if st.get("expected_total") else "—"
        comp = ("完整" if st.get("complete") else "部分") if st["status"] == "ok" else "失败"
        print(f"  {label:<14}{st['retrieved']:>6}{total:>14}{comp:>10}")
    print("  " + "-" * 46)
    print(f"  合计取回 {dstats['before']} 条 → 去重后 **{dstats['after']}** 条"
          f"（合并重复 {dstats['merged']}）")
    if _failures:
        print()
        print("  [WARN] 失败的源（其余源的结果仍然可用）：")
        for f in _failures:
            print(f"    · {f['source']}：{f['reason']}")
    print()
    print(f"  耗时 {elapsed:.1f} 秒")
    print(f"→ {op}")
    print()
    print("  下一步：search_screen.py 两轮筛选")
    print("═" * 68)
    journal("R1", "search.run", sources=len(per_source),
            retrieved=dstats["before"], kept=dstats["after"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
