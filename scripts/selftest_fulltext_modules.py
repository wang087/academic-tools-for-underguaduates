# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文获取新模块的自检聚合器
#
# 为什么要有它
#   每个新模块都带自己的 `--self-test`，但**逐个手敲**既容易漏，
#   也看不出「全绿」这个整体结论。本脚本一次跑完，任一失败即非零退出 ——
#   可以直接接进 run_all.sh 的收尾，或 CI。
#
# 与既有自检的关系
#   与 `selftest_pipeline.py`（端到端跑阶段 0→P）**互补、不重复**：
#     · selftest_pipeline 验的是**脚本之间的契约**；
#     · 本脚本验的是**新模块内部的判据**（离线、秒级、零网络）。
#   两者都不覆盖阶段 E 的 CDP 实跑（那是 `selftest_consent.py` 的领域）。
#
# [STOP] 全部离线
#   本聚合器跑的所有自检都**不发网络请求**：
#     · fulltext_credentials —— 只用临时 LIT_DL_CONFIG_DIR
#     · fulltext_result      —— 纯函数 + 本地落盘
#     · jats_fulltext        —— 纯字符串判据 + 本地落盘
#     · white_list_policy    —— 只读 templates/直连白名单.json
#     · oa_candidates        —— 注入假 fetch
#     · fulltext_publishers  —— 注入假 fetch（且默认闸门关闭，必然不发请求）
#     · fetch_pacing         —— 注入假时钟与假 sleep，不真的等待
#     · fulltext_router      —— 注入假 fetch；守卫用例还额外用「一发请求就断言
#                               失败」的桩，证明 CDP 未失败时确实一个包都没发
#
# 用法：
#   uv run python scripts/selftest_fulltext_modules.py
#     --list         只列出会跑哪些模块
#     --verbose      转发每个模块的完整输出
#
# 退出码：0 = 全部通过；1 = 有失败项
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import time


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding") or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()

_HERE = pathlib.Path(__file__).resolve().parent

# 顺序即依赖顺序：先底层判据，再上层调用者。
SELFTEST_MODULES = (
    "jats_fulltext.py",
    "fulltext_credentials.py",
    "fulltext_result.py",
    "white_list_policy.py",
    "oa_candidates.py",
    "fetch_pacing.py",
    "fulltext_publishers.py",
    "fulltext_router.py",
)


def _module_path(name: str):
    """→ 该自检目标脚本的路径；属于可选模块且未安装时 → None。"""
    local = _HERE / name
    if local.is_file():
        return local
    try:
        import cdp_module as cdpmod                       # noqa: E402
    except Exception:                                     # noqa: BLE001
        return None
    return cdpmod.script(name)


def run_one(name: str, verbose: bool = False) -> tuple[int, float, str]:
    # 取全文 CDP 模块是可选的：它被整目录删掉时，这里**跳过而不是报失败**
    #   （跳过要留痕：返回码 -1 由调用方明确打印 [SKIP]，不算"全绿"之外的噪声）
    mod = _module_path(name)
    if mod is None:
        return -1, 0.0, (f"[SKIP] {name}：属可选模块 optional-cdp-fulltext/，"
                         "本发行版未安装")
    path = mod
    if not path.is_file():
        return 2, 0.0, f"[X] 模块不存在：{path}"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    # [STOP] 明确掐掉「顺手开闸」的可能：聚合自检必须在闸门关闭下全绿。
    env.pop("LIT_FLOW1_ALLOW_PUBLISHER_API", None)
    env.pop("LIT_FLOW1_CREDENTIALS", None)
    # 用**单独的工作目录**，不污染当天真实工作目录里的状态文件
    env["PAPER_WORKDIR"] = str(_HERE.parent / ".work_cdp_selftest_fulltext")
    t0 = time.time()
    cp = subprocess.run([sys.executable, str(path), "--self-test"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=env, cwd=str(_HERE))
    dt = time.time() - t0
    out = ((cp.stdout or "") + (cp.stderr or "")).strip()
    if verbose and out:
        print(out)
    return cp.returncode, dt, out


def main() -> int:
    argv = sys.argv[1:]
    if "--list" in argv:
        for m in SELFTEST_MODULES:
            print(m)
        return 0
    verbose = "--verbose" in argv

    # 聚合自检的工作目录用完即清（不留下状态文件）
    scratch = _HERE.parent / ".work_cdp_selftest_fulltext"
    results = []
    print(f"全文获取新模块自检：{len(SELFTEST_MODULES)} 个模块（全部离线）")
    print("-" * 66)
    for name in SELFTEST_MODULES:
        code, dt, out = run_one(name, verbose=verbose)
        tail = ""
        if not verbose and out:
            # 只留最后一行结论，保持聚合输出可读
            lines = [l for l in out.splitlines() if l.strip()]
            tail = lines[-1].strip() if lines else ""
        mark = "[OK]" if code == 0 else ("[SKIP]" if code == -1 else "[X]")
        print(f"{mark} {name:<26} {dt:6.2f}s  {tail}")
        results.append((name, code, dt))

    skipped = [n for n, c, _ in results if c == -1]
    failed = [(n, c) for n, c, _ in results if c not in (0, -1)]
    total = sum(dt for _, _, dt in results)
    print("-" * 66)
    if failed:
        print(f"[X] {len(failed)}/{len(results)} 个模块失败：{failed}")
        print("    重跑单个模块看详情：python scripts/<模块>.py --self-test")
    else:
        n_ok = sum(1 for _, c, _ in results if c == 0)
        extra = (f"（另有 {len(skipped)} 个属可选模块、未安装已跳过："
                 + "、".join(skipped) + "）") if skipped else ""
        print(f"[OK] {n_ok}/{len(results)} 个模块全部通过 | 合计 {total:.2f}s {extra}")

    # 清理聚合自检的临时工作目录（含各模块的 .work_cdp/selftest 残留）
    try:
        import shutil  # noqa: PLC0415
        shutil.rmtree(scratch, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
