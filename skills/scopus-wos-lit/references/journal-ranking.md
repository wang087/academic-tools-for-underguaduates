# 期刊层级与“前 20%”判定

## 1. 为什么内置表不是官方分区

JCR（Journal Citation Reports）与《中国科学院文献情报中心期刊分区表》都是**商业授权数据**，不能随代码分发。本项目的做法：

- `assets/journal_tiers.seed.json`：只收录各学科公认顶级刊，标 `cas_tier=1` 与 `jcr_quartile=Q1`，**不写影响因子数值，也不写伪造的百分位**。
- 用户导入的 `journal_tiers.json` 优先于种子表，逐条覆盖。
- 报告会区分三种判定：`jif_percentile`（精确）、`cas_tier`（近似）、`rank_unknown`（未知）。

[PROHIBIT] 不得把种子表的 `cas_tier=1` 说成“JCR 前 20%”；它是“中科院一区”的粗判，属于近似。

## 2. 判定优先级

`scripts/litquery/journals.py::JournalTable.rank()`：

1. 有 `jif_percentile`：`percentile >= 100 - top_percent` 才算达标（默认 80）。
2. 无百分位但 `cas_tier == 1`：达标，`approx=true`，`basis="cas_tier"`。
3. 只有 `jcr_quartile == "Q1"`：默认**不达标**；`--accept-q1` 时达标，`approx=true`。原因：Q1 是前 25%，不是前 20%。
4. 都没有：`matched=false`，`passes=false`，`basis="unknown"`。**不得当成达标**。

`rank()` 返回：

```json
{
  "matched": true,
  "title": "Nature Reviews Microbiology",
  "percentile": null,
  "quartile": "Q1",
  "cas_tier": 1,
  "passes": true,
  "approx": true,
  "basis": "cas_tier",
  "match": "title",
  "source": "seed",
  "reason": "CAS tier 1 (approximate top-20% proxy; import JCR percentiles for an exact decision)"
}
```

## 3. 表结构

```json
{
  "schema_version": "1.0",
  "source": "user-jcr-2024",
  "journals": [
    {
      "title": "Nature Reviews Microbiology",
      "aliases": ["Nat Rev Microbiol", "Nature reviews. Microbiology"],
      "issn": ["1740-1526", "1740-1534"],
      "jif": 69.2,
      "jif_percentile": 98.7,
      "jcr_quartile": "Q1",
      "cas_tier": 1,
      "category": "Microbiology",
      "year": 2024
    }
  ]
}
```

- `title` 必填；其余可选。
- `aliases` 用于吸收“Nature reviews. Microbiology”“Nat Rev Microbiol”等写法。
- `issn` 支持带/不带连字符；优先按 ISSN 精确匹配。
- 键名以 `_` 开头的条目被忽略，可写说明。

兼容旧格式：

```json
{
  "中科院一区": ["Nature", "Science"],
  "JCR_Q1": ["Cell"]
}
```

会被转换成 `cas_tier=1` / `jcr_quartile=Q1`。

## 4. CSV 导入

### 4.1 JCR

```bash
python lq.py journals import --csv jcr.csv --format jcr --out journal_tiers.json
```

可识别的列名（大小写不敏感、常见变体）：

| 字段 | 列名候选 |
|---|---|
| 刊名 | `Journal name` / `Journal` / `Full Journal Title` / `Title` |
| ISSN | `ISSN` / `eISSN` / `ISSN/eISSN` |
| 影响因子 | `2024 JIF` / `JIF` / `Impact Factor` |
| 百分位 | `JIF Percentile` / `Percentile` |
| 分区 | `JIF Quartile` / `Quartile` |
| 学科 | `Category` / `Web of Science Category` |

注意：JCR 一个刊可能有多行（多学科分类）。导入时按行保留；同名刊后行覆盖前行。若要按“最宽松分类”判定，导入前自行取每刊最大百分位。

### 4.2 中科院分区

```bash
python lq.py journals import --csv cas.csv --format cas --out journal_tiers.json
```

列名：`期刊名称` / `刊名` / `Journal name`、`分区` / `大类分区`、`ISSN`、`大类`、`Top`。

分区值 `1区`/`1` 都会解析为 `cas_tier=1`。

### 4.3 SJR / Scimago

```bash
python lq.py journals import --csv scimago.csv --format sjr --out journal_tiers.json
```

列名：`Title`、`ISSN`、`SJR Best Quartile`、`H index`、`Categories`。SJR 的 Q1 同样是前 25%，只作为近似；本项目默认不因 `sjr_quartile` 判达标。

## 5. 匹配规则

1. ISSN 精确匹配（去分隔符、大写）。
2. 规范化刊名精确匹配：小写、`&`→`and`、去括号内容、去标点、去 `the/of/and/for/on/in`。
3. 模糊匹配：`difflib` 相似度 ≥ 0.90。

规范化示例：

```text
The Nature Reviews: Microbiology  ->  nature reviews microbiology
Nature reviews. Microbiology      ->  nature reviews microbiology
NATURE REVIEWS MICROBIOLOGY       ->  nature reviews microbiology
```

匹配不到就是 `rank_unknown`，不要用“看起来像顶刊”来猜。

## 6. 实操建议

- 任务开始前先跑 `python lq.py journals stats`，确认 `with_percentile` 的数量；全是 0 说明只有种子表。
- 报告里写明判定依据：`JIF percentile` / `CAS tier 1 (approx)` / `rank_unknown`。
- 同一期刊在不同 JCR 年份、不同学科的百分位不同；报告注明 `year` 与 `category`。
- 多学科刊（Nature、Science、PNAS）通常各分类都在 Q1；用最大百分位更合理。
- 期刊更名、ISSN 变更会导致匹配失败；用 `aliases` 补齐。

## 7. 自检

```bash
python lq.py journals lookup --journal "Nature Reviews Microbiology"
python lq.py journals lookup --journal "Nature Review Microbiology"   # 模糊匹配
python lq.py journals lookup --journal "Some Journal" --top-percent 10
```

输出里重点看三个字段：`matched`、`rank.basis`、`rank.approx`。
