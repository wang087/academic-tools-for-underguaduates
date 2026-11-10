# scopus-wos-lit

一个独立的文献调研工具包：**研究方向 → 权威综述 → 细分方向 → 细化主题检索式 → 文献清单**。面向 Scopus 与 Web of Science，包含可执行的 Python 工具、CDP 浏览器检索、期刊层级判定、综述筛选、Markdown 报告生成与离线测试。

> 本项目**不修改** `2/流程1/`。它借鉴该流程的分层获取、CDP 与报告契约经验，但代码、数据与产物完全独立。

## 1. 能力

| 能力 | 实现 |
|---|---|
| 方向匹配 + 英文关键词 | `assets/direction_taxonomy.json`（9 大类 / 85 个叶子方向） |
| Scopus / WoS 检索式生成与校验 | `query.py`（字段码、布尔、W/n、PRE/n、{}、?、*、过滤器） |
| 综述发现与筛选 | 前 20% 期刊 + 年龄感知被引 + 时效 + 奠基之作 |
| 摘要与全文 | Scopus 记录页 / OpenAlex / Crossref / Unpaywall / 浏览器 PDF |
| 翻译解读与细分方向 | `digest.tasks.json` + 校验 + 报告 |
| 检索不足重试 | 4 级扩检阶梯，逐级记录命中数 |
| 文献清单 | 标题、摘要、地址、一作、通讯、期刊、年份、被引 |
| 各领域综述汇编 | `scripts/field_reviews.py` + `assets/field_catalog.json`（16 个大类，每类 5 篇） |
| 浏览器 | 原生 CDP（无 Selenium/Playwright），单实例复用、导航节流 |

## 2. 快速开始

```bash
# 0) 环境自检
python scripts/lq.py doctor

# 1) 方向 → 关键词
python scripts/lq.py directions match --input "生物学 > 免疫与微生物 > 人体微生物组学" --year-from 2019 --out work/plan.json

# 2) 生成两套检索式
python scripts/lq.py query build --plan work/plan.json --engine both --out work/queries.json

# 3) 找权威综述（未登录 Scopus 也能读命中数/结果行/记录页）
python scripts/lq.py reviews discover --plan work/plan.json --source scopus --out work/reviews.selected.json

# 4) 取摘要与全文
python scripts/lq.py reviews fetch --input work/reviews.selected.json --outdir work/fulltext

# 5) 翻译解读任务 → 填写 → 校验 → 报告
python scripts/lq.py reviews digest-init --input work/reviews.selected.json --out work/digest.tasks.json
python scripts/lq.py reviews digest-check --tasks work/digest.tasks.json --filled work/digest.json
python scripts/lq.py reviews report --discover work/reviews.selected.json --digest work/digest.json --out work/02_权威综述.md
```

细化主题：

```bash
python scripts/lq.py query scaffold --topic-zh "主题" --topic-en "topic" \
  --concept "对象=depression|depressive disorder" \
  --concept "干预=probiotic|gut microbiota" \
  --out work/topic.plan.json
python scripts/lq.py search run --plan work/topic.plan.json --source scopus --min 15 --out work/search.run.json --md work/03_文献清单.md
```

各领域综述汇编（一次生成 16 个大类，每类 5 篇综述）：

```bash
python scripts/field_reviews.py --out work/fields
python scripts/field_reviews.py --out work/fields --only neuroscience,immunology
```

产物：`00_索引.md` + 每个领域一份 `*.md`（领域导读、综述清单、逐篇摘要、中文简要翻译与解读、DOI、一作/通讯）+ `data/*.json`。字段与关键词口径见 `assets/field_catalog.json`。

## 3. 环境

- Python 3.9+（开发环境 3.14），仅标准库；PDF 文本优先用 PyMuPDF，未安装则用 pypdf，两者都无则保留 PDF。
- 浏览器：Microsoft Edge 或 Chrome（CDP 模式）；必须在**非受限**环境启动（Chromium 需要命名管道）。
- 可选：`SCOPUS_API_KEY`、`SCOPUS_INST_TOKEN`、`WOS_API_KEY`。

`doctor` 的 `ok` 只看关键项（Python、词表、期刊表、浏览器可执行文件）；API key 与已启动的 CDP 端口是可选缺失。

## 4. 目录

```text
scopus-wos-lit/
  SKILL.md                  agent 使用手册（工作流、纪律、停止点）
  references/               语法、检索设计、期刊判定、筛选、CDP、契约、排错
  assets/
    direction_taxonomy.json 方向-关键词层级表（85 叶）
    journal_tiers.seed.json 期刊层级种子表（非官方分区）
    field_catalog.json      16 个大类的领域目录（用于批量综述汇编）
    selectors.example.json  CDP 选择器覆盖示例
  scripts/
    lq.py                   入口
    field_reviews.py        各领域综述汇编（OpenAlex 批量）
    litquery/               实现包
  tests/test_litquery.py    81 项离线测试
  examples/                 格式示例（虚构数据，明确标注）
```

## 5. 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：方向匹配、Scopus/WoS 检索式、lint、期刊判定、综述筛选、记录转换、命中数解析、报告、digest 校验、数字解析。测试不访问网络、不启动浏览器。

## 6. 数据与合规

- JCR 与中科院分区是商业授权数据，**不随本项目分发**；内置种子表只做保守兜底，报告中会标注 `approx`。
- 全文只从开放获取副本或用户自己的机构会话获取；付费墙记录保留 DOI，不绕过访问控制。
- 浏览器 profile 含登录 Cookie，已加入 `.gitignore`，不要提交或分发。
- 检索受数据库条款约束；不要高频轮询、不要并发轰炸。

## 7. 已知边界

- WoS 网页未登录时只保证命中数，不保证结果记录；用 `wos-api` 或 `--fallback openalex`。
- Scopus 未登录时结果导航有 2000 条上限，但 `documents found` 仍是库内命中数。
- 期刊层级依赖导入的数据；没有 JCR 百分位时无法做“严格前 20%”判定，只能给近似。
- 标题匹配 DOI 是启发式（相似度 ≥ 0.9 且年份 ±1），关键文献请以记录页 DOI 为准。

## 8. 作为 skill 安装

把整个目录复制到 agent 的 skills 目录（例如 `skills/scopus-wos-lit/`），确保 `SKILL.md` 在根层。命令中的 `python scripts/lq.py` 以项目根为工作目录即可；也可把 `scripts/` 加入 `PATH` 后直接 `python lq.py`。

## 9. 许可

代码 MIT。随附的方向词表与期刊种子表为手工整理，不含任何商业数据库的批量数据。
