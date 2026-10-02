# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 批量下载论文全文（IP 级机构订阅授权 + 多 CDP 实例并行）
#
# ★ 本次修复（针对旧版实测暴露的问题）：
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
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

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


def validate_pdf(path: pathlib.Path, min_pages: int = 2):
    """校验是否为真实论文 PDF（见 store.validate_pdf 的规则说明）。"""
    return store.validate_pdf(path, min_pages=min_pages)


def _write_pdf(raw: bytes, out_path: pathlib.Path):
    """写盘 + 校验。★ 校验不通过只删除**本次刚写入**的文件，
    绝不触碰同名旧文件（历史上曾因去重漏判而误删已下好的 PDF）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    good, why = validate_pdf(out_path)
    if not good:
        out_path.unlink(missing_ok=True)
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


def activate_edge_window() -> None:
    """把 Edge 窗口置前（Page.bringToFront 只能激活标签页，不抬升窗口）。"""
    ps = ("$ws = New-Object -ComObject wscript.shell;"
          "$p = Get-Process msedge -ErrorAction SilentlyContinue |"
          " Where-Object { $_.MainWindowTitle -ne '' } | Select-Object -First 1;"
          "if ($p) { $ws.AppActivate($p.Id) | Out-Null }")
    try:
        import subprocess
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def wait_for_manual(c: CDP, doi: str, max_wait: float, log) -> bool:
    """检测到**真实**挑战页时，弹窗请人过一次验证，通过则返回 True。

    ★ 不会卡死的三重保证：
      1) 循环总时长硬上限 = max_wait；
      2) 每次探针调用带超时（detect.judge timeout=15），CDP 失联只记一次警告；
      3) 弹窗自身带 TTL 自动关闭（notify_user timeout_s），不会残留。
    """
    notify_user("需要人工验证（CAPTCHA）",
                f"检测到人机验证，自动化已暂停。\n\n论文 DOI: {doi}\n\n"
                f"请在已置前的 Edge 窗口中完成验证（勾选 / 拼图）。\n"
                f"完成后无需操作，流程会自动继续。\n（最长等待 {max_wait/60:.0f} 分钟）",
                timeout_s=max_wait + 30)
    log.warn(f"检测到人机验证，请人工处理（最长 {max_wait/60:.0f} 分钟）")
    c.bring_to_front()
    activate_edge_window()
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
            # ★ 验证刚通过时正文往往只渲染了一部分（实测 4005 字符），
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
            log.info(f"  等待中 [{time.time()-t0:.0f}s] 文本 {last}；"
                     f"若弹窗已关闭，请点击 Edge 窗口完成验证")
            c.bring_to_front()
    log.warn("等待人工验证超时，跳过该篇")
    return False


# ══════════════════════════════════════════════════════════════
# 单篇处理
# ══════════════════════════════════════════════════════════════
def settle_challenge(c: CDP, log, grace: float = 25.0, poll: float = 3.0) -> dict:
    """挑战页**宽限期**：Cloudflare 的 JS 挑战多数会在几秒内自动放行。

    ★ 实测（2026-09-11）：academic.oup.com 首次导航返回标题「请稍候…」
      正文仅 118 字符；同一页面 6 秒后再探已完整加载（55265 字符）。
      若一见 challenge 就放弃，会把**本可自动通过**的站点误判为需人工验证
      —— 这是「误触发 CAPTCHA」的第二种形态。

    因此：命中 challenge 后先原地观察 grace 秒；期间一旦转为可读页面即继续。
    """
    t0 = time.time()
    r = detect.judge(c, timeout=20)
    while r["state"] == CHALLENGE and time.time() - t0 < grace:
        time.sleep(poll)
        r = detect.judge(c, timeout=20)
        log.info(f"    挑战宽限 {time.time()-t0:.0f}/{grace:.0f}s → {r['state']} "
                 f"(文本 {r.get('textlen')})")
    return r


def load_landing(c: CDP, landing: str, doi: str, log, max_wait: float,
                 retries: int = 2, grace: float = 25.0, site: str = "",
                 fulltext_cap: int = 0) -> dict:
    """导航落地页 → 精确判定状态 → 必要时重试/等人。返回判定结果 dict。

    fulltext_cap > 0 时，判定结果里会附带 `fulltext` —— 页面正文前 N 字符。
    ★ 这是"边下 PDF 边解读"的关键：正文此刻已在浏览器内存里，
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

        # ★ 加载后再补一次同意书清理：部分站点弹窗在 load 之后才出现，
        #   注入脚本的定时巡查可能刚好错过；这一步确保弹窗不遮挡正文。
        try:
            c.dismiss_consent(timeout=6)
        except Exception:
            pass

        r = detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
        st = r["state"]
        log.info(f"  状态={st} ({r['reason']}) 文本={r.get('textlen')} "
                 f"订阅={r.get('subscribed')} 付费墙={r.get('paywalled')}")

        # ★ 挑战页先给宽限期，多数 Cloudflare JS 挑战会自动放行
        if st == CHALLENGE:
            r = settle_challenge(c, log, grace=grace)
            st = r["state"]
            if st != CHALLENGE:
                log.info(f"  挑战已自动通过 → {st}（文本 {r.get('textlen')}）")

        if st == BLOCKED:
            raise SiteBlocked(
                f"检测到站点封禁文案：{r.get('markers')} @ {site_of(r.get('url',''))}\n"
                "    ⛔ 已停止本轮全部后续请求（含其他站点）。\n"
                "    ⛔ 请人工确认后改用机构 VPN / 图书馆代查等正规途径。")
        if st in (OK, PAYWALL):
            return r
        if st == CHALLENGE:
            # 仅统计宽限后仍需人工介入的真实 CAPTCHA，避免把瞬态 JS challenge 算入。
            if site:
                GUARD.captcha_hit(site, r.get("reason", "challenge"), doi=doi)
            if max_wait > 0 and wait_for_manual(c, doi, max_wait, log):
                # ★ 人工通过后要重新判定，并把 fulltext_cap 一并带上 ——
                #   漏传会让这一篇丢失正文（正文只有这一次顺带取的机会）
                return detect.judge(c, timeout=25, fulltext_cap=fulltext_cap)
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
        # ★ 必须在量正文长度**之前**关弹窗：同意书横幅的文字会一起计入
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

    ★ 为什么单独放 `.work_cdp/` 而不是交付目录：
      这是**解读的中间素材**，不是交付物。放进 EXPORTS 会污染用户的论文目录
      （那里只应有 PDF 与清单），也会让"全文清单"的统计口径变乱。
    ★ 为什么值得存：阶段 H 解读原本要 `to_markdown` 从 PDF 再提一次文本；
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

    # ★ v8：多标签页模式下每篇独占一个标签页（见 run_pool），
    #   标签页用完即关，避免导航到 PDF 后目标被顶替导致后续连接失效
    tabbed = bool(opts.get("multi_tab"))
    try:
        if tabbed:
            c = open_tab("about:blank", port, timeout=opts["call_timeout"], logger=log)
        else:
            c = connect(port, timeout=opts["call_timeout"], logger=log)
    except Exception as e:
        rec.update({"pdf_ok": False, "pdf_error": f"cdp-connect:{type(e).__name__}:{e}"})
        return rec

    try:
        r = load_landing(c, landing, doi, log, opts["manual_wait"],
                         grace=opts["grace"], site=detect.site_of_doi(doi),
                         fulltext_cap=opts.get("fulltext_cap", 0))
        rec.update({
            "url": r.get("url") or "", "title": r.get("citeTitle") or r.get("title") or rec["title"],
            "journal": r.get("journal") or rec["journal"],
            "state": r["state"], "textlen": r.get("textlen"),
            "subscribed": r.get("subscribed"), "paywalled": r.get("paywalled"),
            "abstract": (r.get("abstract") or "")[:2600],
        })

        # ★ 正文落盘：**先于 PDF 尝试**，这样即使 PDF 最终失败，
        #   解读素材也已经拿到（付费墙/降级场景尤其重要）
        ft = r.get("fulltext") or ""
        if opts.get("fulltext_cap", 0) > 0 and ft:
            fp = save_fulltext(doi, year, ft, log)
            if fp:
                rec["fulltext_file"] = fp
                rec["fulltext_chars"] = len(ft)
                log.info(f"  📄 正文已存 {len(ft)} 字符 → {pathlib.Path(fp).name}")

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
        # ★ 多标签页模式：先关标签页再关 ws（反序会留下孤儿标签，累积拖慢浏览器）。
        #   导航到 PDF 后原目标已被 PDF 查看器顶替，此时关标签可能失败 —— 正常。
        try:
            if tabbed:
                c.close_tab_self()
            else:
                c.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# 并行调度
# ══════════════════════════════════════════════════════════════
def run_pool(items: list[tuple[str, dict]], ports: list[int], opts: dict, log) -> list[dict]:
    """多浏览器 × 多标签页并行；同站点串行 + 站点间隔。

    ★ v8 改造（原为「多浏览器 × 单标签页」）：
      旧设计的并发度 = 浏览器进程数。想提速只能多开 Edge，而每个进程
      约占 200–400 MB，开 6 个就吃掉 2 GB，启动与崩溃恢复也慢。
      实测瓶颈在网络等待（导航 + 出版社响应），CPU/内存远没到顶，
      所以改为「每浏览器开 T 个标签页」，并发度 = 浏览器数 × 标签页数，
      增开成本远低于新开浏览器（标签页共享进程池与网络栈）。

    ⛔ 两条不变量（改造不得破坏）：
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

        ⛔ v8 性能修复：旧实现对**每个** pending 项调用 `GUARD.priority()`，
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
                log.info(f"  ⏳ 同站点节流 {wait:.0f}s（{site}）")
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
                # ★ 站点锁包住整个"节流 + 处理 + 失败记账"，保证同站点严格串行。
                #
                # ⛔ v8 修的一个重要缺陷：**失败计数必须在锁内更新**。
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
                    if not is_alive(port):
                        log.warn(f"端口 {port} 失联，尝试重启 …")
                        if not cdp_pool.launch_one(WD, port, level=0, log=log):
                            with rlock:
                                results.append({"doi": doi, "pdf_ok": False,
                                                "pdf_error": f"cdp-down:{port}"})
                            continue
                    log.info(f"[{tag}] {doi} ({meta.get('year')})")
                    # ★ 多标签页模式：process_one 内部 open_tab 新开独占标签页
                    rec = process_one(port, doi, meta, opts, log)
                    # ★ 锁内记账：把受阻状态立刻反映到 hits，
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
                results.append(rec)
                STATE_DATA[doi] = rec
                save_state()
                if rec.get("pdf_ok"):
                    # 登记 DOI → 文件名（题名命名后，去重判断仍可靠）
                    try:
                        store.register_pdf(doi, EXPORTS / rec["pdf_file"], WD)
                    except Exception:
                        pass
                    log.ok(f"  ✅ {rec.get('pdf_size',0)//1024} KB | {rec.get('pdf_file')} "
                           f"[{rec.get('pdf_src')}] 累计 {sum(1 for v in STATE_DATA.values() if v.get('pdf_ok'))}")
                    journal("E", "download.pdf", doi=doi, year=rec.get("year"),
                            file=rec.get("pdf_file"), size_kb=rec.get("pdf_size", 0) // 1024,
                            src=rec.get("pdf_src"), pages=rec.get("pdf_pages"))
                else:
                    log.warn(f"  ⚠️ 失败: {rec.get('pdf_error')}")
                    journal("E", "download.fail", doi=doi, year=rec.get("year"),
                            state=rec.get("state"), reason=rec.get("pdf_error"))
                # ★ 注意：受阻计数已移至站点锁内（见上方 with GUARD.lock(site) 块）。
                #   这里**不要**再记一次，否则会重复计数、让熔断提前触发。

    # ★ 并发通道 = 浏览器端口 × 每浏览器标签页数
    slots = [(p, s) for p in ports for s in range(tabs_per_browser)]
    log.info(f"并发通道 {len(slots)} 个 = {len(ports)} 浏览器 × {tabs_per_browser} 标签页"
             + ("（多标签页模式）" if tabs_per_browser > 1 else ""))
    threads = [threading.Thread(target=worker, args=(p, s), daemon=True)
               for p, s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ★ v8：收尾清理多标签页模式留下的空白标签。
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
            print(f"  ✓ {v.get('pdf_file')} ({v.get('pdf_size',0)//1024} KB) [{v.get('pdf_src')}]")
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
        # ★ 同名消歧：不下载已明确判定为**他人**的论文。
        #   与 build_list.py 共用 scope.py 同一判据源（黑名单语义，只删 dropped）。
        #   零成本省下一批无谓的站点访问 —— 既省时间，也少给出版社添流量。
        _exc = scope.excluded_dois(CDPDIR, total=len(CR))
        if _exc:
            n_ex = len(todo)
            todo = [(d, m) for d, m in todo if (d or "").lower() not in _exc]
            if len(todo) < n_ex:
                LOG.info(f"已跳过 {n_ex - len(todo)} 篇同名他人论文"
                         f"（判定见 .work_cdp/disambiguated.json）")
        todo.sort(key=lambda t: -(t[1].get("year") or 0))
        todo = todo[:opts["limit"]]

    if not todo:
        LOG.info("没有待处理的论文")
        # 即使本轮没有新任务，run_all 也可能已预先启动实例；阶段结束仍应回收。
        cdp_pool.down(WD, base_port=opts["base_port"], span=8, log=LOG)
        LOG.close()
        return

    # 端口池
    ports = opts["ports"] or cdp_pool.alive_ports(WD, opts["base_port"], span=8)
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
    finally:
        # 批量下载结束后回收全部本轮 CDP 实例，避免 detached Edge 长期占用资源。
        span = max(8, max(ports) - opts["base_port"] + 1) if ports else 8
        cdp_pool.down(WD, base_port=opts["base_port"], span=span, log=LOG)
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
