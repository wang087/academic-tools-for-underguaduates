# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 单篇 PDF 获取（浏览器会话内）—— download_all_fulltexts.py 的底层工具
#
# 四种方法按可靠性依次尝试：
#   ① Network 域捕获：Network.enable + 导航到 PDF + Network.getResponseBody
#      —— 绕过 CORS / CSP / 前端拦截，最可靠
#   ② 页面上下文 fetch（同源直链；**必须**在落地页执行，在 PDF 查看器页
#      执行会因 CORS 失败）
#   ③ 重定向后 fetch（ScienceDirect 签名直链场景）：导航到 PDF 链接 →
#      浏览器跟随重定向 → 在**该页上下文** fetch location.href
#   ④ 渲染兜底：PDF 端点被拦但有全文权限时，Page.printToPDF 打印正文页
#
# 本次修复：
#   · 适配新版 cdp.py（端口参数化、硬超时、不再直接摸私有 _id/ws）
#   · 每一步都有超时上限；Network 捕获不再无限 `ws.recv()`
#   · 落盘统一走 store.py 校验（1 页大文件放行，1 页空壳拒绝）
#   · 接入 detect.judge()，挑战页/封禁页不会被误当成「无 PDF」
#
# 用法:
#   python save_pdf_via_browser.py "<landing_url>" "<pdf_url>" "<out_path>" [--port=9222]
# 可移植配置：PAPER_WORKDIR、CDP_PORT
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import base64
import io
import json
import pathlib
import sys
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

import detect                                                    # noqa: E402
import store                                                     # noqa: E402
from cdp import CDPError, connect, is_alive, resolve_port        # noqa: E402
from logutil import get as get_log, workdir as _wd               # noqa: E402

WD = _wd()
LOG = get_log("save_pdf", wd=WD)

PAGE_FETCH_JS = """
(async (pdfUrl) => {
  try {
    const r = await fetch(pdfUrl, {credentials:'include'});
    if (!r.ok) return JSON.stringify({ok:false, status:r.status});
    const b = await r.arrayBuffer(); const by = new Uint8Array(b);
    let s = ''; const CH = 0x8000;
    for (let i = 0; i < by.length; i += CH) s += String.fromCharCode.apply(null, by.subarray(i, i+CH));
    return JSON.stringify({ok:true, status:r.status, size:by.length, b64:btoa(s)});
  } catch(e) { return JSON.stringify({ok:false, error:String(e).slice(0,140)}); }
})"""


def _write(out_path, raw: bytes):
    """原子写盘 + 校验：校验通过才落到最终路径（见 store.save_bytes 的说明）。"""
    got, why = store.save_bytes(raw, out_path)
    if got is None:
        return {"ok": False, "error": f"invalid:{why}"}
    return {"ok": True, "size": len(raw), "path": str(got), "pages": why}


def capture_via_network(c, pdf_url, out_path, timeout=120):
    """方法①：Network 域捕获 PDF 响应体。

    只用公开 API 读完事件流，并设绝对截止时间 —— 不再无条件 `ws.recv()`。
    """
    try:
        c.call("Network.enable", timeout=10)
        c.call("Page.enable", timeout=10)
    except CDPError as e:
        return {"ok": False, "error": f"enable:{str(e)[:60]}"}

    c.call("Page.navigate", {"url": pdf_url}, timeout=15)
    deadline = time.time() + timeout
    target_req = None
    while time.time() < deadline:
        try:
            c.ws.settimeout(max(min(5.0, deadline - time.time()), 0.5))
            raw = c.ws.recv()
        except Exception:
            continue                                  # 超时切片，继续等（受 deadline 约束）
        if not raw:
            return {"ok": False, "error": "socket-closed"}
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        m, p = msg.get("method"), (msg.get("params") or {})
        if m == "Network.responseReceived":
            r = p.get("response") or {}
            u = r.get("url", "")
            mime = (r.get("mimeType") or "").lower()
            if "pdf" in mime or u.split("?")[0].rstrip("/").endswith(".pdf") or "/pdf/" in u:
                target_req = p.get("requestId")
        elif m == "Network.loadingFinished" and target_req and p.get("requestId") == target_req:
            try:
                body = c.call("Network.getResponseBody", {"requestId": target_req}, timeout=60)
            except CDPError as e:
                return {"ok": False, "error": f"getResponseBody:{str(e)[:60]}"}
            data = body.get("body", "")
            rawb = base64.b64decode(data) if body.get("base64Encoded") else data.encode("latin-1")
            if rawb[:4] != b"%PDF":
                return {"ok": False, "error": f"not-a-pdf({len(rawb)}B)"}
            return _write(out_path, rawb)
    return {"ok": False, "error": "timeout-waiting-pdf"}


def capture_via_page_fetch(c, landing, pdf_url, out_path, timeout=120):
    """方法②：落地页上下文 fetch（同源）。"""
    try:
        c.navigate(landing, settle=2.0, load_timeout=25)
        r = c.eval(f"({PAGE_FETCH_JS})({json.dumps(pdf_url)})", timeout=timeout)
    except CDPError as e:
        return {"ok": False, "error": f"fetch:{str(e)[:70]}"}
    d = json.loads(r) if isinstance(r, str) else (r or {})
    if not d.get("ok") or not d.get("b64"):
        return {"ok": False, "error": d.get("error") or f"http-{d.get('status')}"}
    raw = base64.b64decode(d["b64"])
    if raw[:4] != b"%PDF":
        return {"ok": False, "error": "not-a-pdf"}
    return _write(out_path, raw)


def capture_via_redirect_fetch(c, pdf_url, out_path, timeout=120):
    """方法③：跟随重定向后在当前页上下文 fetch 自身（ScienceDirect 签名直链）。"""
    try:
        c.call("Page.navigate", {"url": pdf_url}, timeout=20)
    except CDPError as e:
        return {"ok": False, "error": f"nav:{str(e)[:60]}"}
    for _ in range(12):
        time.sleep(1)
        try:
            cur = c.get_url(timeout=6)
        except CDPError:
            return {"ok": False, "error": "target-destroyed"}
        if cur and cur != pdf_url:
            break
    try:
        r = c.eval(f"({PAGE_FETCH_JS})(location.href)", timeout=timeout)
    except CDPError as e:
        return {"ok": False, "error": f"fetch:{str(e)[:70]}"}
    d = json.loads(r) if isinstance(r, str) else (r or {})
    if not d.get("ok") or not d.get("b64"):
        return {"ok": False, "error": d.get("error") or f"http-{d.get('status')}"}
    raw = base64.b64decode(d["b64"])
    if raw[:4] != b"%PDF":
        return {"ok": False, "error": "not-a-pdf"}
    return _write(out_path, raw)


def capture_via_render(c, landing, out_path, timeout=150):
    """方法④：渲染兜底（有全文权限但 PDF 端点被拦）。"""
    try:
        c.navigate(landing, settle=2.0, load_timeout=25)
        for _ in range(7):
            if len(c.get_text(timeout=15) or "") > 15000:
                break
            time.sleep(3)
        else:
            return {"ok": False, "error": "page-not-ready-for-render"}
        r = c.call("Page.printToPDF", {
            "printBackground": False, "preferCSSPageSize": True,
            "marginTop": 0.6, "marginBottom": 0.6, "marginLeft": 0.5, "marginRight": 0.5,
        }, timeout=timeout)
    except CDPError as e:
        return {"ok": False, "error": f"print:{str(e)[:70]}"}
    data = r.get("data")
    if not data:
        return {"ok": False, "error": "empty-print"}
    raw = base64.b64decode(data)
    if raw[:4] != b"%PDF":
        return {"ok": False, "error": "print-not-pdf"}
    return _write(out_path, raw)


def save_pdf(c, landing, pdf_url, out_path, timeout=120, render=False):
    """依次尝试各方法，首个成功即返回。"""
    methods = [("network", lambda: capture_via_network(c, pdf_url, out_path, timeout)),
               ("page-fetch", lambda: capture_via_page_fetch(c, landing, pdf_url, out_path, timeout)),
               ("redirect-fetch", lambda: capture_via_redirect_fetch(c, pdf_url, out_path, timeout))]
    if render:
        methods.append(("render", lambda: capture_via_render(c, landing, out_path, timeout)))
    last = {}
    for name, fn in methods:
        try:
            res = fn()
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}:{str(e)[:80]}"}
        if res.get("ok"):
            res["method"] = name
            LOG.ok(f"  取回成功 [{name}] {res['size']//1024} KB → {res['path']}")
            return res
        last = res
        LOG.info(f"  [{name}] 失败: {res.get('error')}")
    return last


def main():
    argv = sys.argv[1:]
    port = resolve_port(argv)
    pos = [a for a in argv if not a.startswith("-")]
    render = "--render" in argv
    if len(pos) < 3:
        print('usage: save_pdf_via_browser.py "<landing>" "<pdf_url>" "<out_path>" '
              '[--port=9222] [--render]')
        return
    landing, pdf_url, out_path = pos[0], pos[1], pos[2]
    if not is_alive(port):
        LOG.error(f"端口 {port} 无 CDP 实例")
        raise SystemExit(1)
    # 先看一眼落地页状态，避免把「挑战页」当成「无 PDF」
    c = connect(port, logger=LOG, timeout=30)
    try:
        c.navigate(landing, settle=2.0, load_timeout=25)
        j = detect.judge(c, timeout=25)
        LOG.info(f"落地页判定: {j['state']} ({j['reason']}) 文本={j.get('textlen')}")
        if j["state"] == detect.BLOCKED:
            LOG.error("[STOP] 封禁页 —— 停止。")
            raise SystemExit(2)
        if j["state"] == detect.CHALLENGE:
            LOG.error("人机验证页 —— 需人工过一次 Cookie 才可继续。")
            raise SystemExit(3)
        res = save_pdf(c, landing, pdf_url, out_path, render=render)
        print(json.dumps(res, ensure_ascii=False))
    finally:
        c.close()
        LOG.close()


if __name__ == "__main__":
    main()
