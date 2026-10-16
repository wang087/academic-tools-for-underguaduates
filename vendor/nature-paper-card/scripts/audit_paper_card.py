#!/usr/bin/env python3
"""Audit a Paper Card against its source bundle and output contract.

Every input and output path comes from a CLI argument, so this script has no
repository-root assumption: invoke it by its absolute path resolved relative to the
directory containing the loaded SKILL.md, from a vendored location or otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SECTION_RE = re.compile(r"^##\s+(\d{2})\b.*$", re.M)
PAPER_POINTER_RE = re.compile(r"\[Paper:\s*([^\]]+)\]")
FORBIDDEN_SECTION_RE = re.compile(r"^##\s+(17|18)\b", re.M)

OUT_OF_SCOPE_TERMS = [
    "academic english",
    "self-quiz",
    "comprehension quiz",
    "public article",
    "social-media copy",
    "\u5b66\u672f\u82f1\u6587",
    "\u81ea\u6d4b",
    "\u516c\u4f17\u53f7",
]

# FIX 6: OUT_OF_SCOPE_TERMS used to be matched anywhere in the card, so an ordinary
# sentence such as "It is not a public article and no comprehension quiz is needed."
# produced ERROR out_of_scope. A term is now reported only when it occurs in an
# actual instruction/intent context: as a Markdown heading, or on a line that also
# carries a production verb (a request to write/generate/output that content).
PRODUCTION_VERBS = [
    "write",
    "generate",
    "draft",
    "produce",
    "compose",
    "create",
    "translate",
    "prepare",
    "output",
    "\u5199",
    "\u751f\u6210",
    "\u8f93\u51fa",
    "\u5236\u4f5c",
    "\u64b0\u5199",
    "\u6574\u7406",
    "\u7ffb\u8bd1",
    "\u63d0\u4f9b",
    "\u505a\u4e00\u4efd",
]

HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")

# A production verb near the term is not enough on its own: the card legitimately has
# to state that such content was NOT produced ("no academic-English collection,
# comprehension quiz, or public-article draft was added"), which is exactly the check
# the skill asks the agent to perform. A negation cue shortly before the term therefore
# cancels the hit, so only a positive instruction or a heading reports a violation.
NEGATION_RE = re.compile(
    r"(?:\b(?:no|not|never|without|none|nothing|nor)\b|不|无|没|非|禁|勿)",
    re.I,
)
NEGATION_WINDOW = 48

# FIX 5: sections that legitimately may not need a [Paper: ...] pointer, because the
# card may have to answer them with `Not applicable` / `Not assessable` rather than
# with a located piece of evidence:
#   01 Basic Information      - bibliographic metadata, not an evidence claim
#   02 One-Sentence Summary   - a restatement of the paper's own framing
#   09 Essential Formulas     - explicitly allowed to be `Not applicable`
# Every other section (03-08 and 10-16) is an analysis section and must carry at
# least one pointer; a missing one is an error there and a warning here.
POINTER_OPTIONAL_SECTIONS = ("01", "02", "09")
POINTER_REQUIRED_SECTIONS = tuple(
    f"{number:02d}"
    for number in range(1, 17)
    if f"{number:02d}" not in POINTER_OPTIONAL_SECTIONS
)

# Markdown scaffolding that must not be mistaken for section content.
TABLE_SCAFFOLD_RE = re.compile(r"^\|?[\s:|-]*\|?$")

IDEA_STATUS_TERMS = [
    "innovation status",
    "\u521b\u65b0\u72b6\u6001",
]

IDEA_VALIDATION_TERMS = [
    "validation",
    "how to test",
    "\u5982\u4f55\u9a8c\u8bc1",
]

IDEA_FAILURE_TERMS = [
    "failure mode",
    "possible failure",
    "\u53ef\u80fd\u5931\u8d25",
]

LOCATOR_MODES = ("page-grounded", "structure-grounded", "source-limited")
SOURCE_LIMITED_LOCATORS = ("abstract", "metadata", "user-provided excerpt")


def finding(level: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"level": level, "code": code, "message": message}
    if details:
        item["details"] = details
    return item


def contains_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


def section_text(card: str, number: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(number)}\b[^\n]*\n(.*?)(?=^##\s+\d{{2}}\b|\Z)",
        card,
        re.M | re.S,
    )
    return match.group(1) if match else ""


def substantive_body(text: str) -> str:
    """Strip whitespace, Markdown heading/table scaffolding and source pointers.

    FIX 5: used to decide whether a section has an empty body. `[Paper: ...]` pointers
    are locators, not content, so a section that contains nothing but a pointer still
    counts as empty. Scaffolding that is stripped:

    - blank lines;
    - Markdown heading markers (`#`..`######`);
    - table separator rows and empty table cells (`| --- | --- |`, `|  |  |`).
    """
    kept: list[str] = []
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        value = re.sub(r"^#{1,6}\s*", "", value).strip()
        value = PAPER_POINTER_RE.sub("", value).strip()
        if not value:
            continue
        if TABLE_SCAFFOLD_RE.fullmatch(value):
            continue
        if not value.strip("|-: \t\u2002"):
            continue
        kept.append(value)
    return "\n".join(kept).strip()


def out_of_scope_hits(card: str) -> list[dict[str, str]]:
    """Return excluded terms that appear in a real instruction/intent context (FIX 6).

    A term is reported only when it sits on a Markdown heading line or on a line that
    carries a production verb, and only when no negation cue directly precedes it.
    """
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in card.splitlines():
        lower = line.lower()
        is_heading = bool(HEADING_LINE_RE.match(line))
        has_production_verb = contains_any(line, PRODUCTION_VERBS)
        if not is_heading and not has_production_verb:
            continue
        for term in OUT_OF_SCOPE_TERMS:
            if term in seen:
                continue
            index = lower.find(term.lower())
            if index < 0:
                continue
            window = lower[max(0, index - NEGATION_WINDOW) : index]
            if NEGATION_RE.search(window):
                continue
            seen.add(term)
            hits.append(
                {
                    "term": term,
                    "context": " ".join(line.split())[:160],
                    "matched_as": "heading" if is_heading else "production-verb",
                }
            )
    return hits


def evidence_alias_pattern(alias: str) -> re.Pattern[str]:
    """Token-exact alias matcher (FIX 5).

    A plain substring test let a card that only said `Figure 10` satisfy the inventory
    item `Figure 1`. Boundaries are ASCII-alphanumeric on purpose: `\\w` would also
    cover CJK characters and would break the common Chinese forms `如图1` / `表2`.
    """
    return re.compile(
        rf"(?<![0-9A-Za-z]){re.escape(alias)}(?![0-9A-Za-z])",
        re.I,
    )


def evidence_item_mentioned(card: str, item_id: str) -> bool:
    match = re.match(r"^(Figure|Table|Equation)\s+(\d+[A-Za-z]?)$", item_id)
    if not match:
        return evidence_alias_pattern(item_id).search(card) is not None

    kind, number = match.groups()
    aliases = {
        "Figure": [
            f"Figure {number}",
            f"Fig. {number}",
            f"\u56fe{number}",
            f"\u56fe {number}",
        ],
        "Table": [
            f"Table {number}",
            f"\u8868{number}",
            f"\u8868 {number}",
        ],
        "Equation": [
            f"Equation {number}",
            f"Eq. {number}",
            f"Eq.{number}",
            f"\u516c\u5f0f{number}",
            f"\u516c\u5f0f {number}",
        ],
    }
    return any(evidence_alias_pattern(alias).search(card) for alias in aliases[kind])


def audit(
    card: str,
    bundle: dict[str, Any] | None,
    locator_mode: str,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    sections = SECTION_RE.findall(card)
    expected = [f"{number:02d}" for number in range(1, 17)]

    if sections == expected:
        findings.append(finding("pass", "sections", "Sections 01-16 are present in order."))
    else:
        findings.append(
            finding(
                "error",
                "sections",
                "Sections 01-16 are missing, duplicated, or out of order.",
                expected=expected,
                found=sections,
            )
        )

    forbidden_sections = FORBIDDEN_SECTION_RE.findall(card)
    if forbidden_sections:
        findings.append(
            finding("error", "forbidden_sections", "Sections 17 or 18 must not be present.")
        )
    else:
        findings.append(finding("pass", "forbidden_sections", "No Sections 17 or 18 found."))

    preamble = card.split("## 01", 1)[0]
    metadata_lines = [line for line in preamble.splitlines() if line.lstrip().startswith(">")]
    if len(metadata_lines) >= 7:
        findings.append(
            finding("pass", "status_header", "The evidence-status header has at least seven fields.")
        )
    else:
        findings.append(
            finding(
                "error",
                "status_header",
                "The evidence-status header must contain at least seven blockquote fields.",
                found=len(metadata_lines),
            )
        )

    if locator_mode in preamble.lower():
        findings.append(
            finding(
                "pass",
                "locator_mode_declaration",
                f"The card declares locator mode: {locator_mode}.",
            )
        )
    else:
        findings.append(
            finding(
                "error",
                "locator_mode_declaration",
                f"The card header must declare canonical locator mode: {locator_mode}.",
            )
        )

    pointers = PAPER_POINTER_RE.findall(card)
    if pointers:
        findings.append(
            finding("pass", "paper_pointers", f"Found {len(pointers)} paper source pointers.")
        )
    else:
        findings.append(finding("error", "paper_pointers", "No [Paper: ...] pointers found."))

    # FIX 5a: an empty section body is an error. A card with all 16 headings but empty
    # prose used to pass the audit, because nothing ever looked at the bodies.
    empty_sections = [
        number
        for number in expected
        if not substantive_body(section_text(card, number))
    ]
    if empty_sections:
        findings.append(
            finding(
                "error",
                "section_bodies",
                "Every section 01-16 must have a body; empty bodies were found.",
                empty_sections=empty_sections,
            )
        )
    else:
        findings.append(
            finding("pass", "section_bodies", "All sections 01-16 have a non-empty body.")
        )

    # FIX 5b: one pointer for the whole card is not enough. Analysis sections (03-08,
    # 10-16) must each carry a pointer; 01/02/09 may legitimately be pointer-free, so
    # their absence is only a warning. See POINTER_OPTIONAL_SECTIONS for the rationale.
    missing_required_pointers = [
        number
        for number in POINTER_REQUIRED_SECTIONS
        if not PAPER_POINTER_RE.search(section_text(card, number))
    ]
    if missing_required_pointers:
        findings.append(
            finding(
                "error",
                "section_pointers",
                "Each analysis section (03-08, 10-16) needs at least one [Paper: ...] pointer.",
                sections_without_pointer=missing_required_pointers,
            )
        )
    else:
        findings.append(
            finding(
                "pass",
                "section_pointers",
                "Every analysis section carries at least one [Paper: ...] pointer.",
            )
        )

    missing_optional_pointers = [
        number
        for number in POINTER_OPTIONAL_SECTIONS
        if not PAPER_POINTER_RE.search(section_text(card, number))
    ]
    if missing_optional_pointers:
        findings.append(
            finding(
                "warning",
                "optional_section_pointers",
                "Sections 01, 02, and 09 may omit a pointer, but adding one is preferred.",
                sections_without_pointer=missing_optional_pointers,
            )
        )
    else:
        findings.append(
            finding(
                "pass",
                "optional_section_pointers",
                "Sections 01, 02, and 09 also carry a [Paper: ...] pointer.",
            )
        )

    page_pointers = [
        pointer
        for pointer in pointers
        if "PDF p." in pointer or "PDF pp." in pointer
    ]
    ambiguous = [
        pointer
        for pointer in pointers
        if re.search(r"\bpp?\.", pointer)
        and "PDF p." not in pointer
        and "PDF pp." not in pointer
    ]

    if locator_mode == "page-grounded":
        if bundle is None:
            findings.append(
                finding(
                    "error",
                    "bundle_required",
                    "Page-grounded mode requires a source bundle.",
                )
            )
        if not page_pointers:
            findings.append(
                finding(
                    "error",
                    "page_grounding",
                    "Page-grounded mode requires at least one PDF page pointer.",
                )
            )
        elif ambiguous:
            findings.append(
                finding(
                    "error",
                    "ambiguous_pages",
                    "Page pointers must distinguish PDF page indices.",
                    pointers=ambiguous,
                )
            )
        else:
            findings.append(
                finding("pass", "page_grounding", "PDF page pointers are explicit and unambiguous.")
            )
    else:
        if page_pointers or ambiguous:
            findings.append(
                finding(
                    "error",
                    "page_pointers_forbidden",
                    f"{locator_mode} mode must not emit page-number citations.",
                    pointers=page_pointers + ambiguous,
                )
            )
        else:
            findings.append(
                finding(
                    "pass",
                    "page_pointers_forbidden",
                    f"{locator_mode} mode contains no page-number citations.",
                )
            )

    inventory = bundle.get("evidence_inventory", {}) if bundle else {}
    if bundle is not None:
        for key, label in (("figures", "figure"), ("tables", "table"), ("equations", "equation")):
            items = inventory.get(key, []) if isinstance(inventory, dict) else []
            missing = [
                item.get("id", "")
                for item in items
                if not evidence_item_mentioned(card, item.get("id", ""))
            ]
            if missing:
                findings.append(
                    finding(
                        "error",
                        f"{key}_coverage",
                        f"Not every main {label} from the source bundle appears in the card.",
                        missing=missing,
                    )
                )
            else:
                findings.append(
                    finding(
                        "pass",
                        f"{key}_coverage",
                        f"All {len(items)} inventoried {key} appear in the card.",
                    )
                )
    else:
        findings.append(
            finding(
                "warning",
                "source_inventory_unavailable",
                "No source bundle was supplied; figure, table, and equation coverage was not audited.",
            )
        )

    if locator_mode == "source-limited" and pointers:
        invalid_limited = [
            pointer
            for pointer in pointers
            if not any(token in pointer.lower() for token in SOURCE_LIMITED_LOCATORS)
        ]
        if invalid_limited:
            findings.append(
                finding(
                    "error",
                    "source_limited_scope",
                    "Source-limited pointers must use only Abstract, Metadata, or User-provided excerpt.",
                    pointers=invalid_limited,
                )
            )
        else:
            findings.append(
                finding(
                    "pass",
                    "source_limited_scope",
                    "All source-limited pointers use allowed source scopes.",
                )
            )

    section_12 = section_text(card, "12")
    section_13 = section_text(card, "13")
    if section_12 and section_13:
        findings.append(
            finding("pass", "limitations_split", "Sections 12 and 13 are both present.")
        )

    section_16 = section_text(card, "16")
    if section_16:
        checks = {
            "idea_status": contains_any(section_16, IDEA_STATUS_TERMS),
            "idea_validation": contains_any(section_16, IDEA_VALIDATION_TERMS),
            "idea_failure": contains_any(section_16, IDEA_FAILURE_TERMS),
        }
        for code, passed in checks.items():
            findings.append(
                finding(
                    "pass" if passed else "warning",
                    code,
                    (
                        f"Section 16 includes the required {code.replace('idea_', '')} field."
                        if passed
                        else f"Section 16 may be missing the {code.replace('idea_', '')} field."
                    ),
                )
            )

    # FIX 6: only report excluded terms that appear in an instruction/intent context.
    out_of_scope = out_of_scope_hits(card)
    if out_of_scope:
        findings.append(
            finding(
                "error",
                "out_of_scope",
                "The card contains content excluded by the skill scope.",
                terms=[hit["term"] for hit in out_of_scope],
                contexts=out_of_scope,
            )
        )
    else:
        findings.append(finding("pass", "out_of_scope", "No excluded content detected."))

    errors = sum(item["level"] == "error" for item in findings)
    warnings = sum(item["level"] == "warning" for item in findings)
    passes = sum(item["level"] == "pass" for item in findings)
    return {
        "schema_version": "1.0",
        "summary": {
            "status": "fail" if errors else ("pass_with_warnings" if warnings else "pass"),
            "passes": passes,
            "warnings": warnings,
            "errors": errors,
        },
        "metrics": {
            "locator_mode": locator_mode,
            "sections_found": sections,
            "paper_pointer_count": len(pointers),
            "figures_in_bundle": len(inventory.get("figures", [])),
            "tables_in_bundle": len(inventory.get("tables", [])),
            "equations_in_bundle": len(inventory.get("equations", [])),
            # FIX 5 additive fields; existing keys above are unchanged.
            "sections_with_empty_body": empty_sections,
            "analysis_sections_without_pointer": missing_required_pointers,
            "optional_sections_without_pointer": missing_optional_pointers,
            "out_of_scope_hits": len(out_of_scope),
        },
        "findings": findings,
    }


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        f"Audit status: {summary['status']} "
        f"(passes={summary['passes']}, warnings={summary['warnings']}, errors={summary['errors']})"
    )
    for item in report["findings"]:
        print(f"{item['level'].upper():7} {item['code']}: {item['message']}")
        details = item.get("details")
        if details:
            print(f"        {json.dumps(details, ensure_ascii=False)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a Paper Card and its locator mode.")
    parser.add_argument("--card", type=Path, required=True, help="Paper Card Markdown file.")
    parser.add_argument("--bundle", type=Path, help="Optional source_bundle.json.")
    parser.add_argument(
        "--locator-mode",
        required=True,
        choices=LOCATOR_MODES,
        help="Grounding mode declared by the Paper Card.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON audit report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.card.is_file():
        print("ERROR: --card must point to an existing file.", file=sys.stderr)
        return 2
    if args.bundle and not args.bundle.is_file():
        print("ERROR: --bundle does not exist.", file=sys.stderr)
        return 2
    if args.locator_mode == "page-grounded" and not args.bundle:
        print("ERROR: page-grounded mode requires --bundle.", file=sys.stderr)
        return 2

    try:
        card = args.card.read_text(encoding="utf-8")
        bundle = json.loads(args.bundle.read_text(encoding="utf-8")) if args.bundle else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = audit(card, bundle, args.locator_mode)
    print_report(report)

    if args.report:
        output = args.report.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote audit report: {output}")

    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
