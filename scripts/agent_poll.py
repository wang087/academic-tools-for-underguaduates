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
#   4 = 有卡被拒收/损坏/**容器类型不合规**（需要重派，附清单）
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


# ── 卡不可解析时的**归因**（转义校验）────────────────────────────────
#   为什么不能只报「损坏」：解读卡由**并行 subagent** 直接写 JSON 文本，
#   最常见的失败是**字符串里的引号没转义** —— 典型如把论文标题原样抄进去：
#       "one_line": "本文讨论 "cBAF" 复合物……"      ← 第二个 " 提前闭合字符串
#   此时 json.loads 抛的是 `Expecting ',' delimiter` / `Expecting property name…`，
#   读起来像"结构错了"，于是被笼统归为"损坏卡"重派 —— 而重派时若不告诉
#   subagent **是哪一行、哪个引号**，它很可能原样再写一遍。
#   所以这里做**定位级**归因：算出可疑行号 + 该行原文，并给出可执行的修法。
def _suspect_quote_pos(text: str):
    """扫描出**未转义引号的精确位置** `(line, col, next_char)`；没有则 None。

    判据（比"引号个数为奇数"可靠得多 —— 一行里有两处未转义引号时个数是偶数）：
    一个字符串**正常闭合**后，紧跟的第一个非空白字符只可能是
    `,` `}` `]` `:` 之一；若是字母/汉字，就说明这个 `"` 是**内容里的引号**
    被当成了字符串结尾 —— 即未转义引号。这也正是 `json.loads` 报
    `Expecting ',' delimiter` 的真实原因。
    """
    i, line, col, in_str, n = 0, 1, 0, False, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line, col, i = line + 1, 0, i + 1
            continue
        col += 1
        if in_str:
            if ch == "\\":
                i += 2
                col += 1
                continue
            if ch == '"':
                in_str = False
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] not in ",}]:":
                    return line, col, text[j]
        elif ch == '"':
            in_str = True
        i += 1
    return None


def _odd_quote_lines(text: str) -> list[int]:
    """返回「未转义双引号个数为奇数」的行号（1 基）。

    奇数个未转义 `"` 意味着该行的字符串**没有正常闭合** —— 是未转义引号
    最可靠的信号（比匹配异常文本来得稳）。`\\"` 视为已转义，不计。
    """
    out: list[int] = []
    for i, line in enumerate(text.splitlines(), 1):
        n, j = 0, 0
        while j < len(line):
            if line[j] == "\\":
                j += 2
                continue
            if line[j] == '"':
                n += 1
            j += 1
        if n % 2:
            out.append(i)
    return out


def diagnose_json(path: pathlib.Path) -> dict:
    """卡不可解析 → 给出**具体原因 + 行号 + 修法**；可解析则 ok=True。

    只读、零副作用。返回键：ok / reason / detail / line / col / excerpt / hint。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "reason": "unreadable", "detail": str(e)[:120],
                "line": None, "col": None, "excerpt": "", "hint": "文件读不到"}
    try:
        json.loads(raw)
        return {"ok": True, "reason": "", "detail": "", "line": None,
                "col": None, "excerpt": "", "hint": ""}
    except json.JSONDecodeError as e:
        line, col, msg = e.lineno, e.colno, e.msg
        lines = raw.splitlines()

        def _at(n: int) -> str:
            return lines[n - 1][:160] if 0 < n <= len(lines) else ""

        excerpt = _at(line)
        odd = _odd_quote_lines(raw)
        suspect = _suspect_quote_pos(raw)
        # 归因优先序：未转义引号（精确位置）→ 奇数引号行 → 裸控制字符 → 其它语法
        if suspect and abs(suspect[0] - line) <= 3:
            sline, scol, nxt = suspect
            return {"ok": False, "reason": "unescaped-quote", "detail": msg,
                    "line": sline, "col": scol, "excerpt": _at(sline),
                    "hint": f'第 {sline} 行第 {scol} 列：字符串在这里被一个**内容里的引号**'
                            f'提前闭合（紧跟 {nxt!r}，而合法字符只能是 , }} ] :）。'
                            f'把该引号写成 \\" ，或改用 json.dumps 生成整份卡。'}
        if odd and any(abs(line - L) <= 3 for L in odd):
            hit = min(odd, key=lambda L: abs(line - L))
            return {"ok": False, "reason": "unescaped-quote", "detail": msg,
                    "line": hit, "col": col, "excerpt": _at(hit),
                    "hint": f'第 {hit} 行双引号未闭合（字符串里有未转义的 "）。'
                            f'把该行内容里的 " 写成 \\" ，或改用 json.dumps 生成整份卡。'}
        if "Invalid control character" in msg:
            return {"ok": False, "reason": "raw-control-char", "detail": msg,
                    "line": line, "col": col, "excerpt": excerpt,
                    "hint": f"第 {line} 行字符串里有裸换行/制表符；改用 \\n / \\t 转义。"}
        if suspect:
            sline, scol, nxt = suspect
            return {"ok": False, "reason": "unescaped-quote", "detail": msg,
                    "line": sline, "col": scol, "excerpt": _at(sline),
                    "hint": f'第 {sline} 行第 {scol} 列：字符串被内容里的引号提前闭合'
                            f'（紧跟 {nxt!r}）。把该引号写成 \\" 。'}
        if odd:
            hit = odd[0]
            return {"ok": False, "reason": "unescaped-quote", "detail": msg,
                    "line": hit, "col": col, "excerpt": _at(hit),
                    "hint": f'第 {hit} 行双引号个数为奇数（未转义引号）。'
                            f'把内容里的 " 写成 \\" 。'}
        return {"ok": False, "reason": "json-syntax", "detail": msg,
                "line": line, "col": col, "excerpt": excerpt,
                "hint": f"第 {line} 行第 {col} 列：{msg}（检查尾逗号 / 缺括号 / 单引号当键）"}
    except Exception as e:                                    # pragma: no cover
        return {"ok": False, "reason": "unknown",
                "detail": f"{type(e).__name__}: {e}"[:120],
                "line": None, "col": None, "excerpt": "", "hint": ""}


def check_json(target: pathlib.Path, as_json: bool = False) -> int:
    """对卡文件（或目录下全部 *.json）做**转义校验**并逐张归因。

    退出码：0 = 全部可解析（干净）；4 = 有坏卡（附行号与修法）；2 = 路径不存在。

    为什么要单独给一个入口：卡由并行 subagent 直接写文本，`json.loads`
    一失败整张卡就进不了 build；而"哪一行、哪个引号"才是重派时唯一有用的
    信息。build 之前跑一次，能把这类问题挡在汇总之外
    （而不是让 `refs_build` 报"缺卡 N 篇"那样看不出根因）。
    """
    if not target.exists():
        print(f"[X] 路径不存在：{target}")
        return 2
    files = ([target] if target.is_file()
             else sorted(p for p in target.rglob("*.json") if p.is_file()))
    if not files:
        print(f"[X] 未找到 *.json：{target}")
        return 2
    bad = []
    for f in files:
        d = diagnose_json(f)
        if not d["ok"]:
            bad.append({"file": str(f), **d})
    if as_json:
        print(json.dumps({"checked": len(files), "bad_count": len(bad), "bad": bad},
                         ensure_ascii=False, indent=1))
        return 4 if bad else 0
    print(f"转义校验：{len(files)} 个文件，坏卡 {len(bad)}")
    for d in bad:
        loc = (f"第 {d['line']} 行" if d.get("line") else "行号未知")
        print(f"  [STOP] {d['file']}")
        print(f"        归因：{d['reason']}（{loc}）"
              f"{('：' + d['detail']) if d.get('detail') else ''}")
        if d.get("excerpt"):
            print(f"        原文：{d['excerpt'].strip()[:140]}")
        if d.get("hint"):
            print(f"        修法：{d['hint']}")
    if not bad:
        print("  [OK] 全部可解析（无未转义引号 / 无裸控制字符 / 无语法错）")
    return 4 if bad else 0


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
        out |= _as_names(d.get(key))
    return out


def _as_names(v) -> set[str]:
    out: set[str] = set()
    if not isinstance(v, list):
        return out
    for it in v:
        if isinstance(it, str):
            out.add(it)
        elif isinstance(it, dict):
            n = it.get("result") or it.get("file") or it.get("name")
            if n:
                out.add(pathlib.Path(str(n)).name)
    return out


# ── 卡片**容器类型**断言（H-3）─────────────────────────────────────────
#   并行 subagent 会把数组字段写成整段字符串 —— 申勇 25 张卡里实测 3 处
#   （`methods` 414 字符、`methods` 268 字符、`key_20` 40 字符）。
#   原先两道闸门都拦不住：`_slug_free` 只问「能不能 json.loads 且非空」，
#   `interpret_build.as_list()` 又明写「容忍数组写成字符串」，
#   于是 str 被包成 `[str]` —— 报告照样生成，
#   而下游语料消费者（阶段 K 词云 / `refs_curate.py` 打分）
#   拿到的是 1 条超长条目而不是 3–8 条方法句。取证见 实测结果汇总.md#h-3。
#   断言按阶段分表：阶段 H/H2 是 11/13 字段解读卡，阶段 R2 是概貌卡，字段名不同。
_STR_FIELDS = {
    "H": ("doi", "one_line", "problem_solved"),
    "H2": ("doi", "one_line", "problem_solved", "relation_to_author"),
    "R2": ("doi", "title", "journal", "field_role", "one_sentence",
           "problem_addressed"),
}
_STR_LIST_FIELDS = {
    "H": ("methods", "verified_conclusions", "key_20", "open_questions",
          "direction_tags", "paradigm_tags"),
    "H2": ("methods", "verified_conclusions", "key_20", "open_questions",
           "direction_tags", "paradigm_tags"),
    "R2": ("consensus", "controversies", "open_questions"),
}
_DICT_LIST_FIELDS = {
    "H": {"quantified_gains": ("metric", "value", "context")},
    "H2": {"quantified_gains": ("metric", "value", "context")},
    "R2": {"main_directions": (), "key_concepts": ()},
}


def card_shape_problems(card: dict, stage: str) -> list[str]:
    """返回这张卡的容器类型问题清单；空清单 = 合规。"""
    errs: list[str] = []
    for k in _STR_FIELDS.get(stage, ()):
        v = card.get(k)
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{k}: 应为非空字符串，实为 {type(v).__name__}")
    for k in _STR_LIST_FIELDS.get(stage, ()):
        v = card.get(k)
        if not isinstance(v, list):
            errs.append(f"{k}: 应为字符串数组，实为 {type(v).__name__}")
        elif not all(isinstance(x, str) and x.strip() for x in v):
            errs.append(f"{k}: 数组内含非字符串或空元素")
    for k, need in _DICT_LIST_FIELDS.get(stage, {}).items():
        v = card.get(k)
        if not isinstance(v, list):
            errs.append(f"{k}: 应为对象数组，实为 {type(v).__name__}")
            continue
        for i, g in enumerate(v):
            if not isinstance(g, dict):
                errs.append(f"{k}[{i}]: 应为对象，实为 {type(g).__name__}")
            elif need and not set(need) <= set(g):
                errs.append(f"{k}[{i}]: 缺字段 {'/'.join(need)}")
    if not isinstance(card.get("evidence"), dict):
        errs.append(f"evidence: 应为对象，实为 {type(card.get('evidence')).__name__}")
    return errs


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
    # 归因：不可解析的卡逐张定位（未转义引号 / 裸控制字符 / 其它语法）
    bad_detail = []
    for n in bad:
        diag = diagnose_json(rdir / n)
        if not diag["ok"]:
            bad_detail.append({"file": n, **diag})
    rejected = sorted(_parse_failed_cards(d))
    # 能解析但**容器类型不合规**的卡：不重派就会带病进 build（H-3）
    shape = {n: card_shape_problems(json.loads((rdir / n).read_text(encoding="utf-8")), stage)
             for n in good}
    shape_bad = sorted([{"file": n, "problems": p} for n, p in shape.items() if p],
                       key=lambda x: x["file"])

    total = len(tasks)
    n_done = len(tasks) - len(missing)
    in_flight = max(0, len(missing) - max(0, expect))

    # 建议：下一批派几个 = min(剩余, 单批上限, 在飞控制)
    remaining = len(missing)
    suggest_batch = 0 if remaining == 0 else min(remaining, batch_cap)

    if remaining == 0 and total > 0 and not shape_bad:
        code, verdict = 0, "complete"
    elif remaining == 0 and total == 0:
        code, verdict = 2, "no-tasks"
    elif bad or rejected or shape_bad:
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
        "schema": "flow1-agent-poll/1.1",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tasks_total": total,
        "results_good": n_done,
        "results_raw": len(raw_results),
        "missing_count": remaining,
        "missing": missing[:20],
        "in_flight_estimate": in_flight,
        "unreadable": bad[:20],
        "unreadable_detail": bad_detail[:20],
        "orphan_results": orphan[:20],
        "rejected": rejected[:20],
        "shape_bad_count": len(shape_bad),
        "shape_bad": shape_bad[:20],
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
    ap.add_argument("--check-json", metavar="PATH", default="",
                    help="转义校验：对单个卡或整个目录逐张归因（未转义引号/裸控制字符/"
                         "语法错），给出**行号 + 原文 + 修法**；退出码 0=干净 / 4=有坏卡")
    args = ap.parse_args(argv)

    if args.check_json:
        return check_json(pathlib.Path(args.check_json), as_json=args.json)

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
            print(f"    [STOP] 不可解析的结果卡 {len(r['unreadable'])}："
                  f"{', '.join(r['unreadable'][:3])}")
            # 归因到**行**：重派时把这一行原文一并交给 subagent，
            #   否则它极可能原样再写一遍同一个未转义引号。
            for d in (r.get("unreadable_detail") or [])[:5]:
                loc = (f"第 {d['line']} 行" if d.get("line") else "行号未知")
                print(f"      · {d['file']} → {d['reason']}（{loc}）")
                if d.get("hint"):
                    print(f"        {d['hint']}")
                if d.get("excerpt"):
                    print(f"        原文：{d['excerpt'].strip()[:120]}")
        if r["orphan_results"]:
            print(f"    [INFO] 孤儿卡（上轮残留，不计入）{len(r['orphan_results'])}")
        if r["rejected"]:
            print(f"    [STOP] 被拒收 {len(r['rejected'])}："
                  f"{', '.join(r['rejected'][:3])}")
        if r.get("shape_bad_count"):
            # 能解析、但容器类型不合规（数组写成字符串等）—— 必须重派，
            #   否则 as_list() 会静默兜底，把 1 条超长条目喂给阶段 K 与 refs_curate。
            print(f"    [STOP] 字段类型不合规 {r['shape_bad_count']} 张，需重派：")
            for it in r["shape_bad"][:5]:
                print(f"      · {it['file']} → {'；'.join(it['problems'][:3])}")
        if r["missing_count"]:
            print(f"    → 建议：下一批派 {r['suggest_batch_size']} 个，"
                  f"{r['suggest_interval_s']} 秒后再查")
        elif r["verdict"] == "complete":
            print("    → 已齐，可进入汇总（build）")
        else:
            # 数够了但**卡不合规**：绝不能印「可进入汇总」——
            #   那会让人以为退出码 4 只是提示，带着坏卡跑进 build。
            print(f"    → 篇数已齐，但有坏卡（不可解析 / 被拒收 / 类型不合规），"
                  f"**先重派再 build**（退出码 4）")
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
