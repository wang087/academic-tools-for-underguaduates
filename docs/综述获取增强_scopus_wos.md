# 综述获取增强：接入 Scopus / Web of Science（可选）

本页描述如何**按需**用 `skills/scopus-wos-lit` 增强阶段 R 的综述获取能力。默认流程不变；不调用本页命令时，阶段 R 仍走原有的 Europe PMC / arXiv / OpenAlex 路线。

## 1. 它解决什么问题

原有阶段 R 的库路由以 Europe PMC / arXiv / OpenAlex 为主，**没有 Scopus 与 Web of Science**。当用户要求：

- 按 Scopus 收录或 WoS 收录取综述；
- 用 JCR / 中科院分区做“前 20%”筛选；
- 用 Scopus 的 `TITLE-ABS-KEY` 语法做精确综述检索；
- 需要一作/通讯作者、DOI、摘要的完整题录；

可以调用本增强。它把结果写成阶段 R 认识的 `reviews.json`，后续 `reviews_prepare.py` → R2 解读 → `reviews_build.py` 完全复用。

## 2. 目录与依赖

| 项 | 说明 |
|---|---|
| 技能副本 | `流程1/skills/scopus-wos-lit/`（自包含；含 `SKILL.md`、脚本、词表、测试） |
| 适配脚本 | `流程1/scripts/reviews_discover_scopus.py` |
| 输出 | `.work_cdp/reviews/reviews_scopus.json`（`{"records":[...]}`） |
| 依赖 | Python 3.9+；Scopus/WoS 网页路线需要 Edge/Chrome 与 CDP；OpenAlex 路线零凭据 |
| 可选 | `SCOPUS_API_KEY`、`WOS_API_KEY`（走官方 API） |

## 3. 快速使用

```bash
# ① 发现综述（Scopus；默认 8 篇，rank-policy=off 与阶段 R 的“综述优先”一致）
uv run python 流程1/scripts/reviews_discover_scopus.py \
    --direction "生物学 > 神经生物学 > 神经退行性疾病" \
    --source scopus \
    --limit 8

# ② 组装任务包（原有脚本，无需改动）
uv run python 流程1/scripts/reviews_prepare.py \
    --input .work_cdp/reviews/reviews_scopus.json \
    --fetch-abstracts
```

如果只想先验证链路、不启动浏览器：

```bash
uv run python 流程1/scripts/reviews_discover_scopus.py \
    --direction neuroscience --source openalex --limit 4
```

## 4. 与已有 reviews.json 合并

```bash
uv run python 流程1/scripts/reviews_discover_scopus.py \
    --direction "免疫与感染" --source scopus --limit 6 \
    --out .work_cdp/reviews/reviews_scopus.json \
    --merge-into .work_cdp/reviews/reviews.json
```

合并按 DOI 去重；只补空字段（摘要、OA 链接、why_selected），不覆盖原有记录。

## 5. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--direction` | — | 中文/英文方向；命中 `skills/scopus-wos-lit/assets/direction_taxonomy.json` |
| `--plan` | — | 直接给 litquery plan JSON（优先于 direction） |
| `--source` | `scopus` | `scopus` / `wos` / `openalex` / `scopus-api` / `wos-api` |
| `--limit` | 8 | 输出篇数 |
| `--candidates` | 30 | 候选上限 |
| `--pages` | 3 | Scopus 页数（每页 10 条） |
| `--min-year` | 2018 | 年份下限 |
| `--min-citations` | 10 | 被引下限（年龄感知下限仍生效） |
| `--rank-policy` | `off` | `off` 与阶段 R 一致；`balanced` / `strict` 启用分区 |
| `--sort` | `cited` | `cited` / `relevance` / `date` |
| `--max-per-journal` | 3 | 同刊上限，避免单一顶刊占满 |
| `--browser-budget` | 4 | 允许打开几个 Scopus 记录页补摘要 |
| `--port` | 9222 | CDP 端口 |
| `--merge-into` | — | 合并到已有 reviews.json |
| `--no-browser` | 关 | 只用 OpenAlex/API 时使用 |

## 6. 数据源差异

| 源 | 能拿到 | 限制 |
|---|---|---|
| `scopus` | 命中数、结果行、DOI、摘要、期刊/年份/被引 | 可能需要登录；工具会先从主页重发一次查询 |
| `wos` | 命中数；登录后才有记录 | 未登录只读计数，记录为空 |
| `openalex` | 记录、摘要、被引、通讯作者 | 无分区；期刊层级由本地表判定 |
| `scopus-api` / `wos-api` | 官方字段，最稳 | 需要 key 与机构订阅 |

## 7. 输出字段（与 reviews_prepare 对齐）

```json
{
  "schema_version": "flow1-reviews/1.0",
  "source": "scopus",
  "direction": "神经退行性疾病",
  "records": [
    {
      "title": "…",
      "authors": "A; B; C",
      "journal": "…",
      "year": 2022,
      "doi": "10.…",
      "type": "review",
      "citations": 123,
      "oa_url": "",
      "is_oa": false,
      "abstract": "…",
      "platform": "scopus",
      "why_selected": "topic: …; rank: …",
      "first_author": "…",
      "corresponding_authors": ["…"]
    }
  ],
  "meta": {"total": 1234, "candidates": 30, "status": "ok"}
}
```

`reviews_prepare.py` 会自动归一这些字段；缺摘要时标 `metadata-only`，不会假装读到全文。

## 8. 实测记录（2026-10-24）

- `--source openalex --limit 4 --no-browser`：输出 4 条，`reviews_prepare.py --limit 4` 成功生成 4 个任务包（摘要级 2 / 仅题录 2）。
- `--source scopus --direction neurodegeneration --limit 3 --pages 1`：命中 71,249 条，输出 3 条；`reviews_prepare.py` 成功生成 3 个任务包（摘要级 1 / 仅题录 2；把 `--browser-budget` 调大可得更多摘要）。
- `--merge-into` 实测：已有 1 条 + 新增 2 条 → 3 条，按 DOI 去重。
- Scopus 网页路线在 2026-10-22 曾触发 Elsevier 登录重定向；`scopus-wos-lit` 已实现“从主页重发一次查询”的恢复逻辑，主页可用时能取到命中数与结果行。
- WoS 未登录只保证命中数；要记录请登录或使用 `wos-api`。

## 9. 边界

- 本增强**不修改** `search_plan.py` / `search_run.py` / `reviews_prepare.py`，出问题可随时停用。
- 不要与其它 CDP 任务并发跑同一浏览器；默认单实例、5 秒导航间隔。
- 期刊分区是本地表判定；未导入 JCR 百分位时为近似，报告应保留 `approx` 语义。
- 若 `--source scopus` 连续触发验证码，改用 `openalex` 或 API，并如实报告。
- 若需要打包发布，重跑 `pack_release.py` 以刷新 `PACKAGE_MANIFEST.json`；本增强新增的是 skill 目录与一个适配脚本。
