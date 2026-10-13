# -*- coding: utf-8 -*-
"""测试当前 Edge 是否拥有付费全文权限（IP 级机构授权检测）。
单次、低频，只访问 1 篇论文。
"""
import sys, io, json, time, re, os, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import os
_HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(_HERE))
# 本文件属于**可选模块** `流程1/optional-cdp-fulltext/`；核心脚本在 ../../scripts/
_CORE_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_CORE_SCRIPTS))
from cdp import CDP, first_page_tab

# 待测 DOI 不硬编码：必须由命令行或环境变量给出
TEST_DOI = (sys.argv[1].strip() if len(sys.argv) > 1 else os.environ.get("PAPER_TEST_DOI", "").strip())
if not TEST_DOI:
    sys.stderr.write(
        "未指定待测 DOI。\n"
        "  用法：uv run --with websocket-client python check_access.py <DOI>\n"
        "     或：PAPER_TEST_DOI=<DOI> uv run --with websocket-client python check_access.py\n")
    sys.exit(2)

# 全文可访问的标志 / 无权限的标志
FULLTEXT_MARKERS = [
    "full text", "fulltext", "article content", "download pdf", "pdf",
    "supporting info", "references", "article pdf",
]
PAYWALL_MARKERS = [
    "get access", "purchase pdf", "buy article", "subscribe", "sign in to access",
    "rent this article", "add to cart", "institutional access", "access options",
    "choose a subscription", "purchase access", "get the full article",
]

tab = first_page_tab()
c = CDP(tab["webSocketDebuggerUrl"])
try:
    url = "https://doi.org/" + TEST_DOI
    print(f"访问: {url}")
    c.navigate(url, settle=6, wait_challenge=15)
    time.sleep(3)

    title = c.get_title() or ""
    final_url = c.get_url() or ""
    host = re.sub(r"^https?://([^/]+).*", r"\1", final_url)

    # 全页文本（容错：body 可能尚未就绪或被插件接管）
    txt = ""
    for attempt in range(4):
        try:
            txt = c.eval("(document.body && document.body.innerText) || ''") or ""
            if len(txt) > 200:
                break
        except Exception:
            pass
        time.sleep(3)
    if not txt:
        # 打印页面实际状态帮助诊断
        try:
            state = c.eval("({ready: document.readyState, url: location.href, "
                           "html: document.documentElement ? document.documentElement.outerHTML.length : -1, "
                           "embeds: document.querySelectorAll('embed,object').length})")
            print("页面状态:", state)
        except Exception as e:
            print("状态获取失败:", e)
    low = txt.lower()

    # 检测 PDF 链接
    pdf_links = c.eval("""
    (() => {
      const as = [...document.querySelectorAll('a')];
      return as.map(a => a.href).filter(h => h && /pdf/i.test(h)).slice(0, 6);
    })()
    """) or []

    # 页面长度（全文页通常很长）
    print(f"标题: {title[:80]}")
    print(f"落地: {host}")
    print(f"页面文本长度: {len(txt)}")

    hits_pay = [m for m in PAYWALL_MARKERS if m in low]
    hits_full = [m for m in FULLTEXT_MARKERS if m in low]
    print(f"付费墙标志: {hits_pay[:6]}")
    print(f"全文标志: {hits_full[:6]}")
    print(f"PDF 链接数: {len(pdf_links)}")
    for h in pdf_links[:3]:
        print("   ", h[:100])

    # 结论
    print("\n── 判定 ──")
    if any(x in low for x in ["get access", "purchase pdf", "add to cart", "buy article"]):
        print("[X] 无全文权限（出现购买/订阅入口）")
    elif len(txt) > 20000 and pdf_links:
        print("[OK] 疑似有全文权限（页面含正文且提供 PDF）")
    elif len(txt) > 12000:
        print("[WARN] 页面较长，可能含全文，需人工确认")
    else:
        print("[WARN] 仅摘要或受限页面")

    # 摘要长度
    m = re.search(r"(abstract|摘要)", low)
    if m:
        print(f"\n摘要起始位置: {m.start()}")
        print(txt[m.start():m.start()+500].replace("\n", " ")[:500])
finally:
    c.close()
