# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 用 Crossref 回填权威作者全名与卷期页（阶段 D 补充）
#
# 为什么需要：Europe PMC / PubMed 只给**缩写**（"<Surname> A" 形式），
# 而 GB/T 7714 著录与「共同第一作者」判定都需要**全名**与精确署名序。
# 更关键的是：靠字符串猜「哪一段是姓」会出错——曾把双字名（如 "Given Family"
# 中的 "Family"）误当成姓首字母。Crossref 返回结构化的 family/given，
# 直接存下来，著录时无需再猜。
#
# 输入：<WORKDIR>/.work_cdp/crossref_all.json（默认；--in= 可指定）
#       兼容历史名 master.json —— 两者形状不同，本脚本都能读：
#         crossref_all.json = {"<doi>": {…}}   ← fetch_crossref_all.py 的产物
#         master.json       = [{…}, {…}]        ← 早期案例的题录总表
# 产出：**就地回填**同形状文件，字段符合 build_list.py 的读取契约：
#         authors        [{family, given, name}]  —— ★ 字典列表，不是字符串列表
#         authors_struct [{family, given}]
#         vol / issue / page / article_number
#         author_position（给了 --surname/--given 时）
#       并在 <WORKDIR>/.work_cdp/crossref_authors.json 留档
#
# 本脚本属「元数据 API」类（Crossref REST），归 §5.1 分层策略的**第一层**，允许直连。
#
# 用法：
#   uv run python enrich_crossref_authors.py [--in=…] [--sleep=0.4]
#                                           [--surname=<姓>] [--given=<名>]
#   （--surname/--given 缺省时回退 PAPER_SURNAME / PAPER_GIVEN 环境变量）
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from logutil import get as get_log, journal, workdir as _wd   # noqa: E402

UA = "AuthorPaperAnalysis/1.0 (mailto:author-paper-analysis@example.org)"


def strip_tags(s) -> str:
    """去 HTML 标签并反转义 —— Crossref 的部分字段带 <i>/<sub> 等标记。"""
    import html as _html
    import re as _re
    if not s:
        return ""
    return _html.unescape(_re.sub(r"<[^>]+>", "", str(s)))


def get(url: str, tries: int = 4, timeout: int = 45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for a in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))


def _default_src(cdpdir: pathlib.Path) -> pathlib.Path:
    """默认输入：优先 crossref_all.json —— 那才是本流程实际产出的题录总表；
    只有它不存在时才回退历史名 master.json（早期案例的产物）。"""
    for n in ("crossref_all.json", "master.json"):
        if (cdpdir / n).exists():
            return cdpdir / n
    return cdpdir / "crossref_all.json"


def _records(container):
    """把两种顶层形状统一成**记录列表**。

    ★ 返回的是**原字典对象的引用**（不是副本），因此对 rec 的改动
      会自动反映到 container 上，写回时直接 dump container 即可，
      无需关心原本是 dict 还是 list。
    """
    if isinstance(container, dict):
        out = []
        for k, v in container.items():
            if isinstance(v, dict):
                v.setdefault("doi", k)
                out.append(v)
        return out
    if isinstance(container, list):
        return [r for r in container if isinstance(r, dict)]
    return []


def _display_names(authors) -> list[str]:
    """兼容两种 authors 形状（字典列表 / 字符串列表）——仅用于日志展示。"""
    out = []
    for a in authors or []:
        if isinstance(a, dict):
            n = a.get("name") or ", ".join(
                x for x in (a.get("family"), a.get("given")) if x)
        else:
            n = str(a)
        if n:
            out.append(n)
    return out


def main() -> int:
    wd = _wd()
    cdpdir = wd / ".work_cdp"
    src = None
    sleep = 0.4
    surname = given = ""
    for a in sys.argv[1:]:
        if a.startswith("--in="): src = pathlib.Path(a.split("=", 1)[1]).resolve()
        elif a.startswith("--sleep="): sleep = float(a.split("=", 1)[1])
        elif a.startswith("--surname="): surname = a.split("=", 1)[1]
        elif a.startswith("--given="): given = a.split("=", 1)[1]
    # 缺省从环境变量取（与 build_list.py / disambiguate.py 同一套变量）
    surname = surname or os.environ.get("PAPER_SURNAME", "").strip()
    given = given or os.environ.get("PAPER_GIVEN", "").strip()
    src = src or _default_src(cdpdir)

    if not src.exists():
        print(f"✗ 找不到输入文件: {src}")
        print("  请先运行 fetch_crossref_all.py 产出 crossref_all.json，"
              "或用 --in= 指定题录文件。")
        return 1
    log = get_log("enrich_crossref_authors", wd=wd)
    container = json.loads(src.read_text(encoding="utf-8"))
    master = _records(container)
    if not master:
        print(f"✗ {src.name} 里没有可用的记录"
              "（支持 {doi: 记录} 与 [记录, …] 两种形状）")
        return 1
    if not surname or not given:
        log.warn("未提供 --surname/--given（或 PAPER_SURNAME/PAPER_GIVEN）"
                 "→ 跳过 author_position 回填，其余字段照常更新")

    changed, failed = 0, 0
    archive = {}
    for rec in master:
        doi = rec.get("doi")
        if not doi:
            continue
        try:
            m = get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")["message"]
        except Exception as e:
            failed += 1
            log.warn(f"Crossref 查询失败 {doi}: {type(e).__name__}")
            continue

        # ★ authors 必须是**字典列表** [{family, given, name}]，与
        #   fetch_crossref_all.py 写出的契约一致。历史版本这里写成字符串
        #   列表，会让 build_list.py 的 `a.get("name")` 抛
        #   AttributeError: 'str' object has no attribute 'get'，整条著录链崩掉。
        authors, structured, names = [], [], []
        for a in m.get("author", []) or []:
            fam = (a.get("family") or "").strip()
            giv = (a.get("given") or "").strip()
            if not (fam or giv):
                continue
            nm = f"{fam}, {giv}" if fam and giv else (fam or giv)
            # ★ 一并回填逐作者单位 —— 同名消歧的单位指纹判据依赖它（见 fetch_crossref_all.py）
            affs = []
            for x in (a.get("affiliation") or []):
                if isinstance(x, dict):
                    v = strip_tags(x.get("name", ""))
                    if v:
                        affs.append(v)
            authors.append({"family": fam, "given": giv, "name": nm, "affiliation": affs})
            if fam:
                structured.append({"family": fam, "given": giv})
            names.append(nm)
        if authors:
            old = " / ".join(_display_names(rec.get("authors")))
            new = " / ".join(names)
            if old != new:
                changed += 1
                log.info(f"↻ {doi}\n    旧: {old[:100]}\n    新: {new[:100]}")
            rec["authors"] = authors
            rec["authors_struct"] = structured
            rec["n_authors"] = len(authors)

        # 卷期页：同时写 build_list.py 读取的 vol/page/article_number
        # 与 Crossref 原始键名 volume/pages（历史工作目录仍可读）
        for key in ("volume", "issue"):
            v = m.get(key)
            if v:
                rec[key] = str(v)
                rec["vol" if key == "volume" else key] = str(v)
        pg = m.get("page")
        if pg:
            rec["pages"] = str(pg)
            rec["page"] = str(pg)
        an = m.get("article-number")
        if an:
            rec["article_number"] = str(an)
            if not pg:
                rec["pages"] = str(an)

        # 重算本人署名位次（结构化 family 比较，避免姓名写法差异）
        if surname and given:
            for i, a in enumerate(structured, 1):
                if (a["family"].lower() == surname.lower()
                        and a["given"].lower().startswith(given.lower())):
                    rec["author_position"] = i
                    break

        archive[doi] = {"title": (m.get("title") or [""])[0],
                        "authors_struct": structured,
                        "volume": m.get("volume"), "issue": m.get("issue"),
                        "page": m.get("page"), "article-number": m.get("article-number"),
                        "container-title": (m.get("container-title") or [""])[0],
                        "issued": m.get("issued")}
        time.sleep(sleep)

    # 写回**原形状**（dict 仍为 dict、list 仍为 list）——见 _records() 的引用语义
    src.write_text(json.dumps(container, ensure_ascii=False, indent=2), encoding="utf-8")
    (cdpdir / "crossref_authors.json").write_text(
        json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")
    log.ok(f"回填完成：更新 {changed} 条 / 失败 {failed} 条 / 共 {len(master)} 条")
    journal("D", "crossref.enrich.done", input=src.name, total=len(master),
            updated=changed, failed=failed,
            surname=surname or None, given=given or None)
    print(f"\n>>> 已写回 {src.name}，原始 Crossref 作者记录留档 crossref_authors.json")
    print("    后续著录请使用 authors_struct（family/given 已分开），不要再按空格猜姓。")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
