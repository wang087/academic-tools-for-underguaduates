# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 H2 ②：为 review（研究方向综述）组装自包含任务包
#
# 位置：阶段 H2 ①（refs_curate.py）之后。
# 干什么：把 `.work_cdp/refs/final_list.json` 的每条文献，连同**摘要**与
#         受控标签词表一起，固化成一个个自包含的 `tasks/<doi_slug>.json`，
#         交给并行 subagent 精读（提示词模板见
#         `流程1/subagent-driven-review/SKILL.md` §7 的综述网络版）。
#
# ★ 本脚本**不联网**。摘要来自阶段 D2 已取好的 OpenAlex 数据。
#   这是刻意的：subagent 必须无网可上，从机制上杜绝「顺手 curl 一篇」，
#   从而不可能违反流程 §5.1 的 CDP 强制抓取禁令。
#
# ★ 关于 evidence.source：本阶段的文献**绝大多数只有摘要**（它们是别人的论文，
#   本流程只下载了作者自己的全文）。任务包会如实标注 `abstract`，
#   提示词也要求 subagent 在报告里承认这一限制。**不得**假装读到了全文。
#
# 输入：<WORKDIR>/.work_cdp/refs/final_list.json
# 产出：<WORKDIR>/.work_cdp/refs/tasks/<doi_slug>.json
#       <WORKDIR>/.work_cdp/refs/manifest.json
#
# 用法：
#   uv run python 流程1/scripts/refs_prepare.py
#     --top=40            只准备相关性最高的前 N 条（0=全部；默认 0）
#     --only=DOI1,DOI2    只准备指定 DOI
#     --max-chars=12000   摘要超长时的截断上限（摘要一般远小于此）
#     --self-test         纯逻辑自检（不联网）
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
REFDIR = CDPDIR / "refs"
TASKDIR = REFDIR / "tasks"
RESDIR = REFDIR / "results"
TPL = _HERE.parent / "templates"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)


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
        print(f"⚠ {p.name} 解析失败：{e}", file=sys.stderr)
        return default


def doi_slug(doi: str, wid: str = "") -> str:
    """任务包文件名**只由内容决定**（DOI 优先，回退 OpenAlex ID）。

    ★ 与主流程 §9.4-I-3 同一条教训：绝不按「第几篇」命名 ——
      排序一变（补一条年份、改一次阈值）序号就会重排，
      历史解读卡会**错位到别的文献上**，比缺卡严重得多。
    """
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip().lower())
    s = re.sub(r"[^a-z0-9]+", "_", d).strip("_")
    if s:
        return s
    w = re.sub(r"[^A-Za-z0-9]+", "_", str(wid or "")).strip("_")
    return w.lower() or "unknown"


def authors_str(authors) -> str:
    au = [a for a in (authors or []) if a]
    if not au:
        return ""
    if len(au) <= 3:
        return ", ".join(au)
    return ", ".join(au[:3]) + ", et al."


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("refs")
    top = int(_argval("top", "0") or 0)
    max_chars = int(_argval("max-chars", "12000") or 12000)
    only = [d.strip().lower() for d in (_argval("only", "") or "").split(",") if d.strip()]

    data = load_json(REFDIR / "final_list.json")
    if not data or not data.get("items"):
        print("✗ 找不到 .work_cdp/refs/final_list.json（或为空）")
        print("  请先跑阶段 H2 ①：uv run python 流程1/scripts/refs_curate.py")
        return 2

    items = data["items"]
    if only:
        items = [r for r in items
                 if str(r.get("doi") or "").lower() in only
                 or doi_slug(r.get("doi"), r.get("id")) in only]
    if top:
        items = items[:top]
    if not items:
        print("✗ 选中的文献为空（检查 --only / --top）")
        return 2

    vocab = load_json(CDPDIR / "tag_vocabulary.json") \
        or load_json(TPL / "方法范式标签词表.json", {}) or {}
    profile = load_json(REFDIR / "final_list.json", {}).get("profile") or {}

    TASKDIR.mkdir(parents=True, exist_ok=True)
    RESDIR.mkdir(parents=True, exist_ok=True)

    manifest = []
    n_abs = 0
    n_full = 0
    for i, r in enumerate(items, 1):
        slug = doi_slug(r.get("doi"), r.get("id"))
        abstract = str(r.get("abstract") or "").strip()
        # 全文（仅 OA 综述有）：优先附在任务包里，subagent 直接读，不必联网
        fulltext = ""
        fp = r.get("fulltext_path") or ""
        if fp:
            try:
                fulltext = pathlib.Path(fp).read_text(encoding="utf-8")
            except Exception:
                fulltext = ""
        if len(fulltext) > max_chars * 4:
            fulltext = fulltext[:max_chars * 4]
        truncated = False
        if len(abstract) > max_chars:
            abstract = abstract[:max_chars]
            truncated = True
        if abstract:
            n_abs += 1
        if fulltext:
            n_full += 1
        task = {
            "schema": "refs-review/v1",
            "seq": i,
            "id": r.get("id") or "",
            "doi": r.get("doi") or "",
            "title": r.get("title") or "",
            "authors": r.get("authors") or [],
            "authors_str": authors_str(r.get("authors")),
            "venue": r.get("venue") or "",
            "year": r.get("year"),
            "type": r.get("type") or "",
            "is_review": bool(r.get("is_review")),
            "cited_by_count": r.get("cited_by_count") or 0,
            # 综述网络的边：作者有哪几篇论文引用了本文
            "n_sources": r.get("n_sources") or 0,
            "cited_by_sources": r.get("cited_by_sources") or [],
            "relevance": (r.get("_score") or {}).get("relevance"),
            "relevance_parts": {k: v for k, v in (r.get("_score") or {}).items()
                                if k.startswith("_")},
            "why_included": r.get("_reason") or "",
            "abstract": abstract,
            "fulltext": fulltext,
            "fulltext_chars": len(fulltext),
            "abstract_truncated": truncated,
            "evidence": {
                # ★ v7.2：综述可能拿到了 OA 全文（阶段 D2 的 enrich_reviews 抓的），
                #   有全文时证据层级直接升级 —— 下游 review 的置信度就靠这个字段。
                "source": ("fulltext" if fulltext
                           else ("detailed-abstract" if len(abstract) >= 800
                                 else ("abstract" if abstract else "metadata-only"))),
                "abstract_chars": len(abstract),
                "fulltext_chars": len(fulltext),
                "fulltext_path": r.get("fulltext_path") or "",
                "fulltext_note": r.get("fulltext_note") or "",
                "note": ("本文是作者论文的参考文献。**开放获取的综述**已通过 "
                         "Europe PMC 取到全文；其余只有摘要（或仅有题录）。"
                         "摘要为空时只能依据题录，必须如实标注，不得臆造内容。"),
            },
            "author_context": {
                "author": AUTHOR,
                "direction_tags": list((profile.get("direction_tags") or {}).keys()),
                "top_keywords": [w for w, _ in
                                 sorted((load_json(REFDIR / "final_list.json", {})
                                         .get("profile_keywords") or {}).items(),
                                        key=lambda x: -x[1])[:20]],
            },
            "tag_vocabulary": {
                "direction_tags_vocabulary": vocab.get("direction_tags_vocabulary") or [],
                "paradigm_tags_vocabulary": vocab.get("paradigm_tags_vocabulary") or [],
            },
            "output_schema": OUTPUT_SCHEMA,
        }
        p = TASKDIR / f"{slug}.json"
        p.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append({
            "seq": i, "slug": slug,
            "task": str(p), "result": str(RESDIR / f"{slug}.json"),
            "doi": task["doi"], "title": task["title"],
            "is_review": task["is_review"],
            "cited_by_count": task["cited_by_count"],
            "n_sources": task["n_sources"],
            "relevance": task["relevance"],
            "evidence_source": task["evidence"]["source"],
            "abstract_chars": len(abstract),
        })

    # 陈旧任务包：显式报告 + 清理（孤儿解读卡只告警不删 —— 那是 subagent 的劳动成果）
    current = {m["slug"] for m in manifest}
    stale = sorted(p.name for p in TASKDIR.glob("*.json")
                   if p.stem not in current)
    for n in stale:
        (TASKDIR / n).unlink()
    orphans = sorted(p.name for p in RESDIR.glob("*.json")
                     if p.stem not in current)

    (REFDIR / "manifest.json").write_text(json.dumps({
        "author": AUTHOR,
        "generated_from": str(REFDIR / "final_list.json"),
        "n_tasks": len(manifest),
        "n_with_abstract": n_abs,
        "tasks": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 已生成 {len(manifest)} 个综述任务包 → {TASKDIR}")
    print(f"   含摘要：{n_abs}/{len(manifest)}　｜　**含 OA 全文：{n_full}/{len(manifest)}**"
          + ("（全文级证据，置信度高于摘要级）" if n_full else ""))
    if stale:
        print(f"   已清理陈旧任务包 {len(stale)} 个：{', '.join(stale[:6])}"
              + ("…" if len(stale) > 6 else ""))
    if orphans:
        print(f"   ⚠ 遗留孤儿解读卡 {len(orphans)} 个（未删除，可能仍有价值）："
              + ", ".join(orphans[:6]) + ("…" if len(orphans) > 6 else ""))
    miss = [m for m in manifest if m["evidence_source"] == "metadata-only"]
    if miss:
        print(f"   ⚠ {len(miss)} 条**摘要为空**（OpenAlex 未存缴摘要）→ "
              f"subagent 只能依据题录，报告须如实标注。"
              f"例如：{miss[0]['title'][:50]}")

    print(f"\n清单：{REFDIR / 'manifest.json'}")
    print("下一步：**在同一条消息内**并行分派 4–6 个 subagent（每篇一个），")
    print("        提示词模板见 流程1/subagent-driven-review/SKILL.md §7（综述网络版）；")
    print("        跑完再执行 refs_build.py 汇总。")
    journal("H2", "refs.prepare", n_tasks=len(manifest), n_with_abstract=n_abs)
    log.info(f"✓ 阶段 H2 ②（综述任务包）完成 → {TASKDIR}")
    return 0


# 解读卡 schema：与 subagent-driven-review §6 的**11 个必需字段完全一致**，
# 另加 2 个综述网络专用字段。这样同一套质量控制流程可直接复用。
REQUIRED = ("doi", "one_line", "problem_solved", "methods",
            "verified_conclusions", "key_20", "quantified_gains",
            "open_questions", "direction_tags", "paradigm_tags", "evidence")

OUTPUT_SCHEMA = {
    "doi": "10.xxxx/xxxxx —— 必须与任务包一致，防止张冠李戴",
    "one_line": "一句话定位：本文在整个研究方向中处于什么位置",
    "problem_solved": "本文有效解决了什么问题（1–3 句）",
    "methods": ["明确的方法论 —— 具体到用了什么体系/手段/模型"],
    "verified_conclusions": ["有数据/实验支撑的结论；推测与展望不得写在这里"],
    "key_20": ["二八原则关键信息 —— 按逻辑顺序的有序文字，不是罗列"],
    "quantified_gains": [{"metric": "指标名", "value": "原样引用，含单位",
                          "context": "对比基线 / 实验条件"}],
    "open_questions": ["待验证的问题 / 局限"],
    "direction_tags": ["2–4 个，取自任务包 tag_vocabulary.direction_tags_vocabulary"],
    "paradigm_tags": ["1–3 个，取自任务包 tag_vocabulary.paradigm_tags_vocabulary"],
    "network_role": ("综述网络角色，**本阶段特有**：从 "
                     "奠基工作 / 方法源头 / 共识综述 / 争议焦点 / 最新进展 中选 1 个"),
    "relation_to_author": ("与该作者研究的关系，**本阶段特有**："
                           "该作者在哪些方面沿用/扩展/对照了本文"),
    "evidence": {"source": "abstract 或 metadata-only（**如实填**）",
                 "text_chars": "实际可读字符数"},
}

REQUIRED_FULL = REQUIRED + ("network_role", "relation_to_author")


def self_test() -> int:
    cases = [
        ("10.1038/s41586-024-12345", "", "10_1038_s41586_024_12345"),
        ("https://doi.org/10.1002/anie.202500266", "", "10_1002_anie_202500266"),
        ("", "W3035965352", "w3035965352"),
        ("", "", "unknown"),
    ]
    bad = 0
    for doi, wid, want in cases:
        got = doi_slug(doi, wid)
        if got != want:
            bad += 1
            print(f"  ✗ doi_slug({doi!r},{wid!r}) = {got!r}，期望 {want!r}")
    print(f"文件名生成：{len(cases) - bad}/{len(cases)} 通过")

    assert authors_str(["A", "B", "C"]) == "A, B, C"
    assert authors_str(["A", "B", "C", "D"]) == "A, B, C, et al."
    print("作者串：通过")

    assert set(REQUIRED_FULL) <= set(OUTPUT_SCHEMA), set(REQUIRED_FULL) - set(OUTPUT_SCHEMA)
    assert set(REQUIRED) <= set(REQUIRED_FULL)
    print(f"schema 自洽：通过（{len(REQUIRED)} 个必需字段 + 2 个综述网络字段）")

    # ★ 关键不变量：文件名只由内容决定 —— 同一篇在任何排序下都必须同名
    a = [{"doi": "10.1/x"}, {"doi": "10.1/y"}]
    b = list(reversed(a))
    assert {doi_slug(r["doi"]) for r in a} == {doi_slug(r["doi"]) for r in b}
    print("命名稳定性：通过（与顺序无关）")
    print("\n✅ 自检全部通过" if bad == 0 else "\n❌ 自检有失败项")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
