# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 子代理批次代理进度轮询器（取代固定 sleep 150/200）
#
# 干什么：主 agent 分派一批 subagent 后，**不要盲等固定秒数**。
#         本脚本读 tasks/ 与 results/ 的计数，算出「这一批还剩几篇没回来」，
#         并给出「下一批该派几个、该等多久」的建议。
#
# 用法：
#   uv run python scripts/agent_poll.py                       # 全部阶段
#   uv run python scripts/agent_poll.py --stage H             # 只看阶段 H（interpret/）
#   uv run python scripts/agent_poll.py --stage H2 --json     # 机读，供 agent 判定
#   uv run python scripts/agent_poll.py --expect 5            # 声明的在飞数量
#
# 退出码（供 agent 直接判定，**不解析 stdout**）：
#   0 = 全部完成（可以进入 build/汇总）
#   3 = 仍在进行（按建议的间隔再来一次）
#   4 = 有卡被拒收/损坏（需要重派，附清单）
#   2 = 用法错误或找不到产物
#
# 设计约束：
#   · **零网络、零副作用**（只读文件；不写任何东西）
#   · **不猜**：只报实际数到的文件数；缺卡就是缺卡
#   · 间隔建议由「在飞数量」推出，而不是固定值
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import sys
import time

from logutil import run_root as _run_root  # noqa: E402


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与 fetch_direct.py 同名函数逐字一致）。"""
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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"

# 阶段 → (目录名, 中文标签, 建议的单批上限)
STAGES: dict[str, tuple[str, str, int]] = {
    "H": ("interpret", "阶段 H 作者论文解读", 6),
    "H2": ("refs", "阶段 H2 研究方向综述", 6),
    "R2": ("reviews", "阶段 R2 领域概貌", 4),
}

# 轮询间隔（秒）—— 按在飞数量自适应，取代固定 sleep 150/200
POLL_MIN, POLL_MAX = 30, 60


def _slug_free(path: pathlib.Path) -> bool:
    """结果卡是否可用：能 json.loads 且非空。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return bool(d)
    except Exception:
        return False


def _parse_failed_cards(stage_dir: pathlib.Path) -> set[str]:
    """读 manifest.json 里被拒收/损坏的卡名（存在则用，不存在则空集）。"""
    mf = stage_dir / "manifest.json"
    if not mf.exists():
        return set()
    try:
        d = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out: set[str] = set()
    for key in ("rejected", "invalid", "placeholder_rejected", "bad_cards"):
        v = d.get(key)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    out.add(it)
                elif isinstance(it, dict):
                    n = it.get("result") or it.get("file") or it.get("name")
                    if n:
                        out.add(pathlib.Path(str(n)).name)
    return out


def survey(stage: str, expect: int) -> dict:
    dirname, label, batch_cap = STAGES[stage]
    d = CDPDIR / dirname
    tdir, rdir = d / "tasks", d / "results"

    tasks = sorted(p.name for p in tdir.glob("*.json")) if tdir.is_dir() else []
    raw_results = sorted(p.name for p in rdir.glob("*.json")) if rdir.is_dir() else []
    good = [n for n in raw_results if _slug_free(rdir / n)]

    done = set(good)
    missing = [n for n in tasks if n not in done]
    # 孤儿卡：results 里有、tasks 里没有 -> 上一轮残留，不计入本轮
    orphan = [n for n in raw_results if n not in set(tasks)]
    bad = [n for n in raw_results if n not in done]
    rejected = sorted(_parse_failed_cards(d))

    total = len(tasks)
    n_done = len(tasks) - len(missing)
    in_flight = max(0, len(missing) - max(0, expect))

    # 建议：下一批派几个 = min(剩余, 单批上限, 在飞控制)
    remaining = len(missing)
    suggest_batch = 0 if remaining == 0 else min(remaining, batch_cap)

    if remaining == 0 and total > 0:
        code, verdict = 0, "complete"
    elif remaining == 0 and total == 0:
        code, verdict = 2, "no-tasks"
    elif bad or rejected:
        code, verdict = 4, "needs-remake"
    else:
        code, verdict = 3, "in-progress"

    # 间隔：按在飞数量自适应，落在 [30, 60]
    if remaining <= 2:
        interval = POLL_MIN
    elif remaining <= 6:
        interval = 45
    else:
        interval = POLL_MAX

    return {
        "stage": stage,
        "label": label,
        "dir": str(d),
        "schema": "flow1-agent-poll/1.0",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tasks_total": total,
        "results_good": n_done,
        "results_raw": len(raw_results),
        "missing_count": remaining,
        "missing": missing[:20],
        "in_flight_estimate": in_flight,
        "unreadable": bad[:20],
        "orphan_results": orphan[:20],
        "rejected": rejected[:20],
        "progress_pct": round(n_done / total * 100, 1) if total else 0.0,
        "verdict": verdict,
        "suggest_batch_size": suggest_batch,
        "suggest_interval_s": interval,
        "exit_code": code,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="agent_poll.py",
        description="子代理批次进度轮询（取代固定 sleep 150/200；只读、零网络）")
    ap.add_argument("--stage", choices=sorted(STAGES), help="只看某个阶段")
    ap.add_argument("--expect", type=int, default=0,
                    help="本批已分派但尚未回收的数量（用于估算在飞）；0=不估算")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（机读）")
    args = ap.parse_args(argv)

    stages = [args.stage] if args.stage else sorted(STAGES)
    reports = [survey(s, max(0, args.expect)) for s in stages]

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0],
                         ensure_ascii=False, indent=1))
        return max(r["exit_code"] for r in reports) if reports else 2

    print()
    print("═" * 66)
    print("  子代理批次进度")
    print("═" * 66)
    any_tasks = False
    for r in reports:
        if r["tasks_total"] == 0 and r["results_raw"] == 0:
            continue
        any_tasks = True
        print(f"  【{r['label']}】  {r['dir']}")
        print(f"    任务包 {r['tasks_total']}  已回收 {r['results_good']}"
              f"  缺 {r['missing_count']}   进度 {r['progress_pct']}%")
        if r["unreadable"]:
            print(f"    [WARN] 不可解析的结果卡 {len(r['unreadable'])}："
                  f"{', '.join(r['unreadable'][:3])}")
        if r["orphan_results"]:
            print(f"    [INFO] 孤儿卡（上轮残留，不计入）{len(r['orphan_results'])}")
        if r["rejected"]:
            print(f"    [STOP] 被拒收 {len(r['rejected'])}："
                  f"{', '.join(r['rejected'][:3])}")
        if r["missing_count"]:
            print(f"    → 建议：下一批派 {r['suggest_batch_size']} 个，"
                  f"{r['suggest_interval_s']} 秒后再查")
        else:
            print("    → 已齐，可进入汇总（build）")
        print()
    if not any_tasks:
        print("  未发现任何任务包 —— 先跑 prepare 脚本组装任务包")
        print()
        return 2

    code = max(r["exit_code"] for r in reports)
    hint = {0: "全部完成，可 build", 3: "仍在进行，按建议间隔再来",
            4: "有坏卡，需重派（见上）", 2: "无任务包"}[code]
    print(f"  结论（退出码 {code}）：{hint}")
    print("═" * 66)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
