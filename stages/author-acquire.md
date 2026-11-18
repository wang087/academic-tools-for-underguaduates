# 阶段 A/B/C/D/D2：作者定位与采集

> 本文件由主 `SKILL.md` 拆分而来（按链分组，懒加载）。
> **红线与全局契约遵守主 `SKILL.md`**（硬性要求 1–9、§5.1 分层获取、§5.2 鼠标政策、
> 闸门/数据契约），本文件不复制。进入本阶段才读本文件。

### 阶段 A：作者定位与工作目录

1. `web_fetch` 作者主页 → 确认姓名、单位、研究方向、论文成果页 URL。
2. `web_search` 找 ORCID；用 ORCID API 交叉验证（看是否含已知代表作）。
3. 建立目录：`<工作目录>/<作者名>/`、`<作者名>_paper_Exports/`（全文）、`.work_cdp/`（中间数据与日志）。
   > 历史工作目录若已有 `<作者名>_Exports/` 且含 PDF，`store.exports_dir()` 会自动沿用，
   > 无需手工迁移（见 §7 目录名兼容说明）。
   >

> **ORCID 成果列表为空时**（薛天案例：ORCID 记录存在但 `works` 为 0 条——
> 作者未在 ORCID 侧同步）。此时改用**作者主页 + OpenAlex**作为基准集合：
> · 主页给出**作者自述的完整成果清单**（权威性最高，含署名标记 `*`/`†`/`#`）；
> · OpenAlex 用 **ORCID 关联反查作者 ID**（`https://api.openalex.org/works/doi:<任一代表作>`
> → 取 `authorships` 中的 ORCID 匹配项 → 得 `author.id`），再按 `author.id` 拉全量；
> · 三条线（主页 / ORCID / OpenAlex）一致即可确认身份，**同名风险用「作者列表含本人姓名 + 单位」双重过滤**；
> · 最终列表以**主页清单**为准（作者自己认可的成果集），OpenAlex 仅用于补 DOI 与摘要。

### 阶段 B：启动 CDP 浏览器（多实例并行）

> **为什么必须多实例（踩过的坑）**：单实例运行中 Edge 进程一旦中途退出，
> 脚本没有任何探活机制，后续每一篇都是 `WinError 10061 目标计算机积极拒绝`，
> **整批任务报废**。一实例一 profile 还天然形成
> **Cookie 池**：某个实例被 Cloudflare 挑战时，其余实例照常工作。
> 事故记录见 [实测结果汇总.md](实测结果汇总.md#b-1)。

**启动参数档位**（并列，不叠加；默认 **L0 日常档**，推荐 **L3 极速档**）：

| 档位                        | 参数                                                                                                                                                                                                                                                                   | 说明                                                                                                |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **L0 日常档（默认）** | 必需 3 个：`--remote-debugging-port` `--remote-allow-origins=*` `--user-data-dir`正常窗口：`--window-size=1280,900`跳过开箱体验：`--no-first-run` `--no-default-browser-check`屏蔽权限/通知弹窗：`--deny-permission-prompts` `--disable-notifications` | **「像正常打开的浏览器」**，参数仍然克制                                                      |
| L1 极简档                   | 仅 3 个必需参数                                                                                                                                                                                                                                                        | A/B 对照：判断某站点的异常是否由参数本身引起                                                        |
| L2 扩展档                   | L0 +`--lang=zh-CN`                                                                                                                                                                                                                                                   | 需要中文界面时                                                                                      |
| **L3 极速档（推荐）** | L0 +`--disable-background-networking` `--disable-component-update` `--disable-sync` `--disable-default-apps` `--no-service-autorun` `--disable-client-side-phishing-detection` `--disable-domain-reliability`                                            | **只关后台服务，不改页面指纹**；冷启动更快、后台占用更低。多实例并行冷启动为十几秒级（见 [实测结果汇总.md](实测结果汇总.md#b-3)） |

> **窗口尺寸**：用 `--window-size=1280,900` 开成**正常窗口**，
> **不要**用 `--start-minimized`（最小化窗口的渲染节流会拖慢页面加载，
> 也更容易被判定为异常环境）。屏幕较小时 Edge 会自行收缩到可用范围
> （实际尺寸可能略大于请求值），属正常行为，不要当成故障。

> **权限 / 通知弹窗**：由 `--deny-permission-prompts`（自动拒绝地理位置、
> 摄像头、通知等权限请求）+ `--disable-notifications`（屏蔽网页通知）解决。

> **Cookie / 同意书弹窗**：Chromium/Edge **没有**对应的命令行开关
> （Edge 的「阻止 cookie 横幅」是浏览器设置项，不是启动参数）。
> 由 `cdp.py` 的 `CONSENT_JS` 在页内**保守**关闭，`connect()` 时自动注册：
> · 已知同意管理平台容器共 **98** 个选择器（OneTrust / Cookiebot / Quantcast / Osano /
> Didomi / TrustArc / Usercentrics / CookieYes / Complianz / Iubenda / Klaro / Termly /
> SourcePoint / Evidon / Moove / HubSpot / Borlabs 等），另有 `div[class*="cookie-consent"]`
> 一类通用兜底（**必须带 cookie/consent 关键字**，否则会误伤正文）；
> **找不到已知容器就什么都不做**，绝不全局搜按钮文字；
> · 在每个容器内、容器的 shadow root 内、容器所在 shadow root 内查找按钮
> （`Usercentrics v2` 等把横幅渲染在 shadow DOM 里，`document.querySelector` 查不到）；
> 每个容器至多收 40 个候选按钮，每个选择器至多 5 处命中、总计上限 60 个容器；
> · 只认**明确表示接受**的按钮文字；**刻意不含** reject / decline / settings ——
> 那是拒绝或二次选择，不是本脚本该替用户做的决定；找不到接受按钮则走隐藏兜底；
> · 同一个容器**至多点击一次**（`window` 上挂不可枚举 `WeakSet` 记账）：
> 部分站点「接受按钮只写 cookie、不销毁 DOM」，不记账会被每次巡查重复点击，
> 向站点重复提交同意请求；
> · **iframe 容器一律只隐藏、不点击**（跨域 iframe 内部无法操作，同源 iframe 也不深入），
> 保持「只做人类本来就会做的动作」这一保守边界；
> · 可见性用 `getBoundingClientRect()` 判定，**不用** `offsetParent`
> （规范规定 `position:fixed` 元素的 `offsetParent` 恒为 `null`，
> 用它会把 Cookiebot / Osano / Didomi 这类本身就是 fixed 的横幅全部漏掉）；
> · DOMContentLoaded + `load` + **25×1.2s（约 30s）定时巡查** + 滚动/点击后补扫（应对延迟弹窗）；
> · `CDP.dismiss_consent()` 可在页面加载完成后再补一刀，供下载流程每篇调用；
> · **不改动任何 navigator/screen 属性** —— 是可用性处理，不是指纹伪装。

> [WARN] **不要**加 `--headless`（无头最易被识别）、**不要**加
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

> **提速要点**：① 并行启动（先全部拉起、再统一探活，0.25s 轮询）；
> ② L3 关后台任务；③ `navigate()` 自适应 settle（正文就绪即返回）。

> **端口 ↔ profile 约定**：profile 收在 **`.work_cdp/cdp_profiles/`** 下（不散落在交付目录）；
> `9222 → .edge-cdp-profile`（沿用历史 profile，保留已获得的放行
> Cookie）；其余端口 → `.edge-cdp-p<port>`（独立 profile）。
> 历史遗留若仍建在输出根，**优先沿用、不自动迁移**（换路径等于 Cookie 作废）；
> 收拢用 `cdp_pool.py clean --apply`（先 `down`；**移动**而非删除）。
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

> [STOP] **同名消歧（不可省）** —— 作者聚合普遍**过度合并**（OpenAlex 单一 author id
> 会混入同名他人的无线通信、有机化学、微电网等论文）。跑 `disambiguate.py`，
> 判据由 `PAPER_SURNAME` / `PAPER_GIVEN` / `PAPER_AFFIL_TOKENS` / `PAPER_ANCHOR_DOI` 提供。
>
> **四重判据（按优先级）**：
>
> | 判据                    | 码                                     | 性质                 | 说明                                                                                                                                |
> | ----------------------- | -------------------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
> | **⓪ ORCID 溯源** | `orcid-linked`                       | **肯定**       | DOI 出现在**本人 ORCID 记录**里 → 直接确认。**ORCID→Crossref 主路径上最关键的一条**（作者本人认领，强于任何单位指纹） |
> | ① 姓名精确             | `name-mismatch`                      | 肯定（可排除）       | 记录**有**作者名单，但无「名精确匹配」者                                                                                      |
> | ② 单位指纹             | `affil-hit` / `affil-mismatch`     | 肯定                 | 命中 → 确认；不命中 → 可排除                                                                                                      |
> | — 数据缺失             | `no-author-data` / `no-affil-data` | **非否定证据** | **一律保留**，只标注待核                                                                                                      |
>
> [WARN] **为什么必须有判据⓪**：Crossref **只有少数出版社存缴逐作者单位**
> （一批论文里往往只有一两篇有）。若只靠单位指纹，主路径上必然
> 全部落到「数据缺失」，消歧等于空转 —— 这这正是实跑中真实发生过的情形。
>
> [WARN] **但判据⓪ 必须配同域校验**：ORCID 的**自动导入**（Web of Science / Scopus
> 认领、或同名作者被系统合并）会把**同名他人**的条目写进本人成果记录，此时
> 「DOI 出自 ORCID = 本人认领」这个强信号本身就脏了。故
> `--domain-terms=<方向词1,方向词2,…>`（缺省回退 `PAPER_DOMAIN_TERMS`，再回退本次
> `topic_plan.json` 的主题/方法词）：溯源命中**且**题录含方向词 → `orcid-linked` 确认；
> 溯源命中但方向词**一律不命中** → 降为**待复核**（`orcid-offdomain`，不删不排除，
> `scope.py` 不会据其排除）。判定文本 = 题名 + **刊名** + 摘要 ——
> 只看题名会把本人早年的朴素题名误拦。三处词表都没有时**跳过该校验并告警**，
> 不假装判得动。
>
> [STOP] **同形词不能单独作数**：`aging`（冶金里是「时效处理」）、`plasma`（等离子体）、
> `immune`（计算机里的入侵检测）等词跨领域同形。**期刊层面的硬排除必须先看**，
> 再用关键词判定 —— 只靠标题关键词过滤会让材料学论文混进选池（实测发生过）。
> 词表一律**运行时提供**（同一形词坑在阶段 R 的 arXiv 线上也出现过）。

> **消歧结果已接入下游**。此前 `disambiguated.json` **没有任何脚本读取**，
> 消歧跑完只打印给人看 —— 同名他人论文照样被下载、照样进最终列表，
> 等于这条质量要求在流程上落空。现由 `scripts/scope.py` 统一读取，
> **下载侧（`download_all_fulltexts.py`）与著录侧（`build_list.py`）共用同一判据**。
>
> **只排除「肯定性判定」**（`name-mismatch` / `affil-mismatch`）；
> 「署名块无单位字段」这类**数据缺失**（`no-affil-data`）**一律保留**，
> 并在报告中显式列出待人工核 —— 漏收真论文的代价远大于多留一篇。
> 逃生阀：`PAPER_SCOPE_INCLUDE_ALL=1` 可完全关闭排除，便于对照全量。

### 阶段 D2：反向查找参考文献（综述网络 + 高质量文献列表）

> **闸门**：`uv run python scripts/prepare_stage.py --check D2`
> （退出码 3 = 准备阶段已关闭 `reverse_refs`，本阶段跳过）

> **为什么放在这里**：阶段 D 结束时作者的成果集**已确认无误**（含同名消歧），
> 正好拿它去反向扩展 —— 取作者每篇论文的**参考文献**，倒推出这位作者
> **站在谁的肩上**，从而把「作者论文分析」升级为「研究方向全景」。

> **为什么走 OpenAlex 而不是抓页面**：`referenced_works` 属于**元数据 API**，
> 正是 §5.1 分层策略里的**第一层**（结构化元数据 + 摘要），**允许直连**。
> 全程无浏览器、无风控、**不额外下载任何 PDF**。

```bash
uv run python scripts/refs_backfill.py
#   可选：--min-citations=200   高质量门槛（默认 200）
#   可选：--limit-sources=4     只用前 N 篇作者论文（调试）
#   可选：--top=40              报告里最多列多少条（0=全部）
#   可选：--offline             只用 cache/ 里的响应（断网/复现）
```

**「高质量」的定义（用户指定）**

> **用户修订原文**：「获取他人综述全文或详细摘要进行分析，增加置信度。
> 同时新增修改限制，**引用次数 200 次不作为硬性要求**，
> 重点关注**近几年发表于 "CNS" 及相关高水平刊物的综述文章**。」

| 项                                           | 实现                                                                                                                                                                         |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **入选门槛**                           | 满足任一即入池：**是综述** ／ **核心期刊**（CNS 家族等）／ 被引 > `--min-citations`（**默认 0 = 不设门槛**）／ 被作者 ≥3 篇论文引用（网络核心）         |
| **排序**                               | 五项加权求和（**权重与分量逐条写进报告**）：综述 `40` + 核心期刊 `25` + 近年（近 5 年，每远一年减 4）`20` + 被引（**对数尺度**）`15` + 共引 `10`       |
| [STOP]**被引不再是门槛**               | 旧版 200 次门槛会把**近两年的高质量综述整批挡在门外** —— 而那正是本版要重点看的。现在被引只占 15 分且用对数，1000 次与 100 次只差几分，不主导排序                    |
| [STOP]**「近期」不能单独构成入选理由** | 若让「近 5 年」单独放行，等于把近五年的参考文献**几乎全收进来**，「高质量」就失去意义。**近期只影响排序，不影响入选**                                   |
| 核心期刊判定                                 | `load_core_journals()` 的**显式清单**（**不用前缀匹配** —— `science`/`cell` 前缀会误命中 Science of the Total Environment、Cellulose、Cells 等非核心刊） |

**为综述取全文 / 详细摘要（提高置信度）**

| 证据层级              | 怎么拿到                                                                                                               | 下游用法                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `fulltext`          | **开放获取**综述 → Europe PMC `fullTextXML`（**结构化开放获取接口**，非抓出版社网页；非 OA 一律返回空） | 任务包直接带全文，subagent 读全文，结论置信度最高 |
| `detailed-abstract` | 摘要 ≥800 字符（通常含 Objective/Method/Result）                                                                      | 按详细摘要解读                                    |
| `abstract`          | 常规摘要                                                                                                               | 按摘要解读                                        |
| `metadata-only`     | 连摘要都没有                                                                                                           | 只能依据题录，报告**如实标注**              |

> **预期分布**：绝大多数综述只能拿到**详细摘要**，能取到全文的是少数，
> 另有少量连摘要都没有、只剩题录。分布数据见
> [实测结果汇总.md](实测结果汇总.md#g2-2)。
> `--no-review-fulltext` 可跳过这一步。

> [WARN] **「综述」是启发式判定**：OpenAlex 没有 `review` 类型，判据 = `type` +
> 刊名含 `Reviews` + 题名以 `Review/Survey/Overview/Perspective/Advances in…` 开头。
> 可能漏判或误判，报告里**逐条标出判定理由**供复核。
> **不要**把它当作权威的文献类型标注。

**产出**：

| 文件                                 | 说明                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------ |
| `<作者名>_高质量文献列表.md`       | 交付物：统计 / 综述优先清单 / GB/T 7714 著录 /**反向索引**（哪几篇作者论文引用了它） |
| `.work_cdp/refs/source_refs.json`  | 作者论文 → 其参考文献（含`source_id`，供阶段 G2 查 IF 复用）                            |
| `.work_cdp/refs/refs_pool.json`    | 参考文献的去重题录池（含摘要倒排索引重建的`abstract`）                                   |
| `.work_cdp/refs/high_quality.json` | 下游 G2 / H2 的**唯一输入**                                                          |
| `.work_cdp/refs/cache/`            | HTTP 响应缓存（可断点续跑 / 离线复现）                                                     |

> **诚实性**：OpenAlex 的参考文献**存缴不完整**，作者论文中有一部分会
> 「无参考文献记录」——脚本会在统计里**显式报出这个数字**。
> **缺记录不等于该文没有参考文献**，不得据此推断任何结论。
