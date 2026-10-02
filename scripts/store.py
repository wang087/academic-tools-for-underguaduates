# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文 PDF 的存放 / 命名 / 去重 / 校验（全流程唯一定义）
#
# ★ 为什么单独抽出来：历史上「是否已下载」的判据在两个脚本里各写一遍，
#   且只看 `{year}_{doi}.pdf` 一种命名，于是对早期下载的
#   `{doi}.pdf`（无年份前缀）视而不见 → 重复下载，甚至在
#   「下载成功但校验不通过」时把**原有好文件删掉**。
#
# 目录规范：<WORKDIR>/<作者名>_paper_Exports/   （见 SKILL.md §7）
#   · PAPER_EXPORTS_DIR 可显式指定完整路径（最高优先级）
#   · PAPER_EXPORTS_NAME 可覆盖目录名里的「作者名」部分
#   · 兼容历史 <作者名>_Exports/：两者都在时优先规范目录；规范目录尚不存在
#     而历史目录里已有 PDF 时沿用历史目录，**不迁移、不丢文件**
#
# 命名规范（新文件一律带年份前缀）：
#   <EXPORTS>/<year>_<题名>.pdf            ← 题名命名（默认，见 title_slug）
#   <EXPORTS>/<year>_<doi 中非 [A-Za-z0-9._-] 字符替换为 _>.pdf
# 兼容读取（去重时同时认）：
#   <EXPORTS>/<doi slug>.pdf        ← 早期版本命名，无年份前缀
#   .work_cdp/pdf_index.json        ← DOI → 文件名 登记表（题名命名后仍可去重）
#
# 可移植配置：PAPER_WORKDIR
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import sys
import tempfile

# 真实 1 页 PDF（勘误 / Author Correction / 简讯）体积通常 > 100 KB；
# 而「渲染兜底产出的 1 页空壳」「验证页打印」通常 < 60 KB。
MIN_BYTES = 45000
ONE_PAGE_MIN_BYTES = 100_000


def workdir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("PAPER_WORKDIR", os.getcwd())).resolve()


# 规范目录名：<作者名>_paper_Exports（见 SKILL.md §7 产出物规范）。
# 兼容历史：早期工作目录用的是 <作者名>_Exports —— 若规范目录尚不存在
# 而历史目录里已有 PDF，则**继续沿用历史目录**（不迁移、不复制、不丢文件），
# 只在 stderr 提示一次；两者都不存在时新建规范目录。
_LEGACY_NOTED = False


_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _safe_dir_name(s: str) -> str:
    """作者名 → 可安全用于目录名的片段（只清 Windows 非法字符，不做改写）。"""
    return _NAME_BAD.sub("_", (s or "").strip()).strip(" .")


def _exports_candidates(wd: pathlib.Path) -> list[pathlib.Path]:
    """返回候选全文目录，**顺序即优先级**。

    ★ 目录要「以作者命名」（见原始需求），因此名字来源按优先级取：
        ① PAPER_EXPORTS_NAME（显式覆盖）
        ② PAPER_AUTHOR      （全流程统一的作者名变量）
        ③ 工作目录名         （历史行为；正常流程里 run_all.sh 建的目录
                              就是 <作者名>，二者一致）
      再 × {规范名 _paper_Exports, 历史名 _Exports}。
    ★ 把所有候选都列出来（而不是只算一个名字），是为了让**既有产物
      无论用的是哪个名字都能被找到**，不会因为改了取名规则就「明明下好了
      却查不到」——那正是 §9 记录过的老毛病。
    """
    names: list[str] = []
    for raw in (os.environ.get("PAPER_EXPORTS_NAME", ""),
                os.environ.get("PAPER_AUTHOR", ""),
                wd.name):
        n = _safe_dir_name(raw)
        if n and n not in names:
            names.append(n)
    # 规范名全体在前，历史名全体在后
    return ([wd / f"{n}_paper_Exports" for n in names]
            + [wd / f"{n}_Exports" for n in names])


def exports_dir(wd: pathlib.Path | None = None) -> pathlib.Path:
    global _LEGACY_NOTED
    # ★ 必须 resolve：相对路径（如 Path(".")）的 .name 为空串，
    #   会拼出 "_Exports" 这种错误目录，导致「明明下好了却查不到」。
    wd = pathlib.Path(wd).resolve() if wd else workdir()

    # ① 显式指定完整路径（相对路径按工作目录解析）——优先级最高
    explicit = os.environ.get("PAPER_EXPORTS_DIR", "").strip()
    if explicit:
        d = pathlib.Path(explicit).expanduser()
        if not d.is_absolute():
            d = wd / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    cands = _exports_candidates(wd)

    # ② 已存在且**含 PDF** 的目录优先 —— 避免同一个作者被拆成两个目录
    for d in cands:
        try:
            if d.is_dir() and next(d.glob("*.pdf"), None) is not None:
                if d is cands[1] and not _LEGACY_NOTED:
                    _LEGACY_NOTED = True
                    sys.stderr.write(
                        f"ℹ 沿用历史全文目录 {d.name}/（规范名为 {cands[0].name}/）。"
                        f"如需迁移：把 PDF 移入规范目录即可，流程会自动改用它。\n")
                return d
        except OSError:
            continue

    # ③ 已存在但暂时为空的目录 —— 用它，避免凭空多出一个空目录
    for d in cands:
        if d.is_dir():
            return d

    # ④ 都不存在 → 新建规范目录
    d = cands[0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def doi_slug(doi: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", doi)


def safe_stem(doi: str, year=None) -> str:
    """新文件命名：<year>_<slug>；year 缺失时退化为 <slug>。"""
    return f"{year}_{doi_slug(doi)}" if year else doi_slug(doi)


# ── 题名命名（默认交付命名）──────────────────────────────────
# Windows 文件名禁用字符：[\\/:*?"<>|] 与控制字符；结尾的「. / 空格」也不允许。
_WIN_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *{f"COM{i}" for i in range(1, 10)},
             *{f"LPT{i}" for i in range(1, 10)}}


def title_slug(title: str, maxlen: int = 120) -> str:
    """论文题名 → 安全的文件名片段（保留可读性，供人直接识别）。"""
    t = re.sub(r"<[^>]+>", " ", title or "")
    t = html.unescape(t)
    t = _WIN_BAD.sub(" ", t)
    t = re.sub(r"[\s  -​　]+", " ", t).strip(" .-")
    if len(t) > maxlen:
        t = t[:maxlen].rstrip(" .-")
    if t.split(".")[0].strip().upper() in _RESERVED:
        t = "_" + t
    return t or "untitled"


def stem_for(doi: str, year=None, title: str | None = None) -> str:
    """默认新文件命名：优先 <year>_<题名>，题名缺失时回退 <year>_<DOI>。"""
    if title:
        t = title_slug(title)
        return f"{year}_{t}" if year else t
    return safe_stem(doi, year)


# ── DOI → 文件名 登记表（题名命名后，去重仍可靠）──────────────
_INDEX_FILE = ".work_cdp/pdf_index.json"


def _index_path(wd: pathlib.Path | None = None) -> pathlib.Path:
    wd2 = pathlib.Path(wd).resolve() if wd else workdir()
    return wd2 / _INDEX_FILE


def load_index(wd: pathlib.Path | None = None) -> dict:
    try:
        return json.loads(_index_path(wd).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_index(idx: dict, wd: pathlib.Path | None = None) -> None:
    p = _index_path(wd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def register_pdf(doi: str, path, wd: pathlib.Path | None = None) -> None:
    """登记 DOI → 实际文件名（改名/题名命名后 existing_pdf 仍能命中）。"""
    if not doi:
        return
    idx = load_index(wd)
    idx[doi.lower()] = pathlib.Path(path).name
    save_index(idx, wd)


_YEAR_RE = re.compile(r"^(?:19|20)\d\d_(.+)$")


def candidates(doi: str, year=None, wd: pathlib.Path | None = None) -> list[pathlib.Path]:
    """该 DOI 可能对应的全部文件名（含历史命名）。"""
    d = exports_dir(wd)
    names = {doi_slug(doi)}
    if year:
        names.add(f"{year}_{doi_slug(doi)}")
    return [d / f"{n}.pdf" for n in names]


def existing_pdf(doi: str, year=None, wd: pathlib.Path | None = None,
                 title: str | None = None) -> pathlib.Path | None:
    """若已有该 DOI 的 PDF（任意命名：题名 / DOI / 登记表），返回其路径。"""
    d = exports_dir(wd)
    def valid(p: pathlib.Path) -> bool:
        if not p.exists():
            return False
        ok, _why = validate_pdf(p)
        return ok

    for p in candidates(doi, year, wd):
        if valid(p):
            return p
    # ① 登记表：题名命名后仍可去重
    name = load_index(wd).get((doi or "").lower())
    if name:
        p = d / name
        if valid(p):
            return p
    # ② 题名命名（未登记时直接按题名找）
    if title:
        for n in (f"{year}_{title_slug(title)}" if year else title_slug(title),
                  title_slug(title)):
            p = d / f"{n}.pdf"
            if p.exists() and p.stat().st_size > 10000:
                return p
    # ③ 兜底：扫目录，忽略年份前缀后比对 DOI slug
    slug = doi_slug(doi).lower()
    for f in d.glob("*.pdf"):
        stem = f.stem
        m = _YEAR_RE.match(stem)
        if (m.group(1) if m else stem).lower() == slug:
            if f.stat().st_size > 10000:
                return f
    return None


def validate_pdf(path, min_pages: int = 2, min_bytes: int = MIN_BYTES):
    """校验是否为真实论文 PDF。

    返回 (ok, why)。规则：
      · 必须是 %PDF 头、体积达标；
      · 页数 ≥ min_pages 即通过；
      · **1 页也放行**，前提是体积 ≥ ONE_PAGE_MIN_BYTES
        —— 勘误/Author Correction/简讯本身就是 1 页，而
           「1 页空壳渲染」和「验证页打印」体积都很小。
    """
    try:
        p = pathlib.Path(path)
        if not p.exists():
            return False, "missing"
        size = p.stat().st_size
        if size < min_bytes:
            return False, f"too-small({size})"
        if not p.read_bytes()[:5].startswith(b"%PDF"):
            return False, "not-pdf"
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p), strict=False)
            if getattr(reader, "is_encrypted", False):
                try:
                    if reader.decrypt("") == 0:
                        return False, "encrypted"
                except Exception:
                    return False, "encrypted"
            n = len(reader.pages)
        except Exception as e:
            return False, f"unparsed:{type(e).__name__}"
        if n >= min_pages:
            return True, f"pages={n}"
        if n == 1 and size >= ONE_PAGE_MIN_BYTES:
            return True, "pages=1(big)"
        return False, f"pages={n}(size={size})"
    except Exception as e:
        return False, str(e)[:40]


def save_pdf(raw: bytes, doi: str, year=None, wd: pathlib.Path | None = None,
             title: str | None = None):
    """原子落盘并校验；失败时绝不破坏同名已有有效 PDF。"""
    p = exports_dir(wd) / f"{stem_for(doi, year, title)}.pdf"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.stem}.", suffix=".part", dir=str(p.parent))
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        ok, why = validate_pdf(tmp)
        if not ok:
            return None, why
        os.replace(tmp, p)
        register_pdf(doi, p, wd)
        return p, why
    finally:
        tmp.unlink(missing_ok=True)
