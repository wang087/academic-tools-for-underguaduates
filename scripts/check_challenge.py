# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 站点可达性 / 挑战页 / 封禁 体检（单次、低频，只访问 1 个 URL）
#
# 本次修复：旧版用 `len(txt) < 3000` 当作「出现 CHALLENGE」的判据，
#   于是任何短页面（勘误、简讯、尚未渲染完的 SPA）都会被误判成人机验证，
#   进而触发人工等待 → 表现为「卡住」。现改由 detect.judge() 精确判定。
#
# 退出码：
#   0 = 正常可访问
#   3 = 人机验证（challenge）—— 需人工过一次
#   4 = 页面未就绪 / 未知（not-ready）—— 可重试，不是验证
#   2 = 真实封禁文案 —— 立即全局停止
#   1 = 网络/浏览器异常
#
# 用法: python check_challenge.py [URL] [--port=9222] [--wait=8]
# 可移植配置：PAPER_WORKDIR、CDP_PORT
# ─────────────────────────────────────────────────────────────
import io
import pathlib
import sys
import time


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import detect                                                   # noqa: E402
from cdp import CDPUnavailable, connect, is_alive, resolve_port  # noqa: E402
from detect import BLOCKED, CHALLENGE, ERROR, NOT_READY, OK, PAYWALL  # noqa: E402
from logutil import get as get_log, workdir as _wd                # noqa: E402

URL = "https://www.sciencedirect.com/"
WAIT = 8.0
PORT = None
for a in sys.argv[1:]:
    if a.startswith("--port="):
        PORT = int(a.split("=", 1)[1])
    elif a.startswith("--wait="):
        WAIT = float(a.split("=", 1)[1])
    elif not a.startswith("-"):
        URL = a

WD = _wd()
PORT = PORT if PORT is not None else resolve_port(sys.argv[1:])
LOG = get_log("check_challenge", wd=WD)


def main() -> int:
    if not is_alive(PORT):
        LOG.error(f"端口 {PORT} 上没有 CDP 实例（先运行 cdp_pool.py up）")
        return 1
    try:
        c = connect(PORT, logger=LOG, timeout=25)
    except CDPUnavailable as e:
        LOG.error(str(e))
        return 1

    try:
        LOG.info(f"体检 {URL}")
        c.navigate(URL, settle=WAIT, load_timeout=25)
        r = detect.judge(c, timeout=25)
        st = r["state"]
        LOG.info(f"标题: {(r.get('title') or '')[:70]}")
        LOG.info(f"落地: {detect.site_of(r.get('url') or '')} | 文本 {r.get('textlen')} "
                 f"| 判定 {st} ({r['reason']})")

        if st == BLOCKED:
            LOG.error(f"[STOP] 检测到封禁文案 {r.get('markers')} —— 该站点永久跳过，停止后续访问。")
            return 2
        if st == CHALLENGE:
            LOG.warn("[WARN]  出现人机验证页 —— 请在 Edge 窗口人工完成一次（Cookie 会留存）。"
                     "未完成前不要反复请求该站点。")
            return 3
        if st == NOT_READY:
            LOG.warn(f"页面未就绪（{r['reason']}）—— 稍后重试即可，**不是**验证页。")
            return 4
        if st == ERROR:
            LOG.error(f"网络/导航异常: {r['reason']}")
            return 1
        if st == PAYWALL:
            LOG.warn("页面正常，但无全文权限（付费墙）。")
            return 0
        LOG.ok("[OK] 可正常访问（无需人工）")
        if r.get("subscribed"):
            LOG.ok("   检测到机构订阅授权（页面出现 Subscribed / Download PDF）")
        return 0
    finally:
        c.close()
        LOG.close()


if __name__ == "__main__":
    raise SystemExit(main())
