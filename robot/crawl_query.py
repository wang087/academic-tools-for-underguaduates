#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl_query.py - 读取 crawl_site.py 生成的 JSON 并提供查询 / 导出功能

用途：验证抓取结果、按关键词检索正文、导出 Markdown 语料，供后续流程调用。

命令：
  list       列出所有页面（编号、状态、深度、标题、URL、正文长度）
  show       查看某个页面的完整字段（支持编号 p0001、序号或 URL 片段）
  search     在所有页面正文中检索关键词
  links      列出链接关系（站内 / 站外）
  stats      显示抓取统计与站点概况
  warnings   查看 robots 软限制等警告信息
  export-md  把每个页面导出为 Markdown 文件

示例：
  python crawl_query.py site.json list
  python crawl_query.py site.json show p0003
  python crawl_query.py site.json search "价格" --limit 10
  python crawl_query.py site.json warnings
  python crawl_query.py site.json export-md ./md_out
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


# --------------------------------------------------------------------------------------
# 数据访问 API（可被其他脚本直接 import 使用）
# --------------------------------------------------------------------------------------


def load_document(path: str) -> dict:
    """读取抓取结果 JSON。"""
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if "pages" not in document:
        raise ValueError("不是有效的抓取结果文件（缺少 pages 字段）: %s" % path)
    return document


def iter_pages(document: dict, include_failed: bool = True):
    """遍历页面记录。"""
    for page in document.get("pages", []):
        if not include_failed and not page.get("ok"):
            continue
        yield page


def find_page(document: dict, needle: str) -> dict | None:
    """按编号（p0001）、列表序号（1 起）或 URL 片段查找页面。"""
    pages = document.get("pages", [])
    text = str(needle).strip()
    if re.fullmatch(r"p\d{3,}", text, re.I):
        for page in pages:
            if page.get("id", "").lower() == text.lower():
                return page
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(pages):
            return pages[index]
    matches = [p for p in pages if text in (p.get("url") or "")]
    if not matches:
        lowered = text.lower()
        matches = [p for p in pages if lowered in (p.get("url") or "").lower()
                   or lowered in (p.get("title") or "").lower()]
    return matches[0] if matches else None


def search_text(document: dict, keyword: str, case_sensitive: bool = False,
                limit: int = 50, context: int = 80) -> list:
    """在标题、正文、元信息中检索关键词，返回命中片段。"""
    hits = []
    flag = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(keyword), flag)
    for page in iter_pages(document):
        haystacks = [("text", page.get("text") or ""),
                     ("title", page.get("title") or ""),
                     ("meta", json.dumps(page.get("meta") or {}, ensure_ascii=False))]
        for field, content in haystacks:
            match = pattern.search(content)
            if not match:
                continue
            start = max(0, match.start() - context)
            end = min(len(content), match.end() + context)
            snippet = content[start:end].replace("\n", " ")
            hits.append({"id": page.get("id"), "url": page.get("url"),
                         "field": field, "snippet": snippet,
                         "count": len(pattern.findall(content))})
            break
        if len(hits) >= limit:
            break
    return hits


def export_markdown(document: dict, out_dir: str, include_failed: bool = False) -> int:
    """把每个页面导出为独立的 Markdown 文件，返回写出数量。"""
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    index_lines = ["# %s 抓取结果" % (document.get("site", {}).get("host") or "站点"), ""]
    for page in iter_pages(document, include_failed=include_failed):
        name = "%s.md" % (page.get("id") or "page")
        path = os.path.join(out_dir, name)
        lines = [
            "# %s" % (page.get("title") or "(无标题)"),
            "",
            "- URL: %s" % page.get("url"),
            "- 编号: %s" % page.get("id"),
            "- 状态: %s" % page.get("status"),
            "- 深度: %s" % page.get("depth"),
            "- 渲染方式: %s" % page.get("render_mode"),
        ]
        if page.get("canonical"):
            lines.append("- 规范地址: %s" % page["canonical"])
        if (page.get("meta") or {}).get("description"):
            lines.append("- 描述: %s" % page["meta"]["description"])
        lines.append("")
        if page.get("headings"):
            lines.append("## 标题结构")
            for heading in page["headings"]:
                lines.append("%s %s" % ("#" * min(6, heading["level"] + 1), heading["text"]))
            lines.append("")
        lines.append("## 正文")
        lines.append("")
        lines.append(page.get("text") or "")
        lines.append("")
        if page.get("tables"):
            lines.append("## 表格")
            for table in page["tables"]:
                if table.get("caption"):
                    lines.append("**%s**" % table["caption"])
                for row_index, row in enumerate(table.get("rows", [])):
                    lines.append("| " + " | ".join(row) + " |")
                    if row_index == 0:
                        lines.append("|" + "---|" * len(row))
                lines.append("")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        index_lines.append("- [%s](%s) %s" % (page.get("title") or page.get("id"), name,
                                              page.get("url")))
        written += 1
    with open(os.path.join(out_dir, "index.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(index_lines) + "\n")
    return written


# --------------------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------------------


def cmd_list(document: dict, args) -> int:
    pages = list(iter_pages(document))
    if args.limit:
        pages = pages[: args.limit]
    print("%-7s %-5s %-6s %-9s %-8s %s" % ("编号", "状态", "深度", "正文长度", "渲染", "标题 / URL"))
    for page in pages:
        mark = "  [含警告]" if page.get("warnings") else ""
        print("%-7s %-5s %-6s %-9s %-8s %s" % (
            page.get("id"), page.get("status"), page.get("depth"),
            page.get("text_length"), page.get("render_mode"),
            (page.get("title") or "(无标题)")[:40]))
        print("%-7s %s%s" % ("", page.get("url"), mark))
    print("共 %d 个页面。" % len(pages))
    return 0


def cmd_show(document: dict, args) -> int:
    page = find_page(document, args.target)
    if page is None:
        sys.stderr.write("未找到页面: %s\n" % args.target)
        return 1
    print(json.dumps(page, ensure_ascii=False, indent=2))
    return 0


def cmd_search(document: dict, args) -> int:
    hits = search_text(document, args.keyword, case_sensitive=args.case_sensitive,
                       limit=args.limit, context=args.context)
    if not hits:
        print("未命中: %s" % args.keyword)
        return 1
    for hit in hits:
        print("[%s] %s（%s，命中 %d 次）" % (hit["id"], hit["url"], hit["field"], hit["count"]))
        print("    ...%s..." % hit["snippet"])
    print("共 %d 个页面命中。" % len(hits))
    return 0


def cmd_links(document: dict, args) -> int:
    graph = document.get("link_graph", {})
    edges = graph.get("edges", [])
    if args.source:
        source = find_page(document, args.source)
        if source is None:
            sys.stderr.write("未找到页面: %s\n" % args.source)
            return 1
        edges = [e for e in edges if e.get("from") == source.get("id")]
    if args.limit:
        edges = edges[: args.limit]
    for edge in edges:
        print("%s -> %s%s" % (edge.get("from"), edge.get("to"),
                              "  [%s]" % edge["text"] if edge.get("text") else ""))
    print("站内链接 %d 条，站外 URL %d 个。" % (len(edges),
                                              len(graph.get("external_urls", []))))
    if args.external:
        print("站外 URL：")
        for url in graph.get("external_urls", []):
            print("  %s" % url)
    return 0


def cmd_stats(document: dict, args) -> int:
    crawl = document.get("crawl", {})
    site = document.get("site", {})
    print("站点: %s（%s）" % (site.get("host"), site.get("scheme")))
    print("站点标题: %s" % (site.get("title") or "(无)"))
    print("起始网址: %s" % crawl.get("start_url"))
    print("抓取时间: %s -> %s（%.1fs）" % (crawl.get("started_at"), crawl.get("finished_at"),
                                          crawl.get("duration_seconds") or 0))
    print("渲染引擎: %s" % crawl.get("renderer"))
    robots = document.get("robots", {})
    print("robots.txt: 策略=%s，找到=%s，禁止规则 %d 条" % (
        robots.get("enforcement"), robots.get("found"),
        len(robots.get("disallowed") or [])))
    stats = crawl.get("stats", {})
    for key in ("pages_crawled", "pages_failed", "pages_skipped", "rendered_pages",
                "duplicate_pages", "robots_disallowed_pages", "urls_discovered",
                "edges", "total_bytes", "boilerplate_lines"):
        print("  %-24s %s" % (key, stats.get(key)))
    warnings = document.get("warnings") or []
    print("警告: %d 条%s" % (len(warnings),
                            "（已截断）" if document.get("warnings_truncated") else ""))
    for warning in warnings[:10]:
        print("  [%s] %s" % (warning.get("type"), warning.get("url")))
        print("        %s" % warning.get("message"))
    if len(warnings) > 10:
        print("  ... 其余 %d 条可用 warnings 命令查看" % (len(warnings) - 10))
    if document.get("errors"):
        print("错误（前 10 条）:")
        for error in document["errors"][:10]:
            print("  [%s] %s %s" % (error.get("stage"), error.get("url"), error.get("message")))
    if document.get("skipped"):
        print("跳过（前 10 条）:")
        for item in document["skipped"][:10]:
            print("  [%s] %s" % (item.get("reason"), item.get("url")))
    return 0


def cmd_warnings(document: dict, args) -> int:
    warnings = document.get("warnings") or []
    if args.per_page:
        rows = []
        for page in iter_pages(document):
            for message in page.get("warnings") or []:
                rows.append({"type": "robots" if "robots" in message else "page",
                             "url": page.get("url"), "message": message})
        warnings = rows or warnings
    if not warnings:
        print("没有警告。")
        return 0
    if args.limit:
        warnings = warnings[: args.limit]
    for warning in warnings:
        print("[%s] %s" % (warning.get("type"), warning.get("url")))
        if warning.get("rule"):
            print("    规则: %s" % warning["rule"])
        print("    %s" % warning.get("message"))
    print("共 %d 条警告。" % len(warnings))
    return 0


def cmd_export_md(document: dict, args) -> int:
    count = export_markdown(document, args.out_dir, include_failed=args.include_failed)
    print("已导出 %d 个 Markdown 文件到 %s" % (count, os.path.abspath(args.out_dir)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawl_query.py",
        description="查询 crawl_site.py 生成的 JSON 抓取结果。",
    )
    parser.add_argument("json_file", help="crawl_site.py 输出的 JSON 文件")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出页面")
    p_list.add_argument("--limit", type=int, default=0, help="最多显示多少条")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="查看单个页面的完整 JSON")
    p_show.add_argument("target", help="编号 p0001 / 序号 1 / URL 片段")
    p_show.set_defaults(func=cmd_show)

    p_search = sub.add_parser("search", help="全文检索")
    p_search.add_argument("keyword", help="关键词")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--context", type=int, default=80, help="片段前后字符数")
    p_search.add_argument("--case-sensitive", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_links = sub.add_parser("links", help="查看链接关系")
    p_links.add_argument("--source", help="只看某个页面发出的链接")
    p_links.add_argument("--external", action="store_true", help="同时列出站外 URL")
    p_links.add_argument("--limit", type=int, default=0)
    p_links.set_defaults(func=cmd_links)

    p_stats = sub.add_parser("stats", help="查看统计信息")
    p_stats.set_defaults(func=cmd_stats)

    p_warn = sub.add_parser("warnings", help="查看 robots 等警告信息")
    p_warn.add_argument("--per-page", action="store_true",
                        help="按页面列出（默认读取顶层 warnings 汇总）")
    p_warn.add_argument("--limit", type=int, default=0)
    p_warn.set_defaults(func=cmd_warnings)

    p_export = sub.add_parser("export-md", help="导出 Markdown 语料")
    p_export.add_argument("out_dir", help="输出目录")
    p_export.add_argument("--include-failed", action="store_true", help="包含抓取失败的页面")
    p_export.set_defaults(func=cmd_export_md)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        document = load_document(args.json_file)
    except (OSError, ValueError) as exc:
        sys.stderr.write("读取失败: %s\n" % exc)
        return 1
    return args.func(document, args)


if __name__ == "__main__":
    sys.exit(main())
