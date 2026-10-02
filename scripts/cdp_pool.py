# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# Edge CDP 实例池 —— 并行多实例启动 / 探活 / 调度 / 回收
#
# ★ 设计原则（按用户要求）：
#   1) **优先最少启动参数**：默认只用 3 个必需参数
#        --remote-debugging-port / --remote-allow-origins=* / --user-data-dir
#      已知教训：--disable-blink-features=AutomationControlled、
#      --excludeSwitches、--headless 等非默认参数本身就是可被检测的特征，
#      反而更容易被 Cloudflare 拦。参数越少越接近真实用户浏览器。
#   2) **不同实例用不同启动参数档位**（LEVELS），可在同一轮任务中并行对比
#      哪一档能通过某出版社的风控，而不必反复重启。
#   3) **一实例一 profile**：Edge 同 profile 无法起第二个调试端口，且
#      profile 隔离天然实现「Cookie 池」——一个实例被挑战时，其余不受影响。
#   4) 实例互相独立 → 单实例崩溃不影响整批任务（旧版单实例一崩全崩，
#      batch2.log 里 8 篇全部 WinError 10061 就是这个原因）。
#
# 用法：
#   python cdp_pool.py up --n=3              # 启动 3 个实例（9222-9224）
#   python cdp_pool.py status                # 查看实例状态
#   python cdp_pool.py down                  # 关闭本工作目录下的所有实例
#   python cdp_pool.py renew --port=9223     # 换全新 profile 重建单个实例
#
# 可移植配置：PAPER_WORKDIR（默认当前目录）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cdp import is_alive, list_tabs, resolve_port, version  # noqa: E402
from logutil import get as get_log, workdir as _wd          # noqa: E402

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# ── 启动参数档位 ────────────────────────────────────────────
#
# 档位是**并列**的（不是叠加），由 build_args 按 level 选一套。
#
#   L0 日常档（默认）—— 「像正常打开的浏览器」，参数仍然克制：
#       必需 3 个：port / allow-origins / user-data-dir
#       正常窗口尺寸：--window-size=1280,900（不用 --start-minimized）
#       跳过开箱即用体验：--no-first-run --no-default-browser-check
#       屏蔽权限弹窗：--deny-permission-prompts（自动拒绝地理位置/摄像头/通知等）
#       屏蔽通知弹窗：--disable-notifications
#       弹窗抑制 6 个：见下方 _DIALOG_ARGS（崩溃恢复气泡 / 错误对话框 /
#         重发表单 / 搜索引擎选择页 / 翻译气泡）
#       ※ cookie/同意书弹窗没有对应的命令行开关，改由 cdp.py 的
#         CONSENT_JS 在页内保守关闭（见该文件说明）。
#
#   L1 极简档 —— 仅 3 个必需参数。作为 A/B 对照：
#       当某站点在 L0 下表现异常时，用它判断是否参数本身引起的。
#       （刻意不含弹窗抑制 —— 否则无法判断异常是否由参数引起）
#
#   L2 扩展档 —— L0 + 语言设置，仅在需要中文界面时使用。
#
# ★ 三档都**不含**：--headless（无头最易被识别）、
#   --disable-blink-features=AutomationControlled、--excludeSwitches
#   —— 这些非默认参数本身就是可被检测的指纹。
#   ※ 「弹窗抑制」6 个参数（v6 新增，L0/L2/L3 共有）—— 只关**浏览器自己的
#     对话框**，不碰页面 DOM，也不改 navigator/screen 指纹：
#       --noerrdialogs                       抑制「页面无响应」等错误对话框（模态、会卡住导航）
#       --disable-session-crashed-bubble     抑制崩溃后「恢复页面」气泡
#       --hide-crash-restore-bubble          同上（新版 Chromium 的开关名，两代都带以便兼容）
#       --disable-prompt-on-repost           抑制「重新提交表单」模态对话框（会直接阻断导航）
#       --disable-search-engine-choice-screen 抑制全新 profile 首次启动的搜索引擎选择页
#       --disable-features=Translate,TranslateUI
#                                            抑制「是否翻译此页面？」气泡（英文论文页必现）
#     ★ 前两项尤其重要：本流程会 `taskkill /F` 强制结束实例（见 kill_port），
#       同一 profile 下次启动**必然**弹「恢复页面」，遮挡内容并抢焦点，
#       影响人工过验证码。
#     ★ 未知开关会被 Chromium 静默忽略，因此「两代开关名都带」是安全的。
_DIALOG_ARGS = [
    "--noerrdialogs",
    "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
    "--disable-prompt-on-repost",
    "--disable-search-engine-choice-screen",
    "--disable-features=Translate,TranslateUI",
]

LEVELS = [
    # L0 日常档（默认）
    ["--remote-debugging-port={port}", "--remote-allow-origins=*",
     "--user-data-dir={profile}",
     "--window-size=1280,900",
     "--no-first-run", "--no-default-browser-check",
     "--deny-permission-prompts", "--disable-notifications",
     *_DIALOG_ARGS],
    # L1 极简档（A/B 对照）—— 刻意**不含**弹窗抑制，用于判断异常是否由参数引起
    ["--remote-debugging-port={port}", "--remote-allow-origins=*",
     "--user-data-dir={profile}"],
    # L2 扩展档（L0 + 中文界面）
    ["--remote-debugging-port={port}", "--remote-allow-origins=*",
     "--user-data-dir={profile}",
     "--window-size=1280,900",
     "--no-first-run", "--no-default-browser-check",
     "--deny-permission-prompts", "--disable-notifications",
     *_DIALOG_ARGS,
     "--lang=zh-CN"],
    # L3 极速档（L0 + 关闭启动期后台任务：更快冷启动、更低后台占用）
    ["--remote-debugging-port={port}", "--remote-allow-origins=*",
     "--user-data-dir={profile}",
     "--window-size=1280,900",
     "--no-first-run", "--no-default-browser-check",
     "--deny-permission-prompts", "--disable-notifications",
     *_DIALOG_ARGS,
     "--disable-background-networking", "--disable-component-update",
     "--disable-sync", "--disable-default-apps", "--no-service-autorun",
     "--disable-client-side-phishing-detection", "--disable-domain-reliability"],
]

LEVEL_NAMES = {0: "日常档", 1: "极简档", 2: "扩展档", 3: "极速档"}

# 默认档位：PAPER_CDP_LEVEL 可覆盖（缺省 L0）。
# ★ 为什么需要它：启动用 L3、中途崩溃重启却回落到 L0，会让同一轮任务里
#   实例行为不一致；统一由该变量决定，重启沿用同一档。
DEFAULT_LEVEL = int(os.environ.get("PAPER_CDP_LEVEL", "0") or 0)

# 每个工作目录的实例登记表，便于 status / down
REGISTRY = ".work_cdp/cdp_instances.json"


def find_edge() -> str:
    for p in EDGE_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    raise FileNotFoundError("未找到 msedge.exe（Microsoft Edge）")


def profile_for(wd: pathlib.Path, port: int, tag: str = "") -> pathlib.Path:
    """端口 → profile 目录。9222 沿用历史 profile（保留已获得的放行 cookie）。"""
    if port == 9222 and not tag:
        return wd / ".edge-cdp-profile"
    return wd / f".edge-cdp-p{port}{('-' + tag) if tag else ''}"


def build_args(port: int, profile: pathlib.Path, level: int) -> list[str]:
    """按档位选一套启动参数（档位是并列的，不叠加）。默认 L0 日常档。"""
    lv = max(0, min(level, len(LEVELS) - 1))
    args = [a.format(port=port, profile=str(profile)) for a in LEVELS[lv]]
    args.append("about:blank")
    return args


def _load_registry(wd: pathlib.Path) -> dict:
    try:
        return json.loads((wd / REGISTRY).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_registry(wd: pathlib.Path, reg: dict) -> None:
    p = wd / REGISTRY
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def kill_port(port: int, log=None) -> int:
    """结束监听指定端口的进程树（只动调试实例，不影响用户正常浏览的 Edge）。"""
    killed = 0
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=25, errors="replace").stdout
    except Exception:
        return 0
    pids = set()
    for line in out.splitlines():
        if f":{port}" in line and "LISTEN" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True, timeout=25)
            killed += 1
            if log:
                log.info(f"已终止端口 {port} 上的进程树 PID {pid}")
        except Exception as e:
            if log:
                log.warn(f"终止 PID {pid} 失败: {e}")
    return killed


def launch_one(wd: pathlib.Path, port: int, level: int | None = None,
               profile: pathlib.Path | None = None, fresh: bool = False,
               wait: float = 30.0, log=None, tag: str = "") -> bool:
    """启动一个 Edge CDP 实例并等待就绪。已就绪则直接返回 True。

    level=None 时取 DEFAULT_LEVEL（环境变量 PAPER_CDP_LEVEL，缺省 L0），
    保证「启动用什么档，中途崩溃重启就用什么档」。
    """
    log = log or get_log("cdp_pool", wd=wd)
    if is_alive(port):
        log.info(f"端口 {port} 已有存活实例，复用")
        return True

    level = DEFAULT_LEVEL if level is None else level
    prof = profile or profile_for(wd, port, tag=tag)
    if fresh:
        import shutil
        shutil.rmtree(prof, ignore_errors=True)
    prof.mkdir(parents=True, exist_ok=True)

    edge = find_edge()
    args = build_args(port, prof, level)
    log.info(f"启动 Edge L{level}({LEVEL_NAMES.get(level, '?')}) port={port} "
             f"profile={prof.name} | 参数 {len(args) - 1} 个: "
             f"{' '.join(a for a in args if not a.startswith('--user-data-dir'))}")

    logf = (wd / ".work_cdp" / "logs")
    logf.mkdir(parents=True, exist_ok=True)
    fh = open(logf / f"edge_{port}.log", "a", encoding="utf-8", errors="replace")

    flags = 0
    if os.name == "nt":
        # 脱离父进程，任务结束后继续存活
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    try:
        subprocess.Popen([edge] + args, stdout=fh, stderr=fh,
                         stdin=subprocess.DEVNULL, close_fds=True,
                         creationflags=flags)
    except Exception as e:
        log.error(f"Edge 启动失败: {e}")
        return False

    end = time.time() + wait
    while time.time() < end:
        if is_alive(port):
            v = version(port)
            log.ok(f"CDP 就绪 port={port} | {v.get('Browser')}")
            return True
        time.sleep(0.25)          # 0.25s 轮询：比 1s 平均早 ~0.4s 返回
    log.error(f"端口 {port} 启动后 {wait:.0f}s 内未就绪，见 logs/edge_{port}.log")
    return False


def ensure(wd: pathlib.Path | None = None, n: int = 1, base_port: int = 9222,
           levels: list[int] | None = None, log=None, fresh: bool = False) -> list[int]:
    """确保有 n 个可用实例；返回存活端口列表。

    默认**所有实例都用 L0 日常档**（正常窗口尺寸 + 跳过开箱体验 +
    屏蔽权限/通知弹窗）—— 这是推荐配置。
    传入 levels=[0,1,2] 可让各实例使用不同档位，用于并行 A/B 对比。
    """
    wd = wd or _wd()
    log = log or get_log("cdp_pool", wd=wd)
    levels = levels or [DEFAULT_LEVEL]
    ports, reg = [], _load_registry(wd)

    # ★ 并行启动：先全部拉起（每个 launch_one 内部会等到就绪），
    #   N 个实例的总启动时间 ≈ 最慢的 1 个，而不是 N 个之和。
    import threading
    results: dict[int, bool] = {}

    def _boot(i: int):
        port = base_port + i
        lv = levels[i % len(levels)]
        try:
            results[port] = launch_one(wd, port, level=lv,
                                       fresh=fresh and i == 0, log=log)
        except Exception as e:
            log.warn(f"实例 {port} 启动异常: {str(e)[:80]}")
            results[port] = False

    threads = [threading.Thread(target=_boot, args=(i,), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(n):
        port = base_port + i
        lv = levels[i % len(levels)]
        ok = bool(results.get(port))
        reg[str(port)] = {"profile": profile_for(wd, port).name, "level": lv, "ok": ok}
        if ok:
            ports.append(port)
    _save_registry(wd, reg)

    if not ports:
        log.error("没有任何可用 CDP 实例")
    else:
        log.info(f"可用实例 {len(ports)} 个: {ports}")
    return ports


def alive_ports(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8) -> list[int]:
    """扫描端口段，返回存活的 CDP 端口。"""
    return [p for p in range(base_port, base_port + span) if is_alive(p)]


def status(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8) -> list[dict]:
    wd = wd or _wd()
    reg = _load_registry(wd)
    out = []
    for p in range(base_port, base_port + span):
        live = is_alive(p)
        info = {"port": p, "alive": live,
                "profile": (reg.get(str(p)) or {}).get("profile", profile_for(wd, p).name),
                "level": (reg.get(str(p)) or {}).get("level")}
        if live:
            try:
                v = version(p)
                info["browser"] = v.get("Browser", "")
                tabs = [t for t in list_tabs(p) if t.get("type") == "page"]
                info["tabs"] = len(tabs)
            except Exception:
                pass
        out.append(info)
    return out


def down(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8, log=None) -> int:
    wd = wd or _wd()
    log = log or get_log("cdp_pool", wd=wd)
    n = 0
    for p in range(base_port, base_port + span):
        if is_alive(p):
            n += kill_port(p, log=log)
    log.info(f"已关闭 {n} 个实例")
    return n


def renew(wd: pathlib.Path | None = None, port: int = 9222, level: int | None = None,
          tag: str = "", log=None) -> bool:
    """换全新 profile 重建单个实例（放行 cookie 失效时使用）。"""
    wd = wd or _wd()
    log = log or get_log("cdp_pool", wd=wd)
    log.info(f"重建实例 port={port}（全新 profile）")
    kill_port(port, log=log)
    time.sleep(3)
    return launch_one(wd, port, level=level, tag=tag, fresh=True, log=log)


# ── CLI ─────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("-") else "up"
    wd = _wd()
    kw = {}
    for a in args:
        if a.startswith("--n="): kw["n"] = int(a.split("=", 1)[1])
        elif a.startswith("--port="): kw["port"] = int(a.split("=", 1)[1])
        elif a.startswith("--base="): kw["base_port"] = int(a.split("=", 1)[1])
        elif a.startswith("--span="): kw["span"] = int(a.split("=", 1)[1])
        elif a.startswith("--levels="):
            kw["levels"] = [int(x) for x in a.split("=", 1)[1].split(",") if x.strip()]
        elif a.startswith("--level="): kw["level"] = int(a.split("=", 1)[1])
        elif a.startswith("--tag="): kw["tag"] = a.split("=", 1)[1]
        elif a == "--fresh": kw["fresh"] = True
    log = get_log("cdp_pool", wd=wd)

    if cmd == "up":
        ports = ensure(wd, n=kw.get("n", 1), base_port=kw.get("base_port", 9222),
                       levels=kw.get("levels"), log=log, fresh=kw.get("fresh", False))
        print(f"存活实例: {ports}")
    elif cmd == "status":
        for s in status(wd, kw.get("base_port", 9222)):
            if s["alive"]:
                print(f"  [ON ] :{s['port']}  L{s.get('level')}  profile={s['profile']}  "
                      f"tabs={s.get('tabs')}  {s.get('browser','')}")
            else:
                print(f"  [off] :{s['port']}  profile={s['profile']}")
    elif cmd == "down":
        down(wd, kw.get("base_port", 9222), kw.get("span", 8), log=log)
    elif cmd == "renew":
        renew(wd, kw.get("port", 9222), kw.get("level"), kw.get("tag", ""), log=log)
    elif cmd == "kill":
        kill_port(kw.get("port", 9222), log=log)
    else:
        print(__doc__ or "usage: cdp_pool.py {up|status|down|renew|kill} [--n=] [--port=]")
    log.close()


if __name__ == "__main__":
    main()
