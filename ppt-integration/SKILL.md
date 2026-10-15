---
name: author-paper-ppt-integration
description: 流程1/SKILL.md 的子流程（阶段 P）——把 ppt-master 的演示文稿工作流嵌入作者论文分析流程。材料固定取自《<作者名>_论文解读.md》的文献亮点与《<作者名>_研究方向综述.md》的 review 结果；选择制作 PPT 后，必须进入 ppt-master 的两阶段确认，在其中选择自由设计/模板及具体设计风格，再生成 design_spec.md 与 spec_lock.md。当需要把作者文献分析结果做成 PPT 时使用。
---

# 阶段 P · PPT 生成（ppt-master 整合）

> ## 从属关系（先读）
>
> **本文件是 `流程1/SKILL.md` 的子流程，只负责 SKILL.md §4「阶段 P」。**
>
> - 冲突时**一律以 `流程1/SKILL.md` 为准**；
> - 本文件**不重新定义** ppt-master 的任何内部步骤，只定义**如何进入**它、
>   **哪些环节与主流程衔接**、以及**各环节必须遵守哪些质量门**；
> - ppt-master 自己的执行纪律（`skills/ppt-master/SKILL.md` 的
>   Global Execution Discipline）在进入后**完全适用**。

> ## 本阶段对 ppt-master 做了什么改动
>
> | 项 | ppt-master 原样 | 本流程的处置 |
> |---|---|---|
> | 顶层路由 | Generate / Create Template / Fill / Enhance 四条 | **不改**。本流程只用 Generate |
> | Step 1–2（source 转换、项目初始化） | 原样 | **不改**，由 `ppt_brief.py` 代为调用 |
> | Step 3（模板候选准备） | 准备四个注册库的候选 | **保留** —— Stage 1 展示自由设计/模板选择 |
> | Step 4（Strategist Phase，两阶段阻塞式确认） | Confirm UI 或聊天确认 → 撰写 `design_spec.md` / `spec_lock.md` | **保留确认** —— Stage 2 展示至少三个完整设计方向，用户确认设计风格后才生成两份 Spec |
> | Step 5（图像获取） | 按 §VIII 触发 | **通常跳过** —— 阶段 K 的图谱是 `Acquire Via: user` / `Status: Existing`（已在项目 `images/` 内），不需要"获取"；值为空时也不触发 |
> | Step 6（Executor 手写 SVG） | 原样 | **不改**。仍由主 agent **手写**每一页，不得用生成器批量产出 |
> | Step 7（后处理与导出） | 原样 | **不改**。串行执行，逐条成功再进下一条 |
>
> **一句话**：本流程不再跳过设计决策确认；选择制作 PPT 后必须进入 ppt-master 的确认环节，至少确认设计模式、视觉风格、配色、字体与图标方案。确认完成后才生成 `design_spec.md` / `spec_lock.md` 并进入 SVG 手写与导出。

---

## 1. 适用场景与前置

| 项 | 要求 |
|---|---|
| 何时用 | 准备阶段（阶段 0）中 `make_ppt=yes` |
| 素材前置 | 阶段 H 已产出 `.work_cdp/interpret/results/*.json`（文献亮点）**且/或** 阶段 H2 已产出 `.work_cdp/refs/results/*.json`（review 结论）；**至少有一边非空**，两边都空则本阶段拒绝启动 |
| 软件前置 | 本仓库存在 `ppt-master/`；Python 依赖见 `ppt-master/requirements.txt` |
| 网络 | **不需要**。本阶段全程离线 |

**材料来源（用户指定，不需另行准备）**：

1. 《`<作者名>_论文解读.md`》 —— 该作者**文献亮点解读**
2. 《`<作者名>_研究方向综述.md`》 —— 阶段 H2 产出的 **review 结果**
3. **阶段 K 的知识图谱**（v7.2 新增，`make_graphs=yes` 时）——
   自动写入 `design_spec.md §VIII` 与 §IX，并复制进 `<project>/images/`
4. **方向综述整理**（v7.2 新增）—— 每个方向 → 最紧密综述 + 已确认结论 + 未解决问题；
   数据源与《研究方向综述》**同源**，保证 PPT 与报告口径一致
5. `--no-graphs` 可整体不接入第 3、4 项

> [WARN] **PPT 里用 PNG 而不是 SVG**：PPTX 的 `<image>` 内嵌对 SVG 的支持随
> PowerPoint 版本而异，栅格图最稳。阶段 K 默认只出 SVG（矢量、可无损缩放），
> 所以要进 PPT 就得**同时**出 PNG。`ppt_brief.py` 检测不到 PNG 时会**明确提示**
> 补跑 `--also-png`，而不是悄悄少放一张图。

`ppt_brief.py` 读的是这两份报告背后的**结构化 JSON**（解读卡），
而不是 Markdown 正文 —— 结构化数据比正文更可靠、也不会因为报告排版调整而失效。

> ### v13：材料的解读档位会被记录，且**受限时如实说明**
>
> 阶段 0 确认的两个档位（`interpret_tier` 默认 **L2**、`review_interpret_tier`
> 默认 **L3**）会被 `ppt_brief.py` 读出并写进产物，形如：
>
> ```text
> > 素材档位：作者论文解读 L2；研究方向综述 L3（阶段 0 确认，可在阶段 0 改档）
> ```
>
> [WARN] **L1 素材会让 PPT 少掉一整个维度**：L1 的解读卡 `methods` 留空、
> `quantified_gains` 只留 2–5 个结论性数字且不带误差项，
> 因此「方法概览」与「关键数据」两页会明显变薄。
> `ppt_brief.py` 遇到这种情况**要在产物里如实标注素材受限**，
> 而不是静默产出空页 —— 与 §「检测不到 PNG 时明确提示」同一原则。
>
> 规范定义见 skill `paper-interpretation-writing`。

---

## 2. 执行

```bash
# ① 生成材料 + design_spec + spec_lock（一条命令）
uv run python 流程1/scripts/ppt_brief.py --pages=standard
#    可选：--dry-run 先看会写什么；--title=… --project-name=… --canvas=ppt169
#    可选：--ppt-dir=<ppt-master 路径>，或用 PPT_MASTER_DIR 环境变量

# ② 页面 SVG 由主 agent **手写**到 <project>/svg_output/
#    这不是一条命令，是 Step 6 的作者工作；必须逐页手写，不得脚本批量生成

# ③a SVG 转义**预检**（手写 SVG 后、跑上游质检前先跑这条）
#     正文里单独的 `<`（如 `p < 0.05`、`<5%`）与裸 `&`（如 `R&D`）都会让 XML 无法解析。
#     上游质检器只报 `not well-formed (invalid token): line N, column M` ——
#     看不出是哪种字符、也不知道该怎么改；本流程的预检**定位到行并给出修法**。
uv run python 流程1/scripts/ppt_brief.py --check-svg <project>/svg_output

# ③b SVG 质检（必须 0 error）
uv run python ppt-master/skills/ppt-master/scripts/svg_quality_checker.py <project>

# ④ Step 7 串行导出（一条一条跑，前一条成功再跑下一条）
uv run python ppt-master/skills/ppt-master/scripts/total_md_split.py <project>   # Speaker Notes 启用时
uv run python ppt-master/skills/ppt-master/scripts/finalize_svg.py <project>
uv run python ppt-master/skills/ppt-master/scripts/svg_to_pptx.py <project>
#    §I 里 Speaker Notes = disabled 时改成：svg_to_pptx.py <project> --no-notes
```

**产出**：`<project>/exports/<project_name>_<timestamp>.pptx`
＋ `validation/<project_name>_<timestamp>.report.json`。

---

## 3. Strategist Phase —— 选择并确认设计风格

原 Step 4 的作用是**让用户确认只有用户能定的演示设计选择**。阶段 0 只确认“是否制作 PPT”及页数预算，不替用户决定视觉方案。选择制作 PPT 后，必须按 ppt-master 的 Step 3–4 进入候选准备与两阶段确认：

| Step 4 要确认什么 | 本流程的处理 |
|---|---|
| 沟通契约（受众 / 意图 / 期望结果 / 核心信息） | 由流程材料提出建议，用户在 Stage 1 确认或修改 |
| 自由设计还是套模板 | 在 Stage 1 明确选择 `free_design` 或 `templates` |
| 设计模式与视觉风格 | 在 Stage 2 从 ppt-master 候选中选择或调整；必须记录最终值 |
| 配色、字体、图标、图片策略 | 在 Stage 2 确认；不得由 `ppt_brief.py` 静默替用户决定 |
| 画布、页数、页面节奏 | Stage 1/Stage 2 确认，页数再结合材料形成最终页面清单 |
| 讲者备注、动画、旁白 | Stage 2 按默认值呈现，用户可单独修改 |
| 页面清单与每页内容 | 依据确认后的设计方案与两份结构化材料生成 |

**必须遵守的顺序**：

1. `ppt_brief.py` 只负责整理材料、初始化项目和生成 Stage 1 推荐，不得先写 `design_spec.md` / `spec_lock.md`。
2. 运行 ppt-master 的 Stage 1 确认：用户确认沟通契约及 `free_design` / `templates`。
3. 如选择模板，先完成模板安装与校验；如选择自由设计，记录 `free_design` 状态。
4. 运行 ppt-master 的 Stage 2 确认：展示至少三个完整设计方向，用户选择一种设计风格并确认配色、字体、图标和图片策略。
5. 仅在最终 `result.json` 为 `stage: final` 且 `status: confirmed` 后，生成并校验 `design_spec.md` / `spec_lock.md`。

不得伪造确认回执；没有明确确认时必须停在对应闸门。

> [WARN] 不得用默认“学术简报”风格替代用户确认；默认值只能作为推荐，不能视为确认。

---

## 4. 质量门（各阶段均必须通过）

| 门 | 判据 | 不通过的处置 |
|---|---|---|
| Spec 结构门 | `project_manager.py validate` 通过 | 修 `ppt_brief.py` 的生成逻辑后重跑阶段 P |
| SVG 质检门 | `svg_quality_checker.py` **0 error** | 修对应页面 SVG，不得放行 |
| 导出后检门 | `validation/*.report.json` 状态为 `passed` 或 `passed-with-warnings`，且 PPTX 文件真的存在 | 按 `failure-recovery.md` 修**所属源产物**，不要降级要求 |
| 页面设计闭合 | `svg_output/` 是该页可见内容的**完整**来源；`design_spec.md` 与 `spec_lock.md` 不得提供页面上看不见的内容 | 补进 SVG，不得让导出器推断 |

---

## 5. 常见错误

- [X] **自己再写一份 `design_spec.md`** —— 已有，且上游是决策主体。发现两份冲突时
  **停下报告**，不要在本地"择优"。
- [X] **用脚本批量生成 SVG 页面** —— 违反 ppt-master 的硬规则，主 agent 必须逐页手写。
- [X] **把 `--pages` 当作硬性页数** —— 它是预算区间；`design_spec.md §I` 的
  `Page Count` 必须与 §IX 的实际页数**逐页对齐**，改页数要同时改两处。
- [X] **跳过 `svg_quality_checker.py`** —— 导出器会独立校验最终质检报告，
  缺报告或报告非 final 会直接非零退出。
- [X] **把 PPT 素材来源写成"另行准备"** —— 用户已明确：素材固定取自
  《论文解读》的文献亮点与阶段 H2 的 review 结果。

---

## 6. 检查清单

- [ ] 阶段 0 的 `make_ppt` 为 `yes`（否则本阶段整体跳过）
- [ ] 阶段 H 与/或阶段 H2 已产出解读卡；两边都空时已拒绝启动并说明原因
- [ ] 已跑 `ppt_brief.py`，且只生成材料与 Stage 1 推荐；未在确认前写入 `design_spec.md` / `spec_lock.md`
- [ ] 已完成 ppt-master Stage 1：确认沟通契约及自由设计/模板模式
- [ ] 已完成 ppt-master Stage 2：从至少三个完整设计方向中选择并确认设计风格、配色、字体、图标与图片策略
- [ ] 最终 `result.json` 为 `stage: final` 且 `status: confirmed`
- [ ] 确认后生成 `design_spec.md` / `spec_lock.md`，且 `project_manager.py validate` 通过
- [ ] `design_spec.md` 的 §I `Page Count` 与 §IX 实际页数一致
- [ ] `spec_lock.md` 的 `page_rhythm` 覆盖 P01 到最后一页，无缺号
- [ ] **未伪造任何 Confirm UI 回执**，未启动确认服务器
- [ ] [NEW] **图谱已接入**（若 `make_graphs=yes`）：§VIII 里有 `Acquire Via: user` /
      `Status: Existing` 的行，文件确实在 `<project>/images/` 内
- [ ] [NEW] **图谱页版式未缩到读不清**：整幅铺放 + 角落叠标题，`Crop Policy: no-crop`
- [ ] [NEW] **方向综述整理页**的口径与《研究方向综述.md》一致（同源数据）
- [ ] `svg_output/` 每页由主 agent 手写，未使用生成器
- [ ] **转义预检通过**：`ppt_brief.py --check-svg <project>/svg_output` 无
      裸 `<` / 裸 `&`（正文里的 `<` 写 `&lt;`、`&` 写 `&amp;`）
- [ ] `svg_quality_checker.py` 报 0 error
- [ ] Step 7 三条命令**串行**执行，逐条确认成功
- [ ] 导出的 PPTX 与 `validation/*.report.json` 均已产出
- [ ] 材料来源如实标注为《论文解读》+《研究方向综述》，未引入外部新事实
