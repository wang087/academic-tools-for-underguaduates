---
name: author-paper-analysis
description: 面向本科生文献检索与科研辅助的全流程 Agent 工作流。给定用户的一句话研究意向，自动完成"大方向确定（拆解输入、匹配关键词群）→ 匹配细分专业（从学校学部官网抓取导师名单与研究方向、归类后等用户选择）→ 多源综述检索（按方向选库：Europe PMC / OpenAlex / arXiv / 预印本，综述优先）→ 领域概貌解读"，随后可选地接入作者论文分析（成果列表 / 全文获取 / 中文翻译 / 深度解读 / 研究方向综述 / 知识图谱 / PPT）。用于本科生进实验室选导师、大学生研究计划选题、"科学与社会"类课程文献查找与综述写作等场景。
---
# 本科生文献检索与科研辅助全流程（Author Paper Analysis · 本科版）

> ## 使用本流程的硬性要求（最高优先级，先读）
>
> **本文件夹（`本目录`）中的流程即为权威流程。执行本任务时必须遵守：**
>
> 1. **使用本流程** —— 依 `SKILL.md`（主流程）与各子流程
>    （`subagent-driven-review/SKILL.md` 阶段 H/H2、`ppt-integration/SKILL.md` 阶段 P、
>    `paper-card-integration/SKILL.md` 阶段 H 的 paper-card 引擎）逐步执行，
>    按 §4 的阶段推进，不跳步：
>
>    ```
>    【本科科研辅助前置链路】
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
>    **获得同意后**再改；改完**必须**把版本记录写进 [版本变动.md](版本变动.md)。
> 4. **`SKILL.md` 与 `README.md` 内不得写任何版本说明** ——
>    不写 `vN` 变更日志、不写「本次改动」、不写「详见 §9.x」、
>    不在标题里加版本号（如 `（v11：…）`）。
>    这两个文件只描述**当前有效的可操作流程**；历史沿革、缺陷修复过程、
>    被取代的旧规则，**一律归 [版本变动.md](版本变动.md)**。
>    执行流程时**不需要读** `版本变动.md` —— 它是冷证据，不参与决策。
> 5. **红线** —— §5.1 **分层获取策略**与 §5.2 **鼠标自动化政策**优先级最高；
>    一旦违反，按各自「违规处置」章节执行（**立即停止 + 如实报告**）。
>    §5.2 已**取消“一律禁止”**：允许用**宿主机级 `SendInput`** 点击人机验证复选框
>    （每篇最多 3 轮、失败即停、禁止无限试探）；CDP `Input.*` 仍不用于验证。
>    细则见 §5.2。
>    §5.1 是**分层**规则而非一刀切：摘要 / 综述题录 / 白名单开源库**允许直连**，
>    **原文全文必须走 CDP**；第一层取不到转 CDP。细则见 §5.1。
>    **学部官网师资页属"公开元数据"**，走第一层直连（阶段 S 的
>    `harvest_faculty.py` 即按此实现），无需 CDP。
> 6. **不要臆造事实** —— 导师方向、论文题录、引用数一切以抓取/接口返回为准；
>    解析不到就如实标「页面无该栏位」，**不得用印象补全**。
> 7. **[STOP] 禁止把实测结果写进本文件** ——
>    带日期的运行记录、覆盖率 / 耗时 / 篇数 / 字数 / 人数等结果数据、
>    逐站点或逐校的实测表、取证输出，**一律不得写入 `SKILL.md`**，
>    改写入 [实测结果汇总.md](实测结果汇总.md)（该文件是冷证据，执行流程时不需要读）。
>    本文件里**只保留一句话的坑（禁令）本身**：规则是什么、违反了会怎样。
>    判据：**「某月某日跑某事得到某数」是日志，进汇总文件；
>    「不许这样做，因为会那样」是规则，留在这里。**
>    阈值与上限（每批 ≤ 4、`--min-px-fig 60`、并发 = min(通道数, 站点数) 等）
>    属规则，留在本文件；**推出这些阈值的那批数据**进汇总文件。
> 8. **[STOP] 增加类操作必须在大阶段开始前显式确认，且必须分项确认** ——
>    「增加类操作」指一切**对流程的增加或分支**：新增功能、新增脚本、
>    新增分支或模式、在选择点**走向不同路径**、临时改动既定的阶段编排。
>
>    （1）**时点**：这类操作**不得**在阶段执行中途引入，必须在**该链路的第一个大阶段**
>    开始之前完成确认 —— 本科科研辅助前置链路 = **阶段 N**；
>    作者论文分析链路 = **阶段 0**。
>    中途才发现要新增，一律**停下来回到该起点**确认，不得边走边加。
>
>    （2）**分项**：确认必须**一项一问**，不得合并。
>    · **不得打包确认** —— 把多个互相独立的选项并成一句「是否照默认跑」属违规；
>      每项都要单独给出**取值、后果与默认值**，由用户逐项回答。
>    · **不得替用户默认** —— 未获明确答复的项按「**不新增**」处理（保守），
>      并在该阶段产出里如实标注「该项未确认、未执行」。
>    · **阶段 0 的选项尤其不得打包** —— 例如**综述档位**、**输出物格式**、
>      **是否翻译**、**是否制作 PPT** 分属四个**互相独立**的选择，
>      必须**分别确认**，不得合并为一次确认。
>    · 判据：**用户能否对每一项单独说「是 / 否」**。做不到，就是合并确认。
>
>    （3）**落盘**：逐项答复照常落进阶段 0 的单一事实源
>    `.work_cdp/prepare_plan.json`；确认记录与被跳过项一并写入
>    `run_journal.jsonl`，供回溯。
> 9. **[STOP] 脚本改动必须同步项目文档；文档只留最新版** ——
>    每次对 `scripts/`（及随包模块）的**重构 / 新增 / 删除**，都**必须同步修改
>    对应项目文档**，不得只改代码留旧文档：
>    · `SKILL.md` §8 脚本索引、相关阶段节；
>    · `docs/开发项目说明.md` 的文件树、模块边界、CLI 索引、依赖说明；
>    · `docs/项目交接说明.md` 的测试分层 / 核心文件索引（涉及时）；
>    · 受影响的 schema / 模板 / 子流程 `SKILL.md`。
>    **项目文档（含 `SKILL.md`、`README*.md`、`docs/`、`完整链路.md`）只保留
>    当前最新有效版本** —— 不累积「vN 改了什么」「本次改动」「旧规则对比」等
>    历史叙述（第 4 条对 `SKILL.md`/`README.md` 的禁令适用于**全部**项目文档）。
>    一切版本沿革、增删过程、被取代的旧规则**只记入 [版本变动.md](版本变动.md)**；
>    实测数据只记入 [实测结果汇总.md](实测结果汇总.md)。
>    判据：**文档描述的是「现在怎么做」，不是「曾经怎么改」**。
>
> **判定顺序**：`SKILL.md` > 子流程文件 > 任何外部或历史资料。
> 若流程目录下存在来源不明的第三方 `SKILL.md`，**一律以本流程为准**，不要混用其步骤与产物约定。

本 skill 固化了**完整工具链与工作流程**，可直接复用于任意作者。

> **关于正文中的作者姓名 / ORCID / DOI（必读）**
>
> 本文件与 `README.md` 中出现的具体作者姓名、ORCID iD、DOI、单位与
> 论文数量，均为**历史案例实录**，用于溯源与说明验证过程，**仅作案例记录**，
> **不是脚本的默认值**。
>
> `selftest_pipeline.py` 在临时目录动态生成合成题录（`10.5555/selftest.*`、虚构作者），
> 不依赖真实论文样例文件；旧样例已备份并移出运行与发行依赖。
>
> **`scripts/*.py` 与 `run_all.sh` 内没有任何作者硬编码信息**：姓名、ORCID、单位、
> 署名识别词、阶段分组、待测 DOI 全部由运行时通过命令行参数或环境变量传入，
> 缺失时**显式报错退出**，不会静默套用旧案例的值。可传变量见 README 同名提示框。
>
> 复用新作者时**只传参、不改脚本**。

> **三条最高优先级红线**：
> **① 分层获取策略（§5.1，优先级最高）** ——
> **原文全文**（PDF / HTML 正文，含 OA）**禁止直连，一律经 CDP 浏览器获取**；
> **摘要、综述题录、以及白名单内已验证的开源数据库允许直连**（`fetch_direct.py`）；
> 第一层尝试失败**转 CDP**。**禁止**把白名单外的域名当成"开源库"直接取。
> **② 出版社内容站禁直连** —— 验证类站点
> （ScienceDirect/Cell/OUP/SAGE/Wiley/ACS/RSC/Springer/T&F）
> 绝不用 `curl`/`urllib` 直连（含探活请求）；
> **③ 鼠标自动化政策（§5.2）** —— **允许**用**宿主机级 `SendInput`**
> 点击 Turnstile / 验证复选框（每篇最多 3 轮、失败即停、禁止无限试探）；
> CDP `Input.*` 事件（`isTrusted=True`）**仍不用于**验证，因为判定不依赖该次点击。
> 宿主级点击失败 → 转人工 / 换 profile / 换网络。

---

## 0. 快速开始（最小可用路径）

```bash
# -2) 首跑可选层探测（只读）：无产物目录 + 无 .work_cdp + 环境无 MinerU 时，
#     就「是否安装 MinerU」单独问用户一句，如实告知体积/损益/建议，再 record 落盘。
#     详见「可选解析层（分层共存）+ 首跑 MinerU 安装决策」节。
uv run python scripts/optional_layers.py probe --json
#     收齐答复后： optional_layers.py record --mineru-install=yes|no|defer

# -1) 阶段 0：准备阶段 —— 先在对话里收齐选择，再一条命令落盘
#     （不落盘也能跑，但后续阶段会**保守地跳过**可选环节并提示）
uv run python scripts/prepare_stage.py \
    --set output_format=markdown --set translate=no --set reverse_refs=yes \
    --set make_ppt=no --set refs_bibliography=yes --set curate_review=yes \
    --set interpret_tier=L2 --set review_interpret_tier=L3      # 解读档位
uv run python scripts/prepare_stage.py --check-all     # 看闸门 + 两处档位
uv run python scripts/prepare_stage.py --audit         # 门控齐全性自检
#   档位查询（stdout 只有 L1|L2|L3，便于 $() 捕获）：
#     uv run python scripts/prepare_stage.py --tier H     # 作者论文（默认 L2）
#     uv run python scripts/prepare_stage.py --tier H2    # 研究方向综述（默认 L3）
#     uv run python scripts/prepare_stage.py --tier R2    # 领域概貌（默认 L3）

# 0) 约定环境变量（所有脚本共用的可移植配置）
#
# ★★ 输出根（**用户指示，所有阶段一律遵守**）：
#     全部产物落在 `<年月日>/` 下，按天隔离；
#     设了 PAPER_AUTHOR 时再下一级作者目录（`.work_cdp/` 是按作者的，
#     同一天跑两位作者共用一级目录会互相覆盖）：
#         20261006/                 ← 阶段 N/S/R（还没有作者时）
#         20261006/<作者名>/         ← 阶段 0/A…I（设了 PAPER_AUTHOR 时）
#     例：
#       20261006/outputs/01_导师名单/
#       20261006/<作者名>/.work_cdp/
#       20261006/<作者名>/<作者名>_paper_Exports/
#
#     ★ 脚本默认值**已经内置**该规则（唯一实现：logutil.run_root()），
#       **不设 PAPER_WORKDIR 也会落对地方**。下面这行只在需要显式指定时用
#       （换机/换盘、或想把某次运行放到别处）：
export PAPER_WORKDIR="$PWD/$(date +%Y%m%d)"   # 可省略；省略时同上
export CDP_PORT=9222                           # 默认端口，可省略
# 作者信息一律运行时传入（脚本内零硬编码；缺失时脚本显式报错退出）
export PAPER_AUTHOR="<中文名>"                  # 产物文件名/抬头
export PAPER_AUTHOR_EN="<Given Family>"         # 英文名
export PAPER_ORCID="<ORCID iD>"                 # orcid_extract/pages 必需
export PAPER_NAME_TOKENS="<family, given>|<given family>"   # 本人署名识别（可选）
export PAPER_AFFIL="<单位/职称>"                 # 抬头（可选）

# 1) 建工作目录（全文目录规范名：<作者名>_paper_Exports）
#    store.exports_dir() 还会自动探测历史名 <作者名>_Exports 并沿用，二者不必手工迁移
mkdir -p "$PAPER_WORKDIR/${PAPER_AUTHOR}_paper_Exports"

# 2) 启动 CDP Chromium浏览器 —— 多实例并行（推荐 3 个；推荐 L3 极速档）
bash scripts/start_edge_cdp.sh "$PAPER_WORKDIR" 9222 3 3
#    或纯 Python 版本：
#    uv run --with websocket-client python scripts/cdp_pool.py up --n=3 --levels=3

# 3) 身份核验 + 抓 ORCID 成果列表（ORCID 为空时改以作者主页/OpenAlex 为基准，见 § 4 阶段 A）
uv run python scripts/orcid_probe.py "<ORCID>"                      # API：任职/教育/关键词/works
uv run --with websocket-client python scripts/orcid_extract.py "<ORCID>"   # 网页：权威成果集

# 4) Crossref 核实题录 + 回填作者全名
uv run python scripts/fetch_crossref_all.py && uv run python scripts/retry_crossref.py
uv run python scripts/enrich_crossref_authors.py --surname=<姓> --given=<名>

# 5) 同名消歧（作者聚合普遍过度合并，此步不可省）
uv run python scripts/disambiguate.py --surname=<姓> --given=<名> \
    --affil="<本人署名单位指纹 1>" --affil="<指纹 2>" \
    --anchor-doi=<确定无疑的代表作 DOI,逗号分隔>

# 6) OA 状态检查（仅用于判断获取难度，**不再据此走 HTTP 直取**）
uv run python scripts/unpaywall_check.py

# 7) 全文一律走 CDP 多实例（OA 与付费墙同等对待；遇真挑战时弹窗等人工）
#    注意：--manual-wait 必须 > 0，否则 CAPTCHA 弹窗链完全不触发（见 [版本变动.md](版本变动.md)）
uv run --with websocket-client --with pypdf python optional-cdp-fulltext/download_all_fulltexts.py \
    --n=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35

# 8) PDF 题名命名（新下载已自动题名；此步补齐历史文件并登记索引）
uv run python scripts/rename_to_title.py --dry-run && uv run python scripts/rename_to_title.py

# 9) 生成 GB/T 7714 列表 +《全文清单.md》+ 全文校验
uv run python scripts/build_list.py
uv run --with pypdf python scripts/verify_pdfs.py
uv run --with pypdf python scripts/inventory_build.py

# 10) 中文翻译素材链 → HTML → PDF（含 CJK 校验）
uv run --with pymupdf python scripts/prep_paper.py "<原文PDF>"        # 按页分节文本 + 图注页索引
uv run --with pymupdf python scripts/crop_by_images.py "<原文PDF>" "<figures.json>" "<图页>"
uv run python scripts/translation_build.py "<content.json>" "<figures.json>" "<翻译.html>"
uv run --with pypdf python scripts/render_html_pdf.py "<翻译.html>" "<输出PDF>"

# 11) 论文解读（阶段 H）：组装任务包 → 并行 subagent 精读 → 汇总
uv run --with pypdf --with pillow python scripts/interpret_prepare.py   # 产出 interpret/tasks/*.json
#    然后**在同一条消息内**分批并行分派 subagent（每批 ≤ 6），每篇一个，
#    各读自己的 task.json、各写自己的 result.json
#    （提示词模板见 subagent-driven-review/SKILL.md § 7）
uv run python scripts/interpret_build.py                      # 汇总为《<作者名>_论文解读.md》
```

---

## 1. 适用场景

> ### 目录与术语约定（**默认，无需每次说明**）
>
> **路径锚点**：所有相对路径与命令一律以**本流程根目录**（含 `SKILL.md`、`scripts/`
> 的目录，即本目录）为当前目录（CWD）。**本流程不依赖任何固定文件夹名** ——
> 目录可任意命名/移动/打包，内部引用均为相对路径。
>
> | 说法                        | 指什么                                                                                          |
> | --------------------------- | ----------------------------------------------------------------------------------------------- |
> | **"流程" / "本流程"** | 默认指**本流程根目录**（本目录）。用户说"改流程""跑流程"而未特别说明时，**一律针对本目录** |
> | **"工作文件夹"**      | 开发仓库根目录（本目录的上一级，如 `scientific-agent-skills/`）；**自包含发行时即本目录** |
> | **（自包含发行时）**  | 本目录已自带运行所需副本（`templates/`、`skills/paper-lookup`、`skills/paper-interpretation-writing`、`vendor/`、`研究方法与关键词总表.md`），无需仓库其余部分；"工作文件夹"一律理解为**本目录** |
> | **"工作根目录文件"**  | 如`研究方法与关键词总表.md`、`skills/`、`templates/` 等，均在**本目录**下       |
>
> 本流程的定位：**本科生文献检索与科研辅助 Agent**
>
> 面向的**真实痛点**（不是"帮教授分析论文"，而是本科生的三个典型处境）：
>
> | 痛点场景                                       | 用户实际会怎么说                                   | 本流程怎么接                                              |
> | ---------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------- |
> | **大学生研究计划**（国创/大创/校创）选题 | "我想做个跟阿尔茨海默有关的项目，不知道从哪下手"   | 阶段 N 拆解 → 阶段 S 给导师与方向 → 阶段 R 给该方向综述 |
> | **本科生进实验室**找导师                 | "我是中国科大生命科学与医学部的，想找做免疫的老师" | 阶段 S 直接抓该学部全部导师并按方向归类                   |
> | **"科学与社会"类课程**文献查找           | "要写一篇关于基因编辑伦理的综述，找几篇权威综述"   | 阶段 N/R 走综述优先的检索，产出领域概貌                   |
>
> **风格要求**：面向**没有科研经验的本科生**。
> 解释术语、给出"为什么这步重要"、避免默认对方懂 PubMed 检索式或影响因子概念。
> 产出物要能直接用于**联系导师的邮件**、**开题报告**或**课程论文**。

| 场景                             | 说明                                                                   |
| -------------------------------- | ---------------------------------------------------------------------- |
| **本科科研入门（主场景）** | 一句话意向 → 大方向 → 细分专业与导师 → 该领域优质综述 → 领域概貌   |
| **选导师 / 进实验室**      | 按学部抓取导师名单与研究方向，归类后供选择                             |
| **课程论文 / 综述作业**    | 综述优先检索 + 证据可溯源的概貌解读                                    |
| 分析某作者全部论文               | 输入姓名/主页/ORCID，产出成果列表 + 解读报告（原有链路）               |
| 成果列表整理                     | 产出 GB/T 7714-2015 标准著录（含 DOI、原文链接）                       |
| 全文获取与翻译                   | 开放获取（OA）论文下载全文 → 中文翻译 → PDF                          |
| 绕反爬取数据                     | 出版社站点按分层策略用选定的Chromium浏览器CDP；验证码仅人工处理，禁止自动绕过 |

---

## 2. 环境与前置条件

| 项       | 要求                                                                               |
| -------- | ---------------------------------------------------------------------------------- |
| 操作系统 | Windows（本流程基于 win32 + Git Bash）                                             |
| 浏览器 | Chromium兼容浏览器（Chrome/Chromium/Edge），由PAPER_BROWSER或--browser选择 |
| Python   | 通过`uv` 运行，无需预装依赖（`uv run --with <pkg> python`）                    |
| 必备包   | `websocket-client`（CDP 通信）、`pypdf`（PDF 校验）                            |
| 可选     | Chrome（`--print-to-pdf` 渲染中文 PDF）、`pandoc`                              |

---


> ## [STOP] 加载纪律（上下文管理）
>
> **本主文件是常驻权威核心**（硬性要求、红线 §5.1/§5.2、阶段路由、闸门/数据契约、交付清单）。
> **禁止一次性全量加载所有子 skill 与阶段文件** —— 那会占满上下文、加速压缩。
> 各阶段细节已拆到 `stages/`（按链分组）与既有子流程 skill；**进入某阶段时才读对应文件**：
> 见 §4 路由表。红线与契约**只在本主文件**，阶段文件引用而不复制。
> 脚本/CLI 速查见 `docs/脚本与CLI索引.md`（按需读）；工具清单与关键坑见 `docs/参考_工具与关键坑.md`。

> ## [STOP] 命令编写纪律（跨阶段 · 写/跑命令前必读，全是踩过的坑）
>
> **通用（bash 与 pwsh 都适用）**
> 1. **内联命令只用 ASCII**：不写中文、不用花引号（`‘ ’ “ ”`）、不在字符串里再套同类引号。
>    中文说明放命令**外**的注释/正文；命令里的中文路径/参数用占位符（`<作者名>`）并在执行前替换为真实值。
> 2. **路径优先相对**（锚点＝流程根目录，已文件夹名无关）；含空格/中文的路径**加引号**。
>    已知 `mineru_llama_cpp` 原生 pyd 按 ANSI/cp936 转路径，**含中文路径会崩**——MinerU 须走 ASCII junction。
> 3. **列目录必须限深度 + 加过滤**（如 `find . -maxdepth 3 -name "*.pdf"`），禁止无界递归扫全树。
> 4. **外部命令 stderr 先落盘再截头部**（`cmd > out.log 2>&1` 后读头部），避免输出洪水刷屏/撑爆上下文。
> 5. **写命令前先核 `--help`/argparse**：部分脚本**无 argparse**，`--help` 会**启动真任务**
>    （如 `prepare_stage.py` 无参按默认写计划、`knowledge_graph.py` 未知 `--help` 仍执行）；先确认入口是否支持 `--help`。
>
> **PowerShell 专项（Windows 默认 PS 5.1，非 PS7）**
> 6. **不写 PS7 专有语法**：三元 `?:`、空合并 `??`、管道链 `&&`/`||` 在 PS5.1 报错；用 `if/else`、分号或分行。
> 7. **带中文或稍复杂逻辑 → 先写 `.ps1` 文件（UTF-8 with BOM）再 `pwsh -File` 执行**，不要内联 `-Command` 塞中文。
>    PS5.1 按 ANSI 读**无 BOM** 的中文脚本会乱码（本流程 `install.ps1`/`start.ps1` 按 pwsh7 写，PS5.1 须用 pwsh7 跑）。
> 8. **读文本文件一律 `-Encoding UTF8`**（`Get-Content -Encoding UTF8`）：PS5.1 默认 GBK，会把 UTF-8 读成乱码并误判"文件损坏"。
> 9. **引号转义**：pwsh 字面量用**单引号**（不插值）；需插值用双引号并转义内部 `"` 为 `` `" ``；
>    bash 双引号内注意 `$`、`` ` ``、`\"`。嵌套引号易错——复杂传参改用脚本文件而非内联拼接。
>
> **Python / 依赖 / CDP 专项**
> 10. **`uv run --with` 带齐依赖**：抽取/校验需 `pypdf`+`pillow`+`pymupdf`，CDP 需 `websocket-client`。
>     **缺 pypdf 会静默判全部 PDF 损坏**（`store.validate_pdf` 吞 `ImportError`→`unparsed:ModuleNotFoundError`），
>     不是真损坏；裸调用下载器务必 `--with pypdf`（`run_all.sh` 已带）。
> 11. **CDP 宿主机自动点击默认关闭**：须设 `UC_HOST_CLICK=1`（宽限期提前点再加 `UC_HOST_CLICK_EARLY=1`）；
>     且 `document.hasFocus()` 须为 true（窗口真在前台）才点——两者缺一即不点（见阶段 E / §5.2）。
> 12. **stdout 契约**：`prepare_stage.py --tier/--engine` 的 stdout **只返回枚举值**，日志走 stderr；
>     用 `$(… --tier H)` 捕获时勿混入日志行。

> **§3 工具清单** —— 细节见 [`docs/参考_工具与关键坑.md`](docs/参考_工具与关键坑.md)（进入本阶段再读，勿全量加载）。

## 4. 标准工作流

**阶段顺序（物理顺序以此为准）**：

```
【前置链路：本科科研辅助 —— 从一句话意向走到"领域概貌"】
N 大方向确定（无交互：拆解用户输入 + 匹配关键词群）
  → S 匹配细分专业（抓学部导师名单 → 按方向归类 → **等用户选择**）
  → R 多源综述检索（按方向选库 + 综述优先）
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

> [WARN] **D2 / G2 / H2 / K / P 是插入式子阶段**：编号后缀只表示它**紧邻**哪个主阶段，
> **不表示可选**。它们是否执行由**阶段 0 的准备计划**决定（见阶段 0 的闸门表）。
> 阶段 I 仍是最后一步。
>
> **两条链路的关系**：本科入门场景通常**只走 N → S → R → R2** 就到交付
> （领域概貌 + 导师候选 + 综述清单）。当用户**已经锁定某位导师**、想深挖
> 这位老师的成果时，再从阶段 0 接上原有链路。**不要**默认跑全程——
> 原有链路的全文下载/翻译/PPT 对本科生选题阶段是过重的。

---


**阶段路由表（进入该阶段才读对应文件；不要全量加载）**

| 阶段 | 细节文件 |
|---|---|
| N | `stages/prelude-N.md` |
| S | `stages/prelude-S.md` |
| R / R① / R①.5 / R2 | `stages/prelude-R.md`（scopus-wos 可选增强见其中指针） |
| 0 | `stages/author-0.md` |
| A / B / C / D / D2 | `stages/author-acquire.md` |
| E（含边下载边抽取） | `stages/author-fulltext.md` + `optional-cdp-fulltext/SKILL.md` |
| F / G / G2 | `stages/author-translate.md` |
| H / H2（含并行轮询） | `stages/author-interpret.md` + `subagent-driven-review/SKILL.md` |
| K | `stages/author-graph.md` |
| P | `stages/author-delivery.md` + `ppt-integration/SKILL.md` |
| I | `stages/author-delivery.md` |
| H 深读卡引擎 | `paper-card-integration/SKILL.md` |

> **阶段 N** —— 细节见 [`stages/prelude-N.md`](stages/prelude-N.md)（进入本阶段再读，勿全量加载）。

> **§4.1 并行 subagent 轮询（H/H2/R2 共用）** —— 细节见 [`stages/author-interpret.md`](stages/author-interpret.md)（进入本阶段再读，勿全量加载）。

> **§4.2 边下载边抽取** —— 细节见 [`stages/author-fulltext.md`](stages/author-fulltext.md)（进入本阶段再读，勿全量加载）。

### 可选解析层（分层共存）+ 首跑 MinerU 安装决策

PDF 解析按**三层共存**组织，主链路不变，两层皆可选：

| 层 | 实现 | 适用 | 必装 |
| --- | --- | --- | --- |
| 主链路 | fitz/pypdf（`prep_paper.py` / `extract_parallel.py`） | 数字版 PDF 按页文本 | 是 |
| 可选轻量层 | opendataloader（`vendor/opendataloader/` + `opendataloader_extract.py`） | 数字版确定性 Markdown + 逐元素 bbox JSON，交叉校验/备选引擎；需 Java 11+，**本地模式无 OCR** | 否 |
| 可选深解析层 | MinerU（环境级，`optional_layers.py` 探测） | 扫描件 OCR / 复杂表格 / 公式 | 否 |

> [STOP] **MinerU 不是必装项，是否安装属于「只有用户能定」的环境级选择**，
> 按文件头第 8 条「增加类操作必须分项确认」处理：
>
> - **首跑判据**（三者同时成立才触发询问）：① 工作目录无产物目录
>   （`*_paper_Exports` / `*_Exports` / `outputs`）；② 无临时目录（`.work_cdp/`）；
>   ③ 环境无 MinerU。一条命令查清：
>   `uv run python scripts/optional_layers.py probe [--json]`。
> - **一项一问**：就「是否安装 MinerU」**单独**问用户一句，不得与其他选项打包。
>   问之前先跑 `probe`，**如实告知体积、损益与建议**（量级、边际占用、可省冗余、
>   收益与代价都在 probe 输出里；具体实测证据见 [实测结果汇总.md](实测结果汇总.md)）。
> - **不替用户默认装**：未获明确答复按「不安装」处理；**脚本不代装任何东西**，
>   只探测与记录。安装由用户自行完成。
> - **落盘**：答复用 `optional_layers.py record --mineru-install=yes|no|defer`
>   写进 `.work_cdp/optional_layers.json`，并记入 `run_journal.jsonl`。
>   **记录过决定后不再重复追问**（`probe` 的 `should_ask_mineru_install` 转 false）。
> - **建议口径**：数字版 PDF 为主 → 不必装（fitz 主链 + opendataloader 可选层已够）；
>   确有扫描件 / 复杂表格 / 公式深解析需求 → 再装。MinerU 在含中文路径上有编码 bug，
>   装前须先解决（ASCII junction）。

```bash
# 首跑探测（只读，不创建目录、不联网）：给出是否需询问 + 体积/损益/建议
uv run python scripts/optional_layers.py probe --json
# 收齐用户答复后落盘（yes/no/defer）
uv run python scripts/optional_layers.py record --mineru-install=no
# 可选轻量层：数字版 PDF → Markdown + bbox JSON（默认 .work_cdp/odl/）
uv run python scripts/opendataloader_extract.py "<pdf>" --format markdown,json
uv run python scripts/opendataloader_extract.py --detect   # 0=可用 / 3=不可用
```

> opendataloader 已**完整解压整合**进 `vendor/opendataloader/`（jar + LICENSE +
> NOTICE + THIRD_PARTY，Apache-2.0），不走 zip 运行时解压；`opendataloader_extract.py`
> 是唯一 adapter，缺 Java/jar 时诚实降级（退出 2/3），主链路 fitz 不受影响。
> 深解析（扫描件/表格/公式）不在本层范围 —— 走 MinerU。


> **阶段 S** —— 细节见 [`stages/prelude-S.md`](stages/prelude-S.md)（进入本阶段再读，勿全量加载）。

> **阶段 R/R①/R①.5/R2** —— 细节见 [`stages/prelude-R.md`](stages/prelude-R.md)（进入本阶段再读，勿全量加载）。

> **阶段 0** —— 细节见 [`stages/author-0.md`](stages/author-0.md)（进入本阶段再读，勿全量加载）。

> **阶段 A/B/C/D/D2** —— 细节见 [`stages/author-acquire.md`](stages/author-acquire.md)（进入本阶段再读，勿全量加载）。

> **阶段 E** —— 细节见 [`stages/author-fulltext.md`](stages/author-fulltext.md)（进入本阶段再读，勿全量加载）。

> **阶段 F/G/G2** —— 细节见 [`stages/author-translate.md`](stages/author-translate.md)（进入本阶段再读，勿全量加载）。

> **阶段 H/H2** —— 细节见 [`stages/author-interpret.md`](stages/author-interpret.md)（进入本阶段再读，勿全量加载）。

> **阶段 K** —— 细节见 [`stages/author-graph.md`](stages/author-graph.md)（进入本阶段再读，勿全量加载）。

> **阶段 P/I** —— 细节见 [`stages/author-delivery.md`](stages/author-delivery.md)（进入本阶段再读，勿全量加载）。

> **§5 关键坑与对策** —— 细节见 [`docs/参考_工具与关键坑.md`](docs/参考_工具与关键坑.md)（进入本阶段再读，勿全量加载）。

## 5.1 [STOP] 分层获取策略（**最高优先级** —— 优先级高于其余所有禁令）

> **立规来源**

> **立规依据**：「对于获取**原文**的需求仍然禁止直链抓取；
> 但是对于**摘要、综述**或工作文件夹下**验证可行的开源数据库**可以直接获取，
> 减轻压力。同时若直接获取尝试失败则转向 CDP 获取。」
>
> **一句话**：**拿"原文"走 CDP；拿"摘要 / 综述题录 / 白名单开源库"直接取，
> 取不到再转 CDP。**

### 一、分层表（**这张表就是全部规则**）

| 层               | 内容类别                                          | 获取方式                             | 实现                                                                                           |
| ---------------- | ------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| **第一层** | 元数据（题录、作者、被引数、参考文献关系）        | [OK]**允许直连**               | `fetch_crossref_all.py` / `orcid_probe.py` / `unpaywall_check.py` / `refs_backfill.py` |
| **第一层** | **摘要**                                    | [OK]**允许直连**               | `fetch_direct.py`（多源自动降级）→ 失败转第二层                                             |
| **第一层** | **综述**（题录 / 摘要级）                   | [OK]**允许直连**               | 同上；综述的**正文**属第二层                                                             |
| **第一层** | 白名单内的**开源数据库**                    | [OK]**允许直连**               | `templates/直连白名单.json` 逐条列举（见下）                                                 |
| **第二层** | **原文全文**（PDF / HTML 正文），含 OA 文章 | [STOP]**禁止直连，一律走 CDP** | **可选模块** `optional-cdp-fulltext/`：`download_all_fulltexts.py` / `save_pdf_via_browser.py`（**未安装 → 本层不可用，改走备用链路并如实标注**） |
| **回退**   | 第一层尝试失败                                    | ->**转向 CDP**（模块在时）      | `fetch_direct.py` 写出缺口清单 → `grab_abstracts.py --retry-failed`；模块不在 → 缺口交 `fulltext_router.py --fallback` / `preflight_fallback.py` |

> **判定分界**：这次请求**要和不要拿"论文正文"**？
> 拿正文（PDF / HTML 全文）→ **必须 CDP**；拿摘要 / 题录 / 结构化元数据 → **第一层直连**。

### 二、第一层的执行方式（**不要临场判断**）

「工作文件夹下验证可行的开源数据库」这句话由
**`templates/直连白名单.json`** 落成可执行判据：白名单逐条给出
`domain` / `purpose` / `content_class` / `oa_only` / `verified_by` / `verified_at`。

```bash
# 先问策略（不联网，退出码 0=允许 / 3=禁止）
uv run python scripts/fetch_direct.py --check-domain="<URL>"

# 批量取摘要（多源自动降级：S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed）
uv run python scripts/fetch_direct.py
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

| # | 禁止行为                                              | 说明                                                                           |
| - | ----------------------------------------------------- | ------------------------------------------------------------------------------ |
| 1 | 自编脚本直链下载**原文全文**                    | 含`urllib` / `requests` / `curl` / `wget` / PowerShell，含"临时试一下" |
| 2 | 用`curl -I` / `HEAD` 对**出版社内容站**探活 | 连通性由浏览器判定（`detect.navStatus`）                                     |
| 3 | 以"OA / 开放获取"为由走纯 HTTP 直取**全文**     | **OA 全文也走 CDP**；开放获取只意味着无需订阅                            |
| 4 | 自建下载器 / 第三方下载工具                           | 一律用`download_all_fulltexts.py` / `save_pdf_via_browser.py`              |
| 5 | 把白名单外的域名当作"开源库"直接取                    | 白名单即边界；想加就先按收录判据补进白名单并让`--self-test` 通过             |

### 五、违规处置（不可跳过）

1. **立即停止**该脚本与相关进程，删除临时下载器；
2. **如实报告**：抓了什么、来自哪个站点、属于哪一层、影响范围；
3. 原文类改用 CDP 路径重做；已直连获取的**摘要/元数据**保留但**标注来源**，
   不得冒充 CDP 路径产物；
4. 若站点已因直连被计数，按「封禁应急规则」处理。

---

---

## 5.2 鼠标自动化政策（2026-11-09 修订；原“鼠标自动化禁令”已取消）

> **修订依据**：2026-11-07~09 实测。CDP `Input.dispatchMouseEvent` 虽然
> `isTrusted=true`，但 Turnstile 不放行（token=0）；**宿主机级 `SendInput`**
> 在 ScienceDirect / OUP / ACS 均放行，且不点击的对照组 20 s 内都不放行。
> 定制 Chromium（`navigator.webdriver=false`）下，刘波 15 篇抓取 15/15 通过。
> 取证见 [实测结果汇总.md](实测结果汇总.md#m-2)。

### 一、允许（新政策）

| 场景 | 做法 |
|---|---|
| Turnstile / 人机验证**复选框** | **宿主机级** `user32!SendInput` 绝对坐标点击；每次挑战**最多 3 轮**，失败即停 |
| 等待验证组件渲染 | 逐秒截图轮询；**连续两帧**框位置一致再点（复选框常 3–8 s 才出现） |
| Cookie / 同意书 / 页面功能按钮 | 既有 `CONSENT_JS` / `dismiss_consent()`；必要时同样可用宿主机点击 |

### 二、仍禁止（红线）

| # | 禁止行为 | 原因 |
|---|---|---|
| 1 | 用 CDP `Input.dispatchMouseEvent` / `Input.*` 过验证 | 实测无效（`isTrusted` 为真但 token=0） |
| 2 | 合成**滑块 / 拼图 / 按住**手势 | 属绕过安全机制，且最易触发升级封禁 |
| 3 | 对同一验证页**循环 / 退避式试探**（超过 2 次点击） | 单次挑战会升级为站点级封禁 |
| 4 | 页内 JS 伪造（`element.click()`、改写 `isTrusted`、劫持回调） | 既无效又违规 |
| 5 | 无失败熔断的无人值守验证点击（定时任务里无限重试） | 违背「失败即停」 |

### 三、判定分界与**桌面前提**

- **前提（硬性）**：宿主机点击要求**交互式桌面**（`GetForegroundWindow() != 0`）。
  锁屏 / 已断开的 RDP 会话里按键事件被拒绝（`SendInput` 返回 `ERROR_ACCESS_DENIED`），
  此时**不要重试**，直接转人工或等桌面恢复；批量任务应在有人值守/桌面已连接时跑。
- **看输入通道**：`SendInput`（系统输入队列）允许用于验证；`Input.dispatch*`（DevTools）不允许。
- **看次数**：一次挑战 ≤3 轮点击；第 3 轮仍失败 → 停止，转人工或换 profile。
- **看对象**：只点复选框本身；不得点“重试 / 换题 / 提交”等验证流程按钮。

### 四、失败处置（不可跳过）

1. **立即停止**该站点的自动点击，终止该站点的后续重试；
2. **如实记录**：站点、点击次数、点击前后标题、token/状态；
3. **转人工**：`--manual-wait=N` → 弹窗（TTL 自动关闭）+ 窗口置前，人点一次；
   放行 cookie 留在该 profile，后续同站直接通过；
4. 仍不可得 → 换新 profile / 机构 VPN / 图书馆代查，或在《全文清单.md》注明限制。

### 五、实现位置

- `optional-cdp-fulltext/turnstile_click.py`：截图 → 阈值 180 → 22–50 px 空心方框 →
  连续两帧确认 → `SendInput` 点击 → 等标题脱离挑战；每次挑战 ≤3 轮。
  可直接跑：`python turnstile_click.py [--keyword 请稍候] [--dry]`。
- `optional-cdp-fulltext/host_click.py`：窗口/客户区截图/OCR/`SendInput` 基础函数。
- `optional-cdp-fulltext/download_all_fulltexts.py`：**自动阶段**最多 3 轮宿主机
  点击、不弹窗；全部篇目结束后记录总数并弹 Yes/No，用户确认后才对未获取篇目
  进入**人工阶段**（逐篇置前 + 弹窗提示 DOI）。
  并发验证由全局 `VERIFY_LOCK` 串行；每次点击前用 CDP 校验目标标签页
  `visibilityState=visible`、`hasFocus=true` 且 URL 仍含该 DOI；
  后台标签 `not-ready/tiny` 时先置前重判；新开/关闭标签与验证互斥。
- 取件四策略：`optional-cdp-fulltext/save_pdf_via_browser.py`
  （Network 捕获 → 落地页 fetch → 重定向 fetch → 渲染兜底）。
- 资产 CDN 验证：`optional-cdp-fulltext/asset_pdf.py`（`save_with_asset`）——
  导航 PDF URL → 识别 `cfts`/`Request Verification` → 宿主机点击 → Network 抓 PDF → 渲染兜底。

---

## 6. 日志与可诊断性

### 6.1 每脚本文本日志（`logutil.py`）

所有脚本统一使用 `logutil.py`：

- **落盘位置**：`<WORKDIR>/.work_cdp/logs/<脚本名>_<时间戳>.log`
- **指针文件**：`.work_cdp/logs/latest_<脚本名>.log` —— [WARN] **它是「指针」，内容是该日志的路径，不是日志本身**
- **浏览器日志**：`.work_cdp/logs/edge_<port>.log`（每个实例一份）
- **格式**：`[时:分:秒 +运行秒] 级别 内容`，同时输出到控制台与文件

```bash
# 注意：latest_<name>.log 里存的是**路径**，直接 tail 只会看到一行路径。
#    正确姿势是先取路径再 tail（下面这行是通用写法）：
tail -f "$(cat .work_cdp/logs/latest_download.log)"          # 实时看下载
grep -E "WARN|ERROR" "$(cat .work_cdp/logs/latest_download.log)"   # 只看问题
ls -t .work_cdp/logs/*.log | head                            # 最近写过的日志
```

> 日志器内部用 `errors="replace"` 写文件、控制台包一层容错包装，
> **不会**因为某个生僻字符抛 `UnicodeEncodeError` 而中断整个流程。

### 6.2 操作流水 `run_journal.jsonl`（用于**回溯**）

每脚本日志各写各的、时间戳分散，**串不成一条链** —— 长流程出问题时最难的不是修，
而是**还原现场**：这一轮到底跑了哪些脚本、参数是什么、哪一步开始不对。

`logutil.journal(stage, action, **fields)` 把关键动作按时间顺序固化到
`<WORKDIR>/.work_cdp/logs/run_journal.jsonl`（一行一条 JSON，**只追加**）：

```bash
uv run python scripts/journal_view.py                # 全部，按时间列出
uv run python scripts/journal_view.py --stage=E      # 只看阶段 E（全文下载）
uv run python scripts/journal_view.py --action=download.fail   # 只看失败
uv run python scripts/journal_view.py --tail=40      # 只看最后 40 条
uv run python scripts/journal_view.py --json         # 原样输出，供二次处理
```

已接入的阶段与动作：

| 阶段 | action                                                                                                                    | 关键字段                                                             |
| ---- | ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| C    | `orcid.extract.start` / `orcid.navigate` / `orcid.expand.narrow` / `orcid.extract.done` / `orcid.extract.empty` | orcid, works, cards, text_len, elapsed_s                             |
| D    | `crossref.fetch.start` / `crossref.fetch.done` / `crossref.enrich.done` / `disambiguate.done`                     | dois, ok, failed, updated, confirmed, excluded,**undecidable** |
| E    | `download.start` / `download.pdf` / `download.fail` / `download.done`                                             | doi, file, size_kb, src, pages, reason, circuit                      |
| G    | `list.build` / `list.excluded`                                                                                        | entries, excluded, disambiguation                                    |

> **两条硬性约定**：① 写 journal **绝不影响主流程**（任何异常都被吞掉 —— 日志是旁路，
> 不是依赖）；② **只记录已发生的事实**，不写预期值，否则回溯会自欺。
>
> journal 的 `undecidable`（数据缺失、无法判定）与 `excluded`（肯定性判定为他人）
> 是**两个不同的字段** —— 前者不是排除，混为一谈会让人误以为成果被剔除。

---

## 7. 产出物规范

> ★★ **输出根（用户指示，所有阶段一律遵守）**：全部产物落在
> **`<年月日>/`** 下，按天隔离；设了 `PAPER_AUTHOR` 时再下一级作者目录。
> 脚本默认值已内置该规则（唯一实现 `logutil.run_root()`），无需设置任何环境变量。
>
> ```
> 20261006/                     ← 当天全部产出（阶段 N/S/R 无作者时）
> ├── outputs/                        ← 阶段 S / R 的报告与检索计划
> │   ├── 01_导师名单/
> │   └── 07_检索/
> └── <作者名>/                        ← 设了 PAPER_AUTHOR 时（阶段 0/A…I）
>     ├── .work_cdp/
>     └── <作者名>_paper_Exports/
> ```
>
> 下面这份是**作者目录内部**的结构（即上图的 `<作者名>/`）：

```
<年月日>/<作者名>/
├── <作者名>_paper_Exports/                # 原文全文 PDF（规范目录名）
│   ├── <年份>_<论文标题>.pdf               #   题名命名（可读、可去重）
│   ├── 【补充材料】<年份>_<标题>.pdf        #   仅补充材料/图注页的残缺件
│   └── 全文清单.md                        #   逐篇状态 + 来源 + 页数 + 未获取原因
├── <作者名>_论文列表.md                    # 规范交付名（GB/T 7714-2015 + DOI + 原文链接）
├── <作者名>_论文解读.md                    # 解读（时间线/逐篇四步/关键词/方向/范式）
├── <作者名>_交付物索引.md                   # 交付总览 + 核验链 + 复现步骤
├── <作者名>_论文翻译_<刊><年>_中文版.pdf     # 全文中译（源文件按题名、译文按刊名+年份）
├── <作者名>_高质量文献列表.md               # 阶段 D2：反向查得的综述网络（综述优先）
├── <作者名>_代表性文献参考文献.md            # 阶段 G2：文章 → 参考文献（GB/T 7714）
├── <作者名>_研究方向文献终表.md              # 阶段 H2①：去重 + 相关性整理后的最终列表
├── <作者名>_研究方向综述.md                 # 阶段 H2③：review 结果（单一文件）
├── <作者名>_知识图谱.md                     # 阶段 K：三图说明 + 聚类解读 + 冲突线索 + 剔除记录
├── <作者名>_汇报PPT_<画布>_<日期>/           # 阶段 P：PPT 项目（`make_ppt=yes` 时；见下）
│   └── exports/<项目名>_<时间戳>.pptx        #   **最终 PPTX 交付物**（＋ validation/*.report.json）
├── .work_cdp/                             # 中间数据 + logs/ 日志
│   ├── prepare_plan.json                  #   阶段 0：准备计划 + 阶段闸门（单一事实源）
│   ├── crossref_all.json                  #   题录核实结果（题录总表）
│   ├── disambiguated.json                 #   同名消歧结果（confirmed/review/dropped + code）
│   ├── periods.json                       #   阶段分组（可选，驱动列表与清单的分组）
│   ├── keyword_families.json              #   语义词族表（可选，驱动关键词合并计数）
│   ├── tag_vocabulary.json                #   受控标签词表（消除并行 subagent 标签漂移）
│   ├── journal_tiers.json                 #   中科院分区表（可选，**覆盖**内置种子表）
│   ├── unpaywall.json                     #   OA 状态
│   ├── abstracts_openalex.json            #   摘要语料（解读用）
│   ├── fulltext_state.json                #   全文下载状态（可断点续传）
│   ├── pdf_index.json                     #   DOI → 文件名 登记表（题名命名去重）
│   ├── interpret/                         #   阶段 H：tasks/ + results/ + manifest.json
│   ├── refs/                              #   阶段 D2/G2/H2：见下方结构
│   ├── graph/                             #   阶段 K：*.svg|png + graph_data.json + fulltext/
│   ├── translations/*.html                #   中译 HTML 中间件
│   └── logs/                              #   全部日志（含 edge_<port>.log）
└──                                   # 本 skill
```

**阶段 P 的 PPTX 落点**（`make_ppt=yes` 时）：项目落**流程输出根**（默认 `PAPER_WORKDIR`，
即上图的 `<作者名>/`），**不落** `vendor/ppt-master/projects/` ——
产物混在依赖目录里不利交付，也不该随发行包分发。

```
<作者名>_汇报PPT_<画布>_<日期>/
├── sources/<作者名>_汇报材料.md            # 由《论文解读》+《研究方向综述》汇编
├── design_spec.md                         # 简化 Strategist 的确定性产物（I–X 全节）
├── spec_lock.md                           # 同上（canvas/colors/typography/page_rhythm…）
├── svg_output/*.svg                       # 由主 agent **手写**（不得脚本批量生成）
└── exports/<项目名>_<时间戳>.pptx          # **最终 PPTX 交付物**（＋ validation/*.report.json）
```

> `--outdir=<路径>` 覆盖输出根（默认 `<PAPER_WORKDIR>`）；
> `--project-name=<名>` 默认 `<作者名>_汇报PPT`，ppt-master 的 project_manager
> 会再拼 `_<画布>_<日期>`。

**`.work_cdp/refs/`**
```
.work_cdp/refs/
├── source_refs.json      # 作者论文 DOI → 其参考文献（含 source_id / venue）
├── refs_pool.json        # 参考文献去重题录池（含摘要、被引、期刊、类型）
├── high_quality.json     # D2 产出：高质量筛选结果（G2 / H2 的唯一输入）
├── representative.json   # G2 产出：代表性文献 + 入选理由 + IF 判定
├── final_list.json       # H2① 产出：最终文献列表（含评分分量与方向画像）
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


> **§8 脚本索引** —— 细节见 [`docs/脚本与CLI索引.md`](docs/脚本与CLI索引.md)（进入本阶段再读，勿全量加载）。

## 10. 复用与改动检查清单

> 分两组：**A 改动流程后必查**（开发维护）；**B 跑流程 / 复用新作者时必查**（执行）。
> 红线详见 §5.1/§5.2；阶段细节按 §4 路由表读 `stages/`。本清单只留当前有效项，
> 历史沿革见 `版本变动.md`，实测数据见 `实测结果汇总.md`。

### A. 改动流程后必查（开发）

- [ ] [STOP] **文档只留最新版**：未在 `SKILL.md` / `README*.md` / `docs/` 写版本叙述（无 `vN` 日志、无「本次改动」、标题无版本号）（第 4/9 条）
- [ ] [STOP] **改动已记入 [版本变动.md](版本变动.md)**（行为/功能变更）；实测数据归 [实测结果汇总.md](实测结果汇总.md)
- [ ] [STOP] **脚本重构/增删已同步项目文档（第 9 条）**：SKILL 路由/阶段文件、`docs/开发项目说明.md`、`docs/项目交接说明.md`、受影响 schema/模板/子流程规范均已更新
- [ ] [STOP] **脚本零硬编码**：作者姓名/ORCID/单位/署名词/阶段/待测 DOI 均运行时传入
- [ ] **命令符合「命令编写纪律」**：ASCII 内联、PS5.1 兼容、`.ps1` UTF-8 BOM、读文件 `-Encoding UTF8`、列目录限深、stderr 先落盘
- [ ] **专项自检通过**（按改动模块选跑）：`prepare_stage.py --audit`、`nature_bridge.py --self-test`、`optional_layers.py --self-test`、`opendataloader_extract.py --self-test`、`knowledge_graph.py --self-test`、`selftest_keyword_terms.py`、`selftest_vosviewer_pipeline.py`、`selftest_fallback_wiring.py` 等
- [ ] **`selftest_pipeline.py --offline` 全绿**（纯逻辑改动）；涉网络/CDP 改动另做可控专项验证（未获批准不发起大量请求）
- [ ] **`check_doc_refs.py` 默认门通过**；涉打包再跑 `pack_release.py --self-test`

### B. 跑流程 / 复用新作者时必查（执行）

**红线（最高优先级）**

- [ ] [STOP] **原文全文（含 OA）一律经 CDP**，未自编脚本 HTTP 直链抓取；出版社内容站（ScienceDirect/OUP/SAGE/Wiley/Cell/ACS/RSC/Springer/T&F）零直连探活（§5.1）
- [ ] [STOP] **第一层直连仅限白名单**（`templates/直连白名单.json`）；摘要/综述题录可直连，第一层失败转 CDP（`grab_abstracts.py --retry-failed`），不卡第一层反复重试；直连摘要带 `source` 字段，不冒充 CDP 产物
- [ ] [STOP] **人机验证按 §5.2**：宿主机级 `SendInput`、每篇 ≤3 轮、失败即停转人工；不用 CDP `Input.*` 过验证；`--manual-wait=N` > 0。宿主机点击须 `UC_HOST_CLICK=1` 且 `hasFocus()` 为 true 才点
- [ ] [STOP] **自动化点击仅限非验证元素**（Cookie 弹窗/功能按钮/页内导航）

**阶段 0 与闸门**

- [ ] **阶段 0 已执行**：`.work_cdp/prepare_plan.json` 存在，各选项**逐项确认**（未确认项保守跳过并如实标注）
- [ ] 各阶段 `--check` 闸门**逐条按计划执行**；`--show` 中每个 skip 写明「由谁关闭」，未擅自开启被关闭阶段

**身份与题录**

- [ ] 作者身份唯一（ORCID/单位/代表作交叉验证）；`orcid_probe.py` 任职/教育时间线与主页履历逐条比对
- [ ] `disambiguate.py` 同名消歧（`--affil` 用**具体系/实验室名**而非整所大学）；「单位不匹配」与「字段缺失」分别处理
- [ ] `scope.py` 接入下游（下载/著录共用排除名单，**数据缺失类不误排除**）；被排除他人论文在列表文末逐条注明（DOI+题名+理由，非静默丢弃）
- [ ] `enrich_crossref_authors.py` 回填全名（未按空格猜姓）；DOI 全经 Crossref 核实；共同第一作者查原文脚注
- [ ] ORCID 全量成果抓取（含翻页）去重；**为空时改作者主页 + OpenAlex 基准**

**全文与产物**

- [ ] 多实例 CDP 启动（`cdp_pool.py status` 全 `[ON ]`）；批次 `--delay=25-55 --site-fail-limit=2 --manual-wait=N`（N>0），未反复冲击同站
- [ ] PDF 以**题名**命名（`pdf_index.json` 登记，无改名后重复下载）；`verify_pdfs.py` 页数核查（无补充材料/图注页冒充正文，残缺件改名保留）
- [ ] `store.validate_pdf` 校验（**pypdf 在位**；缺 pypdf 会静默判全损，须显式报错而非当作真损坏）；《全文清单.md》注明限制
- [ ] 列表 GB/T 7714-2015（含 DOI/原文链接，**无 `et al..` 双句点**）；规范交付名 `<作者名>_论文列表.md`（无 `_完整版.md` 重复）

**解读与综述（H/H2/R2）**

- [ ] **并行 subagent**：各读自己的 `task.json`、各写自己的 `result.json`，**零网络请求**
- [ ] 解读卡**四步齐备**（方法论/已验证结论/二八关键信息/量化提升；无数据则 `[]` 并说明，**未编数字**）
- [ ] 关键词三层证据按 NFKC+去空白回溯原文；受控标签从 `tag_vocabulary.json` 取词（未归一标签保留列出）；**未臆造词族合并**（词族表按作者语料裁剪）
- [ ] D2（若 `reverse_refs=yes`）：核心期刊判定用**显式清单**（非前缀匹配）；「综述」判定如实标注启发式
- [ ] G2（若 `refs_bibliography=yes`）：含文章→参考文献两部分；IF 标「≈」；分区未命中写「未判定」而非「不满足」
- [ ] H2：去重剔除清单留档（`dropped.json`）；被作者 ≥3 篇引用条目强制保留；任务包命名只由 DOI 决定；**缺卡与限制逐条申报**
- [ ] **证据层级不夸大**：综述报告声明结论建立在摘要级证据上
- [ ] 中译 PDF 通过 CJK 嵌入校验（若 `translate=yes`）

**图谱 / PPT / 交付（K/P/I）**

- [ ] K（若 `make_graphs=yes`）：三图产出；VOSviewer **空网/仅孤立节点时不宣称形成词群**，如实申报
- [ ] P（若 `make_ppt=yes`）：`design_spec.md` Page Count 与实际页数一致；`spec_lock.md` page_rhythm 无缺号；SVG 质检 0 error；**未伪造 ppt-master 确认回执**
- [ ] D2/G2/H2 只走元数据 API（OpenAlex），P 全程离线，未新增 HTTP 直连抓取
- [ ] **日志留存** `.work_cdp/logs/`；交付清单完整，限制如实注明
