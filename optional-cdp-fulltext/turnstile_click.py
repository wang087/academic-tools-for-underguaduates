# -*- coding: utf-8 -*-
"""Turnstile 复选框：截图定位 + 宿主机级点击（§5.2 政策的参考实现）。

不碰浏览器 DOM/CDP：只截图窗口客户区、找 22–50 px 空心方框、用 user32!SendInput 点击。
单独使用：

    python turnstile_click.py                 # 自动找挑战窗口，点一次，观察 30 s
    python turnstile_click.py --keyword 请稍候
    python turnstile_click.py --dry           # 只定位，不点击

在下载流程里可复用 `click_turnstile(hwnd, timeout=30)`：
    hwnd = find_challenge_window()  # 或已知窗口句柄
    ok = click_turnstile(hwnd, timeout=30)

约束（§5.2）：每次挑战最多 2 次点击；失败即停；只点复选框本身。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import cv2
import numpy as np

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import host_click as H  # noqa: E402

CHALLENGE = ("just a moment", "请稍候", "attention required", "are you a robot",
             "performing security verification", "checking your browser",
             "verify you are human", "请验证", "验证您是真人",
             # Elsevier 资产 CDN（pdf.sciencedirectassets.com/craft/capi/cfts/init）
             # 的 "Request Verification: In Progress" 页
             "security verification", "request verification", "verification in progress",
             "请求验证", "正在验证")


def is_challenge(title: str) -> bool:
    low = (title or "").lower()
    return any(k.lower() in low for k in CHALLENGE)


def grab(hwnd) -> np.ndarray:
    """抓客户区（PrintWindow，失败退回屏幕 BitBlt）。"""
    w, h = H.client_size(hwnd)
    hdc = H.user32.GetDC(hwnd)
    mem = H.gdi32.CreateCompatibleDC(hdc)
    bmp = H.gdi32.CreateCompatibleBitmap(hdc, w, h)
    H.gdi32.SelectObject(mem, bmp)
    try:
        if not H.user32.PrintWindow(hwnd, mem, 3):
            ox, oy = H.client_origin(hwnd)
            src = H.user32.GetDC(0)
            H.gdi32.BitBlt(mem, 0, 0, w, h, src, ox, oy, 0x00CC0020)
            H.user32.ReleaseDC(0, src)
        return cv2.cvtColor(np.asarray(H._bits_to_image(mem, bmp, w, h)), cv2.COLOR_RGB2BGR)
    finally:
        H.gdi32.DeleteObject(bmp)
        H.gdi32.DeleteDC(mem)
        H.user32.ReleaseDC(hwnd, hdc)


def find_checkbox(img: np.ndarray) -> dict | None:
    """阈值 180 找空心方框：22–50 px、近方形、fill 0.08–0.5、周边偏亮。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    n, _, stats, cents = cv2.connectedComponentsWithStats(th, connectivity=4)
    hh, ww = gray.shape
    cands = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (22 <= w <= 50 and 22 <= h <= 50):
            continue
        if not (0.72 <= w / h <= 1.4):
            continue
        fill = area / (w * h)
        if not (0.08 <= fill <= 0.5):
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1])
        if not (0.12 * ww <= cx <= 0.72 * ww and 0.12 * hh <= cy <= 0.88 * hh):
            continue
        x0, y0 = max(0, x - 12), max(0, y - 12)
        x1, y1 = min(ww, x + w + 12), min(hh, y + h + 12)
        bright = float((gray[y0:y1, x0:x1] > 200).mean())
        if bright < 0.55:
            continue
        cands.append({"box": [x, y, w, h], "fill": round(float(fill), 2),
                      "cx": cx, "cy": cy, "bright": round(bright, 2)})
    if not cands:
        return None
    cands.sort(key=lambda c: -c["bright"])
    return cands[0]


def find_challenge_window() -> dict | None:
    for key in ("请稍候", "moment", "Verify", "验证", "robot", "security"):
        w = H.find_window(key)
        if w:
            return w
    return None


def click_turnstile(hwnd, timeout: float = 30.0, max_clicks: int = 2,
                    log=print) -> bool:
    """等复选框出现（连续两帧一致）→ 点击 → 等标题脱离挑战。最多 max_clicks 次。"""
    clicks = 0
    t0 = time.time()
    prev = None
    # 前提：交互式桌面。锁屏 / 断开的 RDP 会话里 GetForegroundWindow()==0，
    #   此时 SendInput 的按键事件会被拒绝（ERROR_ACCESS_DENIED=5），不必空等。
    if H.user32.GetForegroundWindow() == 0:
        log("[turnstile] no interactive desktop (fg=0); host click unavailable")
        return False
    while time.time() - t0 < timeout:
        if not is_challenge(H.window_title(hwnd)):
            return True
        try:
            img = grab(hwnd)
        except Exception:
            time.sleep(1.0)
            continue
        cb = find_checkbox(img)
        if cb and prev and all(abs(a - b) <= 5 for a, b in zip(cb["box"], prev)):
            if clicks >= max_clicks:
                return False
            ox, oy = H.client_origin(hwnd)
            x, y = int(cb["cx"]) + ox, int(cb["cy"]) + oy
            log(f"[turnstile] click {clicks + 1}/{max_clicks} at ({x},{y}) box={cb['box']}")
            # 多窗口并行时，前台窗口可能已被别的线程抢走；SendInput 会返回
            # ERROR_ACCESS_DENIED(5)。点前重新置前，并对 5 号错误重试。
            sent = False
            for attempt in range(3):
                try:
                    if not H.activate(hwnd):
                        # 后台进程常被前台锁挡住：点标题栏激活，再点复选框
                        H.focus_by_titlebar(hwnd)
                    time.sleep(0.2)
                    H.raise_window(hwnd)
                    H.click_at(x, y)
                    sent = True
                    break
                except OSError as e:
                    log(f"[turnstile] SendInput failed ({e}); retry {attempt + 1}/3")
                    time.sleep(0.6)
            if not sent:
                return False
            clicks += 1
            t1 = time.time()
            while time.time() - t1 < 25:
                time.sleep(1.0)
                if not is_challenge(H.window_title(hwnd)):
                    return True
            prev = None
            continue
        prev = cb["box"] if cb else None
        time.sleep(1.0)
    return not is_challenge(H.window_title(hwnd))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default=None)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    H.set_dpi_aware()
    if args.keyword:
        win = H.find_window(args.keyword)
    else:
        win = find_challenge_window()
    if not win:
        print("challenge window not found")
        return 2
    hwnd = win["hwnd"]
    H.activate(hwnd)
    print("window:", win["title"])
    if args.dry:
        img = grab(hwnd)
        cb = find_checkbox(img)
        print("checkbox:", cb)
        return 0 if cb else 3
    ok = click_turnstile(hwnd, args.timeout, log=print)
    print("passed:", ok, "| title:", H.window_title(hwnd))
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
