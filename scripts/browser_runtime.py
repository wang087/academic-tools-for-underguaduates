# -*- coding: utf-8 -*-
"""Shared Chromium discovery, read-only CDP probing and exact process identity.

No browser is launched or process terminated by this module. Environment settings
are deliberately read at call time. Custom builds belong in --browser/PAPER_BROWSER,
never in the generic candidate list.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request


def _executable(value: str, source: str) -> str:
    value = os.path.expandvars(os.path.expanduser(str(value).strip().strip('"')))
    if not value:
        raise FileNotFoundError(f"{source}: empty browser path")
    p = pathlib.Path(value)
    found = str(p.resolve()) if p.is_file() else shutil.which(value)
    if not found or not pathlib.Path(found).is_file():
        raise FileNotFoundError(f"{source}: browser executable not found: {value}")
    if os.name != "nt" and not os.access(found, os.X_OK):
        raise PermissionError(f"{source}: browser is not executable: {found}")
    return str(pathlib.Path(found).resolve())


def browser_candidates() -> list[str]:
    """Installed Chrome, Chromium, then Edge; no user-specific custom builds."""
    candidates = []
    if os.name == "nt":
        for relative in ("Google/Chrome/Application/chrome.exe",
                         "Chromium/Application/chrome.exe",
                         "Microsoft/Edge/Application/msedge.exe"):
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(key)
                if root:
                    candidates.append(str(pathlib.Path(root) / relative))
    elif os.sys.platform == "darwin":
        candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Chromium.app/Contents/MacOS/Chromium",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    candidates += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                   "microsoft-edge", "microsoft-edge-stable"]
    return candidates


def find_browser(binary: str | os.PathLike | None = None) -> str:
    """Explicit > PAPER_BROWSER > CHROME_PATH > EDGE_PATH > installed candidates.

    An invalid explicit or configured path fails closed instead of falling back.
    """
    if binary is not None:
        return _executable(os.fspath(binary), "--browser")
    for key in ("PAPER_BROWSER", "CHROME_PATH", "EDGE_PATH"):
        if os.environ.get(key, "").strip():
            return _executable(os.environ[key], key)
    for candidate in browser_candidates():
        try:
            return _executable(candidate, "installed browser")
        except (FileNotFoundError, PermissionError):
            continue
    raise FileNotFoundError("No Chrome/Chromium/Edge found; set PAPER_BROWSER or pass --browser PATH")


def find_edge(binary=None) -> str:
    """Historical API name; now uses the shared browser selection policy."""
    return find_browser(binary)


def browser_brand(binary: str) -> str:
    name = pathlib.Path(binary).name.lower()
    if "msedge" in name or "microsoft-edge" in name or "microsoft edge" in name:
        return "edge"
    if "chromium" in name:
        return "chromium"
    if "chrome" in name:
        return "chrome"
    return "chromium-compatible"


def normalize_path(value: str) -> str:
    return os.path.normcase(str(pathlib.Path(value).expanduser().resolve()))


def split_command_line(command: str) -> list[str]:
    """Parse Windows command lines without requiring ctypes/COM in the host."""
    if os.name != "nt":
        return shlex.split(command)
    # CommandLineToArgvW quoting rules (including backslashes before quotes).
    args, item, quoted, active, i = [], [], False, False, 0
    while i < len(command):
        c = command[i]
        if c.isspace() and not quoted:
            if active:
                args.append("".join(item)); item = []; active = False
            i += 1; continue
        active = True
        if c == "\\":
            j = i
            while j < len(command) and command[j] == "\\":
                j += 1
            count = j - i
            if j < len(command) and command[j] == '"':
                item.extend("\\" * (count // 2))
                if count % 2:
                    item.append('"')
                else:
                    quoted = not quoted
                i = j + 1; continue
            item.extend("\\" * count); i = j; continue
        if c == '"':
            quoted = not quoted
        else:
            item.append(c)
        i += 1
    if active:
        args.append("".join(item))
    return args


def switch_values(args: list[str], name: str) -> list[str]:
    values = []
    for i, arg in enumerate(args):
        if arg.startswith(name + "="):
            values.append(arg[len(name) + 1:])
        elif arg == name and i + 1 < len(args):
            values.append(args[i + 1])
    return values


def process_info(pid: int) -> dict | None:
    """Query the exact PID. Windows uses CIM; subprocess output goes to a file.

    Permission/query failures are not proof that a process exited and propagate.
    """
    pid = int(pid)
    if pid <= 0:
        raise ValueError("PID must be positive")
    if os.name == "nt":
        script = (f"[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' -ErrorAction Stop; "
                  "if ($null -eq $p) { 'null' } else { "
                  "$p | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate | "
                  "ConvertTo-Json -Compress }")
        with tempfile.TemporaryFile(mode="w+b") as output:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                    stdout=output, stderr=output, stdin=subprocess.DEVNULL, timeout=15)
            output.seek(0)
            text = output.read().decode("utf-8-sig", errors="replace").strip()
        if result.returncode:
            raise RuntimeError(f"CIM process query failed: {text[:300]}")
        data = json.loads(text)
        if data is None:
            return None
        return {"pid": int(data["ProcessId"]), "binary": data.get("ExecutablePath"),
                "args": split_command_line(data.get("CommandLine") or ""),
                "created": str(data.get("CreationDate") or "")}
    proc = pathlib.Path("/proc") / str(pid)
    if pathlib.Path("/proc").is_dir():
        try:
            raw = (proc / "cmdline").read_bytes()
            args = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
            stat = (proc / "stat").read_text()
            return {"pid": pid, "binary": str((proc / "exe").resolve(strict=True)), "args": args,
                    "created": stat[stat.rfind(")") + 2:].split()[19]}
        except FileNotFoundError:
            return None
    raise RuntimeError("Safe process identity query is unavailable on this platform; refusing termination")


def process_matches(info: dict | None, entry: dict, port: int) -> bool:
    """Require exact PID, binary, profile, port and root-process identity."""
    if not info or not entry.get("owned") or not entry.get("binary") or not entry.get("profile"):
        return False
    args = info.get("args") or []
    if not info.get("binary") or info.get("pid") != entry.get("pid"):
        return False
    if normalize_path(info["binary"]) != normalize_path(entry["binary"]):
        return False
    if not args or normalize_path(args[0]) != normalize_path(entry["binary"]):
        return False
    if any(a == "--type" or a.startswith("--type=") for a in args):
        return False
    if switch_values(args, "--remote-debugging-port") != [str(port)]:
        return False
    profiles = switch_values(args, "--user-data-dir")
    if len(profiles) != 1 or normalize_path(profiles[0]) != normalize_path(entry["profile"]):
        return False
    if entry.get("created") and entry["created"] != info.get("created"):
        return False
    return True


def _ws_call(url: str, method: str, params=None, timeout: float = 4) -> dict:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("CDP DOM probe requires websocket-client; install it in the chosen Python environment") from exc
    ws = websocket.create_connection(url, timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ws.settimeout(max(0.1, deadline - time.monotonic()))
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)
    finally:
        ws.close()


def probe_endpoint(port: int, timeout: float = 4) -> dict:
    """Read Browser.getVersion and DOM.getDocument; never open/navigate tabs."""
    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}{path}", timeout=timeout) as r:
            return json.load(r)
    ver = get("/json/version")
    if not ver.get("Browser") or not ver.get("webSocketDebuggerUrl"):
        raise RuntimeError("Port is not a browser CDP endpoint")
    details = _ws_call(ver["webSocketDebuggerUrl"], "Browser.getVersion", timeout=timeout)
    pages = [t for t in get("/json/list") if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    errors = []
    for page in pages:
        try:
            root = _ws_call(page["webSocketDebuggerUrl"], "DOM.getDocument", {"depth": 0}, timeout)
            if root.get("root", {}).get("nodeId"):
                return {"endpoint_version": ver, "browser_version": details, "dom_usable": True,
                        "tabs": len(pages)}
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Browser CDP responds but no usable page DOM: " + ("; ".join(errors) or "no page targets"))


def listener_pids(port: int) -> set[int]:
    """Exact local listening port, never substring match; file-backed output."""
    if os.name != "nt":
        raise RuntimeError("Listener PID verification currently requires Windows")
    with tempfile.TemporaryFile(mode="w+b") as output:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], stdout=output,
                                stderr=output, stdin=subprocess.DEVNULL, timeout=15)
        output.seek(0)
        text = output.read().decode(errors="replace")
    if result.returncode:
        raise RuntimeError("Listener PID query failed")
    return parse_listener_pids(text, port)


def parse_listener_pids(text: str, port: int) -> set[int]:
    pids = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() not in ("LISTENING", "LISTEN"):
            continue
        try:
            if int(parts[1].rsplit(":", 1)[1]) == int(port):
                pids.add(int(parts[-1]))
        except (ValueError, IndexError):
            pass
    return pids
