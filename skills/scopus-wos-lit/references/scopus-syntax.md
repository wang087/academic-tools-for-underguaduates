# Scopus 检索语法

本文件只写**本项目实际使用并验证过**的语法。所有示例都可直接放进 `query lint --engine scopus` 校验。

## 1. 字段码

| 字段码 | 含义 | 示例 |
|---|---|---|
| `TITLE-ABS-KEY(...)` | 标题 + 摘要 + 作者关键词（默认字段） | `TITLE-ABS-KEY({gut microbiota})` |
| `TITLE(...)` | 仅标题 | `TITLE(microbiome)` |
| `ABS(...)` | 仅摘要 | `ABS(dysbiosis)` |
| `KEY(...)` | 仅作者关键词 | `KEY(probiotic)` |
| `AUTHKEY(...)` | 作者关键词（同 KEY，兼容写法） | `AUTHKEY(inflammation)` |
| `AUTH(...)` | 作者名 | `AUTH("Smith, J.")` |
| `AFFIL(...)` | 机构 | `AFFIL(("Peking University"))` |
| `AFFILCOUNTRY(...)` | 机构国家 | `AFFILCOUNTRY(China)` |
| `SRC-TITLE(...)` | 期刊名 | `SRC-TITLE("Nature Reviews Microbiology")` |
| `SRCTYPE(...)` | 来源类型：`j` 期刊 / `b` 图书 / `p` 会议 | `SRCTYPE(j)` |
| `DOI(...)` | DOI | `DOI(10.1038/s41586-020-1234-5)` |
| `PUBYEAR` | 出版年（比较运算符） | `PUBYEAR > 2017` |
| `DOCTYPE(...)` | 文献类型 | `DOCTYPE(re)` |
| `LANGUAGE(...)` | 语言 | `LANGUAGE(english)` |
| `SUBJAREA(...)` | 学科大类代码 | `SUBJAREA(IMMU)` |
| `ISSN(...)` / `CODEN(...)` | 刊号 | `ISSN(1740-1526)` |
| `PUBLISHER(...)` | 出版商 | `PUBLISHER(Elsevier)` |
| `FUND-SPONSOR(...)` | 基金赞助方 | `FUND-SPONSOR(NSFC)` |
| `ALL(...)` | 全字段 | `ALL("gut brain axis")` |

字段码区分大小写不敏感；**字段码用圆括号，不加等号**（`TS=` 是 WoS 的写法，写错会报语法错误）。

## 2. 布尔与括号

```text
AND   交集
OR    并集
AND NOT  排除（Scopus 必须写 AND NOT，不能只写 NOT）
( )   分组，可嵌套
```

示例：

```text
TITLE-ABS-KEY((microbiome OR microbiota) AND depression)
TITLE-ABS-KEY(microbiome AND depression AND NOT animal)
```

Scopus 官方在 2025 年系统升级后声明**没有显式的布尔运算符数量或查询长度上限**，但过度嵌套、深度嵌套邻近运算、错误字段码会被判为过于复杂而拒绝执行。实践上限：单条查询 ≤ 40 个布尔连接符、嵌套 ≤ 4 层、长度 ≤ 4000 字符（`lint` 会在超长时告警）。

## 3. 短语：精确 vs 宽松

| 写法 | 含义 | 行为 |
|---|---|---|
| `{gut microbiota}` | 精确短语 | 词形、词序固定，不自动加复数/词形变化 |
| `"gut microbiota"` | 宽松短语 | 允许词形变化与停用词插入 |

例：

```text
TITLE-ABS-KEY({gut microbiota})        exact
TITLE-ABS-KEY("gut microbiota")        loose
TITLE-ABS-KEY({gut microbiota} OR {intestinal microbiota})
```

`{}` 内不要出现 `{}`；短语里含 `?`/`*` 时按通配处理，不再保证“精确”。

## 4. 邻近运算

| 写法 | 含义 | 例 |
|---|---|---|
| `W/n` | 两词相距 n 个词以内，**顺序不限** | `gut W/3 microbiota` |
| `PRE/n` | 前词在**后词之前** n 个词以内 | `gut PRE/3 microbiota` |

`n` 取值 1–255。`W/n` 只连接两个词，不能直接连接两个短语；多词短语请用 `{}` 或拆成词再用多个 `W/n`。

实践：`W/3`–`W/8` 适合“同一句内共现”，`PRE/3` 适合固定搭配（如 `gene PRE/2 expression`）。`W/1` 已接近相邻，容易误伤，除非确认拼写。

## 5. 通配符

| 符号 | 含义 | 例 |
|---|---|---|
| `*` | 0 个或多个字符 | `microb*` 命中 microbiome / microbiota / microbial |
| `?` | 恰好 1 个字符 | `wom?n` 命中 woman / women |

规则与坑：

- 一个词内**只用一个 `*`**；多个 `*` 会拖慢并降低可解释性，`lint` 会告警。
- 词首 `*`（如 `*biotic`）语法可用但很慢、精度低；`lint` 会告警。
- `*` 不能代替同义词：`therapy*` 不会命中 `treatment`。
- 精确短语 `{...}` 内使用 `*` 会转为通配匹配，失去“精确”语义。

## 6. 过滤条件

```text
PUBYEAR > 2017                 2018 年及以后
PUBYEAR > 2017 AND PUBYEAR < 2027
DOCTYPE(re)                    综述
DOCTYPE(ar)                    期刊论文
DOCTYPE(ip)                    在印论文
DOCTYPE(cp)                    会议论文
DOCTYPE(ch)                    图书章节
LANGUAGE(english)
SRCTYPE(j)                     仅期刊
```

`DOCTYPE` 组合：

```text
(DOCTYPE(re) OR DOCTYPE(ar))
```

`DOCTYPE` 代码：`ar` 期刊论文、`re` 综述、`ip` 在印、`cp` 会议论文、`ch` 图书章节、`ed` 社论、`le` 快报、`no` 短文、`sh` 调查。

## 7. 排除词

```text
TITLE-ABS-KEY(microbiome AND depression) AND NOT TITLE-ABS-KEY(animal)
```

- 必须写 `AND NOT`，并在其后重新给字段码。
- 多个排除词：`AND NOT TITLE-ABS-KEY((animal OR mouse OR rat))`。
- 排除词不要用宽泛词（`review`、`study`、`human`），会误删大量相关记录。

## 8. 年份/类型组合的推荐骨架

```text
TITLE-ABS-KEY((
    ({gut microbiota} OR microbiome)
    AND (depression OR {depressive disorder})
))
AND PUBYEAR > 2017
AND DOCTYPE(re)
AND LANGUAGE(english)
AND NOT TITLE-ABS-KEY((animal OR mouse OR rat))
```

## 9. 结果数量与读数

- 结果页文案为 `N documents found`（本项目正则同时兼容 `N document results`）。
- `No documents found` 表示 0；**页面没出现计数**时是解析失败，不是 0。
- 未登录/未导出时结果导航常见 `results 1 through 10 of 2000` 的上限提示，**2000 是界面导航上限，不是库内总量**；以 `documents found` 为准。
- 命中数受检索式、字段、年份、类型、语言共同影响；报告里必须给出完整检索式与各过滤条件。

## 10. 常见错误

| 错误 | 现象 | 修正 |
|---|---|---|
| `TS=(...)` | 语法错误 | 改用 `TITLE-ABS-KEY(...)` |
| `NOT (...)` | 语法错误/语义不明 | 改 `AND NOT` |
| `{a} AND {b}` 写成 `{a AND b}` | 精确短语里含布尔词 | 拆开 |
| `W/3` 连接短语 | 结果异常 | 用 `{}` 或拆词 |
| 词首 `*` | 慢、噪声大 | 改后缀截断 |
| 字段码后加 `=` | 语法错误 | 去掉 `=` |
