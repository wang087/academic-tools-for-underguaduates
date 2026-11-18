# 阶段 R / R① / R①.5 / R2：综述检索与领域概貌

> 本文件由主 `SKILL.md` 拆分而来（按链分组，懒加载）。
> **红线与全局契约遵守主 `SKILL.md`**（硬性要求 1–9、§5.1 分层获取、§5.2 鼠标政策、
> 闸门/数据契约），本文件不复制。进入本阶段才读本文件。

### 阶段 R：平行检索优质综述（**多平台并行 + 综述优先**）

> **目的**：让本科生在**不读几十篇原始论文**的前提下，先拿到该领域的**概貌**。
> 综述（Review）是最高效的入口 —— 它已经把领域的来龙去脉、主要流派、
> 争议点整理过一遍。
>
> **必须复用工作文件夹下已有的 skill，不要从头自编检索逻辑。**
> 见下方「复用哪些 skill」。

**检索策略：按方向选库 + 综述优先**

> ### 库要**按方向**选，不是三个都跑
>
> **来源**：用户指令 ——「根据不同方向匹配不同数据库。例如计算生物学、计算机科学、
> 人工智能匹配 arXiv；生物、医学匹配 PubMed、Europe PMC 等等」。
>
> **坑**：三线全跑时，arXiv 对湿实验免疫学主题返回的绝大多数是
> **计算机领域**的同形词（入侵检测 / 程序验证意义上的 `immune`）与
> **传染病建模**，对本主题**完全无用**；Google Scholar 反爬不可用。
> **三线里往往只有一条真正产出文献 —— 所谓「并行」名不副实。**
> （数据见 [实测结果汇总.md](实测结果汇总.md#r-1)。）
>
> 反过来，给「机器学习」只查 PubMed 也几乎命中不到预印本方法学论文。
> **故：按方向路由，skip 掉无覆盖的库。**

**路由表（唯一选库依据）**：`templates/方向数据库路由表.json`

覆盖**两套坐标系**（这正是阶段 N 警告过的「正交坐标系」问题）：

| 坐标系             | 数量            | 来源                                           | 例                                   |
| ------------------ | --------------- | ---------------------------------------------- | ------------------------------------ |
| **方法领域** | **24 个** | `研究方法与关键词总表.md`                    | 机器学习与深度学习、质谱与蛋白质组学 |
| **学部方向** | **12 个** | `templates/导师方向规则.json`（阶段 S 归类） | 免疫与感染、生物信息与计算           |

> [WARN] **用户通常先选「学部方向」**（因为那是"谁在做"），但**库是按"研究手段"配的**。
> 故路由表含 `faculty_direction_map`，把 12 个学部方向映射到方法领域，
> **无需用户自己换算**。例：
>
> - 「免疫与感染」→ 药理与临床研究 + 单细胞与多组学 → **只跑 Europe PMC，不跑 arXiv**
> - 「生物信息与计算」→ 生物信息学与基因组学 + 机器学习 → **Europe PMC + arXiv 都要**
> - 「成像与生物物理」→ 医学影像与数字病理 → **临床在 PubMed，影像 AI 在 arXiv**

```bash
# ① 查该方向该跑哪些库（零网络，秒级）—— 方法领域名或学部方向名都接受
uv run python scripts/route_databases.py --domain 机器学习与深度学习
uv run python scripts/route_databases.py --domain 免疫与感染 --terms "innate immunity" "T cell"
uv run python scripts/route_databases.py --list-domains      # 看 24 个方法领域各自的路由
uv run python scripts/route_databases.py --list-databases    # 看 7 个库各自的能力
uv run python scripts/route_databases.py --self-test         # 自检路由表
```

**主路由一览（节选；完整表见 JSON）**：

| 方向                           | 主力库                       | 补充库     | **明确不跑**                      |
| ------------------------------ | ---------------------------- | ---------- | --------------------------------------- |
| 机器学习与深度学习             | **arXiv**              | OpenAlex   | Europe PMC / bioRxiv / medRxiv          |
| 统计与数据分析 / 可视化        | **arXiv**              | OpenAlex   | Europe PMC                              |
| 量子计算 / 材料物理 / 天文地学 | **arXiv**              | OpenAlex   | Europe PMC                              |
| 药理与临床研究                 | **Europe PMC**         | medRxiv    | arXiv                                   |
| 神经科学 / 质谱 / 系统发育     | **Europe PMC**         | bioRxiv    | arXiv                                   |
| **生物信息学与基因组学** | **Europe PMC + arXiv** | bioRxiv    | ——（**唯一两侧都要**）          |
| **医学影像与数字病理**   | **Europe PMC + arXiv** | medRxiv    | ——（临床在 PubMed，影像 AI 在 arXiv） |
| 网络检索与情报分析 / 科研写作  | **OpenAlex**           | Europe PMC | arXiv                                   |

> [WARN] **arXiv 与 PubMed 是两套不同的文献生态**：
> arXiv 覆盖 cs / stat / physics / math / q-bio / eess；
> Europe PMC（含 MEDLINE/PubMed）覆盖生物医学。
> **两者交集很小** —— 这正是要按方向路由的原因。

**兜底策略（方向未登记时）**：`Europe PMC → OpenAlex` 依次补，
直到 ≥ 6 篇或库用尽；并在报告里**明确标注该方向未登记路由，用的是兜底库**。

**检索线（按路由结果执行，不再是固定三线）**：

| 线             | 平台                         | 何时跑                        | 检索式要点                                            |
| -------------- | ---------------------------- | ----------------------------- | ----------------------------------------------------- |
| **主力** | 路由表`primary` 指定的库   | **必跑**                | 见下方「各库语法差异」                                |
| **补充** | 路由表`secondary` 指定的库 | **仅当主力结果 < 6 篇** | 同上                                                  |
| **核实** | Crossref                     | 选定后校验 DOI/卷期页         | `query.bibliographic=`                              |
| ~~A~~         | ~~Google Scholar~~          | **不跑**                | 无官方 API 且反爬；需要时**如实报告该线未取到** |

> ### 各库语法**互不通用**（路由脚本已按库分别生成，勿手工混用）
>
> | 库                | 语法                         | 综述过滤                         | 能否按被引排序                   |
> | ----------------- | ---------------------------- | -------------------------------- | -------------------------------- |
> | Europe PMC        | Europe PMC 语法              | `PUB_TYPE:"Review"`            | [OK] 加`AND (SORT_CITED:y)`    |
> | arXiv             | Atom /`all:` 语法          | `all:"review" OR all:"survey"` | 不支持                           |
> | OpenAlex          | **原始参数串**         | `filter=type:review`           | [OK]`sort=cited_by_count:desc` |
> | Crossref          | **原始参数串**         | ——                             | [X]（仅核实用）                  |
> | bioRxiv / medRxiv | **日期区间**，非关键词 | ——                             | [X]                              |
>
> **`paginate.py` 没有 `--sort` 开关** —— 按被引排序的语法**必须写进 query 里**
> （Europe PMC 用 `AND (SORT_CITED:y)`）。脚本链里这件事由
> `search_plan.py --sort-cited` 落进检索式，`search_run.py` 执行时同一判据，
> **不要**手工拼了喂进去。
>
> [STOP] **`SORT_CITED:y` 的两条硬约束**（违反就查不出东西，且**不报错**、只给 0 条）：
> ① **不能**与 `(PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")` 同用 ——
> 结果池塌缩两个数量级；被引排序时只用单一 `(PUB_TYPE:"Review")`。
> ② OR 组里的限定词**越多池越小、可到 0** —— 每条检索式**不超过 5 个限定词**，
> 多子主题**分条检索再合并**，不要塞成一条长检索式。
> （两组对照数据见 [实测结果汇总.md](实测结果汇总.md#r-3)。）
> **坑**：不加排序时 Europe PMC 默认**按最新返回**，取回的全是当年、
> 被引个位数的新文，作为本科入门材料价值很低；加上排序后高被引的经典综述才浮上来。

> ### [OK] 已验证的检索式（全程零 API key）
>
> **B 线（Europe PMC，推荐主力）** —— 一条命令带计数对账、OA 标记、PMID/PMCID/DOI：
>
> ```bash
> python skills/paper-lookup/scripts/paginate.py \
>     --api europepmc \
>     --query '("Alzheimer" AND "early diagnosis" AND "biomarker") AND (PUB_TYPE:"Review" OR PUB_TYPE:"systematic-review")' \
>     --max-records 12
> ```
>
> **计数对账是内建的**：返回里的 `expected_total` / `retrieved_total` /
> `stopped_at_limit` 会明确告诉你结果是"被上限截断的部分结果"而不是"全部"。
> [WARN] 报告时**必须如实说明是部分结果**，不要把上限内的篇数说成"该领域全部综述"。
>
> **C 线（arXiv）** —— [WARN] **有限流，不能真的"同时"发**：
>
> ```bash
> # 取回后交给随包脚本解析（会处理 Error feed、版本号后缀等坑）
> curl -s "https://export.arxiv.org/api/query?search_query=all:%22Alzheimer%22+AND+all:%22review%22&max_results=12" \
>   | python skills/paper-lookup/scripts/arxiv_atom.py -
> ```
>
> **arXiv 限流：每 3 秒只允许 1 个请求**（实测连续请求会返回
> `HTTP 429` 或纯文本 `Rate exceeded.`）。因此"三线并行"指的是
> **B 线与 A 线并行，C 线串行且要单独降速**；不要把 arXiv 与其他
> 高频请求混在一起跑。`arxiv_atom.py` 遇到限流会**退出码 5** 并打印
> 明确提示，**不要**用自己写的解析绕过它。
>
> **A 线（Google Scholar）** —— [WARN] **无官方 API**，需经浏览器访问且有反爬。
> 按 §5.1 分层策略：**只取题录与排序信息**（属"综述题录"，允许第一层直连），
> **不要**尝试抓取 PDF 全文。若被拦，**如实报告该线未取到**，
> 用 B/C 两线结果继续，不要伪装成三线齐全。

**复用哪些 skill**（**按需加载**）：流程内自带副本在 `skills/` 下（`paper-lookup`、`paper-interpretation-writing`）；仓库根 `skills/` 是同源上游，只拷走 `本目录` 时用流程内副本即可。

| Skill                            | 在本阶段的作用                                                                                                                                                                                                                          |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `paper-lookup`                 | **主力**：跨 11 个学术 API（PubMed / arXiv / OpenAlex / Crossref / Semantic Scholar / Europe PMC / CORE / Unpaywall 等）检索，返回带可复现溯源的结果。**零凭据可用**；配 `NCBI_API_KEY` / `S2_API_KEY` 等可提高限流额度 |
| `literature-review`            | **系统综述方法论**：多库检索、筛选、综合的规范流程                                                                                                                                                                                |
| `research-lookup`              | 需要**当前证据汇编**或更深入的检索时使用                                                                                                                                                                                          |
| `citation-management`          | 综述选定后校验 DOI/PMID、去重、生成规范引用                                                                                                                                                                                             |
| `scientific-critical-thinking` | 对综述质量做证据分级（GRADE 等），避免拿低质量综述当权威                                                                                                                                                                                |
| `bgpt-paper-search`            | 需要**从全文抽取结构化实验数据**（方法/样本量/结论）时使用                                                                                                                                                                        |
| `scopus-wos-lit`               | **可选增强**：Scopus / Web of Science 的综述发现、分区过滤、摘要与全文、一作/通讯；输出阶段 R 认识的 `reviews.json`。见 [docs/综述获取增强_scopus_wos.md](docs/综述获取增强_scopus_wos.md) |

> **不要重复造轮子**：检索式构造、多库降级、去重、计数对账、引用格式化
> 上述 skill 已经覆盖（`paginate.py` 已内建"计数对账 + 限流 + 停止原因"）。
> 本阶段的工作是**编排**它们 + 把结果整理成本科生能读的形态。
>
> [WARN] **这十一个 API 会「用 HTTP 200 返回错误」** —— 例如 arXiv 对畸形参数
> 返回一个标题为 `Error` 的条目、Europe PMC 把错误码放在 200 响应体里。
> 所以**必须校验返回内容的结构**，而不是只看状态码；
> 随包脚本就是为挡这些坑而存在的，**不要绕过它们自己去解析**。

**筛选标准（综述优先）**：

1. **类型优先**：Review / Systematic Review / Meta-analysis / Survey > 原始研究；
2. **影响力**：被引次数高、发表在 CNS 及高水平刊物、领域内公认；
3. **时效性**：近 3–5 年优先（经典奠基性综述可放宽）；
4. **开放获取优先**：本科生常无法访问付费文献，优先选有 OA 全文的。

**产出** `.work_cdp/reviews/reviews.json`（结构同阶段 H 的任务输入格式）：

| 字段                                                       | 说明                                                                               |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `title` / `authors` / `journal` / `year` / `doi` | 题录                                                                               |
| `type`                                                   | `review` / `systematic-review` / `meta-analysis` / `survey` / `research` |
| `citations`                                              | 被引次数（注明来源与日期）                                                         |
| `oa_url`                                                 | 开放获取链接（有则填）                                                             |
| `why_selected`                                           | 一句话说明为什么选它                                                               |
| `platform`                                               | 从哪个平台/接口找到                                                                |

**数量**：**6–12 篇**为宜。少于 6 篇覆盖不全，多于 12 篇本科生读不完。

> **省事做法**：不必手工拼 `reviews.json` —— 把 `paginate.py` 的原始输出
> 直接喂给阶段 R① 的 `reviews_prepare.py --from-europepmc`，
> 它会自动归一字段、判定综述类型、排序（综述优先 + 被引降序）。
>
> **可选增强（Scopus / Web of Science）**：需要按 Scopus/WoS 收录取综述、用 JCR/中科院分区筛“前 20%”，
> 或补一作/通讯与 DOI 时，按需调用本流程自带的增强技能（默认不启用，不改变上面任何步骤）：
>
> ```bash
> uv run python scripts/reviews_discover_scopus.py \
>     --direction "神经科学" --source scopus --limit 8
> uv run python scripts/reviews_prepare.py \
>     --input .work_cdp/reviews/reviews_scopus.json --fetch-abstracts
> ```
>
> 输出 `{"records":[...]}`，字段与 `reviews_prepare.py` 对齐；可用 `--merge-into .work_cdp/reviews/reviews.json` 合并。
> 详见 [docs/综述获取增强_scopus_wos.md](docs/综述获取增强_scopus_wos.md)。

---

### 阶段 R①（脚本）：综述任务包组装

```bash
# 把阶段 R 的检索结果组装成并行 subagent 的任务包
# 产物：.work_cdp/reviews/{tasks,results,manifest.json}
uv run python scripts/reviews_prepare.py \
    --from-europepmc .work_cdp/reviews/europepmc.json \
    --fetch-abstracts
```

- 任务包 schema 与 `interpret_prepare.py` **逐字段同名**（`seq` / `doi` / `title` /
  `authors` / `abstract` / `evidence_source` / `result_path` …），
  所以 §7.2 的提示词模板可**原样套用**，subagent 不需要知道自己在跑哪条链路。
- 缺摘要时证据层级自动标 `metadata-only`，**不假装读到全文**。
- `--status` 之外无副作用；重复跑会覆盖同名任务包，不会累积垃圾。

---

### 阶段 R①.5（脚本）：综述全文获取（**skill 先试 / CDP 兜底**）

> **为什么需要这一步**：`reviews_prepare.py` 出来的任务包
> `evidence_source` **恒为 `abstract`** —— 因为阶段 R 的检索结果里
> **`pmcid` 字段从未被填充**，下游无从知道哪篇在 PMC 有全文。
> 结果 R2 只能读摘要，12 篇综述全部停在摘要级。
>
> 本步骤补上标识符并按 **§5.1 分层获取策略**取全文。

```bash
# 一步到位：补标识符 → 分层 → 第一层 → 缺口写 CDP 清单 → 回填任务包
uv run python scripts/reviews_fulltext.py --all --backfill

# 第二层-A：CDP 抓出版社落地页（需 CDP 实例）
#   启动器在**可选模块**内；本机需 --no-sandbox，且必须用普通 python（uv run 会阻止 Chrome 启动）
python optional-cdp-fulltext/cdp_up_nosandbox.py --n=3
uv run --with websocket-client python scripts/reviews_fulltext.py --cdp --n=3 \
    --delay=120-240 --site-fail-limit=1 --manual-wait=5   # [STOP] --manual-wait 必须 >0

# 第二层-B：出版社被 CAPTCHA 时，改走 PMC 仓库副本（仍经 CDP）
#   --ports 已启动的实例端口，可跳过自动探测（探测偶发失败时的可靠出口）
uv run --with websocket-client python scripts/reviews_fulltext.py \
    --repo --repo-pace=45 --ports 9222,9223,9224

# 副链路（CDP 备用）：上面三层都跑完仍有缺口时才跑，顺序不能颠倒
uv run python scripts/reviews_fulltext.py --fallback

uv run python scripts/reviews_fulltext.py --backfill
```

**分层判据**（照 `templates/直连白名单.json`，**不临场判断**）：

| 层       | 通道                           | 前提                                                                                |
| -------- | ------------------------------ | ----------------------------------------------------------------------------------- |
| 第一层   | EPMC`fullTextXML`            | `inEPMC=Y` **且** `isOpenAccess=Y`（白名单 `abstract+oa-fulltext-xml`） |
| 第二层-A | `download_all_fulltexts.py`  | 其余全部 ——**含 OA 文章**（§5.1 四之3）                                    |
| 第二层-B | `--repo`：PMC 仓库副本经 CDP | 出版社被 CAPTCHA 时；**仍是第二层**（白名单禁直连 `www.ncbi.nlm.nih.gov`）  |
| 副链路（备用） | `--fallback` → `fulltext_router.py` | 上面三层都没拿到全文的那些 DOI；**且**阶段 0 的 `fulltext_fallback` ≠ `off` |

> [STOP] **`reviews_fulltext.py --manual-wait` 默认 0.0，而 0 会让整条 CAPTCHA 弹窗链完全不触发**
> （日志只留 `失败: challenge:title`，本可人工放行的站点被当成不可得）。
> 跑第二层-A **一律显式传正值**，如 `--manual-wait=5`（分钟）。
>
> **第二层-B 的正文判据**：PMC 文章页**不总把正文渲染进 `innerText`** ——
> 摘要视图、`Loading PDF` 外壳、或只给「View on publisher site」入口时，页面骨架也能有几千字。
> 所以「页面打开了」**不等于**「拿到正文」：字数不足 `MIN_FULLTEXT_CHARS` 时，
> 脚本按页内**功能入口**（`Full text` / `PDF` / `View on publisher site`）逐个导航后重读，
> 仍不足则如实记 `too-short`（交副链路或人工），**不写入伪全文**。
> [X] 兜底只走正文入口 —— 人机验证按 §5.2：允许宿主机级点击（≤3 轮），失败即停。

> ### 副链路
>
> 把 nature-downloader 的 `routeDoiDownload` 路由链
> （Crossref 元数据 →（中文硬分流）CNKI → 出版商 API → OA 瀑布 →
> Web Access 浏览器 → API 兜底需显式授权）**只搬编排层**进来，
> 落在 `scripts/fulltext_router.py`（不外链 nature-skills，
> 真正的取件全部委托给既有 7 个 `fulltext_*` / `oa_candidates` / `fetch_pacing` 模块）。
>
> > #### [STOP] 两个前提，缺一不可
> >
> > 1. **CDP 主链路已经失败过** —— `route_doi()` 在 `cdp_failed` 不为 `True` 时
> >    **直接拒绝**（status `route_disabled`，CLI 退出码 3）。
> >    这不是形式检查：§5.1 第二层规定「原文全文一律走 CDP」，
> >    副链路只是主链路跑不动之后的兜底，**不是**绕开 CDP 的近路。
> > 2. **只取直连白名单内的候选** —— 每个候选仍过 `white_list_policy.decide()`
> >    （失败即拒）。**白名单文件本身不得为此改动。**
> >
> > 白名单里能出 `fulltext` 的只有 `www.ebi.ac.uk`（EPMC OA fullText XML）一家，
> > 所以直连兜底的实际覆盖面**很窄** —— 这是如实说明，不是保守话术。
>
> **四种结果**（写进 `<stage>/reviews_fallback.json`，由回填第 ④ 通道收取）：
>
> | status                            | kind    | 含义 / 下一步                                    |
> | --------------------------------- | ------- | ------------------------------------------------ |
> | 各成功态（见 `fulltext_result.py`） | SUCCESS | 拿到正文 `.txt`，回填时与其它通道**按字数择优** |
> | `browser_handoff`                 | HANDOFF | 有合法候选但不在白名单内 → **交给 CDP**（不是失败） |
> | `api_fallback_confirmation_required` | HANDOFF | 出版商 API 兜底 → **需用户显式授权**，不代填、不代登录、不代持凭据 |
> | `oa_not_found` / `oa_resolution_inconclusive` | FAILURE | 确实无路，交付时如实标 `abstract` |
>
> 中文文献（`cnki` 字段 / CNKI 链接 / `zh|zho|chi` 语言 / 标题含 CJK）
> 走 **CNKI 硬分流**，产出检索页链接的交接单 + `preflight_fallback.py` 指引。
>
> **阶段 0 的取舍**：`fulltext_fallback` ∈ `off` / `oa`（默认）/ `oa+api`。
> `oa+api` 才会在三重闸门全开时先试出版商 API（成功后写
> `oa_status:"not_checked_api_first"` 并跳过 OA 瀑布）。
>
> **先干跑一遍看路由判定，零网络**：
>
> ```bash
> uv run python scripts/fulltext_router.py --plan 10.1016/j.cell.2014.03.011
> ```

> [WARN] **不要用 `eutils` 的 `efetch.fcgi?db=pmc` 取 `<body>`** ——
> 白名单把该域名定为 `content_class: "abstract"`，取正文**越界**。
> 且 PMC 系接口对非 OA 文章会返回「HTTP 200 + 无 `<body>`」，
> 失败原因藏在 XML 注释里（详见 `skills/paper-lookup/references/pmc.md`）。

> **坑**：Elsevier / AACR 系**即便放宽间隔仍会被 CAPTCHA**，
> 只能走第二层-B 的 PMC 仓库副本才拿得下。
> 三层各自的实际覆盖度与正文字数见
> [实测结果汇总.md](实测结果汇总.md#r15-1)；
> **诊断与修复全过程见 `outputs/03_领域概貌/CDP跑通记录.md`。**

**回填为什么必要**：R2 的 subagent **只读 `tasks/*.json`**。
正文若不进任务包，subagent 依然只能看到摘要。

> ### [STOP] 回填后必须再跑 `prep_r2_tasks.py` —— 否则 R2 静默读到假全文
>
> **坑**：回填把全文写进任务包的 `text` 字段，
> 而 JSON 里这个字段是**一整条物理行**（换行被转义成 `\n`），
> **读取工具会把超长单行截断** —— 十几万字的正文只剩开头一小段能被读到。
>
> **这是静默失败**：任务包看起来"有全文"，subagent 也以为在读全文，实际没读到，
> 而且**不报错**。具体字符数与受影响的 subagent 数见
> [实测结果汇总.md](实测结果汇总.md#r15-2)。
>
> **对策**：
>
> ```bash
> # 默认认**本次运行**的任务包目录（`.work_cdp/reviews/`，其次本次 outputs/ 下含 tasks/ 的目录）；
> # 跨目录临时处理时才用 --stage-dir 显式指定。
> # [X] 它**不再**回看日期隔离前的旧布局 `outputs/` —— 那里躺着历史案例，
> #     静默选中会改写别人的任务包（宁可报错，也不要错答）。
> python scripts/prep_r2_tasks.py
> ```
>
> 它把全文落到 `<stage>/fulltext/<slug>.txt`（保持段落，并把超长段落
> **软换行到 1200 字符/行**，确保每行都能被完整返回），
> 任务包只留开头摘录 + `fulltext_path` 指针。
> subagent 读法变为：**先读小 JSON 拿题录 → 再分段读 `fulltext_path` 的 txt**。
>
> 原任务包会备份到 `<stage>/tasks_full/`，可用 `--restore` 还原。

**取不到就如实标 `abstract`** —— 脚本会告警，交付时必须说明覆盖度。

---

### 阶段 R2：领域概貌解读（**复用阶段 H 的并行 subagent 机制**）

> **目的**：把阶段 R 选出的综述**读成一份本科生能看懂的领域概貌**，
> 而不是丢一堆 PDF 链接给用户。

**执行方式**：**复用 `subagent-driven-review/SKILL.md` 的同一套并行机制**
（其 §7.2 提示词模板、解读卡 schema、质量控制全部适用）。

> **档位**：本阶段用 **`review_interpret_tier`，默认 `L3`（完整）**。
> 理由：领域概貌是**独立成篇**的交付物，本科生要据此判断"这个方向在解决什么问题、
> 哪些还有争议"，低档会丢掉排除性证据与作者语气限定。
> 查询 `prepare_stage.py --tier R2`（且**阶段 0 的档位确认同时覆盖 R2**）。
> L3 在这里 = 论证链完整 **加上** 术语都要解释，不是"可以说得更肯定"。

```bash
# ① 组装任务包（见阶段 R①）
uv run python scripts/reviews_prepare.py \
    --from-europepmc .work_cdp/reviews/europepmc.json --fetch-abstracts

# ② 在同一条消息内并行分派 subagent，每篇一个
#    提示词模板：子流程 §7.2（领域概貌版）
#    [STOP] 并发度：每批 ≤ 4（见下方说明）；收卡用 agent_poll.py 轮询（30–60 秒），不要盲等固定时长

# ③ 查看进度（有几张卡、缺哪几篇、哪些被拒收）
uv run python scripts/reviews_build.py --status

# ④ 汇总成《领域概貌.md》（合并底稿渲染版）+ 产出综合底稿 synthesis_dossier.json
uv run python scripts/reviews_build.py --topic "阿尔茨海默病早期诊断"

# ⑤ 综述综合步：派**一个**综合 subagent，读 synthesis_dossier.json + 全部解读卡，
#    按 subagent-driven-review/references/r2-synthesis.md 的综述写法写终稿《领域概貌.md》
#    （立 spine→跨源综合共识→合并方向/概念→有界不确定性→引综述；**禁止逐卡拼接**）。
#    任务包：.work_cdp/reviews/synthesis_task.json
```

> ### [STOP] 并发度必须限制在每批 ≤ 4 篇
>
> 整批一次性派出去，**大部分会失败**，且失败的产出**结构合法但内容是
> `placeholder`**；分成小批派则全部成功、无占位符
> （对照数据见 [实测结果汇总.md](实测结果汇总.md#r2-1)）。
>
> 成因：高并发下 subagent 会触发生成长度上限，用 `"placeholder"` / 空串 / 截断
> 填充字段。**schema 校验只管字段类型，管不了内容是不是真话。**
>
> **收卡方式**：用 `agent_poll.py --stage R2` **轮询**（30–60 秒一次），按剩余篇数决定下一批大小；出现坏卡则下一批减半并单独重派。见 §4.1。
>
> 因此 `reviews_build.py` 增加了**内容级校验**：检出占位符的卡会被
> **拒收并单独列出**（不会进入《领域概貌》），并提示"降低并发度重跑"。
> 用 `--allow-placeholder` 可放宽（不建议）。
>
> **推论**：任务包 6–12 篇 → 分 2–4 批跑，不要一次性全发。

> ### 卡片的两种落盘方式（二选一，别混）
>
> | 方式                    | 做法                                                                             | 适用                       |
> | ----------------------- | -------------------------------------------------------------------------------- | -------------------------- |
> | **调用方落盘**    | `agent(prompt, {schema})` 返回值回到调用方，由调用方写 `results/<slug>.json` | 需要 schema 强约束字段     |
> | **subagent 自写** | 提示词里给出任务包的`result_path`，让它直接写文件                              | 字段较多、避免大 JSON 回传 |
>
> 两种都可行。**关键是与下游一致** —— `reviews_build.py` 只读
> `results/*.json`，所以无论哪种方式，卡片最终都必须落到那里。
> （阶段 H 用的是第二种；本阶段两种都验证过。）

**解读卡侧重点（与阶段 H 的差异）**：

| 维度 | 阶段 H（作者论文） | **阶段 R2（本科生概貌）**                                 |
| ---- | ------------------ | --------------------------------------------------------------- |
| 读者 | 研究者             | **没有科研经验的本科生**                                  |
| 重点 | 方法学细节、创新点 | **领域全貌**：在解决什么问题、有哪几派、结论是否一致      |
| 术语 | 保留专业术语       | **解释术语**，必要时给中文对照                            |
| 产出 | 深度解读报告       | **领域概貌**：入门读物 + 关键概念 + 争议点 + 下一步读什么 |

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
