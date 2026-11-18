# 阶段 0：准备阶段

> 本文件由主 `SKILL.md` 拆分而来（按链分组，懒加载）。
> **红线与全局契约遵守主 `SKILL.md`**（硬性要求 1–9、§5.1 分层获取、§5.2 鼠标政策、
> 闸门/数据契约），本文件不复制。进入本阶段才读本文件。

### 阶段 0：准备阶段（**原有链路的起点**）

> **目的**：把「只有用户能定」的选择**一次性前置**，避免跑到一半才发现
> 用户只要一份 Markdown 列表、或反过来漏掉了本该做的综述网络。
> 这些答案落盘为**单一事实源** `.work_cdp/prepare_plan.json`，
> 后续每个阶段开头只问它一句。

**执行方式**：agent 在对话中**逐项确认**下列 13 个问题，收齐后调用脚本落盘。
**不要**让用户面对交互式命令行 —— 脚本是记录器，不是提问器。

> [STOP] **必须分项确认，不得打包**（硬性要求，见文件头第 8 条）：
>
> - **一项一问**：每个选项单独给出**取值、默认值、以及选它的后果**，由用户逐项回答。
> - **不得合并**：把 N 个互相独立的选项并成一句「是否照默认跑」「其余按默认可以吗」
>   属**违规**。**尤其禁止**把**输出物格式（#1）**、**是否翻译（#2）**、
>   **是否制作 PPT（#6）**、**综述解读档位（#9）** 合并成一次确认 ——
>   这四项互相独立，必须**分别**确认。
> - **不得替用户默认**：未获明确答复的项按「**不新增**」处理（保守），
>   并在阶段产出与交付说明里如实标注「该项未确认、未执行」。
> - **判据**：**用户能否对每一项单独说「是 / 否」**。做不到就是合并确认，必须重问。
> - **时点**：确认在**阶段 0 开始之前**完成；跑到中途才想新增选项，
>   必须**停下来回到阶段 0** 补确认，不得边走边加。
>
> 确认记录与被跳过项一并写入 `run_journal.jsonl`，逐项答复落盘为
> `.work_cdp/prepare_plan.json`（单一事实源）。

| #  | 问题                                                                  | 取值                              | 默认                 |
| -- | --------------------------------------------------------------------- | --------------------------------- | -------------------- |
| 1  | **输出文件格式选择**：PDF、Markdown，还是两者都要？             | `markdown` / `pdf` / `both` | `markdown`         |
| 2  | **是否进行翻译环节**（全文中文翻译 → PDF）？                   | 是 / 否                           | 否                   |
| 3  | **是否反向查找相关参考文献**，形成该研究方向整体综述网络？      | 是 / 否                           | **是**         |
| 4  | **是否绘制知识图谱**（overview / 词云 / 方向聚类）？            | 是 / 否                           | **是**         |
| 5  | 方向聚类看最近几年？                                                  | 2 / 3 / 5                         | 3                    |
| 6  | **是否制作相关 PPT**（文献亮点解读 + 综述结果 + 图谱）？        | 是 / 否                           | 否                   |
| 7  | **是否执行本科科研辅助前置链路**（阶段 N / S / R / R2）？       | 是 / 否                           | **是**         |
| 8  | **论文解读档位**（阶段 H，作者本人的论文）                      | `L1` / `L2` / `L3`          | **`L2`**     |
| 9  | **综述解读档位**（阶段 H2 研究方向综述 / 阶段 R2 领域概貌）     | `L1` / `L2` / `L3`          | **`L3`**     |
| 10 | **论文解读引擎**（阶段 H）：原有解读卡 / 额外产出 16 节深读卡？ | `legacy` / `paper-card`       | **`legacy`** |
| 11 | 是否输出「有代表性文献」的参考文献列表（GB/T 7714）？                 | 是 / 否                           | 是                   |
| 12 | 是否对反向查得的文献做去重与相关性整理，并据此产出 review？           | 是 / 否                           | 是                   |
| 13 | PPT 目标页数（简化 Strategist 用，无需再交互）                        | `short`/`standard`/`long`   | `standard`         |

> ### 为什么档位也要在阶段 0 问
>
> 档位决定解读写多深，而**解读是这套流程最贵的产物**（并行 subagent 逐篇精读）。
> 跑到阶段 H 才发现用户只要一份速览表，等于把几十万字符的精读白做了一遍；
> 反过来，跑完才发现要进综述，则要**整批重派**。
> 所以档位与「要不要做 PPT」同级：属于**只有用户能定、且影响下游全部解读阶段**的选择。
>
> - **默认 `interpret_tier=L2`（详细）**：阶段 H 的产物是**逐篇卡片**，
>   正是 L2 的目标形态。要恢复到旧版等深的全量解读，选 `L3`。
> - **默认 `review_interpret_tier=L3`（完整）**：阶段 H2 与 R2 都是
>   **独立成篇**的综述类交付物，低档会丢掉排除性证据与作者语气限定 ——
>   而那正是综述读者最需要的部分。
> - [STOP] **三档只差细节量，不差断言强度**：同一个发现在三档的动词强度一致，
>   且与原文同级。这一点在阶段 H 的 L1 上最容易被违反（"表格要好看"）。
> - 规范定义、句式库与离线校验器见 skill **`paper-interpretation-writing`**。
> - 两个档位是**参数不是开关**（不控制任何阶段是否执行），
>   所以用 `--tier` 查询、用 `--set` 修改。

> ### 为什么「解读引擎」也要在阶段 0 问
>
> 引擎与档位同级：它也**只影响阶段 H 写多深、多贵**，不影响任何阶段是否执行。
>
> - **默认 `interpret_engine=legacy`**：每篇只产出既有 11 字段解读卡，
>   下游（《论文解读》汇总 / PPT / 知识图谱）零改动。
> - `interpret_engine=paper-card`：在原有卡之外**额外**产出 16 节 Paper Card
>   （已并入的 `nature-paper-card`，**流程内，无外部依赖**），
>   把图表清单、三态来源定位、五类溯源标签，以及「作者自述局限」与
>   「Agent 批判」分列带进阶段 H；交付前强制跑**结构审计 + 数字回查原文**。
> - [STOP] **只增不减**：11 字段卡照旧产出，所以引擎切换**不需要改动任何下游脚本**，
>   也让两种引擎共用同一批 `tasks/` `results/` 命名（历史卡不会错位）。
> - [WARN] **代价**：每篇 subagent 的工作量约增一倍（多写 16 节 + 跑质量门）。
>   适合代表作精读、组会材料、进组前深读；批量速览仍建议 `legacy`。
> - 引擎也是**参数不是开关**，所以用 `--engine` 查询、用 `--set` 修改。
> - 接线权威（产出契约、质量门、本地补丁登记）见
>   `paper-card-integration/SKILL.md`。

```bash
# 收齐答案后一条命令落盘（非交互）
uv run python scripts/prepare_stage.py \
    --set output_format=both --set translate=yes --set reverse_refs=yes \
    --set make_ppt=yes --set refs_bibliography=yes --set curate_review=yes \
    --set ppt_pages=standard \
    --set interpret_tier=L2 --set review_interpret_tier=L3 \
    --set interpret_engine=legacy      # 想要 16 节深读卡就改成 paper-card

# 全默认（保守：只出 Markdown，不翻译、不做 PPT，但做综述网络）
uv run python scripts/prepare_stage.py --defaults

# 查看当前计划 / 查看全部闸门 + 两处档位
uv run python scripts/prepare_stage.py --show
uv run python scripts/prepare_stage.py --check-all

# 单阶段闸门：退出码 0 = 执行；3 = 已按准备计划跳过（附原因）
uv run python scripts/prepare_stage.py --check translate

# 档位查询：stdout **只有** L1|L2|L3，便于 T=$(… --tier H) 捕获
uv run python scripts/prepare_stage.py --tier H     # 作者论文（默认 L2）
uv run python scripts/prepare_stage.py --tier H2    # 研究方向综述（默认 L3）
uv run python scripts/prepare_stage.py --tier R2    # 领域概貌（默认 L3）

# 引擎查询：stdout **只有** legacy|paper-card，便于 E=$(… --engine H) 捕获
uv run python scripts/prepare_stage.py --engine H    # 默认 legacy

# 门控齐全性自检（每个阶段都有标签、每个闸门都指向真实阶段）
uv run python scripts/prepare_stage.py --audit
```

**闸门解析规则（`prepare_stage.py` 自动完成，无需人工推导）**

| 选项                                           | 关掉它 → 这些阶段跳过                                                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `undergrad_prelude=no`                       | 阶段**N / S / R / R2** 跳过（不影响原有链路 A→I；两条链路相互独立）                                                                     |
| `translate=no`                               | 阶段 F 整体跳过（**这是唯一决定 F 是否执行的选项**）                                                                                     |
| `output_format`                              | **不控制任何阶段** —— 纯参数：阶段 F 执行时决定要不要顺带渲染 PDF（`markdown` → 只出中译 Markdown；`pdf`/`both` → 另渲染 PDF） |
| `reverse_refs=no`                            | 阶段**D2 / G2 / H2** 全部跳过（下游依赖上游，自动收敛）                                                                                  |
| `refs_bibliography=no`                       | 阶段 G2 跳过（但不影响 D2 / H2）                                                                                                               |
| `curate_review=no`                           | 阶段 H2 跳过                                                                                                                                   |
| `make_graphs=no`                             | 阶段 K 跳过（PPT 仍可做，但不会带图谱）                                                                                                        |
| `make_ppt=no`                                | 阶段 P 跳过                                                                                                                                    |
| `interpret_tier` / `review_interpret_tier` | **不控制任何阶段是否执行** —— 参数，只决定解读写多深                                                                                   |

> **依赖收敛要点**：`reverse_refs=no` 时，即使 `refs_bibliography=yes`、
> `curate_review=yes`，G2 与 H2 也会被**自动关掉**并在计划里写明
> 「← 由 reverse_refs（上游阶段 D2 未执行）关闭」。没有参考文献可取时，
> 这两个阶段无从执行 —— 这是**收敛**，不是静默跳过。

> **`output_format` 只影响产物格式，不影响是否下载原文 PDF**：
> 原文 PDF 是阶段 H 解读的输入，**一律要下**。

**产出**：`.work_cdp/prepare_plan.json`（含 `_gates` 与 `_gate_reasons`）。
