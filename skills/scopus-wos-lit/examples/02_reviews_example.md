# 领域权威综述与细分方向

> 第二步产物：权威综述清单、摘要翻译与解读、细分研究方向

- 生成时间：2026-10-21T23:27:56+0800
- 生成工具：scopus-wos-lit / litquery

## 一、检索与筛选

| 项 | 内容 |
|---|---|
| 数据源 | scopus-web |
| 实际检索式 | 见本节末尾的代码块 |
| 查询哈希 | `ae8c468471f5fa969e5ea183f67b6a8fea3f812c7e81b797d7df950dcc541e18` |
| 库内命中 | 19941 |
| 候选记录 | 30 |
| 入选综述 | 2 |

```text
TITLE-ABS-KEY(({human microbiome} OR {human microbiota} OR microbiome OR microbiota OR {gut microbiome} OR metagenomics OR {microbial community} OR {host-microbe interaction})) AND PUBYEAR > 2018 AND DOCTYPE(re) AND (LANGUAGE(english))
```

| 排除原因 | 数量 |
|---|---|
| journal_not_top20 | 18 |
| not_a_review | 4 |
| older_than_6y | 3 |
| citations_12_below_floor_20 | 3 |

### 需要读者注意

- [WARN] 示例数据：仅用于展示报告格式，不是真实检索结果

## 二、入选综述

排序：综合分 = 期刊层级 50% + 被引 30% + 时效 20%（见 `screen.py`，逐条给出理由）。

| # | 年份 | 标题 | 期刊 | 期刊层级 | 被引 | 一作 | 通讯 |
|---|---|---|---|---|---|---|---|
| 1 | 2021 | [示例综述一：人体微生物组与宿主健康的机制框架](https://doi.org/10.0000/example.2021) | Nature Reviews Microbiology | JIF pct 98 | 512 | Jane Doe | John Smith |
| 2 | 2022 | [示例综述二：微生物代谢产物与免疫调节](https://doi.org/10.0000/example.2022) | Cell Host & Microbe | JIF pct 96 | 240 | Alex Lee | Alex Lee |

## 三、逐篇摘要翻译与解读

### 1. 示例综述一：人体微生物组与宿主健康的机制框架

| 项 | 内容 |
|---|---|
| 期刊 / 年份 | Nature Reviews Microbiology / 2021 |
| 期刊层级 | JIF pct 98 |
| 被引 | 512 |
| 一作 | Jane Doe |
| 通讯 | John Smith |
| DOI | https://doi.org/10.0000/example.2021 |
| 全文 | `fulltext/10.0000_example.2021/fulltext.pdf`（pdf） |

<details>
<summary>英文摘要（275 字符）</summary>

This fictional example abstract illustrates the report format. It summarises how microbial communities colonise host surfaces, how their metabolites signal to immune and neuronal cells, and which methodological limits constrain causal inference. No real findings are claimed.

</details>

**摘要翻译**

本示例摘要仅用于展示版式。综述梳理了微生物群落在宿主表面定植、其代谢产物向免疫与神经细胞传递信号的方式，并讨论了限制因果推断的方法学问题。文中不主张任何真实研究结论。

**解读**

- 研究问题：给出一个把微生物组、宿主屏障与免疫信号联系起来的机制框架。
- 证据基础：以机制实验与人群关联研究为主，随机干预证据有限。
- 主要结论：关联证据较一致，但因果链仍缺少干预验证。
- 局限：方法异质性高，暴露测量与混杂控制不足。

**本篇提示的细分方向**

- 微生物代谢产物与免疫细胞互作：综述用一节讨论代谢产物如何影响免疫细胞功能
- 定植部位差异与屏障功能：分别讨论不同宿主表面的群落与屏障特征

### 2. 示例综述二：微生物代谢产物与免疫调节

| 项 | 内容 |
|---|---|
| 期刊 / 年份 | Cell Host & Microbe / 2022 |
| 期刊层级 | JIF pct 96 |
| 被引 | 240 |
| 一作 | Alex Lee |
| 通讯 | Alex Lee |
| DOI | https://doi.org/10.0000/example.2022 |
| 全文 | 未获取（paywalled） |

<details>
<summary>英文摘要（233 字符）</summary>

This second fictional abstract exists only to demonstrate the layout. It describes short-chain fatty acids, bile acid derivatives and tryptophan metabolites as candidate mediators, and stresses that most evidence remains associative.

</details>

**摘要翻译**

本示例摘要仅用于展示版式。综述把短链脂肪酸、胆汁酸衍生物与色氨酸代谢物列为候选介质，并强调多数证据仍是关联性的，干预研究数量有限且随访较短。

**解读**

- 研究问题：梳理微生物代谢产物调节宿主免疫的证据。
- 证据基础：体外与动物实验为主，人群干预研究较少。
- 主要结论：多条代谢通路显示免疫调节作用，但效应量不稳定。
- 局限：代谢物测量平台不统一，因果方向难以确定。

**本篇提示的细分方向**

- 短链脂肪酸与免疫耐受：综述将短链脂肪酸作为主要介质之一
- 代谢物测量的方法学差异：比较了不同检测平台与样本处理的影响

## 四、细分研究方向总览

| 细分方向 | 支撑综述 | 说明 |
|---|---|---|
| 微生物代谢产物与免疫细胞互作 | 示例综述一; 示例综述二 | 两篇综述都把它作为主线之一 |
| 定植部位差异与屏障功能 | 示例综述一 | 单篇支撑，证据等级有限 |
| 代谢物测量的方法学差异 | 示例综述二 | 方法学分支，可作为独立问题 |

## 五、领域概览（中文）

示例概览：两篇虚构综述分别提供机制框架与代谢物证据的梳理。前者强调宿主-微生物互作的整体结构，后者聚焦具体代谢介质与免疫调节。二者共同指向“关联证据较多、干预证据不足”的现状，并把方法学异质性列为主要局限。该概览仅用于展示报告结构，不代表任何真实领域判断。示例文本继续补足长度，以便通过 digest-check 的 200 字符下限校验，同时不引入任何具体研究结论或数字。报告使用者应把这段文字视为版式占位，而不是领域结论。

## 六、需要你确认

[STOP] 请先阅读以上综述与细分方向，确认无误后给出一个细化研究主题（例如“某干预对某人群某结局的影响”）。
在你确认之前，不进入检索式细化与文献列表阶段。

[WARN] 本次仅入选 2 篇（少于建议的 5 篇），覆盖可能不足。
