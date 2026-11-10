# -*- coding: utf-8 -*-
"""Minimal Chrome DevTools Protocol client (standard library only).

Why hand-rolled instead of Selenium/Playwright:

* no third-party dependency on the target machine;
* the browser is a *real* Edge/Chrome window with a persistent profile, which is
  what makes institutional SSO and publisher pages work;
* every call has an absolute deadline, so a hung page cannot hang the run.

The WebSocket implementation covers only what CDP needs: text frames, ping/pong,
fragmentation and close. It is not a general-purpose WebSocket library.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import UA_EDGE, ensure_dir

DEFAULT_PORT = 9222
HTTP_TIMEOUT = 8.0
CALL_TIMEOUT = 25.0

CONSENT_JS = r"""
(() => {
  const BOXES = [
    '#onetrust-consent-sdk', '#onetrust-banner-sdk', '#onetrust-pc-sdk', '.onetrust-pc-dark-filter',
    '#CybotCookiebotDialog', '#CookiebotWidget', '.CybotCookiebotDialogBodyUnderlay',
    '#qc-cmp2-container', '.qc-cmp2-container', '.qc-cmp2-ui',
    '.osano-cm-window', '#osano-cm-window',
    '#didomi-host', '#didomi-popup', '.didomi-popup-container', '.didomi-notice-popup',
    '#truste-consent-track', '.truste_box_overlay', '.truste_overlay', '#consent_blackbar',
    '#usercentrics-root', '#usercentrics-cmp-ui', '#uc-center-container',
    '[id^="sp_message_container"]', '.message-component', '.sp_veil',
    '#cookiescript_injected', '#cookie-law-info-bar', '.cli-modal-backdrop',
    '#cookiebanner', '.cookie-banner', '#cookie-banner', '.cookie-notice', '#cookie-notice',
    '#cookiebar', '.cookiebar', '.cc-window', '.cc-banner',
    '.cky-consent-container', '.cky-overlay', '.cky-preference-center', '#cookieyes',
    '.cmplz-cookiebanner', '#cmplz-cookiebanner-container', '#cmplz-manage-consent',
    '#iubenda-cs-banner', '.iubenda-cs-container', '#axeptio_overlay',
    '.fc-consent-root', '.fc-dialog-overlay', '#klaro', '.klaro',
    '#termly-code-snippet-support', '.termly-consent-banner',
    '#cookie-consent', '.cookie-consent', '.cookie-consent-banner', '#gdpr-banner',
    '#moove_gdpr_cookie_info_bar', '#hs-eu-cookie-confirmation', '#BorlabsCookieBox',
    '.eu-cookie-compliance-banner', '.eupopup',
    'iframe[src*="consent"]', 'iframe[id*="sp_message"]', 'iframe[src*="cmp"]',
    'div[class*="cookie-consent" i]', 'div[id*="cookie-consent" i]',
    'div[class*="consent-banner" i]', 'div[id*="consent-banner" i]',
    '[role="dialog"][aria-label*="cookie" i]'
  ];
  const BTN = 'button, a[role="button"], input[type="button"], input[type="submit"], [role="button"]';
  const ACCEPT = /^\s*(accept(\s+(all|necessary|selected|recommended|cookies))?(\s+cookies)?|allow(\s+(all|necessary|cookies)){1,2}|agree(\s+to\s+all)?(\s+and\s+continue)?|i\s+(agree|accept|understand)|got\s+it|ok(ay)?|understood|consent|同意(全部|所有)?|接受(全部|所有)?|我(已)?(同意|接受|知道了)|知道了|明白了|确定|确认|允许(全部|所有)?|同意并(继续|关闭)|接受并(继续|关闭))\s*$/i;
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim().replace(/[\s»>→›«·.…!]+$/, '').trim(); }
  let rootsCache = null, rootsAt = -1;
  function roots() {
    const now = Date.now();
    if (rootsCache && now - rootsAt < 3000) return rootsCache;
    const out = [document];
    try {
      for (const el of document.querySelectorAll('*')) {
        if (el.shadowRoot) { out.push(el.shadowRoot); if (out.length >= 20) break; }
      }
    } catch (e) {}
    rootsCache = out; rootsAt = now; return out;
  }
  function visible(el) {
    try {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      const r = el.getBoundingClientRect();
      return r.width >= 30 && r.height >= 16;
    } catch (e) { return false; }
  }
  const clicked = new WeakSet();
  function dismiss() {
    let touched = false, processed = 0;
    for (const sel of BOXES) {
      if (processed >= 50) break;
      let found = [];
      for (const r of roots()) {
        try { found.push(...Array.from(r.querySelectorAll(sel)).slice(0, 4)); } catch (e) {}
        if (found.length >= 4) break;
      }
      for (const box of found) {
        if (processed++ >= 50) break;
        if (!visible(box)) continue;
        touched = true;
        if (box.tagName !== 'IFRAME' && !clicked.has(box)) {
          let done = false;
          try {
            for (const b of box.querySelectorAll(BTN)) {
              if (ACCEPT.test(norm(b.innerText || b.value || b.getAttribute('aria-label')))) {
                b.click(); clicked.add(box); done = true; break;
              }
            }
          } catch (e) {}
          if (!done) {
            setTimeout(() => { try { if (visible(box)) box.style.setProperty('display', 'none', 'important'); } catch (e) {} }, 800);
          }
        }
      }
    }
    if (touched) {
      try {
        for (const el of [document.documentElement, document.body]) {
          if (el && ['hidden', 'clip'].includes(getComputedStyle(el).overflow)) {
            el.style.setProperty('overflow', 'auto', 'important');
          }
        }
      } catch (e) {}
    }
    return touched;
  }
  try { dismiss(); } catch (e) {}
  setInterval(() => { try { dismiss(); } catch (e) {} }, 1500);
  return true;
})();
"""


class CDPError(RuntimeError):
    pass


class CDPUnavailable(CDPError):
    pass


class _WebSocket:
    """Tiny RFC6455 client: enough for ws://127.0.0.1 CDP sessions."""

    def __init__(self, host: str, port: int, path: str, timeout: float = CALL_TIMEOUT):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPUnavailable("WebSocket handshake closed early")
            header += chunk
        head, _, rest = header.partition(b"\r\n\r\n")
        self._buf = rest
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in first:
            raise CDPUnavailable(f"WebSocket handshake failed: {first}")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if expected.lower() not in head.decode("latin-1").lower():
            raise CDPUnavailable("WebSocket accept key mismatch")

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise CDPUnavailable("WebSocket closed")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        header = bytearray([0x80 | opcode])
        header.append(0x80 | len(payload))
        mask = os.urandom(4)
        header.extend(mask)
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_text(self, timeout: float | None = None) -> str:
        if timeout is not None:
            self.sock.settimeout(timeout)
        fragments = bytearray()
        while True:
            b1, b2 = self._read(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            payload = self._read(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                raise CDPUnavailable("WebSocket closed by peer")
            if opcode == 0x9:  # ping
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            fragments.extend(payload)
            if fin:
                return fragments.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._send_control(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ------------------------------------------------------------------ HTTP API
def _http_json(port: int, path: str, method: str = "GET", timeout: float = HTTP_TIMEOUT):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        raw = fh.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def is_alive(port: int = DEFAULT_PORT) -> bool:
    try:
        version(port)
        return True
    except Exception:
        return False


def version(port: int = DEFAULT_PORT) -> dict:
    return _http_json(port, "/json/version", timeout=3)


def list_tabs(port: int = DEFAULT_PORT) -> list:
    return _http_json(port, "/json/list", timeout=5)


def page_tabs(port: int = DEFAULT_PORT) -> list:
    return [t for t in list_tabs(port) if t.get("type") == "page"]


def new_tab(url: str = "about:blank", port: int = DEFAULT_PORT) -> dict:
    path = "/json/new?" + urllib.parse.quote(url, safe=":/?=&%")
    try:
        return _http_json(port, path, method="PUT")
    except urllib.error.HTTPError:
        return _http_json(port, path, method="GET")


def close_tab(tab_id: str, port: int = DEFAULT_PORT) -> bool:
    try:
        _http_json(port, f"/json/close/{tab_id}")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- session
class CDP:
    def __init__(self, ws_url: str, timeout: float = CALL_TIMEOUT, consent: bool = True, tab_id: str = ""):
        from urllib.parse import urlsplit

        parts = urlsplit(ws_url)
        self.ws_url = ws_url
        self.timeout = float(timeout)
        self.tab_id = tab_id
        self.events: list[dict] = []
        self._id = 0
        self._closed = False
        # Global pacing for this tab: reloading result pages back-to-back is the
        # quickest route to a bot challenge. Callers may raise this.
        self.min_nav_interval = 0.0
        self._last_nav = 0.0
        self.ws = _WebSocket(parts.hostname or "127.0.0.1", parts.port or 80, parts.path + ("?" + parts.query if parts.query else ""), timeout)
        self.consent = consent
        if consent:
            try:
                self.call("Page.enable", timeout=8)
                self.call("Page.addScriptToEvaluateOnNewDocument", {"source": CONSENT_JS}, timeout=8)
            except CDPError:
                pass

    # -- low level -----------------------------------------------------
    def call(self, method: str, params: dict | None = None, timeout: float | None = None):
        if self._closed:
            raise CDPUnavailable("session closed")
        budget = float(timeout if timeout is not None else self.timeout)
        deadline = time.time() + budget
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise CDPError(f"timeout waiting for {method} ({budget:.0f}s)")
            try:
                raw = self.ws.recv_text(timeout=max(0.5, remain))
            except socket.timeout as exc:
                raise CDPError(f"timeout receiving {method}") from exc
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != mid:
                self.events.append(msg)
                if len(self.events) > 200:
                    self.events = self.events[-200:]
                continue
            if "error" in msg:
                raise CDPError(f"{method}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result", {})

    def eval(self, expression: str, timeout: float | None = None, await_promise: bool = True):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            timeout,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise CDPError("JS error: " + str(detail.get("text") or detail.get("exception", {}).get("description"))[:200])
        return (result.get("result") or {}).get("value")

    # -- page helpers --------------------------------------------------
    def navigate(self, url: str, settle: float = 1.0, load_timeout: float = 35.0) -> "CDP":
        if self.min_nav_interval > 0:
            wait = self.min_nav_interval - (time.time() - self._last_nav)
            if wait > 0:
                time.sleep(wait)
        self._last_nav = time.time()
        try:
            self.call("Page.navigate", {"url": url}, timeout=30)
        except CDPError:
            pass  # a timed-out navigate often still loads; keep polling
        deadline = time.time() + load_timeout
        while time.time() < deadline:
            try:
                if self.eval("document.readyState", timeout=5) == "complete":
                    break
            except CDPError:
                pass
            time.sleep(0.25)
        if settle > 0:
            time.sleep(settle)
        return self

    def wait_for(self, expression: str, timeout: float = 25.0, interval: float = 0.4) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval(expression, timeout=4):
                    return True
            except CDPError:
                pass
            time.sleep(interval)
        return False

    def text(self, timeout: float | None = None) -> str:
        value = self.eval("(document.body && document.body.innerText) || ''", timeout=timeout)
        return value or ""

    def html(self, timeout: float | None = None) -> str:
        value = self.eval("document.documentElement ? document.documentElement.outerHTML : ''", timeout=timeout)
        return value or ""

    def url(self) -> str:
        try:
            return self.eval("location.href", timeout=4) or ""
        except CDPError:
            return ""

    def title(self) -> str:
        try:
            return self.eval("document.title", timeout=4) or ""
        except CDPError:
            return ""

    def set_user_agent(self, ua: str) -> None:
        try:
            self.call("Network.enable", timeout=6)
            self.call("Network.setUserAgentOverride", {"userAgent": ua, "acceptLanguage": "en-US,en;q=0.9"}, timeout=6)
        except CDPError:
            pass

    def cookies(self) -> list:
        try:
            self.call("Network.enable", timeout=6)
            return (self.call("Network.getAllCookies", timeout=8) or {}).get("cookies") or []
        except CDPError:
            return []

    def cookie_header(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self.cookies() if c.get("name"))

    def dismiss_consent(self) -> bool:
        try:
            return bool(self.eval(CONSENT_JS, timeout=8, await_promise=False))
        except CDPError:
            return False

    def type_into(self, selector: str, text: str) -> bool:
        expr = r"""
        (() => {
          const sel = %s, value = %s;
          const el = document.querySelector(sel);
          if (!el) return false;
          el.focus();
          try {
            if ('value' in el) {
              const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
              setter.call(el, value);
            } else {
              el.textContent = value;
              document.execCommand('insertText', false, value);
            }
          } catch (e) { el.textContent = value; }
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          return true;
        })()
        """ % (json.dumps(selector), json.dumps(text))
        try:
            return bool(self.eval(expr, timeout=8))
        except CDPError:
            return False

    def click(self, selector: str) -> bool:
        expr = "(() => { const el = document.querySelector(%s); if (!el) return false; el.click(); return true; })()" % json.dumps(selector)
        try:
            return bool(self.eval(expr, timeout=8))
        except CDPError:
            return False

    def click_text(self, selector: str, text: str) -> bool:
        expr = r"""
        (() => {
          const els = Array.from(document.querySelectorAll(%s));
          const target = els.find(e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().toLowerCase().includes(%s));
          if (!target) return false;
          target.click(); return true;
        })()
        """ % (json.dumps(selector), json.dumps(text.lower()))
        try:
            return bool(self.eval(expr, timeout=8))
        except CDPError:
            return False

    def close(self) -> None:
        self._closed = True
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# -------------------------------------------------------------------- browser
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
LAUNCH_ARGS = [
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    "--deny-permission-prompts",
    "--disable-notifications",
    "--noerrdialogs",
    "--disable-session-crashed-bubble",
    "--hide-crash-restore-bubble",
    "--disable-prompt-on-repost",
    "--disable-search-engine-choice-screen",
    "--disable-features=Translate,TranslateUI",
    "--window-size=1366,900",
]


def find_browser() -> str | None:
    env = os.environ.get("LQ_BROWSER") or os.environ.get("CHROME_PATH")
    if env and pathlib.Path(env).exists():
        return env
    for path in EDGE_PATHS + CHROME_PATHS:
        if pathlib.Path(path).exists():
            return path
    return None


class Browser:
    """Owns one browser process on one debugging port."""

    def __init__(self, port: int = DEFAULT_PORT, workdir=".", log=None, ua: str = UA_EDGE):
        self.port = int(port)
        # Must be absolute: Chromium resolves --user-data-dir relative to its own
        # working directory, not the caller's, which silently lands the profile
        # in a read-only place and makes the launch fail.
        self.workdir = pathlib.Path(workdir).expanduser().resolve()
        self.log = log
        self.ua = ua
        self.proc = None
        self.profile = ensure_dir(self.workdir / ".work_lq" / f"browser_profile_{self.port}")
        self.log_path = ensure_dir(self.workdir / ".work_lq") / f"browser_{self.port}.log"
        self._logfh = None

    def alive(self) -> bool:
        return is_alive(self.port)

    def ensure(self, timeout: float = 40.0) -> "Browser":
        if self.alive():
            return self
        exe = find_browser()
        if not exe:
            raise CDPUnavailable("Edge/Chrome not found; set LQ_BROWSER to the executable path")
        args = [
            exe,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.profile}",
            f"--user-agent={self.ua}",
        ] + LAUNCH_ARGS + ["about:blank"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._logfh = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            args, stdout=self._logfh, stderr=self._logfh, creationflags=creationflags
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.alive():
                return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.3)
        tail = ""
        try:
            self._logfh.flush()
            tail = self.log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            tail = " | ".join(tail)
        except Exception:
            tail = ""
        code = self.proc.poll() if self.proc else None
        raise CDPUnavailable(
            f"browser did not expose CDP on port {self.port} within {timeout:.0f}s "
            f"(exit={code}, log={self.log_path}): {tail[:400]}"
        )

    def open(self, url: str = "about:blank") -> CDP:
        self.ensure()
        tabs = page_tabs(self.port)
        tab = tabs[0] if tabs else new_tab("about:blank", self.port)
        cdp = CDP(tab["webSocketDebuggerUrl"], consent=True, tab_id=tab.get("id", ""))
        cdp.set_user_agent(self.ua)
        if url and url != "about:blank":
            cdp.navigate(url)
        return cdp

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


def wait_cdp(port: int = DEFAULT_PORT, seconds: float = 30.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_alive(port):
            return True
        time.sleep(0.3)
    return False


def quit_browser(port: int = DEFAULT_PORT) -> bool:
    """Ask the browser to exit through CDP (no taskkill, no profile corruption)."""
    try:
        info = version(port)
        ws_url = info.get("webSocketDebuggerUrl")
    except Exception:
        return False
    if not ws_url:
        return False
    try:
        session = CDP(ws_url, consent=False)
        try:
            session.call("Browser.close", timeout=6)
        except CDPError:
            pass
        session.close()
        return True
    except Exception:
        return False
