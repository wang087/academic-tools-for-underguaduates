---
name: author-paper-analysis
description: 面向本科生文献检索与科研辅助的全流程 Agent 工作流。给定用户的一句话研究意向，自动完成"大方向确定（拆解输入、匹配关键词群）→ 匹配细分专业（从学校学部官网抓取导师名单与研究方向、归类后等用户选择）→ 平行检索优质综述（Google Scholar / PubMed / arXiv 等，英文关键词 + 综述优先）→ 领域概貌解读"，随后可选地接入作者论文分析（成果列表 / 全文获取 / 中文翻译 / 深度解读 / 研究方向综述 / 知识图谱 / PPT）。用于本科生进实验室选导师、大学生研究计划选题、"科学与社会"类课程文献查找与综述写作等场景。
---

# 本科生文献检索与科研辅助全流程（Author Paper Analysis · 本科版）

> ## ⛔ 使用本流程的硬性要求（最高优先级，先读）
>
> **本文件夹（`流程1/`）中的流程即为权威流程。执行本任务时必须遵守：**
>
> 1. **使用本流程** —— 依 `流程1/SKILL.md`（主流程）与各子流程
>    （`subagent-driven-review/SKILL.md` 阶段 H/H2、`ppt-integration/SKILL.md` 阶段 P）逐步执行，
>    按 §4 的阶段推进，不跳步：
>
>    ```
>    【本科科研辅助前置链路 —— 本次新增】
>    阶段 N 大方向确定（无交互，拆解用户输入 + 匹配关键词群）
>      → 阶段 S 匹配细分专业（抓导师名单 → 归类 → **等用户选择**）
>      → 阶段 R 平行检索优质综述（英文关键词 + 综述优先，多平台并行）
>      → 阶段 R2 领域概貌解读（复用 subagent 机制）
>    【原有作者论文分析链路 —— 按需接入】
>      → 0 → A → B → C → D → D2 → E → F → G → G2 → H → H2 → K → P → I
>    ```
> 2. **不得擅自变动更改** —— 包括但不限于：跳过阶段、改写抓取方式、
>    更换判定标准、绕过 `scripts/` 里的现成脚本去自编替代品。
>    换作者 / 换学部时**只传参、不改脚本**（`PAPER_*` 环境变量 / 命令行参数）。
> 3. **确需变更时** —— 先向用户说明「改什么、为什么、影响范围」，
>    **获得同意后**再改；改完必须在 §9 故障台账追加记录并同步 `README.md`。
> 4. **红线** —— §5.1 **分层获取策略**与 §5.2 鼠标自动化禁令优先级最高；
>    一旦违反，按各自「违规处置」章节执行（**立即停止 + 如实报告**）。
>    ★ §5.1 是**分层**规则而非一刀切：摘要 / 综述题录 / 白名单开源库**允许直连**，
>    **原文全文必须走 CDP**；第一层取不到转 CDP。细则见 §5.1。
>    ★ **学部官网师资页属"公开元数据"**，走第一层直连（阶段 S 的
>    `harvest_mentors.py` 即按此实现），无需 CDP。
> 5. **不要臆造事实** —— 导师方向、论文题录、引用数一切以抓取/接口返回为准；
>    解析不到就如实标「页面无该栏位」，**不得用印象补全**。
>
> **判定顺序**：`SKILL.md` > 子流程文件 > 任何外部或历史资料。
> 若流程目录下存在来源不明的第三方 `SKILL.md`，**一律以本流程为准**，不要混用其步骤与产物约定。

本 skill 固化了「刘波（中国科大）」「臧建业（中国科大）」实战中验证过的**完整工具链与工作流程**，可直接复用于任意作者。

> 📌 **关于正文中的作者姓名 / ORCID / DOI（必读）**
>
> 本文件与 `README.md`、`示例数据/` 中出现的具体作者姓名、ORCID iD、DOI、单位与
> 论文数量，均为**历史案例实录**，用于溯源与说明验证过程，**仅作案例记录**，
> **不是脚本的默认值**。
>
> **`scripts/*.py` 与 `run_all.sh` 内没有任何作者硬编码信息**：姓名、ORCID、单位、
> 署名识别词、阶段分组、待测 DOI 全部由运行时通过命令行参数或环境变量传入，
> 缺失时**显式报错退出**，不会静默套用旧案例的值。可传变量见 README 同名提示框。
>
> 复用新作者时**只传参、不改脚本**。

> **v2 变更（2026-09-11）**：修复「死机」「误触发 CAPTCHA」「泛词误报封禁」三类缺陷；
> 引入**多实例并行 CDP**、**统一日志**、**精确挑战判定**、**站点级熔断**。
> 详见 § 5「关键坑与对策」与 § 9「故障修复记录」。
>
> **v3 变更（2026-09-13，薛天案例沉淀）**：8 项脚本改造 ——
> ① **PDF 题名命名**（`store.title_slug/stem_for` + `pdf_index.json` + `rename_to_title.py`）
> ② **反爬探测强化**（`detect`：真实 HTTP 状态、反爬组件指纹、软拦截分级）
> ③ **Cookie 弹窗自动化**（`CONSENT_JS` 40+ 容器、iframe、`dismiss_consent()`）
> ④ **CDP 提速**（新增 L3 极速档、并行启动、0.25s 探活、自适应 settle）
> ⑤ **CAPTCHA 弹窗可靠化**（TTL 自动关闭 + 窗口置前 + 三重防卡死）
> ⑥ **直连禁令固化**（`BROWSER_ONLY` 扩充，验证类站点只走浏览器）
> ⑦ **中译渲染工具**（`render_html_pdf.py` + `render_one.sh`）
> ⑧ 收尾脚本（`make_inventory.py` / `render_rest.py` / 交付物索引）。
> 详见 § 9.1。
>
> ⛔ **三条最高优先级红线（v7.1 修订）**：
> **① 分层获取策略（§5.1，优先级最高）** ——
> **原文全文**（PDF / HTML 正文，含 OA）**禁止直连，一律经 CDP 浏览器获取**；
> **摘要、综述题录、以及白名单内已验证的开源数据库允许直连**（`fetch_direct.py`）；
> 第一层尝试失败**转 CDP**。**禁止**把白名单外的域名当成"开源库"直接取。
> **② 出版社内容站禁直连** —— 验证类站点
> （ScienceDirect/Cell/OUP/SAGE/Wiley/ACS/RSC/Springer/T&F）
> 绝不用 `curl`/`urllib` 直连（含探活请求）；
> **③ 鼠标自动化禁令** —— 绝不用 CDP `Input.*` 事件尝试通过人机验证
> （实测 `isTrusted=True` 但对判定无效），人机验证一律**转人工**。
>
> > **v4 的「CDP 强制抓取禁令」（全部内容一律走 CDP）已于 v7.1 被上方 ① 取代。**
> > §9 台账里 v4 的记录保持原样 —— 那是**当时的决策实录**，不追改。
>
>
> **v6 变更（2026-09-17，流程复核与加固）** —— 七类改动：
> ① **产出目录规范名改为 `<作者名>_paper_Exports`**（历史 `<作者名>_Exports` 自动沿用，不迁移不丢文件）；
> ② **CDP 启动参数补 6 个弹窗抑制开关**（崩溃恢复气泡 / 错误对话框 / 重发表单 /
>    搜索引擎选择页 / 翻译气泡）—— 其中「恢复页面」气泡因流程会 `taskkill /F`
>    而**必然**出现；新增 `PAPER_CDP_LEVEL` 统一启动与重启档位；
> ③ **Cookie 弹窗脚本修三处实质缺陷**（固定定位弹窗被 `offsetParent` 误判为不可见 /
>    只处理首个命中 / shadow DOM 查不到），并消除同一按钮的重复点击。
>    新增**离线自检** `selftest_consent.py`（零网络，验证于真实 Edge）；
> ④ **同名消歧结果接入下游**（此前 `disambiguated.json` 无人消费，等于白跑）——
>    新增 `scope.py`，下载与著录两侧共用，且只排除**肯定性判定**；
> ⑤ **阶段 H 解读改为并行 subagent 执行**，`subagent-driven-review/SKILL.md`
>    已按本流程重写（原为来源不明的第三方技能，含违禁的 HTTP 直取指引）；
>    新增 `interpret_prepare.py` / `interpret_build.py`；
> ⑥ **修复脚本契约断裂**：`enrich_crossref_authors.py` 读一个**从不产出**的
>    `master.json` 且把 authors 写成字符串（会让著录崩栈）、
>    `inventory_build.py` 同样只认 `master.json`、`run_all.sh` 的消歧步骤只打印不执行；
> ⑦ 新增 `build_list.py` 输出规范交付名 `<作者名>_论文列表.md`，并修正
>    GB/T 7714 的 `et al..` 双句点。详见 §9.4。
>
> **v7 变更（2026-09-19，文献综述 / PDF / 参考文献三线整合）** —— 五类改动：
> ① **新增阶段 0「准备阶段」**：把「输出格式（PDF/Markdown）」「是否翻译」
>    「是否反向查参考文献」「是否做 PPT」等**只有用户能定**的选择前置到第 0 步，
>    落盘 `.work_cdp/prepare_plan.json`，后续每个阶段以 `--check` 闸门决定是否执行。
>    新增脚本 `prepare_stage.py`；阶段 A–I 的**编号与语义全部不变**。
> ② **新增阶段 D2「反向查找参考文献」**：取作者每篇论文的 `referenced_works`，
>    倒推其学术脉络，产出《`<作者名>_高质量文献列表.md`》。
>    高质量 = 核心期刊（Nature/Science/Cell/PNAS 等）**或** 被引 > 200；**优先 Review**。
>    新增脚本 `refs_backfill.py`，全程走 OpenAlex 元数据 API（零风控、零 PDF 下载）。
> ③ **新增阶段 G2「代表性文献的参考文献列表」**：对作者本人满足「核心期刊 /
>    第一或通讯作者 / 中科院一区或 IF>5.0」的论文，逐篇列出其参考文献，
>    GB/T 7714 著录，产出《`<作者名>_代表性文献参考文献.md`》，结构固定为
>    **文章 → 参考文献**两部分。新增 `refs_bibliography.py`。
> ④ **新增阶段 H2「去重与相关性整理 → 最终列表 + 研究方向综述」**：
>    `refs_curate.py` 去重（DOI → 题名归一）并按作者主体研究方向打分、剔除无关论文
>    （**剔除项逐条留档，不静默丢弃**）；随后以 `subagent-driven-review` 的**同一套
>    并行 subagent 机制**做 review，`refs_prepare.py` → 并行 subagent → `refs_build.py`
>    产出《`<作者名>_研究方向综述.md`》。
> ⑤ **新增阶段 P「PPT 生成」**：整合 `ppt-master` 工作流，**简化其 Strategist Phase** ——
>    材料固定取自《论文解读》的文献亮点与阶段 H2 的 review 结果，
>    `design_spec.md` / `spec_lock.md` 由 `ppt_brief.py` 确定性生成，
>    跳过 Step 3 模板候选与 Step 4 两阶段阻塞式确认，直接进入 SVG 手写与导出。
>    详见 `流程1/ppt-integration/SKILL.md`。
>
> 另：解读模块新增**受控标签词表**（`templates/方法范式标签词表.json`）与
> **学科通用语义词族**（`templates/关键词词族_学科通用.json`），
> 二者由工作根目录《研究方法与关键词总表.md》派生，用于消除并行 subagent
> 之间的**标签漂移**。
>
> **v7.2 变更（2026-09-19，去重 / 知识图谱 / 综述策略 / PPT 接入）** —— 四类改动：
> ① **消除重复输出**：`论文列表`、`论文解读`、`研究方向综述` 三份交付物**只写一个文件**，
>    不再另写 `_完整版.md` 别名（两者内容逐字节相同，只是让交付目录出现重复文件）。
> ② **新增阶段 K「知识图谱」**（H2 之后、P 之前）：overview（作者为中心、
>    阶段→方向→最紧密综述、Louvain 社区上色、**记忆连接**）、词云（**剔除 mice 等
>    误导性常见词**，聚焦方法与实验范式）、方向聚类（近 N 年热点）。
>    **SVG 优先、失败退 PNG、背景透明、中文清晰**。含「这一簇在讲什么」与
>    「哪里在自相矛盾」两项质检。准备阶段新增 `make_graphs` 确认项。
> ③ **综述提取改版**：**被引 200 次不再是硬性要求**（降为排序信号），
>    改为「综述 + CNS 及高水平刊物 + 近年」加权优先；并为综述取
>    **OA 全文（Europe PMC）或详细摘要**，输出证据层级以提高 review 置信度。
> ④ **PPT 接入图谱与方向综述整理**：图谱自动进 §VIII 与 §IX，方向综述整理成页。
>
> **v13 变更（2026-10-03，解读档位整合 · 用户指令）** —— 把 skill
> `paper-interpretation-writing` 的**三档解读**接进全流程，并补齐门控：
> ① **新增两个阶段 0 选项**：`interpret_tier`（阶段 H 作者论文，**默认 L2**）与
>    `review_interpret_tier`（阶段 H2/R2 综述类，**默认 L3**）。
>    二者是**参数不是开关**，不控制任何阶段是否执行。
> ② **新增档位查询与门控自检**：`prepare_stage.py --tier H|H2|R2`
>    （stdout 只有 `L1|L2|L3`，便于 `$()` 捕获）与 `--audit`（门控齐全性自检）。
> ③ **所有解读模块同步**：`interpret_prepare` / `interpret_build`（阶段 H）、
>    `refs_prepare` / `refs_build`（H2）、`reviews_prepare` / `reviews_build`（R2）、
>    `ppt_brief`（P）一律读取并下发档位；任务包内嵌 `interpret_tier`，
>    subagent **以任务包为准**。
> ④ **提示词由"三件事"扩为"四件事"**：任务包路径 / 结果路径 / 只写自己那份 / **档位**。
>    少了档位，同一批 subagent 会各写各的深度。
> ⑤ **补齐门控表**：阶段 **N / S / R / R2** 此前**没有进闸门表** ——
>    `--check N` 返回 2（未知阶段），而 `run_all.sh` 的 `gate()` 用 `if` 判定，
>    2 与 3 一样落进 else 分支，该阶段被**静默当成跳过**。现新增
>    `undergrad_prelude`（默认开）管辖这四个阶段；`--audit` 会持续守住这类缺口。
> ⑥ **修正枚举取值大小写**：`--set interpret_tier=L3` 此前会报「取值不在列表内」
>    （`coerce` 把输入 `.lower()` 后与大写枚举比对，永远匹配不上）。现改为
>    大小写不敏感匹配、返回规范写法。
> ⑦ **`--tier` 的 stdout 保持干净**：把该分支移到 `logutil.get()` **之前**，
>    否则「=== prepare 启动 …」日志行会混进 stdout，`T=$(… --tier H)` 捕获到
>    `"INFO … L2"`，下游比对 `"L2"` 会**静默失败**。
> ⑧ **登记一处既有门控矛盾**（只记录、未改）：`output_format=markdown` 会关闭
>    阶段 F，但其自身说明与闸门表都写着「阶段 F 仍执行，只出中译 Markdown」。
>    见 `prepare_stage.py` 的 `AUDIT_NOTES` 与阶段 0 章节的 ⚠️ 块，**待拍板**。
>
> > ⚠️ 原有链路（阶段 0 → A → … → I）**脚本契约与产物约定不变**；
> > **阶段 H 的 L2 档渲染输出与改动前逐字节一致**（L2 就是默认档）。
> > 详见 §9.15。
>
> **v13.1（2026-10-03，同一批 · 用户拍板）** —— 修掉上面第 ⑧ 条登记的矛盾：
> `output_format` **不再是闸门**，阶段 F 改由 `translate` 单独判定，
> 本项只决定「要不要顺带渲染 PDF」。实测影响面**只有一格**
> （`translate=yes + markdown`：由静默跳过改为产出中译 Markdown，不渲染 PDF）；
> 默认组合与其余四格完全不变。`selftest_pipeline.py` 已加**行为级回归守卫**
> （45 → **47/47**）。详见 §9.16。

> **v8 变更（2026-10-02，本科科研辅助改造）** —— **大方向调整 + 三个阶段新增**。
> 用户指示：本流程由"作者论文分析"转向**适用于本科生文献检索处理及科研辅助的 Agent**。
> ① **新增阶段 N「大方向确定」**（**无交互**）：拆解用户输入为
>    `school / unit / topic / level`，并用**工作文件夹已有文件**
>    （`研究方法与关键词总表.md` + `templates/关键词词族_学科通用.json` +
>    `templates/方法范式标签词表.json`）匹配关键词群，产出中英双语检索词，
>    落盘 `.work_cdp/topic_plan.json`。**不新建关键词表**。
> ② **新增阶段 S「匹配细分专业」**：从学校学部官网**自动抓取全部导师名单与研究方向**，
>    按方向归类后**停下等用户选择**。新增脚本 `harvest_mentors.py`
>    （标准库实现，两段式缓存、断点可续、未命中不硬塞）。
>    实测中国科大生命科学与医学部 237 人：列表 15 秒 / 详情 4.5 分钟 / 0 失败。
>    新增规则表 `templates/导师方向规则.json`（可改，不写死在代码里）。
> ③ **新增阶段 R「平行检索优质综述」**：Google Scholar / PubMed / arXiv
>    **三线并行 + 综述优先**，**复用** `paper-lookup` / `literature-review` /
>    `research-lookup` / `citation-management` / `scientific-critical-thinking` 等既有 skill，
>    不从头自编检索逻辑。**检索式已实测通过**（Europe PMC 返回
>    `expected_total: 2418` 并自动计数对账）；同时实测确认
>    **arXiv 限流为每 3 秒 1 次请求**（连续请求返回 429 / `Rate exceeded.`），
>    故"并行"须理解为 **B 线与 A 线并行、C 线串行降速**。
> ④ **新增阶段 R2「领域概貌解读」**：**复用** `subagent-driven-review/SKILL.md`
>    的并行 subagent 机制，产出面向本科生的《领域概貌.md》
>    （含术语速查、争议点、推荐阅读顺序、选题切入点）。
> ⑤ **确立目录与术语默认约定**：未特别说明时，"流程"默认指 `流程1/`，
>    "工作文件夹"指仓库根 `scientific-agent-skills/`；`流程/`（无数字）为旧 v7.2 分支。
> ⑥ **红线补充**：学部官网师资页属公开元数据，走第一层直连（无需 CDP）；
>    解析不到的方向字段**如实标注、不得臆造**。
>
> > ⚠️ 原有链路（阶段 0 → A → … → I）**编号、语义、脚本契约全部不变**，
> > 只是不再是默认入口 —— 本科入门场景走 N → S → R → R2 即可交付。
>
> > 阶段顺序（**物理顺序以 §4 的排列为准**）：
> > `0 → A → B → C → D → D2 → E → F → G → G2 → H → H2 → K → P → I`。
> > D2/G2/H2/P 是**插入式子阶段**，编号后缀表示它与哪个主阶段相邻，
> > **不表示可以跳过**；阶段 I 仍是最后一步。
>
> **v7.1 变更（2026-09-19，获取策略分层）** —— 两项改动：
> ① **§5.1 由「CDP 强制抓取禁令」改为「分层获取策略」**（用户修订）：
>    **原文全文**（PDF / HTML 正文，含 OA）**仍然禁止直连、一律走 CDP**；
>    但**摘要、综述题录、以及工作文件夹下已验证可行的开源数据库允许直接获取**，
>    以减轻浏览器压力；**第一层尝试失败则转向 CDP 获取**。
>    白名单落成机器可读的 `templates/直连白名单.json`，
>    由新脚本 `fetch_direct.py` **强制 fail-closed 执行**（表外域名一律拒绝）。
> ② **新增 `fetch_direct.py`（阶段 H 摘要链的第一层）**：多源自动降级
>    （Semantic Scholar 批量 → OpenAlex → Europe PMC → Crossref → PubMed），
>    实测 12 篇全部命中、耗时 5.6 秒、**零浏览器占用**。
>    仍缺的写 `.work_cdp/direct_failed.json` 并转 `grab_abstracts.py --retry-failed`。
>    同时修掉 `fetch_openalex_abstracts.py` 会**覆盖其它源摘要**的隐患。
>
> > **v4 变更（2026-09-14，郭傲案例沉淀）**：
> > ① 新增 **CDP 强制抓取禁令** 并定为最高优先级，同时**移除「OA 直链纯 HTTP 直接获取」**
> > 这一级（`fetch_oa_direct.py` 退场）；
> > ② 新增同名消歧全链路脚本（`orcid_probe` / `disambiguate` / `enrich_crossref_authors`）；
> > ③ 新增中译素材链脚本（`jats_to_text` / `prep_paper` / `crop_by_images` /
> > `extract_figures` / `translation_build`）；
> > ④ 新增 `verify_pdfs` / `inventory_build` 收尾脚本；
> > ⑤ 修复 `download_all_fulltexts.py` 的 **CAPTCHA 弹窗门控**误用（`--manual-wait=0`
> > 会使弹窗链完全不触发）。详见 § 9.2。

---

## 0. 快速开始（最小可用路径）

```bash
# -1) ★ 阶段 0：准备阶段 —— 先在对话里收齐选择，再一条命令落盘
#     （不落盘也能跑，但后续阶段会**保守地跳过**可选环节并提示）
uv run python 流程1/scripts/prepare_stage.py \
    --set output_format=markdown --set translate=no --set reverse_refs=yes \
    --set make_ppt=no --set refs_bibliography=yes --set curate_review=yes \
    --set interpret_tier=L2 --set review_interpret_tier=L3      # ★ v13 解读档位
uv run python 流程1/scripts/prepare_stage.py --check-all     # 看闸门 + 两处档位
uv run python 流程1/scripts/prepare_stage.py --audit         # ★ v13 门控齐全性自检
#   ★ 档位查询（stdout 只有 L1|L2|L3，便于 $() 捕获）：
#     uv run python 流程1/scripts/prepare_stage.py --tier H     # 作者论文（默认 L2）
#     uv run python 流程1/scripts/prepare_stage.py --tier H2    # 研究方向综述（默认 L3）
#     uv run python 流程1/scripts/prepare_stage.py --tier R2    # 领域概貌（默认 L3）

# 0) 约定环境变量（所有脚本共用的可移植配置）
export PAPER_WORKDIR="<工作目录>/<作者名>"      # 作者工作目录
export CDP_PORT=9222                           # 默认端口，可省略
# ★ 作者信息一律运行时传入（脚本内零硬编码；缺失时脚本显式报错退出）
export PAPER_AUTHOR="<中文名>"                  # 产物文件名/抬头
export PAPER_AUTHOR_EN="<Given Family>"         # 英文名
export PAPER_ORCID="<ORCID iD>"                 # orcid_extract/pages 必需
export PAPER_NAME_TOKENS="<family, given>|<given family>"   # 本人署名识别（可选）
export PAPER_AFFIL="<单位/职称>"                 # 抬头（可选）

# 1) 建工作目录（全文目录规范名：<作者名>_paper_Exports）
#    store.exports_dir() 还会自动探测历史名 <作者名>_Exports 并沿用，二者不必手工迁移
mkdir -p "$PAPER_WORKDIR/${PAPER_AUTHOR}_paper_Exports"

# 2) 启动 CDP Edge —— 多实例并行（推荐 3 个；v3 推荐 L3 极速档）
bash 流程1/scripts/start_edge_cdp.sh "$PAPER_WORKDIR" 9222 3 3
#    或纯 Python 版本：
#    uv run --with websocket-client python 流程1/scripts/cdp_pool.py up --n=3 --levels=3

# 3) 身份核验 + 抓 ORCID 成果列表（ORCID 为空时改以作者主页/OpenAlex 为基准，见 § 4 阶段 A）
uv run python 流程1/scripts/orcid_probe.py "<ORCID>"                      # API：任职/教育/关键词/works
uv run --with websocket-client python 流程1/scripts/orcid_extract.py "<ORCID>"   # 网页：权威成果集

# 4) Crossref 核实题录 + 回填作者全名
uv run python 流程1/scripts/fetch_crossref_all.py && uv run python 流程1/scripts/retry_crossref.py
uv run python 流程1/scripts/enrich_crossref_authors.py --surname=<姓> --given=<名>

# 5) ★ 同名消歧（作者聚合普遍过度合并，此步不可省）
uv run python 流程1/scripts/disambiguate.py --surname=<姓> --given=<名> \
    --affil="<本人署名单位指纹 1>" --affil="<指纹 2>" \
    --anchor-doi=<确定无疑的代表作 DOI,逗号分隔>

# 6) OA 状态检查（仅用于判断获取难度，**不再据此走 HTTP 直取**）
uv run python 流程1/scripts/unpaywall_check.py

# 7) ★ 全文一律走 CDP 多实例（OA 与付费墙同等对待；遇真挑战时弹窗等人工）
#    注意：--manual-wait 必须 > 0，否则 CAPTCHA 弹窗链完全不触发（见 § 9.2）
uv run --with websocket-client --with pypdf python 流程1/scripts/download_all_fulltexts.py \
    --n=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35

# 8) PDF 题名命名（新下载已自动题名；此步补齐历史文件并登记索引）
uv run python 流程1/scripts/rename_to_title.py --dry-run && uv run python 流程1/scripts/rename_to_title.py

# 9) 生成 GB/T 7714 列表 +《全文清单.md》+ 全文校验
uv run python 流程1/scripts/build_list.py
uv run --with pypdf python 流程1/scripts/verify_pdfs.py
uv run --with pypdf python 流程1/scripts/inventory_build.py

# 10) 中文翻译素材链 → HTML → PDF（含 CJK 校验）
uv run --with pymupdf python 流程1/scripts/prep_paper.py "<原文PDF>"        # 按页分节文本 + 图注页索引
uv run --with pymupdf python 流程1/scripts/crop_by_images.py "<原文PDF>" "<figures.json>" "<图页>"
uv run python 流程1/scripts/translation_build.py "<content.json>" "<figures.json>" "<翻译.html>"
uv run --with pypdf python 流程1/scripts/render_html_pdf.py "<翻译.html>" "<输出PDF>"

# 11) ★ 论文解读（阶段 H）：组装任务包 → 并行 subagent 精读 → 汇总
uv run --with pypdf python 流程1/scripts/interpret_prepare.py       # 产出 interpret/tasks/*.json
#    然后**在同一条消息内**并行分派 4–6 个 subagent，每篇一个，
#    各读自己的 task.json、各写自己的 result.json
#    （提示词模板见 流程1/subagent-driven-review/SKILL.md § 7）
uv run python 流程1/scripts/interpret_build.py                      # 汇总为《<作者名>_论文解读.md》
```

---

## 1. 适用场景

> ### 📌 目录与术语约定（**默认，无需每次说明**）
>
> | 说法 | 指什么 |
> |---|---|
> | **"流程" / "本流程"** | 默认指 `流程1/`（本目录）。用户说"改流程""跑流程"而未特别说明时，**一律针对 `流程1/`** |
> | **"工作文件夹"** | 指仓库根目录 `scientific-agent-skills/`（即 `流程1/` 的上一级） |
> | **"工作根目录文件"** | 如 `研究方法与关键词总表.md`、`skills/`、`templates/` 等，均在**工作文件夹**下 |
>
> ⚠️ `流程/`（无数字后缀）是**旧的 v7.2 分支**，与 `流程1/`（v7.5）已分叉。> **除非用户明确点名 `流程/`，否则所有改动与执行都以 `流程1/` 为准。**
>
> ### 🎓 本流程的定位：**本科生文献检索与科研辅助 Agent**
>
> 面向的**真实痛点**（不是"帮教授分析论文"，而是本科生的三个典型处境）：
>
> | 痛点场景 | 用户实际会怎么说 | 本流程怎么接 |
> |---|---|---|
> | **大学生研究计划**（国创/大创/校创）选题 | "我想做个跟阿尔茨海默有关的项目，不知道从哪下手" | 阶段 N 拆解 → 阶段 S 给导师与方向 → 阶段 R 给该方向综述 |
> | **本科生进实验室**找导师 | "我是中国科大生命科学与医学部的，想找做免疫的老师" | 阶段 S 直接抓该学部全部导师并按方向归类 |
> | **"科学与社会"类课程**文献查找 | "要写一篇关于基因编辑伦理的综述，找几篇权威综述" | 阶段 N/R 走综述优先的检索，产出领域概貌 |
>
> **风格要求**：面向**没有科研经验的本科生**。
> 解释术语、给出"为什么这步重要"、避免默认对方懂 PubMed 检索式或影响因子概念。
> 产出物要能直接用于**联系导师的邮件**、**开题报告**或**课程论文**。

| 场景 | 说明 |
|---|---|
| **本科科研入门（主场景）** | 一句话意向 → 大方向 → 细分专业与导师 → 该领域优质综述 → 领域概貌 |
| **选导师 / 进实验室** | 按学部抓取导师名单与研究方向，归类后供选择 |
| **课程论文 / 综述作业** | 综述优先检索 + 证据可溯源的概貌解读 |
| 分析某作者全部论文 | 输入姓名/主页/ORCID，产出成果列表 + 解读报告（原有链路） |
| 成果列表整理 | 产出 GB/T 7714-2015 标准著录（含 DOI、原文链接） |
| 全文获取与翻译 | 开放获取（OA）论文下载全文 → 中文翻译 → PDF |
| 绕反爬取数据 | 出版社（ACS/Wiley/RSC/Elsevier）有 Cloudflare 验证，用 CDP + 真实 Edge |

---

## 2. 环境与前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Windows（本流程基于 win32 + Git Bash） |
| 浏览器 | Microsoft Edge（`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`） |
| Python | 通过 `uv` 运行，无需预装依赖（`uv run --with <pkg> python`） |
| 必备包 | `websocket-client`（CDP 通信）、`pypdf`（PDF 校验） |
| 可选 | Chrome（`--print-to-pdf` 渲染中文 PDF）、`pandoc` |

---

## 3. 工具清单

### 3.1 数据源 / API（**优先级最高 —— 零风控风险**）

| 工具 | 用途 | 调用要点 |
|---|---|---|
| **Cherry `web_fetch`** | 抓作者主页、搜索页 | 首个入口；国内高校主页可直接抓 |
| **Cherry `web_search`** | 定位 ORCID、核实作者归属 | 搜 `"<姓名>" <单位> ORCID` |
| **ORCID 公共 API** | 快速拉取成果列表 | `https://pub.orcid.org/v3.0/<ORCID>/works`；注意含重复条目 |
| **ORCID 网页（经 CDP）** | **权威成果列表**（去重后） | 页面显示 `Works (N of M)`，需翻页；比 API 更准 |
| **Crossref API** | 题录核实（期刊/年/卷/期/页/作者） | `https://api.crossref.org/works/<DOI>`；务必带 `User-Agent` 含 mailto |
| **Unpaywall API** | 判断是否 OA、取 OA PDF 链接 | `https://api.unpaywall.org/v2/<DOI>?email=...` |
| **Semantic Scholar API** | 补摘要 | `https://api.semanticscholar.org/graph/v1/paper/batch`（POST） |
| **OpenAlex** | 补摘要（倒排索引重建）；⚠️ 作者聚合**会过度合并**，须消歧 | `?select=abstract_inverted_index` |

> ⛔ **分层获取策略（v7.1 修订，取代 v4 的"一刀切"禁令）**：
>
> | 拿什么 | 怎么拿 |
> |---|---|
> | 元数据（题录 / 作者 / 被引 / 参考文献关系） | ✅ 直连 API（上表即是） |
> | **摘要**、**综述题录** | ✅ **直连**（`fetch_direct.py` 多源自动降级） |
> | 白名单内的开源数据库 | ✅ 直连（`templates/直连白名单.json`） |
> | **原文全文**（PDF / HTML 正文，含 OA） | ⛔ **必须走 CDP** |
> | 第一层取不到 | ➡️ **转 CDP**（`grab_abstracts.py --retry-failed`） |
>
> 完整规则见 § 5.1。**判定分界**：这次请求要和不要拿「论文正文」——
> 要正文必须 CDP；不要正文（摘要/题录/结构化元数据）走第一层直连，取不到再转 CDP。
>
> 浏览器配额仍然是需要节省的资源（多实例 + 站点熔断 + 拟人化间隔是成熟的节流手段），
> 但**摘要是最大宗的浏览器开销来源** —— 把它挪到第一层直连，
> 实测 12 篇全部命中、耗时 5.6 秒、**零浏览器占用**。

### 3.2 浏览器控制（核心）

| 工具 | 用途 |
|---|---|
| **Edge + CDP（多实例）** | 真实浏览器绕过 Cloudflare / 机器人验证；抓 ORCID 页面、出版社摘要与全文 |
| CDP 关键方法 | `Page.navigate`、`Page.enable`、`Runtime.evaluate`（`returnByValue`）、`Page.printToPDF` |
| CDP HTTP 端点 | `http://127.0.0.1:<port>/json`（列标签页）、`/json/version`、`/json/new?<url>` |

### 3.3 文件生成

| 工具 | 用途 |
|---|---|
| **`render_html_pdf.py`（v3）** | ⭐ 中译 HTML → PDF **一条命令**：渲染 + 页数/体积/CJK 校验（退出码 0/1/2） |
| **Chrome/Edge headless** | 上述脚本的底层调用：`--headless=new --print-to-pdf="out.pdf" --print-to-pdf-no-header "file:///..."` |
| **Cherry `to_markdown`** | PDF → Markdown（提取全文文本用于翻译） |
| **`rename_to_title.py`（v3）** | ⭐ PDF 批量重命名为论文标题 + `pdf_index.json` 登记 |
| **Python (uv)** | 数据整合、GB/T 7714 著录生成、关键词统计、清单生成（`make_inventory.py`） |
| **Cherry `report_artifacts`** | 任务结束时申报最终交付物 |

---

## 4. 标准工作流

**阶段顺序（物理顺序以此为准）**：

```
【前置链路：本科科研辅助 —— 从一句话意向走到"领域概貌"】
N 大方向确定（无交互：拆解用户输入 + 匹配关键词群）
  → S 匹配细分专业（抓学部导师名单 → 按方向归类 → **等用户选择**）
  → R 平行检索优质综述（英文关键词 + 综述优先，Google Scholar / PubMed / arXiv 并行）
  → R2 领域概貌解读（并行 subagent，复用阶段 H 机制）

【原有链路：作者论文分析 —— 按需接入，非本科入门必需】
0 准备阶段
  → A 作者定位   → B 启动 CDP   → C 抓 ORCID 成果   → D Crossref 核实 + 同名消歧
  → D2 反向查找参考文献（综述网络 + 高质量文献列表）
  → E 全文获取   → F 翻译与 PDF 导出   → G 生成论文列表
  → G2 代表性文献的参考文献列表
  → H 论文解读（并行 subagent）
  → H2 去重与相关性整理 → 最终列表 → 研究方向综述（review）
  → K 知识图谱
  → P PPT 生成（可选，整合 ppt-master）
  → I 收尾
```

> ⚠️ **D2 / G2 / H2 / K / P 是插入式子阶段**：编号后缀只表示它**紧邻**哪个主阶段，
> **不表示可选**。它们是否执行由**阶段 0 的准备计划**决定（见阶段 0 的闸门表）。
> 阶段 I 仍是最后一步。
>
> 💡 **两条链路的关系**：本科入门场景通常**只走 N → S → R → R2** 就到交付
> （领域概貌 + 导师候选 + 综述清单）。当用户**已经锁定某位导师**、想深挖
> 这位老师的成果时，再从阶段 0 接上原有链路。**不要**默认跑全程——
> 原有链路的全文下载/翻译/PPT 对本科生选题阶段是过重的。

---

### 阶段 N：大方向确定（**最先执行，无交互**）

> **目的**：把用户一句口语化的意向（"我想做跟老年痴呆有关的""对基因编辑伦理感兴趣"）
> 转成**结构化的研究大方向 + 关键词群**，让后续阶段有明确的检索靶子。
>
> ⛔ **本阶段不需要与用户交互** —— 直接根据用户已有输入推断并落盘，
> 然后**继续往下走**。只在输入**完全无法判断大方向**时才回问一句。

**执行步骤**：

1. **解析用户输入**，抽取四个字段：

   | 字段 | 说明 | 例（"我是中国科大生医部的，想做阿尔茨海默早期诊断"） |
   |---|---|---|
   | `school` | 学校 | 中国科学技术大学 |
   | `unit` | 学部/学院 | 生命科学与医学部 |
   | `topic` | 研究主题（自然语言） | 阿尔茨海默病的早期诊断 |
   | `level` | 层次（本科/研究生/课程作业） | 本科 |

2. **匹配关键词群** —— **复用工作文件夹已有文件，不要重新整理**：

   | 来源 | 用途 | ⚠️ 它**不是**什么 |
   |---|---|---|
   | `scientific-agent-skills/研究方法与关键词总表.md` | 24 个研究领域 × **常用研究方法** + 关键词 + 对应技能（**主表**） | ❌ **不是疾病/主题索引** |
   | `流程1/templates/关键词词族_学科通用.json` | 机读版语义词族，供机器匹配与后续词频归一 | ❌ 只收英文单词与中文 2-gram，**不含短语** |
   | `流程1/templates/方法范式标签词表.json` | 受控标签，消除后续解读的标签漂移 | — |

   > ⛔ **不要新建关键词表**。上表已是全仓库 163 个 skill × 24 领域的沉淀。
   >
   > ### ⚠️ 关键认知：总表是「**方法**分类」，不是「**主题**分类」
   >
   > 实测（2026-10-02）：拿「阿尔茨海默」「早期诊断」「生物标志物」去总表里搜，
   > **一个都命中不了** —— 因为总表的 24 个领域是按**研究手段**切的
   > （"单细胞与多组学""机器学习与深度学习""质谱与蛋白质组学"），
   > 而用户说的是**研究对象/疾病**。这是两套正交的坐标系。
   >
   > **所以阶段 N 的正确做法分两段**：
   >
   > | 段 | 做什么 | 产出 |
   > |---|---|---|
   > | **① 主题词**（用户给什么用什么） | 从用户 input 直接抽主题词，**同义扩展**。如"阿尔茨海默"→ 阿尔茨海默病 / AD / Alzheimer's disease / dementia / 老年痴呆 | `topic_terms` |
   > | **② 方法词**（查总表拿） | 用①的主题词**在总表里找"做这个主题会用到什么方法"**，取该领域的关键词群 | `method_terms` |
   >
   > 例：主题"阿尔茨海默早期诊断" → 可能涉及**临床与流行病**（biomarker, diagnosis,
   > cohort）+ **成像与显微**（imaging, MRI, PET）+ **机器学习**（classification,
   > prediction）→ 这三节的关键词群就是检索用的方法词。
   >
   > **两段合起来才是完整的检索词**，并且要**中英双语**（英文用于阶段 R）。
   > 词族表的作用是**合并同义写法**，不是提供主题词。

3. **产出中英双语检索词组**：

   ```json
   {
     "topic_terms_cn": ["阿尔茨海默病", "老年痴呆", "认知障碍"],
     "topic_terms_en": ["Alzheimer's disease", "AD", "dementia",
                        "mild cognitive impairment", "MCI"],
     "method_terms_en": ["biomarker", "diagnosis", "imaging", "PET", "MRI",
                         "classification", "prediction", "cohort"],
     "matched_domains": ["临床与流行病", "成像与显微", "机器学习与深度学习"]
   }
   ```

   > 💡 **检索式组装**：阶段 R 用
   > `(主题词 OR 主题词…) AND (方法词 OR 方法词…)`，
   > 主题词负责"找对领域"，方法词负责"过滤到研究方法匹配的文献"。

4. **落盘** `.work_cdp/topic_plan.json`：

   ```json
   {
     "school": "中国科学技术大学",
     "unit": "生命科学与医学部",
     "level": "本科",
     "topic_cn": "阿尔茨海默病的早期诊断",
     "topic_terms_cn": ["阿尔茨海默病", "老年痴呆", "认知障碍"],
     "topic_terms_en": ["Alzheimer's disease", "AD", "dementia",
                        "mild cognitive impairment", "MCI"],
     "method_terms_en": ["biomarker", "diagnosis", "imaging", "PET",
                         "classification", "prediction", "cohort"],
     "matched_domains": ["临床与流行病", "成像与显微", "机器学习与深度学习"],
     "search_query_hint": "(Alzheimer OR dementia OR \"mild cognitive impairment\") AND (biomarker OR diagnosis OR imaging)"
   }
   ```

   > `search_query_hint` 是给阶段 R 的**检索式草稿**，可直接喂给
   > `paginate.py --query`（Europe PMC 语法），阶段 R 再按 B/C 线各自微调。

5. **继续执行阶段 S**，不需要等用户确认。

> 💡 **为什么要先做这一步**：用户给的是"想做什么"，而检索需要的是
> "用哪些词去哪个领域找"。这一步就是把前者翻译成后者，且**不消耗用户耐心**。

---

### 阶段 S：匹配细分专业（抓导师名单 → 归类 → **等用户选择**）

> **目的**：本科生找课题的真实起点是"**哪位老师在做这个方向**"，
> 而学部官网的师资页动辄 200+ 人、分十几页翻，逐个点开读方向极其费时。
> 本阶段用脚本把「自动爬取 → 初步处理 → 按方向归类」一次做完，
> 只把**结构化候选名单**交给用户。

**一条命令跑完（推荐）**：

```bash
# 按阶段 N 落盘的 school/unit 选择学部；内置表见 --list-units
uv run python 流程1/scripts/harvest_mentors.py \
    --unit life-med \
    --out .work_cdp/mentors
```

**分步跑（调试 / 断点续跑）**：

```bash
# ① 只看有哪些内置学部
uv run python 流程1/scripts/harvest_mentors.py --list-units

# ② 只抓列表页（秒级，先核对人数是否与官网一致）
uv run python 流程1/scripts/harvest_mentors.py --unit life-med --stage=list

# ③ 续抓详情（已抓过的自动跳过，中断可续）
uv run python 流程1/scripts/harvest_mentors.py --unit life-med --stage=detail

# ④ 改了 templates/导师方向规则.json 后，只重新归类（不联网）
uv run python 流程1/scripts/harvest_mentors.py --unit life-med --reclassify
```

**自定义学部**（内置表没有的学校/学院）：

```bash
# 把列表页 URL 粘进来，把页码位置换成 {page}
uv run python 流程1/scripts/harvest_mentors.py \
    --list-url "https://faculty.ustc.edu.cn/xyjslb.jsp?PAGENUM={page}&id=1150&..." \
    --school 中国科学技术大学 --unit-name 生命科学与医学部
```

### ★ v9：深入「教师主页」查找研究方向

脚本默认开启**深度模式**：主页没有方向时，会继续

1. 从主页提取指向**「研究领域 / 研究方向」子页**的导航链接
   （`/slug/zh_CN/yjgk/<id>/list/index.htm` 等），
2. 抓取最多 3 个子页并解析方向正文，
3. 记录 `direction_source`（方向来自哪个子页）与原始存档。

```bash
--no-deep     # 关闭深度模式，只抓主页（排查用）
```

> ### ⚠️ 实测结论：深度模式确实补回了大批数据，但余下的确是网站没填
>
> 2026-10-02 全量实测（USTC 生命科学与医学部 237 人）：
>
> | 指标 | 仅抓主页 | ＋子页(v9) | ＋学院官网(v10) |
> |---|---|---|---|
> | 有研究方向 | 107 人（45%） | 175 人 | **149 人（63%）** |
> | 无研究方向 | 130 人 | 62 人 | **85 人** |
>
> > ⚠️ v9 的 175 人是**虚高**：其中 42 人的"方向"实为面包屑导航
> > （`研究领域 当前位置：中文主页 >> 科学研究`）等假阳性，v10 已剔除。
> > **149 是经逐条核对后的真实数字。**

---

## 阶段 S：学部/学院官网导师抓取（v11：唯一数据源）

### ★ 硬性规定：只从**学部/学院官网**取，不用学校教师主页系统

学校教师主页系统（`faculty.ustc.edu.cn`、`person.zju.edu.cn` 等）有两个硬伤：

| 问题 | 实测证据 |
|---|---|
| 只登记**已在系统里维护主页**的教师，覆盖面不全 | USTC 生医部 faculty 系统 237 人，学院官网另有其人 |
| 大量页面**根本没有「研究方向」栏位** | USTC 237 人中 **130 人**没有；页签存在但内容是 `<p>暂无内容</p>` |

学部/学院官网的师资页由**学院自己维护**，方向/邮箱/职称常**直接写在列表页上**，
新鲜度与完整度都更好。**因此本流程统一以学部官网为准。**

### 用法

```bash
# 看有哪些学校/学院可选
uv run python 流程1/scripts/harvest_faculty.py --list-units

# 全量抓取（列表 → 详情 → 归类 → 报告）
uv run python 流程1/scripts/harvest_faculty.py --unit zju --out outputs/06_浙大生科院
uv run python 流程1/scripts/harvest_faculty.py --unit ustc:life-med --out outputs/01_导师名单

# 只抓列表（快速核对人数）
uv run python 流程1/scripts/harvest_faculty.py --unit zju --stage=list
```

`--unit` 接受三种写法：学校 key（`zju`）、`学校:学院`（`zju:life-sci`）、
中文名/别名（`浙大`、`浙江大学生命科学学院`）。

### 新增一所学校：只改配置，不改脚本

编辑 `templates/学部官网源.json`，在 `universities` 下加一个条目：

```json
"xxx": {
  "name": "XX大学",
  "alias": ["XX大"],
  "units": {
    "life-sci": {
      "name": "生命科学学院",
      "base": "https://bio.xxx.edu.cn",
      "list_type": "wp_teacher_card",
      "sections": [
        { "name": "教授", "path": "/szdw/js/list.htm", "paging": "auto", "detail": "star" }
      ]
    }
  }
}
```

- `detail: "star"` —— 列表页**已含**方向与邮箱（浙大生科院所属），不进详情页
- `detail: "fetch"` —— 列表页只给姓名+链接，须进详情页抽（USTC 生医部所属）

### 两种列表模板（`list_types`）

| 模板 | 结构 | 已验证站点 |
|---|---|---|
| `wp_teacher_card` | `<li class=list-item>` + `<h3>姓名 职称</h3>` + `span.direction` + `span.email` | 浙大生科院 `www.cls.zju.edu.cn` |
| `wp_newsinfo_card` | 列表只给姓名+链接，方向在详情页正文 | 中科大生医部 `biomed.ustc.edu.cn` |

### 实测（2026-10-02）

| 学校 | 栏目 | **人数** | **有方向** | 耗时 |
|---|---|---|---|---|
| 浙江大学 生命科学学院 | 12 | **153** | **110（72%）** | 列表 18 秒 + 详情 100 秒 |
| 中国科学技术大学 生命科学与医学部 | 5 | 161 | 112（70%） | — |

浙大 12 个栏目各自抓到的条数与官网「总共 N 条」**逐项吻合**（如百人计划 26、
求是特聘 25、生态所 22），说明分页无遗漏。

### ⛔ 抽取实现的坑（都踩过）

| # | 坑 | 症状 | 正确做法 |
|---|---|---|---|
| 1 | 容器懒匹配 `(.*?)</div></div>` | 正文只剩 5 字 | 按**标签配平**取容器 |
| 2 | 线索词表不全 | 漏掉「主要研究方向**及内容**」「研究方向」独立成行 | 线索词要含变体与**行尾无冒号**的独立标题 |
| 3 | 编号条目后遇非编号行就停 | 毕国强 3 条只抽到 1 条 | 条目后的说明段**不该中断扫描** |
| 4 | 姓名未去职衔/标记词 | 出现「徐平龙 （兼聘）」「林爱福 长聘」「徐程 高级」 | 用**长词优先**的职称表 + 单独剥离 `兼聘/长聘` 标记 |
| 5 | 姓名字间空格未规范 | 站点排成「周 琦」，与「周琦」重复计数 | 两字中文名合并空格（英文名保留） |
| 6 | 相对 URL 未补主机 | `/clscn/2022/…/page.htm` 抓取失败、静默跳过 | 用 `_abs_url()` 补全 |
| 7 | 跨栏目未合并 | 同一人同时列在「人才队伍」与「研究所」，209 条只有 184 人 | 按姓名合并，方向取**并集**、栏目存 `sections` |
| 8 | 分隔符不全 | 「计算生物学 · 生物物理学」只按逗号切 → 与已拆形式重复 | 分隔符补 `·•・|｜/／` |

### 清洗判据

`harvest_faculty.is_direction()` 是**唯一**判据入口，必须剔除的假阳性：

- 面包屑：`研究领域 当前位置：中文主页 >> 科学研究 >> 研究领域`
- 多栏目导航行：`科学研究 研究领域 论文成果 专利 著作成果 科研项目`
- 栏目名：`发表文章`、`招生招聘`、`基本信息`、`我的相册`、`教师博客`
- 论文列表：`Zuo X#, Zhao R#, …`；无中文引文片段 `Duan Y, Young R, Schnabl B`
- 基金：`…（国家自然科学基金项目,中科院创新项目子课题）` → **只剥括号，保留中文主干**
- 招生方向：`招生方向：细胞生物学…`（招生口径≠研究方向）

### 📌 已弃用脚本（保留但默认拒绝运行）

| 脚本 | 状态 | 说明 |
|---|---|---|
| `harvest_mentors.py` | ⛔ **已弃用** | 抓学校教师主页系统；改用 `harvest_faculty.py` |
| `harvest_colleges.py` | ⛔ **已被取代** | 功能并入 `harvest_faculty.py`（配置驱动） |

两者都会打印弃用说明并 `exit 2`；确需运行加 `--allow-deprecated`。

教师个人主页系统（`faculty.ustc.edu.cn`）只覆盖一部分人，且不少人**确实没填**方向。
学院官网（`biomed.ustc.edu.cn`）是**另一套站点、另一种模板**，方向写在正文里。

```bash
# 列出可选学院
uv run python 流程1/scripts/harvest_colleges.py --list-colleges

# 抓列表 + 详情（5 个学院）
uv run python 流程1/scripts/harvest_colleges.py --out outputs/05_学院官网

# ★ 改规则后**离线**重解析（不联网，秒级）—— 强烈推荐先用这个验证
uv run python 流程1/scripts/harvest_colleges.py --out outputs/05_学院官网 --reparse-only

# 合并进阶段 S 的名单（按姓名匹配）
uv run python 流程1/scripts/harvest_colleges.py --out outputs/05_学院官网 \
    --merge-into outputs/01_导师名单/roster_detail.json
```

### 页面结构（实测）

```text
师资队伍 → 院系师资 → {生命科学学院 smkxy | 基础医学院 jcyxy |
                       临床医学院 lcyxy | 生物医学工程学院 swyxgcxy |
                       附属第一医院 swx}
列表页分页：list.htm / list2.htm / list3.htm（每页 28 条）
详情页：  /<年>/<月日>/c<栏目id>a<文章id>/page.htm

详情页结构：
<div class="Content"><div class="newsinfo">
  <div class="Titleinfo"><h2 class="tits">姓名</h2><div class="date">…来源：…</div></div>
  <div class="article">
    职衔行 → 履历段 → 学术任职段
    主要研究方向及内容： → 介绍句 → 1、2、3 …     ← 方向区
    代表性论文：… → 招生招聘：… → 联系方式：…      ← 其余分区
  </div>
</div></div>
```

### ⛔ 抽取实现的四个坑（都踩过，别再犯）

| # | 坑 | 症状 | 正确做法 |
|---|---|---|---|
| 1 | 容器懒匹配 `(.*?)</div></div>` | 正文只剩 5 个字（在第一个内层 `</div>` 收尾） | 从容器起点**按标签配平**取内容 |
| 2 | 线索词表不全 | 漏掉「主要研究方向**及内容**」「研究方向」独立成行 | 线索词要含"及内容"变体与**行尾无冒号**的独立标题 |
| 3 | **编号条目后遇非编号行就停** | 毕国强 3 条只抽到 1 条；全量漏 62 页 | 条目后的**说明段落不是新条目，但不该中断扫描**；只在遇到下一分区标题时停 |
| 4 | 用「条数更多」判断数据优劣 | 1 条面包屑 vs 1 条真方向 → tie 时丢弃真方向 | 先按 `_is_direction` **过滤**再比数量 |

### 抽取到的结构化字段（161 人）

| 字段 | 覆盖 | 说明 |
|---|---|---|
| `title_line` | 30/161 | 职衔行（讲席教授 / 博导 / 国家杰青…） |
| `directions` | **112/161** | 研究方向（编号条目，已清洗） |
| `direction_intro` | 57/161 | 方向区综述性说明 |
| `bio` | 86/161 | 履历/简介（截 800 字） |
| `email` | **151/161** | 邮箱 |
| `telephone` | 67/161 | 电话 |
| `homepage` | 50/161 | 实验室/学者主页 |
| `recruit_note` | 59/161 | 招生说明（是否欢迎本科生） |
| `representative_papers` | 96/161 | 代表性论文（前 6 条，仅供参考） |

### 清洗判据（`_is_direction`，两脚本共用）

必须剔除的**假阳性**（都实测出现过）：

- 面包屑：`研究领域 当前位置：中文主页 >> 科学研究 >> 研究领域`
- 多栏目名导航行：`科学研究 研究领域 论文成果 专利 著作成果 科研项目`
- 栏目名：`发表文章`、`招生招聘`、`基本信息`、`社会兼职`、`学位：博士`
- 论文列表：`Zuo X#, Zhao R#, Wu M#, …`；无中文的引文片段 `Duan Y, Young R, Schnabl B`
- 基金：`…（国家自然科学基金项目,中科院创新项目子课题）` → 只剥括号，**保留中文主干**
- 招生方向：`招生方向：细胞生物学，分子生物学…`（是招生口径，不是研究方向）

> **判据集中在 `harvest_mentors._is_direction` 与 `harvest_colleges._is_noise` 两处**
> （前者管 catalogue 层，后者管抽取层）。改一处要同步另一处 —— 曾经两处标准漂移，
> 导致 9 条残留漏网。

### ⚠️ 清洗只能做一遍

踩过的坑：先跑 merge、再跑 clean、再跑更严格的 clean，**误差累积**，
把 190 人砍到 144 人（其中大量是误删）。正确顺序是
**恢复 → 合并 → 清洗**，在**一个脚本里一次跑完**；且
"清洗后为空"**不能**覆盖原有的 `no_direction_reason`
（否则会把"官网暂无内容"错写成"判定为导航行"）。

**产出物**（在 `--out` 目录下）：

| 文件 | 内容 |
|---|---|
| `roster_list.json` | 全部教师（姓名 / slug / 主页 URL），按官网顺序 |
| `roster_detail.json` | 每人 + 职称 + 研究方向原文 + 解析状态 |
| `mentors_grouped.json` | 按细分方向归类的机读结果（供 agent 消费） |
| `导师名单与方向归类.md` | **给人看的报告**：分方向的表格，带主页链接 |
| `raw_list/` `raw_detail/` | 原始 HTML 存档（解析有疑问时人工核对） |

**归类规则**：`流程1/templates/导师方向规则.json`（关键词子串匹配，可改）。
未命中任何规则的人进「未分类」桶 —— **脚本不会把人硬塞进某个方向**。

> ⚠️ **归类是关键词粗分类，不是权威学科划分**；一位老师可能出现在多个方向下。
> 报告里每条方向都保留**主页原文**，务必提示用户以老师主页为准。

**★ 本阶段结束时必须停下，等用户选择**：

把归类结果（方向 + 每方向的代表性导师）呈现给用户，并**等待其回复**。
脚本末尾会自动打印建议话术。要点：

- 列出各方向及人数，每方向举 3–8 位代表老师；
- 说明"也可以直接点名某位老师，或说你更关心的问题"；
- 明确告诉用户**这只是粗分类**，可点链接自己看老师主页。

用户选定后 → 进入阶段 R。

> 💡 **实测参考**（中国科学技术大学生命科学与医学部，2026-10）：
> 列表 237 人（与官网自称 237 完全一致）约 15 秒抓完；
> 详情 237 人约 4.5 分钟，0 失败；其中约 123 人页面有结构化「研究方向」栏位，
> 其余页面确无该栏位（脚本如实标注，不臆造）。

---

### 阶段 R：平行检索优质综述（**多平台并行 + 综述优先**）

> **目的**：让本科生在**不读几十篇原始论文**的前提下，先拿到该领域的**概貌**。
> 综述（Review）是最高效的入口 —— 它已经把领域的来龙去脉、主要流派、
> 争议点整理过一遍。
>
> ⛔ **必须复用工作文件夹下已有的 skill，不要从头自编检索逻辑。**
> 见下方「复用哪些 skill」。

**检索策略：按方向选库 + 综述优先**

> ### ★ v12（2026-10-04）：库要**按方向**选，不是三个都跑
>
> **来源**：用户指令 ——「根据不同方向匹配不同数据库。例如计算生物学、计算机科学、
> 人工智能匹配 arXiv；生物、医学匹配 PubMed、Europe PMC 等等」。
>
> **实测依据（2026-10-04，中科大生医部「免疫与感染」）**：三线全跑时
> arXiv 返回的 24 条里绝大多数是**计算机领域**的 'immune'（入侵检测 / 程序验证）
> 与**传染病建模**，对湿实验免疫学**完全无用**；Google Scholar 反爬不可用。
> **三线里只有一条真正产出文献 —— 所谓「并行」名不副实。**
>
> 反过来，给「机器学习」只查 PubMed 也几乎命中不到预印本方法学论文。
> **故：按方向路由，skip 掉无覆盖的库。**

**路由表（唯一选库依据）**：`流程1/templates/方向数据库路由表.json`

覆盖**两套坐标系**（这正是阶段 N 警告过的「正交坐标系」问题）：

| 坐标系 | 数量 | 来源 | 例 |
|---|---|---|---|
| **方法领域** | **24 个** | `研究方法与关键词总表.md` | 机器学习与深度学习、质谱与蛋白质组学 |
| **学部方向** | **12 个** | `templates/导师方向规则.json`（阶段 S 归类） | 免疫与感染、生物信息与计算 |

> ⚠️ **用户通常先选「学部方向」**（因为那是"谁在做"），但**库是按"研究手段"配的**。
> 故路由表含 `faculty_direction_map`，把 12 个学部方向映射到方法领域，
> **无需用户自己换算**。例：
> - 「免疫与感染」→ 药理与临床研究 + 单细胞与多组学 → **只跑 Europe PMC，不跑 arXiv**
> - 「生物信息与计算」→ 生物信息学与基因组学 + 机器学习 → **Europe PMC + arXiv 都要**
> - 「成像与生物物理」→ 医学影像与数字病理 → **临床在 PubMed，影像 AI 在 arXiv**

```bash
# ① 查该方向该跑哪些库（零网络，秒级）—— 方法领域名或学部方向名都接受
uv run python 流程1/scripts/route_databases.py --domain 机器学习与深度学习
uv run python 流程1/scripts/route_databases.py --domain 免疫与感染 --terms "innate immunity" "T cell"
uv run python 流程1/scripts/route_databases.py --list-domains      # 看 24 个方法领域各自的路由
uv run python 流程1/scripts/route_databases.py --list-databases    # 看 7 个库各自的能力
uv run python 流程1/scripts/route_databases.py --self-test         # 自检路由表
```

**主路由一览（节选；完整表见 JSON）**：

| 方向 | 主力库 | 补充库 | **明确不跑** |
|---|---|---|---|
| 机器学习与深度学习 | **arXiv** | OpenAlex | ⛔ Europe PMC / bioRxiv / medRxiv |
| 统计与数据分析 / 可视化 | **arXiv** | OpenAlex | ⛔ Europe PMC |
| 量子计算 / 材料物理 / 天文地学 | **arXiv** | OpenAlex | ⛔ Europe PMC |
| 药理与临床研究 | **Europe PMC** | medRxiv | ⛔ arXiv |
| 神经科学 / 质谱 / 系统发育 | **Europe PMC** | bioRxiv | ⛔ arXiv |
| **生物信息学与基因组学** | **Europe PMC + arXiv** | bioRxiv | ——（**唯一两侧都要**） |
| **医学影像与数字病理** | **Europe PMC + arXiv** | medRxiv | ——（临床在 PubMed，影像 AI 在 arXiv） |
| 网络检索与情报分析 / 科研写作 | **OpenAlex** | Europe PMC | ⛔ arXiv |

> ⚠️ **arXiv 与 PubMed 是两套不同的文献生态**：
> arXiv 覆盖 cs / stat / physics / math / q-bio / eess；
> Europe PMC（含 MEDLINE/PubMed）覆盖生物医学。
> **两者交集很小** —— 这正是要按方向路由的原因。

**兜底策略（方向未登记时）**：`Europe PMC → OpenAlex` 依次补，
直到 ≥ 6 篇或库用尽；并在报告里**明确标注该方向未登记路由，用的是兜底库**。

**检索线（按路由结果执行，不再是固定三线）**：

| 线 | 平台 | 何时跑 | 检索式要点 |
|---|---|---|---|
| **主力** | 路由表 `primary` 指定的库 | **必跑** | 见下方「各库语法差异」 |
| **补充** | 路由表 `secondary` 指定的库 | **仅当主力结果 < 6 篇** | 同上 |
| **核实** | Crossref | 选定后校验 DOI/卷期页 | `query.bibliographic=` |
| ~~A~~ | ~~Google Scholar~~ | ⛔ **不跑** | 无官方 API 且反爬；需要时**如实报告该线未取到** |

> ### ⛔ 各库语法**互不通用**（路由脚本已按库分别生成，勿手工混用）
>
> | 库 | 语法 | 综述过滤 | 能否按被引排序 |
> |---|---|---|---|
> | Europe PMC | Europe PMC 语法 | `PUB_TYPE:"Review"` | ✅ 加 `AND (SORT_CITED:y)` |
> | arXiv | Atom / `all:` 语法 | `all:"review" OR all:"survey"` | ❌ 不支持 |
> | OpenAlex | **原始参数串** | `filter=type:review` | ✅ `sort=cited_by_count:desc` |
> | Crossref | **原始参数串** | —— | ❌（仅核实用） |
> | bioRxiv / medRxiv | **日期区间**，非关键词 | —— | ❌ |
>
> ⛔ **`paginate.py` 没有 `--sort` 开关** —— 按被引排序的语法**必须写进 query 里**
> （Europe PMC 用 `AND (SORT_CITED:y)`）。
> **实测教训**：不加排序时 Europe PMC 默认**按最新返回**，取回的全是当年、
> 被引 0–3 次的新文，作为本科入门材料价值很低；加上后被引 5000+ 的经典综述才浮上来。

> ### ✅ 实测通过的检索式（2026-10-02，全程零 API key）
>
> **B 线（Europe PMC，推荐主力）** —— 一条命令带计数对账、OA 标记、PMID/PMCID/DOI：
>
> ```bash
> python <工作文件夹>/skills/paper-lookup/scripts/paginate.py \
>     --api europepmc \
>     --query '("Alzheimer" AND "early diagnosis" AND "biomarker") AND (PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")' \
>     --max-records 12
> ```
>
> 实测返回 `expected_total: 2418`、`retrieved_total: 5`、`stopped_at_limit: true` ——
> **计数对账是内建的**，会明确告诉你结果是"被上限截断的部分结果"而不是"全部"。
> ⚠️ 报告时**必须如实说明是部分结果**，不要把 12 篇说成"该领域全部综述"。
>
> **C 线（arXiv）** —— ⚠️ **有限流，不能真的"同时"发**：
>
> ```bash
> # 取回后交给随包脚本解析（会处理 Error feed、版本号后缀等坑）
> curl -s "https://export.arxiv.org/api/query?search_query=all:%22Alzheimer%22+AND+all:%22review%22&max_results=12" \
>   | python <工作文件夹>/skills/paper-lookup/scripts/arxiv_atom.py -
> ```
>
> ⛔ **arXiv 限流：每 3 秒只允许 1 个请求**（实测连续请求会返回
> `HTTP 429` 或纯文本 `Rate exceeded.`）。因此"三线并行"指的是
> **B 线与 A 线并行，C 线串行且要单独降速**；不要把 arXiv 与其他
> 高频请求混在一起跑。`arxiv_atom.py` 遇到限流会**退出码 5** 并打印
> 明确提示，**不要**用自己写的解析绕过它。
>
> **A 线（Google Scholar）** —— ⚠️ **无官方 API**，需经浏览器访问且有反爬。
> 按 §5.1 分层策略：**只取题录与排序信息**（属"综述题录"，允许第一层直连），
> **不要**尝试抓取 PDF 全文。若被拦，**如实报告该线未取到**，
> 用 B/C 两线结果继续，不要伪装成三线齐全。

**复用哪些 skill**（均在 `scientific-agent-skills/skills/` 下，**按需加载**）：

| Skill | 在本阶段的作用 |
|---|---|
| `paper-lookup` | **主力**：跨 11 个学术 API（PubMed / arXiv / OpenAlex / Crossref / Semantic Scholar / Europe PMC / CORE / Unpaywall 等）检索，返回带可复现溯源的结果。**零凭据可用**；配 `NCBI_API_KEY` / `S2_API_KEY` 等可提高限流额度 |
| `literature-review` | **系统综述方法论**：多库检索、筛选、综合的规范流程 |
| `research-lookup` | 需要**当前证据汇编**或更深入的检索时使用 |
| `citation-management` | 综述选定后校验 DOI/PMID、去重、生成规范引用 |
| `scientific-critical-thinking` | 对综述质量做证据分级（GRADE 等），避免拿低质量综述当权威 |
| `bgpt-paper-search` | 需要**从全文抽取结构化实验数据**（方法/样本量/结论）时使用 |

> 💡 **不要重复造轮子**：检索式构造、多库降级、去重、计数对账、引用格式化
> 上述 skill 已经覆盖（`paginate.py` 已内建"计数对账 + 限流 + 停止原因"）。
> 本阶段的工作是**编排**它们 + 把结果整理成本科生能读的形态。
>
> ⚠️ **这十一个 API 会「用 HTTP 200 返回错误」** —— 例如 arXiv 对畸形参数
> 返回一个标题为 `Error` 的条目、Europe PMC 把错误码放在 200 响应体里。
> 所以**必须校验返回内容的结构**，而不是只看状态码；
> 随包脚本就是为挡这些坑而存在的，**不要绕过它们自己去解析**。

**筛选标准（综述优先）**：

1. **类型优先**：Review / Systematic Review / Meta-analysis / Survey > 原始研究；
2. **影响力**：被引次数高、发表在 CNS 及高水平刊物、领域内公认；
3. **时效性**：近 3–5 年优先（经典奠基性综述可放宽）；
4. **开放获取优先**：本科生常无法访问付费文献，优先选有 OA 全文的。

**产出** `.work_cdp/reviews/reviews.json`（结构同阶段 H 的任务输入格式）：

| 字段 | 说明 |
|---|---|
| `title` / `authors` / `journal` / `year` / `doi` | 题录 |
| `type` | `review` / `systematic-review` / `meta-analysis` / `survey` / `research` |
| `citations` | 被引次数（注明来源与日期） |
| `oa_url` | 开放获取链接（有则填） |
| `why_selected` | 一句话说明为什么选它 |
| `platform` | 从哪个平台/接口找到 |

**数量**：**6–12 篇**为宜。少于 6 篇覆盖不全，多于 12 篇本科生读不完。

> 💡 **省事做法**：不必手工拼 `reviews.json` —— 把 `paginate.py` 的原始输出
> 直接喂给阶段 R① 的 `reviews_prepare.py --from-europepmc`，
> 它会自动归一字段、判定综述类型、排序（综述优先 + 被引降序）。

---

### 阶段 R①（脚本）：综述任务包组装

```bash
# 把阶段 R 的检索结果组装成并行 subagent 的任务包
# 产物：.work_cdp/reviews/{tasks,results,manifest.json}
uv run python 流程1/scripts/reviews_prepare.py \
    --from-europepmc .work_cdp/reviews/europepmc.json \
    --fetch-abstracts
```

- 任务包 schema 与 `interpret_prepare.py` **逐字段同名**（`seq` / `doi` / `title` /
  `authors` / `abstract` / `evidence_source` / `result_path` …），
  所以 §7.2 的提示词模板可**原样套用**，subagent 不需要知道自己在跑哪条链路。
- 缺摘要时证据层级自动标 `metadata-only`，**不假装读到全文**。
- `--status` 之外无副作用；重复跑会覆盖同名任务包，不会累积垃圾。

---

### 阶段 R2：领域概貌解读（**复用阶段 H 的并行 subagent 机制**）

> **目的**：把阶段 R 选出的综述**读成一份本科生能看懂的领域概貌**，
> 而不是丢一堆 PDF 链接给用户。

**执行方式**：**复用 `subagent-driven-review/SKILL.md` 的同一套并行机制**
（其 §7.2 提示词模板、解读卡 schema、质量控制全部适用）。

> ★ **v13 档位**：本阶段用 **`review_interpret_tier`，默认 `L3`（完整）**。
> 理由：领域概貌是**独立成篇**的交付物，本科生要据此判断"这个方向在解决什么问题、
> 哪些还有争议"，低档会丢掉排除性证据与作者语气限定。
> 查询 `prepare_stage.py --tier R2`（且**阶段 0 的档位确认同时覆盖 R2**）。
> ⛔ L3 在这里 = 论证链完整 **加上** 术语都要解释，不是"可以说得更肯定"。

```bash
# ① 组装任务包（见阶段 R①）
uv run python 流程1/scripts/reviews_prepare.py \
    --from-europepmc .work_cdp/reviews/europepmc.json --fetch-abstracts

# ② ★ 在同一条消息内并行分派 subagent，每篇一个
#    提示词模板：子流程 §7.2（领域概貌版）
#    ⛔ 并发度：每批 3 篇（见下方实测说明）

# ③ 查看进度（有几张卡、缺哪几篇、哪些被拒收）
uv run python 流程1/scripts/reviews_build.py --status

# ④ 汇总成《领域概貌.md》
uv run python 流程1/scripts/reviews_build.py --topic "阿尔茨海默病早期诊断"
```

> ### ⛔ 实测：并发度必须限制在每批 3 篇左右
>
> 2026-10-02 实机验证（12 篇综述）：
>
> | 并发度 | 结果 |
> |---|---|
> | **12 篇同时** | 仅 3 篇成功；失败的产出**结构合法但内容是 `placeholder`** |
> | **3 篇一批**（×4 批） | **12/12 成功**，无占位符 |
>
> 高并发下 subagent 会触发生成长度上限，用 `"placeholder"` / 空串 / 截断
> （实测出现过 `"conclusion": "placeholder"`、`"open_questions": ["ERFERENCE_PLACEHOLDER"]`）
> 填充字段。**schema 校验只管字段类型，管不了内容是不是真话。**
>
> 因此 `reviews_build.py` 增加了**内容级校验**：检出占位符的卡会被
> **拒收并单独列出**（不会进入《领域概貌》），并提示"降低并发度重跑"。
> 用 `--allow-placeholder` 可放宽（不建议）。
>
> **推论**：任务包 6–12 篇 → 分 2–4 批跑，不要一次性全发。

> ### 📌 卡片的两种落盘方式（二选一，别混）
>
> | 方式 | 做法 | 适用 |
> |---|---|---|
> | **调用方落盘** | `agent(prompt, {schema})` 返回值回到调用方，由调用方写 `results/<slug>.json` | 需要 schema 强约束字段 |
> | **subagent 自写** | 提示词里给出任务包的 `result_path`，让它直接写文件 | 字段较多、避免大 JSON 回传 |
>
> 实测两种都可行。**关键是与下游一致** —— `reviews_build.py` 只读
> `results/*.json`，所以无论哪种方式，卡片最终都必须落到那里。
> （阶段 H 用的是第二种；本阶段两种都验证过。）

**解读卡侧重点（与阶段 H 的差异）**：

| 维度 | 阶段 H（作者论文） | **阶段 R2（本科生概貌）** |
|---|---|---|
| 读者 | 研究者 | **没有科研经验的本科生** |
| 重点 | 方法学细节、创新点 | **领域全貌**：在解决什么问题、有哪几派、结论是否一致 |
| 术语 | 保留专业术语 | **解释术语**，必要时给中文对照 |
| 产出 | 深度解读报告 | **领域概貌**：入门读物 + 关键概念 + 争议点 + 下一步读什么 |

**产出** `.work_cdp/reviews/领域概貌.md`，建议结构：

```markdown
# <方向> 领域概貌（面向本科生）

## 一句话总结
这个领域在研究什么、为什么重要。

## 这个领域在解决什么问题
（3–5 条，每条一两句）

## 主要研究方向与流派
| 方向 | 核心思路 | 代表综述 | 主要结论 |
（帮用户理解"这个领域分几块"）

## 目前的一致结论 vs 仍有争议的地方
（哪些是共识，哪些还在吵 —— 本科生选题最该看这里）

## 关键概念速查
| 术语 | 英文 | 解释 |
（把综述里反复出现的术语讲清楚）

## 推荐阅读顺序
1. 先读 <入门综述>（为什么）
2. 再读 <某方向综述>（为什么）
（给出**阅读顺序**而不只是列表）

## 可能的选题切入点
（3–5 个适合本科生/大创的、范围可控的问题）
```

**可选：接上原有链路**。若用户已选定导师并希望深挖其成果，
回到**阶段 0** 走 `A → I`；此时阶段 N 的 `topic_plan.json` 与
阶段 R 的综述清单可作为阶段 H2 的**先验输入**。

---

### 阶段 0：准备阶段（**原有链路的起点**）

> **目的**：把「只有用户能定」的选择**一次性前置**，避免跑到一半才发现
> 用户只要一份 Markdown 列表、或反过来漏掉了本该做的综述网络。
> 这些答案落盘为**单一事实源** `.work_cdp/prepare_plan.json`，
> 后续每个阶段开头只问它一句。

**执行方式**：agent 在对话中逐一确认下列问题，收齐后调用脚本落盘。
**不要**让用户面对交互式命令行 —— 脚本是记录器，不是提问器。

| # | 问题 | 取值 | 默认 |
|---|---|---|---|
| 1 | **输出文件格式选择**：PDF、Markdown，还是两者都要？ | `markdown` / `pdf` / `both` | `markdown` |
| 2 | **是否进行翻译环节**（全文中文翻译 → PDF）？ | 是 / 否 | 否 |
| 3 | **是否反向查找相关参考文献**，形成该研究方向整体综述网络？ | 是 / 否 | **是** |
| 4 | **是否绘制知识图谱**（overview / 词云 / 方向聚类）？ | 是 / 否 | **是** |
| 5 | 方向聚类看最近几年？ | 2 / 3 / 5 | 3 |
| 6 | **是否制作相关 PPT**（文献亮点解读 + 综述结果 + 图谱）？ | 是 / 否 | 否 |
| 7 | **是否执行本科科研辅助前置链路**（阶段 N / S / R / R2）？ | 是 / 否 | **是** |
| 8 | **★ 论文解读档位**（阶段 H，作者本人的论文） | `L1` / `L2` / `L3` | **`L2`** |
| 9 | **★ 综述解读档位**（阶段 H2 研究方向综述 / 阶段 R2 领域概貌） | `L1` / `L2` / `L3` | **`L3`** |
| 10 | 是否输出「有代表性文献」的参考文献列表（GB/T 7714）？ | 是 / 否 | 是 |
| 11 | 是否对反向查得的文献做去重与相关性整理，并据此产出 review？ | 是 / 否 | 是 |
| 12 | PPT 目标页数（简化 Strategist 用，无需再交互） | `short`/`standard`/`long` | `standard` |

> ### ★ v13：为什么档位也要在阶段 0 问
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
> - ⛔ **三档只差细节量，不差断言强度**：同一个发现在三档的动词强度一致，
>   且与原文同级。这一点在阶段 H 的 L1 上最容易被违反（"表格要好看"）。
> - 规范定义、句式库与离线校验器见 skill **`paper-interpretation-writing`**。
> - 两个档位是**参数不是开关**（不控制任何阶段是否执行），
>   所以用 `--tier` 查询、用 `--set` 修改。

```bash
# 收齐答案后一条命令落盘（非交互）
uv run python 流程1/scripts/prepare_stage.py \
    --set output_format=both --set translate=yes --set reverse_refs=yes \
    --set make_ppt=yes --set refs_bibliography=yes --set curate_review=yes \
    --set ppt_pages=standard \
    --set interpret_tier=L2 --set review_interpret_tier=L3

# 全默认（保守：只出 Markdown，不翻译、不做 PPT，但做综述网络）
uv run python 流程1/scripts/prepare_stage.py --defaults

# 查看当前计划 / 查看全部闸门 + 两处档位
uv run python 流程1/scripts/prepare_stage.py --show
uv run python 流程1/scripts/prepare_stage.py --check-all

# 单阶段闸门：退出码 0 = 执行；3 = 已按准备计划跳过（附原因）
uv run python 流程1/scripts/prepare_stage.py --check translate

# ★ v13 档位查询：stdout **只有** L1|L2|L3，便于 T=$(… --tier H) 捕获
uv run python 流程1/scripts/prepare_stage.py --tier H     # 作者论文（默认 L2）
uv run python 流程1/scripts/prepare_stage.py --tier H2    # 研究方向综述（默认 L3）
uv run python 流程1/scripts/prepare_stage.py --tier R2    # 领域概貌（默认 L3）

# ★ v13 门控齐全性自检（每个阶段都有标签、每个闸门都指向真实阶段）
uv run python 流程1/scripts/prepare_stage.py --audit
```

> ### ✅ v13.1：已修掉「`output_format` 关掉阶段 F」这处门控矛盾
>
> **矛盾**：`output_format=markdown` 的 `gate_rule` 关闭阶段 F，但该选项自己的 note、
> `translate` 的 note、`_f_mode="html-only"`、`_translate_output="markdown"`、
> `--show` 的提示语 —— **五处**都假定它**不关** F（该由 `translate` 判定）。
> 后果：`translate=yes` + `output_format=markdown` 时阶段 F 被**静默跳过**，
> **用户要了翻译却什么都拿不到，也不报错**。
>
> **修法（用户拍板）**：`output_format` 不再是闸门（`gates`/`gate_rule` 置空），
> 变成**纯参数**——阶段 F 由 `translate` 单独判定，本项只在 F 执行时决定
> 「要不要顺带渲染 PDF」（经 `_f_mode` 表达）。
>
> **影响面经实测限定为一格**：只有 `translate=yes + markdown` 从 skip 变 run
> （`_f_mode=html-only`，产出中译 Markdown 不渲染 PDF）；其余五种组合
> （含默认的 `markdown + translate=no`）行为完全不变。
>
> `selftest_pipeline.py` 已加**行为级回归守卫**（该组合 `--check F` 必须退出 0、
> `_f_mode` 必须为 `html-only`），有人把闸门加回 `output_format` 会立刻失败。
> 详见 §9.16。

**闸门解析规则（`prepare_stage.py` 自动完成，无需人工推导）**

| 选项 | 关掉它 → 这些阶段跳过 |
|---|---|
| `undergrad_prelude=no` | 阶段 **N / S / R / R2** 跳过（不影响原有链路 A→I；两条链路相互独立） |
| `translate=no` | 阶段 F 整体跳过（**这是唯一决定 F 是否执行的选项**） |
| `output_format` | **不控制任何阶段** —— 纯参数：阶段 F 执行时决定要不要顺带渲染 PDF（`markdown` → 只出中译 Markdown；`pdf`/`both` → 另渲染 PDF） |
| `reverse_refs=no` | 阶段 **D2 / G2 / H2** 全部跳过（下游依赖上游，自动收敛） |
| `refs_bibliography=no` | 阶段 G2 跳过（但不影响 D2 / H2） |
| `curate_review=no` | 阶段 H2 跳过 |
| `make_graphs=no` | 阶段 K 跳过（PPT 仍可做，但不会带图谱） |
| `make_ppt=no` | 阶段 P 跳过 |
| `interpret_tier` / `review_interpret_tier` | **不控制任何阶段是否执行** —— 参数，只决定解读写多深 |

> **依赖收敛要点**：`reverse_refs=no` 时，即使 `refs_bibliography=yes`、
> `curate_review=yes`，G2 与 H2 也会被**自动关掉**并在计划里写明
> 「← 由 reverse_refs（上游阶段 D2 未执行）关闭」。没有参考文献可取时，
> 这两个阶段无从执行 —— 这是**收敛**，不是静默跳过。

> **`output_format` 只影响产物格式，不影响是否下载原文 PDF**：
> 原文 PDF 是阶段 H 解读的输入，**一律要下**。

**产出**：`.work_cdp/prepare_plan.json`（含 `_gates` 与 `_gate_reasons`）。

---

### 阶段 A：作者定位与工作目录

1. `web_fetch` 作者主页 → 确认姓名、单位、研究方向、论文成果页 URL。
2. `web_search` 找 ORCID；用 ORCID API 交叉验证（看是否含已知代表作）。
3. 建立目录：`<工作目录>/<作者名>/`、`<作者名>_paper_Exports/`（全文）、`.work_cdp/`（中间数据与日志）。
   > 历史工作目录若已有 `<作者名>_Exports/` 且含 PDF，`store.exports_dir()` 会自动沿用，
   > 无需手工迁移（见 §7 目录名兼容说明）。

> **v3 分支：ORCID 成果列表为空时**（薛天案例：ORCID 记录存在但 `works` 为 0 条——
> 作者未在 ORCID 侧同步）。此时改用**作者主页 + OpenAlex**作为基准集合：
> · 主页给出**作者自述的完整成果清单**（权威性最高，含署名标记 `*`/`†`/`#`）；
> · OpenAlex 用 **ORCID 关联反查作者 ID**（`https://api.openalex.org/works/doi:<任一代表作>`
>   → 取 `authorships` 中的 ORCID 匹配项 → 得 `author.id`），再按 `author.id` 拉全量；
> · 三条线（主页 / ORCID / OpenAlex）一致即可确认身份，**同名风险用「作者列表含本人姓名 + 单位」双重过滤**；
> · 最终列表以**主页清单**为准（作者自己认可的成果集），OpenAlex 仅用于补 DOI 与摘要。

### 阶段 B：启动 CDP 浏览器（★ 多实例并行）

> **为什么必须多实例（实测教训）**：2026-09-10 的单实例运行中，Edge 进程中途退出，
> 脚本没有任何探活机制，后续 8 篇全部 `WinError 10061 目标计算机积极拒绝`，
> 整批任务报废（见 `.work_cdp/batch2.log`）。一实例一 profile 还天然形成
> **Cookie 池**：某个实例被 Cloudflare 挑战时，其余实例照常工作。

**启动参数档位**（并列，不叠加；默认 **L0 日常档**，v3 推荐 **L3 极速档**）：

| 档位 | 参数 | 说明 |
|---|---|---|
| **L0 日常档（默认）** | 必需 3 个：`--remote-debugging-port` `--remote-allow-origins=*` `--user-data-dir`<br>正常窗口：`--window-size=1280,900`<br>跳过开箱体验：`--no-first-run` `--no-default-browser-check`<br>屏蔽权限/通知弹窗：`--deny-permission-prompts` `--disable-notifications` | **「像正常打开的浏览器」**，参数仍然克制 |
| L1 极简档 | 仅 3 个必需参数 | A/B 对照：判断某站点的异常是否由参数本身引起 |
| L2 扩展档 | L0 + `--lang=zh-CN` | 需要中文界面时 |
| **L3 极速档（v3 推荐）** | L0 + `--disable-background-networking` `--disable-component-update` `--disable-sync` `--disable-default-apps` `--no-service-autorun` `--disable-client-side-phishing-detection` `--disable-domain-reliability` | **只关后台服务，不改页面指纹**；冷启动更快、后台占用更低。实测 3 实例并行冷启动 **15s** |

> **窗口尺寸**：用 `--window-size=1280,900` 开成**正常窗口**，
> **不要**用 `--start-minimized`（最小化窗口的渲染节流会拖慢页面加载，
> 也更容易被判定为异常环境）。实测屏幕较小时 Edge 会自行收缩到可用范围
> （1229×692 屏幕 → 实际 1244×706），属正常行为。

> **权限 / 通知弹窗**：由 `--deny-permission-prompts`（自动拒绝地理位置、
> 摄像头、通知等权限请求）+ `--disable-notifications`（屏蔽网页通知）解决。

> **Cookie / 同意书弹窗**：Chromium/Edge **没有**对应的命令行开关
> （Edge 的「阻止 cookie 横幅」是浏览器设置项，不是启动参数）。
> 由 `cdp.py` 的 `CONSENT_JS` 在页内**保守**关闭，`connect()` 时自动注册；
> **v3 已扩充**：
> · 容器从 20 → **40+**（OneTrust / Cookiebot / Quantcast / Osano / Didomi / TrustArc /
>   Usercentrics / CookieYes / Complianz / Iubenda / Klaro / Termly / SourcePoint / Evidon 等），
>   **找不到已知容器就什么都不做**；
> · 优先点容器内的「接受」按钮；**同源 iframe** 深入 `contentDocument` 再找按钮，
>   跨域 iframe 只隐藏容器（不越权操作）；
> · DOMContentLoaded + `load` + 12×1.2s 定时 + **滚动/点击后补扫**（应对延迟弹窗）；
> · 新增 `CDP.dismiss_consent()`：页面加载完成后再补一刀，供下载流程每篇调用；
> · **不改动任何 navigator/screen 属性** —— 是可用性处理，不是指纹伪装。

> ⚠️ **不要**加 `--headless`（无头最易被识别）、**不要**加
> `--disable-blink-features=AutomationControlled` / `--excludeSwitches`
> —— 这些非默认参数**本身就是可被检测的指纹**；
> **不要**做 JS 指纹注入（新版 `cdp.py` 默认 `stealth=False`）。

```bash
# 推荐入口（3 实例，L3 极速档；并行启动，总耗时 ≈ 最慢的 1 个）
bash scripts/start_edge_cdp.sh "$PAPER_WORKDIR" 9222 3 3

# 保守起见用 L0：
bash scripts/start_edge_cdp.sh "$PAPER_WORKDIR" 9222 3 0

# 需要 A/B 对比参数档位时（0=日常 1=极简 2=扩展 3=极速）
uv run --with websocket-client python scripts/cdp_pool.py up --n=4 --levels=0,3

# 探活 / 关闭 / 单实例换新 profile
uv run --with websocket-client python scripts/cdp_pool.py status
uv run --with websocket-client python scripts/cdp_pool.py down
bash scripts/renew_cdp_instance.sh "$PAPER_WORKDIR" 9223     # 只重建 9223，其余不受影响
```

> **v3 提速要点**：① 并行启动（先全部拉起、再统一探活，0.25s 轮询）；
> ② L3 关后台任务；③ `navigate()` 自适应 settle（正文就绪即返回）。

> **端口 ↔ profile 约定**：`9222 → .edge-cdp-profile`（沿用历史 profile，保留已获得的
> 放行 Cookie）；其余端口 → `.edge-cdp-p<port>`（独立 profile）。
> **一实例一 profile 是硬约束**：Edge 同一 profile 无法起第二个调试端口。

### 阶段 C：抓取 ORCID 全部成果

- 用 `scripts/cdp.py`（CDP 封装）+ `scripts/orcid_extract.py`（翻页抓取）。
- 页面结构：`Works (50 of 79)` / `Page 1 of 2`，需点击 Next 翻页，累积 `innerText`。
- 解析字段：**标题 / 期刊 / 日期 / DOI / CONTRIBUTORS**。

### 阶段 D：Crossref 逐条核实 + 同名消歧

- 并发查询（`ThreadPoolExecutor(max_workers=10)`）；失败的**串行 + 退避重试**（3 次，间隔 3s/6s/9s）。
- 抓取字段：`title, container-title, issued, volume, issue, page, article-number, author`。
- 清洗标题：去 HTML 标签、合并空白、去掉误并入的期刊名与图标文本。
- **回填结构化作者**：`enrich_crossref_authors.py`（默认读 `crossref_all.json`，
  写回 `authors` 字典列表 + `vol/issue/page`；`--surname/--given` 缺省回退
  `PAPER_SURNAME`/`PAPER_GIVEN`）。

> ⛔ **同名消歧（不可省）** —— 作者聚合普遍**过度合并**（OpenAlex 单一 author id
> 会混入同名他人的无线通信、有机化学、微电网等论文）。跑 `disambiguate.py`，
> 判据由 `PAPER_SURNAME` / `PAPER_GIVEN` / `PAPER_AFFIL_TOKENS` / `PAPER_ANCHOR_DOI` 提供。
>
> **四重判据（按优先级，v6.1 重做）**：
>
> | 判据 | 码 | 性质 | 说明 |
> |---|---|---|---|
> | **⓪ ORCID 溯源** | `orcid-linked` | **肯定** | DOI 出现在**本人 ORCID 记录**里 → 直接确认。**ORCID→Crossref 主路径上最关键的一条**（作者本人认领，强于任何单位指纹） |
> | ① 姓名精确 | `name-mismatch` | 肯定（可排除） | 记录**有**作者名单，但无「名精确匹配」者 |
> | ② 单位指纹 | `affil-hit` / `affil-mismatch` | 肯定 | 命中 → 确认；不命中 → 可排除 |
> | — 数据缺失 | `no-author-data` / `no-affil-data` | **非否定证据** | **一律保留**，只标注待核 |
>
> ⚠️ **为什么必须有判据⓪**：Crossref **只有少数出版社存缴逐作者单位**
> （实测郭傲案例 13 篇里仅 **2 篇**有）。若只靠单位指纹，主路径上必然
> 全部落到「数据缺失」，消歧等于空转 —— 这正是 v6 首次实跑的情形。

> ★ **v6：消歧结果已接入下游**。此前 `disambiguated.json` **没有任何脚本读取**，
> 消歧跑完只打印给人看 —— 同名他人论文照样被下载、照样进最终列表，
> 等于这条质量要求在流程上落空。现由 `scripts/scope.py` 统一读取，
> **下载侧（`download_all_fulltexts.py`）与著录侧（`build_list.py`）共用同一判据**。
>
> **只排除「肯定性判定」**（`name-mismatch` / `affil-mismatch`）；
> 「署名块无单位字段」这类**数据缺失**（`no-affil-data`）**一律保留**，
> 并在报告中显式列出待人工核 —— 漏收真论文的代价远大于多留一篇。
> 逃生阀：`PAPER_SCOPE_INCLUDE_ALL=1` 可完全关闭排除，便于对照全量。

### 阶段 D2：反向查找参考文献（综述网络 + 高质量文献列表）

> **闸门**：`uv run python 流程1/scripts/prepare_stage.py --check D2`
> （退出码 3 = 准备阶段已关闭 `reverse_refs`，本阶段跳过）

> **为什么放在这里**：阶段 D 结束时作者的成果集**已确认无误**（含同名消歧），
> 正好拿它去反向扩展 —— 取作者每篇论文的**参考文献**，倒推出这位作者
> **站在谁的肩上**，从而把「作者论文分析」升级为「研究方向全景」。

> **为什么走 OpenAlex 而不是抓页面**：`referenced_works` 属于**元数据 API**，
> 正是 §5.1 分层策略里的**第一层**（结构化元数据 + 摘要），**允许直连**。
> 全程无浏览器、无风控、**不额外下载任何 PDF**。

```bash
uv run python 流程1/scripts/refs_backfill.py
#   可选：--min-citations=200   高质量门槛（默认 200）
#   可选：--limit-sources=4     只用前 N 篇作者论文（调试）
#   可选：--top=40              报告里最多列多少条（0=全部）
#   可选：--offline             只用 cache/ 里的响应（断网/复现）
```

**「高质量」的定义（**v7.2 改版**，用户指定）**

> **用户修订原文**：「获取他人综述全文或详细摘要进行分析，增加置信度。
> 同时新增修改限制，**引用次数 200 次不作为硬性要求**，
> 重点关注**近几年发表于 "CNS" 及相关高水平刊物的综述文章**。」

| 项 | 实现 |
|---|---|
| **入选门槛** | 满足任一即入池：**是综述** ／ **核心期刊**（CNS 家族等）／ 被引 > `--min-citations`（**默认 0 = 不设门槛**）／ 被作者 ≥3 篇论文引用（网络核心） |
| **排序** | 五项加权求和（**权重与分量逐条写进报告**）：综述 `40` + 核心期刊 `25` + 近年（近 5 年，每远一年减 4）`20` + 被引（**对数尺度**）`15` + 共引 `10` |
| ⛔ **被引不再是门槛** | 旧版 200 次门槛会把**近两年的高质量综述整批挡在门外** —— 而那正是本版要重点看的。现在被引只占 15 分且用对数，1000 次与 100 次只差几分，不主导排序 |
| ⛔ **「近期」不能单独构成入选理由** | 若让「近 5 年」单独放行，等于把近五年的参考文献全收进来（实测 235 条几乎全进），「高质量」就失去意义。**近期只影响排序，不影响入选** |
| 核心期刊判定 | `load_core_journals()` 的**显式清单**（**不用前缀匹配** —— `science`/`cell` 前缀会误命中 Science of the Total Environment、Cellulose、Cells 等非核心刊） |

**为综述取全文 / 详细摘要（提高置信度）**

| 证据层级 | 怎么拿到 | 下游用法 |
|---|---|---|
| `fulltext` | **开放获取**综述 → Europe PMC `fullTextXML`（**结构化开放获取接口**，非抓出版社网页；非 OA 一律返回空） | 任务包直接带全文，subagent 读全文，结论置信度最高 |
| `detailed-abstract` | 摘要 ≥800 字符（通常含 Objective/Method/Result） | 按详细摘要解读 |
| `abstract` | 常规摘要 | 按摘要解读 |
| `metadata-only` | 连摘要都没有 | 只能依据题录，报告**如实标注** |

> 实测（示例题录 6 篇作者论文的参考文献）：37 篇综述中
> **全文 2 篇、详细摘要 25 篇、常规摘要 6 篇、仅有题录 4 篇**。
> `--no-review-fulltext` 可跳过这一步。

> ⚠️ **「综述」是启发式判定**：OpenAlex 没有 `review` 类型，判据 = `type` +
> 刊名含 `Reviews` + 题名以 `Review/Survey/Overview/Perspective/Advances in…` 开头。
> 可能漏判或误判，报告里**逐条标出判定理由**供复核。
> **不要**把它当作权威的文献类型标注。

**产出**：

| 文件 | 说明 |
|---|---|
| `<作者名>_高质量文献列表.md` | ⭐ 交付物：统计 / 综述优先清单 / GB/T 7714 著录 / **反向索引**（哪几篇作者论文引用了它） |
| `.work_cdp/refs/source_refs.json` | 作者论文 → 其参考文献（含 `source_id`，供阶段 G2 查 IF 复用） |
| `.work_cdp/refs/refs_pool.json` | 参考文献的去重题录池（含摘要倒排索引重建的 `abstract`） |
| `.work_cdp/refs/high_quality.json` | ⭐ 下游 G2 / H2 的**唯一输入** |
| `.work_cdp/refs/cache/` | HTTP 响应缓存（可断点续跑 / 离线复现） |

> **诚实性**：OpenAlex 的参考文献**存缴不完整**，作者论文中有一部分会
> 「无参考文献记录」——脚本会在统计里**显式报出这个数字**。
> **缺记录不等于该文没有参考文献**，不得据此推断任何结论。

---

### 阶段 E：全文获取（**统一走 CDP**）

```
统一路径  出版社/CDN/PMC 一律 CDP   → download_all_fulltexts.py   多实例 + 站点熔断
取不到    摘要级降级               → 用摘要 + 关键词解读，并如实注明限制
```

> ⛔ **v4 关键变更**：**取消「OA 直链纯 HTTP」这一级**。
> 依 § 5.1 分层策略，**原文全文属第二层** —— **无论是否 OA，一律经 CDP 浏览器获取**。
> 理由是直连请求同样被站点计数、同样触发风控，且拿不到内容时无法纳入流程的
> 判定/日志体系。原 `fetch_oa_direct.py` 已停用。

**唯一路径：CDP 多浏览器 × 多标签页（OA 与付费墙同等对待）**

```bash
uv run --with websocket-client --with pypdf python scripts/download_all_fulltexts.py \
  --n=3 --tabs=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35
```

> ### ★ v8 改造：并发模型由「多浏览器 × 单标签页」改为「多浏览器 × 多标签页」
>
> **为什么改**：旧设计并发度 = 浏览器进程数，想提速只能多开 Edge，
> 而每个进程约占 200–400 MB（开 6 个就吃掉 2 GB），启动与崩溃恢复都慢。
> 实测瓶颈在**网络等待**（导航 + 出版社响应），CPU/内存远没到顶，
> 所以改为每浏览器开 T 个标签页：并发通道 = `--n × --tabs`，
> 标签页共享进程池与网络栈，增开成本远低于新开浏览器。
>
> **⛔ 并发上限 = min(通道数, 不同站点数)** —— 这是实测确认的硬约束：
> 同站点必须串行（反封禁红线），所以**同一出版社的多篇论文，
> 加标签页不会提速**。多标签页只在语料**站点分散**时才带来吞吐收益。
> 实测并发通道：`--n=2 --tabs=3` → 峰值 6；`--n=2 --tabs=6` → 峰值 11。
>
> **两条不变量**（改造已保持，勿破坏）：
> ① 同站点严格串行 —— 站点锁包住「节流 + 处理 + 失败记账」全程；
> ② 一个标签页只归一个线程 —— `open_tab()` 每次新开、`close_tab_self()` 用完即关。
>
> **回退开关**：`--no-multi-tab` 恢复旧行为（一浏览器一标签页），
> 用于排查多标签页相关问题。

> ### 📄 v8 新增：下载的同时产出正文，供解读并行
>
> `detect.py` 的页内探针**本来就已经取回了完整正文**
> （`document.body.innerText`），旧版却只保留长度（`textlen`）就丢弃。
> 现在把它**顺带**落盘到 `.work_cdp/fulltext/<slug>.txt`：
>
> - **零额外请求** —— 正文此刻已在浏览器内存里；
> - **先于 PDF 尝试落盘** —— 即使 PDF 最终失败，解读素材也已拿到
>   （付费墙/降级场景尤其重要）；
> - 挑战页 / 封禁页的正文**会被清空**，避免把验证页文字当论文正文；
> - 落盘位置在 `.work_cdp/` 而非交付目录 —— 它是中间素材，不是交付物。
>
> **好处**：阶段 H 解读原本要等 PDF 下载完再从 PDF 提文本；
> 现在**下载与解读可以并行**：一批下完即可先解读，
> 无需等整轮下载结束。`--no-fulltext` 关闭该行为，`--fulltext-cap=N` 调上限。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--n` | 3 | 并行**浏览器**数 |
| `--tabs` | 3 | **每浏览器标签页数**（v8）。并发通道 = `--n × --tabs` |
| `--no-multi-tab` | — | 回退到旧行为（一浏览器一标签页，`--tabs` 强制为 1） |
| `--fulltext-cap` | 400000 | 顺带取回正文的字符上限（v8）。0 = 不取 |
| `--no-fulltext` | — | 关闭正文落盘（等价 `--fulltext-cap=0`） |
| `--limit` | 12 | 本轮篇数 |
| `--delay` | 25-55 | **同一站点**两篇之间的最小间隔（秒），全局串行 |
| `--site-fail-limit` | 2 | 同一站点连续受阻几次后，本轮**跳过该站点** |
| `--manual-wait` | **0（关闭）** | ⚠️ **本流程一律显式给正值**（如 `--manual-wait=5`，单位分钟）。为 0 时弹窗链完全不触发（§ 9.2 故障 #1）。弹窗为 TTL 自动关闭 + 窗口置前，无人值守也不会残留 |
| `--grace` | 25 | 挑战宽限期（秒）：Cloudflare JS 挑战常在此窗口内**自动放行**，避免误报需人工 |
| `--doi=` | — | 只处理指定 DOI（逗号分隔），便于精确补抓 |
| `--status` | — | 查看累计进度 |

> **v3 补充**：判定结果里的 `navStatus`（真实 HTTP 状态）与 `botVendor`
> （cloudflare-turnstile / datadome / perimeterx / imperva …）会写入日志与 `fulltext_state.json`，
> 便于事后判断「是挑战、是限流、还是真封禁」。

**抓取节奏铁律（防触发风控）**

1. **全文一律走 CDP**（§ 5.1）——**不要**因为"这篇是 OA"就改用 HTTP 直取。
2. **同站点串行 + 25–55s 随机间隔**，模拟人类阅读节奏。
3. **同一站点连续 2 次出现验证/拒绝 → 本轮熔断该站点**，绝不反复试探。
4. 宁可少抓几篇，也不要触发 IP 封禁导致后续全部失败。

**出版社取全文对照表（2026-09 实测；「直连」一列已按 v4 禁令废止）**

| 出版社 | CDP + Edge | 取全文的可行方法（**全部经浏览器**） |
|---|---|---|
| Nature (nature.com) | ✅ | 落地页 fetch `citation_pdf_url` |
| Springer (link.springer.com) | ✅ | 落地页 fetch PDF |
| Wiley (onlinelibrary.*) | ✅ | ⚠️ PDF 端点常回 HTML → **正文页 `Page.printToPDF` 渲染兜底**（有 `textlen > 15000` 门槛，短文体裁会因此取不到，属预期行为） |
| ACS (pubs.acs.org) | ⚠️ 多数可 | 落地页 fetch PDF（偶发验证页） |
| Elsevier (sciencedirect.com) | ✅ | 落地页 fetch；`/pdfft` 重定向签名直链后页内 fetch |
| RSC (pubs.rsc.org) | ✅ | 同 Elsevier |
| OUP (academic.oup.com) | ✅ | 落地页 fetch PDF |
| IUCr (journals.iucr.org) | ✅ | 落地页 fetch PDF |
| PNAS / JBC | ✅ | 落地页 fetch PDF；PNAS 有 Cloudflare，需给足 `--grace` |
| PMC / Europe PMC | ✅ | 文章页可开；`/pdf/` 端点常触发 reCAPTCHA（**转人工，勿自动化**）；OA 全文 XML 见 §3.1 |

> **取不到时的正当后路（按优先级）**：
> ① 放宽 `--grace` 让 JS 挑战自动放行；② `--manual-wait=N` 转人工；
> ③ OA 文章用 Europe PMC `fullTextXML` 取**正文文本**（`jats_to_text.py`）；
> ④ 机构 VPN / 图书馆文献传递；⑤ 在《全文清单.md》如实注明限制。
> **任何情况下都不回到"自编脚本直连"。**

**获取全文 PDF 的四种方法**（按可靠性排序，见 `save_pdf_via_browser.py`）：

1. **页面上下文 fetch**（最常用）：先导航到**落地页**（建立同源会话），再
   `fetch(pdfUrl, {credentials:'include'})` → `arrayBuffer` → base64 → 存盘。
   ⚠️ 必须在落地页执行；在 PDF 查看器页执行会因 CORS 失败。
2. **Network 层捕获**：`Network.enable` + 导航到 PDF + `Network.getResponseBody`。
3. **重定向签名直链 fetch**（ScienceDirect 专用）：导航到 PDF 链接 → 浏览器跟随重定向到
   `pdf.sciencedirectassets.com/.../main.pdf?X-Amz-Security-Token=...` → 在**该页上下文** fetch `location.href`。
4. **渲染兜底**：PDF 端点被拦但有全文权限时，用 `Page.printToPDF` 把正文页转 PDF（Wiley 常用）。
   渲染前必须确认正文长度 > 15000，否则会产出 1 页空壳。

> **先查机构授权**：若本机处于校园网/机构 IP 段，出版社页面会显示 **"Subscribed"**，
> 此时**无需登录凭据**即可读全文与下载 PDF。该授权是 **IP 级**的，CDP 启动的独立
> profile 同样享有，**不必复制用户 Cookie/密码库**。

### 阶段 F：翻译与 PDF 导出

1. 用 `to_markdown` 把原文 PDF 转 Markdown（保留章节结构）。
2. **逐段翻译为中文**，生成带 CSS 排版的 HTML（标题层级、摘要框、图注框、参考文献说明）。
   结构直接照抄 `templates/中文翻译_HTML模板.html` 或 `.work_cdp/translations/` 下任一样板。
3. **（v3 推荐）一条命令渲染 + 校验**：

```bash
uv run --with pypdf python 流程1/scripts/render_html_pdf.py "<翻译>.html" "<输出>.pdf"
# 或经包装脚本：bash .work_cdp/render_one.sh "<翻译>.html" "<输出>.pdf"
# 期望输出：OK pages=… size=…KB cjk_first_page=…（退出码 0 成功 / 2 = CJK 过少）
```

   等价的原生命令（需自行校验）：

```bash
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
URL=$(python -c "import pathlib;print(pathlib.Path(r'<绝对路径>.html').as_uri())")
"$CHROME" --headless=new --disable-gpu --no-sandbox \
  --print-to-pdf="<输出>.pdf" --print-to-pdf-no-header "$URL"
```

4. 校验：`render_html_pdf.py` 已内置（页数 + 体积 + 首页 CJK 字符数）；
   批量场景可跑 `python .work_cdp/render_rest.py`（幂等，已存在则跳过）。

### 阶段 G：生成论文列表（GB/T 7714-2015）

著录格式：
```
[序号] 作者1, 作者2, 作者3, et al. 题名[J]. 刊名, 年, 卷(期): 起-止页. DOI: xxx. 原文链接：https://doi.org/xxx
```
- 作者 > 3 位：取前 3 位 + `et al.`（注意**不要**再多补一个句点，写成 `et al..`）
- 无页码时用文章号（article number）
- 标注**本人署名位次**（第一作者 / 末位作者（通常通讯）/ 第 N 位作者）
- 按年份分阶段组织（博士期/博后期/独立建组期等；阶段由
  `.work_cdp/periods.json` 或 `PAPER_PERIODS` 提供，**不内置任何作者的履历**）

**产出两个文件名（内容一致，`build_list.py` 一次写两份）**：

| 文件名 | 说明 |
|---|---|
| `<作者名>_论文列表.md` | ⭐ **规范交付名**（需求为「文件名形如 XX_论文列表」） |
| ~~`<作者名>_论文列表_完整版.md`~~ | **已取消**（v7.2）—— 内容与规范名**逐字节相同**，写两份只会让交付目录出现重复文件 |

> **非本人论文的处置（需求原文：在输出中予以注明并在流程中自动忽略）**：
> 被 `scope.py` 排除的条目**不会**出现在正文列表里，但会在文末
> 「**已排除的同名他人论文**」表中逐条列出 DOI、题名与**排除理由** ——
> 「自动忽略」与「如实注明」两者同时满足，不做静默丢弃。

### 阶段 G2：代表性文献的参考文献列表

> **闸门**：`uv run python 流程1/scripts/prepare_stage.py --check G2`
> （退出码 3 = 准备阶段已关闭 `reverse_refs` 或 `refs_bibliography`）

**「有代表性的文献」= 作者本人的论文中，满足以下任一条件**（用户指定）：

| # | 条件 | 实现 |
|---|---|---|
| ① | 发表于 **Nature / Science / Cell / PNAS** 等核心期刊 | 与阶段 D2 共用 `refs_backfill.load_core_journals()`，**不重复定义** |
| ② | **该作者为第一或通讯作者** | 由 `PAPER_NAME_TOKENS`（或 `PAPER_SURNAME`+`PAPER_GIVEN`）识别署名位次；**末位作者视为通讯作者** |
| ③ | 期刊为**中科院一区** 或 **IF > 5.0** | 分区查内置种子表 / 用户表；IF 用 OpenAlex `summary_stats.2yr_mean_citedness` 作**代理值** |

```bash
uv run python 流程1/scripts/refs_bibliography.py
#   可选：--if-threshold=5.0         IF 阈值
#   可选：--max-refs-per-paper=0     每篇最多列多少条参考文献（0=全部）
#   可选：--offline                  只用阶段 D2 的缓存
```

**产出**：`<作者名>_代表性文献参考文献.md` —— 结构固定为**两部分**：

```
第一部分 · 代表性文献     —— 入选清单（含本人署名角色与入选理由）+ GB/T 7714 著录
第二部分 · 各代表性文献的参考文献 —— 逐篇分组，列出该文引用的参考文献（GB/T 7714）
```

> ⛔ **关于 IF 与分区的两条硬约束（不得含糊）**
>
> 1. **IF 是代理值**：OpenAlex 的 `2yr_mean_citedness` 与 JCR 影响因子
>    **口径接近但数值不同**，报告里必须标注为「IF≈」，**不可**当作 JCR IF 引用。
> 2. **「未判定」≠「不满足」**：分区表用内置**种子表**
>    （`流程1/templates/期刊分区表_中科院一区.json`；官方《中科院分区表》是
>    **商业授权数据，无法内置全量**）。查不到时就写「未判定」，
>    **绝不把「查不到」当成「不是一区」** —— 这是两个完全不同的结论。
>
> 要把判定换成权威数据：把官方分区表整理成
> `{"中科院一区": ["期刊名", …]}` 放到 `<WORKDIR>/.work_cdp/journal_tiers.json`，
> 用户表会**完全覆盖**内置种子表。

---

### 阶段 H：论文解读

> **执行手册：`流程1/subagent-driven-review/SKILL.md`（必读，本阶段按它执行）**
> 论文数 ≥10 时用**并行 subagent** 逐篇精读；<10 篇可由主 agent 顺序完成，
> 但**下面的解读规范完全一致**。

> ### ★ v13：解读档位（本阶段默认 **L2 详细**）
>
> 解读按**三个档位**产出，档位由**阶段 0 确认**，落盘 `interpret_tier`：
>
> | 档位 | 内容 | 本阶段形态 |
> |---|---|---|
> | L1 精简 | 一句话定位 + 主要成就 + 关键位点与结论数字（2–5 个）；**不含**误差项 / 样本量 / 图表引用 / 实验步骤 | 速览表，适合放进 PPT 与一览表 |
> | **L2 详细（默认）** | 另加误差项、样本量、有限实验过程、排除性证据、局限 | 逐篇卡片，即本阶段既有形态 |
> | L3 完整 | 另加完整论证链、机制细节、逐条局限、作者语气标注 | 深度解读，适合单篇代表作 |
>
> ⛔ **三档只差细节量，不差断言强度。** 同一个发现在三档里的动词强度必须一致，
> 且与原文同级 —— L1 不因为地方小就把原文的「提示」写成「证实」。
>
> ⚠️ **L1 的下游代价（选档前必读）**：阶段 H 的**关键词统计**、
> 阶段 K 的**知识图谱与词云**、以及 `refs_curate.py` 的**方向相关性打分**
> 都从解读卡的 `methods` / `key_20` / 标签里取语料。
> **L1 不写方法清单**（`methods` 留空），这三处会明显变薄 ——
> 属于预期内的取舍，不是缺陷。**若后续要做知识图谱，建议 `L2` 起步。**
> 已核实：这些消费者读的是 `interpret/results/*.json` **卡片**，
> 不是渲染后的 Markdown，所以改档**不会**让它们解析失败。
>
> 改档：`uv run python 流程1/scripts/prepare_stage.py --set interpret_tier=L3`
> 查询：`uv run python 流程1/scripts/prepare_stage.py --tier H`
> 规范：skill `paper-interpretation-writing`（档位规范 / 句式库 / 数据契约 / 离线校验器）

**两步命令**：

```bash
# ⓪ 【v7】安装词表（只做一次；两个文件都已有模板，按该作者语料裁剪后使用）
cp 流程1/templates/方法范式标签词表.json      "$PAPER_WORKDIR/.work_cdp/tag_vocabulary.json"
cp 流程1/templates/关键词词族_学科通用.json    "$PAPER_WORKDIR/.work_cdp/keyword_families.json"
#    ★ 通用词族表是**底座不是终稿**：与本领域无关的族请删掉，
#      否则报告里会出现大量恒为 0 的噪声行。

# ① 先补摘要语料（**v7.1 分层**：直连优先，缺口转 CDP）
uv run python 流程1/scripts/fetch_direct.py                     # 第一层：S2→OpenAlex→EuropePMC→Crossref→PubMed
#   若结尾提示仍有缺口 → 转第二层（CDP）：
#   uv run --with websocket-client python 流程1/scripts/grab_abstracts.py --retry-failed
#   再重跑 fetch_direct.py 把 CDP 结果折进统一产物
#   （fetch_openalex_abstracts.py 仍可用，是 OpenAlex 单源版，且已改为不覆盖其它源）
#   ★ v13：档位会随任务包一起下发；也可临时覆盖：
uv run --with pypdf python 流程1/scripts/interpret_prepare.py   # ② 组装自包含任务包（含 interpret_tier）
#      uv run --with pypdf python 流程1/scripts/interpret_prepare.py --tier L3
#   ③ 同一条消息内并行分派 4–6 个 subagent（每篇一个，各写各的 result.json）
#      ★ 提示词必须写死四件事：任务包路径 / 结果路径 / 「只写你自己那份」/ **档位**
uv run python 流程1/scripts/interpret_build.py                  # ④ 汇总成报告（按档位渲染）
```

> **① 为什么不能省**：没有全文的篇目（付费墙/403/国内不可达）**只能靠摘要解读**。
> 而 Crossref 记录**不含摘要**，摘要来自 `abstracts_openalex.json`。
> 不先跑这一步，任务包会「正文与摘要双空」—— subagent 无米下锅。
> `interpret_prepare.py` 会在结尾**显式告警**这种篇目。

`interpret_prepare.py` 把「题录 + 摘要 + **已抽取的正文文本**（带 `[p.N]` 页标记）」
固化成任务包，因此 subagent **不需要**联网、也**不需要**解析 PDF ——
这是为了从机制上杜绝「顺手 curl 一篇」这种违反 §5.1 禁令的行为。

> **任务包命名与截断（v6.1）**
>
> - **文件名只由 DOI 决定**（`.work_cdp/interpret/tasks/<doi_slug>.json`），
>   序号只写在 JSON 内部。**绝不按「第几篇」命名** —— 排序一变（例如某条题录
>   暂时缺年份），序号就会重排，历史解读卡会**错位到别的论文**上。
> - **超长正文保留头 60% + 尾 40%**，中间以 `[中间省略 N 字符]` 衔接。
>   论文的**结论、量化数据、自述局限都在结尾**，从头部硬截恰好会把它们丢掉。
>   完整文本另存 `.work_cdp/interpret/fulltext/<doi>.txt`，任务包内有
>   `fulltext_path` 可分段深读。
> - 缺失年份、陈旧任务包、孤儿解读卡都会**显式告警**，不静默处理。

#### H.1 单篇解读必须回答的四步（**用户指定的强制格式**）

| 步骤 | 字段 | 要求 |
|---|---|---|
| **1. 明确的方法论** | `methods` | 实验体系、表征手段、计算模型、样本与对照、统计方法、关键参数 —— 具体到可复现，不写「采用多种方法」这类空话 |
| **2. 经过验证的结论** | `verified_conclusions` | **只收有数据/实验支撑**的结论；作者的推测、展望、类比归入待验证问题 |
| **3. 关键信息（二八原则）** | `key_20` | 见下方 H.2 |
| **4. 优化 / 提升数据** | `quantified_gains` | 见下方 H.3 |

> **Goals**：深入理解论文的主旨、关键思路与待解决问题；为读者提炼最重要的关键信息。
> **Constrains**：遵循「二八原则」；输出**阅读的总结文字**（不是摘抄原句）。
> **Skills**：熟练阅读学术论文结构与内容；总结梳理主旨/关键思路/待解决问题；细致入微地分析细节。
>
> 这四步是**用户明确给定**的规则，**不得自行增删字段或降低要求**。

#### H.2 「二八原则」怎么落地

> 「二八原则」是指：**本文有 20% 的内容是关键信息，这部分信息可以让我理解其他 80% 的内容。**

把**那 20%** 整理成**有序的文字**（按逻辑顺序，不是随意罗列），内容包括但不限于：

- 论文**主旨**；
- 论文**有效解决了什么问题**；
- 论文有哪些**待验证的问题**。

#### H.3 量化提升数据必须逐条列出

例如「提升了某方面的性能高达 10%」这类，用一个表格逐条列：

| 要点 | 要求 |
|---|---|
| 数值 | **原样引用**（含单位与有效数字），**不得换算、不得四舍五入、不得改写措辞** |
| 基线 | 相对什么对比（对照组的数值） |
| 条件 | 在什么条件下取得（温度 / pH / 样本量 / 数据集等） |
| 出处 | 图 3b / 表 2 / 正文 p.7 —— 便于回查 |

> 论文**没有**量化提升数据时写 `[]` 并说明 —— **绝不为凑字段编数字**（§3 铁律 5）。

#### H.4 报告必须包含的五部分

1. **时间线要点串联**（按发表先后）：每篇一句定位 + 1–2 条关键数据。
2. **逐篇解读**：每篇按 H.1 的四步展开（含量化数据表）。
3. **关键词统计**：题名 + 摘要 + 解读卡语料做词频统计，给出
   「**语义词族合并计数**」+「**单一条目频次**」两张表。
   词族表见 `templates/关键词词族模板.json`（作者专属）与
   `templates/关键词词族_学科通用.json`（**学科通用底座**，v7 新增）——
   后者由工作根目录《研究方法与关键词总表.md》的 24 个领域派生，
   **全部成员都是单 token**，可直接使用。
   **没有词族表就只出单条目频次，不臆造合并关系**。
4. **主要研究方向归纳**（3–6 条，按被标注篇数排序）——
   **必须来自受控词表**（见下方 H.5）。
5. **研究方法范式（Paradigm）分析** —— 同样**必须来自受控词表**。

#### H.5 标签必须取受控词表（**v7 新增，直接影响报告准确性**）

> **问题**：解读卡由**多个并行 subagent** 分别产出，`direction_tags` /
> `paradigm_tags` 是自由填写的。于是同一概念会写成
> `machine-learning` / `machine learning` / `ML` / `机器学习` ——
> 汇总时被拆成 4 条弱信号，「研究方向归纳」与「范式分析」两节直接失真。
> 子流程手册 §8 把这种现象称为**标准漂移**。

**做法**：

1. 阶段 H 开始前把 `流程1/templates/方法范式标签词表.json` 复制到
   `.work_cdp/tag_vocabulary.json`；
2. `interpret_prepare.py` 会把该词表**嵌进每个任务包**，
   subagent 提示词要求 `direction_tags` / `paradigm_tags` **必须从中取词**；
3. 确实没有合适的词时才自造，且**必须加前缀 `自定义:`**
   （如 `自定义:冷冻电镜断层扫描`）—— 加前缀是为了让「词表缺词」与
   「标注漂移」在汇总时可区分；
4. `refs_curate.py` 与 `refs_build.py` 按「精确命中 → 别名 → 去连字符」三级归一，
   **未归一的标签保留原值并在报告中如实列出**，绝不静默丢弃。

**词表三个维度的定义**（完整表见 `流程1/templates/方法范式标签词表.json`）：

| 维度 | 取值 | 数量约束 |
|---|---|---|
| `direction_tags` | 领域维度（基因组学 / 分子与化学 / 临床与流行病 …） | 2–4 个 |
| `paradigm_tags` | 做法维度（experimental / computational / machine-learning …） | 1–3 个 |
| `network_role`（仅阶段 H2） | 综述网络角色（奠基工作 / 方法源头 / 共识综述 / 争议焦点 / 最新进展） | 1 个 |

> `experimental` 与 `computational` 被标为**互斥范式**：两者同时出现时
> 以篇数多者为准，并在报告中告警 —— 一篇论文同时是"纯湿实验"和"纯计算"
> 通常是标注错误，值得人工看一眼。

**另需如实申报**（`interpret_build.py` 自动生成第六节）：
缺卡的篇目、仅摘要级解读的篇目、正文被截断的篇目 ——
**不得静默略过**，否则读者会误以为覆盖了全部。

### 阶段 H2：去重与相关性整理 → 最终列表 + 研究方向综述（review）

> **闸门**：`uv run python 流程1/scripts/prepare_stage.py --check H2`
> （退出码 3 = 准备阶段已关闭 `reverse_refs` 或 `curate_review`）
>
> **执行手册**：本阶段的解读部分同样按
> `流程1/subagent-driven-review/SKILL.md` 执行（**同一套并行 subagent 机制**），
> 只是把"解读一位作者的论文"换成"解读一个方向的综述网络"。
>
> ★ **v13 档位**：本阶段用 **`review_interpret_tier`，默认 `L3`（完整）**。
> 综述网络的结论建立在"这条文献在网络里干什么"之上，
> 需要完整的排除性证据与证据强度标注才能判断；低档会把这些丢掉。
> 查询 `prepare_stage.py --tier H2`。任务包内含 `interpret_tier`，subagent 以它为准。

**为什么要有这一步**：阶段 D2 输出的是**原始候选池**（138 条很常见），
里面混着三类东西 —— 重复条目、网络边缘的偶发引用、
以及**与该作者主体研究方向无关**的引用（同一篇论文会引很多不相关的文献）。
不去掉它们，review 会被噪声带偏。

#### H2 ① 去重与相关性整理

```bash
uv run python 流程1/scripts/refs_curate.py
#   可选：--cut=25        相关性阈值（0–100，越低保留越多）
#   可选：--keep-top=0    无论如何至少保留前 N 条（0=不设下限）
```

**做了什么**：

| 步骤 | 做法 |
|---|---|
| **去重** | 两级：① DOI 归一后去重；② 无 DOI 的按**题名归一**去重，并与已有 DOI 条目**跨集合**比对。合并时保留信息更全的一条，被引数取最大值；**每一组合并都留档** |
| **方向画像** | 取作者**主体研究方向**：解读卡的 `direction_tags`（归一后）+ 作者题名/摘要的高频词（出现 ≥2 次）+ 常投期刊 |
| **相关性打分** | `相关性 = 30 × 共引强度 + 15 × 期刊命中 + 55 × 关键词重合`，满分 100。三个分量**全部公开在报告里**，可复核、可调参 |
| **剔除** | 见下方保留规则；**被剔除的逐条写进报告与 `dropped.json`，附理由** |

**保留规则**（满足任一即保留）：

1. 被作者 **≥3 篇**论文引用 —— 网络核心，**强制保留**，不受阈值影响；
2. 相关性 ≥ 阈值；
3. 被引用 ≥2 次且与方向词表有词面重合。

> ⛔ **绝不静默丢弃**：剔除是不可逆的，而"这个词表没覆盖到"与
> "这篇真的不相关"在自动判定下难以区分。因此被剔除的条目**一律留档**，
> 报告里单列一节，人工可随时翻案。
> 但**已剔除的条目不会进入 review** —— 这是"整理"，不是"全都要"。

**产出**：`<作者名>_研究方向文献终表.md`（含画像 / 最终列表 / 评分口径 /
被剔除清单 / 去重合并记录）、`.work_cdp/refs/final_list.json`、`dropped.json`。

#### H2 ② 组装综述任务包

```bash
uv run python 流程1/scripts/refs_prepare.py --top=40
```

把 `final_list.json` 的每条文献连同**摘要**与**受控标签词表**固化成
`.work_cdp/refs/tasks/<doi_slug>.json`（自包含，subagent 不需要联网）。

> ⚠️ **证据层级必须如实标注**：本阶段文献**不是作者本人的论文**，
> 本流程只下载了作者自己的全文。因此绝大多数条目**只有摘要**。
> 任务包写 `evidence.source = "abstract"`，摘要为空则写 `"metadata-only"`。
> 提示词要求 subagent 承认这一限制 —— **不得假装读到了全文**。

#### H2 ③ 并行 subagent 精读 → 汇总

```
同一条消息内并行分派 4–6 个 subagent（每条一个，各写各的 result.json）
提示词模板见 流程1/subagent-driven-review/SKILL.md §7（**综述网络版**）
```

```bash
uv run python 流程1/scripts/refs_build.py
```

**产出**：`<作者名>_研究方向综述.md`（**单一文件**，v7.2 起不再写 `_完整版.md` 别名）——
这就是 **review 结果**，也是阶段 P 做 PPT 的两大素材之一。

报告结构：概览 / 方向分布 / 范式分布 / **网络角色分布** /
逐篇解读 / 研究脉络 / 关键结论汇总 / 研究缺口与展望 / **缺卡与限制申报**。

> 解读卡 schema 与阶段 H **完全一致**（同样 11 个必需字段、同样四步规范、
> 同样「不为凑字段编数字」），另加 2 个综述网络专用字段：
> `network_role`（奠基工作 / 方法源头 / 共识综述 / 争议焦点 / 最新进展）、
> `relation_to_author`（该作者在哪些方面沿用 / 扩展 / 对照了本文）。

---

### 阶段 K：知识图谱（overview / 词云 / 方向聚类）

> **闸门**：`uv run python 流程1/scripts/prepare_stage.py --check K`
> （退出码 3 = 准备阶段 `make_graphs=no`）
>
> **位置是硬性的**：**所有搜集工作完成之后、PPT 制作之前**（H2 之后、P 之前）。
> 三张图都要吃「最终」产物 —— 解读卡、综述终表、review 结果；
> 早画出来的图，等后面补了数据就全过期了。

```bash
uv run --with matplotlib --with networkx python 流程1/scripts/knowledge_graph.py
#    --format=auto（默认，SVG 优先、失败退 PNG）｜ --also-png 两种都要
#    --recent-years=3     方向聚类的「近 N 年」
#    --max-reviews=3      每个方向下最多挂几篇综述
#    --max-dirs=12 / --max-review-nodes=24   图上方向/综述节点的上限
#    --dpi=300            PNG 回退时的分辨率
```

**三张图（用户指定）**

| # | 图 | 内容 |
|---|---|---|
| ① | **overview** | 以作者为中心；**阶段 → 研究方向 → 该方向最紧密的综述**（取自 `refs/final_list.json` 的最终 review 列表）；**Louvain 社区检测**按主题聚类上色；两条方法关系紧密时连一条**记忆连接**（有名字 + 一句原因）。**16:10 画幅、标题为「<作者名> 研究工作图谱」**（v7.5：无「（overview）」、无灰色副标题） |
| ② | **词云** | 以作者为中心；**颜色区分研究方向**；**剔除 "mice" 这类有误导性的常见词**（模型生物/样本/载体/泛化词），着力于**研究方法与实验范式** |
| ③ | **方向聚类** | 每个方向**近 N 年**的论文与热点 |

**质检（「这一簇在讲什么？」「哪里在自相矛盾？」）**

| 问题 | 怎么答（**全部由数据聚合，不做语义臆测**） |
|---|---|
| 这一簇在讲什么？ | `cluster_notes()` 汇总该簇的方向、方法范式、高频方法词、文献年份跨度与节点数 |
| 哪里在自相矛盾？ | `find_contradictions()`：同方向内，**一篇的「待验证问题」与另一篇的「已验证结论」高度重合**（重合度 ≥0.30 且共同词 ≥4）→ 一篇说"还没解决"、另一篇说"已经证实"。**输出的是待复核线索，不是已确认的矛盾**，报告措辞严格区分 |

**格式要求（用户指定，已实现）**：

- **SVG 优先**，SVG 失败**自动退 PNG**；`--also-png` 可两者都要。
- ① **overview 画幅固定 16:10**（`16×10 in`；PNG 回退即 `4800×3000 @300dpi`）——
  与演示版面同比例，整幅铺放不出现上下黑边、也不用再被裁（v7.5）。
  该图**按画布原尺寸导出**（`save_fig(..., tight=False)`），
  **所有文字都必须画在画布内**，画到画布外会被直接裁掉。
- **高分辨率**（标准 = 清晰展示所有字符）——词云用**确定性螺旋 + 矩形碰撞检测**逐词试位，
  不靠固定步长硬摆（实测不避让时 `photocatalytic/labeling`、`porous/mof` 会叠死）。
- **背景透明**（`transparent=True`）。
- 中文字体自动探测（Microsoft YaHei / SimHei / Noto Sans CJK …）。

**产出**：`<作者名>_知识图谱.md`（含三张图的说明、聚类解读、记忆连接表、
冲突线索表、**被剔除的词**、方向热点）+ `.work_cdp/graph/*.svg|png` + `graph_data.json`。

> **诚实性约束**：挂不上方向的综述、放不下的低频词、不足 N 年的方向样本 ——
> 全部**显式报出数量**，不静默丢弃；`--max-dirs` / `--max-review-nodes` 截断了多少
> 也会打印。完整清单始终在《研究方向综述》里，图上只是节选。

---

### 阶段 P：PPT 生成（可选，整合 ppt-master）

> **闸门**：`uv run python 流程1/scripts/prepare_stage.py --check P`
> （退出码 3 = 准备阶段 `make_ppt=no`，本阶段跳过）
>
> **执行手册**：`流程1/ppt-integration/SKILL.md`（**必读**，含简化的 Strategist 说明与质量门）

**材料来源（用户指定，不需另行准备）**：

1. 《`<作者名>_论文解读.md`》 —— 该作者**文献亮点解读**
2. 《`<作者名>_研究方向综述.md`》 —— 阶段 H2 的 **review 结果**

```bash
# ① 生成材料 + design_spec.md + spec_lock.md（一条命令）
uv run python 流程1/scripts/ppt_brief.py --pages=standard
# ② 页面 SVG 由主 agent **手写**（ppt-master Step 6；不得脚本批量生成）
# ③ svg_quality_checker.py 必须 0 error
# ④ Step 7 三条命令串行导出：total_md_split → finalize_svg → svg_to_pptx
```

**本流程对 ppt-master 的改动 = 简化 Strategist Phase**：

| 原环节 | 处置 |
|---|---|
| Step 3 模板候选准备 | **跳过**（不套模板，走自由设计） |
| Step 4 两阶段**阻塞式确认**（Confirm UI / 聊天确认） | **跳过交互，保留产物** —— `design_spec.md`（含 I–X 全节）与 `spec_lock.md` 由 `ppt_brief.py` **确定性生成**，因为阶段 0 已把受众/意图/页数/画布等选择收齐，**原地已无可确认的内容** |
| Step 1–2 / 5 / 6 / 7 | **不改**：source 导入、图像获取触发、**手写 SVG**、串行导出与导出后检全部保留 |

> ⛔ **不得伪造确认**：不启动 Confirm UI、不写 `confirm_ui/` 回执、
> 不生成"Stage 1/Stage 2 已确认"的假状态。上游流水线就是决策主体，
> 两份 Spec 文件就是决策证据。详见 `流程1/ppt-integration/SKILL.md` §3。

**v7.2：自动接入阶段 K 的图谱与方向综述整理**

| 项 | 做法 |
|---|---|
| 图谱 | 从 `.work_cdp/graph/*.png` 收集，**复制进 `<project>/images/`**，写入 `design_spec.md §VIII`（`Acquire Via: user`、`Status: Existing`，故 Step 5 不会去"获取"它们、不触发任何抓取），并在 §IX 生成整幅铺放的页面 |
| 为什么用 PNG 而不是 SVG | PPTX 的 `<image>` 内嵌对 SVG 的支持随 PowerPoint 版本而异，栅格图最稳。阶段 K 默认只出 SVG，所以 `ppt_brief.py` **检测不到 PNG 时明确提示补跑 `--also-png`**，而不是悄悄少放一张图 |
| 方向综述整理 | 新增「三、方向综述整理」材料节与对应页面：每个方向 → 最紧密综述 + 已确认结论 + 未解决问题。数据源与《研究方向综述》**同源**，保证口径一致 |
| 关闭 | `--no-graphs` 可整体不接入 |

**产出**：`ppt-master/projects/<项目名>/exports/<项目名>_<时间戳>.pptx`
＋ `validation/*.report.json`。

---

### 阶段 I：收尾

- 校验 `<作者名>_paper_Exports/` 下 PDF 完整性（`store.validate_pdf`）与**页数合理性**
  （正文一般 ≥6 页；2 页件须排查是否为补充材料/图注页）。
- 用 `uv run --with pypdf python 流程1/scripts/inventory_build.py` 生成 `全文清单.md`
  （哪些拿到全文、来源途径、页数、哪些只有摘要、原因）。
  > 题录总表优先读 `crossref_all.json`，回退历史名 `master.json`；
  > 阶段分组来自 `.work_cdp/periods.json`（**未配置就不分组**，不内置任何作者履历）。
- 用 `rename_to_title.py` 确认全部 PDF 已按**题名**命名且索引已登记。
- 生成 `<作者名>_交付物索引.md`（交付总览 + 数据核验链 + 复现步骤 + 本次改造台账）。
- `report_artifacts` 申报交付物。

---

## 5. 关键坑与对策（实战经验）

| 问题 | 现象 | 对策 |
|---|---|---|
| CDP WebSocket 403 | `Handshake status 403 Forbidden` | 启动加 `--remote-allow-origins=*`；并确保旧实例已被杀 |
| Edge 进程退出 | `ConnectionRefusedError 10061` | **多实例池** + 逐篇 TCP 探活 + 自动重启；不要单实例跑长批 |
| **脚本「死机」** | 无输出、长时间不动 | 已修复：所有 CDP 调用带**绝对截止时间**；不再有 `while True: recv()`；`wait_for_manual` 默认关闭 |
| **误触发 CAPTCHA（形态一）** | 短页面/勘误页被当成验证页，弹窗空等 15 分钟 | 已修复：改用 `detect.judge()` 精确判定，**绝不用文本长度当判据** |
| **误触发 CAPTCHA（形态二）** | Cloudflare 的 JS 挑战会**自动放行**，但脚本一见 `just a moment` 就放弃 → 把本可自动通过的站点误报为「需人工验证」 | 已修复：新增 `settle_challenge()` **宽限期**（默认 25s，`--grace=` 可调）。实测 OUP：首探标题「请稍候…」正文 118 字符 → 31s 后自动放行 → 正文 56551 字符 → 成功下载 |
| **泛词误报封禁** | 正文含 `forbidden` / `blocked by` → 触发全局中止 | 已修复：封禁用精确短语；模糊词仅「短页面开头」才算数 |
| 杀进程失败 | `Invalid argument/option - 'C:/Program Files/Git/PID'` | Git Bash 路径转换所致，用 `MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F` |
| 中文打印乱码 | `UnicodeEncodeError: 'gbk' codec` | 统一用 `logutil.py`（内部 `errors="replace"`，不会中断流程） |
| 标题含噪声 | 混入 `check_circle Crossref`、页码 | 在 `clean_title()` 中统一替换/正则清洗 |
| Crossref 限流 | 并发时批量失败 | 失败项串行重试 + 指数退避；UA 带 mailto |
| ORCID 条目重复 | API 返回 107 条但页面仅 79 条 | **以网页 `Works (N of M)` 为准**，按 DOI 去重 |
| 付费全文不可得 | 出版社订阅墙 | 明确注明限制；只翻译已获得全文的论文；其余用摘要级信息解读 |
| **站点在国内根本连不上** | SSRN（`10.2139/*`）在中国大陆 IP 段网络层不可达，不是风控问题 | 已固化进 `detect.CN_INACCESSIBLE`，选取待办时**直接跳过**，不做无谓重试；在清单中注明「国内 IP 不可访问」 |
| 触发风控 | 短时间大量请求出版社 | 低频拟人化 + 站点熔断（见阶段 E） |
| **重复下载 / 误删已有 PDF** | 早期文件无年份前缀，被当成「没有」→ 重下；校验失败时删掉同名旧文件 | 已修复：`store.existing_pdf()` 兼容两种命名；`_write_pdf` 只删本次写入的文件 |
| **1 页勘误被误拒** | Author Correction 本身就是 1 页 | 已修复：`store.validate_pdf()` 放行「1 页且 ≥100 KB」 |
| 摘要 API 缺口 | ACS/Elsevier 不向 Crossref 注册摘要 | 用 **OpenAlex 倒排索引重建** 兜底 |
| 中文 PDF 页数校验 | 无法用 pdftoppm | 用 `pypdf` 提取文本，统计 CJK 字符数间接验证 |
| 🆕 **改名后重复下载** | 题名命名使「按 DOI 找文件」失效，历史去重逻辑失效 | 新增 `.work_cdp/pdf_index.json` 登记表；`store.register_pdf()` 在落盘后写入，`existing_pdf()` 优先查表（**v3**） |
| 🆕 **Ray ID / 429 误判为 IP 封禁** | Cloudflare 403/429 错误页带 Ray ID → 被判 `blocked` → **整轮中止**（实测 PNAS 一次 403 就停掉全部批次） | 探针新增 `SOFT` 名单（经 `hits.softHead` 回传）+ `classify()` 新增规则 5b/5c/5d：Ray ID/限流话术只判 `challenge`（站点级熔断），**不再全局停摆**（**v3**） |
| 🆕 **抓到的是补充材料而非正文** | AHA/Circ 等站点 `links` 首位常是 `suppl_file/*.pdf`（补充材料）或图注页，2 页却通过校验 | 下载后校验页数；正文一般 ≥6 页。残缺件改名 `【补充材料】…` 保留，用 `history/*.pdf` 或 `/doi/pdf/...?download=true` 重抓（**v3**） |
| 🆕 **翻译 PDF 空白/无中文** | HTML 未写入中文或字体未嵌入 | `render_html_pdf.py` 退出码 2 = CJK < 50，需回查 HTML；正常输出 `OK pages=… cjk_first_page=…`（**v3**） |
| 🆕 **自编脚本直连抓取原文（含 curl 探活）** | 用 `curl -I` 测出版社连通性、或用 `urllib`/`requests` 直下**全文** → 同样触发风控且拿不到内容，还无法纳入流程判定 | **严禁**（§ 5.1 **第二层**）；连通性判断交给浏览器（`detect.navStatus` 即真实 HTTP 状态）。**OA 全文同样走 CDP**。★ 摘要/综述题录/白名单开源库属**第一层**，**允许**直连 —— 边界见 §5.1 分层表 |
| 🆕 **用鼠标事件自动过验证** | 「CDP 点击 `isTrusted=True`，应该能骗过 Turnstile」——**错误**：判定不靠该次点击，循环试探还会升级为封禁 | **严禁**（鼠标自动化禁令，§ 5.2）；人机验证一律转人工（`--manual-wait=N` + 窗口置前 + cookie 留存）。仅 Cookie 弹窗/功能按钮可自动化（**v3**） |

---

## 5.1 ⛔ 分层获取策略（v7.1，**最高优先级** —— 优先级高于其余所有禁令）

> **立规来源**
> · v4（2026-09-14）：用户要求「如无特别要求，全部使用 CDP 完成抓取」。
> · **v7.1（2026-09-19）用户修订**：「对于获取**原文**的需求仍然禁止直链抓取；
>   但是对于**摘要、综述**或工作文件夹下**验证可行的开源数据库**可以直接获取，
>   减轻压力。同时若直接获取尝试失败则转向 CDP 获取。」
>
> **一句话**：**拿"原文"走 CDP；拿"摘要 / 综述题录 / 白名单开源库"直接取，
> 取不到再转 CDP。**

### 一、分层表（**这张表就是全部规则**）

| 层 | 内容类别 | 获取方式 | 实现 |
|---|---|---|---|
| **第一层** | 元数据（题录、作者、被引数、参考文献关系） | ✅ **允许直连** | `fetch_crossref_all.py` / `orcid_probe.py` / `unpaywall_check.py` / `refs_backfill.py` |
| **第一层** | **摘要** | ✅ **允许直连** | `fetch_direct.py`（多源自动降级）→ 失败转第二层 |
| **第一层** | **综述**（题录 / 摘要级） | ✅ **允许直连** | 同上；综述的**正文**属第二层 |
| **第一层** | 白名单内的**开源数据库** | ✅ **允许直连** | `templates/直连白名单.json` 逐条列举（见下） |
| **第二层** | **原文全文**（PDF / HTML 正文），含 OA 文章 | ⛔ **禁止直连，一律走 CDP** | `download_all_fulltexts.py` / `save_pdf_via_browser.py` |
| **回退** | 第一层尝试失败 | ➡️ **转向 CDP** | `fetch_direct.py` 写出缺口清单 → `grab_abstracts.py --retry-failed` |

> **判定分界**：这次请求**要和不要拿"论文正文"**？
> 拿正文（PDF / HTML 全文）→ **必须 CDP**；拿摘要 / 题录 / 结构化元数据 → **第一层直连**。

### 二、第一层的执行方式（**不要临场判断**）

「工作文件夹下验证可行的开源数据库」这句话由
**`templates/直连白名单.json`** 落成可执行判据：白名单逐条给出
`domain` / `purpose` / `content_class` / `oa_only` / `verified_by` / `verified_at`。

```bash
# 先问策略（不联网，退出码 0=允许 / 3=禁止）
uv run python 流程1/scripts/fetch_direct.py --check-domain="<URL>"

# 批量取摘要（多源自动降级：S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed）
uv run python 流程1/scripts/fetch_direct.py
```

**两条硬约束**：

1. **fail closed** —— 白名单**之外**的域名一律拒绝，不做"看着像开源库就试试"。
   域名匹配用**后缀边界**（`host == d` 或 `host.endswith("." + d)`），
   不是子串匹配 —— 否则 `api.crossref.org.attacker.com` 会被放行。
2. **白名单不含任何出版社内容站**，且每条 `content_class` **只能是
   `metadata` 或 `abstract`**。这两条由 `fetch_direct.py --self-test` 断言守住 ——
   想往白名单里塞一个能下全文的源，自检会直接失败。

> 当前白名单收录：Crossref、OpenAlex、ORCID、Unpaywall、Semantic Scholar、
> Europe PMC、PubMed E-utilities、arXiv、bioRxiv、CORE、DataCite。
> 收录判据（三条同时满足）：① 公开、无需密钥或有免费额度；
> ② 返回**结构化数据**而非给人读的网页；③ `skills/` 下已有 skill 在用或本流程实跑成功。

### 三、回退规则（**第一层失败 → 第二层**）

```
第一层逐源尝试（S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed）
   ├─ 命中 → 写入 .work_cdp/abstracts_openalex.json，结束（不占用浏览器）
   └─ 全部未命中 → 写入 .work_cdp/direct_failed.json（缺口清单）
                    ↓
              转第二层 CDP：grab_abstracts.py --retry-failed
                    ↓
              重跑 fetch_direct.py 把 CDP 结果折进统一产物
```

- 回退是**自动判定、显式执行**：`fetch_direct.py` 打印确切命令；
  加 `--run-cdp-fallback` 才会真的调起（它需要 CDP 实例已在跑，
  脚本不擅自启动浏览器）。
- **重试有上限**：每个源对同一 DOI 最多试 3 次（指数退避），
  404/403 立即换源不重试。**不要**对同一个源反复冲击。

### 四、第二层（原文）仍然禁止直连 —— 依据不变

1. **风控风险**：直连请求同样被站点计数、同样触发风控；一旦 IP 被计数，
   波及后续**所有**站点的抓取 —— 代价远大于省下的那点时间。
2. **不可诊断**：直连拿到的往往是拦截页 / 半截内容，而流程的判定与日志体系
   （`detect.judge` / `navStatus` / `botVendor`）全部建立在浏览器路径上。
3. **合规与一致性**：真实浏览器访问 = 正常阅读行为；自编脚本批量直取正文
   = 与站点意愿相悖的抓取。
4. **已有成熟工具**：`download_all_fulltexts.py` 已具备多实例并行、站点级熔断、
   断点续传、挑战宽限、人工验证转接 —— 自编脚本必然缺少其中若干项，是**能力退化**。

**第二层仍然明令禁止的行为**：

| # | 禁止行为 | 说明 |
|---|---|---|
| 1 | 自编脚本直链下载**原文全文** | 含 `urllib` / `requests` / `curl` / `wget` / PowerShell，含"临时试一下" |
| 2 | 用 `curl -I` / `HEAD` 对**出版社内容站**探活 | 连通性由浏览器判定（`detect.navStatus`） |
| 3 | 以"OA / 开放获取"为由走纯 HTTP 直取**全文** | **OA 全文也走 CDP**；开放获取只意味着无需订阅 |
| 4 | 自建下载器 / 第三方下载工具 | 一律用 `download_all_fulltexts.py` / `save_pdf_via_browser.py` |
| 5 | 把白名单外的域名当作"开源库"直接取 | 白名单即边界；想加就先按收录判据补进白名单并让 `--self-test` 通过 |

### 五、违规处置（不可跳过）

1. **立即停止**该脚本与相关进程，删除临时下载器；
2. **如实报告**：抓了什么、来自哪个站点、属于哪一层、影响范围；
3. 原文类改用 CDP 路径重做；已直连获取的**摘要/元数据**保留但**标注来源**，
   不得冒充 CDP 路径产物；
4. 若站点已因直连被计数，按「封禁应急规则」处理。

---

---

## 5.2 ⛔ 鼠标自动化禁令（v3，与「分层获取策略」「封禁应急规则」同级）

> **本禁令来源**：2026-09-13 用户要求 —— 将鼠标自动化验证成果固化为禁令。
> **适用对象**：CDP `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` /
> `Input.insertText` / `Input.synthesizeScrollGesture` 及任何等价手段。
> **核心条款**：**禁止**用上述手段**尝试通过人机验证**（Turnstile / CAPTCHA / 滑块 / 挑战页）。
> **一句话**：CDP 鼠标事件适合点**按钮**，不适合破**验证** —— 对后者不实现、不尝试、只转人工。

### 一、禁止清单（红线）

| # | 禁止行为 | 说明 |
|---|---|---|
| 1 | 用 `Input.*` 点击 Turnstile / reCAPTCHA / hCaptcha 的**复选框或验证组件** | 单次、循环均禁止 |
| 2 | 合成 `mouseMoved` 轨迹模拟**滑块 / 拼图 / 按住**手势 | 属绕过安全机制 |
| 3 | 对验证页做**批量 / 循环 / 退避式试探点击** | 即「反复试探」，直接升级为封禁 |
| 4 | 页内 JS 伪造（`element.click()`、改写 `isTrusted`、劫持验证回调） | 既无效又违规 |
| 5 | 把人工验证环节「自动化」以提升流程自动化率 | 本流程**只做转人工** |
| 6 | 将上述任一行为写入脚本 / 宏 / 定时任务 | 含「实验性」「一次性」尝试 |

### 二、允许范围（边界，避免误伤）

对**非验证类**的常规页面元素做自动化点击是**允许的**（属常规 UI 自动化，不在禁令内）：

| 场景 | 做法 |
|---|---|
| Cookie / 同意书弹窗 | `CONSENT_JS` + `dismiss_consent()` |
| 「View PDF」/「Download」/「Accept」等**页面功能按钮** | 落地页上下文 `fetch` 直取直链；必要时 `Input.dispatchMouseEvent` 点击 |
| 页内导航 / 滚动 | 常规操作 |

> **判定分界**：看元素**用途** —— 「**安全验证**」一律转人工；「**页面功能**」可自动化。

### 三、禁止依据 1：实测「点了也没用」（isTrusted 取证）

`Input.dispatchMouseEvent` 经浏览器**输入管线**注入，产出事件 `isTrusted === true`
（与 JS `element.click()` 的 `false` 不同）——页面**无法从事件属性上区分**它和真实点击。

实测（`.work_cdp/test_input_events.py`，本地页面、零网络请求，仅作技术取证）：

```
① JS element.click()           → click     isTrusted=False  (0,0)
② CDP Input.dispatchMouseEvent → mousedown isTrusted=True  (97,61)
                                 mouseup   isTrusted=True  (97,61)
                                 click     isTrusted=True  (97,61)
```

**推论**：既然属性上区分不了，反爬的判据就**必然在别处**（见依据 2）——
「能发出可信点击」**不等于**「能过验证」。

### 四、禁止依据 2：反爬判定不依赖那一次点击

| 判定维度 | 反爬实际看什么 | 为什么自动点击过不了 |
|---|---|---|
| 协议/环境痕迹 | CDP 会话存在性、`Runtime.enable` 时序侧信道、自动化框架特征 | 与「点得像不像」无关 |
| 行为生物特征 | 轨迹加速度/抖动/停顿、悬停犹豫、直线度、间隔规律性 | **单点瞬移点击**是最典型异常样本 |
| 风险评分 | IP 信誉、环境一致性、请求频次 | 勾选框常只是 UI 摆设，**评分不过照样循环/拒绝** |

### 五、禁止依据 3：代价不可承受

机械重复试探会把「单次挑战」升级为「**站点级封禁**」，并波及后续所有站点；
这正是 § 5 与「封禁应急规则」明令禁止的「反复试探」。

### 六、违规处置（不可跳过）

1. **立即停止**该行为与相关脚本，终止本轮全部后续尝试；
2. **如实报告**站点、时间、影响范围；
3. **转人工**：`--manual-wait=N` → 弹窗（TTL 自动关闭）+ 窗口置前，人在 Edge 点一次；
   **放行 cookie 留在该 profile**，后续同站请求直接通过；
4. 仍不可得 → 机构 VPN / 图书馆代查，或在《全文清单.md》中如实注明限制。

---

## 6. 日志与可诊断性

### 6.1 每脚本文本日志（`logutil.py`）

所有脚本统一使用 `logutil.py`：

- **落盘位置**：`<WORKDIR>/.work_cdp/logs/<脚本名>_<时间戳>.log`
- **指针文件**：`.work_cdp/logs/latest_<脚本名>.log` —— ⚠️ **它是「指针」，内容是该日志的路径，不是日志本身**
- **浏览器日志**：`.work_cdp/logs/edge_<port>.log`（每个实例一份）
- **格式**：`[时:分:秒 +运行秒] 级别 内容`，同时输出到控制台与文件

```bash
# ⚠️ 注意：latest_<name>.log 里存的是**路径**，直接 tail 只会看到一行路径。
#    正确姿势是先取路径再 tail（下面这行是通用写法）：
tail -f "$(cat .work_cdp/logs/latest_download.log)"          # 实时看下载
grep -E "WARN|ERROR" "$(cat .work_cdp/logs/latest_download.log)"   # 只看问题
ls -t .work_cdp/logs/*.log | head                            # 最近写过的日志
```

> 日志器内部用 `errors="replace"` 写文件、控制台包一层容错包装，
> **不会**因为某个生僻字符抛 `UnicodeEncodeError` 而中断整个流程。

### 6.2 操作流水 `run_journal.jsonl`（★ v6 新增，用于**回溯**）

每脚本日志各写各的、时间戳分散，**串不成一条链** —— 长流程出问题时最难的不是修，
而是**还原现场**：这一轮到底跑了哪些脚本、参数是什么、哪一步开始不对。

`logutil.journal(stage, action, **fields)` 把关键动作按时间顺序固化到
`<WORKDIR>/.work_cdp/logs/run_journal.jsonl`（一行一条 JSON，**只追加**）：

```bash
uv run python 流程1/scripts/journal_view.py                # 全部，按时间列出
uv run python 流程1/scripts/journal_view.py --stage=E      # 只看阶段 E（全文下载）
uv run python 流程1/scripts/journal_view.py --action=download.fail   # 只看失败
uv run python 流程1/scripts/journal_view.py --tail=40      # 只看最后 40 条
uv run python 流程1/scripts/journal_view.py --json         # 原样输出，供二次处理
```

已接入的阶段与动作：

| 阶段 | action | 关键字段 |
|---|---|---|
| C | `orcid.extract.start` / `orcid.navigate` / `orcid.expand.narrow` / `orcid.extract.done` / `orcid.extract.empty` | orcid, works, cards, text_len, elapsed_s |
| D | `crossref.fetch.start` / `crossref.fetch.done` / `crossref.enrich.done` / `disambiguate.done` | dois, ok, failed, updated, confirmed, excluded, **undecidable** |
| E | `download.start` / `download.pdf` / `download.fail` / `download.done` | doi, file, size_kb, src, pages, reason, circuit |
| G | `list.build` / `list.excluded` | entries, excluded, disambiguation |

> **两条硬性约定**：① 写 journal **绝不影响主流程**（任何异常都被吞掉 —— 日志是旁路，
> 不是依赖）；② **只记录已发生的事实**，不写预期值，否则回溯会自欺。
>
> journal 的 `undecidable`（数据缺失、无法判定）与 `excluded`（肯定性判定为他人）
> 是**两个不同的字段** —— 前者不是排除，混为一谈会让人误以为成果被剔除。

---

## 7. 产出物规范

```
<工作目录>/<作者名>/
├── <作者名>_paper_Exports/                # ⭐ 原文全文 PDF（规范目录名）
│   ├── <年份>_<论文标题>.pdf               #   题名命名（可读、可去重）
│   ├── 【补充材料】<年份>_<标题>.pdf        #   仅补充材料/图注页的残缺件
│   └── 全文清单.md                        #   逐篇状态 + 来源 + 页数 + 未获取原因
├── <作者名>_论文列表.md                    # ⭐ 规范交付名（GB/T 7714-2015 + DOI + 原文链接）
├── <作者名>_论文解读.md                    # ⭐ 解读（时间线/逐篇四步/关键词/方向/范式）
├── <作者名>_交付物索引.md                   # 交付总览 + 核验链 + 复现步骤
├── <作者名>_论文翻译_<刊><年>_中文版.pdf     # 全文中译（源文件按题名、译文按刊名+年份）
├── <作者名>_高质量文献列表.md               # ⭐v7 阶段 D2：反向查得的综述网络（综述优先）
├── <作者名>_代表性文献参考文献.md            # ⭐v7 阶段 G2：文章 → 参考文献（GB/T 7714）
├── <作者名>_研究方向文献终表.md              # ⭐v7 阶段 H2①：去重 + 相关性整理后的最终列表
├── <作者名>_研究方向综述.md                 # ⭐v7 阶段 H2③：review 结果（单一文件）
├── <作者名>_知识图谱.md                     # ⭐v7.2 阶段 K：三图说明 + 聚类解读 + 冲突线索 + 剔除记录
├── .work_cdp/                             # 中间数据 + logs/ 日志
│   ├── prepare_plan.json                  #   ⭐v7 阶段 0：准备计划 + 阶段闸门（单一事实源）
│   ├── crossref_all.json                  #   题录核实结果（题录总表）
│   ├── disambiguated.json                 #   ⭐ 同名消歧结果（confirmed/review/dropped + code）
│   ├── periods.json                       #   阶段分组（可选，驱动列表与清单的分组）
│   ├── keyword_families.json              #   语义词族表（可选，驱动关键词合并计数）
│   ├── tag_vocabulary.json                #   ⭐v7 受控标签词表（消除并行 subagent 标签漂移）
│   ├── journal_tiers.json                 #   ⭐v7 中科院分区表（可选，**覆盖**内置种子表）
│   ├── unpaywall.json                     #   OA 状态
│   ├── abstracts_openalex.json            #   摘要语料（解读用）
│   ├── fulltext_state.json                #   全文下载状态（可断点续传）
│   ├── pdf_index.json                     #   DOI → 文件名 登记表（题名命名去重）
│   ├── interpret/                         #   ⭐ 阶段 H：tasks/ + results/ + manifest.json
│   ├── refs/                              #   ⭐v7 阶段 D2/G2/H2：见下方结构
│   ├── graph/                             #   ⭐v7.2 阶段 K：*.svg|png + graph_data.json + fulltext/
│   ├── translations/*.html                #   中译 HTML 中间件
│   └── logs/                              #   全部日志（含 edge_<port>.log）
└── 流程1/                                  # 本 skill
```

**`.work_cdp/refs/`（v7 新增，阶段 D2 / G2 / H2 的中转站）**

```
.work_cdp/refs/
├── source_refs.json      # 作者论文 DOI → 其参考文献（含 source_id / venue）
├── refs_pool.json        # 参考文献去重题录池（含摘要、被引、期刊、类型）
├── high_quality.json     # ⭐ D2 产出：高质量筛选结果（G2 / H2 的唯一输入）
├── representative.json   # G2 产出：代表性文献 + 入选理由 + IF 判定
├── final_list.json       # ⭐ H2① 产出：最终文献列表（含评分分量与方向画像）
├── dropped.json          # H2① 产出：被剔除清单 + 合并记录（**可翻案**）
├── manifest.json         # H2② 产出：综述任务清单
├── tasks/*.json          # H2② 产出：自包含综述任务包（subagent 的唯一输入）
├── results/*.json        # subagent 产出：综述解读卡（一 task 一 result）
└── cache/                # OpenAlex HTTP 响应缓存（断点续跑 / 离线复现）
```

> **目录名兼容**：规范名为 `<作者名>_paper_Exports`。若工作目录下已有历史
> `<作者名>_Exports` 且其中**已有 PDF**，`store.exports_dir()` 会**继续沿用**
> 历史目录（不迁移、不复制、不丢文件），并在 stderr 提示一次；
> 想迁到规范名，把 PDF 移过去即可，流程会自动改用新目录。
> 也可用 `PAPER_EXPORTS_DIR` 显式指定完整路径。

**原则**：
1. 新增内容一律**新建文件**，不覆盖用户既有文件（除非用户明确要求）。
2. 残缺/异常产物**不删除**，改名后保留（如 `【补充材料】…`），便于回溯。
3. **命名规则**：源 PDF 用 `<year>_<论文标题>.pdf`（读取时兼容历史 `<year>_<DOI 转义>.pdf`
   与 `<DOI 转义>.pdf`，并以 `pdf_index.json` 兜底）；中译 PDF 用 `<刊名缩写><年份>`；
   同刊同年多篇加 `a/b` 后缀。

---

## 8. 脚本索引（`流程1/scripts/`）

### 本科科研辅助链路（v8 新增）

| 脚本 | 功能 | 用法 |
|---|---|---|
| **`harvest_faculty.py`（v11）** | ⭐⭐⭐ **阶段 S：学部/学院官网导师抓取（唯一数据源）**。**配置驱动** —— 学校/学院/栏目/解析规则全在 `templates/学部官网源.json`，**新增学校不必改脚本**。支持两种列表模板：`star`（列表页已含方向+邮箱，如浙大生科院）/ `fetch`（须进详情页，如中科大生医部）。含跨栏目去重、职称剥离、相对 URL 补全、分隔符归一 | `uv run python harvest_faculty.py --list-units`<br>`--unit zju --out outputs/06_浙大生科院`<br>`--unit ustc:life-med` / `--stage=list\|detail\|all` / `--unit-name <栏目>` |
| ~~`harvest_mentors.py`（v8/v9）~~ | ⛔ **已弃用（v11）**：原抓**学校教师个人主页系统**，覆盖面与方向完整度均不如学部官网（实测 USTC 237 人中 130 人无方向栏位）。默认 `exit 2`，确需运行加 `--allow-deprecated` | 请改用 `harvest_faculty.py` |
| ~~`harvest_colleges.py`（v10）~~ | ⛔ **已被取代（v11）**：功能并入 `harvest_faculty.py`（并改为配置驱动）。保留用于复现 USTC 历史数据。默认 `exit 2`，确需运行加 `--allow-deprecated` | 请改用 `harvest_faculty.py` |
| **`merge_roster.py`（v10）** | ⭐ **合并 + 清洗**（仅在两源并存时用）。把学院官网方向并入 roster 再统一清洗。**必须在一次运行内按「恢复→合并→清洗」完成** —— 分多次串跑会误差累积（实测把 190 人砍到 144 人）。v11 起单一数据源场景**不再需要**本步骤 | `uv run python merge_roster.py --roster … --college … --from-backup` |
| **`make_delivery_index.py`（v10）** | ⭐ **生成《交付物索引.md》**：汇总产物清单、数据来源、方向获取途径、结构化字段覆盖率、「为什么有人没有方向」的原因分布 | `uv run python make_delivery_index.py --root outputs --topic "…"` |
| **`reviews_prepare.py`（v8）** | ⭐ **阶段 R①：综述任务包组装**。把阶段 R 的检索结果转成**与 `interpret_prepare` 逐字段同名**的任务包，使 §7.2 的并行 subagent 机制可原样套用。产物写 `.work_cdp/reviews/`，与 `interpret/`、`refs/` **互不干扰**。支持 `--fetch-abstracts` 按 DOI/PMID 从 Europe PMC 补摘要 | `uv run python reviews_prepare.py --from-europepmc .work_cdp/reviews/europepmc.json --fetch-abstracts` |
| **`reviews_build.py`（v8）** | ⭐ **阶段 R②：领域概貌汇总**。把解读卡机械合并为《领域概貌.md》：术语按**出现篇数**排序、共识标 ⭐、**争议原样并列不替用户判定**、阅读顺序按「入门→奠基→方法」排。卡不全时**如实告警**并注明报告基于部分文献 | `uv run python reviews_build.py --topic "阿尔茨海默病早期诊断"`<br>`--status` 只看进度 |

### PRISMA 系统检索链（v13 新增）

> **用户给出检索条件后，自动查该领域论文**的四步链。
> 设计参照工作文件夹 `academic-research-skills-3.22.2`：
> `deep-research/agents/bibliography_agent.md`（检索策略六要素、两轮筛选、偏斜告警）、
> `deep-research/references/systematic_review_toolkit.md`（PRISMA 2020）、
> `references/source_quality_hierarchy.md`（证据金字塔 + 掠夺性红旗）、
> `shared/query_plan.schema.json`（查询计划的可追溯字段）。

| 脚本 | 功能 | 用法 |
|---|---|---|
| **`search_plan.py`（v13）** | ⭐ **阶段 R⓪：检索条件 → 结构化查询计划**。把主题/限定词/年份/类型/OA/数据源编译成**可复现**的 JSON（含各源检索式与 sha256、纳入排除标准、provider roster）。**不联网** | `uv run python search_plan.py --topic "免疫与感染" --topic-en "innate immunity" --terms-en "NK cell,macrophage" --from-year 2021 --doc-types review --oa-only` |
| **`search_run.py`（v13）** | ⭐⭐ **阶段 R①：多源并行检索 + 归一化去重**。**复用** `skills/paper-lookup/scripts/paginate.py` 作取数层（不重写）。含**源归一化**（三源字段类型完全不同）、DOI 优先+题名兜底去重、**单源失败不影响全局** | `uv run python search_run.py --plan outputs/07_检索/search_plan.json`<br>`--sources europepmc,openalex` |
| **`search_screen.py`（v13）** | ⭐⭐ **阶段 R②：两轮筛选 + PRISMA 计数 + 质量分级**。Pass1 题录相关性（可调阈值、**保留命中词**备核）、Pass2 可得性；按元数据给**证据级别**与**红旗**；≥70% 集中度触发**分布偏斜告警**。含**摘要补齐** | `uv run python search_screen.py --raw outputs/07_检索/search_raw.json`<br>`--threshold 0.25` / `--no-backfill` |
| **`search_report.py`（v13）** | ⭐ **阶段 R③：PRISMA 报告**。流程图式计数、**各源实际发送的检索式**、纳入文献表、排除原因、偏斜告警、**六条局限声明** | `uv run python search_report.py --screened outputs/07_检索/search_screened.json` |

> **与既有阶段 R/R2 的关系**：本链产出的是**经过筛选与计数的文献集**（可追溯、可复现），
> 之后交给 `reviews_prepare.py` → 并行 subagent 解读 → `reviews_build.py` 汇总。
> 即：`search_plan → search_run → search_screen → search_report → reviews_prepare → (R2 解读) → reviews_build`。
| **`route_databases.py`（v12）** | ⭐⭐ **阶段 R：方向 → 数据库路由（选库唯一入口）**。按方向查该跑哪些库、**明确该 skip 哪些**，并生成**可直接执行**的检索命令（各库语法已分别拼好）。`--self-test` 零网络自检路由表与总表 24 领域的一致性 | `uv run python route_databases.py --domain 机器学习与深度学习`<br>`--domain 生物信息学与基因组学 --terms single-cell`<br>`--list-domains` / `--list-databases` / `--self-test` |

> **阶段 N 与阶段 R 的检索环节没有专用脚本** —— 阶段 N 是 agent 解析输入 +
> 查已有的关键词总表；阶段 R 的检索**直接复用** `skills/paper-lookup/scripts/paginate.py`
> 与 `arxiv_atom.py`。**这是刻意的**：这些环节的价值在判断与编排，
> 写成脚本反而会僵化；而**能被固化的部分已经固化**（任务包组装、汇总渲染）。

### 基础设施

| 脚本 | 功能 | 用法 |
|---|---|---|
| `logutil.py` | **统一日志**（控制台 + 文件双写，UTF-8 安全，线程安全）+ **操作流水** `journal(stage, action, **fields)` | `from logutil import get, journal` |
| **`journal_view.py`（v6）** | ⭐ **操作流水查看器** —— 回溯「这一轮到底做了什么」：按时间列出各阶段动作与参数，支持 `--stage=` / `--action=` / `--tail=` / `--json` | `uv run python journal_view.py [--stage=E] [--action=download.fail]` |
| **`selftest_consent.py`（v6）** | ⭐ **Cookie 弹窗自动关闭的离线自检**：本地 `file://` 夹具 + 真实 Edge，验证固定定位横幅 / shadow DOM / 装饰符匹配 / 不误点正文按钮 / 不重复点击。**零网络**，不碰出版社站点 | `uv run --with websocket-client python selftest_consent.py [--port=9251]` |
| `detect.py` | **页面状态唯一判定源**（ok/challenge/blocked/paywall/not-ready）+ DOI→出版社映射 | `from detect import judge, site_of_doi` |
| `store.py` | **PDF 存放/命名/去重/校验**唯一实现（v3 起默认**题名命名** + `pdf_index.json` 登记表） | `from store import existing_pdf, validate_pdf, stem_for, register_pdf` |
| `cdp.py` | CDP 客户端（端口参数化、**硬超时**、自动重连、异常分类） | `from cdp import connect, is_alive` |
| `cdp_pool.py` | **多实例池**：并行启动/探活/调度/回收 | `python cdp_pool.py {up\|status\|down\|renew}` |
| `start_edge_cdp.sh` | 启动 CDP Edge（**多实例**，最小参数档位） | `bash start_edge_cdp.sh <workdir> 9222 3 0` |
| `renew_cdp_instance.sh` | 重建**单个**实例（换全新 profile） | `bash renew_cdp_instance.sh <workdir> <port>` |

### 数据采集

| 脚本 | 功能 | 用法 |
|---|---|---|
| `orcid_extract.py` / `orcid_pages.py` | CDP 打开 ORCID、翻页抓全部成果 | `uv run --with websocket-client python orcid_extract.py <ORCID>` |
| `parse_orcid.py` | ORCID 页面文本 → 结构化 JSON | 承接上一步 |
| **`orcid_probe.py`（v4）** | ⭐ **ORCID API 身份核验**：姓名/任职/教育/关键词 + 全部 works（含 DOI）。用于把 ORCID 时间线与作者主页履历逐条比对 | `uv run python orcid_probe.py <ORCID>` |
| `fetch_crossref_all.py` | 并发拉取全部 DOI 的 Crossref 题录 | `uv run python fetch_crossref_all.py` |
| `retry_crossref.py` | 失败 DOI 串行重试（退避） | 承接上一步 |
| **`enrich_crossref_authors.py`（v4）** | ⭐ **回填 Crossref 结构化作者姓名**（family/given 分开）+ 卷期页，重算本人署名位次 | `uv run python enrich_crossref_authors.py --surname=<姓> --given=<名>` |
| **`disambiguate.py`（v4）** | ⭐⭐ **同名消歧**（三重判据：姓名精确 + 署名单位指纹 + 合作者网络）。**作者聚合普遍过度合并，此步不可省** | `uv run python disambiguate.py --surname=… --given=… --affil=… --anchor-doi=…` |
| `unpaywall_check.py` | 批量 OA 状态与 PDF 链接（仅用于判断获取难度，**不据此走直取**） | `uv run python unpaywall_check.py` |
| **`fetch_direct.py`（v7.1）** | ⭐⭐ **第一层直连获取（摘要 / 综述题录）** —— 多源自动降级（S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed），**只对白名单域名发请求（表外一律拒绝）**；缺口写 `direct_failed.json` 并**转第二层 CDP**。`--check-domain=URL` 可单独问策略 | `uv run python fetch_direct.py [--limit=N] [--check-domain=URL] [--run-cdp-fallback] [--self-test]` |
| `fetch_openalex_abstracts.py` | OpenAlex 倒排索引批量重建摘要（零风控） | `uv run python fetch_openalex_abstracts.py` |
| `build_list.py` | 生成 GB/T 7714 完整版列表（含署名位次） | `uv run python build_list.py` |

### 全文获取（**一律经 CDP**，见 § 5.1 禁令）

| 脚本 | 功能 | 用法 |
|---|---|---|
| `download_all_fulltexts.py` | ⭐⭐ **全文批量下载唯一入口**（多实例并行 + 站点熔断 + 断点续传 + 挑战宽限 + 转人工）。**OA 与付费墙同等对待** | `... --n=3 --limit=20 --delay=25-55 --manual-wait=5 --grace=35` |
| `save_pdf_via_browser.py` | 单篇 PDF 获取（4 种方法依次尝试） | `... "<landing>" "<pdf>" "<out>" [--render]` |
| `check_challenge.py` | 站点体检（挑战/封禁/可达性），返回明确退出码 | `... check_challenge.py "<URL>" --port=9222` |
| `check_access.py` | 检测本机是否具备 IP 级机构订阅授权 | `... check_access.py "<DOI>"` |
| `grab_abstracts.py` | CDP 抓出版社摘要（低频拟人化） | `... grab_abstracts.py "<DOI1>,<DOI2>"` 或 `--retry-failed` |
| ~~`fetch_oa_direct.py`~~ | ⛔ **v4 停用**：曾用于 OA 直链纯 HTTP 下载**全文**。v7.1 的分层策略只放开了**摘要/元数据**，**没有**恢复全文直连，故本脚本**继续停用** | 不再调用；保留文件仅作历史回溯。摘要直连请用 `fetch_direct.py` |

### 中译素材链（★ v4 新增）

| 脚本 | 功能 | 用法 |
|---|---|---|
| `prep_paper.py` | 原文 PDF → **按页分节**纯文本 + 图注页索引（翻译逐段对照用） | `uv run --with pymupdf python prep_paper.py "<pdf>"` |
| `crop_by_images.py` | 按页面**位图并集**裁出「图-only」主图（Nature/Cell 拼版图适用） | `... crop_by_images.py "<pdf>" "<out.json>" "<图页>" 200 8` |
| `extract_figures.py` | 抽取 PDF **内嵌位图**（Extended Data 图、单张整图），可降采样 | `... extract_figures.py "<pdf>" "<out.json>" 300 1600` |
| `jats_to_text.py` | **JATS XML → 结构化文本**（OA 全文取正文，非 OA 返回 404） | `uv run python jats_to_text.py <file.xml>` |
| `translation_build.py` | ⭐ 内容 JSON + 图形 JSON → **内联图的**中译 HTML | `uv run python translation_build.py <content.json> <figures.json> <out.html>` |

### 同名消歧与论文解读（★ v6 新增）

| 脚本 | 功能 | 用法 |
|---|---|---|
| **`scope.py`** | ⭐⭐ **同名消歧结果的唯一读取入口** —— 把 `disambiguated.json` 归结为「非本人排除名单」，**下载侧与著录侧共用**。只排除**肯定性判定**（`name-mismatch`/`affil-mismatch`），**数据缺失类一律保留** | `from scope import excluded_dois, summary` |
| **`interpret_prepare.py`** | ⭐ **阶段 H ①**：组装**自包含**单篇任务包（题录 + 摘要 + 带 `[p.N]` 页标记的正文），供 subagent 直接精读；**不联网**（全文取自本地 PDF） | `uv run --with pypdf python interpret_prepare.py [--max-chars=60000] [--no-text] [--only=DOI]` |
| **`interpret_build.py`** | ⭐ **阶段 H ④**：回收 `results/*.json` → 《`<作者名>_论文解读.md`》（时间线 / 逐篇四步 / 关键词统计 / 研究方向 / 方法范式 / **缺卡清单**） | `uv run python interpret_build.py [--families=<词族表.json>]` |

> 阶段 H 的**执行细节**（subagent 提示词模板、解读卡 schema、质量控制）
> 见 `流程1/subagent-driven-review/SKILL.md`。

### 准备阶段与文献综述网络（★ v7 新增）

| 脚本 | 功能 | 用法 |
|---|---|---|
| **`prepare_stage.py`** | ⭐⭐ **阶段 0**：收集准备阶段选择 → 写 `.work_cdp/prepare_plan.json`，并解析出**每个阶段的 run/skip 闸门**（含依赖收敛）。**只读写本地文件，零网络** | `uv run python prepare_stage.py --set k=v …` / `--defaults` / `--show` / `--check <阶段>` / `--check-all` |
| **`refs_backfill.py`** | ⭐⭐ **阶段 D2**：取作者论文的 `referenced_works` → 参考文献题录池 → 按「核心期刊 **或** 被引 > N，**优先 Review**」筛高质量；含**反向索引**（哪几篇作者论文引用了它）。带磁盘缓存与退避重试 | `uv run python refs_backfill.py [--min-citations=200] [--offline] [--self-test]` |
| **`refs_bibliography.py`** | ⭐ **阶段 G2**：作者本人论文中「核心期刊 / 第一或通讯作者 / 中科院一区或 IF>5.0」者 → 逐篇列参考文献，输出**文章 → 参考文献**两部分 md | `uv run python refs_bibliography.py [--if-threshold=5.0] [--self-test]` |
| **`refs_curate.py`** | ⭐ **阶段 H2①**：DOI/题名两级**去重** + 按作者主体研究方向**打分**（分量全公开）+ **剔除无关**（剔除项逐条留档） | `uv run python refs_curate.py [--cut=25] [--keep-top=0] [--self-test]` |
| **`refs_prepare.py`** | ⭐ **阶段 H2②**：组装**自包含**综述任务包（题录 + 摘要 + 受控标签词表）；**不联网** | `uv run python refs_prepare.py [--top=40] [--only=DOI] [--self-test]` |
| **`refs_build.py`** | ⭐ **阶段 H2③**：回收综述解读卡 → 《`<作者名>_研究方向综述.md`》（含网络角色 / 缺卡清单 / 限制申报）；**不联网** | `uv run python refs_build.py [--self-test]` |
| **`ppt_brief.py`** | ⭐ **阶段 P**：汇编 PPT 材料 + **确定性生成** `design_spec.md` / `spec_lock.md`（= 简化的 Strategist Phase）+ 调 ppt-master 初始化与校验 | `uv run python ppt_brief.py [--pages=short\|standard\|long] [--dry-run] [--self-test]` |
| **`knowledge_graph.py`（v7.2）** | ⭐⭐ **阶段 K**：三张图（overview / 词云 / 方向聚类）。Louvain 社区上色、记忆连接（有名字+带原因）、聚类解读与冲突线索检出；**SVG 优先、失败退 PNG、背景透明**；词云用碰撞避让保证「清晰展示所有字符」 | `uv run --with matplotlib --with networkx python knowledge_graph.py [--recent-years=3] [--also-png] [--format=svg\|png\|auto] [--self-test]` |
| **`pack_release.py`** | ⭐ **打包发行版**：把本流程 + 全部依赖（含 `vendor/ppt-master`）输出为一个可独立运行的目录（默认 `<仓库根>/流程1/`），并把文档里的 `流程1/` 路径**同步重写成新目录名**（幂等，重复打包不会变成 `流程11/`）；产出 `PACKAGE_MANIFEST.json`（逐文件 sha256）与 `README-PACKAGE.md` | `uv run python pack_release.py [--dest=路径] [--slim] [--force] [--no-vendor] [--dry-run] [--self-test]` |
| **`selftest_pipeline.py`** | ⭐⭐ **端到端自检**：在临时工作目录里跑一遍 阶段 0 → D2 → G2 → H → H2 → P，验证**脚本之间的契约**（闸门收敛、字段命名、产物格式、外部校验器）。**v7 起为必跑项** —— 它挡下过 4 类静态看不出的缺陷（§9.5-G） | `uv run python selftest_pipeline.py [--offline] [--limit=4] [--keep] [--verbose]` |

> 阶段 H2 的**执行细节**（综述网络版 subagent 提示词、13 字段 schema）
> 见 `流程1/subagent-driven-review/SKILL.md`；
> 阶段 P 的**执行细节**（简化 Strategist 的依据、质量门、检查清单）
> 见 `流程1/ppt-integration/SKILL.md`。

### 命名 / 翻译 / 收尾（★ v3 新增）

| 脚本 | 功能 | 用法 |
|---|---|---|
| `rename_to_title.py` | ⭐ **PDF 重命名为论文标题**（存量批量改名 + 索引登记；同名同大小自动合并） | `uv run python rename_to_title.py [--dry-run] [--keep-dois]` |
| `render_html_pdf.py` | ⭐ **中文翻译 HTML → PDF** + CJK 嵌入校验（退出码 0/1/2） | `uv run --with pypdf python render_html_pdf.py <in.html> <out.pdf>` |
| **`verify_pdfs.py`（v4）** | 全文 PDF 完整性校验 + **页数合理性**（识别补充材料冒充正文） | `uv run --with pypdf python verify_pdfs.py` |
| **`inventory_build.py`（v4）** | ⭐ 生成《全文清单.md》：逐篇状态/来源/页数/未获取原因 + 分阶段覆盖率 | `uv run --with pypdf python inventory_build.py` |
| `render_one.sh`（工作目录 `.work_cdp/`） | 单篇渲染包装（最简命令，绕开复杂命令行判定） | `bash .work_cdp/render_one.sh <in.html> <out.pdf>` |
| `render_rest.py`（工作目录 `.work_cdp/`） | 幂等批量渲染未产出的中译 PDF | `python .work_cdp/render_rest.py` |

**模板**：`templates/` 内含 GB/T 7714 著录模板与中文翻译 HTML 模板（含 CJK 排版 CSS）。
**中译 HTML 样式样板**：`.work_cdp/translations/` 下任一篇（结构 = `meta 区 + 摘要框 + h2/h3 章节 + .figcap 图注 + .refnote`）。

---

## 9. 故障修复记录（2026-09-11）

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | **脚本死机** | `cdp.py:call()` 用 `while True: ws.recv()`，只靠 socket 超时兜底；浏览器退出或 PDF 导航销毁目标时永久阻塞 | 每次调用带绝对截止时间，超时抛 `CDPTimeout`；`navigate()` 全程有界 |
| 2 | **误触发 CAPTCHA** | `patient_load()` 以 `textlen < 6000` 判为 CAPTCHA → 勘误页/短讯页/未渲染完页面全部误判，弹窗空等 **15 分钟** | `detect.judge()` 精确判定；文本长度不再是判据；`--manual-wait` **默认 0（关闭）** |
| 3 | **泛词误报封禁** | `BLOCK_MARKERS` 含裸词 `封禁` / `forbidden` / `blocked by` / `has been blocked` — 生物医学正文（forbidden clone、blocked by inhibitor）必然命中 → 触发「全局中止」 | 改用精确短语；模糊词仅「短页面 + 出现在页面前部」才算数；有 `citation_*` 元数据直接放行 |
| 4 | **单实例一崩全崩** | 单 Edge 实例退出后脚本无探活，后续全部 `WinError 10061`（batch2.log：8/8 失败） | 多实例池 + 逐篇探活 + 实例失联自动重启 + 端口轮转 |
| 5 | **重复下载 / 误删** | 去重只认 `{year}_{doi}.pdf`，对历史 `{doi}.pdf` 视而不见；校验失败时删除该路径文件（可能是旧的好文件） | `store.existing_pdf()` 兼容两种命名；`_write_pdf` 只删本次写入的文件 |
| 6 | **1 页勘误被误拒** | `min_pages=2` 把真正的 Author Correction 当空壳 | 放行「1 页且 ≥100 KB」 |
| 7 | **无日志、无法诊断** | 各脚本各自 `print`，无落盘 | `logutil.py` 统一双写 + 指针文件 |
| 8 | **`logutil` 自身死锁** | `get()` 持 `Lock` 期间构造 `Log`，`Log.__init__` 再取同一把锁 | 改用 `RLock` |
| 9 | **端口硬编码** | `cdp.py` 写死 `127.0.0.1:9222` | 全部参数化（`--port` / `CDP_PORT`） |
| 10 | **节流键错误** | 用落地 URL 前取 `site_of()`，DOI 未解析时全部塌缩成 `doi.org`，站点间隔与熔断失效 | 新增 `detect.site_of_doi()`，按 DOI 前缀定站点 |
| 11 | **`grab_abstracts --retry-failed` 排序失效** | 排序键 `int((d and 0) or 0)` 恒为 0 | 按 Crossref 年份由新到旧 |
| 12 | **挑战页过早放弃（误报需人工）** | Cloudflare JS 挑战通常几秒内自动放行；旧逻辑一命中 challenge 即判定失败。实测 OUP `mjad041`：首探 118 字符「请稍候…」→ 31s 后 56551 字符正常页 | 新增 `settle_challenge()` 宽限期（`--grace=`，默认 25s），期间自动放行则继续 |
| 13 | **同主机 WAF 计数** | 全局统一间隔时，同一主机的连续请求（如 nature.com 多篇 OA）第 3~4 次开始 403 | `fetch_oa_direct.py` 改为 **HostPacer 按主机节流** |
| 14 | **`store.exports_dir` 相对路径塌缩** | 传入 `Path(".")` 时 `.name` 为空 → 拼出 `_Exports`，导致「已下载却查不到」 | 强制 `resolve()` |
| 15 | **窗口最小化 / 开箱体验 / 权限弹窗干扰** | `--start-minimized` 导致渲染节流；新 profile 弹欢迎页；权限与通知弹窗抢焦点 | L0 日常档改为 `--window-size=1280,900` + `--no-first-run --no-default-browser-check` + `--deny-permission-prompts --disable-notifications` |
| 16 | **同意书弹窗遮挡正文** | 无对应命令行开关；部分站点点「接受」后只写 cookie 不销毁 DOM，弹窗持续遮挡 | `CONSENT_JS` 保守处理：**先点击、1.5s 后复查、仍在则强制隐藏**，并恢复被锁滚动；只在已知同意管理容器内操作 |
| 17 | **注入脚本重复堆叠** | `addScriptToEvaluateOnNewDocument` 是累加的且跨进程留存；每篇论文 connect 一次 → 100 篇堆 100 份脚本，每次导航全部执行 | 进程内 `_INJECTED` 记账 + 跨进程 identifier 落盘（`cdp_injected.json`），注册前先 remove 旧的 |
| 18 | **人工过一次验证后仍失败** | `wait_for_manual` 一检测到页面可读就立即返回，但此时正文常只渲染了一部分（实测 4005 字符），`meta[citation_pdf_url]` 还没出现 → 误报 `no-pdf-link` | 验证通过后**再等正文稳定**（`settle` + 重试）再抽取 PDF 链接 |
| 19 | **对国内不可达站点反复重试** | SSRN（`10.2139/*`）在中国大陆 IP 段网络层不可达，与风控无关，重试永远失败 | 固化 `detect.CN_INACCESSIBLE` 跳过表，选待办时直接排除并在清单注明 |

---

## 9.1 故障修复与改造记录（2026-09-13，v3）

> 来源：薛天案例（77 篇论文 / 75 篇全文 / 75 篇中译 / 1110 页）实战。

| # | 类别 | 缺陷 / 需求 | 修复 / 实现 | 涉及文件 |
|---|---|---|---|---|
| 1 | 命名 | PDF 以 DOI 命名，人工无法辨识 | 新增 `title_slug()`（Windows 安全化 + 保留名规避）、`stem_for()`；下载即题名命名 | `store.py`、`fetch_oa_direct.py`、`download_all_fulltexts.py` |
| 2 | 命名 | 改名后「按 DOI 去重」失效 → 会重复下载 | 新增 `.work_cdp/pdf_index.json` 登记表 + `register_pdf()`；`existing_pdf()` 四级回退（DOI→登记表→题名→扫描） | `store.py` |
| 3 | 命名 | 存量文件需批量改名 | 新增 `rename_to_title.py`（`--dry-run` 预览、同内容自动合并） | 新增脚本 |
| 4 | 反爬 | 只有页面文本可判，无法确认真实 HTTP 状态 | 探针新增 `navStatus`（Performance API `responseStatus`） | `detect.py` |
| 5 | 反爬 | 无法识别具体反爬厂商 | 新增 `botVendor`：Cloudflare Turnstile / DataDome / PerimeterX / Imperva / hCaptcha / reCAPTCHA / GeeTest | `detect.py` |
| 6 | 反爬 | **Ray ID / 429 被误判为 IP 封禁 → 整轮中止**（实测 PNAS 一次 403 停掉全部批次） | 新增 `SOFT` 软拦截层级 → 只判 `challenge`（站点级熔断），新增规则 5b/5c/5d | `detect.py` |
| 7 | 自动化 | Cookie 弹窗容器覆盖不足、iframe 型弹窗无法处理 | `CONSENT_JS` 扩至 40+ 容器；跨域 iframe 隐藏、同源 iframe 深入点击；加载/滚动/点击后补扫；新增 `dismiss_consent()` | `cdp.py`、`download_all_fulltexts.py` |
| 8 | 提速 | 3 实例逐个启动，总耗时 = N 个之和 | 两级启动：并行拉起 + 统一探活 → **15s 完成 3 实例** | `start_edge_cdp.sh`、`cdp_pool.py` |
| 9 | 提速 | 探活轮询 1s 粒度太粗；页面 settle 固定等待 | 轮询降至 0.25s；`navigate()` 改自适应 settle（正文/元数据就绪即返回） | `cdp_pool.py`、`cdp.py` |
| 10 | 提速 | 后台任务拖慢冷启动 | 新增 **L3 极速档**（关闭后台网络/组件更新/同步/默认应用等，**不改页面指纹**） | `cdp_pool.py`、`start_edge_cdp.sh` |
| 11 | 验证 | CAPTCHA 弹窗为模态 MessageBox，无人值守时永久残留 | 改 `WScript.Shell.Popup`（**TTL 自动关闭**），实测 0.01s 非阻塞返回 | `download_all_fulltexts.py` |
| 12 | 验证 | 弹窗出现但 Edge 窗口在后台，人看不到 | 新增 `activate_edge_window()`（PowerShell `AppActivate` 抬升窗口）+ 等待中周期置前 | `download_all_fulltexts.py` |
| 13 | 验证 | 等待循环中一次探针异常即中断 | `detect.judge()` 包 try/except，异常仅告警继续；保持硬上限 | `download_all_fulltexts.py` |
| 14 | ⛔禁令 | 曾对验证站点做直连探活（违规） | `BROWSER_ONLY` 扩充 `cell.com`/`elsevier.com`/`sagepub.com` 等；**立「直连禁令」**（README 独立章节 + § 4 阶段 E + 检查清单） | `fetch_oa_direct.py`、README、SKILL |
| 15 | 翻译 | 缺 HTML→PDF 的渲染与校验工具 | 新增 `render_html_pdf.py`（含 CJK 校验、退出码语义）+ `render_one.sh` | 新增脚本 |
| 16 | 收尾 | 缺清单/索引产物 | 新增 `make_inventory.py`（《全文清单.md》）、`render_rest.py`（幂等补渲染）、`<作者>_交付物索引.md` | 新增脚本 + 工作目录产物 |
| 17 | 质量 | 抓到的是**补充材料/图注页**而非正文（AHA 案例：2 页通过校验） | 下载后校验页数；残缺件改名 `【补充材料】…` 保留并重抓正文 | 工作流规范 |
| 18 | ⛔禁令 | 需评估「鼠标事件自动过验证」是否可行 | 实测取证（`test_input_events.py`：CDP `isTrusted=True` vs JS `False`）→ **立「鼠标自动化禁令」§ 5.1**（禁止清单 6 条 + 允许范围 + 三条依据 + 违规处置）并同步 README | SKILL § 5.2、README、`test_input_events.py` |

---

## 9.2 故障修复与改造记录（2026-09-14，v4）

> 来源：郭傲（Ao Guo，中国科大生医部）案例实战 —— 15 篇论文 / 14 篇全文 / 7 篇中译。

| # | 类别 | 缺陷 / 需求 | 修复 / 实现 | 涉及文件 |
|---|---|---|---|---|
| 1 | ⛔**缺陷（严重）** | **CAPTCHA 弹窗链完全未触发**：`download_all_fulltexts.py` 中 `load_landing()` 写的是 `if max_wait > 0 and wait_for_manual(...)`，而 `--manual-wait` 默认 `0.0`，传 0 时该门恒为假 → `notify_user` 弹窗、`activate_edge_window` 窗口置前、等待轮询**整条链都不会被调用**，遇挑战静默跳过（日志只留 `失败: challenge:title`） | ① 本流程**一律显式传 `--manual-wait=N`（N>0）**；② 文档在 §0 / §4 阶段 E / §8 三处标注该坑与正确用法；③ 弹窗链已单独自检（`WScript.Shell.Popup` TTL 自动关闭实测 8.17s 返回；Edge 窗口 `AppActivate` 可达） | `SKILL.md` §0/§4E/§8/§9.2；使用侧命令 |
| 2 | ⛔**禁令** | 需把「全部走 CDP」定为最高优先级，禁止自编脚本从 HTTP 直链获取 | 新增 **§5.1「CDP 强制抓取禁令」**（禁止清单 5 条 + 允许范围 + 四条依据 + 违规处置）；**移除「OA 直链纯 HTTP」这一级**；`fetch_oa_direct.py` 停用；§3.1「能走 API/纯 HTTP 的绝不走浏览器」铁律改写为「元数据走 API、内容走 CDP」 | `SKILL.md` §3.1/§4E/§5.1/§8/§10、README、`run_all.sh` |
| 3 | 数据质量 | **作者聚合过度合并**：OpenAlex 单一 author id 混入无线通信、有机化学、微电网、聚合物、空间物理等多个同名者；Europe PMC 同样混入 5+ 位同名者 | 新增 `disambiguate.py`：三重判据（姓名精确匹配 → 署名块单位指纹 → 合作者网络）。实测在郭傲案例中把 273 条候选收敛为 15 篇，**零误收**；另新增 `orcid_probe.py` 做身份核验 | 新增 `disambiguate.py`、`orcid_probe.py` |
| 4 | 数据质量 | **姓名解析出错**：靠空格猜「哪段是姓」→ `Jie Yu` 误成 `Y JIE`、`Dongming He` 误成 `DONGMING H`；Europe PMC 只给缩写，无法判定共同第一作者 | 新增 `enrich_crossref_authors.py`：从 Crossref 取**结构化** `family`/`given` 存为 `authors_struct`，著录时不再猜；并据此发现 Sci Rep 2017 实为**三人共同第一作者**（Li/Guo/Xie），修正了上游数据的两人说法 | 新增 `enrich_crossref_authors.py` |
| 5 | 数据质量 | **「无单位字段」被误判为「非本人」**：Europe PMC 部分老记录缺 affiliation，若直接按「单位不匹配」丢弃会漏收真论文 | `disambiguate.py` 把「单位不匹配」（**排除依据**）与「署名块无单位字段」（**数据缺失，待复核**）分开处理，并在输出中显式提示 | `disambiguate.py` |
| 6 | 中译 | 缺「原文 PDF → 逐段可译文本 + 主图图像」的素材链，翻译时无图可嵌 | 新增 `prep_paper.py`（按页分节 + 图注页索引）、`crop_by_images.py`（位图并集裁图-only 主图）、`extract_figures.py`（抽内嵌位图）、`jats_to_text.py`（OA 全文 XML→文本） | 4 个新脚本 |
| 7 | 中译 | 中译 HTML 需把原图内联，否则 headless 渲染断链 | 新增 `translation_build.py`：内容 JSON + 图形 JSON → 单文件自包含 HTML（图片 base64 内联）；实测与手工产出**字节等价**（728 KB / 14 页 / CJK 846） | 新增 `translation_build.py` |
| 8 | 收尾 | 缺全文完整性 + 页数合理性的独立校验；缺可移植的《全文清单》生成器 | 新增 `verify_pdfs.py`（校验 + 页数偏少告警）、`inventory_build.py`（分阶段覆盖率 + 未获取原因） | 2 个新脚本 |
| 9 | 提速 | 出版商 CDN 图片跨域，`fetch` 被 CORS 拒绝（`cdn.ncbi.nlm.nih.gov` 与文章页不同源） | 改为**从原文 PDF 直接裁剪**主图（位图并集），绕开跨域；记入 §5 避坑表 | `crop_by_images.py` |
| 10 | 环境 | 本机 TLS 链被中间设备重签 → NCBI 端点证书校验失败；`curl` 报 `CRYPT_E_REVOCATION_OFFLINE` | **不采用**关闭证书校验（已按安全策略拒绝）；改用 Europe PMC REST（证书链正常）替代 NCBI 检索，取 PMCID 时走浏览器 | 工作侧处置，见 §5 避坑表 |

**v4 新增/变更脚本一览**

| 类别 | 脚本 |
|---|---|
| 新增 | `orcid_probe.py`、`disambiguate.py`、`enrich_crossref_authors.py`、`prep_paper.py`、`crop_by_images.py`、`extract_figures.py`、`jats_to_text.py`、`translation_build.py`、`verify_pdfs.py`、`inventory_build.py` |
| 停用 | `fetch_oa_direct.py`（违反 CDP 强制抓取禁令，文件保留仅作历史回溯） |
| 文档变更 | 阶段 E 由「三级优先级」改为「统一走 CDP」；§3.1 抓取铁律改写；新增 §5.1 禁令；§10 检查清单同步 |

---

## 9.3 脚本去硬编码记录（2026-09-14，v5）

> 用户要求：**文档 / 示例中的案例硬编码可保留（须注明）；可执行脚本中的硬编码信息必须全部删除。**

| # | 文件 | 原硬编码内容 | 处置 |
|---|---|---|---|
| 1 | `orcid_extract.py` | `URL = "https://orcid.org/0000-0002-1150-3709"`（写死 ORCID） | 改由 `PAPER_ORCID` 或 `argv[1]` 传入；缺失显式报错退出；兼容完整 ORCID URL |
| 2 | `orcid_pages.py` | 同上 | 同上 |
| 3 | `parse_orcid.py` | `SKIP` 集合内含作者名（`"Bo Liu"`） | 移出静态集合，改由 `PAPER_AUTHOR_NAMES`（竖线分隔）注入 |
| 4 | `check_access.py` | `TEST_DOI` 默认写死一篇 ACS 论文 DOI | 改为必须由 `argv[1]` 或 `PAPER_TEST_DOI` 提供 |
| 5 | `build_list.py` | `clean_title()` 内写死 `"Bo Liu via ResearcherID"` 与 20+ 刊名噪声表 | 改正则（任意署名 + `via ResearcherID`）；刊名噪声改用**记录自身的 journal 字段**判定，不再维护刊名表 |
| 6 | `build_list.py` | 无 DOI 记录块写死「跳过某篇 graphene 论文」+ 刊名 + 作者 `Liu, Bo` | 通用化：不再跳过特定论文；`journal` 取自记录；作者留空并标注（**不臆造作者名**） |
| 7 | `build_list.py` | `liubo_pos()` 内写死 `"liu, bo"` 命中判定 | 改名 `my_position()`，命中词由 `PAPER_NAME_TOKENS`（或 `PAPER_SURNAME`+`PAPER_GIVEN`）给出；未提供则跳过该项标注 |
| 8 | `build_list.py` | `PERIODS` 写死上一案例的完整履历（硕士校/博士校/博后组/建组年） | 改由 `.work_cdp/periods.json` 或 `PAPER_PERIODS`（JSON）提供；两者皆无则**不分组**，仅按年平铺 |
| 9 | `disambiguate.py` / `enrich_crossref_authors.py` / `orcid_probe.py` | 用法示例与注释里的案例姓名、真实 ORCID、锚点 DOI | 全部替换为 `<Surname>` / `0000-0000-0000-0000` 等占位符；技术结论保留（如「不可只用姓氏」） |
| 10 | `run_all.sh` | 用法示例含真实姓名 + ORCID | 替换为占位符 |

**验证**：`grep` 全脚本零命中（姓名 / 真实 ORCID / 具体 DOI）；`py_compile` 全部通过。

**注**：`README.md`、`SKILL.md`、`§9` 故障台账与 `示例数据/*.json` 中的案例信息**保留**，
并在 README / SKILL 顶部加「案例记录，非脚本默认值」说明框。日志（`*.work_cdp/logs/`）属运行实录，同样保留。

---

## 9.4 流程复核与加固记录（2026-09-17，v6）

> 来源：对流程目录的一次完整复核 —— 逐脚本核对可执行性与前后契约，
> 并落实四项用户指示（用本流程 / CDP 弹窗治理 / Cookie 自动关闭 / 论文总结规范）。
> **下列缺陷均已实测复现，修复后亦经实测验证。**

### A. 产出目录命名（用户指示）

| # | 项 | 处置 |
|---|---|---|
| 1 | 全文目录需求为 `XX_paper_Exports`（XX=作者名），而全流程用 `<作者名>_Exports` | `store.exports_dir()` 改为**规范名 `<作者名>_paper_Exports`**；历史 `<作者名>_Exports` 在**规范目录不存在且自身含 PDF** 时自动沿用（不迁移、不丢文件），并提示一次 |
| 2 | 目录名取 `wd.name`，工作目录名 ≠ 作者名时会错 | 名字来源改为 `PAPER_EXPORTS_NAME` → `PAPER_AUTHOR` → `wd.name`，并做 Windows 非法字符清洗 |
| 3 | `run_all.sh` 与文档中的 `_Exports` | 同步更新（含 §0 / §7 / 阶段 I） |

### B. CDP 启动参数：弹窗治理（用户指示 2）

| # | 缺陷 | 处置 |
|---|---|---|
| 4 | 流程会 `taskkill /F` 强杀实例，**同 profile 下次启动必然弹「恢复页面」气泡**，遮挡内容、抢焦点（影响人工过验证码） | L0/L2/L3 补 `--disable-session-crashed-bubble` + `--hide-crash-restore-bubble`（两代开关名都带，未知开关被静默忽略） |
| 5 | 「页面无响应」等错误对话框为**模态**，会卡住导航 | 补 `--noerrdialogs` |
| 6 | 「重新提交表单」模态对话框会**直接阻断导航**（`navigate()` 有重试逻辑） | 补 `--disable-prompt-on-repost` |
| 7 | 全新 profile 首启可能停在搜索引擎选择页 | 补 `--disable-search-engine-choice-screen` |
| 8 | 英文论文页必现「是否翻译此页面？」气泡 | 补 `--disable-features=Translate,TranslateUI` |
| 9 | `cdp_pool.launch_one()` 崩溃重启**写死 `level=0`**，与启动档位不一致 | 新增 `PAPER_CDP_LEVEL`（`DEFAULT_LEVEL`），启动/重启/`renew` 同源；L1 极简档**刻意不含**弹窗抑制，保留 A/B 对照能力 |
| 10 | Python 与 Shell 两处档位定义易漂移 | `cdp_pool.LEVELS` 与 `start_edge_cdp.sh` 的 `BASE/DIALOG/FAST_ARGS` 互相注明「必须同步」，并实测 L0/L2/L3 参数一致 |

### C. Cookie / 同意书弹窗（用户指示 3）

| # | 缺陷（**均已实测复现**） | 处置 |
|---|---|---|
| 11 | ⛔ **固定定位弹窗永远关不掉**：`visible()` 用 `el.offsetParent !== null` 判可见，而规范规定 **`position:fixed` 元素的 `offsetParent` 恒为 null** —— Cookiebot(`#CybotCookiebotDialog`)、Osano(`.osano-cm-window`)、Didomi(`#didomi-host`)、`.cky-consent-container` 等**本身就是 fixed** 的横幅全被判为「不可见」而从不处理 | 改用 `getBoundingClientRect()` 几何尺寸判定；新增夹具实测（`selftest_consent.py`）**从失败转为通过** |
| 12 | ⛔ **只处理每个选择器的首个命中**：同页多个同意组件（横幅+偏好中心+浮标）时后面的全漏 | 改 `querySelectorAll`（每选择器上限 5、总上限 60，防性能劣化） |
| 13 | ⛔ **shadow DOM 内的横幅查不到**：Usercentrics v2 等把 UI 渲染在 shadow root 内，`document.querySelector` 不可见 | 新增 `roots()` 收集 shadow root（3 秒缓存、上限 24 个）后逐个查询；并深入容器自身的 shadowRoot 找「接受」按钮 |
| 14 | ⛔ **同一按钮被重复点击**：站点若「接受按钮只写 cookie 不销毁 DOM」，每次巡查都再点一遍；且点击派发 `click` 事件又触发补扫 → **自放大**（实测 2 秒内同按钮被点 3–4 次） | 用挂在 `window` 上的**不可枚举** `WeakSet` 记账，同文档内每个容器**至多点一次**；SPA 重渲染出的新元素仍会正常再点 |
| 15 | `dismiss_consent()` **恒返回 False**（IIFE 无 `return`），调用方无法据其判断 | IIFE 末尾 `return touchedNow`，返回本次实际是否处理到弹窗 |
| 16 | 接受按钮文字表覆盖不足；带装饰符的标签（`Accept all →`）匹配不上 | 扩充（`Accept cookies` / `Agree and continue` / `同意并继续` / `确认` 等）；匹配前剥掉尾随 `→»·` 等符号。**刻意不含** reject/decline —— 不替用户做拒绝决定 |
| 17 | 巡查仅 14.4s，晚弹窗（>15s）会漏 | 延长到 30s；容器表补偏好中心（`#onetrust-pc-sdk` / `#qc-cmp2-ui` / `.cky-preference-center` / Moove / HubSpot / Borlabs / Drupal EU 等）40+ 项 |
| 18 | `try_fetch_pdf` / `try_render` **重新导航后不关弹窗**；`try_render` 更严重 —— 横幅文字会计入 `innerText`，**把不足 15000 字符的正文「凑」过门槛**，并把横幅打印进成品 PDF | 两处导航后补 `dismiss_consent()`，且在**量正文长度之前**执行 |
| 19 | 无任何自动化手段可验证注入脚本（语法错误会被 `_inject()` 与 `dismiss_consent()` 双双静默吞掉，表现为「弹窗从来不关」却不报错） | 新增 **`selftest_consent.py`**：本地夹具 + 真实 Edge 的**离线**自检（零网络），6 项断言全通过 |

### D. 同名消歧未接入下游（**质量约束落空**）

| # | 缺陷 | 处置 |
|---|---|---|
| 20 | ⛔ `disambiguate.py` 产出 `disambiguated.json`，但**没有任何脚本读取它** —— 消歧只打印给人看，下载与著录仍直接用 `crossref_all.json`，同名他人论文照样被下载、照样进最终列表。SKILL 把消歧列为「不可省」，实际等于白跑 | 新增 **`scope.py`** 作为唯一读取入口；`download_all_fulltexts.py`（省下无谓抓取）与 `build_list.py`（不进正文列表）**共用同一判据** |
| 21 | 若直接按 `confirmed` 做**白名单**：disambiguate 的候选语料来自 `.work_cdp/*.json`（Europe PMC 等），与 `crossref_all.json` 未必同源 —— 语料一旦不一致会**整批误删本人成果**（不可接受的失败模式） | 改用**黑名单**语义（只删 `dropped`），失败方向偏保守：多留可人工剔除，少留无从恢复 |
| 22 | `dropped` 里**混着两类性质完全不同的记录**：「单位不匹配」（肯定性判定）与「署名块无单位字段」（**数据缺失，非否定证据** —— 脚本自己就警告不要当他人丢弃） | `disambiguate.py` 的 `dropped` 条目补 **机器可读的 `code`**；`scope.py` 只排除 `name-mismatch`/`affil-mismatch`，`no-affil-data` **一律保留**并在报告显式列出待核。旧文件无 `code` 时按措辞兜底，宁可少排除。另留逃生阀 `PAPER_SCOPE_INCLUDE_ALL=1` |
| 23 | 需求「非本人论文要在输出中注明并自动忽略」，此前两条都没做到 | `build_list.py` 新增文末「**已排除的同名他人论文**」表（DOI/题名/年份/**排除理由**）+ 判据参数溯源 |

### E. 脚本契约断裂（**全新工作目录跑到该步必然失败**）

| # | 缺陷 | 处置 |
|---|---|---|
| 24 | ⛔ `enrich_crossref_authors.py` 默认读 `.work_cdp/master.json`，而**本目录任何脚本都不产出该文件**（它是早期案例的遗留名）→ 全新工作目录必然「找不到输入文件」退出 | 默认输入改为 `crossref_all.json`（本流程实际总表），`master.json` 作回退 |
| 25 | ⛔ 同脚本要求输入是**列表**，而 `crossref_all.json` 是 `{doi: 记录}` 字典 → 直接报「输入应为记录列表」 | 新增 `_records()` 统一两种顶层形状（返回**原对象引用**，就地回填后按**原形状**写回） |
| 26 | ⛔ 同脚本把 `authors` 写成**字符串列表**，而 `build_list.py` 的 `author_name()` 做 `a.get("name")` → **`AttributeError: 'str' object has no attribute 'get'`，整条著录链崩栈** | 改为写**字典列表** `[{family, given, name}]`，与 `fetch_crossref_all.py` 的契约一致；`build_list.py` 侧再加一层防御（两种形状都能读） |
| 27 | ⛔ 同脚本写 `volume`/`pages`，而 `build_list.py` 读 `vol`/`page`/`article_number` → 卷期页**静默丢失** | 两套键名都写（新键 + Crossref 原始键），历史工作目录仍可读 |
| 28 | `inventory_build.py` 同样**只认 `master.json`** → 阶段 I 必然硬失败 | 同 24 的处置；另移除其中**写死的上一案例履历**（「2011–2018 早期 / 2022–2023 博后期 / 2025– 独立期」），改由 `periods.json` 驱动，未配置则**不分组** |
| 29 | `run_all.sh` 调 `enrich_crossref_authors.py` 时**不传 `--surname/--given`**，署名位次回填静默失效 | 脚本侧支持回退 `PAPER_SURNAME`/`PAPER_GIVEN`；`run_all.sh` 透传 |
| 30 | `run_all.sh` 的**同名消歧步骤只 `echo` 一行提示、从不实际执行** | 改为真调用：由 `PAPER_SURNAME`/`PAPER_GIVEN`/`PAPER_AFFIL_TOKENS`/`PAPER_ANCHOR_DOI` 组装参数；缺项时**明确告警并说明后果**，不静默跳过 |
| 31 | `build_list.py` 只产出 `<作者名>_论文列表_完整版.md`，而需求的文件名是 `XX_论文列表` | 一次写两份：规范名 + 历史名 |
| 32 | `build_list.py` 著录出现 `et al..`（`gbt_authors` 自带句点，模板又补一个）→ 不符合 GB/T 7714 | 拼接前 `rstrip('.')` |
| 33 | `build_list.py` 只包装 `sys.stdout`，其 stderr 提示在 GBK 控制台输出 `\uXXXX` 乱码（§5 已记录该类问题） | stderr 同样包 UTF-8 容错 |
| 34 | `fetch_oa_direct.py`（已停用）的守卫**只在 `__main__` 生效** —— `import` 后调 `main()` 即可绕过，而它一跑就是**违反最高优先级禁令**的纯 HTTP 直取全文 | 抽出 `_refuse()`，`__main__` 与 `main()` **两条路径都必须经过**（均实测退出码 3） |

### F. 阶段 H 论文解读（用户指示 4）

| # | 项 | 处置 |
|---|---|---|
| 35 | 解读规范（方法论/已验证结论/二八关键信息/量化提升四步）散落在需求里，未固化 | 写入 §4 阶段 H（H.1–H.4），并在子流程手册 § 6 落成**固定 schema**，字段不得增删 |
| 36 | 论文数多时单上下文读不完（20 篇 ≈ 40 万字符），硬塞会导致后半程质量断崖 | 引入**并行 subagent**：`interpret_prepare.py` 预先把「题录+摘要+带页标记的正文」固化成自包含任务包，subagent 拿到即读，**不联网、不解析 PDF** |
| 37 | 汇总靠手写，缺卡会被静默略过 | `interpret_build.py` 固定输出六节（含**缺卡清单**与「仅摘要/已截断」如实申报）；关键词统计给出**语义词族合并** + **单条目频次**两张表；词族表由 `templates/关键词词族模板.json` 驱动，**无配置则只出单条目，不臆造合并关系** |
| 38 | ⛔ `subagent-driven-review/SKILL.md` 是**来源不明的第三方英文技能**，与本流程直接冲突：教模型 `Fetch full text (try PMC, then Unpaywall)`（**违反 CDP 强制抓取禁令**）、引用四个**并不存在**的技能、使用 `papers-reviewed.json`/`SUMMARY.md` 等流程外产物约定 | 按本流程**整体重写**为阶段 H 子流程手册（`name: author-paper-interpretation`），保留有价值的「并行 subagent + 批次间合并」内核，其余全部替换为本流程契约；并在文件头列明「与上一版的差异」 |

### G. 流程权威性（用户指示 1）

| # | 项 | 处置 |
|---|---|---|
| 39 | 文档无「必须使用本流程、不得擅自更改」的明确要求，模型可能自创步骤或绕过脚本 | `SKILL.md` 顶部新增**最高优先级硬性要求**四条（使用本流程 / 不得擅自变动 / 变更须先征得同意并登记 / 红线与违规处置），并明确**判定顺序**：`SKILL.md` > 子流程 > 外部资料 |
| 40 | 流程内出现来源不明的同类 `SKILL.md` 时无处置原则 | 同上第 4 条：**一律以本流程为准**，不混用其步骤与产物约定 |

### H. 实跑暴露：ORCID 3.x 改版导致成果抓取失效（2026-09-18，郭傲案例）

> 本节缺陷**不在静态复核范围内** —— 只有真正跑一遍才会暴露：
> 脚本语法、参数、契约全部正确，是**外部站点改版**让它们静默返回 0 条。

| # | 缺陷 | 证据 | 处置 |
|---|---|---|---|
| 41 | ⛔ **成果卡片元素名已变**：`orcid_extract.py` / `orcid_pages.py` 都在数 `app-work-statement`，而 ORCID 3.x 改版后是 **`app-work`**（外层还有 `app-work-stack` / `app-work-stack-group`）→ 永远数到 0 | 实测 DOM 标签名枚举：`["app-work-featured-stack-group","app-work-stack-group","app-work-stack","app-work"]`，`app-work-statement` 计数为 0 | 两个脚本都改为**两个元素名都认**，且只统计「有实质文本」的卡片（避免数到空壳容器而虚高） |
| 42 | ⛔ **成果面板默认折叠**：不点开则 `innerText` 里根本没有成果内容，整页仅 **6.5 KB**（只有姓名/单位/邮箱），`parse_orcid.py` 找不到 `Works (` 标记 → 解析出 0 条 | 实测 `body len: 6583`，且滚动 20s 后仍为 6583（**纯滚动不会加载**，必须点展开） | 新增 `EXPAND_JS`：点击 `expand_more` / `aria-expanded="false"` / `show more` 按钮展开面板；**用不可枚举 WeakSet 记账确保每个按钮只点一次**（`expand_more` 点第二次会把面板重新折叠回去） |
| 43 | ⛔ **轮询循环第 0 轮就退出**：旧逻辑是「没点到 Show more 且数量没变 → break」，而首轮数量必然为 0（面板未展开）→ **立即 break**，`works=0`、`cards=0` | 实测日志：`[0] works=0 clicked=False` 后循环结束 | 改为**以截止时间为准**（90s），数量为 0 时继续等；仅当「数量 > 0 且连续 3 轮不增长、且本轮没点到折叠按钮」才提前结束 |
| 44 | **两个 ORCID 抓取脚本的输入文件名不一致**：`orcid_pages.py` 产 `orcid_works_all.txt`，`orcid_extract.py` 产 `orcid_page.txt`，而 `parse_orcid.py` **只认前者** → 用 `orcid_extract.py` 抓完接不上，直接 `FileNotFoundError` | 代码比对 | `parse_orcid.py` 改为**两个文件名都接受**，都缺时给出可操作提示后退出 |
| 45 | **站点改版会让整条链断掉**：页面抓取一旦得 0 条，后续 Crossref / 下载 / 著录全部无输入 | 上述 41–43 的后果 | `parse_orcid.py` 新增**API 兜底**：页面解析出 0 条时，回退读取 `orcid_probe.json`（公共 API 结果，与前端改版无关）并转换成本流程的中间结构；同时明确告警。**已知差异**：API 无 `CONTRIBUTORS` 字段，署名位次改由 `enrich_crossref_authors.py` 从 Crossref 回填（该步本就在流程内） |

> **教训**：依赖第三方站点 DOM 的抓取脚本，**必须按「会改版」来设计** ——
> 元素选择器要给多个候选、关键内容要主动展开而非依赖滚动、
> 循环退出条件不能把「本轮为 0」当成「已经抓完」、
> 并且**必须有一条不依赖 DOM 的兜底数据源**。

### I. 二次加固：消歧机制 / 解读截断 / 命名稳定性（2026-09-18 实跑后）

> 来源：郭傲案例跑完后针对三处「机制性缺陷」的重做。**这三处都不是笔误，
> 而是设计没到位** —— 跑一次不报错，但结果不对或不可靠。

#### I-1 同名消歧机制重做

| # | 缺陷 | 后果 | 处置 |
|---|---|---|---|
| 46 | ⛔ **主路径上没有可用判据**：ORCID→Crossref 是本流程主路径，而判据①②依赖**逐作者单位**，`fetch_crossref_all.py` **压根没采集 affiliation** | 13 篇全部落到「数据缺失、无法判定」，消歧空转 | 采集 `author[].affiliation`（fetch + enrich 两处都补）。实测 Crossref 只有 2/13 存缴了单位 —— 更说明下一条必需 |
| 47 | ⛔ **缺「ORCID 溯源」判据**：DOI 出现在**作者本人维护的 ORCID 记录**里，是最强的肯定性证据，旧版完全没有这条 | 主路径无判据可用（见 46） | 新增**判据⓪ `orcid-linked`**（优先级最高）：DOI 命中 `orcid_works_parsed.json` / `orcid_probe.json` 即确认归属。实测 13/13 由此确认 |
| 48 | ⛔ `load_records()` **不认 `{doi: 记录}` 形状** | `crossref_all.json` 根本没被读进来，候选语料为空 | 支持该形状（值多为 dict 且占比 ≥60% 即判定） |
| 49 | ⛔ `author_blocks()` 只认**字符串**作者列表；Crossref 是**字典**列表 `[{family,given,affiliation}]` | 旧版把 dict 整个 `str()` 化再切词，既切不出姓也拿不到单位 | 新增形态 C：字典作者按 `family`/`given` 结构化匹配，并取其 `affiliation` |
| 50 | ⛔ **`drop` 判决会让论文凭空消失**：作者数 ≥ `large_collab` 且无合作者佐证时判 `drop`，而 drop **既不入 confirmed 也不入 review** | 代表作（如 22 位作者的 Nature）会从两处同时消失 | 改判 `review`（保留 + 说明理由）。**丢弃不可逆，大合作论文很可能是本人正当成果** |
| 51 | ⛔ 判据③（合作者网络）会**降级 ORCID 已确认的成果** | 13 篇被降为「待复核」；叠加 50 则可能直接消失 | `orcid-linked` 命中者**直接 confirmed，不参与判据③** |
| 52 | 候选语料 `glob("*.json")` 把**非题录**文件也读进来（`pdf_index` / `fulltext_state` / `cdp_injected` / `crossref_authors` …） | 候选数从 13 虚增到 16，凭空多出「无作者名单」幽灵记录 | 显式排除辅助文件 |

**修复后实测**：候选 13 → 确认 **13**（全部 ORCID 溯源）、待复核 0、排除 0、无法判定 0；
`confirmed + review + dropped == 候选数`（无记录凭空消失）。

#### I-2 正文解读截断

| # | 缺陷 | 后果 | 处置 |
|---|---|---|---|
| 53 | ⛔ 超长正文**从头部硬截** | 论文的**结论、量化数据、作者自述局限全在结尾**（Results/Discussion/Conclusion），恰好被截掉。实测 13 篇中 5 篇受影响，解读卡只能对前 6 万字符负责 | 改为**头 60% + 尾 40%**，中间以「`[中间省略 N 字符]`」标记衔接 |
| 54 | 被省略的中段**无处可查** | subagent 无法深读 | 完整文本另存 `.work_cdp/interpret/fulltext/<doi>.txt`，任务包写入 `fulltext_path` / `fulltext_chars` / `text_omitted_chars`，提示词模板同步要求「必要时分段读取」 |
| 55 | 诊断指标误导：用 `text_chars`（**正文**长度）判「摘要也为空」 | 没有全文的篇目天然为 0 → 误报「1 篇无米下锅」，实际它带着 1672 字符摘要 | 新增 `abstract_chars`，判定改为「正文与摘要**都**为空」 |

#### I-3 命名与顺序稳定性

| # | 缺陷 | 后果 | 处置 |
|---|---|---|---|
| 56 | ⛔⛔ **任务包/解读卡按「序号_DOI」命名，而序号会随排序变化** | 一次 Crossref 重采有 2 条失败未补 → `year` 为空 → 排到末尾 → **整体重编号**。重跑一次 prepare 后，13 份已产出的解读卡里 **9 份与 manifest 对不上号**；轻则整片报「缺卡」，重则**把 A 的解读卡当成 B 的结果**（build 按文件名取卡） | 文件名**只由 DOI 决定**（`<doi_slug>.json`），序号只写进 JSON 内部。重跑只覆盖同名文件，永不错位 |
| 57 | 陈旧文件静默残留 | `tasks/` 累积 22 个历史文件，人工核对时极易误判 | prepare 结尾**显式报告并清理**陈旧任务包；对 `results/` 里的**孤儿解读卡**只告警不删（那是 subagent 的劳动成果，可能仍有效） |
| 58 | 题录缺 `year` 无任何提示 | 排序/阶段分组静默错乱（正是 56 的触发条件） | prepare 开头检测并告警，提示先跑 `retry_crossref.py` |

> **一句话教训**：**凡是会被下游按名字引用的产物，命名就必须由内容决定，
> 不能由「第几个」决定** —— 顺序是易变的，内容不是。

### G. 端到端自检暴露的缺陷（`selftest_pipeline.py`，2026-09-19）

> 这一节的缺陷**不在静态复核范围内** —— 脚本各自 `--self-test` 全绿，
> 是**脚本之间的契约**与**缩进层级**在端到端跑的时候才暴露。
> 新增的 `selftest_pipeline.py` 就是为这类缺陷准备的。

| # | 缺陷 | 后果 | 处置 |
|---|---|---|---|
| 78 | ⛔ `--set k=v` 与 `--check X` 的**空格写法不被解析**（`_arg`/`_arg_multi` 只认 `=` 形式） | `--set output_format=pdf` **既不报错也不生效**，整个计划静默落回默认值 —— 用户明确给的选择被丢弃；`--check D2` 还会把 `True` 当成阶段名去查表 | 两个取值函数改为**两种写法都认**；`_arg` 的裸开关只在下一个 token 不是 `--flag` 时才吞它 |
| 79 | ⛔ `interpret_prepare.py` 的 manifest **没有 `slug` 字段**（只有完整 `task`/`result` 路径），而 `refs_prepare.py` 有 | 两个 prepare 脚本的契约不一致，下游按 slug 定位产物时对不上；自检直接 `KeyError` | 给 `interpret_prepare.py` 补 `slug` 字段并与 refs 侧对齐（路径随工作目录变，stem 不变） |
| 80 | ⛔ **PPT Spec 的 `Page Count` 写的是页数预算上限，而不是 §IX 实际页数** | ppt-master 的硬契约是两者必须相等；差 5 页会让 agent 要么缺页、要么**自己编 5 页内容**——正是"为凑数编内容" | `build_slides()` 先按素材实际能支撑的量生成页面清单，`Page Count = len(slides)`；素材不足时**如实少几页**并在 stdout 说明差距 |
| 81 | ⛔ §X 的 `L.append` **缩进多了一级，落进了 `for` 循环体** | §X 被写出 4 次，ppt-master 的 `validate` 报 `section 'speaker_notes' appears more than once` → 项目结构判为 invalid。**肉眼完全看不出来**（生成的 Spec 排版正常） | 缩进改回与 `for` 同级并加注释；自检增加"§I Page Count == §IX 实际 Slide 数"与"Slide 编号 1..N 连续"两条断言 |

> **一句话教训**：**「各自的自检全绿」不等于「能串起来跑」**。
> 78/79 是脚本之间的**契约漂移**，80/81 是**生成器与外部校验器之间的契约**。
> 两类都只有在端到端跑一遍、并且**拿对方的校验器去验自己的产物**时才会现形。
>
> 因此新增的 `流程1/scripts/selftest_pipeline.py` 成为 v7 之后的**必跑项**：
> `uv run python 流程1/scripts/selftest_pipeline.py`（联网 33 项 / `--offline` 18 项）。

### H. 获取策略分层（v7.1，2026-09-19）

> 来源：用户修订 ——「对于获取**原文**的需求仍然禁止直链抓取；但是对于**摘要、综述**
> 或工作文件夹下**验证可行的开源数据库**可以直接获取，减轻压力。同时若直接获取
> 尝试失败则转向 CDP 获取。」

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 82 | v4 的「一律走 CDP」是**一刀切**：连摘要也要开浏览器 | 摘要是**最大宗的浏览器开销来源**（一次解读要几十上百篇），全部压在 CDP 上既慢又无谓地消耗站点配额 | §5.1 改写为**分层策略**：原文=第二层（必须 CDP，含 OA）；摘要/综述题录/白名单开源库=第一层（允许直连）；**第一层失败转第二层** |
| 83 | ⛔ 「验证可行的开源数据库」若靠 agent **临场判断** | 判断标准每次不同，迟早有人把出版社内容站也当"开源库"直连 —— 一刀切禁令想防的正是这个 | 落成 `templates/直连白名单.json`（逐条 `domain`/`content_class`/`verified_by`/`verified_at`），`fetch_direct.py` **fail-closed 执行**：表外域名一律拒绝。收录判据三条同时满足（公开可访问 / 返回结构化数据 / skills 在用或实跑成功） |
| 84 | ⛔ 域名匹配若用**子串** | `api.crossref.org.attacker.com` 含 `api.crossref.org` → 白名单形同虚设 | 改为**后缀边界**（`host == d` 或 `host.endswith("." + d)`）。`--self-test` 里放 3 个边界陷阱用例（8/8 通过） |
| 85 | 白名单可能被逐渐"塞入"能下全文的源 | 分层策略被架空 | `--self-test` **断言**白名单不含出版社内容站、且每条 `content_class` 只能是 `metadata`/`abstract` —— 想塞全文源，自检直接失败 |
| 86 | ⛔ `fetch_openalex_abstracts.py` **无条件重写** `abstracts_openalex.json` | 先跑 `fetch_direct.py`（拿到 S2/EuropePMC 命中）再跑它，非 OpenAlex 的命中被**静默抹掉**，表现为"摘要覆盖率莫名下降" | 改为**读回既有结果再合并**，优先级：CDP > 其它直连源 > OpenAlex > 空 |
| 87 | 「失败转 CDP」若只写在文档里，没有实现 | 缺口清单没人消费，第一层失败的篇目会被当成"本来就取不到" | `fetch_direct.py` 写 `.work_cdp/direct_failed.json` 并**打印确切回退命令**；`--run-cdp-fallback` 可真调起（默认只打印 —— 它需要 CDP 实例已在跑，脚本不擅自启动浏览器） |

> **一句话教训**：**禁令的边界越模糊，执行时就越依赖临场判断，也就越容易失守。**
> v4 用"全部禁止"换来了确定性，代价是能力浪费；v7.1 改为分层后，
> 把"什么算开源库"从**判断**降级成**配置**（白名单）并用自检锁住边界，
> 才同时拿到"减负"和"不越界"。

### 实测：第一层直连的减负效果

| 验证项 | 方法 | 结果 |
|---|---|---|
| 摘要直连覆盖率 | 对示例题录前 12 篇跑 `fetch_direct.py --limit=12` | **12/12 命中**（Semantic Scholar 批量 5 + OpenAlex 7），**零 CDP 占用** |
| 耗时 | 同上 | **5.6 秒**（含磁盘缓存写入） |
| 边界拦截 | `--check-domain` 对 nature.com / 伪装的 `api.crossref.org.attacker.com` | 均**退出码 3（禁止）** |

### I. 打包发行版（v7.1，2026-09-19）

> 来源：用户要求「将修改后全流程及所有依赖打包输出为工作文件夹下『流程1』文件夹」。

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 88 | ⛔ 打包后目录叫 `流程1/`，而文档里到处写的是 `流程1/scripts/...` | 照着文档敲命令**必然敲错**；且换个人接手根本不知道以哪个为准 | `pack_release.py` 在复制时**同步重写路径**（`流程1/` → `流程1/`），并在 `PACKAGE_MANIFEST.json` 里逐个记录重写了哪些文件、各重写几次 |
| 89 | 重写若写成简单的 `流程1/` → `流程1/` | **重复打包会把 `流程1/` 改成 `流程11/`**，再打一次变 `流程111/` —— 一份包被反复打包就废了 | 用负向断言排除已是目标名的形式；`--self-test` 里专设**幂等性断言**（连打两次结果必须相同、第二次重写数为 0） |
| 90 | ⛔ 打包版若仍按「同级仓库目录」找 ppt-master | 它会在 `<流程1>/../ppt-master` 找到**宿主仓库**的那份 —— 包看起来能跑，其实依赖包外路径，一旦单独拷走就断 | `ppt_brief.find_ppt_dir()` 增加**随包副本优先**：`<流程>/vendor/ppt-master` 排在仓库目录之前；`selftest_pipeline.py` 的自检**断言项目确实写进了本流程解析到的 ppt-master**，防止回退 |
| 91 | ppt-master 整仓库含 `.git/`、`projects/`、`docs/assets/` | 把**别人的项目数据**和版本库一起发出去；体积翻几十倍 | 只带运行时（`skills/` + `requirements.txt` + 许可证/赞助文件 —— 后者是它的 `attribution_guard.py` 完整性门要求的）。`LICENSE`/`SPONSORS*.md` 实际在 `skills/ppt-master/` 下，不在仓库根 —— 列错层级会误报"源缺失" |
| 93 | ⛔ `README-PACKAGE.md` 用 `write_text` 落盘，Windows 默认把 `
` 翻成 `

` | 写出的字节与 manifest 里记的 sha256 **对不上** —— 收包人一核验就报"文件不一致"，**看起来像包被篡改过** | 加 `newline=""` 关闭换行转换（其余文件走 `write_bytes`，本就不转换）。核验脚本现报 **12995 个文件全部一致** |
| 92 | 非运行时的大目录（配图样张 44 MB / 图标库 35 MB / 音效 13 MB） | 默认全带 → 包 100 MB+；默认裁剪 → 日后要配图就缺料 | **默认完整**（用户要的是"所有依赖"），另给 `--slim` 显式裁剪，并在打包说明里写清裁了什么、裁掉后什么功能会缺 |

> **一句话教训**：打包不是 `cp -r`。**路径、依赖定位、体积取舍三件事都得显式决策**，
> 而且决策结果要能被收包的人自己核验（sha256 + 重写清单）——
> 否则"这份包能不能独立跑"永远只能靠试。

### J. 去重 / 知识图谱 / 综述策略 / PPT 接入（v7.2，2026-09-19）

> 来源：用户要求「检查所有指令文件，修改所有导致输出重复内容的流程，只保留最终完整版」；
> 「修改综述相关模块，以作者研究方向为核心绘制知识图谱（overview / 词云 / 方向聚类）」；
> 「修改综述提取环节…引用次数 200 次不作为硬性要求，重点关注近几年 CNS 综述」；
> 「修改 ppt 制作模块，增加上一步输出的图表与方向综述整理」。

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 94 | ⛔ `build_list.py` / `interpret_build.py` / `refs_build.py` 各写**两份内容逐字节相同**的交付物（规范名 + `_完整版.md` 历史别名） | 交付目录里出现成对的重复文件，人工核对时还要先确认"哪个是正的"；下游按名引用时也多一处可能失效的路径 | 三个脚本都改为**只写一个文件**（保留规范名：`XX_论文列表.md` / `XX_论文解读.md` / `XX_研究方向综述.md`）。`_完整版.md` 不再产出，文档同步 |
| 95 | ⛔ overview 图把**没有 review 卡的参考文献按"期刊名"当方向键** | 凭空造出上百个假方向节点，每个再挂 3 篇综述 —— 实测 **4 个真方向被 99 个 review 节点淹没**，图完全没法读 | 只挂到**图上真实存在的方向**；挂不上的**如实报数**（"方向无法对应未挂 N 条"），并加 `--max-dirs` / `--max-review-nodes` 上限 |
| 96 | ⛔ 记忆连接不加出度限制 | 一个高频词（实测 `porous`）把十几条连接全吸到自己身上，图上出现一把橙色扇子，其余结构全被压掉 | 按共现强度**贪心选边**，每节点出度 ≤3；并把相连的方法节点**重排到相邻角度**，弧线不再横跨整图 |
| 97 | ⛔ 布局用"每层等分一圈" | 同方向下的综述被甩到圆环对面，节点一多标签叠成一团 | 改**扇区式**：阶段占扇区 → 扇区内分方向 → 方向内分综述；标签沿**径向**外推，同角度自然散开 |
| 98 | ⛔ 方向聚类图的 y 轴未显式限定 | 只有一两个同年份的点时，matplotlib 把轴拉到 ±100 年（实测 1925–2125），图上只剩两个孤点 | 显式 `set_ylim` 到近 N 年窗口；跨度不足 3 年时撑开到 3 年 |
| 99 | ⛔ 词云用固定步长黄金角螺旋 | 字号随频次变化，大字盖住小字 —— `photocatalytic/labeling`、`porous/mof`、`simulation/reduction` 三对完全叠死，**不满足"清晰展示所有字符"** | 改**确定性阿基米德螺旋 + 矩形碰撞检测**：按频次从大到小逐词试位，首个不重叠位置才采纳；放不下的**报数**而不是硬叠 |
| 100 | ⛔ 词云的 `ax.text` 不参与 autoscale，而未设 `xlim/ylim` | `bbox_inches="tight"` 把画布撑到覆盖所有轴外文字 —— 实测产出 **473 846 000 像素**（约 200×195 英寸）的 PNG，**既打不开也不是"高分辨率"** | 显式设 `xlim/ylim`；并新增**导出后尺寸自检**：>2 亿像素即告警（"疑似元素被画到轴外"） |
| 101 | ⛔ 副标题用 `y=1.02` 且 `va` 默认 | 与 `set_title` 撞在一起，两行糊成一团（词云、方向聚类两张图都中招） | 标题加 `pad`，副标题改 `va="bottom"` 置于标题**下方** |
| 102 | ⛔ `refs_backfill.priority_of()` 在 `is_review` **写入之前**调用 | 权重最高的「综述」40 分**恒为 0**，排序退化成"按被引排" —— 恰好是本次改版要摆脱的行为 | 把刚算出的 `is_review` 带进评分调用；自检加入断言「被引 0 的近年 CNS 综述也必须入选且排第一」 |
| 103 | ⛔ 若让"近 N 年"单独构成入选理由 | 近五年的参考文献**全部**入池（实测 235 条几乎全进），"高质量"失去意义 | 明确：**近期只影响排序，不影响入选**；门槛仍是综述／核心期刊／高被引／强共引 |
| 104 | ⛔ `jats_to_text.py` 在模块级**无条件重包 stdout** | 被 `refs_backfill.enrich_reviews()` import 时，中间那层 wrapper 被 GC 关掉底层 buffer → 主脚本后续所有 print 抛 `I/O operation on closed file`（"import 一个工具模块把主流程搞挂"） | 改用**幂等** `_fix_streams()`（与 `logutil._fix_stdout` 同判据），全流程统一 |
| 106 | ⛔ 方向聚类图在**无数据时 `return []`** | 离线/未跑 H2 时第③张图**静默消失**，交付物从"三张图"变成两张 —— 读者会以为流程漏跑，或更糟：以为"这个方向近年没有进展" | 改为**照样出图**：画一张明确写「近 N 年无数据／可能原因／如实留空，不拿更早的论文冒充最新进展」的空白图，保住"三张图"的交付契约 |
| 105 | 白名单新增 Europe PMC `fullTextXML` 时若不加约束 | 等于放开"直连取非 OA 正文"，正是 §5.1 第二层明令禁止的 | 白名单新增 `content_class: abstract+oa-fulltext-xml`，并**断言含全文的源必须 `oa_only=true`** |

> **一句话教训**：**"图标出来了"和"图能读"是两回事**。
> 本次 5 个缺陷（95–99）都不是崩溃，而是**图能生成、但读不了或误导** ——
> 这类问题只能靠**真的把图看一眼**发现，脚本自检与 validate 都不会报。
> 因此阶段 K 的验收标准写成用户的原文：**清晰展示所有字符**。

### 本次实测验证记录（v6，2026-09-18）

| 验证项 | 方法 | 结果 |
|---|---|---|
| Cookie 弹窗自动关闭 | `selftest_consent.py`（本地 `file://` 夹具 + 真实 Edge，**零网络**） | **6/6 通过**：固定定位横幅被点接受 / 装饰符容忍 / shadow DOM 穿透 / 正文同名按钮未误点 / 同容器仅点一次 / 三容器最终不可见 |
| 新启动参数不破坏启动 | 同上自检内启动 L0 实例 | Edge `Edg/154.0.4258.18` **3.5s 就绪** |
| 参数档位一致性 | 解析 shell `extra_args()` 逐档打印 | L0=11、L2=12、L3=18 项，与 `cdp_pool.LEVELS` 一致 |
| 题录链契约 | 对 77 条真实 Crossref 记录跑 `enrich → build_list` | 回填 0 失败；`authors` 为字典列表；`vol/issue/page` 正确；著录 77 条、无双句点 |
| 同名消歧排除 | 构造 `affil-mismatch` / `name-mismatch` / `no-affil-data` 各一条 | **排除前 2 条、保留第 3 条**并如实申报（77→75） |
| 解读链 | `interpret_prepare`（5 篇）→ 伪造 2 张解读卡 → `interpret_build` | 报告生成正确：逐篇四步齐备、量化表成表、**缺卡 3 篇如实列出** |
| 语义词族合并 | 套用 `关键词词族模板.json` | 合并后主题强度可辨（框架化学 31 / 碳捕集 21 / 光催化 18） |
| 停用脚本守卫 | 直接运行 与 `import` 后调 `main()` | 两条路径均**退出码 3** 并打印禁令说明 |
| 全量语法 | `py_compile` 全部 `scripts/*.py` + `bash -n` 全部 `*.sh` | 全部通过 |

---

## 9.5 三次整合改造记录（2026-09-19，v7）

> 来源：用户要求把「文献综述 / PDF 操作 / 查找参考文献」三条线整合进本流程，
> 并新增准备阶段与反向参考文献网络，最后整合 ppt-master。
> 本节记录**改了哪些环节、为什么这么改、以及有意**没有**改什么**。

### A. 新增阶段 0：准备阶段

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 59 | 旧流程从阶段 A 直接开跑，「要不要翻译 / 要不要做 PPT / 要不要反向查参考文献」留到执行中途才问 | 跑了 40 分钟全文下载才发现用户只要一份 Markdown 列表；反向综述网络这类**高质量输入**则常常没人想到要做 | 新增 `prepare_stage.py` + `.work_cdp/prepare_plan.json`，把 7 个选择**前置到第 0 步**；每个阶段以 `--check <阶段>` 读闸门（0=执行 / 3=跳过） |
| 60 | 选项之间的**依赖**若靠人工推导，必然出错（`reverse_refs=no` 时 G2/H2 无输入却仍被排进计划） | 阶段跑到一半才发现没有输入 | `resolve_gates()` 做**依赖收敛**，并在 `_gate_reasons` 里写明「被谁关掉」。计划文件是单一事实源，脚本与 agent 读同一份 |
| 61 | 「输出 PDF」与「下载原文 PDF」容易混为一谈 | 用户选 markdown 后误以为不必下载全文，而全文是解读的**输入** | `output_format` 只控制**产物格式**；阶段 F 在 `markdown` 下变成 `html-only`（出中译 md，不渲染 PDF），原文 PDF 一律照下 |

### B. 新增阶段 D2：反向查找参考文献

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 62 | 只分析作者自己的论文，无法回答「这个方向的全景是什么」 | 报告止步于"他做了什么"，回答不了"他站在谁的肩上" | 新增 `refs_backfill.py`：取 `referenced_works` 倒推综述网络 |
| 63 | ⛔ 核心期刊若用**前缀匹配**，`science` 会命中 Science of the Total Environment、`cell` 会命中 Cellulose / Cells / Cellular Signalling | "高质量"定义形同虚设，非核心刊混入清单 | 改**显式清单**（Nature 是自有品牌，前缀安全；其余逐条列举）。自检里专门放了 4 个前缀陷阱用例（13/13 通过） |
| 64 | 「综述」在 OpenAlex 里**没有独立类型** | 无法实现"优先输出 review" | 用启发式判据（type + 刊名含 Reviews + 题名以 Review/Survey/Overview… 开头），并**在报告里逐条标出判定理由** —— 是启发式就如实说，不冒充权威标注 |

### C. 新增阶段 G2：代表性文献的参考文献列表

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 65 | ⛔ 中科院分区表是**商业授权数据**，无法内置全量 | 若假装能判定，会把"查不到"当成"不是一区"，**静默漏掉本该入选的论文** | 内置**种子表**（只收稳定处于一区的顶级刊）并**显式声明它不是官方分区表**；查不到写「未判定」，措辞上严格区分「未判定」与「不满足」；用户可放 `.work_cdp/journal_tiers.json` 完全覆盖 |
| 66 | ⛔ IF 无免费权威来源 | 同上 | 用 OpenAlex `2yr_mean_citedness` 作**代理值**，报告统一写「IF≈」并声明与 JCR 数值不等 |
| 67 | 代表性与反向参考文献都要**核心期刊判定** | 两处各写一份会漂移 | G2 直接 `import refs_backfill` 复用 `load_core_journals()` / `core_family()`，**单一实现** |

### D. 新增阶段 H2：去重 + 相关性整理 + 研究方向综述

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 68 | ⛔ D2 输出的是**原始候选池**（实测 138 条），混有重复、网络边缘引用、与作者方向无关的引用 | 不去噪，review 会被带偏 | `refs_curate.py` 两级去重 + 相关性打分 + 剔除 |
| 69 | ⛔ 剔除是**不可逆**的，而"词表没覆盖"与"真的不相关"在自动判定下难以区分 | 静默丢弃会不可挽回地损失文献 | 保留规则**显式公开**（含"被 ≥3 篇引用 → 强制保留，不受阈值影响"）；被剔除的**逐条写进报告与 `dropped.json` 并附理由**，可随时翻案 |
| 70 | 题名归一若只剥离**一次**冠词，`The A Study of…` 与 `A Study of…` 会判为不同 | 漏去重 | 循环剥离至不动点（真实题录里冠词叠写确实存在）。自检覆盖 |
| 71 | 合并两条重复记录时若用 `len(cited_by_sources)` 覆写 `n_sources` | 上游没带该字段时**计数清零** → 触发不了"强制保留" → **悄悄丢掉核心文献** | 取「原值 / 合并后边数」最大值 |

### E. 新增阶段 P：ppt-master 整合

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 72 | ppt-master 的 Step 4 Strategist Phase 是**两阶段阻塞式确认** + 人工撰写 spec/lock | 直接照搬会让本流程多出两轮没有信息量的交互 | 材料已由上游定型、版面走自由设计 → **确认环节没有可确认的内容**。`ppt_brief.py` 确定性生成 `design_spec.md`（I–X 全节）与 `spec_lock.md`，并新增 profile `scientific-brief.md` 把这一形态写成正式契约 |
| 73 | ⛔ 简化确认容易被顺手做成"补一个空的确认回执" | 伪造 `confirm_ui/` 状态会让审计**无法区分"用户确认过"与"没人确认过"** | 明确禁止：不启动 Confirm UI、不写回执、不生成假的 stage 状态。上游流水线是决策主体，两份 Spec 就是决策证据 |
| 74 | 简化"决策"容易被误读为简化"质量" | 手写 SVG、0-error 质检、串行导出、导出后检被跳过 | 在 profile 与子流程里都写明：**只简化决策交互，不简化任何能力或质量门**；Step 1–2 / 5 / 6 / 7 原样保留 |

### F. 解读模块准确性（受控标签 + 学科通用词族）

| # | 问题 | 后果 | 处置 |
|---|---|---|---|
| 75 | ⛔ 解读卡由**多个并行 subagent** 产出，`direction_tags` / `paradigm_tags` 自由填写 | 同一概念被拆成 `machine-learning` / `machine learning` / `ML` / `机器学习` 四条弱信号，「研究方向归纳」与「范式分析」两节**直接失真**（子流程 §8 称之为"标准漂移"） | 新增**受控标签词表**（领域 + 范式 + 网络角色三个维度、含别名映射）；词表**嵌进任务包**，subagent 必须从中取词，自造须加 `自定义:` 前缀；`refs_curate.py` / `refs_build.py` 三级归一，**未归一的保留原值并如实列出** |
| 76 | 关键词统计只有一份**空壳**词族模板（示例是上一案例的多孔材料/碳捕集） | 换作者后通用性差，常被整表忽略 | 新增 `关键词词族_学科通用.json`（由工作根目录《研究方法与关键词总表.md》24 个领域派生，**全部单 token**，可直接用）。文档明确它是**底座不是终稿**，需按作者语料裁剪 |
| 77 | 词族成员写成短语（`t cell` / `cell cycle`）会在表里**恒显示 0** | 让人误以为该概念没出现 | `interpret_build.py` 早已检出并剔除；本次在通用词表里**全部使用单 token**，并在文件说明与本文档两处强调该约束 |

### 本次实测验证记录

| 验证项 | 方法 | 结果 |
|---|---|---|
| 阶段 0 闸门与依赖收敛 | 构造 `reverse_refs=no` + `refs_bibliography=yes` + `curate_review=yes` | G2/H2 **被自动关闭**并写明「← 由 reverse_refs（上游阶段 D2 未执行）关闭」；`--check` 退出码 3 |
| 核心期刊判定（含前缀陷阱） | `refs_backfill.py --self-test` | **13/13 通过**：Science of the Total Environment、Cellulose、Cells、Cellular Signalling 均正确判为**非核心** |
| 综述判定 | 同上 | 4/4 通过（刊名 Reviews / 题名 A survey / type=review 均命中；普通研究论文不误判） |
| D2 真实数据 | 对 77 篇题录中的 4 篇跑 `refs_backfill.py` | 取到 **213 条参考文献 / 去重后 211 条 / 高质量 138 条（其中综述 25）**；全程零浏览器、零 PDF 下载 |
| G2 著录与判定 | 对同一作者跑 `refs_bibliography.py` | 代表性文献 **56 篇**；核心期刊 5、第一/通讯 2、一区 17、IF>5.0 56；**`et al..` 双句点未复现** |
| G2 诚实性 | 检查 IF 未取到时的措辞 | 报告写「未判定」而非「不满足」；分区命中数少于 IF 命中数时分别列出 |
| H2① 去重与打分 | `refs_curate.py --self-test` | 4→2 去重（含跨集合）、被引取最大值、`n_sources` 不清零；相关 77.5 vs 无关 7.5；≥3 次共引强制保留 |
| H2① 真实数据 | 对 138 条跑 `--cut=10` | 保留 134、剔除 4；剔除项**逐条列出理由** |
| H2②③ 任务包与汇总 | `refs_prepare.py --top=5` → 伪造 3 张卡 → `refs_build.py` | 报告生成正确：任务 5 / 解读卡 3 / **缺卡 2 如实列出**；未归一标签（`自定义:测试标签`）保留原值 |
| 任务包命名稳定性 | `refs_prepare.py --self-test` | 文件名只由 DOI 决定，**与排序无关**（同名不随顺序变化） |
| 阶段 P Spec 合法性 | `ppt_brief.py` → **ppt-master 自己的** `project_manager.py validate` | **通过**（唯一 warning 是 `svg_output` 为空，属 Step 6 尚未执行的预期状态） |
| 阶段 P 路径解析 | 实跑时发现误命中 `C:\Users\<用户名>\ppt-master` | 已修正为仓库内路径（`parents[1]` 而非 `parents[1].parent`）并加注释 |
| ppt-master 完整性门 | `attribution_guard.py` | 新增 profile 与 routing 行后**仍退出 0** |
| 幂等 stdout 包装 | 脚本互相 import 时复现 `I/O operation on closed file` | 新增幂等 `_fix_streams()`（与 `logutil._fix_stdout` 同判据），问题消除 |
| **打包发行版可独立运行** | 在 `流程1/` 内跑 `selftest_pipeline.py --offline` | **23/23 通过**；且自检断言阶段 P 写入了 `流程1/vendor/ppt-master`（**未回退到宿主仓库**）—— 自包含已证实 |
| **打包路径重写幂等** | `pack_release.py --self-test` | 4/4 + 幂等断言通过（连打两次不会出现 `流程11/`） |
| **端到端全流程（联网，v7.2）** | `selftest_pipeline.py --limit=3` | **47/47 通过** —— 新增阶段 K 三图产出、图谱数据落盘、报告含聚类解读与冲突线索、**图片尺寸守卫**（防 tight-bbox 撑爆）、§VIII 图谱行、图谱复制进项目 `images/` |
| **打包版可独立运行（离线）** | `流程1/` 内跑 `selftest_pipeline.py --offline` | 通过（含阶段 K；**并从 2 张图修到 3 张图** —— 见缺陷 106） |
| **打包版可独立运行（联网）** | `流程1/` 内跑 `selftest_pipeline.py --limit=3` | **47/47 通过**；阶段 P 仍写入 `流程1/vendor/ppt-master`（未回退到宿主仓库） |
| **图谱肉眼验收** | 逐张打开 PNG 检查 | overview 标签无重叠、记忆连接成短弧；词云无叠字、无 "mice" 等误导词；方向聚类轴范围正常、副标题不再压标题。**过程中发现并修掉 5 个"能生成但读不了"的缺陷（95–99）** | —— 阶段 0 闸门与依赖收敛、D2 产出、G2 两部分格式、H/H2 报告如实申报、P 过 ppt-master 自身 validate、闸门全关路径 |
| **端到端全流程（离线）** | `selftest_pipeline.py --offline` | **18/18 通过**（跳过 D2/G2/H2 的联网部分） |
| 仓库结构契约未被破坏 | `uv run --with pytest python -m pytest tests/_meta -q` | 与改动前一致：**12 项中 10 通过**；2 项失败为 `latex-posters` / `scientific-schematics` 的 `example_usage.sh` 可执行位（**Windows 文件权限产物，`skills/` 未被本次改动触碰**，属既有问题） |

> **一句话教训**：**元数据 API 与网页抓取是两套体系** ——
> 拿到"论文内容"必须走 CDP（§5.1），拿"题录/引用关系/被引次数"走 API 既快又零风险。
> 反向参考文献网络之所以能在不触发任何风控的前提下把分析半径扩大一个数量级，
> 正是因为严格守住了这条分界。

---

## 9.6 知识图谱可读性修复（2026-09-19，v7.3，刘波案例实跑后）

> 来源：刘波（中国科大，功能配合物 / MOF / 多孔材料）案例实跑。阶段 K 的三张图
> **全部生成成功、脚本自检全绿、validate 无报错**，但**逐张打开肉眼验收时没有一张达标**
> —— 正是 §9.5-J 那句「"图标出来了"和"图能读"是两回事」的又一次复现。
> **变更已事先征得用户同意**（用户选择「修脚本后重出图」）。

### 修复前的实测现象（肉眼所见，非自检可发现）

| 图 | 现象 |
|---|---|
| overview | ① `自定义:分子磁性与反铁磁耦合` / `自定义:MOF…` / `催化与反应` 三标签叠成一团；② 阶段方块压住方向标签；③ 底部「记忆连接」图例每行被 `…` 截断，**读不到「为何要一起读」**；④ **综述挂载 0 条** —— 标题承诺的「方向 → 最紧密综述」这一层整个是空的 |
| 词云 | 大字压小字：`experimental` 压死 `测试表征`/`催化`/`合成`/`figure`/`mof`/`纳米`/`金属` |
| 方向聚类 | **15 条全部落到「未分类」**（x 轴只剩一个刻度）；y=2024 那一行几十个标题叠成一坨黑线 |

### 根因与处置

| # | 缺陷 | 根因 | 处置 |
|---|---|---|---|
| 107 | ⛔ **词云中文包围盒被低估约 40%** | `bbox()` 用 `len(txt) * 0.58 * size` 估宽 —— 等于**把中文当半角**。中文实际宽约 1.0×字号，碰撞检测因此**系统性漏判**，大字压住中文小字。§9.5-J 缺陷 99 修的是「螺旋半径不够」，**没有修宽度口径**，所以本语料下仍然叠字 | 新增 `text_w()`：逐字符判宽（`ord(ch) > 0x2E80` → 1.0×字号，否则 0.55×字号），`bbox()` 改用它 |
| 108 | ⛔ **方向归类几乎全部失败** | `assign_direction()` 的关键词兜底只吃**题名**（8–15 个词），与画像重合 ≥2 的概率极低；而 `final_list.json` 506 条里**只有 40 条带 review 卡**，其余全走兜底 → 实测 15 条近 3 年文献**全部「未分类」**，x 轴只剩一个刻度 | 兜底语料改为「**题名 + 摘要 + 刊名**」；画像语料由 `methods` 扩为 `methods + key_20`。仍然**不按期刊名硬编方向**，`why` 照旧写明路径与命中词供复核 |
| 109 | ⛔ **overview 综述挂载 0 条** | 挂载逻辑只认 review 卡的 `direction_tags` → 506 条里仅 40 条有卡，其余 466 条一律 `unattached` | 无卡条目复用同一套**关键词兜底**（题名+摘要+刊名 vs 画像），且**只接受图上真实存在的方向**；仍挂不上的如实计入 `unattached`。新增 `attach_stats["how"]` 逐路径报数（实测：关键词兜底 496 / 无法归类 10，**挂上 9 条、未挂 10 条、截断 487 条**） |
| 110 | ⛔ **overview 标签互相叠压** | 每个节点按**固定**径向偏移贴标签，同一扇区内的节点（9 个综述）标签必然重合 | 新增标签避让：按同一套 CJK 宽度口径估包围盒 → 同角度**逐级沿径向外推**（最多 4 级）→ 仍放不下的**跳过并计数**（`skipped_lab`），在 stdout 如实报出，不静默丢 |
| 111 | ⛔ **记忆连接图例被截断** | `one_line(reason, 52)` —— 图例正文被截成每行都以 `…` 收尾，「两条方法为何要一起读」这一整列信息等于白放 | 去掉截断；靠图下方 `wrap=True` 自动换行 |
| 112 | ⛔ **方向聚类标签叠成一坨** | 对**每一条**文献都 `annotate(one_line(title, 34))`，而同方向同年的点全在一条水平线上 → 几十个标签在 y=2024 叠成黑线 | 只标注**每个 (方向, 年份) 内被引最高的前 3 条**，并按序号**垂直错开** |

### 一句话教训

> **§9.5-J 的缺陷 99 修得不够彻底**：它修的是「螺旋半径够不到画布边缘」，
> 却没有修**包围盒的宽度口径** —— 而中文宽度被低估 40% 会让碰撞检测**根本失效**。
> 这类缺陷的共性是：**排版类 bug 不会让脚本报错，只会让图变得不可读**，
> 因此阶段 K 的验收只能是**逐张打开肉眼过一遍**，且判据就是用户那句
> **「清晰展示所有字符」**。本次 6 处修复后，三张图均达标：
> 词云零重叠、overview 的 9 个综述标签各自独立可读、方向聚类标签不再叠死。

> ⚠️ **仍未解决（如实记录）**：方向聚类图的 **x 轴在本语料下塌缩为单一方向**
> （近 3 年 15 条全部归到「材料与结构」）。原因是评分取「共现词命中数最大者」，
> 而该方向标签的画像词覆盖面最广。这**不是可读性问题、是归类语义问题**，
> 修它涉及打分口径变更，**本次未改**，留待后续按需处理。
> 完整的近 3 年清单始终在《研究方向综述》里，图上只是节选。

---

## 9.7 知识图谱绘制改版（2026-09-19，v7.4，用户指定）

> 来源：用户明确要求（并**明确指示本步不受「不得擅自改动」条款约束**）：
> 「方向聚类：增加图例、增加对比性、改为 5 年论文聚类；知识图谱：美观化、
> 保留核心主干、改圆角矩形、不带阴影高光、文字嵌入其中、删除『自定义：』前缀」。
> 变更**已事先获得用户授权**。

### A. 方向聚类（`draw_direction_clusters`）

| # | 改动 | 理由 |
|---|---|---|
| 113 | **补图例**：热点簇配色 + 点大小标尺（被引 0 / ≈1500 / ≈7000） | 旧版无图例，颜色与大小只能靠猜 |
| 114 | **补对比面板**：右侧并排横向条形给出各方向文献数，并在 x 轴刻度标注条数 | 「增加对比性」；顺带让「哪个方向更活跃」一眼可比 |
| 115 | **方向按文献数降序**排列（旧版字典序，主次不分） | 同上 |
| 116 | **窗口默认 3 年 → 5 年**（`--recent-years` 默认值改 5） | 用户指定；实测样本从 15 条增至 44 条，聚类才有统计意义 |
| 117 | 热点簇图例移到**右侧面板空白处** | 放左图左上会压住 2025 年两个点的标题 |

### B. overview（`draw_overview`）

| # | 改动 | 理由 |
|---|---|---|
| 118 | 节点由**散点 + 径向外推标签**改为**圆角矩形 + 文字内嵌**（`boxstyle="round,pad=…,rounding_size=0.9"`，`edgecolor="none"`，flat 填充） | 用户指定；散点方案下标签只能外推，节点一密就互相压字（§9.6 缺陷 110 只是缓解，没有根治） |
| 119 | 新增**框尺寸估计 + 迭代防重叠**（复用词云的 CJK 宽度口径；author 钉在圆心；越界回夹） | 文字内嵌后节点必然变大，必须真正做几何避让 |
| 120 | **保留核心主干**：综述只留相关性前 12、方法只留最高频 10 且需被 ≥3 篇引用；**裁剪数量在 stdout 如实报出** | 「保留核心主干」；旧版把挂上的综述全画出来，节点一多就是毛线团 |
| 121 | **删除显示用标签的「自定义:」前缀**（`disp()`）——**仅改图上显示**，原值仍完整保留在 `graph_data.json` 与两份报告里 | 用户指定；「词表缺词」这一机器可读标记不因此丢失 |
| 122 | Louvain 社区改用**冷色系专用色板** `COMM_PALETTE`，只给「研究方向」层上色 | tab20 前几色含橙红，与「最紧密综述」的强调色 `#E4572E` 撞色，实测两类节点分不出来 |

### C. 方向归类算法（`assign_direction` / `direction_profiles_from_cards` / `compute_direction_clusters`）

> 起因：改完绘图后发现 5 年窗口下 **44 条里 41 条被塞进同一个方向**，
> 「对比面板」把这个缺陷直接暴露了出来。逐条抽查确认**是归类错误而非事实**：
> CO₂ 光还原、光催化、膜分离这些明显属于「催化与反应 / 能源与环境」的文献，
> 判据词却是 `active / reaction / properties / mol / abstract / communications` 这类泛词与噪声词。

| # | 缺陷 | 根因 | 处置 |
|---|---|---|---|
| 123 | ⛔ **宽画像通吃** | 打分取「命中词**个数**」最大者，而「材料与结构」画像词覆盖最广，绝对命中数天然最大 → 长文档偏置 | 改为 **IDF 加权**（`log(1 + N_dirs/df)`，只在一两个方向出现的词才算判别性证据）+ **按画像规模归一化**（除以 `sqrt(画像总权重)`）。实测分布由 `41/1/1/1` 变为跨 5 个方向的 `20/9/9/2/2/2` |
| 124 | ⛔ **画像只喂第一个标签** | `direction_profiles_from_cards` 写的是 `for t in tags[:1]` —— 多数卡的第一标签是「材料与结构」，于是它的画像被撑得极宽 | 改为 `for t in tags:`（计入全部方向标签） |
| 125 | 跨方向普遍词无判别力却堆高命中数 | 无词频门槛 | 新增 **df 截断**：`df > 方向数/2` 的词从画像中剔除 |
| 126 | 同一篇摘要内重复词被重复计分 | `toks` 是 list，长摘要天然占优 | 打分前 `set()` 去重 |
| 127 | 单位/序数/套话进入语料 | `mol / mmol / nm / ml / mg / kj / cm3 / first / second / abstract / communications / obtained / observed / …` | 并入 `MISLEADING` 剔除表（**词云同步受益**：剔除词型 18 → 20） |
| 128 | **新增判据 ②「施引的作者论文方向」** | `final_list` 每条都带 `cited_by_sources`（引用了它的作者论文 DOI），而作者论文的方向标签来自解读卡（受控词表）—— 这是「这条文献属于作者的哪个方向」的**直接证据**，优先级应高于关键词兜底 | 判据顺序改为 ① review 卡标注 → ② 施引论文方向（多数票）→ ③ 关键词兜底。⚠️ 本次 5 年窗口内该判据仅命中 1 条：窗口内的文献由作者**近年**论文引用，而那 22 篇恰好是缺卡的；补齐解读卡后此判据会显著生效 |

> ⚠️ **仍未解决（如实记录）**：关键词兜底本质是**启发式**，本次改动后分布虽已可比，
> 但「分子与化学 20」这一结果**不应被当作权威分类**。可靠的替代路径有两条：
> ① 补齐那 22 篇解读卡，让判据 ② 生效；② 把 x 轴换成数据驱动的**热点簇**或**期刊**。
> 图上与报告中该节均标注为启发式判定，逐条 `assign` 理由可复核。

---

## 9.8 overview 画幅与标题改版（2026-09-23，v7.5，用户指定）

> 来源：用户明确要求：「调整生成知识图谱的部分，使产物**格式契合 16:10 的比例**，
> **删除（overview）字样**，标题中「知识图谱」字样**改为「研究工作图谱」**，
> **删除标题下方灰色解释小字**，使其**美观优雅**」。
> 范围**仅限 overview（`draw_overview`）**：词云与方向聚类的画幅未动。

### A. 画幅改为 16:10（`draw_overview` / `save_fig`）

| # | 改动 | 理由 |
|---|---|---|
| 129 | 画布由 `figsize=(17, 15)`（≈1.13:1）改为 **`(16, 10)`**；绘图区用 `add_axes` 显式指定，上下留出标题带与图注带 | 旧产物实测 **2048×1610（1.27:1）**，整幅铺到 16:9/16:10 版面上必然留边或被裁 |
| 130 | `ax.set_aspect("equal")` → **`"auto"`** | 16:10 宽画幅下，等比例圆会在左右各留下约三分之一空白、节点字反而更小；拉伸为椭圆后画幅填满，字相对画面更大 |
| 131 | `PT_PER_UNIT` 拆成 **`PT_PER_X` / `PT_PER_Y`**，框尺寸、最小净距（点）、"沿哪一轴分开"的比较全部按轴换算 | x 轴被拉伸后「同样 0.1 个数据单位」横竖不等长；沿用单一口径会让框的估计尺寸与实际渲染对不上，**避让直接失效** |
| 132 | `save_fig()` 新增 **`tight` 参数**（默认 `True`，保持既有调用不变）；overview 传 **`tight=False`** | `bbox_inches="tight"` 会被画到坐标轴之外的文字撑开，比例随之漂移；overview 必须按画布原尺寸导出。**前提是所有元素都画在画布内** |
| 133 | 记忆连接图注由 `y=-0.015`（画布外，靠 tight bbox 兜）改为 **`y=0.012`（画布内，`va="bottom"`）**，字号 9 → 8.5 | `tight=False` 后画到画布外的文字会被**直接裁掉**；同时它本身就落在预留的下方文字带里 |
| 134 | 导出后打印实际画幅（`画布固定 16:10（16×10 in，PNG 回退 = 4800×3000 px @300dpi）`） | 尺寸是本次的**验收项**，跑完即可核对，不用开图量 |

### B. 标题改版（`draw_overview`）

| # | 改动 | 理由 |
|---|---|---|
| 135 | 标题 `{作者} 研究工作知识图谱（overview）` → **`{作者} 研究工作图谱`** | 用户指定：删「（overview）」，改「知识图谱」为「研究工作图谱」；同时避免与前面的「研究工作」重复 |
| 136 | **删除标题下方灰色解释小字**（`color="#555555"` 那行 `sub`） | 用户指定。它写的是「以作者为中心 · 阶段 → 方向 → 最紧密综述 · …」，**左侧图例已逐条给出**，重复一遍只抢正题注意力 |
| 137 | 标题改由 `fig.text(0.5, 0.947, …)` 定位（字号 20 → **26**，色 `#16324F`），下加**一条居中的短强调线**（`#E4572E`，与「最紧密综述」同色） | 去掉副标题后标题单独飘在上方会显空；短强调线收住视线，画面更干净 |
| 138 | 图例字号 11 → **10** | 画幅变矮（15 in → 10 in），同样字号在画幅里的占比变大，图例显得压图 |

> ⚠️ **注意（图示与文档的一致性）**：本版**未改**输出文件名
> （`<作者名>_知识图谱_overview.svg|png`）—— 文件名被
> `selftest_pipeline.py`（`*知识图谱*` 通配）与 `ppt_brief.py` 的图注表引用，
> 改名会连带改这两处；用户本次只要求删除**标题里**的「（overview）」。

---

## 9.9 CDP 并发模型改造与三处缺陷修复（2026-10-02，v8，用户指示）

> 用户指示：**解读工作与下载工作并行**（从网页取纯文本全文用于解读，
> 同时进行 CDP 下载）；CDP **由「多浏览器、单标签页」改为「多浏览器、多标签页」**。
>
> 本节记录改造过程中**实测发现**的问题。前两项是**原有代码就存在的缺陷**，
> 被多标签页放大后才暴露出来；第三项是本次新增代码引入的，也已修掉。

### 9.9-A 站点熔断计数在锁外更新 → 放行数超过阈值

| 项 | 内容 |
|---|---|
| **现象** | `--site-fail-limit=2` 时实测**放进去 4 次**（多标签页下更多） |
| **根因** | `GUARD.hit(site, …)` 在 `run_pool` 末尾（**站点锁之外**）调用。已排队等在锁上的线程用**过期的 `hits` 值**通过 `blocked()` 检查 |
| **为何以前没暴露** | 单标签页时并发线程少，通常只有 1 个在排队；多标签页把它放大 |
| **修复** | 失败记账移入 `with GUARD.lock(site)` 块内，锁释放前立即更新 `hits` |
| **验证** | 离线复现：修复前 `{'elsevier': 4}`，修复后 `{'elsevier': 2}`（= 阈值） |

### 9.9-B Windows 上探测已关闭端口固定耗时 ~2.0s

| 项 | 内容 |
|---|---|
| **现象** | 无浏览器运行时，`is_alive()` / `count_tabs()` / `prune_blank_tabs()` 每次**固定 2.0s**（实测三个不同端口都是 2.01–2.05s，与 `timeout=` 参数无关） |
| **影响** | `is_alive()` 在**每个任务前**调用 → 该开销直接乘上任务数；收尾清理按端口各等 2s。空载 12 篇实测 **4.16s** 全耗在此，与下载本身无关 |
| **根因** | Windows 上对已关闭的本地端口发 `urllib` 请求，连接被拒后的重试/解析开销，`timeout` 不生效 |
| **修复** | 新增 `_port_open()`：先用 `socket.connect_ex` 花 0.35s 快速判活，端口没开就直接返回，不发 HTTP |
| **验证** | 2.05s → **0.36s**；空载 12 篇 4.16s → **0.73s** |

### 9.9-C 收尾清理拖慢主流程（本次新引入）

| 项 | 内容 |
|---|---|
| **现象** | 多标签页模式在 `run_pool` 末尾调用 `prune_blank_tabs()`，浏览器不在时白等 |
| **修复** | 同 9.9-B 的 `_port_open()` 前置判活；并给 `close_tab()` 加 `timeout` |
| **说明** | 本项是本轮新增代码引入的，已在同轮修掉，未进入任何发布状态 |

### 9.9-D 改造后的正确性验证（离线，无浏览器）

| 不变量 | 方法 | 结果 |
|---|---|---|
| **同站点严格串行** | 在 `process_one` 内尝试 `lock.acquire(blocking=False)`，能抢到即为违规 | **0 次违规**（4/4 均无法抢到 → 锁确实被持有） |
| **并发通道数** | 统计峰值并发 | `--n=2 --tabs=3` → 峰值 **6**；`--tabs=6` → 峰值 **11**（= 理论通道数） |
| **熔断仍然生效** | 同站点连续失败计数 | 阈值 2 → 实际尝试 **2** 次，其余 4 篇熔断跳过 |
| **正文门控** | 5 项单测（默认不返回 / 显式返回 / 挑战页清空 / 付费墙保留 / 探针报错不崩） | **5/5 通过** |

> ⚠️ **并发上限的重要结论（实测）**：**并发通道上限 = min(通道数, 不同站点数)**。
> 同站点必须串行是反封禁红线，所以**同一出版社的多篇论文加标签页不会提速**。
> 实测：3 个站点时，`--tabs=1` 与 `--tabs=3` 吞吐相同（都受限于 3 个站点）。
> 多标签页只在语料**站点分散**时才有收益 —— 这是设计约束，不是缺陷。

### 9.9-E 正文顺带落盘（"下载与解读并行"的实现）

- `detect.py` 的页内探针**本就取回了完整正文**（`document.body.innerText`），
  旧版只留 `textlen` 就丢弃。新增 `fulltext_cap` 参数按需回传。
- **默认 0（不取）**，保证既有调用零行为变化；下载器默认 `--fulltext-cap=400000`。
- **先于 PDF 尝试落盘**：PDF 失败时解读素材仍在。
- **挑战页/封禁页正文清空**：避免把验证页文字当论文正文。
- 落盘到 `.work_cdp/fulltext/`（中间素材），**不进交付目录**。

> **一句话教训**：**并发不是免费的** —— 加并发通道会同时放大三类隐藏问题：
> 锁外记账的竞态、探测超时的固定开销、清理逻辑的同步阻塞。
> 本次三项里有两项在单标签页时代就已存在，只是并发度低时看不出来。

---

## 9.10 全链路实机验证（2026-10-02，v8）

> 除 CDP 下载外，**N → S → R → R① → R2 → R②** 全链路已用真实数据跑通。
> 本节记录实测数字与**实测暴露的新缺陷**。

### 9.10-A 各阶段实测结果

| 阶段 | 实测结果 |
|---|---|
| **N** 大方向确定 | 主题词 6 个 / 方法词 8 个；方法词在总表中命中 **9 个领域** |
| **S** 导师抓取 | **237 人（与官网自称 237 完全一致）**；有方向 107 人 / 页面无该栏位 130 人；13 个方向桶；报告 128 KB |
| **R** 综述检索 | Europe PMC 命中总数 **72,677**，取回 12 篇（`complete=false`，受 `--max-records` 截断），**开放获取 11/12** |
| **R①** 任务包 | 12/12 篇，全部判定为综述类，摘要级 12/12 |
| **R2** 并行解读 | **12/12 张卡**（分 4 批 × 3 篇）；field_role 分布：入门综述 8、方法综述 3、争议梳理 1 |
| **R②** 领域概貌 | **43 KB / 250 行**，8 个章节，**0 处 repr 泄漏**，**0 处 placeholder** |

### 9.10-B 实测发现：R2 并发度上限 ≈ 3 篇/批

| 并发度 | 结果 |
|---|---|
| **12 篇同时** | 仅 3 篇成功；失败品**结构合法但内容为 `placeholder`** |
| **3 篇一批 × 4 批** | **12/12 成功，无占位符** |

高并发下 subagent 触发生成长度上限，用占位符填充字段。实测污染样本：
`"conclusion": "placeholder"`、`"consensus": ["placeholder"]`、
`"open_questions": ["ERFERENCE_PLACEHOLDER"]`。

> ⛔ **schema 校验只管字段类型，管不了内容是不是真话。**
> 因此 `reviews_build.py` 新增**内容级校验** `validate_card()`：
> 检出占位符/空值的卡**拒收并单独列出**，不进入《领域概貌》。

### 9.10-C 实测发现：汇总器的两类渲染缺陷（已修）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 争议条目出现 `{'issue': '…', 'positions': '…'}` 原始 Python repr（**实测 6 处**） | subagent 对同一字段给出**不同形状**（有时字符串数组，有时 `{issue, positions}` 对象），渲染时直接 f-string 插值 | 新增 `_as_text()` 归一：dict/list/str 一律渲染成可读文本 |
| 2 | "出现篇数"**恒为 1**，方向表摊成 30+ 行 | 对每条 `(方向, 论文)` 组合各出一行，而不同综述**措辞不同**，永不合并 | 按方向名聚合为一行，取最长的 idea 作代表；术语增加"出现篇数"列 |

修复后：**repr 泄漏 0 处**；术语表正确显示 `轻度认知障碍 = 6 篇`
（6 篇综述都提到，即"领域核心概念"的排序信号恢复工作）。

> ⚠️ **仍未解决（如实记录）**：**方向名不会被自动合并** ——
> 各综述自行表述（"大脑能量代谢"vs"脑氧代谢"），措辞不同但含义相近的
> 无法自动归并。当前如实标注"需要合并请人工归并"，不做不可靠的模糊匹配。

### 9.10-D CDP 下载未能实机验证（环境限制）

沙箱下 Edge **无法维持调试端口**：进程能绑定端口，但对 `/json/version`
**零响应直到超时**（listen-but-never-respond）。Edge stderr 给出根因：

```
crashpad_client_win.cc:456] OpenProcess: Access is denied. (0x5)
```

即沙箱禁止跨进程访问，Edge 内部 IPC 通道被拒后网络/IO 线程卡死。
**这是环境限制，不是代码缺陷**；多标签页改造的验证全部在离线完成（§9.9-D）。

> 需要在**完全权限**下补做的验证：真实 `open_tab()` 并发、
> 同站点串行的实网行为、PDF 抓取、正文顺带落盘的实际效果。

---

## 9.11 阶段 S 列表页解析规则修正（2026-10-04，中科大生医部复跑）

**来源**：以 `output2/` 为新输出目录复跑「中国科学技术大学生命科学与医学部」阶段 S。
首次运行**列表解析 0 人**（站点可达、HTML 已存档，非网络问题）。

### 根因（两处，均在**配置**层，脚本本身无缺陷）

| # | 症状 | 根因 |
|---|---|---|
| 1 | 列表页解析 **0 人** | `wp_newsinfo_card` 的 `simple_item_re` 为**空串**，且 `direction_re` 亦为空 → `parse_list_page()` 的分支 A（需 `item_re` **且** `direction_re`）与分支 B（需 `simple_item_re`）**双双不成立**，必然返回空 |
| 2 | 只抓到**第 1 页**（28 人，漏 2/3） | `total_re`/`perpage_re` 写作 `共\s*(\d+)\s*条` / 空串，与本站实际写法（`<em class="all_count">79</em>`、`<em class="per_count">28</em>`，且中间隔 `&nbsp;` 与标签）**不匹配** → `parse_declared_total()` 返回 0 → 页数反推失效 |

**实测站点标记**（与配置假设不同）：

```html
<li class="itemhover clearfix"><a href="/2023/0602/c41923a735563/page.htm">
  <div class="info"><div class="tit">毕国强</div><p class="tit2"></p></div></a></li>

<span class="all_count">总共&nbsp;<em class="all_count">79</em>&nbsp;记录</span>
<span class="per_page">每页&nbsp;<em class="per_count">28</em>&nbsp;记录</span>
<a class="last" href="/smkxy/list3.htm">尾页</a>
```

> ⚠️ 本站**没有 `<h3>`**（配置 `name_re` 原写 `<h3>(.*?)</h3>`，实测 0 命中）。
> 姓名在 `<div class="tit">`；`<p class="tit2">`（职衔位）**实测恒为空**。

### 处置（**只改配置，未改脚本** —— 符合 §阶段 S「新增学校不必改脚本」的设计）

`templates/学部官网源.json` → `list_types.wp_newsinfo_card`：

| 字段 | 旧 | 新 |
|---|---|---|
| `simple_item_re` | `""` | `<li[^>]*class="[^"]*itemhover[^"]*"[^>]*>\s*<a[^>]+href="([^"]+page\.htm)"[^>]*>(.*?)</a>` |
| `item_re` | `<li[^>]*>(.*?)</li>` | 同上（限定 `itemhover`，避免匹配导航 `<li>`） |
| `name_re` | `<h3[^>]*>(.*?)</h3>` | `<div[^>]*class="tit"[^>]*>(.*?)</div>` |
| `total_re` | `共\s*(\d+)\s*条` | `总共[^0-9]{0,40}?(\d+)` |
| `perpage_re` | `""` | `每页[^0-9]{0,40}?(\d+)` |
| `pages_re` | `页码\s*\d+\s*/\s*(\d+)` | `页码[^0-9]{0,80}?(\d+)\s*/\s*(\d+)` |

> `split_name_title()` 会先 `strip_tags`，故分支 B 传入整块 HTML 也能正确取出姓名
> （英文名 `Andrew Macpherson` 亦正常）。

### 修复后实测（2026-10-04）

| 项 | 结果 |
|---|---|
| 列表 | **161 人**（生科院 79 + 基础医 58 + 临床医 11 + 生医工 6 + 附一 7 = **161**，与官网逐项吻合） |
| 分页 | 3/3、3/3、1/1、1/1、1/1 —— **无遗漏** |
| 详情 | 161/161 成功，**0 失败**，约 3 分钟 |
| 有研究方向 | **126/161（78%）** |
| 归类 | 13 个方向桶 |
| 名单比对 | 与 `outputs/05_学院官网/college_list.json`（历史基准 161 人）**逐名对照，差异 0** |

### 如实记录的两项数据缺口（**非解析缺陷，不臆造**）

1. **35 人官网未提供研究方向**。已逐条核对存档 HTML：关键词「研究方向」仅出现在
   **招生招聘套话**中（"欢迎对本实验室研究方向感兴趣的本科生…"），
   按 §清洗判据「招生口径≠研究方向」正确剔除。**再抓一次也拿不到**。
2. **职称列为空**。本站列表页 `<p class="tit2">`（职衔位）实测恒空，
   详情页职衔混在正文自由标记内。故报告「职称」列如实留 `—`，
   **未从正文猜测填充**。

---

## 9.12 方向 → 数据库路由（2026-10-04，v12，**用户指令**）

**来源**：用户指令 ——「根据不同方向匹配不同数据库。例如计算生物学、计算机科学、
人工智能匹配 arXiv；生物、医学匹配 PubMed、Europe PMC 等等」。

### 问题（实测暴露）

复跑「免疫与感染」时三线全跑（Europe PMC + arXiv + Google Scholar），结果是：

| 线 | 实际产出 | 问题 |
|---|---|---|
| Europe PMC | ✅ 12 篇定稿全部来自这里 | —— |
| **arXiv** | ❌ **0 篇被采用** | 返回的 24 条里绝大多数是**计算机领域**的 'immune'（人工免疫系统 / 入侵检测 / 程序验证）与**传染病建模**，非湿实验免疫学 |
| Google Scholar | ❌ 未取到 | 无官方 API + 反爬 |

**三线里只有一条真正产出文献 —— 所谓「并行」名不副实**，且 arXiv 那次请求纯属浪费。

反向问题同样存在：给「机器学习」只查 PubMed，几乎命中不到预印本方法学论文
（该领域成果优先发 arXiv）。**两个生态交集很小。**

### 处置：新增路由表 + 查询/自检脚本（**未改任何现有脚本逻辑**）

| 新增文件 | 作用 |
|---|---|
| `templates/方向数据库路由表.json` | **唯一选库依据**。7 个库 × **24 个方法领域**（与「研究方法与关键词总表.md」逐项一致）+ **`faculty_direction_map`：12 个学部方向 → 方法领域**（与「导师方向规则.json」逐项一致）；含 primary / secondary / **skip（及理由）** / review_filter / 检索式模板 / 兜底策略 / 诚实规则 |
| `scripts/route_databases.py` | 查表 + 生成**可直接执行**的检索命令（各库语法分别拼好）；`--self-test` 零网络自检两套坐标系的一致性 |

**未改动**：`paginate.py`、`arxiv_atom.py`、`reviews_prepare.py` 等一律原样
（符合「换方向只传参、不改脚本」）。

### 关键设计点（都是踩过的坑）

| # | 设计 | 依据 |
|---|---|---|
| 1 | 各库**语法互不通用**，按库分别生成 | `PUB_TYPE:"Review"` 是 Europe PMC 语法，喂给 arXiv 会被当普通词、喂给 OpenAlex 会被忽略。**首版曾把它原样透传给 arXiv，已修为按库取 `review_clause`** |
| 2 | 引文排序语法**写进 query** | ⛔ `paginate.py` **没有 `--sort` 开关**。Europe PMC 用 `AND (SORT_CITED:y)`、OpenAlex 用 `sort=cited_by_count:desc` |
| 3 | skip 列**必须给理由** | 防止无谓请求；也便于用户判断覆盖度是否有偏 |
| 4 | arXiv 标注 `rate_limit_seconds: 3` | 限流是硬约束，必须串行 |
| 5 | 兜底 `Europe PMC → OpenAlex` | 方向未登记时不至于卡死，但**必须在报告里标注用了兜底** |
| 6 | 同形异义词警告写进表 | cs 的 'immune' ≠ 免疫学；cs 的 'transformer' ≠ 电力变压器 |
| 7 | **桥接两套坐标系** | 用户选的是**学部方向**（12 个，按研究对象切），库却按**方法领域**（24 个，按研究手段切）配 —— 两者正交。故加 `faculty_direction_map` 自动换算，**不让用户自己对照**（这正是阶段 N 警告过的坑） |

### 实测验证（2026-10-04）

| 项 | 结果 |
|---|---|
| `route_databases.py --self-test` | ✅ 通过（库 7 / 方法领域 24 / 学部方向 12；**两套坐标系各自逐项一致**） |
| 生成命令实跑 · Europe PMC | ✅ 含 `SORT_CITED:y`，正常返回被引排序结果 |
| 生成命令实跑 · arXiv | ✅ 返回 5 条 transformer 综述（**确切相关**，非计算机噪声） |
| 生成命令实跑 · OpenAlex | ✅ `filter=type:review` **确认生效**（不带过滤返回 repository/article，带过滤返回 review） |
| 学部方向路由 | ✅ 「免疫与感染」→ 只 Europe PMC（不浪费 arXiv 请求）；「生物信息与计算」→ Europe PMC + arXiv |

> 自检还**当场抓出两处问题**：① 初版把 Crossref 标为 `citation_sort: true`，
> 但 Crossref 是 DOI 注册库、**不支持按被引排序**；② 初版未登记 `faculty_direction_map`，
> 导致 12 个学部方向全部走兜底。均已改正 —— 说明自检不是摆设。

---

## 9.13 arXiv 取数改 Python（2026-10-02，v12.1，**用户指令 + 环境实测**）

**来源**：用户指令 ——「改 `route_databases.py` 的 arXiv 命令为 Python 版」。

**背景实测**（本机 DSH 沙箱 `workspace-write`，2026-10-02）：

| 项 | 实测 |
|---|---|
| 令牌完整性 | **Low Mandatory Level**（完整访问下同一进程为 Medium） |
| 走系统 TLS 栈的 HTTPS 客户端 | ❌ 全部失败 —— 系统 curl 8.21.0 / Git curl 8.19.0（均 Schannel）、.NET `Invoke-WebRequest`、`HttpClient`、`certutil`(WinHTTP)，报错统一为 `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS` |
| 同一 curl 打**明文 HTTP** | ✅ 200（故非网络问题；`-k` 也无效，失败发生在握手**之前**） |
| 自带 OpenSSL 的运行时 | ✅ Python 3.14(OpenSSL 3.5.7) 与 Node `fetch` 均 200 |
| 对照实验 | ✅ 完整访问模式下令牌为 Medium，同一 curl 返回 **200** —— 根因是**沙箱降权**，不是本机故障 |

> 取证全文：`直链OA测试/环境取证_curl与TLS.md`。
> ⚠️ 与 §9 已有的环境记录**不是一回事**：那条是 `CRYPT_E_REVOCATION_OFFLINE`（证书链/吊销，握手**之内**），
> 本条是 `SEC_E_NO_CREDENTIALS`（取得凭证，握手**之前**）且**只在沙箱内出现**。

### 改什么（**只改 1 处**）

| 项 | 旧 | 新 |
|---|---|---|
| 取数 | `curl -s '<URL>'` | `python -c "…urllib.request…"` |
| 传递给解析器 | 管道：`\| python …/arxiv_atom.py -` | 落文件：`&& python …/arxiv_atom.py .work_cdp/arxiv_query.xml` |

改动位置：`route_databases.py` 的 `build_plan()` **`arxiv_atom.py` 分支**。
`paginate.py`、`arxiv_atom.py`、`templates/方向数据库路由表.json` **一律未动**；
其余 6 个库（europepmc / openalex / crossref / biorxiv / medrxiv）的生成命令**逐字未变**。

### 为什么不是「只把 curl 换成 python 管道」

管道与 shell 重定向都会让 **shell 决定字节**：PowerShell 在原生程序之间传递文本时按
控制台编码解码再编码，**非 ASCII 作者名（重音字母等）会被破坏**。
改为 **Python 自己 `write_bytes()` 落文件**后，消费端 `_common.read_input` 走的是
`read_bytes()` + **显式 UTF-8 解码** —— 字节安全且与 locale 无关。
残留的 shell 语法只有 `&&`（bash / cmd / PowerShell 7 均支持）。

### 实测验证（2026-10-02）

| 项 | 结果 |
|---|---|
| `python -m py_compile route_databases.py` | ✅ 通过 |
| `route_databases.py --self-test` | ✅ 全部通过（库 7 / 方法领域 24 / 学部方向 12，两套坐标系逐项一致） |
| 生成命令**原样执行**（`--domain 机器学习与深度学习 --terms Alzheimer`） | ✅ exit 0；`total_results: 121`、`returned: 12`，`arxiv_atom.py` 正常解析出 12 条 |

### 本次**未**改动（如实申报）

1. **`search_run.py` 仍把 arXiv 标为「需 `arxiv_atom.py` 管道」并默认跳过** ——
   本次只回答「命令该怎么写」，没有动检索执行器的调度逻辑。
2. **另发现一处既存缺陷（未修）**：`route_databases.py` **缺少**其它脚本都有的
   UTF-8 流守卫（`_fix_streams()`），在 **GBK 控制台**下会崩在 `⛔`(U+26D4) 上：
   `UnicodeEncodeError: 'gbk' codec can't encode character '\u26d4'`，
   **连生成命令都打印不出来**。设 `PYTHONIOENCODING=utf-8` 可绕过。属独立缺陷，待单独授权后再修。

---

## 9.14 七个脚本补 UTF-8 流守卫（2026-10-02，v12.2，**用户指令**）

**来源**：用户指令 ——「修改相关脚本」。

**起因**：§9.13 修 arXiv 命令时发现 `route_databases.py` 在 **GBK 控制台（cp936）** 下崩在
`⛔`(U+26D4) 上：`UnicodeEncodeError: 'gbk' codec can't encode character '\u26d4'`，
**连生成命令都打印不出来**（当时靠 `PYTHONIOENCODING=utf-8` 绕过）。

### 排查：不是个例，但也没有第一眼看上去那么多

对 `scripts/*.py` 做了三轮判定（判据：**AST 提取「会输出到屏幕」位置的字符串字面量**，
即 `print()` / 日志调用 / argparse 的 help 文本 —— 正则字面量不算）：

| 轮次 | 判据 | 命中 |
|---|---|---|
| ① | 字符串里含 GBK 编不出字符 且 无守卫 | 24 —— **误报多**（`store.py` / `build_list.py` 的零宽字符只用于正则与清洗，从不打印） |
| ② | 限定在 print/log/argparse 位置 | 22 |
| ③ | 再按**所有已知守卫写法**过滤 | **8** —— 有 13 个早已用内联写法 `sys.stdout = io.TextIOWrapper(...)`，①的正则没认出它 |

### 改什么（**7 个文件，各 +14 行**，写法与 `fetch_direct.py` **逐字一致**）

| 脚本 | 会被打印、且 GBK 编不出的字符 |
|---|---|
| `check_challenge.py` | ⚠ ✅ ❌ |
| `download_all_fulltexts.py` | ⏳ ⚠ ✓ 📄 |
| `extract_figures.py` | ✅ ✗ |
| `grab_abstracts.py` | ⏳ 🛑 |
| `render_html_pdf.py` | ⚠ |
| `route_databases.py` | ⚠ ⛔ ✓ ✗ |
| `save_pdf_via_browser.py` | 🛑 |

插入内容 = 既有 `_fix_streams()` 函数（含缺失的 `import io`），置于 import 区之后、
**首个 print/log 之前**。该函数在编码已是 UTF-8 时**直接 return**，故 UTF-8 环境下零行为变化。

**未改（如实申报）**：第 8 个 `fetch_oa_direct.py` —— 该脚本 **v4 起已停用**
（`_refuse()` 直接 `exit 3`），且**它自己**已用
`io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")` 包了输出；
含图标字符的部分是**死代码**。**故不动**，避免无谓改动。

### 实测验证（2026-10-02，控制台 `chcp` = **936**，**全程未设 `PYTHONIOENCODING`**）

| 项 | 结果 |
|---|---|
| **改前复现** | `route_databases.py --domain …` → `UnicodeEncodeError: 'gbk' codec can't encode character '\u26d4'`，exit 1 |
| **改后** | 同一命令 → **exit 0，输出正常** |
| `python -m py_compile` ×7 | ✅ 全部通过 |
| 静态复扫 | ✅「会打印且无守卫」的脚本**归零** |
| **动态验证 ×7** | ✅ 子进程 stdout 为管道（locale=cp936）时逐个 `runpy` 加载 → 守卫生效（`stdout.encoding` 变 `utf-8`），打印 `⛔✓✗⚠️✅❌🛑⏳↻` 全部无异常 |
| 对照组（同环境、无守卫） | ✅ 如预期抛 `UnicodeEncodeError` —— 证明上述测试条件**确实有效**，不是「碰巧不崩」 |
| `route_databases.py --self-test` | ✅ 全部通过（库 7 / 领域 24 / 学部方向 12） |

> **改前已备份**：`%TEMP%\流程1_scripts_backup_<时间戳>\`（61 个文件 + SKILL.md + README.md）。
> ⚠️ `流程1/` 在本仓库 git 中**未被跟踪**（`?? 流程1/`），即**没有版本控制兜底** ——
> 这是备份存在的原因，也建议后续把 `流程1/` 纳入 git。

---

## 9.15 解读档位整合（2026-10-03，v13，**用户指令**）

### 9.15-A 来源与授权

**用户指令**：把 skill `paper-interpretation-writing` 整合进 `流程1/`，
改动**原有所有解读模块**使之匹配；核查阶段 0 确认环节并**添加解读档位确认**；
检查各门控是否齐全。**默认**：综述类解读 subagent 用**档位 3**，
作者论文解读按**确认的档位**走。

按本流程 §「硬性要求」第 3 条，改动已先说明「改什么、为什么、影响范围」，
本条即台账记录；`README.md` 已同步。

### 9.15-B 改了什么（15 个文件）

| 文件 | 改动 |
|---|---|
| `scripts/prepare_stage.py` | 新增 3 个选项（`undergrad_prelude` / `interpret_tier` / `review_interpret_tier`）；`VALID_STAGES` 补 `0 N S R R2`；`--tier` 查询；`--audit` 门控自检；`gate_audit()`/`ungated_stages()`/`resolve_tier()`/`AUDIT_NOTES`；修 `coerce` 枚举大小写 |
| `scripts/interpret_prepare.py` | 读档（`--tier` → plan → 默认 L2）并写入每个任务包与 manifest；`--help` |
| `scripts/interpret_build.py` | 三级档位解析 + 分档渲染（`_card_body`=L2 基线、`_card_l1`、`_card_l3`、`_strength_line`）；报告头档位声明 |
| `scripts/refs_prepare.py` / `refs_build.py` | 阶段 H2：读 `review_interpret_tier`，写入任务包/manifest，报告头声明 |
| `scripts/reviews_prepare.py` / `reviews_build.py` | 阶段 R2：同上 |
| `scripts/ppt_brief.py` | 记录两个档位并写入产物；素材受限时如实标注 |
| `scripts/selftest_pipeline.py` | 新增档位相关自检项 |
| `subagent-driven-review/SKILL.md` | 新增 **§6.0 解读档位**（字段×档位表）；§7 / §7.1 / §7.2 三个提示词模板加档位块；「三件事」→**四件事**；§8 增 3 条风险行；§10 增 4 条检查项 |
| `SKILL.md` | v13 变更块；§0 快速开始；阶段 0 / H / H2 / R2 四处；本条台账 |
| `README.md` | v13 变更块；快速开始；阶段 H 注释 |
| `完整链路.md` | R2 任务包含档位；H vs R2 档位来源对照 |
| `ppt-integration/SKILL.md` | 素材档位记录与「素材受限如实说明」 |
| `run_all.sh` | `[0.6]` 只读打印两处档位（**不改行为**） |

### 9.15-C 档位语义（唯一的规范来源）

三档**只差细节量，不差断言强度**。规范定义、句式库、跨 agent 数据契约与
离线校验器全部在 skill `paper-interpretation-writing`；本流程只引用，不复制其内容
（避免两处口径漂移）。默认值：

| 阶段 | 选项 | 默认 | 理由 |
|---|---|---|---|
| H（作者论文） | `interpret_tier` | **L2 详细** | 产物《论文解读.md》是**逐篇卡片**，正是 L2 的目标形态。选 L3 可恢复到与旧版等深 |
| H2 / R2（综述类） | `review_interpret_tier` | **L3 完整** | 两者都是**独立成篇**的综述类交付物；低档会丢掉排除性证据与作者语气限定 |

### 9.15-D 顺带修掉的四个缺陷（都已实测复现 → 修复 → 复验）

| # | 缺陷 | 症状 | 修法 |
|---|---|---|---|
| 1 | **阶段 N/S/R/R2 不在闸门表里** | `--check N` 返回 **2**（未知阶段）；`run_all.sh` 的 `gate()` 用 `if` 判定，**2 与 3 一样落进 else**，该阶段被**静默当成跳过** | 补进 `VALID_STAGES` + `STAGE_LABEL`，新增 `undergrad_prelude`（默认开）管辖；`--audit` 持续守住 |
| 2 | **枚举取值大小写不敏感缺失** | `--set interpret_tier=L3` 报「取值 'L3' 不在 ['L1','L2','L3'] 内」—— `coerce` 把输入 `.lower()` 后与**大写**枚举比对，永远匹配不上 | 改为逐项大小写不敏感比对并**返回规范写法** |
| 3 | **`--tier` 的 stdout 被日志污染** | `logutil.get()` 先打「=== prepare 启动 … ===」到 stdout；`T=$(… --tier H)` 捕获到 `"INFO … L2"`，下游比对 `"L2"` **静默失败** | 把 `--tier` 分支移到 `get("prepare")` **之前** |
| 4 | **`interpret_prepare/build` 的 `--help` 会掉进正常流程** | `--help` 不被识别 → 继续执行 → 因缺题录而退出 2 | 显式 `--help/-h` → 打印用法退出 0 |

### 9.15-E 验证（离线，无浏览器）

**① L2 逐字节基线（最关键）** —— 用 `git show HEAD:scripts/interpret_build.py` 取旧版，
与改后脚本在**同一工作目录**下对同一 fixture 各跑一次：

| 数据集 | 结果 |
|---|---|
| 自建 6 篇夹具 | 除**新增的那一行档位声明**外，SHA256 **完全一致** |
| 仓库 `示例数据/crossref_all.json`（77 条）+ 真实词族模板（20 张卡、64 KB 报告） | 除该行外 **64347 字节完全相同**（差值 77 字节恰为该行） |

> 即：**默认档（L2）的卡片渲染逐字节未变**；唯一差异是任务书要求新增的报告头档位声明行。

**② 档位全链路**（在 `%TEMP%` 独立工作目录，用真实示例数据 77 条）：

| 检查 | 结果 |
|---|---|
| 阶段 0 默认 → `--tier H` / `H2` / `R2` | `L2` / `L3` / `L3`，stdout **只有档位本身** ✅ |
| `--set interpret_tier=l3`（小写） | 接受并规范化为 `L3` ✅ |
| `--set interpret_tier=L9` | 明确报错 ✅ |
| `interpret_prepare.py` 默认 | `manifest` 与全部 **77** 个任务包均为 `L2`，**0 个缺字段** ✅ |
| 阶段 0 设 `L3` → 重跑 prepare | `manifest` 与任务包全部变 `L3`（无需 `--tier`）✅ |
| `--tier L1` 覆盖 | `manifest` 与任务包变 `L1` ✅ |
| 删掉 `prepare_plan.json` 后重跑 | 回退 `L2` + stderr 一句提示，**不报错退出** ✅ |
| `--tier L9`（prepare 的 CLI） | 退出码 **2**，提示可用取值 ✅ |
| `prepare_stage.py --audit` | 门控齐全，退出码 **0** ✅ |

**②-b 阶段 H2 / R2（综述类四个脚本）** —— 与阶段 H 分开验证：

| 检查 | 结果 |
|---|---|
| `refs_prepare.py` 无 plan | 回退 **L3**，`manifest` 与任务包均 `L3` ✅ |
| 阶段 0 设 `review_interpret_tier=L1` → 重跑 | `manifest` 与任务包均变 **L1** ✅ |
| `reviews_prepare.py` 无 plan / `--tier L2` | `L3` / `L2`，任务包同步 ✅ |
| `reviews_build.py` 汇总（任务包 L2） | 报告头写出 `> 解读档位：**L2 详细**…`，摘要标注**来源 = task packet** ✅ |
| 四个脚本 `--help` / `-h` | 均退出 **0**（**改动前 `--help` 会被静默忽略并当正常流程跑**，与 9.15-D-4 同类）✅ |
| **既有渲染 A/B 对拍** | `refs_build` 报告 **0 行删除/改写**，仅新增 2 行（空行 + banner）；`reviews_build` 仅新增 1 行 banner；两边 manifest 与任务包的结构 diff **恰为** `+interpret_tier` ✅ |
| 反向对照（L1 ↔ L3 重渲染） | 报告除 banner 标签外**逐行相同** —— 证明**未引入 L1/L2 差异化渲染**，符合「本次只做读档→下发→声明」的范围 ✅ |

**②-c 阶段 P**：`ppt_brief.py` 自检通过；离线全流程自检确认档位
落到 `source md` / `design_spec.md` / `spec_lock.md` **三份产物**，
且 `spec_lock.md` 的**严格 schema 仍通过** `ppt-master` 自家 `validate`
（档位行只写在允许任意 `- ` 行的 `## forbidden` 段，不新增 `##` 段）。
`spec_lock.md` 的 schema 约束是实测出来的：新增 `##` 段 → `unknown section`（退出 1）；
往 `## communication` 加字段 → `unknown field`（退出 1）。

**③ 未验证项（如实列出）**

1. **端到端离线自检已跑并全绿**：`selftest_pipeline.py --offline` → **45/45 通过，退出 0**，
   且 `vendor/ppt-master/projects/` **无残留**（前后快照一致）。
   但该场景里 `reverse_refs` 是关的（输出显示 `D2=3 P=3`），**所以它没有覆盖阶段 H2**；
   阶段 H2/R2 由上面的 ②-b 单独验证。
2. **L3 的「证据强度」行措辞是实现自定**：规范只要求 L3 有该声明，未规定逐篇卡片句式。
   优先引用卡内 `strength_notes`，其次 `evidence`，都没有才按 `evidence_source` 写。
3. **`fulltext` 集成路径未端到端跑**（夹具无 PDF、环境无 pypdf），仅做函数级分支核对。
4. **L1 的两处渲染口径判断**（由实现方定，可一行改回）：不输出
   `direction_tags`/`paradigm_tags` 标签行；关键数字表**不设「出处」列**
   —— 依据是 tier-specs.md 把「图表引用」列为 L1 禁写项。
   标签仍**正常参与**报告三/四/五节的统计（统计取自卡片字段，不取渲染文本）。
5. **`build` 类脚本没有 `--tier`**（`interpret_build` / `refs_build` / `reviews_build`）：
   改档只能走阶段 0 或 prepare 侧的 `--tier`，再经任务包流转。
   如需 build 也能命令行覆盖，另开一批。
6. **联网版全流程未跑**（本次刻意避免）：`selftest_pipeline.py` 的联网档会启动 CDP
   并访问出版社站点。

### 9.15-F 已知限制与待决策

> **① 待决策（未改）**：`output_format=markdown` 的 `gate_rule` **关闭阶段 F**，
> 但该选项的 note 与闸门表都写着「阶段 F 仍执行，只出中译 Markdown」。
> 按现行规则，**markdown 用户即使 `translate=yes` 也拿不到中译**。
> 已固化进 `prepare_stage.py` 的 `AUDIT_NOTES`，每次 `--audit` 都会打印。
> **两条路**：(a) 去掉 `output_format` 对 F 的闸门（改由 `translate` 判定，
> PDF 与否由 `_f_mode` 表达）；(b) 改文档。**未拍板前不动。**

> **② L1 的下游代价（预期内，非缺陷）**：阶段 H 的关键词统计、阶段 K 的知识图谱与词云、
> `refs_curate.py` 的方向相关性打分，都从解读卡的 `methods`/`key_20`/标签取语料。
> L1 留空 `methods`，这三处会明显变薄。**需要知识图谱时建议 L2 起步。**
> 已核实：这些消费者读 `interpret/results/*.json` **卡片**而非渲染后的 Markdown，
> 因此改档**不会**导致它们解析失败。

> **③ 另一处口径约束**：阶段 H2/R2 的档位与阶段 H **独立**（`review_interpret_tier`
> vs `interpret_tier`）。若把综述档位调到 L1，`refs_build`/`reviews_build` 目前
> **仍按 L3 全量渲染**（本次只做「读档 → 下发 → 声明」，未做低档渲染）。
> 需要低档综述渲染时另开一批。

### 9.15-G 版本控制状态（**纠正 §9.14 的过时结论**）

§9.14 写「`流程1/` 在本仓库 git 中未被跟踪（`?? 流程1/`），即**没有版本控制兜底**」。
**该结论已过时**：`流程1/` 现在**是独立的 git 仓库**（`流程1/.git`），
已有两个提交建立基线（`795f5ed` 建立基线、`e776b0f` 说明独立仓库用法）。
本次改动**有 diff 可回溯**，无需再手工备份到 `%TEMP%`。

> **两个层级别混淆**（实测澄清，两句话都对，取决于在哪个仓库里跑 `git status`）：
>
> | 层级 | 事实 |
> |---|---|
> | **内层**（在 `流程1/` 里跑） | **跟踪**：本次改动 15 个文件全部可见（`git -C 流程1 status --porcelain` 有 15 行） |
> | **外层**（在仓库根跑） | **忽略**：`.git/info/exclude:12` 写着 `/流程1/`，注释说明「已在其中自建独立 git 仓库，故父仓库这里只做忽略，不做跟踪」 |
>
> 所以「`git diff --stat` 看不到本次改动」这句**只在外层成立**；
> 要看本次改动的 diff，必须在 `流程1/` 目录里跑 `git status` / `git diff`。

> 本次实测同时澄清了两条**环境假象**（此前被当成缺陷）：
> - 本机 `bash` 是 **WSL 的 bash**（`C:\WINDOWS\system32\bash.exe`），只认 `/mnt/c/…` 路径；
>   传 Windows 路径必然 `No such file or directory`。
> - 仓库里的 shell 脚本是 **CRLF** 行尾，WSL bash 会报 `$'{\r'` 语法错。
>   把 `run_all.sh` 与 `scientific-schematics/scripts/example_usage.sh` 转成 LF 后
>   `bash -n` **均退出 0** —— 所以 `tests/_meta` 里那两条 `shell_scripts` 失败
>   是**环境假象，不是脚本缺陷**。

### 9.15-H 本次的两处「行为增量」与一次误操作（如实申报）

| 项 | 说明 |
|---|---|
| `--help` 新增到 `ppt_brief.py` / `selftest_pipeline.py` | 这两个脚本原本**没有 argparse**，`--help` 会**落进主流程真实执行**（`selftest_pipeline.py --help` 甚至会跑完全流程并发起网络请求）。为满足「`--help` 退出 0」这条验收标准而加。**唯一行为增量**，仅影响 `-h/--help` 输入 |
| 阶段 P 自检项目名加唯一后缀 | 原固定名会在 `project_manager.py init` 同名时 `FileExistsError` 退出，导致阶段 P 自检**恒失败**（与本次改动无关）。加 `_自检<HHMMSS>` 后每次互不干扰；`{AUTHOR}*` glob 与清理逻辑未改 |
| ⚠️ **一次误触发的联网运行** | 加 `--help` **之前**，为验收跑了 `selftest_pipeline.py --help`，导致**全流程真实执行**（访问了 OpenAlex / Crossref）。**仓库无残留**（临时目录已删、未新建 ppt-master 项目目录，`vendor/ppt-master/projects/` 仍只有两个既有目录）。此事如实记录，供后续判断该脚本是否应默认拦截未识别的 `--*` 参数 |
| 未清理的既有残留 | `vendor/ppt-master/projects/自检作者_文献汇报_ppt169_20261003`（今日 00:16，**本次改动之前**就在）会让阶段 P 自检恒失败。**未删除**（不属于本次改动范围）——**建议清理或改名** |

> **建议**：给 `selftest_pipeline.py` 加一个「未知 `--xxx` 参数 → 直接报错退出 3」的
> 守卫，避免任何人（或 agent）误敲参数就触发一次联网全流程。本次未顺手加，
> 因为它又是一处行为增量。

---

## 9.16 修掉「`output_format` 关掉阶段 F」门控矛盾（2026-10-03，v13.1，**用户拍板**）

### 9.16-A 缺陷是什么

`output_format=markdown` 的 `gate_rule` 把阶段 F 置为 `skip`，**而 `translate` 未参与该判定**。
后果：`translate=yes` + `output_format=markdown` 时阶段 F 被**静默跳过** ——
**用户明确要了中文翻译，却什么都拿不到，且不报错。**

实测矩阵（改前，`_translate_output` 由 `build_plan` 计算）：

| `output_format` | `translate` | 阶段 F | `_translate_output` | 关闭者 |
|---|---|---|---|---|
| markdown | no | skip | off | output_format |
| **markdown** | **yes** | **skip** ← 缺陷 | **markdown** ← 自相矛盾 | **output_format** |
| pdf | no | skip | off | translate |
| pdf | yes | run | html+pdf | — |
| both | no | skip | off | translate |
| both | yes | run | html+pdf | — |

第 2 行里，同一份 `prepare_plan.json` 一边记着「应产出中译 Markdown」，
一边把执行该产出的阶段 F 关掉。

### 9.16-B 为什么判定「规则错、文档对」（5 处独立证据）

一整套为「translate=yes + markdown → 只出中译 Markdown」准备的机制，
因这条闸门**全部不可达**：

| # | 证据 | 状态 |
|---|---|---|
| 1 | `translate` 选项的 note 明写该路径 | 描述了一个走不通的行为 |
| 2 | `_translate_output = "markdown"` | **死代码**：只有该路径赋值，赋值后 F 必 skip |
| 3 | `_f_mode = "html-only"` | **死代码**：需 F=run 且 fmt=markdown，二者互斥 |
| 4 | `render()` 的 `ℹ 阶段 F 已开启但 output_format=markdown → …` | **永不打印**：只在 `_f_mode=="html-only"` 时输出 |
| 5 | `selftest_pipeline.py` 那条断言 | **标签写「F 为 html-only」、断言写 `_f_mode == "off"`** —— 标签留着意图，断言锁着实际行为 |

一个需求在五处留下痕迹却无路径可达 → 判为**疏忽**，非设计。

### 9.16-C 改法与影响面

- `output_format` 的 `gates` / `gate_rule` **置空**，与两个解读档位选项一样成为**纯参数**；
  只被 `_f_mode` 消费。阶段 F 的 run/skip 现**只由 `translate` 判定**。
- `AUDIT_NOTES` 清空（原第 1 条即本项），并在代码注释里保留「为什么不能自动修」的说明。
- `prepare_stage.py` 的 `--show` 文案与 `output_format` 的 note 同步改写。

**影响面（实测，非推断）**：六种组合中**只有一格变化**——

| 组合 | 改前 | 改后 |
|---|---|---|
| **`translate=yes` + `markdown`** | F=skip，**什么都不产出** | **F=run，`_f_mode=html-only`，产出中译 Markdown（不渲染 PDF）** |
| `markdown` + `translate=no`（**默认组合**） | skip | skip（**不变**） |
| `pdf`/`both` + `translate=no` | skip | skip（**不变**） |
| `pdf`/`both` + `translate=yes` | run，html+pdf | run，html+pdf（**不变**） |

> **副作用（正向）**：`_translate_output="markdown"`、`_f_mode="html-only"`、
> `--show` 的那条提示语**三处死代码同时复活为活路径**。

### 9.16-D 验证

| 项 | 结果 |
|---|---|
| 六格矩阵复测 | 第 2 行 `F=run` / `--check F` 退出码 **0** / `_f_mode=html-only` / `_translate_output=markdown`；其余五格逐项与改前一致 ✅ |
| `selftest_pipeline.py --offline` | **47/47 通过，退出 0**（45 → 47：新增 2 条）✅ |
| 新增的 2 条是**行为级回归守卫** | ① `translate=yes + markdown` 时 `--check F` 必须退出 0；② 该组合 `_f_mode` 必须为 `html-only`。**有人把 F 闸门加回 `output_format` 会立刻失败** ✅ |
| `--audit` | 门控齐全，`AUDIT_NOTES` 已空；阶段 G 如实出现在「恒定执行」列表中 ✅ |
| `vendor/ppt-master/projects/` | 前后快照一致，无残留 ✅ |

> ⚠️ **写这条自检时踩到的坑（已记入代码注释）**：`prepare_stage.py --set` 是
> **整份重写** —— 未指定的选项一律回落到默认值，**不是增量合并**。
> 所以回归守卫改完 `output_format`/`translate` 后，必须把本次自检后续步骤依赖的计划
> **原样重建**，否则后面几十条断言会因计划被改而连锁失败。

---

## 10. 复用检查清单

- [ ] 🆕 **脚本零硬编码**：作者姓名/ORCID/单位/署名词/阶段/待测 DOI 均由运行时传入；未修改 `scripts/` 内的任何硬编码值（v5）
- [ ] 🆕 **（最高优先级）全程未自编脚本从 HTTP 直链抓取原文全文**（§5.1 第二层）；**OA 全文同样走 CDP**；对出版社内容站 `curl` 探活零次
- [ ] 🆕 **第一层直连未越界**：所有 HTTP 直连请求的目标域名都在 `templates/直连白名单.json` 内；未把白名单外的域名当作"开源库"直接取
- [ ] 🆕 **第一层失败已转 CDP**：`fetch_direct.py` 的缺口清单（`.work_cdp/direct_failed.json`）已交给 `grab_abstracts.py --retry-failed`，未卡在第一层反复重试
- [ ] 🆕 **摘要来源已标注**：直连取得的摘要写进 `abstracts_openalex.json` 时带 `source` 字段，未冒充 CDP 路径产物
- [ ] 🆕 **未用 CDP `Input.*` 事件尝试通过人机验证**（鼠标自动化禁令）；遇到人机验证一律**转人工**（`--manual-wait=N` > 0 + 弹窗 + 窗口置前 + cookie 留存，N 必须为正）
- [ ] 🆕 未对验证类站点（ScienceDirect/OUP/SAGE/Wiley/Cell/ACS/RSC/Springer/T&F）做任何**直连**尝试（直连禁令）
- [ ] 🆕 自动化点击仅限**非验证类**元素（Cookie 弹窗 / 功能按钮 / 页内导航）
- [ ] 确认作者身份唯一（同名作者用 ORCID/单位/代表作交叉验证）
- [ ] 🆕 **已跑 `orcid_probe.py` 并把 ORCID 任职/教育时间线与作者主页履历逐条比对**（吻合才认定同一人）
- [ ] 🆕 **已跑 `disambiguate.py` 做同名消歧**；`--affil` 用的是**具体系/实验室名**而非整所大学名；「单位不匹配」与「单位字段缺失」已分别处理
- [ ] 🆕 **已跑 `enrich_crossref_authors.py`** 回填作者全名；著录未按空格猜「哪段是姓」
- [ ] 🆕 共同第一作者已核（Crossref 只给署名序，等贡献声明须查原文脚注）
- [ ] 🆕 **已按本流程执行，未擅自变更步骤**；如确需变更，已事先征得用户同意并在 §9 台账登记
- [ ] 🆕 **Cookie 弹窗自检通过**：`uv run --with websocket-client python 流程1/scripts/selftest_consent.py` 6/6（改动过 `CONSENT_JS` 则**必跑**）
- [ ] 🆕 CDP 启动参数含**弹窗抑制 6 项**（L0/L2/L3）；如需统一档位已设 `PAPER_CDP_LEVEL`
- [ ] 工作目录与 `<作者名>_paper_Exports` 已建立（历史 `<作者名>_Exports` 会被自动沿用）
- [ ] **多实例** CDP 已启动（`cdp_pool.py status` 全部 `[ON ]`），档位 L0–L3（推荐 L3 极速档）
- [ ] ORCID 全量成果已抓取（含翻页）并去重；**ORCID 成果为空时改以作者主页 + OpenAlex 为基准**
- [ ] DOI 全部经 Crossref 核实（记录失败与重试）
- [ ] CDP 批次使用 `--delay=25-55 --site-fail-limit=2 --manual-wait=N`（**N > 0**），未反复冲击同一站点
- [ ] 全文 PDF 以**题名**命名；未出现「改名后重复下载」（`pdf_index.json` 已登记）
- [ ] 无「补充材料/图注页」冒充正文（`verify_pdfs.py` 页数核查通过；残缺件已改名保留）
- [ ] 全文 PDF 已用 `store.validate_pdf` 校验；《全文清单.md》已生成并注明限制
- [ ] 🆕 **消歧结果已接入下游**：`scope.py` 生效 —— 下载侧跳过了他人论文、著录侧未收进正文列表；「数据缺失」类**未被误排除**
- [ ] 🆕 被排除的同名他人论文已在《论文列表》文末**逐条注明**（DOI + 题名 + 排除理由），非静默丢弃
- [ ] 列表为 GB/T 7714-2015 著录，含 DOI 与原文链接；**无 `et al..` 双句点**
- [ ] 🆕 已产出规范交付名 `<作者名>_论文列表.md`；交付目录里**没有** `_完整版.md` 重复文件
- [ ] 🆕 **阶段 H 用并行 subagent 执行**：每个 subagent 只读自己的 `task.json`、只写自己的 `result.json`，**未发任何网络请求**
- [ ] 🆕 解读卡含**四步齐备**：方法论 / 已验证结论 / 二八关键信息 / 量化提升数据（无数据则 `[]` 并说明，**未编数字**）
- [ ] 🆕 解读报告含：时间线要点串联 / 逐篇四步 / 关键词统计（词族合并 + 单条目）/ 研究方向 / 方法范式 / **缺卡与限制申报**
- [ ] 🆕 关键词统计未臆造词族合并（无词族表时只出单条目频次）
- [ ] 中译 PDF 全部通过 CJK 嵌入校验（`OK pages=… cjk_first_page=…`）
- [ ] **日志已留存**于 `.work_cdp/logs/`，异常可在其中定位
- [ ] 🆕 **阶段 0 已执行**：`.work_cdp/prepare_plan.json` 存在，7 个选择均已确认；
      后续每个阶段的 `--check` 闸门**逐条按计划执行**，未擅自开启被关闭的阶段
- [ ] 🆕 **闸门原因已复核**：`--show` 中每个 skip 都写明了「由谁关闭」，
      没有"原因不明"的跳过
- [ ] 🆕 **D2 已跑**（若 `reverse_refs=yes`）：《高质量文献列表》已产出，
      且**核心期刊判定用的是显式清单**（未改成前缀匹配）
- [ ] 🆕 **「综述」判定已如实标注为启发式**，理由逐条可查
- [ ] 🆕 **G2 已跑**（若 `refs_bibliography=yes`）：《代表性文献参考文献》含**两部分**
      （文章 → 参考文献）；IF 标注为「≈」；分区未命中写「未判定」而**非**「不满足」
- [ ] 🆕 **H2① 已跑**：去重记录与**被剔除清单逐条留档**（`dropped.json`），
      报告中单列一节，未静默丢弃
- [ ] 🆕 **H2 保留规则生效**：被作者 ≥3 篇引用的条目**强制保留**，未因阈值被误杀
- [ ] 🆕 **H2② 任务包命名只由 DOI 决定**（未按"第几篇"命名）
- [ ] 🆕 **H2③ 综述报告的「缺卡与限制申报」已如实列出**：
      缺卡数、schema 不合规数、**仅有题录无摘要**的条目均已披露
- [ ] 🆕 **证据层级未夸大**：综述报告明确声明结论建立在**摘要级**证据上
- [ ] 🆕 **受控标签已启用**：`.work_cdp/tag_vocabulary.json` 存在；
      解读卡标签从词表取词；**未归一的标签已在报告中保留并列出**
- [ ] 🆕 **词族表是按作者语料裁剪过的**（不是整份通用表直接套用）
- [ ] 🆕 **P 已跑**（若 `make_ppt=yes`）：`design_spec.md` 的 `Page Count` 与 §IX
      实际页数一致；`spec_lock.md` 的 `page_rhythm` 覆盖 P01 至末页无缺号
- [ ] 🆕 **未伪造 ppt-master 确认回执**：无 `confirm_ui/` 产物、未启动确认服务器
- [ ] 🆕 **阶段 P 的 SVG 由主 agent 手写**，未用脚本批量生成；
      `svg_quality_checker.py` 报 0 error；Step 7 三条命令**串行**执行
- [ ] 🆕 **未新增任何 HTTP 直连抓取**：D2/G2/H2 只走元数据 API（OpenAlex），
      P 阶段全程离线
- [ ] 🆕 **端到端自检已通过**：
      `uv run python 流程1/scripts/selftest_pipeline.py`
      （联网 33 项全绿；纯逻辑改动后可先跑 `--offline` 的 18 项）
- [ ] 🆕 **各新脚本的 `--self-test` 已通过**：
      `prepare_stage` / `refs_backfill` / `refs_bibliography` / `refs_curate` /
      `refs_prepare` / `refs_build` / `ppt_brief`
- [ ] 新增内容一律新建文件；`report_artifacts` 已申报交付物
