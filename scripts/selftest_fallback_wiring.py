# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 副链路接线自检 —— 端到端验「reviews_fulltext ↔ fulltext_router」的契约
#
# 为什么要有这个脚本
#   副链路（fulltext_router.py）的每个模块自己都有 `--self-test`，
#   聚合器 selftest_fulltext_modules.py 也跑得到它。但接线断掉的方式
#   从来不是「脚本报错」，而是**契约对不上**：
#     · 「CDP 已失败」的判据在 reviews_fulltext 算一套、在 router 算另一套，
#       于是已经拿到全文的论文被副链路又跑一遍，白耗同一站点的配额；
#     · 副链路取到的是 PDF，却把二进制字节当正文塞进任务包 —— 回填是
#       **按字数择优**的，一段 PDF 字节很容易「胜出」并盖住真正文；
#     · 阶段 0 的 `fulltext_fallback=off` 写了，但没人读，副链路照跑。
#   这三条只有把两个脚本接起来跑一遍才暴露，所以单独成一个自检。
#
# [STOP] 全程零网络
#   `fulltext_router.route_many` 被换成夹具实现 —— 本脚本验的是**接线**，
#   不是路线本身（路线由 fulltext_router.py --self-test 的 40 项断言覆盖）。
#   若哪天有人把这里的夹具换成真调用，脚本会在 CI 里开始发外网请求，
#   那是要立刻发现的事故，不是「测试变强了」。
#
# 用法：
#   uv run python 流程1/scripts/selftest_fallback_wiring.py
#     --keep      保留临时工作目录（默认跑完删除）
#
# 退出码：0 = 全部通过；1 = 有失败项
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


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

# [STOP] 必须在 import reviews_fulltext **之前**设好 PAPER_WORKDIR：
#   那个模块的 WD / WORK / OUTPUTS 是模块级常量，import 时就定死了。
#   顺序错了不会报错，只会静默地在真实工作目录里造文件。
WD = pathlib.Path(tempfile.gettempdir()) / f"flow1_fallback_wiring_{os.getpid()}"
os.environ["PAPER_WORKDIR"] = str(WD)
os.environ.pop("LIT_FLOW1_ALLOW_PUBLISHER_API", None)

import fulltext_router as frt          # noqa: E402
import reviews_fulltext as rf          # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def chk(cond: bool, label: str, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    print(f"  {'[OK]' if cond else '[X] '} {label}"
          + (f" —— {detail}" if detail and not cond else ""))


DOI_GAP = "10.1016/j.gap"        # 三条通道都没有 → 该进副链路
DOI_HAVE = "10.1038/s41577-x"    # 第一层已拿到 → 必须排除
STUB_CHARS = 120
REAL_CHARS = 5000
FALLBACK_CHARS = 30000


def build_fixture(wd: pathlib.Path) -> pathlib.Path:
    """造一个最小的阶段 R 输出 + 三条通道的产物。返回阶段目录。"""
    shutil.rmtree(wd, ignore_errors=True)
    stage = wd / "outputs" / "03_领域概貌"
    (stage / "tasks").mkdir(parents=True)
    work = wd / ".work_cdp"
    work.mkdir(parents=True)

    real = work / "layer1_have.txt"
    real.write_text("R" * REAL_CHARS, encoding="utf-8")
    (work / "reviews_layer1.json").write_text(
        json.dumps([{"doi": DOI_HAVE, "path": str(real)}], ensure_ascii=False),
        encoding="utf-8")

    for slug, doi, title in (("gap", DOI_GAP, "A survey of deep learning"),
                             ("have", DOI_HAVE, "Immunology review")):
        (stage / "tasks" / f"{slug}.json").write_text(
            json.dumps({"doi": doi, "title": title,
                        "evidence_source": "abstract", "text": "abstract only"},
                       ensure_ascii=False), encoding="utf-8")
    (stage / "manifest.json").write_text(
        json.dumps({"items": [{"doi": DOI_GAP}, {"doi": DOI_HAVE}]},
                   ensure_ascii=False), encoding="utf-8")
    return stage


def install_stub(out_txt_name: str = "gap.txt", chars: int = FALLBACK_CHARS):
    """把 route_many 换成夹具；返回捕获到的调用参数。"""
    captured: dict = {}

    def fake_route_many(articles, **kw):
        captured["articles"] = articles
        captured["kw"] = kw
        out = pathlib.Path(kw["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        txt = out / out_txt_name
        txt.write_text("F" * chars, encoding="utf-8")
        return [{"doi": a["doi"], "title": a.get("title", ""), "path": str(txt),
                 "chars": chars, "pdf": None,
                 "status": "native_fulltext_downloaded", "kind": "SUCCESS",
                 "provider": "open_access", "next": "", "reason": "",
                 "oa_status": "confirmed_oa", "candidates": [], "attempts": []}
                for a in articles]

    original = frt.route_many
    frt.route_many = fake_route_many
    return captured, original


def main() -> int:
    keep = "--keep" in sys.argv
    print("=== selftest_fallback_wiring.py（零网络）===")
    stage = build_fixture(WD)
    work = WD / ".work_cdp"

    try:
        print("\n① 缺口判据：只把主链路真没拿到的 DOI 交给副链路")
        captured, original = install_stub()
        try:
            tasks = rf.load_tasks(stage)
            chk(len(tasks) == 2, "任务包读到 2 篇", f"实得 {len(tasks)}")
            res = rf.run_fallback(tasks, mode="oa")
            arts = captured.get("articles") or []
            chk([a["doi"] for a in arts] == [DOI_GAP],
                "缺口只有 1 篇（第一层已覆盖的那篇被排除）",
                f"实得 {[a['doi'] for a in arts]}")
            chk(all(a.get("cdp_failed") is True for a in arts),
                "每篇都显式带 cdp_failed=True（router 不变量一的输入端）")
            chk(captured["kw"].get("mode") == "oa", "mode 透传给 router")
            chk(res.get("success") == 1 and res.get("total") == 1,
                "汇总计数正确", f"实得 {res}")
            chk((work / "reviews_fallback.json").is_file(), "记录已落地 reviews_fallback.json")
        finally:
            frt.route_many = original

        print("\n② mode=off → 短路，一个 DOI 都不交给 router")
        captured, original = install_stub()
        try:
            res = rf.run_fallback(rf.load_tasks(stage), mode="off")
            chk("articles" not in captured, "off 时没有调用 route_many")
            chk(res.get("skipped_reason") == "mode=off",
                "如实转述「用户关掉了」，不当成错误", f"实得 {res}")
        finally:
            frt.route_many = original

        print("\n③ 无缺口 → 不重复跑（省配额）")
        captured, original = install_stub()
        try:
            res = rf.run_fallback(rf.load_tasks(stage), mode="oa")
            chk(res.get("skipped_reason") == "no-gap" and "articles" not in captured,
                "全部已覆盖时不再跑副链路", f"实得 {res}")
        finally:
            frt.route_many = original

        print("\n④ 回填第 ④ 通道：副链路产物进任务包，来源标签可辨")
        stat = rf.backfill(stage, rf.load_tasks(stage))
        d = json.loads((stage / "tasks" / "gap.json").read_text(encoding="utf-8"))
        chk(d.get("evidence_source") == "fulltext", "缺口篇升级为 fulltext")
        chk(d.get("fulltext_chars") == FALLBACK_CHARS,
            f"正文 {FALLBACK_CHARS:,} 字", f"实得 {d.get('fulltext_chars')}")
        chk(str(d.get("fulltext_source", "")).startswith("fallback-"),
            "来源标签带 fallback- 前缀（择优日志里能看出是哪条路线）",
            f"实得 {d.get('fulltext_source')}")
        dh = json.loads((stage / "tasks" / "have.json").read_text(encoding="utf-8"))
        chk(dh.get("fulltext_source") == "europepmc-fullTextXML",
            "第一层那篇的来源没被副链路改写", f"实得 {dh.get('fulltext_source')}")
        chk(stat.get("fulltext") == 2 and stat.get("abstract") == 0,
            "小结正确", f"实得 {stat}")
        mf = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        chk(mf.get("evidence_fulltext") == 2, "manifest 同步跟上", f"实得 {mf}")

        print("\n⑤ 择优不被副链路污染：付费墙残页不得盖住真正文")
        stub = work / "stub.txt"
        stub.write_text("paywall stub " * (STUB_CHARS // 13 + 1), encoding="utf-8")
        (work / "reviews_fallback.json").write_text(json.dumps(
            [{"doi": DOI_HAVE, "path": str(stub), "provider": "open_access",
              "status": "full_text_html_available", "kind": "SUCCESS"}],
            ensure_ascii=False), encoding="utf-8")
        rf.backfill(stage, rf.load_tasks(stage))
        dh = json.loads((stage / "tasks" / "have.json").read_text(encoding="utf-8"))
        chk(dh.get("fulltext_chars") == REAL_CHARS
            and dh.get("fulltext_source") == "europepmc-fullTextXML",
            f"{STUB_CHARS} 字的残页没有盖住 {REAL_CHARS:,} 字的真正文",
            f"实得 {dh.get('fulltext_chars')} / {dh.get('fulltext_source')}")

        print("\n⑥ 只取到 PDF（无正文文本）→ path=null，绝不进候选池")
        pdf = work / "fallback" / "x.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.7\n" + b"0" * 500)
        rec = {"doi": DOI_GAP, "path": None, "pdf": str(pdf), "chars": 0,
               "provider": "elsevier", "status": "downloaded", "kind": "SUCCESS"}
        pool: dict = {}
        rf._add_from_record(pool, rec)
        chk(not pool, "path=null 的记录被 _add_from_record 拒收", f"实得 {pool}")
        # 「PDF 不会变成 path」这件事由 router 侧的 text_path_of 保证，
        #   它自己的 --self-test ⑤ 已经断言过（PDF → text_path_of 返回 None）。
        #   这里不重复验，只验接线端确实按 path=null 处理。
        chk(rec.get("pdf") and not rec.get("path"),
            "记录形态：pdf 字段有值而 path 为 null（两条路分开记，不混用）")

        print("\n⑦ router 侧守卫：CDP 未失败 → 拒绝，且不发任何请求")
        calls: list[str] = []

        def _nonet(url, headers, timeout):
            calls.append(url)
            raise AssertionError(f"不该发请求：{url}")

        r = frt.route_doi({"doi": DOI_GAP, "title": "A survey"},
                          out_dir=work / "fallback", mode="oa+api",
                          cdp_failed=False, fetch_impl=_nonet)
        chk(r.status == "route_disabled" and r.kind == "FAILURE",
            "cdp_failed=False → route_disabled", f"实得 {r}")
        chk(not calls, "守卫拦在发请求之前", f"实际发了 {calls}")
    finally:
        if keep:
            print(f"\n[--keep] 临时工作目录保留在 {WD}")
        else:
            shutil.rmtree(WD, ignore_errors=True)

    failed = [(lb, dt) for lb, ok, dt in RESULTS if not ok]
    print("\n" + "-" * 66)
    if failed:
        print(f"[X] {len(failed)}/{len(RESULTS)} 项失败")
        for lb, dt in failed:
            print(f"    · {lb}" + (f" —— {dt}" if dt else ""))
    else:
        print(f"[OK] {len(RESULTS)}/{len(RESULTS)} 项全部通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
