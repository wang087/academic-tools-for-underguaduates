# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S 引擎库：**站点家族识别 + 通用抽取**（配置驱动抓取的共用底座）
#
# 为什么要独立成库
#   中国高校院系官网的师资页分属若干「站群家族」，每个家族的 HTML 骨架固定，
#   但**不同学校分属不同家族**。早期做法是把正则写进配置的 list_type，
#   每来一所学校就要人肉看 HTML、写正则 —— 无法规模化。
#   本库把「家族识别 + 抽取」集中到一处，使新增学校可以**自动判定**，
#   而不是逐次人工适配。
#
# 判据集中原则（与 harvest_faculty._is_direction 同源）
#   · 人名判据 `is_person_name` 只在这一处定义，抓取与发现两条链路共用，
#     避免标准漂移（流程历史上曾因两处判据漂移漏掉 9 条）。
#
# 只用标准库，零网络：所有函数都是纯字符串处理，便于离线自检。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import html as _html
import json
import re

__all__ = [
    "TAG", "strip_tags", "squash", "is_person_name", "split_title_direction",
    "url_signature", "enclosing_block", "clean_absolute",
    "ENGINES", "BROWSER_ENGINES", "detect_engines", "parse_with", "page_count",
    "declared_total", "looks_js_rendered",
]

TAG = re.compile(r"<[^>]+>")

# 人名判据：中文 2–4 字，或西文 2–4 段
_NAME_CN = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
_NAME_EN = re.compile(r"^[A-Z][a-zA-Z.\-']+(?:\s+[A-Z][a-zA-Z.\-']+){1,3}$")

# 与人名同为 2–4 汉字、但**不是人名**的高频导航/栏目词。
#   实测来源：SJTU / USTC / 复旦 / 南大 四站导航标签统计，
#     取出现频次高且必然不是教师的部分。
NAV_WORDS = {
    "学校主页", "学院主页", "中文首页", "学院概况", "学院简介", "学院领导",
    "组织机构", "规章制度", "人事工作", "院系介绍", "师资队伍", "师资概况",
    "教师名录", "教工名录", "教研队伍", "支撑队伍", "人才招聘", "师者风范",
    "育人名师", "科技之光", "服务之星", "先进典型", "人才培养", "本科教学",
    "本科生", "研究生", "招生就业", "科学研究", "科研概况", "科研成果",
    "获奖成果", "授权专利", "科研合作", "学术交流", "学术报告", "科研动态",
    "党群工作", "党建概况", "党群活动", "工会概况", "学习园地", "校友之家",
    "校友动态", "名录影像", "校友风采", "饮水思源", "基金捐赠", "捐赠办法",
    "校友联络", "校友中心", "年级理事", "地区理事", "安全工作", "安全动态",
    "安全知识", "应急处理", "网上办事", "服务手册", "党政办公室", "学生办公室",
    "教务办公室", "科研办公室", "信息公开", "办事大厅", "个人主页", "加入我们",
    "院长书记邮箱", "首页信息", "历史沿革", "学科介绍", "无机化学", "分析化学",
    "有机化学", "物理化学", "高分子", "化学物理", "材料科学", "最新通知",
    "通知公告", "新闻信息", "新闻中心", "教学信息", "科研信息", "学术活动",
    "本系概况", "化学系", "材料系", "院工会", "院妇委会", "名誉教授",
    "双聘教授", "退休教师", "院士", "兼职教授", "访问教授", "客座教授",
    "博士后", "管理人员", "实验技术", "技术支持", "行政后勤", "联系我们",
    "学院历史", "党政工团", "委员会", "师资力量", "教师队伍", "导师队伍",
    "教授", "副教授", "讲师", "研究员", "副研究员", "助理研究员", "实验师",
    "高级实验师", "工程师", "正高级工程师", "博士生导师", "硕士生导师",
    "博导", "硕导", "更多", "首页", "尾页", "第一页", "最后一页", "跳转到",
    "上页", "下页", "学生工作", "党团组织", "通讯指南", "就业信息", "生涯指导",
    "统计数据", "合作单位", "平台基地", "学科建设", "专业介绍", "培养计划",
    "课程检索", "通知", "公告", "新闻", "党建", "工会", "校友", "捐赠",
    "安全", "信息", "公开", "办事", "大厅", "个人", "主页", "加入", "我们",
    # ── 自动发现实测新增（USTC 化材 / 复旦生科 / 南大化学 的栏目名） ──
    "各系师资", "中心师资", "教育培养", "培养方案", "教学成果", "学位申请",
    "成果转化", "党建工作", "团建工作", "工会工作", "师德师风", "校友作品",
    "综合新闻", "党委通讯", "学术科研", "工会新闻", "学院招聘", "教研系列",
    "科研获奖", "科研论文", "科研专利", "科研项目", "党建思政", "院系概况",
    "教师总表", "名誉教授", "国际访问教授", "国家级人才", "重大项目",
    "岁月留痕", "校友杂志", "时光剪影", "校友往事", "校友同乐", "百年院庆",
    "学术大咖", "商界精英", "研究机构", "科研基地", "重点实验室", "学术机构",
}


# ══════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════
def squash(s: str) -> str:
    """收敛空白与不可见字符（站群 HTML 的 span 碎片会产生大量空白）。"""
    s = (s or "").replace("\u00a0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


def strip_tags(s: str) -> str:
    return _html.unescape(TAG.sub("", s or "")).strip()


def is_person_name(t: str) -> bool:
    """判断文本是否**像人名**。

    唯一判据入口 —— 引擎抽取与自动发现共用。
    """
    t = squash(t)
    if not t or len(t) > 40:
        return False
    # 西文名（允许 "Hans Staffan Persson" 这类多段）
    if _NAME_EN.match(t):
        return True
    c = re.sub(r"[\s\u3000]+", "", t)
    if not _NAME_CN.match(c):
        return False
    if c in NAV_WORDS:
        return False
    # 含职衔/标点的不是人名
    if re.search(r"[（(）)／/、,，·．.·\-—]", c):
        return False
    return True


def split_title_direction(txt: str) -> tuple[str, str]:
    """从 `刘建忠（有机化学）` 拆出 (姓名, 方向)。

    实测：WebPlus 系站群常把方向直接写在链接 title 的括号里，
      这使**列表页即可拿到方向**，无需进详情页。
    """
    txt = squash(txt)
    m = re.match(r"^(.*?)[（(]\s*([^（()）]{2,60}?)\s*[)）]\s*$", txt)
    if m and m.group(1).strip():
        return squash(m.group(1)), squash(m.group(2))
    return txt, ""


def url_signature(u: str) -> str:
    """URL 形态签名 —— 用于把「教师详情链接」与「导航链接」分开。

    核心洞察（实测 SJTU / 复旦 / 南大）：同一个师资页上，
      **教师链接共享同一 URL 形态**（如 `/teacher/<slug>`、
      `/YYYY/MMDD/c<col>a<id>/page.htm`），而导航链接形态各异。
      故「形态最大簇」即为教师集合，无需认识任何 class。

    规则：
      · 末段是文件名（.htm/.html/…）→ 归一化形态
      · 末段是 slug（无扩展名）→ 只保留目录部分

    归一化必须覆盖**十六进制目录**：实测 WebPlus 的教师页 URL 形如
      `/61/93/c28175a352659/page.htm` 与 `/ec/a3/c28175a453795/page.htm` ——
      目录是 hex、条目 id 是 `c<hex>a<hex>`。只把十进制数字归一为 `#`
      无法让这两条落到同一签名，聚类会失效。
    """
    m = re.match(r"^[a-z]+://[^/]+(/.*)?$", u or "", re.I)
    path = (m.group(1) or "/") if m else (u or "/")
    path = path.split("?")[0].split("#")[0]
    segs = [s for s in path.split("/") if s]
    if not segs:
        return "/"
    last = segs[-1]
    if not re.search(r"\.(?:htm|html|php|jsp|asp|aspx|psp)$", last, re.I):
        # slug 型（如 /teacher/wliu2025）→ 只留目录
        return "/" + "/".join(segs[:-1]) + ("/" if len(segs) > 1 else "")
    norm = path
    # `c<hex>a<hex>` 条目 id → c#a#
    norm = re.sub(r"\bc[0-9a-fA-F]{3,}a[0-9a-fA-F]{3,}\b", "c#a#", norm)
    # 纯十六进制路径段（2–8 位）→ #
    norm = re.sub(r"(?<=/)[0-9a-fA-F]{2,8}(?=/|$)", "#", norm)
    # 其余数字串 → #
    return re.sub(r"\d+", "#", norm)


def clean_absolute(base: str, url: str) -> str:
    """相对 URL → 绝对 URL（`javascript:` 一律丢弃）。

    实测坑：浙大兼聘/支撑队伍列表里有站内相对路径，
      直接 fetch 会因缺主机名静默失败，方向永远补不到。
    """
    u = (url or "").strip()
    if not u or u.startswith(("javascript:", "#", "mailto:")):
        return ""
    u = _html.unescape(u)
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http"):
        return u
    b = (base or "").rstrip("/")
    if u.startswith("/"):
        return b + u
    return b + "/" + u.lstrip("./")


def container_signature(page: str, pos: int) -> str:
    """取 pos 所在**最近祖先容器的形态签名**（ul/div 的 class 或 id）。

    为什么需要它：同一师资页上，教师条目通常同属一个容器
      （实测 SJTU：各系教师都在 `div.sheng-ul` 下的 `ul`；
       导航条目分属 `ul.menu` 等其它容器）。
      容器签名使我们能**把没有个人主页、href 是 `javascript:` 的老师也收进来** ——
      实测 SJTU 有 16 人未开通主页，只按 href 聚类会漏掉他们（208 → 186）。
    """
    for m in re.finditer(r"<(ul|ol|div)\b([^>]*)>", page[:pos], re.I):
        attrs = m.group(2) or ""
        best = m.group(1).lower() + "|" + _cls_of(attrs)
    return best


def _cls_of(attrs: str) -> str:
    m = re.search(r'class="([^"]*)"', attrs, re.I)
    if m:
        return squash(m.group(1))
    m = re.search(r'id="([^"]*)"', attrs, re.I)
    return "#" + squash(m.group(1)) if m else ""


def enclosing_block(page: str, pos: int, maxlen: int = 4000) -> str:
    """取 pos 所在的最近 `<li>` / `<div>` 配平块（标签配平，懒正则会提前收尾）。

    实测坑：容器懒匹配 `(.*?)</div></div>` 会让正文只剩 5 字。
    """
    for tag in ("li", "div", "tr", "p"):
        start = page.rfind(f"<{tag}", 0, pos + 1)
        if start < 0:
            continue
        depth, end = 0, None
        for m in re.finditer(rf"<(/?){tag}\b[^>]*>", page[start: start + maxlen],
                             re.I):
            if m.group(1) == "":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    end = start + m.end()
                    break
        if end:
            return page[start:end]
    return page[max(0, pos - 400): pos + 400]


# ══════════════════════════════════════════════════════════════
# 引擎：站点家族
# ══════════════════════════════════════════════════════════════
def _anchors(block: str):
    for m in re.finditer(r"<a\s+([^>]*?)>(.*?)</a>", block, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        hm = re.search(r'href=["\']([^"\']+)["\']', attrs, re.I)
        tm = re.search(r'title=["\']([^"\']*)["\']', attrs, re.I)
        yield (html_unescape_attr(hm.group(1)) if hm else "",
               html_unescape_attr(tm.group(1)) if tm else "",
               strip_tags(inner))


def html_unescape_attr(s: str) -> str:
    return squash(_html.unescape(s or ""))


def _person(name_raw: str, title_attr: str, base: str, href: str,
            section: str) -> dict | None:
    """把一条候选规范成记录；非人名返回 None。

    实测坑：WebPlus 的链接文本/ title 常写成「刘建忠（有机化学）」——
      姓名与方向在同一个串里。早期实现先把括号整体去掉得到姓名，
      随后**又对已去括号的姓名做一次拆分**，方向就被丢了
      （列表页本来能拿到的方向白白退化成"要进详情页"）。
      正确顺序：**先拆一次并同时取走方向**。
    """
    for raw in (name_raw, title_attr):
        nm, direc = split_title_direction(squash(raw))
        if nm and is_person_name(nm):
            return {"name": nm, "title": "",
                    "directions": [direc] if direc else [],
                    "email": "", "url": clean_absolute(base, href),
                    "section": section, "_dir_from_title": bool(direc)}
    # 无括号的纯姓名
    nm = squash(name_raw) or squash(title_attr)
    if is_person_name(nm):
        return {"name": nm, "title": "", "directions": [], "email": "",
                "url": clean_absolute(base, href), "section": section,
                "_dir_from_title": False}
    return None


def eng_wp_news_list(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：WebPlus 新闻列表式师资页。

    实测结构（中科大化学系 / 复旦生科院 / 南大化学 / 浙大 …）：
        <div id="wp_news_wN"><ul class="news_list">
          <li class="news n1 clearfix">
            <span class="news_title"><a href='/2025/0202/c2474a673524/page.htm'
                 title='刘建忠（有机化学）'>刘建忠（有机化学）</a></span>
            <span class="news_meta">2025-02-02</span>
          </li>
    """
    out, seen = [], set()
    blocks = re.findall(
        r'<span[^>]*class="[^"]*news_title[^"]*"[^>]*>(.*?)</span>', page, re.S | re.I)
    if not blocks:
        blocks = re.findall(
            r'<[^>]*class="[^"]*news_title[^"]*"[^>]*>(.*?)</', page, re.S | re.I)
    for b in blocks:
        for href, tattr, inner in _anchors(b):
            rec = _person(inner, tattr, base, href, section)
            if not rec:
                continue
            key = rec["name"] + "|" + rec["url"]
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    return out


def eng_wp_newsinfo_card(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：`li.itemhover` + `div.tit`（中科大生医部实测）。

    列表页只给姓名+链接；方向在详情页正文里（detail=fetch）。
    """
    out, seen = [], set()
    for m in re.finditer(r'<li[^>]*class="[^"]*itemhover[^"]*"[^>]*>(.*?)</li>',
                         page, re.S | re.I):
        b = m.group(1)
        nm = re.search(r'<div[^>]*class="tit"[^>]*>(.*?)</div>', b, re.S | re.I)
        href = ""
        hm = re.search(r'<a[^>]+href="([^"]+page\.htm)"', b, re.I)
        if hm:
            href = hm.group(1)
        for _h, _t, _i in _anchors(b):
            if _h and not href:
                href = _h
            break
        rec = _person(strip_tags(nm.group(1)) if nm else "", "", base, href, section)
        if not rec:
            continue
        if rec["name"] in seen:
            continue
        seen.add(rec["name"])
        out.append(rec)
    return out


def eng_wp_teacher_card(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：`li.list-item` + `h3` + `span.direction` + `span.email`（浙大生科院实测）。

    列表页**已含方向与邮箱**，无需进详情页。
    """
    out, seen = [], set()
    for m in re.finditer(r'<li[^>]*class="[^"]*list-item[^"]*"[^>]*>(.*?)</li>',
                         page, re.S | re.I):
        b = m.group(1)
        nm = re.search(r"<h3>\s*(.*?)\s*(?:<span|</h3>)", b, re.S | re.I)
        if not nm:
            continue
        dm = re.search(r'class="direction">(.*?)</span>', b, re.S | re.I)
        em = re.search(r'class="email">(.*?)</span>', b, re.S | re.I)
        href = ""
        hm = re.search(r'<a[^>]+href="([^"]+)"', b, re.I)
        if hm:
            href = hm.group(1)
        rec = _person(squash(strip_tags(nm.group(1))), "", base, href, section)
        if not rec:
            continue
        if dm:
            rec["directions"] = _split_dirs(strip_tags(dm.group(1)), cfg)
            rec["_dir_from_title"] = bool(rec["directions"])
        if em:
            rec["email"] = squash(strip_tags(em.group(1)))
        key = rec["name"] + "|" + rec["url"]
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def _split_dirs(raw: str, cfg: dict) -> list[str]:
    """方向串 → 条目（分隔符含 ·•・|/ 等站群常用分隔）。"""
    if not raw:
        return []
    pat = (cfg.get("parse_rules", {}).get("direction_split") or "[，,、；;]")
    pat = pat.rstrip("]") + r"·•・|｜/／]"
    out = []
    for p in re.split(pat, raw):
        t = squash(p).strip("  ·-—，,。.；;：:、")
        t = re.sub(r"^(?:主要)?研究(?:方向|领域|兴趣)\s*[：:]\s*", "", t)
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


def eng_generic_cluster(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：**形态聚类**（无固定 class 的自建站，实测 SJTU 生科院）。

    不依赖任何站点特有 class：锚定「形如人名的链接」，再按 URL 形态聚类，
    取规模足够的簇（导航链接形态各异，天然被排除）。
    """
    lt = (cfg.get("list_types", {}).get("generic_cluster") or {})
    min_n = int(lt.get("cluster_min_n", 8))
    min_share = float(lt.get("cluster_min_share", 0.18))

    cands = []
    for m in re.finditer(r"<a\s+([^>]*?)>(.*?)</a>", page, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        hm = re.search(r'href=["\']([^"\']+)["\']', attrs, re.I)
        if not hm:
            continue
        href = html_unescape_attr(hm.group(1))
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        tm = re.search(r'title=["\']([^"\']*)["\']', attrs, re.I)
        txt = squash(strip_tags(inner))
        tattr = html_unescape_attr(tm.group(1)) if tm else ""
        name = txt if is_person_name(txt) else (tattr if is_person_name(tattr) else "")
        if not name:
            continue
        cands.append((url_signature(href), name, tattr, href))

    if not cands:
        return []
    from collections import defaultdict
    by: dict[str, list] = defaultdict(list)
    for sig, name, tattr, href in cands:
        by[sig].append((name, tattr, href))

    total = len(cands)
    kept, seen = [], set()
    accepted_sigs: set[str] = set()
    for sig, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(items) < min_n or len(items) / total < min_share:
            continue
        accepted_sigs.add(sig)
        for name, tattr, href in items:
            if href in seen:
                continue
            seen.add(href)
            # 职称常在 title 属性里（SJTU 的 data-name 即职称）；
            #   方向取**锚点所在块**的文本，由下游 strip 判据过滤。
            pos = page.find(f'href="{href}"')
            block = enclosing_block(page, pos) if pos >= 0 else ""
            rec = {"name": name, "title": tattr, "directions": [],
                   "email": "", "url": clean_absolute(base, href),
                   "section": section, "_block": block}
            kept.append(rec)

    # ── 补充：**没有个人主页**的老师 ──
    #   实测 SJTU：16 人的 href 是 `javascript:alert('暂无开通个人主页')`，
    #     只按 href 聚类会把他们整体漏掉（208 → 186）。
    #     判据：其所在**容器**与已被接受的簇相同 → 视为同一批教师。
    if accepted_sigs:
        acc_names = {r["name"] for r in kept}
        conts: dict[str, int] = {}
        for r in kept:
            pos = page.find(f'href="{r["url"]}"') if r["url"] else -1
            if pos >= 0:
                cs = container_signature(page, pos)
                conts[cs] = conts.get(cs, 0) + 1
        good_conts = {c for c, n in conts.items() if n >= min_n}
        for m in re.finditer(r"<a\s+([^>]*?)>(.*?)</a>", page, re.S | re.I):
            attrs, inner = m.group(1), m.group(2)
            hm = re.search(r'href=["\']([^"\']+)["\']', attrs, re.I)
            href = html_unescape_attr(hm.group(1)) if hm else ""
            if href and not href.startswith("javascript:"):
                continue                      # 已有 href 的上一轮已处理
            txt = squash(strip_tags(inner))
            tm = re.search(r'title=["\']([^"\']*)["\']', attrs, re.I)
            tattr = html_unescape_attr(tm.group(1)) if tm else ""
            name = txt if is_person_name(txt) else (tattr if is_person_name(tattr) else "")
            if not name or name in acc_names:
                continue
            if container_signature(page, m.start()) not in good_conts:
                continue
            acc_names.add(name)
            # 无主页者 url 留空 —— 如实反映「官网未开通」，不臆造链接
            kept.append({"name": name, "title": tattr, "directions": [],
                         "email": "", "url": "", "section": section,
                         "_no_homepage": True, "_block": ""})
    return kept


def eng_tsites_list(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：**学校教师主页系统**（tsites：faculty.*.edu.cn / person.*.edu.cn）。

    [WARN] 本流程**不以它为主数据源**（覆盖面不全、大量页面无方向栏位）；
       仅当院系官网不可用时作为兜底，且必须在报告里注明来源。
    """
    out, seen = [], set()
    for m in re.finditer(
            r'<a[^>]+href="([^"]*(?:teacher|jsp|info)[^"]*)"[^>]*>(.*?)</a>',
            page, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        rec = _person(strip_tags(inner), "", base, href, section)
        if not rec:
            continue
        if rec["name"] in seen:
            continue
        seen.add(rec["name"])
        out.append(rec)
    return out


# ══════════════════════════════════════════════════════════════
# 渲染 / 交互家族（第一层直连拿不到时启用）
#
# 分工（对应 §5.1：师资页属公开元数据，直连为第一层，失败转 CDP）
#   · render      —— 整页 JS 渲染后仍可由家族正则解析（SPA 外壳站）
#   · interactive —— 数据靠**点击分类**异步加载，必须驱动真实浏览器
#                    （实测南大化学：11 个学科链接是 javascript:void(0)，
#                      渲染只出默认院士 3 人，点击才出 236 人）
# ══════════════════════════════════════════════════════════════
def eng_render(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：渲染后解析。

    `page` 若已是渲染产物（HTML），按顺序试各直连家族，取**第一个有结果**的。
    阈值取 1（而非某个"看起来像教师名录"的条数）：
      渲染后的页面可能本身就很小（如某研究所只有 3 人），
      用大阈值会把合法的小名录判成空。真正的规模/污染判断交给
      `score_candidate`（有指纹、计数对账、导航词占比三重判据），
      不在这一层重复设卡。
    """
    for fn in (eng_wp_news_list, eng_wp_teacher_card, eng_wp_newsinfo_card):
        got = fn(page, base, section, cfg)
        if got:
            return got
    return eng_generic_cluster(page, base, section, cfg)


def eng_interactive(page: str, base: str, section: str, cfg: dict) -> list[dict]:
    """家族：**交互枚举**（点击分类）。真正的取数在 faculty_interact.py。

    这里只声明该 section 需要交互；`harvest_faculty` 会据此调用
    `faculty_interact.enumerate_by_clicking`，再把结果转成本函数要求的形状。
    若 `page` 里已经带了 `__INTERACTIVE_RESULT__`（上游注入的枚举结果），
    则直接解析它 —— 这样离线自检也能覆盖本家族。
    """
    marker = "__INTERACTIVE_RESULT__"
    if marker in page:
        blob = page.split(marker, 1)[1].strip()
        try:
            data = json.loads(blob)
        except Exception:
            return []
        out = []
        for t in (data.get("teachers") or []):
            nm = squash(t.get("name") or "")
            if not is_person_name(nm):
                continue
            out.append({"name": nm, "title": t.get("title") or "",
                        "directions": [], "email": "",
                        "url": clean_absolute(base, t.get("url") or ""),
                        "section": section})
        return out
    return []


ENGINES = {
    "wp_news_list": eng_wp_news_list,
    "wp_newsinfo_card": eng_wp_newsinfo_card,
    "wp_teacher_card": eng_wp_teacher_card,
    "generic_cluster": eng_generic_cluster,
    "tsites_list": eng_tsites_list,
    "render": eng_render,
    "interactive": eng_interactive,
}

# 需要真实浏览器参与的两个家族
BROWSER_ENGINES = {"render", "interactive"}

# 家族识别用的结构指纹（命中即加分）
_FINGERPRINTS = {
    "wp_news_list": [r'class="[^"]*news_title', r'id="wp_news_w\d+"',
                     r'class="[^"]*news_list'],
    "wp_newsinfo_card": [r'class="[^"]*itemhover', r'class="tit"'],
    "wp_teacher_card": [r'class="[^"]*list-item', r'class="direction"'],
    "tsites_list": [r'tsites\.', r'xyjslb\.jsp', r'faculty\.[a-z]+\.edu\.cn'],
}


def detect_engines(page: str, base: str = "", section: str = "",
                   cfg: dict | None = None) -> list[dict]:
    """对一页判定「属于哪些家族」，按抽取人数降序返回。

    返回 [{engine, count, fingerprint_hits, sample}]，供自动发现评分与人工解释。
    """
    cfg = cfg or {}
    results = []
    for name, fn in ENGINES.items():
        try:
            ppl = fn(page, base, section, cfg)
        except Exception:
            ppl = []
        hits = sum(1 for p in _FINGERPRINTS.get(name, [])
                   if re.search(p, page, re.I))
        if ppl or hits:
            results.append({"engine": name, "count": len(ppl),
                            "fingerprint_hits": hits,
                            "sample": [p["name"] for p in ppl[:8]]})
    # 人数优先，其次指纹
    results.sort(key=lambda r: (-r["count"], -r["fingerprint_hits"]))
    return results


def parse_with(engine: str, page: str, base: str, section: str,
               cfg: dict) -> list[dict]:
    fn = ENGINES.get(engine)
    return fn(page, base, section, cfg) if fn else []


def page_count(page: str) -> int:
    """推断总页数：优先「页码 x / y」，退化到 listN.htm 的 N。"""
    t = strip_tags(page)
    m = re.search(r"页码[^0-9]{0,20}(\d+)\s*/\s*(\d+)", t)
    if m:
        return max(1, int(m.group(2)))
    ns = [int(x) for x in re.findall(r"[Ll]ist(\d+)\.(?:htm|psp)", page)]
    return max(ns) if ns else 1


def declared_total(page: str) -> int:
    """官网自称的总条数（用于对账；取不到返回 0，**不臆造**）。"""
    t = strip_tags(page)
    for pat in (r"共\s*(\d+)\s*(?:条|人|位|个|名)",
                r"总共[^0-9]{0,12}(\d+)"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return 0


def looks_js_rendered(page: str, parsed_count: int) -> bool:
    """判断「页面看起来是空壳、内容靠 JS/AJAX 填」——如实报告，不硬凑。

    实测：南京大学化学化工学院师资页的 `<ul class="teacher-list">` 为空，
      教师数据由 JS 拉取；直连只能拿到 0 人。这种情况必须如实说明，
      而不是把页脚友情链接当成教师交差。
    """
    if parsed_count > 0:
        return False
    hits = 0
    for pat in (r'class="[^"]*teacher-list[^"]*"\s*>\s*</ul>',
                r"ajax", r"getTeacher", r"teacherList", r"\.load\(",
                r"wp_paging"):
        if re.search(pat, page, re.I):
            hits += 1
    return hits >= 1


def score_candidate(engine: str, people: list[dict], pages: int, declared: int,
                    fp_hits: int) -> tuple[str, int, list[str]]:
    """给「某引擎在某页上的抽取结果」打分并定性。

    这是**复用模式的质量闸门**：没有它，自动发现会把导航链接当教师交差
    （实测 USTC 化材「各系师资」页：抽到 13 条"人"，实为栏目名）。

    返回 (verdict, score, reasons)：
      verdict ∈ {"accept", "review", "reject"}
        accept —— 高置信，可直接落进配置
        review —— 有信号但不确定，需 agent 看一眼（**唯一需要人工介入的情形**）
        reject —— 明确不合格

    判据以「**官网自称的条数**」为主锚：
      流程一贯要求「以抓取/接口返回为准」，官网自己给的 共N条 / 页码x/y
      是最可靠的交叉验证，比任何启发式都硬。
    """
    n = len(people)
    reasons: list[str] = []
    if n == 0:
        return "reject", 0, ["抽到 0 人"]

    score = 0
    # ① 结构指纹：命中说明确实属于该站群家族
    if fp_hits:
        score += 30
        reasons.append(f"结构指纹命中 {fp_hits} 处")

    # ② 导航污染：抽到的"人名"大量是栏目名 → 这是抓错页面的典型症状
    nav_hits = sum(1 for p in people if p["name"] in NAV_WORDS)
    nav_ratio = nav_hits / n
    if nav_ratio > 0.30:
        score -= 45
        reasons.append(f"导航词占比 {nav_ratio:.0%}（疑把栏目当教师）")
    elif nav_ratio > 0.10:
        score -= 12
        reasons.append(f"导航词占比 {nav_ratio:.0%}")

    # ③ 计数对账（主锚）
    est = n * max(1, pages)
    if declared:
        if est >= declared:
            score += 30
            reasons.append(f"计数对账通过：{n}×{pages}={est} ≥ 官网自称 {declared}")
        elif est >= declared * 0.8:
            score += 12
            reasons.append(f"计数对账接近：{est} vs 官网 {declared}")
        else:
            score -= 30
            reasons.append(f"计数对账不符：{est} 远少于官网自称 {declared}")
    else:
        reasons.append("官网未给出总条数（无法对账）")

    # ④ 指向详情页的比例：真教师条目通常几乎都有链接
    linked = sum(1 for p in people if p.get("url"))
    link_ratio = (linked / n) if n else 0.0
    if n and link_ratio >= 0.8:
        score += 10
        reasons.append(f"带主页链接 {linked}/{n}")

    # ⑤ 规模合理性（官网未给总条数时的主锚）
    #   实测依据：导航误判的规模在 9–18 条；而真实教师名录成规模
    #     （SJTU 208、USTC 教师总表 51）。25 条以上且几乎都指向详情页，
    #     几乎不可能是导航菜单 —— 故在**无官网计数可对账**时以此兜底。
    if n >= 25 and link_ratio >= 0.8:
        score += 30
        reasons.append(f"规模与链接形态符合教师名录（{n} 条，"
                       f"{link_ratio:.0%} 有详情页）")

    if score >= 40:
        return "accept", score, reasons
    if score >= 10:
        return "review", score, reasons
    return "reject", score, reasons


# ══════════════════════════════════════════════════════════════
# 详情页 → 研究方向（引擎链路的通用抽取）
# ══════════════════════════════════════════════════════════════
# 段落终止标题：出现即认为方向段结束
_STOP_HEAD = re.compile(
    r"^(?:代表论著|代表性论文|主要论文|发表文章|已发表文章|论文列表|"
    r"承担项目|科研项目|学术经历|教育经历|工作经历|获奖情况|教学情况|"
    r"学生培养|招生信息|招生招聘|联系方式|基本信息|个人简介|专利|"
    r"Selected\s+Publications|Publications|Research\s+Areas?)$", re.I)

# 页签/栏位名（不是方向内容）
_LABEL = re.compile(
    r"^(?:研究方向|研究领域|研究兴趣|科研方向|主页|首页|English|中文|"
    r"更多|详情|下载\s*CV|CV)$", re.I)


def extract_directions_detail(page: str, cfg: dict) -> list[str]:
    """从**详情页**抽研究方向 —— 覆盖三种实测模板。

    实测模板（都能正确处理）：
      ① 页签式：`<div id="research">研究方向</div>` 后逐行列出（上海交大生科院）
      ② 行内式：`主要研究方向：（1）…；（2）…`（上海交大 熊毅页）
      ③ 正文式：`主要研究方向为…` 后接编号条目（中科大 / 浙大）

    关键坑（实测 SJTU）：页首导航里也有一个「研究方向」锚点
      （`<a href="#research">研究方向</a>`），按"第一处命中"取会锁到导航、
      随后立即撞上终止标题（代表论著/承担项目）而返回空。
      故此处**枚举全部命中位置，取抽到条目最多的一处**。
    """
    body = re.sub(r"<script[^>]*>.*?</script>", " ", page, flags=re.S | re.I)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"</(?:p|div|li|tr|h\d|span)>|<br\s*/?>", "\n", body, flags=re.I)
    txt = _html.unescape(TAG.sub("", body))
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    lines = [squash(x) for x in txt.split("\n")]

    # ── ① 优先：页签容器 id="research"（最可靠，天然排除导航）──
    seg = _anchor_block_text(page, "research")
    if seg is not None:
        items = _items_from_lines([squash(x) for x in seg.split("\n")])
        if items:
            return items

    # ── ② 行内式：枚举全部「研究方向：」命中，取条目最多的一处 ──
    #   折行处理是这里的难点，两种真实形态必须都兼容：
    #     (a) 行内编号：`主要研究方向：（1）…；（2）…`，句子可能被排版任意折行
    #     (b) 逐行列出：`主要研究方向：` 换行后每行一个方向
    #   区分信号 = **编号标记**：出现 ≥2 个编号时按编号切（先并回一行），
    #   否则按行切。仅凭"取条目多的那个"会选错（会把 (a) 的折行碎片当条目）。
    best: list[str] = []
    for m in re.finditer(r"(?:主要|目前|当前)?研究(?:方向|领域|兴趣|内容)\s*"
                         r"(?:为|是|包括|有|如下)?\s*[：:]", txt):
        tail = txt[m.end():]
        # 在终止标题处截断，避免把后续栏目并进来
        stop = re.search(r"\n\s*(?:代表论著|代表性论文|主要论文|发表文章|"
                         r"承担项目|学术经历|教育经历|获奖情况|教学情况|"
                         r"学生培养|招生信息|联系方式)", tail)
        if stop:
            tail = tail[: stop.start()]
        numbered = re.findall(r"[（(]\s*[1-9]\d?\s*[)）]", tail)
        if len(numbered) >= 2:
            items = _items_from_lines([re.sub(r"\s*\n\s*", "", tail)])
        else:
            items = _items_from_lines([squash(x) for x in tail.split("\n")])
            # 逐行切只得到一条超长文本 → 多半是被折行的整段，改按并回处理
            if len(items) == 1 and len(items[0]) > SHORT_ITEM_MAX:
                alt = _items_from_lines([re.sub(r"\s*\n\s*", "", tail)])
                if len(alt) > len(items):
                    items = alt
        if len(items) > len(best):
            best = items
    if best:
        return best

    # ── ③ 标题行式：枚举全部命中行，取条目最多的一处 ──
    for i, ln in enumerate(lines):
        if not ln:
            continue
        if not re.match(
                r"^(?:主要|目前|当前)?研究(?:方向|领域|兴趣|内容)\s*[：:]?\s*$",
                ln):
            continue
        items = _items_from_lines(lines[i + 1:])
        if len(items) > len(best):
            best = items
    return best


def _anchor_block_text(page: str, anchor_id: str) -> str | None:
    """取 `id="<anchor_id>"` 所在块的可见文本（标签配平）。

    用配平而非懒正则 —— 懒正则会提前收尾，正文只剩几个字。
    """
    m = re.search(rf'id="{re.escape(anchor_id)}"', page, re.I)
    if not m:
        return None
    start = page.rfind("<", 0, m.start())
    if start < 0:
        return None
    tag_m = re.match(r"<([a-zA-Z0-9]+)", page[start:])
    if not tag_m:
        return None
    tag = tag_m.group(1).lower()
    depth, end = 0, None
    for mm in re.finditer(rf"<(/?){tag}\b[^>]*>", page[start: start + 20000],
                          re.I):
        if mm.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end = start + mm.end()
                break
    seg = page[start:end] if end else page[start: start + 4000]
    seg = re.sub(r"<script[^>]*>.*?</script>", " ", seg, flags=re.S | re.I)
    seg = re.sub(r"<!--.*?-->", " ", seg, flags=re.S)
    seg = re.sub(r"</(?:p|div|li|tr|h\d|span)>|<br\s*/?>", "\n", seg, flags=re.I)
    return _html.unescape(TAG.sub("", seg))


def _items_from_lines(lines: list[str], limit: int = 10) -> list[str]:
    """从若干行里收方向条目（兼容逐行式 / 行内编号式 / 行内分号式）。"""
    return _collect_items([], lines, limit)


# 编号条目：（1） / 1. / 1、 等
_NUM_ITEM = re.compile(r"^[（(]?\s*[1-9]\d?\s*[)）、.．]\s*(.+)$")


def _collect_items(seed: list[str], rest: list[str], limit: int = 10) -> list[str]:
    """从标题行后续文本收集方向条目。

    同时支持：
      · 逐行列出（每行一条）
      · 行内编号（（1）xxx；（2）yyy）
      · 行内分号分隔
    """
    out: list[str] = []
    truncated: list[str] = []

    def push(raw: str) -> None:
        t = squash(raw)
        t = re.sub(r"^[（(]?\s*[1-9]\d?\s*[)）、.．]\s*", "", t)
        t = t.strip("  ·-—，,；;：:、>")
        if not t or _LABEL.match(t):
            return
        # 站点把长条目截断成「…」，紧随其后会给完整版 → 只留完整版
        if t.endswith(("...", "…")):
            truncated.append(t.rstrip(".…"))
            return
        t = re.split(r"[。.]", t)[0].strip()
        if len(t) < 4 or len(t) > 120:
            return
        # 导航/栏位名混入（如「代表论著」被当成方向）→ 丢弃
        if t in NAV_WORDS:
            return
        if t not in out:
            out.append(t)

    emitted_num = False
    for ln in seed + rest:
        if not ln:
            continue
        if _STOP_HEAD.match(ln):
            break
        # 行内编号：切成多条
        parts = re.split(r"(?=[（(]\s*[1-9]\d?\s*[)）])", ln)
        for p in parts:
            p = squash(p)
            if not p:
                continue
            if _NUM_ITEM.match(p):
                push(p)
                emitted_num = True
            elif not emitted_num:
                push(p)
            elif len(p) >= 6:
                push(p)
        if len(out) >= limit:
            break
    # 丢弃「有截断稿且已有完整版」的项由 push 保证；此处仅兜底清理
    out = [x for x in out if x not in truncated][:limit]

    # ── 去「短条目 + 长叙述」重复 ──
    #   实测：站点常先在页签给出**短条目**，又在正文给一整段
    #     展开说明（同一方向的复述）。若都保留，同义方向会重复计票并污染归类。
    #     仅当**已有短条目**时，才丢弃"叙述腔"或"前缀扩展"的长条目 ——
    #     这样不会误伤那些本来就用长句表述方向的站。
    if len(out) > 1:
        short = [x for x in out if len(x) <= SHORT_ITEM_MAX]
        if short:
            narr = re.compile(r"我们|通过|综合运用|旨在|系统(?:挖掘|研究|解析)|"
                              r"解析|围绕|采用|手段|技术(?:与|和)方法")
            cleaned = []
            for x in out:
                if len(x) > SHORT_ITEM_MAX:
                    if narr.search(x):
                        continue
                    if any(x.startswith(s) for s in short):
                        continue
                cleaned.append(x)
            out = cleaned
    return out[:limit]


# 条目长度阈值：超过此长度的"方向"多半是展开叙述而非方向名
SHORT_ITEM_MAX = 45

