# -*- coding: utf-8 -*-
"""Screen candidate reviews for authority, impact and currency.

Rules implemented (all configurable):

* journal must pass the top-N% bar (JCR percentile when available, otherwise a
  CAS-tier-1 proxy that is explicitly marked as approximate);
* citations must clear an age-aware floor, so a brand-new review is not punished
  for not having accumulated citations yet;
* old papers are rejected unless they qualify as foundational (very high absolute
  or per-year citations);
* the final ranking is a transparent weighted score, and every decision carries
  a human-readable reason list.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from . import records as recmod
from .journals import JournalTable

REVIEWISH = ("review", "meta", "systematic", "scoping")


@dataclass
class SelectOptions:
    top_percent: float = 20.0
    min_year: int | None = None
    max_age_years: int = 6
    min_citations: int = 20
    min_citations_per_year: float = 2.0
    citation_floor: int = 5
    foundational_citations: int = 400
    foundational_citations_per_year: float = 40.0
    foundational_min_year: int = 1980
    rank_policy: str = "strict"  # strict | balanced | off
    accept_q1: bool = False
    require_review: bool = True
    allow_unknown_type: bool = False
    limit: int = 8
    max_per_journal: int = 2
    now_year: int | None = None
    require_topic_terms: list[str] = field(default_factory=list)
    min_topic_hits: int = 2
    scope_terms: list[str] = field(default_factory=list)

    def normalized(self) -> "SelectOptions":
        policy = (self.rank_policy or "strict").lower()
        if policy not in ("strict", "balanced", "off"):
            raise ValueError("rank_policy must be strict, balanced or off")
        self.rank_policy = policy
        if self.now_year is None:
            self.now_year = time.localtime().tm_year
        if self.min_year is None:
            self.min_year = int(self.now_year) - int(self.max_age_years)
        return self


@dataclass
class Decision:
    record: dict
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    reject: list[str] = field(default_factory=list)
    score: float = 0.0
    parts: dict = field(default_factory=dict)
    rank: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "score": round(self.score, 4),
            "score_parts": {k: round(v, 4) for k, v in self.parts.items()},
            "reasons": self.reasons,
            "reject_reasons": self.reject,
            "journal_rank": self.rank,
        }


def _is_review(record: dict) -> bool:
    t = (record.get("document_type") or "").lower()
    if t in ("review", "review-inferred"):
        return True
    if any(word in t for word in REVIEWISH):
        return True
    title = record.get("title") or ""
    return bool(re.search(r"(?i)\b(systematic|narrative|critical|scoping)?\s*(review|meta-analysis)\b", title))


def _foundational(record: dict, opts: SelectOptions) -> bool:
    year = record.get("year")
    if not year or int(year) < opts.foundational_min_year:
        return False
    cited = int(record.get("cited_by") or 0)
    cpy = recmod.citation_per_year(record)
    return cited >= opts.foundational_citations or cpy >= opts.foundational_citations_per_year


def _citation_floor(record: dict, opts: SelectOptions) -> int:
    year = record.get("year")
    if not year:
        return opts.min_citations
    age = max(1, int(opts.now_year) - int(year) + 1)
    scaled = opts.min_citations * min(1.0, age / 5.0)
    return max(opts.citation_floor, int(round(scaled)))


def _topic_evidence(record: dict, terms: list[str], min_distinct: int) -> dict:
    """Cheap topical-relevance check against the direction's own keywords.

    Sorting a broad query by citations surfaces mega-cited reviews that mention
    the field only in passing. Requiring either a title hit, two distinct core
    terms, or at least three mentions removes most of that drift without
    pretending to be semantic search.
    """
    title = (record.get("title") or "").lower()
    body = title + " " + (record.get("abstract") or "").lower()
    distinct = 0
    mentions = 0
    title_hit = False
    for term in terms or []:
        t = str(term).strip().lower()
        if not t:
            continue
        if " " in t:
            n = body.count(t)
            if t in title:
                title_hit = True
        else:
            pattern = r"\b" + re.escape(t) + r"\w*"
            n = len(re.findall(pattern, body))
            if re.search(pattern, title):
                title_hit = True
        if n:
            distinct += 1
            mentions += n
    ok = bool(title_hit or distinct >= min_distinct or mentions >= 3)
    return {"ok": ok, "distinct": distinct, "mentions": mentions, "title_hit": title_hit, "has_abstract": bool(record.get("abstract"))}


def _scope_evidence(record: dict, terms: list[str]) -> dict:
    body = ((record.get("title") or "") + " " + (record.get("abstract") or "")).lower()
    matched = []
    for term in terms or []:
        t = str(term).strip().lower()
        if not t:
            continue
        if " " in t:
            if t in body:
                matched.append(t)
        elif re.search(r"\b" + re.escape(t) + r"\w*", body):
            matched.append(t)
    return {"ok": bool(matched), "matched": matched[:5], "has_abstract": bool(record.get("abstract"))}


def evaluate(record: dict, table: JournalTable, opts: SelectOptions) -> Decision:
    opts = opts.normalized()
    reasons: list[str] = []
    reject: list[str] = []

    if opts.require_topic_terms:
        evidence = _topic_evidence(record, opts.require_topic_terms, opts.min_topic_hits)
        if not evidence["has_abstract"]:
            # Screening runs before abstract backfill; without an abstract the
            # only cheap evidence is the title, so do not reject on "off_topic".
            reasons.append("topic: unverified (no abstract at screening time)")
        elif evidence["ok"]:
            reasons.append(
                f"topic: {evidence['distinct']} distinct core terms / {evidence['mentions']} mentions"
            )
        else:
            reject.append("off_topic")

    if opts.scope_terms:
        scope = _scope_evidence(record, opts.scope_terms)
        if not scope["has_abstract"]:
            reasons.append("scope: unverified (no abstract at screening time)")
        elif scope["ok"]:
            reasons.append("scope: " + ", ".join(scope["matched"]))
        else:
            reject.append("out_of_scope")

    is_review = _is_review(record)
    if opts.require_review and not is_review:
        reject.append("not_a_review")
    elif not is_review:
        reasons.append("type not review (accepted by policy)")

    rank = table.rank(title=record.get("journal") or "", issn=record.get("issn") or "", top_percent=opts.top_percent, accept_q1=opts.accept_q1)
    rank_ok = bool(rank.get("passes"))
    unknown_rank = not rank.get("matched")
    if opts.rank_policy == "off":
        rank_ok = True
        reasons.append("journal ranking filter disabled")
    elif not rank_ok:
        if opts.rank_policy == "balanced" and unknown_rank and int(record.get("cited_by") or 0) >= 150:
            rank_ok = True
            reasons.append("rank unknown but citations >= 150 (balanced policy)")
        else:
            reject.append("journal_not_top20" if not unknown_rank else "journal_rank_unknown")
    elif rank.get("approx"):
        reasons.append("rank: " + str(rank.get("reason")))
    else:
        reasons.append("rank: " + str(rank.get("reason")))

    year = record.get("year")
    if not year:
        reject.append("missing_year")
    else:
        age = int(opts.now_year) - int(year)
        if age < -1:
            reject.append("future_year")
        elif int(year) < int(opts.min_year):
            if _foundational(record, opts):
                reasons.append(f"older than {opts.max_age_years}y but kept as foundational (cited {record.get('cited_by')})")
                record.setdefault("flags", []).append("foundational")
            else:
                reject.append(f"older_than_{opts.max_age_years}y")
        else:
            reasons.append(f"published {year}")

    cited = int(record.get("cited_by") or 0)
    floor = _citation_floor(record, opts)
    cpy = recmod.citation_per_year(record)
    if cited < floor:
        if _foundational(record, opts):
            reasons.append(f"citations {cited} below age floor {floor} but foundational")
        else:
            reject.append(f"citations_{cited}_below_floor_{floor}")
    else:
        reasons.append(f"cited {cited} (>= {floor})")
    if cpy < opts.min_citations_per_year and not _foundational(record, opts):
        reject.append(f"citations_per_year_{cpy:.1f}_below_{opts.min_citations_per_year}")

    if opts.require_review and record.get("document_type") == "unknown" and opts.allow_unknown_type:
        reject = [r for r in reject if r != "not_a_review"]

    # Score: rank 50%, citations 30%, recency 20%.
    if rank.get("percentile") is not None:
        rank_norm = float(rank["percentile"]) / 100.0
    elif rank.get("cas_tier") == 1:
        rank_norm = 0.92
    elif rank.get("jcr_quartile") == "Q1":
        rank_norm = 0.85
    else:
        rank_norm = 0.45
    cite_norm = math.log1p(max(0, cited)) / math.log1p(5000)
    cite_norm = min(1.0, cite_norm)
    age_for_score = max(0, int(opts.now_year) - int(year)) if year else 10
    recency = 0.5 ** (age_for_score / 8.0)
    score = 0.5 * rank_norm + 0.3 * cite_norm + 0.2 * recency

    decision = Decision(
        record=record,
        eligible=not reject,
        reasons=reasons,
        reject=reject,
        score=score,
        parts={"rank": rank_norm, "citations": cite_norm, "recency": recency},
        rank=rank,
    )
    return decision


def select(records: list[dict], table: JournalTable, opts: SelectOptions | None = None) -> dict:
    opts = (opts or SelectOptions()).normalized()
    records = recmod.dedupe(records)
    decisions = [evaluate(r, table, opts) for r in records]
    eligible = [d for d in decisions if d.eligible]
    eligible.sort(key=lambda d: d.score, reverse=True)

    chosen: list[Decision] = []
    per_journal: dict[str, int] = {}
    for decision in eligible:
        journal = (decision.record.get("journal") or "").lower()
        if opts.max_per_journal and journal and per_journal.get(journal, 0) >= opts.max_per_journal:
            decision.reasons.append(f"skipped: journal cap {opts.max_per_journal}")
            continue
        chosen.append(decision)
        per_journal[journal] = per_journal.get(journal, 0) + 1
        if len(chosen) >= opts.limit:
            break

    selected = []
    for decision in chosen:
        record = dict(decision.record)
        record["journal_rank"] = decision.rank
        record["selection"] = decision.as_dict()
        record["selection"]["selected"] = True
        selected.append(record)

    rejected = []
    for decision in decisions:
        if decision in chosen:
            continue
        record = dict(decision.record)
        record["journal_rank"] = decision.rank
        record["selection"] = decision.as_dict()
        record["selection"]["selected"] = False
        rejected.append(record)
    rejected.sort(key=lambda r: r["selection"]["score"], reverse=True)

    def count(reason: str) -> int:
        return sum(1 for d in decisions if any(r.startswith(reason) for r in d.reject))

    stats = {
        "candidates": len(records),
        "eligible": len(eligible),
        "selected": len(selected),
        "rejected": len(rejected),
        "rejected_by_reason": {
            "off_topic": count("off_topic"),
            "out_of_scope": count("out_of_scope"),
            "not_a_review": count("not_a_review"),
            "journal_not_top20": count("journal_not_top20"),
            "journal_rank_unknown": count("journal_rank_unknown"),
            "too_old": count("older_than_"),
            "low_citations": count("citations_"),
            "low_citations_per_year": count("citations_per_year_"),
        },
        "policy": {
            "top_percent": opts.top_percent,
            "min_year": opts.min_year,
            "max_age_years": opts.max_age_years,
            "min_citations": opts.min_citations,
            "rank_policy": opts.rank_policy,
            "require_review": opts.require_review,
            "limit": opts.limit,
            "max_per_journal": opts.max_per_journal,
        },
    }
    stats["warnings"] = _warnings(selected, decisions, opts)
    return {"selected": selected, "rejected": rejected[:50], "stats": stats}


def _warnings(selected: list[dict], decisions: list[Decision], opts: SelectOptions) -> list[str]:
    out = []
    if len(selected) < 5:
        out.append(
            f"only {len(selected)} reviews passed; widen the year window, lower min_citations, "
            "or switch rank_policy to balanced/off (and say so in the report)"
        )
    if opts.rank_policy != "off" and selected and all((r.get("journal_rank") or {}).get("approx") for r in selected):
        out.append("all selected ranks are approximations (CAS tier 1 / Q1 proxy); import JCR percentiles for an exact top-20% decision")
    unknown = sum(1 for r in selected if not (r.get("journal_rank") or {}).get("matched"))
    if unknown and opts.rank_policy != "off":
        out.append(f"{unknown} selected records have an unknown journal rank")
    if not any((r.get("abstract") or "").strip() for r in selected):
        out.append("no abstracts available; abstracts must be fetched before interpretation")
    return out
