# -*- coding: utf-8 -*-
"""统一子代理回收质量门；只读任务/结果并写阶段级 gate 报告。"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from agent_poll import CDPDIR, STAGES, survey


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="统一子代理回收质量门（只读 tasks/results，写 gate_report.json）")
    ap.add_argument("--stage", choices=sorted(STAGES), required=True)
    ap.add_argument("--expect", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = survey(args.stage, max(0, args.expect))
    out = CDPDIR / STAGES[args.stage][0] / "gate_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"质量门报告：{out}")
        print(f"阶段 {args.stage}：{report['verdict']}，"
              f"任务 {report['tasks_total']}，结果 {report['results_good']}，"
              f"缺失 {report['missing_count']}，类型错误 {report['shape_bad_count']}")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
