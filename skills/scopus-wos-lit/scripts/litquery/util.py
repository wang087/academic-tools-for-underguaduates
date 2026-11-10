# -*- coding: utf-8 -*-
"""Shared helpers: UTF-8 streams, JSON IO, hashing, logging, HTTP client.

Standard library only. The HTTP client exists because this project must run on
locked-down Windows machines where ``requests`` is not installed, and because
every outbound call needs to be auditable (URL, status, bytes, cache hit).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib

VERSION = "1.0.0"

# UA strings mandated by the task. Edge/Windows is preferred; Android Chrome is
# the fallback used when a site serves a degraded layout to the desktop UA.
UA_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0"
)
UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/138.0.0.0 Mobile Safari/537.36"
)
USER_AGENTS = [UA_EDGE, UA_ANDROID]


# ---------------------------------------------------------------- IO helpers
def setup_stdout() -> None:
    """Force UTF-8 on stdout/stderr so Chinese never dies on a GBK console."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            stream.flush()
        except Exception:
            pass
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


def ensure_dir(path) -> pathlib.Path:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, default=None):
    p = pathlib.Path(path)
    if not p.exists():
        if default is not None:
            return default
        raise FileNotFoundError(str(p))
    with p.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, payload, indent: int = 2) -> pathlib.Path:
    p = pathlib.Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def write_text(path, text: str) -> pathlib.Path:
    p = pathlib.Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, p)
    return p


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe slug that keeps CJK characters."""
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return (text[:max_len] or "untitled").lower()


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def strip_html(html: str) -> str:
    """Minimal, dependency-free HTML to text (good enough for abstracts)."""
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_number(text) -> int | None:
    """Parse counts like '1,234', '1.234', '12 345', '1,234 results'."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).replace("\u00a0", " ").replace("\u202f", " ")
    m = re.search(r"\d[\d.,\s]*", s)
    if not m:
        return None
    raw = re.sub(r"\s", "", m.group(0))
    # Decide whether the last separator is a thousands separator or a decimal.
    if re.search(r"[.,]\d{3}$", raw) and raw.count(raw[-4]) >= 1:
        raw = raw[:-4].replace(",", "").replace(".", "") + raw[-4:]
    candidate = re.sub(r"[.,]", "", raw)
    try:
        return int(candidate)
    except ValueError:
        return None


# ------------------------------------------------------------------- logging
class Log:
    """Tiny logger: human lines on stderr, machine lines to a JSONL file."""

    def __init__(self, path=None, verbose: bool = True):
        self.verbose = verbose
        self.path = pathlib.Path(path) if path else None
        if self.path:
            ensure_dir(self.path.parent)

    def _emit(self, tag: str, message: str) -> None:
        if self.verbose:
            print(f"{tag} {message}", file=sys.stderr, flush=True)

    def info(self, message: str) -> None:
        self._emit("[OK]", message)

    def warn(self, message: str) -> None:
        self._emit("[WARN]", message)

    def error(self, message: str) -> None:
        self._emit("[STOP]", message)

    def debug(self, message: str) -> None:
        if self.verbose:
            self._emit("[..]", message)

    def event(self, kind: str, **fields) -> None:
        if not self.path:
            return
        payload = {"ts": now_iso(), "kind": kind}
        payload.update(fields)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class Response:
    def __init__(self, status: int, headers: dict, body: bytes, url: str):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def text(self) -> str:
        ctype = (self.headers.get("content-type") or "").lower()
        enc = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            enc = m.group(1)
        try:
            return self.body.decode(enc, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)

    def __repr__(self) -> str:
        return f"<Response {self.status} {len(self.body)}B {self.url[:80]}>"


class HttpClient:
    """Auditable HTTP client with pacing, retries and an on-disk GET cache."""

    def __init__(
        self,
        cache_dir=None,
        timeout: float = 30.0,
        retries: int = 2,
        min_interval: float = 0.6,
        log: Log | None = None,
        ua: str = UA_EDGE,
        cache_ttl: float = 7 * 86400,
        verify: bool = True,
    ):
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            ensure_dir(self.cache_dir)
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.log = log or Log(verbose=False)
        self.ua = ua
        self.cache_ttl = cache_ttl
        self.verify = verify
        self._last: dict[str, float] = {}
        self._ctx = ssl.create_default_context()
        if not verify:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE
        self.requests = 0
        self.cache_hits = 0

    # -- cache ---------------------------------------------------------
    def _cache_path(self, key: str):
        if not self.cache_dir:
            return None
        return self.cache_dir / key[:2] / (key + ".json")

    def _cache_get(self, key: str):
        p = self._cache_path(key)
        if not p or not p.exists():
            return None
        try:
            age = time.time() - p.stat().st_mtime
            if age > self.cache_ttl:
                return None
            payload = read_json(p)
            body = base64.b64decode(payload["body_b64"])
            return Response(payload["status"], payload.get("headers", {}), body, payload.get("url", ""))
        except Exception:
            return None

    def _cache_put(self, key: str, resp: Response) -> None:
        p = self._cache_path(key)
        if not p:
            return
        try:
            write_json(
                p,
                {
                    "status": resp.status,
                    "headers": resp.headers,
                    "url": resp.url,
                    "fetched_at": now_iso(),
                    "body_b64": base64.b64encode(resp.body).decode("ascii"),
                },
            )
        except Exception:
            pass

    # -- core ----------------------------------------------------------
    def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        data: bytes | str | None = None,
        timeout: float | None = None,
        use_cache: bool = True,
        accept: str | None = None,
        retries: int | None = None,
    ) -> Response:
        if isinstance(data, str):
            data = data.encode("utf-8")
        hdrs = {
            "User-Agent": self.ua,
            "Accept": accept or "*/*",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if headers:
            hdrs.update(headers)

        key = sha256_text(method + "\n" + url + "\n" + (data or b"").hex())
        if method == "GET" and use_cache:
            hit = self._cache_get(key)
            if hit is not None:
                self.cache_hits += 1
                self.log.event("http", url=url, status=hit.status, cache=True, bytes=len(hit.body))
                return hit

        last_err = None
        max_retries = self.retries if retries is None else max(0, int(retries))
        for attempt in range(max_retries + 1):
            self._pace(url)
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                self.requests += 1
                with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ctx) as fh:
                    raw = fh.read()
                    resp_headers = {k.lower(): v for k, v in fh.headers.items()}
                    body = self._decompress(raw, resp_headers.get("content-encoding", ""))
                    resp = Response(fh.status, resp_headers, body, fh.geturl())
            except urllib.error.HTTPError as exc:
                raw = exc.read() if hasattr(exc, "read") else b""
                body = self._decompress(raw, exc.headers.get("Content-Encoding", ""))
                resp = Response(
                    exc.code,
                    {k.lower(): v for k, v in (exc.headers or {}).items()},
                    body,
                    getattr(exc, "url", url),
                )
            except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
                last_err = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise HttpError(f"{type(exc).__name__}: {exc}", None, url) from exc

            self.log.event(
                "http", url=url, status=resp.status, cache=False, bytes=len(resp.body), attempt=attempt
            )
            if resp.status in (429, 500, 502, 503, 504) and attempt < max_retries:
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if (retry_after or "").isdigit() else 2.0 * (attempt + 1)
                time.sleep(min(delay, 20))
                continue
            if method == "GET" and use_cache and 200 <= resp.status < 300:
                self._cache_put(key, resp)
            return resp

        raise HttpError(f"request failed after retries: {last_err}", None, url)

    def _pace(self, url: str) -> None:
        if self.min_interval <= 0:
            return
        host = urllib.parse.urlsplit(url).netloc.lower()
        last = self._last.get(host, 0.0)
        wait = self.min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    @staticmethod
    def _decompress(raw: bytes, encoding: str) -> bytes:
        enc = (encoding or "").lower()
        try:
            if "gzip" in enc:
                return gzip.decompress(raw)
            if "deflate" in enc:
                return zlib.decompress(raw)
        except Exception:
            return raw
        return raw

    def get(self, url: str, **kwargs) -> Response:
        return self.request(url, "GET", **kwargs)

    def get_json(self, url: str, headers: dict | None = None, **kwargs):
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        resp = self.request(url, "GET", headers=hdrs, **kwargs)
        if not (200 <= resp.status < 300):
            raise HttpError(f"HTTP {resp.status} for {url}: {resp.text[:200]}", resp.status, url)
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise HttpError(f"non-JSON response from {url}: {resp.text[:160]}", resp.status, url) from exc


def qs(base: str, params: dict) -> str:
    """Append query parameters, dropping None values."""
    clean = {k: v for k, v in params.items() if v is not None}
    sep = "&" if urllib.parse.urlsplit(base).query else "?"
    return base + sep + urllib.parse.urlencode(clean, doseq=True, quote_via=urllib.parse.quote)


def truncate(text: str, width: int = 90) -> str:
    text = norm_space(text)
    return text if len(text) <= width else text[: width - 1] + "…"
