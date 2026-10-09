# 网站全站爬取工具（crawl_site.py）

输入一个网址，自动遍历该站点的全部子页面，对 JS 渲染（SPA / 动态加载）页面自动改用无头浏览器渲染后再解析，
最终输出一份结构固定的 UTF-8 JSON 文件，供后续程序或人工检索调用。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `crawl_site.py` | 主程序：抓取 + 解析 + JS 渲染 + 输出 JSON |
| `crawl_query.py` | 结果工具：列表、检索、查看单个页面、查看警告、导出 Markdown 语料 |
| `tests/sample_site/` | 本地测试站点（静态页、动态注入页、robots.txt、sitemap.xml） |
| `tests/out_static.json` | 静态抓取示例输出 |
| `tests/out_js.json` | JS 渲染抓取示例输出 |

## 环境要求

- Python 3.8 及以上（开发与验证环境为 3.12），仅使用标准库，无需 `pip install`。
- 如需抓取 JS 渲染站点，本机需安装 Chrome / Chromium / Edge 之一（自动探测）；
  也可选用 Playwright（可选增强，见下文）。
- 无网络依赖的其他要求；不需数据库、不需 Node.js。

## 快速开始

```bash
# 1. 最基本用法：抓取整站，自动命名输出文件
python crawl_site.py https://example.com

# 2. 指定输出文件、页面上限与深度
python crawl_site.py https://example.com -o example.json --max-pages 300 --max-depth 3

# 3. 强制对每个页面都做 JS 渲染（适合纯前端渲染站点）
python crawl_site.py https://spa.example.com --render always --virtual-time-budget 10000

# 4. 只抓某个路径前缀，并排除若干栏目
python crawl_site.py https://shop.example.com --scope path --exclude "/cart" --exclude "/user"

# 5. 结合 sitemap.xml 提高覆盖率
python crawl_site.py https://example.com --use-sitemap

# 6. 查看结果
python crawl_query.py example.json list
python crawl_query.py example.json search "价格"
python crawl_query.py example.json warnings
python crawl_query.py example.json export-md ./md_out
```

运行结束后，标准输出会给出输出文件路径、页面数、失败数、跳过数与链接数量。

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `url` | 必填 | 起始网址，仅支持 http/https |
| `-o, --output` | `crawl_<host>_<时间>.json` | 输出 JSON 路径 |
| `--max-pages` | 200 | 最多抓取页面数，0 表示不限 |
| `--max-depth` | 4 | 链接深度上限，0 表示不限 |
| `--concurrency` | 5 | 静态抓取并发线程数 |
| `--delay` | 0.2 | 同一站点两次请求的最小间隔秒数 |
| `--timeout` / `--retries` | 20 / 2 | 单次请求超时与重试次数 |
| `--scope` | `host` | 抓取范围：`host` 同主机、`domain` 同主域、`path` 同路径前缀、`any` 任意域名 |
| `--include` / `--exclude` | 无 | 正则白名单 / 黑名单，可重复 |
| `--robots` | `soft` | robots.txt 处理方式：`soft` 禁止后仍抓取并写入警告、`strict` 严格遵守并跳过、`ignore` 不读取 robots.txt |
| `--ignore-robots` | 关闭 | 等同于 `--robots ignore`（保留兼容写法） |
| `--use-sitemap` | 关闭 | 额外读取 robots.txt 与 /sitemap.xml 中的链接作为种子 |
| `--allow-ext` | 无 | 允许跟进的扩展名（如 `--allow-ext .pdf`） |
| `--no-tables` | 关闭 | 不解析表格 |
| `--no-strip-boilerplate` | 关闭 | 不做模板行识别（默认识别站内公共导航/页脚并写入 `text_main`） |
| `--boilerplate-ratio` | 0.5 | 同一行出现在多少比例的页面中即视为模板行 |
| `--boilerplate-min-pages` | 5 | 页面数少于该值时不识别模板行 |
| `--dedupe-content` | 关闭 | 正文重复的页面不再展开其链接 |
| `--max-text-chars` | 200000 | 单页正文最大字符数，0 表示不截断 |
| `--render` | `auto` | JS 渲染策略：`auto` 自动判断 / `always` 总是渲染 / `never` 仅静态 HTML |
| `--render-engine` | `auto` | 渲染引擎：`auto` / `playwright` / `chrome` |
| `--virtual-time-budget` | 6000 | 无头浏览器等待脚本执行的毫秒数 |
| `--wait-selector` / `--scroll` | 无 / 关闭 | Playwright 渲染时等待的选择器、滚动触发懒加载 |
| `--browser-path` | 自动探测 | 指定 Chrome/Edge 可执行文件 |
| `--header` | 无 | 附加请求头，格式 `"Name: value"`，可重复 |
| `--insecure` | 关闭 | 跳过 HTTPS 证书校验 |
| `--indent` | 2 | JSON 缩进，0 表示压缩输出 |
| `-q` / `-v` | 常规 | 静默 / 详细日志（日志走 stderr，不污染 JSON） |

## JS 渲染机制

渲染引擎按以下顺序选择：

1. `--render-engine playwright` 或 `auto` 且已安装 Playwright：使用 Playwright 渲染，
   支持等待 `networkidle`、`--wait-selector`、`--scroll`。
   安装方式：`pip install playwright` 然后 `playwright install chromium`。
2. 否则使用本机 Chrome / Chromium / Edge，命令形如
   `chrome --headless=new --virtual-time-budget=6000 --dump-dom <url>`，
   拿到脚本执行完成后的 DOM 再解析。可用 `CHROME_PATH` 环境变量或 `--browser-path` 指定程序。
3. 两者都不可用时，`--render auto/always` 会给出告警并把页面按静态 HTML 处理，
   `render_reason` 字段会记录原因。

`--render auto` 的判定逻辑（`needs_render`）：

- 页面没有任何 `<script>`：不做渲染。
- 可见内容权重低于 `--render-min-text`（默认 30，中文按字、英文按词计）且含脚本：渲染。
- 命中单页应用特征（`__NEXT_DATA__`、`__NUXT__`、`data-reactroot`、`ng-version`、`id="root"`、
  `id="app"`、`v-app`、`window.__INITIAL_STATE__` 等）且内容偏少：渲染。
- 正文极少但脚本体积较大（>20KB），或声明了 AJAX 爬取协议（`<meta name="fragment">`）：渲染。

判定结果会写进每个页面的 `render_mode`（`static` / `browser-chrome` / `playwright`）与
`render_reason`，便于事后核查。

## robots.txt 处理：软限制（默认）

robots.txt 默认按**软限制**处理：被 `Disallow` 命中的地址**照常抓取**，但会在结果文件中留下警告，
不会静默忽略，也不影响 `Crawl-delay` 的限速。

| 模式 | 参数 | 行为 |
| --- | --- | --- |
| 软限制（默认） | `--robots soft` | 禁止的地址照常抓取；页面写入 `robots_allowed: false` 与 `warnings`，顶层 `warnings` 汇总每条命中的规则 |
| 严格遵守 | `--robots strict` | 禁止的地址不抓取，记入 `skipped`（`reason: "robots"`），不产生警告 |
| 完全忽略 | `--robots ignore`（或 `--ignore-robots`） | 不请求 robots.txt，`robots_allowed` 记为 `null`，不产生警告 |

软限制下的警告位置：

- 顶层 `warnings`：`{ "type": "robots", "url", "rule", "message" }`，最多 5000 条，
  超出时 `warnings_truncated` 为 `true`；`crawl.stats.robots_disallowed_pages` 给出总数。
- 页面内 `warnings`：命中 robots 的页面自带同样的警告文本，便于按页处理。
- 控制台：抓取过程中逐条打印，结束时给出汇总。

虽然软限制会抓取被禁止的地址，仍建议遵守目标站点的意愿：脚本保留 `--robots strict` 供合规场景使用，
并默认维持 `Crawl-delay` 与 `--delay` 限速。

## 模板行识别（text 与 text_main）

整站抓取时，导航、面包屑、页脚等公共内容会在每页正文里重复出现。抓取结束后程序会统计
每一行文本的出现比例，超过 `--boilerplate-ratio`（默认 0.5）的行判为模板行：

- `text`：完整正文，保留所有内容，不做删改。
- `text_main`：去掉模板行后的正文，适合直接阅读或送入后续处理；页面数少于
  `--boilerplate-min-pages`（默认 5）或使用 `--no-strip-boilerplate` 时为 `null`。
- `boilerplate_removed`：本页移除的行数，`crawl.stats.boilerplate_lines` 给出识别出的模板行总数。

例如北京大学生命科学学院师资页：每页正文原本以整段导航菜单开头，处理后 `text_main`
直接以教师姓名、邮箱、实验室与个人介绍开头。

## 输出 JSON 格式（schema_version 1.0）

顶层结构：

```
{
  "schema_version": "1.0",
  "generator":   { "name", "version", "python" },
  "crawl":       { "start_url", "started_at", "finished_at", "duration_seconds",
                   "options", "renderer", "stats" },
  "site":        { "host", "scheme", "title", "description", "pages" },
  "pages":       [ 页面对象, ... ],
  "link_graph":  { "edges": [...], "truncated", "external_urls": [...] },
  "redirects":   [ { "from", "to", "status", "page" } ],
  "robots":      { "url", "found", "crawl_delay", "disallowed": [...], "enforcement": "soft" },
  "warnings":    [ { "type": "robots", "url", "rule", "message" } ],
  "warnings_truncated": false,
  "skipped":     [ { "url", "reason" } ],
  "errors":      [ { "url", "stage", "message", "status" } ]
}
```

`crawl.stats` 字段：`pages_crawled`、`pages_failed`、`pages_skipped`、`rendered_pages`、
`duplicate_pages`、`robots_disallowed_pages`、`urls_discovered`、`edges`、`total_bytes`、
`boilerplate_lines`。

`skipped[].reason` 取值：`robots`（strict 模式下被 robots.txt 禁止）、`scope`（超出抓取范围）、
`extension`（二进制或资源文件）、`pattern`（被 exclude 命中）、`include`（未命中 include）。

页面对象字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 页面编号，形如 `p0001`，与 `link_graph.edges.from` 对应 |
| `url` | string | 规范化后的抓取地址（去锚点、去追踪参数、去尾部斜杠） |
| `final_url` | string | 跟随重定向后的最终地址 |
| `depth` | int | 距起始页的链接深度，起始页为 0 |
| `discovered_from` | string/null | 发现该页面的上级页面 URL |
| `status` | int | HTTP 状态码 |
| `ok` | bool | 是否成功（2xx/3xx 且无异常） |
| `content_type` / `encoding` | string | 响应类型与判定出的字符集 |
| `fetched_at` / `elapsed_ms` | string / int | 抓取时间与耗时 |
| `render_mode` | string | `static` / `browser-chrome` / `playwright` |
| `render_reason` | string/null | 触发渲染的原因或渲染失败原因 |
| `byte_size` | int | 原始响应字节数 |
| `title` / `lang` | string/null | 标题与 `<html lang>` |
| `canonical` | string/null | `<link rel="canonical">` 绝对化结果 |
| `meta` / `og` / `twitter` / `other_meta` | object | 各类元信息 |
| `alternates` / `feeds` / `icon` | array / array / string | 多语言版本、RSS 订阅、站点图标 |
| `headings` | array | `{ "level": 1..6, "text": "..." }` 按出现顺序 |
| `text` | string | 正文纯文本，块级元素换行、行内元素连写、脚本样式已剔除 |
| `text_main` | string/null | 去掉站内公共模板行后的正文，适合直接使用；未启用模板识别时为 null |
| `text_main_length` | int/null | `text_main` 的字符数 |
| `boilerplate_removed` | int | 本页被识别为模板行而移除的行数 |
| `robots_allowed` | bool/null | 该地址是否被 robots.txt 允许；`ignore` 模式下为 null（未知） |
| `warnings` | array | 本页警告文本，软限制下被 robots 禁止的页面会带一条说明 |
| `text_length` / `word_count` | int | 文本长度与词数（中文按字数计入） |
| `text_truncated` | bool | 是否被 `--max-text-chars` 截断 |
| `content_sha256` | string | 正文哈希，用于判断重复内容 |
| `duplicate_of` | string/null | 若正文与已抓页面相同，这里给出首个页面的 URL |
| `links` | array | `{ url, text, rel, internal, target }`，站内站外均记录，按 URL 去重 |
| `images` | array | `{ src, alt, title, width, height }`，兼容 `data-src` 等懒加载属性 |
| `tables` | array | `{ caption, rows: [[单元格...]], truncated }` |
| `json_ld` | array | 解析后的 JSON-LD 结构化数据 |
| `scripts` | object | `{ count, external: [...], truncated }` |
| `stylesheet_count` | int | 样式表数量 |
| `child_urls` | array | 本页可继续抓取的站内链接（已去重、已排除自身） |
| `error` | string/null | 错误说明 |

精简示例（字段有删减）：

```json
{
  "schema_version": "1.0",
  "crawl": { "start_url": "https://example.com", "renderer": "browser-chrome",
             "stats": { "pages_crawled": 12, "rendered_pages": 3 } },
  "site": { "host": "example.com", "title": "示例站点", "pages": 12 },
  "pages": [
    {
      "id": "p0001",
      "url": "https://example.com",
      "status": 200,
      "depth": 0,
      "render_mode": "static",
      "title": "示例站点首页",
      "lang": "zh-CN",
      "meta": { "description": "示例描述" },
      "headings": [{ "level": 1, "text": "示例站点首页" }],
      "text": "示例站点首页\n这是正文内容。",
      "text_length": 24,
      "word_count": 12,
      "links": [{ "url": "https://example.com/about", "text": "关于我们",
                  "rel": [], "internal": true, "target": null }],
      "images": [{ "src": "https://example.com/a.jpg", "alt": "示意图",
                   "title": null, "width": "640", "height": "480" }],
      "child_urls": ["https://example.com/about"]
    }
  ],
  "link_graph": { "edges": [{ "from": "p0001", "to": "https://example.com/about",
                              "text": "关于我们" }], "external_urls": [] },
  "errors": [], "skipped": [], "redirects": []
}
```

## 后续调用示例

Python：

```python
import json

with open("example.json", encoding="utf-8") as f:
    doc = json.load(f)

# 站点概况
print(doc["site"]["title"], doc["crawl"]["stats"]["pages_crawled"])

# 遍历页面：标题 + 正文
for page in doc["pages"]:
    if page["ok"]:
        print(page["id"], page["title"], len(page["text"]))

# 建索引：URL -> 正文
pages_by_url = {p["url"]: p for p in doc["pages"]}
print(pages_by_url["https://example.com/about"]["text"])

# 按关键词检索
hits = [p["url"] for p in doc["pages"] if "价格" in p["text"]]
```

也可以直接复用工具里的 API：

```python
from crawl_query import load_document, iter_pages, search_text, export_markdown

doc = load_document("example.json")
print(search_text(doc, "价格", limit=5))
print(export_markdown(doc, "./md_out"))
```

Node.js：

```js
const doc = require("./example.json");
const titles = doc.pages.map((p) => p.title);
const aboutText = doc.pages.find((p) => p.url.endsWith("/about"))?.text ?? "";
```

命令行（jq）：

```bash
jq -r '.pages[] | "\(.id)\t\(.status)\t\(.title)"' example.json
jq -r '.pages[] | select(.render_mode != "static") | .url' example.json
jq -r '.errors[] | "\(.stage)\t\(.url)\t\(.message)"' example.json
```

## 抓取策略与合规

- robots.txt 默认按**软限制**处理：`Disallow` 命中的地址照常抓取，但会在 `warnings`
  与页面 `robots_allowed` 中留下痕迹；`Crawl-delay`（兼容小数形式）与 `--delay` 仍然生效。
  需要严格遵守时使用 `--robots strict`，此时被禁止的地址记入 `skipped` 且不抓取。
- 默认同主机限定，仅抓取网页类内容并跳过二进制与静态资源。
- 由于默认会抓取被 robots.txt 禁止的地址，请确认你有权抓取目标站点，并遵守其服务条款、
  版权与个人信息保护要求；合规场景请改用 `--robots strict`。
- 对返回 429/5xx 的请求会自动退避重试；并发过高可能触发风控，建议 `--concurrency` 不超过 5。

## 常见问题

- 抓到 0 个页面：检查网址是否正确、网络是否可达、`--include/--exclude` 是否过严；
  `-v` 可看到每个页面的状态与错误。默认软限制下 robots.txt 不会导致 0 页。
- 页面正文为空：该页可能是纯前端渲染。用 `--render always` 或提高 `--virtual-time-budget`；
  若提示未找到浏览器，请安装 Chrome/Edge 或 Playwright。
- 页面内容不完整（懒加载）：使用 Playwright 引擎并加 `--scroll`，或用 `--wait-selector` 等特定元素。
- 想抓 PDF/图片等资源：用 `--allow-ext .pdf` 允许跟进，此类页面不会解析正文。
- 站点同时存在 http 与 https：两者按不同 URL 处理，正文相同者会在 `duplicate_of` 中标注；
  加 `--dedupe-content` 可避免重复展开。
- 输出文件较大：用 `--max-text-chars`、`--no-tables` 或 `--indent 0` 压缩体积。

## 已验证场景

| 场景 | 命令要点 | 结果 |
| --- | --- | --- |
| 本地静态站点（含 robots.txt / sitemap / 表格 / JSON-LD / 懒加载图） | `--render never --use-sitemap --max-pages 20` | 6 页（另 1 个 404 计为失败），robots 拦截与扩展名跳过各 1 条，JSON-LD、表格、懒加载图均正确解析 |
| 本地站点 robots 软限制（默认） | 默认参数 | 被 `Disallow: /private.html` 禁止的页面照常抓取，页面与顶层 `warnings` 各留 1 条警告，`skipped` 中不再出现该地址 |
| 本地站点 robots 严格模式 | `--robots strict` | 同一地址被跳过，`skipped[].reason = "robots"`，无警告 |
| 本地站点 robots 忽略模式 | `--ignore-robots` | 不请求 robots.txt，`robots_allowed` 为 null |
| 本地动态注入页面 | `--render auto` | 自动识别并渲染，脚本注入的标题、正文与链接全部入库 |
| 真实静态站点 `books.toscrape.com` | `--render never --max-pages 12` | 12 页 0 失败，839 条链接关系 |
| 真实 JS 站点 `quotes.toscrape.com/js/` | `--render auto --max-pages 6` | 6 页 0 失败，其中 2 页由无头浏览器渲染，成功抓到脚本生成的名言与分页链接 |
| 北京大学**生命科学学院**“师资力量”栏目 | 见下方示例命令 | 249 页 0 失败（4 个栏目页 + 245 位教师详情页），识别 73 行模板内容，`text_main` 直接以教师姓名与个人信息开头 |

“师资力量”栏目的实际命令（限定到该栏目的三个子栏目与其详情页，结果约 249 页）：

```bash
python crawl_site.py "https://www.bio.pku.edu.cn/homes/Index/page/3.html" \
  -o pku_bio_faculty.json \
  --scope host \
  --include "news_szll_zy/16/|news_zzjs/17/|news_txjg/18/|news_cont_jl/(?:16|17|18)/|/page/3\.html" \
  --max-pages 400 --max-depth 3 --concurrency 5 --delay 0.3
```

其中详情页按栏目区分：`news_cont_jl/16/*` 为博士生导师、`/17/*` 为全体教职工、`/18/*` 为离退休教职工；
同一教师同时出现在“博士生导师”与“全体教职工”两处时，`duplicate_of` 字段会标注重复关系。
