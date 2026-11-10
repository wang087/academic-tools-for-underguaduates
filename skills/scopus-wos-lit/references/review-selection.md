# 综述筛选：权威性、影响力与时效

筛选目标：从候选记录里挑出**本领域值得先读的 5–10 篇综述**，并让每篇的入选理由可复核。

## 1. 默认规则

| 维度 | 默认 | 可调参数 |
|---|---|---|
| 期刊层级 | JCR 百分位 ≥ 80（前 20%） | `--top-percent` |
| 期刊未知 | 严格模式排除 | `--rank-policy strict/balanced/off` |
| 类型 | Review / Meta-analysis | `--min-citations` 不影响类型 |
| 主题相关性 | 标题命中核心词，或摘要 ≥2 个不同核心词 / ≥3 次提及 | 方向级关键词（core 块前 12 个） |
| 范围词 | 命中 ≥1 个 `scope_terms` | 仅当方向节点定义了范围词 |
| 被引下限 | 年龄感知：`max(5, 20 × min(1, 年龄/5))` | `--min-citations` |
| 年均被引 | ≥ 2 | 代码内常量 |
| 时效 | 近 6 年 | `--max-age`、`--min-year` |
| 奠基之作 | 总被引 ≥ 400 或年均 ≥ 40 且年份 ≥ 1980 | 代码内常量 |
| 每刊上限 | 2 篇 | `max_per_journal` |
| 数量 | 8 篇 | `--limit` |

“年龄”按 `当前年 - 出版年 + 1` 计算；新综述不会被“引用还没攒够”直接淘汰。

## 2. 综合分

```text
score = 0.50 × 期刊层级
      + 0.30 × 被引（log 归一）
      + 0.20 × 时效（8 年半衰期）
```

- 期刊层级：有百分位用 `percentile/100`；中科院一区 0.92；Q1 0.85；未知 0.45。
- 被引：`log1p(cited) / log1p(5000)`，封顶 1。
- 时效：`0.5 ** (年龄 / 8)`。

分数只用于排序，**不替代硬条件**：未达标的记录不会因为分数高而入选。

## 3. 拒绝原因（写入报告）

| 代码 | 含义 | 处理 |
|---|---|---|
| `off_topic` | 只有零散提及，不构成该方向综述 | 检查关键词是否过宽；看标题/摘要 |
| `out_of_scope` | 未命中方向的范围词（跨领域） | 检查 `scope_terms`；确实相关时补范围词 |
| `abstract_unavailable` | 拿不到可用于翻译的摘要 | Europe PMC/Scopus 均无正文型摘要；可用 `--allow-no-abstract` 保留并标注 |
| `not_a_review` | 不是综述 | 检查 `document_type` 与标题 |
| `journal_not_top20` | 期刊层级不足 | 可放宽 `--top-percent` |
| `journal_rank_unknown` | 期刊不在表内 | 导入更全的分区表，或 `--rank-policy balanced` |
| `older_than_6y` | 超过时效且非奠基 | 放宽 `--max-age` |
| `citations_N_below_floor_M` | 被引不足 | 放宽 `--min-citations` |
| `citations_per_year_X_below_2` | 年均被引不足 | 同上 |

报告只展示前 50 条被拒记录，完整列表在 JSON 里。

## 4. 奠基之作

满足任一条件即视为奠基之作，允许突破时效：

- 总被引 ≥ 400；
- 年均被引 ≥ 40；
- 年份 ≥ 1980（低于此不纳入，避免把学科史前文献当综述）。

入选后加 `flags: ["foundational"]`，并在 `selection.reasons` 里写明“older than 6y but kept as foundational”。

## 5. 期刊未知时的三种策略

| 策略 | 行为 | 适用 |
|---|---|---|
| `strict`（默认） | 未知一律排除 | 用户明确要求“前 20%” |
| `balanced` | 未知但被引 ≥ 150 可入选 | 分区表覆盖不足，但仍想要高影响力 |
| `off` | 不做期刊过滤 | 探索性调研；报告必须写明“未按分区筛选” |

[PROHIBIT] 不要把 `rank_unknown` 静默当成“达标”。报告里的 `journal_rank.matched=false` 必须保留。

## 6. 综述类型识别

不同来源的类型字段不一致：

| 来源 | 字段 | 备注 |
|---|---|---|
| Scopus | `subtypeDescription` | `Review` |
| WoS | `documentType` | `Review` |
| OpenAlex | `type` | `review` |
| Crossref | `type` | 多为 `journal-article`，需按标题推断 |

Crossref 记录会得到 `review-inferred`；这是**推断**，报告中应保留该值，不要伪造成数据库标注的 Review。

## 7. 被引数的来源差异

- Scopus 的 `citedby-count` 与 WoS 的 `TC` 通常不同（收录范围不同）。
- 记录合并时取两者最大值（`records.merge`）。
- 报告中注明来源库；跨库比较被引数没有意义。

## 8. 数量不足时

入选 < 5 篇时，按顺序：

1. 放宽 `--max-age` 到 10。
2. `--min-citations` 降到 10。
3. `--rank-policy balanced`。
4. `--top-percent 25`（并在报告中说明放宽了“前 20%”）。
5. 换库补候选（Scopus ↔ WoS ↔ OpenAlex）。

每次放宽都要在 `02_权威综述.md` 的“方法与局限”里写明，不能只改参数不记录。

## 9. 输出结构

```json
{
  "selected": [
    {
      "title": "...",
      "journal_rank": {"percentile": 95.0, "passes": true, "basis": "jif_percentile"},
      "selection": {
        "score": 0.83,
        "score_parts": {"rank": 0.95, "citations": 0.71, "recency": 0.84},
        "reasons": ["rank: JIF percentile 95.0 >= 80.0", "published 2022", "cited 300 (>= 20)"],
        "reject_reasons": []
      }
    }
  ],
  "rejected": [],
  "stats": {
    "candidates": 30,
    "eligible": 9,
    "selected": 8,
    "rejected_by_reason": {"journal_not_top20": 12}
  }
}
```

## 10. 自检

- [ ] 每篇入选都有 `journal_rank` 与 `selection.reasons`。
- [ ] `stats.warnings` 已读；少于 5 篇时有明确处置。
- [ ] 没有把 `approx` 判定写成精确判定。
- [ ] 没有把 `review-inferred` 写成数据库原始类型。
- [ ] 被引数标注了来源库。
