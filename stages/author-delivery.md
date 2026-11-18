# 阶段 P/I：PPT 与收尾

> 本文件由主 `SKILL.md` 拆分而来（按链分组，懒加载）。
> **红线与全局契约遵守主 `SKILL.md`**（硬性要求 1–9、§5.1 分层获取、§5.2 鼠标政策、
> 闸门/数据契约），本文件不复制。进入本阶段才读本文件。

### 阶段 P：PPT 生成（可选，整合 ppt-master）

> **闸门**：`uv run python scripts/prepare_stage.py --check P`
> （退出码 3 = 准备阶段 `make_ppt=no`，本阶段跳过）
>
> **执行手册**：`ppt-integration/SKILL.md`（**必读**，含简化的 Strategist 说明与质量门）

**材料来源（用户指定，不需另行准备）**：

1. 《`<作者名>_论文解读.md`》 —— 该作者**文献亮点解读**
2. 《`<作者名>_研究方向综述.md`》 —— 阶段 H2 的 **review 结果**

```bash
# ① 生成材料 + design_spec.md + spec_lock.md（一条命令）
uv run python scripts/ppt_brief.py --pages=standard
# ② 页面 SVG 由主 agent **手写**（ppt-master Step 6；不得脚本批量生成）
# ③ 转义预检：手写 SVG 里的裸 `<` / 裸 `&` 会让 XML 无法解析；
#    先跑这条（定位到行 + 给修法），再跑上游质检 —— 上游只报 invalid token，看不出根因
uv run python scripts/ppt_brief.py --check-svg <project>/svg_output
# ④ svg_quality_checker.py 必须 0 error
# ⑤ Step 7 三条命令串行导出：total_md_split → finalize_svg → svg_to_pptx
```

**本流程对 ppt-master 的改动 = 简化 Strategist Phase**：

| 原环节                                                     | 处置                                                                                                                                                                                                           |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Step 3 模板候选准备                                        | **跳过**（不套模板，走自由设计）                                                                                                                                                                         |
| Step 4 两阶段**阻塞式确认**（Confirm UI / 聊天确认） | **跳过交互，保留产物** —— `design_spec.md`（含 I–X 全节）与 `spec_lock.md` 由 `ppt_brief.py` **确定性生成**，因为阶段 0 已把受众/意图/页数/画布等选择收齐，**原地已无可确认的内容** |
| Step 1–2 / 5 / 6 / 7                                      | **不改**：source 导入、图像获取触发、**手写 SVG**、串行导出与导出后检全部保留                                                                                                                      |

> [STOP] **不得伪造确认**：不启动 Confirm UI、不写 `confirm_ui/` 回执、
> 不生成"Stage 1/Stage 2 已确认"的假状态。上游流水线就是决策主体，
> 两份 Spec 文件就是决策证据。详见 `ppt-integration/SKILL.md` §3。

**自动接入阶段 K 的图谱与方向综述整理**

| 项                      | 做法                                                                                                                                                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 图谱                    | 从`.work_cdp/graph/*.png` 收集，**复制进 `<project>/images/`**，写入 `design_spec.md §VIII`（`Acquire Via: user`、`Status: Existing`，故 Step 5 不会去"获取"它们、不触发任何抓取），并在 §IX 生成整幅铺放的页面 |
| 为什么用 PNG 而不是 SVG | PPTX 的`<image>` 内嵌对 SVG 的支持随 PowerPoint 版本而异，栅格图最稳。阶段 K 默认只出 SVG，所以 `ppt_brief.py` **检测不到 PNG 时明确提示补跑 `--also-png`**，而不是悄悄少放一张图                                     |
| 方向综述整理            | 新增「三、方向综述整理」材料节与对应页面：每个方向 → 最紧密综述 + 已确认结论 + 未解决问题。数据源与《研究方向综述》**同源**，保证口径一致                                                                                  |
| 关闭                    | `--no-graphs` 可整体不接入                                                                                                                                                                                                      |

**产出**：`ppt-master/projects/<项目名>/exports/<项目名>_<时间戳>.pptx`
＋ `validation/*.report.json`。

---

### 阶段 I：收尾

- 校验 `<作者名>_paper_Exports/` 下 PDF 完整性（`store.validate_pdf`）与**页数合理性**
  （正文一般 ≥6 页；2 页件须排查是否为补充材料/图注页）。
- 用 `uv run --with pypdf python scripts/inventory_build.py` 生成 `全文清单.md`
  （哪些拿到全文、来源途径、页数、哪些只有摘要、原因）。
  > **「拿到全文」= PDF ∪ 网页正文**：第一层 EPMC fullTextXML、第二层-B 仓库副本
  > 只产正文不产 PDF，只数 PDF 会把已到手全文的篇目报成「未获取」。
  > 题录覆盖面 = `crossref_all.json` ∪ `.work_cdp/reviews/reviews.json`
  > （**入门链路没有 Crossref 总表**，只认前者会漏报）。
  > 题录总表优先读 `crossref_all.json`，回退历史名 `master.json`；
  > 阶段分组来自 `.work_cdp/periods.json`（**未配置就不分组**，不内置任何作者履历）。
  >
- 用 `rename_to_title.py` 确认全部 PDF 已按**题名**命名且索引已登记。
- 生成 `<日期根>/交付物索引.md`：`uv run python scripts/make_delivery_index.py`
  （**作者链路模式下由脚本产出**：产物清单 + 数据核验 + 限制申报）。
  交付总览、数据核验链、复现步骤与本次改造台账由主 agent 按需补充。
  > 索引**按链路分模式**：为什么必须分 —— 只认入门链路布局时，作者链路会得到
  > 一份「14 行全写未产出、却仍 exit 0」的答非所问交付物（取证见
  > [实测结果汇总.md](实测结果汇总.md#i-1)）。现在两条链路的产物同处**一个日期根**
  > （入门 `outputs/…`、作者 `<作者名>/…`），索引写在该日期根下，一份覆盖两条链路；
  > 只跑了一条就只出那一条；两条都没产物则 `exit 2` 并说明该跑什么。
- `report_artifacts` 申报交付物。
