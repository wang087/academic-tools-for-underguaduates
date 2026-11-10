# -*- coding: utf-8 -*-
"""Browser CDP pool with workdir-scoped, verified process ownership.

Use `up --browser PATH` for custom builds. Existing endpoints are inspected and
attached without claiming ownership. No lifecycle operation deletes profiles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from cdp import is_alive, list_tabs, resolve_port, version
from logutil import get as get_log, workdir as _wd
from browser_runtime import (find_browser, find_edge, browser_brand, normalize_path,
                             probe_endpoint, process_info, process_matches, listener_pids)

_DIALOG_ARGS = [
    "--noerrdialogs",
    "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
    "--disable-prompt-on-repost",
    "--disable-search-engine-choice-screen",
    # 账号/同步类首启弹窗（"We are now syncing your browsing data"）——
    #   Edge 首次用新 profile 启动时，会拿 Windows/Microsoft 账号自动登录并弹同步确认，
    #   遮挡页面、抢焦点，还会把个人账号写进自动化 profile。
    #   msEdge* 是 Edge 特有 feature 名；未知 feature 会被 Chromium 静默忽略。
    "--disable-features=Translate,TranslateUI,msEdgeSyncPromo,"
    "msEdgeIdentityFeature,msEdgeFirstRunExperience,SigninPromo,"
    "msEdgeAccountConsistency",
    # 彻底关掉同步（不弹同步确认，也不上传书签/密码/历史）
    "--disable-sync",
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
     "--disable-default-apps", "--no-service-autorun",
     "--disable-client-side-phishing-detection", "--disable-domain-reliability"],
]

LEVEL_NAMES = {0: "日常档", 1: "极简档", 2: "扩展档", 3: "极速档"}

LEVEL_NAMES = {0: "日常档", 1: "极简档", 2: "扩展档", 3: "极速档"}
DEFAULT_LEVEL = 0  # compatibility constant; calls use runtime default_level().
REGISTRY = ".work_cdp/cdp_instances.json"
_REG_LOCK = threading.RLock()


def default_level() -> int:
    value = int(os.environ.get("PAPER_CDP_LEVEL", "0") or 0)
    if value not in range(len(LEVELS)):
        raise ValueError("PAPER_CDP_LEVEL must be 0, 1, 2 or 3")
    return value


def _port(port):
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port


def _open(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", _port(port))) == 0


def _scope(wd):
    return pathlib.Path(wd if wd is not None else _wd()).resolve()


PROFILE_HOME = ".work_cdp/cdp_profiles"
_LEGACY_NOTED: set[str] = set()


def _profile_name(port: int, tag: str = "") -> str:
    if port == 9222 and not tag:
        return ".edge-cdp-profile"
    return f".edge-cdp-p{port}{('-' + tag) if tag else ''}"


def _port_of_profile_name(name: str) -> int | None:
    """profile 叶子名 → 端口；认不出来返回 None。"""
    if name == ".edge-cdp-profile":
        return 9222
    m = re.match(r"^\.edge-cdp-p(\d+)", name)
    return int(m.group(1)) if m else None


def _note_legacy_profile(p: pathlib.Path) -> None:
    """旧布局提示只打一次（避免多实例刷屏）。"""
    key = str(p)
    if key in _LEGACY_NOTED:
        return
    _LEGACY_NOTED.add(key)
    sys.stderr.write(
        f"[INFO] 沿用历史 profile {p.name}/（位于输出根）。"
        f"如需收进 {PROFILE_HOME}/：先 `cdp_pool.py down`，再 `cdp_pool.py clean --apply`"
        f"（**移动**而非删除，Cookie 一并保留）。\n")


def _binary_key(binary):
    return hashlib.sha256(normalize_path(binary).encode("utf-8")).hexdigest()[:10]


def profile_for(wd: pathlib.Path, port: int, tag: str = "", browser=None) -> pathlib.Path:
    wd, port = _scope(wd), _port(port)
    if tag and not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        raise ValueError("profile tag must contain only letters, digits, _ or -")
    binary = find_browser(browser)
    brand = browser_brand(binary)
    if brand == "edge":
        name = _profile_name(port, tag)
        for legacy in (wd / name, wd / PROFILE_HOME / name):
            if legacy.is_dir():
                if legacy.parent == wd:
                    _note_legacy_profile(legacy)
                return legacy
    return wd / PROFILE_HOME / f".browser-cdp-{brand}-{_binary_key(binary)}-p{port}{('-' + tag) if tag else ''}"


def _managed(wd, profile, port, binary):
    profile = pathlib.Path(profile).resolve()
    brand = browser_brand(binary)
    generic = re.fullmatch(rf"\.browser-cdp-{re.escape(brand)}-{_binary_key(binary)}-p{port}(?:-[A-Za-z0-9_-]+)?", profile.name)
    legacy = brand == "edge" and (profile.name == _profile_name(port) or
                re.fullmatch(rf"\.edge-cdp-p{port}-[A-Za-z0-9_-]+", profile.name))
    return ((profile.parent == (wd / PROFILE_HOME).resolve() and bool(generic or legacy))
            or (profile.parent == wd and bool(legacy)))


def _extra_args(extra_args=None):
    if extra_args is None:
        # POSIX parsing preserves quoted spaces. On Windows quote paths with / separators.
        extra_args = shlex.split(os.environ.get("PAPER_CDP_EXTRA_ARGS", ""))
    return list(extra_args)


def build_args(port: int, profile: pathlib.Path, level: int, extra_args=None) -> list[str]:
    if level not in range(len(LEVELS)):
        raise ValueError("level must be 0, 1, 2 or 3")
    extras = _extra_args(extra_args)
    forbidden = ("--remote-debugging-port", "--remote-debugging-pipe", "--user-data-dir", "--type")
    if any(not a.startswith("--") or any(a == x or a.startswith(x + "=") for x in forbidden) for a in extras):
        raise ValueError("extra args must be switches and cannot override port/profile/process identity")
    return [a.format(port=_port(port), profile=str(profile)) for a in LEVELS[level]] + extras + ["about:blank"]


def _load_registry(wd: pathlib.Path) -> dict:
    path = wd / REGISTRY
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid CDP registry")
    return data


def _save_registry(wd: pathlib.Path, reg: dict) -> None:
    path = wd / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _record(wd, port, entry):
    with _REG_LOCK:
        reg = _load_registry(wd)
        reg[str(port)] = entry
        _save_registry(wd, reg)


def _owned_identity(wd, port, entry):
    if not entry.get("owned") or entry.get("workdir") != str(wd) or not entry.get("pid"):
        raise PermissionError(f"port {port}: no owned process registered for this workdir")
    info = process_info(entry["pid"])
    if info is not None and not process_matches(info, entry, port):
        raise PermissionError(f"port {port}: PID/binary/profile/port identity mismatch; refusing termination")
    if info is not None and _open(port) and entry["pid"] not in listener_pids(port):
        raise PermissionError(f"port {port}: registered PID does not own the listener")
    return info


def kill_port(port: int, log=None, wd: pathlib.Path | None = None) -> int:
    wd, port = _scope(wd), _port(port)
    entry = _load_registry(wd).get(str(port), {})
    info = _owned_identity(wd, port, entry)
    if info is None:
        if _open(port):
            raise PermissionError(f"port {port}: registered root exited but another listener exists; refusing termination")
        entry.update(owned=False, ok=False, state="exited")
        _record(wd, port, entry)
        return 0
    # Root PID only, never image-name, listener substring or unverified process tree.
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(entry["pid"]), "/F"],
                                stdin=subprocess.DEVNULL, timeout=20)
        if result.returncode:
            raise RuntimeError(f"taskkill failed for verified PID {entry['pid']}")
    else:
        os.kill(entry["pid"], signal.SIGTERM)
    entry.update(owned=False, ok=False, state="stopped")
    _record(wd, port, entry)
    if log:
        log.info(f"Stopped verified CDP root PID {entry['pid']} port={port}")
    return 1


def launch_one(wd: pathlib.Path, port: int, level: int | None = None,
               profile: pathlib.Path | None = None, fresh: bool = False,
               wait: float = 30.0, log=None, tag: str = "", browser=None, extra_args=None) -> bool:
    wd, port = _scope(wd), _port(port)
    log = log or get_log("cdp_pool", wd=wd)
    if _open(port):
        probe = probe_endpoint(port)
        entry = _load_registry(wd).get(str(port), {})
        owned = False
        if entry.get("owned"):
            try:
                owned = _owned_identity(wd, port, entry) is not None
            except (PermissionError, RuntimeError, OSError):
                pass
        if browser is not None:
            selected = find_browser(browser)
            if not owned or normalize_path(selected) != normalize_path(entry["binary"]):
                raise PermissionError(f"port {port}: existing endpoint {probe['endpoint_version']['Browser']} cannot be verified as --browser {selected}; refusing attach")
        # Existing registry is untouched. In particular attach is never registered owned.
        log.info(f"port={port} {'owned reuse' if owned else 'non-owned attach'} | {probe['endpoint_version']['Browser']} | DOM usable")
        return True
    previous = _load_registry(wd).get(str(port), {})
    restarting = bool(previous.get("owned"))
    if restarting:
        if _owned_identity(wd, port, previous) is not None:
            raise PermissionError(f"port {port}: registered browser root still running without a ready listener; use status/renew")
        if browser is None:
            browser = previous.get("binary")
            if not browser:
                raise PermissionError("Owned restart has no registered binary; refusing fallback")
        if level is None:
            level = previous.get("level")
        if extra_args is None:
            extra_args = previous.get("extra_args", [])
    binary = find_browser(browser)
    level = default_level() if level is None else level
    if restarting and profile is None and not tag and normalize_path(binary) == normalize_path(previous["binary"]):
        profile = pathlib.Path(previous["profile"])
    prof = pathlib.Path(profile).resolve() if profile is not None else profile_for(wd, port, tag, binary)
    if prof.name.startswith(".edge-cdp") and browser_brand(binary) != "edge":
        raise ValueError("Legacy Edge profile cannot be used with another browser brand")
    if fresh:
        if not _managed(wd, prof, port, binary):
            raise PermissionError("fresh is permitted only for a managed workdir profile")
        # Never delete profiles (including previous managed profiles).
        prof = profile_for(wd, port, tag=f"{tag + '-' if tag else ''}{uuid.uuid4().hex[:12]}", browser=binary)
    args = build_args(port, prof, level, extra_args)
    prof.mkdir(parents=True, exist_ok=True)
    logdir = wd / ".work_cdp/logs"
    logdir.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
    with (logdir / f"browser_{port}.log").open("a", encoding="utf-8", errors="replace") as output:
        proc = subprocess.Popen([binary, *args], stdout=output, stderr=output,
                                stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    entry = {"binary": binary, "profile": str(prof), "pid": proc.pid,
             "owned": True, "workdir": str(wd), "level": level,
             "extra_args": _extra_args(extra_args), "args": args,
             "endpoint": f"http://127.0.0.1:{port}", "ok": False, "state": "starting"}
    # Record launch identity immediately; failed readiness is still an owned root.
    try:
        info = process_info(proc.pid)
    except Exception:
        info = None
    if info:
        entry["created"] = info.get("created")
    _record(wd, port, entry)
    deadline, last_error = time.monotonic() + wait, "endpoint unavailable"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            last_error = f"browser root exited ({proc.returncode})"
            break
        if _open(port):
            try:
                info = process_info(proc.pid)
                if not process_matches(info, entry, port) or proc.pid not in listener_pids(port):
                    raise RuntimeError("launched root process identity cannot be verified")
                probe = probe_endpoint(port, timeout=min(4, max(0.2, deadline - time.monotonic())))
                entry.update(probe, ok=True, state="ready")
                _record(wd, port, entry)
                log.ok(f"CDP ready port={port} binary={binary} | {probe['endpoint_version']['Browser']} | DOM usable")
                return True
            except Exception as exc:
                last_error = str(exc)
        time.sleep(0.25)
    entry.update(ok=False, state="failed", error=last_error)
    _record(wd, port, entry)
    log.error(f"port {port} not ready: {last_error}; see {logdir / f'browser_{port}.log'}")
    return False


def ensure(wd: pathlib.Path | None = None, n: int = 1, base_port: int = 9222,
           levels: list[int] | None = None, log=None, fresh: bool = False,
           browser=None, extra_args=None) -> list[int]:
    wd = _scope(wd)
    if n < 1:
        raise ValueError("n must be positive")
    levels = levels or [None]  # Each launch inherits an owned restart's level or runtime default.
    log = log or get_log("cdp_pool", wd=wd)
    ports = []
    # Sequential registration avoids cross-thread ownership updates and keeps failures clear.
    for i in range(n):
        port = _port(base_port + i)
        if launch_one(wd, port, levels[i % len(levels)], fresh=fresh, log=log,
                      browser=browser, extra_args=extra_args):
            ports.append(port)
    return ports


def alive_ports(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8) -> list[int]:
    return [p for p in range(base_port, base_port + span) if is_alive(p)]


def status(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8) -> list[dict]:
    wd = _scope(wd)
    reg = _load_registry(wd)
    out = []
    for port in range(base_port, base_port + span):
        entry = dict(reg.get(str(port), {}))
        entry.update(port=port, alive=_open(port), owned=False)
        if reg.get(str(port), {}).get("owned"):
            try:
                entry["owned"] = _owned_identity(wd, port, reg[str(port)]) is not None
            except Exception as exc:
                entry["identity_error"] = str(exc)
        if entry["alive"]:
            try:
                entry.update(probe_endpoint(port))
                entry["browser"] = entry["endpoint_version"]["Browser"]
            except Exception as exc:
                entry.update(dom_usable=False, error=str(exc))
            if not entry["owned"]:
                # Registry is historical; do not attribute it to the current listener.
                for key in ("binary", "profile", "pid", "args", "created"):
                    entry.pop(key, None)
                entry["state"] = "non-owned attach"
        out.append(entry)
    return out


def down(wd: pathlib.Path | None = None, base_port: int = 9222, span: int = 8, log=None) -> int:
    wd, total = _scope(wd), 0
    reg = _load_registry(wd)
    refused = []
    for port in range(base_port, base_port + span):
        entry = reg.get(str(port), {})
        if entry.get("owned"):
            try:
                total += kill_port(port, log=log, wd=wd)
            except (PermissionError, RuntimeError, OSError) as exc:
                refused.append(str(exc))
        elif _open(port):
            refused.append(f"port {port}: unregistered/non-owned listener; refusing termination")
    if refused:
        raise PermissionError("; ".join(refused))
    return total


def renew(wd: pathlib.Path | None = None, port: int = 9222, level: int | None = None,
          tag: str = "", log=None, browser=None, extra_args=None) -> bool:
    wd, port = _scope(wd), _port(port)
    entry = _load_registry(wd).get(str(port), {})
    _owned_identity(wd, port, entry)  # Always require a prior owned record.
    binary = find_browser(browser if browser is not None else entry["binary"])
    if not _managed(wd, entry["profile"], port, entry["binary"]):
        raise PermissionError("renew requires a managed profile; user profiles are preserved")
    level = entry.get("level", 0) if level is None else level
    extras = entry.get("extra_args", []) if extra_args is None else extra_args
    # Validate all replacements before touching the old process.
    profile_for(wd, port, tag, binary)
    build_args(port, pathlib.Path(entry["profile"]), level, extras)
    kill_port(port, log=log, wd=wd)
    deadline = time.monotonic() + 10
    while _open(port) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _open(port):
        raise RuntimeError(f"port {port} remains occupied after owned root shutdown; refusing replacement")
    return launch_one(wd, port, level=level, tag=tag, fresh=True, log=log,
                      browser=binary, extra_args=extras)


def _dir_size_bytes(p: pathlib.Path) -> int:
    total = 0
    try:
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def clean(wd: pathlib.Path | None = None, log=None,
          apply: bool = False, purge: bool = False) -> dict:
    """把散落在**输出根**的历史 `.edge-cdp-*` 收进 `.work_cdp/cdp_profiles/`。

    为什么需要它：profile 每个数十至数百 MB 且含 Cookie，长期堆在交付目录里
    既占空间又是凭据泄露面。`profile_for()` 已把**新建**的 profile 放到
    `.work_cdp/` 下，但**历史遗留**仍在输出根。

    **默认只报告，不动盘**（dry-run）。两级显式动作：

    | 参数 | 行为 | 风险 |
    |---|---|---|
    | （无） | 只列出：位置、端口、大小、是否在用 | 零 |
    | `--apply` | **移动**旧目录到 `.work_cdp/cdp_profiles/` | 低：文件与 Cookie 一并保留，`profile_for()` 之后自然命中新位置 |
    | `--purge` | 兼容保留参数名，但安全拒绝删除 profile | 不删除用户数据 |

    **永不**移动/删除「有存活实例」的 profile —— Edge 正在使用它。
    """
    wd = _scope(wd)
    if purge:
        raise PermissionError("Profile deletion is disabled; clean --apply moves profiles and preserves data")
    home = wd / PROFILE_HOME
    # 名字可能撞车的都当历史残留（输出根下以 .edge-cdp 开头者）
    legacy = sorted(p for p in wd.glob(".edge-cdp*") if p.is_dir())
    orphans = sorted(p for p in home.glob(".edge-cdp*") if p.is_dir()) if home.is_dir() else []

    report: list[dict] = []
    moved = purged = 0
    reg = _load_registry(wd)
    candidate_ports = {_port_of_profile_name(p.name) for p in legacy + orphans}
    candidate_ports.update(int(p) for p in reg if str(p).isdigit())
    live = {p for p in candidate_ports if p and _open(p)}
    for entry in reg.values():
        if entry.get("owned") and entry.get("pid"):
            info = process_info(entry["pid"])
            if info is not None:
                values = __import__("browser_runtime").switch_values(info.get("args", []), "--remote-debugging-port")
                live.update(int(p) for p in values if p.isdigit())

    def _record(p: pathlib.Path, where: str) -> dict:
        port = _port_of_profile_name(p.name)
        in_use = port in live
        entry = {"name": p.name, "where": where, "port": port,
                 "in_use": in_use, "mb": _dir_size_bytes(p)}
        report.append(entry)
        return entry

    if not legacy and not orphans:
        if log:
            log.info(f"无 profile 残留（输出根与 {PROFILE_HOME}/ 都干净）")
        return {"legacy": [], "profiles": [], "moved": 0, "purged": 0}

    for p in legacy:
        e = _record(p, "输出根")
        if e["in_use"]:
            if log:
                log.warn(f"跳过 {p.name}/：端口 {e['port']} 有存活实例，Edge 正在用它")
            continue
        if apply:
            home.mkdir(parents=True, exist_ok=True)
            dst = home / p.name
            if dst.exists():
                if log:
                    log.warn(f"跳过 {p.name}/：{PROFILE_HOME}/ 下已有同名目录")
                continue
            import shutil
            try:
                shutil.move(str(p), str(dst))
                moved += 1
                for record in reg.values():
                    if record.get("profile") and normalize_path(record["profile"]) == normalize_path(str(p)):
                        record["profile"] = str(dst.resolve())
                _save_registry(wd, reg)
                if log:
                    log.ok(f"已收拢 {p.name}/ → {PROFILE_HOME}/（{_mb(e['mb'])}，Cookie 保留）")
            except OSError as ex:
                if log:
                    log.error(f"移动 {p.name}/ 失败：{str(ex)[:80]}")
    for p in orphans:
        e = _record(p, PROFILE_HOME)
        if purge and not e["in_use"] and str(e["port"]) not in reg:
            import shutil
            shutil.rmtree(p, ignore_errors=True)
            purged += 1
            if log:
                log.ok(f"已删除孤儿 profile {p.name}/（{_mb(e['mb'])}）")
        elif e["in_use"]:
            if log:
                log.info(f"保留 {p.name}/：端口 {e['port']} 存活")
    if log:
        log.info(f"profile 清点：输出根 {len(legacy)} 个 / 收拢区 {len(orphans)} 个；"
                 f"本次移动 {moved}、删除 {purged}"
                 + ("" if (apply or purge) else "（dry-run，未改盘；确认后加 --apply）"))
    return {"legacy": [r for r in report if r["where"] == "输出根"],
            "profiles": [r for r in report if r["where"] == PROFILE_HOME],
            "moved": moved, "purged": purged}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("up", "status", "down", "renew", "kill", "clean"):
        p = sub.add_parser(command)
        p.add_argument("--workdir", type=pathlib.Path)
        if command in ("up", "renew"):
            p.add_argument("--browser", help="Exact browser executable; invalid paths fail closed")
        if command in ("up", "status", "down"):
            p.add_argument("--base", type=int, default=9222)
        if command in ("status", "down"):
            p.add_argument("--span", type=int, default=8)
        if command in ("renew", "kill"):
            p.add_argument("--port", type=int, default=9222)
        if command == "clean":
            p.add_argument("--apply", action="store_true")
            p.add_argument("--purge", action="store_true", help="Deprecated: deletion is refused")
        if command == "up":
            p.add_argument("--n", type=int, default=1)
            p.add_argument("--levels", type=lambda s: [int(x) for x in s.split(",")])
            p.add_argument("--fresh", action="store_true")
        if command == "renew":
            p.add_argument("--level", type=int)
            p.add_argument("--tag", default="")
    args = parser.parse_args(argv)
    try:
        wd = _scope(args.workdir)
        if args.command == "clean":
            print(json.dumps(clean(wd, apply=args.apply, purge=args.purge), ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            print(json.dumps(status(wd, args.base, args.span), ensure_ascii=False, indent=2))
            return 0
        if args.command == "up":
            ports = ensure(wd, args.n, args.base, args.levels, fresh=args.fresh, browser=args.browser)
            print(f"Ready ports: {ports}")
            return 0 if len(ports) == args.n else 1
        if args.command == "renew":
            return 0 if renew(wd, args.port, args.level, args.tag, browser=args.browser) else 1
        if args.command == "down":
            print(f"Stopped: {down(wd, args.base, args.span)}")
            return 0
        kill_port(args.port, wd=wd)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
