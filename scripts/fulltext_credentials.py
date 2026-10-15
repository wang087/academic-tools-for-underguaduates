# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 出版商 API 凭据读取（只读，不代持）
#
# 立场声明（[STOP] 必须与 SKILL.md:1690-1697 的硬约束一致）
#   1) 凭据**由用户提供**，本流程**不代填、不代登录、不代持**。
#      因此本模块**只读**：它读凭据文件，不创建、不改写、不删除。
#      需要写入时请在**外部 skill**（nature-downloader，**未并入本流程**）里用它的
#      `configure_credentials.py set <provider>` —— 该 skill 已实现
#      0600 权限与原子写，重复造一份只会多一条泄露面。
#      （也可直接设环境变量：`PAPER_EMAIL` / `LIT_DL_CONFIG_DIR` 等，本模块同样读得到）
#   2) 秘钥**绝不**进入日志、journal、manifest、交付物。
#      对外只暴露 `mask()` 后的末四位。
#   3) 文件不存在 = 没有凭据 = 该路线不可用。**不报错、不索要、不猜测**。
#
# 为什么直接复用 nature-downloader 的凭据文件（而不是新建一份）
#   · 备选链路（SKILL.md:1627-1702）本来就是调 nature-downloader；
#     用户为它配好的 key，主链路若能直接读，就不必配两遍。
#   · 读它**不放宽任何边界**：文件本就是用户自己放 key 的地方，
#     权限 0600，且我们不写、不回显明文。
#   · 若用户愿意让本流程有独立凭据文件，可用 `LIT_FLOW1_CREDENTIALS`
#     覆盖路径（同样只读）。
#
# 兼容的路径与格式（与 nature-downloader/scripts/lib/credentials.mjs 一致）
#   路径：$LIT_DL_CONFIG_DIR/credentials.json 或 ~/.config/lit-dl/credentials.json
#   格式：{ "<provider>": { "api_key": "...", "insttoken": "...", ... }, ... }
#   联系邮箱：同目录 settings.json 的 open_access.contact_email
#
# 用法：
#   from fulltext_credentials import provider_credentials, usable_providers, credentials_report
#   creds = provider_credentials("elsevier")        # → dict 或 None
#   print(credentials_report())                     # → 已脱敏的可打印视图
#
#   uv run python scripts/fulltext_credentials.py --show
#   uv run python scripts/fulltext_credentials.py --self-test
#
# 退出码：0 = 正常；1 = 自检失败；2 = 用法错误
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import stat
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
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


_fix_streams()


# ══════════════════════════════════════════════════════════════
# 出版商清单（与 nature-downloader/data/publishers.json 对齐）
#
# 只声明**能力**，不含任何密钥。`credential_fields` 决定
# `is_usable()` 的判定：IEEE 光有 api_key 不算可用 ——
# 它的 Metadata API 不是收费全文权限，必须有 issued Full-Text endpoint。
# ══════════════════════════════════════════════════════════════
PROVIDERS: dict[str, dict] = {
    "elsevier": {
        "name": "Elsevier / ScienceDirect",
        "doi_pattern": r"^10\.(?:1016|1053|1067|1072|1097|1205|1383|1532|1588|1600|1618|2016|3168)/",
        "credential_fields": ("api_key", "insttoken", "authtoken"),
        "config_url": "https://dev.elsevier.com/",
    },
    "springer_nature": {
        "name": "Springer Nature",
        "doi_pattern": r"^10\.(?:1007|1023|1038|1057|1134|1186|1365)/",
        "credential_fields": ("api_key",),
        "config_url": "https://dev.springernature.com/docs/quick-start/api-access/",
    },
    "ieee": {
        "name": "IEEE Xplore",
        "doi_pattern": r"^10\.1109/",
        "credential_fields": ("api_key", "fulltext_endpoint"),
        "config_url": "https://developer.ieee.org/member/register",
    },
}


# ══════════════════════════════════════════════════════════════
# 路径解析
# ══════════════════════════════════════════════════════════════
def config_dir() -> pathlib.Path:
    """凭据目录：`LIT_DL_CONFIG_DIR` 优先，否则 `~/.config/lit-dl`。"""
    env = (os.environ.get("LIT_DL_CONFIG_DIR") or "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return pathlib.Path.home() / ".config" / "lit-dl"


def credentials_path() -> pathlib.Path:
    """凭据文件路径：`LIT_FLOW1_CREDENTIALS` 优先，否则 `config_dir()/credentials.json`。"""
    override = (os.environ.get("LIT_FLOW1_CREDENTIALS") or "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    return config_dir() / "credentials.json"


def settings_path() -> pathlib.Path:
    """普通配置（非秘密）：联系方式等。"""
    return config_dir() / "settings.json"


# ══════════════════════════════════════════════════════════════
# 读取（只读）
# ══════════════════════════════════════════════════════════════
def _read_json(p: pathlib.Path) -> dict:
    try:
        if not p.is_file():
            return {}
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        # 解析失败一律当「没有」——绝不让半个损坏的文件把流程带偏
        return {}


def load_credentials() -> dict:
    """全部凭据（**明文**，仅限本进程内部使用，禁止外传/打印）。"""
    return _read_json(credentials_path())


def provider_credentials(provider: str) -> dict | None:
    """某出版商的凭据；不存在返回 None（不抛异常）。"""
    v = load_credentials().get(provider)
    return v if isinstance(v, dict) and v else None


def is_usable(provider: str, creds: dict | None = None) -> tuple[bool, str]:
    """→ (是否可用, 原因)。判据与 nature-downloader 的 `hasUsablePublisherCredentials` 一致。"""
    if provider not in PROVIDERS:
        return False, f"未知出版商：{provider}"
    c = creds if creds is not None else provider_credentials(provider)
    if not c:
        return False, "未配置凭据"
    if not str(c.get("api_key") or "").strip():
        return False, "缺少 api_key"
    if provider == "ieee" and not str(c.get("fulltext_endpoint") or "").strip():
        # [STOP] IEEE Metadata API 不是收费全文权限 —— 这条必须显式拒绝，
        #   否则会把「查得到元数据」误当「下得到全文」。
        return False, "IEEE 需要 issued Full-Text endpoint 模板（Metadata key 不是全文权限）"
    return True, "可用"


def usable_providers() -> list[str]:
    """当前真正可用的出版商列表（按 PROVIDERS 声明顺序）。"""
    return [p for p in PROVIDERS if is_usable(p)[0]]


def contact_email() -> str:
    """Unpaywall / Crossref 的 polite-pool 联系邮箱。

    优先级：环境变量 `PAPER_EMAIL` → `UNPAYWALL_EMAIL` → settings.json。
    读不到就返回空串（调用方必须把空串当作「不能查 Unpaywall」，
    而不是用一个占位邮箱 —— 占位邮箱对 Unpaywall 等于没有）。
    """
    for key in ("PAPER_EMAIL", "UNPAYWALL_EMAIL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    s = _read_json(settings_path())
    return str(((s.get("open_access") or {}).get("contact_email")) or "").strip()


# ══════════════════════════════════════════════════════════════
# 脱敏展示
# ══════════════════════════════════════════════════════════════
def mask(value: str) -> str:
    """只留末四位；长度 <= 4 全遮蔽。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _file_mode_note(p: pathlib.Path) -> str:
    """文件权限提示。权限过宽只是**提示**，不阻断（不同平台语义不同）。"""
    try:
        mode = p.stat().st_mode
    except Exception:  # noqa: BLE001
        return ""
    if os.name == "nt":
        return "Windows 不提供 POSIX 权限位，请自行确认该文件未被同步到云端"
    bits = stat.S_IMODE(mode)
    if bits & 0o077:
        return f"[WARN] 权限过宽：{oct(bits)}（建议 0600）"
    return f"权限 {oct(bits)}"


def credentials_report() -> dict:
    """可安全打印/落盘的凭据视图：只有掩码与布尔。"""
    path = credentials_path()
    allc = load_credentials()
    out = {
        "credentials_path": str(path),
        "exists": path.is_file(),
        "mode_note": _file_mode_note(path) if path.is_file() else "",
        "contact_email_set": bool(contact_email()),
        "providers": {},
    }
    for p in PROVIDERS:
        c = allc.get(p) if isinstance(allc.get(p), dict) else None
        ok, why = is_usable(p, c)
        entry = {
            "name": PROVIDERS[p]["name"],
            "usable": ok,
            "reason": why,
            "configured_fields": sorted(c.keys()) if c else [],
        }
        if c:
            entry["masked"] = {k: mask(v) for k, v in c.items() if isinstance(v, str)}
        out["providers"][p] = entry
    return out


# ══════════════════════════════════════════════════════════════
# 自检（离线；用临时 LIT_DL_CONFIG_DIR，绝不碰用户真实凭据）
# ══════════════════════════════════════════════════════════════
def self_test() -> int:
    bad = 0
    import logutil  # noqa: PLC0415

    # [STOP] 不用 tempfile：受限沙箱下 %TEMP% 不可写。
    scratch = logutil.workdir() / ".work_cdp" / "selftest" / "creds"
    scratch.mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("LIT_DL_CONFIG_DIR", "LIT_FLOW1_CREDENTIALS",
                                            "PAPER_EMAIL", "UNPAYWALL_EMAIL")}
    try:
        os.environ["LIT_DL_CONFIG_DIR"] = str(scratch)
        os.environ.pop("LIT_FLOW1_CREDENTIALS", None)
        os.environ.pop("PAPER_EMAIL", None)
        os.environ.pop("UNPAYWALL_EMAIL", None)

        # 1. 文件不存在 → 一律「不可用」，且不抛异常
        if credentials_path().exists():
            credentials_path().unlink()
        if usable_providers():
            bad += 1
            print(f"  [X] 无凭据文件时可用列表应为空，实得 {usable_providers()}")
        else:
            print("空配置：可用列表为空（fail closed）")

        # 2. 只配 elsevier → 只有它可用
        (scratch / "credentials.json").write_text(json.dumps({
            "elsevier": {"api_key": "AKIAexample1234", "insttoken": "tok-9876"}
        }), encoding="utf-8")
        got = usable_providers()
        if got != ["elsevier"]:
            bad += 1
            print(f"  [X] 只配 elsevier 时可用列表 = {got}，期望 ['elsevier']")
        else:
            print("单出版商：可用列表 = ['elsevier']")

        # 3. IEEE 只有 api_key → **不可用**（Metadata key 不是全文权限）
        (scratch / "credentials.json").write_text(json.dumps({
            "ieee": {"api_key": "ieee-only-key"}
        }), encoding="utf-8")
        ok, why = is_usable("ieee")
        if ok:
            bad += 1
            print("  [X] IEEE 仅 api_key 被误判为可用（会把元数据权限当全文权限）")
        else:
            print(f"IEEE 门槛：仅 api_key → 不可用（{why}）")

        # 4. IEEE 补齐 endpoint 模板 → 可用
        (scratch / "credentials.json").write_text(json.dumps({
            "ieee": {"api_key": "k", "fulltext_endpoint": "https://x/{doi}"}
        }), encoding="utf-8")
        ok, why = is_usable("ieee")
        if not ok:
            bad += 1
            print(f"  [X] IEEE 补齐 endpoint 后仍不可用：{why}")
        else:
            print("IEEE 门槛：补齐 endpoint 模板 → 可用")

        # 5. 脱敏：明文绝不能出现在 report 里
        secret = "SUPERSECRETKEYVALUE"
        (scratch / "credentials.json").write_text(json.dumps({
            "springer_nature": {"api_key": secret}
        }), encoding="utf-8")
        rep = credentials_report()
        blob = json.dumps(rep, ensure_ascii=False)
        if secret in blob:
            bad += 1
            print("  [X] 明文秘钥泄露进了 credentials_report()")
        else:
            print(f"脱敏：明文未出现在报告中，掩码 = ...{secret[-4:]}")
        if rep["providers"]["springer_nature"]["masked"]["api_key"] != "*" * (len(secret) - 4) + secret[-4:]:
            bad += 1
            print("  [X] 掩码格式不符合「末四位」约定")
        else:
            print("掩码格式：末四位保留，其余替换")

        # 6. 短秘钥全遮蔽
        if mask("abc") != "***" or mask("") != "":
            bad += 1
            print(f"  [X] mask 边界错误：mask('abc')={mask('abc')!r} mask('')={mask('')!r}")
        else:
            print("短秘钥：长度 <= 4 全遮蔽")

        # 7. 损坏的 JSON 当「没有」，不抛异常
        (scratch / "credentials.json").write_text("{ this is not json", encoding="utf-8")
        try:
            if usable_providers():
                bad += 1
                print("  [X] 损坏 JSON 时可用列表应仍为空")
            else:
                print("损坏 JSON：按「无凭据」处理，不抛异常")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  [X] 损坏 JSON 抛出异常：{type(e).__name__}: {e}")

        # 8. 联系邮箱：环境变量优先于 settings.json
        (scratch / "settings.json").write_text(json.dumps({
            "open_access": {"contact_email": "from-settings@example.org"}
        }), encoding="utf-8")
        if contact_email() != "from-settings@example.org":
            bad += 1
            print(f"  [X] 邮箱未从 settings.json 读到：{contact_email()!r}")
        else:
            print("邮箱来源：settings.json 命中")
        os.environ["PAPER_EMAIL"] = "from-env@example.org"
        if contact_email() != "from-env@example.org":
            bad += 1
            print(f"  [X] PAPER_EMAIL 未优先：{contact_email()!r}")
        else:
            print("邮箱优先级：PAPER_EMAIL > settings.json")

        # 9. 只读性：本模块**不得**创建或改写凭据文件
        before = (scratch / "credentials.json").read_bytes()
        provider_credentials("elsevier")
        credentials_report()
        contact_email()
        after = (scratch / "credentials.json").read_bytes()
        if before != after:
            bad += 1
            print("  [X] 本模块改写了凭据文件（应严格只读）")
        else:
            print("只读性：读取路径未改动凭据文件一个字节")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            for f in scratch.iterdir():
                f.unlink()
            scratch.rmdir()
        except Exception:  # noqa: BLE001
            pass

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--show" in argv:
        print(json.dumps(credentials_report(), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
