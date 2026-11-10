# Web of Science 检索语法

适用于 Web of Science Core Collection 的 Advanced Search（本项目通过 `TS=` 构造查询）。Web of Science 与 Scopus 的语法**不通用**，最容易出错的三处：`{}`、`W/n`、`AND NOT`。

## 1. 字段标签

| 标签 | 含义 | 示例 |
|---|---|---|
| `TS=` | Topic：标题 + 摘要 + 作者关键词 + Keywords Plus（默认） | `TS=(gut microbiota)` |
| `TI=` | 标题 | `TI=(microbiome)` |
| `AB=` | 摘要 | `AB=(dysbiosis)` |
| `AK=` | 作者关键词 | `AK=(probiotic)` |
| `KP=` | Keywords Plus | `KP=(inflammation)` |
| `AU=` | 作者 | `AU=(Smith J*)` |
| `AI=` | 作者标识（ResearcherID/ORCID） | `AI=(0000-0001-2345-6789)` |
| `SO=` | 期刊名 | `SO=("Nature Reviews Microbiology")` |
| `DO=` | DOI | `DO=(10.1038/s41586-020-1234-5)` |
| `PY=` | 出版年 | `PY=(2018-2026)` |
| `DT=` | 文献类型 | `DT=(Review)` |
| `LA=` | 语言 | `LA=(English)` |
| `WC=` | WoS 学科分类 | `WC=(Microbiology)` |
| `SU=` | 研究方向 | `SU=(Life Sciences Biomedicine)` |
| `OG=` | 机构 | `OG=(Peking University)` |
| `AD=` | 地址 | `AD=(Beijing)` |
| `CU=` | 国家/地区 | `CU=(China)` |
| `ALL=` | 全字段 | `ALL=("gut brain axis")` |

**标签必须带等号**：`TS=(...)`。写成 `TS(...)` 会被判为语法错误（这是本项目 lint 能直接发现的错误之一）。

## 2. 布尔与括号

```text
AND   交集
OR    并集
NOT   排除
( )   分组
```

WoS 使用 `NOT`，**不写 `AND NOT`**。如需排除：

```text
TS=(microbiome AND depression) NOT TS=(animal OR mouse OR rat)
```

`NOT` 只作用于紧随其后的字段表达式；排除多个词要加括号。

## 3. 短语

WoS 只有双引号短语，**没有 `{}`**：

```text
TS=("gut microbiota")         短语（词序固定，允许有限词形变化）
TS=("gut microbiota" OR "intestinal microbiota")
```

- 引号内不要嵌套引号。
- 需要“精确到词形”时，WoS 没有对应语法，只能加更多同义词或改用 `NEAR`。
- 中文文献请切换 `LA=(Chinese)`；本项目默认 `LA=(English)`。

## 4. 邻近运算

| 写法 | 含义 |
|---|---|
| `NEAR/n` | 两词相距 n 个词以内，顺序不限 |
| `"a b"` | 紧邻短语（顺序固定） |
| `SAME` | 仅用于地址字段，表示同一地址内共现 |

```text
TS=(gut NEAR/5 microbiota)
TS=(gene NEAR/2 expression)
```

`n` 取 1–255。WoS **没有** `PRE/n`（有序邻近）；需要有序时用引号短语。

## 5. 通配符

| 符号 | 含义 | 例 |
|---|---|---|
| `*` | 0 个或多个字符 | `microb*` |
| `?` | 恰好 1 个字符 | `wom?n` |
| `$` | 0 个或 1 个字符 | `colo$r` 命中 color / colour |

规则：

- 一个词内只用一个 `*`。
- 词首 `*` 可用但慢，且容易触发“检索式过于宽泛”的提示。
- WoS 对 `*` 的位置敏感：`*biotic` 与 `biotic*` 结果不同。

## 6. 过滤

```text
PY=(2018-2026)
PY=2021
DT=(Review)
DT=(Article OR Review)
LA=(English)
WC=(Microbiology OR Immunology)
```

文献类型常见值：`Article`、`Review`、`Proceedings Paper`、`Book Chapter`、`Editorial Material`、`Early Access`、`Retracted Publication`。

- 结果页会显示 “Review Article”，但检索式里写 `DT=(Review)`。
- `DT=(Review)` 与 `DT=(Article)` 可能重叠（WoS 的 Review 是独立类型，不是 Article 的子集）；需要“全部综述”时只用 `DT=(Review)`。

## 7. 推荐骨架

```text
TS=(("gut microbiota" OR microbiome)
   AND (depression OR "depressive disorder"))
AND PY=(2018-2027)
AND DT=(Review)
AND LA=(English)
NOT TS=(animal OR mouse OR rat)
```

## 8. 结果数量与读数

- 结果页文案：`N results from Web of Science Core Collection for:`。
- `No results found` 表示 0。
- 未登录时可能只显示命中数、记录卡片为空；这与“0 条结果”不同，必须区分。
- 本项目 `count_from_text(..., "wos")` 优先匹配带 `results from Web of Science` 的完整句式，避免误读侧栏分面计数。

## 9. 与 Scopus 的对照

| 需求 | Scopus | Web of Science |
|---|---|---|
| 主题字段 | `TITLE-ABS-KEY(...)` | `TS=(...)` |
| 精确短语 | `{...}` | 无（用 `"..."`） |
| 宽松短语 | `"..."` | `"..."` |
| 无序邻近 | `W/n` | `NEAR/n` |
| 有序邻近 | `PRE/n` | 无 |
| 排除 | `AND NOT` | `NOT` |
| 综述 | `DOCTYPE(re)` | `DT=(Review)` |
| 年份 | `PUBYEAR > 2017` | `PY=(2018-2026)` |
| 通配 | `*` `?` | `*` `?` `$` |

## 10. 常见错误

| 错误 | 现象 | 修正 |
|---|---|---|
| `TS({a})` | 缺少等号，语法错误 | `TS=({a})` 或 `TS=("a")` |
| `TS=({gut microbiota})` | 花括号不被识别 | 改双引号 |
| `W/5` | 语法错误 | 改 `NEAR/5` |
| `AND NOT` | 语义/语法异常 | 改 `NOT` |
| `PY>2018` | 不稳定 | 改 `PY=(2019-2026)` |
| 把 `TS=` 写进 Scopus | Scopus 报错 | 改 `TITLE-ABS-KEY(...)` |
