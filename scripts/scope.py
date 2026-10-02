# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 「非本人论文」排除名单 —— 同名消歧结果的**唯一读取入口**
#
# 背景：disambiguate.py 产出 <WORKDIR>/.work_cdp/disambiguated.json
#       （{"confirmed": [...], "review": [...], "dropped": [...], "params": {...}}）。
#       但此前**没有任何脚本读它** —— 消歧跑完只打印给人看，下载与著录两侧
#       仍直接用 crossref_all.json，于是同名他人的论文照样被下载、照样进最终
#       交付列表。「非本人论文要在输出中注明并在流程中自动忽略」这条要求，
#       在流程上其实是落空的。
#
# ★ 为什么用**黑名单**（dropped）而不是**白名单**（confirmed）：————
#   disambiguate.py 的候选语料来自 .work_cdp/*.json（含 Europe PMC 原始结果），
#   与 crossref_all.json 未必同源。若按「只保留 confirmed」做白名单，
#   一旦两者语料不一致，**本人成果会被整批误删** —— 这是不可接受的失败模式。
#   而 dropped 是「已明确判定为他人」的记录（带判定理由），只删这些，
#   最坏情况是漏排几条，绝不会把本人的论文删掉。
#   失败方向宁可偏保守：多留几篇可人工剔除，少留则无从恢复。
#
# ★ 三条硬性约定（改动前务必先读）：
#   ① **未做消歧时返回 None，而不是空集合**。None 能明确区分「没跑过消歧」
#      与「跑过且无需排除」，便于日志与交付物如实表述。
#   ② **review（待复核）绝不排除** —— 它对应「署名块无单位字段」这类
#      **数据缺失**，不是否定证据；把待复核当他人丢弃会漏收真论文。
#   ③ **dropped 里也混着「数据缺失」条目，必须按 code 过滤后再排除。————
#      disambiguate.py 的 dropped 同时收纳两种性质的记录：
#        · name-mismatch / affil-mismatch —— 肯定性判定，**可排除**
#        · no-affil-data                   —— 单位字段缺失，**不可排除**
#      （该脚本末尾自己就警告「不要直接当作他人论文丢弃」。）
#      所以这里只排除肯定性判定码；旧版 disambiguated.json 没有 code 字段时，
#      按 why 文本里的「数据缺失 / 无单位字段」措辞兜底识别，宁可少排除。
#
# ★ 逃生阀：设 PAPER_SCOPE_INCLUDE_ALL=1 可**完全关闭排除**（不删任何条目），
#   用于怀疑误排时快速对照全量结果。
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import os
import pathlib

_FILE = "disambiguated.json"

# 可作排除依据的判定码（**肯定性**判定：有证据表明不是本人）
_EXCLUDABLE_CODES = {"name-mismatch", "affil-mismatch"}
# 旧版 disambiguated.json 无 code 字段，只能按 why 措辞兜底识别「数据缺失」
_NON_EXCLUDABLE_MARKERS = ("无单位字段", "无作者名单", "数据缺失", "非否定证据")

# ★ 安全阀：排除比例上限。超过即判定为**语料有问题**而非作者真没成果，拒绝排除。
#   由来（2026-09-18 实测）：郭傲案例的候选语料是 orcid_works_parsed.json，
#   里面**没有作者名单**，disambiguate 据此把 13 篇**全部**判成「他人」。
#   若照单全收，交付列表会被清空 —— 这是不可逆的交付事故。
#   注意：这里是「拒绝排除 + 大声告警」，不是「静默放行」。
MAX_EXCLUDE_RATIO = 0.8
MIN_EXCLUDE_ABS = 4          # 少于这么多条时不做比例判断（样本太小，比例无意义）


def _norm(doi) -> str:
    """DOI 归一化：去 URL 前缀、去空白、小写 —— 与 crossref_all.json 的键一致。"""
    d = str(doi or "").strip().lower()
    for pre in ("https://doi.org/", "http://doi.org/",
                "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(pre):
            d = d[len(pre):]
    return d.strip()


def load(cdpdir) -> dict | None:
    """读取 disambiguated.json；不存在或不可解析则返回 None。"""
    try:
        d = json.loads((pathlib.Path(cdpdir) / _FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def dropped_list(cdpdir) -> list[dict]:
    """disambiguated.json 的 dropped 全量条目（含判定理由与 code）。

    ★ 注意：这是**未过滤**的原始列表，其中包含「数据缺失」类条目，
      不能直接拿来当排除名单 —— 排除请用 excluded_dois()。
    """
    d = load(cdpdir) or {}
    out = []
    for r in d.get("dropped") or []:
        if isinstance(r, dict):
            out.append({"doi": _norm(r.get("doi")),
                        "title": r.get("title") or "",
                        "year": r.get("pubYear") or r.get("year") or "",
                        "code": (r.get("code") or "").strip(),
                        "why": r.get("why") or ""})
    return out


def is_excludable(rec: dict) -> bool:
    """该 dropped 条目是否**可以**作为排除依据。

    肯定性判定（name-mismatch / affil-mismatch）→ 可以；
    「单位字段缺失」这类数据缺失 → 不可以（漏收真论文的代价不可接受）。
    """
    code = (rec.get("code") or "").strip()
    if code:
        return code in _EXCLUDABLE_CODES
    why = rec.get("why") or ""
    return not any(m in why for m in _NON_EXCLUDABLE_MARKERS)


def excluded_dois(cdpdir, total: int | None = None) -> set[str] | None:
    """应排除的 DOI 集合（仅含**肯定性**判定为他人、且带得出 DOI 的条目）。

    返回 **None** = 未做消歧 → 调用方保持原行为，不做任何过滤。

    total —— 候选语料的总条数（可选）。给了就启用**安全阀**：当排除比例过高时
             判定为语料异常并**拒绝排除**（大声告警），避免把本人成果整批删掉。
    """
    if load(cdpdir) is None:
        return None
    if os.environ.get("PAPER_SCOPE_INCLUDE_ALL", "").strip().lower() in ("1", "true", "yes"):
        return set()                     # 逃生阀：关闭排除，便于对照全量

    exc = {r["doi"] for r in dropped_list(cdpdir)
           if r["doi"] and is_excludable(r)}

    if total and len(exc) >= MIN_EXCLUDE_ABS and len(exc) >= total * MAX_EXCLUDE_RATIO:
        import sys as _sys
        _sys.stderr.write(
            f"\n⚠️ 安全阀触发：消歧判定「他人论文」{len(exc)}/{total} 条"
            f"（≥{MAX_EXCLUDE_RATIO:.0%}），已**拒绝套用排除名单**。\n"
            f"   这几乎一定是**候选语料有问题**（例如记录里根本没有作者名单），\n"
            f"   而不是这位作者真的只有这么点成果。\n"
            f"   请检查 .work_cdp/disambiguated.json 的 params 与 dropped[].code，\n"
            f"   确认判据语料是否包含逐作者信息（Europe PMC 原始记录才有）。\n"
            f"   如确需强制排除，设 PAPER_SCOPE_INCLUDE_ALL=0 并手工筛选。\n\n")
        return set()
    return exc


def params(cdpdir) -> dict:
    """消歧时用的判据参数（姓/名/单位指纹/锚点 DOI），用于交付物溯源。"""
    d = load(cdpdir) or {}
    p = d.get("params")
    return p if isinstance(p, dict) else {}


def summary(cdpdir) -> str:
    """一行摘要，供日志与交付物使用。**分别报出「已排除」与「证据不足未排除」。**"""
    d = load(cdpdir)
    if d is None:
        return "未做同名消歧（无 disambiguated.json）→ 不做排除"
    dropped = dropped_list(cdpdir)
    n_ex = len([r for r in dropped if r["doi"] and is_excludable(r)])
    n_hold = len(dropped) - n_ex
    s = (f"已消歧：确认 {len(d.get('confirmed') or [])} 条 + "
         f"待复核 {len(d.get('review') or [])} 条（均保留），"
         f"排除同名他人 {n_ex} 条")
    if n_hold:
        s += f"；另 {n_hold} 条因单位字段缺失（非否定证据）未排除、保留待人工核"
    return s


def find_workdir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()
