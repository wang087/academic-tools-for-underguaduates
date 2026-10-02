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


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    html = pathlib.Path(sys.argv[1]).resolve()
    pdf = pathlib.Path(sys.argv[2]).resolve()
    if not html.exists():
        print(f"HTML 不存在: {html}")
        return 1
    pdf.parent.mkdir(parents=True, exist_ok=True)

    browser = find_browser()
    url = html.as_uri()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--print-to-pdf-no-header", f"--print-to-pdf={pdf}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       errors="replace")
    if not pdf.exists() or pdf.stat().st_size < 20000:
        print("渲染失败:", (r.stderr or r.stdout or "")[-400:])
        return 1

    # 校验：页数 + CJK 嵌入
    try:
        from pypdf import PdfReader
        rd = PdfReader(str(pdf))
        pages = len(rd.pages)
        txt = rd.pages[0].extract_text() or ""
        cjk = sum(1 for c in txt if "\u4e00" <= c <= "\u9fff")
    except Exception as e:
        print("校验读取失败:", str(e)[:120])
        return 2
    print(f"OK pages={pages} size={pdf.stat().st_size//1024}KB cjk_first_page={cjk}")
    if cjk < 50:
        print("⚠️ 首页中文字符过少，可能字体未嵌入")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
