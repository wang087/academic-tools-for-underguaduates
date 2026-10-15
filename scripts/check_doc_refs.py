# -*- coding: utf-8 -*-
"""离线检查项目 Markdown 中引用的命令和路径是否存在。"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DOCS = (ROOT / "README.md", ROOT / "SKILL.md")
EXCLUDED_PARTS = {".git", ".work_cdp", "vendor"}
PATH_RE = re.compile(
    r"(?<![\w.$-])(?:流程1/|<[^>]+>/)?(?:skills/[A-Za-z0-9_.-]+/)?"
    r"(?:scripts|optional-cdp-fulltext|subagent-driven-review|"
    r"ppt-integration|nature-integration|templates|run_all\.sh|README\.md|SKILL\.md)"
    r"(?:/[A-Za-z0-9_.-]+)*"
    r"(?:\.(?:py|mjs|sh|md|json|html))?"
    r"(?::\d+(?:-\d+)?)?"
)
TRAILING = ",.;:!?，。；：！？'\"）】》"


def documents(all_docs: bool) -> list[pathlib.Path]:
    if not all_docs:
        return [p for p in DEFAULT_DOCS if p.is_file()]
    return sorted(
        p for p in ROOT.rglob("*.md")
        if not any(part in EXCLUDED_PARTS for part in p.parts)
    )


def normalize(raw: str) -> str:
    value = raw.rstrip(TRAILING)
    value = re.sub(r":\d+(?:-\d+)?$", "", value)
    value = value.removeprefix("流程1/").removeprefix("<流程1>/")
    if any(mark in value for mark in ("*", "$", "<", ">", "...")):
        return ""
    return value.rstrip("/")


def resolve(reference: str) -> pathlib.Path | None:
    path = (ROOT / reference).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path


def find_references(doc: pathlib.Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        for match in PATH_RE.finditer(line):
            reference = normalize(match.group(0))
            if reference:
                found.append((number, reference))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检查 Markdown 中的项目命令和路径引用")
    parser.add_argument("--all", action="store_true", help="检查项目内全部 Markdown，而非主入口文档")
    args = parser.parse_args()

    missing: list[tuple[pathlib.Path, int, str]] = []
    checked = 0
    references = 0
    for doc in documents(args.all):
        checked += 1
        for line, reference in find_references(doc):
            references += 1
            target = resolve(reference)
            if target is None or not target.exists():
                missing.append((doc, line, reference))

    print(f"文档：{checked} 个；引用：{references} 个")
    if missing:
        print(f"缺失引用：{len(missing)} 个")
        for doc, line, reference in missing:
            print(f"  {doc.relative_to(ROOT)}:{line}: {reference}")
        return 1
    print("[OK] 所有文档引用均存在（离线检查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
