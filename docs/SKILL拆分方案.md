# SKILL.md 拆分方案（v2 · 已确认决策）

> 状态：方案待实施。本文件是设计文档，不是权威流程；权威仍为 `SKILL.md`。
> 约束：**不改动已打包发行版** `dist/流程1-含CDP-20261118/` 与其 zip；拆分只动源码树，发行版另行重打。

## 0. 已确认决策（用户拍板）

1. **按链分组**拆分阶段文件（非逐阶段），控制文件数。
2. **主文件明确声明：不进行全量子 skill 加载**——仅常驻权威核心，阶段文件进入该阶段才读。
3. **去重 `完整链路.md`**：其 N–S–R–R2 操作内容并入 prelude 阶段文件。注意它**不在发行清单**（`pack_release.py` TOP_ITEMS 无此文件，发行版本就没有它），属开发期操作手册。
4. **索引取并集单独拆分**：`SKILL.md §8 脚本索引` 与 `docs/开发项目说明.md §13 CLI 索引` 取**并集**，合并为单独参考文件；主文件与开发说明各留指针。
5. **scopus-wos 综述增强**：核查结论＝**已成功接入**（见 §6），无需新接入。

## 1. 目标与原则

- 主 `SKILL.md`（现 3258 行 / 300KB）瘦身为**常驻权威核心 + 路由表**，目标约 600–800 行；移出约 1900 行阶段细节到懒加载文件。
- **红线与契约单一权威源**：硬性要求 1–9、§5.1/§5.2、闸门/数据契约只留主文件；阶段文件引用不复制（防漂移）。
- **复用既有子 skill**，不重复造文件；相对路径（已文件夹名无关）。
- 收益前提：宿主**真懒加载**（进入阶段才读对应文件）；若全量自动加载则无上下文增益——主文件须显式禁止全量加载。

## 2. 主 SKILL 保留清单（常驻核心）

| 节 | 处理 | 理由 |
|---|---|---|
| 文件头硬性要求 1–9 | 保留 | 全局权威 |
| **新增：加载纪律声明** | 保留（醒目） | 明确"仅常驻本核心；阶段文件按需读；**禁止一次性全量加载所有子 skill/阶段文件**" |
| §0 快速开始 | 保留（精简） | 入口 |
| §1 适用场景 + 目录约定 | 保留 | 锚点 |
| §2 环境 | 保留 | 短 |
| §4 标准工作流 | 保留并改为**路由表** | 阶段顺序 + 每阶段→指向文件 |
| §5.1 分层获取 / §5.2 鼠标政策 | 保留（红线） | 最高优先级 |
| §6 日志 / §7 产出物规范 | 保留核心契约 | 跨阶段 |
| §8 脚本索引 | **移并集参考文件**，主文件留指针 | 见 §4 |
| §10 复用检查清单 | 保留 | 交付门 |
| §3 工具清单 / §5 关键坑 | 移参考文件按需读 | 非每步必需 |

## 3. 阶段文件划分（按链分组，新建 `stages/`）

**复用既有子 skill（不新建）：**
- H/H2/R2 解读细节 → `subagent-driven-review/SKILL.md`（896 行，可另行瘦身）
- P → `ppt-integration/SKILL.md`；H 深读卡引擎 → `paper-card-integration/SKILL.md`；E 实现 → `optional-cdp-fulltext/SKILL.md`

**新建阶段文件：**

| 文件 | 内容（原 SKILL 行号） |
|---|---|
| `stages/prelude-N.md` | 阶段 N（414–515） |
| `stages/prelude-S.md` | 阶段 S 导师抓取（706–958） |
| `stages/prelude-R.md` | R/R①/R①.5 + 领域概貌模板（958–1416）；**吸收 完整链路.md 的 N–R2 操作内容**；含 scopus-wos 可选增强指针 |
| `stages/author-0.md` | 阶段 0 准备（1416–1551） |
| `stages/author-acquire.md` | A/B/C/D/D2 采集（1551–1776） |
| `stages/author-fulltext.md` | E + §4.2 边下载边抽取（1776–1985, 577–706） |
| `stages/author-translate.md` | F/G/G2（1985–2077） |
| `stages/author-interpret.md` | H/H2 路由要点 + §4.1 并行轮询（2077–2398, 515–577）；细节指向 subagent-driven-review |
| `stages/author-graph.md` | K 图谱（2398–2591） |
| `stages/author-delivery.md` | I 收尾（2640–2666） |

## 4. 索引并集拆分

- 新建 `docs/脚本与CLI索引.md`：取 `SKILL.md §8`（脚本功能/用法速查）与 `开发项目说明.md §13`（CLI 参数详解）的**并集**，去重合并为单一脚本/CLI 参考。
- 主 `SKILL.md §8` 改为一句话指针 + 最常用几条命令；`开发项目说明.md §13`  likewise 留指针，保留其模块边界/数据契约等非索引内容。
- `check_doc_refs.py` PATH_RE 增 `stages/`、`docs/脚本与CLI索引.md` 引用解析。

## 5. 加载与引用契约

- 主文件路由表每行：`阶段 X → 先读 stages/X.md（或子 skill Y/SKILL.md）再执行`。
- **加载纪律**（主文件醒目声明）：常驻仅主核心；**禁止全量加载所有子 skill / 阶段文件**；进入某阶段才读该阶段文件；红线/契约 always 在主核心，不随阶段文件复制。
- 阶段文件自包含本阶段步骤；红线/契约写"遵守主 SKILL §5.1/§5.2/硬性要求"，不复制正文。
- 引用全用相对路径。

## 6. scopus-wos 综述增强接入核查（结论：已接入）

| 核查项 | 结果 |
|---|---|
| 主 SKILL 阶段 R 引用 | ✓ 1107（skill 表）/1142–1153（可选增强命令框）/3040（脚本索引） |
| 适配脚本 | ✓ `scripts/reviews_discover_scopus.py` 导入正常、CLI 完整 |
| skill 完整性 | ✓ `skills/scopus-wos-lit/` 含全部 litquery 模块（util.py 在位） |
| 下游契约 | ✓ 离线端到端：合成 `reviews_scopus.json` → `reviews_prepare.py` → 生成 2 个 R2 任务包 |
| 随包 | ✓ skills/ 与 scripts/ 均在 TOP_ITEMS |
| 历史实测 | ✓ docs/综述获取增强_scopus_wos.md §8（2026-10-24，openalex/scopus/merge） |

- 定位为**阶段 R 可选增强**（默认不启用，不改 search_plan/search_run/reviews_prepare）；`route_databases.py` 不含 scopus/wos 属设计如此（独立适配脚本按需调用）。
- `check_doc_refs --all` 曾报该 skill 引用 `scripts/litquery/*.py` 缺失＝**误报**（skill 内部相对路径被按流程根解析）；脚本实际存在于 `skills/scopus-wos-lit/scripts/`。
- 联网实测（scopus/wos 真实检索）需授权且可能需登录，本次未跑；代码级与离线契约已验证。

## 7. 完整链路.md 去重

- 现状：596 行 N–S–R–R2 操作手册；**不在发行清单**（开发期文档）。
- 处理：把其独有操作内容并入 `stages/prelude-N/S/R.md`；`完整链路.md` 退化为薄指针（指向阶段文件）或移除。
- 同步更新引用它的文档（项目交接说明、开发项目说明、README_FOR_AGENTS）。

## 8. 迁移与验证（增量、可回滚、不动 dist）

1. 基线已提交（可回滚）。
2. **试点一条链**（author-acquire）：抽出→主文件改路由指针→验证 `check_doc_refs`、`selftest_pipeline --offline`、红线无复制。
3. 逐链推进（prelude N/S/R → author 0/E/F–I/K），每链独立验证。
4. 索引并集拆文件；完整链路去重；主文件加加载纪律声明并瘦身。
5. 同步 §8 指针、README_FOR_AGENTS、docs（第 9 条）。
6. 终验：`check_doc_refs`（对齐基线）、`selftest_pipeline --offline`、`pack_release --self-test`。
7. 登记 `版本变动.md`。
8. **发行版**：源码拆分验证通过后，如需新发行版另行重打（本次不动 dist/流程1-含CDP-20261118）。

## 9. 风险 / 后续

- 加载机制须确认真懒加载（收益前提）。
- `subagent-driven-review`（896 行）是否一并瘦身——另议。
- 主核心目标行数与 §8 索引留存粒度——已定：索引移并集文件。
- 阶段文件增多放大维护面，受第 9 条文档同步约束。