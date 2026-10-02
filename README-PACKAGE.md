# 流程1 · 打包发行版说明

> 本目录是 `流程/` 的**自包含发行版**：全流程脚本 + 模板 + 子流程文档 + 依赖（`vendor/ppt-master`）。
> 源目录：`C:\Users\22525\scientific-agent-skills\流程`
> 文件数：**12995**　｜　路径重写：**21** 个文件

## 与原 `流程/` 目录的差异

| 项 | 说明 |
|---|---|
| 目录名 | `流程/` → `流程1/`；**文档与脚本里的路径引用已同步重写**（21 个文件），照着文档敲命令不会敲错 |
| 依赖 | 新增 `vendor/ppt-master/`（pptx 生成依赖，阶段 P 用）。`ppt_brief.py` / `selftest_pipeline.py` **优先**用这份随包副本 |
| 未包含 | `ppt-master/projects/`（用户项目数据）、`.git/`（版本库）、`docs/assets/`（官网素材）—— 属开发/用户数据，不应随包分发 |
| 内容 | 其余文件与源目录**逐字节一致**（除路径重写）；可用 `PACKAGE_MANIFEST.json` 的 sha256 核验 |

## 快速开始

```bash
cd C:\Users\22525\scientific-agent-skills\流程1

# ① 端到端自检（离线 20 项，不联网、秒级）
uv run python 流程1/scripts/selftest_pipeline.py --offline
#    联网全量（38 项）
uv run python 流程1/scripts/selftest_pipeline.py

# ② 阶段 0：准备阶段（在对话里收齐选择后落盘）
uv run python 流程1/scripts/prepare_stage.py \
    --set output_format=markdown --set translate=no \
    --set reverse_refs=yes --set make_ppt=no
uv run python 流程1/scripts/prepare_stage.py --check-all
```

完整流程见 [`流程1/SKILL.md`](流程1/SKILL.md)；上手看 [`流程1/README.md`](流程1/README.md)。

## 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows（流程基于 win32 + Git Bash） |
| Python | 通过 `uv` 运行，脚本按需 `uv run --with <pkg>` 拉依赖 |
| 浏览器 | Microsoft Edge（阶段 B–E 的 CDP 抓取要用） |
| 网络 | 阶段 D2/G2/H2 走 OpenAlex 等元数据 API；摘要在第一层直连；**原文全文经 CDP** |

## 抓取策略（v7.1 分层）

| 层 | 内容 | 方式 |
|---|---|---|
| 第一层 | 元数据 / **摘要** / **综述题录** / 白名单开源库 | ✅ 允许直连（白名单：`流程1/templates/直连白名单.json`） |
| 第二层 | **原文全文**（PDF / HTML，含 OA） | ⛔ 禁止直连，一律走 CDP |
| 回退 | 第一层失败 | ➡️ 转 CDP（`grab_abstracts.py --retry-failed`） |

## 核验这份包

```bash
# 1) 逐文件核对 sha256
uv run python - <<'PY'
import json, pathlib, hashlib
m = json.loads(pathlib.Path('PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
bad = []
for f in m['files']:
    p = pathlib.Path(f['path'])
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != f['sha256']:
        bad.append(f['path'])
print('全部一致' if not bad else f'{len(bad)} 个文件不一致: {bad[:5]}')
PY

# 2) 跑自检（能跑通 = 依赖齐全）
uv run python 流程1/scripts/selftest_pipeline.py --offline
```
