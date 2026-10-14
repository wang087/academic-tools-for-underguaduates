#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# 重建单个 CDP 实例：关闭当前实例 → 换全新 profile 启动
#
# 使用场景：
#   出版社（ScienceDirect / RSC）的 Cloudflare 放行 Cookie 失效后，
#   关闭该端口实例、换一个全新 profile 重新尝试。
#   只重建**一个**端口，其余实例不受影响（多实例并行的意义所在）。
#
# 启动参数原则（最小化，与 start_edge_cdp.sh 一致）：
#   [OK] --remote-debugging-port / --remote-allow-origins=* / --user-data-dir
#   [X] 不加 --disable-blink-features=AutomationControlled、
#      --excludeSwitches、--headless 等 —— 非默认参数本身就是可检测特征
#   [X] 不做任何 JS 指纹注入
#
# 用法: bash renew_cdp_instance.sh [工作目录] [端口] [profile后缀]
# ══════════════════════════════════════════════════════════════════
set -u

WORKDIR="${1:-$(pwd)}"
PORT="${2:-9222}"
TAG="${3:-$(date +%H%M%S)}"

for p in "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
         "/c/Program Files/Microsoft/Edge/Application/msedge.exe"; do
  [ -f "$p" ] && MSEDGE="$p" && break
done
[ -z "${MSEDGE:-}" ] && { echo "未找到 msedge.exe"; exit 1; }

# profile 收在 .work_cdp/ 下（与 cdp_pool.py 的 PROFILE_HOME 保持一致）
PROFILE="${WORKDIR}/.work_cdp/cdp_profiles/.edge-cdp-p${PORT}-${TAG}"
LOGDIR="${WORKDIR}/.work_cdp/logs"
mkdir -p "$LOGDIR"

# ── 1) 关闭该端口的实例（只动调试实例，不影响用户正常浏览的 Edge）──
echo "[1/3] 关闭端口 ${PORT} 上的 CDP 实例 ..."
if curl -s --max-time 4 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  for pid in $(netstat -ano 2>/dev/null | grep ":${PORT}" | grep -i LISTEN | awk '{print $NF}' | sort -u); do
    MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /T /F >/dev/null 2>&1 && echo "     已终止 PID $pid"
  done
  sleep 3
else
  echo "     端口 ${PORT} 未被占用"
fi

# ── 2) 全新 profile 启动 ──
echo "[2/3] 用全新 profile 启动: $(basename "$PROFILE")"
rm -rf "$PROFILE" 2>/dev/null
mkdir -p "$PROFILE"
nohup "$MSEDGE" \
  --remote-debugging-port="${PORT}" \
  "--remote-allow-origins=*" \
  --user-data-dir="${PROFILE}" \
  about:blank > "${LOGDIR}/edge_${PORT}.log" 2>&1 &
sleep 8

if curl -s --max-time 8 "http://127.0.0.1:${PORT}/json/version" | grep -q webSocketDebuggerUrl; then
  echo "[3/3] [OK] 新实例就绪 (port=${PORT}, profile=$(basename "$PROFILE"))"
  echo "     提示：全新 profile 未携带任何历史放行 Cookie；"
  echo "           若该站点风控严格，请在窗口中人工过一次验证再继续。"
else
  echo "[3/3] [X] 启动失败，日志："
  tail -15 "${LOGDIR}/edge_${PORT}.log" 2>/dev/null
  exit 1
fi
