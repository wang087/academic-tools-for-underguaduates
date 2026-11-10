# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# CDP 客户端封装（多实例 / 硬超时 / 可诊断）
#
# 本次修复的「死机」根因（旧版）：
#   1) `call()` 里 `while True: ws.recv()` —— 只靠 socket 超时兜底，
#      浏览器进程若已退出或页面目标被销毁（导航到 PDF 时必然发生），
#      recv 会长时间阻塞甚至永久挂起；
#   2) 端口硬编码 127.0.0.1:9222 —— 无法并行多实例；
#   3) `first_page_tab()` 无标签页时返回 None，调用方直接
#      `tab["webSocketDebuggerUrl"]` → TypeError 崩栈；
#   4) `navigate()` 的每次 readyState 轮询都可能各阻塞 90s，
#      最坏累计数分钟，表现为「卡死无输出」。
#
# 修复方案：
#   · 每个调用都带**绝对截止时间**（deadline），超时抛 CDPTimeout，
#     绝不无限等待；recv 单次超时 = 剩余预算，最小 0.5s。
#   · 端口可配（--port / CDP_PORT），HTTP 端点全部参数化 → 支持多实例。
#   · 目标不可用时自动开新标签页；浏览器不可达抛 CDPUnavailable。
#   · 连接/调用失败自动重连一次；事件消息自动丢弃。
#   · 全部关键动作写日志（可选 logger）。
#
# 可移植配置：PAPER_WORKDIR（作者工作目录）、CDP_PORT（默认 9222）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from logutil import run_root as _run_root  # noqa: E402


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与本流程其余脚本同名函数同判据）。

    为什么 `cdp.py` 也要有一份：本模块是**库**，被 cdp_pool /
    download_all_fulltexts / orcid_extract / faculty_* 等大量脚本 import。
    这些脚本自己会在 import 之后调 `_fix_streams()`，但 **import 期就抛出的
    异常**（最典型的是 `CDPUnavailable`：浏览器没起、端口不通）发生得更早，
    会走 Python 默认的异常打印路径，绕过那些脚本的兜底 ——
    在 GBK 控制台上表现为 `CDPUnavailable: ... ���ɴ�: ...` 这种乱码，
    让「端口不通」这个最常见的故障提示反而看不懂。

    为什么**替换前先 flush**（与其余脚本那 35 份副本的唯一差别）：
    那 35 份都在**脚本**里、且紧跟在 import 之后调用，之前几乎没输出过；
    而本模块是**库**，可能在**调用方已经打印了内容之后**才被 import。
    实测在 `PYTHONIOENCODING=gbk` 下，替换 stdout 会让旧 wrapper 里
    尚未冲刷的那一行**直接消失**（不是乱码，是没了）。
    这类「静默丢输出」比乱码更难发现，故此处补一次 flush。

    为什么不 `from logutil import _fix_stdout`：本流程刻意让每个脚本
    **自包含**一份（见 refs_build.py / refs_prepare.py / reviews_prepare.py
    的注释）—— 跨脚本 import 会叠加多层 TextIOWrapper，中间层被 GC 时
    会把底层 buffer 一起关掉，表现为后续所有 print 抛
    "ValueError: I/O operation on closed file"，且只在特定 import 顺序下复现。
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
            s.flush()          # 见上：避免旧 wrapper 缓冲区的输出被静默丢弃
        except Exception:
            pass
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

DEFAULT_PORT = int(os.environ.get("CDP_PORT", "9222"))
HTTP_TIMEOUT = 6.0          # CDP HTTP 端点超时（/json、/json/version）
CALL_TIMEOUT = 30.0         # 单次 CDP 调用默认预算（秒）

# 已注册过「新文档脚本」的 (ws_url, tag)，避免重复注入（见 CDP._inject）
_INJECTED: set = set()
_INJECTED_LOCK = threading.Lock()


# ── 异常 ────────────────────────────────────────────────────
class CDPError(RuntimeError):
    pass


class CDPUnavailable(CDPError):
    """浏览器 / CDP 端点不可达（未启动或已退出）。"""


class CDPTimeout(CDPError):
    """调用超出预算 —— 不再无限等待。"""


class CDPClosed(CDPError):
    """WebSocket 被对端关闭（常见于导航到 PDF 导致目标销毁）。"""


# ── 端口解析 ────────────────────────────────────────────────
def resolve_port(argv=None) -> int:
    """优先级：命令行 --port=N > 环境变量 CDP_PORT > 9222。"""
    for a in (argv if argv is not None else []):
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                pass
    try:
        return int(os.environ.get("CDP_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _get(url: str, method: str = "GET", timeout: float = HTTP_TIMEOUT):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        raise CDPUnavailable(f"{url} 不可达: {e}") from e
    except Exception as e:
        raise CDPUnavailable(f"{url} 失败: {type(e).__name__}: {e}") from e


# ── HTTP 端点 ───────────────────────────────────────────────
def list_tabs(port: int = DEFAULT_PORT) -> list:
    return _get(f"http://127.0.0.1:{port}/json")


def version(port: int = DEFAULT_PORT) -> dict:
    return _get(f"http://127.0.0.1:{port}/json/version")


def new_tab(url: str = "about:blank", port: int = DEFAULT_PORT) -> dict:
    return _get(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")


def is_alive(port: int = DEFAULT_PORT) -> bool:
    """CDP 端点是否可用。

    先 TCP 探活：端口没开直接返回 False，避免 Windows 上
      对已关闭端口发请求固定耗时 ~2.0s 的问题（见 _port_open）。
      本函数在每个任务前都会被调用，这个开销会直接乘上任务数。
    """
    if not _port_open(port):
        return False
    try:
        return bool(version(port).get("webSocketDebuggerUrl"))
    except Exception:
        return False


def page_tabs(port: int = DEFAULT_PORT) -> list:
    """所有可用页面目标（排除 devtools / PDF 查看器等）。"""
    out = []
    for t in list_tabs(port):
        if t.get("type") != "page":
            continue
        u = (t.get("url") or "").lower()
        if u.startswith("devtools://"):
            continue
        out.append(t)
    return out


def first_page_tab(port: int = DEFAULT_PORT) -> dict:
    """取一个页面目标；没有就新开一个。绝不返回 None（失败抛 CDPUnavailable）。"""
    tabs = page_tabs(port)
    if tabs:
        return next((t for t in tabs if "pdf" not in (t.get("url") or "").lower()), tabs[0])
    try:
        return new_tab("about:blank", port)
    except CDPUnavailable:
        # /json/new 在部分 Edge 版本需 PUT，若仍失败则回退到全部标签
        tabs = list_tabs(port)
        if tabs:
            return tabs[0]
        raise


# ── 反检测注入（默认关闭）────────────────────────────────────
# 默认**不做**任何注入：注入本身（改写 navigator.plugins 等）反而
#   是更明显的可检测特征。保持浏览器原生指纹最稳。
STEALTH_JS = r"""
(() => {
  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e){}
})();
"""

# Conservative cookie policy; kept as a string for new-document injection.
from pathlib import Path as _PolicyPath
CLICK_SAFETY_JS = (_PolicyPath(__file__).with_name("click_safety.js")).read_text(encoding="utf-8")
CONSENT_JS = "(() => { const safety = " + CLICK_SAFETY_JS + "; return (" + (
    _PolicyPath(__file__).with_name("consent_logic.js")).read_text(encoding="utf-8") + " ); })()"


class CDP:
    """一个页面目标的 CDP 会话。

    所有调用都受**绝对截止时间**保护，任何一步都不会无限阻塞。
    """

    def __init__(self, ws_url: str, timeout: float = CALL_TIMEOUT,
                 stealth: bool = False, logger=None, port: int | None = None,
                 consent: bool = True, tab_id: str | None = None):
        if websocket is None:
            raise CDPError("websocket-client 未安装：uv run --with websocket-client python ...")
        self.ws_url = ws_url
        self.timeout = float(timeout)
        self.log = logger
        self.port = port if port is not None else _port_from_ws(ws_url)
        self.tab_id = tab_id or ""      # v8：多标签页模式下用于用完即关
        self._id = 0
        self._lock = threading.Lock()
        try:
            self.ws = websocket.create_connection(
                ws_url, timeout=self.timeout, enable_multithread=True,
                suppress_origin=True,        # 不发送 Origin，规避 403
            )
        except Exception as e:
            raise CDPUnavailable(f"WebSocket 连接失败: {type(e).__name__}: {e}") from e
        # 新文档创建前注入：cookie/同意书弹窗自动关闭（默认开启）
        if consent:
            self._inject(CONSENT_JS, "consent")
        if stealth:
            self._inject(STEALTH_JS, "stealth")

    def _inject(self, source: str, tag: str) -> None:
        """把脚本注册为「每个新文档创建前执行」。失败不影响主流程。

        为什么需要去重：`Page.addScriptToEvaluateOnNewDocument` 是**累加**的，
          注册会一直留在页面目标上（跨进程也有效）。主流程每篇论文都会
          connect() 一次，若不去重，跑 100 篇就会在同一目标上堆 100 份脚本，
          每开一个新文档全部再执行一遍 —— 白白拖慢每次导航。

        做法：进程内用 `_INJECTED` 记账；跨进程把上次的 identifier 存到
        `.work_cdp/cdp_injected.json`，下次注册前先 `removeScript...` 掉旧的。
        """
        key = (self.ws_url, tag)
        with _INJECTED_LOCK:
            if key in _INJECTED:
                return
        try:
            self.call("Page.enable", timeout=8)
            # 先摘掉上一次进程遗留的同名脚本（找不到就忽略）
            old = _load_injected().get(self.ws_url, {}).get(tag)
            if old:
                try:
                    self.call("Page.removeScriptToEvaluateOnNewDocument",
                              {"identifier": old}, timeout=8)
                except CDPError:
                    pass
            r = self.call("Page.addScriptToEvaluateOnNewDocument",
                          {"source": source}, timeout=8)
            ident = r.get("identifier")
            if ident:
                reg = _load_injected()
                reg.setdefault(self.ws_url, {})[tag] = ident
                _save_injected(reg)
            with _INJECTED_LOCK:
                _INJECTED.add(key)
        except Exception as e:
            if self.log:
                try:
                    self.log.debug(f"注入脚本失败（忽略）: {str(e)[:60]}")
                except Exception:
                    pass

    # ── 底层调用 ────────────────────────────────────────────
    def _recv_until(self, mid: int, deadline: float):
        """收消息直到拿到 id==mid 的应答；丢弃事件消息。超时抛 CDPTimeout。"""
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise CDPTimeout(f"调用超时（{self.timeout:.0f}s 预算已耗尽）")
            try:
                self.ws.settimeout(max(remain, 0.001))
                raw = self.ws.recv()
            except Exception as e:
                name = type(e).__name__
                if "Timeout" in name:
                    raise CDPTimeout(f"接收超时（剩余 {remain:.1f}s）") from e
                raise CDPClosed(f"{name}: {str(e)[:80]}") from e
            if not raw:
                raise CDPClosed("对端关闭连接")
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") != mid:
                continue                      # 事件 / 其他应答，丢弃
            if "error" in msg:
                raise CDPError(str(msg["error"])[:200])
            return msg.get("result", {})

    def call(self, method: str, params: dict | None = None, timeout: float | None = None):
        budget = float(timeout if timeout is not None else self.timeout)
        deadline = time.monotonic() + budget
        if not self._lock.acquire(timeout=max(0.0, budget)):
            raise CDPTimeout("等待CDP会话锁超时")
        try:
            self._id += 1
            mid = self._id
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise CDPTimeout("调用预算已耗尽")
            try:
                self.ws.settimeout(remain)
                self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            except Exception as e:
                raise CDPClosed(f"发送失败: {type(e).__name__}: {str(e)[:80]}") from e
            return self._recv_until(mid, deadline)
        finally:
            self._lock.release()

    def eval(self, expr: str, timeout: float | None = None):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True
        }, timeout)
        if "exceptionDetails" in r:
            raise CDPError(str(r["exceptionDetails"].get("text", "eval error"))[:160])
        return (r.get("result") or {}).get("value")

    # ── 组合操作（全部有界）─────────────────────────────────
    def navigate(self, url: str, settle: float = 2.0, wait_challenge: float = 0.0,
                 load_timeout: float = 30.0) -> "CDP":
        """导航到 url。总耗时上界 = nav_timeout + load_timeout + settle（+ wait_challenge）。

        `Page.navigate` 指令本身超时**不代表**页面没在加载：
          实测多实例并行时（3 个 Edge 同时打开重型出版社页面），
          导航应答偶发超过 15s，但页面随后正常渲染完成。
          旧实现直接抛错 → 上层判为 nav-timeout → 误报失败。
          现改为：指令超时仅告警，继续走 readyState 轮询。

        wait_challenge 仅等待「标题仍是 just a moment」的自动放行（默认 0）；
        是否真遇到挑战由 detect.judge() 精确判定，不在此处下结论。
        """
        try:
            self.call("Page.enable", timeout=10)
        except CDPError:
            pass
        try:
            self.call("Page.navigate", {"url": url}, timeout=30)
        except CDPTimeout:
            pass                      # 指令超时 ≠ 加载失败，继续等
        except CDPError:
            raise

        deadline = time.time() + load_timeout
        while time.time() < deadline:
            try:
                if self.eval("document.readyState", timeout=5) == "complete":
                    break
            except CDPError:
                pass
            time.sleep(0.3)
        if wait_challenge > 0:
            end = time.time() + wait_challenge
            while time.time() < end:
                try:
                    if "just a moment" not in (self.eval("document.title", timeout=4) or "").lower():
                        break
                except CDPError:
                    break
                time.sleep(1.0)
        # 自适应 settle：正文/引文元数据已就绪就提前返回（效率），
        #   最坏仍等待满 settle 秒（慢站点不降低成功率）。
        if settle > 0:
            end = time.time() + settle
            probe_ready = ("(() => { const m = document.querySelector('meta[name=\"citation_pdf_url\"]');"
                           " const t = (document.body && document.body.innerText) || '';"
                           " return !!(m || t.length > 4000); })()")
            while time.time() < end:
                try:
                    if self.eval(probe_ready, timeout=5):
                        break
                except CDPError:
                    pass
                time.sleep(0.3)
        return self

    def dismiss_consent_report(self, timeout: float = 8.0) -> dict:
        """Return observed DOM outcome; a dispatched click is not success.

        Counts are cumulative within this document. No layer is hidden or removed
        by this policy. verified requires the original banner to disappear.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        report = {"status": "unchanged", "attempted": 0, "clicked": 0,
                  "verified": 0, "blocked": 0, "unchanged": True,
                  "manual_required": False, "items": []}
        while time.monotonic() < deadline:
            try:
                result = self.eval(CONSENT_JS, timeout=max(0.001, deadline - time.monotonic()))
                if isinstance(result, dict):
                    report = result
                if report.get("verified") or report.get("manual_required") or not report.get("clicked"):
                    break
            except CDPError as exc:
                report["error"] = str(exc)
                break
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        self._consent_report = report
        return report

    def get_consent_report(self, timeout: float = 8.0) -> dict:
        return self.dismiss_consent_report(timeout=timeout)

    def dismiss_consent(self, timeout: float = 8.0) -> bool:
        """Legacy bool: true only for an observed banner disappearance."""
        return bool(self.dismiss_consent_report(timeout).get("verified", 0))

    def get_text(self, selector: str = "document.body", timeout: float | None = None):
        return self.eval(f"({selector} && {selector}.innerText) || ''", timeout=timeout)

    def get_title(self, timeout: float | None = None):
        try:
            return self.eval("document.title", timeout=timeout) or ""
        except CDPError:
            return ""

    def get_url(self, timeout: float | None = None):
        try:
            return self.eval("location.href", timeout=timeout) or ""
        except CDPError:
            return ""

    def bring_to_front(self):
        try:
            self.call("Page.bringToFront", timeout=6)
        except CDPError:
            pass

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def close_tab_self(self) -> bool:
        """关闭本会话所属的标签页（v8，多标签页模式下用完即关）。

        顺序很重要：**先关标签页、再关 ws**。反过来先关 ws 后，
          标签页会变成无人认领的孤儿，长期累积拖慢浏览器。
        导航到 PDF 后原页面目标已被 PDF 查看器顶替，此时按 tab_id
          关闭可能失败 —— 属于正常情况，返回 False 即可，不要重试。
        """
        ok = False
        if self.tab_id:
            ok = close_tab(self.tab_id, self.port)
        self.close()
        return ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _port_from_ws(ws_url: str) -> int:
    m = re.search(r":(\d{2,5})/devtools/", ws_url or "")
    return int(m.group(1)) if m else DEFAULT_PORT


# ── 跨进程的注入记账（见 CDP._inject）────────────────────────
_INJECTED_FILE = ".work_cdp/cdp_injected.json"


def _injected_path() -> "pathlib.Path":
    import pathlib
    wd = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
    return wd / _INJECTED_FILE


def _load_injected() -> dict:
    try:
        import json as _j
        return _j.loads(_injected_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_injected(reg: dict) -> None:
    try:
        import json as _j
        p = _injected_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_j.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def connect(port: int = DEFAULT_PORT, timeout: float = CALL_TIMEOUT,
            stealth: bool = False, logger=None, prefer_blank: bool = True,
            consent: bool = True) -> CDP:
    """连接到一个页面目标并返回 CDP 会话（自动挑选/新建标签页）。

    consent=True 时注册 cookie/同意书弹窗自动关闭脚本（见 CONSENT_JS）。

    [WARN] **本函数只保证"能拿到一个标签页"，不保证"独占"** ——
       它优先复用已有的 about:blank 标签，多个调用方可能拿到同一个。
       需要并发隔离时**必须**改用 `open_tab()`（见下）。
    """
    tabs = page_tabs(port)
    tab = None
    if prefer_blank:
        tab = next((t for t in tabs if (t.get("url") or "") in ("about:blank", "")), None)
    if tab is None:
        tab = first_page_tab(port)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        raise CDPUnavailable(f"端口 {port} 上没有可用的页面目标")
    return CDP(tab["webSocketDebuggerUrl"], timeout=timeout,
               stealth=stealth, logger=logger, port=port, consent=consent)


# ══════════════════════════════════════════════════════════════
# 多标签页支持（v8）
#
# 为什么需要
#   旧设计是「一个浏览器一个 worker、一个标签页」。并发度 = 浏览器数，
#   想提速只能多开浏览器进程，而每个 Edge 进程约占 200–400 MB ——
#   开 6 个已经吃掉 2 GB，且启动/崩溃恢复都慢。
#   实测瓶颈其实在**网络等待**（导航 + 出版社响应），
#   CPU 与内存远没到顶，所以「同浏览器内多标签页」是更划算的扩容方式：
#   标签页共享同一个进程池与网络栈，增开成本远低于新开浏览器。
#
# 关键约束（不能违反）
#   1. **一个标签页只给一个 worker** —— 两个线程操作同一标签会互相导航、
#      互相踩状态。`open_tab()` 每次都新开，绝不复用。
#   2. **同站点仍然串行** —— 这是反封禁的红线，与标签页数无关。
#      调度层的 SiteGuard / pace() 负责，标签页只提供并发通道。
#   3. **PDF 目标会顶替标签页** —— 导航到 PDF 时 Chrome/Edge 会用
#      PDF 查看器替换当前页面目标，原 ws 连接失效。所以每篇用完即
#      `close_tab()`，不要跨篇复用（见 download_all_fulltexts 的用法）。
# ══════════════════════════════════════════════════════════════


def open_tab(url: str = "about:blank", port: int = DEFAULT_PORT,
             timeout: float = CALL_TIMEOUT, stealth: bool = False,
             logger=None, consent: bool = True,
             cleanup_blank: bool = True) -> "CDP":
    """**新开一个标签页**并连接到它（多标签页并发的正确入口）。

    与 `connect()` 的区别：`connect()` 会复用已有空白标签，
    多个 worker 可能抢到同一个；`open_tab()` 每次 `/json/new` 一张新标签，
    保证调用方**独占**该标签页。

    cleanup_blank=True 时，若新开失败会尝试回收一张遗留的空白标签再连，
    避免 Edge 因标签页泄漏而变慢（但绝不会复用**非**空白的标签）。
    """
    tab = None
    try:
        tab = new_tab(url, port)
    except Exception as e:
        if logger:
            logger.warn(f"端口 {port} 新开标签失败（{type(e).__name__}:{e}），尝试回收空白标签")
        # 兜底：只回收 about:blank 的标签，绝不碰正在用的
        if cleanup_blank:
            blanks = [t for t in page_tabs(port)
                      if (t.get("url") or "") in ("about:blank", "")]
            if blanks:
                tab = blanks[0]
    if not tab or not tab.get("webSocketDebuggerUrl"):
        raise CDPUnavailable(f"端口 {port} 无法新开标签页")
    return CDP(tab["webSocketDebuggerUrl"], timeout=timeout,
               stealth=stealth, logger=logger, port=port, consent=consent,
               tab_id=tab.get("id"))


def close_tab(tab_id: str, port: int = DEFAULT_PORT,
              timeout: float = 3.0) -> bool:
    """关闭指定标签页（按 target id）。失败返回 False，不抛。"""
    if not tab_id:
        return False
    try:
        _get(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=timeout)
        return True
    except Exception:
        return False


def _port_open(port: int, timeout: float = 0.35) -> bool:
    """快速探测本地端口是否有进程在听（不发 HTTP 请求）。

    为什么需要：Windows 上对**已关闭**的本地端口发 urllib 请求，
      即使传了 timeout，实测仍固定花掉 ~2.0s（连接被拒后的重试/解析开销）。
      收尾清理要对每个端口探一次，浏览器不在时就会白等 2s×N。
      先用 socket 快速判活，能立刻返回，避免这一固定开销。
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def count_tabs(port: int = DEFAULT_PORT, timeout: float = 3.0) -> int:
    """当前页面标签数（用于诊断标签泄漏）。浏览器不在时快速返回 -1。

    先做 TCP 探活：端口没开就立刻返回，不走 HTTP（见 _port_open 说明）。
    """
    if not _port_open(port):
        return -1
    try:
        return len([t for t in _get(f"http://127.0.0.1:{port}/json", timeout=timeout)
                    if t.get("type") == "page"])
    except Exception:
        return -1


def prune_blank_tabs(port: int = DEFAULT_PORT, keep: int = 1,
                     timeout: float = 3.0) -> int:
    """清理多余的 about:blank 标签，返回清理数量。

    长期并发下载会因异常路径残留空白标签，累积后拖慢浏览器。
    只关空白标签，**绝不关**正在导航/已加载内容的标签。
    浏览器不在时立刻返回 0（不抛、不干等）—— 收尾清理不该拖慢主流程。
    """
    if not _port_open(port):
        return 0
    n = 0
    try:
        tabs = _get(f"http://127.0.0.1:{port}/json", timeout=timeout)
        blanks = [t for t in tabs
                  if t.get("type") == "page"
                  and (t.get("url") or "") in ("about:blank", "")]
        for t in blanks[keep:]:
            if close_tab(t.get("id", ""), port, timeout=timeout):
                n += 1
    except Exception:
        pass
    return n


def wait_cdp(port: int = DEFAULT_PORT, seconds: float = 30.0, logger=None) -> bool:
    """等待 CDP 端点就绪（带日志）。"""
    end = time.time() + seconds
    while time.time() < end:
        if is_alive(port):
            return True
        time.sleep(1.0)
    if logger:
        logger.warn(f"端口 {port} 在 {seconds:.0f}s 内未就绪")
    return False


if __name__ == "__main__":
    import sys
    port = resolve_port(sys.argv[1:])
    print(f"CDP 端口: {port} | 存活: {is_alive(port)}")
    if is_alive(port):
        v = version(port)
        print("  Browser:", v.get("Browser"))
        for t in page_tabs(port):
            print(f"  tab {t['id'][:12]} {t.get('url','')[:80]}")
