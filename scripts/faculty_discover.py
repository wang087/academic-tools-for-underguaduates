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
#   uv run python 流程1/scripts/faculty_discover.py --unit ustc:chem --dry-run
#
#   # 2) 新学校：给院系官网入口，自动发现（可持久化）
#   uv run python 流程1/scripts/faculty_discover.py \
#       --site https://life.fudan.edu.cn/ --school 复旦大学 --unit-name 生命科学学院 \
#       --unit-key life-sci --write
#
#   # 3) 只看某个具体师资页属于哪个家族
#   uv run python 流程1/scripts/faculty_discover.py \
#       --url https://chem.ustc.edu.cn/js/list.htm --explain
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
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
from urllib.parse import urlsplit

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from faculty_engines import (detect_engines, declared_total, looks_js_rendered,  # noqa: E402
                             page_count, parse_with, score_candidate, squash,
                             strip_tags)
from logutil import run_root as _run_root                       # noqa: E402

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


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:                        # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"取页面失败：{url} —— {last}")


# ══════════════════════════════════════════════════════════════
# ① 从站内找师资页候选
# ══════════════════════════════════════════════════════════════
def discover_faculty_pages(site: str, page: str) -> list[dict]:
    """在站内找师资页候选：按导航关键词 + 排除招聘/校友类栏目。

    返回 [{label, url, score}]，去重并按「标签越像师资页越靠前」排序。
    """
    out: dict[str, dict] = {}
    base = re.match(r"^[a-z]+://[^/]+", site, re.I)
    origin = base.group(0) if base else site.rstrip("/")

    for m in re.finditer(r"<a\s+([^>]*?)>(.*?)</a>", page, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        hm = re.search(r'href=["\']([^"\']+)["\']', attrs, re.I)
        if not hm:
            continue
        label = squash(strip_tags(inner))
        tm = re.search(r'title=["\']([^"\']*)["\']', attrs, re.I)
        if tm and not label:
            label = squash(_html.unescape(tm.group(1)))
        if not label or len(label) > 24:
            continue
        lab_l = label.lower()
        if not any(k in lab_l for k in FACULTY_KW):
            continue
        if any(k in label for k in NEGATIVE_KW):
            continue
        # 一句话式的标签多为新闻/活动页，排除（实测「朱冰院士做客谈家桢讲坛」）
        if any(k in label for k in NEWS_KW) or len(label) > 12:
            continue
        u = _html.unescape(hm.group(1)).strip()
        if u.startswith(("javascript:", "#", "mailto:")):
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = origin + u
        elif not u.startswith("http"):
            u = site.rstrip("/") + "/" + u.lstrip("./")
        if u in out:
            continue
        # 标签越具体（含"名录/队伍/师资"）越可能是真正的列表页
        s = 0
        if "名录" in label:
            s += 3
        if "队伍" in label or "师资" in label:
            s += 2
        if "教授" in label or "教师" in label or "导师" in label:
            s += 1
        out[u] = {"label": label, "url": u, "score": s}
    return sorted(out.values(), key=lambda r: -r["score"])


def probe_page(url: str, cfg: dict, section: str = "") -> dict:
    """对一个候选页跑全部引擎 + 评分，返回探测结果（含判定）。"""
    rec: dict = {"url": url}
    try:
        page = fetch(url)
    except Exception as e:                            # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return rec
    pages = page_count(page)
    decl = declared_total(page)
    dets = detect_engines(page, url, section, cfg)
    cands = []
    for d in dets:
        ppl = parse_with(d["engine"], page, url, section, cfg)
        verdict, score, reasons = score_candidate(
            d["engine"], ppl, pages, decl, d["fingerprint_hits"])
        cands.append({"engine": d["engine"], "count": len(ppl),
                      "fingerprint_hits": d["fingerprint_hits"],
                      "verdict": verdict, "score": score, "reasons": reasons,
                      "sample": [p["name"] for p in ppl[:8]],
                      "people": ppl})
    cands.sort(key=lambda r: (-r["score"], -r["count"]))
    best = cands[0] if cands else None
    maxn = best["count"] if best else 0
    rec.update({
        "pages": pages, "declared": decl,
        "js_rendered": looks_js_rendered(page, maxn),
        "candidates": [{k: v for k, v in c.items() if k != "people"}
                       for c in cands],
        "_best_people": (best or {}).get("people", []),
        "_best_engine": (best or {}).get("engine", ""),
        "_best_verdict": (best or {}).get("verdict", "reject"),
        "_best_score": (best or {}).get("score", 0),
    })
    return rec


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
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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
def load_cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


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
        url = base + sec["path"]
        rec = probe_page(url, cfg, sec.get("name", ""))
        rec["label"] = sec.get("name", "")
        rec["configured_engine"] = sec.get("engine", "")
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="faculty_discover.py",
        description="阶段 S 复用模式：任意学校/院系 → 自动发现师资页并生成数据源配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  faculty_discover.py --unit ustc:chem\n"
                "  faculty_discover.py --site https://life.fudan.edu.cn/ "
                "--school 复旦大学 --unit-name 生命科学学院 --unit-key life-sci --write\n"
                "  faculty_discover.py --url https://chem.ustc.edu.cn/js/list.htm --explain\n"))
    ap.add_argument("--unit", help="已收录的 `学校:院系`，直接复用配置")
    ap.add_argument("--site", help="院系官网入口 URL（新学校用）")
    ap.add_argument("--school", default="", help="学校全称")
    ap.add_argument("--school-key", default="", help="学校 key（缺省由域名推断）")
    ap.add_argument("--unit-name", default="", help="院系全称")
    ap.add_argument("--unit-key", default="", help="院系 key（缺省用 main）")
    ap.add_argument("--url", help="直接指定一个师资页做家族判定")
    ap.add_argument("--out",
                    default=str(_run_root() / ".work_cdp" / "faculty_discover"),
                    help="输出目录（默认 <流程1>/<年月日>/.work_cdp/faculty_discover）")
    ap.add_argument("--write", action="store_true",
                    help="把选定结果写进 templates/学部官网源.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    ap.add_argument("--explain", action="store_true",
                    help="打印每个候选引擎的得分与理由")
    ap.add_argument("--top", type=int, default=3,
                    help="自动发现时最多采用几个师资页（默认 3）")
    args = ap.parse_args(argv)

    log = get("discover")
    cfg = load_cfg()

    # ── 模式 3：单页家族判定 ──
    if args.url:
        rec = probe_page(args.url, cfg)
        print(f"页面：{args.url}")
        if rec.get("error"):
            print(f"  [X] {rec['error']}")
            return 1
        print(f"  分页 {rec['pages']}｜官网自称 {rec['declared'] or '未给出'}"
              f"｜疑 JS 渲染 {rec['js_rendered']}")
        for c in rec["candidates"]:
            print(f"  · {c['engine']:<20} {c['count']:>4} 人  "
                  f"判定={c['verdict']:<6} 得分={c['score']:>4}  "
                  f"指纹={c['fingerprint_hits']}")
            if args.explain:
                for r in c["reasons"]:
                    print(f"        - {r}")
                print(f"        样例: {c['sample'][:8]}")
        best = rec["_best_engine"]
        print(f"\n  建议引擎：**{best}**（{rec['_best_verdict']}，"
              f"{len(rec['_best_people'])} 人）")
        return 0

    # ── 模式 1：已收录院系，直接复用 ──
    if args.unit:
        got = resolve_from_config(cfg, args.unit)
        if not got:
            print(f"[X] 配置里没有 {args.unit}；用 --site 做新学校自动发现")
            return 2
        sk, uk, ue = got
        print(f"复用已收录数据源：{cfg['universities'][sk]['name']} · {ue['name']}")
        recs = probe_configured(ue, cfg)
        for r in recs:
            print(f"  [{r.get('label')}] {r['url']}")
            if r.get("error"):
                print(f"      [X] {r['error']}")
                continue
            print(f"      配置引擎={r['configured_engine']} → 探测最优="
                  f"{r['_best_engine']}（{r['_best_verdict']}，"
                  f"{len(r['_best_people'])} 人；分页 {r['pages']}；"
                  f"官网自称 {r['declared'] or '—'}）")
            if r["_best_engine"] != r["configured_engine"]:
                print("      [WARN] 与配置不一致，建议核对后更新配置")
        return 0

    # ── 模式 2：新学校自动发现 ──
    if not args.site:
        ap.print_help()
        return 0
    print(f"站点：{args.site}")
    try:
        home = fetch(args.site)
    except Exception as e:                            # noqa: BLE001
        print(f"  [X] 首页打不开：{e}")
        return 1
    cands = discover_faculty_pages(args.site, home)
    print(f"  发现师资页候选 {len(cands)} 个")
    for c in cands:
        print(f"      [{c['label']}] {c['url']}")

    chosen, probed = [], []
    for c in cands[: max(1, args.top * 4)]:
        r = probe_page(c["url"], cfg, c["label"])
        r["label"] = c["label"]
        probed.append(r)
        if r.get("error"):
            print(f"  [X] {c['label']}: {r['error'][:70]}")
            continue
        print(f"  · {c['label']:<14} 引擎={r['_best_engine']:<20} "
              f"{len(r['_best_people']):>4} 人  判定={r['_best_verdict']:<6} "
              f"得分={r['_best_score']:>4}  分页={r['pages']} "
              f"自称={r['declared'] or '—'}")
        if args.explain:
            for cc in r["candidates"]:
                print(f"        {cc['engine']:<20} {cc['count']:>4} 人 "
                      f"{cc['verdict']:<6} {cc['score']:>4}")
                for rs in cc["reasons"]:
                    print(f"            - {rs}")
        if r["_best_verdict"] in ("accept", "review") and r["_best_people"]:
            chosen.append(r)

    if not chosen:
        print("\n未找到可用的师资页。")
        # 直连全失败 → 再看是不是 JS/AJAX 站，并给出**可执行的下一步**
        #   （§5.1：第一层直连失败转 CDP；渲染/交互由 faculty_render、
        #     faculty_interact 两个模块承担，配置里写 engine 即可。）
        js_like = [r for r in probed
                   if not r.get("error") and r.get("js_rendered")]
        if js_like:
            print("    · 该站师资数据由 JS/AJAX 动态加载，直连取不到。")
            print("      → 走浏览器链路（配置里把 section 的 engine 设为）：")
            print("        · `interactive` —— 数据靠**点击分类**加载时（多数 WebPlus 站群）")
            print("        · `render`      —— 整页渲染后即可解析时")
            print("      参考已收录的南京大学化学化工学院：")
            print("        uv run python 流程1/scripts/harvest_faculty.py "
                  "--unit nju:chem --out outputs/nju_chem")
            for r in js_like[:3]:
                print(f"      · 候选页 {r['url']}")
        else:
            print("    · 站内没有形如「师资队伍/教师名录」的入口 → "
                  "请用 --url 指定具体页")
        return 3

    chosen.sort(key=lambda r: -r["_best_score"])
    chosen = chosen[: args.top]
    print(f"\n采纳 {len(chosen)} 个师资页：")
    for r in chosen:
        print(f"  · {r['label']} → 引擎 {r['_best_engine']}，"
              f"{len(r['_best_people'])} 人/页 × {r['pages']} 页"
              f"（{r['_best_verdict']}）")

    # 生成配置
    unit_name = args.unit_name or (chosen[0]["label"] if chosen else "师资队伍")
    unit_key = args.unit_key or "main"
    school_key = args.school_key or _infer_key(args.site)
    entry = build_unit_entry(args.school or school_key, unit_name,
                             args.site, chosen)
    print("\n生成的配置条目：")
    print(json.dumps({school_key: {unit_key: entry}}, ensure_ascii=False, indent=2))

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{school_key}_{unit_key}_discover.json").write_text(
        json.dumps({"school": args.school, "unit": unit_name,
                    "site": args.site, "probed": [
                        {k: v for k, v in r.items() if k != "_best_people"}
                        for r in probed],
                    "entry": {school_key: {unit_key: entry}}},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if args.write:
        wrote = write_config(school_key, args.school or school_key,
                             [school_key], unit_key, entry, dry=args.dry_run)
        print(f"\n已写入配置：{wrote}")
        print(f"  之后直接跑：uv run python 流程1/scripts/harvest_faculty.py "
              f"--unit {school_key}:{unit_key} --out outputs/{unit_name}")
    elif args.dry_run:
        print("\n（--dry-run：未写文件）")
    else:
        print(f"\n→ 探测结果：{outdir}")
        print("  确认无误后加 --write 落进配置")
    return 0


def _infer_key(site: str) -> str:
    m = re.match(r"^[a-z]+://([^./]+)", site, re.I)
    return (m.group(1).replace("-", "") if m else "site")[:16]


if __name__ == "__main__":
    raise SystemExit(main())
