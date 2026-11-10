# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S · **交互枚举**：驱动真实浏览器点击，拿到 JS 隐藏的教师名录
#
# 为什么需要它
#   实测（南京大学化学化工学院 <https://chem.nju.edu.cn/szll/list.htm>）：
#     · 师资名录页 `ul.teacher-list` 是**空壳**，由 AJAX 填充；
#     · 左侧 11 个学科链接全部是 `javascript:void(0)`，**必须点击**才加载；
#     · 该 AJAX（POST /_wp3services/generalQuery）对非浏览器客户端返回 503。
#   → 纯直连、甚至纯渲染（crawl_site --render always）都只能拿到默认的
#     院士 3 人；click 是唯一途径。robot/crawl_site.py 只有
#     `--wait-selector / --scroll`，**没有点击能力**，故这里补上。
#
# 与其它模块的分工（阶段 S 复用模式总览）
#   faculty_discover.py  发现师资页 → 判定家族 → 生成配置
#   faculty_engines.py   直连 HTML 的家族解析（第一层）
#   faculty_render.py    渲染桥（接 robot/crawl_site.py）
#   faculty_interact.py  ← 本模块：点击枚举（JS 隐藏名录）
#   harvest_faculty.py   串起来跑完整阶段 S
#
# 依赖：websocket-client（`uv run --with websocket-client`）与一个
#       `--remote-debugging-port` 启动的 Chrome/Edge。
#       浏览器不可用时**优雅降级**（返回空并说明原因），不影响其余链路。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

__all__ = ["cdp_available", "ensure_browser", "enumerate_by_clicking",
           "CdpUnavailable"]

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
               "academic-use")


class CdpUnavailable(RuntimeError):
    """浏览器/CDP 不可用（调用方应优雅降级并如实报告）。"""


# ══════════════════════════════════════════════════════════════
# 浏览器生命周期
# ══════════════════════════════════════════════════════════════
def _http_json(port: int, path: str, method: str = "GET", timeout: float = 8):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _find_browser() -> str | None:
    cands = [
        os.environ.get("CHROME_PATH") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def cdp_available(port: int) -> bool:
    try:
        _http_json(port, "/json/version")
        return True
    except Exception:
        return False


def _system_pythons() -> list[str]:
    """找一个**非虚拟环境**的系统 Python（Chrome 只能由它启动）。

    实测（本机，决定性）：
        · 系统 `python`（...\\pythoncore-3.14-64\\python.exe）→ Chrome 正常启动（9 进程）
        · `.venv` / `uv run` 的 python                        → Chrome 起不来（0 进程）
      原因是后者的环境会让 Chrome 的 crashpad 子进程 Access denied 而自杀。
      故当"当前解释器"启动失败时，改用系统解释器**代为 spawn**。
    """
    import shutil
    out: list[str] = []
    # 常见系统位置（Windows）
    local = os.environ.get("LOCALAPPDATA") or ""
    for pat in (os.path.join(local, "Python", "pythoncore-*", "python.exe"),
                os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
                r"C:\Python3*\python.exe"):
        try:
            import glob
            out += sorted(glob.glob(pat), reverse=True)
        except Exception:
            pass
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p:
            out.append(p)
    cur = os.path.normcase(os.path.abspath(sys.executable))
    # 排除当前解释器与其同目录（虚拟环境）
    cur_dir = os.path.dirname(cur)
    uniq, seen = [], set()
    for p in out:
        if not p or not os.path.isfile(p):
            continue
        np = os.path.normcase(os.path.abspath(p))
        if np == cur or os.path.dirname(np) == cur_dir or np in seen:
            continue
        seen.add(np)
        uniq.append(p)
    return uniq


def _spawn_browser(exe: str, port: int, headless: bool,
                   profile: str) -> None:
    """启动浏览器（当前解释器）。"""
    args = [exe,
            "--headless=new" if headless else "--start-maximized",
            "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "about:blank"]
    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00000008      # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(args, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags)


def _spawn_browser_via_helper(exe: str, port: int, headless: bool,
                              profile: str) -> bool:
    """用**系统解释器**代为启动浏览器（绕开虚拟环境对 Chrome 的限制）。"""
    argv = ["--headless=new" if headless else "--start-maximized",
            "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "about:blank"]
    code = (
        "import subprocess\n"
        f"exe = {exe!r}\n"
        f"argv = {argv!r}\n"
        "subprocess.Popen([exe] + argv, stdout=subprocess.DEVNULL,\n"
        "                 stderr=subprocess.DEVNULL,\n"
        "                 creationflags=0x08000000 | 0x00000008)\n"
    )
    for py in _system_pythons():
        try:
            subprocess.run([py, "-c", code], timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        for _ in range(24):
            time.sleep(0.4)
            if cdp_available(port):
                return True
    return False


def ensure_browser(port: int = 9222, *, headless: bool = True) -> str:
    """确保有一个带 CDP 的浏览器在跑；返回其可执行路径。

    三级尝试（实测必要）：
      ① 已在跑 → 直接用
      ② 当前解释器启动 → 系统 python 下有效
      ③ 系统解释器代为启动 → venv / `uv run` 下只有这条能成
    `--remote-allow-origins=*` 必需，否则 websocket 握手 403（实测）。
    """
    if cdp_available(port):
        return _find_browser() or "(已在运行的浏览器)"
    exe = _find_browser()
    if not exe:
        raise CdpUnavailable("未找到 Chrome/Edge，无法做交互枚举")
    profile = os.path.join(os.environ.get("TEMP") or "/tmp",
                           f"faculty-cdp-{port}-{random.randint(1000, 9999)}")

    # ② 直接启动
    try:
        _spawn_browser(exe, port, headless, profile)
    except Exception:
        pass
    for _ in range(20):
        time.sleep(0.4)
        if cdp_available(port):
            return exe

    # ③ 借系统解释器启动（venv / uv run 场景）
    if _spawn_browser_via_helper(exe, port, headless, profile):
        return exe
    # ④ 都失败 → 给出**可直接复制执行的**一行命令。
    #    实测结论：`uv run` 对 Chrome 的限制会**沿进程树继承**
    #      （uv → 系统 python → Chrome 依然起不来），故无法在脚本内绕过；
    #      唯一可行路径是在**普通 shell** 里先把调试浏览器起好，流程再连它。
    raise CdpUnavailable(
        f"浏览器无法在脚本内启动（CDP 端口 {port} 不可达）。\n"
        "    本机实测：`uv run` 会阻止 Chrome 启动（其限制沿进程树继承）。\n"
        "    请先另开一个普通终端执行下面这行，再重跑本流程：\n"
        f"      {_prestart_command(port)}\n"
        f"    或直接：uv run python 流程1/scripts/faculty_interact.py "
        f"--start-browser --port {port}")


def _prestart_command(port: int) -> str:
    """给出"在普通 shell 里预启动调试浏览器"的可复制命令。"""
    exe = _find_browser() or "chrome"
    prof = os.path.join(os.environ.get("TEMP") or "/tmp", f"cdp-{port}")
    if os.name == "nt":
        return (f'start "" "{exe}" --headless=new --disable-gpu '
                f'--remote-debugging-port={port} --remote-allow-origins=* '
                f'--user-data-dir="{prof}" about:blank')
    return (f'"{exe}" --headless=new --disable-gpu '
            f'--remote-debugging-port={port} --remote-allow-origins=* '
            f'--user-data-dir="{prof}" about:blank &')


# ══════════════════════════════════════════════════════════════
# CDP 会话（双后端）
#
# [STOP] 实测约束（决定为什么要有两个后端）
#   · 能启动 Chrome 的**只有系统 `python`**：用 `uv run python` 时
#     Chrome 以 0x80000003 终止、拿不到 DOM（见 faculty_render.py 注释）。
#   · 但系统 python 环境里**没有** websocket-client（且本机装不上），
#     只有 `websockets`（16.0，asyncio）。
#   → 故同时支持两个后端，优先 websocket-client，缺失时用 websockets，
#     这样"能启动浏览器"与"能连 CDP"两个条件才能同时满足。
# ══════════════════════════════════════════════════════════════
class _Session:
    """一个 CDP 页面会话；对外只暴露 send()/js()/close()。"""

    def __init__(self, port: int, url: str, user_agent: str):
        tabs = [t for t in _http_json(port, "/json/list") if t.get("type") == "page"]
        if tabs:
            tgt = tabs[0]
        else:
            tgt = _http_json(port,
                             "/json/new?" + urllib.parse.quote(url, safe=""),
                             method="PUT")
        self.ws_url = tgt["webSocketDebuggerUrl"]
        self._id = 0
        self._backend = ""
        self._ws = None
        self._loop = None
        self._async_ws = None
        self._connect()

    # ── 后端选择 ──
    def _connect(self):
        try:
            import websocket  # type: ignore  # websocket-client（同步）
            self._ws = websocket.create_connection(self.ws_url, timeout=60)
            self._backend = "websocket-client"
            return
        except Exception:
            pass
        try:
            import websockets  # type: ignore   # asyncio
            import asyncio
        except Exception as exc:                    # pragma: no cover
            raise CdpUnavailable(
                "需要 websocket-client 或 websockets 之一来驱动 CDP") from exc
        self._loop = asyncio.new_event_loop()
        self._async_ws = self._loop.run_until_complete(
            websockets.connect(self.ws_url, max_size=32 * 1024 * 1024))
        self._backend = "websockets"

    def send(self, method: str, params: dict | None = None, timeout: float = 60):
        self._id += 1
        mid = self._id
        payload = {"id": mid, "method": method, "params": params or {}}
        if self._backend == "websocket-client":
            self._ws.send(json.dumps(payload))
            self._ws.settimeout(timeout)
            while True:
                msg = json.loads(self._ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})
        return self._loop.run_until_complete(self._asend(mid, payload))

    async def _asend(self, mid: int, payload: dict):
        await self._async_ws.send(json.dumps(payload))
        while True:
            msg = json.loads(await self._async_ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{payload['method']}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str, timeout: float = 60):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True,
                       "awaitPromise": True}, timeout=timeout)
        return r.get("result", {}).get("value")

    def close(self):
        try:
            if self._backend == "websocket-client":
                self._ws.close()
            elif self._async_ws is not None:
                self._loop.run_until_complete(self._async_ws.close())
        except Exception:
            pass
        try:
            if self._loop is not None:
                self._loop.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# 交互枚举
# ══════════════════════════════════════════════════════════════
# 常见「教师卡片」选择器（**有序**：越靠前越专指教师列表）
#
# 实测教训（南大化学）：不要用"命中数最多者"来选选择器 ——
#   该站 `ul.wp_listcolumn li` 命中 11 条（那是**学科分类菜单**），
#   而真正的教师列表 `ul.teacher-list li` 只命中 3 条（默认院士）。
#   按"最多"选会把分类菜单当成教师，得到 11 个"人"且每类完全相同。
#   故此处固定**优先级顺序**，取第一个命中数 > 0 的。
_CARD_SELECTORS = [
    "ul.teacher-list li",
    "ul.teacher_list li",
    "div.teacher-list li",
    "ul.list-teachers-item li",
    "ul.teachers-list li",
    "div.teacher-con li",
    "ul.teacher-list > li",
]

_NAME_JS = """(function(sel){
  var out = [];
  document.querySelectorAll(sel).forEach(function(li){
    var a = li.querySelector('a[href]');
    // 姓名优先取专用子元素，退化到链接文本、再退化到整块文本
    var nmEl = li.querySelector('.teacher-name, .name, .teacher-name a, h3, h4, .tit');
    var text = '';
    if (nmEl) text = nmEl.textContent || '';
    if (!text && a) text = a.textContent || '';
    text = text.replace(/\\s+/g, '').trim();
    // 去掉「姓名+职务」粘连里的非姓名部分
    var href = a ? a.href : '';
    var title = a ? (a.getAttribute('title') || '') : '';
    if (href && href.indexOf('javascript:') === 0) href = '';
    out.push({name: text, href: href, title: title});
  });
  return out;
})(%s)"""


def enumerate_by_clicking(url: str, *, port: int = 9222,
                          click_selector: str = "a.col_item_link",
                          card_selector: str = "",
                          wait: float = 3.0, max_clicks: int = 30,
                          user_agent: str = _DEFAULT_UA,
                          headless: bool = True) -> dict:
    """打开页面 → 依次点击每个分类 → 收集全部教师链接。

    返回 {"teachers":[{"name","url","categories"}], "categories":[...],
          "clicks":N, "why": "..."}；浏览器不可用时抛 `CdpUnavailable`。
    """
    ensure_browser(port, headless=headless)
    s = _Session(port, url, user_agent)
    try:
        s.send("Page.enable")
        s.send("Runtime.enable")
        s.send("Page.navigate", {"url": url})
        time.sleep(6)
        # 等首屏 AJAX 落地
        for _ in range(20):
            got = s.js("document.querySelectorAll('"
                       + (_CARD_SELECTORS[0]) + "').length") or 0
            if got:
                break
            time.sleep(0.5)

        # 选卡片选择器：按**优先级顺序**取第一个有命中的。
        # 不能用"命中数最多者"：该站 `ul.wp_listcolumn li` 命中 11（学科菜单），
        #   真正的 `ul.teacher-list li` 只命中 3（默认院士）→ 会把分类菜单当教师。
        sel = card_selector
        if not sel:
            for cand in _CARD_SELECTORS:
                n = s.js(f"document.querySelectorAll({json.dumps(cand)}).length") or 0
                if n > 0:
                    sel = cand
                    break
            sel = sel or _CARD_SELECTORS[0]

        cats = s.js(f"""Array.from(document.querySelectorAll({json.dumps(click_selector)}))
            .map(function(a){{return a.getAttribute('title') || a.textContent.trim();}})
            .filter(Boolean)""") or []
        cats = cats[:max_clicks]

        collected: dict[str, dict] = {}
        per_cat: dict[str, int] = {}
        for i in range(len(cats)):
            s.js(f"""(function(){{
                var a = document.querySelectorAll({json.dumps(click_selector)})[{i}];
                if(!a) return false; a.click(); return true; }})()""")
            time.sleep(wait)
            items = s.js(_NAME_JS % json.dumps(sel)) or []
            keep = 0
            for it in items:
                nm = (it.get("name") or "").strip()
                href = (it.get("href") or "").strip()
                if not nm:
                    continue
                # 过滤栏目名/占位
                if len(nm) > 20 or re.search(r"[，,。；;、/（）()]", nm):
                    continue
                key = href or nm
                rec = collected.setdefault(key, {"name": nm, "url": href,
                                                 "categories": []})
                if cats[i] not in rec["categories"]:
                    rec["categories"].append(cats[i])
                keep += 1
            per_cat[cats[i]] = keep

        return {"teachers": [v for v in collected.values()],
                "categories": cats, "per_category": per_cat,
                "card_selector": sel, "clicks": len(cats),
                "source_url": url, "why": "ok"}
    finally:
        s.close()


if __name__ == "__main__":                       # 手工自检 / 预启动浏览器
    import argparse
    ap = argparse.ArgumentParser(
        description="交互枚举自检（并可在普通 shell 里预启动调试浏览器）",
        epilog=("提示：`uv run` 会阻止 Chrome 启动。若流程报"
                "「浏览器无法在脚本内启动」，\n"
                "      请先在普通终端执行 --start-browser，再重跑流程。"))
    ap.add_argument("url", nargs="?", help="要枚举的页面 URL")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--click-selector", default="a.col_item_link")
    ap.add_argument("--start-browser", action="store_true",
                    help="只启动一个带 CDP 的调试浏览器后退出（供后续流程连接）")
    a = ap.parse_args()

    if a.start_browser:
        try:
            exe = ensure_browser(a.port)
        except CdpUnavailable as e:
            print(f"[X] {e}")
            raise SystemExit(3)
        print(f"[OK] 调试浏览器已就绪：{exe}")
        print(f"  CDP 端口 {a.port} 可用；现在可以重跑 harvest/枚举流程。")
        raise SystemExit(0)

    if not a.url:
        ap.print_help()
        raise SystemExit(0)
    try:
        r = enumerate_by_clicking(a.url, port=a.port,
                                  click_selector=a.click_selector)
    except CdpUnavailable as e:
        print(f"CDP 不可用：{e}")
        raise SystemExit(3)
    print(f"类别 {len(r['categories'])} 个：{r['categories']}")
    print(f"每类命中：{r['per_category']}")
    print(f"唯一教师：{len(r['teachers'])}")
    for t in r["teachers"][:10]:
        print(f"   {t['name']:<10} {'/'.join(t['categories'])[:24]:<26} {t['url']}")
