# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S2：学院官网师资页抓取（biomed.ustc.edu.cn 型）
#
# 为什么需要这个脚本
#   阶段 S 抓的是**教师个人主页系统**（faculty.ustc.edu.cn）。实测 237 人里有
#   62 人该系统**确实没有研究方向数据**（页签是「暂无内容」或只有空占位注释）。
#
#   但这些老师在**学院官网**（biomed.ustc.edu.cn）的师资页上有完整的
#   研究兴趣描述 —— 那是另一套站点、另一种模板：
#
#     师资队伍 → 院系师资 → {生命科学学院 smkxy | 基础医学院 jcyxy |
#                             临床医学院 lcyxy | 生物医学工程学院 swyxgcxy |
#                             附属第一医院 swx}
#     · 列表页分页：list.htm, list2.htm, list3.htm …（每页 28 条）
#     · 详情页：/<年>/<月日>/c<栏目id>a<文章id>/page.htm
#     · 研究方向写在正文里，线索词多样：
#         「主要研究兴趣：」「课题组主要研究方向为」「主要研究方向包括：」
#
# 设计要点
#   · 只用标准库（urllib），与 harvest_mentors.py 保持一致
#   · 列表 → 详情两段式缓存，断点可续
#   · 详情页正文很长（含大量论文列表），**只截取正文前段**做方向抽取，
#     避免把参考文献里的刊名当方向
#   · 与阶段 S 的数据按**姓名**合并（两站无共同 id），冲突时保留更完整的
#
# 用法：
#   uv run python 流程1/scripts/harvest_colleges.py --list-colleges
#   uv run python 流程1/scripts/harvest_colleges.py --out .work_cdp/colleges
#   uv run python 流程1/scripts/harvest_colleges.py --college smkxy --stage=list
#   uv run python 流程1/scripts/harvest_colleges.py --merge-into <roster_detail.json>
#
# 可移植配置：PAPER_WORKDIR
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

# ── UTF-8 兜底（幂等，与同目录其他脚本同判据）──


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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from logutil import get, journal  # type: ignore
except Exception:
    def get(name):                     # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)
        return _L()

    def journal(*a, **k):              # noqa: D103
        pass


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 (academic-use)")

BASE = "https://biomed.ustc.edu.cn"

# 院系师资的 5 个栏目（来自 https://biomed.ustc.edu.cn/smkxy/list.htm 的导航）
COLLEGES = {
    "smkxy":     "生命科学学院",
    "jcyxy":     "基础医学院",
    "lcyxy":     "临床医学院",
    "swyxgcxy":  "生物医学工程学院",
    "swx":       "附属第一医院",
}


# ══════════════════════════════════════════════════════════════
# 网络
# ══════════════════════════════════════════════════════════════
def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"取页面失败：{url} —— {last}")


_TAG = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return _html.unescape(_TAG.sub("", s)).strip()


_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, maxlen: int = 24) -> str:
    """把姓名变成安全的文件名片段，**保留中文**。

    ★ 为什么单独抽出来：旧写法 `re.sub(r'[^A-Za-z0-9]', '', name)` 会把
      中文姓名全部剥掉，导致 161 个存档都叫 `<aid>_x.html`，无法按姓名核对。
    """
    s = _WIN_BAD.sub("", (name or "").strip())
    s = re.sub(r"\s+", "", s)
    s = s.rstrip(". ")                      # Windows 不允许结尾的点/空格
    return s[:maxlen] or "x"


# ══════════════════════════════════════════════════════════════
# 列表页解析
#   结构：<a href="/2014/0319/c41923a735571/page.htm"> 姓名 </a>
#   链接形如 /<年>/<月日>/c<栏目id>a<文章id>/page.htm
# ══════════════════════════════════════════════════════════════
_PROFILE_LINK = re.compile(
    r'<a[^>]+href="(/(?P<y>\d{4})/(?P<md>\d{4})/c(?P<cid>\d+)a(?P<aid>\d+)/page\.htm)"'
    r'[^>]*>(?P<inner>.*?)</a>', re.S | re.I)


def parse_college_list(page: str) -> list[dict]:
    """从学院师资列表页提取 (姓名, 详情页URL)。"""
    out, seen = [], set()
    for m in _PROFILE_LINK.finditer(page):
        href = m.group(1)
        if href in seen:
            continue
        inner = re.sub(r"<img[^>]*>", "", m.group("inner"), flags=re.I)
        name = re.sub(r"\s+", " ", strip_tags(inner)).replace("\u200b", "").strip()
        # 去掉可能混入的职衔/单位尾巴
        name = re.sub(r"[（(].*?[）)]$", "", name).strip(" _·")
        if not name or len(name) > 20:
            continue
        # 纯英文名也接受（如 Andrew Macpherson）
        if not re.search(r"[\u4e00-\u9fff]|[A-Za-z]{2,}", name):
            continue
        seen.add(href)
        out.append({
            "name": name,
            "aid": m.group("aid"),
            "college_id": m.group("cid"),
            "url": BASE + href,
        })
    return out


def parse_total_pages(page: str) -> int:
    """从「总共 79 记录 ... 页码 1/3」推断总页数。"""
    text = strip_tags(page)
    m = re.search(r"页码\s*(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(2))
    m = re.search(r"list(\d+)\.htm[^>]*>\s*尾页", page)
    if m:
        return int(m.group(1))
    return 1


# ══════════════════════════════════════════════════════════════
# 详情页解析：研究方向
#
# 正文结构（实测）：
#   <div class="article"> … <p>正文段落</p> … <p><strong>发表文章：</strong></p>
#   研究方向写在正文段落里，线索词多样，且常常是编号列表。
#
# ⛔ 关键约束：正文后面跟着几十条参考文献，若整页扫描会把刊名当方向。
#    所以**只取「发表文章 / 代表性论文 / 联系方式」之前的正文**。
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# 正文分区
#
# ★ v10 改动：不再「截断」，改为**分区切分**。
#   旧实现遇到「联系方式」就把后面全砍掉，导致「代表性论文 / 招生招聘 /
#   联系方式」三节整体丢失（实测毕国强页正文 1714 字，而全页相关文本约 5000 字）——
#   而这三节恰恰是邮箱、电话、个人主页、招生说明的来源。
#   现在返回 {分区名: 正文}，各字段各取所需区间。
# ══════════════════════════════════════════════════════════════

# 分区标题（顺序即页面上的典型顺序）。用它们把正文切成若干段。
# ⛔ 不要放「招生」单字 —— 页头导航里也有，会把正文切碎。
_SECTION_PATTERNS = [
    ("research", r"主要研究方向及内容|主要研究方向|研究方向及内容|"
                 r"主要研究兴趣|主要研究领域|科研方向|研究方向"),
    ("papers",   r"代表性学术论文|代表性论文|主要论文|发表文章|已发表文章|"
                 r"论文列表|Selected\s+Publications|Publications\b"),
    ("recruit",  r"招生招聘|招生信息|招收研究生|招聘信息"),
    ("contact",  r"联系方式|Contact\s+Information"),
    ("bio",      r"个人简介|简历|工作经历|教育经历"),
    ("awards",   r"获奖信息|荣誉奖励|学术奖励"),
    ("projects", r"科研项目|主持项目|在研项目"),
]
_SECTION_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in _SECTION_PATTERNS), re.I)

# 兼容旧调用：正文里第一个"资料性分区"的位置（用于只在方向区之前取文本）
_BODY_END = re.compile(r"代表性学术论文|代表性论文|主要论文|发表文章|"
                       r"论文列表|已发表文章|联系方式", re.I)

# 研究方向线索词
# ★ v10：补两类漏掉的写法 ——
#   ① 「主要研究方向及内容：」（多了"及内容"，旧正则匹配不上）
#   ② 「研究方向」**独立成行**（后面不跟冒号，李小龙页如此）
_LEAD = re.compile(
    r"(?:课题组|实验室|团队)?\s*(?:主要|目前|当前)?\s*"
    r"(?:研究(?:方向|领域|兴趣|内容|方向及内容)|科研(?:方向|领域))\s*"
    r"(?:主要)?\s*(?:为|是|包括|有|如下|：|:)"
    r"|^(?:主要)?研究(?:方向|领域|兴趣|内容)\s*$"
    r"|Research\s+(?:Focus|Interests?|Areas?)\s*(?:include|are|:)?",
    re.I | re.M)


def _slice_balanced_div(page: str, start: int) -> str:
    """从 `start`（某 <div ...> 之后）起，按标签配平取出该 div 的完整内容。

    ★ 为什么必须配平：正文里嵌套大量 div，用非贪婪正则会在第一个
      内层 `</div>` 处提前收尾，导致正文只剩标题（实测 5 个字）。
    """
    depth = 1
    pos = start
    for m in re.finditer(r"<(/?)div\b[^>]*>", page[start:], re.I):
        if m.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return page[start: start + m.start()]
    return page[start:]


def extract_article_body(page: str) -> str:
    """取**完整**文章正文（不再截断）。

    ★ 实测的页面结构（biomed.ustc.edu.cn，学校站群系统）：
        <div class="Content"><div class="newsinfo"> …正文… </div></div>
      正文由大量 `<span>` 碎片拼成（每个词/短语一个 span），
      所以 strip_tags 之后要再收敛空白，否则方向句会被空格切碎。

    ⛔ 踩过的坑：
      ① 早期用 `class="...article|content|..."` 匹配容器 → 只拿到页头面包屑
         （131 字），正文全丢。现优先精确匹配 `newsinfo`。
      ② 用懒匹配 `(.*?)</div></div>` → 在第一个内层 </div> 收尾（剩 5 字）。
         现按标签配平取容器。
      ③ 在「联系方式」处截断 → 论文/招生/联系方式三节全丢（本版已改）。
    """
    # ① 优先：学校站群的文章容器（按标签配平，避免懒匹配提前收尾）
    body_html = ""
    m = re.search(r'<div[^>]*class="[^"]*newsinfo[^"]*"[^>]*>', page, re.I)
    if m:
        body_html = _slice_balanced_div(page, m.end())
    if not body_html or len(body_html) < 200:
        # ② 次选：任意 Content 容器中**最长**的那个（页头也有 Content）
        best = ""
        for cm in re.finditer(r'<div[^>]*class="[^"]*Content[^"]*"[^>]*>', page, re.I):
            seg = _slice_balanced_div(page, cm.end())
            if len(seg) > len(best):
                best = seg
        if len(best) > len(body_html):
            body_html = best

    body_html = re.sub(r"<script[^>]*>.*?</script>", " ", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<style[^>]*>.*?</style>", " ", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<!--.*?-->", " ", body_html, flags=re.S)

    # 段落边界 → 换行，保证方向条目不会被拼成一行
    body_html = re.sub(r"</(?:p|div|li|tr|h\d)>|<br\s*/?>", "\n", body_html,
                       flags=re.I)
    text = strip_tags(body_html)
    # span 碎片之间常有多余空白，方向句里不该有空格，统一收敛
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def split_sections(body: str) -> dict[str, str]:
    """把正文按已知分区标题切成 {分区名: 文本}。

    同时给出两个派生键：
      · `_head`  第一个分区之前的文本（职衔行 / 履历 / 任职都在这里）
      · `_all`   全文（兜底用）

    ★ 同一个分区名出现多次时，取**最长**的那一段 —— 页头导航里也会出现
      「研究方向」这类词，那一段通常极短。
    """
    out: dict[str, str] = {}
    marks = []
    for m in _SECTION_RE.finditer(body):
        name = next((k for k, v in m.groupdict().items() if v), None)
        if name:
            marks.append((m.start(), name))

    if not marks:
        out["_head"] = body
        out["_all"] = body
        return out

    out["_head"] = body[: marks[0][0]]
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        seg = body[pos:end]
        # 同名的取最长（避开导航里的短命中）
        if name not in out or len(seg) > len(out[name]):
            out[name] = seg
    out["_all"] = body
    return out


def parse_college_profile(page: str) -> dict:
    """从学院详情页抽取**结构化信息**。

    返回字段（全部来自同一份 HTML，不额外发请求）：
      title_line          职衔行（讲席教授 / 博导 / 国家杰青 …）
      directions[]        研究方向条目
      direction_intro     方向区里的介绍性段落
      direction_source    方向来源说明
      bio                 履历/简介段（截断到 800 字）
      email / phone / homepage
      recruit_note        招生说明
      representative_papers[]  代表性论文（前若干条）
      raw_body_chars      正文长度（质量指标）
      sections_found[]    命中的分区名（调试用）
    """
    info: dict = {
        "title_line": "", "directions": [], "direction_intro": "",
        "direction_source": "", "bio": "", "email": "", "phone": "",
        "homepage": "", "recruit_note": "", "representative_papers": [],
        "raw_body_chars": 0, "sections_found": [],
    }
    body = extract_article_body(page)
    info["raw_body_chars"] = len(body)
    if not body:
        return info

    sec = split_sections(body)
    info["sections_found"] = sorted(k for k in sec if not k.startswith("_"))

    # ── 职衔行：正文头部里第一段含职衔词且不长的行 ──
    for ln in (sec.get("_head") or "").split("\n"):
        ln = ln.strip()
        if 4 <= len(ln) <= 120 and re.search(
                r"(教授|研究员|博导|博士生导师|讲师|工程师|院士|杰青|优青|"
                r"人才项目|长江学者)", ln) and not re.search(r"@|毕业于|年获", ln):
            info["title_line"] = ln
            break

    # ── 研究方向 ──
    dirs, src, intro = _parse_direction_section(sec)
    info["directions"] = dirs
    info["direction_source"] = src
    info["direction_intro"] = intro

    # ── 履历/简介：头部里最长的一段叙述文本 ──
    head = sec.get("_head") or ""
    paras = [p.strip() for p in head.split("\n") if len(p.strip()) >= 40]
    if paras:
        info["bio"] = max(paras, key=len)[:800]

    # ── 联系方式 ──
    all_text = sec.get("_all") or body
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", all_text)
    if m:
        info["email"] = m.group(0)
    m = re.search(r"(?:电话|Tel|TEL)[：:\s]*([\d\-‐—\s]{7,20})", all_text)
    if m:
        info["phone"] = re.sub(r"\s+", "", m.group(1)).strip("-")
    m = re.search(r"(?:实验室网址|个人主页|主页|网址)\s*[：:]\s*(https?://\S+)", all_text)
    if not m:
        # 兜底：正文里第一个非学校站群的外部 http 链接
        for mm in re.finditer(r"https?://([^\s，。；、]+)", all_text):
            host = mm.group(1)
            if "ustc.edu.cn" not in host and "biomed" not in host:
                m = mm
                break
    if m:
        info["homepage"] = m.group(1) if m.lastindex else m.group(0)

    # ── 招生说明 ──
    rec = sec.get("recruit") or ""
    if rec:
        note = re.sub(r"^(?:招生招聘|招生信息|招收研究生|招聘信息)\s*[：:]?\s*", "", rec)
        note = re.sub(r"\s+", " ", note).strip()
        info["recruit_note"] = note[:300]

    # ── 代表性论文（前 6 条，仅作参考，不参与方向判定）──
    pap = sec.get("papers") or ""
    if pap:
        items = re.findall(r"(?:^|\n)\s*(?:\d+[、.)．]|\[\d+\])?\s*([^\n]{30,400})", pap)
        clean_p = []
        for it in items:
            it = re.sub(r"\s+", " ", it).strip()
            if re.search(r"\(?(?:19|20)\d{2}\)?", it) and len(it) >= 40:
                clean_p.append(it)
            if len(clean_p) >= 6:
                break
        info["representative_papers"] = clean_p

    return info


def _parse_direction_section(sec: dict) -> tuple[list[str], str, str]:
    """从已切分的分区里抽研究方向。返回 (方向列表, 来源, 介绍段)。

    ★ v10 核心修复：旧实现要求「线索词之后的**首个非空行**必须是编号条目」，
      否则直接放弃。但实测毕国强页是「线索词 → 介绍性长句 → 1、2、3」，
      于是返回 0 条（该页明明有 3 条方向）。全量因此漏掉 62 页。

      现改为**区间扫描**：从线索词起向后最多 2500 字，收集编号条目；
      遇到下一个分区标题、或连续多行非条目时停止。
    """
    # 方向区优先；没有该分区则在 _head 里找线索词（有的页面不设分区标题）
    zone = sec.get("research") or ""
    if not zone:
        zone = sec.get("_head") or sec.get("_all") or ""
    if not zone:
        return [], "", ""

    m = _LEAD.search(zone)
    if not m:
        return [], "", ""

    tail = zone[m.end():]
    # 去掉紧跟在冒号后的空白/换行
    tail = tail.lstrip("　 \t：:\n")

    intro = ""
    lines = tail.split("\n")
    numbered_re = re.compile(r"^\s*[（(]?\s*[1-9]\d?\s*[)）、.．]\s*\S")
    # 分区结束信号：遇到这些行说明方向区已过（进入论文/招生/联系方式等）
    stop_re = re.compile(
        r"^(?:代表性?(?:学术)?论文|主要论文|发表文章|已发表文章|论文列表|"
        r"招生招聘|招生信息|招收研究生|招聘信息|联系方式|"
        r"获奖信息|荣誉奖励|科研项目|主持项目|在研项目|"
        r"Selected\s+Publications|Publications|Contact)", re.I)

    picked: list[str] = []
    blanks = 0
    scanned = 0

    for ln in lines:
        s = ln.strip()
        scanned += len(s)
        if scanned > 3000:
            break
        if not s:
            blanks += 1
            # 连续的空白行只在**尚未收集到任何条目**时才可能终止
            if picked and blanks >= 6:
                break
            continue
        blanks = 0
        if stop_re.match(s):
            break
        if numbered_re.match(s):
            picked.append(s)
            continue
        if picked:
            # ★ 编号条目后面常跟一段**说明文字**（毕国强页：1、神经系统的跨尺度解析
            #   后面就是一段长描述）。这些说明行本身不是新条目，但**不能中断扫描** ——
            #   旧实现一遇到非编号行就 break，导致只收到第 1 条（实测毕国强 1/3、
            #   李小龙 4/9）。现在继续扫，只有遇到分区结束信号或长段空白才停。
            continue
        # 还没进入编号段：这是介绍性文字（毕国强 / 曹灿 都属于这种）
        if len(s) >= 20:
            intro = (intro + " " + s).strip() if intro else s

    # 组装结果
    out: list[str] = []
    if picked:
        for it in picked:
            t = _clean_direction_item(it)
            if t and t not in out:
                out.append(t)
            if len(out) >= 12:
                break
    else:
        # 无编号：按分号/编号切分整段（曹灿式「课题组主要研究方向为…」）
        parts = re.split(r"[（(]?\s*\d+\s*[)）]、?|[；;]", intro or tail[:400])
        for part in parts:
            t = _clean_direction_item(part)
            if t and t not in out:
                out.append(t)
            if len(out) >= 12:
                break

    return out, "学院官网", intro[:400]


def _clean_direction_item(t: str) -> str:
    """清洗单条方向：去编号 → 截句 → 剥基金括号 → 截成果从句 → 判噪。"""
    t = re.sub(r"^\s*[（(]?\s*[1-9]\d?\s*[)）、.．]\s*", "", t)   # 去编号
    t = re.sub(r"\s+", " ", t).strip("　 ·-—，,。.；;：:、")
    t = re.split(r"[。.]", t)[0].strip()                          # 截到第一个句号
    # 剥掉基金/项目括号
    t = re.sub(r"[（(][^（()）]*(?:基金|项目|子课题|973|863|重点研发|"
               r"面上项目|中科院|资助)[^（()）]*[)）]", "", t).strip()
    # 截掉尾部的论文/成果从句
    t = re.split(r"(?:论文(?:发表|列表)|发表论文|SCI\s*论文|代表性论文|已发表|"
                 r"主持(?:国家|项目)|相关研究工作|研究成果|"
                 r"[、，,]\s*(?:Nature|Science|Cell|EMBO|PNAS)\b)", t)[0]
    # ★ v10：条目尾巴上粘着的英文引文（"…检测与功能验证 Cong M, Yang Y, Tong H, …"）
    #   特征：一段中文之后，紧跟「英文姓 首字母, 英文姓 首字母, …」
    #   处理：找到第一个「英文姓 + 空格 + 首字母」序列的起点，从那里截断
    m = re.search(r"(?:\s|^)[A-Z][a-z]{2,}\s+[A-Z]{1,3}[,.]", t)
    if m and re.search(r"[\u4e00-\u9fff]", t[:m.start()]):
        t = t[: m.start()]
    # ★ v10：整条就是引文（含 # * 标记的英文姓名串），直接丢弃
    if len(re.findall(r"[A-Za-z]+\s+[A-Za-z]{1,3}#|[A-Za-z]+\*", t)) >= 2 and \
            not re.search(r"[\u4e00-\u9fff]{6,}", t):
        return ""
    # ★ v10：以「招生方向」「招聘方向」开头的不是研究方向
    if re.match(r"^(?:招生|招聘|报考|招收)", t):
        return ""
    t = t.strip("　 ·-—，,。.；;：:、")
    if not (6 <= len(t) <= 200):
        return ""
    if _is_noise(t):
        return ""
    return t


def parse_directions(page: str) -> tuple[list[str], str]:
    """兼容旧接口：只返回 (方向列表, 来源)。"""
    info = parse_college_profile(page)
    return info["directions"], info["direction_source"]


_NOISE = re.compile(
    r"^(?:教授|副教授|研究员|博士生导师|硕士生导师|联系方式|邮箱|电话|地址|邮编|"
    r"男|女|博士|学士|简介|个人简介)\b"
    r"|@[A-Za-z0-9.\-]+\.(?:com|cn|edu|org)"
    r"|(?:\d{4}\s*年|\d{4}\s*[-–~]\s*\d{4})"     # 履历年份
    r"|^(?:19|20)\d{2}$")

# 期刊 / 论文痕迹：方向句里不该出现刊名与年卷页
#   实测漏出来的样本：
#     「…结构基础，并在此基础上开发肿瘤治疗新型药物」(OK)
#     「Nature Communications (2016」                        ← 刊名+年份
#     「Cell Research、EMBO J」                              ← 刊名
#     「SCI论文53篇，其中以通讯作者和第一作者身份发表SCI论文17篇」 ← 成果统计
#     「和青年科学基金项目（2015」                            ← 基金
_JOURNAL_HINT = re.compile(
    r"\b(?:Nature|Science|Cell|NEJM|Lancet|EMBO|PNAS|eLife|"
    r"J\s*Biol\s*Chem|Nat\s*Chem\s*Biol|Nat\s*Commun|Nat\s*Struct|"
    r"Cell\s*Res|Nucleic\s*Acids|Mol\s*Cell|Genes\s*Dev|Plant\s*Cell|"
    r"Trends\s+in|Current\s+Opinion|Journal\s+of|Frontiers\s+in)\b", re.I)
_ACHIEVEMENT_HINT = re.compile(
    r"SCI\s*论文\s*\d+\s*篇|影响因子|被引\s*\d+\s*次|"
    r"发表于\s*(?:Nature|Science|Cell|《)|"
    r"基金项目|青年科学基金|面上项目|国家重点研发|"
    r"通讯作者|第一作者|共同一作|\(20\d{2}\)|（20\d{2}）|"
    r"博士，教授|博士\s*教授")


def _is_noise(t: str) -> bool:
    if not t or len(t) < 6:
        return True
    if _NOISE.search(t):
        return True
    # ★ 面包屑导航（「研究领域 当前位置：中文主页 >> 科学研究 >> 研究领域」）
    #   这类行含"研究方向"字样却毫无内容，是最常见的假阳性
    if "当前位置" in t or "中文主页" in t or ">>" in t:
        return True
    # ★ 多个栏目名串成的导航行（「科学研究 研究领域」「教学研究 教学资源」）
    NAV = (r"(?:科学研究|研究领域|研究方向|研究兴趣|教学研究|教学资源|"
           r"授课信息|教学成果|论文成果|专利|著作成果|科研项目|获奖信息|"
           r"招生信息|学生信息|我的相册|教师博客|社会兼职|教育经历|"
           r"工作经历|基本信息|团队成员|其他联系方式)")
    if len(re.findall(NAV, t)) >= 2:
        residue = re.sub(NAV, "", t)
        residue = re.sub(r"[\s·\-—、,，。;；]+", "", residue)
        if len(residue) < 6:
            return True
    # ★ 论文列表：英文姓名 + # / * 标记连排
    if len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}#|,\s*[A-Z]{1,3}\s*,", t)) >= 2:
        return True
    # ★ 单个引文片段：「Duan Y, Young R, Schnabl B」这类无中文、以姓名+缩写
    #   逗号连排的串，即使只有 2 个人名也不是方向
    if not re.search(r"[\u4e00-\u9fff]", t) and \
            len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}\b", t)) >= 2:
        return True
    # 刊名 / 论文成果 / 基金 —— 都不是研究方向
    if _JOURNAL_HINT.search(t):
        return True
    if _ACHIEVEMENT_HINT.search(t):
        return True
    # 必须含中文或足够长的英文
    if not re.search(r"[\u4e00-\u9fff]|[A-Za-z]{6,}", t):
        return True
    # 期刊名式短串（全大写缩写 + 逗号很少）
    if re.fullmatch(r"[A-Za-z\s\.,&()\-]{0,60}", t) and len(t) < 25:
        return True
    # 以编号/数字开头但内容极短的碎片
    if re.match(r"^\d+[．.、)）]?\s*$", t):
        return True
    return False


# ══════════════════════════════════════════════════════════════
# 阶段实现
# ══════════════════════════════════════════════════════════════
def stage_list(d: pathlib.Path, log, only: str = "") -> dict:
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "raw_list"
    raw.mkdir(exist_ok=True)

    people, seen_url = [], set()
    colleges = {only: COLLEGES[only]} if only else COLLEGES
    for cid, cname in colleges.items():
        log.info(f"[{cname}] 抓列表 …")
        first = fetch(f"{BASE}/{cid}/list.htm")
        (raw / f"{cid}_1.html").write_text(first, encoding="utf-8")
        pages = parse_total_pages(first)
        got = parse_college_list(first)
        log.info(f"  第 1/{pages} 页 → {len(got)} 人")
        for pg in range(2, pages + 1):
            try:
                page = fetch(f"{BASE}/{cid}/list{pg}.htm")
            except Exception as e:
                log.warn(f"  ⚠ 第 {pg} 页失败：{e}")
                continue
            (raw / f"{cid}_{pg}.html").write_text(page, encoding="utf-8")
            g = parse_college_list(page)
            log.info(f"  第 {pg}/{pages} 页 → {len(g)} 人")
            got.extend(g)
            time.sleep(0.4)
        for p in got:
            p["college"] = cname
            if p["url"] not in seen_url:
                seen_url.add(p["url"])
                people.append(p)
        time.sleep(0.4)

    (d / "college_list.json").write_text(
        json.dumps(people, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✓ 列表完成：{len(people)} 人（{len(colleges)} 个学院）")
    return {"count": len(people)}


def stage_detail(d: pathlib.Path, log, limit: int = 0) -> list[dict]:
    lf = d / "college_list.json"
    if not lf.exists():
        raise SystemExit(f"✗ 缺少 {lf}；先跑 --stage=list")
    people = json.loads(lf.read_text(encoding="utf-8"))

    df = d / "college_detail.json"
    done: dict = {}
    if df.exists():
        try:
            for rec in json.loads(df.read_text(encoding="utf-8")):
                done[rec["url"]] = rec
        except Exception:
            pass

    raw = d / "raw_detail"
    raw.mkdir(exist_ok=True)
    todo = [p for p in people if p["url"] not in done]
    if limit:
        todo = todo[:limit]
    log.info(f"待抓详情：{len(todo)} 人（已有 {len(done)}）")

    ok = hit = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            page = fetch(p["url"], timeout=30)
        except Exception as e:
            done[p["url"]] = {**p, "directions": [], "status": "fetch_failed",
                              "why": str(e)[:160]}
            fail += 1
            continue
        # ★ v10 修复存档命名：旧写法 `re.sub(r'[^A-Za-z0-9]','',name)`
        #   会把中文姓名整段剥掉，161 个存档全叫 `<aid>_x.html`，无法按姓名核对。
        #   现在保留中文（仅替换文件系统非法字符）。
        slug = f"{p['aid']}_{safe_filename(p['name'])}"
        (raw / f"{slug}.html").write_text(page, encoding="utf-8")

        info = parse_college_profile(page)
        dirs = info["directions"]
        rec = {**p, **info}
        if dirs:
            rec["status"] = "ok"
            rec["why"] = ""
            hit += 1
        else:
            rec["status"] = "no_direction"
            rec["why"] = "学院页未找到研究方向线索词"
        done[p["url"]] = rec
        ok += 1
        if i % 20 == 0 or i == len(todo):
            log.info(f"  [{i}/{len(todo)}] 成功 {ok} / 失败 {fail}｜"
                     f"有方向 {hit}")
        time.sleep(0.35)

    merged = [done.get(p["url"]) or {**p, "directions": [], "status": "pending"}
              for p in people]
    df.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    withdir = sum(1 for r in merged if r.get("directions"))
    log.info(f"✓ 详情完成：{withdir}/{len(merged)} 人有研究方向")
    return merged


def _rename_archives(d: pathlib.Path, log) -> None:
    """把 raw_detail 下 `<aid>_x.html` 之类旧命名改为 `<aid>_姓名.html`。"""
    raw = d / "raw_detail"
    df = d / "college_detail.json"
    if not raw.exists() or not df.exists():
        raise SystemExit(f"✗ 缺少 {raw} 或 {df}")
    det = json.loads(df.read_text(encoding="utf-8"))
    by_aid = {str(x["aid"]): x for x in det}

    renamed = 0
    for p in sorted(raw.glob("*.html")):
        aid = p.name.split("_", 1)[0]
        rec = by_aid.get(aid)
        if not rec:
            continue
        want = raw / f"{aid}_{safe_filename(rec['name'])}.html"
        if p.name != want.name:
            if want.exists():
                want.unlink()
            p.rename(want)
            renamed += 1
    log.info(f"✓ 存档改名完成：{renamed} 个")


def reparse_only(d: pathlib.Path, log) -> None:
    """**不联网**：用 raw_detail 下已存的 HTML 按新规则重新解析全部字段。

    ★ 用途：改抽取规则后，先离线看效果，确认无误再决定要不要重抓。
      联网重抓 161 页约 100 秒；离线重解析不到 1 秒，且结果完全可复现。
    """
    raw = d / "raw_detail"
    df = d / "college_detail.json"
    if not raw.exists() or not df.exists():
        raise SystemExit(f"✗ 缺少 {raw} 或 {df}")

    det = json.loads(df.read_text(encoding="utf-8"))
    before = sum(1 for x in det if x.get("directions"))
    miss = 0
    for x in det:
        cand = list(raw.glob(f"{x['aid']}_*.html"))
        if not cand:
            miss += 1
            continue
        page = cand[0].read_text(encoding="utf-8", errors="replace")
        info = parse_college_profile(page)
        # 保留列表页带来的字段（name/aid/url/college），覆盖解析字段
        for k, v in info.items():
            x[k] = v
        x["status"] = "ok" if x["directions"] else "no_direction"
        x["why"] = "" if x["directions"] else "学院页未找到研究方向线索词"

    df.write_text(json.dumps(det, ensure_ascii=False, indent=2), encoding="utf-8")
    after = sum(1 for x in det if x.get("directions"))

    # 附带统计其余结构化字段的覆盖率
    def cov(k):
        return sum(1 for x in det if x.get(k))

    print()
    print("═" * 62)
    print(f"✓ 离线重解析完成（未联网，{len(det)} 页）")
    print(f"  有研究方向：{before} → **{after}** 人"
          + (f"（+{after-before}）" if after > before else ""))
    print(f"  未找到存档：{miss} 页")
    print()
    print("  结构化字段覆盖率：")
    for k, label in (("title_line", "职衔行"), ("directions", "研究方向"),
                     ("direction_intro", "方向介绍段"), ("bio", "履历/简介"),
                     ("email", "邮箱"), ("phone", "电话"),
                     ("homepage", "个人主页"), ("recruit_note", "招生说明"),
                     ("representative_papers", "代表性论文")):
        print(f"    {label:10} {cov(k):3}/{len(det)}")
    print("═" * 62)
    journal("S2", "colleges.reparse", total=len(det), with_dir=after)


def merge_into(d: pathlib.Path, roster_file: pathlib.Path, log) -> None:
    """把学院官网的方向合并进阶段 S 的 roster_detail.json（按姓名匹配）。

    ★ 合并规则：
      · 双方都有方向 → 取**条目更多**的一方（通常学院页更详细），
        并把另一方作为附加来源记录在 `direction_alt`
      · 只有一方有 → 用有的一方
      · 不覆盖任何已有字段（只增补 directions / direction_source）
    """
    college = json.loads((d / "college_detail.json").read_text(encoding="utf-8"))
    roster = json.loads(roster_file.read_text(encoding="utf-8"))

    def norm(n: str) -> str:
        return re.sub(r"[\s·．.、,，]", "", (n or "")).lower()

    by_name: dict[str, dict] = {}
    for c in college:
        if c.get("directions"):
            by_name.setdefault(norm(c["name"]), c)

    filled = 0
    for r in roster:
        c = by_name.get(norm(r.get("name", "")))
        if not c:
            continue
        cd = c["directions"]
        rd = r.get("directions") or []
        if len(cd) > len(rd):
            if rd:
                r["direction_alt"] = rd          # 保留原来源，不丢
            r["directions"] = cd
            r["direction_source"] = "学院官网"
            r["direction_source_url"] = c["url"]
            r["college"] = c.get("college", "")
            r["status"] = "ok"
            r["why"] = ""
            r.pop("no_direction_reason", None)
            filled += 1

    roster_file.write_text(json.dumps(roster, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    wd = sum(1 for r in roster if r.get("directions"))
    log.info(f"✓ 合并完成：新补 {filled} 人；roster 现有方向 {wd}/{len(roster)}")
    print(f"→ 合并后 {wd}/{len(roster)} 人有研究方向（本次补 {filled} 人）")


# ══════════════════════════════════════════════════════════════
# ⛔ 已被取代（v11）—— 默认拒绝运行
#
# 本脚本把「学院官网」当作**补充**数据源（配合已弃用的 harvest_mentors.py）。
# 现流程已改为**统一以学部/学院官网为准**，且把学校/学院/栏目做成配置，
# 由 harvest_faculty.py 承担，用法：
#     uv run python 流程1/scripts/harvest_faculty.py --list-units
#
# 本脚本保留仅为：USTC 场景的历史复现（它含 USTC 生医部 5 个学院的写死配置），
# 以及 `--reparse-only` 离线重解析的对照。确需运行请加 --allow-deprecated。
# ══════════════════════════════════════════════════════════════
_DEPRECATED_MSG = """
⛔ harvest_colleges.py 已被 harvest_faculty.py 取代（v11）
   现流程**统一以学部/学院官网**为唯一数据源，且学校/学院/栏目改为配置驱动
   （templates/学部官网源.json），新增学校不必改脚本。

   ✅ 请改用：harvest_faculty.py
        uv run python 流程1/scripts/harvest_faculty.py --list-units
        uv run python 流程1/scripts/harvest_faculty.py --unit ustc:life-med
        uv run python 流程1/scripts/harvest_faculty.py --unit zju

   确需运行本脚本（复现 USTC 历史数据）：追加 --allow-deprecated
"""


def _guard_deprecated(argv: list[str]) -> None:
    if "--allow-deprecated" not in argv:
        print(_DEPRECATED_MSG, file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    _guard_deprecated(list(sys.argv if argv is None else argv))
    ap = argparse.ArgumentParser(
        prog="harvest_colleges.py",
        description="【已被取代】原阶段 S2：从学院官网师资页抓取导师与研究方向。"
                    "请改用 harvest_faculty.py（配置驱动，统一数据源）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("⛔ 本脚本已被 harvest_faculty.py 取代\n"
                "确需运行请追加 --allow-deprecated\n\n"
                "示例：\n"
                "  harvest_colleges.py --list-colleges\n"
                "  harvest_colleges.py --out .work_cdp/colleges\n"
                "  harvest_colleges.py --college smkxy --stage=list\n"
                "  harvest_colleges.py --merge-into outputs/01_导师名单/roster_detail.json\n"))
    ap.add_argument("--out", default=".work_cdp/colleges", help="输出目录")
    ap.add_argument("--college", choices=list(COLLEGES), help="只抓某个学院")
    ap.add_argument("--stage", choices=["list", "detail", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="详情最多抓几人（调试）")
    ap.add_argument("--merge-into", help="把结果合并进该 roster_detail.json")
    ap.add_argument("--list-colleges", action="store_true", help="列出学院后退出")
    ap.add_argument("--allow-deprecated", action="store_true",
                    help="【已被取代的脚本】显式确认后才允许运行（请优先用 harvest_faculty.py）")
    ap.add_argument("--reparse-only", action="store_true",
                    help="**不联网**：用 raw_detail 下已存的 HTML 重新解析（改规则后验证用）")
    ap.add_argument("--rename-archives", action="store_true",
                    help="把 raw_detail 下旧命名（<aid>_x.html）的存档改名为 <aid>_姓名.html")
    args = ap.parse_args(argv)

    log = get("colleges")

    if args.list_colleges:
        print("院系师资（https://biomed.ustc.edu.cn/smkxy/list.htm）：")
        for k, v in COLLEGES.items():
            print(f"  {k:<10} {v:<16} {BASE}/{k}/list.htm")
        return 0

    d = pathlib.Path(args.out)
    if not d.is_absolute():
        d = WD / d

    if args.merge_into:
        merge_into(d, pathlib.Path(args.merge_into), log)
        return 0

    if args.rename_archives:
        _rename_archives(d, log)
        return 0

    if args.reparse_only:
        reparse_only(d, log)
        return 0

    if args.stage in ("list", "all"):
        stage_list(d, log, only=args.college or "")
    if args.stage in ("detail", "all"):
        stage_detail(d, log, limit=args.limit)

    if args.stage == "all":
        det = json.loads((d / "college_detail.json").read_text(encoding="utf-8"))
        hit = sum(1 for x in det if x.get("directions"))
        print()
        print("═" * 62)
        print(f"✓ 学院官网抓取完成：{len(det)} 人，其中 {hit} 人有研究方向")
        print(f"  数据：{d}")
        print()
        print("  下一步（把方向合并进阶段 S 的名单）：")
        print("    uv run python 流程1/scripts/harvest_colleges.py \\")
        print("      --out <同上> --merge-into outputs/01_导师名单/roster_detail.json")
        print("═" * 62)
        journal("S2", "colleges.harvest", total=len(det), with_dir=hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
