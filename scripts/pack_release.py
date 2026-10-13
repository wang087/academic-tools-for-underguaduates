# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 打包发行版：把「流程1/」全流程 + 全部依赖输出为一个可独立运行的目录
#
# 产出（默认 `<仓库根>/流程1/`）：
#   流程1/
#   ├── SKILL.md / README.md / run_all.sh      # 主流程（路径已按新目录名重写）
#   ├── scripts/                               # 全部脚本
#   ├── templates/                             # 全部模板（含直连白名单/词表/分区表）
#   ├── subagent-driven-review/SKILL.md        # 阶段 H/H2 子流程
#   ├── ppt-integration/SKILL.md               # 阶段 P 子流程
#   ├── skills/                                # 流程内技能副本（paper-lookup、
#   │                                          #   paper-interpretation-writing）
#   ├── 示例数据/                              # 自检夹具
#   ├── 研究方法与关键词总表.md                 # 词表的来源文档（附）
#   ├── vendor/ppt-master/                     # 依赖：ppt-master 运行时（去 .git/projects/docs）
#   ├── vendor/nature-paper-card/               # 依赖：已并入的 nature-paper-card（Apache-2.0）
#   ├── README-PACKAGE.md                      # 打包说明（与原目录的差异）
#   └── PACKAGE_MANIFEST.json                  # 逐文件 sha256 + 重写记录
#
# 设计要点
#   ① **路径重写**：原文档里写的是 `流程1/scripts/xxx.py`，打包后目录叫 `流程1/`，
#      不改就会让人照着敲错命令。只重写 `流程1/` → `流程1/`，并用负向断言
#      （`流程1/(?!1)`）保证**重复打包不会被改成 流程11/**。
#   ② **依赖去重**：ppt-master 只带运行时必需的部分（skills/ + requirements.txt +
#      许可证/赞助文件 —— 后者是它的 `attribution_guard.py` 完整性门要求的）；
#      nature-paper-card **整体带走**（只并入了这一个技能，144 KB，
#      含 UPSTREAM.md —— 收包的人据此知道本地改动过什么）。
#      `projects/`、`.git/`、`docs/assets/` 不带 —— 那是用户数据与开发资料，
#      带上会让包体积翻几十倍，还可能把别人的项目一起发出去。
#   ③ **自包含**：`ppt_brief.find_ppt_dir()` 会**优先**找 `<流程>/vendor/ppt-master`，
#      `nature_bridge.paper_card_root()` 会**优先**找 `<流程>/vendor/nature-paper-card`，
#      所以打包版脱离原仓库也能跑；找不到才回落到同级仓库目录（仅开发期便利）。
#      v17 起流程内还自带 `skills/`，因此阶段 R 的取数层与阶段 H 的内容级
#      校验器同样不需要外部技能目录。
#   ④ **可核验**：manifest 记录每个文件的 sha256 与重写次数，
#      收到包的人能自己确认"这份包是从哪个版本、改了哪些字符"。
#
# 用法：
#   uv run python 流程1/scripts/pack_release.py
#     --dest=<路径>       目标目录（默认 <仓库根>/流程1）
#     --name=流程1        目标目录名（用于路径重写；默认取 --dest 的名字）
#     --no-vendor         不打包 vendor/（ppt-master 与 nature-paper-card 都不带，
#                         只出流程本体；阶段 P 与阶段 H 的 paper-card 引擎将不可用）
#     --force             目标已存在时先清空再打包
#     --dry-run           只打印将要做什么
#     --self-test         纯逻辑自检（不落盘）
#
# 可移植配置：无（全部走命令行）
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import sys

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
SRC = _HERE.parent                     # 流程1/
REPO = SRC.parent                      # 仓库根

# ── 日志（**文件型**：console=False）────────────────────────────
#   stdout 是给人看的打包进度报告，日志不写控制台，只落
#   `.work_cdp/logs/pack_release_<时间戳>.log` 并追加操作流水。
#   打包是**破坏性**操作（`--force` 会先 rmtree 目标目录），
#   留一份「哪一轮、源/目标是什么、写了多少文件、重写了哪些路径」的
#   流水，事后核对才有依据。成功路径额外写 journal。
try:
    from logutil import get as _get_log, journal as _journal  # type: ignore
except Exception:                      # 单文件运行时也要能用
    def _get_log(name, **kw):          # noqa: D103
        class _L:
            def info(self, *a, **k): pass
            def ok(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass
            def close(self): pass
        return _L()

    def _journal(*a, **k):             # noqa: D103
        pass

# 会被重写的文本扩展名（二进制一律原样复制）
TEXT_EXT = {".md", ".py", ".sh", ".json", ".txt", ".html", ".toml", ".cfg", ".yml", ".yaml"}

# 顶层条目：源目录名 → 是否必须
TOP_ITEMS = ["SKILL.md", "README.md", "run_all.sh", "scripts", "templates",
             "subagent-driven-review", "ppt-integration", "示例数据",
             # v17：流程内的技能副本。两者都是**运行时**依赖，不带就断链：
             #   · skills/paper-lookup        —— route_databases.py 直接调它的
             #       paginate.py / arxiv_atom.py 取数（阶段 R 检索链）
             #   · skills/paper-interpretation-writing —— 它的 check_interpretation.py
             #       是「数字回查原文」的唯一实现（stage H 交付质量门，见
             #       nature_bridge.py check）。虽同为本仓库技能，但流程内自带
             #       一份，分发包才不必依赖外部技能目录。
             "skills",
             # 「取全文 CDP」**可选模块**（出版商正文自动化的唯一实现）。
             #   默认**包含**（内部使用方便）；要发行一份不含该能力的版本，
             #   加 `--exclude-optional-cdp-fulltext`（见模块内 README.md 的合规说明）。
             "optional-cdp-fulltext"]

# 打包时**排除**的目录/文件（用户数据、缓存、开发资料）
EXCLUDE_DIRS = {"__pycache__", ".work_cdp", ".git", "projects", "node_modules"}

# `--exclude-optional-cdp-fulltext` 时**额外**排除的顶层目录：
#   出版商的网站条款禁止自动化抓取其内容，发行版需要时可以把这层整个去掉。
#   去掉后流程仍可运行（探测入口会报"未安装 → 改走备用链路"）。
EXCLUDE_TOP_WHEN_NO_CDP = ("optional-cdp-fulltext",)

# **可选**顶层条目：存在就打包，不存在也不报"缺少必需条目"
#   （发行版可能按合规要求把取全文 CDP 模块整个删掉 —— 那是有意为之，不是残缺）
OPTIONAL_TOP_ITEMS = {"optional-cdp-fulltext"}

# `--slim` 额外裁剪的 ppt-master 子目录（**非本流程运行时**所需）：
#   · references/ai-image-comparison/ —— AI 配图风格样张（文档素材，44 MB）
#   · templates/icons/               —— 图标库（本流程 spec_lock 里 icons.library=none，35 MB）
#   · templates/sounds/              —— 转场音效（仅可选音频环节用，13 MB）
# 默认**不裁剪**（保持依赖完整）；需要小体积时才加 --slim，
# 裁剪后若日后要用配图/图标/音效，需回原 ppt-master 取。
SLIM_PATHS = ("skills/ppt-master/references/ai-image-comparison",
              "skills/ppt-master/templates/icons",
              "skills/ppt-master/templates/sounds")
EXCLUDE_FILE_PATTERNS = [r".*\.pyc$", r".*\.log$", r"latest_.*\.log$"]


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rewrite_paths(text: str, name: str) -> tuple[str, int]:
    """把 `流程1/` 重写成 `<name>/`。

    负向断言 `(?!1)` 之类不能写死 —— 目录名是参数化的。
      这里改为：**只匹配后面不紧跟数字或该名字剩余部分**的形式，
      即 `流程1/` 且 `流程` 之后不是 `1`/`2`… 也不是别的中文字。
      简化且可靠的做法：匹配 `流程1/`，但排除 `流程<name尾部>/`。
    """
    if name == "流程":
        return text, 0
    suffix = name[len("流程"):] if name.startswith("流程") else name
    # `流程1/` → `<name>/`；若已经是 `流程1/` 则不再动
    pat = re.compile(r"流程1/(?!" + re.escape(suffix) + r")")
    new, n = pat.subn(f"{name}/", text)
    # ★ 恒等替换不能计数：当 `name == "流程1"` 时，`suffix == "1"`，
    #   正则 `流程1/(?!1)` 会把 `流程1/` **替换成它自己** —— 文本没变，
    #   但 subn 仍报 n=1，导致：
    #     ① `--self-test` 的幂等断言 `(once == twice) and n2 == 0` 失败
    #        （打包发行版改名 `流程` → `流程1` 后必然踩到，与本函数逻辑无关）；
    #     ② 打包统计「N 个文件做了路径重写」虚高 —— 实际一个都没改。
    #   故文本未变时一律返回 0，让「计数」忠实反映「是否真的改了」。
    if new == text:
        return text, 0
    return new, n


def iter_files(root: pathlib.Path, extra_exclude: set | None = None):
    extra = extra_exclude or set()
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if extra and any(part in extra for part in p.parts):
            continue
        if any(re.match(pat, p.name) for pat in EXCLUDE_FILE_PATTERNS):
            continue
        yield p


def self_test() -> int:
    bad = 0
    cases = [
        ("跑 uv run python 流程1/scripts/a.py", "流程1", "跑 uv run python 流程1/scripts/a.py"),
        # 幂等：已经是 流程1/ 的不能再被改成 流程11/
        ("跑 流程1/scripts/a.py", "流程1", "跑 流程1/scripts/a.py"),
        ("见 流程1/SKILL.md §5.1", "流程1", "见 流程1/SKILL.md §5.1"),
        ("本流程1/子流程 的边界", "流程1", "本流程1/子流程 的边界"),   # 保守：宁可多改
    ]
    for src, name, want in cases:
        got, _ = rewrite_paths(src, name)
        if got != want:
            bad += 1
            print(f"  [X] rewrite({src!r}) = {got!r}，期望 {want!r}")
    print(f"路径重写：{len(cases) - bad}/{len(cases)} 通过")

    # 幂等性单独断言（这是最容易出的错：重复打包把 流程1 变成 流程11）
    once, _ = rewrite_paths("流程1/scripts/x.py", "流程1")
    twice, n2 = rewrite_paths(once, "流程1")
    ok = (once == twice) and n2 == 0
    print(f"重复打包幂等：{'通过' if ok else '失败 → ' + twice}")
    bad += 0 if ok else 1

    if not SRC.is_dir():
        print(f"[X] 源目录不存在：{SRC}")
        return 1
    missing = [t for t in TOP_ITEMS
               if t not in OPTIONAL_TOP_ITEMS and not (SRC / t).exists()]
    print(f"源目录必需条目：{'齐全' if not missing else '缺少 ' + str(missing)}")
    bad += 0 if not missing else 1
    print("\n[OK] 自检全部通过" if bad == 0 else "\n[X] 自检有失败项")
    return 0 if bad == 0 else 1


def _run(log) -> int:
    if "--self-test" in sys.argv:
        log.info("self-test 模式")
        return self_test()

    def argval(n, d=None):
        for a in sys.argv[1:]:
            if a.startswith(f"--{n}="):
                return a.split("=", 1)[1]
        return d

    dest = pathlib.Path(argval("dest") or (REPO / "流程1")).resolve()
    name = argval("name") or dest.name
    no_vendor = "--no-vendor" in sys.argv
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    slim = "--slim" in sys.argv
    # 合规开关：默认**包含**取全文 CDP 模块；要发行不含该能力的版本时显式排除。
    no_cdp_mod = "--exclude-optional-cdp-fulltext" in sys.argv
    extra_exclude = set(EXCLUDE_TOP_WHEN_NO_CDP) if no_cdp_mod else set()
    slimmer: list[str] = []

    print("═" * 68)
    print(" 打包发行版")
    print(f" 源 ：{SRC}")
    print(f" 目标：{dest}（目录名 {name}）")
    print(f" 依赖：{'不打 vendor' if no_vendor else '含 vendor/ppt-master + vendor/nature-paper-card'}"
          + ("（--slim：裁剪配图样张/图标库/音效）" if slim else "（完整）"))
    print(" 取全文 CDP 模块：" + ("**已排除**（--exclude-optional-cdp-fulltext："
          "发行版不含出版商正文自动化；流程改走备用链路）" if no_cdp_mod
          else "包含（optional-cdp-fulltext/；合规发行请加 --exclude-optional-cdp-fulltext）"))
    print("═" * 68)

    if not SRC.is_dir():
        log.error(f"源目录不存在：{SRC}")
        print(f"[X] 源目录不存在：{SRC}")
        return 2
    if dest == SRC:
        log.error(f"目标与源相同（会自我覆盖）：{dest}")
        print("[X] 目标与源相同（会自我覆盖）—— 换一个 --dest")
        return 2
    if dest.exists():
        if not force and not dry:
            log.error(f"目标已存在：{dest}")
            print(f"[X] 目标已存在：{dest}")
            print("  要覆盖请加 --force（会先清空该目录）")
            return 2
        if not dry:
            log.warn(f"--force：先清空目标目录 {dest}")
            shutil.rmtree(dest)

    manifest = {"source": str(SRC), "dest": str(dest), "name": name,
                "files": [], "rewritten": [], "skipped": [], "vendor": not no_vendor,
                "slim": slim, "slimmed": [],
                "optional_cdp_fulltext": not no_cdp_mod,
                "excluded_top": sorted(extra_exclude)}

    if not dry:
        dest.mkdir(parents=True, exist_ok=True)

    # ── ① 流程本体 ────────────────────────────────────────────
    n_copy = n_rewrite = 0
    for item in TOP_ITEMS:
        s = SRC / item
        if not s.exists():
            manifest["skipped"].append(f"{item}（源缺失）")
            continue
        if s.is_file():
            files = [s]
        else:
            files = list(iter_files(s, extra_exclude=extra_exclude))
        for f in files:
            rel = f.relative_to(SRC)
            out = dest / rel
            data = f.read_bytes()
            rewrites = 0
            if f.suffix.lower() in TEXT_EXT:
                try:
                    txt = data.decode("utf-8")
                except UnicodeDecodeError:
                    txt = None
                if txt is not None:
                    new, rewrites = rewrite_paths(txt, name)
                    if rewrites:
                        data = new.encode("utf-8")
            if not dry:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
            n_copy += 1
            if rewrites:
                n_rewrite += 1
                manifest["rewritten"].append({"file": str(rel), "count": rewrites})
            manifest["files"].append({"path": str(rel), "bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})

    # ── ② 附带：词表来源文档 ──────────────────────────────────
    doc = REPO / "研究方法与关键词总表.md"
    if doc.exists():
        data = doc.read_bytes()
        if not dry:
            (dest / doc.name).write_bytes(data)
        manifest["files"].append({"path": doc.name, "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
        n_copy += 1
    else:
        manifest["skipped"].append("研究方法与关键词总表.md（源缺失，不影响运行）")

    # ── ③ 依赖：ppt-master 运行时 ─────────────────────────────
    if not no_vendor:
        pm_src = None
        for cand in (REPO / "ppt-master", SRC / "vendor" / "ppt-master"):
            if (cand / "skills" / "ppt-master" / "SKILL.md").exists():
                pm_src = cand
                break
        if pm_src is None:
            print("[WARN] 找不到 ppt-master，跳过 vendor（阶段 P 将不可用）")
            manifest["vendor"] = False
            manifest["skipped"].append("vendor/ppt-master（未找到源）")
        else:
            pm_dst = dest / "vendor" / "ppt-master"
            # LICENSE / SPONSORS*.md 在 `skills/ppt-master/` 里（attribution_guard.py
            #   以 skills/ppt-master 为 _SKILL_DIR 校验它们），不在仓库根 ——
            #   把它们列在根一级会误报"源缺失"，其实随 skills/ 已经进来了。
            keep = ["skills", "requirements.txt", "LICENSE", "README.md"]
            vn = 0
            for item in keep:
                s = pm_src / item
                if not s.exists():
                    manifest["skipped"].append(f"vendor/ppt-master/{item}（源缺失）")
                    continue
                files = [s] if s.is_file() else list(iter_files(s))
                for f in files:
                    rel = f.relative_to(pm_src)
                    if slim and any(str(rel).replace("\\", "/").startswith(x)
                                    for x in SLIM_PATHS):
                        slimmer.append(str(rel))
                        continue
                    out = pm_dst / rel
                    data = f.read_bytes()
                    if not dry:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(data)
                    vn += 1
                    manifest["files"].append(
                        {"path": f"vendor/ppt-master/{rel}", "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
            n_copy += vn
            manifest["slimmed"] = slimmer
            print(f"  · vendor/ppt-master：{vn} 个运行时文件"
                  + (f"（--slim 另裁掉 {len(slimmer)} 个非运行时文件）" if slimmer else ""))

    # ── ③b v17：依赖：已并入的 nature-paper-card（非外部依赖）──────
    #   只并入了**这一个**技能，整目录都是运行时/溯源材料，故整体带走
    #   （含 UPSTREAM.md 的补丁登记 —— 收包的人据此知道本地改动过什么）。
    #   并入后 流程1 不再需要仓库根的 nature-skills-main 检出。
    if not no_vendor:
        ns_src = None
        for cand in (SRC / "vendor" / "nature-paper-card",
                     REPO / "nature-skills-main" / "skills" / "nature-paper-card",
                     REPO / "流程1" / "vendor" / "nature-paper-card"):
            if (cand / "SKILL.md").exists():
                ns_src = cand
                break
        if ns_src is None:
            print("[WARN] 找不到 vendor/nature-paper-card，跳过"
                  "（阶段 H 的 paper-card 引擎将不可用）")
            manifest["skipped"].append("vendor/nature-paper-card（未找到源）")
        else:
            ns_dst = dest / "vendor" / "nature-paper-card"
            ns_files = 0
            for f in iter_files(ns_src):
                rel = f.relative_to(ns_src)
                out = ns_dst / rel
                data = f.read_bytes()
                if not dry:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                ns_files += 1
                manifest["files"].append(
                    {"path": f"vendor/nature-paper-card/{rel}", "bytes": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()})
            n_copy += ns_files
            print(f"  · vendor/nature-paper-card：{ns_files} 个文件"
                  f"（单技能并入，Apache-2.0；补丁登记见其 UPSTREAM.md）")

    # ── ④ 打包说明 ────────────────────────────────────────────
    notes = render_notes(name, dest, manifest)
    if not dry:
        # 必须 newline=""：Windows 上 write_text 默认把 \n 翻成 \r\n，
        #   写出来的字节就和这里算的 sha256 对不上了 —— 收包的人一核验就报
        #   "README-PACKAGE.md 不一致"，看起来像包被篡改过。
        #   （其它文件走 write_bytes，不做换行转换，故无此问题。）
        (dest / "README-PACKAGE.md").write_text(notes, encoding="utf-8", newline="")
        manifest["files"].append(
            {"path": "README-PACKAGE.md", "bytes": len(notes.encode("utf-8")),
             "sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest()})
        (dest / "PACKAGE_MANIFEST.json").write_text(
            json.dumps(portable_manifest(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8")

    total = sum(f["bytes"] for f in manifest["files"])
    log.ok(f"文件 {n_copy} 个（其中 {n_rewrite} 个做了路径重写）｜合计 {total / 1024:.0f} KB"
           f"｜源 {SRC} → 目标 {dest}{'（dry-run）' if dry else ''}")
    if manifest["skipped"]:
        log.warn("跳过：" + "；".join(str(x) for x in manifest["skipped"][:6]))
    _journal("I", "pack.release", src=str(SRC), dest=str(dest), name=name,
             files=n_copy, rewritten=n_rewrite, total_kb=round(total / 1024),
             vendor=manifest["vendor"], slim=slim, dry=bool(dry))
    print(f"\n文件 {n_copy} 个（其中 {n_rewrite} 个做了路径重写）｜"
          f"合计 {total / 1024:.0f} KB")
    if manifest["skipped"]:
        print("跳过：" + "；".join(manifest["skipped"][:6]))
    if dry:
        print("\n（--dry-run：未写入任何文件）")
        return 0
    print(f"\n[OK] 打包完成 → {dest}")
    print("  下一步（在新目录内自检，确认脱离原仓库也能跑）：")
    print(f"    cd {dest}")
    print(f"    uv run python {name}/scripts/selftest_pipeline.py --offline")
    return 0


def main() -> int:
    log = _get_log("pack_release", console=False)
    try:
        return _run(log)
    finally:
        log.close()


def portable_manifest(manifest: dict) -> dict:
    """把清单里的**构建机绝对路径**换成可移植值。

    为什么必须换：`PACKAGE_MANIFEST.json` 会随包分发。
      记录打包者的 `C:\\Users\\<名字>\\AppData\\Local\\Temp\\…` 既没有信息量，
      又把本机布局泄露出去（本流程 v16.1 的主题之一就是"去硬编码"）。
      逐文件 sha256 全部保留 —— 那才是这份清单的用处。
    """
    out = dict(manifest)
    for key in ("source", "dest"):
        if isinstance(out.get(key), str):
            out[key] = pathlib.Path(out[key]).name or out[key]
    return out


def render_notes(name: str, dest: pathlib.Path, manifest: dict) -> str:
    L = []
    L.append(f"# {name} · 打包发行版说明\n")
    L.append(f"> 本目录是 `流程1/` 的**自包含发行版**：全流程脚本 + 模板 + 子流程文档 "
             f"+ 依赖（`vendor/ppt-master`、`vendor/nature-paper-card`、`skills/`）。")
    L.append(f"> 源目录：`{pathlib.Path(manifest['source']).name}`")
    L.append(f"> 文件数：**{len(manifest['files'])}** ｜ "
             f"路径重写：**{len(manifest['rewritten'])}** 个文件\n")

    L.append("## 与原 `流程1/` 目录的差异\n")
    L.append("| 项 | 说明 |")
    L.append("|---|---|")
    L.append(f"| 目录名 | `流程1/` → `{name}/`；**文档与脚本里的路径引用已同步重写**"
             f"（{len(manifest['rewritten'])} 个文件），照着文档敲命令不会敲错 |")
    L.append("| 依赖 ① | `vendor/ppt-master/`（pptx 生成依赖，阶段 P 用）。"
             "`ppt_brief.py` / `selftest_pipeline.py` **优先**用这份随包副本 |")
    L.append("| 依赖 ② | `vendor/nature-paper-card/`（**只并入了这一个** nature 技能，Apache-2.0，"
             "**已并入本流程，非外部依赖**）。阶段 H 的 `paper-card` 引擎用它产出 "
             "16 节深读卡；`nature_bridge.py` **优先**用这份随包副本 |")
    L.append("| 依赖 ③ | `skills/paper-lookup/`（阶段 R 取数层）与 "
             "`skills/paper-interpretation-writing/`（内容级校验器 "
             "`check_interpretation.py` —— 「数字回查原文」的唯一实现） |")
    L.append("| 未包含 | `ppt-master/projects/`（用户项目数据）、`.git/`（版本库）、"
             "`docs/assets/`（上游其余技能与官网素材）—— "
             "属开发/用户数据或纯示例素材，不应随包分发 |")
    L.append("| 内容 | 其余文件与源目录**逐字节一致**（除路径重写）；"
             "可用 `PACKAGE_MANIFEST.json` 的 sha256 核验 |")
    L.append("")

    L.append("## 快速开始\n")
    L.append("```bash")
    L.append(f"cd {dest}")
    L.append("")
    L.append("# ① 端到端自检（离线 20 项，不联网、秒级）")
    L.append(f"uv run python {name}/scripts/selftest_pipeline.py --offline")
    L.append("#    联网全量（38 项）")
    L.append(f"uv run python {name}/scripts/selftest_pipeline.py")
    L.append("")
    L.append("# ② 阶段 0：准备阶段（在对话里收齐选择后落盘）")
    L.append(f"uv run python {name}/scripts/prepare_stage.py \\")
    L.append("    --set output_format=markdown --set translate=no \\")
    L.append("    --set reverse_refs=yes --set make_ppt=no")
    L.append(f"uv run python {name}/scripts/prepare_stage.py --check-all")
    L.append("```")
    L.append("")
    L.append(f"完整流程见 [`{name}/SKILL.md`]({name}/SKILL.md)；"
             f"上手看 [`{name}/README.md`]({name}/README.md)。")
    L.append("")

    L.append("## 环境要求\n")
    L.append("| 项 | 要求 |")
    L.append("|---|---|")
    L.append("| 操作系统 | Windows（流程基于 win32 + Git Bash） |")
    L.append("| Python | 通过 `uv` 运行，脚本按需 `uv run --with <pkg>` 拉依赖 |")
    L.append("| 浏览器 | Microsoft Edge（阶段 B–E 的 CDP 抓取要用） |")
    L.append("| 网络 | 阶段 D2/G2/H2 走 OpenAlex 等元数据 API；"
             "摘要在第一层直连；**原文全文经 CDP** |")
    L.append("")

    L.append("## 抓取策略（v7.1 分层）\n")
    L.append("| 层 | 内容 | 方式 |")
    L.append("|---|---|---|")
    L.append("| 第一层 | 元数据 / **摘要** / **综述题录** / 白名单开源库 | 允许直连"
             f"（白名单：`{name}/templates/直连白名单.json`） |")
    L.append("| 第二层 | **原文全文**（PDF / HTML，含 OA） | 禁止直连，一律走 CDP |")
    L.append("| 回退 | 第一层失败 | -> 转 CDP（`grab_abstracts.py --retry-failed`） |")
    L.append("")

    L.append("## 核验这份包\n")
    L.append("```bash")
    L.append("# 1) 逐文件核对 sha256")
    L.append("uv run python - <<'PY'")
    L.append("import json, pathlib, hashlib")
    L.append(f"m = json.loads(pathlib.Path('PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))")
    L.append("bad = []")
    L.append("for f in m['files']:")
    L.append("    p = pathlib.Path(f['path'])")
    L.append("    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != f['sha256']:")
    L.append("        bad.append(f['path'])")
    L.append("print('全部一致' if not bad else f'{len(bad)} 个文件不一致: {bad[:5]}')")
    L.append("PY")
    L.append("")
    L.append("# 2) 跑自检（能跑通 = 依赖齐全）")
    L.append(f"uv run python {name}/scripts/selftest_pipeline.py --offline")
    L.append("```")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
