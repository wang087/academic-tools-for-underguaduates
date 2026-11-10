# -*- coding: utf-8 -*-
"""关键词三层（core / method / reagent）校验与 VOSviewer 导出。

定位：解读卡（`.work_cdp/interpret/results/*.json`）的**下游**。
  · 校验：`--check`   读卡片，检查 keywords_core/method/reagent/keyword_evidence
  · 导出：`--export`  卡片 → VOSviewer map.txt + network.txt + thesaurus.txt + vocab.csv
  · 全文：`--fulltext` 用 MinerU 全文直接建图（卡片还没跑完时的替代路线）

设计约束（与 SKILL.md §6.7 一致）：
  · 词云只吃 keywords_core；method/reagent 不进词云；
  · evidence 校验对象优先 fulltext_path，其次任务包 text；归一化 NFKC + 去空白；
  · 缺关键词只 WARN，不阻断（老卡片没有这些字段）。

用法：
  python keywords_three_layer.py --check
  python keywords_three_layer.py --export --out .work_cdp/graph/vos
  python keywords_three_layer.py --fulltext --corpus <md 目录> --out ...
  python keywords_three_layer.py --selftest
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import pathlib
import re
import sys
import unicodedata
from collections import Counter

CDP = pathlib.Path(".work_cdp")
RESULTS = CDP / "interpret" / "results"
TASKS = CDP / "interpret" / "tasks"

CORE = "keywords_core"
METHOD = "keywords_method"
REAGENT = "keywords_reagent"
EVID = "keyword_evidence"

#: 常用方法（core 里不该出现；method 里按 M1 0.3 计权）
COMMON_METHODS = {
    "chip-seq", "chip", "atac-seq", "rna-seq", "western blot", "western blotting",
    "co-ip", "co-immunoprecipitation", "immunoprecipitation", "shrna", "sirna",
    "crispr", "crispr/cas9", "qpcr", "rt-qpcr", "flow cytometry", "mass spectrometry",
    "immunofluorescence", "immunohistochemistry", "elisa", "northern blot",
}
MAXN = {CORE: 8, METHOD: 8, REAGENT: 4}


def _n(s: str) -> str:
    """NFKC + 去空白：VOSviewer/PDF 里的连字、全角、断行都靠这一步归一。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")).lower())


def load_cards(results_dir: pathlib.Path = RESULTS) -> list[dict]:
    out = []
    if not results_dir.exists():
        return out
    for p in sorted(results_dir.glob("*.json")):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        j["_file"] = p.name
        out.append(j)
    return out


def card_keywords(card: dict) -> dict:
    def _lst(key):
        v = card.get(key)
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    return {CORE: _lst(CORE), METHOD: _lst(METHOD), REAGENT: _lst(REAGENT)}


def _evidence_map(card: dict) -> dict:
    out = {}
    v = card.get(EVID)
    if isinstance(v, list):
        for it in v:
            if isinstance(it, dict) and it.get("term"):
                out[_n(it["term"])] = it
    return out


def card_issues(card: dict, fulltext: str = "") -> list[str]:
    """返回问题清单（空 = 合格）。缺字段算 WARN 级，由调用方决定怎么用。"""
    errs: list[str] = []
    kw = card_keywords(card)
    for key in (CORE, METHOD, REAGENT):
        if key not in card:
            errs.append(f"{key}: 缺字段")
            continue
        if not isinstance(card.get(key), list):
            errs.append(f"{key}: 应为字符串数组")
            continue
        if len(kw[key]) > MAXN[key]:
            errs.append(f"{key}: {len(kw[key])} 个 > 上限 {MAXN[key]}")
    if not kw[CORE]:
        errs.append(f"{CORE}: 为空")
    elif len(kw[CORE]) < 3:
        errs.append(f"{CORE}: 仅 {len(kw[CORE])} 个（建议 5–8）")

    bad = [t for t in kw[CORE] if _n(t) in {_n(x) for x in COMMON_METHODS}]
    if bad:
        errs.append(f"{CORE}: 混入常用方法 {bad[:3]}")

    dup = set(map(_n, kw[CORE])) & set(map(_n, kw[METHOD]))
    if dup:
        errs.append(f"core/method 重复 {sorted(dup)[:3]}（C2 新方法除外）")

    ev = _evidence_map(card)
    if not ev:
        errs.append(f"{EVID}: 缺字段或为空")
    else:
        for key in (CORE, METHOD, REAGENT):
            for t in kw[key]:
                if _n(t) not in ev:
                    errs.append(f"{EVID}: {t} 无对应 evidence")
        if fulltext:
            nt = _n(fulltext)
            miss = [k for k, v in ev.items()
                    if v.get("evidence") and _n(v["evidence"])[:60] not in nt]
            if miss:
                errs.append(f"{EVID}: {len(miss)} 条无法在全文定位（如 {miss[0][:30]}）")
    return errs


def fulltext_of(slug: str, cards_dir: pathlib.Path = RESULTS,
                tasks_dir: pathlib.Path = TASKS) -> str:
    """优先 fulltext_path，其次任务包 text。"""
    tp = tasks_dir / slug
    if tp.exists():
        try:
            j = json.loads(tp.read_text(encoding="utf-8"))
        except Exception:
            j = {}
        fp = str(j.get("fulltext_path") or "")
        if fp and pathlib.Path(fp).exists():
            try:
                return pathlib.Path(fp).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        return str(j.get("text") or "")
    return ""


def check(cards_dir: pathlib.Path = RESULTS, tasks_dir: pathlib.Path = TASKS) -> dict:
    cards = load_cards(cards_dir)
    rows, warn, bad = [], [], []
    for c in cards:
        slug = c.get("_file", "")
        errs = card_issues(c, fulltext_of(slug, cards_dir, tasks_dir))
        kw = card_keywords(c)
        hard = [e for e in errs if "缺字段" in e or "为空" in e]
        rows.append({"file": slug, "doi": c.get("doi", ""),
                     "n_core": len(kw[CORE]), "n_method": len(kw[METHOD]),
                     "n_reagent": len(kw[REAGENT]), "issues": errs})
        (bad if hard else warn).append(rows[-1]) if errs else None
    return {"n_cards": len(cards), "ok": len(cards) - len([r for r in rows if r["issues"]]),
            "warn": len(warn), "bad": len(bad),
            "rows": rows}


# ── VOSviewer 导出 ────────────────────────────────────────────
def export(cards_dir: pathlib.Path = RESULTS, out: pathlib.Path = CDP / "graph" / "vos",
           min_doc: int = 1, level: str = CORE, min_link: int = 1,
           max_links: int = 12000) -> dict:
    """把卡片的某一层关键词导出成 VOSviewer map + network。"""
    cards = load_cards(cards_dir)
    docs = [card_keywords(c)[level] for c in cards]
    docs = [[t for t in d if t] for d in docs]
    docs = [d for d in docs if d]
    n_docs = len(docs)
    df, pairs = Counter(), Counter()
    for d in docs:
        uniq = list(dict.fromkeys(d))
        for t in uniq:
            df[t] += 1
        for a, b in itertools.combinations(sorted(map(_n, uniq)), 2):
            pairs[(a, b)] += 1

    disp = {}
    for d in docs:
        for t in d:
            disp.setdefault(_n(t), t)
    kept = [k for k in df if df[k] >= min_doc]
    kept.sort(key=lambda k: (-df[k], k))
    tid = {k: i + 1 for i, k in enumerate(kept)}

    out.mkdir(parents=True, exist_ok=True)
    n = max(1, len(kept))
    with (out / "map.txt").open("w", encoding="utf-8", newline="") as f:
        f.write("id\tlabel\tx\ty\tweight\n")
        for i, k in enumerate(kept):
            ang = 2 * math.pi * i / n
            f.write(f"{tid[k]}\t{disp.get(k, k)}\t{math.cos(ang):.4f}\t"
                    f"{math.sin(ang):.4f}\t{df[k]}\n")
    nlink = 0
    with (out / "network.txt").open("w", encoding="utf-8", newline="") as f:
        for (a, b), s in pairs.most_common(max_links):
            if s < min_link or a not in tid or b not in tid:
                continue
            f.write(f"{tid[a]}\t{tid[b]}\t{s}\n")
            nlink += 1
    with (out / "vocab.csv").open("w", encoding="utf-8-sig", newline="") as f:
        f.write("id\tterm\tn_docs\n")
        for k in kept:
            f.write(f"{tid[k]}\t{disp.get(k, k)}\t{df[k]}\n")
    with (out / "thesaurus.txt").open("w", encoding="utf-8", newline="") as f:
        f.write("label\treplace by\n")
    with (out / "corpus.txt").open("w", encoding="utf-8", newline="") as f:
        for d in docs:
            f.write(" ".join(d) + "\n")
    return {"level": level, "n_docs": n_docs, "n_nodes": len(kept), "n_links": nlink,
            "out": str(out)}


# ── 全文建图（无卡片时的替代）────────────────────────────────
WORD = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")
VERB_TAIL = {"presented", "performed", "carried", "shown", "used", "obtained",
             "described", "reported", "indicated", "observed", "found", "seen",
             "given", "taken", "made", "done", "added", "declared", "compared",
             "recorded", "measured", "detected", "analyzed", "calculated", "tested"}
FUNC2 = {"author", "authors", "consistent", "previous", "before", "after", "whether",
         "determine", "following", "according", "respectively", "significant",
         "different", "various", "several", "these", "those", "such", "both",
         "each", "more", "most", "less", "least", "similar", "same", "other",
         "another", "first", "second", "third", "last", "next", "present",
         "current", "recent", "many", "much", "well", "also", "however",
         "therefore", "thus", "whereas", "while", "during", "within", "without"}
BOILER = re.compile(r"(room temperature|error bar|post hoc|left right|wrote manuscript"
                    r"|contributed equally|solution contain|vitro vivo|cacl|mgcl|hepes"
                    r"|significant difference|statistical|anova|scale bar"
                    r"|competing interest|data availab|natl acad|proc natl|ustc edu"
                    r"|informed consent|animal care|purchased|obtained from|cat no"
                    r"|p value|two tailed|one tailed|supplementary|see methods"
                    r"|mean sem|nacl kcl|kcl nacl|declare competing|authors declare"
                    r"|cells expressing|cells incubated|cells transfected"
                    r"|consistent previous|before after|determine whether"
                    r"|online version|findings suggest|important role|suggest that"
                    r"|expression level|cell type|levels of|role of"
                    r"|rights reserved|creative commons|acad sci|mechanisms underlying)", re.I)
STOP = set("""the and for with from that this these those are was were has have had not but
which their they them its it is be been being on in of to as at by or an a we our us can may
also than then there here such more most other into within during between among using used use
shows show shown results result study studies data analysis methods method fig figure table
significantly however therefore thus both each all any some no compared comparison""".split())


NAMES: set = set()


def _clean_md(md: str) -> str:
    t = re.sub(r"<[^>]{1,200}>", " ", md)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    m = re.search(r"(?im)^#{1,3}\s*(references?|bibliography)\s*$", t)
    if m:
        t = t[:m.start()]
    keep = []
    for ln in t.splitlines():
        s = ln.strip()
        if not s or len(s) < 25:
            continue
        if re.search(r"(advertisement|view metrics|academy of sciences|university|"
                     r"institute|school of|department of|foundation|supported by|"
                     r"corresponding author|conflict of interest|e-?mail)", s, re.I):
            continue
        keep.append(s)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", " ".join(keep)))


def fulltext_map(corpus: pathlib.Path, out: pathlib.Path,
                 min_doc: int = 5, top: int = 300, min_link: int = 5,
                 max_ratio: float = 0.5) -> dict:
    docs = []
    for p in sorted(corpus.glob("*.md")):
        try:
            t = _clean_md(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if len(t) >= 2000:
            docs.append(t)
    n_docs = len(docs)
    if not n_docs:
        return {"error": f"没有可用 md：{corpus}"}
    df, per = Counter(), []
    for t in docs:
        toks = [w.strip("-").lower() for w in WORD.findall(t)]
        toks = [w for w in toks if len(w) >= 3 and w not in STOP]
        c = Counter()
        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                g = toks[i:i + n]
                term = " ".join(g)
                if len(term) < 4 or len(term) > 45:
                    continue
                if g[0] in FUNC2 or g[-1] in FUNC2:
                    continue
                if g[-1] in VERB_TAIL or g[0] in VERB_TAIL:
                    continue
                if BOILER.search(term):
                    continue
                if NAMES and any(w in NAMES for w in g):
                    continue
                c[term] += 1
        per.append(set(c))
        for k in c:
            df[k] += 1
    kept = [k for k in df if min_doc <= df[k] <= max_ratio * n_docs]
    kept.sort(key=lambda k: (-df[k], k))
    kept = kept[:top]
    tid = {k: i + 1 for i, k in enumerate(kept)}
    pairs = Counter()
    for s in per:
        for a, b in itertools.combinations(sorted(k for k in s if k in tid), 2):
            pairs[(a, b)] += 1
    out.mkdir(parents=True, exist_ok=True)
    n = max(1, len(kept))
    with (out / "map.txt").open("w", encoding="utf-8", newline="") as f:
        f.write("id\tlabel\tx\ty\tweight\n")
        for i, k in enumerate(kept):
            ang = 2 * math.pi * i / n
            f.write(f"{tid[k]}\t{k}\t{math.cos(ang):.4f}\t{math.sin(ang):.4f}\t{df[k]}\n")
    nlink = 0
    with (out / "network.txt").open("w", encoding="utf-8", newline="") as f:
        for (a, b), s in pairs.most_common():
            if s < min_link:
                continue
            f.write(f"{tid[a]}\t{tid[b]}\t{s}\n")
            nlink += 1
    with (out / "corpus.txt").open("w", encoding="utf-8", newline="") as f:
        for t in docs:
            f.write(t[:20000] + "\n")
    return {"n_docs": n_docs, "n_nodes": len(kept), "n_links": nlink, "out": str(out)}


def selftest() -> int:
    card = {
        "doi": "10.x/y",
        CORE: ["alpha synuclein", "lewy bodies", "dopaminergic neurons"],
        METHOD: ["western blot", "immunofluorescence"],
        REAGENT: ["anti-alpha-synuclein antibody"],
        EVID: [
            {"term": "alpha synuclein", "category": "core", "rule": "C1",
             "evidence": "alpha synuclein aggregates were detected", "confidence": 3},
            {"term": "lewy bodies", "category": "core", "rule": "C1",
             "evidence": "Lewy bodies in substantia nigra", "confidence": 3},
            {"term": "dopaminergic neurons", "category": "core", "rule": "C1",
             "evidence": "dopaminergic neurons were lost", "confidence": 3},
            {"term": "western blot", "category": "method", "rule": "M1",
             "evidence": "western blot analysis", "confidence": 3},
            {"term": "immunofluorescence", "category": "method", "rule": "M1",
             "evidence": "immunofluorescence staining", "confidence": 3},
            {"term": "anti-alpha-synuclein antibody", "category": "reagent", "rule": "R2",
             "evidence": "anti-alpha-synuclein antibody (Abcam)", "confidence": 3},
        ],
    }
    text = ("alpha synuclein aggregates were detected. Lewy bodies in substantia nigra. "
            "dopaminergic neurons were lost. western blot analysis. "
            "immunofluorescence staining. anti-alpha-synuclein antibody (Abcam).")
    assert not card_issues(card, text), card_issues(card, text)
    bad = dict(card)
    bad[CORE] = card[CORE] + ["western blot"]
    assert any("混入常用方法" in e for e in card_issues(bad, text))
    bad2 = dict(card)
    bad2[EVID] = card[EVID][:-1]
    assert any("无对应 evidence" in e for e in card_issues(bad2, text))
    assert _n("Long Noncoding RNAs") == "longnoncodingrnas"
    print("[keywords_three_layer] selftest OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="关键词三层校验 + VOSviewer 导出")
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--tasks", default=str(TASKS))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--fulltext", action="store_true")
    ap.add_argument("--out", default=str(CDP / "graph" / "vos"))
    ap.add_argument("--level", choices=[CORE, METHOD, REAGENT], default=CORE)
    ap.add_argument("--min-doc", type=int, default=1)
    ap.add_argument("--min-link", type=int, default=1)
    ap.add_argument("--corpus", default="")
    ap.add_argument("--names", default="", help="人名过滤表（一行一个，可选）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="缺关键词字段时返回非零（默认只警告）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.check:
        r = check(pathlib.Path(a.results), pathlib.Path(a.tasks))
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"卡片 {r['n_cards']}｜合格 {r['ok']}｜警告 {r['warn']}｜缺字段 {r['bad']}")
            for row in r["rows"]:
                if row["issues"]:
                    print(f"  - {row['file']}: {'; '.join(row['issues'][:3])}")
        return 0 if (r["bad"] == 0 or not a.strict) else 3
    if a.export:
        r = export(pathlib.Path(a.results), pathlib.Path(a.out), a.min_doc, a.level, a.min_link)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    if a.fulltext:
        global NAMES
        if a.names and pathlib.Path(a.names).exists():
            NAMES = {x.strip().lower() for x in
                     pathlib.Path(a.names).read_text(encoding="utf-8").splitlines()
                     if x.strip()}
            print(f"[names] {len(NAMES)} 个人名参与过滤", file=sys.stderr)
        if not a.corpus:
            print("--fulltext 需要 --corpus <md 目录>", file=sys.stderr)
            return 2
        r = fulltext_map(pathlib.Path(a.corpus), pathlib.Path(a.out),
                         a.min_doc, 300, a.min_link)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())