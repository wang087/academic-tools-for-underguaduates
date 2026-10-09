# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 C：CDP 打开 ORCID 记录页，抓取**权威成果集**（含 DOI）
#
# 输出（均落在 <WORKDIR>/.work_cdp/）：
#   orcid_page.txt    整页文本（供 parse_orcid.py 解析）
#   orcid_works.json  逐条成果卡片的 innerText（结构化备份）
#
# 2026-09-18 实测修正（ORCID 3.x 改版 + 一次真实翻车）：
#   ① 元素名：成果卡片已从 `app-work-statement` 变为 **`app-work`**（两代都认）；
#   ② **不要再"点展开"**：曾试过点击 `aria-expanded="false"` 的元素来展开面板，
#      结果点中了导航栏/语言选择器等 13 个无关控件，**把 Angular 应用点坏**
#      （整页文本从 6583 字符掉到 1407，后续所有 eval 全部超时）。
#      实测证明：**干净加载后 `app-work` × 13 本身就带完整文本**
#      （标题/期刊/年份/DOI/CONTRIBUTORS），根本不需要展开 —— 只需要**耐心等**。
#   ③ 退出条件：旧版「没点到按钮且数量没变就 break」在第 0 轮必然成立
#      （首轮计数为 0）→ 立即退出、抓 0 条。改为按**截止时间**轮询，
#      数量为 0 时继续等。
#   ④ 单次 eval 超时不再中断整轮（Angular 重渲染会偶发占满主线程）。
#
# 可移植配置：PAPER_WORKDIR、PAPER_ORCID、CDP_PORT
# 用法：uv run --with websocket-client python orcid_extract.py [<ORCID>]
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys

from logutil import run_root as _run_root  # noqa: E402
WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
AUTHOR = os.environ.get("PAPER_AUTHOR", WORKDIR.name)
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

"""CDP: navigate, wait for ORCID works to render, save full page text + structured JSON."""
import json, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(CDPDIR))
from cdp import CDP, first_page_tab                      # noqa: E402
from logutil import get as get_log, journal              # noqa: E402

# ORCID iD 不硬编码：优先取环境变量 PAPER_ORCID，其次取第一个命令行参数
ORCID_ID = (os.environ.get("PAPER_ORCID") or (sys.argv[1].strip() if len(sys.argv) > 1 else "")).strip()
if not ORCID_ID:
    sys.stderr.write(
        "未指定 ORCID iD。\n"
        "  用法：PAPER_ORCID=0000-0000-0000-0000 uv run --with websocket-client python orcid_extract.py\n"
        "     或：uv run --with websocket-client python orcid_extract.py <ORCID>\n")
    sys.exit(2)
ORCID_ID = ORCID_ID.rstrip("/").split("/")[-1]          # 兼容传入完整 ORCID URL
URL = f"https://orcid.org/{ORCID_ID}"
OUT_TXT = str(CDPDIR / "orcid_page.txt")
OUT_JSON = str(CDPDIR / "orcid_works.json")

LOG = get_log("orcid_extract", wd=WORKDIR)

# ── 页面脚本 ─────────────────────────────────────────────────
# 计数：两代元素名都认，且只数**有实质文本**的卡片（避免数到空壳容器而虚高）
COUNT_JS = r"""
(() => {
  for (const sel of ['app-work', 'app-work-statement']) {
    const els = [...document.querySelectorAll(sel)]
      .filter(e => ((e.innerText || '').trim().length > 40));
    if (els.length) return els.length;
  }
  return 0;
})()
"""

# 温和滚动：window 与各候选滚动容器都推一把（ORCID 的滚动容器不一定是 window）
SCROLL_JS = r"""
(() => {
  const step = Math.max(600, window.innerHeight * 0.9);
  let moved = 0;
  for (const e of [document.scrollingElement, document.documentElement, document.body]) {
    if (!e) continue;
    const before = e.scrollTop;
    e.scrollTop = before + step;
    if (e.scrollTop !== before) moved++;
  }
  try { window.scrollBy(0, step); } catch (e) {}
  return moved;
})()
"""

# 窄口径展开：**仅在记录主体内**找 `expand_more`，且排除 header/nav/menu。
#   实测教训见文件头 ②——宽口径点击会把页面点坏。此脚本默认**不调用**它。
NARROW_EXPAND_JS = r"""
(() => {
  const bad = 'header, nav, mat-menu, .mat-mdc-menu-panel, [role="menu"], ' +
              'app-header, .language-selector, footer';
  let n = 0;
  for (const b of document.querySelectorAll('button, a[role="button"]')) {
    if (n >= 6) break;
    if (b.closest(bad)) continue;                       // 跳过导航/页眉/菜单
    if (!b.closest('main, app-record, .record-body, #main-content')) continue;
    if (!/^expand_more$/.test((b.textContent || '').trim())) continue;
    try { b.click(); n++; } catch (e) {}
  }
  return n;
})()
"""

EXTRACT_JS = r"""
(() => {
  for (const sel of ['app-work', 'app-work-statement']) {
    const els = [...document.querySelectorAll(sel)]
      .filter(e => ((e.innerText || '').trim().length > 40));
    if (els.length) return els.map(e => e.innerText);
  }
  return [];
})()
"""


def _safe_eval(c, expr, timeout=12.0, default=None, tag=""):
    """带容错的 eval —— 单次抖动不应中断整轮（Angular 重渲染会偶发占满主线程）。"""
    try:
        return c.eval(expr, timeout=timeout)
    except Exception as e:
        LOG.warn(f"  页面调用异常（跳过本轮）{tag}: {type(e).__name__}")
        return default


def _wait_for_works(c, gentle_s=45.0, expand_s=30.0):
    """等成果卡片渲染出来。

    分两阶段：
      ① 温和等待（默认 45s）—— 只滚动、**不点击**。实测干净加载后卡片自带全文；
      ② 仅当①仍为 0 时，才做一次**窄口径**展开（只在记录主体内点 expand_more），
         再等 expand_s 秒。
    返回最终计数。
    """
    def poll(seconds, label):
        t0 = time.time()
        last, stable = -1, 0
        while time.time() - t0 < seconds:
            _safe_eval(c, SCROLL_JS, tag="scroll")
            time.sleep(1.5)
            n = int(_safe_eval(c, COUNT_JS, default=0, tag="count") or 0)
            LOG.info(f"  [{label} {time.time()-t0:4.0f}s] 成果卡片={n}")
            if n > 0 and n == last:
                stable += 1
                if stable >= 2:
                    return n
            else:
                stable = 0
            last = n
        return max(last, 0)

    n = poll(gentle_s, "等待")
    if n > 0:
        return n

    LOG.warn("  温和等待后仍为 0 → 尝试一次窄口径展开（仅记录主体内的 expand_more）")
    journal("C", "orcid.expand.narrow", orcid=ORCID_ID,
            clicked=_safe_eval(c, NARROW_EXPAND_JS, default=0, tag="expand"))
    n = poll(expand_s, "展开后")
    return n


tab = first_page_tab()
LOG.info(f"CDP 端口 {os.environ.get('CDP_PORT', '9222')} | ORCID={ORCID_ID}")
journal("C", "orcid.extract.start", orcid=ORCID_ID,
        port=os.environ.get("CDP_PORT", "9222"), script="orcid_extract.py")

c = CDP(tab["webSocketDebuggerUrl"])
t_start = time.time()
try:
    c.navigate(URL, settle=8, load_timeout=40)
    title = c.get_title()
    LOG.info(f"页面标题：{title}")
    journal("C", "orcid.navigate", orcid=ORCID_ID, url=URL, title=title)

    n_works = _wait_for_works(c)
    if n_works:
        LOG.ok(f"成果卡片渲染完成：{n_works} 条"
               f"（耗时 {time.time()-t_start:.0f}s）")
    else:
        LOG.warn("仍未渲染出成果卡片 —— 将保存现有页面文本，"
                 "由 parse_orcid.py 回退到 ORCID API 结果")

    txt = c.get_text() or ""
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(txt)
    LOG.info(f"已保存页面文本：{OUT_TXT}（{len(txt)} 字符）")

    cards = _safe_eval(c, EXTRACT_JS, default=[], tag="extract") or []
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    LOG.info(f"已保存成果卡片：{OUT_JSON}（{len(cards)} 条）")

    journal("C", "orcid.extract.done", orcid=ORCID_ID,
            works=n_works, cards=len(cards), text_len=len(txt),
            elapsed_s=round(time.time() - t_start, 1))

    for i, card in enumerate(cards[:3]):
        LOG.info(f"----- card {i} -----\n{(card or '')[:400]}")

    if not cards:
        LOG.warn("未抓到任何成果卡片。可能原因：ORCID 再次改版（元素名变了）"
                 "或账号侧未公开成果。\n"
                 "   兜底：orcid_probe.py 的 API 结果里已有完整成果（含 DOI），"
                 "parse_orcid.py 会自动回退使用它。")
        journal("C", "orcid.extract.empty", orcid=ORCID_ID,
                reason="no-cards", text_len=len(txt))
except Exception as e:
    LOG.exc(e, "抓取失败：")
    journal("C", "orcid.extract.error", orcid=ORCID_ID,
            error=f"{type(e).__name__}: {e}")
    raise
finally:
    c.close()
    LOG.close()
