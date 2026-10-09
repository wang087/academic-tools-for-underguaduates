# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 并行素材抽取调度器（阶段 E 的「边下载边抽取」）
#
# 干什么：**不等整批下载跑完**。前台 CDP 一取到 PDF，本脚本就把该篇的
#         「全文文本」与「图片素材」抽出来，供阶段 H/H2/R2 的 subagent
#         任务包直接使用。三条产物互不依赖，可先到先用。
#
# 为什么能并行：
#   ① 下载的瓶颈是**网络等待**（导航+出版社响应），CPU/内存远没到顶；
#      抽取是本地 CPU 活，正好填满等待窗口。
#   ② `download_all_fulltexts.py` 已经把页内正文顺带落盘到
#      `.work_cdp/fulltext/<slug>.txt`（零额外请求），本脚本只补
#      「按页分节文本」与「图注页索引 / 主图」，不重复抓网络。
#   ③ 抽取产物与下载产物同名对齐（都以 PDF 主干的 slug 为键），
#      所以**幂等**：重复跑会跳过已完成的篇目。
#
# 用法：
#   uv run python scripts/extract_parallel.py                 # 增量跑一轮
#   uv run python scripts/extract_parallel.py --min-pages=6   # 跳过残缺件
#   uv run python scripts/extract_parallel.py --no-figures    # 只抽文本
#   uv run python scripts/extract_parallel.py --then-prepare  # 跑完顺带组任务包
#   uv run python scripts/extract_parallel.py --status        # 只看进度
#
# 退出码：0=本轮有进展或已全部完成；3=还有篇目没抽取（可再跑）；2=用法/环境错误
#
# 可移植配置：PAPER_WORKDIR
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
import time


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

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import store  # noqa: E402
from logutil import get as get_log, workdir as _wd  # noqa: E402

WD = _wd()
CDPDIR = WD / ".work_cdp"
MDDIR = CDPDIR / "md"            # prep_paper.py 产出（按页分节文本）
FIGDIR = CDPDIR / "figures"      # extract_figures.py 产出（内嵌位图）
STATE = CDPDIR / "extract_state.json"
LOG = get_log("extract")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    """原子写：先写临时文件再替换，避免中断留下半个 JSON。"""
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


# 包名 → 导入名（用于探测当前解释器是否已具备该依赖）
_IMPORT_NAME = {"pymupdf": "fitz", "pypdf": "pypdf", "pillow": "PIL"}


def _has(pkg: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(_IMPORT_NAME.get(pkg, pkg)) is not None


def _py(script: str, *args: str, with_pkgs: tuple[str, ...] = ()) -> tuple[int, str]:
    """调用同目录脚本；返回 (退出码, 输出尾部)。

    依赖解析顺序（很重要）：
      ① **当前解释器已有** → 直接用 `sys.executable`。
         这也是唯一能继承调用方 `uv run --with …` 上下文的方式 ——
         嵌套 `uv run` **不会**继承父进程的 `--with`。
      ② 当前解释器缺 → 用 `uv run --with <pkg>` 拉起一个带依赖的子进程。
      ③ 连 uv 都没有 → 明确失败，**返回 127 而不是硬跑出一个假结果**。
    """
    missing = tuple(p for p in with_pkgs if not _has(p))
    if not missing:
        cmd = [sys.executable, str(_HERE / script)]
    else:
        uv = shutil.which("uv")
        if not uv:
            return 127, (f"missing {', '.join(missing)} and no uv on PATH；"
                         f"请用 uv run --with {' --with '.join(missing)} python {script}")
        cmd = [uv, "run"]
        for pkg in missing:
            cmd += ["--with", pkg]
        cmd += [sys.executable, str(_HERE / script)]
    cmd += [str(a) for a in args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, (out + err).strip()[-400:]


def _needs_text(slug: str) -> bool:
    return not (MDDIR / f"{slug}.txt").exists()


def _needs_figures(slug: str) -> bool:
    return not (FIGDIR / f"{slug}.json").exists()


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="extract_parallel.py",
        description="阶段 E 并行素材抽取：PDF → 按页文本 + 图注页索引 + 内嵌位图（幂等、增量）")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多处理几篇（0=全部）")
    ap.add_argument("--min-pages", type=int, default=2,
                    help="低于该页数视为残缺件，跳过（默认 2）")
    ap.add_argument("--no-figures", action="store_true", help="只抽文本，不抽图")
    ap.add_argument("--min-px-fig", type=int, default=60,
                    help="抽取位图的最小边长像素（默认 60；extract_figures 自身默认 "
                         "120 会滤掉小图，实测把有 7 张图的 PDF 抽成空数组）")
    ap.add_argument("--no-text", action="store_true", help="只抽图，不抽文本")
    ap.add_argument("--then-prepare", action="store_true",
                    help="抽取完成后顺带跑 interpret_prepare.py 组任务包")
    ap.add_argument("--status", action="store_true", help="只看进度，不做抽取")
    args = ap.parse_args()

    # ── 前置断言：抽取所需依赖必须在**本解释器**里可用 ──────────
    #   为什么必须显式检查：store.validate_pdf() 把 `from pypdf import
    #     PdfReader` 包在 `except Exception` 里，缺包时它对**每一个** PDF
    #     都返回 `unparsed:ModuleNotFoundError`（False）。本脚本据此判「残缺件」
    #     并跳过 —— 于是缺 pypdf 会表现成「所有 PDF 都被跳过」，而日志只说
    #     unparsed:ModuleNotFoundError，极易被当成文件坏了。
    #     宁可在这里明确失败，也不要静默跳过全部篇目。
    try:
        import pypdf  # noqa: F401
    except ImportError:
        LOG.error("缺少 pypdf —— store.validate_pdf 无法校验任何 PDF。")
        LOG.error("请用：uv run --with pypdf python 流程1/scripts/extract_parallel.py …")
        LOG.close()
        print()
        print("[X] 缺少 pypdf：本脚本需要它来判「残缺件」。")
        print("  正确调用：uv run --with pypdf python scripts/extract_parallel.py")
        print()
        return 2

    exports = store.exports_dir(WD)
    if not exports.exists():
        LOG.error(f"找不到全文目录：{exports}")
        LOG.close()
        return 2

    pdfs = sorted(p for p in exports.glob("*.pdf"))
    if not pdfs:
        LOG.error(f"{exports} 下没有 PDF —— 先跑阶段 E 下载")
        LOG.close()
        return 2

    state = _load_state()

    if args.status:
        n_txt = sum(1 for p in pdfs if not _needs_text(p.stem))
        n_fig = sum(1 for p in pdfs if not _needs_figures(p.stem))
        print()
        print(f"  全文 PDF      {len(pdfs)}")
        print(f"  按页文本就绪  {n_txt}")
        print(f"  图片素材就绪  {n_fig}")
        print(f"  状态文件      {STATE}")
        print()
        LOG.close()
        return 0 if n_txt == len(pdfs) else 3

    todo = []
    for p in pdfs:
        slug = p.stem
        if not args.no_text and _needs_text(slug):
            todo.append((p, slug))
        elif not args.no_figures and _needs_figures(slug):
            todo.append((p, slug))
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        LOG.ok(f"全部 {len(pdfs)} 篇素材已就绪，无需抽取")
    else:
        LOG.info(f"待抽取 {len(todo)} 篇（共 {len(pdfs)} 篇 PDF）")

    ok_text = ok_fig = skipped = failed = 0
    t0 = time.time()

    for pdf, slug in todo:
        # 残缺件跳过（补充材料/图注页），但**记录原因**，不静默
        try:
            good, why = store.validate_pdf(pdf, min_pages=args.min_pages)
        except Exception as e:
            good, why = False, f"{type(e).__name__}"
        if not good:
            state[slug] = {"skipped": why}
            skipped += 1
            # 区分「真残缺」与「校验器不可用」：后者不应被当成文件问题
            if why.startswith("unparsed:"):
                LOG.error(f"  跳过 {slug}：{why} —— 这是**校验器不可用**，"
                          f"不是文件残缺；请检查 pypdf 是否装好")
            else:
                LOG.warn(f"  跳过 {slug}：{why}")
            continue

        rec: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}

        if not args.no_text and _needs_text(slug):
            rc, tail = _py("prep_paper.py", str(pdf), "--key", slug,
                           with_pkgs=("pymupdf",))
            rec["text_rc"] = rc
            if rc == 0:
                ok_text += 1
                LOG.ok(f"  文本 [OK] {slug}")
            else:
                failed += 1
                rec["text_err"] = tail
                LOG.warn(f"  文本 [X] {slug}（rc={rc}）{tail[-120:]}")

        if not args.no_figures and _needs_figures(slug):
            # 参数：<pdf> <out.json> <min_px>
            #   min_px 用 args.min_px_fig（默认 60）。extract_figures.py 自身
            #     默认 120，实测把**有 7 张位图**的 PDF 抽成空数组 `[]`。
            #   pypdf 的 page.images 还需要 **Pillow**；缺 Pillow 时它抛
            #     ImportError 而被脚本内部吞掉，结果同样是「0 张」。
            #     所以 pypdf 与 pillow 必须一起注入。
            fig_out = FIGDIR / f"{slug}.json"
            FIGDIR.mkdir(parents=True, exist_ok=True)
            rc, tail = _py("extract_figures.py", str(pdf), str(fig_out),
                           str(args.min_px_fig),
                           with_pkgs=("pypdf", "pillow"))
            rec["fig_rc"] = rc
            n_fig = 0
            if rc == 0 and fig_out.exists():
                try:
                    n_fig = len(json.loads(fig_out.read_text(encoding="utf-8")))
                except Exception:
                    n_fig = -1
            rec["fig_count"] = n_fig
            if rc == 0 and n_fig > 0:
                ok_fig += 1
                LOG.ok(f"  图片 [OK] {slug}（{n_fig} 张）")
            elif rc == 0 and n_fig == 0:
                # 抽到 0 张要**明确告警**：可能是 PDF 真没有位图，也可能是
                # min_px 门槛把图全滤掉了 —— 两者不能用同一句话带过。
                LOG.warn(f"  图片 [WARN] {slug}：抽到 0 张。若该 PDF 确有插图，"
                         f"用更小的 --min-px-fig 重试（当前 {args.min_px_fig}）")
            else:
                rec["fig_err"] = tail
                LOG.warn(f"  图片 [X] {slug}（rc={rc}）{tail[-160:]}")

        state[slug] = rec
        _save_state(state)

    elapsed = time.time() - t0
    LOG.info(f"本轮：文本 +{ok_text} / 图片 +{ok_fig} / 跳过 {skipped} / 失败 {failed}"
             f"，耗时 {elapsed:.1f}s")

    if args.then_prepare:
        LOG.info("顺带组任务包：interpret_prepare.py")
        rc, tail = _py("interpret_prepare.py")
        LOG.info(f"  interpret_prepare rc={rc} {tail[-160:]}" if rc else "  任务包已更新")

    # 是否还有未抽取的篇目
    remaining = sum(1 for p in pdfs
                    if (not args.no_text and _needs_text(p.stem))
                    or (not args.no_figures and _needs_figures(p.stem)))
    LOG.close()
    print()
    print(f"  素材进度：{len(pdfs) - remaining}/{len(pdfs)} 篇就绪")
    print("  下一步：还有未抽取的篇目就再跑一轮；已齐可进入阶段 H/H2/R2")
    print()
    return 0 if remaining == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
