# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 操作流水查看器 —— 回溯「这一轮到底做了什么」
#
# 读取 <WORKDIR>/.work_cdp/logs/run_journal.jsonl（由 logutil.journal() 追加），
# 按时间顺序打印成一张可读的表；支持按阶段/动作过滤与只看末尾若干条。
#
# 与 per-script 日志配合使用：
#   · 先在这里定位「哪一步、什么参数、结果如何」；
#   · 再打开 `.work_cdp/logs/latest_<脚本名>.log` 看该步的细节。
#
# 用法：
#   uv run python journal_view.py                    # 全部
#   uv run python journal_view.py --stage=E          # 只看阶段 E
#   uv run python journal_view.py --action=download  # 动作名包含 download
#   uv run python journal_view.py --tail=40          # 只看最后 40 条
#   uv run python journal_view.py --json             # 原样输出 JSONL（供二次处理）
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
from datetime import datetime

from logutil import run_root as _run_root  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
JOURNAL = WD / ".work_cdp" / "logs" / "run_journal.jsonl"

# 展示时优先出现的字段（其余字段按字母序附在后面）
PREFERRED = ("doi", "year", "title", "script", "url", "result", "count",
             "elapsed_s", "pages", "size_kb", "reason", "note")


def arg(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def main() -> int:
    if not JOURNAL.exists():
        print(f"[X] 找不到操作流水：{JOURNAL}")
        print("  journal 由各脚本通过 logutil.journal() 追加；"
              "若刚跑完流程仍无此文件，说明相关脚本尚未接入 journal。")
        return 1

    raw = arg("json") is not None or "--json" in sys.argv
    stage = arg("stage", "")
    action = arg("action", "")
    tail = int(arg("tail", "0") or 0)

    recs, bad = [], 0
    for line in JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            bad += 1                     # 半截行（进程被强杀时会出现）
            continue
        if stage and str(r.get("stage", "")) != stage:
            continue
        if action and action.lower() not in str(r.get("action", "")).lower():
            continue
        recs.append(r)

    if raw:
        for r in recs:
            print(json.dumps(r, ensure_ascii=False))
        return 0

    if tail > 0:
        recs = recs[-tail:]

    if not recs:
        print(f"（无匹配记录；文件共 {JOURNAL.stat().st_size} 字节）")
        return 0

    print(f"# 操作流水 {JOURNAL}")
    print(f"# 匹配 {len(recs)} 条"
          + (f"（过滤：stage={stage} action~{action}）" if (stage or action) else "")
          + (f"；另有 {bad} 行无法解析（多为进程被强杀留下的半截行）" if bad else ""))
    print()
    print(f"{'时间':<9} {'阶段':<4} {'动作':<26} 详情")
    print("-" * 118)
    for r in recs:
        t = str(r.get("ts", ""))[11:19]
        detail = []
        for k in PREFERRED:
            if k in r:
                v = str(r[k])
                detail.append(f"{k}={v[:60]}")
        for k in sorted(r):
            if k in ("ts", "epoch", "stage", "action") or k in PREFERRED:
                continue
            detail.append(f"{k}={str(r[k])[:50]}")
        print(f"{t:<9} {str(r.get('stage','')):<4} {str(r.get('action','')):<26} "
              f"{' | '.join(detail)}")

    # 阶段耗时小结（便于一眼看出卡在哪一步）
    if len(recs) >= 2:
        try:
            t0 = float(recs[0].get("epoch") or 0)
            t1 = float(recs[-1].get("epoch") or 0)
            if t0 and t1:
                print("-" * 118)
                print(f"跨度 {t1 - t0:.0f}s"
                      f"（{datetime.fromtimestamp(t0):%H:%M:%S} → "
                      f"{datetime.fromtimestamp(t1):%H:%M:%S}）")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
