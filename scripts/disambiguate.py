# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 同名消歧（阶段 A / C）—— 作者身份核验的关键一步
#
# 为什么必须有这一步：实测 OpenAlex / Europe PMC 的作者聚合**会严重过度合并**。
# 某案例中一个 OpenAlex author id 混入了无线通信、有机化学、微电网故障诊断、
# 聚合物材料、空间物理等多个同名者，若直接采信，成果列表会掺入大量他人论文。
#
# 三重判据（递进收紧）：
#   ① 姓名精确匹配 —— 名必须是目标名（拒绝 Anping vs Ao、Guang-Ao vs Ao 这类近似）
#   ② 署名块单位命中 —— 目标作者**本人署名块**的单位须命中给定锚点字符串
#   ③ 合作者网络重合 —— 与「锚点论文」的共作者重合数 ≥ 阈值
#
# 判据 ② 是最有力的：同一姓名研究者高度集中于特定机构，而本人在各时期的
# 署名单位相当稳定（博士单位 / 博后单位 / 独立建组单位）。把这些串作为锚点，
# 漏收率极低而误收率接近 0。
#
# 用法：
#   uv run python disambiguate.py --surname=<姓> --given=<名> \
#       --affil="<本人署名单位指纹 1>" --affil="<指纹 2>" \
#       --anchor-doi=<确定无疑的代表作 DOI（逗号分隔）> \
#       --domain-terms="<方向词1,方向词2,…>"      # 判据⓪ 的同域校验（可省，见下）
#
#   --domain-terms 缺省时回退环境变量 PAPER_DOMAIN_TERMS，再回退本次
#     <WORKDIR>/.work_cdp/topic_plan.json 的主题/方法词；三者都没有则**跳过同域校验**
#     并告警 —— 此时「DOI 出自本人 ORCID」单独定案，ORCID 自动导入的同名他人会一并被确认。
#
#   输入取自 <WORKDIR>/.work_cdp/ 下的 europepmc_parsed.json 或 *.json（Europe PMC 原始结果）
#   产出 <WORKDIR>/.work_cdp/disambiguated.json
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import collections
import io
import json
import os
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from logutil import get as get_log, journal, workdir as _wd   # noqa: E402


def parse_args(argv):
    from collections import defaultdict
    o = defaultdict(list)
    o.update({"surname": "", "given": "", "affil": [], "anchor_doi": [],
              "min_coauthor": 2, "large_collab": 40, "input": "", "domain_terms": []})
    for a in argv:
        if a.startswith("--surname="): o["surname"] = a.split("=", 1)[1]
        elif a.startswith("--given="): o["given"] = a.split("=", 1)[1]
        elif a.startswith("--affil="): o["affil"].append(a.split("=", 1)[1])
        elif a.startswith("--anchor-doi="): o["anchor_doi"] += [
            x for x in a.split("=", 1)[1].split(",") if x]
        elif a.startswith("--domain-terms="): o["domain_terms"] += [
            x.strip().lower() for x in re.split(r"[,|、；;]", a.split("=", 1)[1]) if x.strip()]
        elif a.startswith("--min-coauthor="): o["min_coauthor"] = int(a.split("=", 1)[1])
        elif a.startswith("--large-collab="): o["large_collab"] = int(a.split("=", 1)[1])
        elif a.startswith("--input="): o["input"] = a.split("=", 1)[1]
    return o


def load_records(cdpdir: pathlib.Path, inp: str) -> dict:
    """合并 Europe PMC 原始结果与已解析结果，按 DOI/PMID 去重。"""
    recs = {}
    files = []
    if inp:
        files = [pathlib.Path(inp)]
    else:
        files = sorted(cdpdir.glob("*.json"))

    def add(r, src):
        """同一 DOI 可能出现在多个文件里（原始 Europe PMC + 已解析容器）——
        取「信息更全」的那份，避免用半截记录覆盖完整记录。"""
        key = (r.get("doi") or r.get("pmid") or r.get("id") or "").lower()
        if not key:
            return
        score = _richness(r)
        cur = recs.get(key)
        if cur is None or score > cur.get("_rich", -1):
            r["_src"] = src
            r["_rich"] = score
            recs[key] = r
        else:
            cur.setdefault("_also", []).append(src)

    def _richness(r):
        """记录信息量打分。**原始 Europe PMC 结构最优**（逐作者带单位），
        已解析容器次之（只有目标作者的单位），故给原始结构更高权重。"""
        s = 0
        auths = (r.get("authorList") or {}).get("author", []) or []
        s += min(len(auths), 60)
        for a in auths[:60]:
            if (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation"):
                s += 20            # 逐作者单位是关键信息 → 高权重
        if auths:
            s += 40                # 原始 Europe PMC 结构标记
        s += min(len(r.get("abstractText") or r.get("abstract") or "") // 100, 20)
        if r.get("my_affils"):
            s += 10
        if r.get("journalInfo"):
            s += 5
        return s

    # 只把**题录类**文件当候选语料。`.work_cdp/*.json` 里混着大量
    #   **非题录**的辅助文件（去重登记表、下载状态、注入记账、派生留档…），
    #   它们同样含 DOI 字段，旧版一律当候选读进来，凭空多出几条
    #   「无作者名单」的幽灵记录，把候选数从 13 抬到 16，
    #   让「确认/待复核/排除」的分母失真。此处按文件名显式排除。
    _AUX = {
        "disambiguated.json", "master.json",          # 本脚本的产物 / 历史总表
        "pdf_index.json",                             # DOI → 文件名 登记表
        "fulltext_state.json",                        # 下载状态
        "cdp_injected.json",                          # 注入脚本记账
        "crossref_authors.json",                      # enrich 的派生留档
        "keyword_families.json", "manifest.json",     # 语义词族 / 解读任务清单
        "orcid_works.json",                           # ORCID 卡片原文（题录另见 parsed）
    }
    for f in files:
        if f.name in _AUX:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 统一成「记录列表」；容忍三种结构：
        #   ① Europe PMC 原始：{"resultList": {"result": [...]}}
        #   ② 已解析容器：    {"kept": [...], "review": [...]}
        #   ③ 裸列表：        [ {...}, {...} ]
        batches = []
        if isinstance(d, list):
            batches.append(d)
        elif isinstance(d, dict):
            rl = d.get("resultList")
            if isinstance(rl, dict) and isinstance(rl.get("result"), list):
                batches.append(rl["result"])
            for key in ("kept", "review", "confirmed", "records", "result"):
                if isinstance(d.get(key), list):
                    batches.append(d[key])
            # ④ `{doi: 记录}` 形状（crossref_all.json / unpaywall.json 等）。
            #   旧版**不认这种形状** —— 于是 ORCID→Crossref 这条主路径上
            #   候选语料为空，消歧无从施加（实测 2026-09-18）。
            #   判据：值多为 dict、且至少有 3 条，才当作「按 DOI 索引的记录表」。
            if not batches:
                vals = [v for v in d.values() if isinstance(v, dict)]
                if len(vals) >= 3 and len(vals) >= len(d) * 0.6:
                    for k, v in d.items():
                        if isinstance(v, dict):
                            v.setdefault("doi", k)
                    batches.append(vals)
        for batch in batches:
            for r in batch:
                if isinstance(r, dict):
                    add(r, f.stem)
    return recs


def load_orcid_dois(cdpdir) -> set:
    """收集「出自本人 ORCID 记录」的 DOI 集合（判据 ⓪ 的输入）。

    为什么这是一条**肯定性**判据，而且优先级最高：
      ORCID 记录由**作者本人维护**；本流程又已把 ORCID 的任职/教育时间线
      与作者主页履历逐条比对确认过身份。一个 DOI 出现在其 ORCID 成果列表里，
      即等于**作者本人认领了这篇成果** —— 这比单位指纹更直接、更可靠。
    为什么必须有它：ORCID→Crossref 是本流程的主路径，而 Crossref
      **只有少数出版社存缴逐作者单位**（实测郭傲案例 13 篇里仅 2 篇有）。
      若只靠单位指纹，主路径上的消歧必然全部落到「数据缺失、无法判定」。
    """
    out = set()
    for name in ("orcid_works_parsed.json", "orcid_probe.json"):
        p = cdpdir / name
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, list):
            items = d
        elif isinstance(d, dict):
            items = d.get("works") or []
        else:
            items = []
        for r in items:
            if not isinstance(r, dict):
                continue
            v = str(r.get("doi") or "").strip().lower()
            for pre in ("https://doi.org/", "http://dx.doi.org/",
                        "https://dx.doi.org/", "doi:"):
                if v.startswith(pre):
                    v = v[len(pre):]
            v = v.strip()
            if v:
                out.add(v)
    return out


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def _name_ok(fn: str, ini: str, given: str) -> bool:
    """名是否精确匹配给定名。

    防两类误命中：
      · 前缀误命中 —— Anping 不应匹配 Ao（故要求 firstName **以** given 开头，
        而不是 given 以 firstName 开头）；
      · 复名误命中 —— Guang-Ao 不应匹配 Ao（同上规则天然排除）。
    缩写仅在无 firstName 时作为依据。
    """
    g = norm_name(given)
    fn_n = norm_name(fn)
    if fn_n:
        return fn_n == g or (len(g) >= 2 and fn_n.startswith(g))
    return bool(ini) and len(ini.replace(".", "")) <= 2 and ini[0].lower() == g[:1]


def has_author_data(rec) -> bool:
    """记录里**到底有没有作者名单**。

    为什么必须单独判：`author_blocks()` 返回空有两种截然不同的原因 ——
      (a) 记录有作者名单，但里面没有「名精确匹配」的目标作者 → **否定证据**；
      (b) 记录**压根没有作者名单**（如 orcid_works_parsed.json 只有题名/DOI）
          → **数据缺失**，无从判断。
      旧版把两者一律记成 name-mismatch，等于把「无从判断」当成「不是本人」。
      实测 2026-09-18：郭傲案例的候选语料正是 (b)，结果 13 篇**全部**被判他人，
      若非下游有安全阀，交付列表会被清空。
    """
    if ((rec.get("authorList") or {}).get("author") or []):
        return True
    return bool(rec.get("authors"))


def author_blocks(rec, surname: str, given: str):
    """返回目标作者在该记录中的署名块列表（idx 为 1-based 署名位次）。

    兼容两种记录形态：
      A. Europe PMC 原始 —— authorList.author[]，逐作者带 authorAffiliationDetailsList
      B. 已解析容器     —— authors[]（"<Surname> A" 缩写式）+ my_affils[]（仅目标作者的单位）
    """
    out = []

    # ── 形态 A ──
    auths = (rec.get("authorList") or {}).get("author", []) or []
    for i, a in enumerate(auths):
        ln = (a.get("lastName") or "").strip()
        fn = (a.get("firstName") or "").strip()
        ini = (a.get("initials") or "").strip()
        if norm_name(ln) != norm_name(surname) and not ln:
            # 少数记录无 lastName，退回用 fullName 切分
            full = (a.get("fullName") or "").strip()
            parts = full.split()
            if len(parts) >= 2 and norm_name(parts[0]) == norm_name(surname):
                fn, ini = parts[-1], parts[-1]
            else:
                continue
        elif norm_name(ln) != norm_name(surname):
            continue
        if not _name_ok(fn, ini, given):
            continue
        affs = [x.get("affiliation", "") for x in
                ((a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or [])]
        out.append({"idx": i + 1, "affils": affs, "full": a.get("fullName"),
                    "first": fn, "initials": ini})
    if out:
        return out

    # ── 形态 B/C：`rec["authors"]` ──
    #   B = 字符串列表（"<Surname> A" 缩写式），单位只能来自 rec["my_affils"]
    #   C = **字典列表** [{family, given, name, affiliation[]}]（Crossref 契约）
    #   旧版只处理 B，遇到 C（本流程 crossref_all.json 就是 C）会把 dict
    #     整个 str() 化再切词，既切不出姓、也拿不到单位 → 全部落进
    #     「无作者名单」分支。这是消歧在 ORCID→Crossref 路径上失效的第二个原因。
    names = rec.get("authors") or []
    for i, item in enumerate(names):
        if isinstance(item, dict):
            fam = (item.get("family") or "").strip()
            giv = (item.get("given") or "").strip()
            full = (item.get("name") or "").strip() or f"{giv} {fam}".strip()
            affs = [a for a in (item.get("affiliation") or []) if a]
            if fam:
                if norm_name(fam) != norm_name(surname):
                    continue
                if not _name_ok(giv, "", given):
                    continue
            else:
                # 无结构化 family：退回按空格切分
                parts = [p for p in full.split() if p]
                if not parts:
                    continue
                if len(parts) == 2 and norm_name(parts[0]) == norm_name(surname):
                    giv = parts[1]
                elif norm_name(parts[-1]) == norm_name(surname):
                    giv = " ".join(parts[:-1])
                else:
                    continue
                if not _name_ok(giv, "", given):
                    continue
            out.append({"idx": i + 1, "affils": affs, "full": full,
                        "first": giv, "initials": ""})
            continue

        nm = str(item or "")
        parts = [p for p in nm.split() if p]
        if not parts:
            continue
        # 「姓 名首字母」与「名 姓」两种写法都要认
        if len(parts) == 2 and norm_name(parts[0]) == norm_name(surname):
            fn, ini = "", parts[1]
        elif norm_name(parts[-1]) == norm_name(surname):
            fn, ini = " ".join(parts[:-1]), " ".join(parts[:-1])
        else:
            continue
        if not _name_ok(fn, ini, given):
            continue
        out.append({"idx": i + 1, "affils": list(rec.get("my_affils") or []),
                    "full": nm, "first": fn, "initials": ini})
    return out


def all_authors(rec):
    """记录的全部作者名（兼容两种形态）。"""
    auths = (rec.get("authorList") or {}).get("author", []) or []
    if auths:
        return [(a.get("fullName") or "").strip() for a in auths if a.get("fullName")]
    return [str(x).strip() for x in (rec.get("authors") or []) if x]


def main() -> int:
    o = parse_args(sys.argv[1:])
    if not o["surname"] or not o["given"]:
        print(__doc__ or "用法: uv run python disambiguate.py "
                         "--surname=<姓> --given=<名> "
                         "--affil=\"<本人署名单位指纹 1>\" --affil=\"<指纹 2>\" "
                         "--anchor-doi=<确定无疑的代表作 DOI（逗号分隔）>")
        return 1
    wd = _wd()
    cdpdir = wd / ".work_cdp"
    log = get_log("disambiguate", wd=wd)

    recs = load_records(cdpdir, o["input"])
    log.info(f"候选记录（去重后）: {len(recs)}")

    # ── 判据 ① + ② ────────────────────────────────────────
    # 每条 miss 都带一个**机器可读**的 code，写进 disambiguated.json 的 dropped。
    #   必要性：dropped 里混着两类性质完全不同的记录 ——
    #     · 肯定性判定（name-mismatch / affil-mismatch）→ 可作排除依据
    #     · 数据缺失（no-affil-data）→ **不是**否定证据，脚本末尾已明确警告
    #       「不要直接当作他人论文丢弃」
    #   下游 scope.py 按 code 区分二者；若只靠 why 文本匹配，措辞一改就失准。
    orcid_dois = load_orcid_dois(cdpdir)
    if orcid_dois:
        log.info(f"判据⓪ ORCID 溯源：本人 ORCID 记录含 {len(orcid_dois)} 个 DOI")

    # ── 判据⓪ 的同域校验词表 ────────────────────────────────
    #   为什么需要：ORCID 的自动导入（Web of Science / Scopus 认领）会把**同名他人**
    #   的条目拉进本人记录，此时「DOI 出自 ORCID = 本人认领」这个强信号本身就脏了
    #   （实测：一位神经退行方向学者的 ORCID 成果里混入 11 条材料/腐蚀/增材制造论文）。
    #   词表只从**运行时**取，不写死任何作者的领域：
    #     ① --domain-terms=  ② PAPER_DOMAIN_TERMS  ③ 本次 topic_plan.json 的主题/方法词
    #     都没有 → 跳过同域校验并告警（保持旧行为，绝不假装判得动）。
    terms = list(o["domain_terms"])
    if not terms:
        terms = [t.strip().lower() for t in
                 re.split(r"[,|、；;]", os.environ.get("PAPER_DOMAIN_TERMS", "")) if t.strip()]
    if not terms:
        tp = cdpdir / "topic_plan.json"
        if tp.exists():
            try:
                p = json.loads(tp.read_text(encoding="utf-8"))
                terms = [str(x).strip().lower() for x in
                         ((p.get("topic_terms_en") or []) + (p.get("method_terms_en") or [])
                          + [p.get("direction_chosen") or ""]) if len(str(x).strip()) > 2]
            except Exception:  # noqa: BLE001
                terms = []
    terms = sorted({t for t in terms if len(t) > 2})
    if terms:
        log.info(f"判据⓪ 同域校验：{len(terms)} 个方向词（运行时提供）")
    else:
        log.warn("[WARN] 未提供 --domain-terms / PAPER_DOMAIN_TERMS，本次运行也无 "
                 "topic_plan.json → 判据⓪ 的同域校验**跳过**："
                 "ORCID 溯源即确认（若该 ORCID 记录含自动导入条目，同名他人会一并确认）")
    orcid_off: list[tuple] = []

    hit, miss = [], []
    for key, r in recs.items():
        # ── 判据 ⓪：ORCID 溯源（优先级最高，肯定性确认）──────────
        #    DOI 出自本人 ORCID 记录 → 直接确认归属，不再要求单位数据。
        #    旧版没有这条判据，是消歧在 ORCID→Crossref 主路径上完全失效的主因。
        _doi = (r.get("doi") or "").strip().lower()
        for _pre in ("https://doi.org/", "http://dx.doi.org/"):
            if _doi.startswith(_pre):
                _doi = _doi[len(_pre):]
        if _doi and _doi in orcid_dois:
            # 同域校验：ORCID 溯源仍是强信号，但**不足以在方向完全不符时单独定案**
            #   —— 自动导入会把同名他人拉进本人记录。命中不了任何方向词的，
            #   既不是肯定性证据也不是否定性证据 → 转**待复核**（不删、不排除）。
            # 判定文本 = 题名 + **刊名** + 摘要。
            #   必须带刊名：Crossref 常不返回摘要，早期成果题名也更朴素
            #   （如 "Effects of nimodipine on medial septal area cells"，刊名才是
            #   Brain Research / Behavioural Brain Research 这类领域信号），
            #   只看题名会把本人 1990s 的正当成果误拦进待复核。
            _ji = (r.get("journalInfo") or {}).get("journal") or {}
            _txt = " ".join([(r.get("title") or ""),
                             (r.get("journal") or ""), (r.get("short_journal") or ""),
                             (_ji.get("title") or ""),
                             (r.get("abstractText") or r.get("abstract") or "")]).lower()
            _kw = [t for t in terms if t in _txt]
            if terms and _txt.strip() and not _kw:
                r["_reason"] = "orcid-offdomain"
                r["_blocks"] = author_blocks(r, o["surname"], o["given"])
                r["_affil_hits"] = []
                r["_domain_words"] = []
                orcid_off.append((r, "ORCID 溯源但题录/摘要不含任何方向词 "
                                     "（疑 ORCID 自动导入的同名他人），转待复核"))
                continue
            r["_reason"] = "orcid-linked"
            r["_blocks"] = author_blocks(r, o["surname"], o["given"])
            r["_affil_hits"] = []
            r["_domain_words"] = _kw[:6]
            hit.append((r, "ORCID 溯源：该 DOI 出自本人 ORCID 成果记录"))
            continue

        blocks = author_blocks(r, o["surname"], o["given"])
        if not blocks:
            # 区分「没数据」与「有数据但没这个人」——语义完全不同，见 has_author_data()
            if not has_author_data(r):
                r["_reason"] = "no-author-data"
                miss.append((r, "记录无作者名单（数据缺失，非否定证据）"))
            else:
                r["_reason"] = "name-mismatch"
                miss.append((r, "作者列表中无「名精确匹配」的目标作者"))
            continue
        blob = " ".join(af for b in blocks for af in b["affils"]).lower()
        matched = [a for a in o["affil"] if a.lower() in blob]
        r["_blocks"] = blocks
        r["_affil_hits"] = matched
        # 无摘要/无作者单位字段的记录：不能判为「非本人」，标为待复核
        if not blob.strip():
            r["_reason"] = "no-affil-data"
            miss.append((r, "目标作者署名块无单位字段（数据缺失，非否定证据）"))
            continue
        if matched:
            r["_reason"] = "affil-hit"
            hit.append((r, f"单位命中 {matched}"))
        else:
            r["_reason"] = "affil-mismatch"
            miss.append((r, f"单位不匹配：{blob[:70]}"))

    log.info(f"判据①②通过: {len(hit)} 条 | 未通过/待复核: {len(miss)} 条")

    # ── 判据 ③：以锚点论文建立合作者指纹 ──────────────────────
    anchor_set = {d.lower() for d in o["anchor_doi"]}
    anchors = [r for r in recs.values() if (r.get("doi") or "").lower() in anchor_set]

    def name_keys(rec):
        """作者「姓氏+首字母」键集合（同一记录内去重）。

        关键设计：**不要**只用姓氏。姓氏在不同人之间大量重合
          （大姓如 Chen / Li / Wang / Zhang），会使无关论文凭「有个同姓的共同作者」
          被判为本人成果。而锚点论文与其它记录的作者名写法不一致
          （「姓 名首字母」vs「名 姓」全拼），全名又比不相等。
          折中为「姓氏+名首字母」：
            <Surname> DR / <Given> <Surname> → "<surname>|d"   [OK] 相等
            <同姓他人> Z vs <同姓合作者> X                      [X] 不等
        """
        out = set()
        for nm in all_authors(rec):
            nm = re.sub(r"\(.*?\)", " ", nm or "").strip()
            if not nm:
                continue
            parts = [re.sub(r"[^A-Za-z\-']", "", p) for p in re.split(r"[\s,]+", nm)]
            parts = [p for p in parts if p]
            if not parts:
                continue
            if len(parts) >= 2 and len(parts[0]) > 3 and len(parts[1]) <= 3:
                surname, initial = parts[0], parts[1]      # "Green DR"
            elif len(parts) >= 2:
                surname, initial = parts[-1], parts[0]     # "Douglas R. Green"
            else:
                continue
            if len(surname) >= 3 and initial:
                out.add(f"{surname.lower()}|{initial[0].lower()}")
        return out

    co_count = collections.Counter()
    for r in anchors:
        for s in name_keys(r):
            co_count[s] += 1
    self_key_prefix = norm_name(o["surname"]) + "|"
    strong_co = {n for n, c in co_count.items()
                 if c >= o["min_coauthor"] and not n.startswith(self_key_prefix)}
    if anchors:
        log.info(f"锚点论文 {len(anchors)} 篇 | 核心合作者（≥2 篇）: {len(strong_co)} 位")
    else:
        log.info("未提供/未匹配到锚点论文 → 跳过判据③（合作者网络）")

    confirmed, review = [], []
    for r, why in hit:
        # 判据⓪ 命中者**直接确认，不参与判据③**。
        #   ORCID 溯源是肯定性证据（作者本人认领），没有理由再让「合作者网络」
        #   投票把它降级。旧版漏了这一步，后果有二：
        #     ① 13 篇 ORCID 成果全被降级为「待复核」；
        #     ② 更危险的是作者数 ≥ large_collab 的（如 22 位作者的 Nature 论文）
        #        会被判 `drop` —— 而 drop 既不入 confirmed 也不入 review，
        #        会**从两处同时消失**，等于把代表作悄悄弄丢。
        if (r.get("_reason") or "") == "orcid-linked":
            r["_verdict"] = "confirmed"
            r["_why"] = "ORCID 溯源：DOI 出自本人 ORCID 成果记录"
            confirmed.append(r)
            continue

        n = len(all_authors(r))
        coa = sorted(name_keys(r) & strong_co)
        if coa:
            r["_verdict"] = "confirmed"
            r["_why"] = ("单位命中 + 合作者重合 " + ", ".join(
                    k.split("|")[0].title() for k in coa[:3]))
            confirmed.append(r)
        elif n >= o["large_collab"]:
            # 超大型合作**不再静默 drop**：改为待复核并写明理由。
            #   「丢弃」不可逆，而大合作论文很可能是本人的正当成果；
            #   宁可留给人工看一眼，也不能让它凭空消失。
            r["_verdict"] = "review"
            r["_why"] = f"超大型合作（{n} 位作者）且无合作者佐证，待人工确认"
            review.append(r)
        elif anchors:
            r["_verdict"] = "review"
            r["_why"] = "单位命中但合作者无重合（可能是本人早期/跨领域合作，需人工确认）"
            review.append(r)
        else:
            r["_verdict"] = "confirmed"
            r["_why"] = f"单位命中 {r['_affil_hits']}（未启用合作者判据）"
            confirmed.append(r)

    # ORCID 溯源但方向不符的 → **待复核**（不是排除：drop 会被 scope.py 套用，
    #   万一是本人跨领域合作就永久丢失；人工看一眼即可定性）。
    for r, why in orcid_off:
        r["_verdict"] = "review"
        r["_why"] = why
        review.append(r)

    # miss 里混着两种性质完全不同的记录，必须**分开报**：
    #   「肯定性判定为他人」才是排除；「数据缺失」只是**没判**，不是排除。
    #   旧版一律打成「排除 N」，会让人误以为 N 篇被剔除了 ——
    #   实测 2026-09-18 该措辞让「13 篇全部无法判定」看起来像「13 篇全被排除」。
    _n_excl = len([1 for r, _w in miss
                   if (r.get("_reason") or "") in ("name-mismatch", "affil-mismatch")])
    _n_data = len(miss) - _n_excl
    _n_orcid = len([1 for r, _w in hit if (r.get("_reason") or "") == "orcid-linked"])
    log.info(f"确认 {len(confirmed)}（其中 ORCID 溯源 {_n_orcid}）| "
             f"[WARN] 待复核 {len(review)} | 排除 {_n_excl} | "
             f"数据缺失/无法判定 {_n_data}")
    if orcid_off:
        log.warn(f"判据⓪ 同域校验拦下 {len(orcid_off)} 条「DOI 在本人 ORCID 里、"
                 f"但题录与方向词完全不命中」→ 已转待复核。"
                 f"这类条目通常来自 ORCID 的自动导入（同名他人），"
                 f"逐条人工确认后可用 scope.py 的排除名单剔除。")
    if _n_data:
        log.warn(f"有 {_n_data} 条**因语料缺少作者/单位信息而无法判定**（非否定证据）。"
                 f"若占比很高，说明候选语料不合格（需逐作者信息，如 Europe PMC 原始记录），"
                 f"而不是这位作者没有成果 —— 下游 scope.py 的安全阀会拒绝套用排除名单。")

    def show(r, tag):
        ji = r.get("journalInfo") or {}
        j = ((ji.get("journal") or {}).get("title") or r.get("journal") or "")[:34]
        blk = (r.get("_blocks") or [{}])[0]
        n = len(all_authors(r)) or r.get("n_authors") or "?"
        p = blk.get("idx")
        postag = ("[第一作者] " if p == 1 else f"[第{p}/{n}作者] ") if p else ""
        print(f"  {r.get('pubYear') or r.get('year')} | {j:34s} | {postag}"
              f"{(r.get('title') or '')[:56]}")
        print(f"        DOI={r.get('doi')} | {tag}")
        # 关键提示：命中的锚点是「具体实验室/系」还是「泛机构名」，
        # 二者证据强度差别很大——泛机构名（如整所大学）会有大量他人命中。
        if r.get("_affil_hits"):
            print(f"        命中锚点: {' ; '.join(r['_affil_hits'])[:100]}")

    print("\n" + "=" * 104)
    print(f"确认为本人成果: {len(confirmed)} 条")
    print("=" * 104)
    for r in sorted(confirmed, key=lambda x: str(x.get("pubYear") or x.get("year") or "")):
        show(r, r["_why"])
    if review:
        print("\n" + "=" * 104)
        print(f"[WARN] 待人工复核: {len(review)} 条")
        print("=" * 104)
        for r in review:
            show(r, r["_why"])

    out = cdpdir / "disambiguated.json"
    out.write_text(json.dumps(
        {"confirmed": confirmed, "review": review,
         "dropped": [{"doi": r.get("doi"), "title": r.get("title"),
                      "year": r.get("pubYear") or r.get("year"),
                      # code 供下游按「肯定性判定 vs 数据缺失」区分，见 scope.py
                      "code": r.get("_reason") or "",
                      "why": w} for r, w in miss],
         "params": {"surname": o["surname"], "given": o["given"], "affil": o["affil"],
                    "anchor_doi": o["anchor_doi"]}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log.ok(f"写入 {out.name}")
    journal("D", "disambiguate.done", candidates=len(recs),
            confirmed=len(confirmed), review=len(review),
            orcid_linked=_n_orcid,
            excluded=_n_excl, undecidable=_n_data,
            surname=o["surname"], given=o["given"],
            affil=o["affil"], anchor_doi=o["anchor_doi"], out=out.name)

    print("\n提示：")
    print("  · 「单位不匹配」是**排除**依据；「署名块无单位字段」是**数据缺失**，")
    print("    对这类记录请用 Crossref/出版社页面补单位后再判，不要直接当作他人论文丢弃。")
    print("  · 判据③依赖 --anchor-doi：请填 2–3 篇**确定无疑**属于本人的代表作。")
    print("  · **锚点粒度决定精度**：--affil 越具体越好（「系/实验室/研究院」），")
    print("    用整所大学名（如「University of Science and Technology of China」）")
    print("    会命中大量同名他人——此时务必逐条核对「命中锚点」行。")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
