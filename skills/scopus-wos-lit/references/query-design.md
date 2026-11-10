# 检索式设计

目标不是“写一个看起来很专业的检索式”，而是让**每一次命中数变化都可解释**。本项目把检索式拆成概念块（concept block），每块的增删都有记录。

## 1. 从主题到概念块

### 1.1 拆块

| 主题类型 | 推荐块 |
|---|---|
| 干预/暴露 → 结局 | 人群（可选）+ 暴露/干预 + 结局 |
| 机制研究 | 对象/分子 + 过程 + 疾病/表型 |
| 方法学研究 | 方法 + 应用场景 + 评价指标 |
| 方向级调研 | 只用一个“核心主题”块（求全，不求精） |

规则：

- **每块 3–12 个词项**；过少会漏，过多会引入噪声。
- 块与块之间 `AND`，块内 `OR`。
- 方向级调研（阶段 1–2）只用一个 core 块，避免把“方向”误做成“四个约束的交集”。这是本项目 `taxonomy` 的默认行为。
- 细化主题（阶段 4）才使用多块 `AND`。

### 1.2 每块要写全五类词

| 类型 | 作用 | 例（以“运动干预与认知”为例） |
|---|---|---|
| 主词 | 最常用的英文写法 | `exercise` |
| 同义词 | 同义不同写 | `physical activity`、`training` |
| 相关概念 | 邻近但不等价 | `aerobic fitness`、`sedentary behaviour` |
| 上位词 | 更宽（谨慎，易稀释） | `lifestyle intervention` |
| 下位词 | 更窄（谨慎，易漏） | `high-intensity interval training` |

默认只启用「主词 + 同义词」；上位词用 `--with-broader` 显式开启，下位词用 `--with-narrower`。**不要默认把上下位词全开**，否则“抑郁”会命中所有情绪障碍。

### 1.3 中文词的英文化

- 中文概念先拆成“核心名词 + 限定语”，再逐段找英文对应，不要整句机翻。
- 同一中文词可能有多个英文分野：`菌群` → `microbiota` / `microbiome` / `flora`（`flora` 老式，少量保留）。
- 缩写与全称都写：`fecal microbiota transplantation` / `FMT`。
- 英式/美式拼写都写：`behaviour` / `behavior`，`randomised` / `randomized`。
- 连字符变体：`gut-brain axis` / `gut brain axis`（Scopus 对连字符敏感，必要时两条都放）。

## 2. 连接与运算

### 2.1 布尔

```text
(block1) AND (block2) AND (block3)
```

- Scopus 排除：`AND NOT TITLE-ABS-KEY((...))`
- WoS 排除：`NOT TS=((...))`

### 2.2 短语：什么时候用精确，什么时候用邻近

| 情况 | 选择 |
|---|---|
| 固定术语、拼写稳定 | 精确短语（Scopus `{}` / WoS `"..."`） |
| 词序可能变化（`microbiota-gut-brain`） | 邻近（`W/3`、`NEAR/3`） |
| 两词间常插入修饰语 | `W/5`–`W/8` |
| 需要严格顺序 | Scopus `PRE/n`；WoS 用引号短语 |

经验：**先用精确短语**，命中不足再逐级改邻近。反向操作（先邻近再收紧）会让重试记录难以解释。

### 2.3 通配

只在“词根稳定、派生多”时使用：

- 适合：`microb*`、`probiotic*`、`pathogen*`、`inflam*`
- 不适合：`therap*`（漏 treatment）、`disease*`（过宽）、`*biotic`（词首通配）

## 3. 扩检与缩检

### 3.1 命中太少（< 15）

按顺序：

1. 加同义词、缩写、英美拼写。
2. 精确短语 → 邻近（`W/4`）。
3. 打开上位词（`--with-broader`）。
4. 去掉最不关键的概念块（通常是“方法/机制”块）。
5. 年份窗口放宽 3 年。
6. 去掉文献类型/语言过滤。
7. 换库（Scopus ↔ WoS ↔ OpenAlex）。

每一步都要记录命中数；不允许“悄悄换一个更宽的检索式然后说这是原检索式的结果”。

### 3.2 命中太多（> 2000）

1. 收紧短语（邻近 → 精确）。
2. 增加一个概念块（如人群、疾病亚型）。
3. 加 `AND NOT` 排除明显无关领域。
4. 限定 `DOCTYPE`、年份、语言。
5. 用 `TITLE()` 替代 `TITLE-ABS-KEY()`（最激进）。

## 4. 排除词

好的排除词：明确不属于研究问题的对象或方法。

```text
animal OR mouse OR rat OR zebrafish        体内动物实验（若只做人）
case report OR editorial OR comment        非原始研究（若只要原始研究）
pediatric OR child                         非目标人群（若只做成人）
```

坏的排除词：`review`、`study`、`human`、`disease`——会误删大量相关记录，或与检索目标冲突。

## 5. 检索式自检清单

- [ ] 每块至少 3 个词项，且含同义词。
- [ ] 块内 `OR` 都有括号包住。
- [ ] Scopus/WoS 语法没有混用（`{}`、`W/n`、`AND NOT`）。
- [ ] 邻近距离与短语策略一致。
- [ ] 通配符只出现在词根稳定的词上。
- [ ] 排除词不与纳入概念冲突。
- [ ] `query lint` 无 error。
- [ ] 实际命中数已记录，且与检索式 SHA-256 绑定。

## 6. 示例（细化主题）

主题：某干预对某人群某结局的影响。

```text
# Scopus
TITLE-ABS-KEY((
    (probiotic OR {fecal microbiota transplantation} OR {gut microbiota})
    AND (depression OR {depressive disorder} OR {depressive symptoms})
))
AND PUBYEAR > 2017
AND LANGUAGE(english)
```

```text
# Web of Science
TS=(("probiotic" OR "fecal microbiota transplantation" OR "gut microbiota")
   AND ("depression" OR "depressive disorder" OR "depressive symptoms"))
AND PY=(2018-2027)
AND LA=(English)
```

命中不足时的下一版（示意）：

```text
TITLE-ABS-KEY((
    (probiotic* OR microbiota OR microbiome)
    AND (depress* OR {mood disorder})
))
AND PUBYEAR > 2014
```

注意：`depress*` 会命中 `depression`、`depressive`、`depressed`，也会命中 `depressor` 等少量噪声；这是可接受的扩检代价，但必须在重试记录里写明。

## 7. 常见设计错误

1. 把所有同义词、上下位词一次性全开，命中数虚高且不可解释。
2. 方向级调研就做多块 `AND`，候选综述被“方法词”卡死。
3. 用 `AND` 连接本该 `OR` 的同义词（`microbiome AND microbiota` 几乎为 0）。
4. 排除词把 `review` 排掉，却又要找综述。
5. 只给中文检索词，指望数据库自动翻译。
6. 不记录每次重试的检索式与命中数，最后无法复现。
