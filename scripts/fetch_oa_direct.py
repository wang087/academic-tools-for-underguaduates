# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════
# ⛔⛔ 本脚本自 v4（2026-09-14）起【停用】，保留仅为历史回溯 ⛔⛔
#
# 停用原因：违反【CDP 强制抓取禁令】——
#   「如无特别要求，全部使用 CDP 完成抓取，不得自编脚本从 HTTP 直链获取」。
#   本脚本以纯 HTTP 直取全文（含 OA 文章），正是该禁令所禁止的行为。
#   禁令扩大到**全部站点与全部内容**后，不再需要「按域名维护 BROWSER_ONLY 黑名单」。
#
# 请改用：
#   uv run --with websocket-client --with pypdf python download_all_fulltexts.py \
#       --n=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35
#
# 仍允许纯 HTTP 的只有**元数据 API**（Crossref / ORCID / OpenAlex / Unpaywall /
# Europe PMC REST），那些不是「抓取内容」。
#
# 直接运行本文件会被下方守卫拒绝（退出码 3）。
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import io as _io
import sys as _sys


def _refuse() -> None:
    """拒绝执行（退出码 3）。**两条调用路径都必须经过它**：
      ① `python fetch_oa_direct.py`（__main__ 守卫）
      ② `import fetch_oa_direct; fetch_oa_direct.main()`（main() 内守卫）
    ★ 只挡 ① 是不够的：本文件是「保留备查」的，很容易被 import 后调用，
      那样禁令就被绕过了 —— 而它一旦真的跑起来，就是**违反最高优先级禁令**
      的纯 HTTP 直取全文。
    """
    # 控制台默认 GBK，中文提示会乱码 → 包一层 UTF-8 容错包装
    _err = _io.TextIOWrapper(_sys.stderr.buffer, encoding="utf-8", errors="replace")
    _err.write(
        "\n⛔ fetch_oa_direct.py 已于 v4 停用（违反 CDP 强制抓取禁令）。\n"
        "   全文获取一律经 CDP，请改用：\n"
        "     uv run --with websocket-client --with pypdf python download_all_fulltexts.py \\\n"
        "         --n=3 --limit=20 --delay=25-55 --site-fail-limit=2 --manual-wait=5 --grace=35\n"
        "   详见 SKILL.md §5.1 与 README「CDP 强制抓取禁令」。\n\n")
    _err.flush()
    _sys.exit(3)


if __name__ == "__main__":
    _refuse()

# ─────────────────────────────────────────────────────────────
# 以下为停用前的历史实现（不再执行）
# ─────────────────────────────────────────────────────────────

import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import store                                         # noqa: E402
from logutil import get as get_log, workdir as _wd   # noqa: E402

# ★ 已停用：以下模块级初始化会创建目录、开日志文件。为免「仅 import 也产生副作用」，
#    改为惰性初始化（仍保留旧名以便历史代码引用不报错）。
WD = None
CDPDIR = None
EXPORTS = None
LOG = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Cache-Control": "no-cache",
}

# 直接 HTTP 通常拿不到、必须走浏览器的站点 → 直接跳过，省时间
#
# ★★ 硬性禁令（用户明确要求，2026-09-13）★★
#   以下出版社站点**绝不允许**用纯 HTTP / curl 直连尝试（包括「探活」式请求）：
#   ScienceDirect / Cell Press / OUP / SAGE / Wiley / ACS / RSC / Springer / T&F……
#   它们有人机验证（Cloudflare / DataDome 等），直连既拿不到内容，又是
#   触发风控与封禁的高危行为。此类站点**只允许经 CDP 浏览器**访问
#   （download_all_fulltexts.py / save_pdf_via_browser.py）。
BROWSER_ONLY = ("onlinelibrary.wiley.com", "academic.oup.com", "sciencedirect.com",
                "pubs.acs.org", "pubs.rsc.org", "link.springer.com", "tandfonline.com",
                # 补充：Elsevier 家族与 SAGE 等一律浏览器化
                "cell.com", "linkinghub.elsevier.com", "elsevier.com",
                "sagepub.com", "journals.sagepub.com")


def safe_stem(doi: str, year, title: str | None = None) -> str:
    """文件命名：默认 <year>_<题名>（题名缺失回退 <year>_<DOI slug>）。"""
    return store.stem_for(doi, year, title)


def validate_pdf(p: pathlib.Path, min_pages=2, min_bytes=store.MIN_BYTES):
    return store.validate_pdf(p, min_pages=min_pages, min_bytes=min_bytes)


def host_of(url: str) -> str:
    m = re.match(r"^https?://([^/]+)", url or "")
    h = (m.group(1) if m else "").lower()
    for pre in ("www.", "wwws."):
        if h.startswith(pre):
            h = h[len(pre):]
    return h or "unknown"


class HostPacer:
    """按**落地主机**节流：不同出版社可连续跑，同一出版社必须拉开间隔。

    ★ 实测教训：用全局统一间隔时，同一主机的连续请求（如 nature.com 的
      多篇 OA）会在第 3~4 次开始返回 403 —— 出版社 WAF 是按 host 计数的。
    """

    def __init__(self, lo: float, hi: float, log):
        self.lo, self.hi, self.log = lo, hi, log
        self.last: dict[str, float] = {}

    def wait(self, host: str):
        now = time.time()
        gap = random.uniform(self.lo, self.hi)
        delay = self.last.get(host, 0) + gap - now
        if delay > 0:
            self.log.info(f"    ⏳ 同主机节流 {delay:.0f}s（{host}）")
            time.sleep(delay)
        self.last[host] = time.time()


def download(url: str, out: pathlib.Path, timeout: float = 90, referer: str = "") -> dict:
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read()
            final = r.geturl()
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{str(e)[:70]}"}

    if data[:4] != b"%PDF":
        # 有些站点先给 HTML 再 302，或给的是 landing 页
        if "html" in ctype or data[:5].lower().startswith(b"<!doc") or data[:5].lower().startswith(b"<html"):
            return {"ok": False, "error": f"html-not-pdf({ctype[:30]})"}
        return {"ok": False, "error": f"not-pdf({ctype[:30]})"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    good, why = validate_pdf(out)
    if not good:
        out.unlink(missing_ok=True)
        return {"ok": False, "error": f"invalid:{why}"}
    return {"ok": True, "size": len(data), "pages": why, "final": final}


def main():  # noqa: D401
    # ★ import 后调用也必须被拒绝（见 _refuse 的说明）
    _refuse()
    # 以下代码永不执行（_refuse 会 sys.exit）；保留仅为历史回溯
    if True:  # pragma: no cover
        pass
    argv = sys.argv[1:]
    limit, delay, only = 10 ** 6, (2.0, 6.0), []
    for a in argv:
        if a.startswith("--limit="): limit = int(a.split("=", 1)[1])
        elif a.startswith("--delay="):
            v = a.split("=", 1)[1]; lo, _, hi = v.partition("-")
            delay = (float(lo), float(hi) if hi else float(lo) * 2)
        elif a.startswith("--only="): only = [x for x in a.split("=", 1)[1].split(",") if x]

    up = json.loads((CDPDIR / "unpaywall.json").read_text(encoding="utf-8"))
    cr = json.loads((CDPDIR / "crossref_all.json").read_text(encoding="utf-8"))

    todo = []
    for doi, meta in cr.items():
        if doi.startswith("10.2210/"):
            continue
        if only and doi not in only:
            continue
        if store.existing_pdf(doi, meta.get("year")):
            continue
        u = up.get(doi) or {}
        if not u.get("is_oa"):
            continue
        url = u.get("url_pdf") or u.get("url")
        if not url:
            continue
        if any(b in url for b in BROWSER_ONLY):
            continue                     # 留给 CDP 批次
        todo.append((doi, meta, url, u.get("landing") or url))

    todo.sort(key=lambda t: -(t[1].get("year") or 0))
    todo = todo[:limit]
    if not todo:
        LOG.info("没有可通过 HTTP 直取的 OA 待办")
        return
    LOG.info(f"待直取 OA 论文 {len(todo)} 篇，**同主机**间隔 {delay[0]:.0f}-{delay[1]:.0f}s")

    pacer = HostPacer(delay[0], delay[1], LOG)
    ok = fail = 0
    failed = {}
    for i, (doi, meta, url, landing) in enumerate(todo, 1):
        pacer.wait(host_of(url))
        # ★ 已存在（含历史无年份前缀命名）则跳过，绝不覆盖已下好的文件
        if store.existing_pdf(doi, meta.get("year")):
            LOG.info(f"  ⏭ 已有全文，跳过 {doi}")
            continue
        out = EXPORTS / f"{safe_stem(doi, meta.get('year'), meta.get('title'))}.pdf"
        LOG.step(i, len(todo), f"{doi} ({meta.get('year')})")
        r = download(url, out, referer=landing)
        if r.get("ok"):
            ok += 1
            store.register_pdf(doi, out, WD)
            LOG.ok(f"  ✅ {r['size']//1024} KB | {out.name} [{r['pages']}]")
        else:
            # 退一步：用 landing 页再试一次（部分站点需先建立会话）
            if r.get("error", "").startswith("html-not-pdf"):
                r2 = download(landing, out, referer="https://www.google.com/")
                if r2.get("ok"):
                    ok += 1
                    store.register_pdf(doi, out, WD)
                    LOG.ok(f"  ✅(landing) {r2['size']//1024} KB | {out.name} [{r2['pages']}]")
                    r = r2
            if not r.get("ok"):
                fail += 1
                failed[doi] = {"url": url, "error": r.get("error")}
                LOG.warn(f"  ⚠️ {r.get('error')}")
        if i < len(todo):
            time.sleep(random.uniform(*delay))

    (CDPDIR / "oa_direct_failed.json").write_text(
        json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.info(f"直取完成：成功 {ok} / 失败 {fail}（失败项已写入 oa_direct_failed.json，交由 CDP 兜底）")
    LOG.close()


if __name__ == "__main__":
    main()
