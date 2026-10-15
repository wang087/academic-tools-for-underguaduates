# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S：**学部/学院官网**导师抓取（配置驱动，唯一数据源）
#
# 为什么只从学部官网取
#   学校教师主页系统（faculty.ustc.edu.cn / person.zju.edu.cn 等）有两个硬伤：
#     ① 只登记「已在系统里维护主页」的教师，覆盖面不全；
#     ② 大量页面根本没有「研究方向」栏位（实测 USTC 237 人中 130 人没有）。
#   学部/学院官网的师资页由**学院自己维护**，方向/邮箱/职称常直接在列表页上，
#   新鲜度与完整度都更好。故本流程统一以学部官网为准。
#
# 设计
#   · **配置驱动**：所有学校/学院/栏目/解析规则都在
#     `templates/学部官网源.json`，新增学校**不需要改本脚本**。
#   · 两种列表形态：
#       star  —— 列表页**已含**方向与邮箱（浙大生科院所属），无需进详情页
#       fetch —— 列表页只给姓名+链接，须进详情页抽（USTC 生医部所属）
#   · 只用标准库；两段式缓存，断点可续。
#
# 用法：
#   uv run python 流程1/scripts/harvest_faculty.py --list-units
#   uv run python 流程1/scripts/harvest_faculty.py --unit zju
#   uv run python 流程1/scripts/harvest_faculty.py --unit zju --unit-name 生命科学学院
#   uv run python 流程1/scripts/harvest_faculty.py --unit zju --stage=list
#   uv run python 流程1/scripts/harvest_faculty.py --unit zju --out outputs/zju
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

from logutil import run_root as _run_root  # noqa: E402
from run_status import calculate_status, write_status  # noqa: E402

# ── UTF-8 兜底（幂等）──


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


WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 (academic-use)")

CONFIG_PATH = _HERE.parent / "templates" / "学部官网源.json"


# ══════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════
_TAG = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return _html.unescape(_TAG.sub("", s or "")).strip()


def squash(s: str) -> str:
    """收敛空白：站群 HTML 的 span 碎片之间会产生大量空白与 &nbsp;。"""
    s = (s or "").replace("\u00a0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, maxlen: int = 24) -> str:
    """安全文件名片段，**保留中文**（早期版本剥掉中文导致存档全叫 `_x.html`）。"""
    s = _WIN_BAD.sub("", (name or "").strip())
    s = re.sub(r"\s+", "", s).rstrip(". ")
    return s[:maxlen] or "x"


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
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
                time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"取页面失败：{url} —— {last}")


# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════
def load_config(path: pathlib.Path | None = None) -> dict:
    p = pathlib.Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise SystemExit(f"[X] 找不到配置文件 {p}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if "universities" not in cfg:
        raise SystemExit(f"[X] 配置文件缺少 universities 段：{p}")
    # 复用模式：引擎分派型 list_type 由代码提供，避免配置缺项导致 KeyError
    cfg.setdefault("list_types", {}).setdefault(
        "engine_dispatch",
        {"_desc": "引擎分派：解析规则见 scripts/faculty_engines.py",
         "_engine_dispatch": True})
    return cfg


def resolve_unit(cfg: dict, key: str) -> tuple[str, str, dict]:
    """把 `--unit` 的值解析成 (学校key, 学院key, 学院配置)。

    支持三种写法：
      · 学校 key        `zju`
      · `学校:学院`     `zju:life-sci`
      · 中文名/别名     `浙大` / `浙江大学生命科学学院`
    """
    k = (key or "").strip().lower()
    unis = cfg["universities"]

    # ① 显式 `school:unit`
    if ":" in k:
        sk, uk = k.split(":", 1)
        sk, uk = sk.strip(), uk.strip()
        if sk in unis and uk in unis[sk].get("units", {}):
            return sk, uk, unis[sk]["units"][uk]
        raise SystemExit(f"[X] 找不到 {k}；用 --list-units 看可选项")

    # ② 学校 key 且只有一个学院 → 直接用它
    if k in unis:
        units = unis[k].get("units", {})
        if len(units) == 1:
            uk = next(iter(units))
            return k, uk, units[uk]
        raise SystemExit(f"[X] {k} 有多个学院，请用 `{k}:<学院key>`；"
                         f"可选：{list(units)}")

    # ③ 中文名 / 别名 / 学院名 模糊匹配
    for sk, sv in unis.items():
        names = [sk, sv.get("name", "")] + list(sv.get("alias", []))
        if any(n and n.lower() in k for n in names):
            units = sv.get("units", {})
            # 若同时点名了学院
            for uk, uv in units.items():
                if uv.get("name", "") and uv["name"] in key:
                    return sk, uk, uv
            if len(units) == 1:
                uk = next(iter(units))
                return sk, uk, units[uk]
    raise SystemExit(f"无法识别 {key!r}；用 --list-units 看可选项")


# ══════════════════════════════════════════════════════════════
# 通用清洗（与 harvest_colleges / harvest_mentors 判据保持一致）
# ══════════════════════════════════════════════════════════════

# 栏目名 / 导航 / 面包屑 / UI 标签 —— 实测假阳性
_NOT_DIRECTION = re.compile(
    r"^(?:首页|社会兼职|教育经历|工作经历|团队成员|论文成果|著作成果|科研项目|"
    r"专利|获奖信息|个人信息|研究方向|研究兴趣|研究领域|联系方式|已发表文章|"
    r"代表性论文|招生信息|招生招聘|个人简介|基本信息|发表文章|更多|暂无内容|"
    r"我的相册|教师博客|加入我们|其他栏目|其他联系|同专业博导|同专业硕导|"
    r"高级工程师|工程师|实验师|课题组主页|团队主页|回到顶部|打印|关闭|"
    r"English|登录|查看|展开)\s*$"
    r"|(?:查看更多|点击查看|详细)"                       # UI 占位符
    r"|(?:当前位置|中文主页|>>)"                          # 面包屑
    r"|@[A-Za-z0-9.\-]+\.(?:com|cn|edu|org)"             # 邮箱
    r"|(?:国家自然科学基金|青年科学基金|面上项目|基金项目|"
    r"重点研发计划|973|863)"                              # 基金
    r"|(?:校长|副校长|院长|副院长|所长|副所长|系主任|党委书记|"
    r"学部委员|政协委员|人大代表|先进工作者)"              # 职务 / 荣誉
    r"|(?:讲席教授|特聘教授|特任教授|兼职教授|客座教授|博导|博士生导师|"
    r"总编辑|副总编辑|Associate\s+Editor|编委)"            # 头衔
    r"|(?:Google\s+Scholar|总引用|引用频次|H\s*指数)"      # 计量
    r"|(?:国家科学技术进步奖|自然科学奖|技术发明奖|杰出青年基金获得者)"
    r"|^(?:招生|招聘|报考|招收)方向"
    r"|(?:主要学术成就|学术成就)")

_NAV_WORDS = (r"(?:科学研究|研究领域|研究方向|研究兴趣|教学研究|教学资源|"
              r"授课信息|教学成果|论文成果|专利|著作成果|科研项目|获奖信息|"
              r"招生信息|学生信息|我的相册|教师博客|社会兼职|教育经历|"
              r"工作经历|基本信息|团队成员|其他联系方式)")

_JOURNAL = re.compile(
    r"\b(?:Nature|Science|Cell|NEJM|Lancet|EMBO|PNAS|eLife|Trends\s+in|"
    r"Current\s+Opinion|Journal\s+of|Frontiers\s+in)\b", re.I)
_ACHIEVE = re.compile(
    r"SCI\s*论文\s*\d+\s*篇|影响因子|被引\s*\d+\s*次|通讯作者|第一作者|"
    r"共同一作|\(20\d{2}\)|（20\d{2}）|博士，教授")


def strip_fund(t: str) -> str:
    """只剥掉条目里的**基金/项目括号**，保留中文主干。

    为什么必须有这一步：实测（USTC 涂晓明）三条方向写成
        「锥体虫细胞周期调控的分子机制研究（国家自然科学基金项目,中科院创新项目子课题）」
      括号里是基金信息应当去掉，但**主干是真实研究方向**。
      若因为"含基金字样"整条丢弃，会误杀真方向（实测丢过 3 条）。

    [STOP] 调用顺序很重要：**先 strip_fund 再 is_direction**。
      is_direction 保留了「含基金词即拒」的兜底（防其它形式漏过），
      顺序反了就会整条误杀。

    [STOP] 括号内可能**含逗号/分号**（如「（国家自然科学基金项目,中科院创新项目子课题）」），
      而方向分隔符恰恰就是逗号分号 —— 若先按分隔符切，括号会被切碎，
      留下 `中科院创新项目子课题）` 这种残缺片段（实测出现过）。
      因此**必须整条先剥括号，再交给上层按分隔符切**。
    """
    t = t or ""
    # 反复剥，直到没有可剥的（括号可能并列出现）
    for _ in range(4):
        new = re.sub(r"[（(][^（()）]*(?:基金|项目|子课题|973|863|重点研发|"
                     r"面上项目|中科院|资助|科技部|教育部)[^（()）]*[)）]", "", t)
        if new == t:
            break
        t = new
    # 兜底：若还剩**未闭合**的括号残片（说明原文括号不配对），把整段尾巴切掉
    t = re.sub(r"[（(][^（()）]*$", "", t)
    return squash(t).strip("  ·-—，,。.；;：:、")


def is_direction(t: str) -> bool:
    """判断一段文本像不像「研究方向」。

    这是**唯一**判据入口 —— harvest_faculty / harvest_colleges /
      harvest_mentors 三处共用同一套规则，避免标准漂移
      （曾经漂移导致 9 条残留漏网）。
    """
    if not t:
        return False
    t = squash(t)
    if not (2 <= len(t) <= 600):
        return False
    if _NOT_DIRECTION.search(t):
        return False
    if "当前位置" in t or "中文主页" in t or ">>" in t:
        return False
    # 多栏目名串成的导航行
    if len(re.findall(_NAV_WORDS, t)) >= 2:
        residue = re.sub(_NAV_WORDS, "", t)
        residue = re.sub(r"[\s·\-—、,，。;；]+", "", residue)
        if len(residue) < 6:
            return False
    # 论文列表 / 引文片段
    if len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}#|,\s*[A-Z]{1,3}\s*,", t)) >= 2:
        return False
    if not re.search(r"[\u4e00-\u9fff]", t) and \
            len(re.findall(r"[A-Z][a-z]+\s+[A-Z]{1,3}\b", t)) >= 2:
        return False
    if _JOURNAL.search(t) or _ACHIEVE.search(t):
        return False
    if not re.search(r"[\u4e00-\u9fff]|[A-Za-z]{3,}", t):
        return False
    if re.match(r"^[【\[（(].{0,20}[】\]）)]$", t):
        return False
    return True


def split_directions(raw: str, cfg: dict) -> list[str]:
    """把「方向a，方向b、方向c」拆成条目并按判据过滤。"""
    if not raw:
        return []
    # 关键顺序：**先整条剥基金括号，再按分隔符切**。
    #   括号内可能含逗号/分号（「（国家自然科学基金项目,中科院创新项目子课题）」），
    #   先切会把括号切碎，留下 `中科院创新项目子课题）` 这种残片（实测出现过）。
    raw = strip_fund(raw)

    pat = (cfg.get("parse_rules", {}).get("direction_split")) or "[，,、；;]"
    # 补充分隔符：站群里有用「·」「•」「/」「|」分隔的
    #   实测周如鸿页写「计算生物学 · 生物物理学 · 人工智能+」，
    #   只按逗号/分号切会整条留下，去重合并时又和另一种写法并存 → 重复条目。
    pat = pat.rstrip("]") + r"·•・|｜/／]"
    out = []
    for part in re.split(pat, raw):
        t = squash(part).strip("  ·-—，,。.；;：:、")
        # 去掉可能带的「研究方向：」前缀
        t = re.sub(r"^(?:主要)?研究(?:方向|领域|兴趣)\s*[：:]\s*", "", t)
        # 去掉句末的「等」「等研究」这类尾巴
        t = re.sub(r"[，,]?\s*等(?:研究|方面)?$", "", t).strip()
        # 再剥一次（防止切分后新暴露的括号）
        t = strip_fund(t)
        if is_direction(t) and t not in out:
            out.append(t)
    return out


def split_name_title(name_raw: str, cfg: dict) -> tuple[str, str]:
    """从「易文  教授」「徐良 百人计划研究员」里拆出 (姓名, 职称)。

    实测坑：浙大把职衔写成「百人计划研究员」「求是特聘教授」这类**复合词**，
      若只按最长的单个关键词匹配，会出现
        「朱强 百人计划 研究员」→ 姓名="朱强 百人计划"、职称="研究员"
      姓名里残留"百人计划"。因此把常见**职衔前缀**一并识别。
    """
    t = squash(strip_tags(name_raw))
    t = re.sub(r"^\d+[、.)]\s*", "", t)          # 去列表序号
    if not cfg.get("parse_rules", {}).get("strip_title_from_name", True):
        return t, ""

    kws = cfg.get("parse_rules", {}).get("title_keywords") or []
    # 姓名里可能附着的**标记词**（不是职称，但要从姓名剥离）
    #   实测浙大兼聘教师在列表页写「黄俊 （兼聘）」，
    #   研究所里又写「林爱福 长聘」—— 这两类若不清掉，
    #   报告里会出现「徐平龙 （兼聘）」这种假姓名。
    MARK = r"(?:兼聘|长聘|短期|访问|兼职|外聘|双聘|全职)"
    t = re.sub(rf"[\s（(]*{MARK}[\s）)]*\s*$", "", t).strip()
    # 复合职衔前缀（单独出现时也应从姓名里剥离）
    PREFIX = r"(?:百人计划|求是特聘|求是讲席|青年|特聘|特任|兼聘|讲座|客座)"
    # ① 先尝试「[前缀] + 职称词」的完整匹配
    for kw in sorted(kws, key=len, reverse=True):
        m = re.search(rf"\s*(?:{PREFIX})?\s*{re.escape(kw)}\s*$", t)
        if m:
            title = squash(t[m.start():])
            name = t[: m.start()].strip("  ·-—")
            # ② 姓名里若还残留裸前缀/标记词，再去一次
            name = re.sub(rf"\s*{PREFIX}\s*$", "", name).strip("  ·-—")
            name = re.sub(rf"[\s（(]*{MARK}[\s）)]*\s*$", "", name).strip()
            name = name.strip("  ·-—（）()")
            return name, title
    # 没匹配到职称词时，也把标记词清掉
    t = re.sub(rf"[\s（(]*{MARK}[\s）)]*\s*", " ", t).strip()
    return t, ""


# ══════════════════════════════════════════════════════════════
# 列表页解析（按 list_type 分派）
# ══════════════════════════════════════════════════════════════
def _abs_url(base: str, url: str) -> str:
    """把相对 URL 补成绝对 URL。

    实测坑：浙大兼聘/支撑队伍列表里有 `<a href="/clscn/2022/0920/…/page.htm">`
      这种**站内相对路径**，直接拿去 fetch 会失败（缺主机名），
      导致这些人被静默跳过、方向永远补不到。
    """
    u = (url or "").strip()
    if not u or u.startswith("javascript:"):
        return ""
    if u.startswith("//"):
        return "http:" + u
    if u.startswith("/"):
        return base.rstrip("/") + u
    if not u.startswith("http"):
        return base.rstrip("/") + "/" + u.lstrip("/")
    return u


def _clean_name(name: str) -> str:
    """规范姓名：去掉站群为排版插入的**字间空格**。

    实测：站点把两字姓名排成「周 琦」「叶 升」「张 龙」，
      而另一处又写「周琦」—— 不去空格会导致同一个人被当成两个，
      跨栏目去重失效。
    """
    t = squash(name)
    # 仅当整体是「汉字 空格 汉字」的两字组合时才合并（避免误伤英文名）
    if re.fullmatch(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", t):
        return t.replace(" ", "")
    return t


def _browser_fetch(page: str, eng: str, section: dict, base: str,
                   cfg: dict) -> str:
    """为 render / interactive 家族准备「可被引擎解析的 page 内容」。

    · interactive：调 faculty_interact 驱动浏览器**点击分类**枚举教师，
      把结果以 `__INTERACTIVE_RESULT__` 标记注入 page，供 eng_interactive 解析。
    · render：调 faculty_render 整页渲染，把渲染后的 HTML 直接返回。

    两条路都**优雅降级**：浏览器不可用时返回空串并打印原因，
    由上层如实报告"该栏位未取到"，而不是伪造数据。
    """
    u = section.get("_url") or (base.rstrip("/") + section.get("path", ""))
    try:
        if eng == "interactive":
            from faculty_interact import CdpUnavailable, enumerate_by_clicking
            try:
                res = enumerate_by_clicking(
                    u,
                    click_selector=section.get("click_selector", "a.col_item_link"),
                    card_selector=section.get("card_selector", ""),
                    wait=float(section.get("click_wait", 3.0)),
                    port=int(os.environ.get("CDP_PORT", "9222")),
                )
            except CdpUnavailable as e:
                print(f"  [WARN] 交互枚举不可用（{e}）；该栏位未取到", file=sys.stderr)
                return ""
            n = len(res.get("teachers") or [])
            print(f"  交互枚举：点击 {res.get('clicks', 0)} 个分类 → {n} 人")
            if not n:
                return ""
            return "__INTERACTIVE_RESULT__\n" + json.dumps(res, ensure_ascii=False)
        if eng == "render":
            from faculty_render import crawler_available, render_for_engine
            if not crawler_available():
                print("  [WARN] 未找到 robot/crawl_site.py；该栏位未取到", file=sys.stderr)
                return ""
            html = render_for_engine(
                u, virtual_time_budget=int(section.get("virtual_time_budget", 12000)))
            if not html:
                print("  [WARN] 渲染未返回内容；该栏位未取到", file=sys.stderr)
            return html
    except Exception as e:                       # noqa: BLE001
        print(f"  [WARN] 浏览器链路异常：{type(e).__name__}: {e}", file=sys.stderr)
    return ""


def parse_list_page(page: str, lt: dict, cfg: dict, section: dict,
                    base: str = "") -> list[dict]:
    """按 list_type 的规则解析列表页 → [{name,title,directions,email,url}]。

    `engine_dispatch`（复用模式）：不在此处写任何站点特有正则，
      而是按 `section["engine"]` 交给 `faculty_engines.py` 的家族引擎。
      新增学校因此**只需在配置里写引擎名**，不必改本脚本。
    """
    # ── 复用模式：家族引擎分派 ──
    if lt.get("_engine_dispatch") or section.get("engine"):
        eng = section.get("engine") or ""
        if not eng:
            return []
        try:
            from faculty_engines import parse_with  # type: ignore
        except Exception:  # pragma: no cover
            return []
        # 需要真实浏览器的两个家族（render / interactive）：
        #   页面上拿不到数据，必须先驱动浏览器。
        #   §5.1：师资页属公开元数据 → 直连是第一层；此处是**第一层失败后的转 CDP 分支**。
        if eng in ("render", "interactive"):
            page = _browser_fetch(page, eng, section, base, cfg)
            if not page:
                return []
        got = parse_with(eng, page, base, section.get("name", ""), cfg)
        for r in got:
            # 引擎已给出方向（列表页含方向）时不必再进详情页
            if r.get("_dir_from_title"):
                r["direction_source"] = "列表页"
            r.pop("_block", None)
        return got

    out: list[dict] = []
    seen: set[str] = set()

    # ── A. 富卡片（列表页含方向/邮箱）──
    item_re = lt.get("item_re")
    if item_re and lt.get("direction_re"):
        for m in re.finditer(item_re, page, re.S | re.I):
            block = m.group(1)
            nm = re.search(lt["name_re"], block, re.S | re.I)
            if not nm:
                continue
            name, title = split_name_title(nm.group(1), cfg)
            name = _clean_name(name)
            if not name or len(name) > 20:
                continue
            dm = re.search(lt["direction_re"], block, re.S | re.I)
            em = re.search(lt["email_re"], block, re.S | re.I) if lt.get("email_re") else None
            lm = re.search(lt["link_re"], block, re.I) if lt.get("link_re") else None
            rec = {
                "name": name, "title": title,
                "directions": split_directions(strip_tags(dm.group(1)) if dm else "", cfg),
                "email": squash(strip_tags(em.group(1))) if em else "",
                "url": _abs_url(base, _html.unescape(lm.group(1))) if lm else "",
                "section": section.get("name", ""),
            }
            key = name + "|" + rec["section"]
            if key not in seen:
                seen.add(key)
                out.append(rec)

    # ── B. 纯姓名列表（只有姓名+链接）──
    if not out and lt.get("simple_item_re"):
        for m in re.finditer(lt["simple_item_re"], page, re.S | re.I):
            url, inner = m.group(1), m.group(2)
            name, title = split_name_title(inner, cfg)
            name = _clean_name(name)
            if not name or len(name) > 20:
                continue
            key = name + "|" + section.get("name", "")
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "title": title, "directions": [],
                        "email": "", "url": _abs_url(base, _html.unescape(url)),
                        "section": section.get("name", "")})
    return out


def parse_page_count(page: str, lt: dict) -> int:
    """从「页码 1 / 3」推断总页数（容忍 &nbsp; 与标签）。

    引擎链路（复用模式）改用 faculty_engines.page_count：
      除「页码 x/y」外还能识别 WebPlus 的 `listN.htm` 尾页链接 ——
      实测中科大化学系/复旦生科院只给 `尾页 /js/list3.htm`，没有「页码」字样。
    """
    if lt.get("_engine_dispatch"):
        try:
            from faculty_engines import page_count as _pc  # type: ignore
            return _pc(page)
        except Exception:  # pragma: no cover
            pass
    pat = lt.get("pages_re") or r"页码[^0-9]{0,12}(\d+)\s*/\s*(\d+)"
    txt = strip_tags(page)
    m = re.search(pat, txt)
    if m:
        try:
            return max(1, int(m.group(2)))
        except (ValueError, IndexError):
            pass
    return 1


def parse_declared_total(page: str, lt: dict) -> int:
    pat = lt.get("total_re")
    if not pat:
        return 0
    m = re.search(pat, strip_tags(page))
    return int(m.group(1)) if m else 0


def page_url(base: str, path: str, page_no: int, paging: str) -> str:
    """拼第 page_no 页的 URL。

    站群惯例：第 1 页 list.htm，第 2 页起 list2.htm、list3.htm…
    """
    # 跨主机的绝对 URL：原样使用（分页交给 paging 规则处理）
    if path.startswith("http"):
        if paging == "single" or page_no <= 1:
            return path
        head, _, tail = path.rpartition(".htm")
        return f"{head}{page_no}.htm{tail}" if head else path
    d = path.rsplit("/", 1)[0]                  # 去掉 list.htm 得到目录
    if paging == "single":
        return f"{base}{path}"
    if page_no <= 1:
        return f"{base}{path}"
    if paging == "psp":
        return f"{base}{d}/list{page_no}.psp"
    if paging == "query":
        return f"{base}{path}?page={page_no}"
    return f"{base}{d}/list{page_no}.htm"


# ══════════════════════════════════════════════════════════════
# 阶段实现
# ══════════════════════════════════════════════════════════════
def dedupe_people(people: list[dict], log=None) -> list[dict]:
    """按姓名合并跨栏目重复。

    为什么必须合并：浙大把同一人同时列在「人才队伍」（求是特聘/百人计划）
      和其所属「研究所」下 —— 实测 209 条记录里只有 184 个唯一姓名，
      25 条重复。若按「栏目+姓名」去重，报告里同一人会出现两次。

    合并规则（**信息多的胜出，且不丢任何方向**）：
      · 主记录：方向最多的那一条（并列时取职称更具体的）
      · 栏目：全部收集到 `sections` 数组
      · 方向：并集（两个栏目的方向表述可能互补）
      · 邮箱/主页：取第一个非空的
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    for p in people:
        k = p["name"]
        if k not in merged:
            rec = dict(p)
            rec["sections"] = [p.get("section", "")]
            merged[k] = rec
            order.append(k)
            continue
        cur = merged[k]
        sec = p.get("section", "")
        if sec and sec not in cur["sections"]:
            cur["sections"].append(sec)
        # 方向并集。
        # 两个栏目的**表述可能不同**（一个写「计算生物学 · 生物物理学」，
        #   另一个已拆成数组）。合并时对**入库的字符串再拆一次**，
        #   否则会出现「计算生物学 · 生物物理学」与「计算生物学」并存，
        #   看似两条实则重复。实测周如鸿页如此。
        for d in (p.get("directions") or []):
            for piece in split_directions(d, {"parse_rules": {}}):
                if piece not in (cur.get("directions") or []):
                    cur.setdefault("directions", []).append(piece)
        # 补齐空字段
        for f in ("email", "url", "title"):
            if not cur.get(f) and p.get(f):
                cur[f] = p[f]
        # 职称取更长的（"百人计划研究员" 比 "研究员" 更具体）
        if len(p.get("title") or "") > len(cur.get("title") or ""):
            cur["title"] = p["title"]

    out = [merged[k] for k in order]
    if log and len(out) != len(people):
        log.info(f"[OK] 跨栏目去重：{len(people)} 条 → {len(out)} 人"
                 f"（合并 {len(people) - len(out)} 条重复）")
    return out


def stage_list(unit: dict, d: pathlib.Path, log, cfg: dict,
               only_section: str = "") -> list[dict]:
    """抓各师资栏目的列表页，汇总去重。"""
    lt = cfg["list_types"][unit["list_type"]]
    base = unit["base"].rstrip("/")
    raw = d / "raw_list"
    raw.mkdir(parents=True, exist_ok=True)

    people: list[dict] = []
    seen_key: set[str] = set()

    for sec in unit["sections"]:
        if only_section and only_section not in sec["name"]:
            continue
        paging = sec.get("paging", "auto")
        log.info(f"[{sec['name']}] 抓列表 …")
        # 把本栏目的绝对 URL 挂到 section 上，供需要浏览器的家族
        # （render / interactive）使用 —— 它们在直连拿不到数据时要自己去驱动浏览器。
        sec["_url"] = page_url(base, sec["path"], 1, paging)
        try:
            first = fetch(sec["_url"])
        except Exception as e:
            log.warn(f"  [WARN] 首页失败：{e}")
            # 直连失败但栏目声明了浏览器家族 → 仍给一次机会（§5.1 转 CDP）
            if sec.get("engine") in ("render", "interactive"):
                first = ""
            else:
                continue
        (raw / f"{safe_filename(sec['name'])}_1.html").write_text(first, encoding="utf-8")

        npages = 1 if paging == "single" else parse_page_count(first, lt)
        declared = parse_declared_total(first, lt)
        got = parse_list_page(first, lt, cfg, sec, base)
        log.info(f"  第 1/{npages} 页 → {len(got)} 人"
                 + (f"（官网称共 {declared} 条）" if declared else ""))

        for p in range(2, npages + 1):
            try:
                page = fetch(page_url(base, sec["path"], p, paging))
            except Exception as e:
                log.warn(f"  [WARN] 第 {p} 页失败：{e}")
                continue
            (raw / f"{safe_filename(sec['name'])}_{p}.html").write_text(page, encoding="utf-8")
            g = parse_list_page(page, lt, cfg, sec, base)
            log.info(f"  第 {p}/{npages} 页 → {len(g)} 人")
            got.extend(g)
            time.sleep(0.4)

        for r in got:
            k = r["name"] + "|" + r["section"]
            if k in seen_key:
                continue
            seen_key.add(k)
            people.append(r)
        time.sleep(0.3)

    people = dedupe_people(people, log)
    (d / "faculty_list.json").write_text(
        json.dumps(people, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"[OK] 列表完成：{len(people)} 人"
             f"（有方向 {sum(1 for p in people if p['directions'])}）")
    return people


def stage_detail(unit: dict, d: pathlib.Path, log, cfg: dict,
                 limit: int = 0) -> list[dict]:
    """对需要进详情页的栏目补抓研究方向。"""
    lf = d / "faculty_list.json"
    if not lf.exists():
        raise SystemExit(f"[X] 缺少 {lf}；先跑 --stage=list")
    people = json.loads(lf.read_text(encoding="utf-8"))

    df = d / "faculty_detail.json"
    done: dict = {}
    if df.exists():
        try:
            for rec in json.loads(df.read_text(encoding="utf-8")):
                # 只把**真正抓过**的人算作已完成。
                #   实测坑：用 `--limit=8` 试跑时，合并阶段会把**没抓到的其余人**
                #   一并写成 status=no_direction（why 标记为"未补详情"）。
                #   若不剔除，随后跑全量会认为"都已完成"而**只补 0 人** ——
                #   试跑一次就永久废掉这次采集。复用模式必须能先小样试跑。
                if rec.get("why") == _NEVER_FETCHED_WHY:
                    continue
                if rec.get("status") not in ("ok", "no_direction"):
                    continue
                done[rec["name"] + "|" + rec.get("section", "")] = rec
        except Exception:
            pass

    need = [p for p in people if not p.get("directions") and p.get("url")]
    todo = [p for p in need if (p["name"] + "|" + p.get("section", "")) not in done]
    if limit:
        todo = todo[:limit]
    log.info(f"需补详情：{len(todo)} 人（已有 {len(done)}，无需补 "
             f"{len(people) - len(need)}）")

    raw = d / "raw_detail"
    raw.mkdir(exist_ok=True)
    ok = hit = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            page = fetch(p["url"], timeout=30)
        except Exception as e:
            done[p["name"] + "|" + p.get("section", "")] = {
                **p, "status": "fetch_failed", "why": str(e)[:160]}
            fail += 1
            continue
        slug = f"{safe_filename(p['name'])}_{safe_filename(p.get('section',''))}"
        (raw / f"{slug}.html").write_text(page, encoding="utf-8")

        dirs = extract_directions_from_detail(page, cfg)
        dir_src = "详情页正文"
        if not dirs and unit.get("list_type") == "engine_dispatch":
            # 复用模式：详情页模板更多样（页签式/行内式/正文式），
            #   用引擎库的通用抽取再试一次，避免"抓到了但解析不出"。
            try:
                from faculty_engines import extract_directions_detail  # type: ignore
                dirs = extract_directions_detail(page, cfg)
                if dirs:
                    dir_src = "详情页正文（引擎抽取）"
            except Exception:  # pragma: no cover
                pass
        rec = {**p, "directions": dirs}
        if dirs:
            rec["status"] = "ok"
            rec["direction_source"] = dir_src
            hit += 1
        else:
            # 如实分类原因：区分「网站没填」与「解析不到」，
            #   使用者才知道该不该换个方法再抓。
            reason = classify_no_direction(page)
            rec["status"] = "no_direction"
            rec["no_direction_reason"] = reason
            rec["why"] = _NO_DIR_REASON_CN.get(reason, "无法判定")
        done[p["name"] + "|" + p.get("section", "")] = rec
        ok += 1
        if i % 20 == 0 or i == len(todo):
            log.info(f"  [{i}/{len(todo)}] 成功 {ok} / 失败 {fail}｜补到方向 {hit}")
        time.sleep(0.35)

    # 合并：列表页记录 + 详情页补充
    #   取哪一条的判据：**有方向的优先**；两边都没方向时**取详情页记录** ——
    #   因为只有它带着 `classify_no_direction()` 逐页判出的真实原因
    #   （「页面无该栏位」/「暂无内容」/「主页不存在」…）。
    #   曾经的写法只在「详情页补到了方向」时才用详情页记录，于是抓过但确实没填的人
    #   会被列表页记录覆盖，`why` 落到 `_NEVER_FETCHED_WHY`（"未补详情"）——
    #   **把「抓了、网站真的没写」错报成「没抓」**，续跑判据与交付说明都会被骗。
    merged = []
    for p in people:
        k = p["name"] + "|" + p.get("section", "")
        rec = done.get(k)
        if rec is not None and (rec.get("directions") or not p.get("directions")):
            merged.append(rec)
        else:
            p.setdefault("status", "ok" if p.get("directions") else "no_direction")
            p.setdefault("why", "" if p.get("directions") else _NEVER_FETCHED_WHY)
            p.setdefault("direction_source", "列表页" if p.get("directions") else "")
            merged.append(p)

    df.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    withdir = sum(1 for r in merged if r.get("directions"))
    log.info(f"[OK] 详情完成：{withdir}/{len(merged)} 人有研究方向")
    return merged


# 详情页方向线索词（比列表页宽松：允许"及内容"变体与独立成行）
# 实测三种写法都要认：
#   ① 「研究方向：…」   标签 + 冒号
#   ② 「主要研究方向是…」标签 + 系动词
#   ③ 「主要研究兴趣利用冷冻电镜解析…」**标签与正文直接相连、无任何分隔符**
#      （UISTC 王雪娟 / 江维 页实测）；③ 必须放在最后，且用后继字符
#      排除「研究领域」这类会被前缀吃掉的相邻词。
_LEAD = re.compile(
    r"(?:课题组|实验室|团队)?\s*(?:主要|目前|当前)?\s*"
    # 长标签必须排在短标签**前面**：Python 的 `|` 是左侧优先，
    #   若先写 `研究(?:方向|领域|兴趣|…)`，遇到「研究兴趣和方向」
    #   会先在 `研究兴趣` 处匹配成功，`和方向` 就留在正文里了
    #   （实测 潘峰「研究兴趣和方向疼痛表型的识别…」）。
    r"(?:研究(?:兴趣和方向|方向和兴趣|方向及内容|兴趣及方向|方向及兴趣)|"
    r"研究(?:方向|领域|兴趣|内容)|"
    r"科研(?:方向|领域))\s*"
    r"(?:[和及与]\s*(?:方向|领域|内容))?\s*"
    r"(?:主要)?\s*(?:为|是|包括|有|如下|：|:)"
    r"|^(?:主要)?研究(?:方向|领域|兴趣|内容)\s*$"
    r"|(?:主要|目前|当前)?研究(?:方向|兴趣|内容)"
    r"(?:[和及与]\s*(?:方向|领域|内容))?"
    r"(?!\s*[：:为是])\s*(?=\S)",
    re.I | re.M)
_SECTION_STOP = re.compile(
    r"^(?:代表性?(?:学术)?论文|主要论文|发表文章|已发表文章|论文列表|"
    r"招生招聘|招生信息|联系方式|获奖信息|科研项目|Selected\s+Publications)",
    re.I)
_NUM_ITEM = re.compile(r"^\s*[（(]?\s*[1-9]\d?\s*[)）、.．]\s*\S")

# ── 「研究兴趣」段的结束判据（见 _trim_interest_segment）────────────
# ① 行内出现的终止小标题（不要求在行首 —— 实测常与正文连排）
_STOP_HEAD_ANY = re.compile(
    r"(?:代表性?(?:学术)?论著|代表性?(?:学术)?论文|主要论文|发表文章|"
    r"已发表文章|已发表论文|已发表论著|论文列表|部分学术论文|学术论文|"
    r"主要论著|代表性成果|研究成果|"
    r"承担(?:的)?(?:科研)?项目|科研项目|研究项目|"
    r"学术经历|教育经历|工作经历|学习和工作经历|获奖(?:情况|信息)|"
    r"教学情况|学生培养|招生(?:信息|招聘)|联系方式|基本信息|"
    r"个人简介|专利|Selected\s+Publications|Publications)\s*[：:]?")
# ② 自述式简介起点。实测三种写法：
#    「刘北明，教授，博士生导师」  姓名+逗号+职称
#    「丁勇 中国科学技术大学生命科学院教授、博士生导师」 姓名+单位+职称
#    「翁建平 安徽医科大学校长，中国科学技术大学讲席教授」 姓名+职务
_BIO_HEAD = re.compile(
    r"[\u4e00-\u9fff]{2,4}\s*[，,]\s*"
    r"(?:特任|讲席|兼职|客座)?(?:教授|研究员|副教授|副研究员|讲师|院士|"
    r"博导|博士生导师|校长|院长|主任|所长)"
    r"|[\u4e00-\u9fff]{2,4}\s*[，,]?\s*"
    r"(?:中国科学技术大学|清华大学|北京大学|复旦大学|浙江大学|"
    r"上海交通大学|南京大学|安徽医科大学)[^\n]{0,24}?"
    r"(?:教授|研究员|副教授|讲师|院士|校长|院长)"
    r"|[\u4e00-\u9fff]{2,4}\s+[^\n]{0,20}?"
    r"(?:大学|学院|研究院|研究所)[^\n]{0,16}?(?:教授|研究员|副教授)")
# ③ 履历词（毕业 / 学位 / 博士后 / 受聘 / 任职 …）
_BIO_CUE = re.compile(
    r"(?:毕业于|获(?:得)?[^。；;]{0,12}(?:学[士位]|博士|硕士|学位)|"
    r"博士后|受聘于|历任|先后(?:担任|在)|进入科大工作|"
    r"发表于|发表论文\s*\d+\s*余?篇|总引用|学术期刊发表)")
# ④ **孤立的职称串**：句末标点之后紧跟「特任教授 博导」「教授 博士生导师」
#    这类没有姓名的头衔行。实测王雪娟页：「…的分子机制。特任教授 博导 合肥微尺度…」
#    方向段在此已结束，但 ③ 的「毕业于」在更后面，会导致职称被并进方向条目、
#    进而被 is_direction 整条否掉 → 该页方向变空（假「官网无该栏位」）。
_TITLE_RUN = re.compile(
    r"(?:[。.；;]|(?<=[\u4e00-\u9fff)）])\s*)"
    r"(?=(?:特任|讲席|特聘|兼职|客座)?"
    r"(?:教授|研究员|副教授|副研究员|讲师|院士|博导|博士生导师)"
    r"(?:[\s、，,]+(?:特任|讲席|特聘|兼职|客座)?"
    r"(?:教授|研究员|副教授|副研究员|讲师|院士|博导|博士生导师|"
    r"国家\S{0,8}?研究员|研究员))*[\s、，,]*"
    r"(?:\S{0,30}?(?:研究员|博导|教授)|$))")


def _slice_balanced_div(page: str, start: int) -> str:
    """按标签配平取 div 内容（懒正则会提前收尾）。"""
    depth, pos = 1, start
    for m in re.finditer(r"<(/?)div\b[^>]*>", page[start:], re.I):
        if m.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return page[start: start + m.start()]
    return page[start:]


# 「官网确实没有研究方向」的原因分类
#   为什么要分类：使用者最常问的是「换个方法还能不能抓到」。
#     把「网站没填」与「解析不到」区分开，才能回答这个问题。
_NO_HOMEPAGE = ("你访问的主页不存在", "没有上线发布", "遇到错误啦")
_EMPTY_MARKERS = ("暂无内容", "暂无信息", "尚未填写", "No content")

_NO_DIR_REASON_CN = {
    "no_direction_field": "页面没有「研究方向」栏位（网站未填写）",
    "empty_panel": "有「研究方向」页签但内容为「暂无内容」",
    "empty_slot": "模板仅有空的「研究方向」占位注释，无内容",
    "no_homepage": "教师主页未发布（官网返回「主页不存在或没有上线发布」）",
    "list_only_name": "列表页只给了姓名，未进详情页补充",
    "detail_no_lead": "详情页没有研究方向线索词",
    "detail_empty": "详情页有线索词但未解析出条目",
    "fetch_failed": "页面抓取失败",
}

# 「尚未抓取」的标记 —— 与「抓了但网站没填」必须区分：
#   · 前者是**可续跑**的（下次全量会补）
#   · 后者是**数据源固有缺失**（再抓也没有）
# 混为一谈会导致 --limit 试跑一次就永久跳过其余人。
_NEVER_FETCHED_WHY = "列表页无方向且未补详情"


def classify_no_direction(page: str) -> str:
    """判定「没有研究方向」的**具体原因**。

    实测价值（USTC 生医部 237 人）：130 人「无方向」中，
      128 人是网站上**真的没填**（页签是 `<p>暂无内容</p>` 或只有空占位注释），
      2 人主页根本没发布。这是**数据源的固有缺失**，不是解析能力问题；
      如实分类可避免使用者误以为"再爬一次就能拿到"。
    """
    text = strip_tags(page)
    if any(mk in text for mk in _NO_HOMEPAGE):
        return "no_homepage"

    # 必须先去注释：实测有页面全页只有一句 `<!--研究方向-->` 占位注释，
    #   不过滤注释会被误判成"页面有研究方向字样"。
    visible = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    if "研究方向" not in visible:
        return "empty_slot" if "研究方向" in page else "no_direction_field"

    panels = re.findall(r'class="TabbedPanelsContent"[^>]*>(.*?)</div>', visible, re.S)
    if panels:
        texts = [squash(strip_tags(p)) for p in panels]
        if not [x for x in texts if x and "暂无内容" not in x and len(x) > 8]:
            return "empty_panel"
    if any(mk in text for mk in _EMPTY_MARKERS):
        return "empty_panel"
    return "detail_no_lead"


def _trim_interest_segment(seg: str) -> str:
    """把「研究兴趣」正文截到**本栏位真正结束处**。

    为什么需要：不少页面把研究兴趣写成**无编号**的一段
    （实测 USTC「主要研究兴趣 : A；B。早期工作… 刘北明，教授，博士生导师。
      1989 年毕业于… 代表性论著：Lau, P.-M., Zucker, R. S. & …」），
    此时抽取走 split_directions 兜底分支。若直接喂 tail[:400]，
    这段窗口会**越过研究兴趣、吃进个人简介与论文列表**，于是把
    「教授」「博士生导师」甚至 `Lau`/`Zucker`/`Springer` 当成研究方向
    （实测 刘北明 15 条里 7 条是这类噪声，全学部 19/126 人受影响）。

    截断依据按优先级：
      ① 显式的终止小标题（代表性论著 / 承担项目 / 联系方式 …）
      ② 自述式简介起点：「<姓名>，教授/研究员/特聘教授…」这类
         姓名紧跟职称的行，通常就是研究兴趣段的自然结尾
      ③ 兜底：首个句末出现「毕业于 / 获…学位 / 博士后」等履历词的位置
    """
    if not seg:
        return ""
    cut = len(seg)

    # ① 显式小标题
    m = _SECTION_STOP.search(seg) or _STOP_HEAD_ANY.search(seg)
    if m:
        cut = min(cut, m.start())

    # ② 姓名 + 职称（自述简介的起点）
    m = _BIO_HEAD.search(seg)
    if m:
        cut = min(cut, m.start())

    # ③ 履历词
    m = _BIO_CUE.search(seg)
    if m:
        cut = min(cut, m.start())

    # ④ 孤立的职称串（句末标点 + 头衔行）
    m = _TITLE_RUN.search(seg)
    if m:
        cut = min(cut, m.end())

    return seg[:cut].strip(" \t\u00a0；;，,。.、")


def extract_directions_from_detail(page: str, cfg: dict) -> list[str]:
    """从详情页正文抽研究方向（区间扫描：允许线索词与编号条目之间隔介绍句）。"""
    body_html = ""
    for cls in ("newsinfo", "article", "Content", "content"):
        m = re.search(rf'<div[^>]*class="[^"]*{cls}[^"]*"[^>]*>', page, re.I)
        if m:
            seg = _slice_balanced_div(page, m.end())
            if len(seg) > len(body_html):
                body_html = seg
    if not body_html:
        body_html = page
    body_html = re.sub(r"<script[^>]*>.*?</script>", " ", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<style[^>]*>.*?</style>", " ", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<!--.*?-->", " ", body_html, flags=re.S)
    body_html = re.sub(r"</(?:p|div|li|tr|h\d)>|<br\s*/?>", "\n", body_html, flags=re.I)
    body = squash(strip_tags(body_html)) if False else strip_tags(body_html)
    body = re.sub(r"[ \t\u00a0]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)

    m = _LEAD.search(body)
    if not m:
        return []
    tail = body[m.end():].lstrip("  \t：:\n")
    # 先截到本栏位真正结束处，再做行扫描 / 兜底切分。
    # 实测：无编号的行内式页面（「主要研究兴趣 : A；B。早期工作… 姓名，教授…」）
    # 若不截断，兜底分支会把个人简介与论文列表一并切进方向条目。
    tail = _trim_interest_segment(tail)

    picked, blanks, scanned, intro = [], 0, 0, ""
    for ln in tail.split("\n"):
        s = ln.strip()
        scanned += len(s)
        if scanned > 3000:
            break
        if not s:
            blanks += 1
            if picked and blanks >= 6:
                break
            continue
        blanks = 0
        if _SECTION_STOP.match(s):
            break
        if _NUM_ITEM.match(s):
            picked.append(s)
            continue
        if picked:
            continue                     # 条目后的说明段：不中断扫描
        if len(s) >= 20:
            intro = (intro + " " + s).strip() if intro else s

    out: list[str] = []
    if picked:
        for it in picked:
            t = re.sub(r"^\s*[（(]?\s*[1-9]\d?\s*[)）、.．]\s*", "", it)
            t = squash(t)
            t = re.split(r"[。.]", t)[0]
            t = re.sub(r"[（(][^（()）]*(?:基金|项目|子课题|973|863|"
                       r"重点研发|面上项目|中科院|资助)[^（()）]*[)）]", "", t)
            t = re.split(r"(?:论文(?:发表|列表)|发表论文|SCI\s*论文|代表性论文|"
                         r"已发表|主持(?:国家|项目)|相关研究工作|研究成果)", t)[0]
            t = t.strip("  ·-—，,。.；;：:、")
            if is_direction(t) and t not in out:
                out.append(t)
            if len(out) >= 12:
                break
        # 行扫描只按 \n 切：编号条目**挤在同一行**时（实测 申勇
        #   「研究兴趣及方向：1) … 2) … 3) …」），整行会变成**一条**超长条目。
        #   此时按行内编号就地重切。
        if len(out) == 1 and _has_inline_numbering(tail):
            inline = _split_inline_numbered(tail)
            if len(inline) > len(out):
                out = inline
    else:
        # 兜底 A（**优先**）：编号条目挤在同一行
        #   （实测 申勇「研究兴趣及方向：1) … 2) … 3) …」、
        #     郭傲「…聚焦在：1）…；2）…；3）…；4）…」）。
        #   这批页面里方向正文本身含「、」「；」，先走 split_directions
        #   会把一条方向按顿号切碎（郭傲 4 条被切成 11 条碎片）。
        #   编号是比顿号更强的结构信号 —— 有编号时**以编号为准**。
        if _has_inline_numbering(tail):
            out = _split_inline_numbered(tail)
        else:
            out = split_directions(intro or tail[:400], cfg)
        # 兜底 B：有些页面把多条方向**直接连排、中间没有任何分隔符**
        #   （实测 王雪娟 / 江维「模式识别受体活化…研究模式识别受体调节…研究」），
        #   split_directions 切不出来 → 空。**保留整段原文**比丢掉更好：
        #   报告里方向列写「官网原文」，用户能自己读懂；丢空则会被误报成
        #   「官网无该栏位」，那是假信息。is_direction 已排除简介/论文/职务。
        if not out and tail:
            cand = squash(tail[:300])
            # 先剥掉**粘连的头衔尾巴**：实测江维页方向与头衔之间无任何分隔符
            #   「…发育分化研究教授 博士生导师 2007年毕业于…」，
            #   而 _TITLE_RUN 要求标点/汉字边界，这个「研究教授」接得上去却切不开。
            #   这里直接找「教授|研究员|博导…」首次出现处截断，再校验。
            m = re.search(
                r"(?:特任|讲席|特聘|兼职|客座)?"
                r"(?:教授|研究员|副教授|副研究员|讲师|院士|博导|博士生导师)",
                cand)
            if m and m.start() >= 8:
                cand = cand[:m.start()]
            cand = cand.strip("  ·-—，,。.；;：:、")
            if is_direction(cand):
                out = [cand]
    return out


def _has_inline_numbering(t: str) -> bool:
    """一行里是否含 ≥2 个行内编号（`1)` `2)` `（3）`）。"""
    return len(re.findall(r"[（(]?\s*[1-9]\d?\s*[)）]\s*\S", t)) >= 2


def _split_inline_numbered(seg: str) -> list[str]:
    """把「1) … 2) … 3) …」按编号切成条目。

    只在**第一个连续编号区**内切：实测申勇页的方向是 1)2)3)，
    但紧随其后的论文列表里也有大量「2010)」「101)」这类年份/卷号，
    若不设界会一起切进来（一次能切出 20+ 条假条目）。
    界 = 最后一个「N) 且 N ≤ 前一个编号 + 1」的位置。
    """
    if not seg:
        return []
    seg = squash(seg)
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"[（(]?\s*([1-9]\d?)\s*[)）]\s*(?=\S)", seg)]
    if len(marks) < 2:
        return []
    # 取从 1 开始、编号连续递增的最长前缀
    seq = []
    expect = 1
    for st, en, n in marks:
        if n == expect:
            seq.append((st, en))
            expect += 1
        elif seq:
            break
    if not seq:
        return []
    end = seg.find("\n", seq[-1][0])
    end = len(seg) if end < 0 else end
    out: list[str] = []
    for i, (st, en) in enumerate(seq):
        stop = seq[i + 1][0] if i + 1 < len(seq) else end
        t = squash(seg[en:stop]).strip("  ·-—，,。.；;：:、")
        # 编号条目末尾常粘上「教授 博士生导师」等头衔，去掉
        t = _TITLE_RUN.sub("", t).strip("  ·-—，,。.；;：:、")
        if is_direction(t):
            out.append(t)
    return out


# ══════════════════════════════════════════════════════════════
# 归类与报告
# ══════════════════════════════════════════════════════════════
DEFAULT_RULES = {
    "结构与分子生物学": ["结构", "晶体", "冷冻电镜", "核磁", "蛋白", "分子生物"],
    "细胞与信号转导": ["细胞", "信号", "转导", "自噬", "凋亡", "细胞周期"],
    "遗传与发育": ["遗传", "发育", "基因", "基因组", "表观", "干细胞", "再生"],
    "神经与脑科学": ["神经", "脑", "认知", "突触", "行为", "阿尔茨海默", "退行"],
    "免疫与感染": ["免疫", "炎症", "感染", "病毒", "细菌", "微生物", "宿主"],
    "肿瘤与血液": ["肿瘤", "癌", "白血病", "血液", "转移", "化疗"],
    "植物与农业": ["植物", "作物", "水稻", "光合", "农业", "逆境", "营养"],
    "生态与进化": ["生态", "进化", "保护", "多样性", "种群", "环境"],
    "生物信息与计算": ["生物信息", "计算", "算法", "深度学习", "人工智能",
                       "机器学习", "组学", "数据"],
    "成像与生物物理": ["成像", "显微", "生物物理", "光学", "单分子", "膜片钳"],
    "药理与转化医学": ["药物", "药理", "靶点", "治疗", "转化", "临床", "诊断"],
    "合成与生物工程": ["合成生物", "代谢工程", "发酵", "酶工程", "生物工程",
                       "纳米", "材料", "器件"],
    "公共卫生与健康": ["公共卫生", "流行病", "健康", "营养学", "慢病"],
}


def load_rules() -> dict:
    p = _HERE.parent / "templates" / "导师方向规则.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            r = d.get("rules") or d.get("方向规则") or d
            if isinstance(r, dict) and r:
                return {k: v for k, v in r.items() if isinstance(v, list)}
        except Exception:
            pass
    return DEFAULT_RULES


def classify(people: list[dict], rules: dict) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {k: [] for k in rules}
    groups["未分类"] = []
    for p in people:
        text = "；".join(p.get("directions") or [])
        hit = False
        for g, kws in rules.items():
            if any(kw in text for kw in kws):
                groups[g].append(p)
                hit = True
        if not hit:
            groups["未分类"].append(p)
    return {g: ms for g, ms in groups.items() if ms}


def reparse_only(d: pathlib.Path, log, cfg: dict) -> None:
    """**不联网**：用 raw_list/ 与 raw_detail/ 下已存的 HTML 重新解析全部字段。

    用途：改抽取规则后先离线看效果，确认无误再决定要不要重抓。
      联网重抓上百页要一两分钟；离线重解析不到 1 秒，且结果完全可复现。
    """
    lf = d / "faculty_list.json"
    if not lf.exists():
        raise SystemExit(f"[X] 缺少 {lf}；请先跑一次 --stage=list")
    people = json.loads(lf.read_text(encoding="utf-8"))
    before = sum(1 for p in people if p.get("directions"))

    raw_list = d / "raw_list"
    raw_detail = d / "raw_detail"

    # ① 列表页重新解析：按栏目归并回名单
    unit_sections = {}
    for p in people:
        unit_sections.setdefault(p.get("section", ""), 0)
        unit_sections[p["section"]] += 1

    # ② 详情页重新解析：只补「列表页无方向」的人
    hit = miss = 0
    for p in people:
        if p.get("directions"):
            continue
        slug = f"{safe_filename(p['name'])}_{safe_filename(p.get('section',''))}"
        f = raw_detail / f"{slug}.html"
        if not f.exists():
            continue
        page = f.read_text(encoding="utf-8", errors="replace")
        dirs = extract_directions_from_detail(page, cfg)
        if dirs:
            p["directions"] = dirs
            p["direction_source"] = "详情页正文"
            p["status"] = "ok"
            p.pop("why", None)
            p.pop("no_direction_reason", None)
            hit += 1
        else:
            reason = classify_no_direction(page)
            p["status"] = "no_direction"
            p["no_direction_reason"] = reason
            p["why"] = _NO_DIR_REASON_CN.get(reason, "无法判定")
            miss += 1

    lf.write_text(json.dumps(people, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "faculty_detail.json").write_text(
        json.dumps(people, ensure_ascii=False, indent=2), encoding="utf-8")
    after = sum(1 for p in people if p.get("directions"))

    def cov(k):
        return sum(1 for x in people if x.get(k))

    print()
    print("═" * 62)
    print(f"[OK] 离线重解析完成（未联网，{len(people)} 人）")
    print(f"  有研究方向：{before} → **{after}**"
          + (f"（+{after-before}）" if after != before else "（无变化）"))
    print(f"  详情页补到 {hit} 人 / 仍未解析出 {miss} 人")
    print()
    print("  字段覆盖：")
    for k, label in (("title", "职称"), ("directions", "研究方向"),
                     ("email", "邮箱"), ("url", "主页链接"),
                     ("sections", "所属栏目")):
        print(f"    {label:10} {cov(k):3}/{len(people)}")
    print("═" * 62)


def _rename_archives(d: pathlib.Path, log) -> None:
    """把 raw_detail/ 下的存档按 `<姓名>_<栏目>.html` 规范命名。"""
    raw = d / "raw_detail"
    if not raw.exists():
        raise SystemExit(f"[X] 缺少 {raw}")
    n = 0
    for f in sorted(raw.glob("*.html")):
        # 去掉可能的序号/哈希前缀，统一成 姓名_栏目.html
        stem = f.stem
        want = re.sub(r"^[\d_\-]+", "", stem)
        if want and want != stem:
            target = raw / f"{want}.html"
            if not target.exists():
                f.rename(target)
                n += 1
    log.info(f"[OK] 存档改名完成：{n} 个")


def build_report(d: pathlib.Path, uni: dict, unit: dict, people: list[dict],
                 log, topic_label: str = "") -> dict:
    rules = load_rules()
    groups = classify(people, rules)
    withdir = [p for p in people if p.get("directions")]

    L: list[str] = []
    A = L.append
    A(f"# {uni['name']} · {unit['name']} 导师名单与方向归类")
    A("")
    A(f"> 数据源：**学部官网** [{unit['base']}]({unit['base']})"
      f"（栏目 {len(unit['sections'])} 个）")
    A("> ")
    A(f"> 抓取时间：{time.strftime('%Y-%m-%d %H:%M')} | "
      f"共 **{len(people)}** 人，其中 **{len(withdir)}** 人有研究方向")
    A("")
    A("> [WARN] **方向归类是关键词粗分类**，不是权威学科划分；一位老师可能出现在"
      "多个方向下。**请以学院官网原文为准**（下表保留原文与链接）。")
    A("")
    A("---")
    A("")

    # 汇总表
    A("## 方向分布")
    A("")
    A("| 方向 | 人数 | 方向 | 人数 |")
    A("|---|---|---|---|")
    items = [(g, len(ms)) for g, ms in groups.items() if g != "未分类"]
    half = (len(items) + 1) // 2
    for i in range(half):
        a = items[i]
        b = items[i + half] if i + half < len(items) else ("", "")
        bs = f"{b[0]} | {b[1]}" if b[0] else " | "
        A(f"| {a[0]} | {a[1]} | {bs} |")
    A(f"| **未分类** | **{len(groups.get('未分类', []))}** | "
      f"（含官网未提供方向者） | |")
    A("")

    # 按方向列人
    for g, ms in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        A(f"## {g}（{len(ms)} 人）")
        A("")
        A("| 姓名 | 职称 | 研究方向（官网原文） | 邮箱 | 主页 |")
        A("|---|---|---|---|---|")
        for p in sorted(ms, key=lambda x: x["name"]):
            ds = "；".join(p.get("directions") or []) or \
                 "—（" + (p.get("why") or "官网未提供") + "）"
            ds = ds.replace("|", "／").replace("\n", " ")
            if len(ds) > 300:
                ds = ds[:297] + "…"
            mail = p.get("email") or "—"
            url = p.get("url") or ""
            link = f"[链接]({url})" if url else "—"
            A(f"| {p['name']} | {p.get('title') or '—'} | {ds} | {mail} | {link} |")
        A("")

    # 原因分布
    from collections import Counter
    nd = [p for p in people if not p.get("directions")]
    if nd:
        A("---")
        A("")
        A("## 附：官网未提供研究方向的人数与原因")
        A("")
        A("> [WARN] 下列人员的学院官网页面**没有研究方向数据**，脚本如实标注，")
        A("> **未用印象补全**。如需了解请点其主页或查阅论文成果。")
        A("")
        A("| 原因 | 人数 |")
        A("|---|---|")
        for r, n in Counter(p.get("why") or "未标注" for p in nd).most_common():
            A(f"| {r} | {n} |")
        A("")

    out = d / "导师名单与方向归类.md"
    out.write_text("\n".join(L), encoding="utf-8")

    payload = {
        "school": uni["name"], "unit": unit["name"], "base": unit["base"],
        "total": len(people), "with_directions": len(withdir),
        "groups": groups,
        "sections": [s["name"] for s in unit["sections"]],
        "mentors": people,
    }
    (d / "faculty_grouped.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"[OK] 归类完成：{len(groups)} 个方向桶；报告 → {out}")
    return payload


def print_selection_prompt(payload: dict) -> None:
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
        print(f"  · {g}（{len(members)} 人）：{'、'.join(names)}{more}")
        n += 1
        if n >= 20:
            print("  （其余方向见报告）")
            break
    print()
    print("  建议话术：")
    print("    「以上是按学院官网研究方向做的粗归类。你想选哪个方向？"
          "也可以直接点名某位老师。」")
    print("  用户选定后 → 进入阶段 R（平行检索该方向的优质综述）。")
    print()


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
def _list_units(cfg: dict) -> None:
    print("可用的学部/学院官网数据源：")
    print()
    for sk, sv in cfg["universities"].items():
        print(f"  {sk}  —— {sv['name']}（别名：{'、'.join(sv.get('alias', []))}）")
        for uk, uv in sv.get("units", {}).items():
            star = sum(1 for s in uv["sections"] if s.get("detail") == "star")
            fetch = sum(1 for s in uv["sections"] if s.get("detail") == "fetch")
            print(f"      {sk}:{uk}   {uv['name']}")
            print(f"          站点：{uv['base']}")
            print(f"          栏目：{len(uv['sections'])} 个"
                  f"（列表页含方向 {star} / 需进详情页 {fetch}）")
            for s in uv["sections"]:
                tag = "" if s.get("detail") == "star" else "→"
                note = f" {s['note']}" if s.get("note") else ""
                print(f"            {tag} {s['name']}  {s['path']}{note}")
        print()
    print("用法：--unit <学校key> 或 --unit <学校key>:<学院key>")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="harvest_faculty.py",
        description="阶段 S：从**学部/学院官网**抓导师与研究方向（配置驱动，唯一数据源）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  harvest_faculty.py --list-units\n"
                "  harvest_faculty.py --unit zju --out outputs/zju\n"
                "  harvest_faculty.py --unit zju --stage=list\n"
                "  harvest_faculty.py --unit ustc:life-med --out outputs/ustc\n"))
    ap.add_argument("--unit", help="学校 key，或 `学校:学院`（见 --list-units）")
    ap.add_argument("--unit-name", help="只抓某个师资栏目（名称模糊匹配）")
    ap.add_argument("--stage", choices=["list", "detail", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="详情最多抓几人（调试）")
    ap.add_argument("--out", default=str(WD / "outputs" / "01_导师名单"),
                    help="输出目录（默认 <流程1>/<年月日>/outputs/01_导师名单）")
    ap.add_argument("--config", help="自定义配置文件路径")
    ap.add_argument("--list-units", action="store_true", help="列出全部数据源后退出")
    ap.add_argument("--reparse-only", action="store_true",
                    help="**不联网**：用已存 HTML 重新解析（改抽取规则后验证用，秒级）")
    ap.add_argument("--rename-archives", action="store_true",
                    help="规范 raw_detail/ 下的存档命名")
    args = ap.parse_args(argv)

    log = get("faculty")
    cfg = load_config(pathlib.Path(args.config) if args.config else None)

    if args.list_units or not args.unit:
        _list_units(cfg)
        return 0

    sk, uk, unit = resolve_unit(cfg, args.unit)
    uni = cfg["universities"][sk]
    print(f"数据源：{uni['name']} · {unit['name']}")
    print(f"站点：  {unit['base']}")
    print(f"栏目：  {len(unit['sections'])} 个"
          + (f"（仅 {args.unit_name}）" if args.unit_name else ""))
    print()

    d = pathlib.Path(args.out)
    if not d.is_absolute():
        d = WD / d

    if args.rename_archives:
        _rename_archives(d, log)
        return 0
    if args.reparse_only:
        reparse_only(d, log, cfg)
        return 0

    d.mkdir(parents=True, exist_ok=True)
    write_status(WD, status="RUNNING", stage="S",
                 reasons=["导师名单与详情正在抓取"])

    if args.stage in ("list", "all"):
        stage_list(unit, d, log, cfg, only_section=args.unit_name or "")
    if args.stage in ("detail", "all"):
        stage_detail(unit, d, log, cfg, limit=args.limit)

    if args.stage == "list":
        people = json.loads((d / "faculty_list.json").read_text(encoding="utf-8"))
        write_status(WD, status=calculate_status(has_output=bool(people),
                                                  expected=len(people),
                                                  completed=len(people),
                                                  blocked=len(people)),
                     stage="S", expected=len(people), completed=len(people),
                     blocked=len(people),
                     reasons=["仅完成导师列表抓取；详情阶段尚未执行"])
    elif args.stage == "detail":
        people = json.loads((d / "faculty_detail.json").read_text(encoding="utf-8"))
        failed = sum(1 for rec in people if rec.get("status") == "fetch_failed")
        write_status(WD, status=calculate_status(has_output=bool(people),
                                                  expected=len(people),
                                                  completed=len(people) - failed,
                                                  failed=failed),
                     stage="S", expected=len(people), completed=len(people) - failed,
                     failed=failed,
                     reasons=([f"{failed} 位导师详情抓取失败"] if failed else []))

    if args.stage == "all":
        df = d / "faculty_detail.json"
        lf = d / "faculty_list.json"
        if df.exists():
            people = json.loads(df.read_text(encoding="utf-8"))
        elif lf.exists():
            people = json.loads(lf.read_text(encoding="utf-8"))
        else:
            raise SystemExit("[X] 没有抓到数据")
        payload = build_report(d, uni, unit, people, log)
        print()
        print(f"[OK] 共 {payload['total']} 人，"
              f"{payload['with_directions']} 人有研究方向")
        print(f"→ 报告：{d / '导师名单与方向归类.md'}")
        print(f"→ 机读：{d / 'faculty_grouped.json'}")
        print_selection_prompt(payload)
        journal("S", "faculty.harvest", unit=f"{sk}:{uk}",
                total=payload["total"], with_dir=payload["with_directions"])
        failed = sum(1 for rec in people if rec.get("status") == "fetch_failed")
        state = calculate_status(has_output=payload["total"] > 0,
                                 expected=payload["total"],
                                 completed=payload["total"] - failed,
                                 failed=failed)
        write_status(
            WD, status=state, stage="S", expected=payload["total"],
            completed=payload["total"] - failed, failed=failed,
            reasons=([f"{failed} 位导师详情抓取失败"] if failed else []),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
