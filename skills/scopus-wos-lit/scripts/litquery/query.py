# -*- coding: utf-8 -*-
"""Build and lint Scopus / Web of Science boolean queries.

The builder is deterministic: the same plan plus the same options always yields
the same query string (and therefore the same SHA-256, which is recorded in the
search log). Semantic expansion is the agent's job; this module only compiles
and validates.

Engine differences that are easy to get wrong:

======================  ==========================  =======================
feature                 Scopus                      Web of Science
======================  ==========================  =======================
topic field             TITLE-ABS-KEY(...)          TS=(...)
exact phrase            {gut microbiota}            "gut microbiota"
loose phrase            "gut microbiota"            "gut microbiota"
unordered proximity     W/n                         NEAR/n
ordered proximity       PRE/n                       (not available)
negation                AND NOT                     NOT
document type           DOCTYPE(re)                 DT=(Review)
year                    PUBYEAR > 2017              PY=(2018-2026)
language                LANGUAGE(english)           LA=(English)
======================  ==========================  =======================
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field as dc_field

ENGINES = ("scopus", "wos")

SCOPUS_FIELDS = {
    "title-abs-key": "TITLE-ABS-KEY",
    "title": "TITLE",
    "abs": "ABS",
    "key": "KEY",
    "authkey": "AUTHKEY",
    "author": "AUTH",
    "affil": "AFFIL",
    "src": "SRC-TITLE",
    "doi": "DOI",
    "all": "ALL",
}
WOS_FIELDS = {
    "ts": "TS",
    "topic": "TS",
    "title": "TI",
    "abs": "AB",
    "author": "AU",
    "src": "SO",
    "doi": "DO",
    "all": "ALL",
}
DOC_TYPES = {
    "review": {"scopus": "re", "wos": "Review"},
    "article": {"scopus": "ar", "wos": "Article"},
    "conference": {"scopus": "cp", "wos": "Proceedings Paper"},
    "book-chapter": {"scopus": "ch", "wos": "Book Chapter"},
    "editorial": {"scopus": "ed", "wos": "Editorial Material"},
}

# Words that must never receive a trailing wildcard: broadening them hurts
# precision without adding recall.
NO_WILDCARD = {
    "review", "reviews", "study", "studies", "effect", "effects", "role", "roles",
    "human", "humans", "patient", "patients", "disease", "diseases", "analysis",
    "model", "models", "cell", "cells", "gene", "genes", "using", "based",
}


@dataclass
class BuildOptions:
    engine: str = "scopus"
    field: str | None = None
    with_synonyms: bool = True
    with_broader: bool = False
    with_narrower: bool = False
    with_optional: bool = False
    wildcards: bool = False
    phrase_mode: str = "exact"  # exact | loose | proximity
    proximity: int = 3
    include_filters: bool = True
    year_from: int | None = None
    year_to: int | None = None
    document_types: list[str] | None = None
    languages: list[str] | None = None
    drop_roles: list[str] = dc_field(default_factory=list)

    def normalized(self) -> "BuildOptions":
        engine = (self.engine or "").lower()
        if engine not in ENGINES:
            raise ValueError(f"unsupported engine: {self.engine!r}")
        mode = (self.phrase_mode or "exact").lower()
        if mode not in ("exact", "loose", "proximity"):
            raise ValueError(f"unsupported phrase_mode: {self.phrase_mode!r}")
        n = int(self.proximity or 3)
        if n < 1 or n > 255:
            raise ValueError("proximity must be 1..255")
        self.engine = engine
        self.phrase_mode = mode
        self.proximity = n
        return self


def _clean_term(term: str) -> str:
    t = str(term or "")
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("[", " ").replace("]", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" ,;.")
    # Drop any residual quotes/braces: they are syntax, never part of a term.
    t = t.replace('"', "").replace("{", "").replace("}", "")
    if t.upper() in ("AND", "OR", "NOT", "AND NOT"):
        return ""
    return t


def _has_phrase_syntax(term: str) -> bool:
    return '"' in term or "{" in term or "}" in term


def _wildcard(term: str) -> str:
    if len(term.split()) != 1:
        return term
    low = term.lower()
    if low in NO_WILDCARD or len(term) < 5:
        return term
    if term.endswith("*") or term.endswith("?"):
        return term
    if not re.match(r"^[A-Za-z][A-Za-z0-9-]*$", term):
        return term
    return term + "*"


def render_term(term: str, opts: BuildOptions) -> str:
    t = _clean_term(term)
    if not t:
        return ""
    if opts.wildcards:
        t = _wildcard(t)
    words = t.split()

    if len(words) == 1:
        return t

    if opts.phrase_mode == "proximity" and 2 <= len(words) <= 8:
        op = "W" if opts.engine == "scopus" else "NEAR"
        joiner = f" {op}/{opts.proximity} "
        return joiner.join(words)

    if opts.engine == "scopus" and opts.phrase_mode == "exact":
        # Curly braces = exact phrase in Scopus; quotes = loose phrase.
        return "{" + t + "}"
    return '"' + t + '"'


def _collect_terms(concept: dict, opts: BuildOptions) -> list[str]:
    terms: list[str] = []
    terms.extend(concept.get("terms") or [])
    if opts.with_synonyms:
        terms.extend(concept.get("synonyms") or [])
    if opts.with_broader:
        terms.extend(concept.get("broader") or [])
    if opts.with_narrower:
        terms.extend(concept.get("narrower") or [])
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = str(term).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(term).strip())
    return out


def _block(terms: list[str], opts: BuildOptions) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    for term in terms:
        piece = render_term(term, opts)
        if not piece:
            continue
        key = piece.lower()
        if key in seen:
            continue
        seen.add(key)
        rendered.append(piece)
    if not rendered:
        return ""
    if len(rendered) == 1:
        return rendered[0]
    return "(" + " OR ".join(rendered) + ")"


def _field_clause(engine: str, field_name: str | None, expression: str) -> str:
    if engine == "scopus":
        code = SCOPUS_FIELDS.get((field_name or "title-abs-key").lower(), (field_name or "TITLE-ABS-KEY").upper())
        return f"{code}({expression})"
    code = WOS_FIELDS.get((field_name or "ts").lower(), (field_name or "TS").upper())
    return f"{code}=({expression})"


def _year_clause(engine: str, year_from: int | None, year_to: int | None) -> str:
    if not year_from and not year_to:
        return ""
    if engine == "scopus":
        parts = []
        if year_from:
            parts.append(f"PUBYEAR > {int(year_from) - 1}")
        if year_to:
            parts.append(f"PUBYEAR < {int(year_to) + 1}")
        return " AND ".join(parts)
    start = int(year_from) if year_from else 1900
    end = int(year_to) if year_to else time.localtime().tm_year + 1
    return f"PY=({start}-{end})"


def _doctype_clause(engine: str, types: list[str] | None) -> str:
    if not types:
        return ""
    codes = []
    for t in types:
        mapping = DOC_TYPES.get(str(t).lower())
        if mapping:
            codes.append(mapping[engine])
        else:
            codes.append(str(t))
    codes = list(dict.fromkeys(codes))
    if engine == "scopus":
        if len(codes) == 1:
            return f"DOCTYPE({codes[0]})"
        return "(" + " OR ".join(f"DOCTYPE({c})" for c in codes) + ")"
    return f"DT=({' OR '.join(codes)})"


def _language_clause(engine: str, languages: list[str] | None) -> str:
    if not languages:
        return ""
    langs = [str(x) for x in languages]
    if engine == "scopus":
        return "(" + " OR ".join(f"LANGUAGE({x.lower()})" for x in langs) + ")"
    return f"LA=({' OR '.join(langs)})"


def build(plan: dict, opts: BuildOptions | None = None) -> dict:
    opts = (opts or BuildOptions()).normalized()
    concepts = [c for c in (plan.get("concepts") or []) if c]
    if not concepts:
        raise ValueError("plan has no concepts; fill concepts before building a query")

    blocks = []
    used_concepts = []
    total_terms = 0
    for concept in concepts:
        role = (concept.get("role") or "core").lower()
        if role == "optional" and not opts.with_optional:
            continue
        if role in [r.lower() for r in opts.drop_roles]:
            continue
        terms = _collect_terms(concept, opts)
        block = _block(terms, opts)
        if not block:
            continue
        blocks.append(block)
        used_concepts.append({"id": concept.get("id"), "label": concept.get("label"), "terms": len(terms)})
        total_terms += len(terms)

    if not blocks:
        raise ValueError("all concept blocks rendered empty; check terms/synonyms")

    expression = " AND ".join(blocks)
    core = _field_clause(opts.engine, opts.field, expression)

    clauses = []
    if opts.include_filters:
        year_from = opts.year_from
        if year_from is None:
            year_from = (plan.get("filters") or {}).get("year_from")
        year_to = opts.year_to if opts.year_to is not None else (plan.get("filters") or {}).get("year_to")
        doctypes = opts.document_types
        if doctypes is None:
            doctypes = (plan.get("filters") or {}).get("document_types")
        languages = opts.languages
        if languages is None:
            languages = (plan.get("filters") or {}).get("languages")
        clauses.extend(
            x
            for x in (
                _year_clause(opts.engine, year_from, year_to),
                _doctype_clause(opts.engine, doctypes),
                _language_clause(opts.engine, languages),
            )
            if x
        )

    exclude = [str(x) for x in (plan.get("exclude") or []) if str(x).strip()]
    exclude_clause = ""
    if exclude:
        ex_block = _block(exclude, opts)
        if ex_block:
            if opts.engine == "scopus":
                exclude_clause = f"AND NOT {_field_clause(opts.engine, opts.field, ex_block)}"
            else:
                exclude_clause = f"NOT {_field_clause(opts.engine, opts.field, ex_block)}"

    query = core
    if clauses:
        query += " AND " + " AND ".join(clauses)
    if exclude_clause:
        query += " " + exclude_clause

    stats = {
        "concepts_used": len(used_concepts),
        "terms": total_terms,
        "boolean_ops": len(re.findall(r"\b(AND|OR|NOT)\b", query)),
        "proximity_ops": len(re.findall(r"\b(?:W|PRE|NEAR)/\d+", query)),
        "chars": len(query),
    }
    return {
        "engine": opts.engine,
        "query": query,
        "expression": expression,
        "blocks": blocks,
        "concepts": used_concepts,
        "filters": {
            "year_from": opts.year_from if opts.year_from is not None else (plan.get("filters") or {}).get("year_from"),
            "year_to": opts.year_to if opts.year_to is not None else (plan.get("filters") or {}).get("year_to"),
            "document_types": opts.document_types if opts.document_types is not None else (plan.get("filters") or {}).get("document_types"),
            "languages": opts.languages if opts.languages is not None else (plan.get("filters") or {}).get("languages"),
            "included": bool(opts.include_filters),
        },
        "options": {
            "with_synonyms": opts.with_synonyms,
            "with_broader": opts.with_broader,
            "with_narrower": opts.with_narrower,
            "wildcards": opts.wildcards,
            "phrase_mode": opts.phrase_mode,
            "proximity": opts.proximity,
            "field": opts.field,
        },
        "sha256": __import__("hashlib").sha256(query.encode("utf-8")).hexdigest(),
        "stats": stats,
    }


# ------------------------------------------------------------------- linting
SCOPUS_ALLOWED_TAGS = {
    "TITLE-ABS-KEY", "TITLE", "ABS", "KEY", "AUTHKEY", "AUTH", "AFFIL", "AFFILCOUNTRY",
    "SRC-TITLE", "SRCTYPE", "DOI", "PUBYEAR", "DOCTYPE", "LANGUAGE", "SUBJAREA",
    "ALL", "EDITOR", "ISBN", "ISSN", "CODEN", "PUBLISHER", "FUND-SPONSOR",
}
WOS_ALLOWED_TAGS = {
    "TS", "TI", "AB", "AK", "KP", "AU", "AI", "SO", "DO", "PY", "DT", "LA", "WC",
    "SU", "OG", "AD", "CU", "ALL", "ED", "IS", "VL", "PG", "UT", "PMID",
}


def lint(query: str, engine: str = "scopus") -> dict:
    engine = (engine or "scopus").lower()
    errors: list[dict] = []
    warnings: list[dict] = []
    q = query or ""

    def err(code, message, hint=""):
        errors.append({"code": code, "message": message, "hint": hint})

    def warn(code, message, hint=""):
        warnings.append({"code": code, "message": message, "hint": hint})

    if not q.strip():
        err("empty", "query is empty")
        return {"ok": False, "errors": errors, "warnings": warnings, "stats": {}}

    # Balanced parentheses, quotes and braces.
    depth = 0
    in_quote = False
    in_brace = False
    for ch in q:
        if ch == '"' and not in_brace:
            in_quote = not in_quote
        elif ch == "{" and not in_quote:
            in_brace = not in_brace
        elif ch == "}" and not in_quote:
            in_brace = not in_brace
        elif not in_quote and not in_brace:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    err("paren", "unbalanced parentheses: ')' without matching '('")
                    break
    if depth > 0:
        err("paren", f"unbalanced parentheses: {depth} '(' left open")
    if in_quote:
        err("quote", "unbalanced double quote")
    if in_brace:
        err("brace", "unbalanced curly brace")
    if engine == "wos" and ("{" in q or "}" in q):
        err("brace_wos", "Web of Science has no exact-phrase braces; use double quotes", "replace {x} with \"x\"")
    if engine == "scopus" and re.search(r"\bNEAR\s*/", q, re.I):
        err("near_scopus", "Scopus uses W/n and PRE/n, not NEAR/n", "replace NEAR/n with W/n")
    if engine == "wos" and re.search(r"\b(?:W|PRE)\s*/\s*\d", q):
        err("prox_wos", "Web of Science uses NEAR/n, not W/n or PRE/n", "replace W/n with NEAR/n")

    for m in re.finditer(r"\b(W|PRE|NEAR)\s*/\s*([^\s)]*)", q, re.I):
        op, num = m.group(1).upper(), m.group(2)
        if not num.isdigit():
            err("prox_num", f"proximity operator {op}/ is missing a number", "e.g. W/3")
            continue
        n = int(num)
        if n < 1 or n > 255:
            err("prox_range", f"proximity distance {n} outside 1..255")

    if re.search(r"&&|\|\||\s!\s", q):
        err("bad_bool", "use AND / OR / AND NOT instead of && / || / !")
    if engine == "wos" and re.search(r"\bAND\s+NOT\b", q, re.I):
        err("and_not_wos", "Web of Science uses NOT, not AND NOT", "replace 'AND NOT' with 'NOT'")
    if re.search(r"\[\s*\]|\[[^\]]*\]", q):
        warn("brackets", "square brackets are not Scopus/WoS query syntax", "remove them or use parentheses")
    if re.search(r"(?:^|\s)(?:AND|OR|NOT)\s*(?:\)|$)", q, re.I):
        err("dangling_bool", "boolean operator at the end of a clause")
    if re.search(r"(?:^|\()\s*(?:AND|OR)\b", q, re.I):
        err("leading_bool", "boolean operator at the start of a clause")
    if re.search(r"\(\s*\)", q):
        err("empty_paren", "empty parentheses")

    # Field tags.
    allowed = SCOPUS_ALLOWED_TAGS if engine == "scopus" else WOS_ALLOWED_TAGS
    for m in re.finditer(r"\b([A-Z][A-Z0-9-]{1,20})\s*=", q):
        tag = m.group(1)
        if tag not in allowed:
            warn("field_tag", f"unknown field tag {tag}= for {engine}", "check the field-code reference")

    # Wildcards and phrase length.
    for token in re.findall(r"\S+", q):
        if token.count("*") > 1:
            warn("wildcard", f"multiple '*' in {token}", "one truncation per term is easier to reason about")
        if token.startswith("*"):
            warn("wildcard_lead", f"leading wildcard in {token}", "slow and low precision; prefer suffix truncation")
    for phrase in re.findall(r'"([^"]+)"|\{([^}]+)\}', q):
        text = phrase[0] or phrase[1]
        if len(text.split()) > 12:
            warn("long_phrase", "quoted phrase longer than 12 words", "use proximity (W/n, NEAR/n) instead")

    if len(q) > 4000:
        warn("length", f"query has {len(q)} characters", "split into query sets if the UI truncates it")

    stats = {
        "chars": len(q),
        "terms": len(re.findall(r"[{}]|\"", q)),
        "boolean_ops": len(re.findall(r"\b(AND|OR|NOT)\b", q)),
        "proximity_ops": len(re.findall(r"\b(?:W|PRE|NEAR)/\d+", q)),
        "phrases": len(re.findall(r'"[^"]+"|\{[^}]+\}', q)),
        "wildcards": q.count("*"),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# --------------------------------------------------------------- variants
def attempt_variants() -> list[dict]:
    """Escalation ladder used when a query returns fewer than 15 records.

    Each step is a *superset* of the previous one and is logged with its hit
    count, so the final report can show exactly how the query was widened.
    """
    return [
        {
            "label": "A_strict",
            "rationale": "strict: synonyms on, exact phrases, all filters",
            "opts": {"with_synonyms": True, "with_broader": False, "wildcards": False, "phrase_mode": "exact", "proximity": 3, "include_filters": True},
        },
        {
            "label": "B_truncation",
            "rationale": "add truncation and broader terms, keep exact phrases",
            "opts": {"with_synonyms": True, "with_broader": True, "wildcards": True, "phrase_mode": "exact", "proximity": 3, "include_filters": True},
        },
        {
            "label": "C_proximity",
            "rationale": "relax phrases to ordered proximity and widen the year window by 3 years",
            "opts": {"with_synonyms": True, "with_broader": True, "wildcards": True, "phrase_mode": "proximity", "proximity": 4, "include_filters": True, "year_shift": -3},
        },
        {
            "label": "D_core_only",
            "rationale": "keep only core concept blocks and drop filters; last resort before reporting",
            "opts": {"with_synonyms": True, "with_broader": True, "wildcards": True, "phrase_mode": "proximity", "proximity": 6, "include_filters": False, "drop_roles": ["context", "optional"]},
        },
    ]


def summarize_plan(plan: dict) -> dict:
    concepts = plan.get("concepts") or []
    return {
        "topic": (plan.get("topic") or {}).get("zh") or (plan.get("direction") or {}).get("zh") or "",
        "concepts": [
            {
                "id": c.get("id"),
                "label": c.get("label"),
                "role": c.get("role", "core"),
                "n_terms": len(c.get("terms") or []) + len(c.get("synonyms") or []),
                "n_broader": len(c.get("broader") or []),
                "n_narrower": len(c.get("narrower") or []),
            }
            for c in concepts
        ],
        "filters": plan.get("filters") or {},
        "exclude": plan.get("exclude") or [],
    }
