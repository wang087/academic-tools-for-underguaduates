# 阶段 E：全文获取（含 §4.2 边下载边抽取）

> 本文件由主 `SKILL.md` 拆分而来（按链分组，懒加载）。
> **红线与全局契约遵守主 `SKILL.md`**（硬性要求 1–9、§5.1 分层获取、§5.2 鼠标政策、
> 闸门/数据契约），本文件不复制。进入本阶段才读本文件。

## 4.2 边下载边抽取（阶段 E 与 H/H2/R2 并行）

> **前台在取全文的同时，后台就把解读素材抽出来** —— 不要等整批 PDF 下完
> 才开始抽图、抽文本、组任务包。

### 为什么能并行

| 事实                                                                                             | 含义                                            |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| 下载的瓶颈是**网络等待**（导航 + 出版社响应），CPU/内存远没到顶                            | 抽取是本地 CPU 活，正好填满等待窗口             |
| `download_all_fulltexts.py` 已把**页内正文**顺带落盘到 `.work_cdp/fulltext/<slug>.txt` | 这一步**零额外请求**，且先于 PDF 尝试落盘 |
| 抽取产物与下载产物**以同一 slug 为键**                                                     | 增量、幂等，重复跑只跳过已完成的                |

### 三条产物，先到先用

```text
PDF 落盘
 ├─-> 按页分节文本 + 图注页索引   .work_cdp/md/<slug>.txt
 ├─-> 内嵌位图素材                .work_cdp/figures/<slug>.json
 └─-> 单篇解读任务包              .work_cdp/interpret/tasks/<NN>_<slug>.json
```

**三者互不依赖**：图还没抽完，文本已经可以组任务包；任务包也不必等全部图。

### 一条命令编排

```bash
# 增量跑一轮（幂等：已完成的自动跳过）
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py

# 只抽文本 / 只抽图
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py --no-figures
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py --no-text

# 抽完顺带组任务包
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py --then-prepare

# 只看进度
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py --status
```

| 退出码      | 含义                                         |
| ----------- | -------------------------------------------- |
| **0** | 本轮有进展，或全部篇目素材已就绪             |
| **3** | 还有篇目没抽取 —— 下载又完成几篇时再跑一轮 |
| **2** | 用法/环境错误（含**缺 pypdf**，见下）  |

### 编排节奏（与 §4.1 的轮询对齐）

```text
下载一批（--limit=N）
   → extract_parallel.py            # 抽这一批的素材
   → agent_poll.py --stage H        # 看解读卡回来几张
   → 按剩余量决定下一批派几个 / 等多久
   → 回到「下载一批」
```

**等待子代理的 30–60 秒窗口，就是跑抽取的窗口** —— 两边互不抢资源
（一边等网络，一边用 CPU）。

### [三个依赖，缺任何一个都会「静默地什么都不做」

| 依赖              | 谁需要它                                               | 缺了会怎样（**都是静默失败**）                                                                                                                                                                                                                                      |
| ----------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **pypdf**   | `store.validate_pdf`、`extract_figures.py`         | `validate_pdf` 把 `from pypdf import PdfReader` 包在 `except Exception` 里 → **对每个 PDF 都返回 `unparsed:ModuleNotFoundError`（判为无效）** → 本脚本跳过全部篇目、`download_all_fulltexts.py` 删掉刚下好的文件、`verify_pdfs.py` 报「全部文件损坏」 |
| **Pillow**  | `extract_figures.py`（`pypdf` 的 `page.images`） | `page.images` 抛 `ImportError: pillow is required`，被脚本内部 `except ImportError: pass` 吞掉 → **抽出 0 张图**，与「PDF 本来没图」无法区分                                                                                                                 |
| **PyMuPDF** | `prep_paper.py`                                      | 按页文本抽不出来（这条会显式报`需要 PyMuPDF`，不静默）                                                                                                                                                                                                                  |

**因此调用一律带齐三个：**

```bash
uv run --with pypdf --with pillow --with pymupdf python scripts/extract_parallel.py
```

`extract_parallel.py` 的两道防线：

1. **开头断言 pypdf 可用**；需 pypdf + pillow + pymupdf，缺失即退出 2（不让它表现成「所有 PDF 都残缺」）；
2. **日志区分三种结果**：真残缺（`too-small` / `not-pdf`）、校验器不可用
   （`unparsed:*`，报 ERROR）、抽到 0 张图（WARN 并提示调小 `--min-px-fig`）。

> **`--min-px-fig` 默认 60，不是 `extract_figures.py` 自身的 120。**
> 门槛过高会把**明明有图的 PDF 抽成空数组** —— 表现和「没有图」一模一样，静默漏图。


### 阶段 E：全文获取（**统一走 CDP** —— 实现在可选模块里）

> **取全文 CDP 模块（可选，可整目录删除）**
> 「用浏览器/出版商接口取回正文」这层被抽成 `optional-cdp-fulltext/`（合规原因见其 README）。
> 探测入口只有一个：
> ```bash
> uv run python scripts/cdp_module.py        # 0/3 = 可用/未安装；--path 给 shell 用
> ```
> **在** → 按 `optional-cdp-fulltext/SKILL.md` 操作；**不在** → 本层不可用，
> **静默改走备用链路**（白名单直连 + 元数据 API + nature-skill 获取模块），并在报告里如实标注 `cdp_module_absent`。

```
模块在    出版社/CDN/PMC 一律 CDP   → download_all_fulltexts.py   多实例 + 站点熔断
模块不在  出版商/浏览器路线不可用   → 备用链路：白名单直连 + 元数据 API + nature-skill 获取模块
取不到    摘要级降级               → 用摘要 + 关键词解读，并如实注明限制
```

> [STOP] **取消「OA 直链纯 HTTP」这一级**。
> 依 § 5.1 分层策略，**原文全文属第二层** —— **无论是否 OA，一律经 CDP 浏览器获取**
> （模块在时）；原 `fetch_oa_direct.py` 已停用。
> 操作细节（并发模型、`--manual-wait`、站点熔断、排错表）在
> `optional-cdp-fulltext/SKILL.md`。

> ### 并发模型由「多浏览器 × 单标签页」改为「多浏览器 × 多标签页」
>
> **为什么改**：旧设计并发度 = 浏览器进程数，想提速只能多开 Edge，
> 而每个进程约占 200–400 MB（开 6 个就吃掉 2 GB），启动与崩溃恢复都慢。
> 实测瓶颈在**网络等待**（导航 + 出版社响应），CPU/内存远没到顶，
> 所以改为每浏览器开 T 个标签页：并发通道 = `--n × --tabs`，
> 标签页共享进程池与网络栈，增开成本远低于新开浏览器。
>
> **[STOP] 并发上限 = min(通道数, 不同站点数)** —— 这是硬约束：
> 同站点必须串行（反封禁红线），所以**同一出版社的多篇论文，
> 加标签页不会提速**。多标签页只在语料**站点分散**时才带来吞吐收益。
> 并发通道 = `--n × --tabs`；峰值对照数据见
> [实测结果汇总.md](实测结果汇总.md#e-1)。
>
> **两条不变量**（改造已保持，勿破坏）：
> ① 同站点严格串行 —— 站点锁包住「节流 + 处理 + 失败记账」全程；
> ② 一个标签页只归一个线程 —— `open_tab()` 每次新开、`close_tab_self()` 用完即关。
>
> **回退开关**：`--no-multi-tab` 恢复旧行为（一浏览器一标签页），
> 用于排查多标签页相关问题。

> ### 下载的同时产出正文，供解读并行
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

| 参数                  | 默认                | 说明                                                                                                                                                                                       |
| --------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--n`               | 3                   | 并行**浏览器**数                                                                                                                                                                     |
| `--tabs`            | 3                   | **每浏览器标签页数**。并发通道 = `--n × --tabs`                                                                                                                                   |
| `--no-multi-tab`    | —                  | 回退到旧行为（一浏览器一标签页，`--tabs` 强制为 1）                                                                                                                                      |
| `--fulltext-cap`    | 400000              | 顺带取回正文的字符上限。0 = 不取                                                                                                                                                           |
| `--no-fulltext`     | —                  | 关闭正文落盘（等价`--fulltext-cap=0`）                                                                                                                                                   |
| `--limit`           | 12                  | 本轮篇数                                                                                                                                                                                   |
| `--delay`           | 25-55               | **同一站点**两篇之间的最小间隔（秒），全局串行                                                                                                                                       |
| `--site-fail-limit` | 2                   | 同一站点连续受阻几次后，本轮**跳过该站点**                                                                                                                                           |
| `--manual-wait`     | **0（关闭）** | [WARN]**本流程一律显式给正值**（如 `--manual-wait=5`，单位分钟）。为 0 时弹窗链完全不触发（[版本变动.md](版本变动.md) 故障 #1）。弹窗为 TTL 自动关闭 + 窗口置前，无人值守也不会残留 |
| `--grace`           | 25                  | 挑战宽限期（秒）：Cloudflare JS 挑战常在此窗口内**自动放行**，避免误报需人工                                                                                                         |
| `--doi=`            | —                  | 只处理指定 DOI（逗号分隔），便于精确补抓                                                                                                                                                   |
| `--status`          | —                  | 查看累计进度                                                                                                                                                                               |

> **判定结果里的 `navStatus`（真实 HTTP 状态）与 `botVendor`
> （cloudflare-turnstile / datadome / perimeterx / imperva …）会写入日志与 `fulltext_state.json`，
> 便于事后判断「是挑战、是限流、还是真封禁」。

**抓取节奏铁律（防触发风控）**

1. **全文一律走 CDP**（§ 5.1）——**不要**因为"这篇是 OA"就改用 HTTP 直取。
2. **同站点串行 + 25–55s 随机间隔**，模拟人类阅读节奏。
3. **同一站点连续 2 次出现验证/拒绝 → 本轮熔断该站点**，绝不反复试探。
4. 宁可少抓几篇，也不要触发 IP 封禁导致后续全部失败。

**出版社取全文对照表（「直连」一列已废止；核验时间见 [实测结果汇总.md](实测结果汇总.md#e-2)）**

| 出版社                       | CDP + Edge    | 取全文的可行方法（**全部经浏览器**）                                                                                                |
| ---------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Nature (nature.com)          | [OK]          | 落地页 fetch`citation_pdf_url`                                                                                                          |
| Springer (link.springer.com) | [OK]          | 落地页 fetch PDF                                                                                                                          |
| Wiley (onlinelibrary.*)      | [OK]          | [WARN] PDF 端点常回 HTML →**正文页 `Page.printToPDF` 渲染兜底**（有 `textlen > 15000` 门槛，短文体裁会因此取不到，属预期行为） |
| ACS (pubs.acs.org)           | [WARN] 多数可 | 落地页 fetch PDF（偶发验证页）                                                                                                            |
| Elsevier (sciencedirect.com) | [OK]          | 落地页 fetch；`/pdfft` 重定向签名直链后页内 fetch                                                                                       |
| RSC (pubs.rsc.org)           | [OK]          | 同 Elsevier                                                                                                                               |
| OUP (academic.oup.com)       | [OK]          | 落地页 fetch PDF                                                                                                                          |
| IUCr (journals.iucr.org)     | [OK]          | 落地页 fetch PDF                                                                                                                          |
| PNAS / JBC                   | [OK]          | 落地页 fetch PDF；PNAS 有 Cloudflare，需给足`--grace`                                                                                   |
| PMC / Europe PMC             | [OK]          | 文章页可开；`/pdf/` 端点常触发 reCAPTCHA（**转人工，勿自动化**）；OA 全文 XML 见 §3.1                                            |

> **取不到时的正当后路（按优先级）**：
> ① 放宽 `--grace` 让 JS 挑战自动放行；② `--manual-wait=N` 转人工；
> ③ OA 文章用 Europe PMC `fullTextXML` 取**正文文本**（`jats_to_text.py`）；
> ④ 机构 VPN / 图书馆文献传递；⑤ 在《全文清单.md》如实注明限制。
> **任何情况下都不回到"自编脚本直连"。**

**获取全文 PDF 的四种方法**（按可靠性排序，见 `save_pdf_via_browser.py`）：

1. **页面上下文 fetch**（最常用）：先导航到**落地页**（建立同源会话），再
   `fetch(pdfUrl, {credentials:'include'})` → `arrayBuffer` → base64 → 存盘。
   [WARN] 必须在落地页执行；在 PDF 查看器页执行会因 CORS 失败。
2. **Network 层捕获**：`Network.enable` + 导航到 PDF + `Network.getResponseBody`。
3. **重定向签名直链 fetch**（ScienceDirect 专用）：导航到 PDF 链接 → 浏览器跟随重定向到
   `pdf.sciencedirectassets.com/.../main.pdf?X-Amz-Security-Token=...` → 在**该页上下文** fetch `location.href`。
4. **渲染兜底**：PDF 端点被拦但有全文权限时，用 `Page.printToPDF` 把正文页转 PDF（Wiley 常用）。
   渲染前必须确认正文长度 > 15000，否则会产出 1 页空壳。

> **资产 CDN 验证页（Elsevier 专用，2026-11-09 新增）**：取 PDF 时若跳到
> `pdf.sciencedirectassets.com/craft/capi/cfts/init?...main.pdf`，页面显示
> `Request Verification: In Progress` + Cloudflare 复选框，则页内 fetch 只会得到
> 601B HTML。改用 `optional-cdp-fulltext/asset_pdf.py` 的
> `save_with_asset(cdp, hwnd, landing, pdf_url, out)`：
> **导航**到 PDF URL → 识别 `cfts`/`Request Verification` → 宿主机 `SendInput` 点复选框
> → `Network.getResponseBody` 抓页面随后请求的 PDF → 失败才渲染兜底（标 `render(asset-blocked)`）。
> 需要**交互式桌面**（`GetForegroundWindow() != 0`）；锁屏/RDP 断开时按键事件被拒。

> **先查机构授权**：若本机处于校园网/机构 IP 段，出版社页面会显示 **"Subscribed"**，
> 此时**无需登录凭据**即可读全文与下载 PDF。该授权是 **IP 级**的，CDP 启动的独立
> profile 同样享有，**不必复制用户 Cookie/密码库**。

#### 备选链路（主链路失败时启用）

> **主链路** = `download_all_fulltexts.py`（自家 CDP + 反爬 + 站点熔断）。
> **备选链路** = 外部 skill **`nature-downloader`** —— 一条**独立的实现**，
> 走开放获取 / 出版商 API / 机构 Web Access / CNKI 四类官方路线。
>
> **为什么要留这条**：主链路全押在「CDP 能开出页面」这一个前提上。
> 它失败时（浏览器起不来、某站点长期挑战、需要走机构订阅或中文库），
> 若没有第二条路，就只能如实报告「取不到」—— 而其实还有合法途径。

##### 先查前置

```bash
uv run python scripts/preflight_fallback.py
uv run python scripts/preflight_fallback.py --json   # 机读
```

| 退出码      | 含义                                                     |
| ----------- | -------------------------------------------------------- |
| **0** | 至少一条备选路线可用                                     |
| **3** | 备选链路当前不可用（缺前置）→**只能如实报告限制** |

该脚本**只读、不联网访问论文站点**，逐条报告四条路线是否可用：

| 备选路线                     | 需要                                           | 缺省状态           |
| ---------------------------- | ---------------------------------------------- | ------------------ |
| **开放获取**（零凭据） | Node.js ≥ 22                                  | 通常**可用** |
| 出版商 API                   | Node.js +`~/.config/lit-dl/credentials.json` | 需用户自行配置     |
| 机构 Web Access              | Node.js + 机构资源地址 + 已认证浏览器          | 需用户自行配置     |
| CNKI 中文文献                | 同上（+ 已认证 Chrome）                        | 需用户自行配置     |

> [WARN] `nature-downloader` 出厂是 **institution-neutral**（`schools.yaml` 故意留空）。
> 机构订阅与登录会话**只能由用户提供** —— 本流程**不代填、不代登录、不代持凭据**。

##### 触发条件（满足任一即转备选）

1. `download_all_fulltexts.py` 对同一篇**连续两轮**都失败；
2. 站点级熔断把该站点整轮跳过，而该站点是本轮主力；
3. 需要**中文文献（CNKI）**—— 主链路的 CDP 路径覆盖不到；
4. 需要走**机构订阅**而 CDP 的独立 profile 拿不到授权。

##### 转用方式

```bash
# ① 先查前置（缺失即如实报告，不要硬试）
uv run python scripts/preflight_fallback.py

# ② 开放获取路线（零凭据；把 DOI 列表给它，输出到交付目录旁）
node <nature-downloader>/scripts/batch_download.mjs \
     --dois "10.1038/xxx,10.1016/yyy" --out "<作者名>_paper_Exports_fallback" --no-si

# ③ 走出版商 API / 机构 / CNKI 时，先按该 skill 的说明配置，再同样调用
```

**拿到 PDF 后必须回到主流程的收尾**，不得绕过：

```bash
# 归位 + 命名 + 登记 + 校验（与主链路产物同一套契约）
uv run python scripts/rename_to_title.py
uv run --with pypdf python scripts/verify_pdfs.py
uv run --with pypdf python scripts/inventory_build.py
```

##### 五条硬约束

| # | 约束                                                                                       | 理由                                                                 |
| - | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| 1 | **备选链路只取「合法可得」的副本** —— 开放获取、出版商官方 API、用户自己的机构订阅 | 与 §5.1 分层策略同一立场；不绕过付费墙                              |
| 2 | **不得因为「走的是外部 skill」就放松 §5.1**                                         | 全文取回仍是第二层；备选链路只是**换了取数实现**，不是换了规则 |
| 3 | **凭据由用户提供，流程不代持**                                                       | 不写进任何产物、不进 journal、不进 git                               |
| 4 | **失败要如实报告，不得静默回退**                                                     | 哪条路线、哪一步失败、影响哪几篇，写进《全文清单.md》的未获取原因    |
| 5 | **产物必须过同一套校验**（`verify_pdfs.py` + `rename_to_title.py`）              | 否则会出现两套命名/两套登记，去重立即失效                            |

> **`--si` / `--no-si` 必选其一**：该 skill 要求显式声明要不要补充材料。
> 本流程默认 `--no-si`（正文优先），与 SKILL.md:2053「补充材料冒充正文」那条教训一致。
