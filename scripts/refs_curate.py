# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H2 ①：文献去重与研究方向相关性整理 → 最终文献列表
#
# 位置：阶段 H2 的前半段。输入是阶段 D2 的「高质量文献列表」，
#       输出是**去重 + 按相关性排序并剔除无关论文**后的《研究方向文献终表》。
#       后半段（review）由 refs_prepare → 并行 subagent → refs_build 完成。
#
# 做三件事：
#   ① **去重**：DOI 归一后去重；无 DOI 的按「题名归一」二次去重。
#      合并时保留信息更全的那条，并记录「谁和谁合并了」。
#   ② **相关性整理**：拿作者**主体研究方向**（来自解读卡的 direction_tags +
#      作者论文题名/摘要的词频 + 常投期刊）去给每条文献打分。
#   ③ **剔除无关**：低于阈值的剔除，**但绝不静默丢弃** ——
#      被剔除的逐条写进 dropped.json 与报告，附剔除理由，供人工翻案。
#
# 输入（<WORKDIR>/.work_cdp/）：
#   refs/high_quality.json          阶段 D2 产出（必需）
#   refs/refs_pool.json             阶段 D2 产出（必需）
#   crossref_all.json               题录总表（必需；取作者语料）
#   abstracts_openalex.json         摘要语料（可选）
#   interpret/results/*.json        解读卡（可选；有则用其 direction_tags）
#   tag_vocabulary.json             受控标签词表（可选；缺省用内置模板）
#
# 产出：
#   .work_cdp/refs/final_list.json    最终文献列表（下游 refs_prepare 的唯一输入）
#   .work_cdp/refs/dropped.json       被剔除清单 + 理由
#   <WORKDIR>/<作者名>_研究方向文献终表.md
#
# 用法：
#   uv run python 流程1/scripts/refs_curate.py
#     --cut=25           相关性阈值（0–100，越低保留越多）
#     --keep-top=0       无论如何至少保留前 N 条（0=不设下限）
#     --self-test        纯逻辑自检（不联网）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys

from collections import Counter

from logutil import run_root as _run_root  # noqa: E402

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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
REFDIR = CDPDIR / "refs"
TPL = _HERE.parent / "templates"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)

# 与 interpret_build.py 同源的停用词（相关性打分不能靠 the/study 这类词）
STOP = set("""
a an the and or but if then than that this these those of in on at to for from by with without
as is are was were be been being it its their there here we our us they them he she his her
not no nor so such can could may might must shall should will would do does did done have has had
using used use uses study studies paper article results result method methods show shown showed
demonstrate demonstrated reveal revealed report reported present presented propose proposed
new novel high low increase increased decrease decreased effect effects analysis data
however moreover furthermore therefore thus also both each other more most less least
between among during after before under over into out up down about above below
one two three all any own per via see fig tab
""".split())

# 相关性打分的权重 —— 显式写出来，报告里如实公开，便于复核与调参
W_COCITE = 30.0     # 被作者几篇论文引用（综述网络里的边的强度）
W_VENUE = 15.0      # 是否投在与作者同一批期刊
W_KEYWORD = 55.0    # 题名/摘要与作者研究方向词表的加权重合


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
    except Exception as e:
        print(f"[WARN] {p.name} 解析失败：{e}", file=sys.stderr)
        return default


def norm_doi(x) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(x or "").strip().lower())


def norm_title(t: str) -> str:
    """题名归一：小写、去标点、压空白、去前后冠词。用于无 DOI 时的二次去重。"""
    s = (t or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 循环剥离，不能只做一次：题录里真实存在 "The A Study of…" 这种
    #   冠词叠写（出版社存缴时把刊名/系列名的冠词并了进来），
    #   只剥一次会留下 "a study of…"，与 "study of…" 判为不同而漏去重。
    while True:
        s2 = re.sub(r"^(a|an|the)\s+", "", s)
        if s2 == s:
            break
        s = s2
    return s


def tokenize(text: str) -> list[str]:
    """与 interpret_build.py 完全一致的分词（英文单词 ≥3 字符含数字 + 中文 2-gram）。"""
    out = []
    t = (text or "").lower()
    for w in re.findall(r"[a-z][a-z0-9\-]{2,}", t):
        w = w.strip("-")
        if len(w) >= 3 and w not in STOP:
            out.append(w)
    for run in re.findall(r"[一-鿿]{2,}", t):
        for i in range(len(run) - 1):
            out.append(run[i:i + 2])
    return out


# ══════════════════════════════════════════════════════════════
# 受控标签归一
# ══════════════════════════════════════════════════════════════
def load_vocab() -> dict:
    p = CDPDIR / "tag_vocabulary.json"
    if not p.exists():
        p = TPL / "方法范式标签词表.json"
    return load_json(p, {}) or {}


def normalize_tag(tag: str, allowed: list[str], aliases: dict) -> tuple[str, bool]:
    """→ (规范标签, 是否已归一)。未归一的一律保留原值，绝不静默丢弃。"""
    t = str(tag or "").strip()
    if not t:
        return "", True
    if t in allowed:
        return t, True
    low = t.lower()
    if low in aliases:
        return aliases[low], True
    for a in allowed:
        if a.lower() == low:
            return a, True
    # 去连字符/空格后再试一次（machine-learning vs machine learning）
    flat = re.sub(r"[\s\-_]+", "", low)
    for a in allowed:
        if re.sub(r"[\s\-_]+", "", a.lower()) == flat:
            return a, True
    for k, v in aliases.items():
        if re.sub(r"[\s\-_]+", "", k.lower()) == flat:
            return v, True
    return t, False          # 未归一：保留原值


# ══════════════════════════════════════════════════════════════
# 作者主体研究方向画像
# ══════════════════════════════════════════════════════════════
def build_profile(recs: dict, abstracts: dict, cards: list[dict], vocab: dict) -> dict:
    kw = Counter()
    venues = Counter()
    dirs = Counter()
    unnorm = []

    for doi, r in recs.items():
        kw.update(tokenize(str(r.get("title") or "")))
        kw.update(tokenize(str(abstracts.get(doi) or "")))
        j = r.get("journal") or r.get("short_journal") or ""
        if j:
            venues[str(j).strip()] += 1

    allowed_d = vocab.get("direction_tags_vocabulary") or []
    allowed_p = vocab.get("paradigm_tags_vocabulary") or []
    aliases = (vocab.get("aliases") or {})
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}
    al_p = {str(k).lower(): v for k, v in (aliases.get("_范式别名") or {}).items()}

    for c in cards:
        for t in (c.get("direction_tags") or []):
            n, ok = normalize_tag(t, allowed_d, al_d)
            if n:
                dirs[n] += 1
                if not ok:
                    unnorm.append(t)
        for t in (c.get("paradigm_tags") or []):
            n, ok = normalize_tag(t, allowed_p, al_p)
            if n and not ok:
                unnorm.append(t)

    # 词表只保留出现 ≥2 次的词，避免长尾噪声主导打分
    keywords = {w: n for w, n in kw.items() if n >= 2}
    return {
        "keywords": keywords,
        "venues": {v: n for v, n in venues.items()},
        "direction_tags": dict(dirs),
        "unnormalized_tags": sorted(set(unnorm)),
        "corpus_size": len(recs),
    }


# ══════════════════════════════════════════════════════════════
def dedupe(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """两级去重：DOI → 题名归一。返回 (去重后, 合并记录)。"""
    by_doi: dict[str, dict] = {}
    no_doi: list[dict] = []
    merges = []

    def richer(a: dict, b: dict) -> dict:
        """保留信息更全的一条（题名/摘要/期刊/被引）。"""
        better = dict(a)
        for f in ("title", "abstract", "venue", "year", "doi"):
            if not better.get(f) and b.get(f):
                better[f] = b[f]
        better["cited_by_count"] = max(a.get("cited_by_count") or 0,
                                       b.get("cited_by_count") or 0)
        better["n_sources"] = max(a.get("n_sources") or 0, b.get("n_sources") or 0)
        srcs = list(dict.fromkeys((a.get("cited_by_sources") or [])
                                  + (b.get("cited_by_sources") or [])))
        better["cited_by_sources"] = srcs
        # n_sources 取「原值」与「合并后边数」的较大者：
        #   只写 len(srcs) 会在上游没带 cited_by_sources 时把计数清零，
        #   而 n_sources 是「强制保留」规则的判据 —— 清零 = 悄悄丢核心文献。
        better["n_sources"] = max(a.get("n_sources") or 0,
                                  b.get("n_sources") or 0, len(srcs))
        return better

    for it in items:
        d = norm_doi(it.get("doi"))
        if not d:
            no_doi.append(it)
            continue
        if d in by_doi:
            merged = richer(by_doi[d], it)
            merges.append({"kind": "doi", "key": d,
                           "kept": merged.get("id"), "merged": it.get("id"),
                           "title": merged.get("title")})
            by_doi[d] = merged
        else:
            by_doi[d] = it

    # 无 DOI 的按题名归一去重，并与已有 DOI 条目比对
    by_title: dict[str, dict] = {}
    for it in no_doi:
        nt = norm_title(it.get("title"))
        if not nt:
            by_title[f"_untitled_{it.get('id')}"] = it
            continue
        if nt in by_title:
            merged = richer(by_title[nt], it)
            merges.append({"kind": "title", "key": nt,
                           "kept": merged.get("id"), "merged": it.get("id"),
                           "title": merged.get("title")})
            by_title[nt] = merged
        else:
            by_title[nt] = it

    # 跨集合去重：题名与某个有 DOI 的条目相同 → 合并进 DOI 条目
    doi_titles = {norm_title(v.get("title")): k for k, v in by_doi.items()
                  if v.get("title")}
    for nt in list(by_title):
        k = doi_titles.get(nt)
        if k:
            merged = richer(by_doi[k], by_title[nt])
            merges.append({"kind": "cross", "key": nt,
                           "kept": merged.get("id"),
                           "merged": by_title[nt].get("id"),
                           "title": merged.get("title")})
            by_doi[k] = merged
            del by_title[nt]

    return list(by_doi.values()) + list(by_title.values()), merges


def score(item: dict, profile: dict) -> dict:
    """0–100 相关性打分。三个分量都写进结果，便于复核。"""
    n_src = item.get("n_sources") or 0
    cocite = min(1.0, n_src / 4.0)                     # 被 4 篇以上引用即满分
    venue = 1.0 if (item.get("venue") or "") in profile["venues"] else 0.0

    toks = set(tokenize(str(item.get("title") or "")) +
               tokenize(str(item.get("abstract") or "")))
    prof_kw = profile["keywords"]
    if toks and prof_kw:
        hit = sum(prof_kw.get(t, 0) for t in toks)
        denom = max(1.0, sum(sorted(prof_kw.values(), reverse=True)[:80]))
        kw = min(1.0, hit / (denom * 0.25))
    else:
        kw = 0.0

    total = W_COCITE * cocite + W_VENUE * venue + W_KEYWORD * kw
    return {
        "relevance": round(total, 1),
        "_cocite": round(cocite, 3),
        "_venue": round(venue, 3),
        "_keyword": round(kw, 3),
        "_n_sources": n_src,
    }


def decide(item: dict, sc: dict, cut: float) -> tuple[bool, str]:
    """→ (是否保留, 理由)。规则显式、可复核、可翻案。"""
    n = sc["_n_sources"]
    if n >= 3:
        return True, f"被作者 {n} 篇论文引用（核心文献，强制保留）"
    if sc["relevance"] >= cut:
        return True, f"相关性 {sc['relevance']} ≥ 阈值 {cut}"
    if n >= 2 and sc["_keyword"] > 0:
        return True, f"被引用 {n} 次且与研究方向有词面重合"
    return False, (f"相关性 {sc['relevance']} < 阈值 {cut}，且仅被引用 {n} 次"
                   f"（网络边缘），与研究方向词表重合度 {sc['_keyword']}")


# ══════════════════════════════════════════════════════════════
def gbt(rec: dict) -> str:
    au = [a for a in (rec.get("authors") or []) if a]
    astr = ", ".join(au[:3]) + (", et al." if len(au) > 3 else "") if au else "佚名"
    sep = "" if astr.endswith(".") else "."
    title = (rec.get("title") or "").strip().rstrip(".")
    s = f"{astr}{sep} {title}[J]. {rec.get('venue') or ''}, {rec.get('year') or ''}."
    if rec.get("doi"):
        s += f" DOI: {rec['doi']}. 原文链接：https://doi.org/{rec['doi']}"
    return re.sub(r"\s+", " ", s).strip()


def build_report(profile, kept, dropped, merges, cut, stats) -> str:
    L = []
    L.append(f"# {AUTHOR} · 研究方向文献终表\n")
    L.append("> 来源：阶段 D2 反向查得的参考文献 → 去重 → 按研究方向相关性整理 → "
             "剔除与主体研究方向无关的论文。")
    L.append("> 本表是后续 **review（研究方向综述）** 的唯一输入。\n")

    L.append("## 一、整理结果\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    for k, v in stats.items():
        L.append(f"| {k} | {v} |")
    L.append(f"| 相关性阈值 | {cut} |")
    L.append(f"| 去重合并 | {len(merges)} 组 |")
    L.append("")

    L.append("## 二、作者主体研究方向画像\n")
    if profile["direction_tags"]:
        L.append("**方向标签**（来自解读卡，已按受控词表归一）：\n")
        for t, n in sorted(profile["direction_tags"].items(), key=lambda x: -x[1]):
            L.append(f"- {t} —— {n} 篇")
    else:
        L.append("> [WARN] 未找到解读卡（`.work_cdp/interpret/results/`），"
                 "方向画像退化为**题名/摘要词频 + 常投期刊**。"
                 "先跑阶段 H 会让本步明显更准。")
    L.append("")
    top_kw = sorted(profile["keywords"].items(), key=lambda x: -x[1])[:25]
    L.append("**高频研究词**（作者题名 + 摘要，已去停用词）："
             + "、".join(f"{w}({n})" for w, n in top_kw))
    L.append("")
    top_v = sorted(profile["venues"].items(), key=lambda x: -x[1])[:10]
    L.append("**常投期刊**：" + "、".join(f"{v}({n})" for v, n in top_v))
    L.append("")
    if profile["unnormalized_tags"]:
        L.append(f"**未归一的标签**（词表缺词 or 标注漂移，**已保留原值**）："
                 + "、".join(profile["unnormalized_tags"]))
        L.append("")

    L.append("## 三、最终文献列表\n")
    L.append(f"共 **{len(kept)}** 条，按相关性降序。\n")
    L.append("| # | 相关性 | 类型 | 题名 | 期刊 | 年 | 被引 | 被作者引用 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(kept, 1):
        t = (r.get("title") or "（无题名）").replace("|", "\\|")
        if len(t) > 85:
            t = t[:82] + "…"
        L.append(f"| {i} | {r['_score']['relevance']} | "
                 f"{'综述' if r.get('is_review') else '研究论文'} | {t} | "
                 f"{(r.get('venue') or '—')} | {r.get('year') or '—'} | "
                 f"{r.get('cited_by_count') or 0} | {r.get('n_sources') or 0} |")
    L.append("")

    L.append("### GB/T 7714 著录\n")
    for i, r in enumerate(kept, 1):
        L.append(f"[{i}] {gbt(r)}")
    L.append("")

    L.append("## 四、评分口径（可复核）\n")
    L.append("相关性 = "
             f"`{W_COCITE} × 共引强度` + `{W_VENUE} × 期刊命中` + `{W_KEYWORD} × 关键词重合`，满分 100。\n")
    L.append("| 分量 | 含义 |")
    L.append("|---|---|")
    L.append(f"| 共引强度 | 有多少篇作者论文引用了它；≥4 篇即满分（权重 {W_COCITE}） |")
    L.append(f"| 期刊命中 | 是否发表在作者常投期刊上（权重 {W_VENUE}） |")
    L.append(f"| 关键词重合 | 题名/摘要与作者方向词表的加权重合，按作者词表规模归一（权重 {W_KEYWORD}） |")
    L.append("")
    L.append("**保留规则**（满足任一即保留）：")
    L.append("1. 被作者 ≥3 篇论文引用 —— 网络核心，**强制保留**，不受阈值影响；")
    L.append("2. 相关性 ≥ 阈值；")
    L.append("3. 被引用 ≥2 次且与方向词表有词面重合。")
    L.append("")

    L.append("## 五、被剔除清单（**逐条列明，不静默丢弃**）\n")
    if not dropped:
        L.append("无。")
    else:
        L.append(f"共剔除 **{len(dropped)}** 条。这些条目**仍在** "
                 "`.work_cdp/refs/dropped.json` 中，可随时翻案重入。\n")
        L.append("| # | 题名 | 期刊 | 年 | 被引 | 剔除理由 |")
        L.append("|---|---|---|---|---|---|")
        for i, r in enumerate(dropped, 1):
            t = (r.get("title") or "（无题名）").replace("|", "\\|")
            if len(t) > 70:
                t = t[:67] + "…"
            L.append(f"| {i} | {t} | {(r.get('venue') or '—')} | "
                     f"{r.get('year') or '—'} | {r.get('cited_by_count') or 0} | "
                     f"{r['_reason']} |")
    L.append("")

    if merges:
        L.append("## 六、去重合并记录\n")
        L.append(f"共 {len(merges)} 组。`kind` 说明按什么判为重复："
                 "`doi` = DOI 相同；`title` = 题名归一后相同；`cross` = 有 DOI 与无 DOI 两条题名相同。\n")
        L.append("| # | 判据 | 保留 | 合并掉 | 题名 |")
        L.append("|---|---|---|---|---|")
        for i, m in enumerate(merges, 1):
            t = (m.get("title") or "").replace("|", "\\|")[:70]
            L.append(f"| {i} | {m['kind']} | {m.get('kept')} | {m.get('merged')} | {t} |")
        L.append("")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    # 去重：DOI 相同 / 题名归一后相同 / 跨集合
    items = [
        {"id": "W1", "doi": "10.1/a", "title": "A Study of X", "n_sources": 1,
         "cited_by_count": 5, "venue": "J", "year": 2020},
        {"id": "W2", "doi": "https://doi.org/10.1/A", "title": "A study of X",
         "n_sources": 2, "cited_by_count": 9, "venue": "J", "year": 2020},
        {"id": "W3", "doi": "", "title": "The A Study of X!", "n_sources": 1,
         "cited_by_count": 1, "venue": "J", "year": 2020},
        {"id": "W4", "doi": "", "title": "B Study", "n_sources": 1,
         "cited_by_count": 2, "venue": "K", "year": 2021},
    ]
    kept, merges = dedupe(items)
    ids = sorted(x["id"] for x in kept)
    assert ids == ["W1", "W4"], ids
    assert len(merges) == 2, merges
    merged_w1 = [x for x in kept if x["id"] == "W1"][0]
    assert merged_w1["cited_by_count"] == 9, merged_w1
    assert merged_w1["n_sources"] == 2, merged_w1
    print(f"去重：通过（4 → {len(kept)}，合并 {len(merges)} 组，被引取最大值）")

    # 打分与保留规则
    profile = {"keywords": {"carbon": 9, "porous": 7, "framework": 6, "co2": 5},
               "venues": {"ChemCatChem": 3}, "direction_tags": {},
               "unnormalized_tags": [], "corpus_size": 10}
    core = {"id": "A", "title": "Carbon porous framework for CO2", "n_sources": 3,
            "cited_by_count": 100, "venue": "X", "year": 2020}
    edge = {"id": "B", "title": "Wireless microgrid scheduling", "n_sources": 1,
            "cited_by_count": 300, "venue": "Y", "year": 2020}
    sa, sb = score(core, profile), score(edge, profile)
    assert sa["relevance"] > sb["relevance"], (sa, sb)
    assert decide(core, sa, 25)[0] is True
    assert decide(edge, sb, 25)[0] is False, decide(edge, sb, 25)
    assert "强制保留" in decide(core, sa, 25)[1]
    print(f"打分：通过（相关文献 {sa['relevance']} > 无关文献 {sb['relevance']}；"
          "≥3 次共引强制保留）")

    # 标签归一
    allowed = ["machine-learning", "experimental"]
    aliases = {"ml": "machine-learning", "机器学习": "machine-learning"}
    assert normalize_tag("ML", allowed, aliases) == ("machine-learning", True)
    assert normalize_tag("机器学习", allowed, aliases) == ("machine-learning", True)
    assert normalize_tag("Machine Learning", allowed, aliases) == ("machine-learning", True)
    got, ok = normalize_tag("自定义:冷冻电镜", allowed, aliases)
    assert got == "自定义:冷冻电镜" and ok is False
    print("标签归一：通过（大小写/别名/中文均可归；自造标签保留原值并标记未归一）")

    g = gbt({"authors": ["A B", "C D", "E F", "G H"], "title": "T",
             "venue": "V", "year": 2020, "doi": "10.1/x"})
    assert "et al." in g and "et al.." not in g
    print("GB/T 7714 著录：通过")
    print("\n[OK] 自检全部通过")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("refs")
    cut = float(_argval("cut", "25") or 25)
    keep_top = int(_argval("keep-top", "0") or 0)

    hq = load_json(REFDIR / "high_quality.json")
    if not hq or not hq.get("items"):
        print("[X] 找不到 .work_cdp/refs/high_quality.json（或为空）")
        print("  请先跑阶段 D2：uv run python 流程1/scripts/refs_backfill.py")
        return 2
    items = hq["items"]

    recs = load_json(CDPDIR / "crossref_all.json", {}) or {}
    if "records" in recs and isinstance(recs["records"], list):
        recs = {norm_doi(r.get("doi")): r for r in recs["records"] if isinstance(r, dict)}
    abstracts = load_json(CDPDIR / "abstracts_openalex.json", {}) or {}
    cards = []
    rdir = CDPDIR / "interpret" / "results"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if isinstance(c, dict):
                cards.append(c)

    vocab = load_vocab()
    print(f"语料：作者论文 {len(recs)} 篇，摘要 {len(abstracts)} 条，解读卡 {len(cards)} 张")
    profile = build_profile(recs, abstracts, cards, vocab)
    if profile["unnormalized_tags"]:
        print(f"[WARN] {len(profile['unnormalized_tags'])} 个标签未能归一（已保留原值）："
              + "、".join(profile["unnormalized_tags"][:6]))

    deduped, merges = dedupe(items)
    print(f"去重：{len(items)} → {len(deduped)}（合并 {len(merges)} 组）")

    kept, dropped = [], []
    for it in deduped:
        sc = score(it, profile)
        ok, why = decide(it, sc, cut)
        rec = dict(it)
        rec["_score"] = sc
        rec["_reason"] = why
        (kept if ok else dropped).append(rec)

    kept.sort(key=lambda r: (-r["_score"]["relevance"],
                             -(r.get("cited_by_count") or 0)))
    if keep_top and len(kept) < keep_top:
        # 阈值过高导致保留过少时，按相关性补齐到下限（并如实说明补齐了多少）
        pool = sorted(dropped, key=lambda r: -r["_score"]["relevance"])
        need = keep_top - len(kept)
        promoted = pool[:need]
        for r in promoted:
            r["_reason"] = "（阈值下补齐至 --keep-top 下限：" + r["_reason"] + "）"
            r["_promoted"] = True
        kept.extend(promoted)
        dropped = pool[need:]
        print(f"[INFO] --keep-top={keep_top}：从剔除池补回 {len(promoted)} 条（已标注）")

    stats = {
        "阶段 D2 高质量文献": len(items),
        "去重后": len(deduped),
        "最终保留": len(kept),
        "被剔除": len(dropped),
        "其中综述": len([r for r in kept if r.get("is_review")]),
    }

    REFDIR.mkdir(parents=True, exist_ok=True)
    (REFDIR / "final_list.json").write_text(json.dumps(
        {"cut": cut, "profile": {k: v for k, v in profile.items() if k != "keywords"},
         "profile_keywords": profile["keywords"],
         "stats": stats, "items": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    (REFDIR / "dropped.json").write_text(json.dumps(
        {"stats": stats, "merges": merges, "items": dropped},
        ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(profile, kept, dropped, merges, cut, stats)
    p = WD / f"{AUTHOR}_研究方向文献终表.md"
    p.write_text(md, encoding="utf-8")

    print("\n统计：")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\n[OK] 交付物：{p}")
    journal("H2", "refs.curate", cut=cut, **{k: v for k, v in stats.items()})
    log.info(f"[OK] 阶段 H2 ①（去重与相关性整理）完成 → {REFDIR / 'final_list.json'}")
    if not cards:
        print("[WARN] 未找到解读卡 → 方向画像退化为题名/摘要词频。"
              "先跑阶段 H 可显著提升本步准确性。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
