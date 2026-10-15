# -*- coding: utf-8 -*-
"""交付状态的最小统一读写层。"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime


STATUS_FILE = "run_status.json"


def status_path(wd: pathlib.Path) -> pathlib.Path:
    return wd / ".work_cdp" / STATUS_FILE


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def calculate_status(*, has_output: bool, expected: int = 0,
                     completed: int = 0, blocked: int = 0,
                     failed: int = 0) -> str:
    """根据交付数量给出保守的全局状态。"""
    expected = _int(expected)
    completed = _int(completed)
    blocked = _int(blocked)
    failed = _int(failed)
    if not has_output:
        return "NO_OUTPUT"
    if blocked or failed or (expected and completed < expected):
        return "PARTIAL"
    return "COMPLETE"


def write_status(wd: pathlib.Path, *, status: str, stage: str = "",
                 expected: int = 0, completed: int = 0, blocked: int = 0,
                 failed: int = 0, reasons: list[str] | None = None) -> pathlib.Path:
    path = status_path(wd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "stage": stage,
        "expected": _int(expected),
        "completed": _int(completed),
        "blocked": _int(blocked),
        "failed": _int(failed),
        "reasons": [str(x) for x in (reasons or []) if str(x).strip()],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def read_status(wd: pathlib.Path) -> dict:
    path = status_path(wd)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
