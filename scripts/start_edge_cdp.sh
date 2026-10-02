#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# 启动带 CDP 的 Edge（支持**并行多实例**，参数尽量少）
#
# ★ 参数档位（并列，不叠加；默认 L0 日常档，共 8 个参数）
#
#   L0 日常档（默认）——「像正常打开的浏览器」：
#     必需 3 个：
#       --remote-debugging-port=<port>    调试端口
#       --remote-allow-origins=*          否则 WebSocket 握手 403
#       --user-data-dir=<profile>         独立 profile（一实例一份）
#     正常窗口尺寸：
#       --window-size=1280,900            不用 --start-minimized，开成正常窗口
#     跳过开箱即用体验：
#       --no-first-run --no-default-browser-check
#     屏蔽权限与通知弹窗：
#       --deny-permission-prompts         自动拒绝地理位置/摄像头/通知等权限
#       --disable-notifications           屏蔽网页通知弹窗
#     弹窗抑制（6 个，见下方 BASE_ARGS/DIALOG_ARGS 注释）：
#       崩溃恢复气泡 / 错误对话框 / 重发表单 / 搜索引擎选择页 / 翻译气泡
#
#   L1 极简档 —— 仅 3 个必需参数，作为 A/B 对照
#   L2 扩展档 —— L0 + --lang=zh-CN
#
#   ※ cookie/同意书弹窗**没有**对应命令行开关（Edge 的「阻止 cookie 横幅」
#     是设置项而非启动参数），由 cdp.py 的 CONSENT_JS 在页内保守关闭。
#   ✗ 不使用 --headless：无头模式最易被 Cloudflare 识别
#   ✗ 不使用 --disable-blink-features=AutomationControlled / --excludeSwitches：
#     这些非默认参数本身就是可被检测的指纹
#   ✗ 不做任何 JS 指纹注入（新版 cdp.py 默认 stealth=False）
#
# ★ 为什么多实例：旧版单实例一旦浏览器退出，后续全部请求
#   WinError 10061 失败（见 batch2.log），整批任务报废。
#   一实例一 profile 天然形成 Cookie 池：一个被挑战，其余照常工作。
#
# 用法:
#   bash start_edge_cdp.sh [工作目录] [起始端口] [实例数] [档位 0|1|2]
# 示例:
#   bash start_edge_cdp.sh "D:/work/作者名" 9222 3 0
# ══════════════════════════════════════════════════════════════════
set -u

WORKDIR="${1:-$(pwd)}"
BASE_PORT="${2:-9222}"
COUNT="${3:-1}"
# 档位缺省取 PAPER_CDP_LEVEL（与 cdp_pool.py 的 DEFAULT_LEVEL 同一来源），
# 再缺省为 L0 日常档 —— 保证「启动用什么档，崩溃重启就用什么档」。
LEVEL="${4:-${PAPER_CDP_LEVEL:-0}}"

# ── 定位 Edge ────────────────────────────────────────────────────
MSEDGE=""
for p in "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
         "/c/Program Files/Microsoft/Edge/Application/msedge.exe"; do
  [ -f "$p" ] && MSEDGE="$p" && break
done
[ -z "$MSEDGE" ] && { echo "❌ 未找到 msedge.exe"; exit 1; }

LOGDIR="${WORKDIR}/.work_cdp/logs"
mkdir -p "$LOGDIR"

# ── 档位 → 附加参数（与 cdp_pool.py 的 LEVELS 保持一致）──────────
#   注意：多实例默认**全部用 L0 日常档**；如需 A/B 对比可传 1 或 2 手动指定。
#   L3 极速档：在 L0 基础上关闭「后台网络 / 组件更新 / 同步 / 默认应用」等
#   与页面渲染无关的启动期后台任务 —— 冷启动更快、CPU 占用更低。
#   已实测不影响 Cloudflare 放行（这些是后台服务开关，不改页面指纹）。
#   ★ 弹窗抑制（v6 新增，L0/L2/L3 共有）—— 只关**浏览器自己的对话框**，
#     不碰页面 DOM，也不改 navigator/screen 指纹。与 cdp_pool.py 的
#     _DIALOG_ARGS 必须保持一致（两处任一改动都要同步）。
#
#     --deny-permission-prompts        权限请求（地理位置/摄像头/通知）自动拒绝
#     --disable-notifications          屏蔽网页通知
#     --noerrdialogs                   抑制「页面无响应」等错误对话框（模态，会卡住导航）
#     --disable-session-crashed-bubble 抑制崩溃后「恢复页面」气泡
#     --hide-crash-restore-bubble      同上（新版 Chromium 开关名，两代都带）
#     --disable-prompt-on-repost       抑制「重新提交表单」模态对话框（会直接阻断导航）
#     --disable-search-engine-choice-screen  抑制全新 profile 首启的搜索引擎选择页
#     --disable-features=Translate,TranslateUI  抑制「是否翻译此页面？」气泡
#
#   ★ 为什么「恢复页面」气泡必须关：本流程会 taskkill /F 强制结束实例，
#     同一 profile 下次启动**必然**弹出该气泡，遮挡内容并抢焦点。
#   ★ 未知开关会被 Chromium 静默忽略，故「两代开关名都带」是安全的。
BASE_ARGS=(
  --window-size=1280,900
  --no-first-run --no-default-browser-check
  --deny-permission-prompts --disable-notifications
)
DIALOG_ARGS=(
  --noerrdialogs
  --disable-session-crashed-bubble --hide-crash-restore-bubble
  --disable-prompt-on-repost
  --disable-search-engine-choice-screen
  --disable-features=Translate,TranslateUI
)
FAST_ARGS=(
  --disable-background-networking --disable-component-update
  --disable-sync --disable-default-apps --no-service-autorun
  --disable-client-side-phishing-detection --disable-domain-reliability
)

extra_args() {
  local lv="$1"
  case "$lv" in
    0) printf '%s\n' "${BASE_ARGS[@]}" "${DIALOG_ARGS[@]}" ;;
    1) : ;;   # 极简档：无附加参数（A/B 对照用，刻意不含弹窗抑制）
    2) printf '%s\n' "${BASE_ARGS[@]}" "${DIALOG_ARGS[@]}" --lang=zh-CN ;;
    3) printf '%s\n' "${BASE_ARGS[@]}" "${DIALOG_ARGS[@]}" "${FAST_ARGS[@]}" ;;
  esac
  return 0
}

cdp_ready() {  # $1=port
  curl -s --max-time 2 "http://127.0.0.1:$1/json/version" 2>/dev/null | grep -q webSocketDebuggerUrl
}

# ── 阶段一：并行启动全部实例（不再逐个等待，总启动时间≈最慢的单个）──
LAUNCHED=()
ok=0
for i in $(seq 0 $((COUNT - 1))); do
  PORT=$((BASE_PORT + i))
  LV="$LEVEL"                        # 默认全部 L0 日常档（A/B 时可显式指定 1 或 2）

  # 9222 沿用历史 profile（保留已获得的放行 Cookie）；其余端口独立 profile
  if [ "$PORT" -eq 9222 ]; then
    PROFILE="${WORKDIR}/.edge-cdp-profile"
  else
    PROFILE="${WORKDIR}/.edge-cdp-p${PORT}"
  fi
  mkdir -p "$PROFILE"

  if cdp_ready "$PORT"; then
    echo "✅ 端口 ${PORT} 已有存活实例，复用"
    ok=$((ok + 1)); continue
  fi

  # 清理占用该端口的残留进程
  for pid in $(netstat -ano 2>/dev/null | grep ":${PORT}" | grep -i LISTEN | awk '{print $NF}' | sort -u); do
    MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /T /F >/dev/null 2>&1
  done

  mapfile -t EXTRA < <(extra_args "$LV")
  echo "启动 Edge L${LV} port=${PORT} profile=$(basename "$PROFILE") (参数 $((4 + ${#EXTRA[@]})) 个)"
  nohup "$MSEDGE" \
    --remote-debugging-port="${PORT}" \
    "--remote-allow-origins=*" \
    --user-data-dir="${PROFILE}" \
    "${EXTRA[@]}" \
    about:blank > "${LOGDIR}/edge_${PORT}.log" 2>&1 &
  LAUNCHED+=("$PORT")
done

# ── 阶段二：并行探活（0.25s 轮询，最多 40s）─────────────────────
if [ "${#LAUNCHED[@]}" -gt 0 ]; then
  for _ in $(seq 1 160); do
    all_ok=1
    for PORT in "${LAUNCHED[@]}"; do
      cdp_ready "$PORT" || all_ok=0
    done
    [ "$all_ok" -eq 1 ] && break
    sleep 0.25
  done
  for PORT in "${LAUNCHED[@]}"; do
    if cdp_ready "$PORT"; then
      echo "   ✅ CDP 就绪: http://127.0.0.1:${PORT}"
      ok=$((ok + 1))
    else
      echo "   ❌ 端口 ${PORT} 启动失败，日志尾部："
      tail -8 "${LOGDIR}/edge_${PORT}.log" 2>/dev/null | sed 's/^/      /'
    fi
  done
fi

echo "──────────────────────────────────────────────"
echo "就绪实例 ${ok}/${COUNT}（端口 ${BASE_PORT}..$((BASE_PORT + COUNT - 1))）"
[ "$ok" -eq 0 ] && exit 1
exit 0
