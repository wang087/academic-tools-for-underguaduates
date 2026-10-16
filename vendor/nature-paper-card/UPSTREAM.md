# vendor/nature-paper-card —— 上游来源与本地补丁登记

> 本目录是 **`nature-skills` v0.1.0 中 `nature-paper-card` 一个技能**的并入版，
> 宿主为 `流程1`。并入后 **`流程1` 不依赖任何外部 `nature-skills` 检出**。
>
> **只并入了这一个技能。** 上游其余 19 个技能（`nature-reader`、
> `nature-academic-search`、`nature-reviewer`、`nature-literature-pipeline`、
> `nature-paper2ppt` 等）**不在本目录内，也不被本流程调用**。文中出现这些名字时，
> 指的只是 source-map **数据格式**或上游同族技能的边界说明，不是依赖。

## 1. 上游身份

| 项 | 值 |
| --- | --- |
| 技能 | `nature-paper-card`（上游 `manifest.yaml` 版本 `1.2.0`） |
| 上游包 | `nature-skills` v0.1.0（`.codex-plugin/plugin.json`） |
| 仓库 | https://github.com/Yuan1z0825/nature-skills |
| 作者 | Yizhe Yuan and Nature Skills contributors |
| 许可 | **Apache-2.0** —— 见本目录 `LICENSE`（原样保留，未修改） |
| 并入日期 | 2026-10-07 |
| 并入方式 | 本地目录 `nature-skills-main/` 复制（该检出**无 `.git`**，故无 commit 可记；身份以 `plugin.json` 版本号为锚） |
| 本地版本 | `manifest.yaml` 记为 `1.3.0`（见 §3 补丁，属本地改动） |

## 2. 并入内容与结构改动

| 项 | 处置 |
| --- | --- |
| 技能本体 | 全部并入（`SKILL.md` / `manifest.yaml` / `static/` / `references/` / `scripts/` / `agents/` / `evals/` / `tests/`） |
| 术语台账 | 上游在共享层 `nature-shared/core/terminology-ledger.md`。因只并入本技能，`always_load` 的 `../nature-shared/...` 会指向不存在的目录 —— 已**内联**为 `references/terminology-ledger.md`（内容未改，仅加来源注释），并同步改 `manifest.yaml` |
| 教程文档 | 只带 `docs/nature-paper-card-tutorial{,_EN}.md` 两份；上游其余 `docs/` 未并入 |
| README 链接 | 上游写 `../../docs/...`（指向仓库根）—— 已改为 `docs/...` |
| 未并入 | 上游 `.github/`、`scripts/`、`tests/`、`assets/`、`index.html`、根 `README*.md`、`pr-body-*.md`；其余 19 个技能；安装/自动更新链路（`npx skills add`、`autoupdate-skills.sh`）**一律不属于本次合并，也不应再被引用** |

## 3. 本地补丁登记（升级上游时必须重新施加）

以下改动**不是**上游内容。直接覆盖目录会静默丢掉它们，进而丢掉整个接线的质量门。

| # | 文件 | 改动 | 为什么 |
| --- | --- | --- | --- |
| P1 | `scripts/prepare_paper.py` | `source_block_text()` 的候选字段加入 **`original_text`** | 上游漏了它，而它是 `nature-reader` 官方 `source_map.json` 的块文本字段名。未改前：一份合规 source map 会得到 **0 页 / `invalid` / exit 1**，被迫跌到 `source-limited` |
| P2 | `scripts/prepare_paper.py` | `validate_bundle()` 改为依据 `locator_summary.page_locator_reliability` 推荐定位模式 | 上游只看 `source_type`，于是"每块都带可靠页码"的 source map **永远进不了 `page-grounded`**；而审计器又禁止非 page-grounde 模式引用页码 —— 两个脚本原本自相矛盾 |
| P3 | `SKILL.md` | fallback 模式的审计命令改为 argparse **真实接受**的取值 | 上游给的 `--locator-mode structure-grounded-or-source-limited` 会 **exit 2**，把强制质量门变成"审计器坏了→人工自查" |
| P4 | `scripts/prepare_paper.py` | `CAPTION_RE` 放开"必须冒号" | 上游只认 `Figure 1:`，而 Nature/Scientific Reports 用 `Fig. 1 \|` 或 `Figure 1.`。实测真论文正文 47 处图表引用 → 清单 **0/0/0**，使 `output-contract.md` 的"图表清单完备"闸门**空转** |
| P5 | `scripts/audit_paper_card.py` | 新增 `section_bodies`（节正文非空）与 `section_pointers`（每分析节至少一条 `[Paper: …]`）；清单项匹配改精确边界 | 上游审计器是纯结构 lint：实测一张 **03–15 节正文全空**、只有 1 条指针的卡拿到 `pass (13 passes, 0 errors)`、exit 0；且卡里只写 `Figure 10` 就能满足清单项 `Figure 1` |
| P6 | `scripts/audit_paper_card.py` | `out_of_scope` 词表需**指令语境**才判 error | 上游裸子串匹配：正常行文"It is not a public article…"会 **FALSE FAIL** |
| P7 | `scripts/prepare_paper.py` | 单行长度申报 + 正文软换行（`text_soft_wrapped` / `max_line_length` / `line_count`） | 上游 `json.dumps(indent=2)` 让整页正文落成**一个物理行**（实测最长 7918 字符）。读取工具按行长截断时会**静默丢正文**，且 bundle 无任何字段可暴露。`流程1` 有同类实测事故（见 `流程1/SKILL.md` 的软换行修复） |
| P8 | `SKILL.md` | frontmatter 补 `license` / `compatibility` / `metadata.version`；`manifest.yaml` 版本升至 1.3.0 | PyMuPDF 是硬依赖却只在脚本里出现；上游 `manifest.yaml` 的 version 也不在 `SKILL.md` 里 |
| P9 | `SKILL.md` / `README*.md` / `manifest.yaml` | 加"并入范围"说明；内联术语台账；修 `../../docs/` 链接 | 只并一个技能后，必须让读者知道其余同族技能**不存在**，否则会把上游的边界声明当成可调用依赖 |
| P10 | `tests/` | 扩充 `test_prepare_paper.py`；新增 `test_audit_paper_card.py` | 上游只有 7 例、且只覆盖 source-map 定位；审计器**零测试**。P1/P4/P5/P7 都需要回归保护 |

## 4. 质量门（本流程**额外**接上的那一半）

`nature-paper-card` 自带审计器只证明卡片**形态**合规，**不证明内容可信**。
`流程1` 因此在其之上接了内容级校验（`流程1/skills/paper-interpretation-writing/scripts/check_interpretation.py`），
合二为一的入口是：

```bash
uv run python 流程1/scripts/nature_bridge.py check \
  --card <16 节卡> --bundle <source_bundle.json> \
  --locator-mode <实际模式> --report <审计报告>
```

- 结构审计：节序 / 头部 / 定位模式 / 指针 / 节正文非空；
- 内容校验：**逐个数字回查原文**（`NUMBER_NOT_IN_SOURCE`）—— 这是审计器做不到的一半，
  也是"卡片里的 92% 到底是不是原文里的 92%"唯一能被机械判定的地方。

自检：`uv run python 流程1/scripts/nature_bridge.py --self-test`（零网络，含 P1/P2/P5/P7 的回归点）。

## 5. 升级上游时怎么做

1. 用同版本或新版本的上游 `skills/nature-paper-card/` 替换本目录（保留 `LICENSE`、
   `UPSTREAM.md` 与 `docs/`）；
2. 重新施加 §3 的 **P1–P8**（P9/P10 是本流程的说明与测试，按需保留）；
3. 确认 `manifest.yaml` 的 `always_load` 仍指向**技能内**路径（不得再出现 `../`）；
4. 跑 `uv run python 流程1/scripts/nature_bridge.py --self-test`；
5. 跑 `uv run python 流程1/scripts/selftest_nature_integration.py`；
6. 跑 `uv run python 流程1/scripts/pack_release.py --dest=<临时目录> --dry-run`
   确认打包仍带上本目录。
