---
name: author-paper-ppt-integration
description: 流程1/SKILL.md 的子流程（阶段 P）——把 ppt-master 的演示文稿工作流嵌入作者论文分析流程，并对它的 Strategist Phase 做简化：材料固定取自《<作者名>_论文解读.md》的文献亮点与《<作者名>_研究方向综述.md》的 review 结果，design_spec.md 与 spec_lock.md 由 ppt_brief.py 确定性生成，跳过模板候选准备与两阶段阻塞式确认，直接进入 SVG 手写与导出。当需要把作者文献分析结果做成 PPT 时使用。
---

# 阶段 P · PPT 生成（ppt-master 整合）

> ## 📌 从属关系（先读）
>
> **本文件是 `流程1/SKILL.md` 的子流程，只负责 SKILL.md §4「阶段 P」。**
>
> - 冲突时**一律以 `流程1/SKILL.md` 为准**；
> - 本文件**不重新定义** ppt-master 的任何内部步骤，只定义**如何进入**它、
>   **哪些环节被简化**、以及**简化后仍必须遵守哪些质量门**；
> - ppt-master 自己的执行纪律（`skills/ppt-master/SKILL.md` 的
>   Global Execution Discipline）在进入后**完全适用**。

> ## 📌 本阶段对 ppt-master 做了什么改动
>
> | 项 | ppt-master 原样 | 本流程的处置 |
> |---|---|---|
> | 顶层路由 | Generate / Create Template / Fill / Enhance 四条 | **不改**。本流程只用 Generate |
> | Step 1–2（source 转换、项目初始化） | 原样 | **不改**，由 `ppt_brief.py` 代为调用 |
> | Step 3（模板候选准备） | 准备四个注册库的候选 | **跳过** —— 本流程不选模板，走自由设计 |
> | Step 4（Strategist Phase，两阶段阻塞式确认） | Confirm UI 或聊天确认 → 撰写 `design_spec.md` / `spec_lock.md` | **跳过确认交互，保留产物** —— 两份文件由 `ppt_brief.py` **确定性生成**。新增 profile [`scientific-brief.md`](../../ppt-master/skills/ppt-master/workflows/profiles/scientific-brief.md) 把这一形态写成正式契约 |
> | Step 5（图像获取） | 按 §VIII 触发 | **通常跳过** —— 阶段 K 的图谱是 `Acquire Via: user` / `Status: Existing`（已在项目 `images/` 内），不需要"获取"；值为空时也不触发 |
> | Step 6（Executor 手写 SVG） | 原样 | **不改**。仍由主 agent **手写**每一页，不得用生成器批量产出 |
> | Step 7（后处理与导出） | 原样 | **不改**。串行执行，逐条成功再进下一条 |
>
> **一句话**：只简化了**决策交互**，没有简化任何**能力**或**质量门**。

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

> ⚠️ **PPT 里用 PNG 而不是 SVG**：PPTX 的 `<image>` 内嵌对 SVG 的支持随
> PowerPoint 版本而异，栅格图最稳。阶段 K 默认只出 SVG（矢量、可无损缩放），
> 所以要进 PPT 就得**同时**出 PNG。`ppt_brief.py` 检测不到 PNG 时会**明确提示**
> 补跑 `--also-png`，而不是悄悄少放一张图。

`ppt_brief.py` 读的是这两份报告背后的**结构化 JSON**（解读卡），
而不是 Markdown 正文 —— 结构化数据比正文更可靠、也不会因为报告排版调整而失效。

---

## 2. 执行

```bash
# ① 生成材料 + design_spec + spec_lock（一条命令）
uv run python 流程1/scripts/ppt_brief.py --pages=standard
#    可选：--dry-run 先看会写什么；--title=… --project-name=… --canvas=ppt169
#    可选：--ppt-dir=<ppt-master 路径>，或用 PPT_MASTER_DIR 环境变量

# ② 页面 SVG 由主 agent **手写**到 <project>/svg_output/
#    这不是一条命令，是 Step 6 的作者工作；必须逐页手写，不得脚本批量生成

# ③ SVG 质检（必须 0 error）
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

## 3. 简化的 Strategist Phase —— 为什么可以简化

原 Step 4 存在的意义是**收集只有用户能定的选择**。在本流程里，这些选择
**在阶段 0 就已经收齐、之后由上游流水线确定性地固定下来**：

| Step 4 要确认什么 | 本流程里它在哪里被定死 |
|---|---|
| 沟通契约（受众 / 意图 / 期望结果 / 核心信息） | `ppt_brief.py` 的参数与《解读》《综述》两报告的实测内容 |
| 自由设计还是套模板 | **自由设计** —— 这套内容不存在可复用的模板工作区 |
| 画布 / 配色 / 字体 / 页面节奏 | 由 `ppt_brief.py` 确定性推导，写进 `spec_lock.md` |
| 讲者备注 / 动画 / 旁白 | 取工作流默认值（备注开、动画与旁白关），写进 `design_spec.md §I` |
| 页面清单与每页内容 | 由两报告的结构化内容逐条生成，写进 `design_spec.md §IX` |

因此，原地的两阶段确认**已经没有可确认的内容**。此时再弹一个确认页，
不是"守流程"，而是**虚假交互** —— 用户只能对着一屏自己无法影响的默认值点确定。

**简化后的等价保证**（一个都不能少）：

- ✅ `design_spec.md` 与 `spec_lock.md` **仍然存在**，且必须过
  `project_manager.py validate`；
- ✅ 决策**有据可查** —— 两份文件本身就是上游流水线的决策证据；
- ✅ **不得伪造确认** —— 不启动 Confirm UI、不写 `confirm_ui/` 回执、
  不生成"Stage 1/Stage 2 已确认"的假状态。

> ⚠️ **反例（明确禁止）**：为了"对齐原流程"而生成一个空的
> `confirm_ui/template_options.json` 或伪造 `result.json`。
> 那会让后续任何审计都无法区分"用户确认过"与"没人确认过"。

---

## 4. 质量门（简化后**仍然**必须通过）

| 门 | 判据 | 不通过的处置 |
|---|---|---|
| Spec 结构门 | `project_manager.py validate` 通过 | 修 `ppt_brief.py` 的生成逻辑后重跑阶段 P |
| SVG 质检门 | `svg_quality_checker.py` **0 error** | 修对应页面 SVG，不得放行 |
| 导出后检门 | `validation/*.report.json` 状态为 `passed` 或 `passed-with-warnings`，且 PPTX 文件真的存在 | 按 `failure-recovery.md` 修**所属源产物**，不要降级要求 |
| 页面设计闭合 | `svg_output/` 是该页可见内容的**完整**来源；`design_spec.md` 与 `spec_lock.md` 不得提供页面上看不见的内容 | 补进 SVG，不得让导出器推断 |

---

## 5. 常见错误

- ❌ **自己再写一份 `design_spec.md`** —— 已有，且上游是决策主体。发现两份冲突时
  **停下报告**，不要在本地"择优"。
- ❌ **用脚本批量生成 SVG 页面** —— 违反 ppt-master 的硬规则，主 agent 必须逐页手写。
- ❌ **把 `--pages` 当作硬性页数** —— 它是预算区间；`design_spec.md §I` 的
  `Page Count` 必须与 §IX 的实际页数**逐页对齐**，改页数要同时改两处。
- ❌ **跳过 `svg_quality_checker.py`** —— 导出器会独立校验最终质检报告，
  缺报告或报告非 final 会直接非零退出。
- ❌ **把 PPT 素材来源写成"另行准备"** —— 用户已明确：素材固定取自
  《论文解读》的文献亮点与阶段 H2 的 review 结果。

---

## 6. 检查清单

- [ ] 阶段 0 的 `make_ppt` 为 `yes`（否则本阶段整体跳过）
- [ ] 阶段 H 与/或阶段 H2 已产出解读卡；两边都空时已拒绝启动并说明原因
- [ ] 已跑 `ppt_brief.py`，且 `project_manager.py validate` 通过
- [ ] `design_spec.md` 的 §I `Page Count` 与 §IX 实际页数一致
- [ ] `spec_lock.md` 的 `page_rhythm` 覆盖 P01 到最后一页，无缺号
- [ ] **未伪造任何 Confirm UI 回执**，未启动确认服务器
- [ ] 🆕 **图谱已接入**（若 `make_graphs=yes`）：§VIII 里有 `Acquire Via: user` /
      `Status: Existing` 的行，文件确实在 `<project>/images/` 内
- [ ] 🆕 **图谱页版式未缩到读不清**：整幅铺放 + 角落叠标题，`Crop Policy: no-crop`
- [ ] 🆕 **方向综述整理页**的口径与《研究方向综述.md》一致（同源数据）
- [ ] `svg_output/` 每页由主 agent 手写，未使用生成器
- [ ] `svg_quality_checker.py` 报 0 error
- [ ] Step 7 三条命令**串行**执行，逐条确认成功
- [ ] 导出的 PPTX 与 `validation/*.report.json` 均已产出
- [ ] 材料来源如实标注为《论文解读》+《研究方向综述》，未引入外部新事实
