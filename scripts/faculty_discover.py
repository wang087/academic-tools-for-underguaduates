# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S · **复用模式**：任意学校/院系 → 自动发现师资页并生成数据源配置
#
# 解决什么问题
#   旧做法每换一所学校，都要人肉打开官网、看 HTML、手写正则、
#   再逐次确认 —— 无法规模化。本脚本把这件事变成**一次自动运行**：
#
#     ① 给一个院系官网入口（或已收录的 学校+院系）
#     ② 自动找出该站的**师资页候选**（按导航关键词）
#     ③ 对每个候选跑**全部站点家族引擎**，用结构指纹 + 官网自称条数对账打分
#     ④ 输出可直接用的数据源配置；`--write` 落进 templates/学部官网源.json
#     ⑤ 之后 harvest_faculty.py 直接跑，**无需再确认**
#
# 与 §5.1 分层获取策略的关系
#   **学部官网师资页属公开元数据 → 走第一层直连**（本脚本即如此实现），
#   不需要 CDP。本脚本**只取师资页本身**，不触碰出版社内容站。
#
# 用法
#   # 1) 已收录学校：直接按 学校:院系 复用
#   uv run python scripts/faculty_discover.py --unit ustc:chem --dry-run
#
#   # 2) 新学校：给院系官网入口，自动发现（可持久化）
#   uv run python scripts/faculty_discover.py \
#       --site https://life.fudan.edu.cn/ --school 复旦大学 --unit-name 生命科学学院 \
#       --unit-key life-sci --write
#
#   # 3) 只看某个具体师资页属于哪个家族
#   uv run python scripts/faculty_discover.py \
#       --url https://chem.ustc.edu.cn/js/list.htm --explain
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import html as _html
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit, urlunsplit

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from faculty_engines import (detect_engines, declared_total, looks_js_rendered,  # noqa: E402
                             page_count, parse_with, score_candidate, squash,
                             strip_tags)

try:
    from logutil import get  # type: ignore
except Exception:                                    # pragma: no cover
    def get(name):                                   # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)
        return _L()


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 (academic-use)")

CONFIG_PATH = _HERE.parent / "templates" / "学部官网源.json"

# 导航关键词：用来从首页/栏目页找出「师资页」入口
FACULTY_KW = ("师资", "教师", "名录", "队伍", "导师", "教授", "院士",
              "faculty", "people", "teacher", "staff", "members", "professor")

# 明显不是师资页的栏目（避免把「招生/招聘/校友」当师资页）
NEGATIVE_KW = ("招聘", "招生", "报考", "校友", "捐赠", "退休", "逝世",
               "招聘信息", "博士后招聘", "联系方式", "院长信箱")

# 新闻/活动类页面：标签像一句话而不是栏目名 → 不是师资页
NEWS_KW = ("做客", "讲坛", "讲座", "报告会", "研讨会", "会议", "论坛",
           "喜报", "祝贺", "荣获", "获奖", "表彰", "启动", "举办", "召开",
           "调研", "来访", "交流", "共建", "党日", "学习", "贯彻")


def _fix_streams() -> None:
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


class FetchedPage(str):
    """String-compatible response body with its authoritative final URL."""
    def __new__(cls, text: str, final_url: str):
        obj = super().__new__(cls, text)
        obj.final_url = final_url
        return obj


def decode_response(response, requested_url: str) -> FetchedPage:
    raw = response.read()
    content_type = response.headers.get("Content-Type", "")
    header = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    meta = re.search(br"<meta\b[^>]*charset\s*=\s*['\"]?\s*([\w.-]+)", raw[:16384], re.I)
    encodings = ([header.group(1)] if header else []) + ([meta.group(1).decode("ascii")] if meta else [])
    for encoding in encodings + ["utf-8-sig", "gb18030"]:
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeError, LookupError):
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return FetchedPage(text, response.geturl() or requested_url)


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return decode_response(response, url)
        except Exception as exc:
            last = exc
            if i < retries - 1:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"取页面失败：{url} —— {last}")


# ══════════════════════════════════════════════════════════════
# ① 从站内找师资页候选
# ══════════════════════════════════════════════════════════════
class _Links(HTMLParser):
    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.base = ""
        self.links = []
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and not self.base:
            self.base = attrs.get("href", "")
        if tag == "a":
            self.current = [attrs, [], [], ""]
        elif self.current and tag in ("h2", "h3", "h4", "h5"):
            self.current[3] = tag

    def handle_data(self, data):
        if self.current:
            self.current[1].append(data)
            if self.current[3]:
                self.current[2].append(data)

    def handle_endtag(self, tag):
        if self.current and tag == self.current[3]:
            self.current[3] = ""
        if tag == "a" and self.current:
            attrs, text, heading, _ = self.current
            label = squash(attrs.get("title", "")) or squash("".join(heading)) or squash("".join(text))
            self.links.append((attrs, label))
            self.current = None


def safe_link(source: str, href: str, base: str = "") -> str:
    """Resolve native links while keeping discovery on the department host."""
    href = _html.unescape(href or "").strip()
    if not href or href.startswith("#"):
        return ""
    try:
        target = urlsplit(urljoin(urljoin(source, base), href))
        source_parts = urlsplit(source)
        if target.port != source_parts.port:
            return ""
    except ValueError:
        return ""
    if target.scheme.lower() not in ("http", "https"):
        return ""
    if target.hostname != source_parts.hostname or target.username or target.password:
        return ""
    return urlunsplit((target.scheme, target.netloc, target.path or "/", target.query, ""))


def discover_faculty_pages(site: str, page: str) -> list[dict]:
    """Find faculty navigation; preserve actual URL semantics, including base href."""
    parsed = _Links(page)
    out = {}
    keywords = FACULTY_KW + ("研究人员", "专任教师", "人员简介", "教研室")
    for attrs, label in parsed.links:
        label = squash(label)
        low = label.lower()
        personnel_directory = bool(re.search(r"(?:人员简介|教师介绍)\s*$", label))
        if not label or len(label) > (64 if personnel_directory else 32) or not any(k in low for k in keywords):
            continue
        # Course/research themes may contain 学习/交流. An explicit personnel
        # directory heading is stronger evidence than those generic news cues.
        if any(k in label for k in NEGATIVE_KW) or (not personnel_directory and any(k in label for k in NEWS_KW)):
            continue
        url = safe_link(site, attrs.get("href", ""), parsed.base)
        if not url or re.search(r"/(?:news|xinwen|xwzx|xwdt|events?)(?:/|[_.])", urlsplit(url).path, re.I):
            continue
        score = sum(w for key, w in (("名录", 3), ("队伍", 2), ("师资", 2),
                    ("教师", 1), ("导师", 1), ("人员", 1)) if key in label)
        out.setdefault(url, {"label": label, "url": url, "score": score})
    return sorted(out.values(), key=lambda r: -r["score"])


def next_page_url(page: str, url: str) -> str:
    parsed = _Links(page)
    for attrs, label in parsed.links:
        rel = attrs.get("rel", "").lower().split()
        text = squash(label).lower().strip(" >›»→")
        if "next" in rel or text in ("下一页", "下页", "next", "next page"):
            target = safe_link(url, attrs.get("href", ""), parsed.base)
            if target and target != url:
                return target
    return ""


def select_page(page: str, url: str, section: str, cfg: dict) -> dict:
    """Use the shared selector when available; retain compatibility with older engines."""
    import faculty_engines
    selector = getattr(faculty_engines, "select_parser", None)
    if selector:
        return selector(page, url, section, cfg)
    candidates = []
    for det in detect_engines(page, url, section, cfg):
        people = parse_with(det["engine"], page, url, section, cfg)
        verdict, score, reasons = score_candidate(det["engine"], people,
                page_count(page), declared_total(page), det["fingerprint_hits"])
        candidates.append({**det, "people": people, "count": len(people),
                           "verdict": verdict, "score": score, "reasons": reasons})
    candidates.sort(key=lambda c: (-c["score"], -c["count"]))
    best = candidates[0] if candidates else {"engine": "", "people": [],
            "verdict": "reject", "score": 0, "reasons": ["未找到支持的名单结构"]}
    return {**best, "candidates": [{k: v for k, v in c.items() if k != "people"}
                                    for c in candidates]}


def url_key(url: str) -> str:
    parts = urlsplit(url)
    host = re.sub(r"[^a-zA-Z0-9.-]", "_", parts.netloc) or "site"
    return host + "_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def page_scope(page: str, section: str) -> dict:
    title = re.search(r"<title\b[^>]*>(.*?)</title>", page, re.I | re.S)
    headings = [squash(strip_tags(t)) for t in re.findall(r"<h[12]\b[^>]*>(.*?)</h[12]>", page, re.I | re.S)]
    return {"section": section, "title": squash(strip_tags(title.group(1))) if title else "",
            "headings": headings[:8], "unit_coverage": "unverified",
            "note": "计数仅对应当前名单栏目；ALL、教授等标签不能证明全院覆盖"}


def error_page_reason(page: str) -> str:
    scope = page_scope(page, "")
    title = scope["title"].lower()
    visible = squash(strip_tags(re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", page, flags=re.I | re.S)))
    if re.search(r"\b(?:404|403|500|502|503)\b|page not found|access denied|service unavailable|页面不存在|访问出错|网站维护", title, re.I):
        return "错误页标题，不能作为名单证据"
    if len(visible) < 1000 and re.search(r"您访问的页面不存在|您所访问的页面不存在|页面已被删除|页面访问错误|系统维护中", visible):
        return "错误页正文，不能作为名单证据"
    return ""


def record_identity(person: dict) -> str:
    """Keep exact duplicate observations together without guessing identity."""
    if person.get("url"):
        return str(person["url"])
    content = {key: person.get(key) for key in ("title", "directions", "email", "direction_hint")}
    return "content:" + hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def collect_page(url: str, cfg: dict, section: str = "", *, fetcher=None,
                 max_pages: int = 30, first_page: str | None = None) -> dict:
    """Keep parsing confidence separate from verified coverage of the listed scope."""
    fetcher = fetcher or fetch
    current, requested, finals, people, evidence = url, set(), set(), [], []
    declared = pages = 0
    verdicts, engines, reasons, candidates = [], [], [], []
    unfinished, js_rendered = False, False
    scope = {"section": section, "unit_coverage": "unverified"}
    while current and current not in requested and len(requested) < max(1, max_pages):
        requested.add(current)
        final = current
        try:
            page = first_page if len(requested) == 1 and first_page is not None else fetcher(current)
            final = getattr(page, "final_url", current)
            if final in finals:
                raise ValueError("重定向分页循环")
            finals.add(final)
            error = error_page_reason(page)
            selected = ({"engine": "", "people": [], "verdict": "reject", "reasons": [error]}
                        if error else select_page(page, final, section, cfg))
        except Exception as exc:
            evidence.append({"requested_url": current, "source_url": final, "error": f"{type(exc).__name__}: {exc}"})
            reasons.append("页面获取或解析失败")
            unfinished = True
            break
        if len(evidence) == 0:
            scope = page_scope(page, section)
        count_pages, count_declared = page_count(page), declared_total(page)
        pages, declared = max(pages, count_pages), max(declared, count_declared)
        verdict, got = selected.get("verdict", "reject"), selected.get("people", [])
        verdicts.append(verdict)
        engines.append(selected.get("engine", ""))
        reasons.extend(selected.get("reasons", []))
        if len(evidence) == 0:
            candidates = selected.get("candidates", [])
        evidence.append({"requested_url": current, "source_url": final, "engine": engines[-1], "verdict": verdict,
                         "count": len(got), "pages": count_pages, "declared": count_declared,
                         "declared_records": count_declared, "scope": page_scope(page, section),
                         "score": selected.get("score", 0), "reasons": selected.get("reasons", [])})
        js_rendered |= looks_js_rendered(page, len(got))
        if verdict != "reject":
            for person in got:
                rec = {k: v for k, v in person.items() if not k.startswith("_")}
                rec.setdefault("section", section)
                rec["source_url"] = final
                people.append(rec)
        else:
            unfinished = True
        following = "" if error else next_page_url(page, final)
        if following in requested or following in finals:
            unfinished = True
            reasons.append("分页链接形成循环")
            break
        current = following
    if current and len(requested) >= max(1, max_pages):
        unfinished = True
        reasons.append("达到分页上限")
    unique = {}
    for person in people:
        person["record_id"] = record_identity(person)
        unique.setdefault((person.get("name", ""), person["record_id"], person.get("section", "")), person)
    people = list(unique.values())
    identities = {}
    for person in people:
        identities.setdefault(person.get("name", ""), set()).add(person["record_id"])
    ambiguities = [{"name": name, "record_ids": sorted(ids),
                    "urls": sorted({p.get("url", "") for p in people if p.get("name") == name and p.get("url")})}
                   for name, ids in identities.items() if len(ids) > 1]
    if ambiguities:
        for person in people:
            if len(identities.get(person.get("name", ""), set())) > 1:
                person["identity_ambiguity"] = True
        unfinished = True
        reasons.append("同名记录的URL或无URL内容不同，身份待核验；记录条数不等于人数")
    loaded = sum("error" not in e for e in evidence)
    if pages > loaded:
        unfinished = True
        reasons.append("声明分页未全部跟随：仅使用页面真实 next 链接")
    if declared and len(people) != declared:
        unfinished = True
        reasons.append(f"实际名单条目数 {len(people)} 与官网自称 {declared} 不符")
    verdict = "reject" if not people else ("review" if unfinished or any(v != "accept" for v in verdicts) else "accept")
    verified = bool(declared and len(people) == declared and not unfinished and verdict == "accept")
    if not declared:
        reasons.append("官网未提供可对账的名单条目总数，覆盖范围尚未核验")
    engine = engines[0] if engines else ""
    source = evidence[0].get("source_url", url) if evidence else url
    return {"url": url, "requested_url": url, "source_url": source, "engine": engine, "verdict": verdict,
            "pages": max(1, pages, loaded), "pages_fetched": loaded, "declared": declared,
            "declared_records": declared, "record_count": len(people),
            "unique_name_count": len({p.get("name", "") for p in people}),
            "identity_ambiguity": ambiguities, "coverage": "verified" if verified else "unverified",
            "scope": scope, "complete": verified, "partial": not verified,
            "status": "complete" if verified else "partial", "evidence": evidence,
            "reasons": list(dict.fromkeys(reasons)), "candidates": candidates, "records": people,
            "js_rendered": js_rendered, "_best_people": people, "_best_engine": engine,
            "_best_verdict": verdict, "_best_score": evidence[0].get("score", 0) if evidence else 0}


def probe_page(url: str, cfg: dict, section: str = "", *, max_pages: int = 30) -> dict:
    return collect_page(url, cfg, section, max_pages=max_pages)


def discover_site(site: str, cfg: dict, *, fetcher=None, top: int = 3,
                  max_pages: int = 30, max_candidates: int = 12) -> list[dict]:
    """Inspect at most two navigation hops, including department personnel directories."""
    fetcher = fetcher or fetch
    home = fetcher(site)
    home_url = getattr(home, "final_url", site)
    home_candidates = discover_faculty_pages(home_url, home)
    directories = [c for c in home_candidates if re.search(r"(?:人员简介|教师介绍)\s*$", c["label"])]
    # An explicit set of personnel articles is the landing page's roster;
    # global menus such as 研究生导师 should not consume its bounded budget.
    queue = [(c, 1, home_url) for c in (directories or home_candidates)]
    if not queue:
        queue = [({"url": site, "label": "师资"}, 1, site)]
    seen, results = {site}, []
    for candidate, depth, parent in queue:
        url = candidate["url"]
        if url in seen and url != site:
            continue
        seen.add(url)
        if len(results) >= max_candidates:
            break
        try:
            page = home if url == site else fetcher(url)
            rec = collect_page(url, cfg, candidate["label"], fetcher=fetcher,
                               max_pages=max_pages, first_page=page)
            children = discover_faculty_pages(getattr(page, "final_url", url), page)
        except Exception as exc:
            rec = {"url": url, "source_url": url, "verdict": "reject", "records": [],
                   "engine": "", "complete": False, "partial": True, "pages": 0,
                   "declared": 0, "declared_records": 0, "record_count": 0, "unique_name_count": 0,
                   "coverage": "unverified", "scope": {"section": candidate["label"], "unit_coverage": "unverified"},
                   "evidence": [{"requested_url": url, "source_url": url, "error": str(exc)}]}
            children = []
        rec.update(label=candidate["label"], discovery_depth=depth, discovered_from=parent)
        pending = [c for c in children if c["url"] not in seen and c["url"] != url]
        # Global menus also occur on real rosters; only empty/unsupported pages
        # provide evidence that another navigation hop is needed.
        if not rec.get("records") and (pending or any(k in candidate["label"] for k in ("教研室", "人员简介"))):
            rec["complete"], rec["partial"], rec["status"] = False, True, "partial"
            rec["verdict"] = "review"
            rec.setdefault("reasons", []).append("存在下级师资目录，覆盖范围待核验")
            rec["evidence"].append({"source_url": url, "directory_links": pending,
                                    "reason": "有界二层发现；目录不等于完整名单"})
            if depth < 2:
                queue.extend((c, depth + 1, url) for c in pending)
        results.append(rec)
    return results


def public_result(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


# ══════════════════════════════════════════════════════════════
# ② 生成配置条目
# ══════════════════════════════════════════════════════════════
def build_unit_entry(school: str, unit_name: str, site: str,
                     chosen: list[dict]) -> dict:
    """把探测结果转成 `学部官网源.json` 的 unit 条目。

    只写**引擎名 + 页面路径**，不写死任何站点特有 class ——
      解析规则留在 faculty_engines.py，新增学校不必改脚本。
    """
    m = re.match(r"^[a-z]+://[^/]+", site, re.I)
    base = m.group(0) if m else site.rstrip("/")
    base_host = urlsplit(base).netloc.lower()
    sections = []
    for c in chosen:
        u = c["url"]
        # 只取 path，**不能**用 startswith(base) 比较：
        #   站内链接常见 http/https 混写（实测 chem.ustc.edu.cn 首页用 http、
        #   规范地址是 https），按字符串前缀判断会把整条绝对 URL 当成路径，
        #   拼出 `/http://host/...` 这种废 URL。
        sp = urlsplit(u)
        if sp.netloc and sp.netloc.lower() != base_host:
            path = u                      # 跨主机：保留绝对 URL
        else:
            path = sp.path or "/"
            if sp.query:
                path += "?" + sp.query
        if not path.startswith(("/", "http")):
            path = "/" + path
        sec = {"name": c.get("label") or c.get("section") or "师资",
               "path": path, "paging": "auto",
               "detail": "star" if c.get("_dir_in_list") else "fetch",
               "engine": c["_best_engine"]}
        if c.get("note"):
            sec["note"] = c["note"]
        sections.append(sec)
    return {"name": unit_name, "base": base,
            "list_type": "engine_dispatch", "sections": sections}


def write_config(school_key: str, school_name: str, aliases: list[str],
                 unit_key: str, unit_entry: dict, dry: bool = False) -> str:
    """把 unit 写进 templates/学部官网源.json（保留既有内容与排版风格）。"""
    cfg = load_cfg()
    unis = cfg.setdefault("universities", {})
    sk = unis.setdefault(school_key, {"name": school_name, "alias": aliases,
                                      "units": {}})
    sk["name"] = school_name
    # 别名必须**并集**而非覆盖：直接赋值会把既有别名冲掉
    #   （实测新增 ustc 的院系时，把原「中科大/中国科大/科大」覆盖成了 "ustc"，
    #    导致老用户按中文别名调用直接失效）。
    if aliases:
        cur = list(sk.get("alias") or [])
        for a in aliases:
            if a and a not in cur:
                cur.append(a)
        sk["alias"] = cur
    sk.setdefault("units", {})[unit_key] = unit_entry
    # 保证 engine_dispatch 这个 list_type 存在
    cfg.setdefault("list_types", {})["engine_dispatch"] = {
        "_desc": "引擎分派：解析规则由 sections[].engine 指定，见 scripts/faculty_engines.py",
        "_why": "新增学校只写引擎名与页面路径，不写站点特有 class —— 这是复用模式的关键",
    }
    if dry:
        return json.dumps({school_key: {unit_key: unit_entry}},
                          ensure_ascii=False, indent=2)
    # 必须显式 newline="\n"：Windows 上 write_text 默认把 \n 翻成 \r\n，
    #   会把整个配置文件的行尾从 LF 改成 CRLF —— git 于是显示"全文重写"，
    #   真实改动被淹没，review 与 blame 都失效（本仓库配置文件原为 LF）。
    with CONFIG_PATH.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return str(CONFIG_PATH)


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
def load_cfg(path: pathlib.Path | None = None) -> dict:
    target = pathlib.Path(path) if path is not None else CONFIG_PATH
    if not target.exists() and path is None:
        return {"universities": {}, "list_types": {"engine_dispatch": {"_engine_dispatch": True}}}
    try:
        cfg = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or not isinstance(cfg.get("universities"), dict):
            raise ValueError("配置缺少 universities 对象")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[X] 配置文件错误 {target}: {exc}") from exc
    cfg.setdefault("list_types", {}).setdefault("engine_dispatch", {"_engine_dispatch": True})
    return cfg


def resolve_from_config(cfg: dict, unit: str) -> tuple[str, str, dict] | None:
    """按 `学校:院系` 或别名在配置里查找（与 harvest_faculty 同语义）。"""
    k = (unit or "").strip().lower()
    unis = cfg.get("universities", {})
    if ":" in k:
        sk, uk = k.split(":", 1)
        sk, uk = sk.strip(), uk.strip()
        if sk in unis and uk in unis[sk].get("units", {}):
            return sk, uk, unis[sk]["units"][uk]
        return None
    if k in unis and len(unis[k].get("units", {})) == 1:
        uk = next(iter(unis[k]["units"]))
        return k, uk, unis[k]["units"][uk]
    for sk, sv in unis.items():
        names = [sk, sv.get("name", "")] + list(sv.get("alias", []))
        if any(n and n.lower() in k for n in names):
            units = sv.get("units", {})
            for uk, uv in units.items():
                if uv.get("name") and uv["name"] in unit:
                    return sk, uk, uv
            if len(units) == 1:
                uk = next(iter(units))
                return sk, uk, units[uk]
    return None


def probe_configured(unit_entry: dict, cfg: dict) -> list[dict]:
    """已收录院系：逐个 section 探测（用于 `--unit` 复用与校验）。"""
    base = unit_entry["base"].rstrip("/")
    out = []
    for sec in unit_entry["sections"]:
        url = urljoin(base + "/", sec["path"])
        rec = probe_page(url, cfg, sec.get("name", ""))
        rec["label"] = sec.get("name", "")
        rec["configured_engine"] = sec.get("engine", "")
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="官网师资发现与一次性解析（配置可选）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--unit")
    mode.add_argument("--site")
    mode.add_argument("--url")
    ap.add_argument("--direct", action="store_true", help="将 --site 作为名单页，不写全局配置")
    ap.add_argument("--config", help="显式配置路径；缺失或错误即报错")
    ap.add_argument("--school", default="")
    ap.add_argument("--school-key", default="")
    ap.add_argument("--unit-name", default="")
    ap.add_argument("--unit-key", default="")
    ap.add_argument("--out", default=str(_HERE.parent / time.strftime("%Y%m%d") / (os.environ.get("PAPER_AUTHOR") or "") / ".work_cdp" / "faculty_discover"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--max-pages", type=int, default=30)
    args = ap.parse_args(argv)
    cfg = load_cfg(pathlib.Path(args.config) if args.config else None)
    if args.direct and not args.site:
        ap.error("--direct 需要 --site")
    if args.unit:
        resolved = resolve_from_config(cfg, args.unit)
        if not resolved:
            print(f"[X] 配置里没有 {args.unit}；用 --site 或 --url")
            return 2
        recs = probe_configured(resolved[2], cfg)
        source = resolved[2]["base"]
    elif args.url or args.direct:
        source = args.url or args.site
        recs = [probe_page(source, cfg, args.unit_name or "师资", max_pages=args.max_pages)]
    elif args.site:
        source = args.site
        try:
            recs = discover_site(source, cfg, top=args.top, max_pages=args.max_pages)
        except Exception as exc:
            print(f"[X] 首页失败：{exc}")
            return 1
    else:
        ap.print_help()
        return 0
    payload = {"source_url": source, "results": [public_result(r) for r in recs]}
    accepted = [r for r in recs if r.get("verdict") == "accept" and r.get("records")]
    pending = any(r.get("verdict") == "review" for r in recs)
    payload["verdict"] = "review" if pending else ("accept" if accepted else "reject")
    payload["complete"] = bool(accepted) and not pending and all(r.get("complete") for r in recs)
    payload["partial"] = not payload["complete"]
    if (args.url or args.direct) and not any(r.get("records") for r in recs):
        print("名单页未解析到记录；若该页为教研室/师资目录，请用 --site URL 遍历下级入口（不要加 --direct）。")
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("--dry-run：未写文件")
    else:
        outdir = pathlib.Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        for rec in recs:
            target = outdir / (url_key(rec.get("requested_url") or rec["url"]) + "_faculty.json")
            target.write_text(json.dumps(public_result(rec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{rec.get('verdict')} {rec['source_url']} → {target}")
        target = outdir / (url_key(source) + "_discover.json")
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.write and not args.direct and not args.url and accepted and not args.dry_run:
        if args.config and pathlib.Path(args.config).resolve() != CONFIG_PATH.resolve():
            ap.error("--write 仅支持默认配置；自定义配置不会被静默改写")
        school_key = args.school_key or _infer_key(source)
        entry = build_unit_entry(args.school or school_key, args.unit_name or "师资",
                                 source, accepted[:max(1, args.top)])
        print(write_config(school_key, args.school or school_key, [school_key],
                           args.unit_key or "main", entry))
    if pending:
        print("[REVIEW] 待核验：部分名单、目录或解析证据尚不能确认完整性")
        return 4
    return 0 if accepted else 3


def _infer_key(site: str) -> str:
    m = re.match(r"^[a-z]+://([^./]+)", site, re.I)
    return (m.group(1).replace("-", "") if m else "site")[:16]


if __name__ == "__main__":
    raise SystemExit(main())
