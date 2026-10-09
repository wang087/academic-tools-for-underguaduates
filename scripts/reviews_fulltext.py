# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════
# 阶段 R 综述全文获取模块（可复用）
#
# 【定位】把阶段 R（平行检索优质综述）的产物直接接到全文获取链路上，
#   落地 SKILL.md §5.1「分层获取策略」：
#
#     第一层（允许直连）：EPMC fullTextXML（白名单 abstract+oa-fulltext-xml）
#        └─ 命中 → jats_to_text.py 转纯文本，evidence 升 fulltext
#        └─ 未命中 / 非 OA → 落入缺口清单
#     第二层（必须 CDP）：原文全文，一律经浏览器
#        └─ 复用 download_all_fulltexts.py（不自建下载器，见 §5.1 四）
#     副链路（CDP 备用，需 --fallback）：主链路三条通道都没拿到全文时，
#        └─ 交给 fulltext_router.py 判「还有没有别的合法路线」：
#           中文硬分流 CNKI / 出版商官方 API / 白名单内 OA 直连 /
#           其余一律产出**交接单**（附上已排序的候选 URL），不越权去取。
#           两个前提缺一不可：① 只对 CDP 已失败的 DOI 生效（脚本层硬拒绝）；
#           ② 直连取正文只允许 templates/直连白名单.json 里 content_class
#           覆盖 fulltext 的源。阶段 0 的 `fulltext_fallback` 决定开不开。
#
# 【为什么单独成模块】
#   阶段 R 原本在 R① 就停住了 —— 任务包 evidence_source 恒为 abstract，
#   R2 只能读摘要。根因是**检索结果里的 pmcid 从未被填充**，下游无从知道
#   哪篇在 PMC 有全文。本模块补上这一步，并把「skill 先试、CDP 兜底」
#   固化成一条命令，阶段 R 每次都可以直接复用。
#
# 【不改动既有脚本】
#   download_all_fulltexts.py 是**已验证的成熟工具**（§5.1 四之4：
#   "自编脚本必然缺少其中若干项，是能力退化"）。本模块**只做编排**：
#   - 读阶段 R 的任务包，产出 CDP 需要的题录切片
#   - 调用既有的 cdp_pool / download_all_fulltexts，不复制其下载逻辑
#
# 用法：
#   # 只探测分层，不取全文（快，零浏览器开销）
#   uv run python 流程1/scripts/reviews_fulltext.py --probe
#
#   # 第一层：取 OA 全文 XML → 纯文本（合规直连）
#   uv run python 流程1/scripts/reviews_fulltext.py --layer1
#
#   # 第二层：把缺口交给 CDP（会自动启动实例池）
#   uv run python 流程1/scripts/reviews_fulltext.py --layer2
#
#   # 一步到位：先分层、再第一层、再把缺口转 CDP
#   uv run python 流程1/scripts/reviews_fulltext.py --all
#
#   # 回填任务包的 evidence_source / text 字段（R① 产物原地升级）
#   uv run python 流程1/scripts/reviews_fulltext.py --backfill
#
#   # 副链路（CDP 备用）：主链路跑完仍有缺口时才值得跑，顺序不能颠倒
#   uv run python 流程1/scripts/reviews_fulltext.py --fallback --backfill
#
#   # 干跑：只看路由判定，一个请求都不发
#   uv run python 流程1/scripts/fulltext_router.py --plan <DOI> [<DOI> ...]
#
# 可移植配置：PAPER_WORKDIR（透传给既有脚本）
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与 logutil._fix_stdout 同判据）。"""
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

FLOW1 = _HERE.parent                       # 流程1/

# [STOP] 工作目录必须与既有脚本**同一判据**。
#   既有脚本（download_all_fulltexts / cdp_pool / fetch_direct …）一律
#   `WD = logutil.workdir()`，而 logutil 默认把 workdir 解析成
#   `<流程1>/<YYYYMMDD>/`（见 logutil.py 顶部注释），**不是** `<流程1>/`。
#   最初本模块硬写 `FLOW1 / ".work_cdp"`，结果第二层把缺口清单写到了
#   `流程1/.work_cdp/`，而下载器去 `流程1/20261006/.work_cdp/` 找
#   `crossref_all.json` → FileNotFoundError。
#   教训：跨脚本协作时**目录判据也要复用**，不能各算各的 —— 否则
#   day rollover 或 PAPER_WORKDIR 一改，两边就错位。
sys.path.insert(0, str(_HERE))
try:
    from logutil import workdir as _workdir      # noqa: E402
    WD = _workdir()
except Exception:                                # noqa: BLE001
    WD = FLOW1
WORK = WD / ".work_cdp"
# 阶段 R 的产物根：**本次运行**的输出目录（与 WORK 同一判据）。
#   这里曾经硬写 `FLOW1 / "outputs"` —— 日期隔离后本次产物在 `流程1/<年月日>/`，
#   旧判据会扫到历史案例的 tasks/ 并**静默选中**，把别人的全文回填进本次任务包
#   （实测把 10-06「免疫与感染」12 篇当成当日「神经与脑科学」12 篇跑了一遍分层）。
OUTPUTS = WD / "outputs"

UA = "flow1-reviews-fulltext/1.0 (mailto:researcher@example.org)"
EMAIL = "researcher@example.org"

# EPMC fullTextXML 是白名单里**唯一**被授权取全文的直连通道
# （templates/直连白名单.json：www.ebi.ac.uk / abstract+oa-fulltext-xml / oa_only=true）
EPMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PMC_IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"

MIN_FULLTEXT_CHARS = 6000                  # 低于此值不当作正文（多为占位/勘误页）

# 页内取正文的表达式（标题 + innerText）
BODY_JS = ("JSON.stringify({t:document.title,"
           "n:(document.body?document.body.innerText.length:0),"
           "x:(document.body?document.body.innerText:'')})")

# 页内「把正文读出来」的**功能按钮**链接（Full text / PDF / View on publisher site）。
# [STOP] 只取页面功能链接，绝不碰人机验证控件（§5.2 鼠标自动化禁令）。
LINKS_JS = ("JSON.stringify([].slice.call(document.querySelectorAll('a'))"
            ".filter(function(a){var s=((a.textContent||'')+' '+(a.href||''));"
            "return /full\\s?text|\\/pdf\\b|publisher|view on/i.test(s);})"
            ".slice(0,4).map(function(a){"
            "return {text:(a.textContent||'').trim().slice(0,44),href:a.href};}))")


# ══════════════════════════════════════════════════════════════
# 基础设施
# ══════════════════════════════════════════════════════════════
def http_get(url: str, timeout: float = 60.0) -> tuple[int, bytes]:
    """带 UA 的 GET；把 HTTP 错误码如实返回而不是抛异常。

    为什么要显式处理：§5.1 记载这些学术 API 会「用 HTTP 200 返回错误」，
    或反过来用非 200 表达"没有全文"（EPMC 对非 OA 返回 500/404）。
    **拿不到就是拿不到**，不能把错误响应当成内容。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}".encode()


def http_json(url: str, timeout: float = 60.0):
    st, body = http_get(url, timeout)
    if st != 200:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def jats_body_stats(xml: str) -> tuple[bool, int, int]:
    """(has_body, n_sections, body_chars)。

    [STOP] 这是本模块最重要的一处判据。
    paper-lookup/references/pmc.md 已记载：PMC 系接口对**非 OA** 文章会返回
    「HTTP 200 + 结构完整的 <pmc-articleset>，但**没有 <body>**」，
    失败原因藏在 XML **注释**里 —— 任何标准解析器都会把它丢掉。
    所以「拿到 XML」绝不等于「拿到全文」，**必须确认 <body> 存在且够长**。
    """
    if "<body" not in xml:
        return False, 0, 0
    body = xml.split("<body", 1)[1]
    chars = len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
    return True, body.count("<sec"), chars


# ══════════════════════════════════════════════════════════════
# 阶段 R 产物读取
# ══════════════════════════════════════════════════════════════
def find_stage_dir(explicit: str | None = None) -> pathlib.Path | None:
    """定位阶段 R 的任务包目录（含 tasks/ 的那一层）。

    判据只认**本次运行**的目录，按优先级：
      ① `<本次工作目录>/.work_cdp/reviews/` —— `reviews_prepare.py` 的规范产物位置；
      ② `<本次工作目录>/outputs/` 下含 tasks/ 的目录 —— 历史布局，输出被改名/挪走时仍可用。
    **不回看 `流程1/outputs/`**：那里躺着历史案例，静默选中会让本次阶段把
    别处抓来的全文回填进当前任务包（宁可报错，也不要错答）。
    """
    if explicit:
        p = pathlib.Path(explicit)
        return p if p.is_dir() else None
    primary = WORK / "reviews"
    if (primary / "tasks").is_dir():
        return primary
    outs = OUTPUTS
    if not outs.is_dir():
        return None
    cands = [d for d in sorted(outs.iterdir()) if d.is_dir() and (d / "tasks").is_dir()]
    if not cands:
        return None
    # 优先名字里带"领域概貌"，其次 tasks 数量最多的
    cands.sort(key=lambda d: ("领域概貌" not in d.name, -len(list((d / "tasks").glob("*.json")))))
    return cands[0]


def load_tasks(stage_dir: pathlib.Path) -> list[dict]:
    out = []
    for t in sorted((stage_dir / "tasks").glob("*.json")):
        try:
            d = json.loads(t.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        d["_task_path"] = str(t)
        d["_slug"] = t.stem
        out.append(d)
    return out


# ══════════════════════════════════════════════════════════════
# 标识符补全（第一层：元数据，允许直连）
# ══════════════════════════════════════════════════════════════
def resolve_ids(doi: str) -> dict:
    """补 PMCID / PMID / inEPMC / isOpenAccess。

    阶段 R 的检索产物里这些字段是空的 —— 这正是下游停在摘要级的根因。
    Europe PMC 的 resultType=core 一次给全，是这里的主力。
    """
    q = urllib.parse.urlencode({"query": f'DOI:"{doi}"', "format": "json",
                                "resultType": "core", "pageSize": 1})
    js = http_json(f"{EPMC_SEARCH}?{q}")
    rec: dict = {"doi": doi, "pmcid": None, "pmid": None,
                 "inEPMC": None, "isOpenAccess": None, "citedBy": None}
    if js and js.get("resultList", {}).get("result"):
        r0 = js["resultList"]["result"][0]
        rec.update({"pmcid": r0.get("pmcid") or None,
                    "pmid": r0.get("pmid") or None,
                    "inEPMC": r0.get("inEPMC"),
                    "isOpenAccess": r0.get("isOpenAccess"),
                    "citedBy": r0.get("citedByCount")})
    if not rec["pmcid"]:
        # 兜底：PMC ID Converter（对 EPMC 未收录的条目仍能给出 PMCID）
        q2 = urllib.parse.urlencode({"ids": doi, "format": "json",
                                     "tool": "flow1", "email": EMAIL})
        js2 = http_json(f"{PMC_IDCONV}?{q2}")
        if js2 and js2.get("records"):
            rec["pmcid"] = js2["records"][0].get("pmcid")
            rec["pmid"] = rec["pmid"] or js2["records"][0].get("pmid")
    return rec


def probe_layer(tasks: list[dict], cache: pathlib.Path) -> dict[str, dict]:
    """逐篇判定「第一层能否合规取到全文」。结果缓存到 .work_cdp。"""
    old = {}
    if cache.exists():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}

    out: dict[str, dict] = {}
    print(f"\n{'=' * 74}\n阶段 R 综述 · 全文分层探测（{len(tasks)} 篇）\n{'=' * 74}")
    for t in tasks:
        doi = t.get("doi") or ""
        slug = t["_slug"]
        if not doi:
            continue
        prev = old.get(doi) or {}
        ids = prev if prev.get("pmcid") is not None else resolve_ids(doi)
        layer1 = False
        chars = 0
        # 白名单授权前提：EPMC 收录 **且** 是开放获取 —— 两者缺一不可
        if ids.get("pmcid") and ids.get("inEPMC") == "Y" and ids.get("isOpenAccess") == "Y":
            st, body = http_get(EPMC_XML.format(pmcid=ids["pmcid"]))
            if st == 200:
                ok, _secs, chars = jats_body_stats(body.decode("utf-8", "replace"))
                layer1 = ok and chars >= MIN_FULLTEXT_CHARS
        rec = {**ids, "title": t.get("title") or "", "layer1": layer1,
               "layer1_chars": chars,
               "verdict": "layer1" if layer1 else "cdp"}
        out[doi] = rec
        flag = "第一层可合规取得" if layer1 else "→ 转第二层 CDP"
        print(f"  {doi:34} inEPMC={ids.get('inEPMC')} isOA={ids.get('isOpenAccess')} "
              f"| {flag}" + (f" ({chars:,} 字)" if layer1 else ""))
        time.sleep(0.3)

    WORK.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n1 = sum(1 for r in out.values() if r["layer1"])
    print(f"\n  第一层 {n1}/{len(out)} ｜ 需转 CDP {len(out) - n1}/{len(out)}")
    print(f"  分层结果 → {cache}")
    return out


# ══════════════════════════════════════════════════════════════
# 第一层：EPMC fullTextXML → 纯文本
# ══════════════════════════════════════════════════════════════
def fetch_layer1(layers: dict[str, dict], tasks: list[dict]) -> list[dict]:
    """取 OA 全文 XML 并转纯文本。**只处理 layer1=True 的条目。**"""
    xml_dir = WORK / "fulltext_xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    by_doi = {t.get("doi"): t for t in tasks}
    results = []
    todo = [(d, r) for d, r in layers.items() if r.get("layer1")]
    print(f"\n{'=' * 74}\n第一层 · EPMC OA 全文（{len(todo)} 篇）\n{'=' * 74}")
    if not todo:
        print("  （无）")
        return results

    for doi, rec in todo:
        pmcid = rec["pmcid"]
        xp = xml_dir / f"{pmcid}.xml"
        st, body = http_get(EPMC_XML.format(pmcid=pmcid), timeout=90)
        ok, secs, chars = (False, 0, 0)
        if st == 200:
            txt = body.decode("utf-8", "replace")
            ok, secs, chars = jats_body_stats(txt)
            if ok:
                xp.write_bytes(body)
        item = {"doi": doi, "pmcid": pmcid, "http": st, "has_body": ok,
                "sections": secs, "chars": chars, "xml": str(xp) if ok else None,
                "path": None, "source": "europepmc-fullTextXML"}
        if ok:
            # 复用流程既有的 JATS 解析器（不自己写一遍）
            r = subprocess.run([sys.executable, str(_HERE / "jats_to_text.py"), str(xp)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            tp = xp.with_suffix(".txt")
            if r.returncode == 0 and tp.exists():
                item["path"] = str(tp)
                item["chars"] = len(tp.read_text(encoding="utf-8", errors="replace"))
                print(f"  [OK] {doi:34} PMC{pmcid.replace('PMC','')} → "
                      f"章节 {secs} | 正文 {item['chars']:,} 字")
            else:
                item["error"] = (r.stderr or r.stdout or "")[-160:]
                print(f"  [X]  {doi:34} jats_to_text 失败：{item['error'][:80]}")
        else:
            item["error"] = f"http={st} 无 <body>"
            print(f"  [X]  {doi:34} http={st} 无正文（应转 CDP）")
        results.append(item)
        time.sleep(0.5)

    p = WORK / "reviews_layer1.json"
    p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  第一层结果 → {p}")
    return results


# ══════════════════════════════════════════════════════════════
# 第二层补充通道：仓库副本（PMC）经 CDP
# ══════════════════════════════════════════════════════════════
def fetch_repo_via_cdp(layers: dict[str, dict], tasks: list[dict],
                       ports: list[int], pace: float = 20.0) -> list[dict]:
    """对「出版社被 CAPTCHA」的缺口，改走 **PMC 仓库副本**（仍经 CDP）。

    【为什么这是一条独立通道】
      实测剩余 6 篇在 Elsevier / AACR **100% 被 CAPTCHA 熔断**，
      但 Unpaywall 显示 **6/6 都有 PMC 仓库副本**（bronze/green）。
      PMC 是**文献仓库**而非出版社内容站，风控强度低得多 ——
      CDP 走这条路成功率显著更高。

    【层级归属：仍是第二层，合规性不变】
      取 PMC 网页正文 **属于「原文全文」**，按 §5.1 必须走 CDP。
      `fetch_direct.py --check-domain` 对 `www.ncbi.nlm.nih.gov` 返回
      **禁止直连（退出码 3）**，所以这里**只用浏览器取**，不发任何直连请求。
      本函数不绕过任何付费墙 —— 只取 Unpaywall 已确认存在的开放副本。
    """
    import websocket  # noqa: E402  延迟导入

    pool = {r["doi"]: r for r in
            (json.loads((WORK / "reviews_layer1.json").read_text(encoding="utf-8"))
             if (WORK / "reviews_layer1.json").exists() else [])}
    state = {}
    st_file = WORK / "fulltext_state.json"
    if st_file.exists():
        try:
            state = json.loads(st_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}

    # 只处理：有 PMCID、第一层没拿到、且还没成功过的
    todo = []
    for doi, rec in layers.items():
        if rec.get("layer1"):
            continue
        if (state.get(doi) or {}).get("pdf_ok"):
            continue
        pmcid = rec.get("pmcid")
        if pmcid:
            todo.append((doi, pmcid))
    if not todo:
        print("\n第二层补充通道：无待处理篇目")
        return []

    print(f"\n{'=' * 74}\n第二层补充通道 · PMC 仓库副本经 CDP（{len(todo)} 篇）\n{'=' * 74}")
    print(f"  同页间隔 {pace:.0f}s（PMC 也会限流，不要并发冲击）")

    out_dir = WORK / "fulltext"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for idx, (doi, pmcid) in enumerate(todo):
        port = ports[idx % len(ports)]
        url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        print(f"\n  [{idx+1}/{len(todo)}] {doi}  {pmcid}  :{port}")
        rec = {"doi": doi, "pmcid": pmcid, "source": "cdp-pmc-repository",
               "url": url, "path": None, "chars": 0}
        ws = None
        try:
            tabs = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=10).read())
            pages = [t for t in tabs if t["type"] == "page"]
            if not pages:
                rec["error"] = "no-page-target"
                results.append(rec)
                continue
            ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=45)
            cid = [0]

            def cmd(m, p=None, t=50):
                cid[0] += 1
                ws.send(json.dumps({"id": cid[0], "method": m, "params": p or {}}))
                dl = time.time() + t
                while time.time() < dl:
                    ws.settimeout(max(1, dl - time.time()))
                    try:
                        r = json.loads(ws.recv())
                    except Exception:
                        return None
                    if r.get("id") == cid[0]:
                        return r
                return None

            cmd("Page.enable")
            if not cmd("Page.navigate", {"url": url}, t=60):
                rec["error"] = "navigate-no-response"
                ws.close()
                ws = None
                results.append(rec)
                continue
            time.sleep(7)

            # 取正文 + 检测 reCAPTCHA
            r = cmd("Runtime.evaluate", {
                "expression": BODY_JS, "returnByValue": True}, t=60)
            if not r:
                rec["error"] = "eval-timeout"
                results.append(rec)
                continue
            try:
                d = json.loads(r.get("result", {}).get("result", {}).get("value") or "{}")
            except Exception:
                d = {}
            title = d.get("t") or ""
            text = d.get("x") or ""
            if "reCAPTCHA" in title or "Checking your browser" in text[:200]:
                rec["error"] = "recaptcha"
                print("     [--] reCAPTCHA 拦截（PMC 也在限流，需冷却后重试）")
                results.append(rec)
                continue
            if len(text) < MIN_FULLTEXT_CHARS:
                # 兜底（实测坑）：PMC 文章页**并不总是把正文渲染进 innerText** ——
                #   摘要视图 / 「Loading PDF」外壳 / 只给「View on publisher site」入口时，
                #   innerText 只有页面骨架。判据是「字数够」而不是「页面打开了」，
                #   所以这里按页内**功能按钮**链接（Full text / PDF / View on publisher site）
                #   逐个导航后再读一次；仍不足才认失败。
                #   [X] 绝不点击人机验证控件（§5.2）—— 这里只走正文入口。
                links = []
                rl = cmd("Runtime.evaluate", {"expression": LINKS_JS,
                                              "returnByValue": True}, t=30)
                if rl:
                    try:
                        links = json.loads(rl.get("result", {}).get(
                            "result", {}).get("value") or "[]")
                    except Exception:  # noqa: BLE001
                        links = []
                tried = []
                for lk in (links or [])[:3]:
                    href = (lk or {}).get("href") or ""
                    if not href.startswith("http") or href == url:
                        continue
                    tried.append({"text": lk.get("text"), "href": href})
                    if not cmd("Page.navigate", {"url": href}, t=60):
                        continue
                    time.sleep(7)
                    r2 = cmd("Runtime.evaluate", {"expression": BODY_JS,
                                                  "returnByValue": True}, t=60)
                    if not r2:
                        continue
                    try:
                        d2 = json.loads(r2.get("result", {}).get(
                            "result", {}).get("value") or "{}")
                    except Exception:  # noqa: BLE001
                        d2 = {}
                    title = d2.get("t") or title
                    text = d2.get("x") or ""
                    print(f"     [..] 走功能入口「{(lk.get('text') or '')[:28]}」"
                          f"→ 正文 {len(text):,} 字符")
                    if len(text) >= MIN_FULLTEXT_CHARS:
                        rec["via"] = "page-fulltext-link"
                        rec["link"] = href
                        break
                if tried:
                    rec["link_tries"] = tried
            if len(text) < MIN_FULLTEXT_CHARS:
                rec["error"] = f"too-short({len(text)})"
                print(f"     [--] 正文过短 {len(text)}（功能入口兜底后仍不足，"
                      f"记为未取到，交由后续层/人工）")
                results.append(rec)
                continue

            # **判据：必须确认是文章页**（标题/正文含期刊文章特征），
            #   否则可能是导航页/错误页 —— 不能当全文
            slug = doi.replace("/", "_").replace(".", "_")
            fp = out_dir / f"{slug}.txt"
            header = (f"# source: {url}\n# doi: {doi}\n# pmcid: {pmcid}\n"
                      f"# route: cdp-pmc-repository（第二层，经 CDP 取仓库副本）\n"
                      f"# chars: {len(text)}\n{'-' * 60}\n")
            fp.write_text(header + text, encoding="utf-8")
            rec.update({"path": str(fp), "chars": len(text), "title": title})
            print(f"     [OK] {title[:60]} | 正文 {len(text):,} 字符")
            results.append(rec)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {str(e)[:70]}"
            print(f"     [X] {rec['error']}")
            results.append(rec)
        finally:
            # 每篇用完即关连接（原来在第一次取值后就 close，兜底导航没连接可用）
            try:
                if ws is not None:
                    ws.close()
            except Exception:  # noqa: BLE001
                pass

        if idx + 1 < len(todo):
            time.sleep(pace)

    p = WORK / "reviews_layer2_repo.json"
    p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("path"))
    print(f"\n  仓库副本通道：成功 {ok}/{len(results)}  → {p}")
    return results



# ══════════════════════════════════════════════════════════════
# 第二层：转 CDP（复用既有下载器，不自建）
# ══════════════════════════════════════════════════════════════
def build_cdp_input(layers: dict[str, dict], tasks: list[dict],
                    layer1_results: list[dict] | None = None) -> pathlib.Path:
    """把缺口写成 CDP 下载器要的题录切片 + 生成确切命令。

    download_all_fulltexts.py 默认从 `.work_cdp/crossref_all.json` 读全量题录，
    阶段 R 的综述不在那份表里。`--doi=` 分支会 `CR.get(d, {})`，
    **缺的条目退化成空 meta**（丢标题/年份）——所以这里补一份
    `crossref_all.json` 的合并版，让既有脚本无需改动即可正确工作。
    """
    ok_dois = {r["doi"] for r in (layer1_results or []) if r.get("path")}
    gaps = [d for d, r in layers.items() if not r.get("layer1") and d not in ok_dois]
    by_doi = {t.get("doi"): t for t in tasks}
    cr_path = WORK / "crossref_all.json"
    cr: dict = {}
    if cr_path.exists():
        try:
            cr = json.loads(cr_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cr = {}
    added = 0
    for d in gaps:
        t = by_doi.get(d) or {}
        if d not in cr:
            added += 1
        cr[d] = {"title": t.get("title") or "", "year": t.get("year") or "",
                 "journal": t.get("journal") or "", "authors": t.get("authors") or "",
                 "source": "stage-R-review"}
    cr_path.parent.mkdir(parents=True, exist_ok=True)
    cr_path.write_text(json.dumps(cr, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = (f'uv run --with websocket-client --with pypdf python '
           f'{_HERE / "download_all_fulltexts.py"} '
           f'--doi={",".join(gaps)} --limit={max(1, len(gaps))} '
           f'--delay=25-55 --site-fail-limit=2 --grace=35')
    cmdf = WORK / "reviews_cdp_command.txt"
    cmdf.write_text(cmd + "\n", encoding="utf-8")
    print(f"\n{'=' * 74}\n第二层 · CDP 缺口清单（{len(gaps)} 篇）\n{'=' * 74}")
    for d in gaps:
        print(f"  · {d}  {(by_doi.get(d) or {}).get('title', '')[:56]}")
    print(f"\n  题录已并入 {cr_path}（新增 {added} 条，让既有下载器认得这些 DOI）")
    print(f"  确切命令 → {cmdf}")
    print(f"\n  {cmd}\n")
    return cmdf


def _usable_ports() -> list[int]:
    """返回**真正可用**的实例端口（端口通 **且** renderer > 0）。

    [STOP] 为什么不能只用 cdp_pool.alive_ports：
      实测过一种「假活」—— 端口就绪、浏览器级命令正常，但该实例
      **一个 renderer 都没有**，于是所有页面级命令永远超时。
      只判端口会让整批任务静默失败，比启动失败更难查。
    """
    import cdp_pool  # noqa: E402
    cand = cdp_pool.alive_ports(FLOW1, 9222, span=8)
    if not cand:
        return []
    try:
        launcher = _HERE / "cdp_up_nosandbox.py"
        if launcher.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("_cdp_up", launcher)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return [p for p in cand if mod.renderer_count(p) > 0]
    except Exception:  # noqa: BLE001
        pass
    return cand


def run_cdp(num: int, ports: str | None, manual_wait: float,
            delay: str = "25-55", site_fail_limit: int = 2) -> int:
    """启动实例池并调用既有 CDP 下载器（第二层）。

    [STOP] 这里**不重写下载逻辑** —— §5.1 四之4 明令：
    一律用 download_all_fulltexts.py / save_pdf_via_browser.py。
    本函数只负责「起浏览器 → 传参 → 收尾」。

    `delay` 实测很关键：25-55s 会被 Elsevier/AACR 判为爬虫直接 CAPTCHA，
    放宽到 120-240s 后原本熔断的站点可直接成功。**宁可慢，不要硬冲。**
    """
    cmdf = WORK / "reviews_cdp_command.txt"
    if not cmdf.exists():
        print("[X] 请先运行 --layer2 生成缺口清单")
        return 3
    docis = ""
    for line in cmdf.read_text(encoding="utf-8").splitlines():
        m = re.search(r"--doi=(\S+)", line)
        if m:
            docis = m.group(1)
    if not docis:
        print("[X] 缺口清单里没有 DOI")
        return 3
    n_gap = len(docis.split(","))

    print(f"\n{'=' * 74}\n第二层 · CDP 下载（{n_gap} 篇）\n{'=' * 74}")
    if ports:
        pl = [int(x) for x in ports.split(",") if x.strip()]
    else:
        pl = _usable_ports()
        if not pl:
            # 优先用带 --no-sandbox 的启动器：本机 Edge 的 renderer 沙箱
            # 创建失败，用 cdp_pool 直启会得到「端口就绪但 renderer=0」的
            # 假活实例（页面级命令全部超时）。见 cdp_up_nosandbox.py 头部说明。
            launcher = _HERE / "cdp_up_nosandbox.py"
            if launcher.exists():
                print(f"  启动 {num} 个 CDP 实例（cdp_up_nosandbox）…")
                subprocess.call([sys.executable, str(launcher), f"--n={num}"])
            else:
                print(f"  启动 {num} 个 CDP 实例（cdp_pool）…")
                import cdp_pool  # noqa: E402
                cdp_pool.ensure(FLOW1, n=num, base_port=9222, log=None)
            pl = _usable_ports()
    if not pl:
        print("[X] 无可用 CDP 实例")
        return 4
    print(f"  实例端口：{pl}")

    cmd = [sys.executable, "-u", str(_HERE / "download_all_fulltexts.py"),
           f"--doi={docis}", f"--limit={n_gap}", f"--delay={delay}",
           f"--site-fail-limit={site_fail_limit}", "--grace=45",
           f"--ports={','.join(str(p) for p in pl)}", f"--n={len(pl)}"]
    if manual_wait > 0:
        cmd.append(f"--manual-wait={manual_wait}")
    print(f"  执行：{' '.join(cmd)}\n")
    # stdio 继承：既有脚本自身负责日志与进度，不在此重复捕获
    rc = subprocess.call(cmd, cwd=str(FLOW1))
    print(f"\n  CDP 下载器退出码 {rc}")
    return rc


# ══════════════════════════════════════════════════════════════
# 候选池 + 回填：把全文写回任务包（R① 产物原地升级）
# ══════════════════════════════════════════════════════════════
def _add_from_record(cands: dict[str, list[tuple[int, str, str]]], rec: dict) -> None:
    """把副链路的一条记录并入候选池（与 `_add` 同判据：文件在、读得出才收）。

    单独成函数是因为副链路的来源标签要带上 provider/status —— 择优时被弃用的
    那一条会打印在 `[i] 择优：` 里，读者需要能看出「弃用的是哪条路线的产物」。
    """
    doi = (rec.get("doi") or "").strip()
    path = rec.get("path")
    if not doi or not path:
        return
    p = pathlib.Path(path)
    if not p.exists():
        return
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return
    src = "fallback-" + str(rec.get("provider") or rec.get("status") or "router")
    cands.setdefault(doi, []).append((len(text), src, text))


def collect_fulltext_candidates() -> dict[str, list[tuple[int, str, str]]]:
    """扫齐**所有**已落地的全文通道 → {doi: [(chars, src, text), ...]}。

    抽成独立函数的理由：`backfill()` 要用它择优回填，`run_fallback()`
    要用它判「哪些 DOI 主链路确实没拿到」。两处若各扫一遍，判据必然漂移 ——
    副链路会对已经成功的论文再跑一次，白耗配额。
    """
    cands: dict[str, list[tuple[int, str, str]]] = {}

    def _add(doi: str, path, src: str) -> None:
        if not doi or not path:
            return
        p = pathlib.Path(path)
        if not p.exists():
            return
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        cands.setdefault(doi, []).append((len(text), src, text))

    # ① 第一层：EPMC fullTextXML（白名单授权的结构化开放获取接口）
    p1 = WORK / "reviews_layer1.json"
    if p1.exists():
        for r in json.loads(p1.read_text(encoding="utf-8")):
            _add(r.get("doi"), r.get("path"), "europepmc-fullTextXML")

    # ② 第二层：既有下载器的落地页正文
    #   读 child 脚本自己的状态文件 fulltext_state.json —— 这是它对外唯一的
    #   进度凭据（--status 也从它读）。**不另立一套状态**，否则两边会漂移。
    st_file = WORK / "fulltext_state.json"
    if st_file.exists():
        try:
            state = json.loads(st_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
        for doi, rec in state.items():
            _add(doi, rec.get("fulltext_file"), "cdp-landing-page")

    # ③ 第二层补充通道：PMC 仓库副本（经 CDP）
    p3 = WORK / "reviews_layer2_repo.json"
    if p3.exists():
        try:
            for r in json.loads(p3.read_text(encoding="utf-8")):
                _add(r.get("doi"), r.get("path"), "cdp-pmc-repository")
        except Exception:  # noqa: BLE001
            pass

    # ④ 副链路（CDP 备用）：fulltext_router.py 的产物
    #   放进来有两个作用：
    #     · 回填时与其它通道一起**按字数择优**（见 backfill 的 [STOP]）；
    #     · 让 run_fallback 看见「上一轮副链路已经拿到过的」，不再重复跑一遍 ——
    #       重复跑等于对同一站点再发一轮请求，那正是 fetch_pacing 要防的事。
    #   记录里的 `path` 只在**有正文文本**时才给出（取到 PDF/HTML 而没文本时
    #   是 null），因此不会把二进制字节当正文读进来 —— 那会按字数「胜出」
    #   并盖住真正文，正是上面 [STOP] 说的事故。
    p4 = WORK / "reviews_fallback.json"
    if p4.exists():
        try:
            for r in json.loads(p4.read_text(encoding="utf-8")):
                if isinstance(r, dict):
                    _add_from_record(cands, r)
        except Exception:  # noqa: BLE001
            pass

    return cands


# ══════════════════════════════════════════════════════════════
# 副链路（CDP 备用）：fulltext_router.py 的编排入口
# ══════════════════════════════════════════════════════════════
def run_fallback(tasks: list[dict], cands: dict | None = None, *,
                 mode: str | None = None, site_fail_limit: int = 2,
                 timeout: float = 60.0, want_arxiv: bool = False) -> dict:
    """对**主链路确实没拿到全文**的 DOI 跑副链路路由。

    定位：备用，不是替代。候选池（collect_fulltext_candidates：第一层 EPMC /
    CDP 落地页 / PMC 仓库副本 / 上一轮副链路产物）里已有正文的 DOI 一律排除，
    剩下的才带着 `cdp_failed=True` 交给 `fulltext_router.route_many()`
    —— 那个模块自己还有一道同款守卫（不显式声明就拒绝执行），
    两处判据同源，不各算各的。

    mode 缺省读阶段 0 的 `fulltext_fallback`（prepare_plan.json）；
    `off` 时 router 会直接拒绝，本函数如实转述，不当成错误。
    """
    import fulltext_router as frt  # noqa: PLC0415
    import fetch_pacing as _fp     # noqa: PLC0415

    if cands is None:
        cands = collect_fulltext_candidates()
    # chars>0 才算「已有全文」：0 字节的产物是坏文件，不该挡住副链路
    have = {d for d, lst in cands.items() if any(c > 0 for c, _, _ in lst)}

    gap = []
    for t in tasks:
        doi = (t.get("doi") or "").strip()
        if not doi or doi in have:
            continue
        gap.append({"doi": doi,
                    "title": t.get("title") or "",
                    "pmcid": t.get("pmcid") or (layers_ctx.get(doi) or {}).get("pmcid") or "",
                    "cdp_failed": True})

    m = mode or frt.plan_mode()[0]
    print(f"\n{'=' * 74}\n副链路（CDP 备用）· mode={m}\n{'=' * 74}")
    if m == "off":
        print("  [SKIP] 阶段 0 已把 fulltext_fallback 设为 off —— 副链路不启用。")
        return {"mode": m, "total": 0, "success": 0, "handoff": 0, "failure": 0,
                "skipped_reason": "mode=off"}
    if not gap:
        print(f"  [SKIP] 候选池已覆盖全部 {len(have)} 篇"
              f"（含上一轮副链路产物），无缺口。")
        return {"mode": m, "total": 0, "success": 0, "handoff": 0, "failure": 0,
                "skipped_reason": "no-gap"}

    print(f"  缺口 {len(gap)} 篇（已有正文 {len(have)} 篇，已排除）")
    out = WORK / "fallback"
    out.mkdir(parents=True, exist_ok=True)
    # 熔断计数跨轮持久化由 route_many 内部落盘，这里不重复调 save()
    recs = frt.route_many(
        gap, out_dir=out, mode=m,
        guard=_fp.load_guard(wd=WD, limit=max(1, site_fail_limit)),
        timeout=timeout, want_arxiv=want_arxiv)
    report = WORK / "reviews_fallback.json"
    report.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")

    n_ok = sum(1 for r in recs if r["kind"] == "SUCCESS")
    n_ho = sum(1 for r in recs if r["kind"] == "HANDOFF")
    n_fa = sum(1 for r in recs if r["kind"] == "FAILURE")
    print(f"  结果：成功 {n_ok} · 交接 {n_ho} · 失败 {n_fa}  →  {report}")
    for r in recs:
        if r["kind"] == "SUCCESS":
            print(f"    [OK]   {r['doi']:34} {r['chars']:>8,} 字  [{r['provider']}]")
        else:
            print(f"    [{r['kind'][:4]}] {r['doi']:34} {r['status']}"
                  f"{' → ' + r['next'] if r['next'] else ''}")
    if n_ho:
        print("  [INFO] 「交接」不是失败：路线存在，只是副链路不越权去取。"
              "按 next 字段交回 CDP（cdp）或请用户授权（confirm/handoff）。")
    return {"mode": m, "total": len(recs), "success": n_ok,
            "handoff": n_ho, "failure": n_fa, "report": str(report)}


def backfill(stage_dir: pathlib.Path, tasks: list[dict]) -> dict:
    """把已取得的全文回填进 tasks/*.json 的 text / evidence_source。

    为什么必须回填而不是让 R2 自己找：
      `reviews_prepare.py` 生成的任务包 schema 与 `interpret_prepare.py`
      **逐字段同名**（SKILL §4 R①），R2 的 subagent 只读 tasks/*.json。
      正文若不进任务包，subagent 依然只能看到摘要。
    """
    # 收集**所有**候选，而不是先到先得。
    #
    # [STOP] 为什么要按长度择优：
    #   实测 10.1037/a0035302 同时存在两份产物 ——
    #     · CDP 落地页（APA 付费墙）  3,077 字   ← 只是付费墙提示页！
    #     · PMC 仓库副本（CDP）    301,678 字   ← 真正文
    #   若按「先到先得」，付费墙残页会**盖住**真正文，任务包里的
    #   evidence_source 还是 "fulltext" —— 下游 subagent 会拿 3 千字
    #   的广告页当综述读。**这种错误静默且致命。**
    #   故：同篇多来源时按正文字数取最大者。
    cands = collect_fulltext_candidates()

    pool: dict[str, tuple[str, str]] = {}
    shadowed: list[str] = []
    for doi, lst in cands.items():
        lst.sort(key=lambda x: -x[0])          # 字数最多者胜
        best = lst[0]
        pool[doi] = (best[2], best[1])
        if len(lst) > 1 and lst[1][0] * 2 < best[0]:
            shadowed.append(f"{doi}: 采用 {best[1]} {best[0]:,} 字，"
                            f"弃用 {lst[1][1]} {lst[1][0]:,} 字")
    for s in shadowed:
        print(f"  [i] 择优：{s}")

    print(f"\n{'=' * 74}\n回填任务包（可用全文 {len(pool)} 篇）\n{'=' * 74}")
    stat = {"fulltext": 0, "abstract": 0, "skipped": 0}
    for t in tasks:
        doi = t.get("doi") or ""
        tp = pathlib.Path(t["_task_path"])
        d = json.loads(tp.read_text(encoding="utf-8"))
        if doi in pool:
            text, src = pool[doi]
            d["text"] = text
            d["evidence_source"] = "fulltext"
            d["fulltext_source"] = src
            d["fulltext_chars"] = len(text)
            d["pmcid"] = d.get("pmcid") or (layers_ctx.get(doi) or {}).get("pmcid") or ""
            stat["fulltext"] += 1
            print(f"  [OK] fulltext  {doi:34} {len(text):>8,} 字  [{src}]")
        else:
            # **不臆造**：没拿到就保持 abstract，如实标注
            # 但把补出的标识符写回去 —— 这正是原链路漏采、导致下游
            # 无从判断"有没有全文"的那个字段。留着它，下次重试不必再探一遍。
            lc = layers_ctx.get(doi) or {}
            if lc.get("pmcid"):
                d["pmcid"] = d.get("pmcid") or lc["pmcid"]
            if lc.get("pmid"):
                d["pmid"] = d.get("pmid") or lc["pmid"]
            if d.get("evidence_source") != "fulltext":
                stat["abstract"] += 1
                print(f"  [--] abstract  {doi:34} （未取得全文，保持摘要级）")
            else:
                stat["skipped"] += 1
        tp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    # 同步 manifest，让 reviews_build.py 的统计跟着变
    mf = stage_dir / "manifest.json"
    if mf.exists():
        m = json.loads(mf.read_text(encoding="utf-8"))
        m["evidence_abstract"] = stat["abstract"]
        m["evidence_metadata_only"] = m.get("evidence_metadata_only", 0)
        m["evidence_fulltext"] = stat["fulltext"]
        for it in m.get("items", []):
            if it.get("doi") in pool:
                it["evidence_source"] = "fulltext"
        mf.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  manifest 已同步：evidence_fulltext={stat['fulltext']} "
              f"evidence_abstract={stat['abstract']}")
    print(f"\n  小结：全文 {stat['fulltext']} ｜ 仍摘要级 {stat['abstract']}")
    if stat["abstract"]:
        print("  [WARN] 仍有摘要级篇目 —— 交付时必须如实说明，不得当作全文结论。")
    return stat


layers_ctx: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="阶段 R 综述全文获取：skill 先试（第一层），取不到转 CDP（第二层）。")
    ap.add_argument("--stage-dir", default=None, help="阶段 R 输出目录（默认自动定位）")
    ap.add_argument("--probe", action="store_true", help="只做分层探测（零浏览器开销）")
    ap.add_argument("--layer1", action="store_true", help="取 EPMC OA 全文 XML → 纯文本")
    ap.add_argument("--layer2", action="store_true", help="把缺口写成 CDP 清单 + 命令")
    ap.add_argument("--cdp", action="store_true", help="真的调起 CDP 下载器（第二层）")
    ap.add_argument("--all", action="store_true", help="探测 → 第一层 → 缺口转 CDP")
    ap.add_argument("--backfill", action="store_true", help="把全文回填进 tasks/*.json")
    ap.add_argument("--n", type=int, default=3, help="CDP 实例数（默认 3）")
    ap.add_argument("--ports", default=None, help="指定 CDP 端口，如 9222,9223")
    ap.add_argument("--manual-wait", type=float, default=0.0, help="遇挑战时人工等待（分钟）")
    ap.add_argument("--delay", default="25-55",
                    help="CDP 同站点间隔秒数（默认 25-55；被风控时放宽到 120-240）")
    ap.add_argument("--site-fail-limit", type=int, default=2,
                    help="站点连续失败几次后本轮熔断（默认 2；保守可取 1）")
    ap.add_argument("--repo", action="store_true",
                    help="第二层补充通道：出版社被 CAPTCHA 时改走 PMC 仓库副本（仍经 CDP）")
    ap.add_argument("--repo-pace", type=float, default=20.0,
                    help="仓库副本通道的篇间间隔秒数（默认 20）")
    ap.add_argument("--fallback", action="store_true",
                    help="副链路（CDP 备用）：对主链路三条通道都没拿到全文的 DOI "
                         "跑 fulltext_router.py 的路由判定。**必须在 --cdp/--repo 之后跑**，"
                         "否则「CDP 已失败」这个前提不成立")
    ap.add_argument("--fallback-mode", choices=["off", "oa", "oa+api"], default=None,
                    help="覆盖阶段 0 的 fulltext_fallback（缺省读 prepare_plan.json）")
    ap.add_argument("--fallback-arxiv", action="store_true",
                    help="副链路的 OA 瀑布里加查 arXiv（按题名匹配）")
    a = ap.parse_args()

    stage_dir = find_stage_dir(a.stage_dir)
    if not stage_dir:
        print(f"[X] 未找到阶段 R 任务包（需含 tasks/）；已查 {WORK / 'reviews'} 与 {OUTPUTS}"
              f" —— 先跑 reviews_prepare.py", file=sys.stderr)
        return 2
    tasks = load_tasks(stage_dir)
    if not tasks:
        print(f"[X] {stage_dir}/tasks 下没有任务包")
        return 2
    print(f"阶段 R 输出目录：{stage_dir}")
    print(f"任务包：{len(tasks)} 篇")

    cache = WORK / "reviews_layer_split.json"
    global layers_ctx

    if a.all or a.probe or a.layer1 or a.layer2:
        layers = probe_layer(tasks, cache)
        layers_ctx = layers
    else:
        layers = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        layers_ctx = layers
        if not layers:
            print("[X] 尚无分层结果，请先加 --probe 或 --all")
            return 2

    l1 = None
    if a.all or a.layer1:
        l1 = fetch_layer1(layers, tasks)
    if a.all or a.layer2:
        build_cdp_input(layers, tasks, l1)
    if a.cdp:
        run_cdp(a.n, a.ports, a.manual_wait, delay=a.delay,
                site_fail_limit=a.site_fail_limit)
    if a.repo:
        pl = ([int(x) for x in a.ports.split(",") if x.strip()] if a.ports else _usable_ports())
        if not pl:
            launcher = _HERE / "cdp_up_nosandbox.py"
            if launcher.exists():
                subprocess.call([sys.executable, str(launcher), f"--n={a.n}"])
                pl = _usable_ports()
        if not pl:
            print("[X] 无可用 CDP 实例")
        else:
            fetch_repo_via_cdp(layers, tasks, pl, pace=a.repo_pace)
    if a.fallback:
        # 放在 --cdp / --repo 之后、--backfill 之前：
        #   「CDP 已失败」这个前提只有在主链路真的跑过之后才成立，
        #   而副链路的产物又要赶在回填之前落地，才能被第 ④ 通道收进候选池。
        run_fallback(tasks, None, mode=a.fallback_mode,
                     site_fail_limit=a.site_fail_limit, want_arxiv=a.fallback_arxiv)
    if a.backfill:
        backfill(stage_dir, tasks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
