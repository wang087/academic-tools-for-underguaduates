# -*- coding: utf-8 -*-
"""在挑战页上用宿主机 SendInput 点 Cloudflare 复选框（仓库内维护版）。

与旧的外部 solver 的差别：
  1. 锚点用 OCR 文本**去掉空格后**匹配（OCR 常把「请验证您是真人」拆成
     「请 验 证 您 是 真 人」，精确子串会永远失配）；
  2. 复选框只在**锚点左侧**找，不再全图猜方框；
  3. 按 pid 锁定浏览器窗口，多实例并行时不会点到别的窗口。

约束（SKILL §5.2）：每篇最多 3 轮点击；失败即停；只点复选框本身。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import pathlib
import re
import subprocess
import sys
import time
from ctypes import wintypes

import cv2
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import host_click as H  # noqa: E402

OCR = HERE / "ocr.ps1"

CHALLENGE_WORDS = ("just a moment", "请稍候", "attention required", "are you a robot",
                   "performing security verification", "checking your browser",
                   "verify you are human", "请验证", "验证您是真人",
                   "security verification", "request verification",
                   "verification in progress", "请求验证", "正在验证")


def is_challenge(title: str) -> bool:
    low = (title or "").lower()
    return any(k.lower() in low for k in CHALLENGE_WORDS)


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    H.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def find_window(pid: int = 0) -> dict | None:
    cands = []
    for w in H.list_windows():
        if w.get("cls") != "Chrome_WidgetWin_1":
            continue
        if pid and _window_pid(w["hwnd"]) != pid:
            continue
        title = w.get("title") or ""
        if title and is_challenge(title):
            cands.append(w)
    return cands[0] if cands else None


def grab(hwnd) -> tuple[np.ndarray, tuple[int, int]]:
    """屏幕实拍窗口矩形，返回 (img, 窗口左上角屏幕坐标)。

    不用 PrintWindow：它返回的图像尺寸与屏幕坐标不是 1:1（含边框/DPI 缩放），
    照图点会纵向偏下。BitBlt 从屏幕 DC 抓**窗口实际矩形**，图像像素与屏幕
    坐标严格一一对应；调用前先把窗口恢复到前台固定位置。
    """
    # 只在最小化/完全在屏幕外时恢复，**绝不改变窗口尺寸**：
    #   125% DPI 下 SetWindowPos 的逻辑尺寸与物理像素不一致，会让
    #   “截图时的布局”和“点击时的布局”错位（实测偏下 70–90 px）。
    if H.user32.IsIconic(hwnd):
        H.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    rect = wintypes.RECT()
    H.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right < 0 or rect.bottom < 0 or rect.left < -10000:
        w, h = rect.right - rect.left, rect.bottom - rect.top
        H.user32.SetWindowPos(hwnd, -1, 40, 40, w, h, 0x0040)
    try:
        # 只抬 z 序，不再调用 activate：它的 SW_RESTORE 会把窗口挪回旧位置。
        H.raise_window(hwnd)
    except Exception:
        pass
    time.sleep(0.3)
    rect = wintypes.RECT()
    H.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = int(rect.left), int(rect.top)
    w, h = max(1, int(rect.right - rect.left)), max(1, int(rect.bottom - rect.top))
    src = H.user32.GetDC(0)
    mem = H.gdi32.CreateCompatibleDC(src)
    bmp = H.gdi32.CreateCompatibleBitmap(src, w, h)
    H.gdi32.SelectObject(mem, bmp)
    try:
        ok = H.gdi32.BitBlt(mem, 0, 0, w, h, src, left, top, 0x00CC0020)
        if not ok:
            # 屏幕捕获失败（锁屏/安全桌面）时退回 PrintWindow，但此时调用方
            #   不应再按图像坐标点击。
            H.user32.PrintWindow(hwnd, mem, 3)
        return cv2.cvtColor(np.asarray(H._bits_to_image(mem, bmp, w, h)), cv2.COLOR_RGB2BGR), (left, top)
    finally:
        H.gdi32.DeleteObject(bmp)
        H.gdi32.DeleteDC(mem)
        H.user32.ReleaseDC(0, src)


def do_ocr(png: pathlib.Path, should_stop=None) -> dict:
    lines, seen = [], set()
    width = height = 0
    for lang in ("zh-CN", "en-US"):
        if should_stop and should_stop():
            break
        try:
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(OCR), "-Path", str(png), "-Lang", lang],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace")
            chunks = []
            # 必须持续排空 stdout：输出超过管道缓冲区时 PowerShell 会阻塞，
            #   只在进程退出后 read() 会一直等到超时。
            import threading
            reader = threading.Thread(
                target=lambda: chunks.append(proc.stdout.read() if proc.stdout else ""),
                daemon=True)
            reader.start()
            deadline = time.time() + 120
            while proc.poll() is None:
                if (should_stop and should_stop()) or time.time() > deadline:
                    proc.kill()
                    break
                time.sleep(0.2)
            reader.join(2)
            out = "".join(chunks)
        except Exception:
            continue
        for line in (out or "").splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            width = width or d.get("width", 0)
            height = height or d.get("height", 0)
            for ln in d.get("lines", []):
                key = re.sub(r"\s+", "", ln.get("text", "")).lower()
                if key and key not in seen:
                    seen.add(key)
                    lines.append(ln)
    return {"width": width, "height": height, "lines": lines}


def locate_anchor(data: dict) -> dict | None:
    def box_of(ln):
        ws = ln.get("words") or []
        if not ws:
            return None
        x0 = min(w["x"] for w in ws)
        y0 = min(w["y"] for w in ws)
        x1 = max(w["x"] + w["w"] for w in ws)
        y1 = max(w["y"] + w["h"] for w in ws)
        return [x0, y0, x1 - x0, y1 - y0]

    for ln in data.get("lines", []):
        flat = re.sub(r"\s+", "", ln.get("text", "")).lower()
        if (("verify" in flat and "human" in flat)
                or "请验证" in flat or "验证您是真人" in flat
                or "驗證您是真人" in flat):
            b = box_of(ln)
            if b:
                return {"text": ln["text"], "box": b, "kind": "label"}
    return None


def find_checkbox(img: np.ndarray, anchor: dict | None) -> dict | None:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    n, _, stats, cents = cv2.connectedComponentsWithStats(th, connectivity=4)
    hh, ww = gray.shape
    cands = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (20 <= w <= 50 and 20 <= h <= 50):
            continue
        if not (0.7 <= w / h <= 1.45):
            continue
        fill = area / (w * h)
        if not (0.06 <= fill <= 0.55):
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1])
        if anchor:
            ax, ay, aw, ah = anchor["box"]
            # 复选框可能被 OCR 并入锚点行（首字「囗」就是框），
            #   所以搜索区要包含锚点起点附近，而不是只看它左侧。
            if not (ax - 320 <= cx <= ax + 60 and ay - 45 <= cy <= ay + ah + 45):
                continue
        else:
            if not (0.03 * ww <= cx <= 0.7 * ww and 0.3 * hh <= cy <= 0.9 * hh):
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
    # 有锚点时取离标签最近（同一行）的方框；否则取最亮者
    if anchor:
        ax, ay, aw, ah = anchor["box"]
        cands.sort(key=lambda c: (abs(c["cy"] - (ay + ah / 2)), abs(ax - c["cx"])))
    else:
        cands.sort(key=lambda c: -c["bright"])
    return cands[0]


def click_checkbox(hwnd, cb: dict, origin: tuple[int, int],
                   expected_size: tuple[int, int] | None = None) -> tuple[int, int]:
    # 只抬 z 序，**不能再 SW_RESTORE/移动窗口**：SW_RESTORE 会把窗口恢复到
    #   旧位置，而坐标仍按截图时的 origin 计算 -> 点到别处（实测偏下）。
    #   点击本身会让窗口获得焦点，无需再 activate。
    H.raise_window(hwnd)
    time.sleep(0.15)
    rect = wintypes.RECT()
    H.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = int(rect.left), int(rect.top)
    if expected_size and (int(rect.right - rect.left), int(rect.bottom - rect.top)) != tuple(expected_size):
        raise OSError(f"window-size-changed {expected_size} -> "
                      f"{(int(rect.right - rect.left), int(rect.bottom - rect.top))}")
    x, y = int(cb["cx"]) + left, int(cb["cy"]) + top
    # [STOP] 不要用 SendInput 的 ABSOLUTE 归一化坐标：125% DPI 下会被再乘 1.25，
    #   实测目标 (268,477) 实际落到 (336,592)。SetCursorPos 直接用屏幕物理
    #   坐标定位，再发**相对(0,0)**的按下/抬起，坐标不再经过归一化。
    H.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    for _ in range(3):
        try:
            H.raise_window(hwnd)
            H.user32.SetCursorPos(int(x), int(y))
            time.sleep(0.12)
            H._send_mouse(H.MOUSEEVENTF_LEFTDOWN, 0, 0)
            time.sleep(0.08)
            H._send_mouse(H.MOUSEEVENTF_LEFTUP, 0, 0)
            return x, y
        except OSError:
            time.sleep(0.5)
    raise OSError("SendInput failed")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--wait", type=int, default=40)
    ap.add_argument("--pid", type=int, default=0)
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"rounds": [], "passed": False}
    clicks = 0
    t_start = time.time()
    for r in range(args.rounds):
        win = None
        for _ in range(20):
            win = find_window(args.pid)
            if win:
                break
            time.sleep(1)
        if not win:
            report["note"] = "no challenge window"
            break
        # PrintWindow 在后台窗口上可能只给空白；先置前再截图。
        try:
            H.raise_window(win["hwnd"])
            H.focus_by_titlebar(win["hwnd"])
            time.sleep(0.4)
        except Exception:
            pass
        img, origin = grab(win["hwnd"])
        png = out / f"before_r{r}.png"
        # OpenCV 的 imwrite 在中文路径上会静默失败（返回 False 不抛错），
        #   随后 OCR 拿到不存在的文件 -> NullReference；这里统一用 PIL 落盘。
        from PIL import Image
        Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(png)
        anchor = locate_anchor(do_ocr(png))
        cb = find_checkbox(img, anchor)
        rec = {"tag": f"r{r}", "title0": win["title"], "anchor": anchor,
               "checkbox": cb, "img": img.shape[:2], "origin": origin}
        report["rounds"].append(rec)
        if not cb:
            time.sleep(3)
            continue
        if clicks >= 3:
            rec["note"] = "click cap reached"
            break
        if not ctypes.windll.user32.GetForegroundWindow():
            # 定位已完成，但当前进程没有输入桌面：把结论落盘而不是抛异常，
            #   便于区分“没找到复选框”和“系统不允许注入鼠标”。
            rec["click_error"] = "no-input-desktop (GetForegroundWindow=0)"
            report["clicks"] = clicks
            report["seconds"] = round(time.time() - t_start, 1)
            (out / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps({"passed": False, "clicks": clicks,
                              "anchor": anchor, "checkbox": cb,
                              "error": rec["click_error"]}, ensure_ascii=False))
            return 1
        try:
            rec["click"] = click_checkbox(win["hwnd"], cb, origin,
                                          (img.shape[1], img.shape[0]))
        except OSError as e:
            rec["click_error"] = str(e)
            break
        clicks += 1
        # 点击后回读复选框像素：勾选/验证中会明显变暗。
        #   这比“只看标题”可靠——标题变化也可能来自用户手动操作。
        try:
            time.sleep(2)
            img2, _ = grab(win["hwnd"])
            png2 = out / f"after_r{r}.png"
            from PIL import Image as _Img
            _Img.fromarray(cv2.cvtColor(img2, cv2.COLOR_BGR2RGB)).save(png2)
            bx, by, bw, bh = cb["box"]
            crop = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)[by:by + bh, bx:bx + bw]
            rec["after_dark"] = round(float((crop < 120).mean()), 3) if crop.size else None
            rec["checkbox_checked"] = bool(rec.get("after_dark", 0) > 0.42)
        except Exception as e:
            rec["after_error"] = f"{type(e).__name__}: {e}"
        t0 = time.time()
        while time.time() - t0 < args.wait:
            time.sleep(2)
            title = H.window_title(win["hwnd"]) or ""
            if title and not is_challenge(title):
                report["passed"] = True
                report["title"] = title
                break
        if report["passed"]:
            break
    report["clicks"] = clicks
    report["seconds"] = round(time.time() - t_start, 1)
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "clicks": clicks,
                      "anchor": report["rounds"][-1]["anchor"] if report["rounds"] else None},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
