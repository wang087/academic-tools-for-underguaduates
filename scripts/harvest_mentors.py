# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S：匹配细分专业 —— 学部导师名单抓取与初步处理
#          （Match Sub-discipline / Mentor Roster Harvest）
#
# 为什么要有这一步
#   本科生找课题的真实起点不是"关键词"，而是"哪个老师在做这个方向"。
#   但学部官网的师资队伍页面动辄 200+ 人、分 8 页翻，逐个点开读研究方向
#   会吃掉大量 token 与时间。本脚本把「自动爬取 → 初步处理 → 方向归类」
#   一次做完，只把**结构化的候选名单**交给 agent 与人看。
#
# 它在整个流程里的位置
#   大方向确定（阶段 N） → **阶段 S（本脚本）** → 用户选定导师/细分专业
#   → 平行综述检索（阶段 R） → 原 author-paper-analysis 流程（阶段 A…I）
#
# 设计要点
#   · 只用标准库（urllib）—— 不引入 requests/bs4，随包可直接运行
#   · 两段式缓存：`--stage=list` 只抓列表页（快、稳），
#     `--stage=detail` 再逐人抓研究方向页；中断可续，已抓的不重抓
#   · 解析失败**不静默**：逐人记录 ok/why，最终给出成功/失败计数
#   · 分类用**关键词规则表**（templates/导师方向规则.json），
#     规则可改、不写死在代码里；未命中的进「未分类」，绝不硬塞
#   · 不臆造研究方向：页面上没有「研究方向」栏位的人，方向留空并标记
#
# 用法：
#   # ① 列出可用学部（内置常用学部，给出 id）
#   uv run python 流程1/scripts/harvest_mentors.py --list-units
#
#   # ② 抓取某学部全部导师（列表页 + 详情页 + 分类），一条命令到底
#   uv run python 流程1/scripts/harvest_mentors.py \
#       --unit life-med --out .work_cdp/mentors
#
#   # ③ 只抓列表页（不点进详情，秒级，用于先看人数是否合理）
#   uv run python 流程1/scripts/harvest_mentors.py --unit life-med --stage=list
#
#   # ④ 续跑详情（前面中断了，已抓的自动跳过）
#   uv run python 流程1/scripts/harvest_mentors.py --unit life-med --stage=detail
#
#   # ⑤ 自定义学部（任意 tsites 站点的教师列表页）
#   uv run python 流程1/scripts/harvest_mentors.py \
#       --list-url "https://faculty.ustc.edu.cn/xyjslb.jsp?...&id=1150&..." --out ...
#
#   # ⑥ 只重新分类（改了规则表后，不重新联网）
#   uv run python 流程1/scripts/harvest_mentors.py --unit life-med --reclassify
#
# 可移植配置：PAPER_WORKDIR（默认当前目录）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import html
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from logutil import run_root as _run_root  # noqa: E402

# ══════════════════════════════════════════════════════════════
# UTF-8 兜底（与 logutil._fix_stdout 同判据，幂等）
# ══════════════════════════════════════════════════════════════


def _fix_streams() -> None:
    """把 stdout/stderr 换成 UTF-8；已是 UTF-8 就不重包。

    为什么必须幂等：脚本之间会互相 import，每 import 一次重包一层，
      中间那层 wrapper 被 GC 时会关掉底层 buffer，后续 print 全炸。
    """
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
except Exception:                      # 单文件运行时也要能用
    def get(name):                     # noqa: D103
        class _L:
            def info(self, *a, **k):
                print(*a)

            def warn(self, *a, **k):
                print(*a, file=sys.stderr)

            def debug(self, *a, **k):
                pass
        return _L()

    def journal(*a, **k):               # noqa: D103
        pass


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
TEMPLATES = _HERE.parent / "templates"
RULE_FILE = TEMPLATES / "导师方向规则.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
      "(compatible; mentor-roster-harvest/1.0; academic-use)")

# ══════════════════════════════════════════════════════════════
# 内置学部表 —— 只放"已验证过列表页形态"的条目。
# 新增学校/学部时：先用浏览器打开该校教师主页系统的学院列表页，
# 把 URL 粘到 --list-url 跑通，再决定是否登记到这里。
# ══════════════════════════════════════════════════════════════
UNITS = {
    "life-med": {
        "school": "中国科学技术大学",
        "name": "生命科学与医学部",
        "list_url": ("https://faculty.ustc.edu.cn/xyjslb.jsp?"
                     "PAGENUM={page}&id=1150&lang=zh_CN&st=0"
                     "&urltype=tsites.CollegeTeacherList&wbtreeid=1014"),
        "total_pages": 0,          # 0 = 自动探测（页面自称的 totalpage 不可信）
        "note": "实测 237 人 / 每页 12 → 20 页；详情页在 /<拼音>/zh_CN/index.htm",
    },
    "physics": {
        "school": "中国科学技术大学",
        "name": "物理学院",
        "list_url": ("https://faculty.ustc.edu.cn/xyjslb.jsp?"
                     "PAGENUM={page}&id=1146&lang=zh_CN&st=0"
                     "&urltype=tsites.CollegeTeacherList&wbtreeid=1014"),
        "total_pages": 0,          # 0 = 自动探测
        "note": "id 未经逐一核验，首次使用请先 --stage=list 核对人数是否合理",
    },
}

# ══════════════════════════════════════════════════════════════
# 默认分类规则（内置兜底；优先读 templates/导师方向规则.json）
#   规则可改、不写死在代码里。未命中任何规则 → 「未分类」，不硬塞。
# ══════════════════════════════════════════════════════════════
DEFAULT_RULES = {
    "_说明": [
        "导师研究方向 → 细分方向 的分类规则表。",
        "匹配方式：大小写不敏感的子串匹配（中文直接子串）。",
        "只做「可解释的粗归类」，不是权威学科分类。一条方向命中多类时全部记入。",
        "请按目标学部的真实语料增删关键词；规则越贴合，未分类越少。",
    ],
    "免疫与感染": ["免疫", "immun", "炎症", "inflamm", "感染", "infect", "病毒", "virus",
                   "virolog", "抗体", "antibod", "疫苗", "vaccin", "t细胞", "t cell",
                   "微生物", "microb", "菌", "bacteri", "寄生虫"],
    "神经与脑科学": ["神经", "neuro", "脑", "brain", "突触", "synap", "认知", "cognit",
                     "行为", "behavior", "记忆", "memory", "感觉", "sensory", "视觉",
                     "vision", "听觉", "睡眠", "sleep", "环路", "circuit"],
    "肿瘤与血液": ["肿瘤", "tumor", "tumour", "癌", "cancer", "carcin", "白血病",
                   "leukem", "血液", "hemat", "转移", "metasta", "靶向", "targeted"],
    "遗传与发育": ["遗传", "genetic", "基因", "gene", "发育", "develop", "干细胞",
                   "stem cell", "胚胎", "embryo", "分化", "differenti", "表观",
                   "epigen", "染色质", "chromatin", "基因组", "genom"],
    "结构与分子生物学": ["结构", "structur", "蛋白", "protein", "冷冻电镜", "cryo",
                         "晶体", "crystall", "分子", "molecul", "酶", "enzyme",
                         "rna", "dna", "折叠", "fold", "复合物", "complex"],
    "细胞与信号转导": ["细胞", "cell", "信号", "signal", "通路", "pathway", "受体",
                       "receptor", "代谢", "metabol", "自噬", "autophag", "凋亡",
                       "apoptos", "细胞周期", "cell cycle"],
    "药理与转化医学": ["药理", "pharmac", "药物", "drug", "临床", "clinical", "转化",
                       "translat", "诊断", "diagnos", "治疗", "therap", "医学",
                       "medic", "疾病", "disease", "流行病", "epidemi"],
    "生物信息与计算": ["生物信息", "bioinform", "计算", "comput", "统计", "statist",
                       "算法", "algorithm", "机器学习", "machine learning", "深度",
                       "deep learning", "建模", "model", "组学", "omics", "大数据",
                       "data", "人工智能", "artificial intelligence"],
    "成像与生物物理": ["成像", "imaging", "显微", "microscop", "光学", "optic",
                       "生物物理", "biophysic", "光谱", "spectro", "单分子",
                       "single molecule", "物理", "physic", "探针", "probe"],
    "生态与进化": ["生态", "ecolog", "进化", "evolution", "进化", "系统发育",
                   "phylogen", "物种", "species", "保护", "conserv", "群落",
                   "community", "生物多样性", "biodivers"],
    "植物与农业": ["植物", "plant", "农", "agri", "作物", "crop", "光合",
                   "photosynth", "拟南芥", "arabidopsis", "水稻", "rice"],
    "公共卫生与健康": ["公共卫生", "public health", "健康", "health", "营养",
                       "nutrit", "环境健康", "环境卫生", "职业", "occupation",
                       "队列", "cohort", "预防", "prevent"],
}


# ══════════════════════════════════════════════════════════════
# 网络层
# ══════════════════════════════════════════════════════════════
def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    """取一个页面，返回解码后的 HTML。

    只用于**公开的师资队伍页面**（第一层直连，属 §5.1 分层获取策略
    允许的范畴：不涉及出版社内容站、不取论文原文）。
    """
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            # tsites 站点多为 UTF-8；少数老页面用 GBK，做一次嗅探
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"取页面失败（重试 {retries} 次）：{url} —— {last}")


# ══════════════════════════════════════════════════════════════
# 解析层 —— tsites（高校教师主页系统）列表页与详情页
# ══════════════════════════════════════════════════════════════
_TAG = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).strip()


# 方向文本里的枚举前缀：「[1]」「1、」「1)」「（1）」「1.」「(1)」「一、」等
_DIR_NOISE = re.compile(
    r"^\s*(?:[\[\(（【]\s*\d+\s*[\]\)）】]|"
    r"\d+\s*[、\.\)）]|"
    r"[一二三四五六七八九十]+\s*[、\.\)）])\s*")

# 明显不是研究方向的条目：栏目名、导航词、联系方式、履历行、招生欢迎语、UI 占位符
_NOT_DIRECTION = re.compile(
    r"^(?:首页|社会兼职|教育经历|工作经历|团队成员|论文成果|著作成果|科研项目|"
    r"专利|获奖信息|个人信息|研究方向|研究兴趣|联系方式|已发表文章|代表性论文|"
    r"招生信息|个人简介|更多|暂无内容|English|登录|查看|展开)\b"
    r"|(?:Research\s*Focus|Social\s*Affiliations|Education\s*Background|"
    r"Work\s*Experience|查看更多|点击查看|详细)"       # 栏目名 / UI 占位符
    r"|(?:当前位置\s*[：:]|中文主页\s*>>|>>)"            # v9：面包屑导航
    r"|^(?:发表文章|招生招聘|代表性论文|主要论文|论文列表|学位[：:])"  # v10：栏目名
    r"|(?:\d{4}\s*[\.\-~/年]|^\d{4}$)"                  # 履历年份行
    r"|(?:@[A-Za-z0-9.\-]+\.(?:com|cn|edu|org))"        # 邮箱
    r"|(?:欢迎|联系|电话|邮箱|地址|邮编)"                 # 联系/欢迎语
    r"|(?:国家自然科学基金|青年科学基金|面上项目|基金项目|"
    r"重点研发计划|973|863)"                             # v10：基金
    r"|(?:主要学术成就|学术成就)"                          # v10：成果叙述
    r"|^(?:招生|招聘|报考|招收)方向"                       # v10：招生方向≠研究方向
    r"|^(?:我的相册|教师博客|加入我们|其他栏目|其他联系|"
    r"同专业博导|同专业硕导|高级工程师|工程师|实验师|"
    r"课题组主页|团队主页|回到顶部|打印|关闭)$"            # v10：UI/导航标签
)


def _is_direction(t: str) -> bool:
    """判断一条文本像不像「研究方向」。用于剔除栏目名 / 履历 / 联系方式等噪声。"""
    if not t or not (2 <= len(t) <= 600):
        return False
    if _NOT_DIRECTION.search(t):
        return False
    # v9：栏目名 + 面包屑拼成的行（如「研究领域 当前位置：中文主页 >> 科学研究」）
    #   整条都没有实际方向内容 —— 去掉栏目名与面包屑后若所剩无几就判否
    stripped = re.sub(
        r"^(?:研究领域|研究方向|研究兴趣|科学研究|科研方向)\s*", "", t)
    stripped = re.sub(r"当前位置\s*[：:].*$", "", stripped).strip(" ·-—：:")
    if len(stripped) < 2:
        return False
    # v9：多个栏目名串成的导航行（如「科学研究 研究领域 论文成果 专利 著作成果 科研项目」）
    #   特征：整行由 3 个以上的栏目词组成，去掉这些词后所剩无几
    NAV_WORDS = r"(?:科学研究|研究领域|研究方向|研究兴趣|教学研究|教学资源|"
    NAV_WORDS += r"授课信息|教学成果|论文成果|专利|著作成果|科研项目|"
    NAV_WORDS += r"获奖信息|招生信息|学生信息|我的相册|教师博客|社会兼职|"
    NAV_WORDS += r"教育经历|工作经历|基本信息|团队成员|其他联系方式)"
    toks = re.findall(NAV_WORDS, t)
    if len(toks) >= 2:
        residue = re.sub(NAV_WORDS, "", t)
        residue = re.sub(r"[\s·\-—、,，。;；]+", "", residue)
        if len(residue) < 6:                 # 去掉栏目词后没剩下实际内容
            return False
    # 纯栏目名式短标签（如「社会兼职Social Affiliations」）无实义
    if re.match(r"^[\u4e00-\u9fff]{2,8}\s*[A-Za-z ]{0,30}$", t) and \
            re.search(r"(兼职|经历|成果|信息|团队|项目|专利|获奖|简介|查看|更多)", t):
        return False
    # 纯符号 / 无中文也无拉丁字母的行
    if not re.search(r"[\u4e00-\u9fff]|[A-Za-z]{3,}", t):
        return False
    # 「【…】」「[…]」这类整行被括号包住的，多半是按钮而非方向
    if re.match(r"^[【\[（(].{0,20}[】\]）)]$", t):
        return False
    # v10：论文列表（英文姓名 + # / * 标记连排）不是方向
    if len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}#|,\s*[A-Z]{1,3}\s*,", t)) >= 2:
        return False
    # v10：无中文、且含 2 个以上「姓名 + 首字母缩写」的引文片段
    if not re.search(r"[\u4e00-\u9fff]", t) and \
            len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}\b", t)) >= 2:
        return False
    return True


def _strip_dir_noise(t: str) -> str:
    """去掉方向条目的枚举编号，保留正文。

    例：「[1]NKT细胞的功能」→「NKT细胞的功能」；「1、生物学」→「生物学」
    先剔除零宽字符：tsites 的方向文本常以 U+200B 开头（实测「[2]NKT细胞…」
      与「1、生物学」都带），它会挡住 ^ 锚点使编号剥不掉。
    只剥一层；剥完至少要剩 2 个字符，避免把「1型糖尿病」这类真内容削成空。
    """
    t = t.replace("\u200b", "").replace("\ufeff", "").strip()
    s = _DIR_NOISE.sub("", t, count=1).strip(" ·-—:：.")
    return s if len(s) >= 2 else t


def parse_total_pages(page: str, per_page: int = 0) -> int:
    """推断总页数。

    tsites 的「共N条 x/y」里那个 y **不可信**：同一个列表页，
      带 totalpage 参数与不带会给出不同的 y（实测 1/8 与 1/20 同时存在），
      而真实每页 12 条、共 237 条 → 真实总页数是 20。
      所以这里**以「共N条」为准**，用每页条数反推页数。
    """
    text = strip_tags(page)
    m = re.search(r"共\s*(\d+)\s*条", text)
    if m and per_page > 0:
        total = int(m.group(1))
        return max(1, -(-total // per_page))       # 向上取整
    # 兜底：用页面自称的 x/y
    m = re.search(r"共\s*\d+\s*条\s*(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(2))
    m = re.search(r"PAGENUM=(\d+)[^>]*>\s*尾页", page)
    if m:
        return int(m.group(1))
    return 0


# 列表页条目：<a href="http://faculty.ustc.edu.cn/<slug>/zh_CN/index.htm"> 名字 _学部_
_ITEM = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*faculty[^"]*?/(?P<slug>[A-Za-z0-9_\-]+)/'
    r'(?P<lang>zh_CN|en)/index\.htm)"[^>]*>(?P<inner>.*?)</a>',
    re.S | re.I)


# 列表页真实数据源：jQuery/ImageScale 的 addimg(图片, 链接, 姓名, id) 调用。
# tsites 把整页教师渲染进这个 JS 数组，而 <a> 标签只是无脚本时的兜底 ——
# 只认 <a> 会漏人，所以 addimg 优先、<a> 兜底。
_ADDIMG = re.compile(
    r'addimg\(\s*"(?P<img>[^"]*)"\s*,\s*"(?P<href>[^"]*)"\s*,\s*'
    r'"(?P<name>[^"]*)"\s*,\s*"?\d+"?\s*\)', re.I)


def parse_list(page: str) -> list[dict]:
    """解析教师列表页 → [{name, slug, url}]，保持页面顺序、按 slug 去重。"""
    out, seen = [], set()

    def _add(name: str, href: str) -> None:
        m = re.search(r"/([A-Za-z0-9_\-]+)/(?:zh_CN|en)/index\.htm", href or "")
        if not m:
            return
        slug = m.group(1)
        name = re.sub(r"\s+", " ", html.unescape(name or "")).strip(" _")
        if not name or slug in seen:
            return
        seen.add(slug)
        out.append({
            "name": name,
            "slug": slug,
            "url": f"https://faculty.ustc.edu.cn/{slug}/zh_CN/index.htm",
        })

    for m in _ADDIMG.finditer(page):
        _add(m.group("name"), m.group("href"))

    if not out:                       # 兜底：无脚本时的 <a> 列表
        for m in _ITEM.finditer(page):
            inner = strip_tags(re.sub(r"<img[^>]*>", "", m.group("inner"), flags=re.I))
            inner = re.sub(r"\s+", " ", inner).replace("\u200b", "").strip()
            inner = re.sub(r"[_\s]*[^_\s]*(学部|学院)[_\s]*$", "", inner).strip(" _")
            _add(inner, m.group("href"))
    return out


def parse_detail(page: str) -> dict:
    """解析教师详情页 → {title, email, dept, directions[]}。

    页面上没有「研究方向」栏位时 directions 为空列表 —— **不臆造**。
      此时还会尝试从 ``_subpages`` 里找「研究领域 / 研究方向」子页（见
      ``find_direction_subpages``），由调用方决定是否深入抓取。
    """
    info: dict = {"title": "", "email": "", "dept": "", "directions": []}

    def _push(t: str) -> None:
        """统一入口：剥编号 → 判噪声 → 去重。所有方向条目都必须经过这里。"""
        t = _strip_dir_noise(t).lstrip(".").strip()
        if _is_direction(t) and t not in info["directions"]:
            info["directions"].append(t)

    # 职称：教授 / 副教授 / 特任教授 / 研究员 …
    m = re.search(r"#\s*个人信息[^<]*</h[12]>\s*(.{0,400}?)</", page, re.S)
    m2 = re.search(r"<h3[^>]*>\s*(教授|副教授|特任教授|研究员|副研究员|讲师|"
                   r"博士后|工程师|特任副研究员)\s*</h3>", page)
    if m2:
        info["title"] = m2.group(1)
    else:
        mt = re.search(r"\b(教授|副教授|特任教授|研究员|副研究员|讲师)\b", strip_tags(page)[:2000])
        if mt:
            info["title"] = mt.group(1)

    me = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", page)
    if me:
        info["email"] = me.group(1)

    md = re.search(r"职务[：:]\s*</?[^>]*>?\s*([^<\n]{2,40})", page)
    if md:
        info["dept"] = strip_tags(md.group(1))

    # ── 研究方向：以 /yjfx/ 链接为主锚点，容器结构只作兜底 ──
    # 实测 tsites 详情页有**至少三种模板**，容器 class 各不相同：
    #   ① slideTxtBox2：<div class="bd"><ul><li><a href="/slug/zh_CN/yjfx/…">方向</a></li>
    #   ② box-2      ：<div class="text lists"><p><a href="/slug/zh_CN/yjfx/…">[1]方向</a></p>
    #   ③ 无结构化栏位：方向只写在个人简介正文里（如 caocan）
    # 三种模板的**共同点**是：结构化方向一律指向 `/yjfx/` 路径。
    # 所以这里直接以 yjfx 链接为锚，不再依赖 bd / ul / li 任何一种容器 ——
    # 早期版本要求 <div class="bd"> 存在，导致 ② 类页面（白丽、曾筑天…）整片漏掉。
    yjfx_hits = re.findall(
        r'<a[^>]+href="[^"]*/yjfx/[^"]*"[^>]*>(.*?)</a>', page, re.S | re.I)
    if yjfx_hits:
        for f in yjfx_hits:
            _push(re.sub(r"\s+", " ", strip_tags(f)).strip(" ·-—"))
    else:
        # 兜底：没有 yjfx 链接时，退回「研究方向」文字块（兼容未知模板）
        for sec in re.finditer(r"研究方向", page):
            tail = page[sec.end(): sec.end() + 12000]
            stop = re.search(r'<!--[^>]*?(?:社会兼职|论文成果|著作成果|科研项目|专利|获奖)'
                             r'[^>]*?-->|<div[^>]*class="[^"]*(?:footer|bottom)"',
                             tail, re.I)
            block = tail[: stop.start()] if stop else tail
            found = re.findall(r"<li[^>]*>(.*?)</li>|<p[^>]*>(.*?)</p>", block, re.S | re.I)
            for a, b in found:
                _push(re.sub(r"\s+", " ", strip_tags(a or b)).strip(" ·-—"))
            if info["directions"]:
                break

    # 研究方向自己的详情页（部分老师方向在独立页）
    yjfx = re.search(r'href="(/[A-Za-z0-9_\-]+/zh_CN/yjfx/[^"]+)"', page)
    if yjfx:
        info["_yjfx_url"] = "https://faculty.ustc.edu.cn" + html.unescape(yjfx.group(1))

    # v9：主页没有方向时，记录可深入的「研究领域」子页，供调用方补抓
    if not info["directions"]:
        subs = find_direction_subpages(page)
        if subs:
            info["_subpages"] = subs

    # v9：仍没有 → 尝试从个人简介散文里抽取（第五种页面形态）
    if not info["directions"]:
        prose = parse_directions_from_prose(page)
        if prose:
            info["directions"] = prose
            info["direction_source"] = "个人简介正文"

    return info


# ══════════════════════════════════════════════════════════════
# 「研究领域」子页抓取（v9）
#
# 为什么需要
#   实测 237 位教师里有 130 人主页**没有**内嵌研究方向栏位，其中 66 人的主页
#   带有指向**独立子页**的导航链接（`/slug/zh_CN/yjgk/<id>/list/index.htm`，
#   yjgk = 研究领域）。方向正文在那个子页上，只抓主页就会全部漏掉。
#
#   实测：程临钊的主页有「研究领域」子页链接，但子页内容是「暂无内容」——
#   所以**抓了才知道有没有**，不能因为主页没有就断言此人无方向。
# ══════════════════════════════════════════════════════════════

# 研究方向类子页的路径片段 → 语义标签
_DIR_SUBPAGE_KINDS = {
    "yjgk": "研究领域",
    "yjfx": "研究方向",
    "kyfx": "科研方向",
    "kxyj": "科学研究",
}

_SUBPAGE_LINK = re.compile(
    r'href="(/(?P<slug>[A-Za-z0-9_\-]+)/zh_CN/(?P<kind>yjgk|yjfx|kyfx|kxyj)/'
    r'(?P<sid>\d+)/(?:list/)?(?:index\.htm)?)"[^>]*>(?P<label>[^<]{0,24})</a>',
    re.I)

# 子页里明确的「没内容」标记
_EMPTY_MARKERS = ("暂无内容", "暂无信息", "No content", "尚未填写")

# 主页未发布 / 不存在（tsites 的错误页）
_NO_HOMEPAGE = ("你访问的主页不存在", "没有上线发布", "遇到错误啦")


def parse_directions_from_prose(page: str) -> list[str]:
    """从个人简介正文里抽取研究方向（**第五种**页面形态）。

    为什么需要：实测有一类页面既没有 TabbedPanels、也没有 yjfx 链接，
      方向直接写在简介散文里，例如：
        「长期从事肿瘤细胞生物学与肿瘤治疗相关研究，**主要研究方向包括：**
          （1）细胞分裂调控与恶性肿瘤发生发展的分子机制；
          （2）肿瘤微环境在肿瘤治疗耐受中的作用」
      这类内容只能靠**句内线索词**定位，不能靠容器。

    线索词：主要研究方向 / 研究方向包括 / 主要研究领域 / 研究兴趣等。
    抽取策略：定位线索词 → 取其后同一段文本 → 按编号或分号切分。
    """
    body = re.sub(r"<script[^>]*>.*?</script>", " ", page, flags=re.S | re.I)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S | re.I)
    # 段落级切分，避免把整页当一段
    chunks = re.split(r"</(?:p|div|li|td|h\d)>|<br\s*/?>", body, flags=re.I)
    text_chunks = [re.sub(r"\s+", " ", strip_tags(c)).strip() for c in chunks]
    text_chunks = [c for c in text_chunks if c]

    LEAD = re.compile(
        r"(?:主要)?研究(?:方向|领域|兴趣)(?:包括|有|为|是|：|:)|"
        r"主要研究方向|Research\s+(?:Focus|Interests?)\s*(?:include|are|:)?", re.I)

    out: list[str] = []
    for c in text_chunks:
        m = LEAD.search(c)
        if not m:
            continue
        tail = c[m.end():].strip("  ：:，,。.；;")
        if len(tail) < 6:
            continue
        # 按「（1）」「1)」「1、」「(1)」等编号切分；无编号则按分号切
        parts = re.split(r"[（(]\s*\d+\s*[)）]|\d+\s*[、.)）]|[；;]", tail)
        for part in parts:
            t = re.sub(r"\s+", " ", part).strip("  ·-—，,。.；;：:")
            # 截断到方向句的天然边界：一旦出现「主持/承担/发表/获」等
            #   项目与成果叙述，说明方向列举已经结束，后面的不是方向。
            t = re.split(r"(?:先后)?(?:主持|承担|参与|获得|荣获|发表|申请)", t)[0]
            t = _strip_dir_noise(t.strip("  ·-—，,。.；;：:"))
            if 6 <= len(t) <= 200 and _is_direction(t) and t not in out:
                out.append(t)
        if out:
            break
    return out


def classify_no_direction(page: str) -> str:
    """判定「没有方向」的**具体原因**，供报告如实区分。

    为什么值得区分：v9 实测（USTC 生命科学与医学部 237 人）发现
      130 位「无方向」老师里，**128 人是网站上真的没填** ——
      模板里「研究方向」页签对所有人都有，但内容面板是 `<p>暂无内容</p>`；
      另有 2 人主页根本没发布（tsites 错误页「主页不存在或没有上线发布」）。
      这是**数据源的固有缺失**，不是解析能力问题，
      如实分类可以避免使用者误以为"再爬一次就能拿到"。

    返回值：
      no_homepage        主页不存在 / 未发布
      empty_panel        模板有「研究方向」页签，但内容是「暂无内容」
      empty_slot         模板里只有空的 `<!--研究方向-->` 占位注释，无内容
      no_tab             页面没有研究方向相关页签或文字
      unknown            无法判定
    """
    text = strip_tags(page)
    if any(mk in text for mk in _NO_HOMEPAGE):
        return "no_homepage"

    # 必须先去注释再判断！实测有一类页面（如赵忠）全页只有一句
    #   `<!--研究方向-->` 占位注释后面什么都没有 —— 若不过滤注释，
    #   会被误判成"页面有研究方向字样"，归进 unknown 而不是"确无内容"。
    visible = re.sub(r"<!--.*?-->", " ", page, flags=re.S)

    if "研究方向" not in visible:
        # 只在注释里出现过 → 模板占位，确无内容
        if "研究方向" in page:
            return "empty_slot"
        return "no_tab"

    # TabbedPanels 结构：取各内容面板，看是否全是「暂无内容」
    panels = re.findall(r'class="TabbedPanelsContent"[^>]*>(.*?)</div>', visible, re.S)
    if panels:
        texts = [re.sub(r"\s+", " ", strip_tags(p)).strip() for p in panels]
        real = [x for x in texts
                if x and "暂无内容" not in x and len(x) > 8]
        return "has_panel_text" if real else "empty_panel"
    if "暂无内容" in text:
        return "empty_panel"
    return "unknown"


_NO_DIR_REASON_CN = {
    "no_homepage": "教师主页未发布（官网返回「主页不存在或没有上线发布」）",
    "empty_panel": "官网「研究方向」页签存在但内容为「暂无内容」（网站未填写）",
    "empty_slot": "官网模板仅有空的「研究方向」占位注释，无任何内容",
    "no_tab": "页面无「研究方向」页签",
    "has_panel_text": "页签内有文本但未通过方向判据（需人工核对）",
    "unknown": "无法判定（需人工核对）",
}


def find_direction_subpages(page: str) -> list[dict]:
    """找出页面上指向「研究领域 / 研究方向」的子页链接（去重、按 kind 排序）。"""
    out, seen = [], set()
    for m in _SUBPAGE_LINK.finditer(page):
        kind = m.group("kind").lower()
        href = html.unescape(m.group(1))
        if href in seen:
            continue
        seen.add(href)
        label = re.sub(r"\s+", " ", strip_tags(m.group("label"))).strip("【】 ")
        out.append({
            "kind": kind,
            # 优先用页面上的中文标签；取不到则用内置语义名
            "label": label if label and label not in ("查看更多",) else
                     _DIR_SUBPAGE_KINDS.get(kind, kind),
            "url": "https://faculty.ustc.edu.cn" + href,
        })
    # 研究领域(yjgk) / 研究方向(yjfx) 优先于笼统的"科学研究"
    order = {"yjfx": 0, "yjgk": 1, "kyfx": 2, "kxyj": 3}
    out.sort(key=lambda x: order.get(x["kind"], 9))
    return out


def parse_direction_subpage(page: str) -> list[str]:
    """从「研究领域」子页正文里提取方向条目。

    与主页解析共用同一套清洗与判噪规则（`_push` 的等价逻辑），
      避免两处标准漂移。返回空列表表示该子页确实没有内容。
    """
    # 子页若是「暂无内容」，直接判定为空 —— 不把导航文字当方向
    body = re.sub(r"<script[^>]*>.*?</script>", " ", page, flags=re.S | re.I)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S | re.I)
    text = strip_tags(body)
    if any(mk in text for mk in _EMPTY_MARKERS):
        # 仍可能「部分栏目为空」，继续尝试解析；但若全文很短就直接返回
        if len(text) < 400:
            return []

    out: list[str] = []

    # ① 内容区通常是 <div class="...content..."> 或 <ul class="...list...">
    zones = re.findall(
        r'<(?:div|ul)[^>]*class="[^"]*(?:content|list|arti|detail|main)[^"]*"[^>]*>(.*?)'
        r'</(?:div|ul)>', body, re.S | re.I)
    # ② 兜底：整页正文
    zones.append(body)

    for zone in zones:
        # 优先抓条目：<li> / <p>
        items = re.findall(r"<li[^>]*>(.*?)</li>|<p[^>]*>(.*?)</p>", zone, re.S | re.I)
        cands = [a or b for a, b in items] or [zone]
        for raw in cands:
            t = _strip_dir_noise(re.sub(r"\s+", " ", strip_tags(raw)).strip(" ·-—"))
            t = t.replace("\u200b", "").strip()
            if _is_direction(t) and t not in out:
                out.append(t)
        if out:
            break
    return out


# ══════════════════════════════════════════════════════════════
# 分类层
# ══════════════════════════════════════════════════════════════
def load_rules() -> dict:
    if RULE_FILE.exists():
        try:
            data = json.loads(RULE_FILE.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}
        except Exception as e:
            print(f"[WARN] 规则表解析失败，改用内置规则：{e}", file=sys.stderr)
    return {k: v for k, v in DEFAULT_RULES.items() if not k.startswith("_")}


def classify(directions: list[str], rules: dict) -> list[str]:
    """把方向文本归入细分方向；命中多类就全记，未命中返回 []。"""
    if not directions:
        return []
    blob = " ".join(directions).lower()
    hits = []
    for label, kws in rules.items():
        for kw in kws:
            if str(kw).lower() in blob:
                hits.append(label)
                break
    return hits


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
def outdir_of(s: str | None, unit: str) -> pathlib.Path:
    if s:
        p = pathlib.Path(s)
        return p if p.is_absolute() else (WD / p)
    return WD / ".work_cdp" / "mentors" / unit


def stage_list(unit_cfg: dict, list_url_tpl: str, d: pathlib.Path,
               total_pages: int, log) -> dict:
    """抓列表页（分页）→ 汇总全部教师条目。

    totalpage 参数在 tsites 上不可信（同一页可自称 1/8 与 1/20），
      所以始终以「共N条 ÷ 每页条数」反推的真实页数为准；
      并且**连续两页没有新增人员就提前停止**，防止把页数猜大后空转。
    """
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "raw_list"
    raw.mkdir(exist_ok=True)

    # 先不带 totalpage 抓第一页（拿到真实总数与每页条数）
    probe = re.sub(r"[&?]totalpage=\d+", "", list_url_tpl)
    first_url = probe.format(page=1, totalpage=1) if "{totalpage}" in probe \
        else probe.format(page=1)
    log.info("抓列表页 1 …")
    first = fetch(first_url)
    (raw / "page_001.html").write_text(first, encoding="utf-8")

    first_items = parse_list(first)
    per_page = len(first_items) or 12
    declared = int(re.search(r"共\s*(\d+)\s*条", strip_tags(first)).group(1)) \
        if re.search(r"共\s*(\d+)\s*条", strip_tags(first)) else 0
    auto_pages = parse_total_pages(first, per_page=per_page)
    total_pages = total_pages or auto_pages or 1
    log.info(f"共 {declared or '?'} 人 / 每页 {per_page} → 需抓 {total_pages} 页"
             + ("（已按实测校正，忽略页面自称的 totalpage）" if auto_pages != total_pages else ""))

    people = list(first_items)
    for p in range(2, total_pages + 1):
        url = list_url_tpl.format(page=p, totalpage=total_pages) \
            if "{totalpage}" in list_url_tpl else list_url_tpl.format(page=p)
        log.info(f"抓列表页 {p}/{total_pages} …")
        try:
            page = fetch(url)
        except Exception as e:
            log.warn(f"[WARN] 第 {p} 页失败，跳过：{e}")
            continue
        (raw / f"page_{p:03d}.html").write_text(page, encoding="utf-8")
        got = parse_list(page)
        known = {x["slug"] for x in people}
        fresh = [g for g in got if g["slug"] not in known]
        people.extend(fresh)
        log.info(f"   → {len(got)} 人（新增 {len(fresh)}）")
        if got and not fresh:
            log.info(f"   第 {p} 页无新增人员 → 判定已到末页，提前停止")
            break
        time.sleep(0.4)          # 拟人化间隔，避免给学校站点压力

    # 按 slug 去重（跨页重复）
    dedup, seen = [], set()
    for p in people:
        if p["slug"] in seen:
            continue
        seen.add(p["slug"])
        dedup.append(p)

    (d / "roster_list.json").write_text(
        json.dumps(dedup, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"[OK] 列表完成：{len(dedup)} 人（去重后）"
             + (f"；官网自称 {declared} 人" if declared else ""))
    if declared and len(dedup) < declared * 0.9:
        log.warn(f"[WARN] 抓到 {len(dedup)} 人，明显少于官网自称的 {declared} 人 —— "
                 f"可能分页参数有变，请人工核对 {raw} 下的原始页面")
    return {"count": len(dedup), "pages": total_pages, "people": dedup}


def stage_detail(unit_cfg: dict, d: pathlib.Path, log, limit: int = 0,
                 deep: bool = True) -> list[dict]:
    """逐人抓详情页取研究方向。已抓过的（detail 有 directions 或标记 ok）跳过。

    deep=True（默认）时，主页没有方向的还会**深入「研究领域」子页**再找一次
    （见 find_direction_subpages）。这是 v9 新增：实测 130 位「无方向」老师里
    有 66 人主页带「研究领域」子页链接，方向正文在那里而不在主页。
    """
    lst_file = d / "roster_list.json"
    if not lst_file.exists():
        raise SystemExit(f"[X] 缺少 {lst_file}；先跑 --stage=list")
    people = json.loads(lst_file.read_text(encoding="utf-8"))

    det_file = d / "roster_detail.json"
    done: dict = {}
    if det_file.exists():
        try:
            for rec in json.loads(det_file.read_text(encoding="utf-8")):
                done[rec["slug"]] = rec
        except Exception:
            pass

    raw = d / "raw_detail"
    raw.mkdir(exist_ok=True)
    raw_sub = d / "raw_subpage"

    # 深度模式要重跑两类人：
    #   ① 从未抓过的；
    #   ② 抓过但没方向、且**当时没试过子页**的（早期版本留下的记录）。
    #   判据用 _deep_tried 标记，避免每次重跑都把所有无方向的人再刷一遍。
    if deep:
        todo = [p for p in people
                if p["slug"] not in done
                or (not (done[p["slug"]].get("directions") or [])
                    and not done[p["slug"]].get("_deep_tried"))]
    else:
        todo = [p for p in people if p["slug"] not in done]
    if limit:
        todo = todo[:limit]
    log.info(f"待抓详情：{len(todo)} 人（已有记录 {len(done)}）"
             + (" [深度模式：含子页]" if deep else ""))

    ok = fail = 0
    n_sub_tried = n_sub_hit = 0
    for i, p in enumerate(todo, 1):
        try:
            page = fetch(p["url"], timeout=25)
        except Exception as e:
            done[p["slug"]] = {**p, "directions": [], "status": "fetch_failed",
                               "why": str(e)[:200]}
            fail += 1
            log.warn(f"  [{i}/{len(todo)}] [X] {p['name']}：{e}")
            continue

        # 少数老师的页面是空白跳转页（实测曹鹏 /en/ 版只有 536 字节）。
        #   只在**长度明显异常且无任何方向锚点**时才补抓一次中文页；
        #   不做双向乱换（曾把英文页换出教育经历行当成研究方向）。
        if len(page) < 1500 and "/yjfx/" not in page and "/zh_CN/" not in p["url"]:
            try:
                page2 = fetch(p["url"].replace("/en/", "/zh_CN/"), timeout=25)
                if len(page2) > len(page):
                    page = page2
            except Exception:
                pass

        info = parse_detail(page)
        (raw / f"{p['slug']}.html").write_text(page, encoding="utf-8")

        # ── v9：主页没方向 → 深入子页 ──
        rec_extra: dict = {}
        if deep and not info["directions"]:
            subs = info.get("_subpages") or []
            rec_extra["_deep_tried"] = True
            if subs:
                raw_sub.mkdir(exist_ok=True)
                for s in subs[:3]:            # 最多试 3 个子页，够用且不刷爆站点
                    n_sub_tried += 1
                    try:
                        sp = fetch(s["url"], timeout=25)
                    except Exception:
                        continue
                    (raw_sub / f"{p['slug']}_{s['kind']}.html").write_text(
                        sp, encoding="utf-8")
                    found = parse_direction_subpage(sp)
                    if found:
                        info["directions"] = found
                        rec_extra["direction_source"] = s["label"]
                        rec_extra["direction_source_url"] = s["url"]
                        n_sub_hit += 1
                        break
                    time.sleep(0.2)

        rec = {**p, **info, **rec_extra}
        rec.pop("_subpages", None)            # 内部字段不入库
        if info["directions"]:
            rec["status"] = "ok"
            rec["why"] = ""
        else:
            # v9：区分「网站没填」与「解析不到」，让使用者知道该不该再爬
            reason = classify_no_direction(page)
            rec["status"] = "no_direction"
            rec["no_direction_reason"] = reason
            rec["why"] = _NO_DIR_REASON_CN.get(reason, "无法判定")
        done[p["slug"]] = rec
        ok += 1
        if i % 10 == 0 or i == len(todo):
            log.info(f"  [{i}/{len(todo)}] 进行中（成功 {ok} / 失败 {fail}）"
                     + (f"｜子页命中 {n_sub_hit}/{n_sub_tried}" if deep else ""))
        time.sleep(0.35)

    merged = [done.get(p["slug"]) or {**p, "directions": [], "status": "pending"}
              for p in people]
    det_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    with_dir = sum(1 for r in merged if r.get("directions"))
    from_sub = sum(1 for r in merged if r.get("direction_source"))
    log.info(f"[OK] 详情完成：本轮成功 {ok} / 失败 {fail}；"
             f"名单共 {len(merged)} 人，其中 {with_dir} 人已解析出研究方向"
             + (f"（含子页补回 {from_sub} 人）" if from_sub else "")
             + f"，{len(merged) - with_dir} 人无")
    return merged


def build_report(d: pathlib.Path, unit_cfg: dict, log) -> dict:
    """按细分方向归类，产出人可读的 Markdown 报告与机读 JSON。"""
    det_file = d / "roster_detail.json"
    if not det_file.exists():
        raise SystemExit(f"[X] 缺少 {det_file}；先跑 --stage=detail")
    people = json.loads(det_file.read_text(encoding="utf-8"))
    rules = load_rules()

    for p in people:
        p["groups"] = classify(p.get("directions") or [], rules)

    grouped: dict = {}
    for p in people:
        for g in p["groups"] or ["未分类"]:
            grouped.setdefault(g, []).append(p)

    no_dir = [p for p in people if not (p.get("directions") or [])]
    payload = {
        "unit": unit_cfg,
        "total": len(people),
        "classified": sum(1 for p in people if p["groups"]),
        "no_direction": len(no_dir),
        "groups": {g: [{"name": p["name"], "slug": p["slug"], "url": p["url"],
                        "title": p.get("title", ""),
                        "directions": p.get("directions") or []}
                       for p in sorted(v, key=lambda x: x["name"])]
                   for g, v in sorted(grouped.items(), key=lambda kv: -len(kv[1]))},
        "people": people,
    }
    (d / "mentors_grouped.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Markdown 报告 ──
    L = [f"# {unit_cfg.get('school','')} · {unit_cfg.get('name','')} 导师名单与方向归类",
         "",
         f"- 抓取来源：{unit_cfg.get('list_url','(自定义)')}",
         f"- 教师总数：**{len(people)}**",
         f"- 成功归类：**{payload['classified']}**；"
         f"页面上无研究方向栏位：**{payload['no_direction']}**",
         f"- 分类规则：`templates/导师方向规则.json`"
         f"（本次{'已加载' if RULE_FILE.exists() else '未找到，使用内置兜底规则'}）",
         "",
         "> [WARN] 分类是**按关键词做的粗归类**，不是权威学科划分；"
         "一位老师可同时出现在多个方向下。**请以老师主页原文为准**。",
         ""]

    for g, members in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        L.append(f"## {g}（{len(members)} 人）")
        L.append("")
        L.append("| 姓名 | 职称 | 研究方向（官网原文） | 主页 |")
        L.append("|---|---|---|---|")
        for p in sorted(members, key=lambda x: x["name"]):
            ds = "；".join(p.get("directions") or [])
            if not ds:
                # v9：没有方向时写明**具体原因**，而不是笼统的「无该栏位」，
                #   好让使用者判断"是网站没填"还是"值得再抓一次"
                ds = "—（" + (p.get("why") or "官网未提供") + "）"
            src = p.get("direction_source")
            if src:
                ds += f" 【来源：{src}】"
            ds = ds.replace("|", "／").replace("\n", " ")
            if len(ds) > 300:
                ds = ds[:297] + "…"
            L.append(f"| {p['name']} | {p.get('title','') or '—'} | {ds} | "
                     f"[链接]({p['url']}) |")
        L.append("")

    # ── v9：把「无方向」的原因做成统计表，避免使用者误判 ──
    from collections import Counter as _C
    reasons = _C(p.get("no_direction_reason", "unknown")
                 for p in people if not (p.get("directions") or []))
    if reasons:
        L.append("---")
        L.append("")
        L.append("## 附：「官网未提供研究方向」的原因分布")
        L.append("")
        L.append("> [WARN] 下列老师的官网页面**确实没有研究方向数据** ——")
        L.append("> 实测已抓过其「研究领域」子页并核对页面结构，")
        L.append("> **不是解析遗漏，再抓一次也拿不到**。")
        L.append("> 如确需了解，请点「主页」链接自行查看其论文成果或简介。")
        L.append("")
        L.append("| 原因 | 人数 | 说明 |")
        L.append("|---|---|---|")
        for r, n in reasons.most_common():
            L.append(f"| `{r}` | {n} | {_NO_DIR_REASON_CN.get(r, '无法判定')} |")
        L.append("")

    (d / "导师名单与方向归类.md").write_text("\n".join(L), encoding="utf-8")
    log.info(f"[OK] 归类完成：{len(grouped)} 个方向桶；报告 → {d / '导师名单与方向归类.md'}")
    return payload


def print_selection_prompt(payload: dict) -> None:
    """给 agent 用的「提示并等待用户选择」话术。"""
    print()
    print("═" * 70)
    print("下一步：请把下列方向 + 代表性导师名单呈现给用户，并**等待其选择**")
    print("═" * 70)
    n = 0
    for g, members in payload["groups"].items():
        if g == "未分类":
            continue
        names = [m["name"] for m in members][:8]
        more = f" 等 {len(members)} 人" if len(members) > 8 else ""
        tail = "…" if len(members) > 8 else ""
        print(f"  · {g}（{len(members)} 人）：{'、'.join(names)}{tail}{more}")
        n += 1
        if n >= 20:
            print("  （其余方向见报告）")
            break
    print()
    print("  建议话术：")
    print("    「以上是按官网研究方向做的粗归类。你想选哪个方向？"
          "也可以直接点名某位老师，或说你更关心的问题。」")
    print("  用户选定后 → 进入阶段 R（平行检索该方向的优质综述）。")
    print()


# ══════════════════════════════════════════════════════════════
# [STOP] 已弃用（v11）—— 默认拒绝运行
#
# 本脚本抓的是**学校教师个人主页系统**（faculty.ustc.edu.cn 等）。它有两个硬伤：
#   ① 只登记「已在系统里维护主页」的教师，覆盖面不全；
#   ② 大量页面根本没有「研究方向」栏位
#      （实测 USTC 生医部 237 人中 130 人没有）。
#
# 现流程**统一以学部/学院官网为准**，改用：
#     scripts/harvest_faculty.py   （配置驱动，见 templates/学部官网源.json）
#
# 保留本脚本仅为：翻阅历史数据、对照两种数据源差异、必要时回滚。
# 确需运行请显式加 --allow-deprecated。
# ══════════════════════════════════════════════════════════════
_DEPRECATED_MSG = """
[STOP] harvest_mentors.py 已弃用（v11）
   本脚本抓取**学校教师个人主页系统**，覆盖面与方向完整度均不如学部官网。
   实测：USTC 生医部 237 人中 130 人无「研究方向」栏位。

   [OK] 请改用：harvest_faculty.py（配置驱动，学部/学院官网）
        uv run python 流程1/scripts/harvest_faculty.py --list-units
        uv run python 流程1/scripts/harvest_faculty.py --unit ustc:life-med

   确需运行本脚本（如核对历史数据）：追加 --allow-deprecated
"""


def _guard_deprecated(argv: list[str]) -> None:
    if "--allow-deprecated" not in argv:
        print(_DEPRECATED_MSG, file=sys.stderr)
        raise SystemExit(2)


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    _guard_deprecated(list(sys.argv if argv is None else argv))
    ap = argparse.ArgumentParser(
        prog="harvest_mentors.py",
        description="【已弃用】原阶段 S：从**学校教师主页系统**抓导师与研究方向。"
                    "请改用 harvest_faculty.py（学部官网）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("[STOP] 本脚本已弃用，请改用 harvest_faculty.py\n"
                "确需运行请追加 --allow-deprecated\n\n"
                "示例：\n"
                "  harvest_mentors.py --list-units\n"
                "  harvest_mentors.py --unit life-med --out .work_cdp/mentors\n"
                "  harvest_mentors.py --unit life-med --stage=list\n"
                "  harvest_mentors.py --unit life-med --reclassify\n"))
    ap.add_argument("--unit", help=f"内置学部 id：{', '.join(UNITS)}")
    ap.add_argument("--list-url", help="自定义教师列表页模板（用 {page} 占位页码）")
    ap.add_argument("--total-pages", type=int, default=0, help="总页数；0=自动探测")
    ap.add_argument("--school", default="", help="学校名（自定义学部时用）")
    ap.add_argument("--unit-name", default="", help="学部名（自定义学部时用）")
    ap.add_argument("--out", help="输出目录（默认 .work_cdp/mentors/<unit>）")
    ap.add_argument("--stage", choices=["list", "detail", "all"], default="all",
                    help="list=只抓列表；detail=只续抓详情；all=一条命令到底（默认）")
    ap.add_argument("--limit", type=int, default=0, help="详情阶段最多抓几人（0=全部，调试用）")
    ap.add_argument("--no-deep", action="store_true",
                    help="关闭深度模式：只抓主页，不深入「研究领域」子页（v9 默认开启）")
    ap.add_argument("--reclassify", action="store_true",
                    help="只重新分类并出报告，不联网")
    ap.add_argument("--list-units", action="store_true", help="列出内置学部后退出")
    ap.add_argument("--allow-deprecated", action="store_true",
                    help="【已弃用脚本】显式确认后才允许运行（请优先改用 harvest_faculty.py）")
    args = ap.parse_args(argv)

    log = get("mentors")

    if args.list_units:
        print("内置学部：")
        for k, v in UNITS.items():
            print(f"  {k:<10} {v['school']} · {v['name']}"
                  f"（约 {v['total_pages'] or '?'} 页）")
            if v.get("note"):
                print(f"             {v['note']}")
        print("\n自定义：--list-url '<列表页URL，把页码换成 {page}>' "
              "--school <学校> --unit-name <学部>")
        return 0

    # 组装 unit 配置
    if args.unit:
        if args.unit not in UNITS:
            print(f"未知学部 {args.unit!r}；可用：{', '.join(UNITS)}", file=sys.stderr)
            return 2
        cfg = dict(UNITS[args.unit])
        tpl = cfg["list_url"]
        total = args.total_pages or cfg["total_pages"]
    elif args.list_url:
        cfg = {"school": args.school or "(未指定学校)",
               "name": args.unit_name or "(未指定学部)",
               "list_url": args.list_url, "note": "自定义"}
        tpl = args.list_url
        total = args.total_pages
    else:
        print("[X] 需要 --unit 或 --list-url（或 --list-units 查看内置）", file=sys.stderr)
        return 2

    if "{page}" not in tpl:
        # 允许直接粘贴带 PAGENUM=N 的 URL：自动改成模板
        if "PAGENUM=" in tpl:
            tpl = re.sub(r"PAGENUM=\d+", "PAGENUM={page}", tpl)
        else:
            print("[X] 列表页 URL 里找不到 {page} 占位符，也没有 PAGENUM= 可替换",
                  file=sys.stderr)
            return 2
    if "{totalpage}" not in tpl:
        tpl = re.sub(r"totalpage=\d+", "totalpage={totalpage}", tpl) \
            if "totalpage=" in tpl else tpl

    unit_key = args.unit or "custom"
    d = outdir_of(args.out, unit_key)
    log.info(f"输出目录：{d}")

    if args.reclassify:
        payload = build_report(d, cfg, log)
    else:
        if args.stage in ("list", "all"):
            stage_list(cfg, tpl, d, total, log)
        if args.stage in ("detail", "all"):
            stage_detail(cfg, d, log, limit=args.limit, deep=not args.no_deep)

        # 只要详情已存在就出报告 —— 早期版本仅在 --stage=all 时生成，
        #   导致「先 list 再 detail」这条**最常见的分步用法**跑完却没有报告，
        #   用户得再想起第三条命令。现在 detail 跑完即自动归类。
        if args.stage in ("detail", "all") and (d / "roster_detail.json").exists():
            payload = build_report(d, cfg, log)
        elif (d / "mentors_grouped.json").exists():
            payload = json.loads((d / "mentors_grouped.json").read_text(encoding="utf-8"))
        else:
            payload = None

    if payload:
        # 只在**全员**抓完时才提示进入选择环节；限流调试（--limit）时如实说明
        n_total = payload["total"]
        n_done = sum(1 for p in payload["people"] if p.get("status") != "pending")
        print_selection_prompt(payload) if n_done >= n_total else print(
            f"\n[INFO] 仅抓取 {n_done}/{n_total} 人（--limit 调试模式）——"
            f"**不要**在此提示用户选择；补跑 detail 抓完再呈现。\n")
        journal("S", "mentors.harvest", unit=unit_key,
                total=n_total, groups=len(payload["groups"]))
        print(f"→ 报告：{d / '导师名单与方向归类.md'}")
        print(f"→ 机读：{d / 'mentors_grouped.json'}")
    else:
        print(f"→ 列表已就绪：{d / 'roster_list.json'}")
        print("  继续跑详情：uv run python 流程1/scripts/harvest_mentors.py "
              f"--unit {unit_key} --stage=detail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
