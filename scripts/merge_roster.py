# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S 合并：学院官网数据并入 roster，并统一清洗
#
# 为什么要有这个独立步骤
#   阶段 S（faculty.ustc.edu.cn）与阶段 S2（学院官网）是两套站点、两种模板，
#   各自抽出的方向条目质量不同，必须**合并后再统一清洗一次**。
#
# [STOP] 踩过的坑（务必按这个顺序，且**只做一遍**）
#   曾经先跑 merge、再跑 clean(松)、再跑 clean(严)，三个脚本串跑，
#   每个都在前一个的输出上再过滤，误差累积，把 190 人砍到 144 人。
#   正确顺序：**恢复 → 合并 → 清洗**，在一次运行内完成。
#
# [STOP] 另一条教训：清洗后为空时**不要**覆盖原有的 no_direction_reason。
#   否则「官网暂无内容（empty_panel）」会被错写成
#   「判定为导航行（filtered_as_non_direction）」，丢失真实原因。
#
# 用法：
#   uv run python 流程1/scripts/merge_roster.py \
#       --roster outputs/01_导师名单/roster_detail.json \
#       --college outputs/05_学院官网/college_detail.json
#
#   从备份重跑（推荐，保证可复现）：
#   uv run python 流程1/scripts/merge_roster.py --roster <...> \
#       --college <...> --from-backup
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import sys

from logutil import run_root as _run_root  # noqa: E402

# ── UTF-8 兜底 ──


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

# 判据集中在这里引用的 harvest_mentors._is_direction —— 不要在本文件另写一套，
# 否则两处标准会漂移（曾经漂移导致 9 条残留漏网）。
import harvest_mentors as HM  # noqa: E402


def _norm(n: str) -> str:
    """姓名归一：去掉空白与常见分隔符，用于跨站点匹配（两站无共同 id）。"""
    return re.sub(r"[\s·．.、,，]", "", (n or "")).lower()


def _valid(ds) -> list[str]:
    """只保留通过判据的方向条目。

    为什么先过滤再比数量：旧判据直接比 `len()`，遇到
      「roster 1 条面包屑 vs 学院页 1 条真方向」这种 tie 时会跳过合并，
      实测侯文韬/项晟祺/马洪第 3 人的真方向因此被丢弃。
    """
    out = []
    for d in (ds or []):
        t = re.sub(r"\s+", " ", d).strip()
        if HM._is_direction(t):
            out.append(t)
    return out


def merge(roster: list[dict], college: list[dict]) -> dict:
    """把学院官网方向并入 roster（学院页更优时覆盖）。返回统计。"""
    by_name: dict[str, dict] = {}
    for c in college:
        if c.get("directions"):
            by_name.setdefault(_norm(c["name"]), c)

    filled = upgraded = skipped = 0
    for r in roster:
        c = by_name.get(_norm(r.get("name", "")))
        if not c:
            continue
        cd, rd = _valid(c["directions"]), _valid(r.get("directions"))
        if len(cd) > len(rd):
            if rd:
                r["direction_alt"] = rd      # 保留原来源，不丢
                upgraded += 1
            else:
                filled += 1
            r["directions"] = list(cd)
            r["direction_source"] = "学院官网"
            r["direction_source_url"] = c["url"]
            r["college"] = c.get("college", "")
            r["status"] = "ok"
            r.pop("no_direction_reason", None)
            r.pop("why", None)
        else:
            skipped += 1
    return {"filled": filled, "upgraded": upgraded, "skipped": skipped}


def _strip_fund(t: str) -> str:
    """只剥掉条目里的**基金/项目括号**，保留中文主干。

    为什么需要：实测涂晓明的三条是
        「锥体虫细胞周期调控的分子机制研究（国家自然科学基金项目,中科院创新项目子课题）」
      括号里是基金信息应当去掉，但**主干是真实研究方向**。
      若因为"含基金字样"整条丢弃，会误杀 3 条真方向（实测发生过）。
    """
    t2 = re.sub(r"[（(][^（()）]*(?:基金|项目|子课题|973|863|重点研发|"
                r"面上项目|中科院|资助)[^（()）]*[)）]", "", t)
    return re.sub(r"\s+", " ", t2).strip("  ·-—，,。.；;：:、")


def clean(roster: list[dict]) -> int:
    """统一清洗（**只做一遍**）。返回剔除条数。

    步骤：先剥基金括号 → 再用 HM._is_direction 判定。
    顺序很重要：先剥再判，否则带基金括号的真方向会被整条误杀。
    """
    removed = 0
    for x in roster:
        before = x.get("directions") or []
        keep = []
        for d in before:
            t = re.sub(r"\s+", " ", d).strip()
            t = _strip_fund(t)              # 先剥括号
            if t and HM._is_direction(t):   # 再判定
                if t not in keep:
                    keep.append(t)
            else:
                removed += 1
        x["directions"] = keep
        if keep:
            x["status"] = "ok"
            x.pop("no_direction_reason", None)
            x.pop("why", None)
        elif before:
            x["status"] = "no_direction"
            x["no_direction_reason"] = "filtered_as_non_direction"
            x["why"] = "抓到的文本经判定为导航/论文/成果行，非研究方向"
        # before 本来就为空 → **保留**原有 no_direction_reason
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="merge_roster.py",
        description="合并学院官网方向进 roster 并统一清洗（只跑一遍，避免误差累积）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roster", required=True, help="roster_detail.json 路径")
    ap.add_argument("--college", required=True, help="college_detail.json 路径")
    ap.add_argument("--from-backup", action="store_true",
                    help="先从 roster_detail.backup.json 恢复再合并（推荐，保证可复现）")
    args = ap.parse_args(argv)

    log = get("merge")
    rp = pathlib.Path(args.roster)
    if not rp.is_absolute():
        rp = WD / rp

    if args.from_backup:
        bak = rp.with_name(rp.stem + ".backup" + rp.suffix)
        if not bak.exists():
            raise SystemExit(f"[X] 找不到备份 {bak}")
        roster = json.loads(bak.read_text(encoding="utf-8"))
        log.info(f"已从备份恢复：{bak.name}（{len(roster)} 人）")
    else:
        roster = json.loads(rp.read_text(encoding="utf-8"))

    cp = pathlib.Path(args.college)
    if not cp.is_absolute():
        cp = WD / cp
    college = json.loads(cp.read_text(encoding="utf-8"))

    st = merge(roster, college)
    removed = clean(roster)
    rp.write_text(json.dumps(roster, ensure_ascii=False, indent=2), encoding="utf-8")

    wd = sum(1 for x in roster if x.get("directions"))
    print()
    print("═" * 62)
    print(f"[OK] 合并完成：新补 {st['filled']} 人，升级 {st['upgraded']} 人，"
          f"无变化 {st['skipped']} 人")
    print(f"  清洗剔除 {removed} 条")
    print(f"  最终：**{wd}/{len(roster)} 人有研究方向**"
          f"（{wd/len(roster)*100:.0f}%）")
    print("═" * 62)
    journal("S", "roster.merge", total=len(roster), with_dir=wd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
