# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 全文获取：统一结果类型 + 内容分类（新增模块，供各 provider 共用）
#
# 为什么要有这个模块
#   即将接入的多条路线（出版商 API / OA 候选 / 浏览器落地页）必须
#   返回**同一种**结构，否则下游又要写三套分支判断。
#   对照报告 §1.6 的结论：状态语义是本流程相对 nature-downloader 的弱项，
#   所以这里把「成功」「用户交接」「最终失败」的区分**固化进类型**。
#
# [STOP] 与既有实现的边界（刻意不重复造轮子）
#   · PDF 页数/体积校验仍归 `store.validate_pdf` —— 本模块**不**做页数校验，
#     只在落盘后由调用方走 store 那条路（SKILL.md:1686 已列为硬约束）。
#   · JATS 判据归 `jats_fulltext.is_real_fulltext` —— 本模块只做**格式嗅探**，
#     不做「是否真全文」的判断。两者职责不同，不要混淆。
#   · 正文落盘命名归 `store.stem_for`；本模块只负责内容，不负责命名策略。
#
# 用法：
#   from fulltext_result import FullTextResult, classify_bytes, save_bytes
#   r = FullTextResult.failed("elsevier", "api_fulltext_unavailable", reason="403")
#
#   uv run python scripts/fulltext_result.py --self-test
#
# 退出码：0 = 通过；1 = 有失败项
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import pathlib
import re
import sys
from typing import Any


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


def _scripts_dir() -> pathlib.Path:
    """本模块所在目录（= 流程1/scripts）。其余模块同目录，导入前需保证它在 sys.path 上。"""
    here = pathlib.Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    return here


_scripts_dir()


# ══════════════════════════════════════════════════════════════
# 状态码：语义分类，而不是散落的字符串
#
# 三档语义（对照报告建议 P1-6）：
#   SUCCESS   —— 拿到了可用产物，下游可以直接用
#   HANDOFF   —— **不是**最终失败：需要用户或另一条路线接手
#   FAILURE   —— 本条路线走不通，且不会因为重试而变好
# ══════════════════════════════════════════════════════════════
STATUS_KIND: dict[str, str] = {
    # ── 成功 ──
    "downloaded": "SUCCESS",
    "downloaded_with_si": "SUCCESS",
    "open_access_downloaded": "SUCCESS",
    "full_text_html_available": "SUCCESS",
    "native_fulltext_downloaded": "SUCCESS",
    # ── 用户/路线交接（不是最终失败）──
    "carsi_waiting_user": "HANDOFF",
    "publisher_verification_waiting_user": "HANDOFF",
    "sciencedirect_robot_check": "HANDOFF",
    "verification_auto_failed": "HANDOFF",
    "retry_after_user_verification": "HANDOFF",
    "api_fallback_confirmation_required": "HANDOFF",
    "si_confirmation_required": "HANDOFF",
    # 有合法候选、但都不在直连白名单内 → 路线存在，只是「谁来取」的问题。
    #   与 `route_disabled`（FAILURE）的区别在于：后者是这条路本身不合法/没开，
    #   前者是主链路的 CDP 完全能接手。fulltext_router.py 用它产出交接单。
    "browser_handoff": "HANDOFF",
    # ── 凭据/授权问题（要用户动，但不是失败）──
    "credentials_missing": "HANDOFF",
    "credentials_invalid": "HANDOFF",
    "api_not_entitled": "HANDOFF",
    # ── 最终失败 ──
    "api_fulltext_unavailable": "FAILURE",
    "oa_not_found": "FAILURE",
    "oa_resolution_inconclusive": "FAILURE",
    "metadata_ambiguous": "FAILURE",
    "rate_limited": "FAILURE",
    "library_no_permission": "FAILURE",
    "no_full_text_link": "FAILURE",
    "no_authorized_pdf_found": "FAILURE",
    "pdf_fetch_failed": "FAILURE",
    "pdf_corrupt": "FAILURE",
    "pdf_too_short": "FAILURE",
    "not_real_fulltext": "FAILURE",
    "route_disabled": "FAILURE",
    # 取全文 CDP 模块未安装（可选模块被移除时的诚实状态）
    "module_absent": "FAILURE",
    "failed_after_retry": "FAILURE",
}

SUCCESS_STATUSES = frozenset(s for s, k in STATUS_KIND.items() if k == "SUCCESS")
HANDOFF_STATUSES = frozenset(s for s, k in STATUS_KIND.items() if k == "HANDOFF")
FAILURE_STATUSES = frozenset(s for s, k in STATUS_KIND.items() if k == "FAILURE")


def kind_of(status: str) -> str:
    """→ SUCCESS / HANDOFF / FAILURE。未知状态一律按 FAILURE（fail closed）。"""
    return STATUS_KIND.get(str(status or ""), "FAILURE")


def is_success(status: str) -> bool:
    return kind_of(status) == "SUCCESS"


def is_handoff(status: str) -> bool:
    return kind_of(status) == "HANDOFF"


# ══════════════════════════════════════════════════════════════
# 统一结果
# ══════════════════════════════════════════════════════════════
@dataclasses.dataclass
class FullTextResult:
    status: str
    provider: str = ""
    access_mode: str = ""              # publisher_api / open_access / institution_browser
    fmt: str = ""                      # pdf / jats_xml / html / caj
    file: str = ""
    bytes: int = 0
    sha256: str = ""
    http_status: int = 0
    final_url: str = ""
    reason: str = ""
    # 逐次尝试的留痕（source / url / status / reason）
    attempts: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    # 额外字段：OA 证据、回落历史等。**严禁**放秘钥。
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    # ── 构造快捷方式 ────────────────────────────────────────
    @classmethod
    def failed(cls, provider: str, status: str, **kw: Any) -> "FullTextResult":
        return cls(status=status, provider=provider, **kw)

    @classmethod
    def handoff(cls, provider: str, status: str, **kw: Any) -> "FullTextResult":
        return cls(status=status, provider=provider, **kw)

    # ── 语义查询 ────────────────────────────────────────────
    @property
    def kind(self) -> str:
        return kind_of(self.status)

    @property
    def ok(self) -> bool:
        return self.kind == "SUCCESS"

    @property
    def is_final_failure(self) -> bool:
        return self.kind == "FAILURE"

    @property
    def is_handoff(self) -> bool:
        """需要用户或另一条路线接手 —— **不是**最终失败。"""
        return self.kind == "HANDOFF"

    # ── 序列化（脱敏）────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["kind"] = self.kind
        # 单一出口即脱敏：任何 URL 里的秘钥参数都不允许外流
        d["final_url"] = redact_url(self.final_url)
        d["attempts"] = [
            {**a, "url": redact_url(a.get("url", ""))} for a in (self.attempts or [])
        ]
        return d

    def __str__(self) -> str:
        bits = [f"{self.provider or '?'}:{self.status}"]
        if self.fmt:
            bits.append(self.fmt)
        if self.bytes:
            bits.append(f"{self.bytes}B")
        if self.reason:
            bits.append(self.reason[:80])
        return " | ".join(bits)

    # 兼容 dict 风格的取值，便于与既有代码（返回 dict）对接
    def get(self, key: str, default: Any = None) -> Any:
        d = self.to_dict()
        return d.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


# ══════════════════════════════════════════════════════════════
# 秘钥脱敏（URL 查询参数）
# ══════════════════════════════════════════════════════════════
SECRET_QUERY_RE = re.compile(
    r"^(?:api[_-]?key|apikey|secret|password|authtoken|insttoken|"
    r"access[_-]?token|authorization|token|key)$",
    re.I,
)


def has_secret_in_url(url: str) -> bool:
    """URL 的查询串里是否带秘钥参数。

    [STOP] 不要用 `url != redact_url(url)` 来判断 —— 那会把
    「百分号编码被重写」也误报成「带秘钥」（实测：`api.elsevier.com` 的
    路径里 `10.1016/...` 会被编码成 `%2F`，从而误报）。
    """
    text = str(url or "")
    if "?" not in text:
        return False
    try:
        import urllib.parse as _up  # noqa: PLC0415

        q = _up.urlsplit(text).query
        if not q:
            return False
        return any(SECRET_QUERY_RE.match(k) for k, _ in _up.parse_qsl(q, keep_blank_values=True))
    except Exception:  # noqa: BLE001
        # 解析失败时保守判定为「可能带秘钥」——宁可多脱敏一次
        return bool(re.search(r"(?i)(?:api[_-]?key|apikey|token|secret)=", text))


def redact_url(url: str) -> str:
    """剥掉 URL 查询串里的秘钥参数。解析失败时返回原串（不抛异常）。

    无秘钥参数时**原样返回** —— 不改写百分号编码，避免把
    「编码差异」误报成「带秘钥」（见 `has_secret_in_url` 的说明）。
    """
    text = str(url or "")
    if not text or "?" not in text:
        return text
    try:
        import urllib.parse as _up  # noqa: PLC0415

        parts = _up.urlsplit(text)
        if not parts.query:
            return text
        pairs = _up.parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if not SECRET_QUERY_RE.match(k)]
        if len(kept) == len(pairs):
            return text
        return _up.urlunsplit(parts._replace(query=_up.urlencode(kept)))
    except Exception:  # noqa: BLE001
        # [STOP] 解析失败宁可返回原文也不要崩：调用方拿到的是「未脱敏」，
        #   但本函数**只用于日志/报告**，不用于写盘决策。
        return text


def redact_headers(headers: dict) -> dict:
    """请求头脱敏：只保留键名与「已设置」标记。"""
    out = {}
    for k in headers or {}:
        if SECRET_QUERY_RE.match(str(k)):
            out[str(k)] = "[REDACTED]"
        elif re.search(r"key|token|authorization|secret", str(k), re.I):
            out[str(k)] = "[REDACTED]"
        else:
            out[str(k)] = headers[k]
    return out


# ══════════════════════════════════════════════════════════════
# 内容分类（只做格式嗅探，不做「是否真全文」）
# ══════════════════════════════════════════════════════════════
LOGIN_RE = re.compile(
    r"\b(?:log[ -]?in|sign[ -]?in|password|authentication required|"
    r"access denied|forbidden)\b|登录|统一身份认证|无权访问|未订购|未购买",
    re.I,
)

# 各格式落盘时的扩展名与默认目录（与既有交付目录约定一致）
FORMAT_EXT = {"pdf": ".pdf", "caj": ".caj", "jats_xml": ".xml", "html": ".html", "text": ".txt"}
FORMAT_DIR = {"pdf": "PDFs", "caj": "CNKI", "jats_xml": "FullText", "html": "FullText", "text": "FullText"}


def classify_bytes(content_type: str, head: bytes) -> tuple[bool, str, str]:
    """→ (是否可用格式, 格式名, 原因)。

    [STOP] 与 `provider-utils.mjs` 的 `classifyFullTextContent` 语义对齐，
    但**不**承担「是否真全文」；JATS 是否为真全文请再走
    `jats_fulltext.is_real_fulltext()`。
    """
    b = bytes(head or b"")
    if b[:5] == b"%PDF-":
        return True, "pdf", ""
    if re.match(rb"^(?:CAJ|HN|KDH)", b[:12]):
        return True, "caj", ""
    ctype = str(content_type or "").lower()
    text = b.decode("utf-8", "replace").lstrip()
    if "html" in ctype or re.match(r"(?is)^\s*(?:<!doctype html|<html|<head)", text):
        if LOGIN_RE.search(text) or LOGIN_RE.search(b.decode("utf-8", "replace")):
            return False, "html", "login_or_error_page"
        if re.search(r"(?is)<article[\s>]|full[- ]text|article-body|fulltext", text):
            return True, "html", ""
        return False, "html", "not_fulltext_html"
    if "xml" in ctype or "jats" in ctype or re.match(r"(?is)^\s*(?:<\?xml|<article[\s>])", text):
        if re.search(r"(?is)<article[\s>]|<book-part-wrapper[\s>]", text):
            return True, "jats_xml", ""
        return False, "xml", "not_jats_fulltext"
    if text:
        return False, "unknown", "unsupported_or_invalid_content"
    return False, "unknown", "empty_body"


# ══════════════════════════════════════════════════════════════
# 落盘（原子写 + SHA-256）
# ══════════════════════════════════════════════════════════════
def save_bytes(raw: bytes, out_path: pathlib.Path) -> dict:
    """原子落盘：写 `.part-<pid>` → rename。返回 {file, bytes, sha256}。

    为什么必须原子：写一半被中断会留下一个体积达标的坏文件，
    而后续的「已存在即跳过」逻辑会把它当成下载成功**永久保留**。
    """
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(f"{p.name}.part-{os.getpid()}")
    try:
        temp.write_bytes(raw)
        os.replace(temp, p)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "file": str(p),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def store_text_as_fulltext(text: str, out_txt: pathlib.Path, meta: dict | None = None) -> dict:
    """把抽取好的正文按既有约定落盘 `.txt`（软换行 + 头部溯源）。

    复用 `jats_fulltext.write_fulltext_txt`，保证与阶段 R 的 `.txt` 同格式。
    """
    import jats_fulltext  # noqa: PLC0415

    p = jats_fulltext.write_fulltext_txt(text, pathlib.Path(out_txt), meta or {})
    raw = p.read_bytes()
    return {
        "file": str(p),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


# ══════════════════════════════════════════════════════════════
# 自检（离线）
# ══════════════════════════════════════════════════════════════
_PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 200
_JATS = (b'<?xml version="1.0"?><article><front><article-title>T</article-title>'
         b'</front><body><sec><p>' + b"word " * 1500 + b'</p></sec></body></article>')
_HTML_OK = b'<!doctype html><html><head><title>T</title></head><body><article><p>' + b"x" * 300 + b"</p></article></body></html>"
_HTML_LOGIN = b'<!doctype html><html><body><form>Please log in to continue</form></body></html>'
_HTML_NAV = b'<!doctype html><html><body><div id="nav">menu only</div></body></html>'


def self_test() -> int:
    bad = 0

    # ── 1. 状态语义三分 ─────────────────────────────────────
    if kind_of("downloaded") != "SUCCESS" or kind_of("open_access_downloaded") != "SUCCESS":
        bad += 1
        print("  [X] 成功状态分类错误")
    else:
        print("状态分类：成功档命中")
    # 这条是本模块存在的核心理由 —— 用户交接不是失败
    handoffs = ("carsi_waiting_user", "publisher_verification_waiting_user",
                "verification_auto_failed", "api_fallback_confirmation_required")
    wrong = [s for s in handoffs if not is_handoff(s)]
    if wrong:
        bad += 1
        print(f"  [X] 以下状态应为 HANDOFF（不是最终失败）：{wrong}")
    else:
        print("状态分类：用户交接档与失败档分离（carsi_* 等不再等同失败）")
    # 未知状态必须 fail closed
    if kind_of("something_new") != "FAILURE":
        bad += 1
        print("  [X] 未知状态未按 FAILURE 处理（fail closed 失效）")
    else:
        print("未知状态：按 FAILURE 处理（fail closed）")

    # ── 2. 内容分类五形态 ───────────────────────────────────
    cases = [
        ("PDF", "application/pdf", _PDF_HEAD, True, "pdf", ""),
        ("JATS", "application/xml", _JATS, True, "jats_xml", ""),
        ("HTML 全文", "text/html", _HTML_OK, True, "html", ""),
        ("HTML 登录页", "text/html", _HTML_LOGIN, False, "html", "login_or_error_page"),
        ("HTML 导航页", "text/html", _HTML_NAV, False, "html", "not_fulltext_html"),
        ("空体", "", b"", False, "unknown", "empty_body"),
    ]
    for name, ct, head, want_ok, want_fmt, want_why in cases:
        ok, fmt, why = classify_bytes(ct, head)
        if ok != want_ok or fmt != want_fmt or (want_why and why != want_why):
            bad += 1
            print(f"  [X] classify_bytes({name}) = ({ok}, {fmt}, {why})，"
                  f"期望 ({want_ok}, {want_fmt}, {want_why or '(任意)'})")
    print(f"内容分类：{len(cases)} 形态已验")

    # ── 3. 格式嗅探 ≠ 真全文判据（职责边界必须可验证）─────────
    ok, fmt, _ = classify_bytes("application/xml", _JATS)
    import jats_fulltext  # noqa: PLC0415
    real, why = jats_fulltext.is_real_fulltext(_JATS.decode())
    if not (ok and real):
        bad += 1
        print(f"  [X] 真 JATS 夹具应同时通过嗅探与判据：嗅探={ok} 判据={real}({why})")
    else:
        print("职责边界：格式嗅探与「是否真全文」判据各司其职，真全文两边都过")
    # articleset 壳：嗅探可能过，判据必须拦
    shell = ('<?xml version="1.0"?><pmc-articleset><article><front>'
             '<article-title>T</article-title></front></article></pmc-articleset>')
    s_ok, s_fmt, _ = classify_bytes("application/xml", shell.encode())
    s_real, s_why = jats_fulltext.is_real_fulltext(shell)
    if s_real:
        bad += 1
        print("  [X] articleset 壳被真全文判据放行")
    else:
        print(f"职责边界：壳的嗅探={s_ok}/{s_fmt}，判据正确拦下（{s_why}）")

    # ── 4. 秘钥脱敏 ─────────────────────────────────────────
    dirty = "https://api.elsevier.com/content/article/doi/10.1/x?api_key=SECRET123&httpAccept=pdf"
    clean = redact_url(dirty)
    if "SECRET123" in clean:
        bad += 1
        print(f"  [X] api_key 未被剥掉：{clean}")
    elif "httpAccept=pdf" not in clean:
        bad += 1
        print(f"  [X] 脱敏误删了非秘钥参数：{clean}")
    else:
        print("URL 脱敏：秘钥参数剥除，非秘钥参数保留")

    h = redact_headers({"X-ELS-APIKey": "SECRET", "Accept": "application/pdf"})
    if "SECRET" in str(h) or h.get("X-ELS-APIKey") != "[REDACTED]" or h.get("Accept") != "application/pdf":
        bad += 1
        print(f"  [X] 请求头脱敏错误：{h}")
    else:
        print("请求头脱敏：秘钥类键值被遮蔽，普通头保留")

    # 回归防线：百分号编码**不得**被误报成「带秘钥」。
    #   实测踩过：api.elsevier.com 的路径含 `10.1016/...`，编码成 %2F 后
    #   与原文不等，于是 `url != redact_url(url)` 这种写法会误报。
    enc_case = ("https://api.elsevier.com/content/article/doi/10.1016%2Fj.cell.2014.03.011"
                "?httpAccept=application%2Fpdf")
    if has_secret_in_url(enc_case):
        bad += 1
        print(f"  [X] 百分号编码被误判为带秘钥：{enc_case}")
    elif redact_url(enc_case) != enc_case:
        bad += 1
        print(f"  [X] 无秘钥时 redact_url 不该改写 URL：{redact_url(enc_case)}")
    else:
        print("编码回归：无秘钥参数的 URL 既不被误报、也不被改写")
    if not has_secret_in_url(dirty):
        bad += 1
        print("  [X] 真带 api_key 的 URL 反而没被判出来")
    else:
        print("编码回归：真带秘钥的 URL 正确判出")

    # to_dict 是唯一出口 —— 秘钥不能从这里漏出去
    r = FullTextResult(status="api_fulltext_unavailable", provider="elsevier",
                       final_url=dirty, attempts=[{"url": dirty, "status": "http-403"}])
    if "SECRET123" in str(r.to_dict()):
        bad += 1
        print("  [X] FullTextResult.to_dict() 泄露了 URL 中的秘钥")
    else:
        print("结果脱敏：to_dict() 出口已剥掉秘钥")

    # ── 5. 原子落盘 + SHA-256 ───────────────────────────────
    import logutil  # noqa: PLC0415
    scratch = logutil.workdir() / ".work_cdp" / "selftest"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        info = save_bytes(_PDF_HEAD, scratch / "fulltext_result_fixture.pdf")
        want_sha = hashlib.sha256(_PDF_HEAD).hexdigest()
        if info["bytes"] != len(_PDF_HEAD) or info["sha256"] != want_sha:
            bad += 1
            print(f"  [X] 落盘信息错误：{info}")
        else:
            print(f"原子落盘：{info['bytes']}B sha256={info['sha256'][:12]}... 与源一致")
        # 不得留下 .part-* 残片
        leftovers = list(scratch.glob("*.part-*"))
        if leftovers:
            bad += 1
            print(f"  [X] 落盘后残留临时文件：{leftovers}")
        else:
            print("原子落盘：无 .part-* 残留")

        # ── 6. 正文 .txt 与阶段 R 格式一致 ───────────────────
        t = store_text_as_fulltext("line1\n" + "y" * 3000, scratch / "fulltext_result_fixture.txt",
                                   {"source": "https://example.org/x", "doi": "10.1/x"})
        txt = pathlib.Path(t["file"]).read_text(encoding="utf-8")
        longest = max((len(l) for l in txt.splitlines()), default=0)
        if longest > jats_fulltext.SOFT_WRAP:
            bad += 1
            print(f"  [X] .txt 最长行 {longest} 超过 SOFT_WRAP={jats_fulltext.SOFT_WRAP}")
        elif "# source:" not in txt or "# doi:" not in txt:
            bad += 1
            print("  [X] .txt 缺少溯源头部")
        else:
            print(f"正文 .txt：最长行 {longest}，溯源头部齐全（与阶段 R 同格式）")
    finally:
        try:
            for f in scratch.glob("fulltext_result_fixture*"):
                f.unlink()
            if scratch.is_dir() and not any(scratch.iterdir()):
                scratch.rmdir()
        except Exception:  # noqa: BLE001
            pass

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    if "--statuses" in sys.argv[1:]:
        for s in sorted(STATUS_KIND):
            print(f"{STATUS_KIND[s]:<8} {s}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
