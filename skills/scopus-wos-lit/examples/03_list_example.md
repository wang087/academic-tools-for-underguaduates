# 文献与综述列表

> 第三步产物：细化主题检索结果（标题、摘要、地址、一作与通讯）

- 生成时间：2026-10-21T23:27:56+0800
- 生成工具：scopus-wos-lit / litquery

## 一、检索结果概况

| 项 | 内容 |
|---|---|
| 数据源 | scopus-web |
| 最终检索式 | `TITLE-ABS-KEY(({probiotic} OR {gut microbiota}) AND (depression OR {depressive disorder})) AND PUBYEAR > 2017 AND LANGUAGE(english)` |
| 库内命中 | 312 |
| 取回记录 | 3 |
| 检索状态 | ok |

| 尝试 | 说明 | 命中 | 是否采用 |
|---|---|---|---|
| A_strict | strict: synonyms on, exact phrases, all filters | 312 | [YES] |
| B_truncation | add truncation and broader terms | 901 |  |

## 二、文献清单

| # | 年份 | 标题 | 期刊 | 一作 | 通讯 | 被引 |
|---|---|---|---|---|---|---|
| 1 | 2021 | [示例综述一：人体微生物组与宿主健康的机制框架](https://doi.org/10.0000/example.2021) | Nature Reviews Microbiology | Jane Doe | John Smith | 512 |
| 2 | 2022 | [示例综述二：微生物代谢产物与免疫调节](https://doi.org/10.0000/example.2022) | Cell Host & Microbe | Alex Lee | Alex Lee | 240 |
| 3 | 2023 | [示例研究论文：某干预对某人群结局的影响](https://doi.org/10.0000/example.2023) | Example Journal of Clinical Research | Chris Wu | Eve Chen | 37 |

## 三、逐篇信息

### 1. 示例综述一：人体微生物组与宿主健康的机制框架

- 期刊 / 年份：Nature Reviews Microbiology / 2021
- 一作：Jane Doe
- 通讯：John Smith
- 地址：https://doi.org/10.0000/example.2021
- DOI：10.0000/example.2021
- 来源库：scopus-web

**摘要**

This fictional example abstract illustrates the report format. It summarises how microbial communities colonise host surfaces and how their metabolites signal to immune cells.

### 2. 示例综述二：微生物代谢产物与免疫调节

- 期刊 / 年份：Cell Host & Microbe / 2022
- 一作：Alex Lee
- 通讯：Alex Lee
- 地址：https://doi.org/10.0000/example.2022
- DOI：10.0000/example.2022
- 来源库：scopus-web

**摘要**

This second fictional abstract exists only to demonstrate the layout. It describes short-chain fatty acids and bile acid derivatives as candidate mediators.

### 3. 示例研究论文：某干预对某人群结局的影响

- 期刊 / 年份：Example Journal of Clinical Research / 2023
- 一作：Chris Wu
- 通讯：Eve Chen
- 地址：https://doi.org/10.0000/example.2023
- DOI：10.0000/example.2023
- 来源库：scopus-web

**摘要**

A fictional primary study abstract used to show how an article (not a review) is rendered in the list.

## 四、方法说明与局限

- 命中数来自所选数据源页面或 API 返回值，记录在 `attempts` 中，可复核。
- 摘要优先取 Scopus 记录页；缺失时依次用 OpenAlex / Crossref / Scopus Abstract API 补全。
- 通讯作者优先取 OpenAlex 的 `is_corresponding`；缺失时以末位作者推断并在字段中标注 `(inferred)`。
- 期刊层级来自用户导入的 JCR/中科院表；未导入时使用内置种子表的“中科院一区”粗判，属近似判定。
