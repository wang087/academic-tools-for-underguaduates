# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 批量下载论文全文（IP 级机构订阅授权 + 多 CDP 实例并行）
#
# 当前流程（2026-11-11）：
#   自动阶段最多 3 轮宿主机点击、**不弹人工窗**；全部篇目结束后记录
#   总数并弹 Yes/No，确认后才对未获取篇目进入人工验证阶段。
#   并发验证由 VERIFY_LOCK 串行；点击前校验目标标签页可见/聚焦且
#   URL 仍含 DOI；open/close tab 与验证互斥；后台标签 tiny 时先置前。
#
# 本次修复（针对旧版实测暴露的问题）：
#
#   [死机] batch2.log 显示：第 1 篇弹窗等待 15 分钟超时后，后续 7 篇全部
#          `WinError 10061 目标计算机积极拒绝` —— 单实例 Edge 已退出，
#          脚本却继续空跑。根因有两个：
#           ① 单实例模式：一个浏览器崩溃 = 整批任务失败；
#           ② `wait_for_manual()` 默认等 900s，且**任何**短页面都会触发。
#          现改为：多实例池（单实例崩溃自动切换）+ TCP 探活重连 +
#          任何一步都有硬超时。
#
#   [误触发 CAPTCHA] 旧版 `patient_load()` 用 `textlen < 6000` 当作
#          CAPTCHA 判据 → 勘误页/简讯页/未渲染完的页面统统误判，弹窗并
#          空等 15 分钟。现改为由 `detect.judge()` 精确判定：只有命中真实
#          挑战标记（just a moment / are you a robot / captcha challenge…）
#          才叫人；空白页判 not-ready 直接重试；短页面正常处理。
#
#   [误报封禁] 旧 BLOCK_MARKERS 含裸词 `forbidden` / `blocked by` / `封禁`，
#          生物医学正文（forbidden clone、blocked by inhibitor）会命中并
#          触发「全局中止」。现改用精确短语，模糊词仅限短页面开头。
#
# 用法：
#   python download_all_fulltexts.py --n=3 --limit=20        # 3 实例并行
#   python download_all_fulltexts.py --doi=10.1038/xxx       # 指定单篇
#   python download_all_fulltexts.py --status                # 查看进度
#   python download_all_fulltexts.py --sites=sciencedirect.com,nature.com
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import queue
import random
import re
import sys
import threading
import time
from urllib.parse import urlsplit


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding", None) or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            # 不替换 sys.stdout/stderr：临时 TextIOWrapper 被 GC 时会关闭
            # subprocess 管道的底层 fd，导致自检结束阶段出现 closed file。
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# ── 本文件属于**可选模块** `流程1/optional-cdp-fulltext/`：
#   核心脚本仍在 `流程1/scripts/`，这里把它加进 sys.path 才能 import。
#   整目录删掉 = 本模块不可用，流程改走备用链路（见本目录 README.md）。
_CORE_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(_CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CORE_SCRIPTS))

import cdp_pool                                    # noqa: E402
import detect                                      # noqa: E402
import scope                                       # noqa: E402
import store                                       # noqa: E402
from cdp import (CDP, CDPError, CDPTimeout, connect, is_alive,  # noqa: E402
                 open_tab, prune_blank_tabs, count_tabs)
from detect import BLOCKED, CHALLENGE, ERROR, NOT_READY, OK, PAYWALL, site_of  # noqa: E402
from logutil import get as get_log, journal, workdir as _wd    # noqa: E402

WD = _wd()
CDPDIR = WD / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
EXPORTS = store.exports_dir(WD)
STATE = CDPDIR / "fulltext_state.json"

LOG = get_log("download", wd=WD)

try:
    STATE_DATA: dict = json.loads(STATE.read_text(encoding="utf-8"))
except Exception:
    STATE_DATA = {}

_STATE_LOCK = threading.Lock()


def _dom_ready(port, log=None):
    try:
        return bool(cdp_pool.probe_endpoint(port).get("dom_usable"))
    except Exception as exc:
        if log:
            log.warn(f"CDP :{port} DOM 不可用: {exc}")
        return False


def _cleanup_owned(base_port, span, log):
    try:
        return cdp_pool.down(WD, base_port=base_port, span=span, log=log)
    except PermissionError as exc:
        log.warn(f"保留未登记或身份不符的浏览器，受控实例已按登记回收: {exc}")
        return 0


def save_state():
    with _STATE_LOCK:
        STATE.write_text(json.dumps(STATE_DATA, ensure_ascii=False, indent=1),
                         encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# 站点级熔断器：同一站点连续受阻即整轮跳过，避免「反复冲击 → 真封禁」
# ══════════════════════════════════════════════════════════════
class SiteGuard:
    def __init__(self, limit: int = 2, log=LOG):
        self.limit = limit
        self.log = log
        self.hits: dict[str, int] = {}
        # CAPTCHA 是独立信号：NOT_READY/ERROR 不应改变优先级。
        self.captcha_hits: dict[str, int] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._lk = threading.Lock()

    def lock(self, site: str) -> threading.Lock:
        with self._lk:
            return self.locks.setdefault(site, threading.Lock())

    def blocked(self, site: str) -> bool:
        return self.hits.get(site, 0) >= self.limit

    def hit(self, site: str, why: str) -> None:
        with self._lk:
            self.hits[site] = self.hits.get(site, 0) + 1
            n = self.hits[site]
        self.log.warn(f"站点 {site} 受阻 {n}/{self.limit}（{why}）"
                      + ("→ 本轮跳过该站点" if n >= self.limit else ""))

    def captcha_hit(self, site: str, why: str, doi: str = "") -> int:
        """记录宽限后仍需人工验证的 CAPTCHA，并返回累计次数。"""
        with self._lk:
            n = self.captcha_hits[site] = self.captcha_hits.get(site, 0) + 1
        self.log.warn(f"站点 {site} CAPTCHA {n} 次（{why}）")
        journal("E", "download.challenge", doi=doi, site=site, count=n, reason=why)
        return n

    def priority(self, site: str) -> int:
        with self._lk:
            return self.captcha_hits.get(site, 0)

    def summary(self) -> str:
        if not self.hits and not self.captcha_hits:
            return "无站点触发熔断"
        parts = [f"{k}×{v}" for k, v in self.hits.items()]
        parts.extend(f"{k}:CAPTCHA×{v}" for k, v in self.captcha_hits.items())
        return "；".join(parts)


GUARD = SiteGuard()


class SiteBlocked(Exception):
    """真实封禁文案 —— 立即终止整轮全部后续请求。"""


# ══════════════════════════════════════════════════════════════
# PDF 工具
# ══════════════════════════════════════════════════════════════
FETCH_JS = """
(async (u) => {
  try {
    const r = await fetch(u, {credentials:'include'});
    if (!r.ok) return JSON.stringify({ok:false, status:r.status});
    const b = await r.arrayBuffer(); const by = new Uint8Array(b);
    let s = ''; const CH = 0x8000;
    for (let i = 0; i < by.length; i += CH) s += String.fromCharCode.apply(null, by.subarray(i, i+CH));
    return JSON.stringify({ok:true, status:r.status, size:by.length, b64:btoa(s)});
  } catch(e) { return JSON.stringify({ok:false, error:String(e).slice(0,140)}); }
})"""


def _write_pdf(raw: bytes, out_path: pathlib.Path):
    """写盘 + 校验，返回 (why, err)。

    走 store.save_bytes()：临时文件写盘 → 校验 → os.replace 到最终路径。
    校验不通过时**最终路径从未被创建**，因此既不会误删同名旧 PDF，也不会让
    下一轮的 has_pdf() 判定「文件缺失」而无谓重下（旧版直写最终路径再 unlink，
    与基于磁盘文件的去重判据组合成重下闭环，见 store.save_bytes 的说明）。
    """
    got, why = store.save_bytes(raw, out_path)
    if got is None:
        return None, why
    return why, None


def safe_stem(doi: str, year, title: str | None = None) -> str:
    """文件命名：默认 <year>_<题名>（题名缺失回退 <year>_<DOI slug>）。"""
    return store.stem_for(doi, year, title)


# ══════════════════════════════════════════════════════════════
# 人工验证（默认关闭 —— 只在显式 --manual-wait>0 时启用）
# ══════════════════════════════════════════════════════════════
def notify_user(title: str, message: str, timeout_s: float = 180.0) -> None:
    """弹窗提醒（WScript.Shell.Popup：**带自动关闭**，无人值守时不会永久残留）。

    比 MessageBox 更适合本流程：无人值守超时自动消失，不阻塞脚本
    （后台 powershell 进程，主流程照常跑）。
    """
    msg = message.replace("'", "''")
    ttl = max(20, int(timeout_s))
    ps = ("$w = New-Object -ComObject WScript.Shell;"
          f"$w.Popup('{msg}',{ttl},'{title.replace(chr(39), chr(39)*2)}',48) | Out-Null")
    try:
        import subprocess
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def activate_edge_window(port: int | None = None) -> None:
    """Compatibility name: focus only this workdir's verified browser root."""
    if port is None:
        return
    try:
        import cdp_pool
        reg = cdp_pool._load_registry(WD)
        info = cdp_pool._owned_identity(WD.resolve(), int(port), reg.get(str(port), {}))
        if not info:
            return
        pid = int(info['pid'])
    except Exception:
        return  # Never fall back to the first Edge/Chrome window.
    ps = ("$ws = New-Object -ComObject wscript.shell;"
          f"$ws.AppActivate({pid}) | Out-Null")
    try:
        import subprocess
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


_HOST_CLICKS: dict[str, int] = {}
# 验证阶段的全局锁：多浏览器/多标签并行时，任何会改变前台标签页的动作
# （bring_to_front、新开/关闭标签）都必须与“定位-截图-点击-判读”互斥，
# 否则 solver 截图后可能被另一个线程切走标签，点到别的页面。
VERIFY_LOCK = threading.RLock()


def ask_yes_no(title: str, message: str, timeout_s: float = 600.0) -> bool:
    """阻塞式 Yes/No 弹窗；超时/无交互桌面返回 False。

    与 notify_user 的区别：本函数等待用户选择，用于“自动阶段结束后是否
    继续人工验证剩余篇目”。用 WScript.Shell.Popup 的返回值 6=Yes / 7=No。
    """
    import ctypes as _ct
    if not _ct.windll.user32.GetForegroundWindow():
        # 无交互桌面时弹窗没人能点，避免白等 10 分钟。
        return False
    msg = message.replace("'", "''")
    ps = ("$w = New-Object -ComObject WScript.Shell;"
          f"$r = $w.Popup('{msg}',{max(30, int(timeout_s))},"
          f"'{title.replace(chr(39), chr(39)*2)}',4+32+4096);"
          "Write-Output $r")
    try:
        import subprocess
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout_s + 30)
        lines = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
        return bool(lines and lines[-1] == "6")
    except Exception:
        try:
            return input(f"{title}\n{message}\n继续？[y/N] ").strip().lower() in ("y", "yes", "是")
        except Exception:
            return False


def _ensure_active_tab(c: CDP, landing: str, log) -> bool:
    """把**本会话的标签页**置前，并核对它确实是当前可见/聚焦的页面。

    这是多标签并发的关键：solver 只能看到“某个挑战窗口”，无法知道它属于
    哪个标签。先在锁内 bringToFront，再用 visibilityState/hasFocus/URL
    确认，才允许截图点击。
    """
    with VERIFY_LOCK:
        try:
            c.bring_to_front()
            activate_edge_window(getattr(c, "port", None))
            time.sleep(0.35)
            raw = c.eval(
                "JSON.stringify({v:document.visibilityState,"
                "f:document.hasFocus(),u:location.href})", timeout=8)
            info = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if info.get("v") != "visible" or not info.get("f"):
                log.warn(f"  目标标签页不在前台（visibility={info.get('v')}, "
                         f"focus={info.get('f')}），本轮不点击")
                return False
            want = (urlsplit(landing).hostname or "").lower()
            got = (urlsplit(str(info.get("u") or "")).hostname or "").lower()
            cur = str(info.get("u") or "")
            # doi.org 会跳到出版社域名；只要当前 URL 里仍带同一 DOI，
            #   就仍是目标文章（不能只看 host，否则 ACS 等跳转会被误判）。
            doi_path = urlsplit(landing).path.lstrip("/").lower()
            same_doi = bool(doi_path and doi_path in cur.lower())
            if want and got and want != got and not same_doi:
                log.warn(f"  前台标签页不是目标文章（{got} != {want}，"
                         f"URL 不含 {doi_path}），本轮不点击")
                return False
            return True
        except Exception as e:
            log.warn(f"  校验前台标签页失败: {type(e).__name__}: {str(e)[:80]}")
            return False


def wait_for_solver(c: CDP, doi: str, landing: str, log,
                    attempts: int = 3) -> bool:
    """自动验证：最多 attempts 轮；每轮都重新确认目标标签页在前台。"""
    with VERIFY_LOCK:
        for i in range(1, attempts + 1):
            # 点击前再判一次：上一轮可能已经放行，但页面还在加载；
            #   若不复查就在文章页上继续点，会点到正文里的链接。
            pre = detect.judge(c, timeout=15)
            if pre["state"] in (OK, PAYWALL):
                return True
            if pre["state"] != CHALLENGE:
                return False
            if not _ensure_active_tab(c, landing, log):
                time.sleep(1.0)
                continue
            log.info(f"  自动验证第 {i}/{attempts} 轮 …")
            _run_host_solver(c, doi, log, f"auto{i}")
            time.sleep(1.0)
            r = detect.judge(c, timeout=20)
            if r["state"] in (OK, PAYWALL):
                log.ok(f"  自动验证通过（第 {i} 轮，文本 {r.get('textlen')}）")
                return True
            if r["state"] != CHALLENGE:
                return False
        log.warn(f"  自动验证 {attempts} 轮仍未通过，转为待人工")
        return False


def _browser_pid(port: int) -> int:
    try:
        reg = json.loads((CDPDIR / "cdp_instances.json").read_text(encoding="utf-8"))
        return int((reg.get(str(port)) or {}).get("pid") or 0)
    except Exception:
        return 0


def _run_host_solver(c: CDP, doi: str, log, tag: str = "host") -> bool:
    """调用宿主机级点击；自动阶段最多 3 轮，每轮一次点击。"""
    if os.environ.get("UC_HOST_CLICK") != "1":
        return False
    key = str(doi)
    if _HOST_CLICKS.get(key, 0) >= 3:
        log.warn("  宿主机点击已达每篇 3 轮上限，停止自动点击")
        return False
    solver = os.environ.get(
        "UC_HOST_SOLVER", str(pathlib.Path(__file__).with_name("cf_host_solver.py")))
    if not os.path.exists(solver):
        log.warn(f"  host solver 不存在：{solver}")
        return False
    import ctypes as _ct
    if not _ct.windll.user32.GetForegroundWindow():
        log.warn("  GetForegroundWindow=0（当前无前台窗口）；仍尝试宿主机点击，"
                 "失败会在 solver.log 中给出原因")
    import subprocess as _sp
    out = pathlib.Path(os.environ.get("UC_HOST_OUT", r"C:\s2\artifacts\cf\flow")) / (
        re.sub(r"[^0-9A-Za-z]+", "_", doi)[:60] + ("" if tag == "host" else f"_{tag}"))
    out.mkdir(parents=True, exist_ok=True)
    pid = _browser_pid(getattr(c, "port", 0))
    cmd = [sys.executable, solver, "--rounds", "1", "--wait", "40", "--out", str(out)]
    if pid:
        cmd += ["--pid", str(pid)]
    log.info(f"  宿主机点击 solver → {out}（窗口 pid={pid or '未锁定'}）")
    _HOST_CLICKS[key] = _HOST_CLICKS.get(key, 0) + 1
    try:
        p = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=200)
        (out / "solver.log").write_text(
            f"cmd={cmd}\nrc={p.returncode}\n--- stdout ---\n{p.stdout or ''}\n"
            f"--- stderr ---\n{p.stderr or ''}", encoding="utf-8")
        tail = " | ".join((p.stdout or "").strip().splitlines()[-2:])
        log.info(f"  solver exit={p.returncode}" + (f"：{tail[:200]}" if tail else ""))
    except Exception as e:
        log.warn(f"  solver 调用失败: {str(e)[:120]}")
    return True


def wait_for_manual(c: CDP, doi: str, max_wait: float, log,
                    notify: bool = False) -> bool:
    """人工阶段：把标签页置前并等待用户完成验证。

    自动阶段**不调用**本函数，因此不会出现“自动化已暂停”的弹窗。
    只有主流程在自动阶段结束后、用户明确选择继续时，才以 notify=True 调用。
    """
    if max_wait <= 0:
        return False
    if notify:
        notify_user("请完成人工验证",
                    f"论文 DOI: {doi}\n\n请在浏览器窗口中完成验证（勾选 / 拼图）。\n"
                    f"完成后无需操作，流程会自动继续。\n（最长等待 {max_wait/60:.0f} 分钟）",
                    timeout_s=max_wait + 30)
        log.warn(f"请人工完成验证（最长 {max_wait/60:.0f} 分钟）")
    with VERIFY_LOCK:
        c.bring_to_front()
        activate_edge_window(c.port)
    t0 = time.time()
    last = -1
    while time.time() - t0 < max_wait:
        time.sleep(6)
        try:
            r = detect.judge(c, timeout=15)
        except Exception as e:
            log.warn(f"  探针异常（继续等待）: {str(e)[:60]}")
            continue
        if r["state"] in (OK, PAYWALL):
            log.ok(f"验证已通过（{r['reason']}, 文本 {r.get('textlen')}）")
            # 验证刚通过时正文往往只渲染了一部分（实测 4005 字符），
            #   此时 meta[citation_pdf_url] 还没出现 → 会误报 no-pdf-link。
            #   这里再等正文长度稳定下来，才交给后续抽取。
            stable, prev, waited = 0, r.get("textlen", 0), 0.0
            while waited < 20 and stable < 2:
                time.sleep(3)
                waited += 3
                cur = (detect.probe(c, timeout=15) or {}).get("textlen", 0)
                stable = stable + 1 if cur == prev and cur > 0 else 0
                prev = cur
            log.info(f"  正文稳定于 {prev} 字符（+{waited:.0f}s）")
            return True
        if r.get("textlen", 0) != last:
            last = r.get("textlen", 0)
            log.info(f"  等待中 [{time.time()-t0:.0f}s] 文本 {last}")
            with VERIFY_LOCK:
                c.bring_to_front()
    log.warn("等待人工验证超时，跳过该篇")
    return False


# ══════════════════════════════════════════════════════════════
# 单篇处理
# ══════════════════════════════════════════════════════════════
def settle_challenge(c: CDP, log, grace: float = 25.0, poll: float = 3.0,
                     doi: str = "") -> dict:
    """挑战页**宽限期**：Cloudflare 的 JS 挑战多数会在几秒内自动放行。

    实测（2026-09-11）：academic.oup.com 首次导航返回标题「请稍候…」
      正文仅 118 字符；同一页面 6 秒后再探已完整加载（55265 字符）。
      若一见 challenge 就放弃，会把**本可自动通过**的站点误判为需人工验证
      —— 这是「误触发 CAPTCHA」的第二种形态。

    因此：命中 challenge 后先原地观察 grace 秒；期间一旦转为可读页面即继续。
    """
    t0 = time.time()
    r = detect.judge(c, timeout=20)
    host_tried = False
    while r["state"] == CHALLENGE and time.time() - t0 < grace:
        time.sleep(poll)
        r = detect.judge(c, timeout=20)
        log.info(f"    挑战宽限 {time.time()-t0:.0f}/{grace:.0f}s → {r['state']} "
                 f"(文本 {r.get('textlen')})")
        # UC_HOST_CLICK=1 时在宽限期内提前试一次宿主机点击：
        #   人工在宽限期里先点掉时，旧流程永远不会调用 solver，日志还会误写成
        #   「自动通过」。提前试可让脚本自己的点击有可核对的 solver.log。
        if (not host_tried and doi and r["state"] == CHALLENGE
                and os.environ.get("UC_HOST_CLICK") == "1"
                and os.environ.get("UC_HOST_CLICK_EARLY") == "1"
                and time.time() - t0 >= min(12.0, max(4.0, grace * 0.4))):
            _run_host_solver(c, doi, log, "grace")
            host_tried = True
    return r


def load_landing(c: CDP, landing: str, doi: str, log, max_wait: float,
                 retries: int = 2, grace: float = 25.0, site: str = "",
                 fulltext_cap: int = 0, phase: str = "auto") -> dict:
    """导航落地页 → 精确判定状态 → 必要时重试/等人。返回判定结果 dict。

    fulltext_cap > 0 时，判定结果里会附带 `fulltext` —— 页面正文前 N 字符。
    这是"边下 PDF 边解读"的关键：正文此刻已在浏览器内存里，
      顺带回传几乎零成本，无需为解读再导航一次。
    """
    last = {}
    for attempt in range(retries + 1):
        try:
            c.navigate(landing, settle=2.5, load_timeout=25)
        except CDPTimeout as e:
            log.warn(f"导航超时: {e}")
            last = {"state": ERROR, "reason": "nav-timeout", "textlen": 0}
            continue
        except CDPError as e:
            last = {"state": ERROR, "reason": str(e)[:100], "textlen": 0}
            continue

        # 加载后再补一次同意书清理：部分站点弹窗在 load 之后才出现，
        #   注入脚本的定时巡查可能刚好错过；这一步确保弹窗不遮挡正文。
        try:
            c.dismiss_consent(timeout=6)
        except Exception:
            pass

        r = detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
        st = r["state"]
        # 多标签并发时，后台标签会被 Chrome 节流：页面长时间 tiny/not-ready，
        #   既不像 challenge 也不像正文。此时把该标签置前，给它一次渲染机会。
        if st == NOT_READY and (r.get("textlen") or 0) < 200:
            with VERIFY_LOCK:
                if _ensure_active_tab(c, landing, log):
                    time.sleep(1.0)
                    r = detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
                    st = r["state"]
                    log.info(f"  后台标签置前重判 → {st}（文本 {r.get('textlen')}）")
        log.info(f"  状态={st} ({r['reason']}) 文本={r.get('textlen')} "
                 f"订阅={r.get('subscribed')} 付费墙={r.get('paywalled')}")

        # 挑战页先给宽限期，多数 Cloudflare JS 挑战会自动放行
        if st == CHALLENGE:
            r = settle_challenge(c, log, grace=grace, doi=doi)
            st = r["state"]
            if st != CHALLENGE:
                log.info(f"  挑战已解除（自动或人工，CDP 无法区分来源） → {st}（文本 {r.get('textlen')}）")

        if st == BLOCKED:
            raise SiteBlocked(
                f"检测到站点封禁文案：{r.get('markers')} @ {site_of(r.get('url',''))}\n"
                "    [STOP] 已停止本轮全部后续请求（含其他站点）。\n"
                "    [STOP] 请人工确认后改用机构 VPN / 图书馆代查等正规途径。")
        if st in (OK, PAYWALL):
            return r
        if st == CHALLENGE:
            # 自动阶段：最多 3 轮宿主机点击，**不弹窗、不等待人工**；
            # 人工阶段：仅在用户确认后才进入阻塞等待。
            if phase == "auto" and wait_for_solver(c, doi, landing, log, attempts=3):
                return detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
            if phase == "manual" and wait_for_manual(c, doi, max_wait, log, notify=True):
                return detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
            # 仅统计**最终未解除**的挑战，避免自动点掉的页面触发站点熔断。
            if site:
                GUARD.captcha_hit(site, r.get("reason", "challenge"), doi=doi)
            return r
        if st == NOT_READY and attempt < retries:
            log.info(f"  页面未就绪，{4*(attempt+1)}s 后重试 …")
            time.sleep(4 * (attempt + 1))
            continue
        last = r
    return last


def try_fetch_pdf(c: CDP, landing: str, pdf_url: str, out: pathlib.Path,
                  log, timeout: float = 120) -> dict:
    """策略①：在落地页上下文 fetch（同源直链最稳）。"""
    try:
        if c.get_url() != landing:
            c.navigate(landing, settle=2.0, load_timeout=25)
            # 重新导航后同意书弹窗可能再弹一次（未写 cookie 的站点），补一刀
            try:
                c.dismiss_consent(timeout=6)
            except Exception:
                pass
        raw = c.eval(f"({FETCH_JS})({json.dumps(pdf_url)})", timeout=timeout)
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if d.get("ok") and d.get("b64"):
            data = base64.b64decode(d["b64"])
            if data[:4] == b"%PDF":
                why, err = _write_pdf(data, out)
                if why:
                    return {"ok": True, "size": len(data), "file": out.name,
                            "how": "landing-fetch", "pages": why}
                return {"ok": False, "error": f"invalid:{err}"}
            return {"ok": False, "error": "not-pdf-body"}
        return {"ok": False, "error": f"http-{d.get('status') or d.get('error')}"}
    except Exception as e:
        return {"ok": False, "error": f"fetch:{type(e).__name__}:{str(e)[:70]}"}


def try_redirect_fetch(c: CDP, pdf_url: str, out: pathlib.Path,
                       log, timeout: float = 120) -> dict:
    """策略②：导航到 PDF 链接 → 浏览器跟随重定向到签名直链 → 页内 fetch。"""
    try:
        c.call("Page.navigate", {"url": pdf_url}, timeout=20)
    except CDPError as e:
        return {"ok": False, "error": f"nav:{str(e)[:60]}"}
    for _ in range(12):                      # 最多等 12s 让重定向完成
        time.sleep(1)
        try:
            cur = c.get_url(timeout=6)
        except CDPError:
            return {"ok": False, "error": "target-destroyed"}
        if cur and cur != pdf_url:
            break
    try:
        raw = c.eval(f"({FETCH_JS})(location.href)", timeout=timeout)
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if d.get("ok") and d.get("b64"):
            data = base64.b64decode(d["b64"])
            if data[:4] == b"%PDF":
                why, err = _write_pdf(data, out)
                if why:
                    return {"ok": True, "size": len(data), "file": out.name,
                            "how": "redirect-fetch", "pages": why}
                return {"ok": False, "error": f"invalid:{err}"}
            return {"ok": False, "error": "not-pdf-body"}
        return {"ok": False, "error": f"http-{d.get('status') or d.get('error')}"}
    except Exception as e:
        return {"ok": False, "error": f"redirect:{type(e).__name__}:{str(e)[:60]}"}


def try_render(c: CDP, landing: str, out: pathlib.Path, log,
               timeout: float = 150) -> dict:
    """策略③：渲染兜底 —— 有全文权限但 PDF 端点被拦时，把正文页打印为 PDF。"""
    try:
        c.navigate(landing, settle=2.5, load_timeout=25)
        # 必须在量正文长度**之前**关弹窗：同意书横幅的文字会一起计入
        #   innerText，既可能把不足 15000 字符的正文「凑」过门槛，也会让
        #   横幅本身被 Page.printToPDF 打进成品 PDF。
        try:
            c.dismiss_consent(timeout=6)
        except Exception:
            pass
        ok = False
        for _ in range(7):
            t = c.get_text(timeout=15) or ""
            if len(t) > 15000:
                ok = True
                break
            time.sleep(3)
        if not ok:
            return {"ok": False, "error": "page-not-ready-for-render"}
        r = c.call("Page.printToPDF", {
            "printBackground": False, "preferCSSPageSize": True,
            "marginTop": 0.6, "marginBottom": 0.6, "marginLeft": 0.5, "marginRight": 0.5,
        }, timeout=timeout)
        data = r.get("data")
        if not data:
            return {"ok": False, "error": "empty-print"}
        raw = base64.b64decode(data)
        if raw[:4] != b"%PDF":
            return {"ok": False, "error": "print-not-pdf"}
        why, err = _write_pdf(raw, out)
        if why:
            return {"ok": True, "size": len(raw), "file": out.name,
                    "how": "rendered", "pages": why}
        return {"ok": False, "error": f"render-invalid:{err}"}
    except Exception as e:
        return {"ok": False, "error": f"render:{type(e).__name__}:{str(e)[:60]}"}


def save_fulltext(doi: str, year, text: str, log=None) -> str:
    """把落地页正文落盘到 `.work_cdp/fulltext/<slug>.txt`，返回路径（失败返回空串）。

    为什么单独放 `.work_cdp/` 而不是交付目录：
      这是**解读的中间素材**，不是交付物。放进 EXPORTS 会污染用户的论文目录
      （那里只应有 PDF 与清单），也会让"全文清单"的统计口径变乱。
    为什么值得存：阶段 H 解读原本要 `to_markdown` 从 PDF 再提一次文本；
      有了这份正文，解读可以在**PDF 还没下完时**就开始（见 §阶段 E 并行说明）。
    """
    if not text or len(text) < 1500:      # 太短的多半是摘要页/占位页，不值得存
        return ""
    try:
        d = CDPDIR / "fulltext"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{safe_stem(doi, year)}.txt"
        # 头部落盘元信息，便于解读时知道来源与时间
        header = (f"# source: https://doi.org/{doi}\n"
                  f"# doi: {doi}\n# year: {year}\n"
                  f"# chars: {len(text)}\n"
                  f"# note: 落地页正文，非 PDF 提取；可能与最终排版有差异\n"
                  f"{'-' * 60}\n")
        p.write_text(header + text, encoding="utf-8")
        return str(p)
    except Exception as e:
        if log:
            log.warn(f"  正文落盘失败（不影响 PDF 结果）：{type(e).__name__}:{str(e)[:60]}")
        return ""


def process_one(port: int, doi: str, meta: dict, opts: dict, log) -> dict:
    """处理单篇：连接（新 CDP 会话）→ 落地页 → 判定 → 取 PDF。

    v8：当 opts["fulltext_cap"] > 0 时，**顺带**把落地页正文落盘，
    供解读阶段与下载并行使用。取正文不额外发请求 —— 它已在页面内存里。
    """
    year = meta.get("year") or ""
    landing = "https://doi.org/" + doi
    out_pdf = EXPORTS / f"{safe_stem(doi, year, meta.get('title'))}.pdf"
    rec: dict = {"doi": doi, "year": year,
                 "title": meta.get("title") or "", "journal": meta.get("journal") or ""}

    # v8：多标签页模式下每篇独占一个标签页（见 run_pool），
    #   标签页用完即关，避免导航到 PDF 后目标被顶替导致后续连接失效
    tabbed = bool(opts.get("multi_tab"))
    phase = opts.get("verification", "auto")
    try:
        if tabbed:
            # 新开标签会抢占前台，必须与“验证点击”互斥，否则会把正在
            #   识别中的挑战页切走，solver 截图后点到别的标签。
            with VERIFY_LOCK:
                c = open_tab("about:blank", port, timeout=opts["call_timeout"], logger=log)
        else:
            with VERIFY_LOCK:
                c = connect(port, timeout=opts["call_timeout"], logger=log)
    except Exception as e:
        rec.update({"pdf_ok": False, "pdf_error": f"cdp-connect:{type(e).__name__}:{e}"})
        return rec

    try:
        r = load_landing(c, landing, doi, log, opts["manual_wait"],
                         grace=opts["grace"], site=detect.site_of_doi(doi),
                         fulltext_cap=opts.get("fulltext_cap", 0), phase=phase)
        rec.update({
            "url": r.get("url") or "", "title": r.get("citeTitle") or r.get("title") or rec["title"],
            "journal": r.get("journal") or rec["journal"],
            "state": r["state"], "textlen": r.get("textlen"),
            "subscribed": r.get("subscribed"), "paywalled": r.get("paywalled"),
            "abstract": (r.get("abstract") or "")[:2600],
        })

        # 正文落盘：**先于 PDF 尝试**，这样即使 PDF 最终失败，
        #   解读素材也已经拿到（付费墙/降级场景尤其重要）
        ft = r.get("fulltext") or ""
        if opts.get("fulltext_cap", 0) > 0 and ft:
            fp = save_fulltext(doi, year, ft, log)
            if fp:
                rec["fulltext_file"] = fp
                rec["fulltext_chars"] = len(ft)
                log.info(f"  [FILE] 正文已存 {len(ft)} 字符 → {pathlib.Path(fp).name}")

        if r["state"] in (CHALLENGE, NOT_READY, ERROR):
            rec.update({"pdf_ok": False,
                        "pdf_error": f"{r['state']}:{r['reason']}"})
            return rec

        stem = safe_stem(doi, year, r.get("citeTitle") or meta.get("title"))
        out_pdf = EXPORTS / f"{stem}.pdf"

        # ① 出版社原生 PDF（meta[citation_pdf_url] 优先）
        pdf_url = r.get("metaPdf") or (r.get("links") or [None])[0]
        if pdf_url:
            rec["pdf_url"] = pdf_url
            for fn, tag in ((try_fetch_pdf, "landing-fetch"), (try_redirect_fetch, "redirect-fetch")):
                if fn is try_fetch_pdf:
                    res = fn(c, landing, pdf_url, out_pdf, log, opts["pdf_timeout"])
                else:
                    res = fn(c, pdf_url, out_pdf, log, opts["pdf_timeout"])
                if res.get("ok"):
                    rec.update({"pdf_ok": True, "pdf_file": res["file"],
                                "pdf_size": res["size"], "pdf_src": res["how"],
                                "pdf_pages": res.get("pages")})
                    return rec
                rec["pdf_error"] = res.get("error")
                log.info(f"  {tag} 失败: {res.get('error')}")
        else:
            rec["pdf_error"] = "no-pdf-link"

        # ② 渲染兜底（仅在有全文权限、正文足够长时）
        if opts["render"] and (r.get("textlen") or 0) > 15000 and not r.get("paywalled"):
            res = try_render(c, landing, out_pdf, log, opts["pdf_timeout"])
            if res.get("ok"):
                rec.update({"pdf_ok": True, "pdf_file": res["file"],
                            "pdf_size": res["size"], "pdf_src": "rendered",
                            "pdf_pages": res.get("pages")})
                return rec
            rec["pdf_error"] = f"{rec.get('pdf_error')} | {res.get('error')}"
            log.info(f"  render 失败: {res.get('error')}")

        rec["pdf_ok"] = False
        return rec
    except SiteBlocked:
        raise
    except Exception as e:
        rec.update({"pdf_ok": False, "pdf_error": f"{type(e).__name__}:{str(e)[:90]}"})
        return rec
    finally:
        # 多标签页模式：先关标签页再关 ws（反序会留下孤儿标签，累积拖慢浏览器）。
        #   导航到 PDF 后原目标已被 PDF 查看器顶替，此时关标签可能失败 —— 正常。
        try:
            if tabbed:
                with VERIFY_LOCK:
                    c.close_tab_self()
            else:
                c.close()
        except Exception:
            pass


def run_manual_phase(failed: list[tuple[str, dict]], ports: list[int],
                     opts: dict, log) -> list[dict]:
    """自动阶段结束后，按用户确认对失败篇目逐篇做人工验证。

    单线程顺序执行：每篇打开一个标签页并置前，弹窗提示 DOI，等待用户完成
    验证后再取 PDF。与自动阶段的 solver 不再并发，避免抢焦点。
    """
    if not failed:
        return []
    manual = dict(opts)
    manual["verification"] = "manual"
    manual["manual_wait"] = opts["manual_wait"] if opts["manual_wait"] > 0 else 300.0
    port = next((p for p in ports if _dom_ready(p, log)), 0)
    if not port:
        log.warn("人工阶段没有可用 CDP 实例，跳过")
        return []
    out: list[dict] = []
    for idx, (doi, meta) in enumerate(failed, 1):
        log.info(f"[MANUAL {idx}/{len(failed)}] {doi} —— 请在浏览器完成验证")
        rec = process_one(port, doi, meta, manual, log)
        rec["attempts"] = int((STATE_DATA.get(doi) or {}).get("attempts") or 0) + 1
        rec["manual_phase"] = True
        STATE_DATA[doi] = rec
        save_state()
        if rec.get("pdf_ok"):
            try:
                store.register_pdf(doi, EXPORTS / rec["pdf_file"], WD)
            except Exception:
                pass
            log.ok(f"  [MANUAL OK] {rec.get('pdf_size',0)//1024} KB | {rec.get('pdf_file')}")
            journal("E", "download.pdf.manual", doi=doi, file=rec.get("pdf_file"),
                    size_kb=rec.get("pdf_size", 0) // 1024)
        else:
            log.warn(f"  [MANUAL FAIL] {doi}: {rec.get('pdf_error')}")
            journal("E", "download.fail.manual", doi=doi, reason=rec.get("pdf_error"))
        out.append(rec)
    return out


# ══════════════════════════════════════════════════════════════
# 并行调度
# ══════════════════════════════════════════════════════════════
def run_pool(items: list[tuple[str, dict]], ports: list[int], opts: dict, log) -> list[dict]:
    """多浏览器 × 多标签页并行；同站点串行 + 站点间隔。

    v8 改造（原为「多浏览器 × 单标签页」）：
      旧设计的并发度 = 浏览器进程数。想提速只能多开 Edge，而每个进程
      约占 200–400 MB，开 6 个就吃掉 2 GB，启动与崩溃恢复也慢。
      实测瓶颈在网络等待（导航 + 出版社响应），CPU/内存远没到顶，
      所以改为「每浏览器开 T 个标签页」，并发度 = 浏览器数 × 标签页数，
      增开成本远低于新开浏览器（标签页共享进程池与网络栈）。

    [STOP] 两条不变量（改造不得破坏）：
      ① **同站点仍然完全串行** —— GUARD.lock(site) + pace(site) 同时覆盖
         "取任务"与"执行任务"，所以同一出版社永远不会被两个线程同时打。
         这是反封禁红线，与标签页数无关。
      ② **一个标签页只归一个线程** —— 标签页在 process_one 内部新开、
         用完即关（close_tab_self），绝不跨任务复用。
    """
    # 动态优先队列：每次取任务时按最新 CAPTCHA 计数重排。
    # 这样某站点一旦触发验证，其余该站点任务会前置，减少用户反复处理弹窗。
    pending = list(items)
    pending_lock = threading.Lock()
    results: list[dict] = []
    rlock = threading.Lock()
    stop = threading.Event()
    last_site_hit: dict[str, float] = {}
    site_time_lock = threading.Lock()

    tabs_per_browser = max(1, int(opts.get("tabs_per_browser", 1)))

    def take_next():
        """取下一个任务：优先处理已触发 CAPTCHA 的站点，其次年份新者。

        [STOP] v8 性能修复：旧实现对**每个** pending 项调用 `GUARD.priority()`，
          而 `priority()` 每次都要抢 `GUARD._lk`。于是在持有 `pending_lock`
          的同时反复获取另一把锁 —— 线程越多（多标签页）争用越剧烈。
          实测空载 12 篇：单通道 0.012s，6 通道 **4.16s**（350 倍劣化），
          而这与网络、与下载本身毫无关系，纯属调度自伤。

        改为：**一次性快照**各站点优先级（只抢一次 _lk），再用快照排序。
        """
        with pending_lock:
            if not pending:
                return None
            # 先一次性取回全部相关站点的优先级（单次加锁）
            prio = {s: GUARD.priority(s)
                    for s in {detect.site_of_doi(d) for d, _ in pending}}
            best = max(range(len(pending)), key=lambda i: (
                prio.get(detect.site_of_doi(pending[i][0]), 0),
                pending[i][1].get("year") or 0,
                -i,
            ))
            return pending.pop(best)

    def pace(site: str):
        """同站点最小间隔（全局串行），避免并发冲击同一出版社。"""
        lo, hi = opts["delay"]
        with site_time_lock:
            now = time.time()
            wait = last_site_hit.get(site, 0) + random.uniform(lo, hi) - now
            if wait > 0:
                log.info(f"  [WAIT] 同站点节流 {wait:.0f}s（{site}）")
                time.sleep(wait)
            last_site_hit[site] = time.time()

    def worker(port: int, slot: int):
        """一个 (port, slot) 组合 = 一个并发通道 = 每次新开一张标签页。"""
        tag = f"{port}#{slot}" if tabs_per_browser > 1 else str(port)
        while not stop.is_set():
            item = take_next()
            if item is None:
                return
            doi, meta = item
            site = detect.site_of_doi(doi)   # 按 DOI 前缀定站点（落地前后一致）
            try:
                # 站点锁包住整个"节流 + 处理 + 失败记账"，保证同站点严格串行。
                #
                # [STOP] v8 修的一个重要缺陷：**失败计数必须在锁内更新**。
                #   旧版把 GUARD.hit(site,…) 放在锁外（run_pool 末尾统一记账），
                #   于是已排队等在锁上的线程会用**过期的 hits 值**通过 blocked()
                #   检查 —— 实测 limit=2 时放进去了 3 个（多标签页下更多）。
                #   现在：锁内判定 → 执行 → 锁内立即记账，后来者读到的就是新值。
                with GUARD.lock(site):
                    if GUARD.blocked(site):
                        log.warn(f"跳过 {doi}（站点 {site} 已熔断）")
                        with rlock:
                            results.append({"doi": doi, "pdf_ok": False,
                                            "pdf_error": f"site-circuit-open:{site}"})
                        continue
                    pace(site)
                    if not _dom_ready(port, log):
                        log.warn(f"端口 {port} 页面不可用，尝试恢复 …")
                        if not cdp_pool.launch_one(WD, port, level=None, log=log):
                            with rlock:
                                results.append({"doi": doi, "pdf_ok": False,
                                                "pdf_error": f"cdp-down:{port}"})
                            continue
                    log.info(f"[{tag}] {doi} ({meta.get('year')})")
                    # 多标签页模式：process_one 内部 open_tab 新开独占标签页
                    rec = process_one(port, doi, meta, opts, log)
                    # 锁内记账：把受阻状态立刻反映到 hits，
                    #   使仍在排队的同站点任务读到最新计数（见上方说明）
                    if rec.get("state") in (CHALLENGE, NOT_READY, ERROR, BLOCKED):
                        GUARD.hit(site, rec.get("state"))
            except SiteBlocked as e:
                log.error(str(e))
                with rlock:
                    results.append({"doi": doi, "pdf_ok": False,
                                    "pdf_error": "SITE-BLOCKED", "blocked": True})
                stop.set()
                return
            except Exception as e:
                rec = {"doi": doi, "pdf_ok": False,
                       "pdf_error": f"{type(e).__name__}:{str(e)[:90]}"}

            with rlock:
                # 尝试计数：与「校验通过才落盘」配套。二者缺一，一篇取不到的论文
                # 就会每轮都被判为待办、反复重下，反复消耗同一站点的配额，进而
                # 可能把整站打进 site_fail_limit 熔断，殃及其余本来能取的论文。
                # 站点级熔断/端口失联的跳过不计入 —— 那两种情况根本没尝试过这篇。
                rec["attempts"] = int((STATE_DATA.get(doi) or {}).get("attempts") or 0) + 1
                results.append(rec)
                STATE_DATA[doi] = rec
                save_state()
                if rec.get("pdf_ok"):
                    # 登记 DOI → 文件名（题名命名后，去重判断仍可靠）
                    try:
                        store.register_pdf(doi, EXPORTS / rec["pdf_file"], WD)
                    except Exception:
                        pass
                    log.ok(f"  [OK] {rec.get('pdf_size',0)//1024} KB | {rec.get('pdf_file')} "
                           f"[{rec.get('pdf_src')}] 累计 {sum(1 for v in STATE_DATA.values() if v.get('pdf_ok'))}")
                    journal("E", "download.pdf", doi=doi, year=rec.get("year"),
                            file=rec.get("pdf_file"), size_kb=rec.get("pdf_size", 0) // 1024,
                            src=rec.get("pdf_src"), pages=rec.get("pdf_pages"))
                else:
                    log.warn(f"  [WARN] 失败: {rec.get('pdf_error')}")
                    journal("E", "download.fail", doi=doi, year=rec.get("year"),
                            state=rec.get("state"), reason=rec.get("pdf_error"))
                # 注意：受阻计数已移至站点锁内（见上方 with GUARD.lock(site) 块）。
                #   这里**不要**再记一次，否则会重复计数、让熔断提前触发。

    # 并发通道 = 浏览器端口 × 每浏览器标签页数
    slots = [(p, s) for p in ports for s in range(tabs_per_browser)]
    log.info(f"并发通道 {len(slots)} 个 = {len(ports)} 浏览器 × {tabs_per_browser} 标签页"
             + ("（多标签页模式）" if tabs_per_browser > 1 else ""))
    threads = [threading.Thread(target=worker, args=(p, s), daemon=True)
               for p, s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # v8：收尾清理多标签页模式留下的空白标签。
    #   异常路径（连接失败、导航超时）可能留下没人认领的 about:blank，
    #   累积会拖慢浏览器。只清空白标签，绝不碰有内容的。
    if tabs_per_browser > 1:
        freed = 0
        for p in ports:
            freed += prune_blank_tabs(p, keep=1)
        if freed:
            log.info(f"清理遗留空白标签 {freed} 个")
    return results


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
def parse_args(argv: list[str]) -> dict:
    o = {"limit": 12, "delay": (25.0, 55.0), "min_year": 0, "n": 3,
         "ports": [], "sites": [], "skip_sites": [], "dois": [],
         "manual_wait": 0.0, "render": True, "call_timeout": 30.0,
         "pdf_timeout": 120.0, "base_port": 9222, "site_fail_limit": 2,
         "grace": 25.0,
         # 单篇最多尝试几轮；0 = 不设上限（回到旧行为）。
         # 达到上限的论文不再自动重试，避免一篇取不到的论文反复冲击站点。
         "max_attempts": 3,
         # ── v8 新增 ──
         "tabs_per_browser": 3,      # 每浏览器几个标签页（并发 = 浏览器 × 标签页）
         "multi_tab": True,          # 启用多标签页模式（独占标签、用完即关）
         "fulltext_cap": 400000,     # 顺带取回正文的字符上限（0=不取）
         }
    for a in argv:
        if a.startswith("--limit="): o["limit"] = int(a.split("=", 1)[1])
        elif a.startswith("--n="): o["n"] = int(a.split("=", 1)[1])
        elif a.startswith("--tabs="): o["tabs_per_browser"] = max(1, int(a.split("=", 1)[1]))
        elif a.startswith("--fulltext-cap="): o["fulltext_cap"] = int(a.split("=", 1)[1])
        elif a == "--no-fulltext": o["fulltext_cap"] = 0
        elif a == "--no-multi-tab":
            # 回退到旧行为（一浏览器一标签页），用于排查多标签页相关问题
            o["multi_tab"] = False
            o["tabs_per_browser"] = 1
        elif a.startswith("--base-port="): o["base_port"] = int(a.split("=", 1)[1])
        elif a.startswith("--ports="):
            o["ports"] = [int(x) for x in a.split("=", 1)[1].split(",") if x.strip()]
        elif a.startswith("--delay="):
            v = a.split("=", 1)[1]; lo, _, hi = v.partition("-")
            o["delay"] = (float(lo), float(hi) if hi else float(lo) * 2)
        elif a.startswith("--min-year="): o["min_year"] = int(a.split("=", 1)[1])
        elif a.startswith("--doi="): o["dois"] = [x for x in a.split("=", 1)[1].split(",") if x]
        elif a.startswith("--sites="): o["sites"] = [x for x in a.split("=", 1)[1].split(",") if x]
        elif a.startswith("--skip-sites="): o["skip_sites"] = [x for x in a.split("=", 1)[1].split(",") if x]
        elif a.startswith("--manual-wait="): o["manual_wait"] = float(a.split("=", 1)[1]) * 60
        elif a.startswith("--grace="): o["grace"] = float(a.split("=", 1)[1])
        elif a.startswith("--site-fail-limit="): o["site_fail_limit"] = int(a.split("=", 1)[1])
        elif a.startswith("--max-attempts="): o["max_attempts"] = max(0, int(a.split("=", 1)[1]))
        elif a.startswith("--call-timeout="): o["call_timeout"] = float(a.split("=", 1)[1])
        elif a.startswith("--pdf-timeout="): o["pdf_timeout"] = float(a.split("=", 1)[1])
        elif a == "--no-render": o["render"] = False
    return o


def main():
    argv = sys.argv[1:]
    if "--status" in argv:
        got = [d for d, v in STATE_DATA.items() if v.get("pdf_ok")]
        print(f"已尝试 {len(STATE_DATA)} 篇 | 成功 {len(got)} 篇")
        for d in sorted(got):
            v = STATE_DATA[d]
            print(f"  [OK] {v.get('pdf_file')} ({v.get('pdf_size',0)//1024} KB) [{v.get('pdf_src')}]")
        return

    opts = parse_args(argv)
    GUARD.limit = opts["site_fail_limit"]

    CR = json.loads((CDPDIR / "crossref_all.json").read_text(encoding="utf-8"))

    def has_pdf(doi, year):
        return store.existing_pdf(doi, year, WD) is not None

    if opts["dois"]:
        todo = [(d, CR.get(d, {})) for d in opts["dois"]]
    else:
        todo = [(d, m) for d, m in CR.items()
                if not d.startswith("10.2210/")                      # PDB 结构存缴，非论文
                and (m.get("year") or 0) >= opts["min_year"]
                and (not (STATE_DATA.get(d) or {}).get("pdf_ok"))
                and not has_pdf(d, m.get("year"))]
        if opts["sites"]:
            todo = [(d, m) for d, m in todo
                    if any(s in (m.get("journal") or "").lower() or s in d for s in opts["sites"])]
        if opts["skip_sites"]:
            skip = set(opts["skip_sites"])
            todo = [(d, m) for d, m in todo if detect.site_of_doi(d) not in skip]
        # 国内 IP 网络层不可达的站点：直接排除，不做无谓重试
        n_cn = len(todo)
        todo = [(d, m) for d, m in todo if not detect.is_cn_inaccessible(d)]
        if len(todo) < n_cn:
            LOG.info(f"已跳过 {n_cn - len(todo)} 篇中国大陆 IP 不可达的站点"
                     f"（{', '.join(detect.CN_INACCESSIBLE)}）")
        # 同名消歧：不下载已明确判定为**他人**的论文。
        #   与 build_list.py 共用 scope.py 同一判据源（黑名单语义，只删 dropped）。
        #   零成本省下一批无谓的站点访问 —— 既省时间，也少给出版社添流量。
        _exc = scope.excluded_dois(CDPDIR, total=len(CR))
        if _exc:
            n_ex = len(todo)
            todo = [(d, m) for d, m in todo if (d or "").lower() not in _exc]
            if len(todo) < n_ex:
                LOG.info(f"已跳过 {n_ex - len(todo)} 篇同名他人论文"
                         f"（判定见 .work_cdp/disambiguated.json）")
        # 尝试上限：达到 max_attempts 的论文不再自动重试。
        #   --doi= 显式指定时不套用本过滤（上面分支已直接构造 todo）。
        if opts["max_attempts"] > 0:
            def _att(d: str) -> int:
                return int((STATE_DATA.get(d) or {}).get("attempts") or 0)

            n_at = len(todo)
            todo = [(d, m) for d, m in todo if _att(d) < opts["max_attempts"]]
            if len(todo) < n_at:
                LOG.info(f"已跳过 {n_at - len(todo)} 篇达到尝试上限"
                         f"（{opts['max_attempts']} 次）的论文；需重试可加大"
                         f" --max-attempts= 或用 --doi= 显式指定")
        todo.sort(key=lambda t: -(t[1].get("year") or 0))
        todo = todo[:opts["limit"]]

    if not todo:
        LOG.info("没有待处理的论文")
        # 即使本轮没有新任务，run_all 也可能已预先启动实例；阶段结束仍应回收。
        _cleanup_owned(opts["base_port"], 8, LOG)
        LOG.close()
        return

    # 端口池
    candidates = opts["ports"] or cdp_pool.alive_ports(WD, opts["base_port"], span=8)
    ports = [port for port in candidates if _dom_ready(port, LOG)]
    if not ports:
        LOG.info("未发现存活实例，正在启动 …")
        ports = cdp_pool.ensure(WD, n=opts["n"], base_port=opts["base_port"], log=LOG)
    if not ports:
        LOG.error("无可用 CDP 实例，退出")
        raise SystemExit(1)
    ports = ports[:opts["n"]]

    LOG.info(f"待处理 {len(todo)} 篇 | 实例 {ports} | 同站点间隔 "
             f"{opts['delay'][0]:.0f}-{opts['delay'][1]:.0f}s | 人工等待 "
             f"{opts['manual_wait']/60:.0f} 分钟")
    journal("E", "download.start", count=len(todo), ports=ports,
            delay=f"{opts['delay'][0]:.0f}-{opts['delay'][1]:.0f}",
            manual_wait_min=round(opts["manual_wait"] / 60, 1),
            site_fail_limit=opts["site_fail_limit"], grace=opts["grace"],
            dois=[d for d, _m in todo])

    t0 = time.time()
    res: list[dict] = []
    try:
        try:
            res = run_pool(todo, ports, opts, LOG)
        except KeyboardInterrupt:
            LOG.warn("用户中断")
        except Exception as e:
            LOG.error(f"下载池异常: {type(e).__name__}: {str(e)[:120]}")

        # 自动阶段结束：记录总数/成功/未拿到，再询问是否进入人工阶段。
        todo_map = {d: m for d, m in todo}
        failed, seen = [], set()
        for r in res:
            d = r.get("doi")
            if d and not r.get("pdf_ok") and d not in seen:
                seen.add(d)
                failed.append((d, todo_map.get(d, {})))
        ok = sum(1 for r in res if r.get("pdf_ok"))
        LOG.info(f"自动阶段：共 {len(todo)} 篇 | 成功 {ok} | 未拿到 {len(failed)}")
        if failed:
            preview = "\n".join(d for d, _ in failed[:12])
            if len(failed) > 12:
                preview += f"\n…（共 {len(failed)} 篇）"
            msg = (f"本轮共 {len(todo)} 篇：自动成功 {ok} 篇，未拿到 {len(failed)} 篇。\n\n"
                   "是否继续对这些文章进行 CDP 人工验证？\n"
                   "选“是”后逐篇打开页面并等待你完成验证（最长 5 分钟/篇）。\n\n"
                   + preview)
            if os.environ.get("FLOW_NO_MANUAL_ASK") == "1":
                LOG.info("FLOW_NO_MANUAL_ASK=1，跳过人工阶段询问")
            elif ask_yes_no("CDP 下载结果", msg, timeout_s=600):
                LOG.info(f"进入人工验证阶段：{len(failed)} 篇")
                manual_res = run_manual_phase(failed, ports, opts, LOG)
                res.extend(manual_res)
            else:
                LOG.info("用户选择跳过人工验证阶段")
    finally:
        # 批量下载（含人工阶段）结束后回收全部本轮 CDP 实例，避免长期占用资源。
        span = max(8, max(ports) - opts["base_port"] + 1) if ports else 8
        _cleanup_owned(opts["base_port"], span, LOG)
    ok = sum(1 for r in res if r.get("pdf_ok"))
    LOG.info(f"本轮完成：成功 {ok} / {len(res)} | 用时 {(time.time()-t0)/60:.1f} 分钟")
    LOG.info(f"站点熔断情况：{GUARD.summary()}")
    LOG.info(f"累计全文 {sum(1 for v in STATE_DATA.values() if v.get('pdf_ok'))} 篇")
    journal("E", "download.done", ok=ok, total=len(res),
            elapsed_min=round((time.time() - t0) / 60, 1),
            cumulative=sum(1 for v in STATE_DATA.values() if v.get("pdf_ok")),
            circuit=GUARD.summary())
    LOG.close()


if __name__ == "__main__":
    main()
