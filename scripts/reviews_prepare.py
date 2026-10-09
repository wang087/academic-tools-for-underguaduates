# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 R① ：综述任务包组装（Reviews Prepare）
#
# 为什么需要本脚本
#   `interpret_prepare.py`（阶段 H）与 `refs_prepare.py`（阶段 H2）都绑定在
#   作者链路的题录总表上 —— H 要 `.work_cdp/crossref_all.json`，
#   H2 要 `.work_cdp/refs/high_quality.json`。本科前置链路的输入是
#   **阶段 R 检索到的综述清单**，两者对不上，所以单独出一个。
#
#   本脚本**复用** interpret_prepare 的**任务包 schema**（字段名逐一同名），
#   这样 `subagent-driven-review/SKILL.md` 的并行机制与提示词模板可以原样套用，
#   subagent 不需要知道自己在跑哪条链路。
#
# 与阶段 H / H2 的隔离
#   产物写 `.work_cdp/reviews/`，与 `interpret/`、`refs/` **互不干扰**
#   （三个阶段的 task/result 混放会让各自的 build 脚本读到对方的卡）。
#
# 用法：
#   # ① 从阶段 R 的检索结果组装任务包
#   uv run python 流程1/scripts/reviews_prepare.py --input .work_cdp/reviews/reviews.json
#
#   # ② 直接接 paginate.py 的输出（Europe PMC 原始 JSON）
#   uv run python 流程1/scripts/reviews_prepare.py \
#       --from-europepmc .work_cdp/reviews/europepmc.json
#
#   # ③ 把摘要补齐（可选，联网；缺摘要时任务包仍可用，证据层级标 abstract-only）
#   uv run python 流程1/scripts/reviews_prepare.py --fetch-abstracts
#
#   # ④ 自定义输出目录 / 只处理前 N 篇
#   uv run python 流程1/scripts/reviews_prepare.py --input … --limit 8
#
#   # ⑤ 覆盖综述解读档位（可选；缺省读 .work_cdp/prepare_plan.json 的
#   #    review_interpret_tier，再退回默认 L3）
#   uv run python 流程1/scripts/reviews_prepare.py --input … --tier L2
#   #    档位写进每个任务包与 manifest.json，供 subagent 与 reviews_build 使用
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
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

# ── UTF-8 兜底（与 logutil._fix_stdout 同判据，幂等）──


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
UA = "reviews-prepare/1.0 (academic use; mailto:noreply@example.org)"

# 综述类文献的类型标记（用于判定 field_role 与筛选）
REVIEW_TYPES = ("review", "systematic review", "systematic-review", "meta-analysis",
                "meta analysis", "scoping review", "survey", "perspective", "review-article")


# ══════════════════════════════════════════════════════════════
# 综述解读档位（阶段 R2 用 `review_interpret_tier`，与阶段 H2 共用同一个键）
#
# 档位是**参数不是闸门**（prepare_stage.OPTIONS 里 gates 为空）：
#   计划文件缺失/损坏/取值非法只能提示 + 回退默认 L3，绝不报错退出 ——
#   少一个档位不该让整条链路停摆。
# 本脚本只负责把档位**如实带进每个任务包与 manifest**（供 reviews_build 与
#   subagent 读取），不改变任何既有渲染行为。语义见 skill：`paper-interpretation-writing`。
# 自包含一份助手而不 import 别的脚本：流程内脚本互相 import 会带来 module 级
#   副作用与 stdout 重包的坑（见 _fix_streams 注释），几十行自包含更稳。
# ══════════════════════════════════════════════════════════════
TIER_KEY = "review_interpret_tier"
TIER_VALUES = ("L1", "L2", "L3")
DEFAULT_TIER = "L3"                         # 综述类交付物的默认档位
TIER_LABELS = {"L1": "精简", "L2": "详细", "L3": "完整"}
PLAN_PATH = WD / ".work_cdp" / "prepare_plan.json"


def normalize_tier(raw) -> str:
    """把任意输入规范化成 L1/L2/L3（大小写不敏感）；无法识别返回空串。"""
    s = str(raw or "").strip().upper()
    return s if s in TIER_VALUES else ""


def plan_tier(plan_path: pathlib.Path | None = None) -> tuple[str, str]:
    """读阶段 0 的 `review_interpret_tier`。

    返回 (档位, 提示语)；提示语非空表示发生了**回退**（调用方负责打到 stderr）。
    """
    p = plan_path or PLAN_PATH
    if not p.exists():
        return DEFAULT_TIER, (f"[WARN] 找不到 {p}（阶段 0 未跑？）→ "
                              f"综述解读档位回退默认 {DEFAULT_TIER}")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return DEFAULT_TIER, (f"[WARN] {p.name} 解析失败（{e}）→ "
                              f"综述解读档位回退默认 {DEFAULT_TIER}")
    raw = plan.get(TIER_KEY) if isinstance(plan, dict) else None
    got = normalize_tier(raw)
    if not got:
        return DEFAULT_TIER, (f"[WARN] {p.name} 的 {TIER_KEY}={raw!r} 非法"
                              f"（应为 L1/L2/L3）→ 回退默认 {DEFAULT_TIER}")
    return got, ""


def slugify(s: str, maxlen: int = 80) -> str:
    """题名/DOI → 安全的文件名 stem（与 interpret_prepare 的风格一致）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (s or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:maxlen] or "untitled")


def _unescape(s: str) -> str:
    """还原 HTML 实体，并去掉残留的标签。

    为什么需要：Europe PMC 等接口在 title/abstract 里返回**XML 转义**文本，
      例如 `<i>Enterococcus</i>` 会被转义成 `&lt;i&gt;Enterococcus&lt;/i&gt;`。
      实测这类字符串会一路带进任务包、解读卡，最后出现在
      《领域概貌》的表格里（用户直接看到 `&lt;i&gt;` 这种噪声）。
    """
    if not s:
        return s
    import html as _html
    t = _html.unescape(s)
    t = re.sub(r"</?[a-zA-Z][^>]{0,40}>", "", t)   # 去掉 <i> <b> <sub> 等残留标签
    return re.sub(r"\s+", " ", t).strip()


def norm_record(rec: dict) -> dict:
    """把不同来源的记录归一成统一题录。

    支持三种输入：本流程自建的 reviews.json、Europe PMC 原始 JSON、OpenAlex 风格。
    """
    def pick(*keys, default=""):
        for k in keys:
            v = rec.get(k)
            if v:
                return v
        return default

    title = pick("title", "Title")
    doi = pick("doi", "DOI")
    year = pick("year", "pubYear", "publication_year", "Year")
    journal = pick("journal", "journalTitle", "venue", "Journal")
    authors = pick("authors", "authorString", "Authors")
    abstract = pick("abstract", "Abstract", "abstractText")

    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)

    # Europe PMC 的 pubType 形如 "systematic review; review-article; journal article"
    ptype = str(pick("type", "pubType", "publication_type", "pub_type"))
    is_review = any(t in ptype.lower() for t in REVIEW_TYPES) or \
        bool(re.search(r"\breview\b|\bsurvey\b", ptype, re.I))
    if not ptype:
        # 没有类型字段时用题名兜底判断
        is_review = bool(re.search(r"\breview\b|\bsurvey\b|meta-analysis",
                                   title, re.I))

    oa = str(pick("isOpenAccess", "is_oa", "oa")).upper()
    oa_url = pick("oa_url", "oaUrl", "pdf_url", "open_access_url")
    pmid = pick("pmid", "PMID")
    pmcid = pick("pmcid", "PMCID")

    return {
        "title": _unescape(title),
        "doi": doi.strip(),
        "year": str(year).strip(),
        "journal": _unescape(journal),
        "authors": _unescape(authors),
        "abstract": _unescape(abstract),
        "type": ptype.strip(),
        "is_review": bool(is_review),
        "is_oa": oa in ("Y", "YES", "TRUE", "1"),
        "oa_url": oa_url.strip(),
        "pmid": str(pmid).strip(),
        "pmcid": str(pmcid).strip(),
        "citations": rec.get("citations") or rec.get("cited_by_count") or 0,
        "platform": rec.get("platform") or rec.get("source") or "",
        "why_selected": rec.get("why_selected", ""),
    }


def fetch_abstract(rec: dict, timeout: int = 25) -> str:
    """按 DOI/PMID 从 Europe PMC 补摘要（零凭据）。取不到就返回空串。"""
    q = None
    if rec.get("doi"):
        q = f'DOI:"{rec["doi"]}"'
    elif rec.get("pmid"):
        q = f'EXT_ID:{rec["pmid"]}'
    if not q:
        return ""
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
           + urllib.parse.urlencode({"query": q, "format": "json",
                                     "pageSize": 1, "resultType": "core"}))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        res = (data.get("resultList") or {}).get("result") or []
        if res:
            return (res[0].get("abstractText") or "").strip()
    except Exception:
        pass
    return ""


def build(input_records: list[dict], outdir: pathlib.Path,
          limit: int = 0, fetch_abs: bool = False, log=None,
          tier: str = DEFAULT_TIER) -> dict:
    """组装任务包 + manifest。字段名与 interpret_prepare 的任务包**逐一同名**。

    `tier` = 综述解读档位（L1/L2/L3）。写进**每个任务包**与 manifest，
    便于 subagent 按档位写作、reviews_build 汇总时显示。缺省即默认 L3。
    """
    tier = normalize_tier(tier) or DEFAULT_TIER
    tasks_dir = outdir / "tasks"
    results_dir = outdir / "results"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    recs = [norm_record(r) for r in input_records]
    # 综述优先：非综述排后面（不丢，便于人工复核）
    recs.sort(key=lambda r: (not r["is_review"], -int(r.get("citations") or 0)))
    if limit:
        recs = recs[:limit]

    manifest = []
    n_full = n_abs = 0
    for i, rec in enumerate(recs, 1):
        if fetch_abs and not rec["abstract"]:
            rec["abstract"] = fetch_abstract(rec)
            time.sleep(0.4)          # Europe PMC 限速礼貌间隔

        stem = slugify(rec["doi"] or rec["title"])
        taskp = tasks_dir / f"{stem}.json"
        resp = results_dir / f"{stem}.json"

        abstract = rec["abstract"]
        # 本阶段默认只有摘要（综述全文由阶段 R 决定是否取，不在本脚本范围）
        src = "abstract" if abstract else "metadata-only"
        if src == "abstract":
            n_abs += 1
        else:
            n_full += 1

        packet = {
            "seq": i,
            "interpret_tier": tier,          # 综述解读档位（供 subagent 与 build 读取）
            "doi": rec["doi"],
            "title": rec["title"],
            "journal": rec["journal"],
            "year": rec["year"],
            "authors": rec["authors"],
            "abstract": abstract,
            "evidence_source": src,
            "type": rec["type"],
            "is_review": rec["is_review"],
            "is_oa": rec["is_oa"],
            "oa_url": rec["oa_url"],
            "pmid": rec["pmid"],
            "pmcid": rec["pmcid"],
            "citations": rec["citations"],
            "platform": rec["platform"],
            "why_selected": rec["why_selected"],
            "text": "",                     # 本阶段不注入全文
            "result_path": str(resp),
        }
        taskp.write_text(json.dumps(packet, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest.append({
            "seq": i, "doi": rec["doi"], "year": rec["year"],
            "title": rec["title"][:90], "slug": stem,
            "task": str(taskp), "result": str(resp),
            "evidence_source": src,
            "is_review": rec["is_review"], "is_oa": rec["is_oa"],
            "citations": rec["citations"],
        })
        if log:
            log.info(f"  [{i}/{len(recs)}] {rec['title'][:70]}")

    payload = {
        "workdir": str(WD),
        "stage": "R2",
        "interpret_tier": tier,              # 档位随清单落盘，下游无需再猜
        "count": len(manifest),
        "review_count": sum(1 for m in manifest if m["is_review"]),
        "oa_count": sum(1 for m in manifest if m["is_oa"]),
        "evidence_abstract": n_abs,
        "evidence_metadata_only": n_full,
        "tasks_dir": str(tasks_dir),
        "results_dir": str(results_dir),
        "items": manifest,
    }
    (outdir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="reviews_prepare.py",
        description="阶段 R①：把阶段 R 的综述清单组装成并行 subagent 任务包"
                    "（schema 与 interpret_prepare 对齐）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  reviews_prepare.py --input .work_cdp/reviews/reviews.json\n"
                "  reviews_prepare.py --from-europepmc .work_cdp/reviews/epmc.json "
                "--fetch-abstracts\n"))
    ap.add_argument("--input", help="阶段 R 产出的 reviews.json（推荐）")
    ap.add_argument("--from-europepmc", help="Europe PMC paginate.py 的原始输出 JSON")
    ap.add_argument("--from-json", help="任意记录数组 JSON（字段自动归一）")
    ap.add_argument("--out", default=".work_cdp/reviews", help="输出目录")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少篇（0=全部）")
    ap.add_argument("--fetch-abstracts", action="store_true",
                    help="缺摘要时按 DOI/PMID 从 Europe PMC 补齐（联网）")
    ap.add_argument("--tier", type=str.upper, choices=list(TIER_VALUES),
                    help="覆盖综述解读档位 L1 精简 / L2 详细 / L3 完整"
                         "（缺省读 .work_cdp/prepare_plan.json 的 "
                         f"{TIER_KEY}，再退回默认 {DEFAULT_TIER}）")
    args = ap.parse_args(argv)

    log = get("reviews")

    # ── 档位：命令行 > prepare_plan.json > 默认 L3 ──────────────
    #   非法 --tier 由 argparse 以退出码 2 拒绝（choices 校验），不会静默回退。
    if args.tier:
        tier, tier_src, tier_note = args.tier, "命令行 --tier", ""
    else:
        tier, tier_note = plan_tier()
        tier_src = f"{PLAN_PATH.name} · {TIER_KEY}" if not tier_note else "默认回退"
    if tier_note:
        print(tier_note, file=sys.stderr)

    src = args.input or args.from_europepmc or args.from_json
    if not src:
        print("[X] 需要 --input / --from-europepmc / --from-json 之一", file=sys.stderr)
        return 2
    p = pathlib.Path(src)
    if not p.is_absolute():
        p = WD / p
    if not p.exists():
        print(f"[X] 输入文件不存在：{p}", file=sys.stderr)
        return 2

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[X] 输入解析失败：{e}", file=sys.stderr)
        return 2

    # 四种输入形态：
    #   ① {records:[…]}          —— paginate.py 原始输出
    #   ② {items:[…]}            —— 通用数组包装
    #   ③ {resultList:{result:[]}} —— Europe PMC 原生
    #   ④ {included:[…]}         —— **v13 PRISMA 链的 search_screened.json**
    #      [STOP] 早期版本不认这个键，导致 `search_screen.py → reviews_prepare.py`
    #         这一步实际接不上（实测报「输入里没有记录」），
    #         而文档里却写着"之后接既有的 reviews_prepare.py"。
    if isinstance(data, dict):
        records = None
        rl = data.get("resultList")
        if isinstance(rl, dict) and rl.get("result"):
            records = rl["result"]
        for key in ("records", "items", "included"):
            if records is None and isinstance(data.get(key), list):
                records = data[key]
        if records is None:
            records = []
    else:
        records = data
    if not records:
        print("[X] 输入里没有记录", file=sys.stderr)
        return 2

    outdir = pathlib.Path(args.out)
    if not outdir.is_absolute():
        outdir = WD / outdir

    log.info(f"输入 {len(records)} 条记录 → {outdir}")
    payload = build(records, outdir, limit=args.limit,
                    fetch_abs=args.fetch_abstracts, log=log, tier=tier)

    print()
    print("═" * 66)
    print(f"[OK] 任务包就绪：{payload['count']} 篇")
    print(f"  其中综述类：{payload['review_count']}；开放获取：{payload['oa_count']}")
    print(f"  证据层级：摘要级 {payload['evidence_abstract']} / "
          f"仅题录 {payload['evidence_metadata_only']}")
    print(f"  解读档位：{tier}（{TIER_LABELS.get(tier, '')}；来源：{tier_src}）"
          f" 综述类默认 L3，阶段 0 可用 --set {TIER_KEY}=L1|L2|L3 改")
    print(f"  任务包：{payload['tasks_dir']}")
    print(f"  结果目录：{payload['results_dir']}")
    print()
    print("下一步（阶段 R2）：**在同一条消息内**并行分派 4–6 个 subagent，")
    print("  每篇一个，各读自己的 task.json、各写自己的 result.json。")
    print("  提示词模板见 流程1/subagent-driven-review/SKILL.md §7.2（领域概貌版）。")
    print("═" * 66)
    journal("R", "reviews.prepare", count=payload["count"],
            reviews=payload["review_count"], oa=payload["oa_count"],
            interpret_tier=tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
