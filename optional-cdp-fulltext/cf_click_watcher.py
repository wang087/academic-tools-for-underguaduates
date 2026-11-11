# -*- coding: utf-8 -*-
"""常驻自动点击器：在**你自己的桌面会话**里运行，持续处理 Cloudflare 验证码。

为什么需要单独运行：
    agent 的 Bash/计划任务进程虽然也在 session 1，但 `GetForegroundWindow()=0`、
    `SendInput` 返回 err=5（无输入桌面），无法向屏幕注入鼠标。
    这个脚本由你在本机双击/命令行启动后，才拥有交互桌面权限。

用法（在 流程1 目录）：
    .venv\\Scripts\\python.exe optional-cdp-fulltext\\cf_click_watcher.py
    ... --max-minutes 30      # 最多运行 30 分钟后自动退出
    ... --once                # 只扫描一轮就退出（自检用）
或双击：
    optional-cdp-fulltext\\启动自动点击.bat

退出机制：
    · Ctrl+C / 关闭窗口 / taskkill（SIGTERM）→ signal 处理器置停止标志，
      最多一个扫描间隔内退出；
    · 单实例锁 .work_cdp/cf_watcher.pid：重复启动会直接退出，不会两个进程抢着点；
    · 所有等待都是 stop.wait()，不会卡在 sleep 里；
    · 退出时删除自己的锁文件；残留锁（进程已死）会被自动接管。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import signal
import sys
import threading
import time
from ctypes import wintypes

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import host_click as H  # noqa: E402
import cf_host_solver as S  # noqa: E402

STOP = threading.Event()
LOCK: pathlib.Path | None = None


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        wd = pathlib.Path(os.environ.get("PAPER_WORKDIR") or HERE.parent)
        d = wd / ".work_cdp" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "cf_watcher.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def acquire_lock() -> bool:
    global LOCK
    wd = pathlib.Path(os.environ.get("PAPER_WORKDIR") or HERE.parent)
    LOCK = wd / ".work_cdp" / "cf_watcher.pid"
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            log(f"已有 watcher 在运行（pid={old}），本进程退出。")
            return False
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if LOCK and LOCK.exists() and LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK.unlink()
    except Exception:
        pass


def _on_signal(signum, _frame):
    log(f"收到信号 {signum}，准备退出…")
    STOP.set()


def scan_once(last: dict) -> None:
    for w in H.list_windows():
        if STOP.is_set():
            return
        if w.get("cls") != "Chrome_WidgetWin_1":
            continue
        title = w.get("title") or ""
        if not S.is_challenge(title):
            last.pop(w["hwnd"], None)
            continue
        st = last.setdefault(w["hwnd"], [0.0, 0])
        if time.time() - st[0] < 25 or st[1] >= 3:
            continue
        try:
            H.raise_window(w["hwnd"])
            H.focus_by_titlebar(w["hwnd"])
            if STOP.wait(0.3):
                return
            img, origin = S.grab(w["hwnd"])
            png = (pathlib.Path(os.environ.get("PAPER_WORKDIR") or HERE.parent)
                   / ".work_cdp" / "logs" / "cf_watcher_shot.png")
            png.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image
            import cv2
            Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(png)
            anchor = S.locate_anchor(S.do_ocr(png, lambda: STOP.is_set()))
            if STOP.is_set():
                return
            cb = S.find_checkbox(img, anchor)
            if not cb:
                log(f"挑战窗口已识别，但未找到复选框（anchor={'有' if anchor else '无'}）")
                st[0] = time.time()
                continue
            x, y = S.click_checkbox(w["hwnd"], cb, origin, (img.shape[1], img.shape[0]))
            st[0] = time.time()
            st[1] += 1
            log(f"已点击复选框 ({x},{y})，第 {st[1]}/3 次；等待页面响应…")
        except OSError as e:
            log(f"SendInput 失败：{e}（若反复出现，请确认在交互桌面运行）")
            st[0] = time.time()
        except Exception as e:
            log(f"处理失败：{type(e).__name__}: {e}")
            st[0] = time.time()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0, help="扫描间隔秒")
    ap.add_argument("--max-minutes", type=float, default=0, help="0=不限；到时自动退出")
    ap.add_argument("--once", action="store_true", help="只扫描一轮")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    try:
        signal.signal(signal.SIGBREAK, _on_signal)
    except Exception:
        pass

    if not acquire_lock():
        return 0
    try:
        if not ctypes.windll.user32.GetForegroundWindow():
            log("警告：当前进程没有输入桌面（GetForegroundWindow=0）。"
                "请在本机桌面的 PowerShell/cmd 里启动，而不是通过远程 agent。")
        log("自动点击器已启动；等待 Cloudflare 挑战窗口…（Ctrl+C 停止）")
        last = {}
        deadline = (time.time() + args.max_minutes * 60) if args.max_minutes > 0 else None
        while not STOP.is_set():
            scan_once(last)
            if args.once:
                break
            if deadline and time.time() >= deadline:
                log("已到 --max-minutes 上限，自动退出。")
                break
            STOP.wait(max(0.2, args.interval))
        log("已停止。")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
