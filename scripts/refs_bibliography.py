# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 G2：代表性文献的参考文献列表（GB/T 7714-2015）
#
# 位置：紧随阶段 G（生成论文列表）之后，复用阶段 D2 已经取好的参考文献数据。
#
# 「有代表性的文献」= 作者本人的论文中，满足以下**任一**条件：
#   ① 发表于 Nature / Science / Cell / PNAS 等核心期刊；
#   ② 本人为**第一作者或通讯作者**（流程约定：末位作者视为通讯作者）；
#   ③ 期刊为「中科院一区」**或** 影响因子 IF > --if-threshold（默认 5.0）。
#
# 产出：<WORKDIR>/<作者名>_代表性文献参考文献.md
#   结构固定为**两部分**（用户指定）：
#     第一部分  代表性文献列表（GB/T 7714 + 入选理由）
#     第二部分  各代表性文献的参考文献（逐篇分组，GB/T 7714）
#
# 关于 IF 与分区 —— 怎么取、取不到怎么办：
#   · IF 用 OpenAlex source 的 `summary_stats.2yr_mean_citedness` 作**代理值**
#     （2 年平均被引，与 JCR 影响因子口径接近但**数值不等**，报告里如实标注）。
#   · 分区用内置**种子表**（`流程1/templates/期刊分区表_中科院一区.json`）——
#     官方《中科院分区表》是商业授权数据，不能内置全量。
#   · 取不到就**如实写「未判定」**，绝不把「查不到」当成「不是一区」。
#
# 输入（<WORKDIR>/.work_cdp/）：
#   crossref_all.json          题录总表（必需；取作者位次）
#   refs/source_refs.json      阶段 D2 产出（必需；取每篇的参考文献）
#   refs/refs_pool.json        阶段 D2 产出（必需）
#   journal_tiers.json         自定义分区表（可选，覆盖内置种子表）
#
# 用法：
#   uv run python 流程1/scripts/refs_bibliography.py
#     --if-threshold=5.0       IF 阈值
#     --max-refs-per-paper=0   每篇最多列多少条参考文献（0=全部）
#     --offline                不联网，只用阶段 D2 的缓存
#     --self-test              纯逻辑自检（不联网）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_NAME_TOKENS
#             （或 PAPER_SURNAME + PAPER_GIVEN）
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

from logutil import run_root as _run_root  # noqa: E402

def _fix_streams() -> None:
    """UTF-8 兜底（**必须幂等**）。

    为什么不能无条件 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`：
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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
REFDIR = CDPDIR / "refs"
CACHEDIR = REFDIR / "cache"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
EMAIL = os.environ.get("PAPER_EMAIL", "").strip() or "scientific-agent-skills@example.org"
UA = f"scientific-agent-skills/2.65 (mailto:{EMAIL})"
OA = "https://api.openalex.org"
OFFLINE = "--offline" in sys.argv

TPL = _HERE.parent / "templates"
TIER_SEED = TPL / "期刊分区表_中科院一区.json"


def _argval(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def norm(s: str) -> str:
    s = (s or "").lower().replace("&amp;", "&")
    s = re.sub(r"[^\w\s&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_doi(x) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "",
                  str(x or "").strip().lower())


# ══════════════════════════════════════════════════════════════
# 本人署名识别（与 build_list.py 同源规则：环境变量驱动，脚本内零硬编码）
# ══════════════════════════════════════════════════════════════
def name_tokens() -> list[str]:
    toks = [t.strip().lower() for t in
            os.environ.get("PAPER_NAME_TOKENS", "").split("|") if t.strip()]
    if toks:
        return toks
    sn = os.environ.get("PAPER_SURNAME", "").strip().lower()
    gn = os.environ.get("PAPER_GIVEN", "").strip().lower()
    if sn and gn:
        return [f"{sn}, {gn}", f"{gn} {sn}"]
    if sn:
        return [f"{sn},"]
    return []


def author_display(a) -> str:
    if isinstance(a, dict):
        return a.get("name") or ", ".join(
            x for x in (a.get("family"), a.get("given")) if x)
    return str(a or "")


def my_role(authors, toks) -> tuple[bool, str]:
    """→ (是否本人, 角色)。角色 ∈ {第一作者, 通讯作者（末位）, 第N位作者, ''}"""
    if not authors or not toks:
        return False, ""
    names = [author_display(a).lower() for a in authors]
    hits = [i for i, n in enumerate(names) if any(t in n for t in toks)]
    if not hits:
        return False, ""
    i, n = hits[0], len(names)
    if i == 0:
        return True, "第一作者"
    if i == n - 1:
        return True, "通讯作者（末位作者）"
    return True, f"第{i + 1}位作者"


def is_first_or_corresponding(role: str) -> bool:
    return role in ("第一作者", "通讯作者（末位作者）")


# ══════════════════════════════════════════════════════════════
# 期刊分区 / IF
# ══════════════════════════════════════════════════════════════
def load_tiers() -> set[str]:
    """分区表：用户表完全覆盖内置种子表。"""
    user = CDPDIR / "journal_tiers.json"
    p = user if user.exists() else TIER_SEED
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 分区表解析失败 {p}：{e}", file=sys.stderr)
        return set()
    if isinstance(data, dict):
        if user.exists() and "中科院一区" not in data:
            print(f"[WARN] {user} 里没有「中科院一区」键，分区判定将全部落到 IF 兜底",
                  file=sys.stderr)
        vals = data.get("中科院一区") or []
    else:
        vals = data
    return {norm(v) for v in vals if str(v).strip()}


def api(url: str, tries: int = 4):
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
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            CACHEDIR.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                import time
                time.sleep(2.0 * (i + 1))
                continue
            if e.code == 404:
                return None
            break
        except Exception as e:
            last = e
            import time
            time.sleep(1.5 * (i + 1))
    print(f"[WARN] 请求失败：{url}\n   {last}", file=sys.stderr)
    return None


def fetch_source_stats(source_ids: list[str]) -> dict:
    """OpenAlex source → {display_name, issn_l, if_proxy}。"""
    out = {}
    ids = sorted({s for s in source_ids if s})
    for i in range(0, len(ids), 50):
        grp = ids[i:i + 50]
        f = "ids.openalex:" + "|".join(grp)
        url = (f"{OA}/sources?filter={urllib.parse.quote(f, safe=':|')}"
               f"&per-page=50&select=id,display_name,issn_l,summary_stats")
        d = api(url)
        if not d:
            continue
        for r in d.get("results") or []:
            st = r.get("summary_stats") or {}
            out[str(r.get("id") or "").rsplit("/", 1)[-1]] = {
                "display_name": r.get("display_name") or "",
                "issn_l": r.get("issn_l") or "",
                "if_proxy": st.get("2yr_mean_citedness"),
            }
    return out


def source_id_of(pl) -> str:
    src = ((pl or {}).get("source") or {})
    return str(src.get("id") or "").rsplit("/", 1)[-1]


# ══════════════════════════════════════════════════════════════
def load_json(p: pathlib.Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] {p.name} 解析失败：{e}", file=sys.stderr)
        return default


def gbt(rec: dict, max_authors: int = 3) -> str:
    """GB/T 7714-2015 期刊论文著录。"""
    au = [author_display(a) for a in (rec.get("authors") or [])]
    au = [a for a in au if a]
    if au:
        astr = ", ".join(au[:max_authors]) + (", et al." if len(au) > max_authors else "")
    else:
        astr = "佚名"
    sep = "" if astr.endswith(".") else "."
    title = (rec.get("title") or "").strip().rstrip(".")
    venue = rec.get("venue") or rec.get("journal") or ""
    year = rec.get("year") or ""
    s = f"{astr}{sep} {title}[J]. {venue}, {year}."
    if rec.get("doi"):
        s += f" DOI: {rec['doi']}. 原文链接：https://doi.org/{rec['doi']}"
    return re.sub(r"\s+", " ", s).strip()


def select_representative(recs: dict, tiers: set[str], if_thr: float,
                          src_stats: dict, src_of_doi: dict,
                          tokens: list[str] | None = None) -> list[dict]:
    """挑出代表性文献，并为每条附上可复核的入选理由。

    `tokens` 显式传入时优先（自检用），否则从环境变量推导。
    """
    toks = tokens if tokens is not None else name_tokens()
    out = []
    for doi, r in recs.items():
        journal = r.get("journal") or r.get("short_journal") or ""
        nj = norm(journal)
        core = ""
        try:
            import refs_backfill as rb     # 复用同一套核心期刊判定，避免两处定义漂移
            core = rb.core_family(journal, rb.load_core_journals())
        except Exception:
            core = ""
        _, role = my_role(r.get("authors"), toks)
        first_or_corr = is_first_or_corresponding(role)

        sid = src_of_doi.get(doi) or ""
        st = src_stats.get(sid) or {}
        ifp = st.get("if_proxy")
        ifp = float(ifp) if isinstance(ifp, (int, float)) else None

        tier_hit = bool(nj and nj in tiers)
        if_hit = bool(ifp is not None and ifp > if_thr)

        reasons = []
        if core:
            reasons.append(f"核心期刊（{core}）")
        if first_or_corr:
            reasons.append(f"本人为{role}")
        if tier_hit:
            reasons.append("中科院一区（分区表命中）")
        if if_hit:
            reasons.append(f"IF≈{ifp:.2f} > {if_thr}")
        if not reasons:
            continue

        # 未满足的判据要**如实写出「未判定」**，不能省略让人以为没查
        unknown = []
        if not tier_hit:
            unknown.append("分区未命中（不代表非一区）")
        if ifp is None:
            unknown.append("IF 未取到")

        out.append({
            "doi": doi,
            "title": r.get("title") or "",
            "journal": journal,
            "year": r.get("year"),
            "vol": r.get("vol"), "issue": r.get("issue"),
            "page": r.get("page"), "article_number": r.get("article_number"),
            "authors": r.get("authors") or [],
            "role": role or "（未识别到本人署名）",
            "core_family": core,
            "tier_hit": tier_hit,
            "if_proxy": ifp,
            "journal_openalex": st.get("display_name") or "",
            "reasons": reasons,
            "undetermined": unknown,
            "n_refs": 0,
        })
    out.sort(key=lambda r: (r.get("year") or 0), reverse=True)
    return out


def build_report(recs, reps, source_refs, pool, if_thr, max_refs, unresolved):
    L = []
    L.append(f"# {AUTHOR} · 代表性文献的参考文献列表\n")
    L.append("> 著录格式：**GB/T 7714—2015**")
    L.append("> 代表性判据：核心期刊（Nature/Science/Cell/PNAS 等） **或** "
             "本人为第一/通讯作者 **或** 中科院一区 / IF > "
             f"{if_thr}（满足任一即入选）\n")

    # ── 第一部分：文章 ────────────────────────────────────────
    L.append("## 第一部分 · 代表性文献\n")
    L.append(f"共 **{len(reps)}** 篇（作者论文总数 {len(recs)} 篇）。"
             f"按发表年份倒序。\n")
    L.append("| # | 年 | 期刊 | 本人角色 | 入选理由 |")
    L.append("|---|---|---|---|---|")
    for i, r in enumerate(reps, 1):
        j = (r["journal"] or "—").replace("|", "\\|")
        L.append(f"| {i} | {r.get('year') or '—'} | {j} | {r['role']} | "
                 f"{'；'.join(r['reasons'])} |")
    L.append("")
    L.append("### GB/T 7714 著录\n")
    for i, r in enumerate(reps, 1):
        L.append(f"[{i}] {gbt(r)}"
                 + (f" ｜本人署名：{r['role']}" if "未识别" not in r["role"] else ""))
    L.append("")

    # ── 第二部分：参考文献 ────────────────────────────────────
    L.append("## 第二部分 · 各代表性文献的参考文献\n")
    L.append("> 每篇代表性文献下，列出**该文引用的参考文献**（GB/T 7714—2015）。")
    L.append("> 数据来源：OpenAlex `referenced_works`（元数据 API）。"
             "OpenAlex 的参考文献存缴**不完整**——条数少于原文实际参考文献数是常态，"
             "不是流程缺陷；每篇下方标注了本文实际取到的条数。\n")

    total = 0
    for i, r in enumerate(reps, 1):
        sr = source_refs.get(r["doi"]) or {}
        wids = sr.get("refs") or []
        rows = []
        for w in wids:
            rec = pool.get(w)
            if rec and rec.get("title"):
                rows.append(rec)
        rows.sort(key=lambda x: (x.get("year") or 0))
        total += len(rows)
        L.append(f"### [{i}] {r['title']}\n")
        L.append(f"- 原文：{gbt(r)}")
        L.append(f"- OpenAlex 记录的参考文献：**{len(rows)}** 条"
                 + ("（该文在 OpenAlex 无参考文献记录）" if not wids else ""))
        L.append("")
        if not rows:
            L.append("> [WARN] 无可列条目。可能是该文未存缴参考文献，或题录未取到。"
                     "**不臆造、不补齐。**\n")
            continue
        shown = rows[:max_refs] if max_refs else rows
        for k, rec in enumerate(shown, 1):
            L.append(f"{k}. {gbt(rec)}")
        if max_refs and len(rows) > max_refs:
            L.append(f"\n> [WARN] 仅列前 {max_refs} 条（共 {len(rows)} 条，**有意截断**）。")
        L.append("")

    # ── 第三部分：诚实性声明 ──────────────────────────────────
    L.append("## 附 · 判定来源与限制\n")
    L.append(f"- 代表性文献 **{len(reps)}** 篇，合计列出参考文献 **{total}** 条。")
    L.append("- **核心期刊**判定用显式清单（`refs_backfill.py` 的 `load_core_journals()`），"
             "不用前缀匹配——`science`/`cell` 前缀会误命中 Science of the Total "
             "Environment、Cellulose 等非核心刊。")
    L.append("- **IF** 用 OpenAlex `summary_stats.2yr_mean_citedness` 作为**代理值**："
             "口径与 JCR 影响因子接近但**数值不同**，不可直接与其他数据库的 IF 对齐引用。")
    L.append("- **中科院分区**用内置**种子表**（`流程1/templates/期刊分区表_中科院一区.json`），"
             "**不是官方分区表**（官方数据为商业授权，无法内置全量）。"
             "把官方表整理成同结构放到 `.work_cdp/journal_tiers.json` 即可覆盖。")
    L.append("- 判定结果里出现「未判定」时，含义是**数据没查到**，"
             "**不是**「不满足条件」——两者在报告中分开表述。")
    if unresolved:
        L.append(f"- 本期有 **{len(unresolved)}** 篇的期刊 IF 未能取到："
                 + "；".join(f"`{u}`" for u in unresolved[:8])
                 + ("…" if len(unresolved) > 8 else ""))
    L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    toks = ["zhang, san", "san zhang"]
    cases = [
        (["San Zhang", "A B", "C D"], "第一作者", True),
        (["A B", "C D", "San Zhang"], "通讯作者（末位作者）", True),
        (["A B", "San Zhang", "C D"], "第2位作者", False),
        (["A B", "C D"], "（未识别到本人署名）", False),
    ]
    bad = 0
    for authors, want_role, want_fc in cases:
        _, role = my_role(authors, toks)
        role = role or "（未识别到本人署名）"
        if role != want_role or is_first_or_corresponding(role) != want_fc:
            bad += 1
            print(f"  [X] my_role({authors}) = {role}，期望 {want_role}")
    print(f"署名位次判定：{len(cases) - bad}/{len(cases)} 通过")

    tiers = {"journal of the american chemical society", "nature"}
    stats = {"S1": {"display_name": "JACS", "if_proxy": 15.0},
             "S2": {"display_name": "Small", "if_proxy": 13.0},
             "S3": {"display_name": "J Foo", "if_proxy": 1.2}}
    recs = {
        "10.1/a": {"title": "A", "journal": "Journal of the American Chemical Society",
                   "year": 2022, "authors": ["A B"], "doi": "10.1/a"},
        "10.1/b": {"title": "B", "journal": "Small", "year": 2021,
                   "authors": ["A B"], "doi": "10.1/b"},
        "10.1/c": {"title": "C", "journal": "J Foo", "year": 2020,
                   "authors": ["San Zhang", "B"], "doi": "10.1/c"},
        "10.1/d": {"title": "D", "journal": "J Bar", "year": 2019,
                   "authors": ["A B"], "doi": "10.1/d"},
    }
    src_of = {"10.1/a": "S1", "10.1/b": "S2", "10.1/c": "S3", "10.1/d": "S9"}
    got = {r["doi"] for r in select_representative(recs, tiers, 5.0, stats, src_of, tokens=toks)}
    assert got == {"10.1/a", "10.1/b", "10.1/c"}, got
    print("代表性筛选：通过（A 一区+IF；B IF>5；C 第一作者；D 全部不满足→剔除）")

    g = gbt({"authors": ["A B", "C D", "E F", "G H"],
             "title": "T", "venue": "V", "year": 2020, "doi": "10.1/x"})
    assert "et al." in g and "et al.." not in g and "https://doi.org/10.1/x" in g, g
    print("GB/T 7714 著录：通过")

    # 未命中分区/IF 时必须写「未判定」而不是静默
    r = select_representative({"10.1/c": recs["10.1/c"]}, tiers, 5.0, stats, src_of, tokens=toks)[0]
    assert any("未命中" in u for u in r["undetermined"]), r["undetermined"]
    assert any("IF≈1.20" in x for x in r["reasons"]) is False     # 1.2 < 5.0
    print("诚实性标注：通过")
    print("\n[OK] 自检全部通过" if bad == 0 else "\n[X] 自检有失败项")
    return 0 if bad == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("refs")
    if_thr = float(_argval("if-threshold", "5.0") or 5.0)
    max_refs = int(_argval("max-refs-per-paper", "0") or 0)

    if not (CDPDIR / "crossref_all.json").exists() and not (CDPDIR / "master.json").exists():
        print("[X] 找不到题录总表 .work_cdp/crossref_all.json")
        return 2

    source_refs = load_json(REFDIR / "source_refs.json", {}) or {}
    pool = load_json(REFDIR / "refs_pool.json", {}) or {}
    if not source_refs:
        print("[X] 找不到 .work_cdp/refs/source_refs.json")
        print("  请先跑阶段 D2：uv run python 流程1/scripts/refs_backfill.py")
        return 2

    # 题录总表（与 refs_backfill 同源的读取方式）
    data = load_json(CDPDIR / "crossref_all.json", {}) or {}
    if "records" in data and isinstance(data["records"], list):
        data = {norm_doi(r.get("doi")): r for r in data["records"] if isinstance(r, dict)}
    recs = {norm_doi(k): v for k, v in data.items() if isinstance(v, dict)}
    try:
        from scope import excluded_dois  # type: ignore
        ex = excluded_dois(CDPDIR, len(recs)) or set()
        if ex:
            recs = {k: v for k, v in recs.items() if k not in ex}
            print(f"[INFO] scope.py：已排除 {len(ex)} 篇同名他人论文")
    except Exception:
        pass

    # OpenAlex source（拿期刊 id → IF 代理值）
    # 必须覆盖**全部**作者论文，不能只用 source_refs 里那几篇：
    #   D2 可能带了 --limit-sources 调试参数，或部分论文在 OpenAlex 无参考文献记录，
    #   只覆盖一部分会让大量论文的 IF 落到「未判定」，而那是**假**的未判定。
    src_of_doi = {norm_doi(d): sr.get("source_id")
                  for d, sr in source_refs.items() if sr.get("source_id")}
    missing = [d for d in recs if d not in src_of_doi]
    if missing:
        print(f"  补齐 {len(missing)} 篇的期刊来源（复用 D2 缓存，缺则新请求）")
        src_of_doi.update(rebuild_src_of_doi({d: recs[d] for d in missing}))
    stats = fetch_source_stats(list(src_of_doi.values()))

    tiers = load_tiers()
    print(f"分区表：{len(tiers)} 条"
          + ("（用户表）" if (CDPDIR / "journal_tiers.json").exists() else "（内置种子表）"))

    reps = select_representative(recs, tiers, if_thr, stats, src_of_doi)
    unresolved = [r["doi"] for r in reps if r["if_proxy"] is None]
    for r in reps:
        r["n_refs"] = len((source_refs.get(r["doi"]) or {}).get("refs") or [])

    print(f"代表性文献：{len(reps)} 篇（作者论文 {len(recs)} 篇）")
    for reason_c in ("核心期刊（", "本人为", "中科院一区", "IF≈"):
        n = len([r for r in reps if any(x.startswith(reason_c) for x in r["reasons"])])
        print(f"  按 {reason_c}… 入选：{n} 篇")

    md = build_report(recs, reps, source_refs, pool, if_thr, max_refs, unresolved)
    p = WD / f"{AUTHOR}_代表性文献参考文献.md"
    p.write_text(md, encoding="utf-8")

    (REFDIR / "representative.json").write_text(
        json.dumps({"if_threshold": if_thr, "items": reps,
                    "n_refs_total": sum(r["n_refs"] for r in reps)},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[OK] 交付物：{p}")
    journal("G2", "refs.bibliography", n_reps=len(reps), if_threshold=if_thr,
            n_unresolved_if=len(unresolved))
    log.info(f"[OK] 阶段 G2 完成 → {p}")
    if unresolved:
        print(f"[WARN] {len(unresolved)} 篇的期刊 IF 未取到 —— 报告已如实标注「未判定」，"
              f"未按「不满足条件」处理。")
    return 0


def rebuild_src_of_doi(recs: dict) -> dict:
    """从 D2 的缓存里把 DOI → OpenAlex source id 重新提出来（不额外发请求）。"""
    out = {}
    dois = [d for d in recs if d]
    for i in range(0, len(dois), 40):
        grp = dois[i:i + 40]
        f = "doi:" + "|".join(grp)
        url = (f"{OA}/works?filter={urllib.parse.quote(f, safe=':|')}"
               f"&per-page=50&select=id,doi,primary_location")
        d = api(url)
        if not d:
            continue
        for r in d.get("results") or []:
            sid = source_id_of(r.get("primary_location"))
            if sid:
                out[norm_doi(r.get("doi"))] = sid
    return out


if __name__ == "__main__":
    raise SystemExit(main())
