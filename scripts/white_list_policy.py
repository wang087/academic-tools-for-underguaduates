# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 白名单策略的**运行时消费**（fail closed）
#
# 为什么需要这个模块
#   对照报告 §7.1 的第 1 条（高优先级缺口）：
#     `直连白名单.json` 里的 `content_class` / `oa_only` 这两个字段
#     **只被 `fetch_direct.py --self-test` 读**，运行时没有任何消费者。
#     也就是说「表外一律拒绝」与「含全文 XML 的源必须 oa_only」
#     在批量路径上是**构造性保证**（靠各源函数里硬编码的 URL），
#     不是运行时守卫 —— 新增一个源只需写一个新 URL 即可越过声明。
#   本模块把这两个字段变成**可调用、可断言**的判据，供新代码在
#   发请求**之前**调用。既有脚本保持原状（不接线），因此不改动既有行为。
#
# 判据来源（不另立标准）
#   · 域名匹配用 `fetch_direct.domain_allowed()` —— 后缀边界匹配，
#     防 `api.crossref.org.attacker.com` 这类子串陷阱。**本模块不重写它。**
#   · 白名单文件路径用 `fetch_direct.WHITELIST`（= templates/直连白名单.json）。
#   · 三态 content_class 与 oa_only 的规则抄自白名单文件头部的 `_说明`。
#
# 语义
#   requested 与 allowed 的关系是**集合包含**，不是相等：
#     · 请求 fulltext，但该域名只允许 abstract  → 拒绝（越界）
#     · 请求 abstract，而该域名允许到 fulltext  → 允许（取更低层级没问题）
#   `oa_only=true` 的源只应取开放获取子集 —— 因此请求方必须**显式声明**
#   `is_oa=True`；未声明一律拒绝（宁可不取，也不越界）。
#
# 用法：
#   from white_list_policy import decide, audit_whitelist
#   d = decide("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML", "fulltext")
#   d["allowed"]   # → True
#   d = decide("https://www.nature.com/articles/x", "fulltext")
#   d["allowed"]   # → False，reason 说明应走 CDP
#
#   uv run python scripts/white_list_policy.py --self-test
#   uv run python scripts/white_list_policy.py --check-domain <URL> --want fulltext
#   uv run python scripts/white_list_policy.py --audit
#
# 退出码：0 = 允许 / 拒绝时也为 0（结论靠输出）；1 = 自检失败；2 = 用法错误
#         `--check-domain` 拒绝时 **3**（与 fetch_direct.py --check-domain 一致）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import pathlib
import sys
import urllib.parse

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


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


# ══════════════════════════════════════════════════════════════
# 层级定义
# ══════════════════════════════════════════════════════════════
# content_class 的三个合法取值（白名单文件头的 _说明 已定死）
VALID_CONTENT_CLASSES = ("metadata", "abstract", "abstract+oa-fulltext-xml")

# 请求侧可声明的层级。顺序即「包含关系」：fulltext 包含 abstract 包含 metadata
REQUEST_LEVELS = ("metadata", "abstract", "fulltext")

# 白名单某 content_class **能覆盖**哪些请求层级
_COVERS: dict[str, frozenset[str]] = {
    "metadata": frozenset({"metadata"}),
    "abstract": frozenset({"metadata", "abstract"}),
    "abstract+oa-fulltext-xml": frozenset({"metadata", "abstract", "fulltext"}),
}

# 拒绝时给出「下一步该走哪条路」——与 SKILL §5.1 的回退规则一致
NEXT_BROWSER = ("browser", "按 §5.1：原文全文属第二层，改走 CDP"
                           "（download_all_fulltexts.py / save_pdf_via_browser.py）")
NEXT_DROP = ("drop", "该域名未收录为可直连源。不要去猜它算不算「开源数据库」——"
                     "要收录请按白名单的收录判据改 templates/直连白名单.json "
                     "并让 fetch_direct.py --self-test 通过")


def load_entries() -> tuple[list[dict], pathlib.Path | None]:
    """读白名单条目。**复用** fetch_direct 的读取实现，不另写一份。"""
    try:
        import fetch_direct as fd  # noqa: PLC0415
        return fd.load_whitelist(), pathlib.Path(getattr(fd, "WHITELIST", "")) or None
    except Exception:  # noqa: BLE001
        # 兜底：直接读文件（格式与 fetch_direct 完全一致）
        p = pathlib.Path(__file__).resolve().parents[1] / "templates" / "直连白名单.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return [s for s in (d.get("sources") or []) if isinstance(s, dict)], p
        except Exception:  # noqa: BLE001
            return [], p


def domain_allowed(url: str, entries: list[dict]) -> tuple[bool, str]:
    """复用 fetch_direct 的后缀边界匹配；不可用时退回等价实现。"""
    try:
        import fetch_direct as fd  # noqa: PLC0415
        return fd.domain_allowed(url, entries)
    except Exception:  # noqa: BLE001
        try:
            host = (urllib.parse.urlparse(url).hostname or "").lower()
        except Exception:  # noqa: BLE001
            return False, "URL 无法解析"
        if not host:
            return False, "URL 没有主机名"
        for e in entries:
            d = str(e.get("domain") or "").lower().strip()
            if d and (host == d or host.endswith("." + d)):
                return True, d
        return False, f"{host} 不在直连白名单内"


def _entry_for(url: str, entries: list[dict]) -> dict | None:
    ok, hit = domain_allowed(url, entries)
    if not ok:
        return None
    for e in entries:
        if str(e.get("domain") or "").lower().strip() == hit:
            return e
    return None


# ══════════════════════════════════════════════════════════════
# 唯一判据入口
# ══════════════════════════════════════════════════════════════
def decide(url: str, want: str, *, is_oa: bool | None = None,
           entries: list[dict] | None = None) -> dict:
    """→ 决策字典。

    键：
      allowed       是否允许直连
      host          URL 主机名
      matched      命中的白名单域名（未命中为空）
      content_class 该域名允许的层级（未命中为空）
      oa_only       该域名是否只应取 OA 子集
      reason        人类可读理由
      next          拒绝时的下一步：browser（改走 CDP）/ drop（不收录）
      remediation   拒绝时的可执行提示
    """
    ents = entries if entries is not None else load_entries()[0]
    want_norm = str(want or "").strip().lower()
    if want_norm not in REQUEST_LEVELS:
        return {"allowed": False, "host": _host(url), "matched": "", "content_class": "",
                "oa_only": False, "next": "drop",
                "reason": f"未知的请求层级：{want!r}（合法值：{'/'.join(REQUEST_LEVELS)}）",
                "remediation": "调用方必须显式声明要取哪一层，不接受默认值"}

    host = _host(url)
    ent = _entry_for(url, ents)
    if ent is None:
        ok, why = domain_allowed(url, ents)
        return {"allowed": False, "host": host, "matched": "", "content_class": "",
                "oa_only": False, "next": NEXT_BROWSER[0] if want_norm == "fulltext" else NEXT_DROP[0],
                "reason": why,
                "remediation": NEXT_BROWSER[1] if want_norm == "fulltext" else NEXT_DROP[1]}

    cc = str(ent.get("content_class") or "").strip()
    oa_only = bool(ent.get("oa_only"))
    base = {"host": host, "matched": str(ent.get("domain") or ""),
            "content_class": cc, "oa_only": oa_only}

    if cc not in VALID_CONTENT_CLASSES:
        return {**base, "allowed": False, "next": "drop",
                "reason": f"白名单条目 content_class 非法：{cc!r}",
                "remediation": "合法值只有 " + " / ".join(VALID_CONTENT_CLASSES)}

    if want_norm not in _COVERS[cc]:
        # 越界请求：这是本模块存在的核心理由
        next_key, remed = (NEXT_BROWSER if want_norm == "fulltext" else NEXT_DROP)
        return {**base, "allowed": False, "next": next_key,
                "reason": f"{host} 只允许取到 {cc}，不能取 {want_norm}",
                "remediation": remed}

    if oa_only and is_oa is not True:
        # [STOP] oa_only 的源**必须**由调用方显式声明 is_oa=True。
        #   不声明 = 不取 —— 宁可少拿一篇，也不越界取一篇非 OA 正文。
        return {**base, "allowed": False, "next": "drop",
                "reason": f"{host} 声明 oa_only=true，但调用方未声明 is_oa=True"
                          f"（当前 is_oa={is_oa!r}）",
                "remediation": "确认该文章确为开放获取后再调用（is_oa=True）"}

    return {**base, "allowed": True, "next": "", "reason": "允许直连",
            "remediation": ""}


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


# ══════════════════════════════════════════════════════════════
# 白名单体检（把 fetch_direct --self-test 的三条策略断言变成可复用函数）
# ══════════════════════════════════════════════════════════════
_PUBLISHER_HOST_RE = (r"(nature|science|cell|wiley|elsevier|springer|acs\.|rsc\.|"
                      r"oup|tandfonline|sagepub|sciencedirect|ieee|acm\.org|aps\.org)")


def audit_whitelist(entries: list[dict] | None = None) -> dict:
    """白名单自身的合规体检（与 fetch_direct --self-test 同判据，可单独调用）。

    三条判据（缺一即为「策略声明被破坏」）：
      1. 不得收录任何出版社内容站；
      2. content_class 必须是三种合法值之一；
      3. 含 oa-fulltext-xml 的源必须 oa_only=true。
    """
    ents = entries if entries is not None else load_entries()[0]
    import re  # noqa: PLC0415

    bad_pub = [str(e.get("domain") or "") for e in ents
               if re.search(_PUBLISHER_HOST_RE, str(e.get("domain") or ""), re.I)]
    bad_cc = [str(e.get("domain") or "") for e in ents
              if str(e.get("content_class") or "") not in VALID_CONTENT_CLASSES]
    bad_oa = [str(e.get("domain") or "") for e in ents
              if "oa-fulltext-xml" in str(e.get("content_class") or "")
              and not e.get("oa_only")]
    return {
        "count": len(ents),
        "publisher_content_hosts": bad_pub,
        "illegal_content_class": bad_cc,
        "oa_fulltext_without_oa_only": bad_oa,
        "ok": not (bad_pub or bad_cc or bad_oa),
    }


# ══════════════════════════════════════════════════════════════
# 自检（离线；不读网络）
# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    bad = 0

    # ── 0. 真实白名单必须自身体检通过 ────────────────────────
    ents, path = load_entries()
    if not ents:
        bad += 1
        print(f"  [X] 读不到白名单条目（{path}）")
    else:
        au = audit_whitelist(ents)
        if not au["ok"]:
            bad += 1
            print(f"  [X] 白名单自身体检未过：{au}")
        else:
            print(f"白名单体检：{au['count']} 条，三判据全过（{path}）")

    # ── 1. 真实案例：EPMC 允许 fulltext（oa_only 需声明）─────
    epmc = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9361922/fullTextXML"
    d = decide(epmc, "fulltext", is_oa=True, entries=ents)
    if not d["allowed"]:
        bad += 1
        print(f"  [X] EPMC fullTextXML 应允许（is_oa=True）：{d['reason']}")
    else:
        print(f"真实案例：EPMC fullTextXML + is_oa=True → 允许（content_class="
              f"{d['content_class']}, oa_only={d['oa_only']}）")

    # ── 2. ★ 核心：不声明 is_oa 就必须拒绝 ───────────────────
    d0 = decide(epmc, "fulltext", entries=ents)
    if d0["allowed"]:
        bad += 1
        print("  [X] oa_only 源在 is_oa 未声明时被放行 —— 这就是运行时缺口")
    else:
        print(f"oa_only 门槛：未声明 is_oa → 拒绝（{d0['reason'][:40]}...）")
    dn = decide(epmc, "fulltext", is_oa=False, entries=ents)
    if dn["allowed"]:
        bad += 1
        print("  [X] is_oa=False 时 oa_only 源被放行")
    else:
        print("oa_only 门槛：is_oa=False → 拒绝")

    # ── 3. ★ 核心：越界请求必须拒绝 ─────────────────────────
    # eutils 只允许 abstract，取 fulltext 属越界（docs/阶段R全文获取模块.md 明载）
    eutils = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=1"
    d = decide(eutils, "fulltext", entries=ents)
    if d["allowed"]:
        bad += 1
        print("  [X] eutils 取 <body> 属越界，却被放行")
    else:
        print(f"越界拦截：eutils 只允许 abstract，取 fulltext → 拒绝（next={d['next']}）")
    d = decide(eutils, "abstract", entries=ents)
    if not d["allowed"]:
        bad += 1
        print(f"  [X] eutils 取 abstract 应允许：{d['reason']}")
    else:
        print("越界拦截：同一域名取 abstract → 允许（包含关系而非相等）")

    # ── 4. ★ 核心：出版社内容站一律拒绝，并指向 CDP ──────────
    nature = "https://www.nature.com/articles/s41577-022-00763-8"
    d = decide(nature, "fulltext", entries=ents)
    if d["allowed"] or d["next"] != "browser":
        bad += 1
        print(f"  [X] 出版社内容站应拒绝且 next=browser：{d}")
    else:
        print(f"出版社内容站：拒绝并指明走向 CDP（next={d['next']}）")
    for extra in ("https://www.sciencedirect.com/science/article/pii/S1",
                  "https://api.elsevier.com/content/article/doi/10.1/x",
                  "https://onlinelibrary.wiley.com/doi/10.1/x"):
        d = decide(extra, "fulltext", entries=ents)
        if d["allowed"]:
            bad += 1
            print(f"  [X] {extra} 被放行")
    print("出版社内容站：ScienceDirect / Elsevier API / Wiley 全部拒绝")

    # ── 5. 子串陷阱：域名后缀边界 ────────────────────────────
    traps = ["https://api.crossref.org.attacker.com/x",
             "https://evil-api.crossref.org.attacker.com/x",
             "https://xapi.openalex.org.evil.com/x"]
    leaked = [t for t in traps if decide(t, "metadata", entries=ents)["allowed"]]
    if leaked:
        bad += 1
        print(f"  [X] 子串陷阱被放行：{leaked}")
    else:
        print(f"后缀边界：{len(traps)} 个相似域名全部拒绝（非子串匹配）")

    # ── 6. 未知层级必须拒绝（不接受默认值）──────────────────
    d = decide(epmc, "full_text", is_oa=True, entries=ents)
    if d["allowed"]:
        bad += 1
        print("  [X] 未知请求层级被放行")
    else:
        print("未知层级：拒绝并要求调用方显式声明（不接受默认值）")

    # ── 7. 非法 content_class 的条目必须被拦 ─────────────────
    fake_entries = [{"domain": "example.org", "content_class": "everything",
                     "oa_only": False}]
    d = decide("https://example.org/x", "metadata", entries=fake_entries)
    if d["allowed"]:
        bad += 1
        print("  [X] content_class 非法的条目被放行")
    else:
        print(f"条目自守：content_class 非法 → 拒绝（{d['reason'][:32]}...）")

    # ── 8. oa-fulltext-xml 但 oa_only=false 的条目必须被体检抓出 ─
    au = audit_whitelist([{"domain": "a.example", "content_class": "abstract+oa-fulltext-xml",
                           "oa_only": False}])
    if au["ok"] or not au["oa_fulltext_without_oa_only"]:
        bad += 1
        print(f"  [X] 体检未抓出 oa_only 缺失：{au}")
    else:
        print(f"体检：抓到 oa-fulltext-xml 但缺 oa_only（{au['oa_fulltext_without_oa_only']}）")

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--audit" in argv:
        au = audit_whitelist()
        print(json.dumps(au, ensure_ascii=False, indent=2))
        return 0 if au["ok"] else 1
    url, want = "", "fulltext"
    for i, a in enumerate(argv):
        if a == "--check-domain" and i + 1 < len(argv):
            url = argv[i + 1]
        elif a.startswith("--check-domain="):
            url = a.split("=", 1)[1]
        elif a == "--want" and i + 1 < len(argv):
            want = argv[i + 1]
        elif a.startswith("--want="):
            want = a.split("=", 1)[1]
    if not url:
        print(__doc__)
        return 2
    d = decide(url, want, is_oa=True if "--assume-oa" in argv else None)
    print(f"{'允许直连' if d['allowed'] else '禁止直连'}：{url}")
    print(f"   请求层级：{want}")
    if d["matched"]:
        print(f"   命中条目：{d['matched']}（content_class={d['content_class']}, "
              f"oa_only={d['oa_only']}）")
    print(f"   理由：{d['reason']}")
    if d["allowed"]:
        if d["oa_only"]:
            print("   [WARN] 该源 oa_only=true —— 本次判定假设 is_oa=True")
        return 0
    print(f"   下一步：{d['next']}")
    print(f"   提示：{d['remediation']}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
