# -*- coding: utf-8 -*-
"""资产 CDN 人机验证（Elsevier `pdf.sciencedirectassets.com/craft/capi/cfts/init` 等）。

现象：落地页已经正常，取 PDF 时被资产 CDN 拦下，页面显示
`Request Verification: In Progress` + Cloudflare 复选框（标题常为
`Security verification`）。此时 page-fetch 只能拿到 601B 的 HTML。

处理：**导航到 PDF URL** → 识别该挑战页 → 宿主机点击复选框 → 用 Network 域
捕获页面随后自己请求的真实 PDF → 失败才回落到页面渲染。

依赖：`save_pdf_via_browser.py`（四策略）、`turnstile_click.py`（截图+点击）。

用法（单篇）：
    c = connect(port); hwnd = 找到浏览器窗口
    res = save_with_asset(c, hwnd, landing, pdf_url, out_path)
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import save_pdf_via_browser as SP  # noqa: E402
import turnstile_click as T  # noqa: E402

ASSET_MARKERS = ("request verification", "verification in progress",
                 "请求验证", "验证您是真人", "verify you are human")


def on_asset_challenge(c) -> bool:
    try:
        url = (c.get_url() or "").lower()
        txt = (c.get_text() or "")[:5000].lower()
    except Exception:
        return False
    return ("cfts" in url or "pdf.sciencedirectassets.com" in url
            or any(m in txt for m in ASSET_MARKERS))


def capture_after_click(c, hwnd, out, timeout=70) -> dict:
    """Network 先开，点复选框，等页面自己触发 PDF 请求并抓 body。"""
    try:
        c.call("Network.enable", timeout=10)
        c.call("Page.enable", timeout=10)
    except Exception as e:
        return {"ok": False, "error": f"enable:{str(e)[:60]}"}
    clicked = T.click_turnstile(hwnd, timeout=45) if hwnd else False
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline:
        try:
            c.ws.settimeout(max(min(5.0, deadline - time.time()), 0.5))
            raw = c.ws.recv()
        except Exception:
            continue
        if not raw:
            break
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        m, p = msg.get("method"), (msg.get("params") or {})
        if m == "Network.responseReceived":
            r = p.get("response") or {}
            u = (r.get("url") or "").lower()
            if "pdf" in (r.get("mimeType") or "").lower() or ".pdf" in u or "/pdf/" in u:
                target = p.get("requestId")
        elif m == "Network.loadingFinished" and target and p.get("requestId") == target:
            try:
                body = c.call("Network.getResponseBody", {"requestId": target}, timeout=60)
            except Exception as e:
                return {"ok": False, "error": f"getResponseBody:{str(e)[:60]}"}
            data = body.get("body", "")
            rawb = base64.b64decode(data) if body.get("base64Encoded") else data.encode("latin-1")
            if rawb[:4] == b"%PDF":
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(rawb)
                return {"ok": True, "size": len(rawb), "path": str(out),
                        "method": "asset-click+network", "clicked": clicked}
            return {"ok": False, "error": f"not-a-pdf({len(rawb)}B)"}
    return {"ok": False, "error": f"clicked={clicked}, timeout-waiting-pdf"}


def save_with_asset(c, hwnd, landing, pdf_url, out, timeout=20, render=True) -> dict:
    """四策略 → 资产 CDN 挑战（导航+点击+Network 捕获）→ 渲染兜底。"""
    res = SP.save_pdf(c, landing, pdf_url, out, timeout=timeout, render=False)
    if res.get("ok"):
        return res

    # 主动导航到 PDF URL：浏览器才会显示 cfts 验证页（fetch 只会拿到 601B HTML）
    navigated = False
    try:
        c.navigate(pdf_url, settle=1.5, load_timeout=35)
        navigated = True
    except Exception as e:
        res["asset_nav_error"] = str(e)[:80]
    if navigated:
        time.sleep(2.0)
        if on_asset_challenge(c) or T.is_challenge(c.get_title()):
            r2 = capture_after_click(c, hwnd, out)
            if r2.get("ok"):
                return r2
            res["asset_click"] = r2.get("error")
        else:
            # 已经直接是 PDF 查看器：在页内 fetch 当前地址
            try:
                r3 = SP.capture_via_redirect_fetch(c, c.get_url(), out, timeout=60)
                if r3.get("ok"):
                    r3["method"] = "asset-direct"
                    return r3
            except Exception as e:
                res["asset_direct_error"] = str(e)[:80]

    if render:
        r4 = SP.capture_via_render(c, landing, out)
        if r4.get("ok"):
            r4["method"] = "render(asset-blocked)"
            return r4
    return res
