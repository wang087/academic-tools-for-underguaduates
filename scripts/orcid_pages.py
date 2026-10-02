# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 可移植配置：设置环境变量 PAPER_WORKDIR 指向作者工作目录，
# 未设置时使用当前工作目录。所有中间数据存于 <WORKDIR>/.work_cdp/
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys
WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
AUTHOR = os.environ.get("PAPER_AUTHOR", WORKDIR.name)
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

"""CDP: paginate ORCID works page by page, accumulate full text."""
import json, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(CDPDIR))
from cdp import CDP, first_page_tab

# ORCID iD 不硬编码：优先取环境变量 PAPER_ORCID，其次取第一个命令行参数
ORCID_ID = (os.environ.get("PAPER_ORCID") or (sys.argv[1].strip() if len(sys.argv) > 1 else "")).strip()
if not ORCID_ID:
    sys.stderr.write(
        "✗ 未指定 ORCID iD。\n"
        "  用法：PAPER_ORCID=0000-0000-0000-0000 uv run --with websocket-client python orcid_pages.py\n"
        "     或：uv run --with websocket-client python orcid_pages.py <ORCID>\n")
    sys.exit(2)
ORCID_ID = ORCID_ID.rstrip("/").split("/")[-1]          # 兼容传入完整 ORCID URL
URL = f"https://orcid.org/{ORCID_ID}"
OUT = str(CDPDIR / "orcid_works_all.txt")

# ── 与 orcid_extract.py 共用的页面脚本（两处改动必须同步）──────────
# ★ ORCID 3.x 改版：成果卡片元素名 `app-work-statement` → `app-work`，
#   且成果面板**默认折叠**。不展开就直接取文本，只会拿到个人信息
#   （实测整页仅 6.5 KB，parse_orcid.py 找不到 "Works (" 而解析出 0 条）。
COUNT_JS = r"""
(() => {
  for (const sel of ['app-work-statement', 'app-work']) {
    const els = [...document.querySelectorAll(sel)]
      .filter(e => ((e.innerText || '').trim().length > 40));
    if (els.length) return els.length;
  }
  return 0;
})()
"""
EXPAND_JS = r"""
(() => {
  const SKEY = '__cdpOrcidExpanded__';
  let seen;
  try {
    seen = window[SKEY];
    if (!seen || typeof seen.has !== 'function') {
      seen = new WeakSet();
      Object.defineProperty(window, SKEY, {value: seen, enumerable: false,
                                          configurable: true, writable: true});
    }
  } catch (e) { seen = new WeakSet(); }
  let n = 0;
  for (const b of document.querySelectorAll(
      'button, a[role="button"], .mat-icon-button, [aria-expanded="false"]')) {
    if (n >= 20) break;
    if (seen.has(b)) continue;
    const t = (b.textContent || '').trim();
    const collapsed = b.getAttribute('aria-expanded') === 'false';
    // ★ 只认**区块级**折叠开关，刻意排除每条的 "Show more detail"（见 orcid_extract.py 说明）
    const blockToggle = /^expand_more$/.test(t) || collapsed;
    const listMore = /^(show more|load more)$/i.test(t);
    if (blockToggle || listMore) {
      try { b.click(); seen.add(b); n++; } catch (e) {}
    }
  }
  return n;
})()
"""


def _expand_and_wait(c, deadline_s: float = 90.0) -> int:
    """展开折叠面板 + 渐进滚动，直到成果卡片数量稳定（0 时继续等）。"""
    t0 = time.time()
    last, stable = -1, 0
    while time.time() - t0 < deadline_s:
        clicked = c.eval(EXPAND_JS) or 0
        c.eval("window.scrollBy(0, Math.max(600, window.innerHeight))")
        time.sleep(1.5)
        n = int(c.eval(COUNT_JS) or 0)
        print(f"  [{time.time() - t0:4.0f}s] 成果卡片={n} 本轮展开={clicked}")
        if n > 0 and n == last and clicked == 0:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = n
    c.eval("window.scrollTo(0, 0)")
    return int(c.eval(COUNT_JS) or 0)


tab = first_page_tab()
c = CDP(tab["webSocketDebuggerUrl"])
try:
    c.navigate(URL, settle=8, load_timeout=40)
    _expand_and_wait(c)
    chunks = []
    for page in range(1, 6):  # 最多5页兜底
        # 滚动加载，等待稳定
        c.eval("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.0)
        txt = c.get_text() or ""
        chunks.append(f"\n===== PAGE {page} =====\n" + txt)
        print(f"page {page}: textlen={len(txt)}")
        # 定位"Works"区块文本说明页码
        m_block = txt[txt.find("Works ("):txt.find("Works (")+40] if "Works (" in txt else ""
        print("  works-info:", m_block.replace("\n", " ")[:40])
        # 点击下一页
        nxt = c.eval(
            """(() => {
                const A = [...document.querySelectorAll('a, button')];
                const el = A.find(x => /next page|chevron_right|>\\s*$|Page 2/i.test((x.textContent||'')+' '+(x.getAttribute('aria-label')||'')));
                if (el) { el.click(); return true; }
                // 尝试分页组件里的 "2"
                const pg = [...document.querySelectorAll('li')];
                const p = pg.find(x => /^\\s*2\\s*$/.test(x.textContent||'') && /page/i.test(x.getAttribute('class')||'') );
                return false;
            })()"""
        )
        clicked = c.eval(
            """(() => {
                const A = [...document.querySelectorAll('a,button')];
                const sel = A.filter(x => (x.getAttribute('aria-label')||'').toLowerCase().includes('next page') || (x.textContent||'').trim()==='>');
                if (sel.length) { sel[sel.length-1].click(); return true; }
                return false;
            })()"""
        )
        print("  next clicked:", clicked)
        if not clicked:
            break
        time.sleep(3.0)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(chunks))
    print("TOTAL saved len:", sum(len(x) for x in chunks))
finally:
    c.close()
