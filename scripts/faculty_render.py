# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S · **渲染桥**：把 robot/crawl_site.py 接进主流程
#
# 解决什么问题
#   流程1 的既有引擎库（faculty_engines.py）全部基于**直连 HTML**。
#   遇到师资页是 JS/AJAX 空壳的院系（实测：南京大学化学化工学院
#   `ul.teacher-list` 为空、11 个学科靠点击异步加载；北京大学部分院系同理），
#   直连永远只能得 0 人 —— 此前只能在报告里写"需要 CDP"然后放弃。
#   本模块用**用户自编的 robot/crawl_site.py**（无头浏览器渲染）补上这一段，
#   使这类站点也能走完阶段 S。
#
# [STOP] 两条实测硬约束（都必须遵守，否则静默失败）
#   ① **必须用系统 `python` 调用，不能用 `uv run`**。
#      实测：`uv run python robot/crawl_site.py --render always` → JS 渲染 0
#      （Chrome 以 0x80000003 终止、无 DOM）；同一命令用系统 `python` → JS 渲染 1。
#      故本模块在检测到"渲染 0 页"时**自动改用 `sys.executable` 重跑一次**。
#   ② **`file://` 不被接受**（crawl_site 只认 http/https）。
#      故需要"URL 清单 → 批量抓取"时，走 `crawl_urls()`：起一个**本地 HTTP**
#      种子页（127.0.0.1 随机端口），再用 crawl_site 抓它，抓完即关。
#
# 与 §5.1 分层获取策略的关系
#   **学部官网师资页属公开元数据 → 走第一层直连**。
#   本模块只在**直连拿不到**（JS 空壳）时启用，且只渲染师资页/教师主页，
#   不触碰出版社内容站。渲染不是"绕过直连"，而是第一层失效后的转 CDP 分支。
#
# 只用标准库；crawl_site 由本模块按需 import（缺失时优雅降级）。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import http.server
import json
import os
import pathlib
import random
import re
import socketserver
import subprocess
import sys
import tempfile
import threading

_HERE = pathlib.Path(__file__).resolve().parent
_ROBOT_DIR = _HERE.parent / "robot"
_CRAWL_SITE = _ROBOT_DIR / "crawl_site.py"

__all__ = [
    "crawler_available", "crawl_page_html", "crawl_urls",
    "render_for_engine", "CRAWL_SITE_PATH",
]

CRAWL_SITE_PATH = _CRAWL_SITE


def crawler_available() -> bool:
    """crawl_site.py 是否就位（robot/ 目录存在且文件在）。"""
    return _CRAWL_SITE.is_file()


def _load_crawl_module():
    """import robot/crawl_site.py（模块名 crawl_site）。"""
    if str(_ROBOT_DIR) not in sys.path:
        sys.path.insert(0, str(_ROBOT_DIR))
    import crawl_site  # type: ignore
    return crawl_site


# ══════════════════════════════════════════════════════════════
# ① 单页渲染（给引擎库用：拿到"渲染后的 HTML"再交给家族解析）
# ══════════════════════════════════════════════════════════════
def crawl_page_html(url: str, *, virtual_time_budget: int = 12000,
                    timeout: float = 90.0, user_agent: str | None = None,
                    allow_subprocess_retry: bool = True) -> str:
    """渲染单个 URL，返回**渲染后**的 HTML（失败返回空串）。

    先尝试进程内调用（快、无子进程）；若渲染页数为 0（被 `uv run` 限制的典型症状），
    且允许重试，则改用 `sys.executable` 起子进程再试一次。
    """
    html = _crawl_inproc(url, virtual_time_budget, timeout, user_agent)
    if html:
        return html
    if allow_subprocess_retry:
        html = _crawl_subprocess(url, virtual_time_budget, timeout, user_agent)
    return html or ""


def _crawl_inproc(url, vtb, timeout, user_agent) -> str:
    try:
        cs = _load_crawl_module()
    except Exception:
        return ""
    try:
        argv = ["--render", "always", "--render-engine", "chrome",
                "--max-pages", "1", "--max-depth", "0",
                "--virtual-time-budget", str(int(vtb)),
                "--timeout", str(int(timeout)), "--indent", "0",
                "-o", os.devnull, url]
        parser = cs.build_parser()
        opts = parser.parse_args(argv)
        if user_agent:
            opts.user_agent = user_agent
        crawler = cs.Crawler(opts)
    except Exception:
        return ""
    try:
        doc = crawler.run()
    except Exception:
        return ""
    finally:
        try:
            crawler.close()
        except Exception:
            pass
    pages = doc.get("pages") or []
    if not pages:
        return ""
    p = pages[0]
    # 渲染成功但正文空 → 视为失败，交给子进程重试
    if not p.get("ok"):
        return ""
    return _reserialize(p) or ""


def _crawl_subprocess(url, vtb, timeout, user_agent) -> str:
    """用 sys.executable 起 crawl_site.py（绕开 uv run 对浏览器进程的限制）。"""
    if not crawler_available():
        return ""
    out = pathlib.Path(tempfile.mkdtemp(prefix="facultyrender-")) / "one.json"
    cmd = [sys.executable, str(_CRAWL_SITE), url,
           "--render", "always", "--render-engine", "chrome",
           "--max-pages", "1", "--max-depth", "0",
           "--virtual-time-budget", str(int(vtb)),
           "--timeout", str(int(timeout)), "-o", str(out), "-q"]
    if user_agent:
        cmd += ["--user-agent", user_agent]
    try:
        subprocess.run(cmd, timeout=timeout + 60, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except Exception:
        return ""
    try:
        doc = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        return ""
    pages = doc.get("pages") or []
    return _reserialize(pages[0]) if pages else ""


# crawl_site 的页面对象里**不保留原始 HTML**（只有 text/links/tables），
# 故这里把结构化结果**还原成 HTML**，好让 faculty_engines 的家族解析器复用。
def _reserialize(page: dict) -> str:
    """把 crawl_site 的页面对象还原为 HTML 片段（供引擎正则复用）。

    为什么要还原：引擎库认的是 `span.news_title`、`li.itemhover`、
      `h3 + span.direction` 这类**结构指纹**；而 crawl_site 只吐纯文本与链接。
      还原时保留「链接 → <a>、标题 → <h3>、表格 → <table>」这些关键结构，
      使同一套家族判据在"直连"与"渲染"两条链路上行为一致。
    """
    parts: list[str] = []
    for h in (page.get("headings") or []):
        lv = int(h.get("level") or 2)
        parts.append(f"<h{lv}>{_esc(h.get('text') or '')}</h{lv}>")
    for ln in (page.get("links") or []):
        href = _esc(ln.get("url") or ln.get("href") or "")
        txt = _esc(ln.get("text") or "")
        ttl = ln.get("title")
        attr = f' title="{_esc(ttl)}"' if ttl else ""
        parts.append(f'<a href="{href}"{attr}>{txt}</a>')
    for t in (page.get("tables") or []):
        rows = "".join(
            "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>"
            for r in (t.get("rows") or []))
        cap = f"<caption>{_esc(t.get('caption'))}</caption>" if t.get("caption") else ""
        parts.append(f"<table>{cap}{rows}</table>")
    txt = page.get("text") or ""
    if txt:
        parts.append("<pre>" + _esc(txt) + "</pre>")
    return "\n".join(parts)


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ══════════════════════════════════════════════════════════════
# ② 批量抓取（给"已知 URL 清单"用：本地 HTTP 种子页）
# ══════════════════════════════════════════════════════════════
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):        # 静音
        pass


class _ReuseServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _serve_dir(directory: str) -> tuple[_ReuseServer, int]:
    handler = lambda *a, **k: _QuietHandler(*a, directory=directory, **k)  # noqa: E731
    for _ in range(12):
        port = random.randint(18000, 28000)
        try:
            srv = _ReuseServer(("127.0.0.1", port), handler)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, port
    raise RuntimeError("无法在本地起种子页服务")


def crawl_urls(urls: list[str], out_json: pathlib.Path, *,
               concurrency: int = 5, delay: float = 0.3,
               max_text_chars: int = 0, user_agent: str | None = None,
               timeout: float = 90.0, verbose: bool = False) -> dict:
    """抓取**一批指定 URL**（教师主页清单）→ 写 JSON，返回 crawl 文档。

    crawl_site.py 只接受**单个**起始 URL（parser 里只有 positional `url`），
      没有 URL 清单入口；且不认 `file://`。
      故本函数生成一个本地种子页并用 127.0.0.1 上的临时 HTTP 服务提供，
      让 crawl_site 自己去跟进 —— **不修改 crawl_site.py 的接口**。
    """
    if not crawler_available():
        raise RuntimeError(f"找不到爬虫：{_CRAWL_SITE}")
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="facultyseed-"))
    rows = "\n".join(f'<li><a href="{u}">{u}</a></li>' for u in urls)
    (tmpdir / "index.html").write_text(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>faculty seed</title></head><body><ul>" + rows +
        "</ul></body></html>", encoding="utf-8")

    srv, port = _serve_dir(str(tmpdir))
    seed = f"http://127.0.0.1:{port}/index.html"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    try:
        cs = _load_crawl_module()
        argv = ["--render", "never", "--scope", "any",
                "--max-pages", str(len(urls) + 5), "--max-depth", "1",
                "--concurrency", str(concurrency), "--delay", str(delay),
                "--max-text-chars", str(max_text_chars),
                "--no-strip-boilerplate",
                "-o", str(out_json), seed]
        if user_agent:
            argv += ["--user-agent", user_agent]
        if verbose:
            argv.append("-v")
        opts = cs.build_parser().parse_args(argv)
        crawler = cs.Crawler(opts)
        try:
            doc = crawler.run()
        finally:
            try:
                crawler.close()
            except Exception:
                pass
        out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return doc
    finally:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# ③ 给引擎库的适配入口
# ══════════════════════════════════════════════════════════════
def render_for_engine(url: str, **kw) -> str:
    """渲染一页并返回 HTML；引擎库的 `render` 家族用它。"""
    return crawl_page_html(url, **kw)


if __name__ == "__main__":                       # 手工自检
    import argparse
    ap = argparse.ArgumentParser(description="渲染桥自检")
    ap.add_argument("url")
    ap.add_argument("--budget", type=int, default=12000)
    a = ap.parse_args()
    h = render_for_engine(a.url, virtual_time_budget=a.budget)
    print(f"渲染 HTML 长度: {len(h)}")
    print(h[:300])
