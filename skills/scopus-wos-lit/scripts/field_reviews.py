# -*- coding: utf-8 -*-
"""Build one authoritative-review digest per broad discipline.

Data source: OpenAlex (no browser, no subscription). Each field is queried with
``type:review`` + a keyword anchor, sorted by citations, then screened by the
same journal-tier / citation / topic rules used elsewhere in this project.

Usage:
    python scripts/field_reviews.py --out work/fields
    python scripts/field_reviews.py --out work/fields --only neuroscience,immunology
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from litquery import records as recmod  # noqa: E402
from litquery.backends.europepmc import EuropePMC  # noqa: E402
from litquery.backends.openalex import OpenAlex  # noqa: E402
from litquery.journals import load_all  # noqa: E402
from litquery.screen import SelectOptions, select  # noqa: E402
from litquery.util import HttpClient, Log, ensure_dir, read_json, write_json, write_text  # noqa: E402

CATALOG = HERE.parent / "assets" / "field_catalog.json"


def _cell(text, width=100):
    text = re.sub(r"\s+", " ", str(text or "")).strip().replace("|", "\\|")
    return text if len(text) <= width else text[: width - 1] + "…"


def _link(title, url):
    title = _cell(title, 120)
    return f"[{title}]({url})" if url else title


def _first_corr(record):
    first = record.get("first_author") or ""
    corr = "; ".join(record.get("corresponding_authors") or [])
    if corr and record.get("corresponding_inferred"):
        corr += " (inferred)"
    return _cell(first, 40), _cell(corr, 60)


def select_for_field(records, table, field, limit=5, min_citations=50):
    base = dict(
        top_percent=20.0,
        min_year=2018,
        max_age_years=8,
        min_citations=min_citations,
        rank_policy="balanced",
        limit=limit,
        max_per_journal=2,
        require_topic_terms=[str(x) for x in field["keywords"][:10]],
        min_topic_hits=2,
    )
    result = select(records, table, SelectOptions(**base))
    if len(result["selected"]) < max(3, limit - 2):
        relaxed = dict(base)
        relaxed.update(
            min_year=2016,
            min_citations=max(20, min_citations // 2),
            rank_policy="off",
            max_per_journal=3,
            min_topic_hits=1,
        )
        result = select(records, table, SelectOptions(**relaxed))
    return result


def fill_abstracts(records, epmc: EuropePMC):
    missing = [r.get("doi") for r in records if r.get("doi") and not r.get("abstract")]
    if not missing:
        return records
    found = epmc.abstracts(missing)
    for record in records:
        doi = (record.get("doi") or "").lower()
        if doi in found and not record.get("abstract"):
            record["abstract"] = found[doi]
            record["abstract_source"] = "europepmc"
    return records


def field_markdown(field, selection, frontier, zh=None):
    selected = selection["selected"]
    stats = selection["stats"]
    lines = [
        f"# {field['zh']}（{field['en']}）领域权威综述",
        "",
        f"> 数据源：OpenAlex；类型 `review`；2018 年后；按被引降序取前 {frontier} 条候选；"
        "期刊层级采用本地表（JCR 百分位优先，缺失时中科院一区近似）。",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "- 生成工具：scopus-wos-lit / field_reviews.py",
        "",
        "## 一、领域导读",
        "",
        field.get("overview_zh") or "",
        "",
        "**检索锚点**：`" + " | ".join(field["keywords"]) + "`",
        "",
        "## 二、综述清单",
        "",
        f"候选 {stats.get('candidates', 0)} 条，入选 {len(selected)} 条。",
        "",
        "| # | 年份 | 标题 | 期刊 | 被引 | 一作 | 通讯 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, record in enumerate(selected, 1):
        first, corr = _first_corr(record)
        lines.append(
            "| {i} | {year} | {title} | {journal} | {cited} | {first} | {corr} |".format(
                i=i,
                year=record.get("year") or "",
                title=_link(record.get("title"), record.get("url")),
                journal=_cell(record.get("journal"), 48),
                cited=record.get("cited_by") or 0,
                first=first,
                corr=corr,
            )
        )
    lines.extend(["", "## 三、逐篇摘要", ""])
    for i, record in enumerate(selected, 1):
        first, corr = _first_corr(record)
        lines.extend(
            [
                f"### {i}. {record.get('title')}",
                "",
                f"- 期刊 / 年份 / 被引：{record.get('journal') or ''} / {record.get('year') or ''} / {record.get('cited_by') or 0}",
                f"- 一作：{first or '未取得'}",
                f"- 通讯：{corr or '未取得'}",
                f"- DOI：{recmod.doi_url(record.get('doi')) or '未取得'}",
            ]
        )
        rank = record.get("journal_rank") or {}
        if rank.get("matched"):
            lines.append(f"- 期刊层级：{rank.get('reason') or rank.get('basis')}")
        abstract = record.get("abstract") or ""
        lines.append("")
        if abstract:
            lines.extend(
                [
                    "<details>",
                    f"<summary>英文摘要（{len(abstract)} 字符）</summary>",
                    "",
                    abstract,
                    "",
                    "</details>",
                    "",
                ]
            )
        else:
            lines.extend(["- [WARN] 未取得摘要。", ""])

    if zh:
        by_index = {int(item.get("index", i)): item for i, item in enumerate(zh.get("reviews") or [], 1)}
        lines.extend(["## 四、中文简要翻译与解读", ""])
        for i, record in enumerate(selected, 1):
            item = by_index.get(i) or {}
            lines.append(f"### {i}. {item.get('title_zh') or record.get('title')}")
            lines.append("")
            lines.append(f"- 简要翻译：{item.get('summary_zh') or '[WARN] 待补'}") 
            lines.append(f"- 解读：{item.get('interpretation') or '[WARN] 待补'}")
            lines.append("")

    section_no = "五" if zh else "四"
    lines.extend(
        [
            f"## {section_no}、方法与局限",
            "",
            "- 只纳入 OpenAlex 标注为 `review` 的记录；不包含原始研究、试验方案与社论。",
            "- 期刊层级由本地表判定；未导入 JCR 百分位时，一区为近似判定，报告已保留 `approx` 语义。",
            "- 主题相关性要求标题命中，或摘要命中至少两个核心词；无摘要时不做主题拒绝。",
            "- 摘要为英文原文；中文部分为简要翻译与解读，不是逐句全译。",
            f"- 本轮入选 {len(selected)} 篇；少于 5 篇时说明该领域 OpenAlex 综述覆盖或筛选阈值偏紧。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="work/fields")
    parser.add_argument("--catalog", default=str(CATALOG))
    parser.add_argument("--only", default="")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-records", type=int, default=50)
    parser.add_argument("--min-citations", type=int, default=50)
    parser.add_argument("--mailto", default="")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--zh-dir", default="", help="directory with per-field Chinese brief JSON (default: <out>/zh)")
    args = parser.parse_args(argv)

    outdir = ensure_dir(pathlib.Path(args.out))
    data_dir = ensure_dir(outdir / "data")
    zh_dir = pathlib.Path(args.zh_dir) if args.zh_dir else (outdir / "zh")
    log = Log(path=outdir / "field_reviews.jsonl", verbose=True)
    catalog = read_json(args.catalog)
    fields = catalog.get("fields") or []
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    if only:
        fields = [f for f in fields if f["id"] in only]

    cache = None if args.no_cache else ensure_dir(outdir / ".http_cache")
    http = HttpClient(cache_dir=cache, log=log, min_interval=0.8)
    openalex = OpenAlex(http, mailto=args.mailto or None)
    epmc = EuropePMC(http)
    table = load_all(None)

    rows = []
    failures = []
    for index, field in enumerate(fields, 1):
        log.info(f"[{index}/{len(fields)}] {field['zh']} ...")
        anchor = "|".join(field["keywords"][:10])
        filters = "type:review,from_publication_date:2018-01-01"
        if field.get("openalex_filter"):
            filters += "," + str(field["openalex_filter"])
        try:
            result = openalex.search(
                anchor=anchor,
                filters=filters,
                max_records=args.max_records,
                sort="cited_by_count:desc",
                title_only=True,
            )
        except Exception as exc:
            log.error(f"{field['id']}: OpenAlex request failed: {type(exc).__name__}: {exc}")
            failures.append({"id": field["id"], "error": str(exc)})
            continue
        if openalex.disabled:
            log.error(f"OpenAlex disabled: {openalex.disabled_reason}")
            failures.append({"id": field["id"], "error": openalex.disabled_reason})
            break

        records = result.records or []
        selection = select_for_field(records, table, field, limit=args.limit, min_citations=args.min_citations)
        selected = fill_abstracts(selection["selected"], epmc)
        selection["selected"] = selected
        selection["stats"]["selected"] = len(selected)
        payload = {
            "field": field,
            "query": {"anchor": anchor, "filters": filters},
            "total": result.total,
            "candidates": len(records),
            "selected": selected,
            "rejected": selection["rejected"][:30],
            "stats": selection["stats"],
        }
        write_json(data_dir / f"{field['id']}.json", payload)
        zh_path = zh_dir / f"{field['id']}.json"
        zh = read_json(zh_path) if zh_path.exists() else None
        write_text(outdir / f"{field['id']}.md", field_markdown(field, selection, args.max_records, zh))
        rows.append(
            {
                "id": field["id"],
                "zh": field["zh"],
                "en": field["en"],
                "count": len(selected),
                "journals": list(dict.fromkeys(r.get("journal") for r in selected if r.get("journal")))[:3],
                "file": f"{field['id']}.md",
            }
        )
        log.info(f"{field['id']}: {result.total} hits, {len(selected)} selected")
        time.sleep(0.6)

    # Rebuild the index from every data file so --only does not drop fields.
    rows = []
    order = {f["id"]: i for i, f in enumerate(catalog.get("fields") or [])}
    for path in data_dir.glob("*.json"):
        payload = read_json(path)
        field = payload.get("field") or {}
        selected = payload.get("selected") or []
        rows.append(
            {
                "id": field.get("id"),
                "zh": field.get("zh"),
                "en": field.get("en"),
                "count": len(selected),
                "journals": list(dict.fromkeys(r.get("journal") for r in selected if r.get("journal")))[:3],
                "file": f"{field.get('id')}.md",
            }
        )
    rows.sort(key=lambda r: order.get(r["id"], 999))

    lines = [
        "# 各领域权威综述索引",
        "",
        "> 数据源：OpenAlex；每个领域只取 `review` 类型、2018 年后、按被引降序；"
        "期刊层级用本地表（缺失时近似判定）。",
        "> 中文简要翻译与解读见 [中文简要翻译与解读.md](中文简要翻译与解读.md)。",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"- 领域数：{len(rows)}",
        "",
        "| 领域 | 入选 | 代表期刊 | 文件 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        journals = "；".join(_cell(j, 42) for j in row["journals"])
        lines.append(f"| {row['zh']}（{row['en']}） | {row['count']} | {journals} | [{row['file']}]({row['file']}) |")
    if failures:
        lines.extend(["", "## 未完成", ""])
        for failure in failures:
            lines.append(f"- {failure['id']}：{failure['error']}")
    lines.extend(
        [
            "",
            "## 使用说明",
            "",
            "- 每个领域的明细包含：领域导读、综述清单、逐篇英文摘要、DOI、一作与通讯作者。",
            "- 这不是穷尽检索；每个领域最多 5 篇，目的是给出可读的入口。",
            "- 若某领域少于 5 篇，说明 OpenAlex 的 `review` 类型覆盖不足或筛选阈值偏紧，可放宽后重跑。",
            "",
        ]
    )
    write_text(outdir / "00_索引.md", "\n".join(lines))
    print(json.dumps({"out": str(outdir), "fields": len(rows), "failures": failures}, ensure_ascii=False))
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
