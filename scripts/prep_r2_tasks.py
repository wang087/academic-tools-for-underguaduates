# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════
# 阶段 R2 任务包整理：把全文从 JSON 里「搬出来」
#
# 【为什么必须这么做】
#   `reviews_fulltext.py --backfill` 把全文写进了任务包的 `text` 字段。
#   但 JSON 里这个字段是**一整条物理行**（换行被转义成 \n）——
#   实测 `10_1016_j_cell_2014_03_011.json` 的 text 行长达 **162,777 字符**，
#   而 **Read 工具会把超长单行截断到 2000 字符**。
#
#   后果：阶段 R2 的 subagent 无论怎么读，**只能看到全文的 1.2%**，
#   4 个 subagent 全部在「以为读到了」的状态下中途失败/产出空卡。
#   这是**静默失败**：任务包看起来「有全文」，subagent 也以为在读全文。
#
# 【做法】
#   1. 备份原 tasks/ → tasks_full/（保留带全文的原始包，随时可查）
#   2. 把 `text` 落到真实 .txt（保留段落换行）→ <stage>/fulltext/<slug>.txt
#   3. 任务包的 `text` 只留**开头摘录**（供快速看开头），并标注：
#        text_truncated = true
#        fulltext_path  = <上面那个 .txt 的绝对路径>
#        fulltext_chars = 完整正文字数
#   4. subagent 读「小 JSON 拿题录」+「.txt 分段读正文」
#
# 【为什么不直接截断任务包】
#   截断会丢内容，而 L3 档位要求「论证链完整」。
#   换成独立 .txt 后，subagent 可以用 Read 的 offset/limit **分段读完整篇**，
#   既不受单行 2000 字符限制，也不丢正文。
#
# 用法：
#   python scripts/prep_r2_tasks.py                 # 默认认**本次运行**的 .work_cdp/reviews/
#   python scripts/prep_r2_tasks.py --stage-dir <path>
#   python scripts/prep_r2_tasks.py --excerpt 3000
#   python scripts/prep_r2_tasks.py --restore       # 从 tasks_full/ 还原
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import io
import json
import pathlib
import shutil
import sys


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与 render_html_pdf.py 等同名函数逐字一致）。

    旧写法是无条件 `sys.stdout = io.TextIOWrapper(...)`：若本模块在别的脚本
    已包过之后被 import，会叠加第二层 wrapper，中间层被 GC 时把底层 buffer
    一起关掉（后续 print 抛 "I/O operation on closed file"）。故先判编码。
    """
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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── 日志（**文件型**：console=False）────────────────────────────
#   stdout 是给操作者的整理进度表，日志不写控制台，只落
#   `.work_cdp/logs/prep_r2_tasks_<时间戳>.log` 并追加操作流水。
#   本脚本会**改写 tasks/ 下的任务包**（只留摘录 + 指向 .txt），
#   属破坏性操作 —— 落一份日志便于事后核对「哪一轮改了多少篇」。
try:
    from logutil import get as _get_log, journal as _journal  # type: ignore
except Exception:                      # 单文件运行时也要能用
    def _get_log(name, **kw):          # noqa: D103
        class _L:
            def info(self, *a, **k): pass
            def ok(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass
            def close(self): pass
        return _L()

    def _journal(*a, **k):             # noqa: D103
        pass

FLOW1 = pathlib.Path(__file__).resolve().parent.parent

# 本次运行的工作目录：与 `logutil.workdir()` **同一判据**（`流程1/<年月日>/`）。
#   原来这里硬写 `FLOW1`，于是默认目录落到日期隔离前的旧布局 `流程1/outputs/03_领域概貌` ——
#   不带 `--stage-dir` 时会去**改写历史案例的任务包**（静默、不报错）。
try:
    from logutil import workdir as _workdir          # noqa: E402
    WD = _workdir()
except Exception:  # noqa: BLE001
    WD = FLOW1
WORK = WD / ".work_cdp"


def find_stage_dir(explicit: str | None) -> pathlib.Path | None:
    """定位阶段 R 的任务包目录（含 tasks/ 的那一层），**只认本次运行的目录**。

    优先级：① `<本次工作目录>/.work_cdp/reviews/`（`reviews_prepare.py` 的规范产物位置）
    ② `<本次工作目录>/outputs/` 下含 tasks/ 的目录（历史布局）。
    [X] 不回看 `流程1/outputs/` —— 那里躺着历史案例，静默选中会把它们的
        任务包改写成本次的形态（宁可报错，也不要错答）。
    """
    if explicit:
        p = pathlib.Path(explicit)
        return p if p.is_dir() else None
    primary = WORK / "reviews"
    if (primary / "tasks").is_dir():
        return primary
    outs = WD / "outputs"
    if not outs.is_dir():
        return None
    cands = [d for d in sorted(outs.iterdir()) if d.is_dir() and (d / "tasks").is_dir()]
    if not cands:
        return None
    cands.sort(key=lambda d: ("领域概貌" not in d.name,
                              -len(list((d / "tasks").glob("*.json")))))
    return cands[0]


def wrap_long_lines(text: str, width: int = 1200) -> str:
    """把超长段落软换行，确保**每一行都能被读取工具完整返回**。

    [STOP] 为什么必要：读取工具对**单行**有约 2000 字符上限，超出即
    `... (line truncated to 2000 chars)`。综述里常有整段 3000–8000 字符的
    长段落 —— 只搬出 JSON 还不够，**长段落本身仍会被截断**。
    按 width 软换行后，任何一行都远小于上限，全文 100% 可读。
    换行只加在空白/标点处，不改动任何字符内容。
    """
    out: list[str] = []
    for line in text.splitlines():
        if len(line) <= width:
            out.append(line)
            continue
        buf = ""
        for token in line.split(" "):
            if not buf:
                buf = token
            elif len(buf) + 1 + len(token) <= width:
                buf += " " + token
            else:
                out.append(buf)
                buf = token
        if buf:
            out.append(buf)
    return "\n".join(out)


def _run(log) -> int:
    ap = argparse.ArgumentParser(description="把任务包全文搬到独立 .txt，供 R2 subagent 分段读")
    ap.add_argument("--stage-dir", default=None)
    ap.add_argument("--excerpt", type=int, default=2500,
                    help="任务包 text 字段保留的开头字符数（默认 2500）")
    ap.add_argument("--restore", action="store_true",
                    help="从 tasks_full/ 还原（撤销本次整理）")
    a = ap.parse_args()

    stage = find_stage_dir(a.stage_dir)
    if not stage:
        miss = (f"未找到阶段任务包目录（需含 tasks/）；"
                f"已查 {WORK / 'reviews'} 与 {WD / 'outputs'}"
                f" —— 先跑 reviews_prepare.py")
        log.error(miss)
        print(f"[X] {miss}")
        return 2
    tasks = stage / "tasks"
    full = stage / "fulltext"
    backup = stage / "tasks_full"
    print(f"阶段目录：{stage}")
    log.info(f"阶段目录：{stage}（excerpt={a.excerpt} restore={a.restore}）")

    # ── 还原模式 ──
    if a.restore:
        if not backup.is_dir():
            log.error(f"没有备份可还原：{backup}")
            print(f"[X] 没有备份可还原：{backup}")
            return 2
        n = 0
        for f in backup.glob("*.json"):
            shutil.copy2(f, tasks / f.name)
            n += 1
        log.ok(f"已从 tasks_full/ 还原 {n} 个任务包")
        _journal("R2", "prep_r2.restore", stage_dir=str(stage), restored=n)
        print(f"[OK] 已从 tasks_full/ 还原 {n} 个任务包")
        return 0

    # ── 备份（只备份一次；重复跑不覆盖已有备份）──
    if not backup.is_dir():
        backup.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in tasks.glob("*.json"):
            shutil.copy2(f, backup / f.name)
            n += 1
        log.info(f"已备份 {n} 个原始任务包 → {backup.name}/")
        print(f"已备份 {n} 个原始任务包 → {backup.name}/（含全文，可回溯）")
    else:
        log.info(f"备份已存在 → {backup.name}/（保持不动）")
        print(f"备份已存在 → {backup.name}/（保持不动）")

    full.mkdir(parents=True, exist_ok=True)

    print(f"\n{'slug':<40} {'原文字数':>10} {'txt行数':>8} {'最长行':>8}")
    print("-" * 72)
    changed = 0
    for f in sorted(tasks.glob("*.json")):
        # **全文以 tasks_full/ 为准**（备份保留完整 text；tasks/ 里已只剩摘录）。
        #   这样本脚本可**反复运行**：每次都从完整版重新生成，不会越跑越短。
        src = (backup / f.name) if (backup / f.name).exists() else f
        try:
            dsrc = json.loads(src.read_text(encoding="utf-8"))
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  [X] {f.name}: {e}")
            continue
        text = dsrc.get("text") or ""
        if not text or len(text) <= a.excerpt:
            print(f"  {f.stem[:40]:<40} {'(无完整正文)':>10}")
            continue

        # ① 全文落独立 .txt（保持段落，并对超长段落软换行）
        wrapped = wrap_long_lines(text)
        tp = full / f"{f.stem}.txt"
        tp.write_text(wrapped, encoding="utf-8")
        longest = max((len(x) for x in wrapped.splitlines()), default=0)

        # ② 任务包只留开头摘录 + 指针
        d["text_excerpt"] = text[: a.excerpt]
        d["text"] = text[: a.excerpt]
        d["text_truncated"] = True
        d["fulltext_path"] = str(tp)
        d["fulltext_chars"] = len(text)
        d["text_note"] = ("JSON 单行会被读取工具截断到 2000 字符，故 text 仅存摘录；"
                          "完整正文在 fulltext_path 指向的 txt（已软换行，全文可读），"
                          "请分段读完")
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        changed += 1
        print(f"  {f.stem[:40]:<40} {len(text):>10,} {len(wrapped.splitlines()):>8} {longest:>8}")

    print("-" * 72)
    log.ok(f"整理 {changed} 篇 → 完整正文 {full}")
    _journal("R2", "prep_r2.extract", stage_dir=str(stage), changed=changed,
             fulltext_dir=str(full), excerpt=a.excerpt)
    print(f"[OK] 整理 {changed} 篇")
    print(f"  完整正文 → {full}")
    print(f"  原始备份 → {backup}")
    print("\n  subagent 读法：先读 tasks/<slug>.json 拿题录，"
          "\n                 再用 Read(fulltext_path, offset/limit) 分段读完整正文。")
    return 0


def main() -> int:
    log = _get_log("prep_r2_tasks", console=False)
    try:
        return _run(log)
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
