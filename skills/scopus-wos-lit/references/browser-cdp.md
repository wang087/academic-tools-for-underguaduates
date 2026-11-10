# 浏览器与 CDP

本项目用**真实 Edge/Chrome 窗口 + 持久化 profile**访问 Scopus/WoS。原因：两家都是登录态/机构 IP/反爬敏感站点，纯 HTTP 抓取既不稳定也不礼貌；而 CDP 能复用用户已登录的会话。

## 1. 已验证事实（2026-10，Edge 156，未登录机构账号）

### Scopus

| 项 | 结果 |
|---|---|
| 入口 | `https://www.scopus.com/pages/home#basic` |
| 检索 URL | `https://www.scopus.com/results/results.uri?src=s&sot=b&sdt=b&s=<query>&origin=searchbasic&limit=10` |
| 命中数文案 | `93,652 documents found`（`count_from_text` 同时兼容 `document results`） |
| 结果行 | `<tr>` 内含 `a[href*="/pages/publications/"]`；单元格依次为：序号、标题、作者、来源、年份、被引 |
| 每页条数 | 固定 10 条；URL 里的 `limit=25` 会被站点改回 `limit=10` |
| 翻页 | 页面上的 `button`，文字为 `Next`（无 `aria-label`/`data-testid`）；点击后原地换页，不重载 |
| 排序 | `sort=cp-f` 被引最高、`plf-f` 最新、`r-f` 相关度 |
| 记录页 | `https://www.scopus.com/pages/publications/<id>`；未登录可见 `DOI:`、摘要、作者、被引 |
| 摘要 | 结果页 “Show abstract” 可展开；记录页直接显示 Abstract |
| 限制 | 未登录时结果导航常见 “of 2000 results” 上限；这是界面上限，不是库内总量 |

### Web of Science

| 项 | 结果 |
|---|---|
| 入口 | `https://www.webofscience.com/wos/woscc/advanced-search`，会跳转到 `https://webofscience.clarivate.cn/...` |
| 查询框 | `#advancedSearchInputArea`（textarea） |
| 执行按钮 | `[data-ta="run-search"]`（**不是**文字为 “Search” 的按钮） |
| 结果页 | URL 含 `/wos/woscc/summary/...` |
| 命中数文案 | `17,142 results from Web of Science Core Collection for:` |
| 记录 | 未登录/无权限时 `app-record` 为空占位符，**只能读命中数** |

> 结论：Scopus 网页路线可完成“命中数 + 结果行 + DOI + 摘要”；WoS 网页路线在未登录时只保证命中数。需要 WoS 记录时用 `--source wos-api` 或 `--fallback openalex`。

## 2. 启动参数

```text
--remote-debugging-port=<port>
--remote-allow-origins=*
--user-data-dir=<WORK>/.work_lq/browser_profile_<port>
--disable-extensions
--no-first-run --no-default-browser-check
--deny-permission-prompts --disable-notifications
--noerrdialogs
--disable-session-crashed-bubble --hide-crash-restore-bubble
--disable-prompt-on-repost
--disable-search-engine-choice-screen
--disable-features=Translate,TranslateUI
--window-size=1366,900
--user-agent=<Edge UA>
```

- `--disable-extensions`：满足“关闭所有扩展”的要求。
- 同意书弹窗没有浏览器开关；由注入脚本只处理已知 CMP 容器（OneTrust/Cookiebot/Didomi/Usercentrics 等），不做全局按钮扫描。
- 不启用 headless：无头模式更容易被识别。
- 不注入 `navigator.webdriver` 等指纹伪装；只做正常用户会做的动作。

## 3. UA

优先：

```text
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0
```

备用：

```text
Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/138.0.0.0 Mobile Safari/537.36
```

同时通过启动参数与 `Network.setUserAgentOverride` 设置，避免页面内 JS 看到不一致的 UA。

## 4. 生命周期：只启动一次

```bash
python lq.py browser start          # 启动并保留
python lq.py browser status         # 是否存活
python lq.py browser stop           # 任务全部结束后再关
```

- 默认端口 `9222`；同一端口已有实例时 `Browser.ensure()` 直接复用，不会重复启动。
- 每个 CLI 命令结束只关闭 WebSocket，**不关闭浏览器**。
- 所有命令共用一个页面标签；Scopus 与 WoS 在同一标签内切换，profile 保留两家 Cookie。
- 禁止在循环里 `browser stop/start`，也禁止对同一 URL 反复 `navigate` 重试。

## 5. 导航节流

- `Runtime.min_nav_interval` 默认 5 秒，`--min-nav-interval` 可调大。
- 翻页是必要导航；同一查询的参数不变时直接读缓存命中数（`.work_lq/count_cache.json`）。
- 命中数缓存键 = `engine + sha256(query)`；`--refresh` 才重新读页面。

## 6. 登录

1. `python lq.py browser start`（会弹出可见窗口）。
2. 用户在窗口里完成机构登录/SSO。
3. 重跑同一命令；profile 保留会话，无需重复登录。
4. 若机构要求 VPN/校园网，先连网再启动。

**登录重定向的自动恢复（2026-10-22 实测）**：Scopus 有时会对直接构造的 `results.uri` 请求强制 SSO
（跳转 `id.elsevier.com/as/authorization.oauth2?...&prompt=login`），但同一会话打开主页仍保留游客检索框。
`ScopusWeb.open_query` 现在会先做一次**从主页发起**的恢复：打开 `https://www.scopus.com/pages/home#basic`，
定位搜索框（`input#autosuggest-...-input`）→ 输入查询 → 点 `Search` → 读取结果页。
实测该路径返回了 `9,261 documents found` 与结果行；只有主页也跳登录时才报 `login`。
每次查询最多恢复一次，不循环刷新。

登录状态检测：URL 含 `id.elsevier.com`、`authorization.oauth2`、`prompt=login`、`/signin`、`/login`、`shibboleth`
判为登录墙；Scopus 未登录仍可用（主页路线），WoS 未登录只读计数。

## 7. 反爬与验证码

- 命中 `Just a moment`、`Verify you are human`、`captcha`、`cf-challenge` 时，`PageState.state="challenge"`。
- 处置：立即停止该查询 → `web probe --outdir` 保存 HTML/文本快照 → 报告用户 → 等人工通过或换 API。**不要刷新循环。**
- 连续两次 challenge：本次任务改用 `--source scopus-api` / `wos-api`，或只做 OpenAlex。

## 8. 选择器覆盖

默认选择器在 `web.py::DEFAULT_SELECTORS`。站点改版时**不要改代码**，在 `<WORK>/selectors.json` 覆盖：

```json
{
  "scopus": {
    "search_inputs": ["input[data-testid='search-input']", "#searchterm"],
    "search_buttons": ["button[data-testid='search-button']"]
  },
  "wos": {
    "query_input": "#advancedSearchInputArea",
    "run_button": "[data-ta='run-search']"
  }
}
```

## 9. 快照诊断

```bash
python lq.py web probe --engine scopus --query "TITLE-ABS-KEY(microbiome)" --outdir snapshots/scopus
```

产物：`scopus.html`、`scopus.txt`、`scopus.json`（URL/标题/大小）。只做**一次**导航，用于确认页面状态与选择器。

## 10. 沙箱与故障

| 现象 | 原因 | 处置 |
|---|---|---|
| `mojo ... Check failed: Access is denied` | 浏览器进程被沙箱禁止创建命名管道 | 在普通终端/非受限方式运行 `browser start`；不要把失败当成网站拒绝 |
| `browser did not expose CDP within 40s` | 端口被占用、Edge 启动失败、策略禁用远程调试 | 换端口 `--port 9223`；检查企业策略 `RemoteDebuggingAllowed` |
| WebSocket 403 | 缺少 `--remote-allow-origins=*` | 用本项目启动器 |
| 页面空白 | 需要登录/验证码/JS 未完成 | 快照后人工检查 |

## 11. 访问边界

- 按任务要求，本项目**不读取 robots.txt**，也不因 robots 禁止而跳过页面；这只适用于用户本人有访问权的页面。
- 优先用官方 API（Scopus/WoS）与开放获取接口；浏览器只用于登录态页面、动态渲染与反爬场景。
- 保持低频、可停止：单实例、单标签、默认 5 秒导航间隔；遇到验证码立即停，不循环重试。
- 不绕过付费墙：无 OA 副本时保留 DOI，交由用户通过机构权限获取。

## 12. 安全

- profile 内含登录 Cookie，**不要提交到 git、不要打包分发**；`.gitignore` 已排除 `.work_lq/`。
- 不要把 Cookie 写入报告或日志。
- 下载的全文仅用于用户自己的研究，遵守出版商许可。
