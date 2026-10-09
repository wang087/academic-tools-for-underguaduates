#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# 作者论文分析 · 一键执行（数据采集阶段）
#
# 用法： bash run_all.sh "<作者姓名>" "<ORCID>" ["<工作目录>"] [实例数]
# 示例： bash run_all.sh "<作者姓名>" "<ORCID iD>" "<工作目录>" 3
#
# v2（2026-09-11）：
#   · 多实例并行 CDP（默认 3 个，参数档位 L0/L1/L2）
#   · 全文获取**一律走 CDP**（v4 取消 OA 纯 HTTP 直取**全文**；
#     v7.1 改为分层：**原文**仍必须 CDP，**摘要/综述题录/白名单开源库**允许直连）
#   · 全程日志落盘 <作者目录>/.work_cdp/logs/
#
# v6（2026-09-17）：
#   · 全文目录改为 <作者名>_paper_Exports（历史 <作者名>_Exports 自动沿用）
#   · 补上**同名消歧**步骤（此前只打印提示、从不实际执行），判据取自
#     PAPER_SURNAME / PAPER_GIVEN / PAPER_AFFIL_TOKENS / PAPER_ANCHOR_DOI
#   · enrich 改为透传环境变量回退，不再因缺 --surname/--given 而失效
#   · CDP 档位可由 PAPER_CDP_LEVEL 统一指定（启动与崩溃重启同档）
#
# [WARN] 本脚本**必须**配合 流程1/SKILL.md 使用；不要绕过流程自编抓取步骤。
# ══════════════════════════════════════════════════════════════════
set -u
# 管道中的前置脚本失败必须传递出来，不能被 tail 的成功码掩盖。
set -o pipefail

AUTHOR_NAME="${1:-}"
ORCID_ID="${2:-}"
# 输出根（用户指示）：默认 <流程1>/<年月日>/<作者名>，即按天隔离的作者目录。
#   仍可用第 3 个参数覆盖（换盘/换机时用）。
BASE_DIR="${3:-$(cd "$(dirname "$0")" && pwd)/$(date +%Y%m%d)}"
N_INST="${4:-3}"

if [ -z "$AUTHOR_NAME" ] || [ -z "$ORCID_ID" ]; then
  echo "用法: bash run_all.sh \"<作者姓名>\" \"<ORCID>\" [\"<输出根>\" 默认 流程1/<年月日>] [实例数 默认3]"
  echo "      例: bash run_all.sh 刘北明 0000-0000-0000-0000"
  echo "          → 输出落 流程1/$(date +%Y%m%d)/刘北明/"
  exit 1
fi

WORKDIR="${BASE_DIR}/${AUTHOR_NAME}"
SDIR="$(cd "$(dirname "$0")" && pwd)/scripts"
export PAPER_WORKDIR="${WORKDIR}"
export PAPER_AUTHOR="${AUTHOR_NAME}"

# ── CDP 档位（PAPER_CDP_LEVEL 统一指定，缺省 L0 日常档）────────────
#   本变量同时决定**首次启动**（下面传给 start_edge_cdp.sh）与**崩溃重启**
#   （cdp_pool.DEFAULT_LEVEL 自己的同名环境变量），保证一轮任务内档位一致。
#
#   ★ 必须在这里显式校验，不能只依赖下游的 case 匹配：
#     start_edge_cdp.sh 的 extra_args() 用 `case "$lv"` 分档，**不匹配时不报错、
#     直接返回空**，于是非法值会静默退化成「只有 3 个必需参数」的 L1 ——
#     丢掉全部弹窗抑制（含 --disable-session-crashed-bubble 等 6 项）。
#     这类「不报错但行为变了」的静默失败，比直接报错难查得多。
#     校验放在建目录/启动之前，做到 fail-fast、不留半成品。
CDP_LEVEL="${PAPER_CDP_LEVEL:-0}"
case "${CDP_LEVEL}" in
  0|1|2|3) ;;
  *)
    echo "[X] PAPER_CDP_LEVEL='${CDP_LEVEL}' 非法：只接受 0 / 1 / 2 / 3。" >&2
    echo "    0=L0 日常档（默认） 1=L1 极简档 2=L2 扩展档 3=L3 极速档" >&2
    echo "    不传该变量即用 L0；要 L3 极速档： export PAPER_CDP_LEVEL=3" >&2
    exit 2
    ;;
esac

# 全文阶段结束即回收本流程启动的 Edge CDP 实例；EXIT trap 兜底异常/中断。
cleanup_cdp() {
  uv run python "${SDIR}/cdp_pool.py" down --base=9222 --span="${N_INST}" >/dev/null 2>&1 || true
}
trap cleanup_cdp EXIT INT TERM

echo "════════════════════════════════════════════"
echo " 作者: ${AUTHOR_NAME}  ORCID: ${ORCID_ID}"
echo " 工作目录: ${WORKDIR}"
echo " CDP 实例: ${N_INST} 个（端口 9222..$((9222 + N_INST - 1))，档位 L${CDP_LEVEL}）"
echo "════════════════════════════════════════════"

# ── 0. 建目录 ────────────────────────────────────────────────────
# 全文目录规范名：<作者名>_paper_Exports（见 SKILL §7）。
# 若历史目录 <作者名>_Exports 已存在且含 PDF，store.exports_dir() 会自动沿用，
# 不会迁移、不会丢文件。
mkdir -p "${WORKDIR}/${AUTHOR_NAME}_paper_Exports" "${WORKDIR}/.work_cdp/logs"
echo "[0/7] 目录已建立"

# ── 0.5 v7：准备阶段闸门 ──────────────────────────────────────
# 阶段 0（prepare_stage.py）在**对话里**收齐选择后落盘；本脚本只读闸门，
# 不替用户做选择。没有计划文件时按**保守**处理（跳过可选环节）并明确告知。
gate() {   # gate <阶段> → 0=执行，3=跳过
  uv run python "${SDIR}/prepare_stage.py" --check "$1" >/dev/null 2>&1
}
if [ ! -f "${WORKDIR}/.work_cdp/prepare_plan.json" ]; then
  echo "[0.5] [WARN] 未找到 .work_cdp/prepare_plan.json（阶段 0 未执行）"
  echo "      将**保守跳过**反向参考文献网络（D2/G2/H2）等可选环节。"
  echo "      建议先在对话里确认选择，再跑："
  echo "        uv run python 流程1/scripts/prepare_stage.py --set output_format=markdown \\"
  echo "            --set reverse_refs=yes --set translate=no --set make_ppt=no"
fi
if gate D2; then RUN_D2=1; else RUN_D2=0; echo "[0.5] 阶段 D2 已按准备计划跳过"; fi

# ── 0.6 v13：解读档位（只读展示，**不改变本脚本的任何行为**）────────
#   本脚本只做数据采集；解读档位由阶段 H / H2 / R2 各自的脚本读取任务包后生效。
#   在这里打印一遍是为了让「跑完采集才发现档位选错」这种事不发生。
#   --tier 的 stdout 现在**只有** L1|L2|L3（已把日志行移到 stderr 之前的处理），
#   所以可以直接去掉空白后使用；取不到就跳过，绝不因此中断。
TIER_H="$(uv run python "${SDIR}/prepare_stage.py" --tier H  2>/dev/null | tr -d '[:space:]')"
TIER_REVIEW="$(uv run python "${SDIR}/prepare_stage.py" --tier H2 2>/dev/null | tr -d '[:space:]')"
if [ -n "${TIER_H}" ]; then
  echo "[0.6] 解读档位：作者论文 ${TIER_H}；综述类（H2/R2）${TIER_REVIEW:-L3}"
  echo "      改档： uv run python 流程1/scripts/prepare_stage.py --set interpret_tier=L1|L2|L3"
  echo "             uv run python 流程1/scripts/prepare_stage.py --set review_interpret_tier=L1|L2|L3"
else
  echo "[0.6] [INFO] 未读到解读档位（将按默认：作者论文 L2、综述类 L3）"
fi

# ── 0.7 v17：解读引擎（同样只读展示，不改变本脚本的行为）────────────
#   legacy = 既有 11 字段解读卡；paper-card = 额外产出 16 节 Paper Card，
#   交付前强制跑结构审计 + 数字回查原文（nature_bridge.py check）。
#   在这里打印，是为了让「跑到阶段 H 才发现想要深读卡」这种事不发生。
ENGINE_H="$(uv run python "${SDIR}/prepare_stage.py" --engine H 2>/dev/null | tr -d '[:space:]')"
if [ -n "${ENGINE_H}" ]; then
  echo "[0.7] 解读引擎：${ENGINE_H}"
  if [ "${ENGINE_H}" = "paper-card" ]; then
    echo "      深读卡由已并入的 nature-paper-card 产出（流程内，无外部依赖）"
    echo "      交付前质量门： uv run python 流程1/scripts/nature_bridge.py check --help"
  fi
  echo "      改引擎： uv run python 流程1/scripts/prepare_stage.py --set interpret_engine=legacy|paper-card"
else
  echo "[0.7] [INFO] 未读到解读引擎（将按默认 legacy：既有 11 字段解读卡）"
fi

# ── 1. 启动多实例 CDP Edge（最小参数档位）────────────────────────
#   档位由 ${CDP_LEVEL}（= PAPER_CDP_LEVEL，缺省 L0）决定，经第 4 个位置参数
#   传入；**不要再写死一个常量** —— 那会让 PAPER_CDP_LEVEL 被
#   start_edge_cdp.sh 的 `${4:-${PAPER_CDP_LEVEL:-0}}` 短路，静默失效。
echo "[1/7] 启动 ${N_INST} 个 CDP Edge 实例（档位 L${CDP_LEVEL}）..."
bash "${SDIR}/start_edge_cdp.sh" "$WORKDIR" 9222 "$N_INST" "${CDP_LEVEL}" || {
  echo "      [X] CDP 启动失败"; exit 1; }

# ── 2. 抓取 ORCID 成果 ─────────────────────────────────────────
echo "[2/7] 抓取 ORCID 成果列表 ..."
uv run --with websocket-client python "${SDIR}/orcid_extract.py" "https://orcid.org/${ORCID_ID}" 2>&1 | tail -5
uv run --with websocket-client python "${SDIR}/parse_orcid.py" 2>&1 | tail -3 \
  || echo "      [WARN] 解析脚本需按输出路径微调"

# ── 3. 提取 DOI ────────────────────────────────────────────────
echo "[3/7] 提取 DOI ..."
uv run python - <<PYEOF
import json, re, pathlib, os
wd = pathlib.Path(os.environ["PAPER_WORKDIR"])
src = wd / ".work_cdp" / "orcid_works_parsed.json"
recs = json.loads(src.read_text(encoding="utf-8")) if src.exists() else []
dois = set()
for r in recs:
    d = (r.get("doi") or "").strip()
    d = d.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    if re.match(r"^10\.\d{4,9}/\S+$", d):
        dois.add(d)
out = wd / ".work_cdp" / "dois.txt"
out.write_text("\n".join(sorted(dois)), encoding="utf-8")
print(f"      共 {len(dois)} 个有效 DOI → {out}")
PYEOF

# ── 4. Crossref 核实 + 回填作者全名 + 同名消歧 ─────────────────
echo "[4/7] Crossref 逐条核实题录 ..."
uv run python "${SDIR}/fetch_crossref_all.py" 2>&1 | tail -3
uv run python "${SDIR}/retry_crossref.py" 2>&1 | tail -3
echo "[4/7] 回填 Crossref 结构化作者姓名 ..."
# --surname/--given 省略时脚本自行回退 PAPER_SURNAME / PAPER_GIVEN
uv run python "${SDIR}/enrich_crossref_authors.py" 2>&1 | tail -5

# 同名消歧 —— 作者聚合普遍过度合并，此步不可省。
#   判据全部来自环境变量；缺少姓名时**明确告警并说明后果**，不静默跳过。
if [ -n "${PAPER_SURNAME:-}" ] && [ -n "${PAPER_GIVEN:-}" ]; then
  DIS_ARGS=(--surname="${PAPER_SURNAME}" --given="${PAPER_GIVEN}")
  if [ -n "${PAPER_AFFIL_TOKENS:-}" ]; then
    IFS='|' read -r -a _affs <<< "${PAPER_AFFIL_TOKENS}"
    for _a in "${_affs[@]}"; do
      [ -n "$_a" ] && DIS_ARGS+=(--affil="$_a")
    done
  fi
  [ -n "${PAPER_ANCHOR_DOI:-}" ] && DIS_ARGS+=(--anchor-doi="${PAPER_ANCHOR_DOI}")
  echo "[4/7] 同名消歧（判据：单位指纹 ${#DIS_ARGS[@]} 项）..."
  uv run python "${SDIR}/disambiguate.py" "${DIS_ARGS[@]}" 2>&1 | tail -14
else
  echo "[4/7] [WARN] 未设置 PAPER_SURNAME / PAPER_GIVEN → **跳过同名消歧**。"
  echo "     后果：同名他人的论文会被一并下载并进入最终列表（后续可按"
  echo "     disambiguated.json 排除，但需先补跑消歧）。请设置后重跑："
  echo "       export PAPER_SURNAME=<姓>  PAPER_GIVEN=<名>"
  echo "       export PAPER_AFFIL_TOKENS=\"<单位指纹1>|<单位指纹2>\"   # 竖线分隔，越具体越好"
  echo "       export PAPER_ANCHOR_DOI=<确定无疑的代表作DOI,逗号分隔>"
fi

# ── 4.5 v7：反向查找参考文献（阶段 D2）───────────────────────
#    元数据 API（OpenAlex），零风控、不下载任何 PDF。
#    闸门由阶段 0 的 reverse_refs 决定；关掉时下游 G2/H2 也一并关闭。
if [ "${RUN_D2}" = "1" ]; then
  echo "[4.5] 反向查找参考文献（OpenAlex referenced_works）..."
  uv run python "${SDIR}/refs_backfill.py" --min-citations=200 2>&1 | tail -14
else
  echo "[4.5] [SKIP]  阶段 D2 已跳过 → G2 / H2 同样不会执行（没有参考文献可取）"
fi

# ── 4.8 v7.1：摘要链（第一层直连优先，缺口转第二层 CDP）─────────
#    分层依据 SKILL §5.1：摘要属第一层（允许直连），原文全文属第二层（必须 CDP）。
#    取不到的写 .work_cdp/direct_failed.json，并打印 CDP 回退命令。
echo "[4.8] 摘要链（第一层直连：S2→OpenAlex→EuropePMC→Crossref→PubMed）..."
uv run python "${SDIR}/fetch_direct.py" 2>&1 | tail -12

# ── 5. OA 检查（仅判断获取难度，不据此走 HTTP 直取）────────────
echo "[5/7] Unpaywall 开放获取检查 ..."
uv run python "${SDIR}/unpaywall_check.py" 2>&1 | tail -10

# ── 6. 原文全文一律走 CDP（OA 与付费墙同等对待）────────────────
#    [STOP] §5.1 第二层：不得自编脚本从 HTTP 直链获取**原文全文**（含 OA 文章）；
#       第一层（摘要/元数据/白名单开源库）已在 4.8 走直连，不得混用
#    [WARN] --manual-wait 必须 > 0，否则 CAPTCHA 弹窗链完全不触发
echo "[6/7] 全文抓取（${N_INST} 实例并行，全部经 CDP）..."
uv run --with websocket-client --with pypdf python "${SDIR}/download_all_fulltexts.py" \
  --n="${N_INST}" --limit=20 --delay=25-55 --site-fail-limit=2 \
  --manual-wait=5 --grace=35 2>&1 | tail -25

# ── 7. 生成 GB/T 7714 列表 + 全文校验 + 清单 ───────────────────
echo "[7/7] 生成 GB/T 7714-2015 论文列表 ..."
uv run python "${SDIR}/build_list.py" 2>&1 | tail -3
echo "[7/7] 全文 PDF 校验 ..."
uv run --with pypdf python "${SDIR}/verify_pdfs.py" 2>&1 | tail -8
echo "[7/7] 生成《全文清单.md》..."
uv run --with pypdf python "${SDIR}/inventory_build.py" 2>&1 | tail -8

echo ""
echo "════════════════════════════════════════════"
echo " [OK] 数据采集完成"
echo " 全文 PDF: ${WORKDIR}/${AUTHOR_NAME}_paper_Exports/"
echo " 论文列表: ${WORKDIR}/${AUTHOR_NAME}_论文列表.md"
[ "${RUN_D2}" = "1" ] && \
echo " 高质量文献: ${WORKDIR}/${AUTHOR_NAME}_高质量文献列表.md"
echo " 日志:     ${WORKDIR}/.work_cdp/logs/"
echo ""
echo " 下一步（需 LLM 参与，无法全自动）："
echo "   ① 阶段 F  翻译全文 → templates/中文翻译_HTML模板.html → Chrome 打印 PDF"
if gate G2; then echo "   ② 阶段 G2 refs_bibliography.py →《${AUTHOR_NAME}_代表性文献参考文献.md》"; fi
echo "   ③ 阶段 H  装词表 → interpret_prepare.py → 并行 subagent → interpret_build.py"
echo "              档位 ${TIER_H:-L2}：提示词必须写死「任务包路径 / 结果路径 / 只写自己那份 / 档位」四件事"
if gate H2; then
echo "   ④ 阶段 H2 refs_curate.py → refs_prepare.py → 并行 subagent → refs_build.py"
echo "              →《${AUTHOR_NAME}_研究方向综述.md》（review 结果）"
fi
if gate P; then echo "   ⑤ 阶段 P  ppt_brief.py → 手写 SVG → 质检 0 error → 串行导出 PPTX"; fi
echo "   ⑥ 阶段 I  report_artifacts 申报交付物"
echo "════════════════════════════════════════════"
