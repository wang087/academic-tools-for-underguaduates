# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 中文翻译 HTML → PDF（Chrome/Edge headless 渲染）+ 中文嵌入校验
#
# 用法：
#   uv run --with pypdf python render_html_pdf.py <input.html> <output.pdf>
#
# 校验内容：页数 ≥1、文件 >20 KB、首页含 CJK 字符（证明中文字体已嵌入）。
# 退出码：0 成功；1 渲染失败；2 校验失败。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import pathlib
import subprocess
import sys


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

# ── 日志（**文件型**：console=False）────────────────────────────
#   本脚本的 stdout 有**机器可解析的契约**（末行 `OK pages=… size=… cjk_…`），
#   调用方按它取页数与体积。因此日志**一律不写控制台**，只落
#   `.work_cdp/logs/render_html_pdf_<时间戳>.log` 并追加操作流水 ——
#   这样既有事后回溯，又不会把契约行淹掉。
#   （同类教训见 prepare_stage.py --tier：控制台日志混进 stdout 会造成静默失败。）
try:
    from logutil import get as _get_log, journal as _journal  # type: ignore
except Exception:                      # 单文件运行时也要能用
    def _get_log(name, **kw):          # noqa: D103
        class _L:
            def info(self, *a, **k): pass
            def ok(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass
            def close(self): pass
        return _L()

    def _journal(*a, **k):             # noqa: D103
        pass

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    raise FileNotFoundError("未找到 Chrome / Edge 可执行文件")


def _run(log) -> int:
    html = pathlib.Path(sys.argv[1]).resolve()
    pdf = pathlib.Path(sys.argv[2]).resolve()
    log.info(f"渲染 {html} → {pdf}")

    if not html.exists():
        log.error(f"HTML 不存在: {html}")
        _journal("F", "render.html_missing", html=str(html))
        print(f"HTML 不存在: {html}")
        return 1
    pdf.parent.mkdir(parents=True, exist_ok=True)

    browser = find_browser()
    log.info(f"浏览器: {browser}")
    url = html.as_uri()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--print-to-pdf-no-header", f"--print-to-pdf={pdf}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       errors="replace")
    if not pdf.exists() or pdf.stat().st_size < 20000:
        tail = (r.stderr or r.stdout or "")[-400:]
        log.error(f"渲染失败: {tail}")
        _journal("F", "render.pdf_failed", html=str(html), pdf=str(pdf),
                 reason=tail[:160])
        print("渲染失败:", tail)
        return 1

    # 校验：页数 + CJK 嵌入
    try:
        from pypdf import PdfReader
        rd = PdfReader(str(pdf))
        pages = len(rd.pages)
        txt = rd.pages[0].extract_text() or ""
        cjk = sum(1 for c in txt if "\u4e00" <= c <= "\u9fff")
    except Exception as e:
        log.error(f"校验读取失败: {str(e)[:120]}")
        _journal("F", "render.verify_failed", pdf=str(pdf), error=str(e)[:160])
        print("校验读取失败:", str(e)[:120])
        return 2

    size_kb = pdf.stat().st_size // 1024
    log.ok(f"OK pages={pages} size={size_kb}KB cjk_first_page={cjk}")
    _journal("F", "render.pdf", html=str(html), pdf=str(pdf),
             pages=pages, size_kb=size_kb, cjk_first_page=cjk)
    print(f"OK pages={pages} size={size_kb}KB cjk_first_page={cjk}")
    if cjk < 50:
        log.warn("首页中文字符过少，可能字体未嵌入")
        print("[WARN] 首页中文字符过少，可能字体未嵌入")
        return 2
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__ or "用法: uv run --with pypdf python render_html_pdf.py "
                         "<input.html> <output.pdf>")
        return 1
    log = _get_log("render_html_pdf", console=False)
    try:
        return _run(log)
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
