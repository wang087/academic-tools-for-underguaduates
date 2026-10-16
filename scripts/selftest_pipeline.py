# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全流程自检（v7）—— 端到端跑一遍阶段 0 → D2 → G2 → H → H2 → P
#
# 为什么要有这个脚本
#   流程的脚本各自都有 `--self-test`，但它们只验**纯逻辑**；
#   真正会断的是**脚本之间的契约**（上游写的字段下游读不读得到、
#   文件名是否对得上、闸门关掉时下游是否也能正确收敛）。
#   这类缺陷只有端到端跑一遍才暴露 —— [版本变动.md](版本变动.md) 的教训就是这么来的。
#
# 特点
#   · 在**临时工作目录**里跑，不碰任何真实案例数据；
#   · 用 `流程1/示例数据/crossref_all.json` 作题录输入（77 篇真实 Crossref 记录）；
#   · 三个阶段的解读卡由**夹具**生成（不调用 LLM），但字段严格按 schema，
#     并刻意混入：缺卡、schema 不合规卡、未归一标签 —— 验证流程**如实申报**而不是静默略过；
#   · `--offline` 时跳过需要联网的 D2/G2/P，只验其余环节。
#
# 用法：
#   uv run python 流程1/scripts/selftest_pipeline.py
#     --offline         跳过联网环节（D2 / G2 / P 的联网部分）
#     --limit=4         D2 只处理前 N 篇作者论文（默认 4，控制耗时）
#     --keep            保留临时工作目录（默认跑完删除）
#     --verbose         打印每个脚本的完整输出
#
# 退出码：0 = 全部通过；1 = 有失败项
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

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
REPO = _HERE.parents[1]              # 仓库根（流程1/scripts → 流程 → 仓库根）
SDIR = _HERE
TPL = _HERE.parent / "templates"
SAMPLE = _HERE.parent / "示例数据" / "crossref_all.json"

AUTHOR = "自检作者"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    mark = "[OK]" if ok else "[X]"
    line = f"  {mark} {name}"
    if detail:
        line += f"   —— {detail}"
    print(line)
    return bool(ok)


def run(script: str, wd: pathlib.Path, args: list[str] | None = None,
        verbose: bool = False, timeout: int = 600,
        with_pkgs: list[str] | None = None):
    """以子进程跑一个流程脚本，返回 (returncode, stdout+stderr)。

    `with_pkgs`：该阶段需要的额外依赖（如阶段 K 的 matplotlib/networkx）。
    用 `uv run --with` 按需加载，不污染项目环境 —— 与流程文档里的命令一致。
    **无 uv 时回退到当前解释器**（发行包内嵌 runtime 的依赖已由 install.ps1 装好，
      此时 `sys.executable` 就是它）——否则换机器后自检会因为"没有 uv"整段假失败。
    """
    env = dict(os.environ)
    env["PAPER_WORKDIR"] = str(wd)
    env["PAPER_AUTHOR"] = AUTHOR
    env["PAPER_NAME_TOKENS"] = "hou, xudong|xudong hou|zhou, cong-zhao"
    env["PAPER_SURNAME"] = "Hou"
    env["PAPER_GIVEN"] = "Xudong"
    if with_pkgs and shutil.which("uv"):
        cmd = (["uv", "run", "--no-project"]
               + [x for pkg in with_pkgs for x in ("--with", pkg)]
               + ["python", str(SDIR / script)] + (args or []))
    else:
        cmd = [sys.executable, str(SDIR / script)] + (args or [])
    try:
        p = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = f"[TIME] 超时（{timeout}s）\n{e.stdout or ''}"
        return 124, out
    if verbose:
        print(f"\n──── {script} {' '.join(args or [])} ────")
        print(out[-4000:])
    return p.returncode, out


def run_split(script: str, wd: pathlib.Path, args: list[str] | None = None,
              verbose: bool = False, timeout: int = 300):
    """跑脚本并**分别**返回 (returncode, stdout, stderr)。

    为什么不能复用 `run()`：它把 stdout 与 stderr 合并成一份输出，而
      `prepare_stage.py --tier` 的契约恰恰是「**stdout 里恰好只有档位**」——
      合并之后就再也分不清"stdout 干净"和"日志行混进来了"，
      而这正是本条自检要挡的失败模式（调用方写 `T=$(… --tier H)`，
      拿到 "INFO … L2" 后与 "L2" 比对永远不相等，且**静默**）。
    """
    env = dict(os.environ)
    env["PAPER_WORKDIR"] = str(wd)
    env["PAPER_AUTHOR"] = AUTHOR
    cmd = [sys.executable, str(SDIR / script)] + (args or [])
    try:
        p = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = e.stdout or ""
        err = f"[TIME] 超时（{timeout}s）"
    if verbose:
        print(f"\n──── {script} {' '.join(args or [])} ────")
        print((out + err)[-4000:])
    return rc, out, err


def make_fixture_cards(wd: pathlib.Path, verbose: bool) -> None:
    """造解读卡夹具：3 张合规 + 1 张缺字段 + 1 张未归一标签，另留 2 篇缺卡。"""
    indir = wd / ".work_cdp" / "interpret"
    man = json.loads((indir / "manifest.json").read_text(encoding="utf-8"))
    resdir = indir / "results"
    resdir.mkdir(parents=True, exist_ok=True)
    tasks = man["tasks"]

    def card(doi, ok=True, unnorm=False):
        c = {
            "doi": doi,
            "one_line": f"【夹具】{doi} 在作者脉络中的位置",
            "problem_solved": "夹具用途，非真实解读。",
            "methods": ["夹具方法 A", "夹具方法 B"],
            "verified_conclusions": ["夹具结论"],
            "key_20": ["夹具关键信息"],
            "quantified_gains": [{"metric": "夹具指标", "value": "+10%",
                                  "context": "相对夹具基线"}],
            "open_questions": ["夹具待验证问题"],
            "direction_tags": (["自定义:未归一标签"] if unnorm
                               else ["分子与化学", "材料与结构"]),
            "paradigm_tags": (["自定义:夹具范式"] if unnorm else ["computational"]),
            "evidence": {"source": "fulltext", "text_chars": 1234},
        }
        if not ok:
            c.pop("verified_conclusions")   # 故意缺必需字段
            c.pop("paradigm_tags")
        return c

    n = 0
    for t in tasks[:3]:
        (resdir / f"{t['slug']}.json").write_text(
            json.dumps(card(t["doi"]), ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
    if len(tasks) > 3:
        (resdir / f"{tasks[3]['slug']}.json").write_text(
            json.dumps(card(tasks[3]["doi"], ok=False), ensure_ascii=False,
                       indent=2), encoding="utf-8")
        n += 1
    if len(tasks) > 4:
        (resdir / f"{tasks[4]['slug']}.json").write_text(
            json.dumps(card(tasks[4]["doi"], unnorm=True), ensure_ascii=False,
                       indent=2), encoding="utf-8")
        n += 1
    if verbose:
        print(f"── 夹具：写入 {n} 张解读卡（含 1 张 schema 不合规、1 张未归一标签），"
              f"故意留 {max(0, len(tasks) - n)} 篇缺卡")


def make_refs_cards(wd: pathlib.Path, verbose: bool) -> int:
    """造综述解读卡夹具：3 张（含 1 张带 network_role），其余留缺卡。"""
    refdir = wd / ".work_cdp" / "refs"
    man = json.loads((refdir / "manifest.json").read_text(encoding="utf-8"))
    resdir = refdir / "results"
    resdir.mkdir(parents=True, exist_ok=True)
    roles = ["奠基工作", "方法源头", "共识综述"]
    n = 0
    for i, t in enumerate(man["tasks"][:3]):
        c = {
            "doi": t["doi"],
            "one_line": f"【夹具】{t['title'][:40]} 在网络中的位置",
            "problem_solved": "夹具用途。",
            "methods": ["夹具方法"],
            "verified_conclusions": ["夹具结论"],
            "key_20": ["夹具关键信息"],
            "quantified_gains": [],
            "open_questions": ["夹具缺口"],
            "direction_tags": ["分子与化学"],
            "paradigm_tags": ["review"],
            "network_role": roles[i % len(roles)],
            "relation_to_author": "作者沿用其框架",
            "evidence": {"source": t.get("evidence_source") or "abstract",
                         "text_chars": t.get("abstract_chars") or 0},
        }
        (resdir / f"{t['slug']}.json").write_text(
            json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
    if verbose:
        print(f"── 夹具：写入 {n} 张综述卡")
    return n


def ppt_master_root() -> pathlib.Path:
    """ppt-master 仓库根（含 skills/ppt-master/SKILL.md）—— 复用 ppt_brief 的定位。

    不能写死 REPO/"ppt-master"：打包发行版把依赖放在 `<流程>/vendor/ppt-master`，
      那里才是 `project_manager.py` 真正所在。用它来调 validate。
    """
    try:
        sys.path.insert(0, str(SDIR))
        from ppt_brief import find_ppt_dir          # type: ignore
        d = find_ppt_dir()
        if d:
            return d
    except Exception:
        pass
    return REPO / "ppt-master"


def ppt_projects_dir() -> pathlib.Path:
    """**历史**：ppt-master 的 projects 目录。

    v17.19 起阶段 P 的项目**不再落这里**（改落流程输出根，见 ppt_brief `--outdir`），
    本函数仅留给仍想查看旧产物的调用，新自检一律用 `wd` 作为输出根。
    """
    return ppt_master_root() / "projects"


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        # 为什么要有 --help：本脚本没有 argparse，参数靠 `--x in sys.argv` 判定，
        #   所以在加上这个分支之前 `--help` 会被**当成正常运行**——直接跑一遍
        #   全流程（联网模式下还会真的发网络请求）。一个查询开关不该有副作用。
        print(
            "用法：python 流程1/scripts/selftest_pipeline.py [选项]\n"
            "\n"
            "全流程自检（阶段 0 → D2 → G2 → H → H2 → K → P），在临时工作目录里跑，\n"
            "不碰任何真实案例数据。含解读档位（--tier / --set / --audit）集成检查。\n"
            "\n"
            "选项：\n"
            "  --offline          跳过联网环节（D2 / G2 / P 的联网部分）\n"
            "  --limit=N          D2 只处理前 N 篇作者论文（默认 4）\n"
            "  --keep             保留临时工作目录（默认跑完删除）\n"
            "  --verbose          打印每个脚本的完整输出\n"
            "  -h, --help         显示本帮助\n"
            "\n"
            "退出码：0 = 全部通过；1 = 有失败项；2 = 找不到示例题录")
        return 0

    verbose = "--verbose" in sys.argv
    offline = "--offline" in sys.argv
    keep = "--keep" in sys.argv
    limit = "4"
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = a.split("=", 1)[1]

    print("═" * 68)
    print(" 流程全流程自检（v7）")
    print(f" 仓库：{REPO}")
    print(f" 模式：{'离线（跳过 D2/G2/P 联网部分）' if offline else '联网'}")
    print("═" * 68)

    if not SAMPLE.exists():
        print(f"[X] 找不到示例题录：{SAMPLE}")
        return 2

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="flow_selftest_"))
    wd = tmp / AUTHOR
    (wd / ".work_cdp").mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE, wd / ".work_cdp" / "crossref_all.json")
    print(f"\n临时工作目录：{wd}\n")

    try:
        # ── 阶段 0：准备阶段 ──────────────────────────────────
        print("【阶段 0】准备阶段")
        rc, out = run("prepare_stage.py", wd, [
            "--set", "output_format=markdown", "--set", "translate=no",
            "--set", "reverse_refs=yes", "--set", "make_ppt=yes",
            "--set", "refs_bibliography=yes", "--set", "curate_review=yes",
            "--set", "ppt_pages=short"], verbose=verbose)
        check("阶段 0 生成计划", rc == 0 and (wd / ".work_cdp" / "prepare_plan.json").exists())
        plan = json.loads((wd / ".work_cdp" / "prepare_plan.json").read_text(encoding="utf-8"))
        check("闸门已解析", plan.get("_gates", {}).get("D2") == "run"
              and plan.get("_gates", {}).get("F") == "skip",
              f"F={plan.get('_gates', {}).get('F')}（translate=no 应跳过）")
        # v13.1：本用例是 output_format=markdown **且 translate=no** → 阶段 F 由
        #   translate 关闭（output_format 已不再是闸门），故 _f_mode=off。
        #   改前这条的标签写「F 为 html-only」而断言写 off —— 标签描述意图、
        #   断言锁实际行为，二者矛盾正是 [版本变动.md](版本变动.md) 那处缺陷留下的痕迹。
        check("output_format=markdown 且 translate=no → F 关闭（_f_mode=off）",
              plan.get("_f_mode") == "off", f"_f_mode={plan.get('_f_mode')}")

        # v13.1 回归守卫（行为级）：output_format **不再**决定阶段 F 是否执行。
        #   改前这一组合会被静默跳过 —— 用户要了翻译却什么都拿不到，且不报错。
        #   若有人把 F 闸门加回 output_format，下面第一条会立刻失败。
        #   [WARN] `--set` 是**整份重写**（未指定的项一律回落到默认值），
        #      所以这里改完必须把本次自检后续步骤依赖的计划**原样重建**。
        run("prepare_stage.py", wd, [
            "--set", "output_format=markdown", "--set", "translate=yes"], verbose=verbose)
        rc_md, _ = run("prepare_stage.py", wd, ["--check", "F"], verbose=verbose)
        check("translate=yes + output_format=markdown → 阶段 F 仍执行（v13.1 修复）",
              rc_md == 0, f"--check F 退出码={rc_md}（应为 0=执行）")
        plan_md = json.loads(
            (wd / ".work_cdp" / "prepare_plan.json").read_text(encoding="utf-8"))
        check("该组合下 _f_mode=html-only（只出中译 Markdown，不渲染 PDF）",
              plan_md.get("_f_mode") == "html-only",
              f"_f_mode={plan_md.get('_f_mode')}")
        run("prepare_stage.py", wd, [
            "--set", "output_format=markdown", "--set", "translate=no",
            "--set", "reverse_refs=yes", "--set", "make_ppt=yes",
            "--set", "refs_bibliography=yes", "--set", "curate_review=yes",
            "--set", "ppt_pages=short"], verbose=verbose)
        # 解读档位（v9）默认值：阶段 0 未显式指定时必须落到 L2 / L3
        check("prepare_plan.json 含两个档位键且默认 L2 / L3",
              plan.get("interpret_tier") == "L2"
              and plan.get("review_interpret_tier") == "L3",
              f"interpret_tier={plan.get('interpret_tier')!r} "
              f"review_interpret_tier={plan.get('review_interpret_tier')!r}")

        rc, _ = run("prepare_stage.py", wd, ["--check", "D2"], verbose=verbose)
        check("--check D2 返回 0（执行）", rc == 0)
        rc, _ = run("prepare_stage.py", wd, ["--check", "F"], verbose=verbose)
        check("--check F 返回 3（跳过）", rc == 3)

        # 依赖收敛：关掉 reverse_refs → G2/H2 也必须被关掉
        rc, _ = run("prepare_stage.py", wd, [
            "--set", "reverse_refs=no", "--set", "refs_bibliography=yes",
            "--set", "curate_review=yes"], verbose=verbose)
        plan2 = json.loads((wd / ".work_cdp" / "prepare_plan.json").read_text(encoding="utf-8"))
        g2 = plan2["_gates"]["G2"] == "skip"
        h2 = plan2["_gates"]["H2"] == "skip"
        why = "reverse_refs" in (plan2["_gate_reasons"].get("G2") or "")
        check("依赖收敛：reverse_refs=no → G2/H2 自动关闭且写明原因",
              rc == 0 and g2 and h2 and why,
              f"G2={plan2['_gates']['G2']} H2={plan2['_gates']['H2']} "
              f"reason={plan2['_gate_reasons'].get('G2')}")

        # 恢复成开启状态继续后面的测试
        run("prepare_stage.py", wd, [
            "--set", "reverse_refs=yes", "--set", "refs_bibliography=yes",
            "--set", "curate_review=yes", "--set", "make_ppt=yes",
            "--set", "ppt_pages=short"], verbose=verbose)

        # ── 阶段 0 · 解读档位（v9）：--tier / --set / --audit ────
        print("\n【阶段 0 · 解读档位】--tier 查询 / 改档 / --audit")
        # 最重要的三条：① --tier 退出码 0；② stdout **去空白后恰好**是
        #   L1/L2/L3（防日志行混入后调用方静默拿到 "INFO … L2"）；
        #   ③ 未知阶段退出码 2（而不是静默当成跳过）。
        for stage, key in (("H", "interpret_tier"),
                           ("H2", "review_interpret_tier"),
                           ("R2", "review_interpret_tier")):
            rc_t, so, _ = run_split("prepare_stage.py", wd, ["--tier", stage],
                                    verbose=verbose)
            val = so.strip()
            check(f"--tier {stage} 退出码 0 且 stdout 恰为 L1/L2/L3",
                  rc_t == 0 and val in ("L1", "L2", "L3"),
                  f"rc={rc_t} stdout={val!r}（阶段 0 现值 {plan.get(key)!r}）")

        rc_t, so_h, _ = run_split("prepare_stage.py", wd, ["--tier", "H"], verbose=verbose)
        check("--tier stdout 无日志杂质（无换行 / 无 '===' 前缀）",
              "\n" not in so_h.strip() and "===" not in so_h,
              f"stdout={so_h!r}")

        rc_t, _, _ = run_split("prepare_stage.py", wd, ["--tier", "ZZ"], verbose=verbose)
        check("--tier ZZ（未知阶段）退出码 2", rc_t == 2, f"rc={rc_t}")

        # 改档：L1 → --tier H 返回 L1；L3 → 返回 L3；小写 l2 也接受为 L2
        run("prepare_stage.py", wd, ["--set", "interpret_tier=L1"], verbose=verbose)
        _, so1, _ = run_split("prepare_stage.py", wd, ["--tier", "H"], verbose=verbose)
        run("prepare_stage.py", wd, ["--set", "interpret_tier=L3"], verbose=verbose)
        _, so3, _ = run_split("prepare_stage.py", wd, ["--tier", "H"], verbose=verbose)
        check("改档生效：interpret_tier=L1 → L1；=L3 → L3",
              so1.strip() == "L1" and so3.strip() == "L3",
              f"L1→{so1.strip()!r} L3→{so3.strip()!r}")
        run("prepare_stage.py", wd, ["--set", "interpret_tier=l2"], verbose=verbose)
        plan_lc = json.loads((wd / ".work_cdp" / "prepare_plan.json").read_text(encoding="utf-8"))
        _, so2, _ = run_split("prepare_stage.py", wd, ["--tier", "H"], verbose=verbose)
        check("档位大小写不敏感：--set interpret_tier=l2 被接受为 L2",
              plan_lc.get("interpret_tier") == "L2" and so2.strip() == "L2",
              f"plan={plan_lc.get('interpret_tier')!r} stdout={so2.strip()!r}")

        rc_a, so_a, se_a = run_split("prepare_stage.py", wd, ["--audit"], verbose=verbose)
        check("--audit 退出码 0（门控齐全）", rc_a == 0,
              ((so_a or se_a).strip().splitlines() or [""])[-1][:88])

        # `--set` 每次都会把**未显式给出的选项**重建为默认值，所以改档测试
        #   必须把下游依赖的选项一并复原，否则后面的 H/H2/P 会静默落回默认闸门。
        run("prepare_stage.py", wd, [
            "--set", "reverse_refs=yes", "--set", "refs_bibliography=yes",
            "--set", "curate_review=yes", "--set", "make_ppt=yes",
            "--set", "ppt_pages=short",
            "--set", "interpret_tier=L2", "--set", "review_interpret_tier=L3"],
            verbose=verbose)
        _, so_r, _ = run_split("prepare_stage.py", wd, ["--tier", "H"], verbose=verbose)
        check("档位已复原为默认（H→L2）", so_r.strip() == "L2", f"stdout={so_r.strip()!r}")

        # ── 阶段 P 读档位的容错：缺失 / 非法都只提示、不报错退出 ──
        probe_wd = tmp / "_tier_probe"
        (probe_wd / ".work_cdp").mkdir(parents=True, exist_ok=True)
        probe = probe_wd / "_tier_probe.py"
        probe.write_text(
            "import json, sys\n"
            f"sys.path.insert(0, {str(SDIR)!r})\n"
            "import ppt_brief\n"
            "print(json.dumps(ppt_brief.load_tiers(), ensure_ascii=False, sort_keys=True))\n",
            encoding="utf-8")
        env_p = dict(os.environ)
        env_p["PAPER_WORKDIR"] = str(probe_wd)
        env_p["PAPER_AUTHOR"] = AUTHOR
        want_default = '{"interpret_tier": "L2", "review_interpret_tier": "L3"}'
        pr = subprocess.run([sys.executable, str(probe)], cwd=str(REPO), env=env_p,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
        check("无 prepare_plan.json 时档位回退 L2/L3 且不报错退出",
              pr.returncode == 0 and (pr.stdout or "").strip() == want_default
              and "回退默认" in (pr.stderr or ""),
              f"rc={pr.returncode} stdout={(pr.stdout or '').strip()[-64:]!r} "
              f"stderr有提示={'回退默认' in (pr.stderr or '')}")
        (probe_wd / ".work_cdp" / "prepare_plan.json").write_text(
            json.dumps({"interpret_tier": "LL", "review_interpret_tier": 3}),
            encoding="utf-8")
        pr2 = subprocess.run([sys.executable, str(probe)], cwd=str(REPO), env=env_p,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120)
        check("档位非法（LL / 数字）时同样回退且不报错退出",
              pr2.returncode == 0 and (pr2.stdout or "").strip() == want_default,
              f"rc={pr2.returncode} stdout={(pr2.stdout or '').strip()[-64:]!r}")

        # ── 阶段 D2：反向查找参考文献 ─────────────────────────
        print("\n【阶段 D2】反向查找参考文献")
        if offline:
            check("阶段 D2 跳过（--offline）", True, "未联网")
        else:
            rc, out = run("refs_backfill.py", wd,
                          [f"--limit-sources={limit}", "--top=10"], verbose=verbose)
            hq = wd / ".work_cdp" / "refs" / "high_quality.json"
            ok = rc == 0 and hq.exists()
            n = 0
            if ok:
                n = len(json.loads(hq.read_text(encoding="utf-8")).get("items") or [])
            check("阶段 D2 产出 high_quality.json", ok and n > 0, f"{n} 条高质量文献")
            rep = wd / f"{AUTHOR}_高质量文献列表.md"
            check("阶段 D2 交付物", rep.exists())
            if rep.exists():
                t = rep.read_text(encoding="utf-8")
                check("报告含反向索引与诚实性声明",
                      "反向索引" in t and "诚实性" in t)

            # ── 阶段 G2 ──────────────────────────────────────
            print("\n【阶段 G2】代表性文献的参考文献列表")
            rc, out = run("refs_bibliography.py", wd,
                          ["--max-refs-per-paper=5"], verbose=verbose)
            rep = wd / f"{AUTHOR}_代表性文献参考文献.md"
            check("阶段 G2 交付物", rc == 0 and rep.exists())
            if rep.exists():
                t = rep.read_text(encoding="utf-8")
                check("报告为「文章 → 参考文献」两部分",
                      "第一部分" in t and "第二部分" in t and "代表性文献" in t)
                check("未出现 et al.. 双句点", "et al.." not in t)
                check("IF 标注为代理值（≈）", "IF≈" in t)

        # ── 摘要链（v7.1 分层策略的第一层） ─────────────────────
        print("\n【摘要链】第一层直连（摘要 / 综述题录）")
        if offline:
            check("摘要链跳过（--offline）", True, "未联网")
        else:
            rc, out = run("fetch_direct.py", wd, ["--limit=8"], verbose=verbose)
            absj = wd / ".work_cdp" / "abstracts_openalex.json"
            n_abs = 0
            if absj.exists():
                d = json.loads(absj.read_text(encoding="utf-8"))
                n_abs = len([1 for v in d.values()
                             if (v.get("abstract") if isinstance(v, dict) else v)])
            check("第一层取到摘要", rc == 0 and n_abs > 0,
                  f"{n_abs} 篇，**零浏览器占用**")
            check("缺口清单已生成（供 CDP 回退）",
                  (wd / ".work_cdp" / "direct_failed.json").exists(),
                  "第一层全覆盖时为空清单")

        # 策略边界：白名单外必须被拒（fail closed）
        for url, want in (("https://api.openalex.org/works/doi:10.1/x", 0),
                          ("https://www.nature.com/articles/x", 3),
                          ("https://api.crossref.org.attacker.com/x", 3)):
            rc_c, _ = run("fetch_direct.py", wd, [f"--check-domain={url}"],
                          verbose=verbose)
            check(f"策略判定 {url[:34]}… → {want}", rc_c == want, f"实际 {rc_c}")

        # ── 阶段 H：论文解读（夹具） ───────────────────────────
        print("\n【阶段 H】论文解读（夹具）")
        (wd / ".work_cdp" / "tag_vocabulary.json").write_text(
            (TPL / "方法范式标签词表.json").read_text(encoding="utf-8"), encoding="utf-8")
        (wd / ".work_cdp" / "keyword_families.json").write_text(
            (TPL / "关键词词族_学科通用.json").read_text(encoding="utf-8"), encoding="utf-8")
        check("词表已就位", True, "tag_vocabulary.json + keyword_families.json")

        rc, out = run("interpret_prepare.py", wd, [], verbose=verbose)
        check("阶段 H ① 任务包生成", rc == 0
              and (wd / ".work_cdp" / "interpret" / "manifest.json").exists())

        # 任务包必须带 interpret_tier：subagent **只认任务包**，不自己去问
        #   阶段 0（见 subagent-driven-review § 6.0）。若 interpret_prepare.py
        #   尚未接入该字段，**记为待接入而不判失败** —— 该脚本此刻可能正由
        #   另一位 agent 修改，本自检不替它决定实现进度。
        iman = wd / ".work_cdp" / "interpret" / "manifest.json"
        if iman.exists():
            im = json.loads(iman.read_text(encoding="utf-8"))
            _, tier_h, _ = run_split("prepare_stage.py", wd, ["--tier", "H"],
                                     verbose=verbose)
            want_tier = tier_h.strip() or "L2"
            man_tier = im.get("interpret_tier")
            pkts = sorted((wd / ".work_cdp" / "interpret" / "tasks").glob("*.json"))
            pkt_tiers = []
            for p in pkts:
                try:
                    pkt_tiers.append(json.loads(p.read_text(encoding="utf-8"))
                                     .get("interpret_tier"))
                except Exception:
                    pkt_tiers.append(None)
            if man_tier is None and not any(pkt_tiers):
                check("解读任务包含 interpret_tier（待 interpret_prepare.py 接入）",
                      True, "manifest.json 与任务包都还没有该字段 → 记为**待接入**，不判失败")
            else:
                bad = [p.name for p, t in zip(pkts, pkt_tiers) if t != want_tier]
                check("manifest.json 与每个任务包的 interpret_tier == 阶段 0 档位",
                      man_tier == want_tier and not bad,
                      f"manifest={man_tier!r} 期望={want_tier!r} "
                      + (f"不符的任务包：{bad[:3]}" if bad else f"（{len(pkts)} 个任务包）"))

        make_fixture_cards(wd, verbose)
        rc, out = run("interpret_build.py", wd, [], verbose=verbose)
        rep = wd / f"{AUTHOR}_论文解读.md"
        check("阶段 H ④ 报告生成", rc == 0 and rep.exists())
        if rep.exists():
            t = rep.read_text(encoding="utf-8")
            # 关键：缺卡与不合规卡必须**如实申报**，不得静默略过
            # 断言的是**语义**（缺卡与字段不全都必须被报出），不是某一版措辞
            check("解读报告如实申报缺卡 / 字段不全",
                  "缺卡" in t and ("字段不全" in t or "不合规" in t),
                  "已如实申报" if "缺卡" in t else "缺失！")
            check("关键词统计使用词族表",
                  "语义词族" in t or "词族" in t)

        # ── 阶段 H2：去重 + 相关性整理 + 综述 ───────────────────
        print("\n【阶段 H2】去重与相关性整理 → 综述")
        rc, out = run("refs_curate.py", wd, ["--cut=10"], verbose=verbose)
        fl = wd / ".work_cdp" / "refs" / "final_list.json"
        drop = wd / ".work_cdp" / "refs" / "dropped.json"
        if offline:
            check("阶段 H2 ① 需要 D2 产物（离线时跳过）", True, "未联网")
        else:
            check("阶段 H2 ① 产出最终列表", rc == 0 and fl.exists())
            check("被剔除清单已留档（不静默丢弃）", drop.exists())
            rep = wd / f"{AUTHOR}_研究方向文献终表.md"
            check("阶段 H2 ① 交付物", rep.exists())
            if rep.exists():
                t = rep.read_text(encoding="utf-8")
                check("报告含评分口径与被剔除清单",
                      "评分口径" in t and "被剔除清单" in t)
                check("方向画像用上了阶段 H 的标签",
                      "受控词表" in t or "方向标签" in t)

            rc, out = run("refs_prepare.py", wd, ["--top=5"], verbose=verbose)
            rman = wd / ".work_cdp" / "refs" / "manifest.json"
            check("阶段 H2 ② 任务包生成", rc == 0 and rman.exists())
            make_refs_cards(wd, verbose)
            rc, out = run("refs_build.py", wd, [], verbose=verbose)
            rep = wd / f"{AUTHOR}_研究方向综述.md"
            check("阶段 H2 ③ 综述报告生成", rc == 0 and rep.exists())
            if rep.exists():
                t = rep.read_text(encoding="utf-8")
                check("综述报告含网络角色分布", "网络角色" in t)
                check("综述报告如实申报缺卡与限制",
                      "缺卡与限制申报" in t)
                check("综述报告声明证据层级为摘要级",
                      "摘要级" in t or "证据层级" in t)

        # ── 阶段 K：知识图谱（v16.3：径向整齐树 + 时间线）────────
        print("\n【阶段 K】知识图谱（overview / 词云 / 方向聚类）")
        rc, out = run("knowledge_graph.py", wd, ["--format=png", "--dpi=110"],
                      verbose=verbose,
                      with_pkgs=["matplotlib", "networkx", "python-igraph",
                                 "adjustText", "wordcloud"], timeout=900)
        gdir = wd / ".work_cdp" / "graph"
        pngs = sorted(gdir.glob("*.png")) if gdir.is_dir() else []
        check("阶段 K 产出三张图", rc == 0 and len(pngs) >= 3,
              "、".join(f.name for f in pngs) or (out[-160:] if out else "无输出"))
        check("图谱数据已落盘", (gdir / "graph_data.json").exists())
        rep = wd / f"{AUTHOR}_知识图谱.md"
        check("阶段 K 报告已生成", rep.exists())
        if rep.exists():
            t = rep.read_text(encoding="utf-8")
            check("报告含聚类解读与冲突线索两节",
                  "这一簇在讲什么" in t and "自相矛盾" in t)
            check("报告如实列出被剔除的词", "剔除" in t)
        # 尺寸守卫：词云曾因文字画到轴外而产出 4.7 亿像素的"坏图"
        import struct as _st
        bad_size = []
        for f in pngs:
            try:
                with open(f, "rb") as fh:
                    fh.read(16)
                    w_, h_ = _st.unpack(">II", fh.read(8))
                if w_ * h_ > 200_000_000:
                    bad_size.append(f"{f.name} {w_}x{h_}")
            except Exception:
                pass
        check("图片尺寸正常（无 tight-bbox 撑爆）", not bad_size,
              "；".join(bad_size) if bad_size else "均在 2 亿像素以内")
        # v16.2 新增契约：取词口径可核 + 字号自述必须落盘
        import json as _json
        try:
            gd = _json.loads((gdir / "graph_data.json").read_text(encoding="utf-8"))
        except Exception as e:
            gd = {}
            check("graph_data.json 可解析", False, str(e))
        if gd:
            ws = (gd.get("wordcloud") or {}).get("stats") or {}
            check("词云记录了取词口径（来源权重/阈值/关键词覆盖率）",
                  bool(ws.get("weights")) and bool(ws.get("thresholds"))
                  and "keywords_coverage" in ws,
                  f"coverage={ws.get('keywords_coverage')} "
                  f"weights={ws.get('weights')}")
            fn = gd.get("figure_notes") or {}
            check("三张图都记录了字号自述",
                  len(fn) >= 3 and all(v.get("effective_pt") for v in fn.values()),
                  "、".join(f"{k.split('_')[-1]}" for k in fn))
            check("范式标签不再混进词云权重（单独统计）",
                  not any(t in (gd.get("wordcloud") or {}).get("weights", {})
                          for t in ("experimental", "mechanistic", "imaging")),
                  "experimental/mechanistic/imaging 不应出现在词云权重里")
            # v16.3 新增契约：overview 布局必须可审计
            ol = gd.get("overview_layout") or {}
            ometa = ol.get("meta") or {}
            # 门槛随语料规模缩放：`pos` 装的是**参与布局的**节点（真实例 53/81），
            #   写死 `>= 10` 会让 `--offline` 的小样例（只有 5 个节点）永远报红，
            #   而这条契约的本意是"有没有落盘"，不是"语料够不够大"。
            n_nodes = len(gd.get("nodes") or [])
            n_pos = len(ol.get("pos") or {})
            check("overview 节点坐标已落盘（布局可事后审计）",
                  bool(n_pos) and n_pos >= min(10, max(1, n_nodes)),
                  f"落盘 {n_pos} 个坐标（语料节点 {n_nodes}）")
            check("overview 记录了阶段扇区与三层半径",
                  bool(ometa.get("sectors"))
                  and (ometa.get("radii") or {}).__len__() >= 3,
                  f"sectors={len(ometa.get('sectors') or [])} "
                  f"radii={len(ometa.get('radii') or {})}")
            check("记忆连接弧线全部画出（端点都在外环上）",
                  ometa.get("ring_missing_link_endpoints") == []
                  and ol.get("n_arcs_drawn", 0)
                  >= len(((gd.get("memory_links") or []))),
                  f"弧线 {ol.get('n_arcs_drawn')} / 连接 "
                  f"{len(gd.get('memory_links') or [])}"
                  f"｜缺失端点 {ometa.get('ring_missing_link_endpoints')}")
        if rep.exists():
            t = rep.read_text(encoding="utf-8")
            check("报告含字号自述一节", "字号自述" in t)
            check("报告含词云取词口径", "取词口径" in t)
            check("报告含 overview 布局自述（扇区/坐标落盘）",
                  "径向整齐树" in t and "overview_layout.pos" in t)
            check("报告含词云布局引擎与放置口径（放下/未放下）",
                  "wordcloud" in t and "未放下" in t or "全部放下" in t)

        # ── 阶段 P：PPT ───────────────────────────────────────
        print("\n【阶段 P】PPT 材料与 Spec 生成")
        # v17.19：项目改落**流程输出根**（ppt_brief 默认 --outdir=WD），
        #   不再进 ppt-master/projects —— 自检也据此在 wd 下找新建项目。
        PROJ = wd
        ppt_root = ppt_master_root()
        ppts = sorted(PROJ.glob(f"{AUTHOR}*"))
        before = set(ppts)
        # 项目名带本次运行的唯一后缀：`project_manager.py init` 用
        #   `<项目名>_<画布>_<YYYYMMDD>` 作目录名，**同名当天已存在就直接报错**
        #   退出。上一次自检中断（或带 --keep）留下的同名目录会让本项永远失败，
        #   而且失败原因看起来像"PPT 阶段坏了"，与改动无关。
        #   加后缀后每次运行都有自己的目录，`{AUTHOR}*` 的 glob 与清理逻辑不变。
        run_tag = time.strftime("%H%M%S")
        rc, out = run("ppt_brief.py", wd,
                      ["--pages=short",
                       f"--project-name={AUTHOR}_文献汇报_自检{run_tag}"],
                      verbose=verbose)
        after = set(sorted(PROJ.glob(f"{AUTHOR}*")))
        new = sorted(after - before)
        gate_blocked = rc == 3
        check("阶段 P 执行成功或被确认门阻断", rc in (0, 3),
              (out.strip().splitlines() or [""])[-1][:90])
        if new:
            proj = new[-1]
            # 断言项目确实落在**本流程输出根**下（v17.19 起不再落 ppt-master/projects）。
            #   打包发行版若因路径解析错误把产物写到包外，这条断言会抓到。
            inside = PROJ.resolve() in proj.resolve().parents
            check("阶段 P 写入了流程输出根（非 ppt-master/projects）", inside,
                  f"输出根：{PROJ}")
            if gate_blocked:
                check("阶段 P 确认门正常阻断", "尚未完成 ppt-master 两阶段确认" in out)
                check("确认门阻断时不伪造 Spec", not (proj / "design_spec.md").exists()
                      and not (proj / "spec_lock.md").exists())
            else:
                check("design_spec.md / spec_lock.md 已生成",
                      (proj / "design_spec.md").exists() and (proj / "spec_lock.md").exists())
                pm = ppt_root / "skills" / "ppt-master" / "scripts" / "project_manager.py"
                p = subprocess.run([sys.executable, str(pm), "validate", str(proj)],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
                check("ppt-master 自己的 validate 通过", p.returncode == 0,
                      (p.stdout or "").strip().splitlines()[-1][:80])
                check("Spec 的 Page Count 与 §IX 页数一致", _spec_consistent(proj))
                spec_txt = (proj / "design_spec.md").read_text(encoding="utf-8")
                check("阶段 K 图谱已写入 §VIII（Acquire Via: user）",
                      "知识图谱" in spec_txt and "| user | Existing |" in spec_txt)
                n_img = len(list((proj / "images").glob("*知识图谱*")))
                check("图谱已复制进项目 images/", n_img >= 1, f"{n_img} 张")

                # 档位必须落到**三份产物**上：source md / design_spec.md /
                #   spec_lock.md。否则读材料的人无从知道"写浅了"是档位约定
                #   还是漏写，而 spec_lock.md 的档位行还必须是合法 `- ` 数据行
                #   （它是严格 schema，多一个 `##` 段就会被判 unknown section）。
                plan_p = json.loads((wd / ".work_cdp" / "prepare_plan.json")
                                    .read_text(encoding="utf-8"))
                wa = plan_p.get("interpret_tier") or "L2"
                wr = plan_p.get("review_interpret_tier") or "L3"
                lock_txt = (proj / "spec_lock.md").read_text(encoding="utf-8")
                sdir = proj / "sources"
                src_txt = ""
                if sdir.is_dir():
                    cands = sorted(sdir.glob("*.md"))
                    if cands:
                        src_txt = cands[0].read_text(encoding="utf-8")
                marks = {"design_spec.md": f"作者论文解读 {wa}" in spec_txt
                         and f"研究方向综述 {wr}" in spec_txt,
                         "spec_lock.md": f"作者论文解读 {wa}" in lock_txt
                         and f"研究方向综述 {wr}" in lock_txt,
                         "source md": f"作者论文解读 {wa}" in src_txt
                         and f"研究方向综述 {wr}" in src_txt}
                check("档位记录进 source md / design_spec.md / spec_lock.md",
                      all(marks.values()),
                      f"阶段 0 档位 H={wa} H2={wr}；"
                      + "、".join(f"{k}={'[OK]' if v else '[X]'}" for k, v in marks.items()))
            if not keep:
                shutil.rmtree(proj, ignore_errors=True)
        else:
            check("找到新建的 ppt-master 项目", False,
                  f"未在流程输出根 {PROJ} 下发现 {AUTHOR}* 目录")

        # ── 闸门关闭路径 ─────────────────────────────────────
        print("\n【闸门】全关（反向/PPT 都关）")
        run("prepare_stage.py", wd, [
            "--set", "reverse_refs=no", "--set", "make_ppt=no"], verbose=verbose)
        rc_d2, _ = run("prepare_stage.py", wd, ["--check", "D2"], verbose=verbose)
        rc_p, _ = run("prepare_stage.py", wd, ["--check", "P"], verbose=verbose)
        check("D2 / P 均返回 3（跳过）", rc_d2 == 3 and rc_p == 3,
              f"D2={rc_d2} P={rc_p}")

    finally:
        if keep:
            print(f"\n（保留临时目录：{wd}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── 汇总 ──────────────────────────────────────────────────
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    n_all = len(RESULTS)
    print("\n" + "═" * 68)
    print(f" 结果：{n_pass}/{n_all} 通过")
    if n_pass != n_all:
        print(" 失败项：")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"   [X] {name}   {detail}")
    print("═" * 68)
    return 0 if n_pass == n_all else 1


def _spec_consistent(proj: pathlib.Path) -> bool:
    """§I 的 Page Count 必须等于 §IX 里实际出现的 Slide 数。"""
    import re
    spec = (proj / "design_spec.md").read_text(encoding="utf-8")
    m = re.search(r"\|\s*Page Count\s*\|\s*(\d+)\s*\|", spec)
    if not m:
        return False
    declared = int(m.group(1))
    actual = len(re.findall(r"^#### Slide \d+", spec, re.M))
    return declared == actual


if __name__ == "__main__":
    raise SystemExit(main())
