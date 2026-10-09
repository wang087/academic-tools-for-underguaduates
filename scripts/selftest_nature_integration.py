# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# nature-paper-card 并入自检（v17）—— 端到端验「流程1 ↔ 已并入的单个技能」的接线
#
# 并入范围：**只有 `nature-paper-card` 一个技能**（落 vendor/nature-paper-card/）。
#   本脚本第 [1] 组断言会检查范围没有漂移（vendor/ 下不得再出现整包镜像）。
#
# 为什么要有这个脚本
#   接线断掉的方式从来不是「脚本报错」，而是**契约对不上**：
#     · 阶段 0 写了 interpret_engine，但 interpret_prepare.py 不读；
#     · 任务包下发了 paper_card 路径，但那个技能根本没并进来；
#     · 审计器说 pass，而卡片其实是空的（实测发生过）；
#     · 内容级校验没接上，于是「数字回查原文」这唯一的能力形同不存在。
#   这些只有端到端跑一遍才暴露。本脚本不调用任何 LLM：解读卡与深读卡
#   都由**夹具**生成，字段严格按 schema，并刻意混入不合规样本，
#   验证流程是**如实申报**而不是静默略过。
#
# 全程零网络、零外部依赖（只读 流程1/ 内已并入的副本）。
#
# 用法：
#   uv run python 流程1/scripts/selftest_nature_integration.py
#     --keep      保留临时工作目录（默认跑完删除）
#     --verbose   打印每个子脚本的完整输出
#
# 退出码：0 = 全部通过；1 = 有失败项；2 = 环境缺失（并入副本不在位）
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

FLOW = pathlib.Path(__file__).resolve().parent.parent      # 流程1/
REPO = FLOW.parent
SDIR = FLOW / "scripts"
PAPER_CARD = FLOW / "vendor" / "nature-paper-card"
INTERP = FLOW / "skills" / "paper-interpretation-writing"
AUTHOR = "自检作者"

RESULTS: list[tuple[str, bool, str]] = []


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


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'[OK]' if ok else '[X]'} {name}" + (f"   —— {detail}" if detail else ""))
    return bool(ok)


def mkworkdir(prefix: str) -> pathlib.Path:
    """建可写的临时工作目录。

    不用 `tempfile.mkdtemp()`：它以 0700 建目录，在受限沙箱下会
      「建得出来、写不进去」（PermissionError）。默认权限即可。
    """
    base = pathlib.Path(tempfile.gettempdir())
    d = base / f"{prefix}{os.getpid()}_{next(tempfile._get_candidate_names())}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(script: pathlib.Path, wd: pathlib.Path, args: list[str] | None = None,
        verbose: bool = False, timeout: int = 300):
    """在指定 PAPER_WORKDIR 下跑一个脚本，返回 (退出码, stdout, stderr)。"""
    env = dict(os.environ)
    env["PAPER_WORKDIR"] = str(wd)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, str(script), *(args or [])]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=timeout)
    out, err = p.stdout or "", p.stderr or ""
    if verbose:
        print(f"    $ {script.name} {' '.join(args or [])}")
        print("    " + out.replace("\n", "\n    ").strip()[:1500])
        if err.strip():
            print("    [stderr] " + err.replace("\n", "\n    ").strip()[:800])
    return p.returncode, out, err


# ── 夹具 ─────────────────────────────────────────────────────
def write_records(wd: pathlib.Path) -> None:
    """最小题录总表：两条记录，字段形状与 fetch_crossref_all.py 的产物一致。"""
    recs = {
        "10.1000/selftest.0001": {
            "title": "Self-test paper one on sodium channel gating",
            "journal": "Journal of Self Tests",
            "short_journal": "J. Self Test",
            "year": 2003,
            "authors": [{"given": "San", "family": "Zhang"}],
            "abstract": "We measured gating currents and report a 92 percent reduction.",
        },
        "10.1000/selftest.0002": {
            "title": "Self-test paper two on conotoxin block",
            "journal": "Journal of Self Tests",
            "short_journal": "J. Self Test",
            "year": 2005,
            "authors": [{"given": "San", "family": "Zhang"}],
            "abstract": "Blocking affinity differed by two orders of magnitude.",
        },
    }
    (wd / ".work_cdp").mkdir(parents=True, exist_ok=True)
    (wd / ".work_cdp" / "crossref_all.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")


def write_card(wd: pathlib.Path, slug: str) -> None:
    """写一份**合规**的 11 字段解读卡（夹具，不调 LLM）。"""
    card = {
        "doi": slug.replace("_", "/", 1),
        "one_line": "本文在自检夹具中定位为一个可复核的解读单元。",
        "problem_solved": "验证下游汇总能否正确消费结构化卡片。",
        "methods": ["全细胞膜片钳记录，保持电位 −100 mV，阶跃至 −10 mV"],
        "verified_conclusions": ["WT 通道的 IC50 = 297 ± 86 nM（n = 6）。"],
        "key_20": ["这是一个用于自检的夹具卡，不代表任何真实研究结论。"],
        "quantified_gains": [
            {"metric": "IC50", "value": "297 ± 86 nM", "context": "WT 通道"}
        ],
        "open_questions": ["夹具不涉及真实局限。"],
        "direction_tags": ["离子通道"],
        "paradigm_tags": ["experimental"],
        "evidence": {"source": "fulltext", "text_chars": 1234},
    }
    res = wd / ".work_cdp" / "interpret" / "results"
    res.mkdir(parents=True, exist_ok=True)
    (res / f"{slug}.json").write_text(json.dumps(card, ensure_ascii=False, indent=1),
                                      encoding="utf-8")


def main() -> int:
    _fix_streams()
    verbose = "--verbose" in sys.argv
    keep = "--keep" in sys.argv

    print("=== nature 并入自检（零网络、无 LLM）===")
    print(f"流程根：{FLOW}")
    print()

    # ── ① 并入是否在位、范围是否未漂移 ────────────────────────
    print("[1] 并入副本在位性与并入范围")
    check("vendor/nature-paper-card 存在", (PAPER_CARD / "SKILL.md").is_file())
    for rel in ("manifest.yaml", "scripts/prepare_paper.py",
                "scripts/audit_paper_card.py", "references/card-schema.md",
                "references/terminology-ledger.md", "LICENSE", "UPSTREAM.md",
                "docs/nature-paper-card-tutorial.md",
                "tests/test_prepare_paper.py", "tests/test_audit_paper_card.py"):
        check(f"存在 {rel}", (PAPER_CARD / rel).is_file())
    # 只并入这一个技能：vendor/ 下不得再出现整包镜像
    stray = [p.name for p in (FLOW / "vendor").iterdir()
             if p.is_dir() and p.name not in ("ppt-master", "nature-paper-card")]
    check("vendor/ 下无多余并入项（只 paper-card + ppt-master）", not stray,
          f"多余：{stray}")
    check("没有整包镜像 vendor/nature-skills", not (FLOW / "vendor" / "nature-skills").exists())
    # always_load 不得越出技能目录（上游 ../nature-shared/... 已内联）。
    #   只查**有效 YAML**：注释里可以写 `../nature-shared/...` 解释历史，那不是引用。
    man_txt = (PAPER_CARD / "manifest.yaml").read_text(encoding="utf-8")
    man_effective = [ln for ln in man_txt.splitlines()
                     if not ln.lstrip().startswith("#")]
    check("manifest.yaml 无 ../ 越界引用",
          not any("../" in ln for ln in man_effective))
    check("内容级校验器在位",
          (INTERP / "scripts" / "check_interpretation.py").is_file())
    code, out, _ = run(SDIR / "nature_bridge.py", FLOW, ["locations"])
    check("nature_bridge locations 退出 0", code == 0)
    check("locations 指向流程内单技能副本（非仓库根检出）",
          "vendor" in out and "nature-paper-card" in out
          and "nature-skills-main" not in out,
          "解析到流程内 vendor/nature-paper-card")
    code, out, _ = run(SDIR / "nature_bridge.py", FLOW, ["--self-test"])
    check("nature_bridge --self-test 通过", code == 0,
          "含 source_map 字段名、定位模式、空壳卡、编造数字、节号误判五项回归")
    if code != 0 and not verbose:
        print("    " + out.replace("\n", "\n    ")[-1200:])

    # ── ② 阶段 0 的引擎参数 ───────────────────────────────────
    print("\n[2] 阶段 0：interpret_engine 选项与查询")
    wd0 = mkworkdir("nat_selftest_plan_")
    try:
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--defaults"], verbose=verbose)
        check("prepare_stage --defaults 成功", code == 0)
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--engine", "H"])
        check("默认引擎 = legacy", code == 0 and out.strip() == "legacy", out.strip())
        code, out, _ = run(SDIR / "prepare_stage.py", wd0,
                           ["--set", "interpret_engine=paper-card"], verbose=verbose)
        check("--set interpret_engine=paper-card 成功", code == 0)
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--engine", "H"])
        check("改档后查询 = paper-card", code == 0 and out.strip() == "paper-card",
              out.strip())
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--audit"], verbose=verbose)
        check("门控自检通过（含引擎查询表）", code == 0 and "解读引擎查询表" in out)
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--check-all"])
        check("--check-all 含 ---interpret_engine---", "---interpret_engine---" in out)
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--set", "interpret_engine=bogus"])
        check("非法引擎取值被拒（退出 2）", code == 2)
        code, out, _ = run(SDIR / "prepare_stage.py", wd0, ["--engine", "H2"])
        check("无引擎概念的阶段被拒（退出 2）", code == 2)
    finally:
        if not keep:
            shutil.rmtree(wd0, ignore_errors=True)

    # ── ③ 任务包：两种引擎的契约 ──────────────────────────────
    print("\n[3] 阶段 H ①：任务包的引擎契约")
    wd = mkworkdir("nat_selftest_h_")
    try:
        write_records(wd)
        code, out, err = run(SDIR / "interpret_prepare.py", wd,
                             ["--no-text", "--limit=2", "--engine=legacy"],
                             verbose=verbose)
        check("legacy 组包成功", code == 0, err.strip()[:120])
        man = json.loads((wd / ".work_cdp" / "interpret" / "manifest.json")
                         .read_text(encoding="utf-8"))
        check("manifest 顶层 interpret_engine=legacy",
              man.get("interpret_engine") == "legacy", str(man.get("interpret_engine")))
        tasks = man.get("tasks") or []
        check("产出 2 个任务包", len(tasks) == 2, f"实得 {len(tasks)}")
        if tasks:
            pkt = json.loads(pathlib.Path(tasks[0]["task"]).read_text(encoding="utf-8"))
            check("任务包 interpret_engine=legacy",
                  pkt.get("interpret_engine") == "legacy")
            check("legacy 任务包**不含** paper_card 块", "paper_card" not in pkt)
            check("legacy 任务包不含多余字段（形态不变）",
                  set(pkt) == {
                      "seq", "interpret_tier", "interpret_engine", "doi", "title",
                      "journal", "year", "authors", "my_position", "abstract", "pdf",
                      "pages", "evidence_source", "text_extraction_status",
                      "text_extraction_backend", "text_extraction_errors",
                      "text_chars", "text_truncated", "text_omitted_chars",
                      "fulltext_chars", "fulltext_path", "figure_dir",
                      "figures_manifest", "figure_count", "text", "tag_vocabulary",
                      "result_path"})

        # paper-card 引擎
        code, out, err = run(SDIR / "interpret_prepare.py", wd,
                             ["--no-text", "--limit=2", "--engine=paper-card"],
                             verbose=verbose)
        check("paper-card 组包成功", code == 0, err.strip()[:200])
        man2 = json.loads((wd / ".work_cdp" / "interpret" / "manifest.json")
                          .read_text(encoding="utf-8"))
        check("manifest 顶层 interpret_engine=paper-card",
              man2.get("interpret_engine") == "paper-card")
        tasks2 = man2.get("tasks") or []
        if tasks2:
            pkt2 = json.loads(pathlib.Path(tasks2[0]["task"]).read_text(encoding="utf-8"))
            pc = pkt2.get("paper_card") or {}
            check("任务包含 paper_card 块", bool(pc))
            missing = [k for k in ("skill_md", "prepare_script", "audit_script",
                                   "bridge_script", "content_check_script",
                                   "card_path", "source_bundle_path",
                                   "audit_report_path") if not pc.get(k)]
            check("paper_card 块无空字段", not missing, f"缺：{missing}")
            for k in ("skill_md", "prepare_script", "audit_script",
                      "bridge_script", "content_check_script"):
                check(f"下发路径存在：{k}",
                      bool(pc.get(k)) and pathlib.Path(pc[k]).is_file(), str(pc.get(k)))
            check("下发路径全部在流程内（无外部依赖）",
                  all("nature-skills-main" not in str(pc.get(k, ""))
                      for k in ("skill_md", "prepare_script", "audit_script",
                                "content_check_script")))
            check("不再下发已舍弃的 reader_skill 字段", "reader_skill" not in pc)
            check("深读卡路径落在 paper_cards/",
                  "paper_cards" in str(pc.get("card_path")))
            check("质量门指向 nature_bridge check",
                  "nature_bridge" in pc.get("quality_gate", "")
                  and "check" in pc.get("quality_gate", ""))
            check("契约声明双产出（只增不减）",
                  "11 字段" in pc.get("dual_output", ""))
        # 旧引擎的历史结果不能被新引擎覆盖（两种引擎共存的前提）
        check("两种引擎共用同一批 tasks/results 命名",
              {t["slug"] for t in tasks} == {t["slug"] for t in tasks2})
    finally:
        if not keep:
            shutil.rmtree(wd, ignore_errors=True)

    # ── ④ 交付质量门：审计 + 数字回查原文 ─────────────────────
    print("\n[4] 交付质量门：nature_bridge check")
    wd2 = mkworkdir("nat_selftest_gate_")
    try:
        pcd = wd2 / ".work_cdp" / "interpret" / "paper_cards"
        pcd.mkdir(parents=True, exist_ok=True)
        smap = pcd / "smap.json"
        smap.write_text(json.dumps({
            "paper": {"title": "Gate test", "source_type": "pdf"},
            "blocks": [
                {"id": "S001", "page": 1, "type": "paragraph",
                 "original_text": "Activity dropped by 92 percent across "
                                  "three independent replicates (n = 6)."},
                {"id": "S002", "page": 2, "type": "paragraph",
                 "original_text": "Figure 1 shows the dose response at 10 micromolar."},
            ],
            "pages": [{"page": 1, "block_ids": ["S001"]},
                      {"page": 2, "block_ids": ["S002"]}],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        bundle = pcd / "b.json"
        code, out, err = run(SDIR / "nature_bridge.py", wd2,
                             ["prepare", "--pdf", str(smap), "--output", str(bundle)],
                             verbose=verbose)
        check("bridge prepare 成功（source_map 通道可用）", code == 0, err.strip()[:150])
        check("source_bundle 已产出", bundle.is_file())

        # 一份「数字都能在原文找到」的卡 → 应通过
        good = pcd / "good.md"
        head = "\n".join("> " + x for x in (
            "Source coverage: Full paper", "Extraction confidence: High",
            "Locator mode: page-grounded", "Primary analytical lens: methods",
            "Secondary analytical lens: None", "Context verification: Paper-only",
            "Card completeness: Complete relative to supplied source"))
        secs = []
        for n in range(1, 17):
            secs.append(f"\n## {n:02d} Section {n}\n[Paper: PDF p. 1] 实测 92% "
                        f"（n = 6）。\n")
        good.write_text(f"# Card\n{head}\n" + "".join(secs), encoding="utf-8")
        rep = pcd / "good.audit.json"
        code, out, _ = run(SDIR / "nature_bridge.py", wd2,
                           ["check", "--card", str(good), "--bundle", str(bundle),
                            "--locator-mode", "page-grounded", "--report", str(rep)],
                           verbose=verbose)
        check("合规卡 + 数字可溯源 → 质量门通过（退出 0）", code == 0,
              f"退出 {code}")
        merged = json.loads(rep.read_text(encoding="utf-8")) if rep.is_file() else {}
        check("合并报告含两部分",
              "structural_audit" in merged and "content_check" in merged)
        check("合并报告申报数字已回查原文",
              (merged.get("source_checked") is True))

        # 编造一个原文没有的数字 → 必须失败（这是 B2 的全部意义）
        bad = pcd / "bad.md"
        bad.write_text(f"# Card\n{head}\n\n## 10 Evidence\n"
                       f"[Paper: PDF p. 1] 抑制率达到 97.5%，样本量 n = 42。\n",
                       encoding="utf-8")
        rep2 = pcd / "bad.audit.json"
        code, out, _ = run(SDIR / "nature_bridge.py", wd2,
                           ["check", "--card", str(bad), "--bundle", str(bundle),
                            "--locator-mode", "page-grounded", "--report", str(rep2)],
                           verbose=verbose)
        check("编造数字 → 质量门失败（退出非 0）", code != 0, f"退出 {code}")
        check("失败原因指名 NUMBER_NOT_IN_SOURCE", "NUMBER_NOT_IN_SOURCE" in out)
    finally:
        if not keep:
            shutil.rmtree(wd2, ignore_errors=True)

    # ── ⑤ 汇总：申报引擎与深读卡状态 ──────────────────────────
    print("\n[5] 阶段 H ④：汇总报告的引擎申报")
    wd3 = mkworkdir("nat_selftest_build_")
    try:
        write_records(wd3)
        run(SDIR / "interpret_prepare.py", wd3,
            ["--no-text", "--limit=2", "--engine=paper-card"], verbose=verbose)
        man = json.loads((wd3 / ".work_cdp" / "interpret" / "manifest.json")
                         .read_text(encoding="utf-8"))
        # 只给第一篇产出卡与审计报告 —— 第二篇留「未跑」，验证如实申报
        first = (man["tasks"][0])["slug"]
        write_card(wd3, first)
        pcd = wd3 / ".work_cdp" / "interpret" / "paper_cards"
        pcd.mkdir(parents=True, exist_ok=True)
        (pcd / f"{first}.md").write_text("# 深读卡夹具\n", encoding="utf-8")
        (pcd / f"{first}.audit.json").write_text(json.dumps({
            "summary": {"status": "pass", "audit_errors": 0, "content_errors": 0,
                        "audit_exit": 0, "content_exit": 0}}), encoding="utf-8")
        code, out, err = run(SDIR / "interpret_build.py", wd3, verbose=verbose)
        check("interpret_build 成功（paper-card 引擎）", code == 0, err.strip()[:150])
        # 报告名 = <作者名>_论文解读.md；作者名缺省取工作目录名，
        #   所以按 glob 找而不是硬编码名字。
        found = list(wd3.glob("*_论文解读.md"))
        report = found[0] if found else wd3 / "_missing_.md"
        check("报告已产出", report.is_file(), report.name)
        body = report.read_text(encoding="utf-8") if report.is_file() else ""
        check("报告头部申报引擎 = paper-card", "解读引擎：**paper-card**" in body)
        check("报告含深读卡与审计状态表", "深读卡（16 节 Paper Card）与审计状态" in body)
        check("未审计的篇目标为「未跑」（不粉饰）", "未跑" in body)
        check("报告点明「未验证 ≠ 已验证」", "未验证 ≠ 已验证" in body)

        # legacy 引擎：报告不得出现深读卡章节（引擎之间互不污染）
        wd4 = mkworkdir("nat_selftest_build_l_")
        try:
            write_records(wd4)
            run(SDIR / "interpret_prepare.py", wd4,
                ["--no-text", "--limit=2", "--engine=legacy"], verbose=verbose)
            m4 = json.loads((wd4 / ".work_cdp" / "interpret" / "manifest.json")
                            .read_text(encoding="utf-8"))
            write_card(wd4, (m4["tasks"][0])["slug"])
            code, out, err = run(SDIR / "interpret_build.py", wd4, verbose=verbose)
            f4 = list(wd4.glob("*_论文解读.md"))
            body4 = (f4[0].read_text(encoding="utf-8") if f4 else "")
            check("legacy 汇总成功", code == 0, err.strip()[:150])
            check("legacy 报告申报引擎 = legacy", "解读引擎：**legacy**" in body4)
            check("legacy 报告**不含**深读卡章节",
                  "深读卡（16 节 Paper Card）" not in body4)
        finally:
            if not keep:
                shutil.rmtree(wd4, ignore_errors=True)
    finally:
        if not keep:
            shutil.rmtree(wd3, ignore_errors=True)

    # ── 汇总 ─────────────────────────────────────────────────
    fails = [r for r in RESULTS if not r[1]]
    print()
    print("─" * 66)
    print(f"通过 {len(RESULTS) - len(fails)}/{len(RESULTS)}")
    if fails:
        print("失败项：")
        for name, _ok, detail in fails:
            print(f"  · {name}" + (f"   —— {detail}" if detail else ""))
    print("结果：" + ("PASS" if not fails else "FAIL"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
