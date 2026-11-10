# 故障排查

## 1. 命中数为 `null`

**含义**：页面没有解析到计数，不等于 0。

排查顺序：

1. `python lq.py web probe --engine scopus --query "<query>" --outdir snapshots/scopus`
2. 看 `snapshots/scopus/scopus.txt` 里是否有 `documents found`。
3. 有 → 正则需要补；在 `web.count_from_text` 增加模式，并加测试。
4. 没有 → 页面可能是登录墙/验证码/空白。看 `title` 与 URL。
5. 仍无 → 用 `--source scopus-api` 或等人工通过。

[PROHIBIT] 不要把 `null` 写进报告说“0 篇”。

## 2. 命中数为 0，但预期有结果

| 可能原因 | 检查 |
|---|---|
| 字段码写错 | `python lq.py query lint --engine scopus --file q.txt` |
| 概念块被 `AND` 过死 | 看 `queries.json` 的 `blocks`，是否 3 个以上 `AND` |
| 排除词误伤 | 暂时去掉 `exclude` 重跑 |
| 年份/类型/语言过滤 | `--no-filters` 试一次 |
| 短语过精确 | `--phrase-mode proximity` |
| 该库确实不收录 | 换 WoS/OpenAlex |

## 3. 登录墙

- Scopus：URL 出现 `/signin`；未登录通常仍可读，若被强制跳转，按 `browser-cdp.md` 第 6 节登录。
- WoS：未登录只能读计数；记录为空不是 0。
- 处理：`browser start` → 人工登录 → 重跑同一命令。不要清空 profile（会丢会话）。

## 4. WoS 记录为空

现象：`count` 很大，`records=[]`，`records_meta.note` 提示未登录。

处置：

```bash
python lq.py search run --plan topic.plan.json --source wos --fallback openalex --out run.json
```

或设置 `WOS_API_KEY` 改 `--source wos-api`。

## 5. 验证码 / Cloudflare

现象：`state=challenge`，标题 `Just a moment...`。

处置：

1. 停止该查询，不再刷新。
2. 保存快照。
3. 人工在浏览器里通过验证。
4. 重跑；若连续出现，改用 API 或 OpenAlex。

## 6. 浏览器启动失败

| 报错 | 处置 |
|---|---|
| `mojo ... Access is denied` | 浏览器被沙箱限制；在普通终端运行 `python lq.py browser start`，或让 agent 用非受限方式启动 |
| `did not expose CDP within 40s` | `--port 9223` 换端口；检查企业策略 |
| `WebSocket handshake failed` | 确认启动参数含 `--remote-allow-origins=*` |
| `Edge/Chrome not found` | 设置 `LQ_BROWSER` 为可执行文件路径 |

## 7. API 错误

| 状态 | 含义 | 处置 |
|---|---|---|
| 401 | key 无效/无权限 | 检查 `SCOPUS_API_KEY` / `WOS_API_KEY` 与机构订阅 |
| 429 | 限流 | 等待；降低 `--max-records`；不要并发 |
| 400 | 查询语法或参数 | 用 `query lint`；确认 WoS 用 `TS=`、Scopus 用 `TITLE-ABS-KEY(...)` |
| 404 | DOI 不存在 | 换标题检索或标记未取得 |

## 8. 摘要缺失

顺序：

1. `reviews fetch` 已尝试 OpenAlex。
2. 手动检查 `runtime.scopus_api.abstract(doi)`（需 key）。
3. 打开 DOI 页，看是否有 `Abstract` 区块。
4. 仍无 → 报告 `[WARN] 未取得摘要`，不要根据标题编写。

## 9. 全文缺失

`fulltext.status`：

| 状态 | 含义 | 处置 |
|---|---|---|
| `pdf` / `html` | 成功 | 检查 `chars` 是否过小 |
| `pdf_no_text` | PDF 是扫描件 | 需要 OCR，报告说明 |
| `paywalled` | 无 OA 副本 | 用机构权限下载，保留 DOI |
| `browser_failed` | 浏览器路线失败 | 看 `error`；确认登录与网络 |
| `http_403` | 出版商拒绝 | 不要循环重试，改人工 |

## 10. 期刊未知

`journal_rank.matched=false`。处置：

```bash
python lq.py journals import --csv jcr.csv --format jcr --out journal_tiers.json
python lq.py journals lookup --journal "<刊名>" --table journal_tiers.json
```

仍未知 → `--rank-policy balanced`，并在报告写明。

## 11. 命中 < 15

`search.run.json` 的 `status=insufficient`。报告必须列出每一步命中数。可尝试：

- 增加同义词/缩写/英美拼写；
- 精确短语改邻近；
- 去掉一个非核心概念块；
- 年份窗口放宽；
- 换库；
- 仍不足 → 向用户报告，不要硬凑。

## 12. DOI 匹配错

`enrich_records` 用标题相似度 ≥ 0.9 且年份 ±1 才接受 OpenAlex 的标题匹配。若仍错：

- 检查 `doi_source` 字段：`openalex-title-match` 是推断，`scopus-record-page` 是页面直读。
- 对关键文献，用 `reviews fetch` 打开记录页核对 DOI。
- 报告中标注 `doi_source`。

## 13. 重复记录

`records.dedupe` 以 DOI 优先、规范化标题兜底。重复常见于：

- 同一文不同 DOI（预印本/正式版）；
- 标题大小写/标点差异；
- Scopus 与 WoS 同一篇。

处置：看 `dedupe` 后的 `id`，必要时人工合并；报告只保留一条并注明来源。

## 14. 中文乱码

- 运行环境：所有脚本入口调用 `setup_stdout()` 强制 UTF-8。
- PowerShell：读文件用 `-Encoding UTF8`；写 `.ps1` 用 UTF-8 with BOM。
- 重定向：`python ... 2> err.log`，再用 `Get-Content -Encoding UTF8` 查看。

## 15. 缓存导致结果不变

`count_cache.json` 以 `engine + sha256(query)` 为键。检索式没变时命中数不会重查。

- 需要重查：加 `--refresh`。
- 改了检索式：SHA 变了，会自动重查。
- 想彻底清空：删除 `.work_lq/count_cache.json`（不要删整个 `.work_lq`，会丢浏览器 profile）。

## 16. digest-check 不通过

| 报错 | 修正 |
|---|---|
| `translation missing or too short` | 补完整翻译（≥60 字符） |
| `need >=2 bullets and >=100 chars` | 补解读 |
| `need >=2 sub-directions per review` | 每篇至少 2 条，且带 evidence |
| `unresolved placeholder text` | 删除 TODO/待补充 |
| `emoji found` | 用 `[OK]`/`[WARN]` 替代 |
