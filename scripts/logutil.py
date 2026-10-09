# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 统一日志（所有脚本共用）
#
# 设计要点：
#   1) 每条日志同时写「控制台」+「文件」，文件落盘于
#      <WORKDIR>/.work_cdp/logs/<name>_<YYYYmmdd_HHMMSS>.log
#   2) 文件始终 UTF-8 写入，规避 Windows 控制台 GBK 编码崩溃
#      （旧脚本用 io.TextIOWrapper 包 stdout，遇到生僻字符仍可能抛
#        UnicodeEncodeError 直接中断流程；此处改成 errors="replace" 兜底）
#   3) 同时维护 logs/latest_<name>.log 指针文件，便于事后定位
#   4) 线程安全（多实例并行时会并发写日志）
#
# 可移植配置：环境变量 PAPER_WORKDIR（默认 <流程1>/<YYYYMMDD>/）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import os
import pathlib
import sys
import threading
import time

# 必须用 RLock：get() 持锁期间会构造 Log，而 Log.__init__ 内部要写
#   第一条日志（再次获取同一把锁）。用普通 Lock 会**直接死锁**。
_LOCK = threading.RLock()
_LOGS: dict[str, "Log"] = {}

# ══════════════════════════════════════════════════════════════
# 统一输出根 —— 全流程**所有产物**的默认落点
#
# 规则（用户指示）：所有输出放在 `流程1/<年月日>/` 下，按天隔离。
#   例：2026-10-06 运行 → `流程1/20261006/`
#   设了 `PAPER_AUTHOR` 时再下一级作者目录 → `流程1/20261006/<作者名>/`
#   ★ 作者这一级不是装饰：`.work_cdp/`（题录总表、消歧结果、全文状态）
#     是**按作者**的，同一天跑两位作者若共用一个目录会互相覆盖。
#
# ★ 这里是**唯一实现**。其余脚本一律 `from logutil import run_root`，
#   不要各写一份日期拼接 —— 否则跨零点运行会被切成两个目录。
# ★ 仍可被 `PAPER_WORKDIR` 覆盖（自检脚本要用临时目录，且便于换机运行）。
# ══════════════════════════════════════════════════════════════
def run_root() -> pathlib.Path:
    """`<流程1>/<YYYYMMDD>/[<PAPER_AUTHOR>/]`（不存在则创建）。"""
    d = pathlib.Path(__file__).resolve().parents[1] / time.strftime("%Y%m%d")
    author = (os.environ.get("PAPER_AUTHOR") or "").strip()
    if author:
        d = d / author
    d.mkdir(parents=True, exist_ok=True)
    return d


def workdir() -> pathlib.Path:
    """本次运行的工作目录：`PAPER_WORKDIR` 优先，否则 `run_root()`。"""
    return pathlib.Path(os.environ.get("PAPER_WORKDIR") or run_root()).resolve()


def logdir(wd: pathlib.Path | None = None) -> pathlib.Path:
    d = (wd or workdir()) / ".work_cdp" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fix_stdout() -> None:
    """让控制台输出永不因编码问题中断流程。"""
    for stream_name in ("stdout", "stderr"):
        s = getattr(sys, stream_name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, stream_name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True))
        except Exception:
            pass


class Log:
    """极简日志器：控制台 + 文件双写。"""

    LEVELS = {"DEBUG": 10, "INFO": 20, "OK": 25, "WARN": 30, "ERROR": 40}

    def __init__(self, name: str, wd: pathlib.Path | None = None,
                 console: bool = True, level: str = "INFO"):
        _fix_stdout()
        self.name = name
        self.console = console
        self.level = self.LEVELS.get(level.upper(), 20)
        self.t0 = time.time()
        d = logdir(wd)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = d / f"{name}_{stamp}.log"
        self.fh = open(self.path, "a", encoding="utf-8", errors="replace", buffering=1)
        # 指针文件：始终指向该脚本最近一次运行的日志
        try:
            (d / f"latest_{name}.log").write_text(str(self.path), encoding="utf-8")
        except Exception:
            pass
        self.info(f"=== {name} 启动 | pid={os.getpid()} | workdir={wd or workdir()} ===")

    # ── 内部 ────────────────────────────────────────────────
    def _emit(self, tag: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        el = time.time() - self.t0
        line = f"[{ts} +{el:7.1f}s] {tag:<5} {msg}"
        with _LOCK:
            try:
                self.fh.write(line + "\n")
            except Exception:
                pass
            if self.console:
                try:
                    print(line, flush=True)
                except Exception:
                    pass

    def _log(self, lvl: str, msg: str) -> None:
        if self.LEVELS[lvl] >= self.level:
            self._emit(lvl, str(msg))

    # ── 公开 API ────────────────────────────────────────────
    def debug(self, msg): self._log("DEBUG", msg)
    def info(self, msg):  self._log("INFO", msg)
    def ok(self, msg):    self._log("OK", msg)
    def warn(self, msg):  self._log("WARN", msg)
    def error(self, msg): self._log("ERROR", msg)

    def step(self, i, n, msg):
        self._emit("STEP", f"[{i}/{n}] {msg}")

    def exc(self, e: BaseException, ctx: str = ""):
        self.error(f"{ctx}{type(e).__name__}: {e}")

    def close(self):
        try:
            self.info(f"=== {self.name} 结束 | 用时 {time.time()-self.t0:.0f}s | 日志 {self.path} ===")
            self.fh.close()
        except Exception:
            pass


def get(name: str, **kw) -> Log:
    """按名字取（或新建）日志器 —— 同一进程内同名复用。"""
    with _LOCK:
        lg = _LOGS.get(name)
        if lg is None or getattr(lg.fh, "closed", False):
            lg = Log(name, **kw)
            _LOGS[name] = lg
        return lg


# ══════════════════════════════════════════════════════════════
# 操作流水（run journal）—— 用于**回溯「这一轮到底做了什么」**
#
# 与 per-script 日志的分工：
#   · `logs/<脚本>_<时间戳>.log` = **文本流水**，给人看某一步的细节；
#   · `logs/run_journal.jsonl`   = **结构化事件流**（一行一 JSON，只追加），
#     跨脚本、跨阶段，记录「哪个阶段 / 哪个脚本 / 对哪个对象 /
#     用了什么参数 / 结果如何」，可直接检索与复盘。
#
# 为什么需要它：多阶段长流程出问题时，最难的往往不是修，而是**还原现场**
# —— 上一轮到底跑了哪些脚本、参数是什么、哪一步开始不对。per-script 日志
# 各写各的、时间戳分散，串不起来。journal 把关键动作按时间顺序固化成
# 一条链，配合 `latest_*.log` 指针即可完整回溯。
#
# 两条约定：
#   ① 写 journal **绝不影响主流程** —— 任何异常都被吞掉（日志是旁路，不是依赖）；
#   ② 只记录**已发生的事实**，不写预期值 —— 否则回溯会自欺。
# ══════════════════════════════════════════════════════════════
JOURNAL = "run_journal.jsonl"


def journal_path(wd: pathlib.Path | None = None) -> pathlib.Path:
    return logdir(wd) / JOURNAL


def journal(*args, **fields) -> None:
    """追加一条操作记录到 run_journal.jsonl。

    调用形式：`journal(stage, action, **字段)`
      stage   —— 流程阶段，如 "C"、"E"、"H"（也可写成关键字 `stage=`）
      action  —— 动作，如 "orcid.navigate"、"download.pdf"（也可写 `action=`）
      其余关键字 —— 任意附加字段（doi / url / 参数 / 结果 / 耗时 …），空值自动省略

    ★ 为什么签名是 `*args, **fields` 而不是 `(stage, action, **fields)`：
      后者会让「字段名撞上形参名」变成**硬错误**。调用方想把「阶段目录」
      记成一个字段、很自然地写成 `stage=...` 时，Python 在**进入函数体之前**
      就抛 `TypeError: journal() got multiple values for argument 'stage'`，
      下面的 try/except **根本来不及生效** —— 日志这个小动作直接崩掉主流程。
      这与本函数「日志是旁路、不是依赖，任何异常都被吞掉」的契约**直接冲突**。
      改成 *args 之后，字段名与 stage/action 永不冲突，契约才真正成立。
      （实测复现：`prep_r2_tasks.py` 接入 journal 的首个版本就是这样崩的。）
    """
    try:
        import json as _json
        stage = args[0] if len(args) > 0 else fields.pop("stage", "")
        action = args[1] if len(args) > 1 else fields.pop("action", "")
        # 位置/关键字给出的 stage/action 是**权威值**。若调用方又传了同名字段
        #   （`*args` 签名的副作用：它不再报错，而是留在 fields 里），
        #   既不能让它**覆盖**权威值（否则流水会丢掉真实阶段），
        #   也不该静默**丢弃**（那会让人以为字段没生效）。
        #   折中：改名 `stage_field` / `action_field` 保留，两全。
        for _k in ("stage", "action"):
            if _k in fields:
                fields[_k + "_field"] = fields.pop(_k)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "epoch": round(time.time(), 3),
               "stage": str(stage), "action": str(action)}
        for k, v in fields.items():
            if v is None or v == "":
                continue
            rec[k] = v if isinstance(v, (int, float, bool, list, dict)) else str(v)
        line = _json.dumps(rec, ensure_ascii=False)
        with _LOCK:
            with open(journal_path(), "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
    except Exception:
        pass
