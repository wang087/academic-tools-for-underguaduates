#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl_site.py - 网站全站爬取与结构化导出工具

功能：
  1. 输入一个网址，自动遍历其全部子页面（同域深度优先/广度优先）。
  2. 静态页面直接抓取；对 JS 渲染站点（SPA、React/Vue、懒加载内容）自动改用
     无头浏览器渲染后再解析（Chrome/Edge --dump-dom，或已安装的 Playwright）。
  3. 把每个页面的标题、元信息、正文、标题层级、链接、图片、表格、JSON-LD 等
     统一输出为一份 JSON 文件，供后续程序调用。

仅使用 Python 标准库；Playwright 为可选项，未安装也能工作。

用法示例：
  python crawl_site.py https://example.com
  python crawl_site.py https://example.com -o site.json --max-pages 200 --render auto
  python crawl_site.py https://spa.example.com --render always --virtual-time-budget 8000
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import gzip
import hashlib
import html as html_module
import http.cookiejar
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser

# --------------------------------------------------------------------------------------
# 常量与全局配置
# --------------------------------------------------------------------------------------

SCRIPT_NAME = "crawl_site.py"
SCRIPT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)

# 不是网页、无需跟进解析的扩展名
BINARY_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff",
    ".avif", ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".ogg",
    ".flac", ".aac", ".zip", ".rar", ".7z", ".gz", ".tar", ".bz2", ".xz", ".exe", ".msi",
    ".dmg", ".apk", ".deb", ".rpm", ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".woff",
    ".woff2", ".ttf", ".eot", ".otf", ".map", ".wasm", ".bin", ".iso", ".psd", ".ai",
}

# 追踪参数，规范化 URL 时剔除
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "msclkid", "yclid", "dclid", "igshid", "mc_cid", "mc_eid",
    "_ga", "_gl", "ref_src", "spm", "scm", "from_source", "share_token", "wt_mc",
}

# 常见复合顶级域名，用于判断“同域”范围
MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "co.jp", "ne.jp", "or.jp",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "com.hk", "com.tw",
    "com.au", "net.au", "org.au", "com.br", "com.mx", "co.kr", "co.in", "com.sg",
    "com.my", "co.nz", "com.tr", "com.ar", "com.tw", "co.za", "com.vn",
}

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav",
    "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object"}

# SPA / JS 渲染特征
SPA_MARKERS = (
    "__NEXT_DATA__", "__NUXT__", "data-reactroot", "ng-version", "id=\"root\"", "id='root'",
    "id=\"app\"", "id='app'", "id=\"__next\"", "id=\"__nuxt\"", "window.__INITIAL_STATE__",
    "data-server-rendered", "v-app", "ng-app",
)

_LOG_LOCK = threading.Lock()
_LOG_LEVEL = 1  # 0=静默 1=常规 2=详细


def log(message: str, level: int = 1) -> None:
    """输出进度信息到 stderr（纯文本，无图标）。"""
    if _LOG_LEVEL < level:
        return
    with _LOG_LOCK:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------------------
# URL 处理
# --------------------------------------------------------------------------------------


def normalize_url(url: str, base: str | None = None, strip_params: bool = True) -> str | None:
    """把链接规范化为可比较的绝对 URL；无法处理时返回 None。"""
    if not url:
        return None
    url = html_module.unescape(url.strip())
    if not url or url.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "sms:", "about:")):
        return None
    if base:
        url = urllib.parse.urljoin(base, url)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not host:
        return None
    netloc = host
    if parts.port and not ((scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)):
        netloc = "%s:%d" % (host, parts.port)
    path = parts.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    query = parts.query
    if strip_params and query:
        kept = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        query = urllib.parse.urlencode(kept, doseq=True)
    # 去掉末尾多余的 "/"（保留根路径）
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def registrable_domain(host: str) -> str:
    """粗略提取可注册域名（用于 --scope domain）。"""
    host = host.lower().strip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in MULTI_PART_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def url_extension(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    _, ext = os.path.splitext(path)
    return ext.lower()


def same_scope(url: str, start_url: str, scope: str) -> bool:
    """判断 url 是否在爬取范围内。"""
    if scope == "any":
        return True
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(start_url)
    if scope == "host":
        return a.hostname == b.hostname
    if scope == "domain":
        return registrable_domain(a.hostname or "") == registrable_domain(b.hostname or "")
    if scope == "path":
        if a.hostname != b.hostname:
            return False
        base = b.path if b.path.endswith("/") else b.path.rsplit("/", 1)[0] + "/"
        return (a.path or "/").startswith(base)
    return True


# --------------------------------------------------------------------------------------
# HTTP 抓取
# --------------------------------------------------------------------------------------


class FetchResult:
    __slots__ = ("url", "final_url", "status", "headers", "body", "content_type",
                 "encoding", "elapsed_ms", "redirects", "error")

    def __init__(self, url: str):
        self.url = url
        self.final_url = url
        self.status = 0
        self.headers: dict = {}
        self.body: bytes = b""
        self.content_type: str = ""
        self.encoding: str | None = None
        self.elapsed_ms = 0
        self.redirects: list = []
        self.error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


class _RecordingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.chain: list = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        self.chain.append({"from": req.full_url, "to": newurl, "status": code})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sniff_encoding(body: bytes, content_type: str | None) -> str:
    """从响应头或 HTML 声明中猜测字符集，失败时做常见编码回退。"""
    declared = None
    if content_type:
        m = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", content_type, re.I)
        if m:
            declared = m.group(1).lower()
    if not declared:
        head = body[:8192]
        m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w\-]+)", head, re.I)
        if not m:
            m = re.search(rb"charset\s*=\s*[\"']?\s*([\w\-]+)", head, re.I)
        if m:
            declared = m.group(1).decode("ascii", "ignore").lower()
    candidates = []
    if declared:
        candidates.append(declared)
        alias = {"gb2312": "gb18030", "gbk": "gb18030", "ansi_x3.4-1968": "utf-8"}
        if declared in alias:
            candidates.append(alias[declared])
    candidates += ["utf-8", "gb18030", "big5", "shift_jis", "cp1252", "latin-1"]
    for enc in candidates:
        try:
            body.decode(enc, "strict")
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def decode_body(body: bytes, encoding: str) -> str:
    try:
        return body.decode(encoding, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


class Fetcher:
    """基于标准库的 HTTP 抓取器（支持 gzip/deflate、cookie、重试、限速）。"""

    def __init__(self, user_agent: str, timeout: float, retries: int, delay: float,
                 verify_ssl: bool, extra_headers: dict | None, max_bytes: int,
                 allow_encoding: bool = True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = max(0, retries)
        self.delay = max(0.0, delay)
        self.extra_headers = extra_headers or {}
        self.max_bytes = max_bytes
        self.allow_encoding = allow_encoding
        self.cookie_jar = http.cookiejar.CookieJar()
        self._ssl_context = None if verify_ssl else ssl._create_unverified_context()
        self._host_lock = threading.Lock()
        self._host_last: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}

    # -- 限速 -------------------------------------------------------------------------
    def set_host_delay(self, host: str, seconds: float) -> None:
        if seconds:
            with self._host_lock:
                self._host_delay[host] = max(self._host_delay.get(host, 0.0), seconds)

    def _wait_for_host(self, host: str) -> None:
        required = max(self.delay, self._host_delay.get(host, 0.0))
        if required <= 0:
            return
        while True:
            with self._host_lock:
                now = time.monotonic()
                last = self._host_last.get(host, 0.0)
                wait = required - (now - last)
                if wait <= 0:
                    self._host_last[host] = now
                    return
            time.sleep(min(wait, 1.0))

    # -- 抓取 -------------------------------------------------------------------------
    def fetch(self, url: str) -> FetchResult:
        result = FetchResult(url)
        host = urllib.parse.urlsplit(url).hostname or ""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.allow_encoding:
            headers["Accept-Encoding"] = "gzip, deflate"
        headers.update(self.extra_headers)

        last_error = None
        for attempt in range(self.retries + 1):
            self._wait_for_host(host)
            started = time.monotonic()
            redirect_handler = _RecordingRedirectHandler()
            opener = urllib.request.build_opener(
                redirect_handler, urllib.request.HTTPCookieProcessor(self.cookie_jar)
            )
            if self._ssl_context is not None:
                opener.add_handler(urllib.request.HTTPSHandler(context=self._ssl_context))
            request = urllib.request.Request(url, headers=headers)
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    raw = self._read_limited(response)
                    result.status = response.status
                    result.final_url = response.geturl()
                    result.headers = {k.lower(): v for k, v in response.headers.items()}
                    result.content_type = response.headers.get("Content-Type", "") or ""
                    result.encoding = sniff_encoding(raw, result.content_type)
                    result.body = raw
                result.redirects = redirect_handler.chain
                result.elapsed_ms = int((time.monotonic() - started) * 1000)
                return result
            except urllib.error.HTTPError as exc:
                body = b""
                try:
                    body = self._read_limited(exc)
                except Exception:
                    pass
                result.status = exc.code
                result.final_url = exc.geturl() if hasattr(exc, "geturl") else url
                result.headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                result.content_type = (exc.headers or {}).get("Content-Type", "") or ""
                result.encoding = sniff_encoding(body, result.content_type)
                result.body = body
                result.redirects = redirect_handler.chain
                result.elapsed_ms = int((time.monotonic() - started) * 1000)
                # 4xx 不重试（除 429），5xx 重试
                if exc.code == 429 or exc.code >= 500:
                    last_error = "HTTP %d" % exc.code
                    if attempt < self.retries:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                return result
            except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as exc:
                last_error = "%s: %s" % (type(exc).__name__, exc)
                result.elapsed_ms = int((time.monotonic() - started) * 1000)
                if attempt < self.retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        result.error = last_error or "unknown error"
        return result

    def _read_limited(self, response) -> bytes:
        chunks = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= self.max_bytes:
                break
        body = b"".join(chunks)
        encoding = (response.headers.get("Content-Encoding", "") or "").lower()
        try:
            if "gzip" in encoding:
                body = gzip.decompress(body)
            elif "deflate" in encoding:
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception:
            pass
        return body


# --------------------------------------------------------------------------------------
# HTML 解析：正文、标题层级、链接、图片、表格、元信息
# --------------------------------------------------------------------------------------


class HTMLExtractor(HTMLParser):
    """把 HTML 解析为结构化数据。基于标准库，容忍不规范标记。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict = {}
        self.og: dict = {}
        self.twitter: dict = {}
        self.other_meta: dict = {}
        self.canonical: str | None = None
        self.base_href: str | None = None
        self.alternates: list = []
        self.feeds: list = []
        self.icon: str | None = None
        self.lang: str | None = None
        self.dir: str | None = None
        self.headings: list = []
        self.links: list = []
        self.images: list = []
        self.tables: list = []
        self.json_ld: list = []
        self.script_srcs: list = []
        self.script_count = 0
        self.stylesheet_count = 0
        self.has_noscript = False

        self._counts: dict[str, int] = {}
        self._head_depth = 0
        self._title_depth = 0
        self._body_buf: list[str] = []
        self._a_depth = 0
        self._a_text: list[str] = []
        self._a_attrs: dict = {}
        self._h_level: str | None = None
        self._h_text: list[str] = []
        self._cell_depth = 0
        self._cell_text: list[str] = []
        self._row: list[str] = []
        self._rows: list = []
        self._in_table = 0
        self._table_caption: list[str] = []
        self._in_caption = False
        self._ld_buffer: list[str] = []
        self._in_ld = False

    # -- 工具 -------------------------------------------------------------------------
    def _increment(self, tag: str) -> int:
        self._counts[tag] = self._counts.get(tag, 0) + 1
        return self._counts[tag]

    def _decrement(self, tag: str) -> int:
        self._counts[tag] = max(0, self._counts.get(tag, 0) - 1)
        return self._counts[tag]

    def _in_skip(self) -> bool:
        return any(self._counts.get(t, 0) > 0 for t in SKIP_TAGS)

    def _push_text(self, text: str) -> None:
        if not text:
            return
        if self._in_ld:
            self._ld_buffer.append(text)
            return
        if self._in_skip():
            return
        if self._title_depth > 0:
            self.title_parts.append(text)
        if self._cell_depth > 0:
            self._cell_text.append(text)
        if self._in_caption:
            self._table_caption.append(text)
        if self._a_depth > 0:
            self._a_text.append(text)
        if self._h_level is not None:
            self._h_text.append(text)
        if self._head_depth == 0:
            # 表格单元格内的文本**也要**进正文。
            #   实测坑（南京大学化学化工学院教师页，236 人）：
            #   该站整页内容包在 `<table class="wp_article_list_table"><tr><td>…`
            #   里，早期实现用 `and self._cell_depth == 0` 把 td 内文本一律排除，
            #   导致正文只剩一个 BOM（text_length=0，而页面真实文本最多 32405 字），
            #   236 个教师页 14/14 抽查全部丢正文。
            #   表格布局在中文高校站群中极常见，故此处不再排除单元格文本；
            #   结构化表格仍由 `_cell_text → _row → tables` 独立收集，不受影响。
            self._body_buf.append(text)

    @staticmethod
    def _attr(attrs: list, name: str) -> str | None:
        for key, value in attrs:
            if key.lower() == name:
                return value
        return None

    # -- 标签处理 ---------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):  # noqa: D102
        tag = tag.lower()
        skipping = self._in_skip()
        if tag in SKIP_TAGS:
            if tag == "script" and not skipping:
                self.script_count += 1
                src = self._attr(attrs, "src")
                if src:
                    self.script_srcs.append(src)
                if "ld+json" in (self._attr(attrs, "type") or "").lower():
                    self._in_ld = True
                    self._ld_buffer = []
            elif tag == "noscript" and not skipping:
                self.has_noscript = True
            elif tag == "style" and not skipping:
                self.stylesheet_count += 1
            self._increment(tag)
            return
        if skipping:
            self._increment(tag)
            return
        if tag == "html":
            self.lang = self._attr(attrs, "lang") or self.lang
            self.dir = self._attr(attrs, "dir") or self.dir
        elif tag == "head":
            self._head_depth += 1
        elif tag == "title":
            self._title_depth += 1
        elif tag == "meta":
            self._handle_meta(attrs)
        elif tag == "link":
            self._handle_link(attrs)
        elif tag == "base":
            href = self._attr(attrs, "href")
            if href and self.base_href is None:
                self.base_href = href
        elif tag == "a":
            self._a_depth += 1
            if self._a_depth == 1:
                self._a_text = []
                self._a_attrs = {k.lower(): v for k, v in attrs}
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self._h_level is None:
                self._h_level = tag
                self._h_text = []
        elif tag == "img":
            self._handle_image(attrs)
        elif tag == "table":
            self._in_table += 1
            self._rows = []
            self._table_caption = []
        elif tag == "caption":
            self._in_caption = True
            self._table_caption = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell_depth += 1
            if self._cell_depth == 1:
                self._cell_text = []
        if tag in BLOCK_TAGS:
            self._push_text("\n")
        self._increment(tag)

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        tag = tag.lower()
        if tag == "meta":
            self._handle_meta(attrs)
        elif tag == "link":
            self._handle_link(attrs)
        elif tag == "img":
            self._handle_image(attrs)
        elif tag in BLOCK_TAGS:
            self._push_text("\n")

    def handle_endtag(self, tag):  # noqa: D102
        tag = tag.lower()
        if tag not in SKIP_TAGS and self._in_skip():
            self._decrement(tag)
            return
        if tag == "head":
            self._head_depth = max(0, self._head_depth - 1)
        elif tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
        elif tag == "a":
            self._a_depth = max(0, self._a_depth - 1)
            if self._a_depth == 0 and self._a_attrs:
                href = self._a_attrs.get("href")
                if href:
                    self.links.append({
                        "href": href,
                        "text": collapse_ws("".join(self._a_text)),
                        "rel": (self._a_attrs.get("rel") or "").split(),
                        "target": self._a_attrs.get("target"),
                        "title": self._a_attrs.get("title"),
                    })
                self._a_attrs = {}
                self._a_text = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self._h_level == tag:
                text = collapse_ws("".join(self._h_text))
                if text:
                    self.headings.append({"level": int(tag[1]), "text": text})
                self._h_level = None
                self._h_text = []
        elif tag in ("td", "th"):
            self._cell_depth = max(0, self._cell_depth - 1)
            if self._cell_depth == 0:
                self._row.append(collapse_ws("".join(self._cell_text)))
                self._cell_text = []
        elif tag == "tr":
            if self._row and any(c for c in self._row):
                self._rows.append(self._row)
            self._row = []
        elif tag == "caption":
            self._in_caption = False
        elif tag == "table":
            self._in_table = max(0, self._in_table - 1)
            if self._in_table == 0:
                if self._row and any(c for c in self._row):
                    self._rows.append(self._row)
                    self._row = []
                if self._rows:
                    self.tables.append({
                        "caption": collapse_ws("".join(self._table_caption)) or None,
                        "rows": self._rows,
                    })
                self._rows = []
                self._table_caption = []
        elif tag == "script":
            if self._in_ld:
                raw = "".join(self._ld_buffer).strip()
                self._in_ld = False
                self._ld_buffer = []
                if raw:
                    try:
                        self.json_ld.append(json.loads(raw))
                    except Exception:
                        pass

        if tag in BLOCK_TAGS:
            self._push_text("\n")
        self._decrement(tag)

    def handle_data(self, data):  # noqa: D102
        self._push_text(data)

    # -- 元信息子处理 -----------------------------------------------------------------
    def _handle_meta(self, attrs) -> None:
        name = (self._attr(attrs, "name") or "").strip().lower()
        prop = (self._attr(attrs, "property") or "").strip().lower()
        http_equiv = (self._attr(attrs, "http-equiv") or "").strip().lower()
        content = self._attr(attrs, "content")
        charset = self._attr(attrs, "charset")
        if charset:
            self.meta["charset"] = charset
        if content is None:
            return
        if prop.startswith("og:"):
            self.og[prop[3:]] = content
        elif name.startswith("twitter:"):
            self.twitter[name[8:]] = content
        elif name in ("description", "keywords", "author", "robots", "viewport",
                      "generator", "theme-color", "application-name", "referrer",
                      "googlebot", "format-detection"):
            self.meta[name] = content
        elif prop:
            self.meta.setdefault(prop, content)
        elif name:
            if name not in self.other_meta and len(self.other_meta) < 50:
                self.other_meta[name] = content
        elif http_equiv:
            self.meta.setdefault(http_equiv, content)

    def _handle_link(self, attrs) -> None:
        rel = (self._attr(attrs, "rel") or "").lower().split()
        href = self._attr(attrs, "href")
        if not href:
            return
        if "canonical" in rel:
            self.canonical = href
        elif "icon" in rel or "shortcut" in rel or "apple-touch-icon" in rel:
            if self.icon is None:
                self.icon = href
        elif "alternate" in rel:
            hreflang = self._attr(attrs, "hreflang")
            ltype = (self._attr(attrs, "type") or "").lower()
            if hreflang:
                self.alternates.append({"hreflang": hreflang, "href": href})
            elif "rss" in ltype or "atom" in ltype or "xml" in ltype:
                self.feeds.append({"type": ltype, "href": href,
                                   "title": self._attr(attrs, "title")})
        elif "stylesheet" in rel:
            self.stylesheet_count += 1
        elif "preload" in rel or "modulepreload" in rel:
            pass

    def _handle_image(self, attrs) -> None:
        src = (self._attr(attrs, "src") or self._attr(attrs, "data-src")
               or self._attr(attrs, "data-original") or self._attr(attrs, "data-lazy-src")
               or self._attr(attrs, "data-echo"))
        srcset = self._attr(attrs, "srcset") or self._attr(attrs, "data-srcset")
        if not src and srcset:
            first = srcset.split(",")[0].strip().split(" ")[0]
            src = first or None
        if not src:
            return
        self.images.append({
            "src": src,
            "alt": self._attr(attrs, "alt"),
            "title": self._attr(attrs, "title"),
            "width": self._attr(attrs, "width"),
            "height": self._attr(attrs, "height"),
        })

    # -- 结果 -------------------------------------------------------------------------
    def body_text(self) -> str:
        raw = "".join(self._body_buf)
        lines = []
        for line in raw.split("\n"):
            line = collapse_ws(line)
            if line:
                lines.append(line)
        return "\n".join(lines)


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def content_weight(text: str) -> int:
    """内容权重：中日韩字符与英文单词各计 1，避免用字符数误判中文站点。"""
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]", text))
    tokens = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + tokens


def script_bytes(html_text: str) -> int:
    return sum(len(m) for m in re.findall(r"(?is)<script[^>]*>.*?</script>", html_text))


def parse_html(html_text: str, page_url: str, include_tables: bool = True,
               max_tables: int = 20, max_table_rows: int = 200) -> dict:
    """解析 HTML 并返回结构化字典（链接保持原始写法，由调用方解析为绝对地址）。"""
    parser = HTMLExtractor()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception as exc:  # 解析失败也要返回已有结果
        log("  解析警告 %s: %s" % (page_url, exc), 2)

    base = parser.base_href or page_url
    tables = []
    if include_tables:
        for table in parser.tables[:max_tables]:
            rows = table["rows"][:max_table_rows]
            tables.append({"caption": table["caption"], "rows": rows,
                           "truncated": len(table["rows"]) > max_table_rows})

    return {
        "title": collapse_ws("".join(parser.title_parts)) or None,
        "lang": parser.lang,
        "dir": parser.dir,
        "meta": parser.meta,
        "og": parser.og,
        "twitter": parser.twitter,
        "other_meta": parser.other_meta,
        "canonical": parser.canonical,
        "base_href": parser.base_href,
        "alternates": parser.alternates,
        "feeds": parser.feeds,
        "icon": parser.icon,
        "headings": parser.headings,
        "raw_links": parser.links,
        "raw_images": parser.images,
        "tables": tables,
        "json_ld": parser.json_ld,
        "scripts": {"count": parser.script_count,
                    "external": parser.script_srcs[:50],
                    "truncated": len(parser.script_srcs) > 50},
        "stylesheet_count": parser.stylesheet_count,
        "has_noscript": parser.has_noscript,
        "text": parser.body_text(),
        "_resolve_base": base,
    }


# --------------------------------------------------------------------------------------
# JS 渲染：无头浏览器
# --------------------------------------------------------------------------------------


def find_browser() -> str | None:
    """寻找本机可用的 Chrome/Chromium/Edge 可执行文件。"""
    env = os.environ.get("CHROME_PATH") or os.environ.get("BROWSER_PATH")
    if env and os.path.isfile(env):
        return env
    system = platform.system()
    candidates = []
    if system == "Windows":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")]
        for root in roots:
            if not root:
                continue
            candidates += [
                os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(root, "Google", "Chrome Beta", "Application", "chrome.exe"),
                os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(root, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                os.path.join(root, "Chromium", "Application", "chrome.exe"),
            ]
    elif system == "Darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    else:
        candidates += [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium",
            "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge", "/snap/bin/chromium",
            "/usr/bin/brave-browser",
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge",
                 "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


class ChromeRenderer:
    """通过无头 Chrome/Edge 的 --dump-dom 获取渲染后的 DOM。"""

    def __init__(self, browser_path: str, user_agent: str, timeout: float,
                 virtual_time_budget: int, extra_args: list | None = None,
                 reuse_profile: bool = True):
        self.browser_path = browser_path
        self.user_agent = user_agent
        self.timeout = timeout
        self.virtual_time_budget = virtual_time_budget
        self.extra_args = extra_args or []
        self._profile_dir = tempfile.mkdtemp(prefix="crawlsite-profile-") if reuse_profile else None
        self._lock = threading.Lock()
        self._headless_flag = "--headless=new"

    def close(self) -> None:
        if self._profile_dir and os.path.isdir(self._profile_dir):
            shutil.rmtree(self._profile_dir, ignore_errors=True)

    def render(self, url: str) -> tuple[str | None, str | None, str]:
        """返回 (html, error, mode)。"""
        with self._lock:
            if self._profile_dir is None:
                profile = tempfile.mkdtemp(prefix="crawlsite-profile-")
                cleanup = True
            else:
                profile = self._profile_dir
                cleanup = False
            try:
                html, error = self._run_once(url, profile)
                if html is None and self._headless_flag == "--headless=new":
                    # 老版本 Chrome 不支持 --headless=new，退回旧参数
                    self._headless_flag = "--headless"
                    html, error = self._run_once(url, profile)
                if html is None:
                    return None, error, "browser-chrome"
                return html, None, "browser-chrome"
            finally:
                if cleanup:
                    shutil.rmtree(profile, ignore_errors=True)

    def _run_once(self, url: str, profile: str) -> tuple[str | None, str | None]:
        args = [
            self.browser_path,
            self._headless_flag,
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-sync",
            "--hide-scrollbars",
            "--mute-audio",
            "--no-pings",
            "--user-data-dir=%s" % profile,
            "--user-agent=%s" % self.user_agent,
            "--virtual-time-budget=%d" % self.virtual_time_budget,
            "--dump-dom",
            url,
        ] + self.extra_args
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=creationflags,
            )
        except OSError as exc:
            return None, "启动浏览器失败: %s" % exc
        try:
            stdout, _ = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            return None, "浏览器渲染超时（%.0fs）" % self.timeout
        if not stdout:
            return None, "浏览器未返回 DOM（退出码 %s）" % process.returncode
        return stdout.decode("utf-8", "replace"), None


def _kill_process_tree(process: subprocess.Popen) -> None:
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000)
        else:
            process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=10)
    except Exception:
        pass


class PlaywrightRenderer:
    """使用已安装的 Playwright（可选）渲染，支持等待选择器与滚动加载。"""

    def __init__(self, timeout: float, wait_selector: str | None = None,
                 scroll: bool = False, user_agent: str | None = None):
        self.timeout = timeout * 1000
        self.wait_selector = wait_selector
        self.scroll = scroll
        self.user_agent = user_agent
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def render(self, url: str) -> tuple[str | None, str | None, str]:
        with self._lock:
            try:
                browser = self._ensure_browser()
                context = browser.new_context(user_agent=self.user_agent) if self.user_agent \
                    else browser.new_context()
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(self.timeout, 10000))
                    except Exception:
                        pass
                    if self.wait_selector:
                        try:
                            page.wait_for_selector(self.wait_selector, timeout=min(self.timeout, 15000))
                        except Exception:
                            pass
                    if self.scroll:
                        page.evaluate(
                            """async () => {
                                await new Promise((resolve) => {
                                    let total = 0;
                                    const timer = setInterval(() => {
                                        window.scrollBy(0, window.innerHeight);
                                        total += window.innerHeight;
                                        if (total >= document.body.scrollHeight || total > 20000) {
                                            clearInterval(timer); resolve();
                                        }
                                    }, 120);
                                });
                            }"""
                        )
                    html_text = page.content()
                finally:
                    context.close()
                return html_text, None, "playwright"
            except Exception as exc:
                return None, "Playwright 渲染失败: %s" % exc, "playwright"

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------------------
# 爬虫主体
# --------------------------------------------------------------------------------------


class Crawler:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        start = normalize_url(options.url)
        if not start:
            raise ValueError("无效的起始网址: %s" % options.url)
        self.start_url = start
        self.start_host = urllib.parse.urlsplit(start).hostname or ""
        self.start_origin = "%s://%s" % (urllib.parse.urlsplit(start).scheme,
                                         urllib.parse.urlsplit(start).netloc)
        self._sitemap_urls: list = []
        self.fetcher = Fetcher(
            user_agent=options.user_agent,
            timeout=options.timeout,
            retries=options.retries,
            delay=options.delay,
            verify_ssl=not options.insecure,
            extra_headers=parse_header_list(options.header),
            max_bytes=options.max_bytes,
        )
        self.robots: urllib.robotparser.RobotFileParser | None = None
        self.robots_info: dict = {"url": None, "found": False, "crawl_delay": None,
                                  "disallowed": [], "enforcement": options.robots}
        self.pages: list = []
        self.errors: list = []
        self.skipped: list = []
        self.warnings: list = []
        self.warnings_truncated = False
        self.redirects: list = []
        self.edges: list = []
        self.external_urls: list = []
        self._external_seen: set[str] = set()
        self.visited: set[str] = set()
        self._skip_seen: set[str] = set()
        self._robots_blocked: dict[str, str] = {}
        self._robots_skip_seen: set[str] = set()
        self.seen_hashes: dict[str, str] = {}
        self.stats = {
            "pages_crawled": 0, "pages_failed": 0, "pages_skipped": 0,
            "urls_discovered": 0, "rendered_pages": 0, "total_bytes": 0,
            "duplicate_pages": 0, "edges": 0, "boilerplate_lines": 0,
            "robots_disallowed_pages": 0,
        }
        self.include_patterns = [re.compile(p) for p in (options.include or [])]
        self.exclude_patterns = [re.compile(p) for p in (options.exclude or [])]
        self.renderer = None
        self._renderer_kind = "none"
        self._setup_renderer()
        self._lock = threading.Lock()

    # -- 初始化渲染器 -----------------------------------------------------------------
    def _setup_renderer(self) -> None:
        mode = self.options.render
        if mode == "never":
            return
        engine = self.options.render_engine
        if engine in ("auto", "playwright") and PlaywrightRenderer.available():
            self.renderer = PlaywrightRenderer(
                timeout=self.options.timeout,
                wait_selector=self.options.wait_selector,
                scroll=self.options.scroll,
                user_agent=self.options.user_agent,
            )
            self._renderer_kind = "playwright"
            log("渲染引擎: Playwright")
            return
        if engine == "playwright" and not PlaywrightRenderer.available():
            log("提示: 未安装 Playwright，改用本机浏览器渲染（pip install playwright && playwright install chromium 可获得更强渲染）")
        browser = self.options.browser_path or find_browser()
        if browser:
            self.renderer = ChromeRenderer(
                browser_path=browser,
                user_agent=self.options.user_agent,
                timeout=max(self.options.timeout * 2, 30.0),
                virtual_time_budget=self.options.virtual_time_budget,
                extra_args=list(self.options.browser_arg or []),
            )
            self._renderer_kind = "browser-chrome"
            log("渲染引擎: 无头浏览器 %s" % browser)
        else:
            log("警告: 未找到 Chrome/Edge，JS 渲染不可用；如需渲染请安装 Chrome 或 Playwright")

    # -- robots.txt -------------------------------------------------------------------
    def load_robots(self) -> None:
        self.robots_info["enforcement"] = self.options.robots
        if self.options.robots == "ignore":
            log("robots.txt: 已忽略（--robots ignore），不读取 robots.txt")
            return
        robots_url = self.start_origin + "/robots.txt"
        self.robots_info["url"] = robots_url
        result = self.fetcher.fetch(robots_url)
        if result.status == 200 and result.body:
            text = decode_body(result.body, result.encoding or "utf-8")
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(text.splitlines())
            self.robots = parser
            self.robots_info["found"] = True
            # 标准库只接受整数 crawl-delay 且忽略通配分组，这里自行解析指令
            delay, disallowed, sitemaps = parse_robots_directives(
                text, self.options.user_agent)
            self.robots_info["disallowed"] = disallowed[:200]
            if delay:
                self.robots_info["crawl_delay"] = float(delay)
                self.fetcher.set_host_delay(self.start_host, float(delay))
            if self.options.use_sitemap:
                self._sitemap_urls.extend(sitemaps)
            log("robots.txt: 已读取（规则 %d 条，策略=%s%s）" % (
                len(self.robots_info["disallowed"]),
                "软限制，禁止后仍抓取并记录警告" if self.options.robots == "soft" else "严格遵守",
                "，crawl-delay=%.1fs" % self.robots_info["crawl_delay"]
                if self.robots_info["crawl_delay"] else ""))

    def robots_allows(self, url: str) -> bool:
        if self.robots is None:
            return True
        try:
            return self.robots.can_fetch(self.options.user_agent, url)
        except Exception:
            return True

    def match_disallow_rule(self, url: str) -> str:
        """找出与 URL 匹配的 Disallow 规则，用于生成警告信息。"""
        rules = self.robots_info.get("disallowed") or []
        path = urllib.parse.urlsplit(url).path or "/"
        best = None
        for rule in rules:
            if not rule or "*" in rule or "$" in rule:
                continue
            if path.startswith(rule) and (best is None or len(rule) > len(best)):
                best = rule
        return best or "/（通配符规则，详见 robots.txt）"

    # -- 站点地图 ---------------------------------------------------------------------
    def collect_sitemap_seeds(self) -> list:
        if not self.options.use_sitemap:
            return []
        candidates = list(dict.fromkeys(self._sitemap_urls))
        base = self.start_origin
        candidates += ["%s/sitemap.xml" % base, "%s/sitemap_index.xml" % base]
        seeds: list = []
        queue = list(dict.fromkeys(candidates))
        seen = set()
        while queue and len(seeds) < self.options.max_pages:
            sitemap_url = queue.pop(0)
            if sitemap_url in seen:
                continue
            seen.add(sitemap_url)
            result = self.fetcher.fetch(sitemap_url)
            if result.status != 200 or not result.body:
                continue
            text = decode_body(result.body, result.encoding or "utf-8")
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
            for loc in locs:
                loc = html_module.unescape(loc.strip())
                if re.search(r"\.xml(\.gz)?$", loc, re.I) or "sitemap" in loc.lower():
                    if loc not in seen and len(seen) < 20:
                        queue.append(loc)
                else:
                    normalized = normalize_url(loc)
                    if normalized:
                        seeds.append(normalized)
        seeds = list(dict.fromkeys(seeds))
        if seeds:
            log("sitemap: 发现 %d 个候选页面" % len(seeds))
        return seeds

    # -- URL 过滤 ---------------------------------------------------------------------
    def should_visit(self, url: str) -> tuple[bool, str]:
        if url in self.visited:
            return False, "seen"
        if not same_scope(url, self.start_url, self.options.scope):
            return False, "scope"
        ext = url_extension(url)
        if ext and ext in BINARY_EXTENSIONS and ext not in (self.options.allow_ext or []):
            return False, "extension"
        for pattern in self.exclude_patterns:
            if pattern.search(url):
                return False, "pattern"
        if self.include_patterns and not any(p.search(url) for p in self.include_patterns):
            return False, "include"
        if self.options.robots != "ignore" and not self.robots_allows(url):
            if self.options.robots == "strict":
                if url not in self._robots_skip_seen:
                    self._robots_skip_seen.add(url)
                    self.stats["robots_disallowed_pages"] += 1
                return False, "robots"
            # 软限制：robots.txt 禁止的地址照常抓取，但记录规则以便写入警告
            self._robots_blocked[url] = self.match_disallow_rule(url)
        return True, ""

    # -- 页面处理 ---------------------------------------------------------------------
    def process(self, url: str, depth: int, parent: str | None) -> dict | None:
        result = self.fetcher.fetch(url)
        self.stats["total_bytes"] += len(result.body)
        robots_rule = self._robots_blocked.get(url)
        robots_allowed = None if self.options.robots == "ignore" else robots_rule is None
        if robots_rule is not None:
            self.stats["robots_disallowed_pages"] += 1
        for hop in result.redirects:
            with self._lock:
                self.redirects.append({"from": hop["from"], "to": hop["to"],
                                       "status": hop["status"], "page": url})

        page: dict = {
            "id": "",
            "url": url,
            "final_url": result.final_url,
            "depth": depth,
            "discovered_from": parent,
            "status": result.status,
            "ok": bool(result.ok),
            "content_type": result.content_type,
            "encoding": result.encoding,
            "fetched_at": utc_now_iso(),
            "elapsed_ms": result.elapsed_ms,
            "render_mode": "static",
            "render_reason": None,
            "byte_size": len(result.body),
            "title": None,
            "lang": None,
            "canonical": None,
            "meta": {},
            "og": {},
            "twitter": {},
            "other_meta": {},
            "alternates": [],
            "feeds": [],
            "icon": None,
            "headings": [],
            "text": "",
            "text_length": 0,
            "text_main": None,
            "text_main_length": None,
            "boilerplate_removed": 0,
            "word_count": 0,
            "text_truncated": False,
            "content_sha256": None,
            "duplicate_of": None,
            "links": [],
            "images": [],
            "tables": [],
            "json_ld": [],
            "scripts": {},
            "stylesheet_count": 0,
            "robots_allowed": robots_allowed,
            "warnings": [],
            "error": None,
            "child_urls": [],
        }

        if robots_rule is not None:
            message = ("robots.txt 禁止抓取该地址（Disallow: %s），"
                       "当前为软限制策略，已照常抓取并保留本警告" % robots_rule)
            page["warnings"].append(message)
            with self._lock:
                if len(self.warnings) < 5000:
                    self.warnings.append({"type": "robots", "url": url,
                                          "rule": robots_rule, "message": message})
                else:
                    self.warnings_truncated = True
            log("  警告: %s 被 robots.txt 禁止（Disallow: %s），软限制下仍抓取" % (url, robots_rule))

        if result.error:
            page["error"] = result.error
            self.stats["pages_failed"] += 1
            with self._lock:
                self.errors.append({"url": url, "stage": "fetch", "message": result.error,
                                    "status": result.status})
            return page

        content_type = (result.content_type or "").lower()
        if result.status >= 400:
            page["error"] = "HTTP %d" % result.status
            self.stats["pages_failed"] += 1
            with self._lock:
                self.errors.append({"url": url, "stage": "http",
                                    "message": "HTTP %d" % result.status,
                                    "status": result.status})
            if "html" not in content_type:
                return page
        elif "html" not in content_type and "xml" not in content_type:
            page["error"] = "非 HTML 内容（%s）" % (result.content_type or "未知类型")
            return page

        static_html = decode_body(result.body, result.encoding or "utf-8")
        html_text = static_html
        needs, reason = self.needs_render(static_html)
        if needs and self.renderer is not None:
            rendered, render_error, mode = self.renderer.render(url)
            if rendered:
                html_text = rendered
                page["render_mode"] = mode
                page["render_reason"] = reason
                self.stats["rendered_pages"] += 1
                log("  使用 JS 渲染: %s（%s）" % (url, reason), 2)
            else:
                page["render_reason"] = "渲染失败: %s" % render_error
                with self._lock:
                    self.errors.append({"url": url, "stage": "render",
                                        "message": render_error or "未知错误", "status": 0})
        elif needs and self.renderer is None:
            page["render_reason"] = "需要 JS 渲染但无可用渲染器"

        data = parse_html(html_text, url, include_tables=self.options.include_tables,
                          max_tables=self.options.max_tables)
        base = data.pop("_resolve_base") or url

        text = data["text"]
        page.update({
            "title": data["title"],
            "lang": data["lang"],
            "canonical": normalize_url(data["canonical"], base) if data["canonical"] else None,
            "meta": data["meta"],
            "og": data["og"],
            "twitter": data["twitter"],
            "other_meta": data["other_meta"],
            "alternates": data["alternates"],
            "feeds": [{"type": f["type"], "url": normalize_url(f["href"], base),
                       "title": f["title"]} for f in data["feeds"]],
            "icon": normalize_url(data["icon"], base) if data["icon"] else None,
            "headings": data["headings"],
            "tables": data["tables"],
            "json_ld": data["json_ld"],
            "scripts": data["scripts"],
            "stylesheet_count": data["stylesheet_count"],
            "text": text,
            "text_length": len(text),
            "word_count": len(re.findall(r"[A-Za-z0-9_']+", text)) + len(re.findall(r"[\u4e00-\u9fff]", text)),
        })

        if self.options.max_text_chars and len(text) > self.options.max_text_chars:
            page["text"] = text[: self.options.max_text_chars]
            page["text_truncated"] = True

        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        page["content_sha256"] = digest
        with self._lock:
            if text.strip() and digest in self.seen_hashes:
                page["duplicate_of"] = self.seen_hashes[digest]
                self.stats["duplicate_pages"] += 1
            else:
                self.seen_hashes[digest] = url

        # 链接与图片解析
        links = []
        link_seen: set = set()
        child_urls: list = []
        skip_expand = bool(page["duplicate_of"]) and self.options.dedupe_content
        for raw in data["raw_links"]:
            resolved = normalize_url(raw["href"], base)
            if not resolved or resolved in link_seen:
                continue
            link_seen.add(resolved)
            internal = same_scope(resolved, self.start_url, self.options.scope)
            links.append({
                "url": resolved,
                "text": raw["text"],
                "rel": raw["rel"],
                "internal": internal,
                "target": raw["target"],
            })
            if internal:
                if not skip_expand and resolved != url:
                    child_urls.append(resolved)
            else:
                with self._lock:
                    if resolved not in self._external_seen and len(self._external_seen) < 2000:
                        self._external_seen.add(resolved)
                        self.external_urls.append(resolved)
        page["links"] = links
        page["child_urls"] = list(dict.fromkeys(child_urls))

        images = []
        for raw in data["raw_images"]:
            src = normalize_url(raw["src"], base)
            if src:
                images.append({"src": src, "alt": raw["alt"], "title": raw["title"],
                               "width": raw["width"], "height": raw["height"]})
        page["images"] = images[: self.options.max_images]

        return page

    # -- 渲染判定 ---------------------------------------------------------------------
    def needs_render(self, html_text: str) -> tuple[bool, str]:
        mode = self.options.render
        if mode == "always":
            return True, "选项 --render always"
        if mode == "never":
            return False, ""
        lowered = html_text.lower()
        stripped = re.sub(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>", " ", html_text)
        stripped = re.sub(r"(?s)<!--.*?-->", " ", stripped)
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        weight = content_weight(stripped)
        head = lowered[:200000]
        threshold = self.options.render_min_text
        has_script = "<script" in head
        if not has_script:
            return False, ""
        if weight < threshold:
            return True, "可见内容过少（权重 %d < %d）且含脚本" % (weight, threshold)
        spa = any(marker.lower() in head for marker in SPA_MARKERS)
        if spa and weight < threshold * 3:
            return True, "检测到单页应用容器（权重 %d）" % weight
        if "<noscript>" in head and weight < threshold:
            return True, "内容依赖脚本（存在 noscript 提示）"
        if weight < 20 and script_bytes(html_text) > 20000:
            return True, "静态内容极少但脚本体积较大"
        if re.search(r"<meta[^>]+name=[\"']fragment[\"']", head):
            return True, "AJAX 爬取协议页面"
        return False, ""

    # -- 主流程 -----------------------------------------------------------------------
    def run(self) -> dict:
        started = time.monotonic()
        started_at = utc_now_iso()
        log("起始网址: %s" % self.start_url)
        log("抓取范围: %s，最多 %s 个页面，深度 %s" % (
            self.options.scope,
            self.options.max_pages if self.options.max_pages else "不限",
            self.options.max_depth if self.options.max_depth else "不限"))
        self.load_robots()

        seeds = [self.start_url]
        seeds += self.collect_sitemap_seeds()
        frontier = [(url, 0 if url == self.start_url else 1, None) for url in dict.fromkeys(seeds)]
        depth = 0
        processed = 0
        max_pages = self.options.max_pages or 10 ** 9
        max_depth = self.options.max_depth or 10 ** 9

        while frontier and processed < max_pages:
            batch = []
            for url, url_depth, parent in frontier:
                if processed + len(batch) >= max_pages:
                    break
                allowed, reason = self.should_visit(url)
                if not allowed:
                    if reason != "seen" and url not in self._skip_seen:
                        self._skip_seen.add(url)
                        self.stats["pages_skipped"] += 1
                        if len(self.skipped) < 2000:
                            self.skipped.append({"url": url, "reason": reason})
                    continue
                self.visited.add(url)
                batch.append((url, url_depth, parent))
            frontier = []
            if not batch:
                break

            log("第 %d 层：待抓取 %d 个页面" % (batch[0][1] + 1, len(batch)))
            with futures.ThreadPoolExecutor(max_workers=max(1, self.options.concurrency)) as pool:
                future_map = {pool.submit(self.process, url, d, parent): url
                              for url, d, parent in batch}
                for future in futures.as_completed(future_map):
                    url = future_map[future]
                    try:
                        page = future.result()
                    except Exception as exc:  # 单个页面失败不影响整体
                        page = None
                        with self._lock:
                            self.errors.append({"url": url, "stage": "process",
                                                "message": "%s: %s" % (type(exc).__name__, exc),
                                                "status": 0})
                    processed += 1
                    if page is None:
                        self.stats["pages_failed"] += 1
                        continue
                    with self._lock:
                        page["id"] = "p%04d" % (len(self.pages) + 1)
                        self.pages.append(page)
                        self.stats["pages_crawled"] += 1
                        self.stats["urls_discovered"] += len(page["child_urls"])
                        # 记录本页 -> 子页面的链接关系（仅站内），携带锚文本
                        child_set = set(page["child_urls"])
                        anchor_text = {}
                        for link in page["links"]:
                            if link["url"] in child_set:
                                anchor_text.setdefault(link["url"], link["text"])
                        for child in page["child_urls"]:
                            if len(self.edges) < self.options.max_edges:
                                self.edges.append({
                                    "from": page["id"],
                                    "to": child,
                                    "text": anchor_text.get(child),
                                })
                        self.stats["edges"] = len(self.edges)

                    log("  [%d] %s -> %s%s" % (
                        page["status"], page["url"],
                        (page["title"] or "(无标题)")[:60],
                        " [渲染]" if page["render_mode"] != "static" else ""))
                    if page["error"]:
                        log("      错误: %s" % page["error"], 2)

                    next_depth = page["depth"] + 1
                    if next_depth <= max_depth:
                        for child in page["child_urls"]:
                            if child not in self.visited:
                                frontier.append((child, next_depth, page["url"]))
            depth += 1
            if self.options.max_depth and depth > self.options.max_depth:
                break

        duration = time.monotonic() - started
        log("抓取完成：%d 个页面，%d 个失败，%d 个跳过，用时 %.1fs" % (
            self.stats["pages_crawled"], self.stats["pages_failed"],
            self.stats["pages_skipped"], duration))

        if self.options.strip_boilerplate:
            boilerplate_lines = strip_boilerplate(
                self.pages, self.options.boilerplate_ratio,
                self.options.boilerplate_min_pages)
            self.stats["boilerplate_lines"] = boilerplate_lines
            if boilerplate_lines:
                log("模板行识别：%d 行（已写入 text_main，text 保持不变）" % boilerplate_lines)
            else:
                log("模板行识别：未启用（页面数少于 %d）" % self.options.boilerplate_min_pages)

        root_page = next((p for p in self.pages if p["url"] == self.start_url), None)
        if root_page is None:
            root_page = self.pages[0] if self.pages else None

        document = {
            "schema_version": SCHEMA_VERSION,
            "generator": {
                "name": SCRIPT_NAME,
                "version": SCRIPT_VERSION,
                "python": platform.python_version(),
            },
            "crawl": {
                "start_url": self.start_url,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "duration_seconds": round(duration, 2),
                "options": {k: v for k, v in vars(self.options).items()},
                "renderer": self._renderer_kind,
                "stats": dict(self.stats),
            },
            "site": {
                "host": self.start_host,
                "scheme": urllib.parse.urlsplit(self.start_url).scheme,
                "title": root_page["title"] if root_page else None,
                "description": (root_page["meta"].get("description") if root_page else None),
                "pages": len(self.pages),
            },
            "pages": self.pages,
            "link_graph": {
                "edges": self.edges,
                "truncated": len(self.edges) >= self.options.max_edges,
                "external_urls": self.external_urls,
            },
            "redirects": self.redirects,
            "robots": self.robots_info,
            "warnings": self.warnings,
            "warnings_truncated": self.warnings_truncated,
            "skipped": self.skipped,
            "errors": self.errors,
        }
        return document

    def close(self) -> None:
        if self.renderer is not None:
            try:
                self.renderer.close()
            except Exception:
                pass


def strip_boilerplate(pages: list, ratio: float = 0.5, min_pages: int = 5) -> int:
    """识别站内公共模板行（导航、页脚等）并写入每个页面的 text_main 字段。

    判定方式：某一行文本在超过 ratio 比例的页面中重复出现即视为模板行。
    原始 text 字段保持不变；页面数少于 min_pages 或未启用时，text_main 记为 None。
    返回识别出的模板行数量。
    """
    for page in pages:
        page["text_main"] = None
        page["text_main_length"] = None
        page["boilerplate_removed"] = 0
    if len(pages) < max(2, min_pages):
        return 0

    line_counts: Counter = Counter()
    page_lines: list = []
    for page in pages:
        lines = [line.strip() for line in (page.get("text") or "").split("\n") if line.strip()]
        page_lines.append(lines)
        line_counts.update(set(lines))

    threshold = max(2, int(len(pages) * ratio))
    boilerplate = {line for line, count in line_counts.items() if count >= threshold}

    for page, lines in zip(pages, page_lines):
        kept = [line for line in lines if line not in boilerplate]
        page["text_main"] = "\n".join(kept)
        page["text_main_length"] = len(page["text_main"])
        page["boilerplate_removed"] = len(lines) - len(kept)
    return len(boilerplate)


def parse_robots_directives(text: str, user_agent: str) -> tuple[float | None, list, list]:
    """解析 robots.txt，返回 (crawl-delay, disallow 列表, sitemap 列表)。

    标准库的 RobotFileParser 只接受整数 crawl-delay，且在 Python 3.12 中把通配分组
    放进 default_entry，crawl_delay() 读不到，因此这里自行解析。
    """
    token = user_agent.split("/")[0].strip().lower()
    groups: list = []
    sitemaps: list = []
    current = None
    last_key = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or last_key != "user-agent":
                current = {"agents": [], "delay": None, "disallow": []}
                groups.append(current)
            current["agents"].append(value.lower())
        elif key in ("disallow", "allow"):
            if current is not None and key == "disallow" and value:
                current["disallow"].append(value)
        elif key == "crawl-delay":
            if current is not None:
                try:
                    current["delay"] = float(value)
                except ValueError:
                    pass
        elif key == "sitemap" and value:
            sitemaps.append(value)
        last_key = key

    def matches(group) -> int:
        """0=不匹配 1=通配 2=精确匹配"""
        if any(agent == "*" for agent in group["agents"]):
            return 1
        if any(agent and (agent in token or token in agent) for agent in group["agents"]):
            return 2
        return 0

    delay = None
    disallowed: list = []
    best = 0
    for group in groups:
        score = matches(group)
        if score == 0:
            continue
        if score > best:
            best = score
            delay = group["delay"]
            disallowed = list(group["disallow"])
        elif score == best:
            if group["delay"] is not None:
                delay = group["delay"]
            disallowed += group["disallow"]
    return delay, list(dict.fromkeys(disallowed)), sitemaps


def parse_header_list(items: list | None) -> dict:
    headers = {}
    for item in items or []:
        if ":" in item:
            key, value = item.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers


# --------------------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="抓取一个网站的全部子页面，输出结构化 JSON（支持 JS 渲染站点）。",
        epilog="""示例:
  %(prog)s https://example.com
  %(prog)s https://example.com -o example.json --max-pages 300 --render always
  %(prog)s https://shop.example.com --scope domain --exclude "/cart" --exclude "/user"
  %(prog)s https://spa.example.com --render always --virtual-time-budget 10000 --wait-selector "#app"
""",
    )
    parser.add_argument("url", help="起始网址，例如 https://example.com")
    parser.add_argument("-o", "--output", help="输出 JSON 文件路径（默认 crawl_<host>.json）")
    parser.add_argument("--max-pages", type=int, default=200, help="最多抓取页面数，0 表示不限（默认 200）")
    parser.add_argument("--max-depth", type=int, default=4, help="最大链接深度，0 表示不限（默认 4）")
    parser.add_argument("--concurrency", type=int, default=5, help="并发抓取线程数（默认 5）")
    parser.add_argument("--timeout", type=float, default=20.0, help="单次请求超时秒数（默认 20）")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数（默认 2）")
    parser.add_argument("--delay", type=float, default=0.2, help="同一站点请求间隔秒数（默认 0.2）")
    parser.add_argument("--max-bytes", type=int, default=6 * 1024 * 1024, help="单页最大下载字节数")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="自定义 User-Agent")
    parser.add_argument("--header", action="append", help="附加请求头，格式 'Name: value'，可重复")
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="等同于 --robots ignore（保留兼容写法）")
    parser.add_argument("--robots", choices=["soft", "strict", "ignore"], default="soft",
                        help="robots.txt 处理方式：soft 禁止后仍抓取并写入警告（默认）、"
                             "strict 严格遵守并跳过、ignore 完全不读取 robots.txt")
    parser.add_argument("--use-sitemap", action="store_true", help="同时用 sitemap.xml 补充待抓取链接")
    parser.add_argument("--scope", choices=["host", "domain", "path", "any"], default="host",
                        help="抓取范围：host 同主机（默认）、domain 同主域、path 同路径前缀、any 全部")
    parser.add_argument("--include", action="append", help="仅抓取匹配该正则的 URL，可重复")
    parser.add_argument("--exclude", action="append", help="排除匹配该正则的 URL，可重复")
    parser.add_argument("--allow-ext", action="append",
                        help="允许跟进这些扩展名（如 .pdf 也当作页面抓取）")
    parser.add_argument("--max-edges", type=int, default=20000, help="链接关系图最多记录多少条边")
    parser.add_argument("--max-images", type=int, default=200, help="每页最多保留多少张图片")
    parser.add_argument("--max-tables", type=int, default=20, help="每页最多保留多少个表格")
    parser.add_argument("--max-text-chars", type=int, default=200000,
                        help="每页正文最大字符数，0 表示不截断（默认 200000）")
    parser.add_argument("--no-tables", dest="include_tables", action="store_false",
                        help="不提取表格")
    parser.add_argument("--no-strip-boilerplate", dest="strip_boilerplate",
                        action="store_false",
                        help="不做模板行识别（默认会识别站内公共导航/页脚，写入 text_main）")
    parser.add_argument("--boilerplate-ratio", type=float, default=0.5,
                        help="同一行出现在多少比例的页面中即视为模板行（默认 0.5）")
    parser.add_argument("--boilerplate-min-pages", type=int, default=5,
                        help="页面数少于该值时不识别模板行（默认 5）")
    parser.add_argument("--dedupe-content", action="store_true",
                        help="内容重复的页面不再继续展开其链接")
    parser.add_argument("--render", choices=["auto", "always", "never"], default="auto",
                        help="JS 渲染策略：auto 自动判断（默认）、always 总是渲染、never 只抓静态 HTML")
    parser.add_argument("--render-engine", choices=["auto", "playwright", "chrome"], default="auto",
                        help="渲染引擎：auto 优先 Playwright 否则本机浏览器、playwright、chrome")
    parser.add_argument("--render-min-text", type=int, default=30,
                        help="auto 模式下内容权重低于该值且含脚本时启用渲染（中文按字、英文按词计，默认 30）")
    parser.add_argument("--virtual-time-budget", type=int, default=6000,
                        help="无头浏览器等待脚本执行的虚拟时间预算毫秒（默认 6000）")
    parser.add_argument("--wait-selector", help="Playwright 渲染时等待的 CSS 选择器")
    parser.add_argument("--scroll", action="store_true", help="Playwright 渲染时滚动页面以触发懒加载")
    parser.add_argument("--browser-path", help="指定 Chrome/Edge 可执行文件路径")
    parser.add_argument("--browser-arg", action="append", help="附加给浏览器的命令行参数，可重复")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数，0 表示压缩输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出错误信息")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    parser.add_argument("--version", action="version",
                        version="%s %s (schema %s)" % (SCRIPT_NAME, SCRIPT_VERSION, SCHEMA_VERSION))
    return parser


def default_output_name(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or "site"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "crawl_%s_%s.json" % (re.sub(r"[^A-Za-z0-9._-]", "_", host), stamp)


def write_json(document: dict, path: str, indent: int) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=indent if indent > 0 else None,
                         separators=(",", ":") if indent <= 0 else None)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.write("\n")
    os.replace(temp_path, path)


def main(argv: list[str] | None = None) -> int:
    global _LOG_LEVEL
    for stream in (sys.stdout, sys.stderr):
        try:
            # 管道/重定向时统一输出 UTF-8，终端下保留本机编码以免乱码
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = build_parser()
    options = parser.parse_args(argv)
    if options.ignore_robots:
        options.robots = "ignore"
    _LOG_LEVEL = 0 if options.quiet else (2 if options.verbose else 1)

    try:
        crawler = Crawler(options)
    except ValueError as exc:
        sys.stderr.write("错误: %s\n" % exc)
        return 2
    except Exception as exc:
        sys.stderr.write("初始化失败: %s: %s\n" % (type(exc).__name__, exc))
        return 2

    output = options.output or default_output_name(crawler.start_url)
    exit_code = 0
    try:
        document = crawler.run()
        write_json(document, output, options.indent)
        stats = document["crawl"]["stats"]
        print("输出文件: %s" % os.path.abspath(output))
        print("页面数: %d（失败 %d，跳过 %d，JS 渲染 %d）" % (
            stats["pages_crawled"], stats["pages_failed"],
            stats["pages_skipped"], stats["rendered_pages"]))
        print("链接关系: %d 条，外链域名外 URL %d 个" % (
            len(document["link_graph"]["edges"]),
            len(document["link_graph"]["external_urls"])))
        if stats.get("robots_disallowed_pages"):
            if options.robots == "soft":
                print("警告: %d 个页面被 robots.txt 禁止，按软限制策略已抓取，"
                      "详情见输出文件的 warnings 字段。" % stats["robots_disallowed_pages"])
            else:
                print("robots.txt: 已按严格策略跳过 %d 个被禁止的地址。" %
                      stats["robots_disallowed_pages"])
        if stats["pages_crawled"] == 0:
            print("提示: 未抓到任何页面，请检查网址、robots.txt 或网络连通性。")
            exit_code = 1
    except KeyboardInterrupt:
        sys.stderr.write("已中断。\n")
        exit_code = 130
    except Exception as exc:
        sys.stderr.write("运行失败: %s: %s\n" % (type(exc).__name__, exc))
        exit_code = 1
    finally:
        crawler.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
