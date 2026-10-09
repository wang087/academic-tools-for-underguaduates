# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════
# CDP 实例启动（本环境专用：附加 --no-sandbox）
#
# 【为什么需要这个文件】
#   实测本机环境下 Edge 的 **renderer 进程沙箱**创建失败：
#     - 浏览器主进程能起、DevTools 端口就绪、浏览器级 CDP 命令正常
#     - 但 renderer 一个都起不来（子进程只有 crashpad/gpu/utility）
#     - 于是所有页面级命令（Runtime.evaluate / 取正文）永远超时
#
#   逐项二分定位结果：
#     --no-sandbox                  -> renderer=8  ✅ 唯一有效
#     --disable-gpu-sandbox         -> renderer=0
#     --disable-crash-reporter      -> renderer=0
#     --disable-features=RendererCodeIntegrity -> renderer=0
#     creationflags / stdio 重定向 / uv run / profile -> 均无关
#
#   `cdp_pool.py` 没有「附加启动参数」的扩展点，且它是**已验证的成熟工具**，
#   不宜为单机环境硬编码开关。故本文件**只做启动**，不改动 cdp_pool：
#   它复用 cdp_pool 的档位表与探活逻辑，仅额外追加 --no-sandbox。
#
#   [STOP] 只应在「不加 --no-sandbox 时 renderer 起不来」的环境使用。
#          正常环境请直接用 `cdp_pool.py up`（沙箱是有价值的防线）。
#
# 用法：
#   python scripts/cdp_up_nosandbox.py --n=3
#   python scripts/cdp_up_nosandbox.py --n=3 --ports=9222,9223,9224
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import io
import pathlib
import subprocess
import sys
import time
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _fix_streams() -> None:
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

import cdp_pool  # noqa: E402  复用其档位表 / profile 规则 / 探活
from logutil import workdir as _workdir  # noqa: E402

# 本环境必需：renderer 沙箱创建失败，必须关闭
EXTRA_ARGS = ["--no-sandbox"]


def alive(port: int, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout).read()
        return True
    except Exception:
        return False


def renderer_count(port: int) -> int:
    """统计该实例的 renderer 子进程数 —— 判据：>0 才算真可用。"""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        import json
        procs = json.loads(ps.stdout or "[]")
    except Exception:
        return -1
    if isinstance(procs, dict):
        procs = [procs]
    main = None
    for q in procs:
        cl = q.get("CommandLine") or ""
        if f"remote-debugging-port={port}" in cl and "--type=" not in cl:
            main = q.get("ProcessId")
            break
    if main is None:
        return -1
    return sum(1 for q in procs
               if q.get("ParentProcessId") == main and "--type=renderer" in (q.get("CommandLine") or ""))


def launch(wd: pathlib.Path, port: int, level: int, wait: float = 30.0) -> bool:
    if alive(port):
        print(f"  [=] :{port} 已有存活实例，复用（renderer={renderer_count(port)}）")
        return True
    prof = cdp_pool.profile_for(wd, port)
    prof.mkdir(parents=True, exist_ok=True)
    args = cdp_pool.build_args(port, prof, level)          # 复用档位表
    # build_args 末尾是 about:blank，把附加参数插到它前面
    args = args[:-1] + EXTRA_ARGS + args[-1:]
    edge = cdp_pool.find_edge()

    logf = wd / ".work_cdp" / "logs"
    logf.mkdir(parents=True, exist_ok=True)
    fh = open(logf / f"edge_{port}.log", "a", encoding="utf-8", errors="replace")
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 8) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    subprocess.Popen([edge] + args, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags)
    print(f"  [>] :{port} 启动中 … profile={prof.name} 附加参数={EXTRA_ARGS}")

    end = time.time() + wait
    while time.time() < end:
        if alive(port):
            # 端口就绪 ≠ 可用：**必须**确认 renderer 起来了
            for _ in range(20):
                n = renderer_count(port)
                if n > 0:
                    print(f"  [OK] :{port} 就绪且 renderer={n}")
                    return True
                if n == 0 and time.time() > end:
                    break
                time.sleep(0.5)
            print(f"  [X] :{port} 端口就绪但 renderer 未起来")
            return False
        time.sleep(0.25)
    print(f"  [X] :{port} {wait:.0f}s 内未就绪，见 logs/edge_{port}.log")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="启动 CDP 实例（附加 --no-sandbox，本机环境专用）")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--ports", default=None, help="显式端口，如 9222,9223,9224")
    ap.add_argument("--level", type=int, default=cdp_pool.DEFAULT_LEVEL)
    a = ap.parse_args()

    wd = _workdir()
    ports = ([int(x) for x in a.ports.split(",") if x.strip()] if a.ports
             else [9222 + i for i in range(a.n)])

    print("=" * 68)
    print(f"启动 CDP 实例（附加 {EXTRA_ARGS}）| workdir={wd}")
    print("  原因：本机 Edge renderer 沙箱创建失败，不加则页面级命令永远超时")
    print("=" * 68)

    import threading
    ok: dict[int, bool] = {}

    def boot(p):
        try:
            ok[p] = launch(wd, p, a.level)
        except Exception as e:  # noqa: BLE001
            print(f"  [X] :{p} 异常 {type(e).__name__}: {e}")
            ok[p] = False

    ts = [threading.Thread(target=boot, args=(p,), daemon=True) for p in ports]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    good = sorted(p for p, v in ok.items() if v)
    print(f"\n可用实例 {len(good)} 个: {good}")
    for p in good:
        print(f"  :{p}  renderer={renderer_count(p)}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
