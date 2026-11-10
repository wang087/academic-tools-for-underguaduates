# -*- coding: utf-8 -*-
"""Markdown deliverables.

Three documents, matching the three gates in the workflow:

1. ``direction`` - matched direction, English keyword base, built queries.
2. ``reviews``   - authoritative reviews, abstract translation/interpretation,
                   induced sub-directions, and the confirmation gate.
3. ``list``      - final literature list for a refined topic.

The report layer never invents content: missing translations or interpretations
render as an explicit ``[STOP]`` placeholder so an unfinished digest cannot be
mistaken for a finished one.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import records as recmod
from .util import now_iso

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def _cell(text, width=120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().replace("|", "\\|")
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text


def _link(title: str, url: str) -> str:
    title = _cell(title)
    if not url:
        return title
    return f"[{title}]({url})"


def _authors(record: dict) -> tuple[str, str]:
    first, corr = recmod.first_and_corresponding(record)
    return _cell(first, 60), _cell(corr, 80)


def _rank_label(rank: dict) -> str:
    if not rank:
        return "rank unknown"
    if rank.get("percentile") is not None:
        return f"JIF pct {rank['percentile']:.0f}"
    if rank.get("cas_tier") == 1:
        return "CAS tier 1 (proxy)"
    if rank.get("jcr_quartile"):
        return f"JCR {rank['jcr_quartile']}"
    return "below threshold"


def _header(title: str, subtitle: str = "") -> list[str]:
    lines = [f"# {title}", ""]
    if subtitle:
        lines.extend([f"> {subtitle}", ""])
    lines.extend([f"- 生成时间：{now_iso()}", "- 生成工具：scopus-wos-lit / litquery", ""])
    return lines


def direction_report(plan: dict, builds: dict, counts: dict | None = None, title: str = "研究方向关键词与检索式") -> str:
    direction = plan.get("direction") or {}
    topic = plan.get("topic") or {}
    lines = _header(title, "第一步产物：方向匹配 + 英文关键词底座 + 可执行检索式")
    lines.append("## 一、方向命中")
    lines.append("")
    if direction.get("matched"):
        lines.append("| 项 | 内容 |")
        lines.append("|---|---|")
        lines.append(f"| 输入 | {_cell(direction.get('input'))} |")
        lines.append(f"| 层级路径 | {_cell(' > '.join(direction.get('path') or []))} |")
        lines.append(f"| 英文名 | {_cell(direction.get('en'))} |")
        lines.append(f"| 匹配方式 | {_cell(direction.get('match_type'))}（置信度 {direction.get('confidence')}） |")
        lines.append("")
    elif topic:
        lines.append(f"- 研究主题：{_cell(topic.get('zh'))} / {_cell(topic.get('en'))}")
    else:
        lines.append("- [WARN] 未命中内置方向词表，关键词由 agent 依据领域知识补全。")
    lines.append("")

    lines.append("## 二、英文关键词（方向级，未细分）")
    lines.append("")
    keywords = plan.get("english_keywords") or []
    if keywords:
        lines.append("| # | English keyword |")
        lines.append("|---|---|")
        for i, kw in enumerate(keywords, 1):
            lines.append(f"| {i} | {_cell(kw)} |")
    else:
        lines.append("- [STOP] 无关键词：请先补全 concepts。")
    lines.append("")

    concepts = plan.get("concepts") or []
    if concepts:
        lines.append("## 三、概念块")
        lines.append("")
        lines.append("| 概念 | 角色 | 主词 | 同义词 | 上位词 | 下位词 |")
        lines.append("|---|---|---|---|---|---|")
        for concept in concepts:
            lines.append(
                "| {label} | {role} | {terms} | {syn} | {broader} | {narrower} |".format(
                    label=_cell(concept.get("label")),
                    role=_cell(concept.get("role") or "core"),
                    terms=_cell("; ".join(concept.get("terms") or []), 160),
                    syn=_cell("; ".join(concept.get("synonyms") or []), 120),
                    broader=_cell("; ".join(concept.get("broader") or []), 80),
                    narrower=_cell("; ".join(concept.get("narrower") or []), 80),
                )
            )
        lines.append("")

    if builds:
        lines.append("## 四、检索式")
        lines.append("")
        for engine, built in builds.items():
            lines.append(f"### {engine}")
            lines.append("")
            lines.append("```text")
            lines.append(built.get("query") or "")
            lines.append("```")
            lines.append("")
            stats = built.get("stats") or {}
            lines.append(
                f"- 概念块 {stats.get('concepts_used', '?')} 个 / 词项 {stats.get('terms', '?')} 个 / "
                f"布尔运算符 {stats.get('boolean_ops', '?')} 个 / 邻近运算符 {stats.get('proximity_ops', '?')} 个"
            )
            if counts and counts.get(engine):
                info = counts[engine]
                lines.append(f"- 命中数：{info.get('count')}（{info.get('evidence') or ''}）")
            lines.append("")
    lines.append("## 五、下一步")
    lines.append("")
    lines.append("- 以本检索式在 Scopus / Web of Science 检索权威综述，并执行 `reviews discover`。")
    lines.append("- 方向级关键词不再向下细分；细分方向在读完综述后归纳。")
    lines.append("")
    return "\n".join(lines)


def reviews_report(
    discover: dict,
    digest: dict | None = None,
    title: str = "领域权威综述与细分方向",
    min_reviews_expected: int = 5,
) -> str:
    selected = discover.get("selected") or []
    digest = digest or {}
    by_id = {str(item.get("id")): item for item in digest.get("reviews") or []}
    stats = discover.get("stats") or {}

    lines = _header(title, "第二步产物：权威综述清单、摘要翻译与解读、细分研究方向")
    lines.append("## 一、检索与筛选")
    lines.append("")
    chosen = discover.get("chosen") or {}
    query_display = chosen.get("query") or ""
    if not query_display:
        parts = []
        if discover.get("anchor"):
            parts.append("OpenAlex anchor: " + str(discover["anchor"]))
        if discover.get("filters"):
            parts.append("OpenAlex filters: " + str(discover["filters"]))
        query_display = "\n".join(parts) or "(未记录检索式)"
    lines.append("| 项 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 数据源 | {_cell(discover.get('engine'))} |")
    lines.append(f"| 实际检索式 | 见本节末尾的代码块 |")
    lines.append(f"| 查询哈希 | `{chosen.get('sha256') or '—'}` |")
    total = discover.get("total")
    lines.append(f"| 库内命中 | {total if total is not None else '未取得'} |")
    lines.append(f"| 候选记录 | {discover.get('candidates', 0)} |")
    lines.append(f"| 入选综述 | {len(selected)} |")
    lines.append("")
    lines.append("```text")
    lines.append(query_display)
    lines.append("```")
    lines.append("")
    if stats:
        lines.append("| 排除原因 | 数量 |")
        lines.append("|---|---|")
        for reason, count in (stats.get("rejected_by_reason") or {}).items():
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    warnings = stats.get("warnings") or []
    if warnings:
        lines.append("### 需要读者注意")
        lines.append("")
        for warning in warnings:
            lines.append(f"- [WARN] {warning}")
        lines.append("")

    lines.append("## 二、入选综述")
    lines.append("")
    lines.append("排序：综合分 = 期刊层级 50% + 被引 30% + 时效 20%（见 `screen.py`，逐条给出理由）。")
    lines.append("")
    lines.append("| # | 年份 | 标题 | 期刊 | 期刊层级 | 被引 | 一作 | 通讯 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, record in enumerate(selected, 1):
        first, corr = _authors(record)
        lines.append(
            "| {i} | {year} | {title} | {journal} | {rank} | {cited} | {first} | {corr} |".format(
                i=i,
                year=record.get("year") or "",
                title=_link(record.get("title"), record.get("url")),
                journal=_cell(record.get("journal"), 60),
                rank=_rank_label(record.get("journal_rank") or {}),
                cited=record.get("cited_by") or 0,
                first=first,
                corr=corr,
            )
        )
    lines.append("")

    lines.append("## 三、逐篇摘要翻译与解读")
    lines.append("")
    for i, record in enumerate(selected, 1):
        item = by_id.get(str(record.get("id"))) or by_id.get(recmod.normalize_doi(record.get("doi"))) or {}
        lines.append(f"### {i}. {record.get('title')}")
        lines.append("")
        lines.append("| 项 | 内容 |")
        lines.append("|---|---|")
        lines.append(f"| 期刊 / 年份 | {_cell(record.get('journal'))} / {record.get('year') or ''} |")
        lines.append(f"| 期刊层级 | {_rank_label(record.get('journal_rank') or {})} |")
        lines.append(f"| 被引 | {record.get('cited_by') or 0} |")
        lines.append(f"| 一作 | {_authors(record)[0]} |")
        lines.append(f"| 通讯 | {_authors(record)[1]} |")
        lines.append(f"| DOI | {recmod.doi_url(record.get('doi')) or '未取得'} |")
        if record.get("abstract_source"):
            lines.append(f"| 摘要来源 | {record['abstract_source']} |")
        if (record.get("fulltext") or {}).get("path"):
            lines.append(f"| 全文 | `{record['fulltext']['path']}`（{record['fulltext'].get('status')}） |")
        elif record.get("fulltext"):
            lines.append(f"| 全文 | 未获取（{record['fulltext'].get('status')}） |")
        lines.append("")

        abstract_en = record.get("abstract") or ""
        if abstract_en:
            lines.extend(
                [
                    "<details>",
                    f"<summary>英文摘要（{len(abstract_en)} 字符）</summary>",
                    "",
                    abstract_en,
                    "",
                    "</details>",
                    "",
                ]
            )
        else:
            lines.append("- [WARN] 未取得摘要，无法翻译。")

        abstract_zh = item.get("abstract_zh") or ""
        lines.append("**摘要翻译**")
        lines.append("")
        lines.append(abstract_zh if abstract_zh else "- [STOP] 待填写：摘要中文翻译。")
        lines.append("")
        lines.append("**解读**")
        lines.append("")
        bullets = item.get("interpretation") or []
        if isinstance(bullets, str):
            bullets = [bullets]
        if bullets:
            for bullet in bullets:
                lines.append(f"- {bullet}")
        else:
            lines.append("- [STOP] 待填写：方法与结论解读（3-5 条）。")
        lines.append("")
        lines.append("**本篇提示的细分方向**")
        lines.append("")
        subs = item.get("sub_directions") or []
        if subs:
            for sub in subs:
                if isinstance(sub, str):
                    lines.append(f"- {sub}")
                else:
                    evidence = f"：{sub.get('evidence')}" if sub.get("evidence") else ""
                    lines.append(f"- {sub.get('name')}{evidence}")
        else:
            lines.append("- [STOP] 待填写：由本篇归纳的细分方向。")
        lines.append("")

    lines.append("## 四、细分研究方向总览")
    lines.append("")
    sub_map = digest.get("sub_direction_map") or []
    if sub_map:
        lines.append("| 细分方向 | 支撑综述 | 说明 |")
        lines.append("|---|---|---|")
        for item in sub_map:
            refs = item.get("reviews") or []
            ref_text = "; ".join(
                _cell((r.get("title") if isinstance(r, dict) else str(r)), 60) for r in refs
            ) if refs else ""
            lines.append(f"| {_cell(item.get('name'))} | {ref_text} | {_cell(item.get('note'), 120)} |")
    else:
        lines.append("- [STOP] 待填写：汇总后的细分研究方向。")
    lines.append("")

    summary = digest.get("summary_zh") or ""
    if summary:
        lines.append("## 五、领域概览（中文）")
        lines.append("")
        lines.append(summary)
        lines.append("")

    lines.append("## 六、需要你确认")
    lines.append("")
    lines.append("[STOP] 请先阅读以上综述与细分方向，确认无误后给出一个细化研究主题（例如“某干预对某人群某结局的影响”）。")
    lines.append("在你确认之前，不进入检索式细化与文献列表阶段。")
    lines.append("")
    if len(selected) < min_reviews_expected:
        lines.append(f"[WARN] 本次仅入选 {len(selected)} 篇（少于建议的 {min_reviews_expected} 篇），覆盖可能不足。")
        lines.append("")
    return "\n".join(lines)


def list_report(run: dict, title: str = "文献与综述列表", max_records: int | None = None) -> str:
    records = run.get("records") or []
    if max_records:
        records = records[:max_records]
    chosen = run.get("chosen") or {}
    lines = _header(title, "第三步产物：细化主题检索结果（标题、摘要、地址、一作与通讯）")
    lines.append("## 一、检索结果概况")
    lines.append("")
    lines.append("| 项 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 数据源 | {_cell(run.get('engine'))} |")
    lines.append("| 最终检索式 | 见下方代码块 |")
    lines.append(f"| 库内命中 | {chosen.get('count') if chosen.get('count') is not None else '未取得'} |")
    lines.append(f"| 取回记录 | {len(records)} |")
    lines.append(f"| 检索状态 | {run.get('status')} |")
    lines.append("")
    lines.append("```text")
    lines.append(chosen.get("query") or "(未记录检索式)")
    lines.append("```")
    lines.append("")
    if run.get("status") != "ok":
        lines.append(
            "[WARN] 检索结果未达到目标数量。请根据下方尝试记录调整检索词，或按报告建议向用户说明，"
            "不要用不足的样本冒充完整检索。"
        )
        lines.append("")
    attempts = run.get("attempts") or []
    chosen_sha = (chosen or {}).get("sha256")
    if attempts:
        lines.append("| 尝试 | 说明 | 命中 | 是否采用 |")
        lines.append("|---|---|---|---|")
        for attempt in attempts:
            used = bool(chosen_sha and attempt.get("sha256") == chosen_sha)
            lines.append(
                f"| {attempt.get('label')} | {_cell(attempt.get('rationale'), 90)} | "
                f"{attempt.get('count') if attempt.get('count') is not None else '未取得'} | "
                f"{'[YES]' if used else ''} |"
            )
        lines.append("")

    lines.append("## 二、文献清单")
    lines.append("")
    lines.append("| # | 年份 | 标题 | 期刊 | 一作 | 通讯 | 被引 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, record in enumerate(records, 1):
        first, corr = _authors(record)
        url = record.get("url") or recmod.doi_url(record.get("doi")) or record.get("source_url")
        lines.append(
            "| {i} | {year} | {title} | {journal} | {first} | {corr} | {cited} |".format(
                i=i,
                year=record.get("year") or "",
                title=_link(record.get("title"), url),
                journal=_cell(record.get("journal"), 50),
                first=first,
                corr=corr,
                cited=record.get("cited_by") or 0,
            )
        )
    lines.append("")

    lines.append("## 三、逐篇信息")
    lines.append("")
    for i, record in enumerate(records, 1):
        first, corr = _authors(record)
        lines.append(f"### {i}. {record.get('title')}")
        lines.append("")
        lines.append(f"- 期刊 / 年份：{record.get('journal') or ''} / {record.get('year') or ''}")
        lines.append(f"- 一作：{first or '未取得'}")
        lines.append(f"- 通讯：{corr or '未取得'}")
        lines.append(f"- 地址：{record.get('url') or recmod.doi_url(record.get('doi')) or '未取得'}")
        if record.get("doi"):
            lines.append(f"- DOI：{record['doi']}")
        if record.get("source_db"):
            lines.append(f"- 来源库：{record['source_db']}")
        lines.append("")
        abstract = record.get("abstract") or ""
        lines.append("**摘要**")
        lines.append("")
        lines.append(abstract if abstract else "- [WARN] 未取得摘要。")
        lines.append("")

    lines.append("## 四、方法说明与局限")
    lines.append("")
    lines.append("- 命中数来自所选数据源页面或 API 返回值，记录在 `attempts` 中，可复核。")
    lines.append("- 摘要优先取 Scopus 记录页；缺失时依次用 OpenAlex / Crossref / Scopus Abstract API 补全。")
    lines.append("- 通讯作者优先取 OpenAlex 的 `is_corresponding`；缺失时以末位作者推断并在字段中标注 `(inferred)`。")
    lines.append("- 期刊层级来自用户导入的 JCR/中科院表；未导入时使用内置种子表的“中科院一区”粗判，属近似判定。")
    lines.append("")
    return "\n".join(lines)


def digest_tasks(selected: list[dict]) -> dict:
    tasks = []
    for record in selected:
        tasks.append(
            {
                "id": str(record.get("id")),
                "doi": record.get("doi") or "",
                "title": record.get("title") or "",
                "journal": record.get("journal") or "",
                "year": record.get("year"),
                "abstract_en": record.get("abstract") or "",
                "abstract_zh": "",
                "interpretation": [],
                "sub_directions": [],
                "notes": "",
            }
        )
    return {
        "schema_version": "litquery-digest/1.0",
        "created_at": now_iso(),
        "instruction": [
            "逐篇完成 abstract_zh / interpretation / sub_directions。",
            "abstract_zh 必须是完整中文翻译，不得只写要点。",
            "interpretation 3-5 条，写清研究问题、方法、主要结论、局限。",
            "sub_directions 由该综述内容归纳，2-5 条，每条带 evidence。",
            "全部完成后运行 lq reviews digest-check，再生成报告。",
        ],
        "reviews": tasks,
        "sub_direction_map": [],
        "summary_zh": "",
    }


def digest_check(tasks: dict, filled: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    expected = {str(t.get("id")): t for t in tasks.get("reviews") or []}
    got = {str(t.get("id")): t for t in filled.get("reviews") or []}
    missing = [k for k in expected if k not in got]
    for key in missing:
        errors.append({"id": key, "field": "review", "message": "missing review entry"})

    for key, item in got.items():
        if key not in expected:
            warnings.append({"id": key, "field": "review", "message": "entry not in task list"})
            continue
        abstract_zh = (item.get("abstract_zh") or "").strip()
        if len(abstract_zh) < 60:
            errors.append({"id": key, "field": "abstract_zh", "message": "translation missing or too short (<60 chars)"})
        if EMOJI_RE.search(abstract_zh):
            warnings.append({"id": key, "field": "abstract_zh", "message": "emoji found; replace with [OK]/[WARN]"})
        interpretation = item.get("interpretation") or []
        if isinstance(interpretation, str):
            interpretation = [interpretation]
        text = " ".join(str(x) for x in interpretation)
        if len(interpretation) < 2 or len(text) < 100:
            errors.append({"id": key, "field": "interpretation", "message": "need >=2 bullets and >=100 chars"})
        subs = item.get("sub_directions") or []
        if len(subs) < 2:
            errors.append({"id": key, "field": "sub_directions", "message": "need >=2 sub-directions per review"})
        for sub in subs:
            name = sub if isinstance(sub, str) else sub.get("name")
            if not name:
                errors.append({"id": key, "field": "sub_directions", "message": "sub-direction without a name"})
        if re.search(r"TODO|待补充|待填写", abstract_zh + text):
            errors.append({"id": key, "field": "placeholder", "message": "unresolved placeholder text"})

    sub_map = filled.get("sub_direction_map") or []
    if len(sub_map) < 3:
        warnings.append({"id": "-", "field": "sub_direction_map", "message": "fewer than 3 sub-directions in the overview"})
    summary = (filled.get("summary_zh") or "").strip()
    if len(summary) < 200:
        warnings.append({"id": "-", "field": "summary_zh", "message": "overview shorter than 200 chars"})

    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked": len(got), "expected": len(expected)}


def write(path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def search_log(run: dict) -> dict:
    return {
        "created_at": now_iso(),
        "engine": run.get("engine"),
        "status": run.get("status"),
        "target_min": run.get("target_min"),
        "chosen": run.get("chosen"),
        "attempts": run.get("attempts"),
        "records_meta": run.get("records_meta"),
        "notes": run.get("notes"),
    }
