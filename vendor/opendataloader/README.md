# opendataloader-pdf（可选轻量解析层 · 已并入流程1）

> 上游：https://github.com/opendataloader-project/opendataloader-pdf
> 版本：CLI 2.5.22｜许可证：Apache-2.0（Copyright 2025-2026 Hancom, Inc.）
> 本目录是**完整解压并整合进流程的随包副本**，不是 zip 运行时解压。

## 定位（分层共存）

流程1 的 PDF 解析分三层，本模块是其中的**可选轻量层**：

| 层 | 实现 | 适用 | 是否必装 |
|---|---|---|---|
| 主链路 | fitz/pypdf（`scripts/prep_paper.py` / `extract_parallel.py`） | 数字版 PDF 按页文本 | 是 |
| **可选轻量层** | **opendataloader（本目录）** | 数字版 PDF 确定性 Markdown + 逐元素 bbox JSON，交叉校验/备选引擎 | 否 |
| 可选深解析层 | MinerU（环境级，见 `scripts/optional_layers.py`） | 扫描件 OCR / 复杂表格 / 公式 | 否 |

**本层不替换主链路**。数字版 PDF 上约 0.7s/篇，输出确定性 Markdown 与带
`bounding box` 的 JSON（利于溯源/引用定位）。**本地模式无 OCR**：扫描件 /
复杂表格 / 公式需上游 hybrid 模式（另装 Python AI 栈），那不在本可选层范围 ——
深解析请走 MinerU。

## 目录内容

```
vendor/opendataloader/
├─ opendataloader-pdf-cli-2.5.22.jar   #  fat jar（24.3 MB，含全部运行依赖）
├─ LICENSE                              #  Apache-2.0（保留，合规必需）
├─ NOTICE                               #  上游署名与第三方声明（Apache-2.0 §4 要求保留）
├─ THIRD_PARTY/                         #  fat jar 内第三方组件的许可证与声明（保留）
│  ├─ THIRD_PARTY_LICENSES.md
│  ├─ THIRD_PARTY_NOTICES.md
│  └─ licenses/*.txt
└─ README.md                            #  本整合说明（替换上游营销 README）
```

> 上游 44KB 营销 README 已删除（运行时不需要，完整文档见上游仓库）。
> LICENSE / NOTICE / THIRD_PARTY **不可删**：fat jar 捆绑了 JJ2000、Plexus、
> ICU 等第三方组件，再分发须随附其许可证与声明。

## 运行要求

- **Java 11+**（本机已装 Java 26）。jar 不自带 JRE。
- 无 Python 依赖、无模型、无 GPU、无网络（本地模式纯规则确定性解析）。

## 流程侧入口（唯一 adapter）

`scripts/opendataloader_extract.py` 是核心侧访问本模块的唯一入口
（类比 `nature_bridge.py` 之于 `vendor/nature-paper-card`）：

```bash
# 可用性探测（0=可用 / 3=不可用）
uv run python 流程1/scripts/opendataloader_extract.py --detect

# 抽取：数字版 PDF → Markdown + bbox JSON（默认输出 .work_cdp/odl/）
uv run python 流程1/scripts/opendataloader_extract.py "<pdf>" --format markdown,json

# 零网络自检
uv run python 流程1/scripts/opendataloader_extract.py --self-test
```

jar 发现优先级：`OPENDATALOADER_JAR`（显式覆盖）> `vendor/opendataloader/*.jar`。
缺 Java 或缺 jar 时**诚实降级**（退出 2/3），主链路 fitz 不受影响。

## 发行打包

`scripts/pack_release.py` 在含 vendor 时整目录带走本模块（同 nature-paper-card）；
`--no-vendor` 时不带，流程改回 fitz 主链。jar 为二进制，随包保留原样、不做路径重写。

## 本地改动登记

- 删除上游 `README.md`（营销文档），替换为本整合说明。
- 其余文件（jar / LICENSE / NOTICE / THIRD_PARTY）保持上游原样，未改字节。
- 升级版本时：替换 jar、同步 LICENSE/NOTICE/THIRD_PARTY、更新本文件版本号。