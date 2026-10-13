# -*- coding: utf-8 -*-
"""# 取全文 CDP 模块的**唯一探测入口**（核心只认这一个文件）

## 为什么要有它

「用浏览器访问出版商站点取回正文」这条路径，为符合出版商的网站条款
（Elsevier 等明确禁止 robot/crawler/自动化下载）**被单独抽成一个可整目录删除的模块**：
`<流程1>/optional-cdp-fulltext/`。核心脚本**不直接 import 那个目录里的任何东西** ——
它们只问本文件三个问题：模块在不在、脚本在哪、能不能 import。

## 运行时行为（用户指定）

```
模块在   → 走正常 CDP 路线（多实例批量下载、出版商 API、浏览器另存 PDF）
模块不在 → **静默改走备用链路**（nature-skill 的获取模块 + 白名单直连 + 元数据 API）
```

"静默"指的是**不报错、不中断**，不是"不留痕"：缺模块这件事会写进日志/结果里的
`reason`，报告与交付索引里照实申报（本流程的规矩：能力缺失必须可回查）。

## 用法

```python
import cdp_module as cdpmod
cdpmod.available()                      # → bool
cdpmod.script("download_all_fulltexts.py")   # → Path | None（模块缺失时为 None）
fpb = cdpmod.module_import("fulltext_publishers")   # → module | None
```

命令行（SKILL.md 里的「探测引用入口」就是这条）：

    python 流程1/scripts/cdp_module.py            # 打印状态
    python 流程1/scripts/cdp_module.py --self-test
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import os
import pathlib
import sys

MODULE_DIRNAME = "optional-cdp-fulltext"
# 判定"模块在不在"的锚点文件：缺它就等于模块没装配好（不是"部分可用"）
ANCHOR = "download_all_fulltexts.py"
# 该模块提供的脚本（核心侧只通过这些名字取路径）
# 只列**本模块提供的**（出版商正文自动化）；浏览器底层与实例池属核心，不在其列：
#   cdp.py / cdp_pool.py / detect.py / start_edge_cdp.sh / renew_cdp_instance.sh
PROVIDED = ("download_all_fulltexts.py", "fulltext_publishers.py",
            "save_pdf_via_browser.py", "cdp_up_nosandbox.py",
            "check_challenge.py", "check_access.py")
# 备用链路：模块缺失时改走这里（nature-skill 的获取模块）
FALLBACK = {
    "route": "nature-downloader（外部 skill，未并入本流程）",
    "how": "python 流程1/scripts/preflight_fallback.py 查各备选路线；"
           "白名单直连与元数据 API 路线不受影响",
}


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        st = getattr(sys, name, None)
        if st is not None and hasattr(st, "buffer"):
            try:
                setattr(sys, name, io.TextIOWrapper(st.buffer, encoding="utf-8",
                                                    errors="replace", line_buffering=True))
            except Exception:
                pass


_fix_streams()

FLOW_ROOT = pathlib.Path(__file__).resolve().parents[1]


def module_dir() -> pathlib.Path:
    """→ `<流程1>/optional-cdp-fulltext/`（**不管存不存在**都返回路径）。"""
    return FLOW_ROOT / MODULE_DIRNAME


def available() -> bool:
    """模块是否可用 = 目录在 **且** 锚点脚本在。"""
    return (module_dir() / ANCHOR).is_file()


def script(name: str) -> pathlib.Path | None:
    """→ 模块内某个脚本/资源的绝对路径；模块缺失或该文件不在 → None。

    核心侧**不要**自己拼 `optional-cdp-fulltext/xxx` 这种路径 —— 一律走这里，
    这样"移除模块"只需删目录，不用改任何调用点。
    """
    p = module_dir() / name
    if not available():
        return None
    return p if p.exists() else None


def syspath() -> bool:
    """把模块目录加进 `sys.path`（供运行时 `import`）；模块缺失 → False。"""
    d = str(module_dir())
    if not available():
        return False
    if d not in sys.path:
        sys.path.insert(0, d)
    return True


def module_import(name: str):
    """**可选 import**：模块缺失或导入出错 → None（不抛）。

    为什么是 None 而不是抛异常：调用点都要做"缺模块 → 走备用链路"的分支，
    抛异常会逼着每个调用点写 try/except，容易漏。
    """
    if name in sys.modules:
        return sys.modules[name]
    if not syspath():
        return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def describe() -> str:
    """一行状态，供日志/报告/自检打印。"""
    if available():
        return f"CDP 取全文模块：可用（{module_dir()}）"
    return (f"CDP 取全文模块：**未安装**（缺 {module_dir()}）"
            f" → 取全文改走备用链路：{FALLBACK['route']}")


def self_test() -> int:
    bad = 0
    d = module_dir()
    print(f"模块目录：{d}")
    print(f"存在性：{d.is_dir()}｜可用性：{available()}")
    if available() != (d / ANCHOR).is_file():
        bad += 1
        print("  [X] available() 与锚点文件存在性不一致")
    miss = [n for n in PROVIDED if available() and not (d / n).exists()]
    if miss:
        bad += 1
        print(f"  [X] available()=True 但缺这些文件：{miss}")
    if available():
        if script(ANCHOR) is None:
            bad += 1
            print("  [X] script(锚点) 返回 None，但模块可用")
        if not syspath() or str(d) not in sys.path:
            bad += 1
            print("  [X] syspath() 未生效")
        print(f"  [OK] {describe()}")
    else:
        # 缺模块时必须"诚实降级"：script() 全 None、syspath() False
        if script(ANCHOR) is not None or syspath():
            bad += 1
            print("  [X] 模块缺失时 script()/syspath() 未降级")
        print(f"  [OK] 缺模块降级口径正确 → {FALLBACK['route']}")
    print("\n[OK] 自检全部通过" if bad == 0 else "\n[X] 自检有失败项")
    return 0 if bad == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--path" in sys.argv:
        # 给 shell 用：模块在 → 打印模块目录（供拼脚本路径）；不在 → 打印空行
        print(str(module_dir()) if available() else "")
        return 0
    if "--status" in sys.argv:
        # 给 shell 用：0 = 可用，3 = 未安装（与流程里"3=跳过"的约定一致）
        return 0 if available() else 3
    print(describe())
    if available():
        print("\n该模块提供的脚本：")
        for n in PROVIDED:
            print(f"  {'[OK]' if script(n) else '[--]'} {n}")
    else:
        print(f"\n备用链路：{FALLBACK['route']}")
        print(f"  怎么查：{FALLBACK['how']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())