# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 K：知识图谱（overview / 词云 / 方向聚类）
#
# 位置：**所有搜集工作完成之后、PPT 制作之前**（H2 之后、P 之前）。
#       理由：三张图都要吃"最终"的产物 —— 解读卡、综述终表、review 结果；
#       放在中间画出来的图，等后面补了数据就全过期了。
#
# 三张图（用户指定）：
#   ① overview  —— 以作者为中心；不同阶段的研究方向；方向下挂**最紧密的综述**
#                   （取自 refs/final_list.json 的最终 review 列表）。
#                   · Louvain 社区检测 → 按主题聚类上色
#                   · **记忆连接**：两条方法关系紧密（该领域常用方法 / 新进展）
#                     就在图上连一条**有名字、带一句原因**的边
#                   · 质检：每个聚类回答「这一簇在讲什么？」；
#                     并检出「这里哪些地方在自相矛盾？」
#   ② 词云      —— 以作者为中心；颜色区分研究方向；**剔除 "mice" 这类有误导性的
#                   常见词**，着力于**研究方法与实验范式**
#   ③ 方向聚类  —— 每个方向**近 5 年**的最新论文与热点
#
# 格式：**SVG 优先**，SVG 失败自动退 PNG；高分辨率（标准 = 清晰展示所有字符）；
#       **背景透明**。
#
# 只读本地文件，不发起任何网络请求。
#
# 依赖（按需加载，不污染项目环境）：
#   uv run --with matplotlib --with networkx python 流程1/scripts/knowledge_graph.py
#
# 用法：
#   uv run --with matplotlib --with networkx python 流程1/scripts/knowledge_graph.py
#     --recent-years=3      方向聚类的"近 N 年"（默认 5）
#     --max-reviews=3       每个方向下最多挂几篇综述（默认 3）
#     --dpi=300             PNG 回退时的分辨率（默认 300）
#     --format=svg|png|auto 默认 auto：先试 SVG，失败退 PNG
#     --formats=svg         **只出 SVG**（批量出图时省一半时间；SVG 失败即报错）
#     --wc-lang=both|zh|en  词云语言口径（默认 both；只要中文用 zh）
#     --wc-min-docs=3       **只来自方法描述**的词至少要出现在几篇论文里
#     --wc-weight-keywords=3 / -title=2 / -abstract=2 / -methods=1
#                           **文章关键词 > 题名 > 摘要 > 卡片方法描述** 的权重
#     --self-test           纯逻辑自检（不联网、不落盘）
#
# 可移植配置：PAPER_WORKDIR / PAPER_AUTHOR / PAPER_ORCID / PAPER_AFFIL
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import zlib

from collections import Counter, defaultdict

from logutil import run_root as _run_root  # noqa: E402

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

try:
    from logutil import get, journal  # type: ignore
except Exception:
    class _L:
        def info(self, *a, **k):
            print(*a)

        def warn(self, *a, **k):
            print(*a, file=sys.stderr)

    def get(_n):            # noqa: D103
        return _L()

    def journal(*a, **k):   # noqa: D103
        pass

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WD / ".work_cdp"
REFDIR = CDPDIR / "refs"
GRAFDIR = CDPDIR / "graph"
TPL = _HERE.parent / "templates"

AUTHOR = (os.environ.get("PAPER_AUTHOR", "").strip() or WD.name)
ORCID = os.environ.get("PAPER_ORCID", "").strip()
AFFIL = os.environ.get("PAPER_AFFIL", "").strip()


# ══════════════════════════════════════════════════════════════
# 绘图运行时依赖（v16.3 起为**硬依赖**）
#
# 为什么用 igraph 而不是 Graphviz：
#   本版要的是「**径向整齐树**」（layout_reingold_tilford_circular）。
#   Graphviz 的 twopi/circo 也能做，但它需要系统装 `dot` 二进制 ——
#   实测本机**未安装**，而「换机器只传参就能跑」是本流程的硬要求。
#   igraph 把 22 个布局算法装进纯 wheel（Python 3.14 实测可装，无需编译器），
#   没有系统级前置依赖。
#
# 为什么另加 adjustText：
#   布局只负责**节点位置**，标签避让必须是**独立的一步**。
#   旧版把两件事混在一起 —— 靠推节点来躲标签，结果把径向层级整个推没了
#   （实测综述被推成左侧一团、方法漂到外圈）。
#
# 缺依赖时**直接失败**（用户选定策略 B）：不产出降级图、不留半成品，
#   并给出可直接复制的完整命令。
# ══════════════════════════════════════════════════════════════
GRAPH_DEPS = ("python-igraph", "adjustText", "wordcloud")
PY_DEPS = ("matplotlib", "networkx") + GRAPH_DEPS


def graph_deps_cmd(script: str = "知识图谱") -> str:
    """可直接复制的完整运行命令（缺依赖时打印用）。"""
    pkgs = " ".join(f"--with {m}" for m in PY_DEPS)
    return f"uv run {pkgs} python 流程1/scripts/knowledge_graph.py"


def missing_graph_deps() -> list:
    """→ 缺失的绘图依赖名（空列表 = 齐全）。

    `wordcloud` 只在 `--wc-engine=library`（默认）下需要；
      走 `custom` 时缺它不该拦住出图 —— 由调用方按引擎决定是否检查。
    """
    miss = []
    for mod, pkg in (("igraph", "python-igraph"), ("adjustText", "adjustText"),
                     ("wordcloud", "wordcloud")):
        try:
            __import__(mod)
        except Exception:
            miss.append(pkg)
    return miss


# ══════════════════════════════════════════════════════════════
# 词云剔除表 —— 「有误导性的常见词」
#
# 用户点名要剔除 "mice"。为什么这类词必须剔：
#   它们是**模型系统/载体**，不是**方法**。频次还往往最高（一篇小鼠实验
#   通篇都在说 mice），留在图上会把真正的信号（成像、组学、建模）
#   全挤到角落 —— 图看起来"信息量大"，实际读不出作者的**方法学特征**。
#   本表分三类，都归到"不该进词云"：模型生物、泛化容器、载体/材料。
# ══════════════════════════════════════════════════════════════
MISLEADING = set("""
mice mouse murine rat rats zebrafish fly flies drosophila worm worms
celegans cynomolgus macaque monkey monkeys primate rabbit rabbits
patient patients human humans donor donors subject subjects volunteer volunteers
sample samples dataset datasets data set sets group groups cohort cohorts
case cases control controls treatment treatments trial trials
cell cells line lines strain strains clone clones
vitro vivo situ
study studies research paper papers article articles work works
result results finding findings report reports
method methods approach approaches technique techniques
analysis analyses assay assays test tests experiment experiments
protein proteins gene genes rna dna mrna molecule molecules compound compounds
level levels type types kind kinds role roles effect effects
high low large small significant important
mol mmol nm ml mg kj cm3 first second third respectively
obtained observed found shown studied reported total measured
""".split())

# 中文常见但无方法学信息的词（与分词器的中文 2-gram 对齐）
MISLEADING_ZH = set("""
研究 分析 方法 结果 实验 数据 样本 细胞 小鼠 大鼠 患者 人群 水平 影响
作用 机制 模型 检测 表达 变化 显著 提高 降低 增加 减少 不同 相关
""".split())

# ── 词云专用剔除表（**不参与** tokenize，只作用于词云取词）────────────
# 背景：实测 25 张解读卡的词云上出现三类垃圾，逐词排查后在此归类。
#   与 MISLEADING 的区别：MISLEADING 是"载体/泛化"语义（任何文本源都该剔），
#   下面三张表是"**进词云就只占位置**"的形态类垃圾（单位、受控标签、虚词）。

# ① 单位 / 量纲 / 缓冲液 / 电极液缩写 —— 数字后面的量纲不是方法。
#    实测来源：min(56)、div(21，"days in vitro")、kcl(17)、e18(12)、vpp(27)。
UNIT_ABBR = set("""
min mins sec secs hr hrs ms us ns hz khz mhz ghz
nm um mm cm km ml ul nl pl mg ug ng pg mol mmol umol nmol
pa kpa mpa gpa kv mv uv ma ua na deg rpm ph od wt vol eq
div dv vpp kcl nacl cacl cacl2 mgcl2 naoh hcl co2 h2o o2
hepes egta edta tris sds pbs tbs dmso
""".split())

# ② 受控范式词表自身的标签（templates/方法范式标签词表.json 的
#    paradigm_tags_vocabulary）**不得被当普通词切进云** ——
#    它们是 15 个固定标签，谁都能打上，描述不了这位作者；
#    实测 experimental(21)、mechanistic(18)、imaging(15) 全部来自这里。
#    范式分布改为单独统计，进图注与报告（见 wc_stats["paradigms"]）。
PARADIGM_EN = set("""
experimental computational data-driven machine-learning deep-learning
mechanistic statistical screening omics imaging clinical review
benchmark method-development hybrid
""".split())

# ③ 正文/页脚里的通用英文词（不是方法，是来源噪声）。
GENERIC_EN = set("""
fulltext student students supplementary appendix author authors university
institute department school laboratory lab corresponding equally contributed
accessed download downloaded copyright licence license creative commons
mean median average ratio total sum std var sem sd ci
""".split())

# ④ 中文虚词/泛化词（用户点名 定量，其余为同类）。
#    与 MISLEADING_ZH 分开：这张表只在**词云**生效，
#    方向画像（direction_profiles_from_cards）继续用 MISLEADING_ZH，避免改动聚类口径。
GENERIC_ZH = set("""
定量 参数 用于 作者 每个 定义 条件 作为 距离 间隔 固定 人工 单位 数值
时间 标准 分别 所有 各种 相同 一致 程度 过程 方式 形式 部分 情况
之间 同一 直接 出现 施加 区分 测试 初始 局部 持续 线性 系数 结构 像素
计数 强度 中心 生长 标记 样品 穿孔 结果 含量 大小 多少 以上 以下 平均
比较 得到 重复 正文 基于 校正 活动 诱导 投影 剔除 定位 单个 低密度 同步 随机
状态 评估 自制 制备 诱发 量化 剂量 鉴定 随后 分别 其次 最后 此外 综上 以下
""".split())

# ⑤c 不能作**词头**的泛化词：跨词边界的碎片常以它们开头
#     （实测「…比较用…」被切成 3-gram「比较用」并上云）。
#     刻意只收**不做构词前缀**的词：像「定量」是「绝对定量」的词尾、
#     「活动」可作「活动依赖」的词头，都不放进来，避免误杀真术语。
GENERIC_HEAD_ZH = set("""
比较 得到 重复 正文 基于 校正 单个 定位 投影 剔除 施加 出现 定义
""".split())

# 流程内部已用的停用词（与 interpret_build 保持一致，避免两处口径漂移）
STOP = set("""
a an the and or but if then than that this these those of in on at to for from by with without
as is are was were be been being it its their there here we our us they them he she his her
not no nor so such can could may might must shall should will would do does did done have has had
using used use uses show shown showed demonstrate demonstrated reveal revealed report reported
present presented propose proposed new novel increase increased decrease decreased
however moreover furthermore therefore thus also both each other more most less least
between among during after before under over into out up down about above below
one two three all any own per via see fig tab
""".split())


def _argval(name, default=None):
    for a in sys.argv[1:]:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return default


def load_json(p, default=None):
    p = pathlib.Path(p)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] {p.name} 解析失败：{e}", file=sys.stderr)
        return default


def as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    return [v]


def one_line(v, n: int = 0) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return (s[:n - 1] + "…") if (n and len(s) > n) else s


def tokenize(text: str) -> list[str]:
    """与 interpret_build.py 完全一致：英文单词（≥3 字符、含数字）+ 中文 2-gram。"""
    out = []
    t = (text or "").lower()
    for w in re.findall(r"[a-z][a-z0-9\-]{2,}", t):
        w = w.strip("-")
        if len(w) >= 3 and w not in STOP:
            out.append(w)
    for run in re.findall(r"[一-鿿]{2,}", t):
        for i in range(len(run) - 1):
            out.append(run[i:i + 2])
    return out


# ══════════════════════════════════════════════════════════════
# 词云取词（v16.2）
#
# 取词优先级（用户指定）：**文章自带 Keywords → 题名 → 摘要 → 解读卡方法描述**。
#   前三级是"作者/期刊自己认定"的词，最可信；方法描述只作兜底与补充。
#
# 中文为什么不能再用 2-gram 硬切：
#   tokenize() 把任意 CJK 串切成全部 2-gram，于是
#   「小鼠海马→鼠海」「突触前→触前」「谷氨酸→谷氨」「神经元→经元」
#   这些**跨词边界的碎片**直接上云，图上读不出意思。实测这 4 个片段
#   分别出现 11/17/12/57 次，都进了 top-60。
#
# 改用**左右邻接熵**（无监督新词发现的经典判据）：
#   一个真词的左右邻居应当"杂"（熵高），一个碎片的邻居必然"死"（熵低）。
#   实测：触前 左邻熵=0（17 次里 17 次前面都是「突」）、经元 左邻熵=0、
#         鼠海 右邻熵=0、谷氨 右邻熵=0.81 —— 全部判为碎片；
#         而 海马/突触/神经元/谷氨酸/突触前/荧光/成像/记录 熵都够，全部留下。
#   附带收益：候选词从"只有 2 字"扩展到 2–4 字，云上出现
#   「荧光断层」「突触前」「神经元」这类完整术语，可读性直接提升。
#
# 本函数**不改 tokenize()** —— 那个口径与 interpret_build.py
#   绑定（见其 docstring），动了会漂移方向画像与冲突检出。
# ══════════════════════════════════════════════════════════════
WC_MIN_COUNT = 3        # 中文候选词的最低出现次数
WC_MIN_ENTROPY = 0.9    # 左右邻接熵下限（bit）；两者取小
WC_MAX_N = 4            # 中文词最长 4 字
WC_EN_MIN_LEN = 3       # 英文词最短 3 字符
WC_FLOOR_PT = 5.0       # 最终物理尺寸下的字号下限（见 note_fonts）
# **只来自方法描述**的词，至少要在这几篇论文里出现过才算"这位作者的方法"。
#   为什么需要这条：方法描述是散文，词量比关键词/题名大两个数量级
#   （实测 3413 : 212）。邻接熵只能挡"不成词"的碎片，挡不住
#   「得到 / 重复 / 正文 / 比较用 / 校正」这类**成词但在单篇里偶然出现**的散词。
#   一个只在一篇论文里出现的方法词，不构成作者的方法学特征。
#   关键词/题名来源**不受此限**（它们是作者自己认定的词，一两篇也算数）。
WC_MIN_DOCS = 3

# 记忆连接判据（v16.3）
MEM_MIN_SHARED = 3      # 共现篇数下限
MEM_MIN_LIFT = 1.5      # 相对共现强度下限（1.0 = 与独立出现无异）
MEM_MAX_LINKS = 8       # 图上最多画几条（旧版 12 条已把画面占满）
MEM_MAX_DEGREE = 3      # 单节点出度上限，避免"一把橙色扇子"

# ⑤ 不能作为术语开头的虚词/连词/介词。
#    实测反例：「…结果与统计…」被切成 3-gram「与统计」并留在云上。
#    只限制**开头**：结尾的「前/后/中」在「突触前 / 突触后 / 中枢」里是真词。
FUNC_HEAD_ZH = set("与和及或在对从由把被为使将其该这那每并且而则即如若因故但因"
                   "的了着过们")
# ⑤b 不能作为术语**结尾**的虚词/助词。「…中的…」被切成「中的」并上云，
#    而结尾的「的」在任何真术语里都不会出现，可以安全地挡住。
FUNC_TAIL_ZH = set("的了着过们与和及或把被使将")

_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_EN_TOK = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _entropy(counter) -> float:
    """Shannon 熵（bit）。空集返回 0。"""
    import math
    tot = sum(counter.values())
    if tot <= 0:
        return 0.0
    return -sum((v / tot) * math.log2(v / tot) for v in counter.values())


def zh_terms(text: str):
    """中文候选词 → (保留 {词: 次数}, 统计)。

    统计里带每个候选词的左右邻接熵与邻居样例，供报告如实列出
    「为什么这个词被判为碎片」—— 不做静默丢弃。
    """
    cnt = Counter()
    left = defaultdict(Counter)
    right = defaultdict(Counter)
    for run in _CJK_RUN.findall(text or ""):
        for n in range(2, WC_MAX_N + 1):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                cnt[g] += 1
                left[g][run[i - 1] if i > 0 else "#"] += 1
                j = i + n
                right[g][run[j] if j < len(run) else "#"] += 1
    keep, info = {}, {}
    for g, c in cnt.items():
        hl, hr = _entropy(left[g]), _entropy(right[g])
        if c < WC_MIN_COUNT:
            why = "重复不足"
        elif g[0] in FUNC_HEAD_ZH or g[-1] in FUNC_TAIL_ZH:
            why = "虚词边界"
        elif len(g) > 2 and g[:2] in GENERIC_HEAD_ZH:
            why = "泛化词组合"
        elif min(hl, hr) < WC_MIN_ENTROPY:
            why = "跨词碎片"
        else:
            why = ""
        info[g] = {"n": c, "h_left": round(hl, 2), "h_right": round(hr, 2),
                   "left": left[g].most_common(3), "right": right[g].most_common(3),
                   "keep": not why, "why": why}
        if not why:
            keep[g] = c
    return keep, info


def zh_grams(text: str) -> set:
    """只取 2–4 字候选（不算熵）—— 配合**全局**门控使用。"""
    out = set()
    for run in _CJK_RUN.findall(text or ""):
        for n in range(2, WC_MAX_N + 1):
            for i in range(len(run) - n + 1):
                out.add(run[i:i + n])
    return out


def en_terms(text: str) -> list:
    """英文候选词：tokenize 的英文分支 + 词云专用剔除表。"""
    out = []
    for w in _EN_TOK.findall((text or "").lower()):
        w = w.strip("-")
        if len(w) < WC_EN_MIN_LEN or w in STOP:
            continue
        # 「单字母 + 数字」是实验编号（E18 = 胚胎 18 天），不是方法词。
        #   刻意只挡这一种：IL6 / CD4 / D2 这类是真术语，留给词表判断。
        if re.fullmatch(r"[a-z]\d{1,3}", w):
            continue
        if w in MISLEADING or w in UNIT_ABBR or w in GENERIC_EN:
            continue
        if w in PARADIGM_EN:
            continue
        out.append(w)
    return out


# ── 文章自带 Keywords 的解析 ───────────────────────────────────
# 只在**结果干净**时才采纳：一旦发觉把 Introduction 段落吞进来，
#   整份丢弃并记录原因。宁可少一个来源，也不让散文上云。
_KW_HEAD = re.compile(r"(?im)^[ \t]*(?:key\s?words?|关键词|关键字)\s*[:：]?[ \t]*")
_KW_ANY = re.compile(r"(?i)\bkey\s?words?\s*[:：]\s*")
_KW_STOP = re.compile(
    r"(?i)^(introduction|abstract|background|methods?|materials(\s+and\s+methods)?|"
    r"results?|discussion|conclusions?|references?|acknowledg\w*|received|accepted|"
    r"published|citation|supplementary|abbreviations|author contributions?)\b")
_KW_NEXT = re.compile(r"\s{2,}(?=[A-Z0-9一-鿿])")
# 有些出版商把关键词与 Introduction 用**一个空格**接在一起
#   （实测 "…ASAP Introduction Understanding neural circuit function…"）。
#   只认双空格会把整段 Introduction 吞进来 → 整份被门控丢弃 → 白丢关键词。
#   这里显式在下一个章节名处截断。
_KW_SECTION = re.compile(r"\b(?:Introduction|Abstract|Background|References|"
                         r"Materials and Methods|Results|Discussion)\b")
_KW_GLUED = re.compile(r"^[a-z]{18,}$")
# 参考文献里的作者名会被连在关键词后面（实测 "… homeostatic plasticity, Zito and Svoboda"），
#   遇到它就在此截断 —— 它不是关键词，但前面的关键词是好的。
#   **必须要求 `X and Y` 形式**：只写 `^[A-Z][a-z]+$` 会把
#   `Acetylcholine` 这类**首字母大写的正常关键词**也误判成作者名，
#   从而在第一条就把整份关键词截断（这个 bug 真出现过）。
_KW_AUTHORS = re.compile(r"^[A-Z][a-zA-Z\-']+\s+(?:and|&)\s+[A-Z][a-zA-Z\-']+$")


def parse_article_keywords(text: str):
    """→ (关键词列表, 说明)。说明用于报告，写清"采纳/截断/丢弃 + 为什么"。

    解析策略是**逐条走、遇到散文就停**，而不是"整段读完再判"：
      出版商的排版差异极大（关键词与 Introduction 之间有时是双空格、
      有时只有一个空格、有时直接接摘要正文），整段读完再判会把
      「前 3 条关键词 + 后面 600 字散文」也判成脏数据**整份丢掉** ——
      实测那样只有 2/25 篇能取到关键词。改成遇到第一条不像术语的
      条目就**在此截断**并保留前面的（要求 ≥2 条），
      覆盖率升到 7–8/25，且保留下来的全是干净术语。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    marks = list(_KW_HEAD.finditer(text)) or list(_KW_ANY.finditer(text))
    if not marks:
        return [], "正文里没有 Keywords/关键词 段"
    out, stops = [], []
    for m in marks:
        seg = text[m.end():m.end() + 600].replace("-\n", "").replace("\n", " ")
        cut = len(seg)
        for stop in _KW_NEXT.finditer(seg):
            if stop.start() > 12:
                cut = min(cut, stop.start())
                break
        sec = _KW_SECTION.search(seg)
        if sec and sec.start() > 12:
            cut = min(cut, sec.start())
        seg = seg[:cut]
        for p in re.split(r"[·•;、|/,]+", seg):
            p = re.sub(r"\s+", " ", p).strip(" .·•-–—\t")
            if not p:
                continue
            if _KW_STOP.match(p):
                stops.append(f"到章节名 {p[:18]!r} 止")
                break
            if (len(p) > 60 or p.count(" ") > 5 or ". " in p
                    or _KW_GLUED.match(p.lower()) or re.search(r"[\[\]()\d]", p)
                    or _KW_AUTHORS.match(p)):
                stops.append(f"在 {p[:24]!r} 处截断（不像术语）")
                break
            out.append(p)
    # 去重保序
    seen, ded = set(), []
    for p in out:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            ded.append(p)
    why_tail = ("；" + "；".join(stops[:2])) if stops else ""
    # ── 采纳门：条数不在可信区间就整份丢弃 ──
    if not (2 <= len(ded) <= 12):
        return [], f"有效候选 {len(ded)} 条，不在 2–12 条的可信区间，整份丢弃{why_tail}"
    return ded, f"采纳 {len(ded)} 条{why_tail}"


def load_article_keywords() -> tuple[dict, list]:
    """→ ({doi: [关键词]}, [逐卡说明])。来源：interpret/tasks/<slug>.json 的正文
    与解读卡自带的 keywords 字段（若 subagent 填了，优先用后者）。"""
    man = load_json(CDPDIR / "interpret" / "manifest.json", {}) or {}
    slug2doi = {str(t.get("slug") or ""): str(t.get("doi") or "").lower()
                for t in (man.get("tasks") or [])}
    notes, out = [], {}
    rdir = CDPDIR / "interpret" / "results"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if not isinstance(c, dict):
                continue
            doi = str(c.get("doi") or slug2doi.get(p.stem) or "").lower()
            if not doi:
                continue
            own = as_list(c.get("keywords"))
            if own:
                out[doi] = [str(x).strip() for x in own if str(x).strip()]
                notes.append({"doi": doi, "source": "解读卡 keywords 字段",
                              "keywords": out[doi], "why": "卡片直接给出"})
                continue
            slug = p.stem
            tp = CDPDIR / "interpret" / "tasks" / f"{slug}.json"
            if not tp.exists():
                notes.append({"doi": doi, "source": "正文", "keywords": [],
                              "why": "没有 interpret 任务包，无法取正文关键词"})
                continue
            txt = (load_json(tp, {}) or {}).get("text") or ""
            kws, why = parse_article_keywords(txt)
            if kws:
                out[doi] = kws
            notes.append({"doi": doi, "source": "正文 Keywords 段",
                          "keywords": kws, "why": why})
    return out, notes


def method_terms_by_topics(cards) -> dict:
    """方法层取词 → {词: {出现过的 doi}}。

    **与词云同一套筛选**（v16.3）：中文走全局邻接熵 + 泛化词表，
      英文走 en_terms。旧版在这里直接用 `tokenize()`，
      于是图上的方法节点混进了 `经元`(神经元) `鼠海`(大鼠海马) `养基`(培养基)
      `谷氨`(谷氨酸) `马神`(海马神经元) `氨酸` 这类**跨词边界碎片**，
      以及 `div/min/e18/kcl/student` —— 实测 40 个方法节点里 11 个是垃圾。

    **刻意不动 `tokenize()`**：方向画像（`direction_profiles_from_cards`）
      与冲突检出仍走旧口径（与 `interpret_build.py` 绑定），
      改它会静默漂移聚类结果。这里只影响**图上显示的方法节点**。
    """
    per_doc = {}
    for doi, c in cards.items():
        per_doc[doi] = " ".join(str(m) for m in as_list(c.get("methods")))
    keep_zh, _info = zh_terms(" ".join(per_doc.values()))
    for g in list(keep_zh):
        if g in MISLEADING_ZH or g in GENERIC_ZH:
            del keep_zh[g]
    out = defaultdict(set)
    for doi, text in per_doc.items():
        for g in zh_grams(text) & keep_zh.keys():
            out[g].add(doi)
        for w in set(en_terms(text)):
            out[w].add(doi)
    return out


def load_vocab() -> dict:
    p = CDPDIR / "tag_vocabulary.json"
    if not p.exists():
        p = TPL / "方法范式标签词表.json"
    return load_json(p, {}) or {}


def normalize_tag(tag: str, allowed: list[str], aliases: dict) -> str:
    t = str(tag or "").strip()
    if not t:
        return ""
    if t in allowed:
        return t
    low = t.lower()
    if low in aliases:
        return aliases[low]
    flat = re.sub(r"[\s\-_]+", "", low)
    for a in allowed:
        if re.sub(r"[\s\-_]+", "", a.lower()) == flat:
            return a
    for k, v in aliases.items():
        if re.sub(r"[\s\-_]+", "", str(k).lower()) == flat:
            return v
    return t


# ══════════════════════════════════════════════════════════════
# 数据装载
# ══════════════════════════════════════════════════════════════
def load_author_papers() -> tuple[dict, dict]:
    """→ (题录 {doi: rec}, 解读卡 {doi: card})"""
    cr = load_json(CDPDIR / "crossref_all.json", {}) or {}
    if "records" in cr and isinstance(cr["records"], list):
        cr = {str(r.get("doi") or "").lower(): r for r in cr["records"]}
    recs = {str(k).lower(): v for k, v in cr.items() if isinstance(v, dict)}

    cards = {}
    man = load_json(CDPDIR / "interpret" / "manifest.json", {}) or {}
    slug2doi = {t.get("slug"): str(t.get("doi") or "").lower()
                for t in (man.get("tasks") or [])}
    rdir = CDPDIR / "interpret" / "results"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if isinstance(c, dict):
                doi = str(c.get("doi") or slug2doi.get(p.stem) or "").lower()
                if doi:
                    cards[doi] = c
                elif slug2doi.get(p.stem):
                    cards[slug2doi[p.stem]] = c
    return recs, cards


def load_review_data() -> tuple[dict, dict, dict]:
    """→ (final_list, review 卡 {slug: card}, 卡元信息 {slug: meta})"""
    final = load_json(REFDIR / "final_list.json", {}) or {}
    cards, meta = {}, {}
    man = load_json(REFDIR / "manifest.json", {}) or {}
    for t in (man.get("tasks") or []):
        meta[t.get("slug")] = t
    rdir = REFDIR / "results"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            c = load_json(p)
            if isinstance(c, dict):
                cards[p.stem] = c
    return final, cards, meta


def periods_of(recs: dict) -> dict:
    """作者论文的阶段分组。

    优先读 `.work_cdp/periods.json`（用户配置）；没有就**按年份等分**——
    并在报告里写明"这是算法分段，不是作者履历"，不冒充真实阶段。
    """
    cfg = load_json(CDPDIR / "periods.json")
    years = sorted(int(r.get("year")) for r in recs.values()
                   if str(r.get("year") or "").isdigit())
    if cfg and isinstance(cfg, dict):
        out = {}
        for label, rng in cfg.items():
            if str(label).startswith("_") or not isinstance(rng, (list, tuple)):
                continue
            try:
                lo, hi = int(rng[0]), int(rng[1])
            except Exception:
                continue
            out[label] = (lo, hi)
        if out:
            return {"source": "config", "periods": out}
    if not years:
        return {"source": "none", "periods": {}}
    lo, hi = years[0], years[-1]
    span = max(1, hi - lo + 1)
    n = 3 if span >= 9 else (2 if span >= 4 else 1)
    out = {}
    for i in range(n):
        a = lo + round(span * i / n)
        b = lo + round(span * (i + 1) / n) - 1
        if i == n - 1:
            b = hi
        out[f"阶段 {i + 1}（{a}–{b}）"] = (a, b)
    return {"source": "auto", "periods": out}


# ══════════════════════════════════════════════════════════════
# 图 ①：overview
# ══════════════════════════════════════════════════════════════
def build_overview(recs, cards, final, rcards, rmeta, vocab, max_reviews=3,
                   max_dirs=12, max_review_nodes=24):
    """构建作者中心图谱：作者 → 阶段方向 → 综述；外加方法层与记忆连接。

    返回 (nodes, edges, communities, memory_links, cluster_notes, contradictions)
    """
    allowed_d = vocab.get("direction_tags_vocabulary") or []
    allowed_p = vocab.get("paradigm_tags_vocabulary") or []
    aliases = vocab.get("aliases") or {}
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}
    al_p = {str(k).lower(): v for k, v in (aliases.get("_范式别名") or {}).items()}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(nid, kind, label, **kw):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "kind": kind, "label": label, **kw}
        return nid

    add_node("author", "author", AUTHOR, year=None)

    # ── 阶段 → 方向 ────────────────────────────────────────────
    pinfo = periods_of(recs)
    def period_label(year):
        y = int(year) if str(year or "").isdigit() else None
        if y is None:
            return "年份未知"
        for lab, (lo, hi) in pinfo["periods"].items():
            if lo <= y <= hi:
                return lab
        return "其他"

    # 作者论文按 (阶段, 方向) 聚合
    dir_papers = defaultdict(list)      # (period, direction) → [doi]
    dir_paradigm = defaultdict(Counter)  # direction → paradigm 计数
    dir_methods = defaultdict(Counter)   # direction → 方法词计数
    doi_dir = {}                         # doi → 主方向
    for doi, c in cards.items():
        tags = [normalize_tag(t, allowed_d, al_d) for t in as_list(c.get("direction_tags"))]
        tags = [t for t in tags if t]
        if not tags:
            tags = ["未标注方向"]
        year = (recs.get(doi) or {}).get("year")
        per = period_label(year)
        for t in tags:
            dir_papers[(per, t)].append(doi)
        doi_dir[doi] = tags[0]
        for p in as_list(c.get("paradigm_tags")):
            n = normalize_tag(p, allowed_p, al_p)
            if n:
                dir_paradigm[tags[0]][n] += 1
        for m in as_list(c.get("methods")):
            dir_methods[tags[0]].update(set(tokenize(str(m))))

    # 阶段节点 + 方向节点 + 边
    for (per, d), dois in sorted(dir_papers.items()):
        pid = f"period::{per}"
        add_node(pid, "period", per)
        edges.append({"source": "author", "target": pid, "kind": "阶段",
                      "weight": len(dois), "label": f"{len(dois)} 篇"})
        did = f"dir::{per}::{d}"
        add_node(did, "direction", d, period=per, n_papers=len(dois))
        edges.append({"source": pid, "target": did, "kind": "方向",
                      "weight": len(dois), "label": f"{len(dois)} 篇"})

    # ── 方向下挂「关系最紧密的综述」────────────────────────────
    #   来源：refs/final_list.json（阶段 H2① 的最终 review 列表），
    #   按「相关性」降序取前 max_reviews 条，且**优先综述**。
    items = final.get("items") or []
    def rel_of(x):
        s = x.get("_score") or {}
        return float(s.get("relevance") or 0)

    # 只把综述挂到**图上真实存在的研究方向**上。
    #   旧版对没有 review 卡的文献回退用「期刊名」当方向键，于是凭空造出
    #   上百个假方向节点、每个再挂 3 篇综述 —— 实测 4 个真方向被 99 个
    #   review 节点淹没，图彻底没法读。**挂不上的就如实不挂**，并报出数量。
    real_dirs = {nodes[nid]["label"] for nid in nodes if nid.startswith("dir::")}
    # 方向兜底画像：只有少数参考文献带 review 卡（本语料 506 条里仅 40 条），
    #   只用卡片标签会导致「综述挂载 0 条」—— overview 标题承诺的
    #   「方向 → 最紧密综述」这一层整个是空的。这里对无卡条目复用
    #   assign_direction 的**关键词兜底**（题名+摘要+刊名 vs 作者解读卡画像），
    #   且**只接受图上真实存在的方向**；仍挂不上的如实计入 unattached 并报数。
    _prof = direction_profiles_from_cards(cards, vocab)
    _how = Counter()
    dir_of_ref = defaultdict(list)
    unattached = 0
    for x in items:
        c = rcards.get(str(x.get("id") or "")) or {}
        tags = [normalize_tag(t, allowed_d, al_d)
                for t in as_list(c.get("direction_tags"))]
        tags = [t for t in tags if t in real_dirs]
        if tags:
            _how["review 卡标注"] += 1
        else:
            _blob = " ".join([str(x.get("title") or ""),
                              str(x.get("abstract") or ""),
                              str(x.get("venue") or "")])
            d, why = assign_direction(_blob, {}, _prof, allowed_d, al_d)
            if d in real_dirs:
                tags = [d]
                _how["关键词兜底"] += 1
            else:
                _how["无法归类"] += 1
        if not tags:
            unattached += 1
            continue
        dir_of_ref[tags[0]].append(x)

    attached = 0
    truncated = 0
    for key, xs in sorted(dir_of_ref.items(),
                          key=lambda kv: -len(kv[1]))[:max_dirs]:
        xs = sorted(xs, key=lambda x: (0 if x.get("is_review") else 1, -rel_of(x)))
        picked = xs[:max_reviews]
        truncated += max(0, len(xs) - len(picked))
        targets = [nid for nid in nodes if nid.startswith("dir::") and
                   nodes[nid]["label"] == key]
        if not targets:
            continue
        for i, x in enumerate(picked):
            if attached >= max_review_nodes:
                truncated += len(picked) - i
                break
            rid = f"ref::{x.get('id') or x.get('doi') or i}"
            t = one_line(x.get("title") or "（无题名）", 70)
            add_node(rid, "review", t,
                     year=x.get("year"), venue=x.get("venue") or "",
                     is_review=bool(x.get("is_review")),
                     cited=x.get("cited_by_count") or 0,
                     relevance=rel_of(x),
                     direction=key)
            edges.append({"source": targets[0], "target": rid, "kind": "综述",
                          "weight": 1, "label": "最紧密综述"})
            attached += 1
    attach_stats = {"attached": attached, "unattached": unattached,
                    "truncated": truncated, "n_dirs_shown": len(real_dirs),
                    "how": dict(_how)}

    # ── 方法层 + 记忆连接（v16.3 重写判据）─────────────────────
    #   取词：改用 `method_terms_by_topics()`（与词云同一套筛选），
    #     不再直接 tokenize —— 旧版混进了 经元/鼠海/养基/谷氨/马神/氨酸
    #     这些跨词边界碎片，以及 div/min/e18/kcl/student。
    #   连边：旧判据是"共现篇数 ≥2"，但本语料里高频词几乎篇篇都出现，
    #     于是连出来的是 {神经,经元,统计,对照} 四元团，而且
    #     `神经 ↔ 经元` 权重最高 —— 那是**把一个词连到它自己的子串**。
    #     新判据见下：先禁"子串对"，再按 **lift**（相对共现强度）排序。
    method_doi = method_terms_by_topics(cards)
    n_docs = max(1, len(cards))
    top_methods = sorted(method_doi, key=lambda w: (-len(method_doi[w]), w))[:40]

    for w in top_methods:
        add_node(f"m::{w}", "method", w, n_papers=len(method_doi[w]))

    # 记忆连接判据（v16.3）：
    #   ① 禁「子串对」——`a in b or b in a` 一律不收（神经/经元 这类）；
    #   ② 共现篇数下限 MEM_MIN_SHARED；
    #   ③ 用 **lift** 排序：lift = 共现篇数 × 总篇数 /（词a篇数 × 词b篇数）。
    #      lift≈1 表示"两者独立出现"，>1 才是真的绑在一起。
    #      实测按 lift 排，前几名从"四元团"换成了有信息量的具体方法对。
    #   ④ 单节点出度 ≤3，最多 MEM_MAX_LINKS 条，避免一把橙色扇子。
    co = {}
    for i, a in enumerate(top_methods):
        for b in top_methods[i + 1:]:
            if a in b or b in a:
                continue
            shared = method_doi[a] & method_doi[b]
            if len(shared) < MEM_MIN_SHARED:
                continue
            lift = (len(shared) * n_docs) / max(1, len(method_doi[a]) * len(method_doi[b]))
            if lift < MEM_MIN_LIFT:
                continue
            co[(a, b)] = (lift, len(shared))

    degree = Counter()
    picked_pairs = []
    for (a, b), (lift, n) in sorted(co.items(), key=lambda kv: (-kv[1][0], -kv[1][1])):
        if len(picked_pairs) >= MEM_MAX_LINKS:
            break
        if degree[a] >= MEM_MAX_DEGREE or degree[b] >= MEM_MAX_DEGREE:
            continue
        degree[a] += 1
        degree[b] += 1
        picked_pairs.append(((a, b), lift, n))

    memory_links = []
    for (a, b), lift, n in picked_pairs:
        shared = sorted(method_doi[a] & method_doi[b])
        # 原因**逐条由数据生成**：共现篇数 + 年份跨度 + 涉及方向分布。
        #   旧版是同一句套话（括号里永远是 experimental/mechanistic），
        #   12 条理由一字不差，等于没有理由。
        yrs = sorted(int((recs.get(d) or {}).get("year"))
                     for d in shared
                     if str((recs.get(d) or {}).get("year") or "").isdigit())
        yspan = f"{yrs[0]}–{yrs[-1]}" if yrs else "年份未标注"
        dirs = Counter(doi_dir.get(d, "未标注方向") for d in shared)
        dir_txt = "、".join(f"{k} {v}" for k, v in dirs.most_common(2))
        reason = f"在 {n} 篇中共同出现（{yspan}；{dir_txt}），lift={lift:.1f}"
        name = f"{a} 与 {b}"
        edges.append({"source": f"m::{a}", "target": f"m::{b}",
                      "kind": "记忆连接", "weight": n, "label": name,
                      "reason": reason, "shared": shared[:6]})
        memory_links.append({"name": name, "reason": reason, "n": n,
                             "lift": round(lift, 2), "shared": shared[:6]})
    if not memory_links:
        # 如实说明：不是"没有关系"，而是**没有达到判据**的组合。
        print("[INFO] 记忆连接：本语料未检出达到判据的方法对"
              f"（需共现 ≥{MEM_MIN_SHARED} 篇且 lift ≥{MEM_MIN_LIFT}）")

    # 把方法挂到使用最多的方向上（供 Louvain 形成有意义的社区）
    for w in top_methods:
        bydir = Counter(doi_dir.get(d, "未标注方向") for d in method_doi[w])
        d, n = bydir.most_common(1)[0]
        tgts = [nid for nid in nodes
                if nid.startswith("dir::") and nodes[nid]["label"] == d]
        if tgts:
            edges.append({"source": tgts[0], "target": f"m::{w}",
                          "kind": "方法", "weight": n, "label": f"{n} 篇"})
        else:
            edges.append({"source": "author", "target": f"m::{w}",
                          "kind": "方法", "weight": n, "label": f"{n} 篇"})

    return (nodes, edges, memory_links, dir_paradigm, dir_methods, doi_dir,
            pinfo, attach_stats)


def louvain(nodes, edges, seed=42):
    """Louvain 社区检测。缺 networkx 时**如实降级**（按方向分色）而不是报错退出。"""
    try:
        import networkx as nx
    except Exception:
        return None, "未安装 networkx（用 --with networkx 运行）→ 退化为按方向分色"
    G = nx.Graph()
    for nid in nodes:
        G.add_node(nid)
    for e in edges:
        s, t, w = e["source"], e["target"], max(1, int(e.get("weight") or 1))
        if G.has_node(s) and G.has_node(t):
            if G.has_edge(s, t):
                G[s][t]["weight"] += w
            else:
                G.add_edge(s, t, weight=w)
    if G.number_of_nodes() == 0:
        return {}, ""
    try:
        comms = nx.algorithms.community.louvain_communities(G, weight="weight", seed=seed)
    except Exception as e:                       # 老版本 networkx 没有 louvain
        return None, f"Louvain 不可用（{e}）→ 退化为按方向分色"
    return {nid: i for i, c in enumerate(comms) for nid in c}, ""


def cluster_notes(nodes, edges, comm, dir_paradigm, dir_methods, doi_dir, cards):
    """回答「这一簇在讲什么？」—— **全部由数据聚合**，不做语义臆测。"""
    out = {}
    if not comm:
        return out
    for nid, cid in comm.items():
        out.setdefault(cid, {"n": 0, "dirs": Counter(), "paras": Counter(),
                             "methods": Counter(), "years": []})
        o = out[cid]
        o["n"] += 1
        nd = nodes[nid]
        if nd["kind"] == "direction":
            o["dirs"][nd["label"]] += 1
            o["paras"].update(dir_paradigm.get(nd["label"], {}))
            o["methods"].update(dict(dir_methods.get(nd["label"], {})))
        elif nd["kind"] == "review":
            o["dirs"][nd.get("venue") or "未标注"] += 1
            if nd.get("year"):
                o["years"].append(nd["year"])
    notes = {}
    for cid, o in out.items():
        ds = "、".join(k for k, _ in o["dirs"].most_common(3)) or "（无方向节点）"
        ps = "、".join(k for k, _ in o["paras"].most_common(3)) or "—"
        ms = "、".join(k for k, _ in o["methods"].most_common(6)) or "—"
        yr = f"{min(o['years'])}–{max(o['years'])}" if o["years"] else "—"
        notes[cid] = (f"**这一簇在讲什么**：以「{ds}」为主，"
                      f"方法范式集中在 {ps}；高频方法词：{ms}；"
                      f"涉及文献年份 {yr}；共 {o['n']} 个节点。")
    return notes


def find_contradictions(cards, doi_dir, recs, topn=10):
    """检出「这里哪些地方在自相矛盾？」—— 用**可计算的信号**，不臆断语义。

    判据（两条，都基于卡片自身文字的重合）：
      ① **A 的待验证问题 与 B 的已验证结论 高度重合** ——
         同一方向里，一篇说"这还没解决"，另一篇说"已经证实了"。
      ② **同方向内对立范式并存**：experimental 与 computational 同方向同时是主力。
    输出的是**待人工复核的线索**，不是"已确认的矛盾"——报告措辞严格区分。
    """
    try:
        from refs_curate import tokenize as _tk  # 复用同一套分词，避免口径漂移
        tk = _tk
    except Exception:
        tk = tokenize

    by_dir = defaultdict(list)
    for doi, c in cards.items():
        d = doi_dir.get(doi) or "未标注方向"
        by_dir[d].append((doi, c))

    sig = []
    for d, xs in by_dir.items():
        if len(xs) < 2:
            continue
        for i, (d1, c1) in enumerate(xs):
            for d2, c2 in xs[i + 1:]:
                a = set(tk(" ".join(map(str, as_list(c1.get("open_questions"))))))
                b = set(tk(" ".join(map(str, as_list(c2.get("verified_conclusions"))))))
                if not a or not b:
                    continue
                inter = a & b
                denom = max(1, min(len(a), len(b)))
                ratio = len(inter) / denom
                if ratio >= 0.30 and len(inter) >= 4:
                    sig.append({
                        "kind": "未解决问题 vs 已验证结论",
                        "direction": d,
                        "a": (recs.get(d1) or {}).get("title") or d1,
                        "b": (recs.get(d2) or {}).get("title") or d2,
                        "shared": sorted(inter)[:8],
                        "ratio": round(ratio, 2),
                    })
    sig.sort(key=lambda x: -x["ratio"])
    return sig[:topn]


# ══════════════════════════════════════════════════════════════
# 绘图公共设施
# ══════════════════════════════════════════════════════════════
CJK_FONTS = ["Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC",
             "PingFang SC", "Hiragino Sans GB", "WenQuanYi Zen Hei", "Arial Unicode MS"]


def setup_mpl():
    """导入 matplotlib 并配置中文字体 + 透明背景。"""
    try:
        import matplotlib
    except Exception:
        print("未安装 matplotlib。请用：")
        print("  uv run --with matplotlib --with networkx python 流程1/scripts/knowledge_graph.py")
        return None
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    picked = [f for f in CJK_FONTS if f in have]
    if not picked:
        print("[WARN] 未找到中文字体（试过：" + "、".join(CJK_FONTS) + "）")
        print("  → 图中的中文会显示为方框。请安装任一中文字体，或把图注改为英文。")
    plt.rcParams["font.sans-serif"] = picked + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "none"
    plt.rcParams["savefig.facecolor"] = "none"
    plt.rcParams["savefig.transparent"] = True
    return plt


def save_fig(fig, outdir: pathlib.Path, stem: str, fmt="auto", dpi=300,
             also_png=False, tight=True) -> list[pathlib.Path]:
    """保存图片：**SVG 优先**，SVG **失败才**退 PNG；两者都透明背景。

    `auto` 不等于"两种都出"：用户要的是 **SVG 优先、失败才换 PNG**。
      无条件两种都画既让产物目录多一份重复内容（同一张图两种格式），
      又让出图时间翻倍（实测 31s → SVG 单出约 15s）。
      确实两种都要时用 `--also-png` 显式要求。

    `tight=False`：按**画布原始尺寸**导出，不再收 tight bbox。
      尺寸有硬性要求的图必须用这一档：overview 要求整幅 16:10，
      而 tight bbox 会被画到坐标轴之外的文字（标题/图注）撑开，
      比例随之漂移（实测 16×10 的画布导成了 5121×4033）。
      前提是**所有元素都画在画布之内**，否则会被静默裁掉。

    v16.2：**降级必须留痕**。`auto` 下 SVG 失败会静默变 PNG
      （交付物从"可无损缩放"变成"固定分辨率栅格"），而下游
      `ppt_brief.py` 正好只要 PNG，于是这次降级不会被任何环节察觉。
      现在把"本次到底是 SVG 还是 PNG 回退、为什么"写进 FIG_NOTES，
      最终落到 graph_data.json 与报告里。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    svg_ok = False
    bbox = "tight" if tight else None
    if fmt in ("auto", "svg"):
        p = outdir / f"{stem}.svg"
        try:
            fig.savefig(p, format="svg", transparent=True, bbox_inches=bbox)
            made.append(p)
            svg_ok = True
        except Exception as e:
            FIG_NOTES[stem]["degraded_to_png"] = f"SVG 导出失败：{e}"
            if fmt == "svg":
                print(f"[X] SVG 导出失败（--format=svg 已禁用 PNG 回退）：{e}",
                      file=sys.stderr)
                return made
            print(f"[WARN] SVG 导出失败（{e}）→ 改用 PNG")
    if fmt == "png" or not svg_ok or also_png:
        p = outdir / f"{stem}.png"
        try:
            fig.savefig(p, format="png", transparent=True, dpi=dpi,
                        bbox_inches=bbox)
            # 尺寸自检：`bbox_inches="tight"` 会被画到轴外的元素撑爆画布。
            #   实测出现过 4.7 亿像素的 PNG（约 200×195 英寸）——
            #   那不是"高分辨率"，是打不开的坏图。超过 2 亿像素就明确报警。
            try:
                import struct
                with open(p, "rb") as fh:
                    fh.read(16)
                    wpx, hpx = struct.unpack(">II", fh.read(8))
                FIG_NOTES[stem]["png_px"] = [wpx, hpx]
                if wpx * hpx > 200_000_000:
                    print(f"[WARN] {p.name} 尺寸异常：{wpx}×{hpx} = {wpx * hpx:,} 像素"
                          f"（疑似有元素被画到坐标轴之外，导致 tight bbox 撑爆画布）",
                          file=sys.stderr)
            except Exception:
                pass
            made.append(p)
        except Exception as e:
            print(f"[X] PNG 导出失败：{e}", file=sys.stderr)
    FIG_NOTES[stem]["made"] = [x.suffix.lstrip(".") for x in made]
    return made


# ── 字号自述（v16.2）──────────────────────────────────────────
# 起因：本流程的图都在**超大画布**（15–17 in）上用**绝对字号**，
#   而"能不能读"取决于**最终摆放尺寸**。实测方向聚类图 17 in 宽、
#   论文题名标注 6 pt；若缩到期刊双栏 183 mm（7.205 in），
#   有效字号只剩 6 × 7.205/17 = 2.54 pt —— 早就读不出来了，
#   而**没有任何一步会发现这件事**。
#   nature-figure 用"扫最终 PDF 的 Tf 字号"解决；本流程的图用于
#   报告与 PPT，不需要引入 PyMuPDF：按画布→目标尺寸的线性比例换算即可。
# 结论以 **WARN 而非 FAIL** 呈现：幻灯片场景（13.333 in）下是够的，
#   只有"哪天要放进论文/报告排版"才会踩线。
FONT_TARGETS = {
    "幻灯片 16:9（13.333 in 宽）": 13.333,
    "论文双栏 183 mm（7.205 in 宽）": 7.205,
    "论文单栏 89 mm（3.504 in 宽）": 3.504,
}
FIG_NOTES: dict = {}
# overview 的节点坐标与布局元信息（v16.3 起**落盘**）。
#   为什么必须落盘：位置不写出来，布局就**无法事后审计** ——
#   旧版把"扇区式径向布局"写在注释里，实际早被无约束推力破坏，
#   因为没人能拿到渲染时的坐标，这个偏差存在了很久才被发现。
OVERVIEW_LAYOUT: dict = {}


def note_fonts(stem: str, canvas_in, sizes, extra=None, quiet=False):
    """记录一张图的画布尺寸与所有字号，换算各目标尺寸下的**有效最小字号**。

    `sizes` —— [(说明, pt), ...]，画在**纸上/幻灯片上**的最小可读单元。
    """
    w_in = float(canvas_in[0])
    items = [(str(lab), float(pt)) for lab, pt in sizes if pt]
    rec = FIG_NOTES.setdefault(stem, {})
    rec["canvas_in"] = [round(w_in, 3), round(float(canvas_in[1]), 3)]
    rec["font_pt"] = {lab: pt for lab, pt in items}
    if extra:
        rec.update(extra)
    if not items:
        rec["effective_pt"] = {}
        return rec
    eff, below = {}, []
    for tname, t_in in FONT_TARGETS.items():
        scale = t_in / w_in if w_in else 0.0
        worst = min(items, key=lambda x: x[1])
        eff[tname] = {"scale": round(scale, 4),
                      "min_pt": round(worst[1] * scale, 2),
                      "min_what": worst[0]}
        if worst[1] * scale < WC_FLOOR_PT:
            below.append(f"{tname}：{worst[0]} {worst[1]:g} pt → "
                         f"{worst[1] * scale:.2f} pt")
    rec["effective_pt"] = eff
    rec["below_floor_pt"] = WC_FLOOR_PT
    rec["below_floor"] = below
    if below and not quiet:
        print(f"[WARN] {stem}：按目标尺寸换算后有条目低于 {WC_FLOOR_PT:g} pt —— "
              + "｜".join(below), file=sys.stderr)
    return rec



def _disp_width(s: str) -> float:
    """显示宽度：CJK≈1.0，ASCII≈0.55（与词云同一口径）。"""
    return sum((1.0 if ord(ch) > 0x2E80 else 0.55) for ch in str(s or ""))


def _wrap_disp(s: str, max_units: float) -> list[str]:
    """按**显示宽度**折行（str 的 textwrap 会把中文按 1 字符算，折得不对）。"""
    out, cur, w = [], "", 0.0
    for ch in str(s or ""):
        cw = 1.0 if ord(ch) > 0x2E80 else 0.55
        if w + cw > max_units and cur:
            out.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out or [""]


def _method_ring_order(method_labels, memory_links):
    """把方法排成一圈，让**有记忆连接的**尽量相邻。

    做法：在"记忆连接"构成的邻接图上做**一维谱排序**（Laplacian 的
      Fiedler 向量，即经典的 seriation）：把相连的词排到相邻位置。
      先按连通分量分组（分量之间本来就没有连接），分量内再谱排序。
      兜底：numpy 不可用或矩阵退化时保持原顺序。

    为什么不用贪心 Hamilton 路径（上一版）：8 条连接通常分成好几个
      连通分量，一条路径没法让所有对都相邻；谱排序是全局最优的近似，
      实测弧线明显变短，不再出现横跨 120° 的长弧。
    """
    labels = list(method_labels)
    if len(labels) < 3:
        return labels
    idx = {w: i for i, w in enumerate(labels)}
    adj = defaultdict(set)
    for m in memory_links or []:
        parts = str(m.get("name") or "").split(" 与 ")
        if len(parts) == 2 and parts[0] in idx and parts[1] in idx:
            adj[parts[0]].add(parts[1])
            adj[parts[1]].add(parts[0])
    if not adj:
        return labels
    comps, seen = [], set()
    for w in labels:
        if w in seen:
            continue
        stack, comp = [w], []
        seen.add(w)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    comps.sort(key=lambda c: (-len(c), idx[c[0]]))
    ordered = []
    for comp in comps:
        if len(comp) <= 2:
            ordered += comp
            continue
        try:
            import numpy as np
            pos = {w: i for i, w in enumerate(comp)}
            A = np.zeros((len(comp), len(comp)))
            for u in comp:
                for v in adj.get(u, ()):
                    if v in pos:
                        A[pos[u], pos[v]] = 1.0
            L = np.diag(A.sum(axis=1)) - A
            _vals, vecs = np.linalg.eigh(L)
            order = np.argsort(vecs[:, 1])
            ordered += [comp[i] for i in order]
        except Exception:
            ordered += comp
    return ordered


def _angular_span(angles):
    """→ (start_deg, width_deg)：覆盖这些角度的**最小**扇区（处理 0/360 跨接）。"""
    if not angles:
        return None
    a = sorted({round(float(x) % 360.0, 3) for x in angles})
    if len(a) == 1:
        return (a[0] - 6.0, 12.0)
    gaps = [((a[(i + 1) % len(a)] - a[i]) % 360.0, i) for i in range(len(a))]
    width, k = max(gaps)
    start = a[(k + 1) % len(a)]
    span = (a[k] - start) % 360.0
    if span <= 0:
        span = 360.0
    return (start, span)


def compute_overview_layout(nodes, edges, memory_links,
                            tree_scale=0.78, ring_radius=1.02, ring_cap=12):
    """→ (pos, meta)。**可单测、可落盘**的 overview 布局（v16.3 抽出）。

    为什么把布局从绘图里抽出来：
      旧版布局内联在 `draw_overview()` 里，位置既不落盘也无法单测 ——
      于是"扇区式径向布局"被后面的无约束推力悄悄破坏了很久都没人发现。
      抽出来之后：① 位置进 `graph_data.json` 可事后审计；
      ② 自检能直接断言"每层半径单调递增""子树扇区不重叠"。

    结构：
      树 —— 作者(根) → 阶段 → 方向 → 综述，交给 igraph 的
        `layout_reingold_tilford_circular`（径向整齐树）。
        实测：根在圆心、每层等半径、每个子树扇区连续且宽度∝叶子数。
      方法环 —— 方法**不放进树里**（40 个方法词挂进去会把层级压垮），
        另置外环，并按记忆连接把相关方法排到相邻位置。
    """
    import igraph as ig
    import math

    tree_kinds = ("阶段", "方向", "综述")
    t_edges = [(e["source"], e["target"]) for e in edges
               if e.get("kind") in tree_kinds]
    t_nodes, seen = [], set()
    for nid in ("author",):
        if nid in nodes:
            t_nodes.append(nid)
            seen.add(nid)
    for s, t in t_edges:
        for nid in (s, t):
            if nid in nodes and nid not in seen:
                t_nodes.append(nid)
                seen.add(nid)

    pos, meta = {}, {"tree_scale": tree_scale, "ring_radius": ring_radius,
                     "n_tree_nodes": len(t_nodes), "ring": [], "sectors": []}
    if len(t_nodes) < 2:
        pos["author"] = (0.0, 0.0)
        return pos, meta

    idx = {nid: i for i, nid in enumerate(t_nodes)}
    g = ig.Graph(n=len(t_nodes),
                 edges=[(idx[s], idx[t]) for s, t in t_edges
                        if s in idx and t in idx],
                 directed=True)
    root = [idx["author"]] if "author" in idx else [0]
    lay = g.layout_reingold_tilford_circular(root=root)
    raw = {nid: (float(lay[i][0]), float(lay[i][1])) for nid, i in idx.items()}
    rmax = max((math.hypot(x, y) for x, y in raw.values()), default=1.0) or 1.0
    for nid, (x, y) in raw.items():
        pos[nid] = (x / rmax * tree_scale, y / rmax * tree_scale)
    meta["rmax_raw"] = round(rmax, 4)
    meta["radii"] = {}
    for nid, (x, y) in pos.items():
        r = round(math.hypot(x, y), 4)
        meta["radii"].setdefault(round(r, 4), 0)
        meta["radii"][round(r, 4)] += 1

    # ── 扇区：每个阶段占一段连续角度（从它自己的后代角度推出）──
    children = defaultdict(list)
    for s, t in t_edges:
        children[s].append(t)

    def _desc_angles(pid):
        out = []
        for did in children.get(pid, []):
            for nid in (did, *children.get(did, [])):
                if nid in pos:
                    x, y = pos[nid]
                    if math.hypot(x, y) > 1e-9:
                        out.append(math.degrees(math.atan2(y, x)))
        return out

    for nid in t_nodes:
        if nodes[nid]["kind"] != "period":
            continue
        span = _angular_span(_desc_angles(nid))
        if not span:
            continue
        meta["sectors"].append({"id": nid, "label": nodes[nid]["label"],
                                "start": round(span[0], 2),
                                "width": round(span[1], 2)})

    # ── 方法外环 ──────────────────────────────────────────────
    #   环的成员必须**优先包含所有记忆连接的端点**：否则被截断掉端点，
    #   弧线画不出来 —— 实测 ring_cap=12 时 8 条连接全被跳过，图上一条弧都没有。
    m_all = {nodes[nid]["label"]: nid for nid in nodes
             if nodes[nid]["kind"] == "method"}
    need = set()
    for m in memory_links or []:
        for p in str(m.get("name") or "").split(" 与 "):
            if p in m_all:
                need.add(p)
    ring_cap = max(int(ring_cap), len(need))
    ranked = sorted(m_all, key=lambda w: (-(nodes[m_all[w]].get("n_papers") or 0), w))
    ordered_labels = [w for w in ranked if w in need]
    for w in ranked:
        if len(ordered_labels) >= ring_cap:
            break
        if w not in ordered_labels:
            ordered_labels.append(w)
    ordered_labels = _method_ring_order(ordered_labels, memory_links)
    lab2id = {w: m_all[w] for w in ordered_labels}
    n_ring = len(ordered_labels)
    for i, lab in enumerate(ordered_labels):
        ang = -math.pi / 2 + 2 * math.pi * (i / max(1, n_ring))
        nid = lab2id[lab]
        pos[nid] = (ring_radius * math.cos(ang), ring_radius * math.sin(ang))
        meta["ring"].append({"id": nid, "label": lab,
                             "angle_deg": round(math.degrees(ang) % 360, 2),
                             "n_papers": nodes[nid].get("n_papers")})
    meta["n_method_nodes_total"] = len(m_all)
    meta["n_method_nodes_ring"] = n_ring
    meta["ring_missing_link_endpoints"] = sorted(need - set(ordered_labels))
    return pos, meta


def draw_overview(plt, nodes, edges, comm, outdir, stem, fmt, dpi, also_png=False,
                  memory_links=None):
    """作者中心**径向整齐树**（v16.3 重写）。

    与旧版的根本区别 —— 旧版把「定位置」和「躲标签」混成一件事：
      自己算扇区角度，再用 **150 轮无约束推力**（只钉住作者、只夹边界）
      把节点往任意方向推。推的结果是**径向层级被抹掉**：
      渲染出来综述挤成左侧一团、方法漂到外圈，"扇区式径向布局"
      只存在于代码注释里（实测 93 节点 / 126 边）。
    新版把两件事分开：
      ① 层级位置 → `compute_overview_layout()`（igraph 径向整齐树）；
      ② 标签避让 → adjustText（**不动节点位置**，只移文字）。
    另外：综述题名不再截断上云（旧版 24 条里 23 条被截成 "…"），
      改为**左图编号 + 右侧完整清单**。
    """
    import math
    if not nodes:
        return []
    pos, lmeta = compute_overview_layout(nodes, edges, memory_links or [])
    if not pos:
        return []

    # ── 版式：左=树（正方形、等比），右=编号清单（两栏）+ 记忆连接条 ──
    FIG_W, FIG_H = 16.0, 10.0
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    ax = fig.add_axes([0.010, 0.090, 0.500, 0.830])       # 正方形绘图区
    axc1 = fig.add_axes([0.522, 0.185, 0.226, 0.735])     # 清单左栏
    axc2 = fig.add_axes([0.756, 0.185, 0.226, 0.735])     # 清单右栏
    axl = fig.add_axes([0.522, 0.058, 0.460, 0.115])      # 记忆连接条
    for _a in (ax, axc1, axc2, axl):
        _a.set_axis_off()
    ax.set_aspect("equal")
    R = lmeta["ring_radius"]
    ax.set_xlim(-R - 0.24, R + 0.24)
    ax.set_ylim(-R - 0.20, R + 0.24)

    # ── 阶段扇区（浅色扇形 + 弧线），让"扇区式"真的看得见 ─────
    SECTOR_COLORS = ["#EAF2FB", "#FDF1E7", "#EDF7EF", "#F3EEF9", "#FBF3E9"]
    for i, sec in enumerate(lmeta["sectors"]):
        a0 = math.radians(sec["start"])
        a1 = math.radians(sec["start"] + sec["width"])
        th = [a0 + (a1 - a0) * k / 60 for k in range(61)]
        rr = lmeta["tree_scale"]
        ax.fill([0.0] + [rr * math.cos(t) for t in th],
                [0.0] + [rr * math.sin(t) for t in th],
                color=SECTOR_COLORS[i % len(SECTOR_COLORS)], zorder=0)
        ax.plot([rr * math.cos(t) for t in th], [rr * math.sin(t) for t in th],
                color="#C9D6E4", lw=0.9, zorder=1)

    # ── 树边 ─────────────────────────────────────────────────
    for e in edges:
        k = e.get("kind")
        if k not in ("阶段", "方向", "综述"):
            continue
        s, t = e["source"], e["target"]
        if s not in pos or t not in pos:
            continue
        w = 1.5 if k == "阶段" else (0.9 if k == "方向" else 0.5)
        col = "#8FA8C4" if k != "综述" else "#E8B69E"
        ax.plot([pos[s][0], pos[t][0]], [pos[s][1], pos[t][1]],
                ls="-", lw=w, color=col, alpha=0.75, zorder=2)

    # ── 节点 ─────────────────────────────────────────────────
    #   关键改动：**节点是「小圆点」，标签是可移动的文字**。
    #   旧版把标签做成药丸状节点本身 —— 标签一动，节点也就动了，
    #   于是 adjustText 无处施力（旧版干脆自己推节点，把层级推没了）。
    BOX = {
        "author":    dict(fs=17, pad=0.55, fc="#1B3A6B", tc="#FFFFFF", wrap=18),
        "period":    dict(fs=11, pad=0.42, fc="#2E86AB", tc="#FFFFFF", wrap=22),
        "direction": dict(fs=9,  fc="#7FB3D5", tc="#0F2C46", dot=34),
        "method":    dict(fs=8,  fc="#95A3B3", tc="#33475B", dot=30),
        "review":    dict(fs=6.5, fc="#E4572E", tc="#FFFFFF", dot=64),
    }
    # 综述编号（左图橙点 ↔ 右侧清单一致）
    rev_order = sorted(
        (nid for nid in nodes if nodes[nid]["kind"] == "review"),
        key=lambda nid: (str(_parent_period(nid, edges, nodes)),
                         str(nodes[nid].get("direction") or ""),
                         -(nodes[nid].get("relevance") or 0), nid))
    rev_no = {nid: i + 1 for i, nid in enumerate(rev_order)}

    # Louvain 社区只给「研究方向」层上色（社区仍可见）；
    #   综述/方法保持类型色 —— 三类都上色会让整张图失去视觉主次。
    COMM_PALETTE = ["#4E79A7", "#59A14F", "#B07AA1", "#76B7B2",
                    "#8C6BB1", "#5B8C5A", "#6A9FB5", "#AF7AC5"]
    movable = []          # 交给 adjustText 的文字（**只放方向层**）
    sec_mid = {}
    for sec in lmeta["sectors"]:
        sec_mid[sec["id"]] = sec["start"] + sec["width"] / 2.0
    n_dir_seen = 0
    for nid, nd in nodes.items():
        if nid not in pos:
            continue
        k = nd["kind"]
        if k == "method":
            continue          # 方法环单独画（固定位置，不参与避让）
        cfg = BOX.get(k, BOX["method"])
        x, y = pos[nid]
        if k == "review":
            ax.scatter([x], [y], s=cfg["dot"], c=cfg["fc"], edgecolors="white",
                       linewidths=0.7, zorder=5)
            ax.text(x, y, str(rev_no.get(nid, "")), ha="center", va="center",
                    fontsize=cfg["fs"], color="white", zorder=6)
            continue
        if k == "direction":
            # 圆点钉在树的位置上；标签先放在**径向外侧**，
            #   并按奇偶**径向错开两档**（同半径上 25 个标签必然互撞）。
            rr = math.hypot(x, y) or 1.0
            color = cfg["fc"]
            if comm and nid in comm:
                color = COMM_PALETTE[comm[nid] % len(COMM_PALETTE)]
            ax.scatter([x], [y], s=cfg["dot"], c=[color],
                       edgecolors="white", linewidths=0.6, zorder=5)
            off = 0.055 if (n_dir_seen % 2 == 0) else 0.155
            n_dir_seen += 1
            t = ax.text(x * (rr + off) / rr, y * (rr + off) / rr,
                        _wrap_disp(nd["label"], 10)[0],
                        ha="center", va="center", fontsize=cfg["fs"],
                        color=cfg["tc"], zorder=6)
            movable.append(t)
            continue
        if k == "period":
            # 阶段名从圆心附近**移到本扇区的中角**：留在 r=0.26 会压住作者名
            #   （实测「阶段 2」药丸直接盖在「刘北明」上）。
            ax.scatter([x], [y], s=26, c=cfg["fc"], edgecolors="white",
                       linewidths=0.6, zorder=5)
            mid = math.radians(sec_mid.get(nid, math.degrees(math.atan2(y, x))))
            # 阶段名放在**扇区外的空带**（r≈0.86，介于综述环 0.78 与方法环 1.02 之间）：
            #   放在 r=0.26 会压住作者名；放在 0.66 又会被方向层标签压住。
            rp = lmeta["tree_scale"] * 1.10
            ax.text(rp * math.cos(mid), rp * math.sin(mid),
                    _wrap_disp(nd["label"], cfg["wrap"])[0],
                    ha="center", va="center", fontsize=cfg["fs"],
                    color="#12496B", zorder=6,
                    bbox=dict(boxstyle=f"round,pad={cfg['pad']},rounding_size=0.9",
                              facecolor="#FFFFFF", edgecolor="#9EC4E0", alpha=0.92))
            continue
        lab = _wrap_disp(nd["label"], cfg["wrap"])[0]
        ax.text(x, y, lab, ha="center", va="center", fontsize=cfg["fs"],
                color=cfg["tc"], zorder=6,
                bbox=dict(boxstyle=f"round,pad={cfg['pad']},rounding_size=0.9",
                          facecolor=cfg["fc"], edgecolor="none"))

    # ── 方法外环 + 记忆连接弧线 ───────────────────────────────
    #   弧线**沿外环画**（走两节点间的短弧），不画弦：
    #     弦会穿过圆心、压在树和标签上 —— 实测上一版 8 条弧全部横穿画面。
    #     沿环画还有个好处：环序已保证相连的方法相邻，弧线自然短。
    ring = lmeta["ring"]
    ang_of = {r["id"]: math.radians(r["angle_deg"]) for r in ring}
    Rarc = lmeta["ring_radius"] + 0.085
    n_drawn_arcs = 0
    for e in edges:
        if e.get("kind") != "记忆连接":
            continue
        s, t = e["source"], e["target"]
        if s not in ang_of or t not in ang_of:
            continue
        a0, a1 = ang_of[s], ang_of[t]
        d = (a1 - a0) % (2 * math.pi)
        if d <= 1e-9:
            continue
        if d > math.pi:                      # 走短弧
            a0, a1 = a1, a0
            d = (a1 - a0) % (2 * math.pi)
        th = [a0 + d * k / 48 for k in range(49)]
        ax.plot([Rarc * math.cos(x) for x in th],
                [Rarc * math.sin(x) for x in th],
                ls="--", lw=1.6, color="#E4572E", alpha=0.9, zorder=3,
                solid_capstyle="round")
        n_drawn_arcs += 1
    for r in ring:
        x, y = pos[r["id"]]
        ax.scatter([x], [y], s=BOX["method"]["dot"], c=BOX["method"]["fc"],
                   edgecolors="white", linewidths=0.6, zorder=5)
        rr = math.hypot(x, y) or 1.0
        # 方法环标签**固定放在环外侧、不参与 adjustText**：
        #   12 个标签在环上的间距约 1.7 in，本身就够放；
        #   上一版把它交给 adjustText，结果被甩到 r≈2 处、脱离圆点
        #   （且当时没画引线），环的结构直接看不见了。
        ax.text(x * (rr + 0.085) / rr, y * (rr + 0.085) / rr, r["label"],
                ha="center", va="center", fontsize=BOX["method"]["fs"],
                color="#1F3B57", zorder=6)

    # ── 标签避让：**只移文字，不动节点**（旧版靠推节点，把层级推没了）──
    label_note = "未调用 adjustText"
    try:
        from adjustText import adjust_text
        adjust_text(movable, ax=ax,
                    expand=(1.10, 1.20), force_text=(0.20, 0.35),
                    only_move={"text": "xy", "static": "xy",
                               "explode": "xy", "pull": "xy"},
                    arrowprops=dict(arrowstyle="-", color="#9AA7B4", lw=0.6,
                                    shrinkA=1, shrinkB=2),
                    lim=250, precision=0.01, timeout=8)
        label_note = "adjustText 已执行"
    except Exception as ex:                                   # pragma: no cover
        label_note = f"adjustText 未生效：{type(ex).__name__}: {ex}"
        print(f"[WARN] 标签避让未生效（{label_note}）—— 文字可能重叠", file=sys.stderr)

    # ── 图例（画布左上角空白处）──────────────────────────────
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    # 图例挪到**画布底部横排**：放在左上角会压住正题标题（实测两者重叠）。
    fig.legend(handles=[
        Patch(facecolor=BOX["author"]["fc"], edgecolor="none", label="作者"),
        Patch(facecolor=BOX["period"]["fc"], edgecolor="none", label="阶段"),
        Patch(facecolor=BOX["direction"]["fc"], edgecolor="none",
              label="研究方向（Louvain 社区上色）"),
        Patch(facecolor=BOX["review"]["fc"], edgecolor="none",
              label="最紧密综述（编号见右侧清单）"),
        Patch(facecolor=BOX["method"]["fc"], edgecolor="none", label="方法 / 范式（外环）"),
        Line2D([], [], ls="--", lw=1.6, color="#E4572E", label="记忆连接"),
    ], loc="lower center", bbox_to_anchor=(0.5, 0.002), frameon=False,
        fontsize=9, handlelength=1.3, handleheight=0.9, borderpad=0.2,
        labelspacing=0.5, ncol=6, columnspacing=1.6)

    # ── 标题：一行正题 + 一条短强调线（放在左侧树区正上方）─────
    fig.text(0.26, 0.960, f"{AUTHOR} 研究工作图谱", ha="center", va="center",
             fontsize=24, color="#16324F")
    fig.add_artist(Line2D([0.26 - 0.022, 0.26 + 0.022], [0.927, 0.927],
                          transform=fig.transFigure, color="#E4572E",
                          lw=2.0, solid_capstyle="round", alpha=0.9))

    # ── 右侧清单：两栏放「最紧密综述」完整题名（不截断）────────
    FS = 7.0
    PANEL_TOP = "最紧密综述（编号对应左图橙点）"
    entries = []
    for nid in rev_order:
        nd = nodes[nid]
        per = _parent_period(nid, edges, nodes) or "—"
        block = [f"{rev_no[nid]:>2d}. [{per} · {nd.get('direction') or '—'}]"]
        block += ["   " + seg for seg in _wrap_disp(nd["label"], 34)]
        ven = str(nd.get("venue") or "").strip()
        extra = " · ".join(x for x in (str(nd.get("year") or ""), ven[:26],
                                       f"被引 {nd.get('cited') or 0}") if x)
        if extra:
            block.append("   " + extra)
        entries.append(block)
    # 两栏均分；栏高按面板实际高度算，超出就如实报"余 N 条见报告"。
    half = (len(entries) + 1) // 2
    cols = [entries[:half], entries[half:]]
    per_col_lines = int(0.735 * FIG_H * 72 / (FS * 1.42))
    for i, (axc, group) in enumerate(zip((axc1, axc2), cols)):
        lines = [PANEL_TOP if i == 0 else PANEL_TOP + "（续）", ""]
        used = 2
        shown = 0
        for block in group:
            if used + len(block) > per_col_lines - 1:
                break
            lines += block
            used += len(block)
            shown += 1
        rest = len(group) - shown
        if rest:
            lines.append(f"… 余 {rest} 条见《{AUTHOR}_知识图谱.md》")
        axc.text(0.0, 1.0, "\n".join(lines), ha="left", va="top", fontsize=FS,
                 color="#1A1A1A", transform=axc.transAxes, linespacing=1.42)

    # ── 记忆连接条（横跨右侧两栏，每条一行）────────────────────
    link_lines = ["记忆连接（橙色虚线：两条方法为何要一起读）"]
    for i, m in enumerate(memory_links or [], 1):
        one = f"{i:>2d}. {m['name']} —— {m.get('reason') or ''}"
        link_lines.append(_wrap_disp(one, 74)[0])
    axl.text(0.0, 1.0, "\n".join(link_lines), ha="left", va="top", fontsize=FS,
             color="#8C3A1C", transform=axl.transAxes, linespacing=1.42)

    note_fonts(stem, (FIG_W, FIG_H),
               [("右侧清单正文", FS), ("方法环标签", BOX["method"]["fs"]),
                ("方向节点", BOX["direction"]["fs"]), ("图例", 9), ("标题", 24)],
               extra={"n_nodes": len(pos), "n_tree_nodes": lmeta["n_tree_nodes"],
                      "n_method_ring": lmeta["n_method_nodes_ring"],
                      "n_reviews": len(rev_order), "n_review_entries": len(entries),
                      "n_memory_links": len(memory_links or []),
                      "n_memory_arcs_drawn": n_drawn_arcs,
                      "label_avoidance": label_note,
                      "layout": lmeta})
    made = save_fig(fig, outdir, stem, fmt, dpi, also_png, tight=False)
    plt.close(fig)
    OVERVIEW_LAYOUT.clear()
    OVERVIEW_LAYOUT.update({
        "pos": {k: [round(v[0], 5), round(v[1], 5)] for k, v in pos.items()},
        "meta": lmeta,
        "review_numbers": {str(k): v for k, v in rev_no.items()},
        "n_arcs_drawn": n_drawn_arcs,
        "label_avoidance": label_note,
    })
    if made:
        print(f"[INFO] overview：径向整齐树（igraph layout_reingold_tilford_circular）"
              f"｜树节点 {lmeta['n_tree_nodes']}｜方法外环 "
              f"{lmeta['n_method_nodes_ring']}/{lmeta['n_method_nodes_total']}"
              f"｜综述编号 {len(rev_order)} 条｜弧线 {n_drawn_arcs}/"
              f"{len(memory_links or [])} 条｜{label_note}")
    return made


def _parent_period(nid, edges, nodes):
    """综述节点 → 它所属的阶段标签。"""
    for e in edges:
        if e.get("kind") == "综述" and e["target"] == nid:
            p = nodes.get(e["source"]) or {}
            return p.get("period") or p.get("label") or "—"
    return "—"


def _draw_wordcloud_custom(plt, weights, colors, outdir, stem, fmt, dpi,
                          direction_label, also_png=False, wc_stats=None):
    """词云：颜色区分方向；**词源与剔除口径见图上副标题**。

    碰撞避让是**必须**的，不能靠"黄金角螺旋 + 固定步长"糊过去：
      字号随频次变化，大字会盖住后面所有小字。实测不避让时
      "photocatalytic / labeling"、"porous / mof"、"simulation / reduction"
      三对词完全叠死，读者根本读不出词 —— 而需求明确要求
      **清晰展示所有字符**。这里用确定性的阿基米德螺旋 + 矩形碰撞检测：
      按频次从大到小逐词试位，第一个不重叠的位置即采纳。

    v16.2：取词口径改由 `compute_wordcloud_weights` 负责（关键词优先 +
      邻接熵筛词），本函数只管**排版与避让**，并记录字号自述。
    """
    import math
    if not weights:
        return []
    fig, ax = plt.subplots(figsize=(15, 11))
    fig_w_in, fig_h_in = 15.0, 11.0
    items = sorted(weights.items(), key=lambda x: -x[1])[:90]
    if not items:
        plt.close(fig)
        return []
    mx = max(w for _, w in items)

    ax.set_xlim(-1.0, 1.0)
    ax.set_ylim(-1.0, 1.0)
    ax.set_aspect("auto")
    ax.set_axis_off()

    placed = []          # [(x0, y0, x1, y1)]
    PAD = 0.004

    # 数据坐标 ↔ 点数的换算：x/y 各跨 2 个数据单位，坐标轴约占画布的 78%
    # （其余留给标题、图例与边距）。**必须按实际画布尺寸算**，
    # 用固定的"每单位多少点"会在改 figsize 时立刻失准。
    pt_per_x = fig_w_in * 72 * 0.78 / 2.0
    pt_per_y = fig_h_in * 72 * 0.78 / 2.0

    def text_w(txt, size_pt):
        """按字符类型估宽：CJK 约 1.0×字号，ASCII 约 0.55×字号。

        旧版一律按 0.58×字号 ×字符数 估宽（等于把中文当半角），
          中文词的实际宽度被**低估约 40%** → 碰撞检测漏判 → 大字压住中文小字
          （实测 `experimental` 压死 `测试表征`/`催化`/`合成`）。
          这里逐字符判宽，中英混排的包围盒才与实际渲染一致。
        """
        return sum((1.0 if ord(ch) > 0x2E80 else 0.55) for ch in txt) * size_pt

    def bbox(cx, cy, txt, size_pt):
        # 数据单位下的近似包围盒（宽按字符类型算，见 text_w）
        w = text_w(txt, size_pt) / pt_per_x
        h = size_pt * 1.18 / pt_per_y
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def hits(b):
        for p in placed:
            if not (b[2] + PAD < p[0] or b[0] - PAD > p[2] or
                    b[3] + PAD < p[1] or b[1] - PAD > p[3]):
                return True
        return False

    skipped = 0
    for w, n in items:
        size = 10 + 40 * (n / mx) ** 0.8
        col = colors.get(w, "#2E86AB")
        ok = False
        # 螺旋半径必须**够到画布边缘**：旧版步长 0.012、只试 600 步，
        #   最大半径仅 0.29 —— 所有词被挤在中心一个小圆盘里，
        #   实测 90 个词只放得下 11 个，而且互相压字。
        #   取 0.040×√k（900 步 → 半径可达 1.2）铺满整个画布。
        for k in range(900):
            rad = 0.040 * math.sqrt(k)
            ang = k * 2.399963
            cx, cy = rad * math.cos(ang), rad * 0.78 * math.sin(ang)
            b = bbox(cx, cy, w, size)
            if b[0] < -0.99 or b[2] > 0.99 or b[1] < -0.99 or b[3] > 0.99:
                continue
            if not hits(b):
                ax.text(cx, cy, w, fontsize=size, color=col,
                        ha="center", va="center", alpha=0.93)
                placed.append(b)
                ok = True
                break
        if not ok:
            skipped += 1
    if skipped:
        print(f"[INFO] 词云：{skipped} 个低频词放不下（画布已满），已省略 —— "
              f"完整词频见 graph_data.json 的 wordcloud.weights")

    lgd = "；".join(str(k) for k in list(direction_label)[:8])
    ax.set_title(f"{AUTHOR} 研究方法与实验范式词云", fontsize=20, pad=34)
    # 副标题放在标题**下方**（va="bottom" + 小 y），否则两行会糊在一起。
    # v16.2：把**词源与剔除口径**写在图上 —— 读者才知道这张云是什么口径出来的。
    n_kw = len((wc_stats or {}).get("keywords_used") or {})
    par = (wc_stats or {}).get("paradigms") or {}
    par_txt = ("｜范式：" + " · ".join(f"{k} {v}" for k, v in list(par.items())[:4])
               if par else "")
    sub = (f"词源：文章关键词（{n_kw} 篇）＞ 题名 ＞ 解读卡方法描述"
           f" ｜ 已剔除单位/试剂缩写、受控范式标签与跨词碎片"
           f" ｜ 词大小 = 加权频次{par_txt}")
    ax.text(0.5, 1.012, sub, transform=ax.transAxes, ha="center", va="bottom",
            fontsize=10, color="#555555")
    ax.text(0.5, -0.035, lgd, transform=ax.transAxes, ha="center", va="top",
            fontsize=9, color="#555555")
    note_fonts(stem, (fig_w_in, fig_h_in),
               [("词云副标题", 10), ("方向图例", 9), ("标题", 20)] +
               [(f"词 {t}", 10 + 40 * (n / mx) ** 0.8) for t, n in items[:1]],
               extra={"n_words_placed": len(placed), "n_words_skipped": skipped,
                      "n_terms_available": len(weights), "engine": "custom"})
    made = save_fig(fig, outdir, stem, fmt, dpi, also_png, tight=False)
    plt.close(fig)
    return made


# ══════════════════════════════════════════════════════════════
# 词云布局引擎（v17.2）
#
# 为什么接 `wordcloud`（amueller）：它用**积分图占位检测**在像素级找空位，
#   是这一领域的成熟实现；我们自研的"阿基米德螺旋 + 矩形碰撞"是同一个思路的
#   手搓版本，只是在还没有库的时候够用。接进来之后：
#     多字词/长英文词的贴合更紧、画布利用率更高、词的长宽按**真实字形**算
#     （自研版是按"CJK≈1.0 / ASCII≈0.55 字号"估的，估偏了就会压字）。
#
# **但只借它算布局，不借它出图**（这是关键取舍）：
#   库的 to_file()/to_svg() 只画词，没有标题、副标题、方向图例，
#   而这三样是本流程图件的固定契约；把整张图交给它就会丢掉它们。
#   所以：用它算出每个词的 `(词, 字号px, 位置, 颜色)`，再用 matplotlib 复刻绘制。
#   收益是标题/图例/矢量 SVG/字号自述/碰撞质检**全部保留**，且只有一条出图路径。
#
# 复刻必须满足两个条件，否则词会互相压住（都实测过）：
#   ① **两边绑同一个字体文件**。库按文件画，matplotlib 按族名查；
#      `.ttc` 集合里 matplotlib 可能选到另一款 —— 实测库用 msyh.ttc（常规体）、
#      matplotlib 解析 "Microsoft YaHei" 拿到的是 msyhl.ttc（细体），
#      字形宽度不同 → 复刻后墨迹框重叠 **41 对/90 词**。改用 .ttf（SimHei）
#      并用 FontProperties(fname=…) 绑定后降到 **2 对**（视觉无碰撞）。
#   ② **坐标系纵横比必须与布局画布一致**。库的布局是 1500×1100 的像素空间；
#      若 matplotlib 的 axes 物理比例不是 1500:1100（比如 14.1×9.5 in），
#      纵向会被压缩 → 词挤在一起。这里按画布比例**反算** rect 宽度。
#
# 坐标语义（有坑，已核对库源码 `to_image`）：
#   `layout_` 里存的是 **(y, x)**，库自己画的时候写的是
#   `pos = (int(position[1] * scale), int(position[0] * scale))`。
#   按 (x, y) 用会得到一张完全错乱的图（实测与库自身渲染的 IoU 只有 0.033）。
# ══════════════════════════════════════════════════════════════
WC_LIB_CANVAS = (1500, 1100)     # 布局画布（px）；与图件 16:11.6 的画幅一致
WC_LIB_PX_PER_IN = 100.0         # 1500 px ↔ 15 in（1 px = 0.72 pt）
WC_LIB_RELATIVE_SCALING = 0.8    # 频次对字号的影响；0.45 太扁（实测字号只有 38–70px）
WC_LIB_MAX_FONT = 70             # px
WC_LIB_MIN_FONT = 10             # px
WC_LIB_MARGIN = 3                # px，词间最小净距
WC_LIB_RANDOM_STATE = 42         # 固定 → 可复现
WC_ENGINE_DEFAULT = "library"


def resolve_cjk_font_file():
    """→ (字体文件路径 or None, 字体名)。

    **必须返回文件路径**：`wordcloud` 按文件渲染、matplotlib 按族名查表，
    两边必须锁到同一个文件，否则字形宽度不一致、复刻出来会压字（见上方说明）。
    优先 `.ttf`：`.ttc` 是字体集合，同一个名字可能对应多个 face，绑定不稳。
    """
    from matplotlib import font_manager
    cands = list(CJK_FONTS)
    by_name = {}
    for f in font_manager.fontManager.ttflist:
        if f.name in cands:
            by_name.setdefault(f.name, f.fname)
    order = sorted(by_name.items(),
                   key=lambda kv: (0 if kv[1].lower().endswith(".ttf") else 1,
                                   cands.index(kv[0]) if kv[0] in cands else 99))
    if not order:
        return None, ""
    name, path = order[0]
    return path, name


def wordcloud_library_layout(weights, font_file, canvas=None, max_words=90,
                             relative_scaling=None, max_font=None, min_font=None,
                             margin=None, random_state=WC_LIB_RANDOM_STATE):
    """只用 `wordcloud` **算布局** → (layout, info)。

    `layout` —— [(词, 字号px, (y, x) 左上角, 方向, 颜色)]，顺序即放置顺序
    `info`   —— 放置/未放置计数、字号区间、随机种子等，供报告如实申报

    纯计算、不绘图：可单测，也能被自检直接断言（见 self_test ⑭）。
    """
    from wordcloud import WordCloud
    w_px, h_px = canvas or WC_LIB_CANVAS
    if not weights:
        return [], {"requested": 0, "placed": 0, "dropped": []}
    wc = WordCloud(
        font_path=font_file,
        width=int(w_px), height=int(h_px),
        mode="RGBA",
        background_color=None,                     # 透明背景
        prefer_horizontal=1.0,                     # 中文一律横排（竖排中文极难读）
        relative_scaling=(WC_LIB_RELATIVE_SCALING if relative_scaling is None
                          else relative_scaling),
        max_words=int(max_words),
        max_font_size=(WC_LIB_MAX_FONT if max_font is None else max_font),
        min_font_size=(WC_LIB_MIN_FONT if min_font is None else min_font),
        margin=(WC_LIB_MARGIN if margin is None else margin),
        random_state=random_state,
        collocations=False,                        # 我们直接给词频，不做搭配统计
    )
    wc.generate_from_frequencies(dict(weights))
    placed = dict(wc.words_ or {})
    sizes = [int(e[1]) for e in wc.layout_]
    # 「请求数」= min(max_words, 可用词型数)：直接把 580 个候选当分母会误导
    #   （图上只可能画 weight 最高的前 90 个），所以两个口径分开报。
    n_avail = len(weights)
    n_req = min(int(max_words), n_avail)
    topn = sorted(weights, key=lambda w: -float(weights[w]))[:n_req]
    info = {
        "engine": "wordcloud(amueller)",
        "canvas_px": [int(w_px), int(h_px)],
        "pixel_per_inch": WC_LIB_PX_PER_IN,
        "random_state": random_state,
        "relative_scaling": wc.relative_scaling,
        "max_words": int(max_words),
        "n_available": n_avail,
        "n_requested": n_req,
        "requested": n_req,
        "placed": len(placed),
        "dropped": [w for w in topn if w not in placed],
        "font_size_px": [min(sizes), max(sizes)] if sizes else None,
        "max_font_size_px": wc.max_font_size,
        "min_font_size_px": wc.min_font_size,
    }
    return list(wc.layout_), info


def draw_wordcloud(plt, weights, colors, outdir, stem, fmt, dpi, direction_label,
                   also_png=False, wc_stats=None, engine=None):
    """词云：**布局交给 `wordcloud` 库，绘制仍走 matplotlib**（v17.2）。

    `engine` —— library（默认）｜ custom（自研螺旋 + 矩形碰撞，见
      `_draw_wordcloud_custom`）。保留 custom 只为 A/B 与回退，日常用 library。
    """
    eng = (engine or WC_ENGINE_DEFAULT).lower()
    if eng == "custom":
        return _draw_wordcloud_custom(plt, weights, colors, outdir, stem, fmt, dpi,
                                      direction_label, also_png, wc_stats)
    if not weights:
        return []

    import math
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Patch
    from PIL import ImageFont

    font_file, font_name = resolve_cjk_font_file()
    font_missing = None
    if not font_file:
        font_missing = ("未找到任何中文字体文件（试过：" + "、".join(CJK_FONTS) +
                        "）→ 中文会显示为方框")
        print(f"[WARN] {font_missing}\n"
              f"  → 图的可用性受影响；字号自述与报告会如实标注。", file=sys.stderr)

    layout, linfo = wordcloud_library_layout(weights, font_file,
                                             max_words=90)
    if not layout:
        print("[WARN] 词云布局为空（wordcloud 未放置任何词）")
        return []

    w_px, h_px = linfo["canvas_px"]
    FIG_W, FIG_H = 15.0, 11.0
    # 先按画布纵横比反算 rect 宽度，保证 axes 的物理比例 == 画布比例（见文件头说明②）
    rect_h = 0.820
    rect_w = rect_h * FIG_H * (w_px / h_px) / FIG_W
    rect_x = (1.0 - rect_w) / 2.0
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    ax = fig.add_axes([rect_x, 0.072, rect_w, rect_h])
    ax.set_xlim(0, w_px)
    ax.set_ylim(h_px, 0)                     # y 向下，与库的像素空间一致
    ax.set_axis_off()

    ax_w_in = FIG_W * rect_w
    pt_per_px = ax_w_in * 72.0 / w_px        # 字号 px → pt
    fp = FontProperties(fname=font_file) if font_file else None
    pil_cache, ink_boxes, over_pairs = {}, [], 0
    for ((word, _freq), size, position, _orientation, _color) in layout:
        size = int(size)
        if font_file:
            if size not in pil_cache:
                pil_cache[size] = ImageFont.truetype(font_file, size)
            pil_font = pil_cache[size]
            ascent, _descent = pil_font.getmetrics()
            bb = pil_font.getbbox(word)
            y, x = int(position[0]), int(position[1])   # 库内部是 (y, x)
            ink_boxes.append((x + bb[0], y + bb[1], x + bb[2], y + bb[3]))
            y_baseline = y + ascent
            x_left = x
        else:
            y_baseline, x_left = int(position[0]), int(position[1])
        ax.text(x_left, y_baseline, word, fontsize=size * pt_per_px,
                fontproperties=fp, color=colors.get(word, "#2E86AB"),
                ha="left", va="baseline", zorder=4)
    # 复刻自检：墨迹框重叠对数（用 PIL 量，与库的占位检测同源）
    for i in range(len(ink_boxes)):
        for j in range(i + 1, len(ink_boxes)):
            a, b = ink_boxes[i], ink_boxes[j]
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                over_pairs += 1

    # ── 标题 / 副标题 / 方向图例（库不管这三样，由我们补）────────
    n_kw = len((wc_stats or {}).get("keywords_used") or {})
    par = (wc_stats or {}).get("paradigms") or {}
    par_txt = ("｜范式：" + " · ".join(f"{k} {v}" for k, v in list(par.items())[:4])
               if par else "")
    drop_n = len(linfo["dropped"])
    # 副标题拆两行：单行放不下 —— 实测一行 140 个显示单位会**溢出画布右侧**
    #   （画布 15 in = 1080 pt，9.5 pt 下约 114 个 CJK 单位的容量）。
    sub1 = (f"布局 = wordcloud 库占位检测 ｜ 候选 {linfo['n_available']} 个词型按权重"
            f"取前 {linfo['n_requested']} 个，放下 {linfo['placed']}"
            + (f"，未放下 {drop_n} 个（见 graph_data.json）" if drop_n else "，全部放下"))
    sub2 = (f"词源：文章关键词（{n_kw} 篇）＞ 题名 ＞ 解读卡方法描述"
            f"｜词大小 = 加权频次{par_txt}")
    fig.text(0.5, 0.958, f"{AUTHOR} 研究方法与实验范式词云", ha="center",
             va="center", fontsize=20, color="#16324F", fontproperties=fp)
    fig.text(0.5, 0.925, sub1, ha="center", va="center", fontsize=9,
             color="#555555", fontproperties=fp)
    fig.text(0.5, 0.904, sub2, ha="center", va="center", fontsize=9,
             color="#555555", fontproperties=fp)
    if direction_label:
        names = list(direction_label)
        fig.legend(handles=[Patch(facecolor=direction_label[d], edgecolor="none",
                                  label=str(d)) for d in names[:8]],
                   loc="lower center", bbox_to_anchor=(0.5, 0.004), ncol=4,
                   frameon=False, fontsize=9, handlelength=1.1, handleheight=0.9,
                   columnspacing=1.4, labelspacing=0.4,
                   prop=fp if fp else None)
    FIG_NOTES.setdefault(stem, {})["wordcloud_engine"] = "library"
    FIG_NOTES[stem]["wordcloud_info"] = {
        "placed": linfo["placed"], "requested": linfo["requested"],
        "dropped_n": drop_n, "dropped_head": linfo["dropped"][:20],
        "font_size_px": linfo["font_size_px"],
        "canvas_px": linfo["canvas_px"], "pixel_per_inch": WC_LIB_PX_PER_IN,
        "random_state": linfo["random_state"],
        "relative_scaling": linfo["relative_scaling"],
        "replay_ink_overlaps": over_pairs,
        "font_file": font_file, "font_name": font_name,
        "font_missing": font_missing,
    }
    if over_pairs:
        print(f"[WARN] 词云复刻后有 {over_pairs} 对词墨迹框重叠 —— "
              f"matplotlib 与 PIL 的字形度量存在差异，请目视核对最终图",
              file=sys.stderr)
    note_fonts(stem, (FIG_W, FIG_H),
               [("词云副标题", 9), ("方向图例", 9), ("标题", 20)] +
               [("最小词", (linfo["font_size_px"] or [10, 10])[0] * pt_per_px),
                ("最大词", (linfo["font_size_px"] or [10, 10])[1] * pt_per_px)],
               extra={"n_words_placed": linfo["placed"],
                      "n_words_skipped": drop_n,
                      "n_terms_available": linfo["n_available"],
                      "n_terms_requested": linfo["n_requested"],
                      "engine": "library"})
    made = save_fig(fig, outdir, stem, fmt, dpi, also_png, tight=False)
    plt.close(fig)
    return made


def draw_direction_clusters(plt, rows, recent_years, outdir, stem, fmt, dpi,
                            also_png=False):
    """方向聚类：**CiteSpace 式时间线**（v16.3 重写）。

    旧版是「x=方向、y=年份」的散点 + 右侧冗余条形面板。实测两个硬伤：
      ① x 轴只有 2 个刻度（成像与显微 13 条、电生理与信号 1 条）——
         单列散点退化，同一年份的 5 个点全挤在一条水平线上，
         标签靠固定 9 pt 垂直错开，互相压住；
      ② 右侧条形把"方向"编码在 y 轴上（散点却在 x 轴），两panel 语义不一致，
         而且条形与 x 刻度里已有的"(13 条)"重复。
    新版把轴**转过来**（CiteSpace 的 timeline view 做法）：
      行 = 方向（行标签自带条数），x = 年份，点大小 = 被引。
      方向再多也不会挤；带内用 adjustText 放题名（带引线，不会飘走）。
    热点簇着色只在**样本量够**时启用，样本不足的方向一律灰色并明写原因 ——
      实测 1 篇论文也会被算出"热点簇"，那种着色是噪声。
    """
    import math
    MIN_CLUSTER_N = 4        # 少于这么多篇就不做热点簇着色
    if not rows:
        # 没有数据时**也要出图**（画一张明确写"无数据"的空白图），
        #   不能 return [] 静默少一张。
        #   起因：离线/未跑 H2 时 final_list 为空 → 第③张图消失，
        #   交付物从"三张图"悄悄变成两份，读者只会以为流程漏跑了，
        #   或者更糟——以为"这个方向近年没有进展"。
        fig, ax = plt.subplots(figsize=(12, 8))
        note = "\n".join([
            f"近 {recent_years} 年窗口内没有可归类的文献", "",
            "可能原因：尚未跑阶段 D2 / H2（无综述终表），",
            "或该窗口内确实没有新论文。", "",
            "本图如实留空，不拿更早的论文冒充最新进展。",
        ])
        ax.text(0.5, 0.5, note, transform=ax.transAxes, ha="center",
                va="center", fontsize=14, color="#B03A1A")
        ax.set_title(f"{AUTHOR} 各研究方向近 {recent_years} 年论文与热点聚类",
                     fontsize=18, pad=30)
        ax.set_axis_off()
        note_fonts(stem, (12, 8), [("空数据说明", 14), ("标题", 18)],
                   extra={"empty": True})
        made = save_fig(fig, outdir, stem, fmt, dpi, also_png, tight=False)
        plt.close(fig)
        return made

    # ── 行序：按条数降序（多的在上），同数按名称 ──────────────
    cnt = Counter(r["direction"] for r in rows)
    dirs = sorted(cnt, key=lambda d: (-cnt[d], d))
    ypos = {d: i for i, d in enumerate(dirs)}
    n_row = len(dirs)

    FIG_W = 17.0
    FIG_H = max(5.0, 1.15 * n_row + 3.0)
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    ax = fig.add_axes([0.150, 0.120, 0.825, 0.760])

    # ── 带色（隔行浅底），让每行可追踪 ────────────────────────
    for d in dirs:
        if ypos[d] % 2 == 0:
            ax.axhspan(ypos[d] - 0.45, ypos[d] + 0.45, color="#F4F7FA",
                       zorder=0)

    cmap = plt.get_cmap("tab10")
    seen_cluster = {}
    texts = []
    n_grey_rows = []
    for d in dirs:
        rs = [r for r in rows if r["direction"] == d]
        clusterable = len(rs) >= MIN_CLUSTER_N
        if not clusterable:
            n_grey_rows.append(d)
        # 每行只标注被引最高的前 N 条；其余点仍在图上（不静默删点）
        topn = sorted(rs, key=lambda r: -(r.get("cited") or 0))[:4]
        label_ids = {id(r) for r in topn}
        for r in rs:
            x = int(r["year"])
            ck = r.get("hotspot") or "—"
            if clusterable:
                seen_cluster.setdefault(ck, len(seen_cluster))
                col = cmap(seen_cluster[ck] % 10)
            else:
                col = "#B8C2CC"
            ax.scatter([x], [ypos[d]],
                       s=40 + min(760, (r.get("cited") or 0) / 9),
                       c=[col], alpha=0.82, edgecolors="white",
                       linewidths=0.6, zorder=3)
            if id(r) in label_ids and r.get("title"):
                t = ax.text(x, ypos[d] + 0.34, one_line(r["title"], 42),
                            fontsize=7, color="#3B4A5A", zorder=4,
                            ha="center", va="bottom")
                texts.append(t)

    # ── 轴 ───────────────────────────────────────────────────
    ys = [int(r["year"]) for r in rows]
    lo_y, hi_y = min(ys) - 1, max(ys) + 1
    if hi_y - lo_y < 3:
        mid = (lo_y + hi_y) / 2
        lo_y, hi_y = int(mid - 1.5), int(mid + 1.5)
    ax.set_xlim(lo_y, hi_y)
    ax.set_ylim(n_row - 0.6, -0.6)                 # 第一行在最上面
    ax.set_yticks(range(n_row))
    ax.set_yticklabels([f"{d}（{cnt[d]} 条）" for d in dirs], fontsize=10)
    ax.set_xticks(range(int(lo_y) + 1, int(hi_y) + 1))
    ax.tick_params(axis="x", labelsize=10)
    ax.set_xlabel("发表年份", fontsize=12)
    ax.grid(True, axis="x", ls=":", alpha=0.45, zorder=1)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.set_title(f"{AUTHOR} 各研究方向近 {recent_years} 年论文与热点聚类",
                 fontsize=18, pad=38)
    sub = (f"行 = 研究方向（括号内为近 {recent_years} 年条数）｜ x = 发表年份 ｜ "
           f"点大小 = 被引次数")
    if seen_cluster:
        sub += f" ｜ 颜色 = 热点簇（≥{MIN_CLUSTER_N} 篇才着色）"
    ax.text(0.0, 1.015, sub, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=10, color="#555555")

    # ── 标签避让（带引线，不会飘离自己的点）──────────────────
    label_note = "未调用 adjustText"
    if texts:
        try:
            from adjustText import adjust_text
            adjust_text(texts, ax=ax,
                        expand=(1.04, 1.12), force_text=(0.10, 0.22),
                        only_move={"text": "y", "static": "y",
                                   "explode": "xy", "pull": "y"},
                        arrowprops=dict(arrowstyle="-", color="#AAB6C2",
                                        lw=0.5, shrinkA=1, shrinkB=2),
                        lim=200, precision=0.01, timeout=8)
            label_note = "adjustText 已执行"
        except Exception as ex:                               # pragma: no cover
            label_note = f"adjustText 未生效：{type(ex).__name__}: {ex}"
            print(f"[WARN] 方向聚类标签避让未生效（{label_note}）", file=sys.stderr)

    # ── 图例 ─────────────────────────────────────────────────
    from matplotlib.lines import Line2D
    handles = [
        Line2D([], [], marker="o", ls="none", markerfacecolor="#8899AA",
               markeredgecolor="white", markersize=4, label="被引 0"),
        Line2D([], [], marker="o", ls="none", markerfacecolor="#8899AA",
               markeredgecolor="white", markersize=9, label="≈ 1500"),
        Line2D([], [], marker="o", ls="none", markerfacecolor="#8899AA",
               markeredgecolor="white", markersize=14, label="≈ 7000"),
    ]
    if seen_cluster:
        from matplotlib.patches import Patch
        handles += [Patch(facecolor=cmap(i % 10), edgecolor="none", label=str(k))
                    for k, i in sorted(seen_cluster.items(), key=lambda kv: kv[1])[:8]]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=9,
              ncol=2, handlelength=1.1, handleheight=0.9, labelspacing=0.45,
              columnspacing=1.2, title="点大小 = 被引 ｜ 色块 = 热点簇",
              title_fontsize=9)

    if n_grey_rows:
        ax.text(0.0, -0.085,
                "灰色行 = 近 %d 年不足 %d 篇，样本不足、不做热点簇着色"
                "（1 篇也算出一个簇只是噪声）：%s"
                % (recent_years, MIN_CLUSTER_N, "、".join(n_grey_rows[:6])),
                transform=ax.transAxes, ha="left", va="top", fontsize=9,
                color="#8A6D3B")
    if len(rows) < 5:
        ax.text(0.5, 0.5, f"近 {recent_years} 年样本仅 {len(rows)} 条\n"
                          "样本不足，如实留空：不拿更早的论文冒充最新进展",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="#B03A1A", alpha=0.85)

    note_fonts(stem, (FIG_W, FIG_H),
               [("论文题名标注", 7), ("行标签", 10), ("图例", 9), ("标题", 18)],
               extra={"n_rows": n_row, "n_points": len(rows),
                      "n_rows_without_cluster": len(n_grey_rows),
                      "min_cluster_n": MIN_CLUSTER_N,
                      "label_avoidance": label_note,
                      "encoding": "timeline: 行=方向, x=年份"})
    made = save_fig(fig, outdir, stem, fmt, dpi, also_png, tight=False)
    plt.close(fig)
    return made


# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# 词云权重：四级语料按"可信度"加权
#   文章自带 Keywords（最可信，作者/期刊自己认定）
#     > 题名 > 摘要 > 解读卡方法描述（兜底与补充，量最大但最杂）
# 权重可用 --wc-weight-keywords / -title / -abstract / -methods 覆盖。
# ══════════════════════════════════════════════════════════════
WC_WEIGHTS = {"keywords": 3, "title": 2, "abstract": 2, "methods": 1}


def compute_wordcloud_weights(cards, recs, vocab, doi_dir,
                              weights=None, lang="both", min_docs=WC_MIN_DOCS):
    """词云权重：关键词优先 + 全局邻接熵筛词 + 单位/受控标签剔除。

    `cards`  —— doi → 解读卡
    `recs`   —— doi → 题录（本轮起用于取**题名**；旧版收了这个参数却没用）
    `lang`   —— both（默认）｜ zh 只出中文词 ｜ en 只出英文词

    语料优先级（用户指定）：
      ① 文章自带 Keywords（权重 3）—— 作者/期刊自己认定的词，最可信
      ② 题名（权重 2）            —— 结构化来源，稳定可得
      ③ 摘要（权重 2）            —— **仅当该篇既无关键词也取不到题名时**才用。
         摘要与"方法描述"同属**散文**，把它整篇切词上云正是"大量无用信息"
         的来源（实测把它加进来后，英文词型从 200 级暴涨到 1600 级，
         云被 neuronal/synaptic/activity 这类通用词占满）。故降级为兜底。
      ④ 解读卡方法描述（权重 1）—— 量最大也最杂，作补充，保住中文方法词

    中文门控必须**全局**统计：邻接熵与频次都依赖整个语料的分布，
      逐篇统计会让 3 次以下的真词（海马、谷氨酸）与碎片一起被误杀。
    """
    weights = dict(WC_WEIGHTS, **(weights or {}))
    allowed_p = vocab.get("paradigm_tags_vocabulary") or []
    aliases = vocab.get("aliases") or {}
    al_p = {str(k).lower(): v for k, v in (aliases.get("_范式别名") or {}).items()}

    kw_by_doi, kw_notes = load_article_keywords()
    abs_by_doi = load_json(CDPDIR / "abstracts_openalex.json", {}) or {}

    # ── 第一遍：收集语料 ──────────────────────────────────────
    corpora = []            # (doi, 方向, 来源, 权重, 文本)
    paradigms = Counter()
    for doi, c in cards.items():
        d = doi_dir.get(doi) or "未标注方向"
        # 范式标签：**不再切词上云**（15 个受控标签，见 PARADIGM_EN），
        #   改为单独统计进图注与报告 —— 不是静默丢弃。
        for p in as_list(c.get("paradigm_tags")):
            p = normalize_tag(p, allowed_p, al_p)
            if p:
                paradigms[p] += 1

        kws = kw_by_doi.get(doi) or []
        title = str((recs.get(doi) or {}).get("title") or "").strip()
        if kws:
            corpora.append((doi, d, "keywords", weights["keywords"], " ; ".join(kws)))
        if title:
            corpora.append((doi, d, "title", weights["title"], title))
        elif not kws:
            abstract = str((abs_by_doi.get(doi) or {}).get("abstract") or "").strip()
            if abstract:
                corpora.append((doi, d, "abstract", weights["abstract"], abstract))
        mtext = " ".join(str(m) for m in as_list(c.get("methods")))
        if mtext:
            corpora.append((doi, d, "methods", weights["methods"], mtext))

    # ── 第二遍：中文候选词走全局门控 ──────────────────────────
    keep_zh, zh_info = zh_terms(" ".join(t for *_, t in corpora))

    w = Counter()
    term_dir = defaultdict(Counter)     # 词 → {方向: 次数}
    term_src = defaultdict(Counter)     # 词 → {来源: 次数}
    term_doi = defaultdict(set)         # 词 → {出现在哪些论文}
    dropped = Counter()
    drop_reason = defaultdict(Counter)
    src_hits = Counter()

    def _drop(tok, why, n=1):
        dropped[tok] += n
        drop_reason[tok][why] += n

    # 中文词表门（旧版只在 tokenize 后做过 MISLEADING/MISLEADING_ZH；
    #   本版新增 GENERIC_ZH，并把二者统一到全局门之后）
    for g in list(keep_zh):
        if g in MISLEADING_ZH:
            _drop(g, "泛化/载体词")
            del keep_zh[g]
        elif g in GENERIC_ZH:
            _drop(g, "词云泛化词")
            del keep_zh[g]

    if lang in ("both", "zh"):
        for g, st in zh_info.items():
            if not st["keep"]:
                _drop(g, st.get("why") or "跨词碎片")
    if lang in ("both", "en"):
        raw_en = set()
        for *_, text in corpora:
            raw_en |= {x.strip("-") for x in _EN_TOK.findall(text.lower())
                       if len(x.strip("-")) >= WC_EN_MIN_LEN}
        for t in raw_en - set(en_terms(" ".join(x for *_, x in corpora))):
            if t in STOP:
                continue
            _drop(t, "受控范式标签" if t in PARADIGM_EN else
                  ("单位/试剂/编号" if (t in UNIT_ABBR or re.fullmatch(r"[a-z]\d{1,3}", t)) else
                   ("来源噪声词" if t in GENERIC_EN else "泛化/停用")))

    for doi, d, src, wt, text in corpora:
        if lang in ("both", "zh"):
            hits = zh_grams(text) & keep_zh.keys()
            for g in hits:
                w[g] += wt
                term_dir[g][d] += 1
                term_src[g][src] += 1
                term_doi[g].add(doi)
            if hits:
                src_hits[src] += len(hits)
        if lang in ("both", "en"):
            hits = set(en_terms(text))
            for t in hits:
                w[t] += wt
                term_dir[t][d] += 1
                term_src[t][src] += 1
                term_doi[t].add(doi)
            if hits:
                src_hits[src] += len(hits)
    color_of, dir_label = {}, {}
    palette = ["#1B3A6B", "#E4572E", "#2E86AB", "#7D3C98", "#117A65",
               "#B9770E", "#943126", "#1A5276"]
    # ── 方法描述来源的"单篇孤词"过滤（见 WC_MIN_DOCS 说明）────────
    #   判据用**论文数**（term_doi），不是方向数 —— 方向只有两三个，
    #   用方向数会把中文词几乎全杀掉（实测入云词型从 1449 掉到 159）。
    curated = ("keywords", "title", "abstract")
    for t in list(w):
        if len(term_doi[t]) >= min_docs:
            continue
        if any(src in curated for src in term_src[t]):
            continue
        _drop(t, "仅单篇孤词", 1)
        del w[t]
        del term_dir[t]
        del term_src[t]
        term_doi.pop(t, None)
    dirs = sorted({d for _, c in term_dir.items() for d in c})
    for i, d in enumerate(dirs):
        dir_label[d] = palette[i % len(palette)]
    for tok, c in term_dir.items():
        color_of[tok] = dir_label[c.most_common(1)[0][0]]

    # 候选碎片是全量 2–4 gram（实测近 2 万条），**不能全量落盘**：
    #   上一版这样写把 graph_data.json 从 254 KB 顶到 8.8 MB。
    #   这里按出现次数封顶，并显式记下"被封顶的总数"，不静默截断。
    frag_all = {g: st for g, st in zh_info.items() if not st["keep"]}
    frag_keep = sorted(frag_all.items(), key=lambda x: -x[1]["n"])[:80]
    reason_keep = {t: dict(drop_reason[t]) for t, _ in dropped.most_common(80)
                   if t in drop_reason}
    stats = {
        "weights": weights,
        "lang": lang,
        "n_cards": len(cards),
        "keywords_used": {k: v for k, v in kw_by_doi.items()},
        "keywords_notes": kw_notes,
        "keywords_coverage": f"{len(kw_by_doi)}/{len(cards)}",
        "paradigms": dict(paradigms.most_common()),
        "source_terms": dict(src_hits.most_common()),
        "term_source": {t: dict(term_src[t]) for t in w},
        "thresholds": {"min_count": WC_MIN_COUNT, "min_entropy": WC_MIN_ENTROPY,
                       "max_n": WC_MAX_N, "min_docs": min_docs,
                       "font_floor_pt": WC_FLOOR_PT},
        "n_terms_kept": len(w),
        "n_dropped_types": len(dropped),
        "n_dropped_hits": sum(dropped.values()),
        "n_fragment_candidates": len(frag_all),
        "fragments": dict(frag_keep),
        "fragments_truncated": f"仅列出现次数最高的 80/{len(frag_all)} 条",
        "drop_reasons": reason_keep,
        "drop_reasons_truncated": f"仅列出现次数最高的 80/{len(dropped)} 条",
    }
    return w, color_of, dir_label, dropped, stats


def direction_profiles_from_cards(cards, vocab):
    """从作者解读卡里学出「方向 → 关键词画像」，供方向归类兜底使用。"""
    allowed_d = vocab.get("direction_tags_vocabulary") or []
    aliases = vocab.get("aliases") or {}
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}
    prof = defaultdict(Counter)
    for c in cards.values():
        tags = [normalize_tag(t, allowed_d, al_d)
                for t in as_list(c.get("direction_tags"))]
        tags = [t for t in tags if t]
        if not tags:
            continue
        # 画像语料 = methods + key_20（方法与关键信息）。只用 methods 时画像太窄：
        #   实测某化学语料下 506 条参考文献里仅 40 条带 review 卡，其余走关键词兜底
        #   几乎全落到「未分组」，方向聚类图 x 轴只剩一个刻度。补 key_20 后
        #   画像覆盖到研究对象/体系词，兜底才有意义。
        blob = " ".join(map(str, as_list(c.get("methods")) +
                            as_list(c.get("key_20"))))
        for tok in set(tokenize(blob)):
            if tok in MISLEADING or tok in MISLEADING_ZH:
                continue
            # v7.4：计入该卡的**全部**方向标签，而不是只计 tags[0]。
            #   旧版只喂第一个标签 —— 而多数卡的第一标签是「材料与结构」，
            #   于是它的画像被撑得极宽，成为"泛词通吃"的根源。
            for t in tags:
                prof[t][tok] += 1
    # v7.4：丢掉**跨方向普遍存在**的词（df 超过方向数一半）——
    #   这类词（active / reaction / properties / stability）在任何方向都出现，
    #   对判别毫无贡献，却会把命中数堆高，让宽画像获胜。
    _nd = max(1, len(prof))
    _df = Counter()
    for _c in prof.values():
        for _t in _c:
            _df[_t] += 1
    _cut = max(1, _nd // 2)
    for _d in list(prof):
        for _t in [t for t in prof[_d] if _df[t] > _cut]:
            del prof[_d][_t]
    return prof


def assign_direction(text, rcard, prof, allowed_d, al_d, min_hit=2):
    """给一条文献定方向。

    ① **首选**：该文献的 review 卡里的 `direction_tags`（人工/agent 标注，最可信）；
    ② **兜底**：题名与该方向的**关键词画像**（来自作者解读卡的方法描述）重合 ≥ min_hit。
       兜底是**启发式**，报告里会写明用了哪条路径、命中了几个词。

    为什么不直接用期刊名或"最近的方向"：那是**编**一个归属，
      比"未分组"更糟 —— 读者会当成事实。
    """
    tags = [normalize_tag(t, allowed_d, al_d)
            for t in as_list((rcard or {}).get("direction_tags"))]
    tags = [t for t in tags if t]
    if tags:
        return tags[0], "review 卡标注"
    toks = sorted({t for t in tokenize(str(text or ""))
                   if t not in MISLEADING and t not in MISLEADING_ZH})
    # v7.4：按 **IDF 加权**排序，消除「宽画像通吃」。
    #   旧版取**命中词数**最大者 —— 「材料与结构」的画像词覆盖面最广，
    #   逐词计数天然最大，实测 5 年窗口下 44 条里 41 条被它吸走，
    #   x 轴只剩一个有效刻度、图失去对比性。
    #   改为对每个命中词按其**跨方向稀有度** log(1 + N_dirs/df) 加权：
    #   只在少数方向画像里出现的词才是判别性证据，泛词几乎不加分。
    #   `min_hit` 退化为"至少命中几个词"的下限，排序不再由它决定。
    import math as _math
    _N = max(1, len(prof))
    _df = Counter()
    for _d, _cnt in prof.items():
        for _t in _cnt:
            _df[_t] += 1
    #   还要**按画像规模归一化**：命中词的绝对权重和会随画像变大而单调增长，
    #     不归一化等于"谁的画像大谁赢"（长文档偏置）。除以 sqrt(画像总权重)
    #     后，比较的才是"这条文献与哪个方向的**密度**更接近"。
    _mass = {d: sum(_math.log(1.0 + _N / max(1, _df[t])) for t in cnt)
             for d, cnt in prof.items()}
    best, best_score, best_shared = "", 0.0, []
    for d, cnt in prof.items():
        shared = [t for t in toks if cnt.get(t)]
        if not shared:
            continue
        score = (sum(_math.log(1.0 + _N / max(1, _df[t])) for t in shared)
                 / _math.sqrt(max(1e-9, _mass.get(d, 0.0))))
        if score > best_score:
            best, best_score, best_shared = d, score, shared
    if best and len(best_shared) >= max(1, min_hit):
        return best, f"关键词兜底（命中 {len(best_shared)} 词：{'、'.join(best_shared[:4])}）"
    return "未分组", "无法归类"


def compute_direction_clusters(final, rcards, rmeta, recs, recent_years, vocab,
                               cards=None):
    """方向聚类：取每个方向**近 N 年**的文献，按方向内关键词共现分热点簇。"""
    import datetime
    this_year = datetime.date.today().year
    lo = this_year - recent_years + 1

    allowed_d = vocab.get("direction_tags_vocabulary") or []
    aliases = vocab.get("aliases") or {}
    al_d = {str(k).lower(): v for k, v in (aliases.get("_方向别名") or {}).items()}
    prof = direction_profiles_from_cards(cards or {}, vocab)

    # v7.4：新增判据 ②「施引的作者论文方向」。
    #   起因：5 年窗口下实测 44 条里 41 条都被关键词兜底塞进「材料与结构」——
    #   该方向画像词覆盖面最广，逐词命中数天然最大，于是**赢者通吃**，
    #   图上 x 轴只剩一个有效刻度，完全没有对比性。
    #   而 final_list 的每条都带 `cited_by_sources`（引用了它的作者论文 DOI），
    #   作者论文的方向标签来自解读卡（受控词表）—— 这才是
    #   「这条文献属于作者的哪个方向」的**直接证据**，优先级高于关键词兜底。
    src_dir: dict[str, list[str]] = {}
    for _doi, _c in (cards or {}).items():
        _t = [normalize_tag(t, allowed_d, al_d)
              for t in as_list(_c.get("direction_tags"))]
        _t = [t for t in _t if t]
        if _t:
            src_dir[str(_doi).lower()] = _t

    rows, hs = [], defaultdict(Counter)
    how = Counter()
    for x in (final.get("items") or []):
        y = x.get("year")
        if not str(y or "").isdigit() or int(y) < lo:
            continue
        c = rcards.get(str(x.get("id") or "")) or {}
        # 关键词兜底的语料不能只有**题名**：题名通常只有 8–15 个词，
        #   与画像重合 ≥2 的概率极低 —— 实测 506 条里绝大多数因此落到
        #   「未分组」，x 轴只剩一个刻度、图失去意义。改为「题名 + 摘要 + 刊名」，
        #   兜底命中率才有统计意义；返回的 `why` 会写明走的是哪条路径与命中词，
        #   报告里逐条可复核（仍然**不**按期刊名硬编方向）。
        blob = " ".join([str(x.get("title") or ""),
                         str(x.get("abstract") or ""),
                         str(x.get("venue") or "")])
        d, why = assign_direction(blob, c, prof, allowed_d, al_d)
        # 判据 ②：review 卡没标注时，看**引用了它的作者论文**落在哪些方向
        if why != "review 卡标注":
            votes = Counter()
            for s in as_list(x.get("cited_by_sources")):
                k = str(s).lower().replace("https://doi.org/", "").strip()
                for t in src_dir.get(k, []):
                    votes[t] += 1
            if votes:
                top, n = votes.most_common(1)[0]
                d, why = top, f"施引论文方向（{n} 票）"
        how["review 卡标注" if why == "review 卡标注" else
            ("施引论文方向" if why.startswith("施引论文方向") else
             ("关键词兜底" if why.startswith("关键词兜底") else "无法归类"))] += 1
        toks = [t for t in tokenize(str(x.get("title") or ""))
                if t not in MISLEADING and t not in MISLEADING_ZH]
        rows.append({"direction": d, "year": int(y),
                     "title": x.get("title") or "",
                     "cited": x.get("cited_by_count") or 0,
                     "id": x.get("id") or x.get("doi") or "",
                     "assign": why,
                     "tokens": toks[:14]})
        hs[d].update(toks)
    # 热点簇标签 = 该方向近 N 年最高频的关键词（数据驱动，不做语义命名）
    for r in rows:
        d = r["direction"]
        cand = [t for t in r["tokens"] if t in dict(hs[d].most_common(8))]
        r["hotspot"] = cand[0] if cand else "其他"
    return rows, {d: [t for t, _ in c.most_common(6)] for d, c in hs.items()}, how


# ══════════════════════════════════════════════════════════════
def build_report(overview_notes, memory_links, contradictions, wc_dropped,
                 hotspot_words, rows, graphs, pinfo, opts, wc_stats=None) -> str:
    L = []
    L.append(f"# {AUTHOR} 知识图谱\n")
    L.append(f"> 作者：{AUTHOR}" + (f"（{AFFIL}）" if AFFIL else ""))
    if ORCID:
        L.append(f"> ORCID：https://orcid.org/{ORCID}")
    L.append("> 生成位置：**所有搜集工作完成后、PPT 制作之前**（阶段 K）")
    L.append("> 数据来源：解读卡 `interpret/results/` + 综述终表 `refs/final_list.json` "
             "+ review 卡 `refs/results/`；**全程无网络请求**\n")

    L.append("## 一、产出图件\n")
    L.append("| 图 | 文件 | 说明 |")
    L.append("|---|---|---|")
    names = {"overview": "① overview", "wordcloud": "② 词云", "directions": "③ 方向聚类"}
    desc = {
        "overview": "**径向整齐树**（igraph layout_reingold_tilford_circular）：作者在圆心 → "
                    "阶段（扇区）→ 方向 → 综述（橙点编号，完整题名见右侧清单）；"
                    "方法词另置外环，记忆连接沿环画弧",
        "wordcloud": "以作者为中心；颜色区分研究方向；取词按「文章关键词 > 题名 > 摘要 > "
                     "解读卡方法描述」加权，中文用左右邻接熵挡跨词碎片",
        "directions": "**时间线**：行 = 方向（括号内条数），x = 年份，点大小=被引，"
                      "颜色=热点簇（样本不足的行不着色）",
    }
    for k, files in graphs.items():
        fl = "、".join(f"`{pathlib.Path(f).name}`" for f in files) or "（未产出）"
        L.append(f"| {names.get(k, k)} | {fl} | {desc.get(k, '')} |")
    L.append("")
    L.append("> **格式**：SVG 优先（矢量、可无损缩放），SVG 失败自动退 PNG "
             f"（{opts['dpi']} dpi）。两者**背景均为透明**。")
    L.append("")

    L.append("## 二、overview · 聚类解读（「这一簇在讲什么？」）\n")
    _ol = OVERVIEW_LAYOUT or {}
    if _ol.get("meta"):
        _m = _ol["meta"]
        _secs = _m.get("sectors") or []
        L.append(f"> **布局**：径向整齐树（`igraph layout_reingold_tilford_circular`）——"
                 f"树节点 {_m.get('n_tree_nodes')}，方法外环 "
                 f"{_m.get('n_method_nodes_ring')}/{_m.get('n_method_nodes_total')}，"
                 f"弧线 {_ol.get('n_arcs_drawn')} 条。"
                 f"标签避让：{_ol.get('label_avoidance')}。")
        if _secs:
            L.append("> **阶段扇区**：" + "；".join(
                f"{s['label']} 占 {s['width']:.0f}°" for s in _secs))
        L.append(f"> 节点坐标已落盘 `graph_data.json` 的 `overview_layout.pos`"
                 f"（{len(_ol.get('pos') or {})} 个节点）—— 布局可事后审计，"
                 f"不靠注释声称。")
        L.append("")
    if overview_notes:
        for cid in sorted(overview_notes):
            L.append(f"- **簇 {cid}**：{overview_notes[cid]}")
    else:
        L.append("> 未得到聚类（networkx 不可用或图为空）。")
    L.append("")

    L.append("## 三、记忆连接（两条方法为何要一起读）\n")
    L.append("> 判据：两条方法**在多篇文献中共同出现**。原因由数据生成"
             "（共现篇数 + 共同范式 + 涉及篇目），**不是人为编的理由**。\n")
    if memory_links:
        L.append("| # | 关系名 | 共现篇数 | 原因 | 涉及篇目 |")
        L.append("|---|---|---|---|---|")
        for i, m in enumerate(memory_links, 1):
            L.append(f"| {i} | {m['name']} | {m['n']} | {m['reason']} | "
                     + "、".join(f"`{d}`" for d in m["shared"][:3]) + " |")
    else:
        L.append("> 本案例未发现达到共现阈值的方法对"
                 "（阈值：同一对方法在 ≥2 篇论文中共同出现）。")
    L.append("")

    L.append("## 四、自相矛盾线索（「哪些地方在自相矛盾？」）\n")
    L.append("> [WARN] **这些是待人工复核的线索，不是已确认的矛盾。**")
    L.append("> 判据：同一研究方向内，**一篇的「待验证问题」与另一篇的「已验证结论」"
             "高度重合**（重合度 ≥ 0.30 且共同词 ≥ 4）—— 即一篇说「还没解决」、"
             "另一篇说「已经证实」。这类冲突值得回原文核对，"
             "也可能是**用了同一个词但指的不是同一件事**。\n")
    if contradictions:
        L.append("| # | 方向 | 文献 A（提出未解决问题） | 文献 B（声称已验证） | 重合度 | 共同词 |")
        L.append("|---|---|---|---|---|---|")
        for i, c in enumerate(contradictions, 1):
            L.append(f"| {i} | {c['direction']} | {one_line(c['a'], 44)} | "
                     f"{one_line(c['b'], 44)} | {c['ratio']} | "
                     + "、".join(c["shared"][:5]) + " |")
    else:
        L.append("> 未检出达到阈值的冲突线索。**这不等于「没有矛盾」** —— "
                 "本判据只覆盖「未解决问题 vs 已验证结论」这一类文字级信号。")
    L.append("")

    L.append("## 五、词云取词与剔除记录（**如实列出被剔的词**）\n")
    _ws = wc_stats or {}
    if _ws:
        L.append(f"> **取词口径**（v16.2）：文章关键词（权重 "
                 f"{_ws['weights'].get('keywords')}）＞ 题名（{_ws['weights'].get('title')}）"
                 f"＞ 摘要（{_ws['weights'].get('abstract')}，仅在既无关键词也无题名时用）"
                 f"＞ 解读卡方法描述（{_ws['weights'].get('methods')}）。"
                 f"语言口径 `{_ws.get('lang')}`。")
        L.append(f"> **文章关键词采纳**：{_ws.get('keywords_coverage', '?')} 篇取到并采纳。"
                 "关键词段解析不干净时**整份丢弃**（宁可少一个来源，也不让散文上云）。")
        L.append(f"> **范式标签不再切词上云**（它们是 15 个受控标签，描述不了作者），"
                 f"改为在此单列：{_ws.get('paradigms') or '（无）'}。")
        L.append(f"> **中文筛词**：左右邻接熵 ≥ {_ws['thresholds']['min_entropy']} bit "
                 f"且频次 ≥ {_ws['thresholds']['min_count']} 才保留，"
                 f"候选词长 {_ws['thresholds']['max_n']} 字以内 —— "
                 "用来挡「小鼠海马→鼠海」这类跨词边界碎片。")
        L.append(f"> **来源贡献的词型数**：{_ws.get('source_terms') or '（无）'}。")
        L.append("")
        notes = _ws.get("keywords_notes") or []
        if notes:
            L.append("### 逐篇关键词采纳情况\n")
            L.append("| DOI | 来源 | 原因 | 采纳的词 |")
            L.append("|---|---|---|---|")
            for n in notes:
                kw = "、".join(n.get("keywords") or []) or "—"
                L.append(f"| `{n.get('doi')}` | {n.get('source')} | {n.get('why')} | "
                         f"{one_line(kw, 70)} |")
            L.append("")
        frag = _ws.get("fragments") or {}
        if frag:
            sample = sorted(frag.items(), key=lambda x: -x[1]["n"])[:12]
            L.append("### 被判为「跨词碎片 / 虚词边界」的中文候选（前 12）\n")
            L.append("| 候选 | 次数 | 左邻接熵 | 右邻接熵 | 左邻样例 | 判定 |")
            L.append("|---|---|---|---|---|---|")
            for g, st in sample:
                L.append(f"| {g} | {st['n']} | {st['h_left']} | {st['h_right']} | "
                         f"{'、'.join(str(c) for c, _ in st['left'][:3])} | "
                         f"{st.get('why') or '—'} |")
            L.append("")
    L.append("> 剔除的是**载体词 / 泛化词 / 单位与试剂缩写 / 受控范式标签 / 跨词碎片** —— "
             "它们要么描述不了作者，要么根本不是词。\n")
    _wci = (FIG_NOTES.get(f"{AUTHOR}_知识图谱_词云") or {}).get("wordcloud_info") or {}
    if _wci:
        _eng = ("`wordcloud`（amueller）占位检测 —— 只借它**算布局**，"
                "绘制仍走 matplotlib（标题/图例/矢量 SVG 因此保留）"
                if _wci.get("canvas_px") and _wci.get("random_state") is not None
                else "自研螺旋 + 矩形碰撞")
        L.append(f"> **布局引擎**：{_eng}")
        if _wci.get("canvas_px"):
            L.append(f"> **放置情况**：候选 {_wci.get('n_terms_available')} 个词型"
                     f"（见上）→ 按权重取前 {_wci.get('n_terms_requested')} 个 → "
                     f"**放下 {_wci.get('placed')} 个**"
                     + (f"，**未放下 {_wci.get('dropped_n')} 个**"
                        f"（{'、'.join(map(str, (_wci.get('dropped_head') or [])[:10]))} …）"
                        if _wci.get("dropped_n") else "，全部放下")
                     + "。")
            L.append(f"> 布局画布 {_wci.get('canvas_px')} px @ "
                     f"{_wci.get('pixel_per_inch')} px/in；`random_state="
                     f"{_wci.get('random_state')}`（固定 → 可复现）；"
                     f"`relative_scaling={_wci.get('relative_scaling')}`；"
                     f"字号 {_wci.get('font_size_px')} px。")
            L.append(f"> 绑定字体文件：`{_wci.get('font_file')}`（{_wci.get('font_name')}）"
                     "—— **两边必须锁同一个文件**，否则复刻出来会压字（见版本变动 v17.2）。")
            if _wci.get("replay_ink_overlaps"):
                L.append(f"> [WARN] 复刻后有 **{_wci['replay_ink_overlaps']} 对**词墨迹框"
                         "重叠（matplotlib 与 PIL 的字形度量存在细微差异）—— "
                         "**请目视核对最终图**；数量远大于个位数时说明映射出了问题。")
            if _wci.get("font_missing"):
                L.append(f"> [WARN] {_wci['font_missing']}")
        L.append("")
    if wc_dropped:
        top = "、".join(f"{w}({n})" for w, n in wc_dropped.most_common(30))
        L.append(f"被剔除的高频词（前 30）：{top}")
        L.append("")
        L.append(f"（共剔除 {len(wc_dropped)} 个**候选词型**、{sum(wc_dropped.values())} 次出现；"
                 f"其中入云的只有 {(_ws.get('n_terms_kept') or 0)} 个词型 —— "
                 f"被剔的绝大多数是「只出现 1–2 次的碎片/短词」，"
                 f"它们本就不会进云，列出来是为了口径可核）")
        reasons = (_ws.get("drop_reasons") or {})
        bucket = Counter()
        for _t, _rs in reasons.items():
            for _why, _n in _rs.items():
                bucket[_why] += 1
        if bucket:
            L.append("")
            L.append("按原因归类（词型数）：" +
                     "、".join(f"{k} {v}" for k, v in bucket.most_common()))
    else:
        L.append("> 无语料或未触发剔除。")
    L.append("")

    L.append("## 六、方向聚类 · 近 N 年热点\n")
    if hotspot_words:
        L.append("| 方向 | 近 N 年热点关键词（按频次，数据驱动，未做语义命名） | 样本数 |")
        L.append("|---|---|---|")
        cnt = Counter(r["direction"] for r in rows)
        for d, ws in sorted(hotspot_words.items(), key=lambda x: -cnt.get(x[0], 0)):
            L.append(f"| {d} | {'、'.join(ws)} | {cnt.get(d, 0)} |")
    else:
        L.append(f"> 近 {opts['recent_years']} 年窗口内没有足够的文献，"
                 "**如实留空**（不拿更早的论文冒充「最新进展」）。")
    L.append("")

    L.append("## 七、阶段划分说明\n")
    if pinfo["source"] == "config":
        L.append("阶段分组来自 `.work_cdp/periods.json`（**用户/流程配置**）。")
    elif pinfo["source"] == "auto":
        L.append("> [WARN] 阶段分组是**按年份等分的算法分段**（`.work_cdp/periods.json` 不存在），"
                 "**不代表作者真实履历**（博士期/博后期/建组期）。"
                 "要得到有意义的阶段划分，请配置 `periods.json`。")
        L.append("")
        for lab, (lo, hi) in pinfo["periods"].items():
            L.append(f"- {lab}")
    else:
        L.append("> 题录缺少年份，未做阶段划分。")
    L.append("")

    L.append("## 八、限制与未做之事\n")
    L.append("- 图谱的**节点与边全部来自流程已有产物**，没有引入外部知识；"
             "因此图上没有出现的关系不代表不存在。")
    L.append("- 方法层的节点由**方法描述文本分词**得到，"
             "英文取单词、中文取 2-gram —— 会有同义词未合并的情况，"
             "报告已如实列出高频词，便于人工判断。")
    L.append("- **词云与图谱的分词口径不同**（这是刻意的）："
             "图谱/方向画像沿用 `tokenize()` 的中文 2-gram（与 `interpret_build.py` 同口径，"
             "改它会漂移方向画像与冲突检出）；"
             "词云另走「关键词优先 + 左邻接熵筛词」，并为可读性扩到 2–4 字。"
             "两者**不应互相对齐**，也不要用词云的词去解释图谱的簇。")
    L.append("- 词云的邻接熵筛词仍会漏掉个别**粘着语素**（如「氨酸」这类"
             "只作后缀的字组），它们读起来不成词但也不是跨词边界碎片；"
             "如需剔除请加入 `GENERIC_ZH`。")
    L.append("- 文章关键词来自出版方网页/PDF 文本的 Keywords 段，"
             "**解析不干净就整份丢弃**（见 §五 的逐篇表）；"
             "因此关键词覆盖率偏低是**如实反映**，不是漏跑。"
             "若解读卡自带 `keywords` 字段，则优先用它。")
    L.append("- 热点簇的标签取该方向近 N 年**最高频关键词**，"
             "是**数据驱动的命名**，不是领域共识的热点名称。")
    L.append("")

    # ── 九、字号自述（放到目标尺寸后还能不能读）──────────────
    L.append("## 九、字号自述（这批图放到目标尺寸后还能不能读）\n")
    L.append(f"> 本流程的图都画在**超大画布**（12–17 in）上，用的是**绝对字号**；"
             f"能不能读取决于**最终摆放尺寸**。下表按线性比例换算，"
             f"下限取 **{WC_FLOOR_PT:g} pt**。低于下限只报 WARN —— "
             "幻灯片场景通常是够的，只有放进论文/报告排版才会踩线。\n")
    if FIG_NOTES:
        L.append("| 图 | 画布 (in) | 最小字号条目 | " +
                 " | ".join(f"{t}" for t in FONT_TARGETS) + " |")
        L.append("|---|---|---|" + "---|" * len(FONT_TARGETS))
        for stem, rec in FIG_NOTES.items():
            eff = rec.get("effective_pt") or {}
            cells = []
            for t in FONT_TARGETS:
                v = eff.get(t) or {}
                mark = " [WARN]" if (v.get("min_pt") is not None
                                     and v["min_pt"] < WC_FLOOR_PT) else ""
                cells.append(f"{v.get('min_pt', '—')} pt（{v.get('min_what', '—')}）{mark}")
            what = "—"
            if eff:
                w0 = min(eff.values(), key=lambda v: v["min_pt"])
                what = f"{w0['min_what']}（{w0['min_pt']} pt @ {list(FONT_TARGETS)[0]}）"
            L.append(f"| {stem.split('_')[-1]} | {rec.get('canvas_in')} | {what} | "
                     + " | ".join(cells) + " |")
        L.append("")
        warn = {k: v.get("below_floor") for k, v in FIG_NOTES.items()
                if v.get("below_floor")}
        if warn:
            L.append("> [WARN] 以下条目在对应目标尺寸下低于下限，"
                     "**要放进该尺寸就必须放大字号或缩小画布**：\n")
            for k, v in warn.items():
                L.append(f"> - {k}：" + "；".join(v))
            L.append("")
        else:
            L.append("> 所有条目在全部目标尺寸下均不低于下限。")
            L.append("")
        deg = {k: v["degraded_to_png"] for k, v in FIG_NOTES.items()
               if v.get("degraded_to_png")}
        if deg:
            L.append("> [WARN] 这些图发生了 **SVG→PNG 降级**（交付物从可无损缩放变成"
                     "固定分辨率栅格，PPT 用没问题，但要矢量就只能修 SVG 导出）：\n")
            for k, v in deg.items():
                L.append(f"> - {k}：{v}")
            L.append("")
        else:
            L.append("> 未发生 SVG→PNG 降级。")
            L.append("")
    else:
        L.append("> 未产出图件，无字号记录。")
        L.append("")
    return "\n".join(L)


def self_test() -> int:
    bad = 0
    # ① 误导性词必须被挡住，方法词必须留下
    for w in ("mice", "mouse", "patients", "samples", "vitro"):
        if w not in MISLEADING:
            bad += 1
            print(f"  误配：{w!r} 应在 MISLEADING 中")
    for w in ("microscopy", "cryo", "dft", "transcriptomic"):
        if w in MISLEADING or w in STOP:
            bad += 1
            print(f"  误配：{w!r} 是方法词，不应被剔除")
    print(f"误导性词表：{'通过' if bad == 0 else '失败'}")

    # ② 分词与 interpret_build 口径一致（CD8/DNA 这类必须留住）
    toks = tokenize("CD8 DNA mice microscopy Th17")
    for must in ("cd8", "dna", "microscopy", "th17"):
        if must not in toks:
            bad += 1
            print(f"  分词丢了 {must!r}，得到 {toks}")
    print("分词口径：通过" if bad == 0 else "分词口径：失败")

    # ③ 阶段划分：无配置时按年份等分，且覆盖全部年份
    recs = {f"d{i}": {"year": y}
            for i, y in enumerate([2005, 2008, 2012, 2016, 2019, 2023])}
    pi = periods_of(recs)
    assert pi["source"] == "auto", pi
    def lab(y):
        for l, (lo, hi) in pi["periods"].items():
            if lo <= y <= hi:
                return l
        return None
    for y in (2005, 2008, 2012, 2016, 2019, 2023):
        if lab(y) is None:
            bad += 1
            print(f"  年份 {y} 未被任何阶段覆盖：{pi['periods']}")
    print("阶段划分：通过" if bad == 0 else "阶段划分：失败")

    # ④ 冲突检出：A 的 open_questions 与 B 的 verified_conclusions 重合
    #    用例必须真的越过阈值（共同词 ≥4 且重合度 ≥0.30）——
    #      否则测的是"阈值没被触发"，而不是"检出逻辑对不对"。
    cards = {
        "a": {"open_questions": [
                  "the proton transfer mechanism in porous catalyst remains unresolved"],
              "verified_conclusions": []},
        "b": {"open_questions": [],
              "verified_conclusions": [
                  "porous catalyst proton transfer mechanism resolved by spectroscopy"]},
    }
    sig = find_contradictions(cards, {"a": "催化与反应", "b": "催化与反应"},
                              {"a": {"title": "A"}, "b": {"title": "B"}})
    if not sig:
        bad += 1
        print("  未检出构造的冲突线索")
    else:
        print(f"冲突检出：通过（重合度 {sig[0]['ratio']}，共同词 "
              f"{sig[0]['shared'][:4]}）")

    # ⑤ 导出格式分支
    fmt_bad = 0
    for fmt, want in (("auto", (True, True)), ("svg", (True, False)),
                      ("png", (False, True))):
        ws, wp = fmt in ("auto", "svg"), fmt in ("auto", "png")
        if (ws, wp) != want:
            fmt_bad += 1
            print(f"  format={fmt} 分支错误")
    bad += fmt_bad
    print("导出格式分支：通过" if fmt_bad == 0 else "导出格式分支：失败")

    # ⑥ 记忆连接：共现 ≥2 才建边，且原因必须由数据生成（含篇数）
    nodes = {}
    mw = {"alpha": {"d1", "d2"}, "beta": {"d1", "d2"}, "gamma": {"d3"}}
    n = len(mw["alpha"] & mw["beta"])
    mem_bad = 0
    if n < 2:
        mem_bad += 1
        print("  共现计数错误")
    bad += mem_bad
    print("记忆连接判据：通过" if mem_bad == 0 else "记忆连接判据：失败")

    # ⑦ 中文邻接熵筛词：碎片必须被挡，真词必须留下。
    #    语料刻意复刻用户实测的两类边界：
    #      「触前」左邻恒为「突」、「经元」左邻恒为「神」、
    #      「谷氨」右邻恒为「酸」、「鼠海」右邻恒为「马」 → 全部是碎片；
    #      而 海马/突触/神经元/谷氨酸/突触前/成像 的左右邻居都杂 → 真词。
    zh_text = (
        "取大鼠海马切片。取小鼠海马神经元。在体海马网络。与海马区的突触。的海马电活动。"
        "的突触前膜。与突触后的密度。在突触可塑性。成突触传递。化突触结构。"
        "记录神经元的形态。培养神经元的方法。观察神经元的活动。的神经元网络。与神经元连接。"
        "释放谷氨酸的过程。的谷氨酸受体。测谷氨酸浓度。与谷氨酸相关。于谷氨酸能系统。"
        "的突触前膜。与突触前钙。在突触前囊泡。"
        "荧光成像系统。的成像参数。与成像分辨率。在成像条件下。"
        "谷氨酰胺合成。"
    )
    keep_zh, zh_info = zh_terms(zh_text)
    frag_bad = 0
    for g in ("触前", "谷氨", "经元", "鼠海"):
        if g in keep_zh:
            frag_bad += 1
            print(f"  碎片 {g!r} 未被筛掉（左邻 {zh_info[g]['left']} "
                  f"右邻 {zh_info[g]['right']}，熵 "
                  f"{zh_info[g]['h_left']}/{zh_info[g]['h_right']}）")
    for g in ("海马", "神经元", "突触", "谷氨酸", "突触前", "成像"):
        if g not in keep_zh:
            frag_bad += 1
            print(f"  真词 {g!r} 被误杀（熵 "
                  f"{zh_info.get(g, {}).get('h_left')}/{zh_info.get(g, {}).get('h_right')}）")
    bad += frag_bad
    print("中文邻接熵筛词：通过" if frag_bad == 0 else "中文邻接熵筛词：失败")

    # ⑧ 英文剔除表：受控范式标签 / 单位 / 编号 / 来源噪声词必须挡住，
    #    真方法词必须留下。
    en_bad = 0
    for w in ("experimental", "mechanistic", "imaging", "div", "kcl", "min",
              "e18", "vpp", "fulltext"):
        if w in set(en_terms(w)):
            en_bad += 1
            print(f"  英文垃圾 {w!r} 未被挡住")
    for w in ("tomography", "microscopy", "electrophysiology", "optogenetic"):
        if w not in set(en_terms(w)):
            en_bad += 1
            print(f"  英文方法词 {w!r} 被误杀")
    bad += en_bad
    print("英文剔除表：通过" if en_bad == 0 else "英文剔除表：失败")

    # ⑨ 词云专用中文泛化表
    zh_bad = 0
    for w in ("定量", "参数", "用于", "作者"):
        if w not in GENERIC_ZH:
            zh_bad += 1
            print(f"  {w!r} 应在 GENERIC_ZH 中")
    bad += zh_bad
    print("中文泛化表：通过" if zh_bad == 0 else "中文泛化表：失败")

    # ⑩ 关键词解析门：干净列表采纳；把 Introduction 吞进来的必须整份丢弃
    kw_ok = "Keywords Acetylcholine · Neuronal network · Synaptic current\nIntroduction\nText"
    kws, why = parse_article_keywords(kw_ok)
    kw_bad = 0
    if len(kws) != 3:
        kw_bad += 1
        print(f"  干净关键词列表未被正确采纳：{kws}（{why}）")
    intro = "Keywords: a, b\n1. Introduction\n" + "text " * 200
    kws2, why2 = parse_article_keywords(intro)
    if len(kws2) > 3:
        kw_bad += 1
        print(f"  吞掉 Introduction 的关键词段未被丢弃：{kws2[:5]}（{why2}）")
    if not parse_article_keywords("no keyword section here")[1]:
        kw_bad += 1
        print("  无关键词段时应给出说明")
    bad += kw_bad
    print("关键词解析门：通过" if kw_bad == 0 else "关键词解析门：失败")

    # ⑪ 方向聚类抖动必须**跨进程可复现**（原 hash() 会随 PYTHONHASHSEED 变）
    j1 = ((zlib.crc32(b"W2020850910") % 100) / 100.0 - 0.5) * 0.5
    j2 = ((zlib.crc32(b"W2020850910") % 100) / 100.0 - 0.5) * 0.5
    if j1 != j2 or not isinstance(j1, float):
        bad += 1
        print("  抖动不可复现")
    print("抖动可复现：通过")

    # ⑫ 字号自述：17 in 画布上的 6 pt 在 183 mm（7.205 in）下必须报低于下限
    FIG_NOTES.clear()
    rec = note_fonts("selftest_方向聚类", (17, 10), [("题名标注", 6), ("图例", 9)],
                     quiet=True)
    eff = rec["effective_pt"]["论文双栏 183 mm（7.205 in 宽）"]["min_pt"]
    if abs(eff - 6 * 7.205 / 17) > 0.05 or not rec["below_floor"]:
        bad += 1
        print(f"  字号自述换算错误：{eff} / below={rec['below_floor']}")
    FIG_NOTES.clear()
    print("字号自述：通过")

    # ⑬ overview 布局：径向整齐树的**结构契约**。
    #    这条防的是"布局被后续步骤悄悄破坏"——旧版正是把它写在注释里、
    #    实际被 150 轮无约束推力推没了，很久没人发现。
    lay_bad = 0
    f_nodes = {"author": {"kind": "author", "label": "A"}}
    f_edges = []
    for pi, pname in enumerate(("P1", "P2"), 1):
        f_nodes[f"period::{pname}"] = {"kind": "period", "label": f"阶段 {pi}"}
        f_edges.append({"source": "author", "target": f"period::{pname}",
                        "kind": "阶段", "weight": 2})
        for di in range(2 if pi == 1 else 1):
            did = f"dir::{pname}::{di}"
            f_nodes[did] = {"kind": "direction", "label": f"方向{di}"}
            f_edges.append({"source": f"period::{pname}", "target": did,
                            "kind": "方向", "weight": 2})
            for ri in range(2):
                rid = f"ref::{pname}::{di}::{ri}"
                f_nodes[rid] = {"kind": "review", "label": f"题名 {ri}",
                                "direction": f"方向{di}", "relevance": 1}
                f_edges.append({"source": did, "target": rid,
                                "kind": "综述", "weight": 1})
    for mi, mname in enumerate(("m甲", "m乙", "m丙")):
        f_nodes[f"m::{mname}"] = {"kind": "method", "label": mname, "n_papers": 3 - mi}
    f_links = [{"name": "m甲 与 m乙", "reason": "x", "n": 3}]
    # 自检是**纯逻辑**检查、不渲染图；缺依赖时如实跳过并说明，
    #   与"渲染时硬失败"（策略 B）不冲突 —— 但**不能把跳过报成通过**。
    #   overview 只需要 igraph + adjustText，`wordcloud` 归词云那条（⑭）判。
    _miss_ov = [m for m in missing_graph_deps() if m != "wordcloud"]
    _miss_deps = missing_graph_deps()
    if _miss_ov:
        print(f"overview 布局契约：跳过（缺 {'、'.join(_miss_ov)}；"
              f"渲染会硬失败，自检允许跳过）")
        lay_bad = 0
    else:
        f_pos, f_meta = compute_overview_layout(f_nodes, f_edges, f_links)
        if f_pos.get("author") != (0.0, 0.0):
            lay_bad += 1
            print(f"  作者不在圆心：{f_pos.get('author')}")
        # 三层半径必须严格递增（阶段 < 方向 < 综述）
        import math as _m2

        def _r(nid):
            x, y = f_pos[nid]
            return round(_m2.hypot(x, y), 4)

        r_per = _r("period::P1")
        r_dir = _r("dir::P1::0")
        r_rev = _r("ref::P1::0::0")
        if not (0 < r_per < r_dir < r_rev):
            lay_bad += 1
            print(f"  层级半径非递增：阶段 {r_per} / 方向 {r_dir} / 综述 {r_rev}")
        if len(f_meta.get("sectors") or []) != 2:
            lay_bad += 1
            print(f"  扇区数不对：{f_meta.get('sectors')}")
        # 记忆连接的端点**必须都在外环上**，否则弧线画不出来
        ring_labels = {r["label"] for r in f_meta["ring"]}
        if not {"m甲", "m乙"} <= ring_labels:
            lay_bad += 1
            print(f"  记忆连接端点不在环上：环={sorted(ring_labels)}")
        bad += lay_bad
        print("overview 布局契约：通过" if lay_bad == 0 else "overview 布局契约：失败")

    # ⑭ 词云库布局：确定性 + 放置口径 + 坐标落在画布内。
    #    这条防的是三类静默错误：random_state 被人改掉（图不可复现）、
    #    "放下/未放下"的口径被算错（报告数字失真）、
    #    layout_ 的 (y, x) 被当成 (x, y)（整张图错乱）。
    if "wordcloud" in _miss_deps:
        print(f"词云库布局契约：跳过（缺 wordcloud；--wc-engine=custom 不需要它）")
    else:
        lib_bad = 0
        _fx = {"神经元": 23, "突触": 17, "海马": 17, "记录": 17, "成像": 14,
               "荧光": 12, "电生理": 9, "synaptic": 14, "tomography": 10}
        _ff, _fn = resolve_cjk_font_file()
        if _ff and not pathlib.Path(_ff).exists():
            lib_bad += 1
            print(f"  解析到的字体文件不存在：{_ff}")
        _l1, _i1 = wordcloud_library_layout(_fx, _ff, max_words=90)
        _l2, _i2 = wordcloud_library_layout(_fx, _ff, max_words=90)
        if [(w[0][0], s, (int(p[0]), int(p[1]))) for w, s, p, *_ in _l1] != \
           [(w[0][0], s, (int(p[0]), int(p[1]))) for w, s, p, *_ in _l2]:
            lib_bad += 1
            print("  固定 random_state 下两次布局不一致（不可复现）")
        if _i1["n_requested"] != min(90, len(_fx)):
            lib_bad += 1
            print(f"  请求数口径不对：{_i1['n_requested']} vs {min(90, len(_fx))}")
        if _i1["placed"] != _i1["n_requested"] - len(_i1["dropped"]):
            lib_bad += 1
            print(f"  放置/未放置计数不自洽：{_i1}")
        W_, H_ = _i1["canvas_px"]
        for (_wf, _sz, _pos, _o, _c) in _l1:
            y_, x_ = int(_pos[0]), int(_pos[1])
            if not (0 <= x_ <= W_ and 0 <= y_ <= H_):
                lib_bad += 1
                print(f"  坐标越界：{_wf[0]} → (x={x_}, y={y_}) 画布 {W_}x{H_}")
                break
        bad += lib_bad
        print("词云库布局契约：通过" if lib_bad == 0 else "词云库布局契约：失败")

    print("\n[OK] 自检全部通过" if bad == 0 else "\n[X] 自检有失败项")
    return 0 if bad == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    log = get("graph")
    also_png = "--also-png" in sys.argv
    fmt = (_argval("format", "auto") or "auto").lower()
    if fmt not in ("auto", "svg", "png"):
        print(f"[X] --format 取值 {fmt!r} 不在 auto/svg/png 内")
        return 2
    if _argval("formats"):
        fmt = "svg"
    try:
        dpi = int(_argval("dpi", "300") or 300)
        max_reviews = int(_argval("max-reviews", "3") or 3)
        recent_years = int(_argval("recent-years", "5") or 5)
        max_dirs = int(_argval("max-dirs", "12") or 12)
        max_review_nodes = int(_argval("max-review-nodes", "24") or 24)
        wc_min_docs = int(_argval("wc-min-docs", str(WC_MIN_DOCS)) or WC_MIN_DOCS)
    except ValueError:
        print("[X] --dpi / --max-reviews / --recent-years / --max-dirs / "
              "--max-review-nodes / --wc-min-docs 必须是整数")
        return 2

    # ── 词云引擎 ──────────────────────────────────────────────
    wc_engine = (_argval("wc-engine", WC_ENGINE_DEFAULT)
                 or WC_ENGINE_DEFAULT).lower()
    if wc_engine not in ("library", "custom"):
        print(f"[X] --wc-engine 取值 {wc_engine!r} 不在 library/custom 内")
        return 2

    # ── 绘图依赖硬门（策略 B）────────────────────────────────
    #   在**读数据、画任何东西之前**判，避免留下半成品产物目录。
    #   缺依赖不降级、不静默：直接给完整命令并退出。
    #   `wordcloud` 只在 library 引擎下需要 —— 走 custom 时不拦。
    _miss = missing_graph_deps()
    if wc_engine == "custom":
        _miss = [m for m in _miss if m != "wordcloud"]
    if _miss:
        print(f"[X] 缺少绘图依赖：{'、'.join(_miss)}")
        print("   本阶段为**硬依赖**（径向整齐树布局、独立标签避让、词云占位检测），"
              "不再产出降级图。")
        print(f"   请改用：{graph_deps_cmd()}")
        return 2
    wc_lang = (_argval("wc-lang", "both") or "both").lower()
    if wc_lang not in ("both", "zh", "en"):
        print(f"[X] --wc-lang 取值 {wc_lang!r} 不在 both/zh/en 内")
        return 2
    wc_weights = {}
    for _k in ("keywords", "title", "abstract", "methods"):
        _v = _argval(f"wc-weight-{_k}")
        if _v is None:
            continue
        try:
            wc_weights[_k] = float(_v)
        except ValueError:
            print(f"[X] --wc-weight-{_k} 必须是数字，收到 {_v!r}")
            return 2
    opts = {"dpi": dpi, "max_reviews": max_reviews, "recent_years": recent_years,
            "also_png": also_png, "max_dirs": max_dirs,
            "max_review_nodes": max_review_nodes,
            "wc_lang": wc_lang, "wc_weights": wc_weights or None,
            "wc_engine": wc_engine,
            "wc_min_docs": wc_min_docs}

    recs, cards = load_author_papers()
    final, rcards, rmeta = load_review_data()
    if not cards and not (final.get("items")):
        print("[X] 既没有解读卡（interpret/results/），也没有综述终表（refs/final_list.json）")
        print("  请先跑阶段 H 与阶段 H2 —— 本阶段刻意放在它们之后，"
              "就是为了吃「最终」产物。")
        return 2
    vocab = load_vocab()
    print(f"素材：作者论文 {len(recs)} 篇｜解读卡 {len(cards)} 张｜"
          f"综述终表 {len(final.get('items') or [])} 条｜review 卡 {len(rcards)} 张")

    plt = setup_mpl()
    if plt is None:
        return 2

    # ── ① overview ────────────────────────────────────────────
    (nodes, edges, memory_links, dir_paradigm, dir_methods, doi_dir, pinfo,
     attach_stats) = build_overview(recs, cards, final, rcards, rmeta, vocab,
                                    max_reviews=max_reviews,
                                    max_dirs=max_dirs,
                                    max_review_nodes=max_review_nodes)
    comm, why = louvain(nodes, edges)
    if why:
        print(f"[INFO] {why}")
    notes = cluster_notes(nodes, edges, comm, dir_paradigm, dir_methods, doi_dir, cards)
    contradictions = find_contradictions(cards, doi_dir, recs)
    graphs = {}
    graphs["overview"] = draw_overview(plt, nodes, edges, comm, GRAFDIR,
                                       f"{AUTHOR}_知识图谱_overview", fmt, dpi, also_png,
                                       memory_links)

    # ── ② 词云 ────────────────────────────────────────────────
    w, color_of, dir_label, dropped, wc_stats = compute_wordcloud_weights(
        cards, recs, vocab, doi_dir, weights=opts.get("wc_weights"),
        lang=opts["wc_lang"], min_docs=opts["wc_min_docs"])
    graphs["wordcloud"] = draw_wordcloud(plt, w, color_of, GRAFDIR,
                                         f"{AUTHOR}_知识图谱_词云", fmt, dpi, dir_label,
                                         also_png, wc_stats, opts["wc_engine"])

    # ── ③ 方向聚类 ────────────────────────────────────────────
    rows, hotspots, dir_how = compute_direction_clusters(
        final, rcards, rmeta, recs, recent_years, vocab, cards=cards)
    graphs["directions"] = draw_direction_clusters(plt, rows, recent_years, GRAFDIR,
                                                   f"{AUTHOR}_知识图谱_方向聚类", fmt, dpi,
                                                   also_png)

    GRAFDIR.mkdir(parents=True, exist_ok=True)
    (GRAFDIR / "graph_data.json").write_text(json.dumps({
        "author": AUTHOR,
        "nodes": list(nodes.values()),
        "edges": edges,
        "communities": comm or {},
        "cluster_notes": notes,
        "memory_links": memory_links,
        "contradictions": contradictions,
        "wordcloud": {"weights": dict(w.most_common(120)),
                      "colors": color_of,
                      "direction_colors": dir_label,
                      "dropped": dict(dropped.most_common(80)),
                      "stats": wc_stats},
        "direction_clusters": {
            "recent_years": recent_years,
            "rows": [{k: v for k, v in r.items() if k != "tokens"} for r in rows],
            "hotspots": hotspots},
        "periods": pinfo,
        "figure_notes": FIG_NOTES,
        "overview_layout": OVERVIEW_LAYOUT,
        "graphs": {k: [str(x) for x in v] for k, v in graphs.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(notes, memory_links, contradictions, dropped, hotspots,
                      rows, graphs, pinfo, opts, wc_stats=wc_stats)
    report = WD / f"{AUTHOR}_知识图谱.md"
    report.write_text(md, encoding="utf-8")

    made = [f for fs in graphs.values() for f in fs]
    print(f"\n产出图件 {len(made)} 个 → {GRAFDIR}")
    for k, fs in graphs.items():
        for f in fs:
            print(f"  · {k}: {f.name}")
    print(f"报告：{report}")
    print(f"记忆连接 {len(memory_links)} 条｜聚类 {len(notes)} 簇｜"
          f"冲突线索 {len(contradictions)} 条")
    print(f"词云：入云 {len(w)} 个词型｜被剔 {len(dropped)} 个候选词型"
          f"（{sum(dropped.values())} 次；多数是低熵的跨词碎片）"
          f"｜关键词覆盖率 {wc_stats.get('keywords_coverage')}")
    if dir_how:
        print("方向归类：" + "｜".join(f"{k} {v} 条" for k, v in dir_how.most_common()))
    print(f"综述挂载：挂上 {attach_stats['attached']} 条｜"
          f"方向无法对应未挂 {attach_stats['unattached']} 条｜"
          f"超上限截断 {attach_stats['truncated']} 条 —— "
          f"**完整清单始终在《研究方向综述》里，图上只是节选**")
    if attach_stats.get("how"):
        print("  挂载路径：" + "｜".join(f"{k} {v} 条"
                                        for k, v in sorted(attach_stats["how"].items())))
    if not made:
        print("[WARN] 一张图都没产出 —— 请检查 matplotlib 是否可用（见上方提示）。")
    journal("K", "graph.build", n_nodes=len(nodes), n_edges=len(edges),
            n_memory_links=len(memory_links), n_clusters=len(notes),
            n_contradictions=len(contradictions), files=[f.name for f in made],
            fmt=fmt, dpi=dpi)
    log.info(f"[OK] 阶段 K 完成 → {GRAFDIR}")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
