---
name: paper-interpretation-writing
description: Write Chinese-language paper interpretations (论文解读) at three selectable tiers of detail — L1 精简 (abstract, main achievements and outlook only; key loci and headline numbers; table-ready), L2 详细 (more data and a limited account of the experimental process), L3 完整 (full narrative for reviews and representative-work write-ups). Use when writing a Chinese overview of a research paper, filling an interpretation card, building a literature table, or drafting a review's per-paper summary, or when fusing several source registers into one interpretation. Supplies the tier content contract, a slot-by-slot sentence library, verb-strength grading, an anti-pattern list, and a deterministic validator that enforces tier budgets, flags invented numbers, empty filler, and speculation leaked into conclusions.
license: MIT
compatibility: Python 3.12+ with the standard library only. The bundled validator is deterministic and local-only — it reads text files and makes no network, model, or external-service calls.
metadata:
  version: "2.0"
  skill-author: K-Dense Inc.
---

# 论文解读写作模块（Paper Interpretation Writing）

一个**可跨 agent 复用**的解读生成模块。任何 agent 或流水线阶段只要拿到一篇论文，
就能按本模块的契约产出**指定档位**的中文解读。

## 模块契约（调用方只需要知道这三件事）

| | 内容 |
|---|---|
| **输入** | ① 论文（全文 / 摘要，或已抽好的 `assets/interpretation_unit.template.json`）<br>② 档位 `L1` / `L2` / `L3` |
| **输出** | 该档位的中文解读（Markdown），无表头、无元讨论、无自检附录 |
| **约束** | 档间一致（L1 ⊂ L2 ⊂ L3）、语气与证据对齐、数字原样引用、不臆造 |

调用方式：

```text
按 paper-interpretation-writing 的 L2 档，解读 <论文路径>。
```

或先抽事实、再逐档消费同一个事实表（推荐用于批量）：

```text
按 paper-interpretation-writing 的档位规范，为这批论文各抽一份
assets/interpretation_unit.template.json，再分别渲染 L1 供表格使用。
```

**这个模块不负责**：检索论文、翻译全文、判断论文好坏、写文献综述的整体框架。
它只负责"把一篇论文的要点写成中文解读"，以及保证同一条事实在三档里说的一致。

## 三档：先选档，再动笔

档位由**用途**决定，不由篇幅偏好决定。选错档会把表格塞满，或把综述写成速览。

| 档位 | 用途 | 读者 | 篇幅 | 一句话特征 |
|---|---|---|---|---|
| **L1 精简** | 表格、清单、速览、代表作一览 | 需要判断"值不值得读"的人 | 150–300 字 | 只有摘要、主要成就、回顾展望 |
| **L2 详细** | 解读报告逐篇卡片、组会材料 | 同领域研究者 | 400–700 字 | L1 + 更多数据 + 有限实验过程 |
| **L3 完整** | 综述、代表作解读、深度报告 | 跨领域读者 | 800–1500 字 | L2 + 完整论证链与机制细节 |

**选档规则**

- 输出会被放进**表格单元格或列表项** → `L1`。
- 输出会被**逐篇并列**、需要支撑"这篇做了什么"的判断 → `L2`。
- 输出会**独立成篇**，或进综述的"研究进展"段落 → `L3`。
- 拿不准时**选低一档**：L1 可以随时扩写成 L2，反之要删已经写进读者脑子的东西。
- 只拿到**摘要**时，任何档位都**只能写摘要能支撑的内容**，且在解读末尾如实标注证据层级
  （写在正文里的一句话，不是附录）。

### 各档内容范围

| 内容 | L1 | L2 | L3 |
|---|---|---|---|
| 一句话定位（做了什么 + 最硬结论） | ✅ 必含 | ✅ | ✅ |
| 主要成就（条目式） | ✅ 2–4 条 | ✅ | ✅ 展开成段 |
| 关键位点 / 靶点 / 基因名 | ✅ 只留核心 | ✅ | ✅ |
| 结论性数字 | ✅ 2–5 个 | ✅ 8–15 个 | ✅ 全部关键数字，含误差项 |
| 误差项、样本量、基线 | ❌ | ✅ 部分 | ✅ 完整 |
| 实验过程（用什么体系/手段） | ❌ 除最核心方法名 | ✅ 有限，一句一个 | ✅ 完整，含逐步论证 |
| 机制细节链条 | ❌ | ⚠️ 一句概括 | ✅ |
| 排除性证据 | ❌ | ✅ | ✅ |
| 待验证问题 / 局限 | ⚠️ 一句展望 | ✅ | ✅ 逐条 |
| 图表引用（图 3b / 表 2） | ❌ | ⚠️ 仅关键数据 | ✅ |
| 作者语气标注（L1/L2/L3 动词） | — | — | ✅ 明示推测处 |

> **L1 的"不含"是硬约束**，不是建议。表格里出现「凝胶迁移实验显示解离常数为
> 105.83 ± 8.3」会让整张表失去可比性——列宽被一个数撑爆，读者也不再逐行读数字。

完整档位规范、各档句式与三档实例见 `references/tier-specs.md`。

## 档间一致性（本模块最容易被违反的约束）

同一篇论文的多档解读往往由**不同 agent 分别产出**，于是同一个发现在 L1 里被写成
「证实」、在 L3 里被写成「提示」。这是本模块要消除的首要缺陷。

三条硬约束：

1. **包含关系**：L1 的每一条事实都必须出现在 L2 与 L3 中（L3 是超集，不是另一种说法）。
2. **语气不得跨档升级**：L1 的动词强度 ≤ L2 ≤ L3 中同一条的强度。
   三档都必须与原文动词同级，所以实际上三者应当**同级**，不是递增。
3. **数字不得跨档换算**：`28,279` 在三档里都是 `28,279`，L1 也不写成"近三万人"。

> 递增的是**细节量**，不是**断言强度**。把 L1 写得更"肯定"以便表格好看，
> 是本模块最常见的误用。

## 语气纪律：动词强度分级（所有档位通用）

中文解读最典型的失真，是把英文的中等语气词升级成强断言。按证据选词：

| 级别 | 中文动词 | 英文对应 | 用在哪 |
|---|---|---|---|
| **L1 强** | 证明、证实、确定、鉴定出、揭示 | demonstrate, establish, identify, reveal | 有直接实验或统计检验支撑 |
| **L2 中** | 表明、显示、说明、指出、支持 | show, indicate, illustrate, support | 有数据，但结论需一步推理 |
| **L3 弱** | 提示、暗示、指向、能够、可能、有望、潜在 | suggest, implicate, can, may, potential | 相关性、间接证据、展望 |

**校准方法**：找到原文的动词，按上表落到同一级，再选中文词。

- 摘要写 `illustrate` → 「说明了」(L2)，不写「证明了」(L1)。
- 讨论写 `suggest potential therapeutic avenues` → 「提示了…潜在途径」(L3)，
  不写「提供了有效的治疗方案」。
- 论文**已用 L3 表述的结论（尤其演化、起源、语言一类宏大命题），解读一律保持 L3**。
  这一步与档位无关：L1 也要写「提示」，不能因为地方小就说得更满。

## 硬规则

1. **术语首现中英对照**：`胎儿血红蛋白（HbF）`、`交联免疫沉淀（CLIP）`。
   首现之后一律用缩写。基因/蛋白符号（BACH2、NRF2、HBG1）与核苷酸基序（UCAU）
   按中文惯例裸写，不需对照。
2. **数值保留原样**：含单位、有效数字、基线、条件。L1 只**取舍**数字，不**改写**数字。
3. **区分已验证结论与作者主张**：有数据支撑的进结论；推测、展望、未做的设想
   进"待验证问题"。L1 若放不下局限，就把展望写成弱语气，不要省略成断言。
4. **缺口具名**：写清此前**不知道什么**。「具有重要意义」是空话。
5. **导出性陈述不写进解读**：不写"本解读分为三部分"，不写"详见原文"，
   不写解读自身的篇幅说明。

## 负面清单

| 不要写 | 为什么 | 改成 |
|---|---|---|
| 采用了多种方法 | 空话，不可复现 | 点名方法：`MultiSuSiE 多祖源精细定位` |
| 进行了深入研究 | 无信息量 | 写研究做了什么 |
| 取得了显著成果 | 不可证伪 | 写具体结果与数值 |
| 具有重要意义 / 众所周知 | 空话 | 点名缺口与受益对象 |
| 首次 / 重大突破 | 除非原文有 `first`，否则越界 | 删，或降到「此前未被报道」 |
| 有望治愈 / 将应用于临床 | 把 L3 展望写成 L1 断言 | 「提示了…潜在的治疗途径」 |
| 「约三万人」代替 28,279 | 换算即失真 | 原样引用 |
| 在 L1 里写误差项与实验步骤 | 破坏表格可比性 | 移到 L2/L3 |

完整检查表见 `references/checklist.md`。

## 工作流（四步，任何档位都走这四步）

1. **抽事实表** —— 按 `assets/interpretation_unit.template.json` 抽一次。
   这一步只做一次，三档共用；批量处理时它是并行 subagent 的产物。
   抽不到就留空并标记，**不要用印象补全**。
2. **定档** —— 按用途选 L1/L2/L3，写下字数预算。
3. **写** —— 按 `references/tier-specs.md` 的该档句式与禁写项落笔。
   同类事实在别档已有措辞时，**沿用同一措辞**，以保证档间一致。
4. **自检** —— 跑校验器（下节），再人工核对两条机器查不到的：动词强度是否与原文对齐、
   机制描述是否忠实于原文。

## 数据契约：解读单元（interpretation unit）

`assets/interpretation_unit.template.json` 是本模块的**跨 agent 接口**。
一个 agent 抽出它，任意数量的下游 agent / 脚本就能消费它渲染任何档位，
无需重读论文。字段与"写什么"的对应见该文件内的注释。

关键字段：`paper`（题录）、`evidence`（证据层级）、`gap`、`approach`、
`findings[]`（每条带 `tier` 标注它够不够格进 L1）、`mechanism`、`exclusions[]`、
`significance`（三个落点）、`open_questions[]`、`strength`（该条的语气级别）。

> `findings[].tier` 是**归属性标注**，不是重要度评分：它回答"这条能不能进精简档"。
> 有了它，L1 的取舍就不再靠临场判断，批量产出时也不会各写各的。

## 自检

```bash
# 按档位校验（字数预算、该档禁写项、术语对照、收尾落点）
python scripts/check_interpretation.py 解读.md --tier L1

# 额外逐个核对数字是否真在原文里 —— 抓臆造数字最有效的一步
python scripts/check_interpretation.py 解读.md --tier L2 --source 原文.txt

# 机器可读报告 / 让警告也失败
python scripts/check_interpretation.py 解读.md --tier L3 --json --strict

# 可选：加开 deai 模型二审（本地 Qwen3-0.6B 适配器，规则黑名单外的同义变体）。
# 只出 MODEL_* warning、不影响退出码；需 torch/transformers/peft，
# 先跑 python 流程1/scripts/selftest_small_model.py 确认环境。
python scripts/check_interpretation.py 解读.md --tier L2 --model
```

校验器覆盖：空话、越界推测、术语缺对照、缺收尾落点、**档位超篇幅**、
**该档禁写项（如 L1 里的误差项与实验步骤）**，并在给了 `--source` 时
逐个核对数字出处。

**校验器查不到的两件事**：动词强度是否与原文对齐、机制描述是否忠实于原文。
报告的 `notice` 字段如实写着这一点，一次 clean run 不等于解读正确。

## 接入 流程1 阶段 H

`流程1`（本仓库的本科生科研辅助流程）阶段 H 用并行 subagent 逐篇产出解读卡，
再由 `interpret_build.py` 汇总。字段与档位的对应：

| 卡片字段 | 档位 | 说明 |
|---|---|---|
| `one_line` | L1 强度 | 1 句定位 |
| `problem_solved` | L1/L2 | 缺口 → 做了什么 → 最关键发现 |
| `key_20` | L2 | 3–8 句有序文字 |
| `verified_conclusions` | L2 | 逐条挂证据 |
| `methods` / `quantified_gains` | 清单式 | 不走档位文体，数值原样引用 |
| `open_questions` | L3 | 展望、局限、未做的设想 |

字段 schema 与分派方式以 `流程1/subagent-driven-review/SKILL.md` § 6 / § 7 为准；
冲突时以该流程文件为准，本模块只补充文字写法与档位取舍。

> **与相邻技能的边界**：`scientific-writing` 管英文稿件的起草、修订与投稿审计；
> 本模块只管**中文解读的成段文字与档位取舍**。两者可并用，不重叠。

## 示例

- **`references/tier-specs.md`** —— 三档逐档规范 + 同一篇论文（NOVA1 人源化小鼠）
  的 L1 / L2 / L3 三份成品，可直接当模板改。
- **`references/worked-example.md`** —— BACH2–NRF2 论文的英文原文逐句拆解，
  用于练动词强度校准。
- **`references/sentence-patterns.md`** —— 按档位组织的句式库，
  每个槽位给出 模板 → 变体 → 真实例句 → 常见错误。
