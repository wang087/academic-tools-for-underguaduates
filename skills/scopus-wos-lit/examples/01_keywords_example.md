# 研究方向关键词与检索式

> 第一步产物：方向匹配 + 英文关键词底座 + 可执行检索式

- 生成时间：2026-10-21T23:27:56+0800
- 生成工具：scopus-wos-lit / litquery

## 一、方向命中

| 项 | 内容 |
|---|---|
| 输入 | microbiome |
| 层级路径 | 生物学 > 免疫与微生物 > 人体微生物组学 |
| 英文名 | Human Microbiome |
| 匹配方式 | contains（置信度 0.97） |


## 二、英文关键词（方向级，未细分）

| # | English keyword |
|---|---|
| 1 | human microbiome |
| 2 | human microbiota |
| 3 | microbiome |
| 4 | microbiota |
| 5 | gut microbiome |
| 6 | metagenomics |
| 7 | microbial community |
| 8 | host-microbe interaction |

## 三、概念块

| 概念 | 角色 | 主词 | 同义词 | 上位词 | 下位词 |
|---|---|---|---|---|---|
| 核心主题（方向级关键词并集） | core | human microbiome; human microbiota; microbiome; microbiota; gut microbiome; metagenomics; microbial community; host-microbe interaction |  |  |  |
| 定植部位 | optional | oral microbiome; skin microbiome; vaginal microbiome; lung microbiome |  |  |  |
| 方法 | optional | 16S rRNA sequencing; shotgun metagenomics; metabolomics; metatranscriptomics |  |  |  |
| 宿主互作 | optional | microbial colonization; dysbiosis; immune maturation |  |  |  |

## 四、检索式

### scopus

```text
TITLE-ABS-KEY(({human microbiome} OR {human microbiota} OR microbiome OR microbiota OR {gut microbiome} OR metagenomics OR {microbial community} OR {host-microbe interaction})) AND PUBYEAR > 2018 AND DOCTYPE(re) AND (LANGUAGE(english))
```

- 概念块 1 个 / 词项 8 个 / 布尔运算符 10 个 / 邻近运算符 0 个

### wos

```text
TS=(("human microbiome" OR "human microbiota" OR microbiome OR microbiota OR "gut microbiome" OR metagenomics OR "microbial community" OR "host-microbe interaction")) AND PY=(2019-2027) AND DT=(Review) AND LA=(English)
```

- 概念块 1 个 / 词项 8 个 / 布尔运算符 10 个 / 邻近运算符 0 个

## 五、下一步

- 以本检索式在 Scopus / Web of Science 检索权威综述，并执行 `reviews discover`。
- 方向级关键词不再向下细分；细分方向在读完综述后归纳。
