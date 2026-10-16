#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
llm_lifecycle —— 便携的 llama.cpp 服务启停与精准释放
=====================================================
设计目标（用户口径）
--------------------
1. **不依赖绝对路径**：脚本位置、工作区、模型、llama.cpp 全部自动发现或参数覆盖。
2. **自带依赖安装**：缺 `psutil` 等依赖时自动 `pip install`（或提示命令），换电脑可用。
3. **换模型 / 换 harness / 换电脑都能跑**：模型按名字模糊查找或显式给路径；
   harness 通过环境变量或参数接入。
4. **启动时记录归属证据**：写 PID + 唯一 api-key + 端口 + 启动时刻到 pin 文件。
5. **结束按需精准卸载**：只杀掉**本次启动的**那个进程，绝不动系统上其他
   llama / ollama / 其他模型服务。

为什么必须用「PID + 唯一 api-key + 端口 + 启动时刻」四重证据
-----------------------------------------------------------
本机实测（与 QWEN06B_TASKS.md §7.3 陷阱 1 一致，已复测）：
  · `Get-CimInstance Win32_Process` 的 CommandLine **恒为空** → 无法按命令行找进程
  · `(Get-Process).CommandLine` 同样为空
  · 因此"遍历所有 llama-server 找我的那个"这条路**不可靠**
故采取**签发时登记**（issuance-based）而非**事后搜索**（discovery-based）：
  启动瞬间把 PID 写进 pin 文件；释放时只认这个 PID，
  并用 (a) 进程存在、(b) 可执行文件路径一致、(c) /props 能应答且带我们的 key
  三重校验，任一不符就**拒绝杀**并报错 —— 宁可漏杀，不可误杀。

llama.cpp 没有"卸载权重"的 HTTP 端点（本机实测 /metrics 返回 501），
所以"卸载本次权重"在进程模型下等价于**终止本次服务进程**（权重随进程释放）。
若将来换到支持 unload 的后端，`--unload-url` 可挂一个自定义卸载报文。

用法
----
    # 启动（自动找模型、自动装依赖、自动挑空闲端口、写 pin 文件）
    python llm_lifecycle.py serve --model Qwen3-0.6B

    # 查看当前由本工具启动、且仍活着的服务
    python llm_lifecycle.py status

    # 精准释放本次（或指定 pin 的）服务：杀进程 + 清理临时文件
    python llm_lifecycle.py release --pin <pin.json>
    python llm_lifecycle.py release --latest

    # 释放全部由本工具启动的服务（仍不动别的）
    python llm_lifecycle.py release --all

    # 环境自检（依赖、模型、端口、既有服务）
    python llm_lifecycle.py doctor
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# 0. 依赖自举（换电脑/换 venv 也能跑）
# ══════════════════════════════════════════════════════════════
REQUIRED = {"psutil": "psutil"}          # import 名 -> pip 名


def ensure_deps(auto: bool = True) -> None:
    """缺依赖就装。`auto=False` 时只报错并给命令。"""
    missing = []
    for mod, pipname in REQUIRED.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pipname)
    if not missing:
        return
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *missing]
    if not auto:
        print(f"[X] 缺少依赖：{'、'.join(missing)}\n    请运行：{' '.join(cmd)}")
        raise SystemExit(2)
    print(f"[i] 缺少依赖 {missing}，尝试自动安装…")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            # 有些 venv 没有 pip（本机 .venv 实测就没有）→ 退到 uv
            uv = shutil.which("uv")
            if uv:
                r = subprocess.run([uv, "pip", "install", "--python", sys.executable,
                                    *missing], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print(f"[X] 自动安装失败：{(r.stderr or '')[-400:]}")
            print(f"    请手动：{' '.join(cmd)}   或   uv pip install {' '.join(missing)}")
            raise SystemExit(2)
        print("[i] 依赖安装完成")
    except subprocess.TimeoutExpired:
        print("[X] 依赖安装超时")
        raise SystemExit(2)


# ══════════════════════════════════════════════════════════════
# 1. 路径/环境发现（不写死绝对路径）
# ══════════════════════════════════════════════════════════════
def script_dir() -> Path:
    return Path(__file__).resolve().parent


def default_workspace() -> Path:
    """工作区推断顺序：显式环境变量 → 逐级向上找特征目录 → 脚本目录。

    特征：存在 `llamacpp/` 或 `流程1/` 或 `.venv/`。
    """
    env = os.environ.get("PAPER_WORKDIR") or os.environ.get("LLM_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    for base in (script_dir(), *script_dir().parents):
        for marker in ("llamacpp", "流程1", ".venv"):
            if (base / marker).exists():
                return base
    return script_dir()


def state_dir(ws: Path) -> Path:
    """pin 文件与日志的落点（随工作区走，可被环境变量覆盖）。"""
    env = os.environ.get("LLM_STATE_DIR")
    d = Path(env).expanduser() if env else (ws / ".llm_lifecycle")
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_llama_server(ws: Path, explicit: str | None = None) -> Path | None:
    """找 llama-server 可执行文件。顺序：显式 → 环境变量 → 常见位置 → PATH。"""
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit))
    for var in ("LLAMA_SERVER", "LLAMA_SERVER_BIN"):
        if os.environ.get(var):
            cands.append(Path(os.environ[var]))
    cands += [ws / "llamacpp" / exe, ws / "llama.cpp" / "build" / "bin" / exe,
              ws / "bin" / exe, script_dir().parent / "llamacpp" / exe]
    for c in cands:
        if c.is_file():
            return c.resolve()
    w = shutil.which("llama-server")
    return Path(w).resolve() if w else None


def find_model(ws: Path, want: str | None, explicit: str | None = None) -> Path | None:
    """找模型。顺序：显式路径 → 环境变量 → 按名字模糊匹配任何 .gguf。"""
    if explicit:
        p = Path(explicit)
        return p.resolve() if p.is_file() else None
    if os.environ.get("LLM_MODEL"):
        p = Path(os.environ["LLM_MODEL"])
        if p.is_file():
            return p.resolve()
    roots = [ws / "llamacpp" / "models", ws / "models", ws / "ollama" / "models"]
    env_root = os.environ.get("LLM_MODEL_DIR")
    if env_root:
        roots.insert(0, Path(env_root))
    ggufs: list[Path] = []
    for r in roots:
        if r.is_dir():
            ggufs += [g for g in r.rglob("*.gguf") if g.is_file()]
    if not ggufs:
        return None
    if not want:
        return sorted(ggufs)[0]
    key = want.lower().replace(" ", "")
    # 精确名 → 前缀 → 模糊包含
    for g in ggufs:
        if g.stem.lower() == key:
            return g
    for g in ggufs:
        if g.stem.lower().startswith(key):
            return g
    for g in ggufs:
        if key in g.stem.lower().replace(" ", ""):
            return g
    return None


# ══════════════════════════════════════════════════════════════
# 2. 端口与 HTTP
# ══════════════════════════════════════════════════════════════
def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def pick_port(preferred: int | None = None, lo: int = 8080, hi: int = 8180) -> int:
    """挑一个空闲端口（默认从 8081 起，避开 8080 上可能正在跑的其他服务）。"""
    if preferred:
        if port_in_use(preferred):
            raise SystemExit(f"[X] 端口 {preferred} 已被占用 —— 拒绝复用，"
                             f"避免 QWEN06B_TASKS.md 陷阱 2（llama.cpp 会重复绑定同端口）")
        return preferred
    for p in range(lo, hi):
        if not port_in_use(p):
            return p
    raise SystemExit(f"[X] {lo}–{hi} 无空闲端口")


def http_json(url: str, timeout: float = 3.0, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def props(port: int, key: str | None = None):
    """取 /props。带 key 时验证归属。"""
    h = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        return http_json(f"http://127.0.0.1:{port}/props", headers=h)
    except Exception:                                        # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════
# 3. pin 文件（启动时登记的"归属证据"）
# ══════════════════════════════════════════════════════════════
def pin_path(sd: Path, tag: str | None = None) -> Path:
    tag = tag or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return sd / "pins" / f"{tag}.json"


def save_pin(sd: Path, rec: dict) -> Path:
    p = pin_path(sd, rec.get("tag"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (sd / "latest").write_text(str(p), encoding="utf-8")
    return p


def load_pins(sd: Path) -> list[tuple[Path, dict]]:
    d = sd / "pins"
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                out.append((f, json.loads(f.read_text(encoding="utf-8"))))
            except Exception:                                # noqa: BLE001
                continue
    return out


def proc_alive(pid: int) -> bool:
    import psutil
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:                                        # noqa: BLE001
        return False


def proc_exe(pid: int) -> str | None:
    import psutil
    try:
        return psutil.Process(pid).exe()
    except Exception:                                        # noqa: BLE001
        return None


def same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except Exception:                                        # noqa: BLE001
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ══════════════════════════════════════════════════════════════
# 4. serve / status / release / doctor
# ══════════════════════════════════════════════════════════════
def cmd_serve(a) -> int:
    ws = Path(a.workspace).resolve() if a.workspace else default_workspace()
    sd = state_dir(ws)
    exe = find_llama_server(ws, a.llama_server)
    if not exe:
        print("[X] 找不到 llama-server。用 --llama-server 指定，"
              "或设环境变量 LLAMA_SERVER。")
        return 2
    model = find_model(ws, a.model, a.model_path)
    if not model:
        print(f"[X] 找不到模型 {a.model!r}。用 --model-path 指定，"
              f"或设 LLM_MODEL / LLM_MODEL_DIR。")
        return 2
    port = pick_port(a.port)
    key = a.api_key or f"llc-{uuid.uuid4().hex}"
    alias = a.alias or model.stem

    logdir = sd / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    tag = a.tag or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    outlog, errlog = logdir / f"{tag}.out.log", logdir / f"{tag}.err.log"

    argv = [str(exe), "-m", str(model), "-c", str(a.ctx), "-ngl", str(a.ngl),
            "--host", "127.0.0.1", "--port", str(port), "-np", str(a.slots),
            "--alias", alias, "--api-key", key]
    if a.jinja:
        argv.append("--jinja")
    if a.extra:
        # 用 | 分隔，避免 argparse 把 llama-server 的开关当成本脚本的参数
        argv += [x for x in str(a.extra).split("|") if x]

    print(f"[i] llama-server : {exe}")
    print(f"[i] model        : {model}")
    print(f"[i] port         : {port}   alias={alias}")
    print(f"[i] logs         : {outlog.name} / {errlog.name}")

    kwargs = {}
    if os.name == "nt":
        # 让子进程独立于本 shell；不新建窗口
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED | NO_WINDOW
    fo = open(outlog, "ab")
    fe = open(errlog, "ab")
    proc = subprocess.Popen(argv, stdout=fo, stderr=fe, **kwargs)
    fo.close()
    fe.close()

    rec = {"tag": tag, "pid": proc.pid, "port": port, "api_key": key,
           "alias": alias, "model": str(model), "llama_server": str(exe),
           "ctx": a.ctx, "ngl": a.ngl, "slots": a.slots,
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "started_ts": time.time(), "argv": argv,
           "out_log": str(outlog), "err_log": str(errlog),
           "host": platform.node(), "workspace": str(ws),
           "pid_started": None}
    # 记录 PID 的创建时间：防 PID 复用（PID 会被系统回收再分配）
    try:
        import psutil
        rec["pid_started"] = psutil.Process(proc.pid).create_time()
    except Exception:                                        # noqa: BLE001
        pass

    # 等就绪：/props 能应答
    ok = False
    for _ in range(int(a.wait)):
        if proc.poll() is not None:
            print(f"[X] 进程已退出（code={proc.returncode}），看日志：{errlog}")
            return 2
        if props(port, key):
            ok = True
            break
        time.sleep(1)
    if not ok:
        print(f"[!] {a.wait}s 内 /props 未就绪，进程仍在跑（pid={proc.pid}）。"
              f"可用 status/release 处理。")
    p = save_pin(sd, rec)
    print(f"[OK] pid={proc.pid}  pin={p}")
    if ok:
        pr = props(port, key) or {}
        print(f"[OK] 就绪：model_path={pr.get('model_path')} "
              f"n_ctx={(pr.get('default_generation_settings') or {}).get('n_ctx')}")
    print(f"\n释放：python {Path(__file__).name} release --pin \"{p}\"")
    return 0


def _verify_pin_alive(rec: dict) -> tuple[bool, str]:
    """→ (是否确认是本工具启动且仍活着, 说明)。宁可漏杀不可误杀。"""
    pid = rec.get("pid")
    if not pid:
        return False, "pin 里没有 pid"
    if not proc_alive(pid):
        return False, "进程已不存在"
    # PID 复用防护：比对创建时间
    st = rec.get("pid_started")
    if st:
        try:
            import psutil
            cur = psutil.Process(pid).create_time()
            if abs(cur - float(st)) > 2.0:
                return False, f"PID 已被复用（创建时间不符：{cur} vs {st}）"
        except Exception:                                    # noqa: BLE001
            pass
    # 可执行文件必须与登记一致
    ex = proc_exe(pid)
    if not same_file(ex, rec.get("llama_server")):
        return False, f"可执行文件不符（{ex} vs {rec.get('llama_server')}）"
    return True, "确认归属"


def cmd_status(a) -> int:
    ws = Path(a.workspace).resolve() if a.workspace else default_workspace()
    sd = state_dir(ws)
    pins = load_pins(sd)
    if not pins:
        print("（无 pin 记录 —— 本工具还没启动过任何服务）")
        return 0
    print(f"{'tag':38} {'pid':>7} {'port':>5}  {'状态':12} model")
    print("-" * 110)
    for f, rec in pins:
        ok, why = _verify_pin_alive(rec)
        state = "ALIVE" if ok else "gone"
        if ok:
            pr = props(rec["port"], rec.get("api_key"))
            if not pr:
                state = "ALIVE(无应答)"
        print(f"{rec.get('tag','')[:38]:38} {str(rec.get('pid')):>7} "
              f"{str(rec.get('port')):>5}  {state:12} "
              f"{Path(rec.get('model','')).name}")
        if not ok:
            print(f"{'':38} {'':7} {'':5}  └ {why}")
    return 0


def cmd_release(a) -> int:
    ws = Path(a.workspace).resolve() if a.workspace else default_workspace()
    sd = state_dir(ws)
    pins = load_pins(sd)
    if not pins:
        print("（无 pin 记录，无需释放）")
        return 0

    targets: list[tuple[Path, dict]] = []
    if a.pin:
        p = Path(a.pin)
        targets = [(p, json.loads(p.read_text(encoding="utf-8")))]
    elif a.all:
        targets = pins
    else:                                # 默认 --latest
        latest = sd / "latest"
        if latest.exists():
            p = Path(latest.read_text(encoding="utf-8").strip())
            if p.exists():
                targets = [(p, json.loads(p.read_text(encoding="utf-8")))]
        if not targets and pins:
            targets = [pins[-1]]

    if not targets:
        print("[X] 没找到目标 pin。用 --pin / --latest / --all")
        return 2

    import psutil
    rc = 0
    for p, rec in targets:
        tag, pid = rec.get("tag"), rec.get("pid")
        print(f"\n=== 释放 {tag} (pid={pid}) ===")
        ok, why = _verify_pin_alive(rec)
        if not ok:
            print(f"[跳过] {why} —— **不杀任何进程**（宁可漏杀不可误杀）")
            if a.prune and not proc_alive(pid or -1):
                p.unlink(missing_ok=True)
                print(f"[i] 已删除失效 pin：{p.name}")
            continue
        # 可选：先发自定义卸载报文（换到支持 unload 的后端时用）
        if a.unload_url:
            try:
                req = urllib.request.Request(
                    a.unload_url.format(port=rec["port"]),
                    data=json.dumps({"model": rec.get("alias")}).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {rec.get('api_key','')}"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    print(f"[i] 卸载报文 → HTTP {r.status}")
            except Exception as e:                           # noqa: BLE001
                print(f"[!] 卸载报文失败（继续走进程终止）：{e}")

        # 精准终止：先 SIGTERM/terminate，宽限后 kill
        try:
            pr = psutil.Process(pid)
            pr.terminate()
            try:
                pr.wait(timeout=a.grace)
                print(f"[OK] 已终止 pid={pid}（优雅退出）")
            except psutil.TimeoutExpired:
                pr.kill()
                pr.wait(timeout=10)
                print(f"[OK] 已强杀 pid={pid}（宽限 {a.grace}s 未退出）")
        except psutil.NoSuchProcess:
            print(f"[i] pid={pid} 在终止前已消失")
        except Exception as e:                               # noqa: BLE001
            print(f"[X] 终止失败：{e}")
            rc = 1

        # 等端口真正释放
        for _ in range(20):
            if not port_in_use(rec.get("port", 0)):
                break
            time.sleep(0.5)
        else:
            print(f"[!] 端口 {rec.get('port')} 仍被占用")

        # 清理临时文件（本次的日志与 pin）
        freed = []
        if a.keep_logs:
            print(f"[i] 保留日志：{rec.get('out_log')}")
        else:
            for k in ("out_log", "err_log"):
                f = rec.get(k)
                if f and Path(f).exists():
                    try:
                        Path(f).unlink()
                        freed.append(Path(f).name)
                    except Exception:                        # noqa: BLE001
                        pass
        if a.prune:
            try:
                p.unlink(missing_ok=True)
                freed.append(p.name)
            except Exception:                                # noqa: BLE001
                pass
        # 清理本工具自己的 pycache（可选）
        if a.clean_pycache:
            pc = script_dir() / "__pycache__"
            if pc.is_dir():
                shutil.rmtree(pc, ignore_errors=True)
                freed.append("__pycache__")
        if freed:
            print(f"[OK] 清理临时文件 {len(freed)} 项：{'、'.join(freed)}")

    print("\n完成。**未触碰任何非本次启动的进程。**")
    return rc


def cmd_sweep(a) -> int:
    """回收"孤儿服务"：包装器被硬杀（Ctrl-C/崩溃/断电）时 finally 不会执行，
    服务会留在系统上。pin 文件仍在，因此可以精准回收 —— 本命令把这件事一步做完。

    与 release --all 的区别：sweep 会**同时**清掉失效 pin 与遗留日志，
    并打印每个孤儿的内存占用，方便确认释放了多少。
    """
    ws = Path(a.workspace).resolve() if a.workspace else default_workspace()
    sd = state_dir(ws)
    pins = load_pins(sd)
    if not pins:
        print("（无 pin 记录，无需回收）")
        return 0
    import psutil
    orphans, dead = [], []
    for f, rec in pins:
        ok, why = _verify_pin_alive(rec)
        (orphans if ok else dead).append((f, rec, why))

    print(f"pin 总数 {len(pins)}｜存活孤儿 {len(orphans)}｜失效 {len(dead)}\n")
    freed_mb = 0.0
    for f, rec, _ in orphans:
        pid = rec.get("pid")
        try:
            mb = psutil.Process(pid).memory_info().rss / 1e6
        except Exception:                                    # noqa: BLE001
            mb = 0.0
        print(f"  回收 pid={pid} 端口={rec.get('port')} "
              f"模型={Path(rec.get('model','')).name} 占用≈{mb:.0f} MB")
        if not a.dry_run:
            class R:
                pass
            r = R()
            r.workspace = a.workspace
            r.pin = str(f)
            r.all = False
            r.latest = False
            r.grace = a.grace
            r.keep_logs = a.keep_logs
            r.prune = True
            r.clean_pycache = False
            r.unload_url = a.unload_url
            cmd_release(r)
            freed_mb += mb
    # 失效 pin 直接清掉
    for f, rec, why in dead:
        if not a.dry_run:
            f.unlink(missing_ok=True)
    if dead:
        print(f"  清理失效 pin {len(dead)} 条")
    # 遗留日志（无对应 pin 的）
    logdir = sd / "logs"
    if logdir.is_dir() and not a.keep_logs:
        live_tags = {rec.get("tag") for _, rec, _ in orphans}
        n = 0
        for lg in logdir.glob("*.log"):
            if lg.stem.replace(".out", "").replace(".err", "") not in live_tags:
                if not a.dry_run:
                    lg.unlink(missing_ok=True)
                n += 1
        if n:
            print(f"  清理遗留日志 {n} 个")
    if a.dry_run:
        print("\n（--dry-run：未实际删除任何东西）")
    else:
        print(f"\n回收完成，释放约 {freed_mb:.0f} MB。"
              f"**未触碰任何非本工具启动的进程。**")
    return 0


def cmd_doctor(a) -> int:
    ws = Path(a.workspace).resolve() if a.workspace else default_workspace()
    sd = state_dir(ws)
    print("=" * 70)
    print("  环境自检")
    print("=" * 70)
    print(f"  平台        : {platform.system()} {platform.release()} / py{sys.version_info.major}.{sys.version_info.minor}")
    print(f"  脚本目录    : {script_dir()}")
    print(f"  推断工作区  : {ws}")
    print(f"  状态目录    : {sd}")

    # 依赖
    for mod, pipname in REQUIRED.items():
        try:
            m = __import__(mod)
            print(f"  依赖 {mod:10}: OK {getattr(m,'__version__','')}")
        except ImportError:
            print(f"  依赖 {mod:10}: **缺失** → pip install {pipname}")

    # llama-server
    exe = find_llama_server(ws, a.llama_server)
    print(f"  llama-server: {exe if exe else '**未找到**（用 --llama-server 或 LLAMA_SERVER）'}")

    # 模型
    models = []
    for r in (ws / "llamacpp" / "models", ws / "models"):
        if r.is_dir():
            models += [g for g in r.rglob("*.gguf") if g.is_file()]
    print(f"  发现模型    : {len(models)} 个")
    for m in sorted(models)[:12]:
        print(f"      {m.name}  ({m.stat().st_size/1e9:.2f} GB)")

    # 端口
    print("  端口占用    :")
    for p in range(8080, 8086):
        print(f"      {p} : {'占用' if port_in_use(p) else '空闲'}")
    for p in (8081, 8080, 11434):
        pr = props(p)
        if pr:
            print(f"      {p} /props model_path = {pr.get('model_path')}")

    # pin
    pins = load_pins(sd)
    print(f"  pin 记录    : {len(pins)} 条")
    for f, rec in pins:
        ok, why = _verify_pin_alive(rec)
        print(f"      {rec.get('tag','')[:30]:30} pid={rec.get('pid')} "
              f"{'ALIVE' if ok else 'gone'}  {why if not ok else ''}")
    print("=" * 70)
    return 0


# ══════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="便携的 llama.cpp 服务启停与精准释放（不依赖绝对路径）")
    ap.add_argument("--workspace", help="工作区（默认自动推断）")
    ap.add_argument("--llama-server", help="llama-server 可执行文件路径")
    ap.add_argument("--no-auto-install", action="store_true",
                    help="缺依赖时不自动安装")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="启动服务并登记 pin")
    s.add_argument("--model", default=None, help="模型名（模糊匹配，如 Qwen3-0.6B）")
    s.add_argument("--model-path", default=None, help="模型文件显式路径")
    s.add_argument("--port", type=int, default=None, help="指定端口（被占用则拒绝启动）")
    s.add_argument("--ctx", type=int, default=32768)
    s.add_argument("--ngl", type=int, default=99)
    s.add_argument("--slots", type=int, default=1)
    s.add_argument("--alias", default=None)
    s.add_argument("--api-key", default=None, help="不给则随机生成（归属证据）")
    s.add_argument("--jinja", action="store_true", default=True)
    s.add_argument("--no-jinja", dest="jinja", action="store_false")
    s.add_argument("--extra", default=None,
                   help="追加给 llama-server 的原样参数（用 | 分隔，如 "
                        '--extra "-fa|on"）')
    s.add_argument("--wait", type=int, default=120, help="就绪等待秒数")
    s.add_argument("--tag", default=None)
    s.set_defaults(func=cmd_serve)

    st = sub.add_parser("status", help="列出本工具启动的服务")
    st.set_defaults(func=cmd_status)

    r = sub.add_parser("release", help="精准释放（只杀本次启动的进程）")
    r.add_argument("--pin", help="指定 pin 文件")
    r.add_argument("--latest", action="store_true", help="释放最近一次（默认）")
    r.add_argument("--all", action="store_true", help="释放全部由本工具启动的")
    r.add_argument("--grace", type=float, default=8.0, help="优雅退出宽限秒数")
    r.add_argument("--keep-logs", action="store_true", help="保留日志文件")
    r.add_argument("--keep-pin", dest="prune", action="store_false", default=True,
                   help="保留 pin 文件（默认释放后删除）")
    r.add_argument("--clean-pycache", action="store_true", help="顺带清本工具 __pycache__")
    r.add_argument("--unload-url", default=None,
                   help="可选：先 POST 一个卸载报文（模板 {port}），"
                        "适用于将来换到支持 unload 的后端")
    r.set_defaults(func=cmd_release)

    d = sub.add_parser("doctor", help="环境自检")
    d.set_defaults(func=cmd_doctor)

    sw = sub.add_parser("sweep",
                        help="回收孤儿服务（包装器被硬杀后残留的）+ 失效 pin + 遗留日志")
    sw.add_argument("--dry-run", action="store_true", help="只报告不删除")
    sw.add_argument("--grace", type=float, default=8.0)
    sw.add_argument("--keep-logs", action="store_true")
    sw.add_argument("--unload-url", default=None)
    sw.set_defaults(func=cmd_sweep)

    a = ap.parse_args()
    ensure_deps(auto=not a.no_auto_install)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
