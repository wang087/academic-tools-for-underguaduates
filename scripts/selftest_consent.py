# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 同意书弹窗自动关闭 —— 离线自检（`cdp.py` 的 CONSENT_JS）
#
# 为什么需要它：CONSENT_JS 是**注入到页面里的字符串**，Python 侧
#   `py_compile` 完全检查不到它的语法与行为；而注入失败会被 `_inject()`
#   静默吞掉（只写一条 debug 日志），`dismiss_consent()` 也吞 CDPError。
#   结果是「cookie 弹窗从来不关」这种缺陷**不会报错**，只表现为
#   正文被遮挡、`try_render` 把横幅打进成品 PDF。
#
# 本自检完全离线：本地 file:// 夹具页面，**零网络请求**，
#   不接触任何出版社站点，因此不违反 CDP 强制抓取禁令。
#   它在本地 Edge 里验证三件事：
#     ① 固定定位（position:fixed）横幅能被识别 —— 这正是 v6 修的
#        `offsetParent !== null` 缺陷：旧判据下 fixed 元素恒被判为不可见；
#     ② shadow DOM 内的横幅能被穿透并点到「接受」；
#     ③ 正文里同名的普通按钮**不会**被误点（保守性）。
#
# 用法：
#   uv run --with websocket-client python selftest_consent.py [--port=9251] [--keep]
#
# 退出码：0 全部通过；1 有断言失败；2 环境问题（未找到 Edge / 启动失败）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import os
import pathlib
import shutil
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

FIXTURE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>consent fixture</title></head>
<body>
  <h1>Article body</h1>
  <p id="content">This paragraph stands in for the paper text.</p>
  <!-- ③ 正文里的普通按钮：文字同样是 Accept，但不在任何已知同意容器内，
       绝不能被点到（保守性原则） -->
  <button id="benign" onclick="window.__clicked=(window.__clicked||[]).concat('benign')">Accept</button>

  <!-- ① 固定定位横幅（Cookiebot 形态）。旧 offsetParent 判据下恒不可见 -->
  <div id="CybotCookiebotDialog"
       style="position:fixed;left:0;right:0;bottom:0;height:120px;background:#eee;z-index:9999">
    <p>We use cookies</p>
    <button id="accept1"
            onclick="window.__clicked=(window.__clicked||[]).concat('accept1')">Accept all cookies</button>
  </div>

  <!-- ② 固定定位 + 按钮带装饰符（"→"） -->
  <div id="cookie-banner"
       style="position:fixed;left:0;top:0;right:0;height:100px;background:#ddd;z-index:9999">
    <button id="accept2"
            onclick="window.__clicked=(window.__clicked||[]).concat('accept2')">Accept all →</button>
  </div>

  <!-- ③ shadow DOM 内的横幅（Usercentrics v2 形态）：宿主本身 0 尺寸 -->
  <div id="usercentrics-root"></div>
  <script>
    const host = document.getElementById('usercentrics-root');
    const sr = host.attachShadow({mode: 'open'});
    sr.innerHTML =
      '<div style="position:fixed;left:0;top:200px;width:400px;height:150px;' +
      'background:#ccc;z-index:9999">' +
      '<button id="accept3" ' +
      'onclick="window.__clicked=(window.__clicked||[]).concat(\\'accept3\\')">同意并继续</button></div>';
  </script>
</body></html>
"""


def _arg(name: str, default):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def main() -> int:
    port = int(_arg("port", "9251"))
    keep = "--keep" in sys.argv

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="consent_selftest_"))
    # 必须在 import cdp / cdp_pool **之前**设好：两者的模块级常量在
    #   import 时求值（DEFAULT_PORT、日志目录、注入登记表路径）。
    os.environ["PAPER_WORKDIR"] = str(tmp)
    os.environ["CDP_PORT"] = str(port)

    import cdp          # noqa: E402
    import cdp_pool     # noqa: E402

    page = tmp / "fixture.html"
    page.write_text(FIXTURE, encoding="utf-8")
    url = page.as_uri()

    print(f"自检工作目录: {tmp}")
    print(f"夹具页面:     {url}")
    print(f"CDP 端口:     {port}\n")

    rc = 0
    c = None
    try:
        if not cdp_pool.launch_one(tmp, port, level=0, wait=40.0):
            print("[X] Edge CDP 实例启动失败（见上方日志）")
            return 2
        print(f"[OK] Edge 已就绪（L0 日常档，含新增弹窗抑制参数）")

        c = cdp.connect(port, timeout=30)
        c.navigate(url, settle=1.5, load_timeout=20)

        # 与 download_all_fulltexts.load_landing() 的调用方式一致
        handled = c.dismiss_consent(timeout=10)
        print(f"[OK] dismiss_consent() 返回 {handled}（v6 起为真实结果，旧版恒 False）")

        clicked = c.eval("window.__clicked || []", timeout=10) or []
        print(f"  已点击按钮: {clicked}")

        # 点击后 1.5s 才走隐藏兜底，等它落地
        time.sleep(2.2)
        # 隐藏兜底施于**容器本身**；shadow 宿主被 display:none 后其影子内容
        # 同样不渲染，因此三者都查容器自身的 computed display 即可。
        hidden = c.eval(
            "(() => { const ids = ['CybotCookiebotDialog','cookie-banner','usercentrics-root'];"
            " const out = {};"
            " for (const id of ids) { const e = document.getElementById(id);"
            "   out[id] = e ? getComputedStyle(e).display : 'missing'; }"
            " return out; })()", timeout=10) or {}
        counts = {k: clicked.count(k) for k in ("accept1", "accept2", "accept3", "benign")}
        print(f"  弹窗最终状态: {hidden}")
        print(f"  各按钮点击次数: {counts}\n")

        checks = [
            ("固定定位横幅 Cookiebot 被点到「接受」", counts["accept1"] == 1),
            ("按钮文字带装饰符 → 仍能匹配", counts["accept2"] == 1),
            ("shadow DOM 内的横幅被穿透并点到", counts["accept3"] == 1),
            ("正文里的同名 Accept 按钮未被误点", counts["benign"] == 0),
            ("同一容器只点一次（无重复提交）", max(counts.values()) <= 1),
            ("三个容器最终均不可见", all(v == "none" for v in hidden.values())),
        ]
        for name, ok in checks:
            print(f"  {'[OK]' if ok else '[X]'} {name}")
            if not ok:
                rc = 1
    except Exception as e:
        print(f"[X] 自检异常: {type(e).__name__}: {e}")
        rc = 2
    finally:
        try:
            if c:
                c.close()
        except Exception:
            pass
        try:
            cdp_pool.kill_port(port)
        except Exception:
            pass
        if keep:
            print(f"\n（--keep：保留 {tmp}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("[OK] 同意书弹窗自检全部通过" if rc == 0 else "[X] 自检未通过"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
