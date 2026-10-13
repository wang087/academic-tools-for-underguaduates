# `optional-cdp-fulltext/` —— **可选模块**：用浏览器 / 出版商接口取回正文

> 本目录是流程里**唯一**「自动访问出版商站点取正文」的实现。
> 整目录删掉即可从一份发行版中移除该能力；流程其余部分照常工作（见下）。
> 删除方式：`rm -rf 流程1/optional-cdp-fulltext/`（或打包时用
> `pack_release.py --exclude-optional-cdp-fulltext`）。

## 为什么单独放

出版商的网站条款（如 Elsevier 的 *Website Terms and Conditions*）明确禁止：

> You may not use any robots, spiders, crawlers or other automated downloading programs,
> algorithms or devices, or any similar or equivalent manual process, to: (i) continuously and
> automatically search, scrape, extract, deep link or index any Content; …

本流程原先把「原文全文一律经 CDP 浏览器抓取」写成硬性阶段（§5.1 第二层）。
为了能交付一份**不含该能力**的发行版，这部分被抽成独立可选模块：
**核心不再 import 本目录里的任何东西**，只通过唯一探测入口
`流程1/scripts/cdp_module.py` 询问"在不在、脚本在哪"。

## 运行时行为（探测入口定义，用户指定）

```
模块在   → 走正常 CDP 路线：多实例浏览器下载 / 出版商 API / 浏览器另存 PDF
模块不在 → **静默改走备用链路**：白名单内直连 + 元数据 API + nature-skill 获取模块
```

"静默"= **不报错、不中断**，不是"不留痕"：缺模块这件事会写进日志与结果里的
`reason`（`cdp_module_absent`）、`reviews_cdp_command.txt` 会写明改走哪条备用链路，
报告里照实申报。能力缺失必须可回查，这是本流程的既有规矩。

探测：

```bash
uv run python 流程1/scripts/cdp_module.py            # 状态 + 本模块提供的脚本清单
uv run python 流程1/scripts/cdp_module.py --path     # 模块目录（未安装则空；给 shell 用）
uv run python 流程1/scripts/cdp_module.py --status   # 0=可用，3=未安装
uv run python 流程1/scripts/cdp_module.py --self-test
```

## 本模块提供

| 文件 | 作用 |
|---|---|
| `download_all_fulltexts.py` | 批量下载**原文全文**（多 CDP 实例并行、IP 级机构订阅、逐站点熔断/节流） |
| `fulltext_publishers.py` | 出版商页面/接口取正文（`classify_doi` / `fetch_fulltext` / API 闸门） |
| `save_pdf_via_browser.py` | 把当前页面另存为 PDF（单篇兜底） |
| `cdp_up_nosandbox.py` | 下载器专用的**无沙箱**启动器（本机 Edge renderer 沙箱起不来时的办法） |
| `check_challenge.py` | 人机验证页/空壳页判定（取正文链路的诊断） |
| `check_access.py` | 机构访问权限诊断（取正文链路的诊断） |

## **不在**本模块内（属核心，不随本模块增删）

`cdp.py`（CDP 客户端）、`cdp_pool.py`（多实例池 + 回收）、`detect.py`（挑战识别）、
`start_edge_cdp.sh` / `renew_cdp_instance.sh`（启动器）——
它们是**双用途基础设施**：阶段 B/C 抓 ORCID、阶段 S 抓导师官网页也在用，
不属于出版商内容。把它们移进本模块会让「移除模块后 ORCID/导师阶段不受影响」不成立。

## 依赖

- 本模块的脚本把 `<流程1>/scripts/` 加进 `sys.path`（见各文件顶部 bootstrap 段），
  因此 `logutil` / `store` / `scope` / `cdp` / `detect` / `white_list_policy` 等核心模块照常可 import。
- 运行期依赖：`uv run --with websocket-client --with pypdf python …`（与核心脚本一致）。
- 需要本机有一个 **Chromium 内核浏览器**（Edge / Chrome / Chromium），
  可用 `PAPER_BROWSER=<绝对路径>` 指定；候选见 `cdp_pool.py` 与 `render_html_pdf.py`。

## 移除后的自检口径

`selftest_fulltext_modules.py` 会把本模块内的 `fulltext_publishers.py` 标为
`[SKIP]`（跳过而不是失败）；`selftest_pipeline.py --offline` 不受影响。
移除后请重跑这两条确认降级路径：

```bash
uv run python 流程1/scripts/selftest_fulltext_modules.py
uv run python 流程1/scripts/fulltext_router.py --self-test
uv run python 流程1/scripts/cdp_module.py --status    # 期望 3
```