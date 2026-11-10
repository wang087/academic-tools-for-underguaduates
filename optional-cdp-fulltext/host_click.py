# -*- coding: utf-8 -*-
"""
宿主机级（OS 级）点击脚本 —— 参考 BetterGI 的分层实现

BetterGI 的做法（本脚本的对应关系）：
  ┌ BetterGI                        ┌ 本脚本
  │ GameCapture（Windows.Graphics）  │ PrintWindow(PW_RENDERFULLCONTENT) / BitBlt 抓窗口
  │ OpenCv 模板匹配 / PaddleOCR      │ Windows.Media.Ocr + 像素连通域（24×24 复选框）
  │ GameWindow（找窗口/置前/DPI）     │ EnumWindows + SetForegroundWindow + PerMonitor DPI
  │ SendInput / Interception 注入     │ user32!SendInput（绝对坐标 + 缓动路径）
  └ 不依赖游戏/浏览器内部接口          └ 不依赖 CDP，浏览器无需开调试端口

要点：
  1. 只碰「窗口 + 屏幕像素 + 系统输入队列」，不碰浏览器 DOM/CDP；
  2. 抓的是**客户区物理像素**，坐标经 ClientToScreen 换成屏幕坐标，
     再用 SendInput 的 ABSOLUTE|VIRTUALDESK 归一化到 0..65535；
  3. 输入走 Windows 输入管线（与真实鼠标同一入口），因此页面看到的是
     正常 mouse 事件（带移动轨迹），而不是 DevTools 注入事件。

用法：
  python host_click.py --url=https://doi.org/10.1016/j.molcel.2022.04.016 --launch
  python host_click.py --title-keyword="Just a moment" --dry
  python host_click.py --title-keyword="Just a moment" --clicks=1
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import pathlib
import random
import subprocess
import sys
import time
from ctypes import wintypes

import numpy as np
from PIL import Image

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = pathlib.Path(__file__).resolve().parent
OCR = HERE.parent / "clicktest" / "ocr.ps1"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

def _declare_prototypes():
    """显式声明原型：64 位下 HWND/HDC 是 8 字节，不声明会被截断成 int32。"""
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    for _fn, _args in (
            ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
            ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
            ("GetWindowTextLengthW", [wintypes.HWND]),
            ("IsWindowVisible", [wintypes.HWND]),
            ("ShowWindow", [wintypes.HWND, ctypes.c_int]),
            ("SetForegroundWindow", [wintypes.HWND]),
            ("BringWindowToTop", [wintypes.HWND]),
            ("SetFocus", [wintypes.HWND]),
            ("GetForegroundWindow", []),
            ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
            ("AttachThreadInput", [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]),
            ("ClientToScreen", [wintypes.HWND, ctypes.POINTER(POINT)]),
            ("GetClientRect", [wintypes.HWND, ctypes.POINTER(RECT)]),
            ("GetDC", [wintypes.HWND]),
            ("ReleaseDC", [wintypes.HWND, wintypes.HDC]),
            ("PrintWindow", [wintypes.HWND, wintypes.HDC, wintypes.UINT]),
            ("GetCursorPos", [ctypes.POINTER(POINT)]),
            ("GetSystemMetrics", [ctypes.c_int]),
            ("EnumWindows", [ctypes.c_void_p, wintypes.LPARAM]),
            ("keybd_event", [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]),
        ("SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.UINT]),
        ("SwitchToThisWindow", [wintypes.HWND, wintypes.BOOL]),
    ):
        fn = getattr(user32, _fn)
        fn.argtypes = _args
        if _fn in ("GetWindowTextLengthW", "IsWindowVisible", "GetForegroundWindow",
                   "GetSystemMetrics"):
            fn.restype = ctypes.c_int
        elif _fn == "GetDC":
            fn.restype = wintypes.HDC
        elif _fn in ("PrintWindow", "ShowWindow", "SetForegroundWindow", "BringWindowToTop",
                     "SetFocus", "AttachThreadInput", "ClientToScreen", "GetClientRect",
                     "GetCursorPos", "SetWindowPos"):
            fn.restype = wintypes.BOOL
        elif _fn == "GetWindowThreadProcessId":
            fn.restype = wintypes.DWORD

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# ── DPI：必须在任何窗口坐标计算之前 ────────────────────────────
def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0

_declare_prototypes()


def _send_mouse(flags: int, x: int = 0, y: int = 0) -> None:
    mi = MOUSEINPUT(x, y, 0, flags, 0, None)
    inp = INPUT(INPUT_MOUSE, _INPUTUNION(mi=mi))
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        raise OSError(f"SendInput 失败 err={ctypes.get_last_error()}")


def to_absolute(x: int, y: int) -> tuple[int, int]:
    """屏幕物理像素 → SendInput 绝对坐标（虚拟桌面 0..65535）。"""
    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    return nx, ny


# ── 窗口 ──────────────────────────────────────────────────────
def list_windows() -> list[dict]:
    out: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        out.append({"hwnd": hwnd, "title": buf.value, "cls": cls.value})
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(keyword: str, cls: str = "Chrome_WidgetWin_1") -> dict | None:
    cands = [w for w in list_windows()
             if w["cls"] == cls and keyword.lower() in w["title"].lower()]
    # 优先标题里带文章/浏览器的；避免选到 DevTools
    cands.sort(key=lambda w: ("devtools" in w["title"].lower(), len(w["title"])))
    return cands[0] if cands else None


def raise_window(hwnd) -> None:
    """把窗口抬到 z 序最上层（不改焦点）。后台进程被前台锁挡住时，靠它保证
    点击坐标落在目标窗口上；点击本身会触发窗口激活。"""
    try:
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # TOPMOST|NOSIZE|NOMOVE
    except Exception:
        pass


def focus_by_titlebar(hwnd) -> bool:
    """点一下标题栏让窗口获得焦点（后台进程不能用 SetForegroundWindow 时）。

    返回是否已成为前台窗口。
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    raise_window(hwnd)
    ox, oy = client_origin(hwnd)
    tx, ty = ox + 240, max(4, oy - 12)
    try:
        move_to(tx, ty, steps=4, jitter=0.5)
        time.sleep(0.05)
        nx, ny = to_absolute(tx, ty)
        _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
        time.sleep(0.04)
        _send_mouse(MOUSEEVENTF_LEFTDOWN, nx, ny)
        time.sleep(0.07)
        _send_mouse(MOUSEEVENTF_LEFTUP, nx, ny)
    except OSError:
        return False
    time.sleep(0.35)
    return user32.GetForegroundWindow() == hwnd


def activate(hwnd) -> bool:
    """把窗口置前并**核实**它真的成了前台窗口。

    多窗口并行时 SetForegroundWindow 经常被前台锁静默拒绝；不核实就点击，
    坐标会落到最上层那个窗口上（实测：4 窗口重叠时点击几乎全错）。
    做法：置顶 + AttachThreadInput 解锁 + 每轮 0.2s 后复核，最多 4 轮。
    """
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    for _ in range(4):
        if user32.GetForegroundWindow() == hwnd:
            time.sleep(0.15)
            return True
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass
        try:
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # TOPMOST
        except Exception:
            pass
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(tid_me, tid_fg, True)
        try:
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetFocus(hwnd)
        finally:
            user32.AttachThreadInput(tid_me, tid_fg, False)
        time.sleep(0.2)
    return user32.GetForegroundWindow() == hwnd


def client_origin(hwnd) -> tuple[int, int]:
    pt = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_size(hwnd) -> tuple[int, int]:
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


# ── 抓图（BetterGI 的 GameCapture 位置）───────────────────────
def _bits_to_image(memdc, bmp, w: int, h: int) -> Image.Image:
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负值 = top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    if got != h:
        raise OSError(f"GetDIBits 失败 got={got}")
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
    return Image.fromarray(arr[:, :, [2, 1, 0]])   # BGRA → RGB


def capture_client(hwnd, out_png: pathlib.Path, mode: str = "auto") -> dict:
    w, h = client_size(hwnd)
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    used = ""
    try:
        if mode in ("auto", "print"):
            ok = user32.PrintWindow(hwnd, mem, 3)   # PW_CLIENTONLY|PW_RENDERFULLCONTENT
            if ok:
                img = _bits_to_image(mem, bmp, w, h)
                used = "PrintWindow"
            else:
                img = None
        else:
            img = None
        if img is None or mode == "bitblt":
            ox, oy = client_origin(hwnd)
            srcdc = user32.GetDC(0)
            gdi32.BitBlt(mem, 0, 0, w, h, srcdc, ox, oy, 0x00CC0020)
            user32.ReleaseDC(0, srcdc)
            img = _bits_to_image(mem, bmp, w, h)
            used = "BitBlt"
        # 全黑判定：GPU 合成窗口 PrintWindow 可能返回黑图 → 退回屏幕抓取
        if img.getbbox() is None or np.asarray(img).mean() < 8:
            ox, oy = client_origin(hwnd)
            srcdc = user32.GetDC(0)
            gdi32.BitBlt(mem, 0, 0, w, h, srcdc, ox, oy, 0x00CC0020)
            user32.ReleaseDC(0, srcdc)
            img = _bits_to_image(mem, bmp, w, h)
            used = "BitBlt(fallback)"
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_png)
        return {"file": out_png.name, "w": w, "h": h, "mode": used,
                "mean": round(float(np.asarray(img).mean()), 1)}
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


# ── OCR / 识别 ────────────────────────────────────────────────
def ocr(png: pathlib.Path, lang: str = "en-US") -> dict:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(OCR), "-Path", str(png), "-Lang", lang],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip().startswith("{")]
    if not lines:
        return {"width": 0, "height": 0, "lines": [], "err": r.stderr[-300:]}
    d = json.loads(lines[-1])
    d["lang"] = lang
    return d


def ocr_best(png: pathlib.Path) -> dict:
    """中英各跑一次，取行数多的（挑战页会随 Cloudflare 语言切换中/英文）。"""
    best = None
    for lang in ("zh-CN", "en-US"):
        try:
            d = ocr(png, lang)
        except Exception:
            continue
        d["lang"] = lang
        if best is None or len(d.get("lines", [])) > len(best.get("lines", [])):
            best = d
    return best or {"width": 0, "height": 0, "lines": []}


def word_box(line: dict) -> dict:
    ws = line.get("words") or []
    if not ws:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    return {"x": min(w["x"] for w in ws), "y": min(w["y"] for w in ws),
            "w": max(w["x"] + w["w"] for w in ws) - min(w["x"] for w in ws),
            "h": max(w["y"] + w["h"] for w in ws) - min(w["y"] for w in ws)}


def find_line(data: dict, needle: str) -> dict | None:
    for ln in data.get("lines", []):
        if needle.lower() in ln["text"].lower():
            return {"text": ln["text"], "box": word_box(ln)}
    return None


def find_checkbox_full(img_path: pathlib.Path, y_min: int = 0, y_max: int | None = None,
                       x_min: int = 0, x_max: int | None = None) -> dict | None:
    """全图找空心方框（不依赖 OCR 锚点）。

    挑战页的 "Are you a robot?" 标题与复选框常常相隔 150–400px，
    先前「锚点左侧 ±60px」的区域假设会漏掉它 —— 这里改成整图连通域 +
    形状/位置打分：
      · 24–40px、近正方形、空心（fill≈0.3）
      · 优先内容列（x≈窗口 40%）、排除顶部书签栏与底部状态栏
    """
    arr = np.asarray(Image.open(img_path).convert("L"))
    H, W = arr.shape
    y_max = H if y_max is None else min(H, y_max)
    x_max = W if x_max is None else min(W, x_max)
    sub = arr[y_min:y_max, x_min:x_max]
    mask = sub < 248
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    from collections import deque
    cands = []
    for yy in range(h):
        for xx in range(w):
            if not mask[yy, xx] or seen[yy, xx]:
                continue
            q = deque([(yy, xx)])
            seen[yy, xx] = True
            minx = maxx = xx
            miny = maxy = yy
            n = 0
            while q:
                cy, cx = q.popleft()
                n += 1
                minx = min(minx, cx); maxx = max(maxx, cx)
                miny = min(miny, cy); maxy = max(maxy, cy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            cw, ch = maxx - minx + 1, maxy - miny + 1
            if 18 <= cw <= 64 and 18 <= ch <= 64 and 0.72 <= cw / ch <= 1.38:
                fill = n / (cw * ch)
                if 0.06 <= fill <= 0.72:
                    cands.append({"box": [x_min + minx, y_min + miny, cw, ch],
                                  "fill": round(float(fill), 3)})

    def score(c):
        x, y, cw, ch = c["box"]
        cx, cy = x + cw / 2, y + ch / 2
        s = 0.0
        s -= abs(cx - W * 0.42) / W * 1.2
        s -= max(0, cy - H * 0.85) / H * 2
        s -= max(0, 120 - cy) / H * 2
        s += 0.6 - abs(c["fill"] - 0.3)
        s += min(cw, ch) / 64 * 0.3
        return s

    if not cands:
        return None
    best = max(cands, key=score)
    best["score"] = round(score(best), 3)
    best["candidates"] = len(cands)
    best["method"] = "full-scan"
    return best


def _close_box(a, b, tol: int = 5) -> bool:
    return all(abs(a[i] - b[i]) <= tol for i in range(4))


def wait_stable_checkbox(hwnd, outdir: pathlib.Path, attempts: int = 24,
                         interval: float = 1.2, stable_gap: float = 0.7) -> tuple[dict | None, list]:
    """等到复选框**连续两帧**出现在同一位置再返回。

    挑战页的 Turnstile 会在「折叠 → 重新渲染 → 再折叠」之间反复切换
    （Cloudflare 自身校验/重置），单帧识别很容易正好撞上消失窗口。
    做法：每隔 interval 抓一帧，找到候选后 0.7s 再抓一帧复核；
    两帧框位置一致（±5px）才算稳定。
    """
    trace = []
    for i in range(attempts):
        p1 = outdir / f"wait_{i:02d}_a.png"
        capture_client(hwnd, p1)
        cb1 = find_checkbox_full(p1)
        rec = {"i": i, "found": bool(cb1), "box": cb1["box"] if cb1 else None,
               "score": cb1.get("score") if cb1 else None}
        if cb1:
            time.sleep(stable_gap)
            p2 = outdir / f"wait_{i:02d}_b.png"
            capture_client(hwnd, p2)
            cb2 = find_checkbox_full(p2)
            rec["confirm"] = bool(cb2 and _close_box(cb1["box"], cb2["box"]))
            if rec["confirm"]:
                trace.append(rec)
                return cb2, trace
        trace.append(rec)
        time.sleep(interval)
    return None, trace


def find_square(img_path: pathlib.Path, region: tuple[int, int, int, int]) -> dict | None:
    arr = np.asarray(Image.open(img_path).convert("L"))
    x0, y0, x1, y1 = region
    sub = arr[y0:y1, x0:x1]
    mask = sub < 245
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    from collections import deque
    best = []
    for yy in range(h):
        for xx in range(w):
            if not mask[yy, xx] or seen[yy, xx]:
                continue
            q = deque([(yy, xx)])
            seen[yy, xx] = True
            minx = maxx = xx
            miny = maxy = yy
            n = 0
            while q:
                cy, cx = q.popleft()
                n += 1
                minx = min(minx, cx); maxx = max(maxx, cx)
                miny = min(miny, cy); maxy = max(maxy, cy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            cw, ch = maxx - minx + 1, maxy - miny + 1
            if 18 <= cw <= 60 and 18 <= ch <= 60 and 0.75 <= cw / ch <= 1.34:
                fill = n / (cw * ch)
                if fill < 0.72:
                    best.append({"box": [x0 + minx, y0 + miny, cw, ch],
                                 "fill": round(fill, 2)})
    best.sort(key=lambda b: (b["box"][1], b["box"][0]))
    return best[0] if best else None


# ── 鼠标：OS 级绝对移动 + 点击 ────────────────────────────────
def move_to(x: int, y: int, steps: int = 16, jitter: float = 1.5) -> None:
    sx, sy = _cursor()
    for i in range(1, steps + 1):
        t = i / steps
        e = 1 - (1 - t) ** 2.2
        x1 = int(sx + (x - sx) * e + random.uniform(-jitter, jitter) * (1 - t))
        y1 = int(sy + (y - sy) * e + random.uniform(-jitter, jitter) * (1 - t))
        nx, ny = to_absolute(x1, y1)
        _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
        time.sleep(random.uniform(0.012, 0.03))
    nx, ny = to_absolute(x, y)
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)


def _cursor() -> tuple[int, int]:
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def click_at(x: int, y: int) -> None:
    move_to(x, y)
    time.sleep(random.uniform(0.15, 0.28))
    nx, ny = to_absolute(x, y)
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
    time.sleep(random.uniform(0.05, 0.1))
    _send_mouse(MOUSEEVENTF_LEFTDOWN, nx, ny)
    time.sleep(random.uniform(0.07, 0.13))
    _send_mouse(MOUSEEVENTF_LEFTUP, nx, ny)


def window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


# ── 主流程 ────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://doi.org/10.1016/j.molcel.2022.04.016")
    ap.add_argument("--launch", action="store_true", help="用独立 profile 新开一个普通 Edge 窗口")
    ap.add_argument("--title-keyword", default="", help="窗口标题关键字（默认自动）")
    ap.add_argument("--out", default=str(HERE / "shots"))
    ap.add_argument("--clicks", type=int, default=1)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--settle", type=float, default=6.0, help="启动后等待页面渲染的秒数")
    args = ap.parse_args()
    set_dpi_aware()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rep: dict = {"url": args.url, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "screen": {"w": user32.GetSystemMetrics(0), "h": user32.GetSystemMetrics(1),
                            "dpi": user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else -1}}

    proc = None
    if args.launch:
        prof = HERE / "edge_profile"
        prof.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([EDGE, f"--user-data-dir={prof}", "--new-window",
                                 "--no-first-run", "--no-default-browser-check",
                                 "--window-size=1280,900", args.url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rep["edge_pid"] = proc.pid

    # 等窗口出现
    keywords = ([args.title_keyword] if args.title_keyword
                else ["请稍候", "验证", "moment", "sciencedirect", "nature"])
    win = None
    end = time.time() + 45
    while time.time() < end:
        for kw in keywords:
            win = find_window(kw)
            if win:
                break
        if win:
            break
        time.sleep(0.7)
    if not win:
        rep["error"] = "未找到浏览器窗口"
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 2
    rep["window"] = {"hwnd": int(win["hwnd"]), "title": win["title"], "cls": win["cls"]}
    activate(win["hwnd"])
    time.sleep(args.settle)

    rep["title_before"] = window_title(win["hwnd"])
    rep["shot1"] = capture_client(win["hwnd"], out / "01_window.png")
    d1 = ocr_best(out / "01_window.png")
    rep["ocr1"] = {"lines": len(d1.get("lines", [])), "lang": d1.get("lang"),
                   "sample": [ln["text"][:70] for ln in d1.get("lines", [])[:10]]}
    anchor = None
    for needle in ("Verify you are human", "验证您是人类", "验证你是人类", "Are you a robot",
                   "请完成安全验证", "人机验证", "Accept all", "Download PDF"):
        anchor = find_line(d1, needle)
        if anchor:
            anchor["needle"] = needle
            break
    rep["anchor"] = anchor

    if not anchor:
        rep["error"] = "OCR 未找到可点击锚点"
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 3

    cw, ch = client_size(win["hwnd"])
    s = cw / d1["width"] if d1.get("width") else 1.0
    rep["scale"] = round(s, 4)
    b = anchor["box"]
    if anchor["needle"] in ("Verify you are human", "验证您是人类", "验证你是人类",
                            "Are you a robot", "请完成安全验证", "人机验证"):
        region = (max(0, int(b["x"] - 180)), max(0, int(b["y"] - 60)),
                  int(b["x"] + 30), int(b["y"] + b["h"] + 60))
        sq = find_square(out / "01_window.png", region)
        rep["detect_method"] = "anchor-region"
        if not sq:
            # 锚点与复选框常隔 150–400px（"Are you a robot?" 标题在上、组件在下），
            #   区域假设会漏；改为在锚点下方的整条带里做全图式扫描。
            sq = find_checkbox_full(out / "01_window.png",
                                    y_min=max(0, b["y"] - 40), y_max=b["y"] + 560,
                                    x_min=max(0, b["x"] - 320), x_max=b["x"] + 380)
            rep["detect_method"] = "full-scan(anchor-band)"
        rep["checkbox_pixels"] = sq
        if not sq:
            rep["error"] = "像素分析未找到复选框"
            (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(rep, ensure_ascii=False, indent=1))
            return 4
        bx, by, bw, bh = sq["box"]
        cx = int((bx + bw / 2) * s)
        cy = int((by + bh / 2) * s)
        rep["target"] = {"kind": "checkbox", "box": sq["box"]}
    else:
        cx = int((b["x"] + b["w"] / 2) * s)
        cy = int((b["y"] + b["h"] / 2) * s)
        rep["target"] = {"kind": "text", "box": b}

    ox, oy = client_origin(win["hwnd"])
    rep["click_client"] = [cx, cy]
    rep["click_screen"] = [ox + cx, oy + cy]
    print(f"窗口 {rep['window']['title']!r} 客户区 {cw}x{ch} 抓图 {rep['shot1']['mode']}")
    print(f"锚点 {anchor['needle']!r} → 点击 客户区({cx},{cy}) 屏幕({ox+cx},{oy+cy})")

    if args.dry:
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0

    # 点击并观察
    t0 = time.time()
    rep["cursor_before"] = list(_cursor())
    click_at(ox + cx, oy + cy)
    rep["cursor_after"] = list(_cursor())
    rep["cursor_moved_to_target"] = (abs(rep["cursor_after"][0] - (ox + cx)) <= 2
                                     and abs(rep["cursor_after"][1] - (oy + cy)) <= 2)
    rep["clicked"] = True
    for wait in (3, 8, 15, 25, 40):
        time.sleep(max(0.5, wait - (time.time() - t0)))
        p = out / f"02_after_{wait}s.png"
        sh = capture_client(win["hwnd"], p)
        d = ocr_best(p)
        texts = [ln["text"] for ln in d.get("lines", [])]
        joined = " ".join(texts)
        ver = any(("verify you are human" in t.lower()) or ("验证您是人类" in t)
                  or ("验证你是人类" in t) for t in texts)
        art = any(k in joined for k in ("Abstract", "Download PDF", "Molecular Cell", "Nature"))
        rep[f"at_{wait}s"] = {"title": window_title(win["hwnd"]), "shot": sh,
                              "verify": ver, "article_like": art,
                              "lines": len(texts), "head": texts[:6]}
        print(f"[{wait}s] title={window_title(win['hwnd'])!r} verify={ver} article={art} lines={len(texts)}")
        if art and not ver:
            break

    rep["title_after"] = window_title(win["hwnd"])
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("window", "title_before", "anchor", "click_screen",
                                          "title_after") if k in rep},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
