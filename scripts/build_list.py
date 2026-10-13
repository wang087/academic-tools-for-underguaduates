# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 可移植配置：设置环境变量 PAPER_WORKDIR 指向作者工作目录，
# 未设置时使用当前工作目录。所有中间数据存于 <WORKDIR>/.work_cdp/
# ─────────────────────────────────────────────────────────────
import os, pathlib, sys

from logutil import run_root as _run_root  # noqa: E402
WORKDIR = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()
CDPDIR = WORKDIR / ".work_cdp"
CDPDIR.mkdir(parents=True, exist_ok=True)
AUTHOR = os.environ.get("PAPER_AUTHOR", WORKDIR.name)
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

"""Integrate ORCID page data + Crossref authoritative records -> 论文列表 MD."""
import json, re, sys, io, html
import scope as _scope          # 同名消歧排除名单（.work_cdp/disambiguated.json）
# 必须起别名：本脚本主循环里有一个**局部变量就叫 `journal`**
#   （`journal = it.get("journal")`，刊物名），直接 `from logutil import journal`
#   会被它遮蔽 —— 调用时报 `TypeError: 'str' object is not callable`，
#   而且是在**写完文件之后**才炸，属于「日志把主流程搞挂」。
from logutil import journal as op_journal     # 操作流水（可回溯）
# stdout 与 stderr 都要包：本脚本的「缺少输入 / 未提供变量」等提示走 stderr，
# 不包的话在 GBK 控制台上会输出成 \uXXXX 乱码（SKILL §5 已记录该类问题）。
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def _load(name, required=False, default=None):
    """读取 .work_cdp 下的中间产物；缺失时给出**可操作**的提示而不是裸 FileNotFoundError。"""
    p = CDPDIR / name
    if not p.exists():
        if required:
            sys.stderr.write(
                f"\n[X] 缺少必需输入 {p}\n"
                f"  请先运行产出该文件的步骤（见 SKILL.md §4 阶段 D / §8 脚本索引）。\n"
                f"  若本案例未抓 ORCID 网页（例如改用 orcid_probe.py 走 API），\n"
                f"  请改用 disambiguate.py + enrich_crossref_authors.py 的产物构建列表。\n\n")
            sys.exit(2)
        return default
    return json.load(open(str(p), encoding="utf-8"))


CR = _load("crossref_all.json", required=True)
# 旧版依赖 ORCID 网页抓取产物；v4 起改为**可选**（走 API 路径的案例没有此文件）
ORCID_RECS = _load("orcid_works_parsed.json", default=[])

# 1) 从 ORCID 记录取 date/year + contributors 补充（无该文件时为空，不影响主流程）
orcid_info = {}
for r in ORCID_RECS:
    doi = (r.get("doi") or "").replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip().lower()
    key = doi if doi else ("__nodoi__" + (r.get("title") or "")[:40])
    orcid_info[key] = {
        "orcid_year": r.get("year", ""),
        "orcid_date": r.get("date", ""),
        "contributors": r.get("contributors", []),
    }
if not ORCID_RECS:
    sys.stderr.write("[INFO] 未找到 orcid_works_parsed.json（本案例可能走 ORCID API 路径）"
                     "→ 跳过 ORCID 年份/贡献者补充，改用 Crossref 记录。\n")

def clean_title(t, journal=""):
    t = t or ""
    # 去除 icon 文本与分页噪声（"<任意署名> via ResearcherID" 用正则，不写死作者名）
    for pat in ["check_circle Crossref "]:
        t = t.replace(pat, "")
    t = re.sub(r"\b[\w.\-]+(?:\s+[\w.\-]+){0,3}\s+via\s+ResearcherID\s*", "", t)
    t = re.sub(r"\b\d+\s+Page\s+\d+\s+of\s+\d+\s*", "", t)
    # 去除 "<i> </i> <sub> </sub>" 标签残余，并把标签间空白合并（如 "[Ti 12 In 6 ...]" -> "[Ti12In6...]"）
    t = html.unescape(re.sub(r"</?(i|sub|sup)>", "", t))
    # 合并所有空白（含换行/制表/\xa0/  等），并在标点后保留必要空格
    t = re.sub(r"[\s  -   ]+", " ", t).strip()
    # 尾随期刊名（当期刊被吞进标题行时）—— 用**本记录自身的期刊名**判定，不维护硬编码刊名表
    if journal:
        j = str(journal).strip()
        if j:
            t = re.sub(r"\s+" + re.escape(j) + r"\s*$", "", t, flags=re.I).strip()
            # 括号形式（如 "…… (Chinese Journal of Inorganic Chemistry)"）
            t = re.sub(r"\s*[(（]\s*" + re.escape(j) + r"\s*[)）]\s*$", "", t, flags=re.I).strip()
    return t

# 2) 整合：以 Crossref 为准，年份用 Crossref；无 DOI 的两条单独保留
merged = {}
for doi, rec in CR.items():
    doi = doi.lower()
    rec["doi"] = doi
    merged[doi] = rec

# 2b) 排除同名他人的论文（原始需求：非本人论文要在输出中注明并自动忽略）
#     来源是 disambiguate.py 的 dropped 列表 —— **黑名单**语义，只删「已明确
#     判定为他人」的条目；绝不按 confirmed 做白名单（语料不同源会误删本人成果）。
#     excluded_dois() 返回 None（没跑过消歧）时不做任何过滤，保持原行为。
_dropped = _scope.dropped_list(CDPDIR)
_excluded_recs = []
_exc = _scope.excluded_dois(CDPDIR, total=len(merged))
if _exc is None:
    sys.stderr.write("[INFO] 未找到 disambiguated.json → 未做同名消歧，"
                     "本列表为**全量候选**（建议先跑 disambiguate.py，见 SKILL §4 阶段 D）\n")
elif _exc:
    for d in list(merged):
        if d in _exc:
            _excluded_recs.append(merged.pop(d))
    sys.stderr.write(f"[INFO] 已按 disambiguated.json 排除同名他人论文 {len(_excluded_recs)} 条"
                     f"（剩余 {len(merged)} 条，被排除项在文末如实列出）\n")
else:
    sys.stderr.write("[INFO] disambiguated.json 中无需排除的记录\n")

extra_nodoi = []
for r in ORCID_RECS:
    doi = (r.get("doi") or "").replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip().lower()
    if doi and doi in merged:
        # 补 contributors
        pass
    else:
        title = clean_title(r.get("title", ""), r.get("journal") or r.get("orcid_journal") or "")
        if not title:
            continue
        extra_nodoi.append({
            "doi": "",
            "title": title,
            "journal": r.get("journal") or r.get("orcid_journal") or "",
            "year": r.get("year", ""),
            "vol": "", "issue": "", "page": "",
            "article_number": "",
            "type": "",
            # 无 DOI 的 ORCID 记录拿不到结构化作者，留空由著录侧标注（不臆造作者名）
            "authors": [],
            "orcid_date": r.get("date", ""),
            "_nodoi_note": "ORCID 记录；未检索到 DOI，题录字段以 ORCID 页面为准",
        })

def author_name(a):
    """兼容两种 authors 形状：字典（{family,given,name}）与纯字符串。

    历史教训：enrich_crossref_authors.py 曾把 authors 写成**字符串列表**，
      而这里直接 a.get("name") → AttributeError: 'str' object has no attribute
      'get'，整条著录链崩掉。上游已修正为字典列表，这里再做一层防御，
      保证任一上游形状都不会让流程中断。
    """
    if isinstance(a, dict):
        n = a.get("name")
        if n:
            return n
        return ", ".join(x for x in (a.get("family"), a.get("given")) if x)
    return str(a or "")

def gbt_authors(authors):
    """GB/T 7714 前3位 + et al."""
    if not authors:
        return ""
    names = [author_name(a) for a in authors if author_name(a)]
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + ", et al."

# 本人署名识别：姓名关键词不硬编码，由环境变量给出
#   PAPER_NAME_TOKENS="zhou, cong-zhao|cong-zhao zhou|zhou cz"（竖线分隔，小写，任一命中即认定本人）
#   未提供时回退到 PAPER_SURNAME / PAPER_GIVEN 组合。
_TOKENS = [t.strip().lower() for t in os.environ.get("PAPER_NAME_TOKENS", "").split("|") if t.strip()]
if not _TOKENS:
    _sn = os.environ.get("PAPER_SURNAME", "").strip().lower()
    _gn = os.environ.get("PAPER_GIVEN", "").strip().lower()
    if _sn and _gn:
        _TOKENS = [f"{_sn}, {_gn}", f"{_gn} {_sn}"]
    elif _sn:
        _TOKENS = [f"{_sn},"]
if not _TOKENS:
    sys.stderr.write("[INFO] 未提供 PAPER_NAME_TOKENS / PAPER_SURNAME+PAPER_GIVEN"
                     "→ 跳过「本人署名位次」标注。\n")

def my_position(authors):
    """返回 (是否本人, 本人位置标注)；姓名词表来自环境变量，无硬编码"""
    if not authors or not _TOKENS:
        return ("", "")
    names = [author_name(a).lower() for a in authors]
    hits = [i for i, n in enumerate(names) if any(tok in n for tok in _TOKENS)]
    if not hits:
        return ("", "")
    i = hits[0]
    n = len(names)
    if i == 0:
        return ("是", "第一作者")
    if i == n - 1:
        return ("是", "末位作者（通常为通讯作者）")
    return ("是", f"第{i+1}位作者")

def sort_key(rec):
    y = rec.get("year") or ""
    try:
        return (int(y), (rec.get("title") or ""))
    except Exception:
        return (9999, (rec.get("title") or ""))

items = sorted([r for r in merged.values()] + extra_nodoi, key=sort_key)

# v4 修复：旧版把作者姓名/单位/ORCID、署名识别词与阶段分组**硬编码**为上一案例，
#   换作者后会输出错误标题与文件名。现全部改为从环境变量/数据推导（脚本内零作者信息）。
AUTHOR_ZH = AUTHOR
AUTHOR_EN = os.environ.get("PAPER_AUTHOR_EN", "")
ORCID_ID = os.environ.get("PAPER_ORCID", "")
AFFIL = os.environ.get("PAPER_AFFIL", "")
_n = len(items)

# G-1：抬头必须与**自家尾部披露**同口径。
#   旧版无条件写「均经 Crossref 逐条核实题录」「经核实归属本人的 N 条」，
#   而同一个文件的尾注却写「确认 164 条 + 待复核 13 条（均保留）」，
#   且 14 条只有 ORCID 记录、连 DOI 都没有，根本没有 Crossref 题录可核
#   —— 首尾相互否定，读到抬头的人不会去翻尾注（取证见 实测结果汇总.md#g-1）。
_sd = _scope.load(CDPDIR) or {}
_n_crossref = len(merged)                    # 有 Crossref 核实题录的条数
_n_nodoi = _n - _n_crossref                  # 仅 ORCID 记录（无 DOI）
_n_conf = len(_sd.get("confirmed") or [])
_n_review = len(_sd.get("review") or [])
_n_dropped = len([r for r in _dropped if r["doi"] and _scope.is_excludable(r)])

lines = []
lines.append(f"# {AUTHOR_ZH}{'（' + AUTHOR_EN + '）' if AUTHOR_EN else ''}论文列表\n")
lines.append(f"> 作者：{AUTHOR_ZH}{'（' + AUTHOR_EN + '）' if AUTHOR_EN else ''}"
             + (f"，{AFFIL}" if AFFIL else ""))
lines.append("> ORCID：" + (f"https://orcid.org/{ORCID_ID}" if ORCID_ID else "（未提供）")
             + f" ｜ 共 **{_n} 条**"
             + f" ｜ 题录经 Crossref 逐条核实 **{_n_crossref}** 条"
             + (f"，仅 ORCID 记录（无 DOI，题录以 ORCID 页面为准）**{_n_nodoi}** 条"
                if _n_nodoi else ""))
lines.append("> 著录格式：GB/T 7714—2015（作者多于 3 位取前 3 位加 et al.；期刊名采用刊名标准写法）")
lines.append("")
if _sd:
    _attr = (f"**归属核对**：判据⓪（ORCID 溯源 + 同域校验）与署名单位指纹交叉判得 "
             f"确认 **{_n_conf}** 条")
    if _n_review:
        _attr += (f"、**待人工复核 {_n_review} 条**"
                  "（溯源命中但题录未命中方向词 —— 按流程**一律保留在表中**，"
                  "引用前请逐条回原文核对作者身份）")
    if _n_dropped:
        _attr += f"、已排除同名他人 **{_n_dropped}** 条（列在文末专节，未计入上表）"
else:
    _attr = "**归属核对**：未运行同名消歧（无 `disambiguated.json`），本表归属**未经核对**"
lines.append(f"**说明：** 以下为{AUTHOR_ZH}的全部成果（按发表年升序）。{_attr}。"
             "本人署名位置每条约注：第一作者 / 末位作者（通常通讯）/ 其他位次合作。")
lines.append("")
lines.append("> 提示：可用环境变量覆盖抬头信息 —— "
             "`PAPER_AUTHOR_EN`（英文名）、`PAPER_ORCID`、`PAPER_AFFIL`（单位/职称）；"
             "「本人署名位次」由 `PAPER_NAME_TOKENS`（或 `PAPER_SURNAME`+`PAPER_GIVEN`）驱动；"
             "阶段分组由 `.work_cdp/periods.json` 或 `PAPER_PERIODS` 驱动。")
lines.append("")

# 分组：按阶段 —— **不硬编码任何作者的履历**
#   配置来源（优先级从高到低）：
#     ① 环境变量 PAPER_PERIODS = '[["2004–2009","独立建组前",2004,2009], ...]'
#     ② 工作目录下 .work_cdp/periods.json（同上结构）
#   两者皆无 → 不分组，仅按年份升序平铺。
PERIODS = []
_pj = CDPDIR / "periods.json"
if _pj.exists():
    try:
        PERIODS = [(str(a), str(b), int(c), int(d)) for a, b, c, d in json.load(open(str(_pj), encoding="utf-8"))]
    except Exception as e:
        sys.stderr.write(f"[WARN] periods.json 解析失败，忽略阶段分组：{e}\n")
if not PERIODS and os.environ.get("PAPER_PERIODS"):
    try:
        PERIODS = [(str(a), str(b), int(c), int(d))
                   for a, b, c, d in json.loads(os.environ["PAPER_PERIODS"])]
    except Exception as e:
        sys.stderr.write(f"[WARN] PAPER_PERIODS 解析失败，忽略阶段分组：{e}\n")

def period_of(year):
    try:
        y = int(year)
    except Exception:
        return None
    for label, desc, lo, hi in PERIODS:
        if lo <= y <= hi:
            return (label, desc)
    return None

idx = 1
current_label = None
for it in items:
    y = str(it.get("year", ""))
    p = period_of(y)
    if p and p[0] != current_label:
        current_label = p[0]
        lines.append("")
        lines.append(f"## 阶段：{p[0]}（{p[1]}）\n")
    journal = it.get("journal") or it.get("orcid_journal", "")
    title = clean_title(it.get("title", ""), journal)
    if not title:
        title = "（题名待补）"
    vol, iss, pg = it.get("vol", ""), it.get("issue", ""), it.get("page", "")
    an = it.get("article_number") or ""
    type_ = it.get("type", "")
    authors = it.get("authors", [])
    isME, pos = my_position(authors)
    doi = it.get("doi", "")
    auth_s = gbt_authors(authors)
    loc = f"[J]"
    # 页码处理
    if pg and iss:
        locn = f"{vol}({iss}): {pg}"
    elif pg:
        locn = f"{vol}: {pg}"
    elif an:
        locn = f"{vol}({iss}): {an}" if iss else f"{vol}: {an}"
    elif vol:
        locn = f"{vol}"
    else:
        locn = ""
    # 句点不再重复：gbt_authors() 在 >3 位作者时返回 "…, et al."（自带句点），
    #   旧写法再补一个 ". " 会输出 "et al.. Title" —— 不符合 GB/T 7714 著录。
    auth_prefix = (auth_s.rstrip(".") + ". ") if auth_s else ""
    entry = f"[{idx}] {auth_prefix}{title}{loc}. {journal}, {y}" + (", " + locn if locn else "") + "."
    if doi:
        entry += f" DOI: {doi}."
        entry += f" 原文链接：https://doi.org/{doi}."
    elif it.get("_nodoi_note"):
        entry += f" （{it['_nodoi_note']}）"
    else:
        entry += " （ORCID 记录；暂未检索到 DOI）"
    tag = "｜本人署名：" + pos if isME else ""
    lines.append(entry)
    if tag:
        lines[-1] = lines[-1] + tag
    lines.append("")
    idx += 1

lines.append("---")

# 如实注明被排除的同名他人论文（原始需求：非本人论文要在输出中注明）。
#   不静默丢弃 —— 读者能一眼看到「排除了哪些、依据是什么」，便于复核。
if _excluded_recs:
    lines.append(f"## 已排除的同名他人论文（{len(_excluded_recs)} 条，未计入上表）\n")
    lines.append("> 判定依据：`disambiguate.py` 的署名单位指纹 + 合作者网络判据"
                 "（详见 `.work_cdp/disambiguated.json`）。\n")
    lines.append("| # | 年份 | 题名 | DOI | 排除理由 |")
    lines.append("|---|---|---|---|---|")
    _why = {r["doi"]: r["why"] for r in _dropped if r["doi"]}
    for i, r in enumerate(sorted(_excluded_recs,
                                 key=lambda x: str(x.get("year") or "")), 1):
        d = (r.get("doi") or "").lower()
        t = clean_title(r.get("title", ""), r.get("journal") or "")[:70]
        lines.append(f"| {i} | {r.get('year') or '—'} | {t} | `{d}` | "
                     f"{(_why.get(d) or '判定为同名他人')[:60]} |")
    lines.append("")

_p = _scope.params(CDPDIR)
lines.append(f"*题录来源：{_n_crossref} 条经 Crossref API 逐条核实字段"
             + (f"；另 {_n_nodoi} 条仅有 ORCID 记录（无 DOI），题录以其页面为限。*"
                if _n_nodoi else "。*")
             + ("" if not _sd else
                f"\n*归属判据：判据⓪ ORCID 溯源（附**同域校验**：溯源命中但题录"
                f"未命中方向词者降为待复核，不据此排除）+ 署名单位指纹 + 合作者网络。*"))
lines.append(f"*同名消歧：{_scope.summary(CDPDIR)}。*")
if _p:
    lines.append(f"*消歧判据参数：姓=`{_p.get('surname','')}` 名=`{_p.get('given','')}` "
                 f"单位指纹=`{'; '.join(_p.get('affil') or []) or '—'}` "
                 f"锚点 DOI=`{', '.join(_p.get('anchor_doi') or []) or '—'}`。*\n")
else:
    lines.append("")

# 交付物命名（**单一文件**）：
#   <作者名>_论文列表.md —— 规范交付名（需求原文「文件名形如 XX_论文列表」）
#
# [STOP] 不再另写 `<作者名>_论文列表_完整版.md`。该文件曾是**内容逐字节相同**的历史别名，
#   写两份只会让交付目录出现两个内容一样的文件，人工核对时还要先确认"哪个是正的"。
#   本文件本身就是完整版（无删减），不需要第二份来"表示完整"。
body = "\n".join(lines)
out_main = WORKDIR / f"{AUTHOR_ZH}_论文列表.md"
with open(str(out_main), "w", encoding="utf-8") as f:
    f.write(body)
print("saved:", out_main)
print("entries:", idx - 1)

op_journal("G", "list.build", entries=idx - 1, total_crossref=len(CR),
           excluded=len(_excluded_recs), disambiguation=_scope.summary(CDPDIR),
           files=[out_main.name])
for r in _excluded_recs[:20]:
    op_journal("G", "list.excluded", doi=(r.get("doi") or "").lower(),
               year=r.get("year"), title=(r.get("title") or "")[:80])
