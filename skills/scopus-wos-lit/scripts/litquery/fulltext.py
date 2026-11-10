# -*- coding: utf-8 -*-
"""Abstract and full-text retrieval.

Respecting access boundaries:

* open-access copies (Unpaywall / OpenAlex / Europe PMC / Semantic Scholar) are
  fetched over plain HTTPS;
* publisher pages that are not open access are opened in the user's browser
  session, and the PDF link advertised by the page's own ``citation_pdf_url``
  metadata is downloaded with that session's cookies;
* if neither works, the record is reported as ``paywalled`` with the DOI link,
  never as a silent success.

PDF text extraction uses PyMuPDF when present, then pypdf, and otherwise keeps
the PDF and reports ``pdf_saved_text_unavailable``.
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse

from . import records as recmod
from .util import HttpClient, HttpError, ensure_dir, norm_space, slugify, strip_html, write_json

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0"
)


class FulltextStats:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def add(self, status: str) -> None:
        self.counts[status] = self.counts.get(status, 0) + 1


def pdf_to_text(path) -> str:
    p = pathlib.Path(path)
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(p))
        text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
        return norm_space(text)
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        return norm_space("\n".join((page.extract_text() or "") for page in reader.pages))
    except Exception:
        return ""


def html_to_text(path) -> str:
    raw = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        return norm_space(soup.get_text("\n"))
    except Exception:
        return norm_space(strip_html(raw))


def _is_pdf(body: bytes, content_type: str) -> bool:
    if body[:5] == b"%PDF-":
        return True
    return "application/pdf" in (content_type or "").lower()


def _cookies_for(url: str, cookies: list) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    picked = []
    for cookie in cookies:
        domain = (cookie.get("domain") or "").lstrip(".")
        if not domain or host == domain or host.endswith("." + domain):
            picked.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(picked)


def resolve_oa(record: dict, runtime) -> list[dict]:
    """Return candidate open-access URLs, best first."""
    candidates: list[dict] = []
    if record.get("oa_url"):
        candidates.append({"url": record["oa_url"], "source": "record"})
    doi = recmod.normalize_doi(record.get("doi"))
    if not doi:
        return candidates

    oa = runtime.openalex.by_doi(doi)
    # OpenAlex records already carry best_oa_location in oa_url.
    if oa and oa.get("oa_url") and all(c["url"] != oa["oa_url"] for c in candidates):
        candidates.append({"url": oa["oa_url"], "source": "openalex"})

    try:
        payload = runtime.http.get_json(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(runtime.mailto or 'research@example.org')}"
        )
        for location in payload.get("oa_locations") or []:
            url = location.get("url_for_pdf") or location.get("url")
            if url:
                candidates.append({"url": url, "source": "unpaywall", "host": location.get("host_type")})
    except HttpError:
        pass

    try:
        payload = runtime.http.get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&resultType=core&pageSize=3&query="
            + urllib.parse.quote(f'DOI:"{doi}"')
        )
        for result in (payload.get("resultList") or {}).get("result") or []:
            for item in ((result.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
                url = item.get("url")
                if url and item.get("availability") in ("Open access", "Free", "OA"):
                    candidates.append({"url": url, "source": "europepmc", "doc": item.get("documentStyle")})
    except HttpError:
        pass

    seen = set()
    unique = []
    for candidate in candidates:
        url = candidate.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(candidate)
    unique.sort(key=lambda c: 0 if "pdf" in (c.get("url") or "").lower() or c.get("doc") == "pdf" else 1)
    return unique


def _save_html(dest_dir: pathlib.Path, url: str, runtime) -> dict:
    resp = runtime.http.get(url, headers={"User-Agent": UA, "Referer": "https://doi.org/"})
    if not (200 <= resp.status < 300):
        return {"status": f"http_{resp.status}", "url": url}
    if _is_pdf(resp.body, resp.headers.get("content-type", "")):
        pdf_path = dest_dir / "fulltext.pdf"
        pdf_path.write_bytes(resp.body)
        text = pdf_to_text(pdf_path)
        if text:
            (dest_dir / "fulltext.txt").write_text(text, encoding="utf-8")
        return {
            "status": "pdf" if text else "pdf_no_text",
            "path": str(pdf_path),
            "chars": len(text),
            "url": url,
        }
    html_path = dest_dir / "fulltext.html"
    html_path.write_text(resp.text, encoding="utf-8")
    text = html_to_text(html_path)
    (dest_dir / "fulltext.txt").write_text(text, encoding="utf-8")
    return {"status": "html", "path": str(html_path), "chars": len(text), "url": url}


def fetch_fulltext(record: dict, runtime, outdir, allow_browser: bool = True) -> dict:
    doi = recmod.normalize_doi(record.get("doi"))
    key = doi or record.get("id") or recmod.clean_title(record.get("title"))[:60]
    dest = ensure_dir(pathlib.Path(outdir) / slugify(key, 80))

    for candidate in resolve_oa(record, runtime):
        try:
            result = _save_html(dest, candidate["url"], runtime)
        except HttpError as exc:
            continue
        if result.get("status") in ("pdf", "html") and result.get("chars", 0) > 500:
            result["route"] = candidate.get("source")
            write_json(dest / "fulltext.json", {"record": record, "result": result})
            return result

    if allow_browser and doi:
        try:
            cdp = runtime.cdp()
            cdp.navigate(f"https://doi.org/{doi}", settle=3.0, load_timeout=45)
            pdf_url = cdp.eval(
                """(() => { const m = document.querySelector('meta[name="citation_pdf_url"]'); return m ? m.content : ''; })()"""
            )
            if pdf_url:
                cookies = _cookies_for(pdf_url, cdp.cookies())
                headers = {"User-Agent": UA, "Referer": cdp.url()}
                if cookies:
                    headers["Cookie"] = cookies
                resp = runtime.http.get(pdf_url, headers=headers)
                if _is_pdf(resp.body, resp.headers.get("content-type", "")):
                    pdf_path = dest / "fulltext.pdf"
                    pdf_path.write_bytes(resp.body)
                    text = pdf_to_text(pdf_path)
                    if text:
                        (dest / "fulltext.txt").write_text(text, encoding="utf-8")
                    result = {
                        "status": "pdf" if text else "pdf_no_text",
                        "path": str(pdf_path),
                        "chars": len(text),
                        "url": pdf_url,
                        "route": "browser-cookie",
                    }
                    write_json(dest / "fulltext.json", {"record": record, "result": result})
                    return result
            text = cdp.text()
            if len(text) > 4000 and re.search(r"(?i)\b(abstract|introduction)\b", text):
                (dest / "page.txt").write_text(text, encoding="utf-8")
                result = {"status": "browser_text", "path": str(dest / "page.txt"), "chars": len(text), "url": cdp.url(), "route": "browser-page"}
                write_json(dest / "fulltext.json", {"record": record, "result": result})
                return result
        except Exception as exc:
            result = {"status": "browser_failed", "error": f"{type(exc).__name__}: {exc}", "url": f"https://doi.org/{doi}"}
            write_json(dest / "fulltext.json", {"record": record, "result": result})
            return result

    result = {
        "status": "paywalled",
        "url": recmod.doi_url(doi),
        "note": "no open-access copy found; use the DOI link with institutional access",
    }
    write_json(dest / "fulltext.json", {"record": record, "result": result})
    return result


def fetch_reviews(selected: list[dict], runtime, outdir, allow_browser: bool = True, log=None) -> dict:
    """Fetch abstracts and full text for the selected reviews, with a manifest."""
    outdir = ensure_dir(outdir)
    stats = FulltextStats()
    out = []
    for record in selected:
        record = dict(record)
        if not record.get("abstract") and record.get("doi"):
            try:
                oa = runtime.openalex.by_doi(record["doi"])
                if oa and oa.get("abstract"):
                    record["abstract"] = oa["abstract"]
                    record["abstract_source"] = "openalex"
            except Exception:
                pass
        if not record.get("abstract") and record.get("scopus_id"):
            try:
                page = runtime.scopus().record_page(record["scopus_id"])
                if page.get("abstract"):
                    record["abstract"] = page["abstract"]
                    record["abstract_source"] = "scopus-record-page"
            except Exception:
                pass
        if not record.get("abstract") and record.get("doi"):
            try:
                from .web import parse_scopus_abstract

                cdp = runtime.cdp()
                cdp.navigate("https://doi.org/" + recmod.normalize_doi(record["doi"]), settle=2.0, load_timeout=40)
                abstract = parse_scopus_abstract(cdp.text())
                if abstract:
                    record["abstract"] = abstract
                    record["abstract_source"] = "publisher-page"
            except Exception:
                pass
        fulltext = fetch_fulltext(record, runtime, outdir, allow_browser=allow_browser)
        stats.add(fulltext.get("status", "unknown"))
        record["fulltext"] = fulltext
        out.append(record)
        if log:
            log.info(f"fulltext {fulltext.get('status')}: {(record.get('title') or '')[:70]}")
    manifest = {
        "count": len(out),
        "status_counts": stats.counts,
        "reviews": out,
        "note": "paywalled records keep their DOI link; full text must be obtained through institutional access",
    }
    write_json(outdir / "fulltext_manifest.json", manifest)
    return manifest
