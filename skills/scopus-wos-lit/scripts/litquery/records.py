# -*- coding: utf-8 -*-
"""Canonical bibliographic record + converters for every backend.

One record shape is used everywhere so that screening, enrichment, full-text
retrieval and Markdown rendering never need backend-specific branches.

Record fields (all optional except title):

    id, doi, title, abstract, authors[], first_author, corresponding_authors[],
    journal, issn, publisher, year, volume, issue, pages, cited_by,
    document_type, language, url, source_db, source_url, retrieved_at,
    oa_url, fulltext, journal_rank, selection, flags[]
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata

from .util import norm_space, parse_number, strip_html

REVIEW_TYPES = {
    "review",
    "systematic review",
    "meta analysis",
    "meta-analysis",
    "scoping review",
    "narrative review",
    "review article",
    "review-article",
}


def normalize_doi(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"(?i)^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text)
    text = text.strip().rstrip(".,;)")
    return text.lower()


def doi_url(doi: str) -> str:
    d = normalize_doi(doi)
    return f"https://doi.org/{d}" if d else ""


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_text(value: str) -> str:
    """Remove C0/C1 control characters that some metadata feeds leak into titles."""
    return norm_space(CONTROL_RE.sub("", str(value or "")))


def clean_title(value: str) -> str:
    text = strip_html(value or "")
    text = sanitize_text(text)
    return text.rstrip(" .")


def normalize_type(value: str, title: str = "") -> str:
    raw = (value or "").strip()
    key = re.sub(r"[^a-z-]+", " ", raw.lower()).strip()
    if key in REVIEW_TYPES or "review" in key or "meta analysis" in key:
        return "review"
    title_looks_review = bool(
        re.search(r"(?i)\b(a |an |systematic |narrative |critical |scoping )?(review|meta-analysis|metaanalysis)\b", title or "")
    )
    if key in ("", "unknown", "journal-article", "article"):
        return "review-inferred" if title_looks_review else (key or "unknown")
    if key:
        return key.replace(" ", "-")
    return "review-inferred" if title_looks_review else "unknown"


def is_review(record: dict) -> bool:
    t = (record.get("document_type") or "").lower()
    return t == "review" or t.startswith("review") or "meta" in t


def _author(name="", given="", family="", is_first=False, is_corresponding=False, affiliation="", orcid="", source="") -> dict:
    full = sanitize_text(name) or sanitize_text(" ".join(x for x in (given, family) if x))
    return {
        "name": full,
        "given": given or "",
        "family": family or "",
        "is_first": bool(is_first),
        "is_corresponding": bool(is_corresponding),
        "affiliation": norm_space(affiliation),
        "orcid": orcid or "",
        "source": source,
    }


def _finalize(record: dict) -> dict:
    authors = [a for a in (record.get("authors") or []) if a.get("name")]
    if not authors and record.get("first_author"):
        authors = [_author(name=record["first_author"], is_first=True)]
    if authors and not any(a.get("is_first") for a in authors):
        authors[0]["is_first"] = True
    record["authors"] = authors
    record["first_author"] = next((a["name"] for a in authors if a.get("is_first")), authors[0]["name"] if authors else "")
    corresponding = [a["name"] for a in authors if a.get("is_corresponding")]
    record["corresponding_authors"] = corresponding
    if not record.get("corresponding_authors") and authors:
        record["corresponding_inferred"] = True
        record["corresponding_authors"] = [authors[-1]["name"]]
    else:
        record["corresponding_inferred"] = False
    record["title"] = clean_title(record.get("title"))
    for key in ("journal", "publisher", "language"):
        if record.get(key):
            record[key] = sanitize_text(record[key])
    if record.get("abstract"):
        record["abstract"] = sanitize_text(record["abstract"])
    record["doi"] = normalize_doi(record.get("doi"))
    # Some backends store a bare DOI in "url"; always expose a clickable link.
    url = (record.get("url") or "").strip()
    if url and not re.match(r"(?i)^https?://", url):
        url = doi_url(url) if url.startswith("10.") else ""
    record["url"] = url or doi_url(record["doi"])
    if record["doi"]:
        # Keep the database page as provenance, but cite the DOI link: it is the
        # stable address a reader can actually resolve.
        if url and not record.get("source_url"):
            record["source_url"] = url
        record["url"] = doi_url(record["doi"])
    if record["doi"]:
        # DOI is the only identifier that is stable across runs and databases.
        record["id"] = "doi:" + record["doi"]
    else:
        # Python's built-in hash is salted per process; use a stable digest so a
        # digest file can be matched to a later report run.
        digest = hashlib.sha1(record["title"].lower().encode("utf-8")).hexdigest()[:16]
        record["id"] = "title:" + digest
    record.setdefault("cited_by", 0)
    try:
        record["cited_by"] = int(record.get("cited_by") or 0)
    except (TypeError, ValueError):
        record["cited_by"] = 0
    try:
        record["year"] = int(record.get("year")) if record.get("year") else None
    except (TypeError, ValueError):
        record["year"] = None
    record.setdefault("source_db", "")
    record.setdefault("retrieved_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    record.setdefault("flags", [])
    return record


def inverted_index_to_text(index) -> str:
    if not index:
        return ""
    if isinstance(index, str):
        return norm_space(index)
    positions = []
    for word, idxs in index.items():
        for pos in idxs or []:
            positions.append((int(pos), word))
    positions.sort()
    return norm_space(" ".join(word for _, word in positions))


def from_openalex(work: dict) -> dict:
    authors = []
    for i, authorship in enumerate(work.get("authorships") or []):
        author = authorship.get("author") or {}
        insts = [inst.get("display_name") for inst in (authorship.get("institutions") or []) if inst.get("display_name")]
        authors.append(
            _author(
                name=author.get("display_name") or authorship.get("raw_author_name") or "",
                is_first=(authorship.get("author_position") == "first") or i == 0,
                is_corresponding=bool(authorship.get("is_corresponding")),
                affiliation=", ".join(insts[:2]),
                orcid=(author.get("orcid") or "").replace("https://orcid.org/", ""),
                source="openalex",
            )
        )
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    oa = (work.get("best_oa_location") or {}).get("pdf_url") or (work.get("best_oa_location") or {}).get("landing_page_url")
    record = {
        "id": "openalex:" + str(work.get("id", "")).rsplit("/", 1)[-1],
        "doi": work.get("doi") or "",
        "title": work.get("display_name") or work.get("title") or "",
        "abstract": inverted_index_to_text(work.get("abstract_inverted_index")),
        "authors": authors,
        "journal": source.get("display_name") or "",
        "issn": source.get("issn_l") or "",
        "publisher": source.get("host_organization_name") or "",
        "year": work.get("publication_year"),
        "cited_by": work.get("cited_by_count") or 0,
        "document_type": normalize_type(work.get("type"), work.get("display_name") or ""),
        "language": work.get("language") or "",
        "url": work.get("doi") or work.get("id") or "",
        "source_db": "openalex",
        "source_url": work.get("id") or "",
        "oa_url": oa or "",
        "flags": ["oa"] if work.get("open_access", {}).get("is_oa") else [],
    }
    return _finalize(record)


def from_crossref(item: dict) -> dict:
    authors = []
    for i, author in enumerate(item.get("author") or []):
        affs = [a.get("name") for a in (author.get("affiliation") or []) if a.get("name")]
        authors.append(
            _author(
                given=author.get("given") or "",
                family=author.get("family") or author.get("name") or "",
                is_first=(author.get("sequence") == "first") or i == 0,
                affiliation=", ".join(affs[:2]),
                orcid=(author.get("ORCID") or "").replace("https://orcid.org/", ""),
                source="crossref",
            )
        )
    issued = (
        (item.get("published-print") or {}).get("date-parts")
        or (item.get("published-online") or {}).get("date-parts")
        or (item.get("issued") or {}).get("date-parts")
        or [[None]]
    )
    year = issued[0][0] if issued and issued[0] else None
    title = (item.get("title") or [""])[0]
    container = (item.get("container-title") or [""])[0]
    record = {
        "id": "crossref:" + (item.get("DOI") or ""),
        "doi": item.get("DOI") or "",
        "title": title,
        "abstract": strip_html(item.get("abstract") or ""),
        "authors": authors,
        "journal": container,
        "issn": ",".join(item.get("ISSN") or []),
        "publisher": item.get("publisher") or "",
        "year": year,
        "volume": item.get("volume") or "",
        "issue": item.get("issue") or "",
        "pages": item.get("page") or "",
        "cited_by": item.get("is-referenced-by-count") or 0,
        "document_type": normalize_type(item.get("type"), title),
        "language": item.get("language") or "",
        "url": item.get("URL") or "",
        "source_db": "crossref",
        "source_url": item.get("URL") or "",
    }
    return _finalize(record)


def from_scopus(entry: dict, abstract: str = "") -> dict:
    authors = []
    raw_authors = entry.get("author") or []
    for i, author in enumerate(raw_authors):
        name = author.get("authname") or author.get("preferred-name", {}).get("ce:indexed-name") or ""
        authors.append(_author(name=name, is_first=(author.get("seq") == "1") or i == 0, source="scopus"))
    # Some exports carry an explicit corresponding marker.
    for author in authors:
        if author.get("is_corresponding"):
            break
    record = {
        "id": "scopus:" + (entry.get("eid") or entry.get("dc:identifier") or ""),
        "doi": entry.get("prism:doi") or "",
        "title": entry.get("dc:title") or "",
        "abstract": abstract or entry.get("dc:description") or "",
        "authors": authors,
        "journal": entry.get("prism:publicationName") or "",
        "issn": entry.get("prism:issn") or entry.get("prism:eIssn") or "",
        "publisher": entry.get("prism:publisher") or "",
        "year": (entry.get("prism:coverDate") or "")[:4] or None,
        "volume": entry.get("prism:volume") or "",
        "issue": entry.get("prism:issueIdentifier") or "",
        "pages": entry.get("prism:pageRange") or "",
        "cited_by": parse_number(entry.get("citedby-count")) or 0,
        "document_type": normalize_type(entry.get("subtypeDescription"), entry.get("dc:title") or ""),
        "language": entry.get("language") or "",
        "url": entry.get("prism:url") or "",
        "source_db": "scopus",
        "source_url": entry.get("prism:url") or "",
    }
    return _finalize(record)


def from_wos(hit: dict) -> dict:
    names = hit.get("names") or {}
    authors = []
    for i, author in enumerate(names.get("authors") or []):
        authors.append(
            _author(
                name=author.get("displayName") or author.get("wosStandard") or "",
                is_first=i == 0,
                is_corresponding=bool(author.get("isCorresponding")),
                orcid=(author.get("researcherId") or ""),
                source="wos",
            )
        )
    # Starter API sometimes exposes reprint authors separately.
    reprint = names.get("reprintAuthors") or []
    reprint_names = {
        (r.get("displayName") or "").lower()
        for r in reprint
        if isinstance(r, dict)
    }
    for author in authors:
        if author["name"].lower() in reprint_names:
            author["is_corresponding"] = True
            author["source"] = "wos-reprint"
    source = hit.get("source") or {}
    identifiers = hit.get("identifiers") or {}
    citations = hit.get("citations") or []
    cited = 0
    for c in citations:
        if isinstance(c, dict) and c.get("count") is not None:
            cited = parse_number(c.get("count")) or 0
    record = {
        "id": "wos:" + (hit.get("uid") or ""),
        "doi": identifiers.get("doi") or "",
        "title": hit.get("title") or "",
        "abstract": strip_html(hit.get("abstract") or ""),
        "authors": authors,
        "journal": source.get("sourceTitle") or "",
        "issn": source.get("issn") or "",
        "publisher": source.get("publisher") or "",
        "year": source.get("publishYear") or None,
        "volume": source.get("volume") or "",
        "issue": source.get("issue") or "",
        "pages": source.get("pages") or "",
        "cited_by": cited,
        "document_type": normalize_type(hit.get("documentType"), hit.get("title") or ""),
        "language": hit.get("language") or "",
        "url": f"https://www.webofscience.com/wos/woscc/full-record/{hit.get('uid')}" if hit.get("uid") else "",
        "source_db": "wos",
        "source_url": "",
    }
    return _finalize(record)


def merge(base: dict, extra: dict) -> dict:
    """Fill gaps in ``base`` from ``extra``; never overwrite a non-empty field."""
    if not base:
        return _finalize(dict(extra))
    out = dict(base)
    for key in ("abstract", "journal", "issn", "publisher", "year", "volume", "issue", "pages", "language", "oa_url", "doi"):
        if not out.get(key) and extra.get(key):
            out[key] = extra[key]
    if not out.get("authors") and extra.get("authors"):
        out["authors"] = extra["authors"]
    elif extra.get("authors") and len(extra["authors"]) > len(out.get("authors") or []):
        # A web result row only knows the first author; OpenAlex/Crossref know the
        # full list. Prefer the richer list instead of freezing a one-author record.
        out["authors"] = extra["authors"]
    if not out.get("url") and extra.get("url"):
        out["url"] = extra["url"]
    out["cited_by"] = max(int(out.get("cited_by") or 0), int(extra.get("cited_by") or 0))
    if (extra.get("document_type") or "").startswith("review"):
        out["document_type"] = "review"
    # Corresponding author flags are the most valuable enrichment.
    extra_corr = {a["name"].lower() for a in (extra.get("authors") or []) if a.get("is_corresponding")}
    if extra_corr:
        for author in out.get("authors") or []:
            if author["name"].lower() in extra_corr:
                author["is_corresponding"] = True
        out["corresponding_inferred"] = False
    out["source_db"] = out.get("source_db") or extra.get("source_db")
    return _finalize(out)


def dedupe(records: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    out: list[dict] = []
    for rec in records:
        rec = _finalize(dict(rec))
        key = rec["doi"] or re.sub(r"\W+", "", rec["title"].lower())
        if not key:
            continue
        if key in seen:
            out[seen[key]] = merge(out[seen[key]], rec)
            continue
        seen[key] = len(out)
        out.append(rec)
    return out


def citation_per_year(record: dict) -> float:
    year = record.get("year")
    if not year:
        return 0.0
    age = max(1, time.localtime().tm_year - int(year) + 1)
    return float(record.get("cited_by") or 0) / age


def authors_short(record: dict, limit: int = 3) -> str:
    names = [a["name"] for a in record.get("authors") or []]
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + " et al."


def first_and_corresponding(record: dict) -> tuple[str, str]:
    first = record.get("first_author") or (record.get("authors") or [{}])[0].get("name", "")
    corr = "; ".join(record.get("corresponding_authors") or [])
    if corr and record.get("corresponding_inferred"):
        corr += " (inferred: last author)"
    return first, corr


def sort_key(record: dict):
    return (-(record.get("cited_by") or 0), -(record.get("year") or 0))


def stats(records: list[dict]) -> dict:
    years = [r["year"] for r in records if r.get("year")]
    return {
        "records": len(records),
        "with_abstract": sum(1 for r in records if r.get("abstract")),
        "with_doi": sum(1 for r in records if r.get("doi")),
        "reviews": sum(1 for r in records if is_review(r)),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
    }
