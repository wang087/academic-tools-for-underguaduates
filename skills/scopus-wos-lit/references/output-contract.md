# 产物契约

所有 JSON 均为 UTF-8、`ensure_ascii=false`、缩进 2。字段缺失时写 `null` 或省略，不要用空字符串冒充“已取得”。

## 1. `plan.json`（方向/主题计划）

```json
{
  "schema_version": "litquery-plan/1.0",
  "created_at": "2026-10-21T19:00:00",
  "direction": {
    "input": "生物学 > 免疫与微生物 > 人体微生物组学",
    "matched": true,
    "match_type": "path",
    "confidence": 0.99,
    "path": ["生物学", "免疫与微生物", "人体微生物组学"],
    "zh": "人体微生物组学",
    "en": "Human Microbiome",
    "synonyms_zh": ["人类微生物组"]
  },
  "english_keywords": ["human microbiome", "human microbiota", "..."],
  "concepts": [
    {
      "id": "c1",
      "label": "核心主题（方向级关键词并集）",
      "role": "core",
      "terms": ["human microbiome", "..."],
      "synonyms": [],
      "broader": [],
      "narrower": [],
      "proximity": 2,
      "exact": true
    },
    {
      "id": "c2",
      "label": "方法",
      "role": "optional",
      "terms": ["metagenomics", "16S rRNA sequencing"]
    }
  ],
  "exclude": [],
  "filters": {
    "year_from": 2019,
    "year_to": null,
    "document_types": ["review"],
    "languages": ["English"]
  },
  "notes": ["..."]
}
```

`role`：

- `core`：必选，进 `AND`。
- `context`：默认进入检索（`AND`），用于细化主题。
- `optional`：默认**不**进入检索；`--with-optional` 才启用。

## 2. `queries.json`（检索式）

```json
{
  "schema_version": "litquery-queries/1.0",
  "plan": "plan.json",
  "builds": {
    "scopus": {
      "engine": "scopus",
      "query": "TITLE-ABS-KEY(...) AND PUBYEAR > 2018 AND DOCTYPE(re)",
      "sha256": "…",
      "stats": {"concepts_used": 1, "terms": 8, "boolean_ops": 9, "proximity_ops": 0, "chars": 210},
      "lint": {"ok": true, "errors": [], "warnings": []}
    },
    "wos": {"…": "…"}
  }
}
```

同时生成 `queries_scopus.txt`、`queries_wos.txt`，内容只有检索式一行，便于粘贴。

## 3. `reviews.selected.json`（综述发现）

```json
{
  "status": "ok",
  "engine": "scopus-web",
  "attempts": [
    {"label": "A_strict", "rationale": "...", "query": "...", "sha256": "...", "count": 19941, "cached": false}
  ],
  "chosen": {"label": "A_strict", "query": "...", "sha256": "...", "count": 19941},
  "total": 19941,
  "candidates": 30,
  "selected": [
    {
      "id": "scopus-web:105050320037",
      "doi": "10.4103/mgr.MEDGASRES-D-26-00073",
      "title": "...",
      "abstract": "...",
      "authors": [{"name": "...", "is_first": true, "is_corresponding": false}],
      "first_author": "...",
      "corresponding_authors": ["..."],
      "corresponding_inferred": true,
      "journal": "...",
      "year": 2027,
      "cited_by": 0,
      "document_type": "review",
      "url": "https://doi.org/...",
      "source_db": "scopus-web",
      "journal_rank": {"matched": true, "percentile": 95.0, "passes": true, "approx": false, "basis": "jif_percentile"},
      "selection": {"selected": true, "score": 0.83, "score_parts": {}, "reasons": [], "reject_reasons": []},
      "fulltext": {"status": "pdf", "path": "fulltext/xxx/fulltext.pdf", "chars": 48000}
    }
  ],
  "rejected": [],
  "stats": {
    "candidates": 30,
    "eligible": 9,
    "selected": 8,
    "rejected_by_reason": {"journal_not_top20": 12, "not_a_review": 3},
    "warnings": []
  },
  "journal_table": {"entries": 145, "with_percentile": 0, "cas_tier1": 138},
  "notes": []
}
```

`status`：`ok` / `no_selection`。`attempts[].count` 为 `null` 表示未解析到计数，不是 0。

## 4. `digest.tasks.json` / `digest.json`

```json
{
  "schema_version": "litquery-digest/1.0",
  "created_at": "…",
  "reviews": [
    {
      "id": "…",
      "doi": "…",
      "title": "…",
      "abstract_en": "…",
      "abstract_zh": "",
      "interpretation": [],
      "sub_directions": [],
      "notes": ""
    }
  ],
  "sub_direction_map": [],
  "summary_zh": ""
}
```

填写后 `sub_directions` 为 `[{"name": "...", "evidence": "..."}]`，`sub_direction_map` 为 `[{"name": "...", "reviews": ["标题/DOI"], "note": "..."}]`。

## 5. `search.run.json`（细化主题检索）

```json
{
  "status": "ok",
  "engine": "scopus-web",
  "target_min": 15,
  "attempts": [
    {"label": "A_strict", "query": "...", "sha256": "...", "count": 312, "cached": false, "lint_ok": true}
  ],
  "chosen": {"label": "A_strict", "query": "...", "count": 312},
  "records": [
    {
      "title": "...",
      "abstract": "...",
      "doi": "...",
      "url": "https://doi.org/...",
      "journal": "...",
      "year": 2022,
      "cited_by": 41,
      "first_author": "...",
      "corresponding_authors": ["..."],
      "authors": [],
      "source_db": "scopus-web",
      "doi_source": "scopus-record-page"
    }
  ],
  "records_meta": {"source_total": 312, "returned": 30, "records_available": true},
  "stats": {"records": 30, "with_abstract": 28, "with_doi": 30, "reviews": 12, "year_min": 2018, "year_max": 2026},
  "notes": []
}
```

`status`：`ok` / `insufficient` / `failed`。`insufficient` 时仍保留 `chosen` 与已取记录，但报告必须写明未达目标。

## 6. Markdown

| 文件 | 必需小节 |
|---|---|
| `01_方向关键词.md` | 方向命中、英文关键词、概念块、检索式、下一步 |
| `02_权威综述.md` | 检索与筛选、入选综述、逐篇翻译解读、细分方向总览、确认门 |
| `03_文献清单.md` | 检索概况、文献清单、逐篇信息、方法与局限 |

标题层级：`#` 文档标题、`##` 一级小节、`###` 单篇/单条。表格中的 `|` 必须转义为 `\|`。

## 7. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 输入错误、后端不可用、lint 失败 |
| 3 | 未达目标（方向未命中、无入选综述、命中 < 15） |
| 130 | 用户中断 |

## 8. 日志

- `.work_lq/lq.jsonl`：命令、HTTP 请求、状态码、缓存命中。
- `.work_lq/count_cache.json`：`engine + sha256(query)` → 命中数、页面状态、检查时间。
- `fulltext/fulltext_manifest.json`：每篇的全文状态与路径。
- 快照：`web probe --outdir` 生成的 `.html/.txt/.json`。

日志与快照都可能含页面文本，**不要提交到公开仓库**。
