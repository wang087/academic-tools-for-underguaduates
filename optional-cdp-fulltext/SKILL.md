# 可选模块 · 取全文 CDP（操作手册）

> 本手册原先是主 `SKILL.md` 的阶段 E / R①.5 与「全文获取」章节的一部分。
> 为能交付**不含出版商正文自动化**的发行版，这部分连同实现一起抽到这里
> （合规依据与移除方式见本目录 `README.md`）。
>
> **模块缺失时不要照本手册操作** —— 先跑探测：
> `uv run python 流程1/scripts/cdp_module.py`（返回 3 = 未安装）。

## 一、入口与探测

```bash
uv run python 流程1/scripts/cdp_module.py            # 状态 + 本模块提供的脚本
uv run python 流程1/scripts/cdp_module.py --path     # 模块目录（未安装则空）
uv run python 流程1/scripts/cdp_module.py --status   # 0=可用 3=未安装
```

主 `SKILL.md` 只保留这一条探测入口；阶段 E / R①.5 在探测为"未安装"时
**静默改走备用链路**（不报错、不中断），并在报告与 `reviews_cdp_command.txt`
里写明原因（`cdp_module_absent`）。

## 二、阶段 E：原文全文获取（统一走 CDP）

```
统一路径  出版社/CDN/PMC 一律 CDP   → download_all_fulltexts.py   多实例 + 站点熔断
取不到    摘要级降级               → 用摘要 + 关键词解读，并如实注明限制
```

> [STOP] **取消「OA 直链纯 HTTP」这一级**：依 §5.1 分层策略，**原文全文属第二层** ——
> **无论是否 OA，一律经 CDP 浏览器获取**。原 `fetch_oa_direct.py` 已停用。

```bash
uv run --with websocket-client --with pypdf python \
    流程1/optional-cdp-fulltext/download_all_fulltexts.py \
    --n=3 --tabs=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35
```

- 并发模型：**多浏览器 × 多标签页**（`--n × --tabs`）。旧设计并发度=浏览器进程数，
  每个进程 200–400 MB，实测瓶颈在网络等待而非 CPU/内存。
- `--manual-wait` 必须 > 0：人机验证弹窗链依赖它（默认关闭会静默失去转人工能力）。
- 逐站点熔断（`SiteGuard`）与跨轮节流（`fetch_pacing`）都在核心模块里，
  本模块直接复用。

## 三、阶段 R①.5：综述全文（skill 先试 / CDP 兜底）

`reviews_fulltext.py`（核心）先走第一层（PMC / 白名单），把缺口写成
`reviews_cdp_command.txt` 交给本模块下载器；模块缺失时该文件改写成
"改走备用链路"的三行说明，流程继续。

```bash
uv run python 流程1/scripts/reviews_fulltext.py --layer1 --layer2 --cdp --fallback
#   --cdp 在模块缺失时打印 [INFO] 并返回 3（跳过，非失败）
```

## 四、取正文之外的诊断

| 脚本 | 用途 |
|---|---|
| `check_challenge.py` | 站点体检（人机验证 / 封禁 / 可达性），返回明确退出码 |
| `check_access.py` | 检测本机是否具备 IP 级机构订阅授权 |
| `save_pdf_via_browser.py` | 单篇：把当前页面另存为 PDF（4 种方法依次尝试） |
| `cdp_up_nosandbox.py` | 无沙箱启动器（本机 Edge renderer 沙箱起不来时的办法） |

## 五、排错（原主手册的 CDP 行，随手册一起抽走）

| 现象 | 报错 | 处置 |
|---|---|---|
| CDP WebSocket 403 | `Handshake status 403 Forbidden` | 启动加 `--remote-allow-origins=*`；确保旧实例已被杀 |
| Edge 进程退出 | `ConnectionRefusedError 10061` | **多实例池** + 逐篇 TCP 探活 + 自动重启；不要单实例跑长批 |
| 脚本「死机」 | 无输出、长时间不动 | 所有 CDP 调用带**绝对截止时间**；不再有 `while True: recv()`；`wait_for_manual` 默认关闭 |
| 误触发 CAPTCHA（形态一） | 短页面/勘误页被当成验证页，弹窗空等 15 分钟 | 用 `detect.judge()` 精确判定，**绝不用文本长度当判据** |
| 误触发 CAPTCHA（形态二） | Cloudflare 的 JS 挑战会**自动放行**，但一见 `just a moment` 就放弃 | `settle_challenge()` 宽限期（默认 25s，`--grace=` 可调） |
| 泛词误报封禁 | 正文含 `forbidden` / `blocked by` → 全局中止 | 封禁用精确短语；模糊词仅「短页面开头」才算数 |
| 杀进程失败 | `Invalid argument/option - 'C:/Program Files/Git/PID'` | Git Bash 路径转换所致，用 `MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F` |
| 浏览器找不到 | `FileNotFoundError: 未找到 Chrome / Chromium / Edge` | 设 `PAPER_BROWSER=<绝对路径>`（候选见 `cdp_pool.py` / `render_html_pdf.py`） |

## 六、自检

```bash
uv run python 流程1/optional-cdp-fulltext/fulltext_publishers.py --self-test
uv run python 流程1/optional-cdp-fulltext/download_all_fulltexts.py --self-test   # 如提供
uv run python 流程1/scripts/selftest_fulltext_modules.py     # 聚合（本模块缺席时标 [SKIP]）
```