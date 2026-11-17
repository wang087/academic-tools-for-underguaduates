---
name: scopus-wos-literature
description: 用 Scopus / Web of Science 完成“研究方向 → 权威综述 → 细分方向 → 细化主题检索式 → 文献清单”的全流程。当用户给出学科方向（如生物学下的某个三级方向）、要求查找该领域权威综述、翻译并解读摘要、归纳细分研究方向，或要求在确认主题后生成中英文同义词/上下位词并用 Scopus/WoS 语法（OR、AND、AND NOT、W/n、PRE/n、{}、?、*）检索文献时使用。也适用于仅需 Scopus/WoS 命中数核对、期刊分区判定、一作/通讯作者提取的场景。
allowed-tools: Read Write Bash(python)
license: MIT
compatibility: Python 3.9+；需要 Microsoft Edge 或 Chrome 以启用 CDP 网页检索（Scopus/WoS 订阅由用户浏览器会话提供）；可选设置 SCOPUS_API_KEY / WOS_API_KEY 走官方 API。无需安装第三方 Python 包。
metadata:
  version: "1.0.0"
  project: scopus-wos-lit
---

# Scopus / Web of Science 文献工作流

本 skill 把一条研究方向的文献调研拆成五个可验收的阶段。每个阶段都有确定的命令、确定的产物和明确的停止点。**不得跳阶段，不得用估计值代替检索结果。**

## 0. 三条硬约束

1. **[PROHIBIT] 不得编造。** 命中数必须来自 Scopus/WoS 页面或 API 返回；摘要必须来自数据库/出版商/OpenAlex 等可溯源字段；拿不到就写“未取得”，并说明原因。`count=None`（页面没解析出来）与 `count=0`（确实没有结果）是两件事。
2. **[PROHIBIT] 不得反复启停浏览器或反复重载结果页。** 浏览器启动一次后全程复用同一个 CDP 实例；同一查询不重复导航；翻页是必要操作，重载不是。默认导航间隔 5 秒，遇到验证码立即停下报告，不要硬刷。
3. **[PROHIBIT] 不得改动 `2/`。** 本项目是独立工程，只借鉴其分层获取、CDP 与报告契约的经验；所有代码、产物、日志都写在 `scopus-wos-lit/` 与用户工作目录内。

## 1. 何时使用

| 场景 | 使用 |
|---|---|
| 用户给一个研究方向，要该领域权威综述 | [YES] |
| 要先把综述读懂，再归纳细分方向 | [YES] |
| 用户确认了细化主题，要中英文检索词与可执行检索式 | [YES] |
| 只查某篇论文的 DOI / 被引 / 一作通讯 | [YES]（可只用 `reviews fetch` 或 `journals lookup`） |
| 需要全库穷尽检索、系统综述 PRISMA 全流程 | 不适用；本 skill 是“方向调研 + 代表性检索”，不是系统综述 |
| 没有 Scopus/WoS 访问权，也不接受 OpenAlex 替代 | 先说明限制；可只用 OpenAlex 走通流程，但报告必须标注数据源 |

## 2. 首次准备

### 2.1 目录约定

```text
<WORK>/                        用户工作目录（每次任务一个）
  .work_lq/                    缓存、count_cache.json、lq.jsonl、浏览器 profile
  fulltext/                    综述全文与 manifest
  01_方向关键词.md
  02_权威综述.md
  03_文献清单.md
  plan.json                    方向关键词计划
  reviews.selected.json        入选综述（含筛选理由）
  digest.tasks.json            翻译/解读任务
  digest.json                  agent 填写后的翻译/解读
  search.run.json              细化主题检索结果
```

### 2.2 期刊分区数据（决定“前 20%”是否可信）

内置 `assets/journal_tiers.seed.json` 只是**保守种子表**：它把公认顶级刊标为 `cas_tier=1`，属近似判定，不是官方分区。

要得到严格的“JCR 前 20%”，先导入官方数据（用户自行取得，商业授权数据不随本项目分发）：

```bash
python lq.py journals import --csv jcr.csv --format jcr --out journal_tiers.json
python lq.py journals import --csv cas.csv --format cas --out journal_tiers.json
python lq.py journals stats --table journal_tiers.json
```

`journal_tiers.json` 放在 `<WORK>/` 下会被自动读取；也可用 `--journal-table` 指定。JCR CSV 需含 `Journal name / ISSN / JIF Percentile / JIF Quartile` 列（列名大小写与常见变体均可识别）。

### 2.3 访问方式

按可靠性从高到低：

1. `--source scopus-api` / `wos-api`：设置 `SCOPUS_API_KEY`、`WOS_API_KEY`。最稳，优先。
2. `--source scopus`：浏览器会话（Scopus 未登录也能读命中数、结果行、记录页 DOI 与摘要）。
3. `--source wos`：浏览器会话。未登录时**只能读命中数**，结果记录为空，必须 `--fallback openalex` 或改用 API。
4. `--source openalex`：无凭据兜底，能出记录，但期刊分区仍需本地表判定，报告中必须标注。

## 3. 工作流

### 阶段 1：方向 → 英文关键词（不细分方向）

输入可以是一句话、中文路径或英文方向名。先匹配内置层级表（85 个叶子方向）：

```bash
python lq.py directions match --input "生物学 > 免疫与微生物 > 人体微生物组学" --year-from 2019 --out plan.json
```

产物 `plan.json` 含：命中路径、8–40 个英文关键词、一个 `role=core` 的主概念块、若干 `role=optional` 的上下文块（默认不参与检索）、综述筛选过滤器。

规则：

- **只做方向级关键词**，此时不生成细分方向。细分方向必须来自随后读到的综述。
- 若匹配到的是上位大类（有子节点而没有自己的关键词），工具会用子方向关键词的并集，并在 `notes` 里提示；此时应建议用户给出更具体的方向，或接受较宽的检索。
- 未命中词表时 `matched=false`，`concepts=[]`。**不要拿空词表去检索**；由 agent 依据领域知识补 concepts（见阶段 4 的 `query scaffold`）。
- 叶节点可带 `scope_terms`（范围词），只用于发现阶段剔除跨领域记录，**不进入检索式**；没有 scope_terms 的方向不做范围过滤。
- agent 可以补同义词/上位词/下位词，但必须写回 `plan.json`，不得只在对话里口头补充。

生成并校验检索式：

```bash
python lq.py query build --plan plan.json --engine both --out queries.json
```

产物：Scopus 与 WoS 两套检索式、SHA-256、lint 结果。Scopus 默认 `TITLE-ABS-KEY(...)`，WoS 默认 `TS=(...)`；短语默认精确（Scopus `{...}` / WoS `"..."`）。

### 阶段 2：权威综述（前 20% + 高被引 + 时效）

```bash
python lq.py reviews discover --plan plan.json --source scopus --top-percent 20 --limit 8 --candidates 30 --max-attempts 3 --out reviews.selected.json
```

筛选规则（`scripts/litquery/screen.py`，逐条写入 `selection.reasons`）：

| 维度 | 默认 | 说明 |
|---|---|---|
| 期刊层级 | JCR 百分位 ≥ 80（前 20%） | 无百分位时用中科院一区粗判，标 `approx`；`Q1` 不等于前 20%，需 `--accept-q1` 显式放宽 |
| 被引 | 年龄感知下限：20 篇 × min(1, 年龄/5)，最低 5 | 新综述不被“引用还没攒够”误杀 |
| 时效 | 近 6 年 | 超龄但总被引 ≥ 400 或年均 ≥ 40 视为奠基之作保留，并加 `foundational` 标记 |
| 类型 | 综述 / Meta 分析 | `require_review=true` |
| 主题相关性 | 标题命中核心词，或摘要命中 ≥2 个不同核心词 / ≥3 次提及 | 剔除“高被引但只是顺带提到”的综述 |
| 范围词 | 方向节点可带 `scope_terms`（如 human/patient/gut），要求命中 ≥1 | 剔除跨领域漂移（例如植物/环境微生物组） |
| 排序 | 层级 50% + 被引 30% + 时效 20% | 分数与三项分项都写入 JSON |

若入选少于 5 篇，报告会给出 `warnings`。此时按顺序尝试：放宽 `--max-age`、降低 `--min-citations`、`--rank-policy balanced`、改用 `--source openalex` 补候选。**任何放宽都要在报告里写明。**

实现细节（影响结果稳定性，务必知道）：

- Scopus 网页每页固定 10 条；翻页用页面上的 `Next` 按钮（`next_page()`），不靠 `page=` 参数，也不重载页面。
- 结果按“被引最高”排序（`cp-f`）；10 条/页 × 指定页数构成候选池（`--candidates` 是上限，实际受页数限制）。
- 方向节点带 `scope_terms` 时，候选必须命中至少一个范围词，否则记 `out_of_scope` 拒绝。
- 入选综述若 OpenAlex 无摘要，先查 Europe PMC（无需浏览器），再用 Scopus 记录页补齐（最多 8 篇）；两者都拿不到可用摘要的综述会被排除并记 `abstract_unavailable`，除非显式 `--allow-no-abstract`。
- 若 Scopus 对结果 URL 强制 SSO，工具会自动从主页重发一次查询（主页游客检索框）；主页也跳登录才报 `login`，不循环刷新。
- 摘要会做“是否像正文”的校验：只有化学编号列表、参考文献碎片等段落会被拒绝，避免把页面噪声当摘要翻译。

取摘要与全文：

```bash
python lq.py reviews fetch --input reviews.selected.json --outdir fulltext
```

全文路线：OpenAlex/Unpaywall/Europe PMC 的 OA 副本 → 浏览器打开 DOI 页读 `citation_pdf_url` 并用该会话 Cookie 下载 → 都失败则记 `paywalled` 并保留 DOI 链接。每条记录的 `fulltext.status`、路径、字符数都在 `fulltext/fulltext_manifest.json` 中。

### 阶段 3：翻译、解读、细分方向（确认门）

先生成任务单：

```bash
python lq.py reviews digest-init --input reviews.selected.json --out digest.tasks.json
```

agent 逐篇填写 `digest.tasks.json` 并另存 `digest.json`：

```json
{
  "reviews": [
    {
      "id": "doi:10.xxxx/xxxx",
      "abstract_zh": "摘要的完整中文翻译，不是要点摘录",
      "interpretation": ["研究问题与范围", "方法与证据类型", "主要结论", "局限与争议"],
      "sub_directions": [{"name": "由本文归纳的细分方向", "evidence": "支撑它的原文要点"}]
    }
  ],
  "sub_direction_map": [{"name": "跨综述归纳的细分方向", "reviews": ["标题或 DOI"], "note": "为什么成立"}],
  "summary_zh": "领域概览，200 字以上"
}
```

校验并出报告：

```bash
python lq.py reviews digest-check --tasks digest.tasks.json --filled digest.json
python lq.py reviews report --discover reviews.selected.json --digest digest.json --out 02_权威综述.md
```

报告结构：检索与筛选 → 入选表 → 逐篇（英文摘要折叠、中文翻译、解读、本篇提示的细分方向）→ 细分方向总览 → **确认门**。

**[STOP] 到此必须停。** 把 `02_权威综述.md` 交给用户阅读，等待用户确认并给出细化研究主题。用户没确认前，不得进入阶段 4。

### 阶段 4：细化主题 → 检索词与检索式

用户给出细化主题后，agent 负责生成：

- 中文同义词、英文同义词
- 相关概念
- 上位词、下位词
- 常见英文检索词
- 排除词（如动物实验、非目标人群）

写成新的 plan（不要改方向级 plan）：

```bash
python lq.py query scaffold --topic-zh "细化主题" --topic-en "refined topic" \
  --concept "研究对象=depression|depressive disorder" \
  --concept "干预=gut microbiota|probiotic|fecal microbiota transplantation" \
  --concept "结局=depressive symptoms|emotion" \
  --synonym "研究对象=major depressive disorder" \
  --year-from 2018 --out topic.plan.json
```

然后：

```bash
python lq.py query build --plan topic.plan.json --engine both --out topic.queries.json
python lq.py query lint --engine scopus --file topic_scopus.txt
```

语法要点（完整表见 `references/scopus-syntax.md`、`references/wos-syntax.md`）：

| 需求 | Scopus | Web of Science |
|---|---|---|
| 精确短语 | `{gut microbiota}` | `"gut microbiota"` |
| 宽松短语 | `"gut microbiota"` | `"gut microbiota"` |
| 无序邻近 | `gut W/5 microbiota` | `gut NEAR/5 microbiota` |
| 有序邻近 | `gut PRE/3 microbiota` | 无；用精确短语 |
| 词根 | `microb*` | `microb*` |
| 单字符 | `wom?n` | `wom?n` |
| 排除 | `AND NOT` | `NOT` |
| 综述 | `DOCTYPE(re)` | `DT=(Review)` |
| 年份 | `PUBYEAR > 2017` | `PY=(2018-2026)` |

[PROHIBIT] 不要把 Scopus 的 `{}` 写进 WoS，也不要把 `W/n` 写进 WoS；lint 会直接报错。

### 阶段 5：检索、数量不足重试、清单

```bash
python lq.py search run --plan topic.plan.json --source scopus --min 15 --max-attempts 4 --max-records 30 --out search.run.json --md 03_文献清单.md
```

重试阶梯（`query.attempt_variants()`，每次命中数都记录）：

| 步 | 动作 |
|---|---|
| A | 同义词 + 精确短语 + 全部过滤器 |
| B | 加词根截断、加同义词、加宽泛词 |
| C | 精确短语改为邻近（W/4、NEAR/4），年份窗口放宽 3 年 |
| D | 只保留 core 概念块，去掉过滤器（最后手段） |

判定：

- 任一步命中 ≥ 15：采用该步，继续取记录。
- 四步都 < 15：`status=insufficient`，**不得**用不足的样本冒充完整结果；向用户报告每一步的命中数与可选调整方向（加中文概念、换同义写法、换库、放宽年份/类型）。
- 命中数解析失败（`None`）：先按 `references/troubleshooting.md` 排查页面状态，不能当成 0。

清单字段：标题、摘要、地址（DOI/记录页）、一作、通讯、期刊、年份、被引。通讯作者优先取 OpenAlex `is_corresponding`；缺失时以末位作者推断并在字段中标注 `(inferred)`。

## 3.5 批量领域综述（大类学科汇编）

当用户要求“每个大类学科各整理一份综述”时，使用领域目录而不是逐个方向跑五阶段：

```bash
python scripts/field_reviews.py --out <WORK>/fields
python scripts/field_reviews.py --out <WORK>/fields --only neuroscience,immunology
```

- 目录：`assets/field_catalog.json`，当前 16 个大类（神经、免疫、微生物、生物信息、流行病与群体调查、肿瘤、心血管、细胞与分子、遗传与发育、生化与代谢、药理与临床、医学影像、生态与进化、环境、材料、计算机与 AI）。
- 每个领域：OpenAlex `type:review` + 标题锚点，2018 年后，按被引降序取前 50 条，按本地期刊表与主题相关性筛 5 篇。
- 产物：`00_索引.md` + 每个领域一份 Markdown（领域导读、综述清单、逐篇英文摘要、DOI、一作/通讯）。
- 这是“入口型汇编”，不是穷尽检索；摘要缺失会标 `[WARN]`，期刊层级未导入 JCR 时为近似判定。
- 需要中文翻译与逐篇解读时，再对入选篇目走 `digest` 流程，不要批量机器翻译 80 篇。

## 4. 命令速查

```bash
python lq.py doctor
python lq.py directions match --input "<方向>" --out plan.json
python lq.py query build --plan plan.json --engine both --out queries.json
python lq.py reviews discover --plan plan.json --source scopus --out reviews.selected.json
python lq.py reviews fetch --input reviews.selected.json --outdir fulltext
python lq.py reviews digest-init --input reviews.selected.json --out digest.tasks.json
python lq.py reviews digest-check --tasks digest.tasks.json --filled digest.json
python lq.py reviews report --discover reviews.selected.json --digest digest.json --out 02_权威综述.md
python lq.py search run --plan topic.plan.json --source scopus --min 15 --out search.run.json --md 03_文献清单.md
python lq.py list build --input search.run.json --out 03_文献清单.md
python lq.py journals lookup --journal "Nature Reviews Microbiology"
python lq.py journals import --csv jcr.csv --format jcr --out journal_tiers.json
python lq.py browser status
```

退出码：`0` 成功；`2` 输入/后端错误；`3` 未达目标（方向未命中、无入选综述、命中 < 15）。

## 5. 浏览器与反爬纪律

- **只启动一次**：`python lq.py browser start` 后，后续命令自动复用 `127.0.0.1:9222`。任务结束后才 `browser stop`。
- **导航节流**：默认 5 秒；`--min-nav-interval` 可调大，不建议调小。
- **关闭扩展与弹窗**：启动参数已带 `--disable-extensions`、`--deny-permission-prompts`、`--disable-notifications` 与崩溃/翻译/重发表单抑制；页内同意书脚本只处理已知 CMP 容器，不全局点按钮。
- **出现验证码/Cloudflare**：立即停止该查询，保存快照（`web probe --outdir`），报告用户；换 API 或等用户手动通过验证后再继续。不要循环刷新。
- **登录**：Scopus 未登录仍可读命中数与记录页；WoS 未登录只能读命中数。需要完整记录时让用户在弹出的浏览器窗口完成机构登录，然后重跑同一命令（浏览器与 profile 会保留会话）。
- **CDP 启动失败**（`mojo ... Access is denied`）：说明浏览器进程被沙箱限制，需在普通终端或用非受限方式启动 `lq.py browser start`；不要把失败当成“网站拒绝”。

## 6. 命令行执行纪律（本机 PowerShell）

内联命令只用 ASCII；涉及中文或稍复杂逻辑时先写 `.ps1`（UTF-8 with BOM）再 `-File` 执行。禁用 PowerShell 7 专有语法 `?:`、`??`、`&&`、`||`。读取文本统一 `-Encoding UTF8`。列目录限定深度与过滤条件。外部命令的 stderr 先重定向到文件再截取头部。

## 7. 产物与质量检查

交付前逐项核对：

- [ ] `01_方向关键词.md`：方向路径、英文关键词、两套检索式、lint 结果、命中数（若已跑）。
- [ ] `02_权威综述.md`：入选理由、摘要翻译、解读、细分方向、确认门。
- [ ] `03_文献清单.md`：标题、摘要、地址、一作、通讯、年份、被引；不足 15 条时有明确说明。
- [ ] 每个数字都有来源（页面/API + 时间）；`rank_unknown` 没有被当成达标。
- [ ] 没有 emoji 堆叠；提示统一用 `[OK]` `[WARN]` `[STOP]` `[PROHIBIT]` `[YES]`。
- [ ] 报告里不出现“示例细分方向”式占位内容；细分方向必须来自本次综述。

## 8. 参考文件

| 文件 | 内容 |
|---|---|
| `references/scopus-syntax.md` | 字段码、布尔、邻近、通配、过滤、限制 |
| `references/wos-syntax.md` | 字段标签、布尔、NEAR、通配、过滤 |
| `references/query-design.md` | 概念块设计、同义词/上下位词、扩检与缩检 |
| `references/journal-ranking.md` | JCR/中科院/SJR 表结构与“前 20%”判定 |
| `references/review-selection.md` | 综述筛选与奠基之作规则 |
| `references/browser-cdp.md` | CDP 启动、复用、选择器覆盖、故障处理 |
| `references/interpretation.md` | 摘要翻译与解读的写法与校验 |
| `references/output-contract.md` | JSON/Markdown 字段契约 |
| `references/troubleshooting.md` | 命中数为空、登录墙、验证码、0 结果 |

## 9. 常见错误（逐条避免）

1. 把 `Q1` 当“前 20%”——Q1 是前 25%，必须用 JCR 百分位或中科院一区。
2. 把“页面没解析出命中数”写成 0。
3. 用方向级关键词直接下结论“该领域只有 N 篇”——检索式与库覆盖都会限制结果。
4. 综述没读就编细分方向。
5. 用户未确认就进入细化检索。
6. 命中数不足仍输出“代表性文献列表”而不加警告。
7. 反复刷新结果页试图绕过验证码。
8. 把 `W/n`、`{}` 写进 WoS。
