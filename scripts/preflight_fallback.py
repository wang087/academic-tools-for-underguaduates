# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 备选下载链路 · 前置检查（阶段 E 备用）
#
# 干什么：主下载链路（download_all_fulltexts.py，走 CDP + 自家反爬）失败时，
#         转用 **备选链路**。备选链路是**外部 skill**，需要 Node.js 与
#         user 自己的凭据/机构资源地址，因此**不能假定现成可用** ——
#         本脚本只做**只读探测**，把「哪些能力现在真的能用」讲清楚。
#
# 设计原则（与主流程一致）：
#   · **只读**：不写任何文件、不装任何依赖、不发论文站点的请求
#   · **不臆造**：探不到就如实说「不可用」，不假装能用
#   · 退出码说话，不用解析 stdout
#
# 用法：
#   uv run python scripts/preflight_fallback.py
#   uv run python scripts/preflight_fallback.py --json
#
# 退出码：
#   0 = 至少一条备选路线可用
#   3 = 备选链路当前不可用（缺前置）—— 主链路失败时只能如实报告限制
#   2 = 用法错误
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

from logutil import run_root as _run_root  # noqa: E402

MIN_NODE_MAJOR = 22


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；与 fetch_direct.py 同名函数逐字一致）。"""
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

WD = pathlib.Path(os.environ.get("PAPER_WORKDIR") or str(_run_root())).resolve()

# 备选链路所在位置（按优先级探测）
CANDIDATE_ROOTS = [
    WD / "nature-skills-main" / "skills" / "nature-downloader",
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "nature-skills-main" / "skills" / "nature-downloader",
    pathlib.Path(__file__).resolve().parent.parent / "vendor" / "nature-downloader",
]

# 其它外部依赖 skill（主链路已经/可能用到的）
EXT_SKILLS = {
    "paper-lookup": "PRISMA 检索链的取数层（search_run.py 依赖其 paginate.py）",
    "literature-review": "系统综述方法论",
    "citation-management": "DOI/PMID 校验、去重、引用格式化",
    "scientific-critical-thinking": "综述质量证据分级（GRADE）",
    "bgpt-paper-search": "全文结构化实验数据抽取",
    "paper-interpretation-writing": "解读档位的规范定义",
}


def find_downloader() -> pathlib.Path | None:
    for r in CANDIDATE_ROOTS:
        if (r / "scripts" / "batch_download.mjs").is_file():
            return r
    return None


def node_version() -> tuple[str | None, int | None]:
    exe = shutil.which("node")
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, timeout=20)
        v = (out.stdout or b"").decode().strip().lstrip("v")
        major = int(v.split(".")[0]) if v and v.split(".")[0].isdigit() else None
        return v, major
    except Exception:
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="preflight_fallback.py",
        description="备选下载链路前置检查（只读；不联网访问论文站点）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    rep: dict = {"schema": "flow1-fallback-preflight/1.0",
                 "workdir": str(WD), "routes": {}, "external_skills": {}}

    # ── 主链路 ────────────────────────────────────────────────
    nv, nmaj = node_version()
    rep["node"] = {"version": nv,
                   "ok": bool(nmaj and nmaj >= MIN_NODE_MAJOR),
                   "min_required": MIN_NODE_MAJOR}

    nd = find_downloader()
    rep["nature_downloader"] = {"path": str(nd) if nd else None, "found": nd is not None}

    if nd:
        # ── 备选路线 1：纯 OA（零凭据）────────────────────────
        #   batch_download.mjs --open-access / OA 路由不需要机构凭据。
        rep["routes"]["oa"] = {
            "available": bool(nmaj and nmaj >= MIN_NODE_MAJOR),
            "needs": "Node.js >= %d" % MIN_NODE_MAJOR,
            "how": f'node "{nd / "scripts" / "batch_download.mjs"}" '
                   f'--dois <DOI,...> --out <目录> --no-si',
            "note": "开放获取路线，零凭据。**正文取回仍属第二层**，"
                    "只是把「怎么取」委托给这个 skill。",
        }
        # ── 备选路线 2：出版商 API（需凭据）─────────────────
        cfgdir = pathlib.Path(os.environ.get(
            "LIT_DL_CONFIG_DIR", pathlib.Path.home() / ".config" / "lit-dl"))
        cred = cfgdir / "credentials.json"
        rep["routes"]["publisher_api"] = {
            "available": bool(nmaj and nmaj >= MIN_NODE_MAJOR) and cred.is_file(),
            "needs": "Node.js + %s" % cred,
            "config_dir": str(cfgdir),
            "credentials_present": cred.is_file(),
            "how": "python scripts/configure_credentials.py（在 skill 目录下跑）",
            "note": "Elsevier / Springer Nature / IEEE 的官方 API 路线。",
        }
        # ── 备选路线 3：机构 Web Access（需机构资源地址 + 已登录浏览器）
        #   判定必须保守：`schools.yaml` 出厂是**故意留空**的
        #     （institution-neutral），只有用户跑过 configure_school.py 才会有内容。
        #     早先写成 `"schools: []" not in txt` 是**反了**，会把「未配置」
        #     报成「可用」—— 那正是本流程最忌讳的过度声称。
        schools = nd / "data" / "schools.yaml"
        school_configured = False
        try:
            compact = schools.read_text(encoding="utf-8").replace(" ", "")
            school_configured = "schools:[]" not in compact
        except Exception:
            school_configured = False

        # 已认证会话：只能**自述**，探不到就按「未确认」处理
        session_selfreported = os.environ.get("LIT_DL_AUTHENTICATED") == "1"

        rep["routes"]["institutional"] = {
            "available": bool(nmaj and nmaj >= MIN_NODE_MAJOR)
                         and school_configured and session_selfreported,
            "needs": "Node.js + 机构资源地址（configure_school.py url <URL>）+ 已认证浏览器",
            "school_configured": school_configured,
            "session_selfreported": session_selfreported,
            "how": "python scripts/configure_school.py url \"https://<你的图书馆资源页>\"",
            "note": "[WARN] 该路线需要**用户自己的**机构订阅与已登录会话；"
                    "本流程不代填、不代登录。未配置时如实报「不可用」。",
        }
        # ── 备选路线 4：CNKI（中文文献，需机构）──────────────
        rep["routes"]["cnki"] = {
            "available": bool(nmaj and nmaj >= MIN_NODE_MAJOR)
                         and school_configured and session_selfreported,
            "needs": "Node.js + 机构 Web Access + 已认证 Chrome（远程调试）",
            "how": "node batch_download.mjs --cnki-url <URL> --out <目录> --no-si",
            "note": "中文文献专用；主链路（CDP）覆盖不到 CNKI 时的唯一补充。",
        }
    else:
        rep["routes"]["oa"] = {
            "available": False,
            "needs": "nature-downloader skill（未找到）",
            "searched": [str(p) for p in CANDIDATE_ROOTS],
        }

    # ── 其它外部依赖 skill ───────────────────────────────────
    roots = [WD / "skills", WD.parent / "skills",
             pathlib.Path(__file__).resolve().parent.parent.parent / "skills"]
    for name, why in EXT_SKILLS.items():
        hit = next((r / name for r in roots if (r / name).is_dir()), None)
        rep["external_skills"][name] = {"present": hit is not None,
                                        "path": str(hit) if hit else None,
                                        "why": why}

    # ── 结论 ─────────────────────────────────────────────────
    usable = [k for k, v in rep["routes"].items() if v.get("available")]
    rep["usable_routes"] = usable
    rep["exit_code"] = 0 if usable else 3

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return rep["exit_code"]

    print()
    print("═" * 68)
    print("  备选下载链路 · 前置检查（只读）")
    print("═" * 68)
    node_s = rep["node"]["version"] or "未找到"
    print(f"  Node.js           {node_s}"
          f"  {'[OK]' if rep['node']['ok'] else f'[X] 需要 >={MIN_NODE_MAJOR}'}")
    print(f"  nature-downloader {rep['nature_downloader']['path'] or '未找到'}")
    print()
    print("  备选路线：")
    labels = {"oa": "开放获取（零凭据）", "publisher_api": "出版商 API（需凭据）",
              "institutional": "机构 Web Access（需机构地址）",
              "cnki": "CNKI 中文文献（需机构）"}
    for k, v in rep["routes"].items():
        mark = "可用" if v.get("available") else "不可用"
        print(f"    {mark}  {labels.get(k, k)}")
        if not v.get("available"):
            print(f"            需要：{v.get('needs')}")
    print()
    print("  外部依赖 skill：")
    for n, v in rep["external_skills"].items():
        print(f"    {'[OK]' if v['present'] else '[X]'} {n:<32} {v['why']}")
    print()
    if usable:
        print(f"  结论（退出码 0）：可用路线 = {', '.join(usable)}")
        print("  主链路失败时，按 SKILL.md 阶段 E「备选链路」一节转用。")
    else:
        print("  结论（退出码 3）：备选链路当前不可用 —— 主链路失败时")
        print("  **只能如实报告限制**，不得伪装成已获取。")
    print("═" * 68)
    return rep["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
