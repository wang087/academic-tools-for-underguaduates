# 阶段 R 综述全文获取 · 可复用模块

> **模块**：`流程1/scripts/reviews_fulltext.py`
> **依据**：`SKILL.md` §5.1 分层获取策略（最高优先级）、§5.2 鼠标自动化禁令
> **定位**：把阶段 R 的检索产物直接接到全文获取链路上，**skill 先试、CDP 兜底**

## 实测战绩（2026-10-06）

| 项 | 结果 |
| --- | --- |
| 阶段 R 综述 | 12 篇 |
| 补全 PMCID | **12/12**（原检索全空） |
| **最终取得全文** | **12/12（100%）** |
| 正文字数合计 | **1,303,702 字** |
| 正文占比（去站点框架） | **97–100%** |

三条通道分别贡献：

| 通道 | 层 | 篇数 | 说明 |
| --- | --- | --- | --- |
| Europe PMC `fullTextXML` | 第一层 | 4 | 白名单授权的 OA 全文接口 |
| CDP 抓出版社落地页 | 第二层 | 2 | 含 PDF；Imperva 挑战可自动通过 |
| **CDP 抓 PMC 仓库副本** | 第二层 | **6** | **突破 Elsevier/AACR CAPTCHA 的关键** |

---

## 一、为什么需要这个模块

阶段 R（平行检索优质综述）原本在 R① 就停住了：

```
阶段 R 检索 ──→ R① 组装任务包 ──→ R2 并行精读 ──→ R② 领域概貌
                    ↑
           evidence_source 恒为 "abstract"
           pmcid 字段从未被填充
```

**实测**（`outputs/03_领域概貌/manifest.json` 原始状态）：

```text
count: 12 | review_count: 12 | oa_count: 4
evidence_abstract: 12
evidence_metadata_only: 0
```

12 篇**全部**停在摘要级，而其中 **12/12 在 PMC 都有全文**。
根因不是"取不到"，而是**检索环节没采 `pmcid`** —— 下游无从知道哪篇有全文可拿。

R2 的 12 张解读卡如实标了 `evidence.source = "abstract"`，
**没有谎报读到全文**（这点是合规的）；问题出在上游供料不足。

---

## 二、分层规则（照抄 §5.1，不临场判断）

| 层 | 内容 | 方式 | 本模块的实现 |
| --- | --- | --- | --- |
| **第一层** | 元数据 / 摘要 / 综述题录 | ✅ 允许直连 | `resolve_ids()` 走 Europe PMC + PMC ID Converter |
| **第一层** | 白名单内开源数据库 | ✅ 允许直连 | `fetch_layer1()` 走 EPMC `fullTextXML` |
| **第二层** | **原文全文**（含 OA） | 🛑 **必须 CDP** | `build_cdp_input()` + `run_cdp()` |
| **第二层补充** | **仓库副本（PMC）** | 🛑 **仍走 CDP** | `fetch_repo_via_cdp()` |
| **回退** | 第一层失败 | → 转 CDP | `build_cdp_input()` 自动生成缺口清单 |

### 「仓库副本」通道为什么是合规的

出版社落地页被 CAPTCHA 挡住时，改抓 **PMC 仓库副本** —— 这**不改变层级**：

- 取 PMC 网页正文**属于「原文全文」**，按 §5.1 **仍必须走 CDP**；
  本模块**只用浏览器取**，不发任何直连请求。
- `fetch_direct.py --check-domain` 实测对 `www.ncbi.nlm.nih.gov` 返回
  **禁止直连（退出码 3）** —— 即白名单**不允许**直连取它。
- 它**不绕过任何付费墙**：只取 Unpaywall 已确认存在的开放副本
  （这 6 篇实测均为 `bronze` / `green`）。

> 换句话说：这是**换了一个受风控更弱的合法副本来源**，
> 而不是放宽获取规则。

### 关键判据：EPMC 而不是 PMC eFetch

`templates/直连白名单.json` 是**机器可读的边界**：

| 域名 | `content_class` | 允许取到 |
| --- | --- | --- |
| `www.ebi.ac.uk` | `abstract+oa-fulltext-xml` | 摘要 + **OA 全文 XML**（`oa_only: true`） |
| `eutils.ncbi.nlm.nih.gov` | `abstract` | **仅摘要** |

实测 `eutils` 的 `efetch.fcgi?db=pmc` **也能返回 `<body>` 全文**（12/12 成功），
但白名单把该域名定为 `content_class: "abstract"` —— **取 `<body>` 属越界**。

> 因此本模块**只用 EPMC `fullTextXML` 作为第一层的全文通道**。
> 想放开 `eutils` 需先按 §5.1 收录判据改白名单并让 `fetch_direct.py --self-test` 通过。

### 第一层的双重前提

```python
if pmcid and inEPMC == "Y" and isOpenAccess == "Y":   # 两者缺一不可
```

只满足 `inEPMC=Y` 但 `isOpenAccess=N` 时，EPMC 返回 500 且**无 `<body>`**。

---

## 三、用法

```bash
cd 流程1

# ① 只探测分层（零浏览器开销，先看有多少篇能走第一层）
uv run python scripts/reviews_fulltext.py --probe

# ② 第一层：取 OA 全文 XML → 纯文本
uv run python scripts/reviews_fulltext.py --layer1

# ③ 第二层：把缺口写成 CDP 清单 + 确切命令
uv run python scripts/reviews_fulltext.py --layer2

# ④ 第二层-A：CDP 抓出版社落地页
python scripts/reviews_fulltext.py --cdp --n=3 --delay=120-240 --site-fail-limit=1

# ⑤ 第二层-B：出版社被 CAPTCHA 时，改走 PMC 仓库副本（关键补充）
python scripts/reviews_fulltext.py --repo --repo-pace=45

# ⑥ 回填任务包（evidence_source: abstract → fulltext）
python scripts/reviews_fulltext.py --backfill
```

> **建议顺序**：①→②→④；④ 若被 CAPTCHA 熔断，再跑 ⑤；最后 ⑥。
> 实测 ④ 只拿到 2/8，⑤ 把剩下的 **6/6 全部拿下**。

**CDP 实例**（第二层需要）：

```bash
# 推荐：本机环境 renderer 沙箱创建失败，用带 --no-sandbox 的启动器
#       —— 它就绪判定含「renderer > 0」，能挡住假活实例
python optional-cdp-fulltext/cdp_up_nosandbox.py --n=3

# 正常环境可用原命令（沙箱是有价值的防线，不要默认关闭）
uv run --with websocket-client --with pypdf python scripts/cdp_pool.py up --n=3

python scripts/cdp_pool.py status
python scripts/cdp_pool.py down
```

> [WARN] **「端口就绪」不等于「实例可用」。**
> 本机实测过一种假活：`cdp_pool` 报 `CDP 就绪`，但该实例
> **renderer 数为 0**，于是所有页面级命令（`Runtime.evaluate`、取正文）
> 永远超时 —— 比启动失败更难查。
> 诊断只需一条：`Get-CimInstance Win32_Process` 数该实例的 `--type=renderer` 子进程。
> 根因与 12 组对照实验见 `outputs/03_领域概貌/CDP跑通记录.md`。

---

## 四、产物

| 文件 | 内容 |
| --- | --- |
| `<workdir>/.work_cdp/reviews_layer_split.json` | 逐篇分层判决（含 PMCID / inEPMC / isOpenAccess） |
| `<workdir>/.work_cdp/reviews_layer1.json` | 第一层取回结果（XML 路径 + 纯文本路径 + 字数） |
| `<workdir>/.work_cdp/fulltext_xml/*.xml` `.txt` | EPMC JATS 原文与转换后纯文本 |
| `<workdir>/.work_cdp/reviews_cdp_command.txt` | 第二层确切命令（可直接复制执行） |
| `<workdir>/.work_cdp/crossref_all.json` | 并入的阶段 R 题录（让既有下载器认得这些 DOI） |
| `<workdir>/.work_cdp/fulltext/*.txt` | 第二层 CDP 落地页正文（既有下载器产出） |
| `<workdir>/.work_cdp/fulltext_state.json` | 既有下载器的状态文件（本模块**只读不写**，避免两套状态漂移） |
| `<workdir>/.work_cdp/reviews_layer2_repo.json` | 仓库副本通道结果（PMC 经 CDP，`--repo`） |

> `<workdir>` 由 `logutil.workdir()` 决定，默认 `流程1/<YYYYMMDD>/`。

---

## 五、五个必须知道的坑

### ① 工作目录判据必须复用，不能各算各的

既有脚本一律 `WD = logutil.workdir()`，默认解析成 **`流程1/<YYYYMMDD>/`**
（**不是** `流程1/`）。本模块第一版硬写 `FLOW1 / ".work_cdp"`，
结果缺口清单写到了 `流程1/.work_cdp/`，而下载器去
`流程1/20261006/.work_cdp/` 找 `crossref_all.json`：

```text
FileNotFoundError: '...\流程1\20261006\.work_cdp\crossref_all.json'
```

**已修**：模块改为 `from logutil import workdir`，与既有脚本同一判据。

### ② 「拿到 XML」≠「拿到全文」

`skills/paper-lookup/references/pmc.md` 记载的陷阱：PMC 系接口对**非 OA** 文章
返回 **HTTP 200 + 结构完整的 `<pmc-articleset>`，但没有 `<body>`**，
失败原因藏在 XML **注释**里 —— 标准解析器会把它丢掉。

所以 `jats_body_stats()` 把「确认 `<body>` 存在且够长」作为**唯一判据**，
低于 `MIN_FULLTEXT_CHARS = 6000` 一律不当作正文。

### ③ 付费墙残页会**静默盖住**真正文

实测 `10.1037/a0035302` 同时存在两份产物：

| 来源 | 字数 | 实际是什么 |
| --- | --- | --- |
| CDP 落地页（APA 出版社） | 3,077 | **付费墙提示页** |
| CDP 仓库副本（PMC） | 301,678 | 真正文 |

若回填按「先到先得」，**3 千字的广告页会盖住 30 万字的真正文**，
而任务包里 `evidence_source` 仍然是 `"fulltext"` ——
下游 subagent 会把它当综述精读。**这种错误静默且致命。**

**已修**：`backfill()` 收集**全部候选**后按**正文字数取最大者**，
并在择优时打印被弃用的来源，便于核对。

### ④ 出版社风控与对策

| 站点 | 间隔 25–55s | 间隔 120–240s | 仓库副本 |
| --- | --- | --- | --- |
| 10.1158（AACR） | CAPTCHA 熔断 | ✅ 成功 | — |
| 10.1111（Wiley） | ✅ 成功 | — | — |
| **10.1016（Elsevier）** | CAPTCHA | CAPTCHA | ✅ **成功** |
| **10.4049（J Immunol）** | CAPTCHA | CAPTCHA | ✅ **成功** |
| 10.1037（APA） | 付费墙 | 付费墙 | ✅ **成功** |

**结论**：
1. **放大间隔确实有效**（10.1158 从熔断变成功）；
2. 但 **Elsevier / AACR 系即便放宽间隔仍被 CAPTCHA**；
3. 对这类站点，**改走 PMC 仓库副本是唯一有效路径**（6/6 成功）。

> **重要**：诊断过程本身会触发风控 —— 反复启停实例 + 高频访问同一批
> 出版社，会把 IP 计入风控。**排查应优先用离线/最小实例，不要拿目标站点试。**

### ⑤ [STOP] 回填后**必须**再跑 `prep_r2_tasks.py`，否则 R2 静默读到假全文

**这是本模块最容易踩的坑，且完全静默。**

`backfill()` 把全文写进任务包的 `text` 字段。但 JSON 里这个字段是
**一整条物理行**（换行被转义成 `\n`）：

```text
10_1016_j_cell_2014_03_011.json
  text 所在行长度 = 162,777 字符   ← 单行！
  读取工具对单行的上限 ≈ 2000 字符
  → subagent 只能看到全文的 1.2%
```

**实测后果**：阶段 R2 首批 4 个 subagent **全部失败**，
因为它们"以为"读到了全文（任务包确实含 `text`，`evidence_source` 也是
`fulltext`），实际只拿到开头 2000 字符。

**修复**（两步）：

```bash
# 1) 回填（照旧）
python scripts/reviews_fulltext.py --backfill

# 2) 【必跑】把全文搬到可读的 .txt
python scripts/prep_r2_tasks.py
```

`prep_r2_tasks.py` 做三件事：

1. 全文 → `<stage>/fulltext/<slug>.txt`（保持段落）；
2. **超长段落软换行到 1200 字符/行** ——
   只搬出 JSON 还不够，综述里常有 3000–8000 字符的长段落，
   **段落本身也会被截断**，必须换行才能 100% 可读；
3. 任务包只留开头摘录 + `fulltext_path` 指针（8KB → 小文件）。

原任务包备份到 `<stage>/tasks_full/`，`--restore` 可还原。

**修复后实测**：12 篇的 `.txt` 行数 122–1066、**最长行稳定 1200**，
subagent 分段读完后产出的卡片 `evidence.source` 均为 `fulltext`，
且自述"分 N 段读完正文、正文在第 X–Y 行、其后为参考文献"——
**可以核对，不是自称**。

> **教训**：判断"subagent 能不能读到"时，不能只看**字段是否存在**，
> 要看**读取工具实际能返回多少**。单行长文本是个隐蔽的截断点。

---

## 六、与既有脚本的关系（**不重复造轮子**）
§5.1 四之4 明令：原文类**一律用** `download_all_fulltexts.py` /
`save_pdf_via_browser.py`，自建下载器是**能力退化**。

因此本模块**只做编排**，下载逻辑全部复用：

| 能力 | 复用自 |
| --- | --- |
| JATS → 纯文本 | `scripts/jats_to_text.py` |
| 多实例并行 / 站点级熔断 / 断点续传 / 挑战宽限 / PDF 落盘 | `scripts/download_all_fulltexts.py` |
| 实例池起停 / 探活 / 档位表 / profile 规则 | `scripts/cdp_pool.py` |
| 落地页正文落盘 | `download_all_fulltexts.save_fulltext()` |
| 白名单判定 | `scripts/fetch_direct.py --check-domain` |

本模块新增的只有四件事：
**① 补 PMC 标识符**（阶段 R 漏采的那一步）、
**② 分层判决**、
**③ 仓库副本通道**（`--repo`）、
**④ 择优回填**。

---

## 七、实测结果（2026-10-06）

| 项 | 结果 |
| --- | --- |
| 阶段 R 综述 | 12 篇 |
| 补全 PMCID | **12/12** |
| 第一层可合规取全文 | 4/12（`inEPMC=Y` 且 `isOpenAccess=Y`） |
| 第二层需 CDP | 8/12 |
| **最终取得全文** | **12/12（100%）** |
| 正文字数合计 | **1,303,702 字** |

各通道明细：

| DOI | 来源 | 正文/总字数 | 正文占比 |
| --- | --- | --- | --- |
| 10.1016/j.cell.2014.03.011 | cdp-pmc-repository | 162,254 | 99% |
| 10.1016/j.immuni.2017.04.008 | cdp-pmc-repository | 120,637 | 99% |
| 10.1016/j.immuni.2019.03.025 | cdp-pmc-repository | 120,711 | 99% |
| 10.1016/j.jaci.2009.12.980 | cdp-pmc-repository | 132,875 | 99% |
| 10.1037/a0035302 | cdp-pmc-repository | 301,904 | 100% |
| 10.1038/s41392-021-00687-0 | europepmc-fullTextXML | 83,604 | 100% |
| 10.1038/s41422-020-0332-7 | europepmc-fullTextXML | 67,377 | 98% |
| 10.1038/s41423-020-0488-6 | europepmc-fullTextXML | 66,135 | 97% |
| 10.1038/s41577-022-00763-8 | europepmc-fullTextXML | 56,824 | 100% |
| 10.1111/j.1600-065x.2008.00737.x | cdp-landing-page | 41,933 | 98% |
| 10.1158/2159-8290.cd-19-0761 | cdp-landing-page | 87,390 | 99% |
| 10.4049/jimmunol.0802740 | cdp-pmc-repository | 62,058 | 98% |

> 「正文占比」= 去掉站点导航/搜索框等框架后，正文占全文的比例。
> 12 篇均在 **97–100%**，确认取到的是**真实综述正文**而非页面框架。

---

## 八、复现命令

```bash
cd 流程1

# 1) 审计：确认阶段 R 只有摘要级
python scripts/probe_review_fulltext.py     # 元数据复核
python scripts/probe_layer1_fulltext.py     # 第一层可达性

# 2) 策略问询（不联网；0=允许 / 3=禁止）
python scripts/fetch_direct.py --check-domain="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9361922/fullTextXML"
python scripts/fetch_direct.py --check-domain="https://www.nature.com/articles/s41577-022-00763-8"

# 3) 分层获取
python scripts/reviews_fulltext.py --layer1
python scripts/reviews_fulltext.py --layer2
uv run --with websocket-client --with pypdf python scripts/cdp_pool.py up --n=3
uv run --with websocket-client --with pypdf python scripts/download_all_fulltexts.py \
    --doi=<8 个 DOI，见 reviews_cdp_command.txt> --limit=8 \
    --delay=25-55 --site-fail-limit=2 --grace=35 --ports=9222,9223,9224 --n=3

# 4) 回填 + 重跑下游
python scripts/reviews_fulltext.py --backfill
uv run python scripts/reviews_build.py --topic "<方向>"
```

---

## 九、接入阶段 R 的位置

在 `SKILL.md` 阶段 R① 与 R2 之间插入本模块：

```text
阶段 R   检索        paginate.py（skill）
阶段 R①  任务包      reviews_prepare.py
阶段 R⓪.5 全文获取   reviews_fulltext.py --all --cdp --backfill   ← 本模块
阶段 R2  并行精读    subagent（任务包此时已有 text 字段）
阶段 R②  汇总        reviews_build.py
```

**回填为什么必要**：`reviews_prepare.py` 的任务包 schema 与
`interpret_prepare.py` **逐字段同名**，R2 的 subagent **只读 `tasks/*.json`**。
正文若不进任务包，subagent 依然只能看到摘要。
