# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# JATS 全文判据与正文抽取（可复用模块）
#
# 为什么单独抽出来
#   判据原本只写在 reviews_fulltext.py 的闭包里（阶段 R 专用）。
#   但「拿到 XML」不等于「拿到全文」这件事**与阶段无关**：
#     PMC 系接口对**非 OA** 文章会返回
#       「HTTP 200 + 结构完整的 <pmc-articleset>，但**没有 <body>**」，
#     失败原因藏在 XML **注释**里，任何标准解析器都会把它丢掉。
#   本模块把它提升为全流程共用判据，供：
#     · 阶段 R 的 EPMC fullTextXML 通道
#     · 新增的出版商 API（Springer Nature 返回的就是 JATS）
#     · PMC 仓库副本通道
#   共用**同一份**实现，避免两处漂移。
#
# 与既有实现的关系
#   `jats_body_stats()` 的**语义**与 `reviews_fulltext.jats_body_stats()` 逐字等价。
#   `--self-test` 里有一条**交叉验算**：若 `reviews_fulltext.py` 可导入，
#   就对同一份夹具比对两个实现的结果，不一致即失败。
#   这样「两处漂移」会在自检阶段暴露，而不是等下游读到假全文。
#
# 用法：
#   from jats_fulltext import jats_body_stats, MIN_FULLTEXT_CHARS, is_real_fulltext
#   ok, secs, chars = jats_body_stats(xml)
#   real, why = is_real_fulltext(xml)
#
#   uv run python scripts/jats_fulltext.py --self-test
#   uv run python scripts/jats_fulltext.py --file <path.xml>
#
# 退出码：0 = 通过；1 = 有失败项
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import pathlib
import re
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
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ══════════════════════════════════════════════════════════════
# 判据常量
# ══════════════════════════════════════════════════════════════

# 低于此字符数不当作正文（多为占位页 / 勘误页 / 付费墙残页）。
# 与 reviews_fulltext.MIN_FULLTEXT_CHARS 同值。改这里就是改全流程。
MIN_FULLTEXT_CHARS = 6000

# 单行软换行上限：必须与 prep_r2_tasks.py 的约定一致。
# 原因见 docs/阶段R全文获取模块.md 五之⑤：
#   JSON 里的全文是**一整条物理行**，而读取工具对单行的上限约 2000 字符，
#   于是 subagent 只能看到全文的 1.2% 却以为读到了全文 —— 静默失败。
SOFT_WRAP = 1200


# ══════════════════════════════════════════════════════════════
# 核心判据
# ══════════════════════════════════════════════════════════════
def jats_body_stats(xml: str) -> tuple[bool, int, int]:
    """→ (has_body, n_sections, body_chars)。

    [STOP] 这是本模块最重要的一处判据，语义与
    `reviews_fulltext.jats_body_stats()` **逐字等价**（有交叉验算自检）。

    只认 `<body` 的存在，不依赖任何 XML 解析器 —— 因为失败原因是
    藏在 XML **注释**里的，标准解析器会把它丢掉。
    """
    if not xml or "<body" not in xml:
        return False, 0, 0
    body = xml.split("<body", 1)[1]
    chars = len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
    return True, body.count("<sec"), chars


def strip_jats_tags(xml: str) -> str:
    """剥 JATS 标签，压空白。给 `--file` 人工查看用，不用于判据。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml or "")).strip()


def is_real_fulltext(xml: str, min_chars: int = MIN_FULLTEXT_CHARS) -> tuple[bool, str]:
    """判据的唯一对外入口 → (是否真全文, 原因)。

    判据是**合取**，两者缺一不可：
      · `<body>` 必须存在（区分「articleset 壳」与真全文）；
      · 正文字符数必须 >= min_chars（区分真正文与占位/残页）。
    """
    has_body, n_sec, chars = jats_body_stats(xml)
    if not has_body:
        return False, "no-body（HTTP 200 但无 <body>，多半是非 OA 的 articleset 壳）"
    if chars < min_chars:
        return False, f"body-too-short({chars}<{min_chars}, sec={n_sec})"
    return True, f"body-chars={chars} sec={n_sec}"


# ══════════════════════════════════════════════════════════════
# 可选：正文抽取（与 prep_r2_tasks.py 的落盘约定对齐）
# ══════════════════════════════════════════════════════════════
def body_text(xml: str) -> str:
    """抽出 `<body>` 之后的纯文本（保留段落粗结构，压行内空白）。

    与 `jats_to_text.py` 的完整结构化转换**不重复**：
    本函数只做「够用于判据 + 够用于落盘」的最小提取。
    需要章节结构时仍应走 `scripts/jats_to_text.py`。
    """
    if not xml or "<body" not in xml:
        return ""
    body = xml.split("<body", 1)[1]
    # 块级标签转换行，行内标签直接去掉
    body = re.sub(r"</(?:p|sec|title|abstract|td|th|tr|list-item|fig|caption)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    lines = []
    for raw in body.splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def soft_wrap(text: str, width: int = SOFT_WRAP) -> str:
    """把超长行软换行到 width 字符/行。

    [STOP] 不是美化：读取工具对单行的上限约 2000 字符，
    段落本身过长同样会被截断（见 docs/阶段R全文获取模块.md 五之⑤）。
    """
    if width <= 0:
        return text
    out = []
    for line in (text or "").splitlines():
        while len(line) > width:
            out.append(line[:width])
            line = line[width:]
        out.append(line)
    return "\n".join(out)


def write_fulltext_txt(text: str, out_path: pathlib.Path, meta: dict | None = None) -> pathlib.Path:
    """落盘正文 .txt：头部写溯源元信息，正文做软换行。

    头部字段与 download_all_fulltexts.save_fulltext() 的约定保持一致
    （source / doi / chars / note），便于下游统一解析。
    """
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    m = dict(meta or {})
    header = [
        f"# source: {m.get('source', '')}",
        f"# doi: {m.get('doi', '')}",
    ]
    if m.get("pmcid"):
        header.append(f"# pmcid: {m['pmcid']}")
    if m.get("route"):
        header.append(f"# route: {m['route']}")
    header.append(f"# chars: {len(text or '')}")
    header.append(f"# note: {m.get('note', 'JATS 正文，非 PDF 提取')}")
    header.append("-" * 60)
    p.write_text("\n".join(header) + "\n" + soft_wrap(text or ""), encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════

# 夹具有意覆盖四种形态：
#   REAL      真全文（有 body、够长）
#   SHELL     非 OA 的 articleset 壳（HTTP 200 + 无 body）—— 本模块存在的理由
#   SHORT     有 body 但只是占位/摘要页
#   EMPTY     空 / 畸形
_FIXTURE_SHELL = (
    '<?xml version="1.0"?>\n'
    '<pmc-articleset>\n'
    '  <!--\n'
    '    The publisher of this article does not allow downloading of the full text\n'
    '    in XML format.\n'
    '  -->\n'
    '  <article><front><article-title>Fixture</article-title></front></article>\n'
    '</pmc-articleset>\n'
)
_FIXTURE_REAL = (
    '<article><front><article-title>Fixture</article-title></front>'
    '<body><sec><title>S1</title><p>' + ("word " * 2000) + '</p></sec>'
    '<sec><title>S2</title><p>' + ("more " * 2000) + '</p></sec></body></article>'
)
_FIXTURE_SHORT = '<article><body><sec><p>only a stub</p></sec></body></article>'


def self_test() -> int:
    bad = 0

    # ── 1. 判据四形态 ────────────────────────────────────────
    cases = [
        ("REAL", _FIXTURE_REAL, True, 2),
        ("SHELL", _FIXTURE_SHELL, False, 0),
        ("SHORT", _FIXTURE_SHORT, True, 1),
        ("EMPTY", "", False, 0),
    ]
    for name, xml, want_body, want_sec in cases:
        has, sec, chars = jats_body_stats(xml)
        if has != want_body or sec != want_sec:
            bad += 1
            print(f"  [X] jats_body_stats({name}) = ({has}, {sec}, {chars})，"
                  f"期望 (body={want_body}, sec={want_sec})")
    print(f"判据四形态：{len(cases) - bad}/{len(cases)} 通过")

    # ── 2. 「拿到 XML」不等于「拿到全文」必须被拦 ─────────────
    ok_shell, why_shell = is_real_fulltext(_FIXTURE_SHELL)
    if ok_shell:
        bad += 1
        print(f"  [X] articleset 壳被误判为全文（原因应为 no-body，实得 {why_shell}）")
    else:
        print(f"articleset 壳被正确拦下：{why_shell}")

    ok_real, why_real = is_real_fulltext(_FIXTURE_REAL)
    if not ok_real:
        bad += 1
        print(f"  [X] 真全文被误拦：{why_real}")
    else:
        print(f"真全文正确放行：{why_real}")

    ok_short, why_short = is_real_fulltext(_FIXTURE_SHORT)
    if ok_short:
        bad += 1
        print(f"  [X] 占位页被误判为全文（原因应为 body-too-short，实得 {why_short}）")
    else:
        print(f"占位页被正确拦下：{why_short}")

    # ── 3. 交叉验算：与 reviews_fulltext 的实现逐字比对 ────────
    try:
        import reviews_fulltext as _rf  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"交叉验算：跳过（reviews_fulltext 不可导入：{type(e).__name__}）")
    else:
        drift = 0
        for name, xml, _b, _s in cases:
            mine = jats_body_stats(xml)
            theirs = _rf.jats_body_stats(xml)
            if mine != theirs:
                drift += 1
                print(f"  [X] 与 reviews_fulltext 漂移 {name}：本模块={mine} 既有={theirs}")
        if drift:
            bad += 1
        else:
            print(f"交叉验算：{len(cases)}/{len(cases)} 与 reviews_fulltext 完全一致")
        if getattr(_rf, "MIN_FULLTEXT_CHARS", None) != MIN_FULLTEXT_CHARS:
            bad += 1
            print(f"  [X] MIN_FULLTEXT_CHARS 漂移：本模块={MIN_FULLTEXT_CHARS} "
                  f"既有={getattr(_rf, 'MIN_FULLTEXT_CHARS', None)}")
        else:
            print(f"阈值一致性：MIN_FULLTEXT_CHARS={MIN_FULLTEXT_CHARS} 两边相同")

    # ── 4. 软换行：最长行必须等于 width ──────────────────────
    wrapped = soft_wrap("x" * 5000 + "\nshort", width=1200)
    longest = max((len(l) for l in wrapped.splitlines()), default=0)
    if longest != 1200:
        bad += 1
        print(f"  [X] soft_wrap 最长行 = {longest}，期望 1200")
    else:
        print(f"软换行：最长行 = {longest}（= 读取工具安全上限内）")

    # ── 5. 落盘头部字段齐全 ─────────────────────────────────
    # [STOP] 不用 tempfile：受限沙箱下 %TEMP% 不可写（实测 PermissionError）。
    #   落点与全流程其余自检一致 —— 走 logutil.workdir()，跑完即删。
    import logutil  # noqa: PLC0415
    scratch = logutil.workdir() / ".work_cdp" / "selftest"
    try:
        p = write_fulltext_txt("hello body", scratch / "jats_fulltext_fixture.txt",
                               {"source": "u", "doi": "10.1/x", "pmcid": "PMC1", "route": "r"})
        head = p.read_text(encoding="utf-8").splitlines()[:6]
        joined = " ".join(head)
        for need in ("# source:", "# doi:", "# pmcid:", "# route:", "# chars:"):
            if need not in joined:
                bad += 1
                print(f"  [X] 落盘头部缺字段 {need}")
        print("落盘头部字段：通过")
    finally:
        try:
            (scratch / "jats_fulltext_fixture.txt").unlink(missing_ok=True)
            if scratch.is_dir() and not any(scratch.iterdir()):
                scratch.rmdir()
        except Exception:  # noqa: BLE001
            pass

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    path = None
    for i, a in enumerate(argv):
        if a == "--file" and i + 1 < len(argv):
            path = argv[i + 1]
        elif a.startswith("--file="):
            path = a.split("=", 1)[1]
    if not path:
        print(__doc__)
        return 2
    p = pathlib.Path(path)
    if not p.is_file():
        print(f"[X] 文件不存在：{p}")
        return 2
    xml = p.read_text(encoding="utf-8", errors="replace")
    ok, why = is_real_fulltext(xml)
    print(f"{p.name}: {'真全文' if ok else '不是全文'} — {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
