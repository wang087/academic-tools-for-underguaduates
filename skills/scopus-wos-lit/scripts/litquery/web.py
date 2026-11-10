# -*- coding: utf-8 -*-
"""Scopus / Web of Science web search through a real browser (CDP).

What is verified in this environment (2026-10, Edge 156, no institutional sign-in):

* Scopus: a plain URL search works; the page reports ``N documents found``;
  result rows are server-rendered and expose title/authors/source/year/citations;
  a document page exposes the DOI and abstract without signing in.
* Web of Science: the search UI lives at ``webofscience.com`` which redirects to
  the regional host ``webofscience.clarivate.cn``; the advanced-search textarea
  is ``#advancedSearchInputArea`` and the run button is ``[data-ta="run-search"]``.
  The hit count is readable, but result records stay empty placeholders until an
  entitled user signs in.

The module therefore separates *count* (always available) from *records*
(available on Scopus, or on WoS after sign-in). Callers must not pretend an
empty record list means "no papers".
"""
from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field

from .cdp import CDP

SCOPUS_SEARCH = "https://www.scopus.com/results/results.uri"
SCOPUS_PAPER = "https://www.scopus.com/pages/publications/"
WOS_ADVANCED = "https://www.webofscience.com/wos/woscc/advanced-search"
WOS_SUMMARY = "https://www.webofscience.com/wos/woscc/summary/"

DEFAULT_SELECTORS = {
    "scopus": {
        "search_inputs": [
            "input[data-testid='search-input']",
            "textarea[data-testid='search-input']",
            "#searchterm",
            "input[name='searchterm']",
            "[contenteditable='true']",
        ],
        "search_buttons": [
            "button[data-testid='search-button']",
            "button[type='submit']",
            "button[aria-label*='Search' i]",
        ],
        "result_row": "tr",
        "record_link": "a[href*='/pages/publications/']",
    },
    "wos": {
        "query_input": "#advancedSearchInputArea",
        "run_button": "[data-ta='run-search']",
        "record": "app-record",
    },
}


@dataclass
class PageState:
    engine: str
    url: str
    title: str
    count: int | None
    state: str  # ok | empty | login | challenge | unknown
    evidence: str = ""
    records_available: bool = False

    def as_dict(self) -> dict:
        return {
            "engine": self.engine,
            "url": self.url,
            "title": self.title,
            "count": self.count,
            "state": self.state,
            "evidence": self.evidence,
            "records_available": self.records_available,
        }


def _load_selectors(workdir=None) -> dict:
    merged = json.loads(json.dumps(DEFAULT_SELECTORS))
    if not workdir:
        return merged
    path = pathlib.Path(workdir) / "selectors.json"
    if not path.exists():
        return merged
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return merged
    for engine, values in (override or {}).items():
        if engine in merged and isinstance(values, dict):
            merged[engine].update(values)
    return merged


def normalize_count_text(text: str) -> str:
    return (text or "").replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")


def count_from_text(text: str, engine: str) -> tuple[int | None, str]:
    """Extract the hit count. Returns (count, evidence)."""
    t = normalize_count_text(text)
    low = t.lower()
    if engine == "scopus":
        if "no documents found" in low:
            return 0, "no documents found"
        patterns = [
            r"([\d][\d,\.\s]{0,15})\s+documents?\s+found",
            r"([\d][\d,\.\s]{0,15})\s+document\s+results",
            r"results\s*1\s*through\s*\d+\s*of\s*([\d,\.]+)",
        ]
    else:
        if "no results found" in low:
            return 0, "no results found"
        patterns = [
            r"([\d][\d,\.\s]{0,15})\s+results?\s+from\s+web\s+of\s+science",
            r"([\d][\d,\.\s]{0,15})\s+results?",
        ]
    for pattern in patterns:
        m = re.search(pattern, t, re.I)
        if m:
            raw = re.sub(r"\s", "", m.group(1)).strip(".,")
            # Drop a trailing thousands separator artefact.
            digits = re.sub(r"[^\d]", "", raw)
            if digits:
                return int(digits), m.group(0)[:80]
    return None, ""


def detect_state(text: str, url: str, title: str = "") -> tuple[str, str]:
    low = (text or "").lower()
    title_low = (title or "").lower()
    url_low = (url or "").lower()
    if "just a moment" in title_low or "verify you are human" in low or "captcha" in low or "cf-challenge" in url_low:
        return "challenge", "anti-bot challenge page"
    # Elsevier SSO: id.elsevier.com/as/authorization.oauth2?...&prompt=login
    if (
        "id.elsevier.com" in url_low
        or "authorization.oauth2" in url_low
        or "prompt=login" in url_low
        or "/signin" in url_low
        or "/login" in url_low
        or "shibboleth" in url_low
    ):
        return "login", "redirected to sign-in"
    return "ok", ""


SCOPUS_SORT = {
    "cited": "cp-f",
    "date": "plf-f",
    "relevance": "r-f",
}


def scopus_url(query: str, page: int = 1, limit: int = 10, sort: str = "cp-f") -> str:
    params = {"src": "s", "sot": "b", "sdt": "b", "s": query, "origin": "searchbasic"}
    if limit:
        params["limit"] = str(limit)
    if sort:
        params["sort"] = SCOPUS_SORT.get(sort, sort)
    if page and page > 1:
        params["page"] = str(page)
    return SCOPUS_SEARCH + "?" + urllib.parse.urlencode(params)


def parse_scopus_rows(cdp: CDP) -> list[dict]:
    rows = cdp.eval(
        """(() => {
          const out = [];
          document.querySelectorAll('tr').forEach(tr => {
            const a = tr.querySelector("a[href*='/pages/publications/']");
            if (!a) return;
            const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
            if (cells.length < 5) return;
            const m = (a.getAttribute('href') || '').match(/publications\\/(\\d+)/);
            out.push({cells: cells, title: (a.innerText || '').trim(), href: a.href,
                      scopus_id: m ? m[1] : '', row_text: (tr.innerText || '').slice(0, 400)});
          });
          return out;
        })()"""
    ) or []
    records = []
    for row in rows:
        cells = row.get("cells") or []
        title = row.get("title") or (cells[1] if len(cells) > 1 else "")
        if not title:
            continue
        authors = cells[2] if len(cells) > 2 else ""
        source = cells[3] if len(cells) > 3 else ""
        year = cells[4] if len(cells) > 4 else ""
        citations = cells[5] if len(cells) > 5 else "0"
        journal, volume, issue = _split_source(source)
        row_text = row.get("row_text") or ""
        head = row_text.lower()[:40]
        doc_type = "review" if "review" in head else "article"
        records.append(
            {
                "scopus_id": row.get("scopus_id"),
                "title": title,
                "authors_text": authors,
                "first_author": _first_author(authors),
                "journal": journal,
                "volume": volume,
                "issue": issue,
                "year": _year(year),
                "cited_by": _int(citations),
                "url": row.get("href") or (SCOPUS_PAPER + str(row.get("scopus_id") or "")),
                "source_db": "scopus-web",
                "document_type": doc_type,
            }
        )
    return records


def _split_source(source: str) -> tuple[str, str, str]:
    lines = [x.strip() for x in (source or "").split("\n") if x.strip()]
    journal = lines[0] if lines else ""
    volume = issue = ""
    rest = " ".join(lines[1:]) if len(lines) > 1 else ""
    m = re.search(r",?\s*(\d+)\s*\((\d+)\)", rest or source or "")
    if m:
        volume, issue = m.group(1), m.group(2)
    return journal, volume, issue


def _first_author(text: str) -> str:
    # Scopus renders authors separated by newlines and/or semicolons; a comma is
    # part of "Family, Initials" and must not split the list.
    first = re.split(r"[;\n]", text or "")[0].strip()
    first = re.sub(r"^[,\s]+", "", first)
    return first.rstrip(".")


def _year(text: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m.group(0)) if m else None


def _int(text) -> int:
    m = re.search(r"\d[\d,]*", str(text or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def looks_like_abstract(text: str) -> bool:
    """Reject fragments that merely follow the word "Abstract" on a page.

    Scopus document pages sometimes have no abstract (reports, commissions) and
    the next section can be a chemical list or reference fragment. A real
    abstract is prose: mostly letters, several sentences, few registry numbers.
    """
    t = normalize_count_text(text)
    if len(t) < 200:
        return False
    letters = sum(1 for ch in t if ch.isalpha())
    if letters / max(1, len(t)) < 0.55:
        return False
    if len(re.findall(r"\b\d{2,7}-\d{2}-\d\b", t)) >= 3:
        return False
    if len(re.findall(r"[.!?](?:\s|$)", t)) < 3:
        return False
    return True


def parse_scopus_abstract(text: str) -> str:
    """Extract the abstract from a Scopus document page's visible text.

    The page renders ``Abstract`` then the body, then ``References``/``Cited by``.
    """
    t = normalize_count_text(text)
    idx = t.find("Abstract")
    if idx < 0:
        return ""
    body = t[idx + len("Abstract") :]
    body = re.sub(r"^\s*[:\-]?\s*", "", body)
    stop = re.search(r"\n\s*(References|Cited by|Similar documents|Keywords|Indexed keywords|Funding|Show all)\b", body)
    if stop:
        body = body[: stop.start()]
    body = re.sub(r"\s*Show all\b.*$", "", body, flags=re.S)
    body = re.sub(r"\s+", " ", body).strip()
    return body if looks_like_abstract(body) else ""


def parse_scopus_record_page(cdp: CDP) -> dict:
    text = cdp.text()
    html = cdp.html()
    doi = ""
    m = re.search(r"\bDOI\s*[:\s]\s*(10\.\d{4,9}/[^\s<\"']+)", text)
    if not m:
        m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", html)
    if m:
        doi = m.group(1).rstrip(".,;)")
    title = ""
    tm = re.search(r"\bBack\b\s*\n+\s*(.+?)\n", text)
    if tm:
        title = tm.group(1).strip()
    return {
        "doi": doi,
        "title": title,
        "abstract": parse_scopus_abstract(text),
        "text_len": len(text),
        "url": cdp.url(),
    }


def parse_wos_rows(cdp: CDP) -> list[dict]:
    rows = cdp.eval(
        """(() => {
          const out = [];
          document.querySelectorAll('app-record').forEach(rec => {
            const text = rec.innerText || '';
            if (!text.trim()) return;
            const links = Array.from(rec.querySelectorAll('a')).map(a => [a.innerText.trim(), a.href]);
            out.push({text: text.slice(0, 600), links: links.slice(0, 8)});
          });
          return out;
        })()"""
    ) or []
    records = []
    for row in rows:
        text = re.sub(r"\s+", " ", row.get("text") or "")
        if len(text) < 20:
            continue
        doi = ""
        for _label, href in row.get("links") or []:
            m = re.search(r"(10\.\d{4,9}/[^\s&?#]+)", href or "")
            if m:
                doi = m.group(1)
                break
        # Empty Angular placeholders render as whitespace; require a year or DOI
        # before treating a node as a real record.
        if not doi and not re.search(r"\b(19|20)\d{2}\b", text):
            continue
        records.append({"title": text[:300], "doi": doi, "raw_text": text, "source_db": "wos-web"})
    return records


class ScopusWeb:
    def __init__(self, cdp: CDP, workdir=None, log=None, settle: float = 2.5):
        self.cdp = cdp
        self.selectors = _load_selectors(workdir)["scopus"]
        self.log = log
        self.settle = settle
        # Used to avoid reloading the exact page that was just used for a count.
        self.last_query = None
        self.last_page = None
        self.last_state = None

    def is_loaded(self, query: str, page: int = 1) -> bool:
        return self.last_query == query and self.last_page == page and self.last_state is not None

    def _login_state(self, query: str, page: int = 1) -> PageState:
        state = PageState(
            engine="scopus",
            url=self.cdp.url(),
            title=self.cdp.title(),
            count=None,
            state="login",
            evidence="redirected to Elsevier sign-in",
            records_available=False,
        )
        self.remember(query, page, state)
        return state

    def _home_search(self, query: str) -> PageState | None:
        """Retry the search from the Scopus homepage after a login redirect.

        A direct ``results.uri`` request can force authentication, while the
        homepage still exposes a guest search box. One navigation only; if the
        homepage is also a login page, return None and let the caller report it.
        """
        self.cdp.navigate("https://www.scopus.com/pages/home#basic", settle=3.0, load_timeout=45)
        current = self.cdp.url()
        if "id.elsevier.com" in current or "authorization.oauth2" in current:
            return None
        selector = self.cdp.eval(
            """(() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Search');
              if (!btn) return '';
              let node = btn;
              for (let i = 0; i < 6 && node; i++) {
                const inputs = node.querySelectorAll ? node.querySelectorAll('input[type=text]') : [];
                if (inputs.length) {
                  const el = inputs[0];
                  if (!el.id) el.id = 'lq-home-search-input';
                  return '#' + el.id;
                }
                node = node.parentElement;
              }
              return '';
            })()"""
        )
        if not selector:
            return None
        if not self.cdp.type_into(selector, query):
            return None
        time.sleep(0.3)
        clicked = self.cdp.eval(
            """(() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Search');
              if (!btn) return false;
              btn.click();
              return true;
            })()"""
        )
        if not clicked:
            return None
        ready = self.cdp.wait_for(
            "/(documents? found|document results|No documents found)/i.test(document.body ? document.body.innerText : '')",
            timeout=30,
        )
        if not ready:
            return None
        text = self.cdp.text(30)
        count, evidence = count_from_text(text, "scopus")
        if count is None:
            return None
        state = PageState(
            engine="scopus",
            url=self.cdp.url(),
            title=self.cdp.title(),
            count=count,
            state=("empty" if count == 0 else "ok"),
            evidence=evidence + " (via homepage)",
            records_available=count > 0,
        )
        self.remember(query, 1, state)
        return state

    def open_query(self, query: str, page: int = 1, limit: int = 10, sort: str = "cited", attempts: int = 1) -> PageState:
        """Load one results page. Default is a single navigation on purpose:
        reloading a results page repeatedly is the fastest way to trip a bot
        challenge. Callers should reuse this page for pagination instead."""
        url = scopus_url(query, page=page, limit=limit, sort=sort)
        state = None
        for attempt in range(max(1, attempts)):
            self.cdp.navigate(url, settle=self.settle, load_timeout=50)
            current = self.cdp.url()
            if "id.elsevier.com" in current or "authorization.oauth2" in current:
                recovered = self._home_search(query)
                if recovered is not None:
                    return recovered
                return self._login_state(query, page)
            ready = self.cdp.wait_for(
                "/(documents? found|document results|No documents found)/i.test(document.body ? document.body.innerText : '')",
                timeout=20,
            )
            if not ready:
                # Fall back to the on-page form, still on the same document.
                self._type_query(query)
                self.cdp.wait_for(
                    "/(documents? found|document results|No documents found)/i.test(document.body ? document.body.innerText : '')",
                    timeout=20,
                )
            text = self.cdp.text(30)
            count, evidence = count_from_text(text, "scopus")
            status, why = detect_state(text, self.cdp.url(), self.cdp.title())
            state = PageState(
                engine="scopus",
                url=self.cdp.url(),
                title=self.cdp.title(),
                count=count,
                state=("empty" if count == 0 else status),
                evidence=evidence or why,
                records_available=count is not None and count > 0,
            )
            if status == "login":
                recovered = self._home_search(query)
                if recovered is not None:
                    return recovered
            if count is not None:
                break
        if state is None:
            state = PageState("scopus", self.cdp.url(), self.cdp.title(), None, "unknown")
        self.remember(query, page, state)
        return state

    def remember(self, query: str, page: int, state: PageState) -> None:
        self.last_query = query
        self.last_page = page
        self.last_state = state

    def _type_query(self, query: str) -> bool:
        for selector in self.selectors.get("search_inputs") or []:
            if self.cdp.eval("!!document.querySelector(%s)" % json.dumps(selector), timeout=4):
                if self.cdp.type_into(selector, query):
                    for button in self.selectors.get("search_buttons") or []:
                        if self.cdp.click(button):
                            return True
                    self.cdp.eval("document.querySelector(%s).form && document.querySelector(%s).form.requestSubmit()" % (json.dumps(selector), json.dumps(selector)))
                    return True
        return False

    def rows(self, assume_type: str | None = None) -> list[dict]:
        records = parse_scopus_rows(self.cdp)
        if assume_type:
            # The query already restricted DOCTYPE(re); the badge lives in a
            # separate table row, so inferring it per-row is unreliable.
            for record in records:
                record["document_type"] = assume_type
        return records

    def next_page(self, timeout: float = 20.0) -> bool:
        """Advance the result list with the on-page pager.

        This is an in-page state change, not a reload: safer and faster than
        guessing URL parameters (Scopus ignores ``page=`` in several sessions).
        """
        before = self.cdp.eval(
            """(() => { const a = document.querySelector("a[href*='/pages/publications/']"); return a ? a.innerText.trim() : ''; })()"""
        )
        clicked = self.cdp.eval(
            """(() => {
              const visible = b => !b.disabled && (b.offsetWidth > 0 || b.offsetHeight > 0);
              const preferred = Array.from(document.querySelectorAll(
                "button[data-testid*='next' i], button[aria-label*='next' i], a[aria-label*='next' i], [data-testid='next-page']"
              )).filter(visible);
              if (preferred.length) { preferred[0].click(); return true; }
              // The current Scopus pager is a plain button whose text is "Next".
              const byText = Array.from(document.querySelectorAll("button, a")).filter(
                b => visible(b) && /^next$/i.test((b.innerText || '').trim())
              );
              if (!byText.length) return false;
              byText[0].click();
              return true;
            })()"""
        )
        if not clicked:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            after = self.cdp.eval(
                """(() => { const a = document.querySelector("a[href*='/pages/publications/']"); return a ? a.innerText.trim() : ''; })()"""
            )
            if after and after != before:
                return True
        return False

    def record_page(self, scopus_id: str) -> dict:
        self.cdp.navigate(SCOPUS_PAPER + str(scopus_id), settle=self.settle, load_timeout=45)
        self.cdp.wait_for("/Abstract/.test(document.body ? document.body.innerText : '')", timeout=15)
        return parse_scopus_record_page(self.cdp)


class WosWeb:
    def __init__(self, cdp: CDP, workdir=None, log=None, settle: float = 3.0):
        self.cdp = cdp
        self.selectors = _load_selectors(workdir)["wos"]
        self.log = log
        self.settle = settle
        self.signed_in = False
        self.last_query = None
        self.last_state = None

    def is_loaded(self, query: str) -> bool:
        return self.last_query == query and self.last_state is not None

    def open_query(self, query: str, attempts: int = 1) -> PageState:
        last_error = ""
        for _attempt in range(max(1, attempts)):
            self.cdp.navigate(WOS_ADVANCED, settle=self.settle, load_timeout=60)
            ok = self.cdp.wait_for("!!document.querySelector('#advancedSearchInputArea')", timeout=25)
            if not ok:
                last_error = "advanced-search textarea not found"
                continue
            self.cdp.dismiss_consent()
            if not self.cdp.type_into("#advancedSearchInputArea", query):
                last_error = "could not type into the query textarea"
                continue
            time.sleep(0.4)
            if not self.cdp.click("[data-ta='run-search']"):
                last_error = "run-search button not found"
                continue
            if not self.cdp.wait_for("location.href.includes('/summary')", timeout=30):
                last_error = "did not reach the summary page"
                continue
            self.cdp.wait_for("/(results? from Web of Science|No results found)/i.test(document.body ? document.body.innerText : '')", timeout=25)
            time.sleep(0.8)
            text = self.cdp.text(30)
            count, evidence = count_from_text(text, "wos")
            status, why = detect_state(text, self.cdp.url(), self.cdp.title())
            parsed = parse_wos_rows(self.cdp)
            self.signed_in = any(r.get("doi") for r in parsed)
            state = PageState(
                engine="wos",
                url=self.cdp.url(),
                title=self.cdp.title(),
                count=count,
                state=("empty" if count == 0 else status),
                evidence=(evidence or why or last_error),
                records_available=bool(parsed),
            )
            self.last_query = query
            self.last_state = state
            return state
        text = self.cdp.text(20)
        return PageState("wos", self.cdp.url(), self.cdp.title(), None, "unknown", last_error or "no count parsed")

    def rows(self) -> list[dict]:
        return parse_wos_rows(self.cdp)


def extract_dois(html: str) -> list[str]:
    two = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", html or "")
    out = []
    for doi in two:
        doi = doi.rstrip(".,;)]}'\"")
        if doi.lower().endswith(".pdf"):
            doi = doi[:-4]
        if doi not in out:
            out.append(doi)
    return out


def save_snapshot(cdp: CDP, directory, tag: str) -> dict:
    folder = pathlib.Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    html = cdp.html()
    text = cdp.text()
    (folder / f"{tag}.html").write_text(html, encoding="utf-8")
    (folder / f"{tag}.txt").write_text(text, encoding="utf-8")
    meta = {"url": cdp.url(), "title": cdp.title(), "html_bytes": len(html), "text_bytes": len(text)}
    (folder / f"{tag}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
