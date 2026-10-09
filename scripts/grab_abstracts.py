# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# CDP 抓取出版社摘要（绕 Cloudflare / 机器人验证）
#
# 本次修复：
#   1) 端口参数化（--port / CDP_PORT），可指定实例；旧版硬编码 9222。
#   2) 挑战判定改用 detect.judge()，不再用「标题含 captcha」这类宽泛匹配
#      —— 正文里出现 "captcha" 也会被旧版判成挑战页并空转 40 轮。
#   3) 单篇硬超时；整轮有预算上限，不会无限卡住。
#   4) `--retry-failed` 的排序 bug 修复：旧版 `int((d and 0) or 0)` 恒为 0，
#      等于完全没排序。现按 Crossref 年份由新到旧。
#   5) 全部动作写日志。
#
# 用法:
#   python grab_abstracts.py "10.1038/xxx,10.1016/yyy" [--limit=3] [--delay=18-45]
#   python grab_abstracts.py --retry-failed [--limit=5]
#   python grab_abstracts.py --port=9223 ...
#
# 可移植配置：PAPER_WORKDIR、CDP_PORT
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import pathlib
import random
import sys
import time


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

import detect                                                   # noqa: E402
from cdp import CDPError, CDPTimeout, connect, is_alive, resolve_port  # noqa: E402
from detect import BLOCKED, CHALLENGE, ERROR, NOT_READY, OK, PAYWALL  # noqa: E402
from logutil import get as get_log, workdir as _wd                # noqa: E402

WD = _wd()
CDPDIR = WD / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
OUT = CDPDIR / "abstracts.json"
LOG = get_log("abstracts", wd=WD)

try:
    DATA: dict = json.loads(OUT.read_text(encoding="utf-8"))
except Exception:
    DATA = {}


def save():
    OUT.write_text(json.dumps(DATA, ensure_ascii=False, indent=1), encoding="utf-8")


# 正文段落兜底选择器（部分出版社不用标准 abstract 容器）
FALLBACK_SEL = ["article p", "section p", "main p", ".content p", "#content p"]

ABSTRACT_JS = r"""
(() => {
  const clean = s => (s||'').replace(/\s+/g,' ').trim();
  const KEY = /(we (report|demonstrate|present|show|describe|design|develop|identify|find)|herein|here we|this (work|study|paper|article)|structural|structure of|crystal structure|cryo-?EM|mechanism|kinetochore|histone|chromatin|nucleosome|complex|reveals?)/i;
  const out = [];
  const SEL = ['section#abstract','div#abstract','#abstract','section.abstract','div.abstract',
    '[data-testid="abstract"]','.article-section__abstract','#Abs1-content','.abstractSection',
    'div.abstract.author','#abstracts','.c-article-section__content','.abstract-content','#abstract-content',
    'section[aria-labelledby*="abstract"]'];
  for (const s of SEL) {
    for (const el of document.querySelectorAll(s)) {
      const t = clean(el.innerText);
      if (t.length >= 120 && KEY.test(t)) { out.push({sel:s, len:t.length, text:t}); break; }
    }
    if (out.length) break;
  }
  if (!out.length) {
    for (const s of %s) {
      for (const el of document.querySelectorAll(s)) {
        const t = clean(el.innerText);
        if (t.length >= 250 && t.length <= 2600 && KEY.test(t)) out.push({sel:s, len:t.length, text:t});
      }
    }
  }
  out.sort((a,b) => b.len - a.len);
  return JSON.stringify(out.slice(0,3));
})()
""" % json.dumps(FALLBACK_SEL)


def extract_abstract(c) -> str:
    try:
        raw = c.eval(ABSTRACT_JS, timeout=20) or "[]"
        cands = json.loads(raw)
        if cands:
            return cands[0]["text"]
    except Exception:
        pass
    try:
        txt = c.get_text(timeout=20) or ""
    except CDPError:
        return ""
    for kw in ("Abstract", "ABSTRACT", "摘要"):
        i = txt.find(kw)
        if i >= 0:
            seg = txt[i:i + 2200]
            if len(seg) > 120:
                return seg
    return ""


def human_pause(lo, hi, log, label=""):
    t = random.uniform(lo, hi)
    log.info(f"    [WAIT] {label}停顿 {t:.0f}s ...")
    time.sleep(t)


def human_scroll(c, steps=3):
    """模拟人类阅读式滚动（同时触发懒加载）。"""
    try:
        for i in range(1, steps + 1):
            c.eval(f"window.scrollTo({{top: document.body.scrollHeight*{i/steps:.2f},"
                   f"behavior:'smooth'}})", timeout=8)
            time.sleep(random.uniform(1.2, 2.6))
    except Exception:
        pass


def grab_one(c, doi, tries=2, delay=(18.0, 45.0)) -> dict:
    """单条抓取。tries 默认仅 2 次，避免连续冲击同一站点。"""
    url = "https://doi.org/" + doi
    last = ""
    for attempt in range(tries):
        try:
            c.navigate(url, settle=random.uniform(3, 6), load_timeout=25)
            r = detect.judge(c, timeout=25)
            st = r["state"]
            LOG.info(f"    状态={st} ({r['reason']}) 文本={r.get('textlen')}")

            if st == BLOCKED:
                return {"title": "", "url": r.get("url", ""), "abstract": "",
                        "error": "SITE-BLOCKED", "markers": r.get("markers")}
            if st == CHALLENGE:
                last = "challenge"
                if attempt < tries - 1:
                    human_pause(delay[0] * 1.2, delay[1] * 1.2, LOG, "挑战重试")
                    continue
                return {"title": "", "url": r.get("url", ""), "abstract": "",
                        "error": "challenge-not-passed"}
            if st in (NOT_READY, ERROR):
                last = r["reason"]
                if attempt < tries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                return {"title": "", "url": r.get("url", ""), "abstract": "",
                        "error": f"{st}:{r['reason']}"}

            human_scroll(c, steps=random.choice([2, 3]))
            txt = extract_abstract(c) or r.get("abstract") or ""
            if not txt and attempt < tries - 1:
                human_pause(delay[0], delay[1], LOG, "空摘要重试")
                continue
            return {"title": r.get("citeTitle") or r.get("title") or "",
                    "url": r.get("url", ""), "journal": r.get("journal", ""),
                    "abstract": txt[:2600], "len": len(txt),
                    "subscribed": r.get("subscribed"), "paywalled": r.get("paywalled")}
        except (CDPTimeout, CDPError) as e:
            last = f"{type(e).__name__}:{str(e)[:60]}"
            LOG.warn(f"    CDP 异常: {last}")
            if attempt == tries - 1:
                return {"title": "", "url": "", "abstract": "", "error": last}
            time.sleep(4)
        except Exception as e:
            last = f"{type(e).__name__}:{str(e)[:60]}"
            if attempt == tries - 1:
                return {"title": "", "url": "", "abstract": "", "error": last}
            human_pause(delay[0], delay[1], LOG)
    return {"title": "", "url": "", "abstract": "", "error": last or "failed"}


def main():
    args = sys.argv[1:]
    limit, delay, port = 3, (18.0, 45.0), resolve_port(args)
    retry_failed = "--retry-failed" in args
    dois: list[str] = []
    for a in list(args):
        if a.startswith("--limit="): limit = int(a.split("=", 1)[1])
        elif a.startswith("--port="): port = int(a.split("=", 1)[1])
        elif a.startswith("--delay="):
            v = a.split("=", 1)[1]; lo, _, hi = v.partition("-")
            delay = (float(lo), float(hi) if hi else float(lo) * 2)
        elif a.startswith("--doi="): dois = [x for x in a.split("=", 1)[1].split(",") if x]
        elif not a.startswith("-") and "," in a:
            dois = [x.strip() for x in a.split(",") if x.strip()]

    if retry_failed:
        try:
            cr = json.loads((CDPDIR / "crossref_all.json").read_text(encoding="utf-8"))
        except Exception:
            cr = {}
        # 修复排序 bug：按 Crossref 年份由新到旧（旧版排序键恒为 0）
        targets = [d for d, v in DATA.items() if not (v.get("abstract") or "").strip()]
        targets.sort(key=lambda d: -((cr.get(d) or {}).get("year") or 0))
    else:
        targets = dois

    if not targets:
        LOG.info("无待抓条目。用法: grab_abstracts.py \"<doi1>,<doi2>\" | --retry-failed")
        return

    targets = targets[:limit]
    if not is_alive(port):
        LOG.error(f"端口 {port} 无 CDP 实例，先运行 cdp_pool.py up")
        return
    LOG.info(f"本次处理 {len(targets)} 条 | 端口 {port} | 间隔 {delay[0]:.0f}-{delay[1]:.0f}s")

    for i, doi in enumerate(targets):
        if i > 0:
            human_pause(delay[0], delay[1], LOG, "条目间")
        LOG.step(i + 1, len(targets), doi)
        try:
            c = connect(port, logger=LOG, timeout=30)
        except Exception as e:
            LOG.error(f"  连接失败: {e}")
            break
        try:
            r = grab_one(c, doi, delay=delay)
        finally:
            try:
                c.close()
            except Exception:
                pass
        if r.get("error") == "SITE-BLOCKED":
            LOG.error(f"[STOP] 检测到封禁文案 —— 立即停止全部后续请求。（{r.get('markers')}）")
            DATA[doi] = r
            save()
            break
        DATA[doi] = r
        save()
        flag = "OK  " if r.get("abstract") else "MISS"
        LOG.info(f"  {flag} {doi} | abs={r.get('len', 0)} | "
                 f"{(r.get('title') or r.get('error') or '')[:60]}")

    have = sum(1 for v in DATA.values() if (v.get("abstract") or "").strip())
    LOG.info(f"完成：库中 {len(DATA)} 条，其中有摘要 {have} 条")
    LOG.close()


if __name__ == "__main__":
    main()
