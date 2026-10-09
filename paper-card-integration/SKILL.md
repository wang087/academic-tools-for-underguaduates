---
name: author-paper-card-integration
description: 流程1/SKILL.md 的子流程（阶段 H 的 paper-card 引擎）——把已并入的 nature-paper-card 接进阶段 H 的单篇解读：在原有 11 字段解读卡之外额外产出 16 节 Paper Card，并以「结构审计 + 数字回查原文」作为交付质量门。当需要论文精读卡、方法拆解、证据链分析、结论边界时使用。
---

# 阶段 H · paper-card 引擎（nature-paper-card 并入）

> ## 从属关系（先读）
>
> **本文件是 `流程1/SKILL.md` 的子流程，只负责阶段 H 的 `interpret_engine=paper-card` 路径。**
>
> - 冲突时**一律以 `流程1/SKILL.md` 为准**；
> - 本文件**不重新定义** nature-paper-card 的任何内部步骤，只定义**如何进入**它、
>   **哪些环节被裁剪**、以及**并入后仍必须遵守哪些质量门**；
> - nature-paper-card 自己的分析纪律（`SKILL.md` 的 Routing protocol 与
>   `static/core/principles.md`）在进入后**完全适用**。

---

## 1. 并入范围：只有这一个技能

| 项 | 事实 |
| --- | --- |
| 并入了什么 | `nature-skills` v0.1.0 中的 **`nature-paper-card` 一个技能**，落在 `流程1/vendor/nature-paper-card/`（27 个文件，144 KB，Apache-2.0） |
| 没并入什么 | 上游其余 **19 个技能**（`nature-reader` / `nature-academic-search` / `nature-reviewer` / `nature-literature-pipeline` / `nature-paper2ppt` / `nature-figure` / `nature-writing` …）**一律没有并入**，本流程也不调用它们 |
| 外部依赖 | **无**。路径解析只有一个权威 `scripts/nature_bridge.py`，它**优先**找 `<流程1>/vendor/nature-paper-card`，仓库根的上游检出仅作开发期回退 |
| 术语台账 | 上游放在共享层 `nature-shared/core/terminology-ledger.md`，因只并一个技能而**内联**为 `vendor/nature-paper-card/references/terminology-ledger.md`（`manifest.yaml` 的 `always_load` 已同步改指技能内路径，不再有 `../` 越界引用） |
| 上游的安装/自动更新 | `npx skills add`、`autoupdate-skills.sh`、Codex/OpenClaw/Hermes 的技能目录配置 —— **均不属于本次合并，也不应再被引用** |

> **[STOP] `nature-reader` 的 source-map 是「数据格式」，不是「依赖」。**
> `prepare_paper.py` 接受 `source_map.json`，其块文本字段名（`original_text`）与页码字段
> 来自上游 `nature-reader` 的输出规范。**任何符合该格式的 source map 都能用**，
> 不需要安装 `nature-reader`。遇到"要全文双语阅读材料"这类需求，
> 走**阶段 F**，而不是去调用一个不存在的技能。

---

## 2. 引擎开关与产出契约

阶段 0 收口，写在 `.work_cdp/prepare_plan.json`：

```bash
uv run python 流程1/scripts/prepare_stage.py --set interpret_engine=legacy|paper-card
uv run python 流程1/scripts/prepare_stage.py --engine H      # 查询（stdout 只有取值）
```

| 引擎 | 产出 | 何时用 |
| --- | --- | --- |
| `legacy`（**默认**） | 只有 11 字段 JSON 解读卡 | 批量速览、《论文解读》汇总、PPT、知识图谱 |
| `paper-card` | **额外**产出 16 节 Paper Card + 审计证据 | 代表作精读、组会材料、进组前深读 |

> **[STOP] `paper-card` 引擎是「只增不减」。**
> 11 字段 JSON 卡**照旧产出** —— `interpret_build.py`、`ppt_brief.py`、`knowledge_graph.py`
> 只认它。这条设计使引擎切换**不需要改动任何下游脚本**，也让两种引擎共用同一批
> `tasks/` `results/` 命名（历史卡不会错位）。

落盘契约（**两样都要有，缺一不可**）：

```text
.work_cdp/interpret/
├── tasks/<doi_slug>.json                   任务包（含 interpret_engine 与 paper_card 路径块）
├── results/<doi_slug>.json                 ① 11 字段卡（机器读；下游唯一入口）
└── paper_cards/
    ├── <doi_slug>.md                       ② 16 节 Paper Card（人读的深读本体）
    ├── <doi_slug>.source_bundle.json       prepare_paper.py 的产物（页码/图表清单/校验块）
    ├── <doi_slug>.audit.json               质量门合并报告
    └── <doi_slug>.pages/                   仅需视觉核对时渲染的页面 PNG
```

`paper_cards/` 与 `results/` **必须分开**：一个按文件名取卡的消费者若无差别扫描，
会把 16 节 Markdown 当成 JSON 卡读崩。

---

## 3. 执行

```bash
# ① 组包（下发引擎与全部路径；paper-card 引擎会先做可用性前置检查）
uv run --with pypdf python 流程1/scripts/interpret_prepare.py --engine=paper-card
#     只用相位作用域内子集：--only=<DOI> --limit=N

# ② 并行 subagent 逐篇精读（提示词模板见
#    subagent-driven-review/SKILL.md § 7.3「paper-card 引擎版」）
#    每个 subagent：读自己的 task.json → 按已并入技能的 Routing protocol 出 16 节卡
#    → 同时写 11 字段 JSON 卡 → 自己先跑一次质量门

# ③ 交付质量门（**每条都必须跑**，退出码非 0 即为 error）
uv run python 流程1/scripts/nature_bridge.py check \
  --card  .work_cdp/interpret/paper_cards/<slug>.md \
  --bundle .work_cdp/interpret/paper_cards/<slug>.source_bundle.json \
  --locator-mode page-grounded \
  --report .work_cdp/interpret/paper_cards/<slug>.audit.json

# ④ 汇总（会申报引擎，并逐篇列出深读卡与审计状态）
uv run python 流程1/scripts/interpret_build.py
```

**位置无关**：`nature_bridge.py` 只认 `<流程1>/vendor/nature-paper-card`，
可由 `PAPER_CARD_DIR` 覆盖；分发包里只有前者。

---

## 4. 质量门：两半，缺一不可

| 门 | 由谁判定 | 判定什么 | **不**判定什么 |
| --- | --- | --- | --- |
| 结构审计 | `nature_bridge.py check` → 已并入技能的 `audit_paper_card.py` | 节序 01–16、证据状态头、定位模式声明、`[Paper: …]` 指针、**节正文非空**、每分析节至少一条指针、图表清单覆盖（有 bundle 时）、越界内容 | 内容是否**可信** —— 它不读原文 |
| 内容级校验 | `nature_bridge.py check` → `skills/paper-interpretation-writing/scripts/check_interpretation.py` | **逐个数字回查原文**（`NUMBER_NOT_IN_SOURCE`）、空话、越界推测、术语缺中英对照、未支撑的新颖性表述 | 「这句话原文里没有」；语气强度与机制忠实度 |

> **为什么要接第二半（本流程新增，非上游能力）**：上游审计器是纯结构 lint。
> 实测：一张 **03–15 节正文全空**、只有 1 条指针的卡拿到
> `pass (13 passes, 0 errors)`、退出码 0。**结构合规 ≠ 内容可信。**
>
> **归一化是必要的、且是公开的**：16 节卡的节标题是 `## 01 …`，
> 而数字校验器会把节号 `01`–`16` 当成臆造数字（实测未处理时每篇必报 15 条假 error）。
> `nature_bridge.py` 因此在**剥掉标题行与证据状态头之后**再做内容校验，
> 并把剥离条数写进报告的 `normalization` 字段 —— 可复核，不是静默篡改。

---

## 5. 本地补丁（升级上游时必须重新施加）

完整清单与逐条理由见 [`vendor/nature-paper-card/UPSTREAM.md`](../vendor/nature-paper-card/UPSTREAM.md) §3。
摘要：

| 补丁 | 修的是什么（未修前的**实测**后果） |
| --- | --- |
| P1 `original_text` | 合规 source map 得到 **0 页 / `invalid` / exit 1**，被迫跌到 `source-limited` |
| P2 定位模式改由 `locator_summary` 判定 | 每块都带可靠页码的 source map **永远进不了 `page-grounded`**，而审计器又禁止非该模式引用页码 |
| P3 `SKILL.md` 的 `--locator-mode` 取值 | 上游命令 **exit 2**，把强制质量门变成"审计器坏了→人工自查" |
| P4 `CAPTION_RE` 放开冒号 | 实测真论文 47 处图表引用 → 清单 **0/0/0**，"图表清单完备"闸门**空转** |
| P5 审计器补 `section_bodies` / `section_pointers` / 精确匹配 | 空壳卡 **13 PASS / 0 error / exit 0**；卡里只写 `Figure 10` 就能满足清单项 `Figure 1` |
| P6 `out_of_scope` 需指令语境 | 正常行文"It is not a public article…" **FALSE FAIL** |
| P7 单行长度申报 + 软换行 | 整页正文落成**一个物理行**（实测最长 7918 字符），读取工具截断即**静默丢正文** |
| P8 frontmatter/版本 | PyMuPDF 是硬依赖却只在脚本里出现；版本号不在 `SKILL.md` |
| P9 并入范围说明 + 内联台账 + 修链接 | 只并一个技能后，必须让人知道其余同族技能**不存在** |
| P10 测试 | 上游 7 例只覆盖 source-map 定位，**审计器零测试** |

> **为什么必须人工重施加**：这些都是**语义补丁**，不是格式转换 ——
> 上游同名函数重排一行就可能让补丁落空，而落空的后果是"质量门静默失效"，
> 不会报错。自动覆盖 = 悄悄退回有缺陷的版本。

---

## 6. 常见错误

- [X] **以为并入了整包 nature-skills** —— 只并了 `nature-paper-card`；调用
  `nature-reader` / `nature-reviewer` 等会失败。全文阅读材料走阶段 F，审稿走阶段 H2。
- [X] **用 `--engine=paper-card` 却只写 16 节卡** —— 11 字段 JSON 卡缺了，
  `interpret_build.py` 会把它算作**缺卡**（并行 subagent 的劳动成果作废）。
- [X] **跳过 `nature_bridge.py check`** —— 那等于放弃"数字回查原文"这唯一的内容级判定；
  上游审计器的 PASS **不能**当作内容可信的证据。
- [X] **把 16 节卡写进 `results/`** —— 该目录是 11 字段 JSON 的专用位置，
  混入 Markdown 会让下游按文件名取卡时读崩。深读卡一律进 `paper_cards/`。
- [X] **在 `--locator-mode` 上写上游文档的旧取值**
  （`structure-grounded-or-source-limited`）—— argparse 会直接 exit 2。
- [X] **只并技能却不带 UPSTREAM.md / LICENSE** —— Apache-2.0 要求保留许可与声明；
  且下一个人无从知道哪些是本地补丁。

---

## 7. 检查清单

- [ ] 阶段 0 已确认 `interpret_engine`（`--engine H` 查询得到取值）
- [ ] `uv run python 流程1/scripts/nature_bridge.py --self-test` 通过
- [ ] `uv run python 流程1/scripts/selftest_nature_integration.py` 通过
- [ ] 每个启用本引擎的篇目都**同时**有 `paper_cards/<slug>.md` 与 `results/<slug>.json`
- [ ] 每篇都跑过 `nature_bridge.py check`，且 `audit.json` 的 `summary.status` 为 `pass`
- [ ] 定位模式与该次准备的实际结果一致（`source_bundle.json` 的
      `validation.recommended_locator_mode`）；非 page-grounded 时**不得**出现页码引用
- [ ] 数字回查确实开着（合并报告 `source_checked: true`，而不是"忘了给 --source"）
- [ ] 作者自述局限（§12）与 Agent 批判（§13）分列，未被混写
- [ ] 第 16 节的研究想法标注了 `innovation status`，且**未**宣称 novelty
- [ ] 《论文解读》第六节如实列出深读卡与审计状态（含"未跑"的篇目）
- [ ] `vendor/nature-paper-card/LICENSE` 与 `UPSTREAM.md` 随包分发
