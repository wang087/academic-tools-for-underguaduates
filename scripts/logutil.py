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
# 可移植配置：环境变量 PAPER_WORKDIR（默认当前目录）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import os
import pathlib
import sys
import threading
import time

# ★ 必须用 RLock：get() 持锁期间会构造 Log，而 Log.__init__ 内部要写
#   第一条日志（再次获取同一把锁）。用普通 Lock 会**直接死锁**。
_LOCK = threading.RLock()
_LOGS: dict[str, "Log"] = {}


def workdir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()


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
# ★ 两条约定：
#   ① 写 journal **绝不影响主流程** —— 任何异常都被吞掉（日志是旁路，不是依赖）；
#   ② 只记录**已发生的事实**，不写预期值 —— 否则回溯会自欺。
# ══════════════════════════════════════════════════════════════
JOURNAL = "run_journal.jsonl"


def journal_path(wd: pathlib.Path | None = None) -> pathlib.Path:
    return logdir(wd) / JOURNAL


def journal(stage: str, action: str, **fields) -> None:
    """追加一条操作记录到 run_journal.jsonl。

    stage  —— 流程阶段，如 "C"、"E"、"H"
    action —— 动作，如 "orcid.navigate"、"download.pdf"
    fields —— 任意附加字段（doi / url / 参数 / 结果 / 耗时 …），空值自动省略
    """
    try:
        import json as _json
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
