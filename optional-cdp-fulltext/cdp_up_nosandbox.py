# -*- coding: utf-8 -*-
"""Explicit opt-in compatibility CLI for --no-sandbox.

Normal workflows should use cdp_pool.py up. This CLI delegates ownership,
profiles, readiness and registry to the shared pool. Importing it has no browser
side effects. renderer_count keeps its historical signature but now reports a
DOM capability indicator (1 usable, -1 unavailable), not an OS process count.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

_CORE_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(_CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CORE_SCRIPTS))

import cdp_pool
from browser_runtime import probe_endpoint
from logutil import workdir as _workdir

EXTRA_ARGS = ["--no-sandbox"]


def alive(port: int, timeout: float = 2.0) -> bool:
    try:
        return bool(probe_endpoint(port, timeout=timeout).get("dom_usable"))
    except Exception:
        return False


def _renderer_count_once(port: int) -> int:
    return 1 if alive(port) else -1


def renderer_count(port: int, attempts: int = 3) -> int:
    """Compatibility capability indicator: >0 means a real page DOM responds."""
    for attempt in range(max(1, attempts)):
        result = _renderer_count_once(port)
        if result > 0:
            return result
        if attempt + 1 < attempts:
            time.sleep(0.2)
    return -1


def launch(wd: pathlib.Path, port: int, level: int | None, wait: float = 30.0,
           browser=None, extra_args=None) -> bool:
    # This function is the explicit opt-in entrypoint; generic pool defaults stay sandboxed.
    extras = cdp_pool._extra_args(extra_args)
    extras = list(dict.fromkeys([*extras, *EXTRA_ARGS]))
    return cdp_pool.launch_one(wd, port, level=level, wait=wait,
                               browser=browser, extra_args=extras)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--ports", help="Explicit ports, e.g. 9222,9223")
    ap.add_argument("--level", type=int, default=None)
    ap.add_argument("--browser")
    ap.add_argument("--workdir", type=pathlib.Path)
    args = ap.parse_args(argv)
    try:
        wd = args.workdir or _workdir()
        if args.n < 1:
            raise ValueError("n must be positive")
        ports = ([int(p) for p in args.ports.split(",") if p.strip()]
                 if args.ports else list(range(9222, 9222 + args.n)))
        if not ports:
            raise ValueError("ports must not be empty")
        print("Explicit --no-sandbox requested; using owned CDP pool and DOM readiness probe.")
        good = [p for p in ports if launch(wd, p, args.level, browser=args.browser)]
        print(f"Ready ports: {good}")
        return 0 if len(good) == len(ports) else 1
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
