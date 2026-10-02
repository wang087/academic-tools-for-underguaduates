# 流程 · 本科生文献检索与科研辅助（v8.0）

> ## 📌 目录与术语约定（**默认，无需每次说明**）
>
> | 说法 | 指什么 |
> |---|---|
> | **"流程" / "本流程"** | 默认指 **`流程1/`**（本目录）。未特别说明时，所有改动与执行都以这里为准 |
> | **"工作文件夹"** | 仓库根目录 **`scientific-agent-skills/`**（本目录的上一级） |
> | **"工作根目录文件"** | 如 `研究方法与关键词总表.md`、`skills/` 等，都在**工作文件夹**下 |
>
> ⚠️ `流程/`（**无数字后缀**）是旧的 **v7.2** 分支，与 `流程1/`（**v8.0**）已分叉。
> 除非用户明确点名 `流程/`，否则一切以 `流程1/` 为准。

> ## ⛔ 使用本流程的硬性要求（最高优先级，先读）
>
> **本文件夹（`流程1/`）中的流程即为权威流程。执行本任务时必须：**
>
> 1. **使用本流程** —— 依 [SKILL.md](SKILL.md)（主流程）与各子流程
>    （[subagent-driven-review](subagent-driven-review/SKILL.md) 阶段 H/H2/R2、
>    [ppt-integration](ppt-integration/SKILL.md) 阶段 P）逐步执行：
>
>    ```
>    【前置链路：本科科研辅助 —— 一句话意向 → 领域概貌】
>    N 大方向确定（无交互）
>      → S 匹配细分专业（抓导师 → 归类 → 等用户选择）
>      → R 平行检索优质综述（Scholar / PubMed / arXiv 并行，综述优先）
>      → R2 领域概貌解读
>    【原有链路：作者论文分析 —— 按需接入】
>    0 → A → B → C → D → D2 → E → F → G → G2 → H → H2 → K → P → I
>    ```
> 2. **不得擅自变动更改** —— 包括但不限于：跳过阶段、改写抓取方式、更换判定标准、
>    绕过 `scripts/` 里的现成脚本去自编替代品。换作者/换学部时**只传参、不改脚本**。
> 3. **确需变更时** —— 先说明「改什么、为什么、影响范围」，**获得同意后**再改；
>    改完必须在 SKILL §9 故障台账追加记录并同步本文件。
> 4. **红线** —— SKILL §5.1 **分层获取策略**、§5.2 鼠标自动化禁令优先级最高。
>    ★ §5.1 是**分层**规则：摘要 / 综述题录 / 白名单开源库**允许直连**，
>    **原文全文必须走 CDP**；第一层取不到转 CDP。
>    ★ **学部官网师资页属公开元数据**，走第一层直连（阶段 S 即按此实现），无需 CDP。
> 5. **不要臆造事实** —— 导师方向、题录、被引一切以抓取/接口返回为准；
>    解析不到就如实标注，**不得用印象补全**。
>
> **判定顺序**：`SKILL.md` > 子流程文件 > 任何外部或历史资料。
> 若流程目录下存在来源不明的第三方 `SKILL.md`，**一律以本流程为准**。

> 📖 **第一次用？先看 [完整链路.md](完整链路.md)** —— 端到端操作手册，
> 从用户一句话走到「导师名单 + 优质综述 + 领域概貌」，
> 每条命令都已实跑验证，并把实测踩到的坑（官网分页参数不可信、
> arXiv 限流、总表是方法分类而非主题索引）逐条写明。

---

## 🎓 本流程解决什么问题（v8 大方向调整）

面向**本科生**的三个真实痛点：

| 痛点场景 | 用户会怎么说 | 流程怎么接 |
|---|---|---|
| **大学生研究计划**（国创/大创/校创）选题 | "想做个跟阿尔茨海默有关的项目，不知从哪下手" | N 拆解意向 → S 给方向与导师 → R 给该方向综述 → R2 给概貌 |
| **本科生进实验室**找导师 | "我是中国科大生医部的，想找做免疫的老师" | S 直接抓该学部全部导师并按方向归类 |
| **"科学与社会"类课程**文献查找 | "写基因编辑伦理的综述，找几篇权威综述" | N/R 走综述优先检索，产出可直接引用的概貌 |

**交付物**（本科入门场景通常到 R2 即可）：

```text
.work_cdp/
├── topic_plan.json              阶段 N：大方向 + 中英关键词群
├── mentors/
│   ├── 导师名单与方向归类.md     阶段 S：分方向的导师表格（给人看）★
│   ├── mentors_grouped.json      阶段 S：机读归类结果
│   └── roster_detail.json        阶段 S：全部导师 + 方向原文
└── reviews/
    ├── reviews.json             阶段 R：选定的综述清单（6–12 篇）
    └── 领域概貌.md               阶段 R2：面向本科生的领域概貌 ★
```

---

## 原有链路（作者论文分析，按需接入）

当用户**已锁定某位导师**、想深挖其成果时，从**阶段 0** 接上原有链路：

**「准备阶段确认 → 论文数据采集 → 题录核实 → 同名消歧 → 反向查找参考文献形成综述网络 → 全文获取 → 中文翻译 PDF → 论文列表 → 代表性文献参考文献 → 深度解读 → 研究方向综述 → 知识图谱 → 可选 PPT」**

来源：2026-09 对中国科学技术大学 **刘波（Bo Liu, ORCID 0000-0002-1150-3709）**、
**臧建业（Jianye Zang, ORCID 0000-0003-2648-9783）** 与
**薛天（Tian Xue, ORCID 0000-0001-6187-122X，77 篇 / 75 篇全文 / 75 篇中译）** 的实战分析。

> 📌 **关于本文档中的作者姓名 / ORCID / DOI（必读）**
>
> 本 README、`SKILL.md` 与 `示例数据/` 中出现的**具体作者姓名、ORCID iD、DOI、
> 单位与论文数量**，全部是**历史案例的实录**，用于说明流程的来源与验证过程，
> **仅作案例记录，不是任何脚本的默认值或配置来源**。
>
> **可执行脚本（`scripts/*.py`、`run_all.sh`）内不含任何作者硬编码信息** ——
> 姓名、ORCID、单位、署名识别词、阶段分组、待测 DOI 一律由**运行时提供**
> （命令行参数或环境变量），缺失时脚本会显式报错退出而不是套用旧案例的值。
> 相关环境变量：`PAPER_WORKDIR`、`PAPER_AUTHOR`、`PAPER_AUTHOR_EN`、`PAPER_ORCID`、
> `PAPER_AFFIL`、`PAPER_AUTHOR_NAMES`、`PAPER_NAME_TOKENS`、
> `PAPER_SURNAME`、`PAPER_GIVEN`、`PAPER_PERIODS`、`PAPER_TEST_DOI`、`CDP_PORT`。
>
> 若需按新作者复用，请**只通过上述变量/参数传值**，不要改动脚本。

> **v2（2026-09-11）**：修复「死机 / 误触发 CAPTCHA / 泛词误报封禁」三类缺陷，
> 引入**多实例并行 CDP**、**统一日志**、**精确挑战判定**、**站点级熔断**、**分主机节流**。
> 详见 [SKILL.md](SKILL.md) § 9「故障修复记录」。
>
> **v3（2026-09-13，薛天案例实战沉淀）**：在 v2 基础上完成 **8 项脚本改造**，覆盖
> ① PDF 题名命名 ② 反爬探测强化 ③ Cookie 弹窗自动化 ④ CDP 启动/运行提速
> ⑤ CAPTCHA 弹窗可靠性 ⑥ 直连禁令固化 ⑦ 中文翻译渲染工具 ⑧ 收尾与索引脚本。
> 详见下文「v3 脚本改造清单」与 [SKILL.md](SKILL.md) § 9.1。
>
> **v4（2026-09-14，郭傲案例沉淀）**：新增 **CDP 强制抓取禁令**（最高优先级），
> **移除「OA 直链纯 HTTP 直接获取」条目**（`fetch_oa_direct.py` 停用）；
> 新增同名消歧全链路脚本（`orcid_probe` / `disambiguate` / `enrich_crossref_authors`）、
> 中译素材链脚本（`prep_paper` / `crop_by_images` / `extract_figures` / `jats_to_text` /
> `translation_build`）与收尾脚本（`verify_pdfs` / `inventory_build`）；
> 并修复 `download_all_fulltexts.py` 的 **CAPTCHA 弹窗门控**误用。详见
> 「v4 脚本改造清单」与 [SKILL.md](SKILL.md) § 9.2。
>
> **v7.1 补充**：新增 `pack_release.py`，把全流程 + 全部依赖打包为可独立运行的
> `流程1/`（路径重写、依赖随包、sha256 可核验）。包内离线路 23/23 通过，
> 且自检断言阶段 P **确实用了包内 `vendor/ppt-master`**。详见
> [SKILL.md](SKILL.md) § 9.5-I。
>
> **v7（2026-09-19，文献综述 / PDF / 参考文献三线整合）** —— 五类改动：
> ① 新增**阶段 0「准备阶段」**：把「输出格式 / 是否翻译 / 是否反向查参考文献 /
>    是否做 PPT」等只有用户能定的选择前置，落盘 `.work_cdp/prepare_plan.json`，
>    后续每阶段以 `--check` 闸门决定是否执行（含**依赖收敛**）；
> ② 新增**阶段 D2「反向查找参考文献」**：取 `referenced_works` 倒推综述网络，
>    高质量 = 核心期刊（显式清单）**或**被引 > 200，**优先 Review**；
> ③ 新增**阶段 G2「代表性文献的参考文献列表」**（GB/T 7714，文章 → 参考文献两部分）；
> ④ 新增**阶段 H2「去重与相关性整理 → 研究方向综述」**（剔除项逐条留档，不静默丢弃）；
> ⑤ 新增**阶段 P「PPT 生成」**：整合 ppt-master 并**简化其 Strategist Phase**。
> 另：解读模块新增**受控标签词表**与**学科通用词族**，消除并行 subagent 的标签漂移。
> 新增 `selftest_pipeline.py` 端到端自检（联网 33 项 / 离线 18 项），
> 它挡下过 4 类静态看不出的缺陷。详见 [SKILL.md](SKILL.md) § 9.5。
>
> **v7.2（2026-09-19，去重 / 知识图谱 / 综述策略 / PPT 接入）** —— 四类改动：
> ① **消除重复输出**：`论文列表` / `论文解读` / `研究方向综述` 只写**一个文件**，
>    不再另写 `_完整版.md` 别名；
> ② **新增阶段 K「知识图谱」**（H2 之后、P 之前）：overview（Louvain 上色 + 记忆连接）、
>    词云（剔除 mice 等误导词，聚焦方法与范式）、方向聚类（近 N 年热点）；
>    **SVG 优先 / 失败退 PNG / 背景透明 / 字符清晰**；
> ③ **综述提取改版**：**被引 200 次不再是硬性要求**，改为「综述 + CNS 及高水平刊物 +
>    近年」加权优先，并为综述取 **OA 全文或详细摘要**以提高置信度；
> ④ **PPT 接入图谱与方向综述整理**。详见 [SKILL.md](SKILL.md) § 9.5-J。
>
> **v7.3（2026-09-19，知识图谱可读性修复）** —— 来源：刘波案例实跑。阶段 K 三张图
> **全部生成成功、自检全绿**，但逐张肉眼验收**没有一张达标**（§9.5-J 那句
> 「"图标出来了"和"图能读"是两回事」再次复现）。**变更已事先征得用户同意**。六处修复：
> ① **词云中文包围盒口径**（旧版 `len×0.58×字号` 等于把中文当半角，宽度低估约 40%
>    → 碰撞检测**系统性漏判**；改为逐字符判宽：CJK 1.0×、ASCII 0.55×字号）；
> ② **方向归类兜底语料**由「仅题名」扩为「题名+摘要+刊名」，画像语料由 `methods`
>    扩为 `methods + key_20`（旧版实测 15 条近 3 年文献**全部「未分类」**）；
> ③ **overview 综述挂载**：无 review 卡的条目复用关键词兜底，实测**挂上 0 条 → 9 条**，
>    并新增 `attach_stats["how"]` 逐路径报数；
> ④ **overview 标签避让**（同角度逐级径向外推，放不下则跳过并如实计数）；
> ⑤ **记忆连接图例不再截断**（旧版每行以 `…` 收尾，「为何要一起读」整列信息白放）；
> ⑥ **方向聚类只标注每个 (方向,年份) 被引前 3 条**并垂直错开（旧版几十个标签叠成黑线）。
> 详见 [SKILL.md](SKILL.md) § 9.6（含**仍未解决**项的如实记录：该语料下方向聚类 x 轴
> 塌缩为单一方向，属归类语义问题，本次未改）。
>
> **v7.4（2026-09-19，知识图谱绘制改版）** —— 用户指定（并**明确指示本步不受
> 「不得擅自改动」条款约束**）。两类改动：
> **A. 方向聚类**：① **补图例**（热点簇配色 + 点大小标尺）；② **补对比面板**
> （右侧横向条形给出各方向文献数，x 轴刻度带条数）；③ 方向按文献数**降序**；
> ④ 窗口默认 **3 年 → 5 年**。
> **B. overview**：⑤ 节点由散点改为**圆角矩形 + 文字内嵌**（flat 填充、**无阴影高光**），
> 并新增框尺寸估计与**迭代防重叠**；⑥ **保留核心主干**（综述限前 12、方法限前 10 且需被
> ≥3 篇引用，裁剪数量如实报出）；⑦ **删除显示用标签的「自定义:」前缀**（**仅改图上显示**，
> 原值仍保留在 `graph_data.json` 与报告中）；⑧ Louvain 社区改用**冷色系色板**避免与
> 「最紧密综述」的强调橙撞色。
> **C. 顺带修掉被对比面板暴露出来的归类缺陷**（⛔ 宽画像通吃等 6 项）：IDF 加权 +
> 按画像规模归一化 + 画像计入全部方向标签 + df 截断 + 词去重 + 噪声词表扩充；
> 实测方向分布由 `41/1/1/1` 变为跨 5 个方向的 `20/9/9/2/2/2`。
> 详见 [SKILL.md](SKILL.md) § 9.7（含**仍未解决**项：关键词兜底仍属启发式，
> 「分子与化学 20」不应被当作权威分类）。
>
> **v8.0（2026-10-02，本科科研辅助改造）** —— **大方向调整 + 三个阶段新增**。
> ⚠️ **原有链路（0 → A → … → I）编号、语义、脚本契约全部不变**，
> 只是不再是默认入口。四类改动：
> ① **新增阶段 N「大方向确定」**（**无交互**，位于所有环节之前）：把用户一句话意向
>    拆解为 `school / unit / topic / level`，并用**工作文件夹已有文件**
>    （`研究方法与关键词总表.md` + `templates/关键词词族_学科通用.json` +
>    `templates/方法范式标签词表.json`）匹配关键词群，产出中英双语检索词 →
>    `.work_cdp/topic_plan.json`。**不新建关键词表**。
> ② **阶段 S「匹配细分专业」**：从**学部/学院官网**自动抓取导师名单与研究方向，
>    按方向归类后**停下等用户选择**。
>
>    > ### ★ v11 硬性规定：只从学部官网取，不用学校教师主页系统
>    >
>    > 学校教师主页系统（`faculty.ustc.edu.cn`、`person.zju.edu.cn`）有两个硬伤：
>    >   ① 只登记「已在系统里维护主页」的教师，覆盖面不全；
>    >   ② **大量页面根本没有「研究方向」栏位** —— 实测 USTC 生医部 237 人中
>    >      130 人没有（页签存在但内容是 `<p>暂无内容</p>`）。
>    >
>    > 学部官网由**学院自己维护**，方向/邮箱/职称常直接写在列表页上。
>    > **故统一以学部官网为准。**
>    >
>    > **配置驱动**：学校/学院/栏目/解析规则全在 `templates/学部官网源.json`，
>    > **新增学校不必改脚本**。主脚本 `scripts/harvest_faculty.py`：
>    > ```bash
>    > uv run python 流程1/scripts/harvest_faculty.py --list-units
>    > uv run python 流程1/scripts/harvest_faculty.py --unit zju --out outputs/06_浙大生科院
>    > uv run python 流程1/scripts/harvest_faculty.py --unit ustc:life-med
>    > ```
>    >
>    > 两种列表模板：`star`（列表页已含方向，浙大生科院）/ `fetch`（进详情页，中科大生医部）。
>    >
>    > 实测（2026-10-02）：
>    >
>    > | 学校 | 栏目 | 人数 | 有方向 | 耗时 |
>    > |---|---|---|---|---|
>    > | 浙江大学 生命科学学院 | 12 | **153** | **110（72%）** | 列表 18s + 详情 100s |
>    > | 中国科学技术大学 生命科学与医学部 | 5 | 161 | 112（70%） | — |
>    >
>    > 浙大各栏目抓到的条数与官网「总共 N 条」逐项吻合，分页无遗漏。
>    >
>    > **已弃用**：`harvest_mentors.py`（学校教师主页，默认 `exit 2`）、
>    > `harvest_colleges.py`（功能并入 `harvest_faculty.py`）。
>    > 保留代码供查阅历史数据与回滚，确需运行加 `--allow-deprecated`。
> ③ **新增阶段 R「平行检索优质综述」**：Google Scholar / PubMed / arXiv
>    **三线并行 + 综述优先**，**复用**工作文件夹下既有 skill
>    （`paper-lookup` / `literature-review` / `research-lookup` /
>    `citation-management` / `scientific-critical-thinking` / `bgpt-paper-search`），
>    **不从头自编检索逻辑**。
>
>    > ### ★ v13：用户给出检索条件后**自动查该领域论文**（PRISMA 系统检索链）
>    >
>    > 四步链，设计参照工作文件夹 `academic-research-skills-3.22.2` 的
>    > `deep-research/agents/bibliography_agent.md`（检索策略六要素 / 两轮筛选 /
>    > 分布偏斜告警）、`references/systematic_review_toolkit.md`（PRISMA 2020）、
>    > `references/source_quality_hierarchy.md`（证据金字塔 + 掠夺性红旗）。
>    >
>    > ```bash
>    > # ① 条件 → 可复现的查询计划（不联网）
>    > uv run python 流程1/scripts/search_plan.py --topic "免疫与感染" \
>    >     --topic-en "innate immunity" --terms-en "NK cell,macrophage" \
>    >     --from-year 2021 --doc-types review --oa-only --out outputs/07_检索
>    >
>    > # ② 多源并行检索 + 归一化去重（复用既有 paginate.py 取数）
>    > uv run python 流程1/scripts/search_run.py --plan outputs/07_检索/search_plan.json
>    >
>    > # ③ 两轮筛选 + PRISMA 计数 + 证据分级（含摘要补齐）
>    > uv run python 流程1/scripts/search_screen.py --raw outputs/07_检索/search_raw.json
>    >
>    > # ④ PRISMA 报告
>    > uv run python 流程1/scripts/search_report.py --screened outputs/07_检索/search_screened.json
>    > ```
>    >
>    > 之后接既有的 `reviews_prepare.py` → 并行 subagent 解读 → `reviews_build.py`。
>    >
>    > **实测**（浙大生科院「免疫与感染」，Europe PMC + OpenAlex + Crossref）：
>    > 识别 **180** → 硬条件后 120 → Pass1 相关性 86 → **纳入 85**；
>    > 摘要补齐 107/130；耗时 检索 42s + 筛选 447s。
>    >
>    > ⚠️ **三个必须知道的局限**（报告里也逐条写明）：
>    > 1. 各源结果均被 `--max-records` 截断，**不是穷尽检索**；
>    > 2. Pass1 是**词面重叠打分，不是语义判断**，换词同义的文献可能漏掉；
>    > 3. 证据级别**仅依据元数据标注**，不代表文献本身质量合格。
>    >
>    > 另一个实测发现：`paginate.py` 请求 Europe PMC 用 `resultType=lite`，
>    > **不含摘要**（180 条里仅约 50 条有摘要）。而 Pass1 依赖题名+摘要，
>    > 故 `search_screen.py` 内置**摘要补齐**（按 DOI 走 `resultType=core`）。
>    > 没有改共享的 `paginate.py`（它有独立测试套件，不宜动）。
> ④ **新增阶段 R2「领域概貌解读」**：**复用**
>    `subagent-driven-review/SKILL.md` 的并行 subagent 机制，
>    产出面向本科生的《领域概貌.md》（术语速查 / 争议点 / 推荐阅读顺序 / 选题切入点）。
> 另：确立**目录与术语默认约定**（"流程"默认= `流程1/`，"工作文件夹"= 仓库根），
> 并补充红线「学部师资页走第一层直连」与「不得臆造事实」。
>
> **v12.1（2026-10-02，arXiv 取数改 Python）** —— 用户指令 + 环境实测。
> 本机 DSH 沙箱（`workspace-write`）内命令进程为 **Low 完整性级别**，
> Schannel/SSPI 的 `AcquireCredentialsHandle` 失败（`SEC_E_NO_CREDENTIALS`），
> **所有走系统 TLS 栈的 HTTPS 客户端一律不可用**（系统 curl / Git curl / .NET / certutil
> 实测全挂；打**明文 HTTP 却正常**；对照实验：完整访问下行令牌为 Medium，同一 curl 返回 200）。
> Python 自带 OpenSSL、不经 SSPI，不受影响。
> 故把 `route_databases.py` 中**唯一**用 curl 的 arXiv 分支改为 Python：
> 取数写成 `.work_cdp/arxiv_query.xml`，再交由 `arxiv_atom.py` 读该文件
> （**不用管道、不用重定向** —— 避免 shell 按控制台编码重编码、破坏非 ASCII 作者名）。
> **只改这 1 处**，其余 6 个库的生成命令逐字未变。
> 实测：`py_compile` 通过、`--self-test` 通过、生成命令**原样执行** exit 0（返回 12 条）。
> 取证见 `直链OA测试/环境取证_curl与TLS.md`；详见 [SKILL.md](SKILL.md) § 9.13。
>
> **v12.2（2026-10-02，7 个脚本补 UTF-8 流守卫）** —— 用户指令。承接 v12.1：
> 修 arXiv 命令时发现 `route_databases.py` 在 **GBK 控制台（cp936）** 下崩在 `⛔` 上
> （`UnicodeEncodeError: 'gbk' codec can't encode character`），连生成命令都打印不出来。
> 三轮排查后确认**真正完全没有编码保护的只有 8 个**（多数「疑似」是误报：零宽字符只用于
> 正则/清洗、或早已用内联 `sys.stdout = io.TextIOWrapper(...)` 守卫），最终给
> **7 个**脚本补上与 `fetch_direct.py` **逐字一致**的 `_fix_streams()`：
> `check_challenge.py` / `download_all_fulltexts.py` / `extract_figures.py` /
> `grab_abstracts.py` / `render_html_pdf.py` / `route_databases.py` / `save_pdf_via_browser.py`。
> 第 8 个 `fetch_oa_direct.py` **未动**（v4 起已停用，且自带 stderr 的 UTF-8 包装）。该守卫
> 在编码已是 UTF-8 时直接 return，**UTF-8 环境零行为变化**。
> 实测（`chcp`=936、未设 `PYTHONIOENCODING`）：改前同一命令 exit 1 崩溃、改后 exit 0；
> 7 个文件 `py_compile` 全通过；「会打印且无守卫」静态归零；动态逐个验证 + 对照组均通过。
> 详见 [SKILL.md](SKILL.md) § 9.14。
>
> **v7.5（2026-09-23，overview 画幅与标题改版）** —— 用户指定。**只动 overview
> （`draw_overview` / `save_fig`），词云与方向聚类未动**：
> ① 画布由 `17×15` 改为 **16×10（严格 16:10）**，产物实测由 `2048×1610`（1.27:1）
> 变为 **`4800×3000`（1.6000:1）**，整幅铺放不再留边/被裁；为铺满宽画幅，
> 径向布局**改为椭圆**（`aspect="auto"`），框尺寸与防重叠换算**按轴分开**
> （`PT_PER_X` / `PT_PER_Y`）；
> ② `save_fig()` 新增 `tight` 参数（默认 `True`），overview 用 `tight=False`
> **按画布原尺寸导出**，画到画布外的文字会被裁掉 —— 记忆连接图注因此移入画布内；
> ③ 标题由「<作者名> 研究工作知识图谱（overview）」改为 **「<作者名> 研究工作图谱」**，
> **删除标题下方灰色解释小字**（其内容图例已逐条给出），标题字号 20→26 并加一条
> 居中短强调线；
> ④ **输出文件名不变**（`<作者名>_知识图谱_overview.svg|png`）——
> 该名字被 `selftest_pipeline.py` 与 `ppt_brief.py` 引用。
> 详见 [SKILL.md](SKILL.md) § 9.8。
>
> **v7.1（2026-09-19，获取策略分层）** —— 用户修订：
> 「对于获取**原文**的需求仍然禁止直链抓取；但对于**摘要、综述**或工作文件夹下
> **验证可行的开源数据库**可以直接获取，减轻压力。若直接获取尝试失败则转向 CDP 获取。」
> ① §5.1 由「CDP 强制抓取禁令」改写为**分层获取策略**（原文=第二层必须 CDP；
>    摘要/综述题录/白名单开源库=第一层允许直连；**第一层失败转第二层**）；
> ② 新增 `templates/直连白名单.json`（机器可读、**fail-closed** 执行）与
>    `fetch_direct.py`（多源自动降级）；`--self-test` 断言白名单不含出版社内容站、
>    内容层级只能是 metadata/abstract；
> ③ 修掉 `fetch_openalex_abstracts.py` **覆盖其它源摘要**的隐患。
> 实测：示例题录前 12 篇摘要 **12/12 命中、5.6 秒、零浏览器占用**。
> 详见 [SKILL.md](SKILL.md) § 9.5-H。
>
> **v6（2026-09-17，流程复核与加固）**：对流程目录做了一次完整复核，逐脚本核对
> 可执行性与前后契约，并落实四项指示（用本流程 / CDP 弹窗治理 / Cookie 自动关闭 /
> 论文总结规范）。主要改动：
> ① 全文目录规范名改为 **`<作者名>_paper_Exports`**（历史目录自动沿用）；
> ② CDP 启动参数补 **6 个弹窗抑制开关**（崩溃恢复气泡 / 错误对话框 / 重发表单 /
>    搜索引擎选择页 / 翻译气泡），新增 `PAPER_CDP_LEVEL`；
> ③ Cookie 脚本修 **三处实质缺陷**（固定定位弹窗被误判为不可见 / 只处理首个命中 /
>    shadow DOM 查不到）+ 消除重复点击，并新增**离线自检** `selftest_consent.py`；
> ④ 新增 `scope.py`，把**同名消歧结果接入下载与著录两侧**（此前 `disambiguated.json`
>    无人消费，消歧等于白跑）；
> ⑤ 阶段 H 改为**并行 subagent** 执行，`subagent-driven-review/SKILL.md` 已按本流程
>    重写（原为来源不明的第三方技能，含违禁的 HTTP 直取指引）；新增
>    `interpret_prepare.py` / `interpret_build.py`；
> ⑥ 修复**脚本契约断裂**（`enrich_crossref_authors.py` 读从不产出的 `master.json`、
>    authors 写成字符串致著录崩栈、`inventory_build.py` 同样只认 `master.json`、
>    `run_all.sh` 消歧步骤只打印不执行）等 **40 项**缺陷。
> 详见 [SKILL.md](SKILL.md) § 9.4（含逐项实测验证记录）。
>
> **v6.1（2026-09-18，郭傲案例实跑后二次加固）**：实跑暴露了三处**机制性**缺陷
> （不是笔误，是设计没到位 —— 跑一次不报错，但结果不对或不可靠）：
> ① **同名消歧重做**：新增**判据⓪ ORCID 溯源**（DOI 出自本人 ORCID 记录即确认，
>    这是 ORCID→Crossref 主路径上唯一可用的强判据）；采集 Crossref 逐作者单位；
>    修 `load_records` 不认 `{doi:记录}`、`author_blocks` 不认字典作者；
>    **取消会静默丢论文的 `drop` 判决**；排除辅助文件避免幽灵候选。
>    实测：候选 13 → 确认 **13**、排除 0、无法判定 0。
> ② **解读截断改头尾保留**：旧版从头部硬截，**恰好丢掉结论/量化数据/自述局限**
>    （全在结尾）。改为保留**头 60% + 尾 40%**，中段另存 `fulltext/` 可分段补读。
>    实测 5 篇受影响，重派后 **5 篇全部补读了中段**，捞回大量关键数据。
> ③ **命名稳定性**：任务包/解读卡改为**只由 DOI 命名**。旧版「序号_DOI」在排序
>    变化时会整体重编号 —— 实测重跑一次 prepare 后，**13 份解读卡里 9 份对不上号**
>    （轻则报缺卡，重则把 A 的卡当成 B 的结果）。另加陈旧文件清理与缺年份告警。
> 详见 [SKILL.md](SKILL.md) § 9.4 I 组。
>
> **v5（2026-09-14，周丛照案例）**：**脚本去硬编码**。`README.md` / `SKILL.md` / `示例数据/`
> 中的案例姓名、ORCID、DOI 予以保留（**仅作案例记录，已加注说明**）；
> 而**所有可执行脚本（`scripts/*.py`、`run_all.sh`）内的作者硬编码信息全部删除**，
> 改为运行时经命令行参数 / 环境变量传入，缺失时**显式报错退出**（不再静默套用旧案例值）。
> 涉及：`orcid_extract.py`、`orcid_pages.py`（原写死 ORCID URL）、
> `parse_orcid.py`（原写死作者名于 SKIP 表）、
> `check_access.py`（原写死待测 DOI）、
> `build_list.py`（原写死署名识别词 `liubo_pos`、刊名噪声表、阶段分组 `PERIODS`、
> 无 DOI 记录的臆造作者）、`disambiguate.py` / `enrich_crossref_authors.py` / `orcid_probe.py`
> （注释内案例姓名）。新增变量：`PAPER_AUTHOR_NAMES`、`PAPER_NAME_TOKENS`、
> `PAPER_SURNAME`、`PAPER_GIVEN`、`PAPER_PERIODS`、`PAPER_TEST_DOI`。
>
> ⛔ **三条最高优先级红线（v7.1 修订）**：
> **① 分层获取策略（§5.1，优先级最高）** —— **原文全文**（PDF / HTML 正文，**含 OA**）
> **禁止直连，一律经 CDP**；**摘要、综述题录、以及 `templates/直连白名单.json` 内
> 已验证的开源数据库允许直连**（`fetch_direct.py`）；**第一层失败转 CDP**。
> 禁止把白名单外的域名当成"开源库"直接取。
> **② 出版社内容站禁直连** —— 验证类站点
> （ScienceDirect/Cell/OUP/SAGE/Wiley/ACS/RSC/Springer/T&F）绝不用 `curl`/`urllib` 直连；
> **③ 鼠标自动化禁令** —— 绝不用 CDP `Input.*` 事件尝试通过人机验证（实测 `isTrusted=True`
> 也无效），人机验证一律**转人工**。
>
> > v4 的「CDP 强制抓取禁令」（全部内容一律走 CDP）已由上方 ① 取代；
> > 下方章节 ⑥「直连禁令」与 v4 记录保持原样，作历史回溯。

---

## 目录结构

```
流程1/
├── SKILL.md                              # ⭐ 技能主文件（工作流 + 工具清单 + 避坑指南）
├── README.md                             # 本文件（快速上手）
├── run_all.sh                            # 一键执行（建目录→起CDP→抓ORCID→Crossref→消歧→OA→下载→列表）
├── subagent-driven-review/SKILL.md       # ⭐ 子流程：阶段 H/H2 并行 subagent 解读手册（含综述网络版）
├── ppt-integration/SKILL.md              # ⭐ 子流程：阶段 P 整合 ppt-master（简化 Strategist）
├── scripts/
│   ├── ── 基础设施 ──────────────────────
│   ├── logutil.py                        #   统一日志（控制台+文件双写，UTF-8 安全）
│   ├── detect.py                         #   ⭐ 页面状态唯一判定源 + DOI→出版社映射
│   ├── store.py                          #   ⭐ PDF 命名/去重/校验唯一实现（<作者>_paper_Exports）
│   ├── scope.py                          #   ⭐⭐ 同名消歧排除名单唯一入口（下载/著录共用）
│   ├── selftest_consent.py               #   ⭐ Cookie 弹窗自动关闭的离线自检（真实 Edge，零网络）
│   ├── cdp.py                            #   CDP 客户端（端口参数化、硬超时、异常分类、CONSENT_JS）
│   ├── cdp_pool.py                       #   ⭐ 多实例池（启动/探活/调度/回收）
│   ├── start_edge_cdp.sh                 #   启动 CDP Edge（多实例、最小参数档位）
│   ├── renew_cdp_instance.sh             #   重建单个实例（换全新 profile）
│   ├── ── 数据采集 ──────────────────────
│   ├── orcid_extract.py / orcid_pages.py #   CDP 打开 ORCID 并翻页抓取全部成果
│   ├── parse_orcid.py                    #   页面文本 → 结构化 JSON
│   ├── orcid_probe.py                    #   ⭐ ORCID API 身份核验（任职/教育/关键词/works）
│   ├── fetch_crossref_all.py             #   并发拉取 Crossref 权威题录
│   ├── retry_crossref.py                 #   失败项串行退避重试
│   ├── enrich_crossref_authors.py        #   ⭐ 回填 Crossref 结构化作者全名 + 卷期页
│   ├── disambiguate.py                   #   ⭐⭐ 同名消歧（姓名+单位指纹+合作者网络）
│   ├── unpaywall_check.py                #   批量 OA 状态与全文链接
│   ├── fetch_openalex_abstracts.py       #   OpenAlex 单源重建摘要（v7.1 起不覆盖其它源）
│   ├── build_list.py                     #   生成 GB/T 7714-2015 列表（含署名位次）
│   ├── ── 全文获取（一律经 CDP）──────────
│   ├── download_all_fulltexts.py         #   ⭐⭐ 全文下载唯一入口（多实例+熔断+续传+转人工）
│   ├── ⭐ fetch_direct.py（v7.1）        #   第一层直连取摘要（S2/OpenAlex/EuropePMC/Crossref/PubMed）
│   ├── ~~fetch_oa_direct.py~~            #   ⛔ v4 停用（曾为 OA 直链 HTTP 取**全文**；v7.1 未恢复全文直连）
│   ├── save_pdf_via_browser.py           #   单篇 PDF 获取（4 种方法）
│   ├── check_challenge.py                #   站点体检（挑战/封禁/可达性）
│   ├── check_access.py                   #   检测本机 IP 级机构订阅授权
│   ├── grab_abstracts.py                 #   CDP 抓出版社摘要（低频拟人化）
│   ├── ── v3 新增：命名 / 渲染 ──────────
│   ├── rename_to_title.py                #   ⭐ PDF 重命名为论文标题（含索引登记）
│   ├── render_html_pdf.py                #   ⭐ 中文翻译 HTML → PDF + CJK 嵌入校验
│   ├── ── v4 新增：中译素材链 / 收尾 ─────
│   ├── prep_paper.py                     #   原文 PDF → 按页分节文本 + 图注页索引
│   ├── crop_by_images.py                 #   按位图并集裁「图-only」主图
│   ├── extract_figures.py                #   抽取内嵌位图（Extended Data 图）
│   ├── jats_to_text.py                   #   JATS XML（OA 全文）→ 结构化文本
│   ├── translation_build.py              #   内容 JSON + 图形 JSON → 内联图的中译 HTML
│   ├── verify_pdfs.py                    #   全文完整性 + 页数合理性校验
│   ├── inventory_build.py                #   生成《全文清单.md》（分阶段覆盖率）
│   ├── ── v6 新增：消歧接入 / 论文解读 ────
│   ├── scope.py                          #   ⭐⭐ 同名消歧排除名单唯一入口（下载/著录共用）
│   ├── interpret_prepare.py              #   阶段 H ①：组装自包含单篇任务包（含正文，带页标记）
│   ├── interpret_build.py                #   阶段 H ④：回收解读卡 → 《论文解读.md》
│   ├── ── v7 新增：准备阶段 / 综述网络 / PPT ──
│   ├── prepare_stage.py                  #   ⭐⭐ 阶段 0：准备计划 + 阶段闸门（单一事实源，零网络）
│   ├── refs_backfill.py                  #   ⭐⭐ 阶段 D2：反向查参考文献 → 高质量文献列表（OpenAlex）
│   ├── refs_bibliography.py              #   ⭐ 阶段 G2：代表性文献的参考文献列表（GB/T 7714）
│   ├── refs_curate.py                    #   ⭐ 阶段 H2①：去重 + 方向相关性整理 → 最终列表
│   ├── refs_prepare.py                   #   阶段 H2②：组装自包含综述任务包（不联网）
│   ├── refs_build.py                     #   阶段 H2③：回收综述卡 → 《研究方向综述.md》
│   ├── ppt_brief.py                      #   ⭐ 阶段 P：PPT 材料 + 确定性生成 spec/lock（简化 Strategist）
│   ├── knowledge_graph.py                #   ⭐⭐ 阶段 K：知识图谱（overview/词云/方向聚类）
│   ├── selftest_pipeline.py              #   ⭐⭐ 端到端自检（阶段 0→D2→G2→H→H2→K→P，**必跑**）
│   └── selftest_consent.py               #   Cookie 弹窗自动关闭的离线自检（零网络）
└── templates/
    ├── 参考文献著录模板_GBT7714.md
    ├── 中文翻译_HTML模板.html
    ├── 关键词词族模板.json                # 作者专属语义词族表（供 interpret_build --families=）
    ├── 关键词词族_学科通用.json            # ⭐v7 学科通用词族底座（24 领域派生，全单 token）
    ├── 方法范式标签词表.json               # ⭐v7 受控标签词表（direction/paradigm/network_role）
    └── 期刊分区表_中科院一区.json          # ⭐v7 分区**种子表**（非官方分区表；可被用户表覆盖）
```

> **v6 起，全文目录规范名为 `<作者名>_paper_Exports/`**（历史 `<作者名>_Exports/`
> 会被自动沿用，不迁移不丢文件）。另有 `.work_cdp/pdf_index.json` 登记表
> （DOI → 文件名，题名命名后仍可去重）、`.work_cdp/interpret/`（解读任务包与解读卡）、
> 以及 `<作者名>_论文翻译_<刊><年>_中文版.pdf` 中译成品。

---

## 快速开始

```bash
# ⓪ ★ v7：准备阶段 —— 先在对话里收齐选择，再一条命令落盘（决定后续阶段是否执行）
uv run python 流程1/scripts/prepare_stage.py \
    --set output_format=markdown --set translate=no --set reverse_refs=yes \
    --set make_ppt=no --set refs_bibliography=yes --set curate_review=yes
uv run python 流程1/scripts/prepare_stage.py --check-all      # 看每阶段 run/skip

export PAPER_WORKDIR="D:/UserData/Desktop/<作者名>"
export CDP_PORT=9222

# ★ 作者信息一律运行时传入（脚本内零硬编码）——缺失时脚本会显式报错退出
#   下列取值是**当前案例（周丛照）的示例值**，换作者请整组替换，不要改脚本
export PAPER_AUTHOR="周丛照"                  # 中文名（用于产物文件名/抬头）
export PAPER_AUTHOR_EN="Cong-Zhao Zhou"       # 英文名
export PAPER_ORCID="0000-0002-6881-7151"      # orcid_extract/pages 必需
export PAPER_NAME_TOKENS="zhou, cong-zhao|cong-zhao zhou|zhou cz"   # 本人署名识别（可选）
export PAPER_AFFIL="中国科学技术大学"          # 抬头单位（可选）
# ★ 同名消歧判据（阶段 D，不可省）—— 缺姓名会导致消歧被跳过并告警
export PAPER_SURNAME="Zhou"                    # 姓
export PAPER_GIVEN="Cong-Zhao"                 # 名
export PAPER_AFFIL_TOKENS="系/实验室/研究院名1|指纹2"   # 竖线分隔，**越具体越好**
export PAPER_ANCHOR_DOI="10.xxxx/代表作1,10.xxxx/代表作2"   # 确定无疑的代表作
# 可选：统一 CDP 档位（启动与崩溃重启同档）；0 日常 / 1 极简 / 2 扩展 / 3 极速
export PAPER_CDP_LEVEL=0

# ① 启动 3 个 CDP Edge 实例（含弹窗抑制；档位 0/1/2/3）
bash 流程1/scripts/start_edge_cdp.sh "$PAPER_WORKDIR" 9222 3 0
uv run --with websocket-client python 流程1/scripts/cdp_pool.py status   # 探活
# 改过 CONSENT_JS 才需要跑（离线自检，零网络）：
# uv run --with websocket-client python 流程1/scripts/selftest_consent.py

# ② 身份核验 → 抓 ORCID 全量成果 → Crossref 核实 → 同名消歧
uv run python 流程1/scripts/orcid_probe.py "$PAPER_ORCID"
uv run --with websocket-client python 流程1/scripts/orcid_extract.py "$PAPER_ORCID"
uv run python 流程1/scripts/fetch_crossref_all.py && uv run python 流程1/scripts/retry_crossref.py
uv run python 流程1/scripts/enrich_crossref_authors.py     # 姓/名自动取 PAPER_SURNAME/GIVEN
uv run python 流程1/scripts/disambiguate.py \
    --surname="$PAPER_SURNAME" --given="$PAPER_GIVEN" \
    --affil="<单位指纹>" --anchor-doi="$PAPER_ANCHOR_DOI"
# ⭐ 消歧结果由 scope.py 自动接入下载与著录两侧（v6 起），无需手工过滤
uv run python 流程1/scripts/unpaywall_check.py

# ②.5 ★ v7：反向查找参考文献（闸门：prepare_stage.py --check D2）
#     元数据 API（OpenAlex），零浏览器、零风控、不下载任何 PDF
uv run python 流程1/scripts/refs_backfill.py --min-citations=200
#     → 《<作者名>_高质量文献列表.md》＋ .work_cdp/refs/{source_refs,refs_pool,high_quality}.json

# ③ ★ 全文一律走 CDP（OA 与付费墙同等对待）；--manual-wait 必须 > 0
uv run --with websocket-client --with pypdf python 流程1/scripts/download_all_fulltexts.py \
    --n=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35

# ④ 题名命名（新下载已自动题名；此步为历史文件补齐 + 索引登记）
uv run python 流程1/scripts/rename_to_title.py --dry-run   # 先预览
uv run python 流程1/scripts/rename_to_title.py

# ⑤ 生成列表 / 校验 / 清单
uv run python 流程1/scripts/build_list.py
uv run --with pypdf python 流程1/scripts/verify_pdfs.py
uv run --with pypdf python 流程1/scripts/inventory_build.py   # 生成《全文清单.md》

# ⑥ 中文翻译：素材 → HTML（内联图）→ PDF（含 CJK 校验）
uv run --with pymupdf python 流程1/scripts/prep_paper.py "<原文PDF>"
uv run --with pymupdf python 流程1/scripts/crop_by_images.py "<原文PDF>" "<figs.json>" "<图页>"
uv run python 流程1/scripts/translation_build.py "<content.json>" "<figs.json>" "<翻译.html>"
uv run --with pypdf python 流程1/scripts/render_html_pdf.py "<翻译.html>" "<输出PDF>"

# ⑥.5 ★ v7.1：摘要链（**第一层直连优先，缺口转 CDP**）
uv run python 流程1/scripts/fetch_direct.py
#    ↑ S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed，命中即停
#    仅对 templates/直连白名单.json 内的域名发请求；表外一律拒绝
#    若结尾提示仍有缺口 → 转第二层（需 CDP 实例在跑），再重跑本脚本折进产物：
#    uv run --with websocket-client python 流程1/scripts/grab_abstracts.py --retry-failed

# ⑦ 论文解读（阶段 H）：组装任务包 → 并行 subagent → 汇总
uv run --with pypdf python 流程1/scripts/interpret_prepare.py
#    ↑ 产出 .work_cdp/interpret/tasks/*.json（题录+摘要+正文，自包含）
#    然后在**同一条消息内**并行分派 4–6 个 subagent，每篇一个，
#    各读自己的 task.json、各写自己的 result.json
#    （提示词模板见 流程1/subagent-driven-review/SKILL.md § 7）
uv run python 流程1/scripts/interpret_build.py            # → 《<作者名>_论文解读.md》
# 可选：语义词族合并（把 framework/frameworks、CO2 capture/carbon dioxide capture 并成一族）
# cp 流程1/templates/关键词词族模板.json "$PAPER_WORKDIR/.work_cdp/keyword_families.json"

# ★ v7：解读前先装**受控标签词表** + **学科通用词族**（消除并行 subagent 的标签漂移）
cp 流程1/templates/方法范式标签词表.json     "$PAPER_WORKDIR/.work_cdp/tag_vocabulary.json"
cp 流程1/templates/关键词词族_学科通用.json   "$PAPER_WORKDIR/.work_cdp/keyword_families.json"
#   ★ 通用词族表是**底座不是终稿**：与本领域无关的族请删掉，避免报告里出现恒为 0 的噪声行

# ⑧ ★ v7：代表性文献的参考文献列表（闸门：--check G2）
uv run python 流程1/scripts/refs_bibliography.py
#     → 《<作者名>_代表性文献参考文献.md》（**文章 → 参考文献**两部分，GB/T 7714）

# ⑨ ★ v7：去重与方向相关性整理 → 最终列表 → 研究方向综述（闸门：--check H2）
uv run python 流程1/scripts/refs_curate.py                 # 去重 + 打分 + 剔除（剔除项逐条留档）
uv run python 流程1/scripts/refs_prepare.py --top=40       # 组装自包含综述任务包
#    ↑ 同样在**同一条消息内**并行分派 4–6 个 subagent（提示词见
#      subagent-driven-review/SKILL.md § 7.1 **综述网络版**）
uv run python 流程1/scripts/refs_build.py                  # → 《<作者名>_研究方向综述.md》

# ⑩ ★ v7：可选 PPT（闸门：--check P）—— 材料自动取自 ⑦ 与 ⑨ 的产物
uv run python 流程1/scripts/ppt_brief.py --pages=standard
#     → 生成 design_spec.md / spec_lock.md（**简化 Strategist**）并初始化 ppt-master 项目
#     余下按 流程1/ppt-integration/SKILL.md：手写 SVG → 质检 0 error → 串行导出
```

> 环境变量：`PAPER_WORKDIR`（作者工作目录）、`CDP_PORT`（默认 9222）。
> 未设置时取当前目录与目录名。

---

## v3 脚本改造清单（2026-09-13 固化）

> 来源：薛天案例实战。所有改动均已落盘到 `scripts/` 内的对应文件，
> 本节为**变更台账**，便于后续复用与回溯。

### ① PDF 题名命名（`store.py` + `rename_to_title.py`）

| 项 | 内容 |
|---|---|
| 新增函数 | `title_slug(title, maxlen=120)`：题名 → Windows 安全文件名（清理 `\/:*?"<>|` 与控制字符、去尾随 `.`/空格、规避 `CON/PRN/COM1…` 保留名） |
| 新增函数 | `stem_for(doi, year, title)`：默认 `<year>_<题名>`，题名缺失时回退 `<year>_<DOI slug>` |
| 新增登记表 | `.work_cdp/pdf_index.json`（`DOI → 实际文件名`），`register_pdf()` 写入、`existing_pdf()` 优先读取 |
| 兼容性 | `existing_pdf()` 依次尝试：DOI 命名 → 登记表 → 题名命名 → 目录扫描。**改名后不会重复下载** |
| 新脚本 | `rename_to_title.py`：批量把存量 `<year>_<DOI>.pdf` 改名为 `<year>_<题名>.pdf`；支持 `--dry-run` 预览、`--keep-dois` 保留 DOI 便于核对；同名同大小自动合并去重 |
| 调用点 | `fetch_oa_direct.py`、`download_all_fulltexts.py` 下载即用题名命名，并在成功后 `register_pdf()` |

### ② 反爬探测强化（`detect.py`）

| 项 | 内容 |
|---|---|
| HTTP 真状态 | 探针新增 `navStatus`（读 `performance.getEntriesByType('navigation')[0].responseStatus`），不再只靠页面文本猜 |
| 组件指纹 | 新增 `botVendor`：扫描 iframe/script/DOM，识别 **Cloudflare Turnstile、DataDome、PerimeterX、Imperva/Incapsula、hCaptcha、reCAPTCHA、GeeTest** |
| 挑战标记扩充 | 新增 `pardon our interruption`、`are you a human`、`cf-mitigated`、`akamai`、`reference #18`、`异常流量` 等；模糊词（`blocked by`/`forbidden`）仍**只在短页面前部**才算数 |
| **软拦截分级（关键）** | 探针内新增 `SOFT` 名单（`ray id`、`too many requests`、`rate limit`、`请求过于频繁`、`访问频繁`），命中项由 `hits.softHead` 回传；`classify()` 判为 **CHALLENGE（站点级熔断）**，**不再触发全局停摆**。修复了 PNAS 403 被误判为「IP 封禁」导致整轮中止的问题 |
| 新增规则 | 5b：`botVendor` + 短页 → challenge；5c：HTTP 429/503 + 短页 → challenge；5d：软拦截 → challenge |

### ③ Cookie 弹窗自动化（`cdp.py`）

| 项 | 内容 |
|---|---|
| 容器扩充 | `CONSENT_JS` 的 `BOXES` 从 20 → **40+**：OneTrust、Cookiebot、Quantcast、Osano、Didomi、TrustArc、Usercentrics、CookieYes、Complianz、Iubenda、Klaro、Termly、Axeptio、Evidon、SourcePoint(`#sp_message_container_*`) 及通用 `[class*=cookie-consent]` |
| iframe 处理 | 同意书常置于 iframe：跨域只隐藏容器；**同源** iframe 深入 `contentDocument` 再找「接受」按钮 |
| 巡查增强 | DOMContentLoaded + `load` + 12×1.2s 定时 + **滚动/点击后补扫**（部分站点延迟弹窗） |
| 新增方法 | `CDP.dismiss_consent(timeout=8)`：页面加载完成后再补一刀（注入脚本可能错过晚弹窗）。`download_all_fulltexts.py` 在每次落地判定前调用 |

### ④ CDP 启动与运行提速（`start_edge_cdp.sh` / `cdp_pool.py` / `cdp.py`）

| 项 | 内容 |
|---|---|
| 新增档位 | **L3 极速档** = L0 + `--disable-background-networking --disable-component-update --disable-sync --disable-default-apps --no-service-autorun --disable-client-side-phishing-detection --disable-domain-reliability`（只关后台服务，**不改页面指纹**） |
| 并行启动 | 两级流程：先**并行拉起全部实例**，再统一探活 → N 实例总启动时间 ≈ 最慢的 1 个。实测 **3 实例冷启动 15s**（原逐个等待 ≥30s） |
| 探活提速 | `cdp_pool.launch_one()` 轮询 1.0s → **0.25s**；shell 端 `curl --max-time 2` + 0.25s 轮询 |
| 页面就绪 | `CDP.navigate()` 的 `settle` 改**自适应**：正文 >4000 字符或 citation 元数据就绪即提前返回，最坏仍等满 settle |
| Python 端同步 | `cdp_pool.LEVELS` 与 shell 档位一致（0/1/2/3），`LEVEL_NAMES` 增加「极速档」；`ensure()` 并行启动 |

### ⑤ CAPTCHA 弹窗可靠性（`download_all_fulltexts.py`）

| 项 | 内容 |
|---|---|
| 弹窗不卡死 | `notify_user()` 由 `System.Windows.Forms.MessageBox`（模态、需手点）改为 **`WScript.Shell.Popup`（带 TTL 自动关闭）**；实测调用 **0.01s 非阻塞返回** |
| 窗口置前 | 新增 `activate_edge_window()`：`Page.bringToFront` 只激活标签页，另用 PowerShell `AppActivate` 抬升 Edge 窗口（验证码窗口能被看见） |
| 等待循环加固 | `wait_for_manual()` 内 `detect.judge()` 包 try/except（CDP 抖动只警告不中断）、每轮重新置前窗口、提示文案带倒计时 |
| 三重防卡死 | ① 循环硬上限 `max_wait` ② 每次探针超时（15s）③ 弹窗自身 TTL |

### ⑥ 直连禁令 → v4 升格为「CDP 强制抓取禁令」→ v7.1 改为「分层获取策略」

> **v3（2026-09-13）**：ScienceDirect、Cell Press、OUP、SAGE、Wiley、ACS、RSC、Springer、T&F
> 等**存在人机验证**的站点，绝不允许用 curl / urllib 直连（含「探活」式请求）。
>
> **v4（2026-09-14）**：禁令**扩大到全部站点与全部内容抓取** ——
> **如无特别要求，一律经 CDP 完成；不得自编脚本从 HTTP 直链获取**（**OA 文章同样适用**）。
> 原 `BROWSER_ONLY` 跳过表所在的 `fetch_oa_direct.py` **整体停用**，
> 因此不再需要「按域名维护黑名单」——**所有内容获取都只有 CDP 一条路**。
>
> 仍允许纯 HTTP 的是**元数据 API**（Crossref / ORCID / OpenAlex / Unpaywall / Europe PMC REST），
> 它们属于 §5.1 分层策略的**第一层**。详见本文件「⛔ 分层获取策略」章节与 v4 清单第 ① 项
> （v7.1 起该条已由「全部走 CDP」改为分层：原文仍属第二层必须 CDP）。

### ⑦ 中文翻译渲染（`render_html_pdf.py`）

| 项 | 内容 |
|---|---|
| 功能 | 翻译 HTML → PDF（Chrome/Edge `--headless=new --print-to-pdf-no-header`），并**校验**页数、体积、首页 CJK 字符数 |
| 退出码 | 0 成功；1 渲染失败；2 校验不通过（CJK < 50），便于批处理判断 |
| 配套 | 单篇渲染包一层 `bash .work_cdp/render_one.sh <html> <pdf>`，便于在工具调用受限时以最简命令执行 |

### ⑧ 收尾与索引脚本（工作目录 `.work_cdp/`，非流程脚本）

| 脚本 | 作用 |
|---|---|
| `make_inventory.py` | 生成《全文清单.md》：逐篇列全文状态、来源、页数、未获取原因、按阶段统计覆盖率 |
| `render_rest.py` | 批量渲染尚未产出的中译 PDF（幂等，已存在则跳过） |
| 交付物索引 | 工作目录下 `薛天_交付物索引.md`（作者名可替换）：交付清单 + 数据核验链 + 复现步骤 + 变更台账 |

---

## v4 脚本改造清单（2026-09-14 固化）

> 来源：郭傲（Ao Guo，中国科大生医部）案例实战 —— 15 篇论文 / 14 篇全文 / 7 篇中译。
> 变更台账，详见 [SKILL.md](SKILL.md) § 9.2。

### ① ⛔ CDP 强制抓取禁令（v4 原文；**v7.1 已由「分层获取策略」取代**，此条留存作历史记录）

| 项 | 内容 |
|---|---|
| **条款** | **如无特别要求，全部使用 CDP 完成抓取；不得自编脚本从 HTTP 直链获取**目标内容 |
| **禁止** | `urllib`/`requests`/`curl`/`wget` 直取内容；`curl -I` 探活；以「OA」为由走直链；自建下载器；写进临时脚本 |
| **允许** | **元数据 API**（Crossref / ORCID / OpenAlex / Unpaywall / Europe PMC REST）与**本地文件处理**不受限 |
| **判定** | 拿「元数据」→ API；拿「论文内容（PDF/HTML 正文）」→ **CDP** |
| **影响** | 阶段 E 由「三级优先级」改为**统一走 CDP**；`fetch_oa_direct.py` **停用** |

### ② 同名消歧全链路（`orcid_probe.py` / `disambiguate.py` / `enrich_crossref_authors.py`）

| 项 | 内容 |
|---|---|
| 问题 | 作者聚合**普遍过度合并**：OpenAlex 单一 author id 混入无线通信 / 有机化学 / 微电网 / 聚合物 / 空间物理等多个同名者；Europe PMC 亦混入 5+ 位同名者 |
| 判据 | ① 姓名精确匹配（拒绝 `Anping`→`Ao` 这类前缀误命中）→ ② **本人署名块单位指纹**（最强判据）→ ③ **合作者网络重合**（姓氏+首字母，避免只用姓氏导致 Chen/Zhu/Li 大量误命中） |
| 关键区分 | 「单位**不匹配**」= 排除依据；「署名块**无单位字段**」= 数据缺失，待复核——**不可混为一谈**（否则漏收真论文） |
| 姓名解析 | Crossref 返回结构化 `family`/`given`，存为 `authors_struct`；**不要按空格猜「哪段是姓」**（曾把 `Jie Yu` 误成 `Y JIE`、`Dongming He` 误成 `DONGMING H`） |
| 实测 | 郭傲案例：273 条候选 → 15 篇，零误收；并发现 Sci Rep 2017 实为**三人共同第一作者**，修正上游数据的两人说法 |

### ③ 中译素材链（`prep_paper` / `crop_by_images` / `extract_figures` / `jats_to_text` / `translation_build`）

| 脚本 | 作用 |
|---|---|
| `prep_paper.py` | 原文 PDF → **按页分节**文本 + 图注页索引（翻译逐段对照；图注页的**前一页**即主图页） |
| `crop_by_images.py` | 按页面**位图并集**裁「图-only」主图（Nature/Cell 拼版图；不含正文，也不被 drawings 误导） |
| `extract_figures.py` | 抽取**内嵌位图**（Extended Data 图），可降采样控体积 |
| `jats_to_text.py` | **JATS XML → 文本**（Europe PMC `fullTextXML`，仅 `inEPMC=Y`；非 OA 返回 404） |
| `translation_build.py` | 内容 JSON + 图形 JSON → **图片 base64 内联**的单文件 HTML（headless 渲染不断链） |

### ④ 收尾脚本（`verify_pdfs.py` / `inventory_build.py`）

| 脚本 | 作用 |
|---|---|
| `verify_pdfs.py` | `store.validate_pdf` 校验 + **页数合理性**告警（识别补充材料冒充正文） |
| `inventory_build.py` | 生成《全文清单.md》：分阶段覆盖率 + 逐篇来源/页数/未获取原因 |

### ⑤ ⛔ 修复：CAPTCHA 弹窗门控误用（**重要**）

`download_all_fulltexts.py` 中 `load_landing()` 的门控是：

```python
if st == CHALLENGE:
    if max_wait > 0 and wait_for_manual(c, doi, max_wait, log):
```

`--manual-wait` **默认 0.0**（=关闭），此时该门恒为假 → `notify_user` 弹窗、
`activate_edge_window` 窗口置前、等待轮询**整条链都不会被调用**，遇挑战静默跳过
（日志只留 `失败: challenge:title`，**看不出弹窗本应触发**）。

> ✅ **正确用法：一律显式传正值** —— `--manual-wait=5`（单位**分钟**）。

---

## 打包发行版（`流程1/`）

```bash
# 把本流程 + 全部依赖（含 vendor/ppt-master）打包成可独立运行的目录
uv run python 流程1/scripts/pack_release.py --force
#    --dest=<路径>   换个目标目录        --slim    裁掉配图样张/图标库/音效（省 ~90 MB）
#    --no-vendor     只出流程本体        --dry-run 先看会写什么

# 验证包能独立跑（关键：必须用包内的 vendor/ppt-master，而不是宿主仓库那份）
uv run python 流程1/scripts/selftest_pipeline.py --offline
```

**打包做了什么、没做什么**

| 项 | 说明 |
|---|---|
| 路径重写 | 文档与脚本里的 `流程1/…` 全部改写成 `流程1/…`（**幂等**，重复打包不会变成 `流程11/`），逐个记录在 `PACKAGE_MANIFEST.json` |
| 依赖 | `vendor/ppt-master/`（只带运行时；不含 `.git/`、`projects/`、`docs/assets/`） |
| 定位 | `ppt_brief.py` **优先**用随包副本，所以包脱离原仓库也能跑；自检会断言这一点 |
| 未包含 | `ppt-master/projects/`（用户项目数据）、`.git/`（版本库）—— 不应随包分发 |
| 核验 | `PACKAGE_MANIFEST.json` 逐文件 sha256 + 重写清单；`README-PACKAGE.md` 给出一键核验脚本 |

> ⚠️ **文档改了要重新打包**，否则包里还是旧说明。打包脚本自身也会一起进包，
> 所以包内可以直接再打一次（`--dest=` 换目录即可）。

---

## 自检（**v7 起：改动流程后必跑**）

```bash
# ① 端到端全流程（推荐）：临时目录里真跑一遍 阶段 0→D2→G2→H→H2→P
uv run python 流程1/scripts/selftest_pipeline.py            # 联网 33 项
uv run python 流程1/scripts/selftest_pipeline.py --offline   # 离线 18 项（跳过 D2/G2/H2）
#    --limit=3 控制 D2 处理篇数；--keep 保留临时目录便于排查；--verbose 打印全部输出

# ② 各脚本的纯逻辑自检（零网络、秒级）
for s in prepare_stage refs_backfill refs_bibliography refs_curate refs_prepare refs_build ppt_brief; do
  uv run python "流程1/scripts/$s.py" --self-test || echo "❌ $s"
done
```

> **为什么必须端到端跑**：各脚本的 `--self-test` 全绿**不等于**能串起来跑。
> v7 的端到端自检一共挡下 4 类静态看不出的缺陷 ——
> `--set` 空格写法被静默忽略、两个 prepare 脚本的 manifest 字段不一致、
> PPT Spec 的 `Page Count` 与 §IX 页数不等、§X 因缩进落进循环体而重复 4 次。
> 详见 SKILL §9.5-G。

---

## 工具链总览

| 类别 | 工具 | 作用 |
|---|---|---|
| 网页 | Cherry `web_fetch` / `web_search` | 作者主页、ORCID 定位 |
| 标识 | `orcid_probe.py`（API）+ `orcid_extract.py`（**CDP 实抓网页**） | API 拿任职/教育/关键词；网页拿权威成果集合（比 API 更准，去重后） |
| 题录 | **Crossref API**（`fetch_crossref_all` + `enrich_crossref_authors`） | 期刊/年/卷/期/页/**结构化作者姓名**/DOI 权威核实 |
| 🆕 消歧 | **`disambiguate.py`** | 姓名精确 + 署名单位指纹 + 合作者网络，剔除同名他人 |
| 全文 | **CDP 多实例（唯一路径）** | 全文获取一律经真实 Edge；OA 与付费墙同等对待（v4 起取消 HTTP 直取） |
| 浏览器 | **Edge + CDP（多实例）** | 绕 Cloudflare 抓 ORCID 页面、出版社摘要与全文 |
| 🆕 中译素材 | `prep_paper` / `crop_by_images` / `extract_figures` / `jats_to_text` / `translation_build` | 原文 → 逐段文本 + 图-only 主图 → 内联图 HTML |
| 转换 | Cherry `to_markdown` 或 `prep_paper.py` | PDF → Markdown / 按页分节文本 |
| 导出 | Chrome/Edge `--headless --print-to-pdf`（经 `render_html_pdf.py`） | 中文翻译 HTML → PDF |
| 校验 | `pypdf`（经 `store.validate_pdf` / `verify_pdfs.py`） | 页数、体积与中文字符嵌入检查 |
| 🆕 收尾 | `inventory_build.py` | 生成《全文清单.md》（分阶段覆盖率 + 未获取原因） |
| 日志 | `logutil.py` | 全流程落盘于 `.work_cdp/logs/` |

---

## 多实例并行 CDP（v2 核心）

**为什么要多实例**：单实例一旦浏览器退出，脚本无探活机制，后续请求全部
`WinError 10061` 失败，整批任务报废。一实例一 profile 还天然形成 **Cookie 池**：
某个实例被 Cloudflare 挑战时，其余实例照常工作。

**启动参数档位**（并列，默认全部实例用 **L0 日常档**；缺省档位由 `PAPER_CDP_LEVEL` 指定，
**启动与崩溃重启同档**）：

| 档位 | 参数 | 何时用 |
|---|---|---|
| **L0 日常档（默认）** | port / allow-origins / user-data-dir<br>`--window-size=1280,900`<br>`--no-first-run --no-default-browser-check`<br>`--deny-permission-prompts --disable-notifications`<br>**+ 弹窗抑制 6 项**（见下） | 首选，「像正常打开的浏览器」 |
| L1 极简档 | 仅 3 个必需参数（**刻意不含弹窗抑制**） | A/B 对照 |
| L2 扩展档 | L0 + `--lang=zh-CN` | 需中文界面时 |
| L3 极速档 | L0 + 关闭启动期后台任务 | 推荐；冷启动更快、后台占用更低 |

**弹窗抑制 6 项（v6 新增，L0/L2/L3 共有）** —— 只关**浏览器自己的对话框**，
不碰页面 DOM，也不改 `navigator`/`screen` 指纹：

| 参数 | 抑制什么 | 为什么必须 |
|---|---|---|
| `--noerrdialogs` | 「页面无响应」等错误对话框 | 模态，会**卡住导航** |
| `--disable-session-crashed-bubble`<br>`--hide-crash-restore-bubble` | 崩溃后「恢复页面」气泡 | 本流程会 `taskkill /F` 强杀实例，同 profile 下次启动**必然**弹它，遮挡内容并抢焦点（影响人工过验证码）。两代开关名都带，未知开关会被静默忽略 |
| `--disable-prompt-on-repost` | 「重新提交表单」模态对话框 | 会**直接阻断导航**（`navigate()` 有重试逻辑） |
| `--disable-search-engine-choice-screen` | 全新 profile 首启的搜索引擎选择页 | 否则首启可能停在选择页 |
| `--disable-features=Translate,TranslateUI` | 「是否翻译此页面？」气泡 | 英文论文页必现 |

- **正常窗口**：用 `--window-size` 而非 `--start-minimized`（最小化会渲染节流、拖慢加载）。
- **权限 / 通知弹窗**：`--deny-permission-prompts` + `--disable-notifications`。
- **Cookie / 同意书弹窗**：**没有**命令行开关；由 `cdp.py` 的 `CONSENT_JS` 页内保守关闭
  （只在已知同意管理容器内操作 → 先点「接受」→ 1.5s 后复查 → 仍在则强制隐藏 → 恢复滚动）。
  不改任何 navigator/screen 属性。**v6 修了三处实质缺陷**，详见 SKILL §9.4 C 组；
  改动后请跑 `selftest_consent.py` 自检（离线、零网络、真实 Edge）。

✗ 不用 `--headless`  ✗ 不用 `--disable-blink-features=AutomationControlled`
✗ 不用 `--excludeSwitches`  ✗ 不做 JS 指纹注入

**档位速查（v3）**：`0` 日常档（默认）｜`1` 极简档（A/B 对照）｜`2` 扩展档（中文界面）｜
**`3` 极速档（推荐）** —— L0 + 关闭启动期后台任务（后台网络/组件更新/同步/默认应用等）。
L3 只关后台服务，**不改 `navigator`/`screen` 等页面指纹**，实测不影响 Cloudflare 放行。

**端口 ↔ profile**：`9222 → .edge-cdp-profile`（沿用历史，保留放行 Cookie）；
其余 → `.edge-cdp-p<port>`。**一实例一 profile 是硬约束**。

---

## 核心避坑（详见 SKILL.md § 5 / § 9）

1. **CDP 403** → 启动必须加 `--remote-allow-origins=*`
2. **Edge 掉线 / 整批报废** → 用多实例池 + 逐篇探活 + 自动重启
3. **脚本死机** → 所有 CDP 调用已带硬超时；不再有无限 `recv()`
4. **误触发 CAPTCHA** → 判定改用 `detect.judge()`；**绝不**用文本长度当判据
4b. 🆕 **CAPTCHA 弹窗不触发** → `--manual-wait` **默认 0.0**，此时 `if max_wait > 0 and wait_for_manual(...)` 恒为假，**整条弹窗链都不会被调用**（日志只留 `失败: challenge:title`）。**一律显式传正值**，如 `--manual-wait=5`（单位分钟）
5. **泛词误报封禁** → 封禁判定用精确短语；正文里的 `forbidden` / `blocked by` 不再误伤
6. **Git Bash 杀进程报错** → `MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F`
7. **中文打印乱码** → 统一用 `logutil.py`（内部 `errors="replace"`，不会中断流程）
8. **Crossref 限流** → 并发失败后串行退避重试
9. **ORCID 条目重复** → 以网页 `Works (N of M)` 为准，按 DOI 去重
10. **付费全文拿不到** → 先查机构授权 IP 段（页面出现 "Subscribed" 即有权）；
    受阻则如实注明限制，**不伪造内容**
11. **同一主机被 WAF 计数** → **按主机**节流（不是全局统一间隔）
12. **重复下载 / 误删已有 PDF** → `store.existing_pdf()` 兼容历史命名；只删本次写入的文件
13. **PDF 下载方式** → 必须在**落地页**用页面上下文 `fetch`；Wiley 用 `Page.printToPDF` 渲染兜底
14. 🆕 **改名后重复下载** → 题名命名会让「按 DOI 找文件」失效；
    `register_pdf()` 必须在新文件落盘后调用（两个下载脚本已内置）
15. 🆕 **Ray ID / 429 被误判为 IP 封禁** → 那是**单次 WAF 拦截**，只做站点级熔断
    （探针 `SOFT` 名单 → `hits.softHead`），**不要**触发全局停摆；真正的封禁短语才全局中止
16. 🆕 **PDF 抓到了但只有图注页/补充材料** → 落地页的 `links` 里
    `suppl_file/...pdf` 是**补充材料**、`history/....pdf` 才是正文；
    下载后必须校验页数（正文一般 ≥6 页），残缺件改名保留为 `【补充材料】…` 后重抓
17. 🆕 **翻译渲染空白/无中文** → `render_html_pdf.py` 退出码 2 即「CJK < 50」，
    多为 HTML 未写入中文或字体缺失；重渲染前先确认 HTML 内容
18. 🆕 **对验证站点做 `curl` 探活** → **禁止**。这类请求同样计数、同样触发风控，
    且拿不到内容。连通性判断一律交给浏览器（`detect.navStatus`）
19. 🆕 **用鼠标事件自动过验证** → **禁止**（见下方「鼠标自动化禁令」）。
    CDP 输入事件的 `isTrusted=True` 与真人点击属性上无法区分，但反爬判据
    **不在这一次点击**（而在协议痕迹/行为特征/风险评分上）→ **点了也没用**；
    且循环试探会升级为站点级封禁。人机验证一律**转人工**（`--manual-wait=N` +
    窗口置前，cookie 留存复用）；只有 Cookie 弹窗、「View PDF」等**页面功能按钮**
    才允许自动化点击
20. 🆕 **固定定位的 cookie 弹窗永远关不掉** → `visible()` 曾用
    `el.offsetParent !== null` 判可见，而规范规定 **`position:fixed` 元素的
    `offsetParent` 恒为 null** —— Cookiebot / Osano / Didomi / CookieYes 等
    **本身就是 fixed** 的横幅全被判为「不可见」。**已修**（改用
    `getBoundingClientRect`），并由 `selftest_consent.py` 实测覆盖
21. 🆕 **shadow DOM 里的弹窗查不到** → Usercentrics v2 等把 UI 渲染在 shadow root
    内，`document.querySelector` 不可见。**已修**（新增 `roots()` 收集 shadow root）
22. 🆕 **同一「接受」按钮被重复点击** → 站点若「只写 cookie 不销毁 DOM」，
    每次巡查都会再点一遍；且点击派发 `click` 事件又触发补扫 → **自放大**
    （实测 2 秒内同按钮被点 3–4 次）。**已修**（不可枚举 WeakSet 记账，同文档至多一点一次）
23. 🆕 **`disambiguated.json` 无人消费** → 同名消歧跑完只打印给人看，
    下载与著录仍用全量 `crossref_all.json`，他人论文照样进最终列表。
    **已修**：新增 `scope.py`，下载/著录两侧共用；且**只排除肯定性判定**，
    「单位字段缺失」类一律保留（漏收真论文的代价远大于多留一篇）
24. 🆕 **`master.json` 是幽灵文件** → `enrich_crossref_authors.py` 与
    `inventory_build.py` 都默认读它，而**本目录任何脚本都不产出**该文件
    → 全新工作目录跑到该步必然失败。**已修**（默认改读 `crossref_all.json`，旧名作回退）
25. 🆕 **`enrich` 把 authors 写成字符串列表** → `build_list.py` 的 `a.get("name")`
    直接 `AttributeError` 崩栈。**已修**（写字典列表，与 `fetch_crossref_all.py` 契约一致；
    著录侧再加一层形状容错）
26. 🆕 **`run_all.sh` 的消歧步骤只 `echo` 不执行** → 「不可省」的步骤实际从未运行。
    **已修**（真调用，判据取自 `PAPER_SURNAME`/`PAPER_GIVEN`/`PAPER_AFFIL_TOKENS`/
    `PAPER_ANCHOR_DOI`；缺项时明确告警并说明后果）

27. 🆕 **消歧在主路径上无从施加** → ORCID→Crossref 是主路径，而判据依赖**逐作者单位**，
    偏偏 Crossref **只有少数出版社存缴单位**（实测 13 篇里仅 2 篇）。只靠单位指纹，
    消歧必然全部落到「数据缺失」而空转。**已修**：新增**判据⓪ ORCID 溯源**
    （DOI 出自本人 ORCID 记录即确认 —— 作者本人认领，强于任何单位指纹）
28. 🆕 **`drop` 判决会让论文凭空消失** → 作者数 ≥ `large_collab` 且无合作者佐证时判 `drop`，
    而 drop **既不入 confirmed 也不入 review**，22 位作者的 Nature 代表作会从两处同时消失。
    **已修**：改判 `review`（保留 + 写明理由）—— 丢弃不可逆，大合作论文很可能是本人正当成果
29. 🆕 **解读从头部硬截，恰好丢掉结论** → 论文的结论、量化数据、作者自述局限**全在结尾**，
    头部硬截把它们全删了。**已修**：保留**头 60% + 尾 40%**，中段另存可补读。
    实测 5 篇受影响，重派后全部补读中段，捞回 R²、P 值、CI 等大量关键数据
30. 🆕 **产物按「序号_名字」命名会被顺序变化打乱** → 一次 Crossref 重采有 2 条失败未补，
    `year` 为空 → 排到末尾 → 整体重编号 → **13 份解读卡里 9 份与 manifest 对不上号**，
    轻则报缺卡、重则**把 A 的解读卡当成 B 的结果**。**已修**：文件名**只由 DOI 决定**，
    序号只写在 JSON 内部。**凡会被下游按名字引用的产物，命名必须由内容决定，不能由「第几个」决定**

---

## 🖱️ 鼠标自动化禁令（最高优先级，与「直连禁令」「封禁应急规则」同级）

> **本禁令来源**：2026-09-13 用户要求 —— 将鼠标自动化验证成果固化为禁令。
> **适用对象**：CDP `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` /
> `Input.insertText` / `Input.synthesizeScrollGesture` 及任何等价手段。
> **核心条款**：**禁止**用上述手段去**尝试通过人机验证**（Turnstile / CAPTCHA / 滑块 / 挑战页）。

### 一、禁止清单（红线，不得触碰）

| # | 禁止行为 | 说明 |
|---|---|---|
| 1 | 用 `Input.*` 点击 Turnstile / reCAPTCHA / hCaptcha 的**复选框或验证组件** | 单次、循环均禁止 |
| 2 | 合成 `mouseMoved` 轨迹模拟**滑块 / 拼图 / 按住**手势 | 属绕过安全机制 |
| 3 | 对验证页做**批量 / 循环 / 退避式试探点击** | 即「反复试探」，直接升级为封禁 |
| 4 | 用页内 JS 伪造（`element.click()`、改写 `isTrusted`、劫持验证回调） | 既无效又违规 |
| 5 | 把人工验证环节「自动化」以提升流程自动化率 | 本流程对此**只做转人工** |
| 6 | 将上述任一行为写入脚本、宏、定时任务 | 含「实验性」「一次性」尝试 |

### 二、允许范围（边界，避免误伤）

对**非验证类**的常规页面元素做自动化点击是**允许的**（属常规 UI 自动化，不在禁令内）：

- Cookie / 同意书弹窗 —— `CONSENT_JS` + `dismiss_consent()`
- 「View PDF」「Download」「Accept」等**页面功能按钮**
- 页内导航、落地页上下文 `fetch` 直取 PDF 直链

> **判定分界**：看元素的**用途** —— 是「**安全验证**」还是「**页面功能**」。
> 前者一律转人工；后者可自动化。

### 三、禁止依据 1：实测证明「点了也没用」（isTrusted 实测）

```
① JS element.click()           → click     isTrusted=False  (0,0)
② CDP Input.dispatchMouseEvent → mousedown isTrusted=True  (97,61)
                                 mouseup   isTrusted=True  (97,61)
                                 click     isTrusted=True  (97,61)
```
（复现脚本：`.work_cdp/test_input_events.py`，**本地页面、零网络请求**，仅作技术取证）

**结论**：CDP 走浏览器**输入管线**注入，事件属性与真人点击**完全无法区分**。
反过来说——既然属性层区分不了，反爬的判据**必然不在这一次点击上**（见下）。

### 四、禁止依据 2：反爬判定不依赖那一次点击

| 判定维度 | 反爬实际看什么 | 为什么自动点击过不了 |
|---|---|---|
| 协议 / 环境痕迹 | CDP 会话存在性、`Runtime.enable` 时序侧信道、自动化框架特征 | 与「点得像不像」无关 |
| 行为生物特征 | 轨迹加速度/抖动/停顿、悬停犹豫、直线度、间隔规律性 | **单点瞬移点击**是最典型异常样本 |
| 风险评分 | IP 信誉、UA/字体/WebGL 一致性、请求频次 | 勾选框常只是 UI 摆设，**评分不过点了也照样循环/拒绝** |

### 五、禁止依据 3：代价不可承受

- 反爬对「重复 + 机械化」试探最敏感：连续尝试会把**单次挑战升级为站点级封禁**；
- 一旦 IP 真被封禁，**后续所有站点**抓取全部受牵连，代价远大于 1–2 篇论文；
- 这正是流程「绝不反复试探」铁律所禁止的行为。

### 六、违规处置（不可跳过）

1. **立即停止**该行为与相关脚本，终止本轮全部后续尝试；
2. **如实报告**：站点、时间、影响范围；
3. **转人工**：`--manual-wait=N` 弹窗（TTL 自动关闭）+ 窗口置前，由人在 Edge 窗口点一次；
   **放行 cookie 留在该 profile**，后续同站请求直接放行；
4. 仍不可得 → 机构 VPN / 图书馆代查，或在《全文清单.md》中如实注明限制。

> **一句话**：CDP 鼠标事件适合点**按钮**，不适合破**验证**。
> 前者是自动化，后者是绕过安全机制——本流程对后者**不实现、不尝试、只转人工**。

---

## ⛔ 分层获取策略（v7.1，**最高优先级** —— 优先级高于其余所有禁令）

> **用户要求**
> · v4（2026-09-14）：「如无特别要求，全部使用 CDP 完成抓取。」
> · **v7.1（2026-09-19）修订**：「对于获取**原文**的需求仍然禁止直链抓取；
>   但是对于**摘要、综述**或工作文件夹下**验证可行的开源数据库**可以直接获取，
>   减轻压力。同时若直接获取尝试失败则转向 CDP 获取。」
>
> **一句话**：**拿"原文"走 CDP；拿"摘要 / 综述题录 / 白名单开源库"直接取，
> 取不到再转 CDP。**

### 一、分层表（**这张表就是全部规则**）

| 层 | 内容类别 | 获取方式 | 实现 |
|---|---|---|---|
| **第一层** | 元数据（题录 / 作者 / 被引数 / 参考文献关系） | ✅ **允许直连** | `fetch_crossref_all.py` / `orcid_probe.py` / `unpaywall_check.py` / `refs_backfill.py` |
| **第一层** | **摘要** | ✅ **允许直连** | `fetch_direct.py`（多源自动降级） |
| **第一层** | **综述**（题录 / 摘要级） | ✅ **允许直连** | 同上（综述的**正文**属第二层） |
| **第一层** | 白名单内的**开源数据库** | ✅ **允许直连** | `templates/直连白名单.json` |
| **第二层** | **原文全文**（PDF / HTML 正文），**含 OA** | ⛔ **禁止直连，一律走 CDP** | `download_all_fulltexts.py` / `save_pdf_via_browser.py` |
| **回退** | 第一层尝试失败 | ➡️ **转向 CDP** | `direct_failed.json` → `grab_abstracts.py --retry-failed` |

> **判定分界**：这次请求要**不要拿"论文正文"**？
> 拿正文 → **必须 CDP**；不拿正文（摘要/题录/结构化元数据）→ **第一层直连**，取不到再转 CDP。

### 二、第一层怎么执行（**不要临场判断**）

「验证可行的开源数据库」由 `templates/直连白名单.json` 落成可执行判据，
`fetch_direct.py` **fail-closed** 执行：**表外域名一律拒绝**。

```bash
# 先问策略（不联网；退出码 0=允许 / 3=禁止）
uv run python 流程1/scripts/fetch_direct.py --check-domain="<URL>"

# 批量取摘要（S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed，命中即停）
uv run python 流程1/scripts/fetch_direct.py
```

两条由 `--self-test` 守住的红线：

1. 域名匹配用**后缀边界**，不是子串 —— 否则 `api.crossref.org.attacker.com` 会被放行；
2. 白名单**不含任何出版社内容站**，且每条 `content_class` **只能是 `metadata` 或 `abstract`**
   —— 想往里塞一个能下全文的源，自检直接失败。

### 三、回退规则

```
第一层逐源尝试 → 命中即写 .work_cdp/abstracts_openalex.json（不占用浏览器）
        └─ 全部未命中 → .work_cdp/direct_failed.json
                          ↓
                    grab_abstracts.py --retry-failed（第二层 CDP）
                          ↓
                    重跑 fetch_direct.py 折进统一产物
```

每源对同一 DOI 最多重试 3 次（指数退避），404/403 立即换源 —— **不对同一源反复冲击**。

### 四、第二层（原文）仍然禁止直连

| # | 禁止行为 |
|---|---|
| 1 | 自编脚本直链下载**原文全文**（`urllib`/`requests`/`curl`/`wget`/`Invoke-WebRequest`，含"临时试一下"） |
| 2 | 用 `curl -I` / `HEAD` 对**出版社内容站**做探活或状态码探测 |
| 3 | 以「OA / 开放获取」为由走纯 HTTP 直取**全文** —— **OA 全文也走 CDP** |
| 4 | 自建下载器 / 第三方下载工具 |
| 5 | 把**白名单外**的域名当作"开源库"直接取 |

**依据**：

1. **风控风险**：直连正文请求同样被站点计数、同样触发风控；一旦 IP 被计数，波及后续**所有**站点。
2. **不可诊断**：直连拿到的常是拦截页/半截内容，而流程的判定与日志体系（`detect.judge` / `navStatus` / `botVendor`）全部建立在浏览器路径上。
3. **合规与一致性**：真实浏览器访问 = 正常阅读行为；脚本批量直取正文与站点意愿相悖。
4. **能力退化**：`download_all_fulltexts.py` 已具备多实例并行、站点熔断、断点续传、挑战宽限、转人工。

### 五、违规处置（不可跳过）

1. **立即停止**该脚本与相关进程，删除临时下载器；
2. **如实报告**：抓了什么、来自哪个站点、属于哪一层、影响范围；
3. 原文类改用 CDP 路径重做；已直连获取的**摘要/元数据**保留但**标注来源**，不得冒充 CDP 路径产物；
4. 若站点已因直连被计数，按下方「封禁应急规则」处理。

**减负实测**：对示例题录前 12 篇跑 `fetch_direct.py` —— **12/12 命中摘要、5.6 秒、零浏览器占用**
（Semantic Scholar 批量 5 + OpenAlex 7）。

---

---

## 🚫 直连禁令（v3；已被上方 v4 禁令吸收，保留作历史条目）

> **用户明确要求（2026-09-13）**：ScienceDirect、Cell Press、OUP、SAGE、Wiley、ACS、RSC、
> Springer、T&F 等**存在人机验证**的站点，**绝不允许**用 `curl` / `urllib` 等直连方式访问，
> **包括「探活」式的 HEAD/GET 请求**。
>
> ⚠️ **该项自 v4 起曾扩大到全部站点；v7.1 起改为分层**（见上方「分层获取策略」：
> 原文仍禁直连；摘要/综述题录/白名单开源库允许直连）。
> 且其代码载体 `fetch_oa_direct.py` 已停用；本条仅作历史回溯。
>
> - 连通性/状态判断一律交给浏览器：`detect.judge()` 返回的 `navStatus` 即真实 HTTP 状态。
> - 违规示例（**严禁**）：`curl -I https://www.sciencedirect.com/...`、用 `urllib` 试状态码。

---

## 🛑 封禁应急规则（最高优先级）

> **实战澄清**：ScienceDirect / RSC 出现的是**可解的 CAPTCHA 人机验证**
> （`Are you a robot? ... completing the captcha challenge below`），**不是 IP 封禁**——
> 差别在于正常 Edge profile 已持有 Cloudflare 放行 cookie，而全新 profile 没有。
>
> **解法**：① 在 CDP 窗口人工完成一次 CAPTCHA（cookie 会留存）；
> ② 或经用户同意后，用其既有 Edge profile 启动 CDP。

**一旦出现下列任一信号，立即终止本次任务的全部后续网络尝试：**

| 信号 | 典型文案 |
|---|---|
| **地址被封禁** | `此地址已被封禁`、`Your IP has been blocked`、`Access Denied`、`您已被阻止访问` |
| 站点级封禁 | `Forbidden`（短拦截页）、`unusual traffic from your network` |
| 账号级封禁 | `Your account has been suspended`、`访问受限` |

**处置流程（不可跳过）：**
1. **立即停止** —— 终止脚本进程，不再发出任何请求（含重试、含换站点）
2. **永久跳过** —— 被封站点在后续所有批次中不再尝试
3. **如实报告** —— 向用户说明站点、原因、影响范围
4. **转人工** —— 建议人工浏览、机构 VPN 或图书馆代查

> 代码层面已实现：`download_all_fulltexts.py` 检测到封禁短语即抛 `SiteBlocked`，
> **全局中止**并停止访问其他站点。
>
> **经验法则：同一站点连续 2 次出现人机验证/拒绝，即应放弃该站点（`--site-fail-limit=2` 默认已启用），
> 而不是分批次反复试探。**

---

## 日志与回溯

**① 每脚本文本日志**（`.work_cdp/logs/<脚本>_<时间戳>.log`）

> ⚠️ `latest_<脚本>.log` 是**指针文件**，内容是该日志的**路径**，不是日志本身 ——
> 直接 `tail` 它只会看到一行路径。要先取路径再 tail：

```bash
tail -f "$(cat .work_cdp/logs/latest_download.log)"                 # 实时看下载
grep -E "WARN|ERROR" "$(cat .work_cdp/logs/latest_download.log)"    # 只看问题
tail -f .work_cdp/logs/edge_9222.log                                # 看浏览器
ls -t .work_cdp/logs/*.log | head                                   # 最近写过的日志
```

**② 操作流水**（`.work_cdp/logs/run_journal.jsonl`，一行一 JSON，只追加）

用于**回溯「这一轮到底做了什么」** —— 跨脚本、跨阶段，按时间串成一条链：

```bash
uv run python 流程1/scripts/journal_view.py                  # 全部
uv run python 流程1/scripts/journal_view.py --stage=E        # 只看全文下载阶段
uv run python 流程1/scripts/journal_view.py --action=download.fail   # 只看失败项
uv run python 流程1/scripts/journal_view.py --tail=40        # 只看最后 40 条
```

> 注意区分两个字段：`excluded`（**肯定性**判定为同名他人，已排除）与
> `undecidable`（语料缺作者/单位信息，**未判定、未排除**）—— 混为一谈会误以为成果被剔除。

---

## 交付物规范

```
<工作目录>/<作者名>/
├── <作者名>_paper_Exports/                # ⭐ 原文全文 PDF（v6 规范目录名）
│   ├── <年份>_<论文标题>.pdf               #   题名可读，重名自动合并
│   ├── 【补充材料】<年份>_<标题>.pdf        #   仅补充材料/图注页的残缺件（注明来源）
│   └── 全文清单.md                        #   逐篇获取状态 + 来源 + 页数 + 未获取原因
├── <作者名>_论文列表.md                     # ⭐ 规范交付名（GB/T 7714-2015 + DOI + 原文链接）
├── <作者名>_论文解读.md                     # ⭐ 时间线 / 逐篇四步 / 关键词 / 方向 / 范式
├── <作者名>_交付物索引.md                   # 交付总览 + 数据核验链 + 复现步骤
├── <作者名>_论文翻译_<刊><年>_中文版.pdf     # 全文中译（保留章节/图注/表注）
├── <作者名>_高质量文献列表.md               # ⭐v7 阶段 D2：反向查得的综述网络（综述优先）
├── <作者名>_代表性文献参考文献.md            # ⭐v7 阶段 G2：文章 → 参考文献（GB/T 7714）
├── <作者名>_研究方向文献终表.md              # ⭐v7 阶段 H2①：去重 + 相关性整理后的最终列表
├── <作者名>_研究方向综述.md                 # ⭐v7 阶段 H2③：review 结果（单一文件）
├── <作者名>_知识图谱.md + .work_cdp/graph/  # ⭐v7.2 阶段 K：三张图（SVG 优先；overview 固定 16:10，v7.5）+ 数据
├── .work_cdp/                             # 中间数据 + logs/
│   ├── prepare_plan.json                  #   ⭐v7 阶段 0：准备计划 + 阶段闸门（单一事实源）
│   ├── crossref_all.json                  #   题录核实结果（题录总表）
│   ├── disambiguated.json                 #   ⭐ 同名消歧（confirmed/review/dropped + code）
│   ├── periods.json / keyword_families.json  # 阶段分组 / 语义词族（均可选）
│   ├── tag_vocabulary.json                #   ⭐v7 受控标签词表（消除 subagent 标签漂移）
│   ├── journal_tiers.json                 #   ⭐v7 中科院分区表（可选，**覆盖**内置种子表）
│   ├── unpaywall.json                     #   OA 状态
│   ├── abstracts_openalex.json            #   摘要语料（解读用）
│   ├── fulltext_state.json                #   逐篇下载状态（断点续传）
│   ├── pdf_index.json                     #   DOI → 文件名 登记表（题名命名去重）
│   ├── interpret/                         #   ⭐ 阶段 H：tasks/ + results/ + manifest.json
│   ├── refs/                              #   ⭐v7 阶段 D2/G2/H2：池 + 终表 + 任务包 + 综述卡 + cache/
│   ├── translations/*.html                #   中译 HTML 中间件
│   └── logs/                              #   全部日志（含 edge_<port>.log）
└── 流程1/                                  # 本 skill
```

**PPT 产出（阶段 P，`make_ppt=yes` 时）**：

```
ppt-master/projects/<项目名>/
├── sources/<作者名>_汇报材料.md            # 由《论文解读》+《研究方向综述》汇编
├── design_spec.md                         # ⭐ 简化 Strategist 的确定性产物（I–X 全节）
├── spec_lock.md                           # ⭐ 同上（canvas/colors/typography/page_rhythm…）
├── svg_output/*.svg                       # 由主 agent **手写**（不得脚本批量生成）
└── exports/<项目名>_<时间戳>.pptx          # 最终交付物（＋ validation/*.report.json）
```

> **目录名兼容**：规范名为 `<作者名>_paper_Exports`。若已有历史 `<作者名>_Exports`
> 且其中已含 PDF，`store.exports_dir()` 会**继续沿用**（不迁移、不丢文件）并提示一次。
> 也可用 `PAPER_EXPORTS_DIR` 显式指定完整路径。

**原则**：
1. 新增内容一律**新建文件**，不覆盖用户既有文件（除非用户明确要求）。
2. 残缺/异常产物**不删除**，改名后保留（如 `【补充材料】…`），便于回溯。
3. 中译 PDF 与源 PDF 命名规则不同：源文件用**论文标题**，译文用**刊名缩写 + 年份**
   （便于按刊物/年份快速定位；同刊同年多篇加 `a/b` 后缀）。

---

## 版本控制（2026-10-02 建立）

**本目录 `流程1/` 是一个独立的本地 git 仓库**（`流程1/.git/`），与外层的
`K-Dense-AI/scientific-agent-skills` 克隆**互不相干**。

> ⚠️ **为什么不是提交进外层仓库**：外层是**上游公开仓库的克隆**（有真实 remote
> `origin`），把个人科研流程（含 88.8 MB 第三方 vendor）混进去，会污染上游历史、
> 且存在**误 push** 风险。独立仓库给出同样的回滚能力，零外部影响。

| 项 | 值 |
|---|---|
| 仓库位置 | `流程1/.git/`（约 1.4 MB） |
| 分支 | `main` |
| 首次提交 | `chore: 建立 流程1 版本控制基线` |
| 入库范围 | 文档 + `scripts/`(59) + `templates/` + `示例数据/` + 子流程，共 **84 个文件 / ~4.4 MB** |
| **不入库** | `vendor/`（第三方拷贝快照 88.8 MB，含 9.1 MB 生成物）—— 见 `.gitignore` |
| 换行策略 | `core.autocrlf=false`（本目录 CRLF/LF 混杂，**按磁盘原样存储**，避免全量「已修改」） |
| 提交身份 | 仓库级 `流程1 (local) <flow1@localhost>` —— **仅本地**，改法见下 |

### 日常用法

```bash
cd 流程1

git status                     # 看改了哪些文件
git diff                       # 看具体改了哪几行
git add -A && git commit -m "…" # 提交一次改动
git log --oneline              # 历史
git checkout -- <file>         # 丢弃某文件的改动（危险：不可恢复）
git revert <commit>            # 更安全的回退方式
```

**改提交身份**（如需）：

```bash
git -C 流程1 config user.name  "你的名字"
git -C 流程1 config user.email "you@example.com"
git -C 流程1 commit --amend --reset-author --no-edit   # 顺便改写首次提交的作者
```

### 与外层仓库的关系

外层仓库已把 `流程1/` 等本机 scratch 写进**只在本机生效**的
`.git/info/exclude`（**不是** `.gitignore` —— 后者是被跟踪文件，改动会进入上游 diff）。
因此外层 `git status` 不会把 `流程1/` 报成未跟踪，本目录的改动也**不会**混进上游提交。

### 若日后要改「vendor 不入库」的决定

删掉 `流程1/.gitignore` 里的 `vendor/` 一行，然后：

```bash
cd 流程1 && git add vendor/ && git commit -m "chore: 纳入 vendor 快照"
```

（注意：那会一次性写入约 88 MB 二进制。）
