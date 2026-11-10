"""Versioned keyword contract; local-only, no extraction or guessed translation.

Consumers must use validate_keyword_contract(card, task)['keywords'], not raw
card keywords. errors reject the card's structure; rejected/warnings exclude only
unsupported terms. Inputs are never mutated. Unversioned legacy cards may omit
evidence; that compatibility path is explicit in warnings.
"""
from __future__ import annotations

import json
import pathlib
import re

ALIAS_PATH = pathlib.Path(__file__).resolve().parent.parent / "templates" / "关键词同义词表.json"


def _known_alias(term: str, surface: str) -> bool:
    try:
        aliases = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    values = aliases.get(term) if isinstance(aliases, dict) else None
    return isinstance(values, list) and surface in values

SOURCE_FIELDS = ("title", "abstract", "text", "author_keywords")
EVIDENCE_FIELDS = ("term", "surface", "source_field", "quote")


def keyword_contract() -> dict:
    """Return a fresh, JSON-serializable task declaration."""
    return {
        "version": 1,
        "recommended_max_keywords": 12,
        "evidence_field": "keyword_source_evidence",
        "three_layer_priority": "keywords_core/method/reagent plus keyword_evidence keep their category/rule/evidence/confidence shape; source evidence never replaces them",
        "keywords": "list[str]: complete scientific nouns/phrases; preserve English multiword terms and Chinese terms; no token/n-gram splitting or quota",
        "keyword_source_evidence": "list[dict] with nonempty string term/surface/source_field/quote; one supported evidence row per keyword",
        "source_fields": list(SOURCE_FIELDS),
        "evidence_rule": "quote must occur verbatim in the corresponding task field, surface in quote; term equals surface or has an explicit alias in templates/关键词同义词表.json or parenthetical quote; no guessed translation",
        "empty_policy": "No reliable terms: keywords=[] and keyword_source_evidence=[]; report WARN, never invent for coverage",
        "all_tiers": "L1/L2/L3 independently select complete keywords from sources, never split methods prose",
    }


def _source_strings(task: dict, field: str) -> list[str]:
    value = task.get(field)
    if isinstance(value, str):
        return [value]
    if field == "author_keywords" and isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def _explicit_alias(term: str, surface: str, quote: str) -> bool:
    # Deliberately conservative: two strings merely co-occurring is not an alias.
    # Accept only adjacent parenthetical naming, including full-width Chinese ().
    for left, right in ((term, surface), (surface, term)):
        pattern = (r"(?<![\w])" + re.escape(left) + r"\s*[（(]\s*"
                   + re.escape(right) + r"\s*[）)]")
        if re.search(pattern, quote):
            return True
    return False


def _validate_keyword_source_contract(card: dict, task: dict | None = None) -> dict:
    """Return {declared, version, errors, warnings, keywords, keyword_source_evidence, rejected}.

    Only an explicit task keyword_contract.version mandates the new fields.
    A malformed/unsupported declaration fails closed. Empty arrays are valid and
    always warn. Max 12 is advice, not a quota or structural rejection.
    """
    result = {"declared": False, "version": None, "errors": [],
              "warnings": [], "keywords": [], "keyword_source_evidence": [], "rejected": []}
    errors, warnings = result["errors"], result["warnings"]
    if not isinstance(card, dict):
        errors.append("card: expected object")
        return result
    task = task if isinstance(task, dict) else {}
    declaration = task.get("keyword_contract")
    if "keyword_contract" in task:
        if not isinstance(declaration, dict) or "version" not in declaration:
            errors.append("task.keyword_contract: expected object with version")
            return result
        result["declared"] = True
        result["version"] = declaration["version"]
        if type(declaration["version"]) is not int or declaration["version"] != 1:
            errors.append("task.keyword_contract.version: unsupported version (expected integer 1)")
            return result
    required = result["declared"]
    if not required and "keywords" not in card and "keyword_source_evidence" not in card:
        return result
    words = card.get("keywords")
    if not isinstance(words, list):
        errors.append("keywords: expected list[str]")
    elif any(not isinstance(x, str) or not x.strip() for x in words):
        errors.append("keywords: elements must be nonempty strings")
    has_evidence = "keyword_source_evidence" in card
    rows = card.get("keyword_source_evidence", [])
    if required or has_evidence:
        if not isinstance(rows, list):
            errors.append("keyword_source_evidence: expected list[dict]")
        else:
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    errors.append(f"keyword_source_evidence[{i}]: expected object")
                    continue
                for field in EVIDENCE_FIELDS:
                    if not isinstance(row.get(field), str) or not row[field].strip():
                        errors.append(f"keyword_source_evidence[{i}].{field}: expected nonempty string")
                if isinstance(row.get("source_field"), str) and row["source_field"] not in SOURCE_FIELDS:
                    errors.append(f"keyword_source_evidence[{i}].source_field: expected title|abstract|text|author_keywords")
        if required and not has_evidence:
            errors.append("keyword_source_evidence: required by task keyword_contract.version=1")
    if errors:
        return result
    if not words:
        warnings.append("WARN: no reliable keywords; empty arrays are valid, never invent for coverage")
    if len(words) > 12:
        warnings.append("WARN: more than recommended 12 keywords; prefer fewer complete terms")
    if not required and not has_evidence:
        result["keywords"] = list(dict.fromkeys(words))
        warnings.append("WARN: legacy keywords have no evidence (unversioned compatibility only)")
        return result
    supported = {}
    for i, row in enumerate(rows):
        term, surface, field, quote = (row[k] for k in EVIDENCE_FIELDS)
        reason = ""
        if term not in words:
            reason = "evidence term is absent from keywords"
        elif surface not in quote:
            reason = "surface is not verbatim in quote"
        elif not any(quote in source for source in _source_strings(task, field)):
            reason = "quote is not verbatim in task source_field"
        elif term != surface and not (_known_alias(term, surface) or _explicit_alias(term, surface, quote)):
            reason = "canonical term differs without explicit alias in template/quote; no inferred translation"
        if reason:
            result["rejected"].append({"term": term, "evidence_index": i, "reason": reason})
            warnings.append(f"WARN: excluded keyword {term!r}: {reason}")
        else:
            supported.setdefault(term, []).append(dict(row))
    for term in dict.fromkeys(words):
        if term in supported:
            result["keywords"].append(term)
            result["keyword_source_evidence"].extend(supported[term])
            if re.fullmatch(r"[\u3400-\u9fff]{1,3}", term):
                warnings.append(f"WARN: short Chinese term {term!r} needs terminology review; evidence alone cannot rule out fragments")
        else:
            reason = "no trustworthy keyword evidence"
            result["rejected"].append({"term": term, "reason": reason})
            warnings.append(f"WARN: excluded keyword {term!r}: {reason}")
    if words and not result["keywords"]:
        warnings.append("WARN: zero supported keywords; interpretation card retained")
    return result


LAYER_FIELDS = ("keywords_core", "keywords_method", "keywords_reagent")

def source_evidence(card: dict, *, legacy=True):
    """Only source-shaped old evidence is compatible; layer evidence is never coerced."""
    if "keyword_source_evidence" in card:
        return card["keyword_source_evidence"]
    rows = card.get("keyword_evidence")
    if legacy and isinstance(rows, list) and (not rows or all(
            isinstance(row, dict) and all(k in row for k in EVIDENCE_FIELDS)
            and not any(k in row for k in ("category", "rule", "evidence", "confidence")) for row in rows)):
        return rows
    return None


def validate_keyword_contract(card: dict, task: dict | None = None) -> dict:
    """Independent source contract, with explicit source-shaped v1 legacy support."""
    if not isinstance(card, dict):
        return _validate_keyword_source_contract(card, task)
    adapted = dict(card)
    task = task if isinstance(task, dict) else {}
    declaration = task.get("keyword_contract")
    modern = isinstance(declaration, dict) and declaration.get("evidence_field") == "keyword_source_evidence"
    rows = source_evidence(card, legacy=not modern)
    if rows is not None:
        adapted["keyword_source_evidence"] = rows
    if any(k in card for k in LAYER_FIELDS) and "keywords" not in card:
        adapted["keywords"] = list(dict.fromkeys(x for k in LAYER_FIELDS
            for x in (card.get(k) if isinstance(card.get(k), list) else []) if isinstance(x, str)))
    # A category evidence list by itself is an unrelated contract, not a source declaration.
    report = _validate_keyword_source_contract(adapted, task)
    if rows is not None and "keyword_source_evidence" not in card:
        report["warnings"].append("WARN: legacy source-shaped keyword_evidence accepted; migrate to keyword_source_evidence")
    return report
