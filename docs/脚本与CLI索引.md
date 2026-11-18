# 脚本与 CLI 索引（按需读）

> 本文件是脚本/CLI 索引的**并集**：上半「脚本功能速查」源自 SKILL.md §8，下半「CLI 参数详解」源自 docs/开发项目说明.md §13。按需查阅，主文件与开发说明各留指针。

## 8. 脚本索引（`scripts/`）

### 本科科研辅助链路

| 脚本                                 | 功能                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 用法                                                                                                                                                                                                            |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`harvest_faculty.py`**     | [***]**阶段 S：学部/学院官网导师抓取（唯一数据源）**。**配置驱动** —— 学校/学院/栏目/解析规则全在 `templates/学部官网源.json`，**新增学校不必改脚本**。支持列表页模板 `wp_teacher_card` / `wp_newsinfo_card` 与**复用模式的 `engine_dispatch`**（家族引擎分派）。含跨栏目去重、职称剥离、相对 URL 补全、分隔符归一                                                                                                                                                                                                     | `uv run python harvest_faculty.py --list-units` `--unit zju` / `--unit ustc:chem` / `--stage=list\|detail\|all` / `--unit-name <栏目>`                                                                  |
| **`faculty_discover.py`**    | [***]**阶段 S 复用模式：任意学校/院系 → 自动发现师资页 + 判定站点家族 + 生成数据源配置**。给定院系官网入口，自动找师资页、跑全部家族引擎、用**结构指纹 + 官网自称条数对账**打分，`--write` 落进配置；之后同一学校零确认复用。诚实识别 **JS 渲染空壳站**并说明需 CDP                                                                                                                                                                                                                                                                 | `uv run python faculty_discover.py --site https://chem.ustc.edu.cn/ --school 中国科学技术大学 --unit-name 化学系 --unit-key chem --write` / `--url <师资页> --explain` / `--unit ustc:chem`（校验已收录） |
| **`faculty_engines.py`**     | [***]**站点家族引擎库**（复用模式底座，被上面两者共用）。家族：`wp_news_list` / `wp_newsinfo_card` / `wp_teacher_card` / `generic_cluster`（**结构无关的 URL 形态聚类**）/ `tsites_list` / `render` / `interactive`（后两者需浏览器）。判据集中一处避免漂移                                                                                                                                                                                                                                                                      | 由`harvest_faculty` / `faculty_discover` 调用；改判据后必跑 `selftest_engines.py`                                                                                                                         |
| **`faculty_render.py`**      | [**]**渲染桥**：把用户自编的 `robot/crawl_site.py` 接进阶段 S。直连拿到空壳时用无头浏览器渲染后再解析；含 `crawl_urls()`（起本地 HTTP 种子页批量抓 URL 清单，因 crawl_site 只接受单个起始 URL 且不认 `file://`）                                                                                                                                                                                                                                                                                                                               | 由`harvest_faculty` 的 `render` 家族调用；也可 `python scripts/faculty_render.py <url>` 自检                                                                                                        |
| **`faculty_interact.py`**    | [**]**交互枚举**：驱动真实浏览器**逐一点击分类**，取出 JS 异步加载的教师名录（空壳站的唯一可行链路）。含三级浏览器启动与**优雅降级**；`--start-browser` 可在普通终端预启动调试浏览器                                                                                                                                                                                                                                                                                                                                                    | `uv run python faculty_interact.py <url>` / `--start-browser --port 9222`                                                                                                                                   |
| **`selftest_engines.py`**    | [**]**引擎离线自检**（零网络，秒级，39 项）。用真实站点骨架做 fixture，锁住无主页者不漏、导航不混入、`研究方向` 锚点在导航里、`共N条` 对账、`listN.htm` 分页、JS 空壳识别等                                                                                                                                                                                                                                                                                                                                                                    | `uv run python selftest_engines.py`                                                                                                                                                                           |
| **`merge_roster.py`**        | **合并 + 清洗**（仅在两源并存时用）。把学院官网方向并入 roster 再统一清洗。**必须在一次运行内按「恢复→合并→清洗」完成** —— 分多次串跑会误差累积（曾把名单砍掉近四分之一，数据见 [实测结果汇总.md](实测结果汇总.md#s-8)）。单一数据源场景**不再需要**本步骤                                                                                                                                                                                                                                                                                                                        | `uv run python merge_roster.py --roster … --college … --from-backup`                                                                                                                                        |
| **`make_delivery_index.py`** | **生成《交付物索引.md》**：汇总产物清单、数据来源、方向获取途径、结构化字段覆盖率、「为什么有人没有方向」的原因分布、检索与 PRISMA 计数、**解读证据层级**。**按链路分模式**（判据 = 是否找到 `*_论文列表.md` 等作者链路产物）：入门链路读 `outputs/01_导师名单/` + `outputs/07_检索*/` + `.work_cdp/reviews/`；**作者链路**读 `*_论文列表.md` / `*_论文解读.md` / `*_研究方向综述.md` / `*_知识图谱.md` / `*_汇报PPT*` / `*_Exports/全文清单.md`，证据层级改读 **`interpret/manifest.json`**。两条链路的产物**同处一个日期根**（入门 `outputs/…`、作者 `<作者名>/…`），索引统一写到 `<日期根>/交付物索引.md`；只跑了一条链路时只出那一条的索引，**两条都没产物则 exit 2**（不再产出一份「全是未产出」的文件） | `uv run python make_delivery_index.py`（自动判别链路）/ `--paper-only` / `--mentor-only` / `--root <路径>` / `--direction <方向>` |
| **`reviews_prepare.py`**     | **阶段 R①：综述任务包组装**。把阶段 R 的检索结果转成**与 `interpret_prepare` 逐字段同名**的任务包，使 §7.2 的并行 subagent 机制可原样套用。产物写 `.work_cdp/reviews/`，与 `interpret/`、`refs/` **互不干扰**。支持 `--fetch-abstracts` 按 DOI/PMID 从 Europe PMC 补摘要                                                                                                                                                                                                                                                     | `uv run python reviews_prepare.py --from-europepmc .work_cdp/reviews/europepmc.json --fetch-abstracts`                                                                                                        |
| **`reviews_discover_scopus.py`** | **阶段 R 可选增强（默认不启用）**：调用 `skills/scopus-wos-lit` 从 Scopus / WoS / OpenAlex 发现综述，输出阶段 R 认识的 `{"records":[...]}`；可用 `--merge-into` 合并到既有 `reviews.json`。不改原检索链，失败可停用。详见 [docs/综述获取增强_scopus_wos.md](docs/综述获取增强_scopus_wos.md) | `uv run python reviews_discover_scopus.py --direction "神经科学" --source scopus --limit 8` |
| **`reviews_build.py`**       | **阶段 R②：领域概貌汇总**。把解读卡机械合并为《领域概貌.md》：术语按**出现篇数**排序、共识标 、**争议原样并列不替用户判定**、阅读顺序按「入门→奠基→方法」排。卡不全时**如实告警**并注明报告基于部分文献                                                                                                                                                                                                                                                                                                                         | `uv run python reviews_build.py --topic "阿尔茨海默病早期诊断"--status` 只看进度                                                                                                                              |
| **`reviews_fulltext.py`**    | **阶段 R①.5：综述全文获取（skill 先试 / CDP 兜底）**。把阶段 R 任务包接到 §5.1 分层链路上：**补 PMCID**（原检索漏采，是下游停在摘要级的根因）→ **第一层** EPMC `fullTextXML`（白名单授权通道）→ **第二层-A** 缺口转 `download_all_fulltexts.py` → **第二层-B** `--repo` 出版社被 CAPTCHA 时改走 **PMC 仓库副本（仍经 CDP）** → **回填**（按字数**择优**，防付费墙残页盖住真正文）→ **副链路** `--fallback` 三层都没拿到时转 `fulltext_router.py`（CDP 备用，产物由回填第 ④ 通道收取）。**取不到就如实保持 `abstract`，不臆造**。用法见 `docs/阶段R全文获取模块.md` | `uv run python reviews_fulltext.py --all --cdp --repo --fallback --backfill`（分步：`--probe` / `--layer1` / `--layer2` / `--cdp` / `--repo` / `--fallback [--fallback-mode off\|oa\|oa+api] [--fallback-arxiv]` / `--backfill`。**`--fallback` 必须在 `--cdp`/`--repo` 之后、`--backfill` 之前**）                                                |
| **`fulltext_router.py`**     | **阶段 R①.5 的副链路路由（CDP 备用）**。把 nature-downloader `routeDoiDownload` 的**编排层**搬进来（不外链 nature-skills）：Crossref 元数据 →（中文硬分流）CNKI → 出版商 API → OA 瀑布 → 浏览器交接 → API 兜底需显式授权；真正的取件全部委托给既有 `oa_candidates` / `white_list_policy` / `fulltext_publishers` / `fulltext_credentials` / `fetch_pacing` / `jats_fulltext` / `fulltext_result`。**[STOP] 两个前提**：① CDP 主链路**已失败**（`cdp_failed` 非 `True` 直接拒绝，退出码 3）；② 候选**必须在 `templates/直连白名单.json` 内**（白名单文件不得为此改动）。**不代填、不代登录、不代持凭据** | `uv run python fulltext_router.py --plan 10.xxxx/yyy`（零网络干跑）；`--from-json .work_cdp/reviews/fallback_gap.json --out …/fallback --report …/reviews_fallback.json [--mode off\|oa\|oa+api]`；`--self-test`                                                |
| **`selftest_fallback_wiring.py`** | **副链路接线自检（零网络、无 LLM）**。副链路每个模块各自都有 `--self-test`，但接线断掉的方式从来不是「脚本报错」，而是**契约对不上**：① 「CDP 已失败」的判据两边各算一套 → 已拿到全文的论文被重跑，白耗同一站点配额；② 副链路取到 PDF 却把二进制字节当正文塞进任务包 → 回填**按字数择优**时字节很容易胜出并盖住真正文；③ 阶段 0 写了 `fulltext_fallback=off` 但没人读 → 副链路照跑。三条只有把 `reviews_fulltext.py` 与 `fulltext_router.py` 接起来跑一遍才暴露，故单独成检。`route_many` 被换成夹具实现 —— 验的是**接线**，路线本身由 `fulltext_router.py --self-test` 的 40 项断言覆盖 | `uv run python selftest_fallback_wiring.py [--keep] [--verbose]` |
| **`cdp_up_nosandbox.py`**    | **显式诊断用实例启动器**（仅用户明确调用时添加 `--no-sandbox`）；默认通用池保持沙箱开启。通过CDP DOM能力探活，不按Edge进程名计数。跨平台环境不支持精确listener身份验证时fail-closed；不作为正常流程的自动回退 | `python cdp_up_nosandbox.py --n=3 --browser <Chromium路径>` |
| **`prep_r2_tasks.py`**       | **阶段 R①.5→R2 的交接整理（必跑）**。把全文从任务包 JSON 的 `text` 字段**搬出**到 `<stage>/fulltext/<slug>.txt`：JSON 单行会被读取工具截断（十几万字的正文只剩开头一小段能被读到），且超长段落本身也会被截断 —— 本脚本把段落**软换行到 1200 字符/行**，确保全文 100% 可读。任务包只留摘录 + `fulltext_path` 指针。原包备份到 `tasks_full/`，`--restore` 可还原（备份只建一次、全文以 `tasks_full/` 为准，故**可反复运行而不会越跑越短**）。定位任务包目录**只认本次运行目录**：`.work_cdp/reviews/` → 本次 `outputs/` 下含 `tasks/` 者；**不回看**日期隔离前的 `outputs/`                                                                                                                                          | `python prep_r2_tasks.py`（`--excerpt 3000` / `--restore`）                                                                                                                                               |

### PRISMA 系统检索链

> **用户给出检索条件后，自动查该领域论文**的四步链。
> 设计参照工作文件夹 `academic-research-skills-3.22.2`：
> `deep-research/agents/bibliography_agent.md`（检索策略六要素、两轮筛选、偏斜告警）、
> `deep-research/references/systematic_review_toolkit.md`（PRISMA 2020）、
> `references/source_quality_hierarchy.md`（证据金字塔 + 掠夺性红旗）、
> `shared/query_plan.schema.json`（查询计划的可追溯字段）。

| 脚本                           | 功能                                                                                                                                                                                                                                        | 用法                                                                                                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`search_plan.py`**   | **阶段 R⓪：检索条件 → 结构化查询计划**。把主题/限定词/年份/类型/OA/数据源编译成**可复现**的 JSON（含各源检索式与 sha256、纳入排除标准、provider roster）。`--sort-cited` 把 `AND (SORT_CITED:y)` 写进 Europe PMC 检索式（并按塌缩判据把综述过滤收成单一 `(PUB_TYPE:"Review")`）。**不联网**                                                    | `uv run python search_plan.py --topic "免疫与感染" --topic-en "innate immunity" --terms-en "NK cell,macrophage" --from-year 2021 --doc-types review --sort-cited --max-records 16` |
| **`search_run.py`**    | [**]**阶段 R①：多源并行检索 + 归一化去重**。**复用** `skills/paper-lookup/scripts/paginate.py` 作取数层（不重写；该层按 `工作目录/skills → skills → 工作文件夹/skills` 逐个探测）。含**源归一化**（三源字段类型完全不同）、DOI 优先+题名兜底去重、**单源失败不影响全局**；`sort_cited` 与计划同一判据         | `uv run python search_run.py --plan outputs/07_检索/search_plan.json--sources europepmc,openalex`                                                              |
| **`search_screen.py`** | [**]**阶段 R②：两轮筛选 + PRISMA 计数 + 质量分级**。Pass1 题录相关性（可调阈值、**保留命中词**备核）、Pass2 可得性；按元数据给**证据级别**与**红旗**；≥70% 集中度触发**分布偏斜告警**。含**摘要补齐** | `uv run python search_screen.py --raw outputs/07_检索/search_raw.json--threshold 0.25` / `--no-backfill`                                                     |
| **`search_report.py`** | **阶段 R③：PRISMA 报告**。流程图式计数、**各源实际发送的检索式**、纳入文献表、排除原因、偏斜告警、**六条局限声明**                                                                                                       | `uv run python search_report.py --screened outputs/07_检索/search_screened.json`                                                                               |

> **与既有阶段 R/R2 的关系**：本链产出的是**经过筛选与计数的文献集**（可追溯、可复现），
> 之后交给 `reviews_prepare.py` → 并行 subagent 解读 → `reviews_build.py` 汇总。
> 即：`search_plan → search_run → search_screen → search_report → reviews_prepare → (R2 解读) → reviews_build`。
> | **`route_databases.py`** | [**] **阶段 R：方向 → 数据库路由（选库唯一入口）**。按方向查该跑哪些库、**明确该 skip 哪些**，并生成**可直接执行**的检索命令（各库语法已分别拼好）。`--self-test` 零网络自检路由表与总表 24 领域的一致性 | `uv run python route_databases.py --domain 机器学习与深度学习--domain 生物信息学与基因组学 --terms single-cell``--list-domains` / `--list-databases` / `--self-test` |

> **阶段 N 与阶段 R 的检索环节没有专用脚本** —— 阶段 N 是 agent 解析输入 +
> 查已有的关键词总表；阶段 R 的检索**直接复用** `skills/paper-lookup/scripts/paginate.py`
> 与 `arxiv_atom.py`。**这是刻意的**：这些环节的价值在判断与编排，
> 写成脚本反而会僵化；而**能被固化的部分已经固化**（任务包组装、汇总渲染）。

### 基础设施

| 脚本                              | 功能                                                                                                                                                                                                              | 用法                                                                        |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `logutil.py`                    | **统一日志**（控制台 + 文件双写，UTF-8 安全，线程安全）+ **操作流水** `journal(stage, action, **fields)`                                                                                            | `from logutil import get, journal`                                        |
| **`journal_view.py`**     | **操作流水查看器** —— 回溯「这一轮到底做了什么」：按时间列出各阶段动作与参数，支持 `--stage=` / `--action=` / `--tail=` / `--json`                                                                | `uv run python journal_view.py [--stage=E] [--action=download.fail]`      |
| **`selftest_consent.py`** | **普通Cookie同意窗识别回归**（不含人机验证；真实验证provider仍须人工）：本地 `file://` fixture + 可选Chrome/Chromium/Edge，验证fixed/shadow/遮挡/按钮状态与实际DOM消失；不触碰出版社站点                            | `uv run --with websocket-client python selftest_consent.py [--port=9251]` |
| `detect.py`                     | **页面状态唯一判定源**（ok/challenge/blocked/paywall/not-ready）+ DOI→出版社映射                                                                                                                           | `from detect import judge, site_of_doi`                                   |
| `store.py`                      | **PDF 存放/命名/去重/校验**唯一实现                                                                                                                                                                         | `from store import existing_pdf, validate_pdf, stem_for, register_pdf`    |
| `cdp.py`                        | CDP 客户端（端口参数化、**硬超时**、自动重连、异常分类）                                                                                                                                                    | `from cdp import connect, is_alive`                                       |
| `cdp_pool.py`                   | **多实例池**：并行启动/探活/调度/回收                                                                                                                                                                       | `python cdp_pool.py {up\|status\|down\|renew}`                               |
| `start_edge_cdp.sh`             | 启动 CDP Chromium浏览器（**多实例**，最小参数档位）                                                                                                                                                                   | `bash start_edge_cdp.sh <workdir> 9222 3 0`                               |
| **`agent_poll.py`**       | [**]**子代理批次进度轮询**（阶段 H/H2/R2）：读 `tasks/` 与 `results/` 计数，给出「下一批派几个、隔多久再查」。**取代盲等固定时长**。只读、零网络；退出码即结论（0=齐/3=在跑/4=有坏卡/2=无任务包） | `uv run python agent_poll.py --stage H`                                   |
| `renew_cdp_instance.sh`         | 重建**单个**实例（换全新 profile）                                                                                                                                                                          | `bash renew_cdp_instance.sh <workdir> <port>`                             |

### 数据采集

| 脚本                                      | 功能                                                                                                                                                                                                                                                                      | 用法                                                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `orcid_extract.py` / `orcid_pages.py` | CDP 打开 ORCID、翻页抓全部成果                                                                                                                                                                                                                                            | `uv run --with websocket-client python orcid_extract.py <ORCID>`                                    |
| `parse_orcid.py`                        | ORCID 页面文本 → 结构化 JSON                                                                                                                                                                                                                                             | 承接上一步                                                                                            |
| **`orcid_probe.py`**              | **ORCID API 身份核验**：姓名/任职/教育/关键词 + 全部 works（含 DOI）。用于把 ORCID 时间线与作者主页履历逐条比对                                                                                                                                                     | `uv run python orcid_probe.py <ORCID>`                                                              |
| `fetch_crossref_all.py`                 | 并发拉取全部 DOI 的 Crossref 题录                                                                                                                                                                                                                                         | `uv run python fetch_crossref_all.py`                                                               |
| `retry_crossref.py`                     | 失败 DOI 串行重试（退避）                                                                                                                                                                                                                                                 | 承接上一步                                                                                            |
| **`enrich_crossref_authors.py`**  | **回填 Crossref 结构化作者姓名**（family/given 分开）+ 卷期页，重算本人署名位次                                                                                                                                                                                     | `uv run python enrich_crossref_authors.py --surname=<姓> --given=<名>`                              |
| **`disambiguate.py`**             | [**]**同名消歧**（三重判据：姓名精确 + 署名单位指纹 + 合作者网络）。**作者聚合普遍过度合并，此步不可省**                                                                                                                                                      | `uv run python disambiguate.py --surname=… --given=… --affil=… --anchor-doi=…`                  |
| `unpaywall_check.py`                    | 批量 OA 状态与 PDF 链接（仅用于判断获取难度，**不据此走直取**）                                                                                                                                                                                                     | `uv run python unpaywall_check.py`                                                                  |
| **`fetch_direct.py`**             | [**]**第一层直连获取（摘要 / 综述题录）** —— 多源自动降级（S2 批量 → OpenAlex → Europe PMC → Crossref → PubMed），**只对白名单域名发请求（表外一律拒绝）**；缺口写 `direct_failed.json` 并**转第二层 CDP**。`--check-domain=URL` 可单独问策略 | `uv run python fetch_direct.py [--limit=N] [--check-domain=URL] [--run-cdp-fallback] [--self-test]` |
| `fetch_openalex_abstracts.py`           | OpenAlex 倒排索引批量重建摘要（零风控）                                                                                                                                                                                                                                   | `uv run python fetch_openalex_abstracts.py`                                                         |
| `build_list.py`                         | 生成 GB/T 7714 完整版列表（含署名位次）                                                                                                                                                                                                                                   | `uv run python build_list.py`                                                                       |

### 全文获取（**一律经 CDP**，见 § 5.1 禁令；实现为可选模块）
> **取全文 CDP 模块（可选，可整目录删除）**
> 「用浏览器/出版商接口取回正文」这层被抽成 `optional-cdp-fulltext/`（合规原因见其 README）。
> 探测入口只有一个：
> ```bash
> uv run python scripts/cdp_module.py        # 0/3 = 可用/未安装；--path 给 shell 用
> ```
> **在** → 按 `optional-cdp-fulltext/SKILL.md` 操作；**不在** → 本层不可用，
> **静默改走备用链路**（白名单直连 + 元数据 API + nature-skill 获取模块），并在报告里如实标注 `cdp_module_absent`。


| 脚本                                | 功能                                                                                                                                                             | 用法                                                              |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **`optional-cdp-fulltext/`**（可选模块）| [**]**全文批量下载 / 出版商取正文 / 浏览器另存 PDF / 挑战与机构访问诊断** —— 见 `optional-cdp-fulltext/SKILL.md`。**整目录可删**：删掉后本流程不含出版商正文自动化，改走备用链路 | `uv run python scripts/cdp_module.py`（探测） |
| **`preflight_fallback.py`** | **备选下载链路前置检查**（只读）：逐条报 Node.js 版本、nature-downloader 位置、四条备选路线可用性、外部依赖 skill 是否在位。退出码 0=有可用路线 / 3=不可用 | `uv run python preflight_fallback.py [--json]`                  |
| `grab_abstracts.py`               | CDP 抓出版社摘要（低频拟人化）                                                                                                                                   | `... grab_abstracts.py "<DOI1>,<DOI2>"` 或 `--retry-failed`   |
| ~~`fetch_oa_direct.py`~~         | [STOP]**已停用**：曾用于 OA 直链纯 HTTP 下载**全文**。分层策略只放开了**摘要/元数据**，**没有**恢复全文直连，故本脚本**继续停用**  | 不再调用；保留文件仅作历史回溯。摘要直连请用`fetch_direct.py`   |

### 中译素材链

| 脚本                              | 功能                                                                                                                                                                                                                 | 用法                                                                            |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `prep_paper.py`                 | 原文 PDF →**按页分节**纯文本 + 图注页索引（翻译逐段对照用）                                                                                                                                                   | `uv run --with pymupdf python prep_paper.py "<pdf>"`                          |
| `crop_by_images.py`             | 按页面**位图并集**裁出「图-only」主图（Nature/Cell 拼版图适用）                                                                                                                                                | `... crop_by_images.py "<pdf>" "<out.json>" "<图页>" 200 8`                   |
| `extract_figures.py`            | 抽取 PDF**内嵌位图**（Extended Data 图、单张整图），可降采样                                                                                                                                                   | `... extract_figures.py "<pdf>" "<out.json>" 300 1600`                        |
| `jats_to_text.py`               | **JATS XML → 结构化文本**（OA 全文取正文，非 OA 返回 404）                                                                                                                                                    | `uv run python jats_to_text.py <file.xml>`                                    |
| **`extract_parallel.py`** | [**]**边下载边抽取调度**（阶段 E↔F/H 并行）：PDF → 按页分节文本 + 图注页索引 + 内嵌位图，**幂等增量**；`--then-prepare` 顺带组解读任务包；**开头断言 pypdf 可用**；需 pypdf + pillow + pymupdf | `uv run --with pypdf --with pymupdf python extract_parallel.py`               |
| **`optional_layers.py`** | [**]**可选解析层首跑探测 + MinerU 安装决策记录**（只读探测 + 本地落盘，不联网、不代装）：判「无产物目录 + 无 `.work_cdp` + 环境无 MinerU」为首跑，给出体积/损益/建议供 agent 逐项问用户，`record` 落盘 yes/no/defer 后不再追问。详见「可选解析层」节 | `uv run python optional_layers.py probe [--json]` / `record --mineru-install=yes\|no\|defer` / `status` / `--self-test` |
| **`opendataloader_extract.py`** | [**]**可选轻量解析层**（opendataloader-pdf，需 Java 11+）：数字版 PDF → 确定性 Markdown + 逐元素 `bounding box` JSON，约 0.7s/篇，作交叉校验/备选引擎，**不替换 fitz 主链**；本地模式无 OCR（扫描件走 MinerU）。已完整整合进 `vendor/opendataloader/`（jar+许可证），本脚本是唯一 adapter | `uv run python opendataloader_extract.py "<pdf>" [--format markdown,json]` / `--detect` / `--self-test` |
| `translation_build.py`          | 内容 JSON + 图形 JSON →**内联图的**中译 HTML                                                                                                                                                                  | `uv run python translation_build.py <content.json> <figures.json> <out.html>` |

### 同名消歧与论文解读

| 脚本                                         | 功能                                                                                                                                                                                                                                                    | 用法                                                                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **`scope.py`**                       | [**]**同名消歧结果的唯一读取入口** —— 把 `disambiguated.json` 归结为「非本人排除名单」，**下载侧与著录侧共用**。只排除**肯定性判定**（`name-mismatch`/`affil-mismatch`），**数据缺失类一律保留**                        | `from scope import excluded_dois, summary`                                                                                  |
| **`interpret_prepare.py`**           | **阶段 H ①**：组装**自包含**单篇任务包（题录 + 摘要 + 带 `[p.N]` 页标记的正文），供 subagent 直接精读；**不联网**（全文取自本地 PDF）。`--engine=paper-card` 时额外下发 16 节深读卡的产出契约与全部脚本路径                      | `uv run --with pypdf --with pillow python interpret_prepare.py [--engine=legacy\|paper-card] [--max-chars=60000] [--no-text] [--only=DOI] [--limit=N]`（重跑必带本轮 `--only`/`--limit`；缺 `pillow` 会静默丢图表） |
| **`interpret_build.py`**             | **阶段 H ④**：回收 `results/*.json` → 《`<作者名>_论文解读.md`》（时间线 / 逐篇四步 / 关键词统计 / 研究方向 / 方法范式 / **缺卡清单**）。`paper-card` 引擎下另申报引擎与逐篇「深读卡 + 审计状态」                                   | `uv run python interpret_build.py [--families=<词族表.json>]`                                                               |
| **`nature_bridge.py`**               | [**]**阶段 H 的 paper-card 引擎适配层（唯一入口）**：路径解析（**只认流程内已并入的 `vendor/nature-paper-card`**）+ 结构审计 + **内容级校验（数字回查原文）**，`check` 子命令把后两者合为一个质量门。`--self-test` 为零网络自检 | `uv run python nature_bridge.py check --card C --bundle B --locator-mode M --report R` / `--self-test`                    |
| **`selftest_nature_integration.py`** | [**]**并入自检（零网络、无 LLM）**：验并入范围（**只一个技能**）、`always_load` 不越界、两处引擎契约、以及质量门对"空壳卡 / 编造数字"的确切反应                                                                                           | `uv run python selftest_nature_integration.py [--keep] [--verbose]`                                                         |

> 阶段 H 的**执行细节**（subagent 提示词模板、解读卡 schema、质量控制）
> 见 `subagent-driven-review/SKILL.md`；
> paper-card 引擎的**接线权威**（产出契约、质量门、上游补丁登记、常见错误）
> 见 `paper-card-integration/SKILL.md`。

### 准备阶段与文献综述网络

| 脚本                               | 功能                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | 用法                                                                                                                                                                                                                                                                                                |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`prepare_stage.py`**     | [**]**阶段 0**：收集准备阶段选择 → 写 `.work_cdp/prepare_plan.json`，并解析出**每个阶段的 run/skip 闸门**（含依赖收敛）。**只读写本地文件，零网络**                                                                                                                                                                                                                                                                                                                                                                                                          | `uv run python prepare_stage.py --set k=v …` / `--defaults` / `--show` / `--check <阶段>` / `--check-all`                                                                                                                                                                                |
| **`refs_backfill.py`**     | [**]**阶段 D2**：取作者论文的 `referenced_works` → 参考文献题录池 → 按「核心期刊 **或** 被引 > N，**优先 Review**」筛高质量；含**反向索引**（哪几篇作者论文引用了它）。带磁盘缓存与退避重试                                                                                                                                                                                                                                                                                                                                                           | `uv run python refs_backfill.py [--min-citations=200] [--offline] [--self-test]`                                                                                                                                                                                                                  |
| **`refs_bibliography.py`** | **阶段 G2**：作者本人论文中「核心期刊 / 第一或通讯作者 / 中科院一区或 IF>5.0」者 → 逐篇列参考文献，输出**文章 → 参考文献**两部分 md                                                                                                                                                                                                                                                                                                                                                                                                                                 | `uv run python refs_bibliography.py [--if-threshold=5.0] [--self-test]`                                                                                                                                                                                                                           |
| **`refs_curate.py`**       | **阶段 H2①**：DOI/题名两级**去重** + 按作者主体研究方向**打分**（分量全公开）+ **剔除无关**（剔除项逐条留档）                                                                                                                                                                                                                                                                                                                                                                                                                                            | `uv run python refs_curate.py [--cut=25] [--keep-top=0] [--self-test]`                                                                                                                                                                                                                            |
| **`refs_prepare.py`**      | **阶段 H2②**：组装**自包含**综述任务包（题录 + 摘要 + 受控标签词表）；**不联网**                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `uv run python refs_prepare.py [--top=40] [--only=DOI] [--self-test]`                                                                                                                                                                                                                             |
| **`refs_build.py`**        | **阶段 H2③**：回收综述解读卡 → 《`<作者名>_研究方向综述.md`》（含网络角色 / 缺卡清单 / 限制申报）；**不联网**                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `uv run python refs_build.py [--self-test]`                                                                                                                                                                                                                                                       |
| **`ppt_brief.py`**         | **阶段 P**：汇编 PPT 材料 + **确定性生成** `design_spec.md` / `spec_lock.md`（= 简化的 Strategist Phase）+ 调 ppt-master 初始化与校验。项目落**流程输出根**（默认 `PAPER_WORKDIR`），**不落** `vendor/ppt-master/projects/`。另提供**手写 SVG 转义预检**：正文里的裸 `<`（`p < 0.05`）与裸 `&`（`R&D`）会让 XML 无法解析，上游质检器只报 `invalid token`、看不出根因；本预检**定位到行并给修法**，应在 `svg_quality_checker.py` **之前**跑                                                                                                                                                                                                                                                                                                                                                                                                                             | `uv run python ppt_brief.py [--pages=short\|standard\|long] [--outdir=<路径>] [--check-svg=<project>/svg_output] [--dry-run] [--self-test]`                                                                                                                                                                                                              |
| **`knowledge_graph.py`**   | [**]**阶段 K**：三张图（overview / 词云 / 方向聚类）。overview 用 **igraph 径向整齐树**（作者在圆心 → 阶段扇区 → 方向 → 综述编号；方法词另置外环；记忆连接沿环画弧），标签避让交给 **adjustText**（只移文字、不动节点）；词云**按「文章关键词 > 题名 > 摘要 > 解读卡方法描述」加权取词**（中文用**左右邻接熵**挡跨词碎片、剔除单位/试剂缩写与受控范式标签），**布局用 `wordcloud` 库自绘**（`to_image()` 位图直接嵌进 matplotlib 画布，位置即所见；标题/图注/图例仍矢量，见 v17.11）；纯 ASCII 词按词切**拉丁等宽字体**并收紧字距，最大字号 ≤ 6×最小字号（v17.12）；方向聚类改为 **CiteSpace 式时间线**（行=方向、x=年份）；三张图都做**字号自述**，overview 坐标落盘可审计 | `uv run --with matplotlib --with networkx --with python-igraph --with adjustText --with wordcloud python knowledge_graph.py [--recent-years=3] [--also-png] [--format=svg\|png\|auto] [--wc-engine=library\|custom] [--wc-lang=both\|zh\|en] [--wc-min-docs=3] [--wc-weight-keywords=3] [--self-test]` |
| **`keywords_three_layer.py`** | **关键词三层校验 + VOSviewer 导出**（v18）。`--check` 校验卡片 `keywords_core/method/reagent/keyword_evidence`（数量上限、core 混入常用方法、core/method 重复、evidence 按 NFKC+去空白对 `fulltext_path` 回溯）；`--export` 把某一层导出成 VOSviewer `map/network/thesaurus/vocab/corpus`；`--fulltext` 在没有卡片关键词时用 MinerU 全文直接建图 | `uv run python keywords_three_layer.py --check` / `--export --out .work_cdp/graph/vos --min-doc 1` / `--fulltext --corpus <md目录>` / `--selftest` |
| **`pack_release.py`**      | **打包发行版**：把本流程 + 全部依赖（含 `vendor/ppt-master`、`vendor/opendataloader`）输出为一个可独立运行的目录（默认 `<仓库根>/<源目录名>-release/`）。文档与脚本均为**相对路径**（锚点＝流程根目录），不含固定文件夹名，**无需路径重写**（`rewrite_paths` 恒等、计数 0）；产出 `PACKAGE_MANIFEST.json`（逐文件 sha256）与 `README-PACKAGE.md`                                                                                                                                                                                                                                                                              | `uv run python pack_release.py [--dest=路径] [--slim] [--force] [--no-vendor] [--dry-run] [--self-test]`                                                                                                                                                                                          |
| **`selftest_pipeline.py`** | [**]**端到端自检**：在临时工作目录里跑一遍 阶段 0 → D2 → G2 → H → H2 → P，验证**脚本之间的契约**（闸门收敛、字段命名、产物格式、外部校验器）。**为必跑项** —— 它挡下过 4 类静态看不出的缺陷（[版本变动.md](版本变动.md)）                                                                                                                                                                                                                                                                                                                                 | `uv run python selftest_pipeline.py [--offline] [--limit=4] [--keep] [--verbose]`                                                                                                                                                                                                                 |

> 阶段 H2 的**执行细节**（综述网络版 subagent 提示词、13 字段 schema）
> 见 `subagent-driven-review/SKILL.md`；
> 阶段 P 的**执行细节**（简化 Strategist 的依据、质量门、检查清单）
> 见 `ppt-integration/SKILL.md`。

### 命名 / 翻译 / 收尾

| 脚本                                          | 功能                                                                          | 用法                                                                  |
| --------------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `rename_to_title.py`                        | **PDF 重命名为论文标题**（存量批量改名 + 索引登记；同名同大小自动合并） | `uv run python rename_to_title.py [--dry-run] [--keep-dois]`        |
| `render_html_pdf.py`                        | **中文翻译 HTML → PDF** + CJK 嵌入校验（退出码 0/1/2）                 | `uv run --with pypdf python render_html_pdf.py <in.html> <out.pdf>` |
| **`verify_pdfs.py`**                  | 全文 PDF 完整性校验 +**页数合理性**（识别补充材料冒充正文）             | `uv run --with pypdf python verify_pdfs.py`                         |
| **`inventory_build.py`**              | 生成《全文清单.md》：逐篇状态/来源通道/**页数 ∪ 正文字数**/未获取原因 + 分阶段覆盖率。题录取 `crossref_all.json` ∪ `reviews/reviews.json`，入门链路同样可用             | `uv run --with pypdf python inventory_build.py`                     |

**模板**：`templates/` 内含 GB/T 7714 著录模板与中文翻译 HTML 模板（含 CJK 排版 CSS）。
**中译 HTML 样式样板**：`.work_cdp/translations/` 下任一篇（结构 = `meta 区 + 摘要框 + h2/h3 章节 + .figcap 图注 + .refnote`）。

---

---

---

# CLI 参数详解（原 开发项目说明.md §13）

## 13. 命令行接口索引

统一从**本流程根目录**（含 `SKILL.md`、`scripts/` 的目录，文件夹名不限）执行，所有路径均为相对该目录的相对路径。Python 命令的推荐形式是 `uv run python scripts/<脚本>.py ...`；也可按 `README-PACKAGE.md` 的发行目录方式执行。此索引列出主要公开 CLI，不替代参数解析器；不确定参数或版本变化时先运行 `--help`。`prepare_stage.py` 使用自定义参数读取器，没有通用 `--help`，应看本节参数说明和脚本内置用法，或执行只读的 `--audit`。尖括号内容为占位符，不应原样复制。

**不要对有副作用的入口用错误参数试探。** `prepare_stage.py` 无参数会按默认值生成并写入计划；`knowledge_graph.py` 即使未知 `--help` 也可能继续执行；因此应先确认入口是否支持 `--help`，优先查 argparse/内置 usage。

### 13.1 准备阶段与环境

```bash
uv run python scripts/prepare_stage.py --set output_format=markdown --set translate=no --set make_ppt=no
uv run python scripts/prepare_stage.py --answers answer.json
uv run python scripts/prepare_stage.py --defaults
uv run python scripts/prepare_stage.py --show
uv run python scripts/prepare_stage.py --check H
uv run python scripts/prepare_stage.py --check-all
uv run python scripts/prepare_stage.py --tier H       # stdout 仅 L1/L2/L3
uv run python scripts/prepare_stage.py --engine H     # stdout 仅 legacy/paper-card
uv run python scripts/prepare_stage.py --audit
uv run python scripts/selftest_pipeline.py --offline --limit=4 --verbose
```

`prepare_stage.py` 的命令族：重复 `--set key=value`；`--answers PATH`；`--defaults`；`--show`；`--check STAGE`（0=run、3=skip、2=未知阶段）；`--check-all`；`--tier STAGE`；`--engine STAGE`；`--audit`。`--tier` 支持 H/H2/R2，`--engine` 以脚本当前映射为准。计划默认位置由 `PAPER_WORKDIR` 决定，落在 `.work_cdp/prepare_plan.json`。

`selftest_pipeline.py`：`--offline` 跳过需要联网的部分；`--limit N` 限制 D2 样本；`--keep` 保留临时目录；`--verbose` 输出子脚本详情。退出码 0=通过、1=失败。它验证合成夹具契约，不证明实际联网结果质量。

### 13.2 师资抓取与发现

```bash
uv run python scripts/harvest_faculty.py --list-units
uv run python scripts/harvest_faculty.py --unit ustc:life-med --stage=list --out outputs/01_导师名单
uv run python scripts/harvest_faculty.py --unit ustc:life-med --stage=all --out outputs/01_导师名单
uv run python scripts/harvest_faculty.py --site https://<院系官网>/ --dry-run
uv run python scripts/faculty_discover.py --site https://<院系官网>/ --school <学校全称> --unit-name <院系全称> --unit-key <key> --write
```

`harvest_faculty.py` 参数：`--site URL` 与 `--url URL` 或 `--unit KEY` 三者互斥；`--unit-name` 选择栏目；`--stage list|detail|all`；`--limit N` 调试限制详情数；`--max-pages N` 页面发现上限；`--out DIR`；`--config JSON`；`--dry-run`；`--list-units`；`--reparse-only`（使用已存 HTML，不联网）；`--rename-archives`。`faculty_discover.py` 额外支持 `--direct`、`--school/--school-key`、`--unit-name/--unit-key`、`--out`、`--write`、`--dry-run`、`--explain`、`--top`、`--max-pages`；以 `--help` 核对精确组合。

### 13.3 检索与领域综述

```bash
uv run python scripts/route_databases.py --domain "机器学习与深度学习" --terms single-cell --json
uv run python scripts/route_databases.py --list-domains
uv run python scripts/route_databases.py --list-databases
uv run python scripts/search_plan.py --topic "免疫与感染" --topic-en "innate immunity" --terms-en "NK cell,macrophage" --from-year 2021 --doc-types review --sort-cited --max-records 16
uv run python scripts/search_run.py --plan outputs/07_检索/search_plan.json --sources europepmc,openalex
uv run python scripts/search_screen.py --raw outputs/07_检索/search_raw.json --threshold 0.25
uv run python scripts/search_report.py --screened outputs/07_检索/search_screened.json
uv run python scripts/reviews_prepare.py --input .work_cdp/reviews/reviews.json --limit 12 --tier L3
uv run python scripts/reviews_fulltext.py --stage-dir .work_cdp/reviews --probe
uv run python scripts/reviews_build.py --dir .work_cdp/reviews --topic "研究方向" --status
```

- `search_plan.py` 必需 `--topic-en`；常用可选 `--terms-en`、`--logic OR|AND`、`--from-year`、`--through-year`、`--doc-types`、`--oa-only`、`--sort-cited`、`--sources`、`--max-records`、`--out`。
- `search_run.py` 必需 `--plan PATH`；可用 `--sources CSV`、`--out DIR`、`--timeout SEC`。
- `search_screen.py` 必需 `--raw PATH`；支持 `--plan`、`--threshold`、`--out`、`--no-backfill`、`--backfill-limit`。
- `search_report.py` 必需 `--screened PATH`；支持 `--plan`、`--out`、`--name`。
- `reviews_prepare.py` 必须且只能提供一个来源选项：`--input`、`--from-europepmc`、`--from-json`；支持 `--out`、`--limit`、`--fetch-abstracts`、`--tier L1|L2|L3`。
- `reviews_fulltext.py` 以 `--probe`/`--layer1`/`--layer2`/`--cdp`/`--all` 选择操作；可选 `--stage-dir`、`--backfill`、`--n`、`--ports`、`--manual-wait`、`--delay`、`--site-fail-limit`、`--repo`、`--repo-pace`、`--fallback`、`--fallback-mode off|oa|oa+api`、`--fallback-arxiv`。顺序必须满足 `--cdp/--repo` 在 `--fallback` 前，且 fallback 服从阶段 0 与 CDP 已失败前提。
- `reviews_build.py` 支持 `--dir`、`--out`、`--topic`、`--status`、`--allow-placeholder`。

### 13.4 作者采集、CDP 与论文产物

```bash
bash run_all.sh "<作者姓名>" "<ORCID>" "<输出根可选>" 3
uv run python scripts/cdp_pool.py up --workdir "<工作目录>" --base 9222 --n 3 --levels 0
uv run python scripts/cdp_pool.py status --workdir "<工作目录>" --base 9222 --span 3
uv run python scripts/cdp_pool.py down --workdir "<工作目录>" --base 9222 --span 3
uv run python scripts/orcid_probe.py "<ORCID>"
uv run python scripts/orcid_extract.py "<ORCID>"
uv run python scripts/disambiguate.py --surname "<姓>" --given "<名>" --affil "<单位指纹>" --anchor-doi "<DOI>"
uv run python scripts/interpret_prepare.py --engine=legacy --limit=10
uv run python scripts/agent_poll.py --stage H --json
uv run python scripts/interpret_build.py
```

`run_all.sh` 位置参数：`AUTHOR_NAME ORCID_ID [BASE_DIR] [N_INST]`，默认输出根为 `YYYYMMDD`，作者工作目录为 `<BASE_DIR>/<作者名>`，CDP 默认 3 实例；环境变量 `PAPER_CDP_LEVEL` 仅接受 0/1/2/3，默认 0。该脚本要求 Git Bash/Bash、`uv` 和可用浏览器配置，执行前必须完成准备计划；具体阶段及环境变量按 `SKILL.md` 和脚本注释设置。

`cdp_pool.py` 子命令：`up|status|down|renew|kill|clean`。公共 `--workdir`；up/renew 可用 `--browser PATH`；up/status/down 可用 `--base PORT`；status/down 可用 `--span N`；renew/kill 可用 `--port PORT`；up 另有 `--n N`、`--levels CSV`、`--fresh`；renew 另有 `--level 0..3`、`--tag NAME`；clean 默认 dry-run，实际变更需 `--apply`。`--purge` 已废弃且删除被拒绝。Profile 归属按 workdir 管理，不要手工删除。

主链路文件接口：ORCID 页面解析结果→Crossref 题录→同名消歧结果→PDF/全文清单→task/card。具体作者定位和字段判据以 `SKILL.md` 的 A-E/H 阶段为准；完整的可选下载器接口见 `optional-cdp-fulltext/SKILL.md`，不可绕过它直接抓出版商全文。

### 13.5 解读、图谱、校验与交付

```bash
uv run python scripts/prep_r2_tasks.py --stage-dir .work_cdp/reviews --excerpt 2500
uv run python scripts/agent_poll.py --stage R2 --expect 4
uv run python scripts/subagent_gate.py --stage R2 --expect 4
uv run python scripts/reviews_build.py --dir .work_cdp/reviews
uv run python scripts/keywords_three_layer.py --check
uv run python scripts/keywords_three_layer.py --export --out .work_cdp/graph/vos --level keywords_core --min-doc 1
uv run python scripts/vosviewer_export.py --workdir "<工作目录>" --stage H --min-occurrences 1 --max-terms 500
uv run python scripts/nature_bridge.py --self-test
uv run python scripts/make_delivery_index.py --root "<日期根>" --mentor-only
```

- `agent_poll.py`：`--stage H|H2|R2`；`--expect N`；`--json`；`--check-json PATH`（检查单卡/目录 JSON 转义与语法）。常见退出码 0=齐、3=仍在运行、4=坏卡、2=无任务包/用法问题，按实际输出判断。
- `subagent_gate.py`：必需 `--stage H|H2|R2`；可用 `--expect N`、`--json`。
- `prep_r2_tasks.py`：`--stage-dir PATH`；`--excerpt N`（默认 2500）；`--restore` 从备份还原。
- `keywords_three_layer.py`：`--results`、`--tasks`；模式 `--check`/`--export`/`--fulltext`；`--out`、`--level keywords_core|keywords_method|keywords_reagent`、`--min-doc`、`--min-link`、`--corpus`、`--names`、`--json`、`--strict`、`--selftest`。
- `vosviewer_export.py`：必需 `--workdir`；`--stage H|H2`（默认 H）；可用 `--aliases`、`--out`、`--stage-dir`、`--min-occurrences`（默认 **1**，保留孤立词；小语料阈值 2 会剔掉约 95% 的词，聚焦共享词时调高）、`--max-terms`（默认 500，按篇频降序截断）。
- `nature_bridge.py`：主模式为 `--self-test` 或子命令 `locations`、`prepare`、`audit`、`content-check`、`check`。`prepare` 必需 `--pdf`/`--output`，可用 `--render-dir`/`--render-scale`；`audit` 必需 `--card`/`--locator-mode`，可用 `--bundle`/`--report`；`content-check` 必需 `--card`，并提供 `--source` 或 `--from-bundle`，可用 `--strict`；交付质量门 `check` 必需 `--card`/`--locator-mode`/`--report`，可用 `--bundle`、`--source` 或 `--from-bundle` 和 `--strict`。`--self-test` 是顶层零网络自检。
- `make_delivery_index.py`：`--root`、`--topic`、`--direction`、`--paper-only`、`--mentor-only`；两种模式不可同时指定，默认按发现到的产物自动判别。

#### 13.5.1 图谱与 PPT 参数补充

`knowledge_graph.py` 读取阶段 H/H2 最终产物，本地处理、不联网；除非已在阶段 0 逐项确认，否则不能启用 `--kw-model`。常用参数均采用 `--name=value` 形式：

| 参数 | 默认/取值 | 用途 |
|---|---|---|
| `--recent-years=N` | 默认 3 | 方向时间线窗口 |
| `--max-reviews=N` | 默认 3 | 每方向挂接综述上限 |
| `--dpi=N` | 默认 300 | PNG 输出分辨率 |
| `--format=auto/svg/png` | 默认 auto | 输出格式；auto 为 SVG 优先并允许回退 |
| `--formats=svg` | 可选 | 只出 SVG |
| `--also-png` | 关闭 | 在 SVG 外额外生成 PNG |
| `--max-dirs=N` / `--max-review-nodes=N` | 默认 12 / 24 | 方向数与综述节点上限 |
| `--wc-lang=both/zh/en` | 默认 both | 词云语言 |
| `--wc-engine=library/custom` | 默认 library | 词云布局实现；library 需要 wordcloud |
| `--wc-min-docs=N` | 默认按脚本常量 | 方法描述关键词最低篇数 |
| `--wc-weight-keywords=N` 等 | 默认 keywords=3/title=2/abstract=2/methods=1 | 控制词源权重；支持 `--wc-weight-key_20=N` |
| `--kw-model` | 关闭 | 使用独立 kw LoRA 小模型精筛，需阶段 0 明确确认 |
| `--strict-fonts` | 关闭 | 字号不足时转硬失败 |
| `--self-test` | 可选 | 逻辑自检 |

示例：

```bash
uv run --with matplotlib --with networkx --with python-igraph --with adjustText --with wordcloud python scripts/knowledge_graph.py --recent-years=3 --format=svg --also-png --wc-lang=both
```

`ppt_brief.py` 的参数是 `--name=value` 或布尔开关：`--ppt-dir=PATH`、`--outdir=PATH`、`--project-name=NAME`、`--title=TEXT`、`--pages=short|standard|long`、`--canvas=ppt169`、`--lang=zh-CN`、`--dry-run`、`--no-graphs`、`--check-svg=PATH`、`--self-test`。默认读取阶段 0 页数选择，并在 `$PPT_MASTER_DIR` 或随包 `vendor/ppt-master` 查找依赖；PPT 的风格/画布等用户选择必须按阶段 P 规范确认。

其他阶段工具完整索引见 `SKILL.md` §8。尤其全文校验 `verify_pdfs.py`、批量抽取 `extract_parallel.py`、翻译渲染 `render_html_pdf.py` 参数较多，执行前运行 `uv run python scripts/<脚本>.py --help` 并按各阶段规范调用。

### 13.6 Windows PowerShell 启动器

```powershell
.\start_local.ps1 doctor
.\start_local.ps1 scripts/harvest_faculty.py --url "<名单URL>" --stage list
.\start_local.ps1 -Model scripts/selftest_small_model.py
```

首参 `Script` 默认 `doctor`；命名开关 `-Model` 切换到 `SMALL_MODEL_PYTHON` 或脚本自动发现的独立模型解释器；剩余参数原样传给目标 Python 脚本。普通模式用项目 `.venv/Scripts/python.exe`。doctor 会导入核心 PDF/CDP/图谱依赖，缺 `.venv` 或依赖时报错退出。

### 13.7 可选解析层（分层共存）

```bash
uv run python scripts/optional_layers.py probe [--json]      # 首跑探测 + 体积/损益/建议
uv run python scripts/optional_layers.py record --mineru-install=yes|no|defer [--note=TEXT]
uv run python scripts/optional_layers.py status
uv run python scripts/optional_layers.py --self-test
uv run python scripts/opendataloader_extract.py "<pdf>" [--out DIR] [--format markdown,json] [--image-output off]
uv run python scripts/opendataloader_extract.py --detect [--json]
uv run python scripts/opendataloader_extract.py --self-test
```

- `optional_layers.py`：只读探测 + 本地落盘，不联网、不代装。子命令 `probe`（默认）/`status`/`record`/`--self-test`。首跑判据＝无产物目录（`*_paper_Exports`/`*_Exports`/`outputs`）＋无 `.work_cdp`＋环境无 MinerU；`probe --json` 的 `should_ask_mineru_install` 标明是否需向用户发问，`mineru_advisory` 给出体积/损益/建议。`record` 写 `.work_cdp/optional_layers.json`（schema `optional-layers`）并记 journal；记录后不再追问。退出码 probe/status/self-test 0；record 0=落盘、2=取值错误。
- `opendataloader_extract.py`：`vendor/opendataloader/` 的唯一 adapter，需 Java 11+。jar 发现＝`OPENDATALOADER_JAR` > `vendor/opendataloader/*.jar`。默认输出 `.work_cdp/odl/<主干>.md/.json`（JSON 逐元素含 `bounding box`/`page number`/`type`）。本地模式无 OCR。退出码 0=成功、1=解析失败、2=缺 Java/jar 或用法错误；`--detect` 0=可用、3=不可用。
- 两脚本均不替换 fitz/pypdf 主链路（`prep_paper.py`/`extract_parallel.py`）；深解析（扫描件/表格/公式）走 MinerU，不在 opendataloader 本地模式范围。
