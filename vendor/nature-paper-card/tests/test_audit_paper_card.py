#!/usr/bin/env python3
"""Regression tests for Paper Card content auditing (FIX 5 and FIX 6)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "audit_paper_card.py"
SPEC = importlib.util.spec_from_file_location("nature_paper_card_audit", SCRIPT_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)

HEADINGS = [
    "Basic Information",
    "One-Sentence Summary",
    "Research Question",
    "Research Background and Development Path",
    "Core Pain Points Identified by the Paper",
    "Core Idea",
    "Method Overview",
    "Core Module Breakdown",
    "Essential Formulas and Symbols",
    "Experimental Design and Evidence Chain",
    "Correct Interpretation of the Conclusions",
    "Limitations Explicitly Acknowledged by the Authors",
    "Critical Analysis",
    "Knowledge Learned",
    "Connections to Existing Knowledge",
    "Research Ideas",
]

PREAMBLE = "\n".join(
    [
        "# Paper Card",
        "",
        "> Title: Synthetic card",
        "> Authors: Nobody",
        "> Venue: Nature",
        "> Year: 2026",
        "> Locator mode: structure-grounded",
        "> Source: synthetic",
        "> Reading date: 2026-01-01",
        "",
    ]
)


def build_card(bodies: dict[str, str]) -> str:
    parts = [PREAMBLE]
    for index, title in enumerate(HEADINGS, start=1):
        number = f"{index:02d}"
        parts.append(f"## {number} {title}\n")
        parts.append(bodies.get(number, "") + "\n")
    return "\n".join(parts)


def all_bodies(extra: str = "[Paper: Synthetic source, Abstract] Content.") -> dict[str, str]:
    return {f"{index:02d}": extra for index in range(1, 17)}


def codes(report: dict, level: str | None = None) -> dict[str, str]:
    return {
        item["code"]: item["level"]
        for item in report["findings"]
        if level is None or item["level"] == level
    }


class EmptyBodyTests(unittest.TestCase):
    """FIX 5a: all 16 headings with empty bodies must fail the audit."""

    def test_empty_bodies_are_errors(self) -> None:
        bodies = all_bodies()
        for number in [f"{index:02d}" for index in range(3, 16)]:
            bodies[number] = ""
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["section_bodies"], "error")
        self.assertGreater(report["summary"]["errors"], 0)
        self.assertEqual(report["summary"]["status"], "fail")
        details = next(
            item["details"]
            for item in report["findings"]
            if item["code"] == "section_bodies"
        )
        self.assertIn("03", details["empty_sections"])
        self.assertIn("15", details["empty_sections"])

    def test_pointer_only_section_counts_as_empty(self) -> None:
        bodies = all_bodies()
        bodies["07"] = "[Paper: Synthetic source, Abstract]"
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        details = next(
            item["details"]
            for item in report["findings"]
            if item["code"] == "section_bodies"
        )
        self.assertEqual(details["empty_sections"], ["07"])

    def test_not_applicable_is_content(self) -> None:
        bodies = all_bodies()
        bodies["09"] = "Not applicable."
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["section_bodies"], "pass")


class PerSectionPointerTests(unittest.TestCase):
    """FIX 5b: one pointer for the whole card is not enough."""

    def test_analysis_sections_need_their_own_pointer(self) -> None:
        bodies = all_bodies("[Paper: Synthetic source, Abstract] Content.")
        bodies["10"] = "Content without any pointer."
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["section_pointers"], "error")
        details = next(
            item["details"]
            for item in report["findings"]
            if item["code"] == "section_pointers"
        )
        self.assertEqual(details["sections_without_pointer"], ["10"])

    def test_optional_sections_only_warn(self) -> None:
        bodies = all_bodies("[Paper: Synthetic source, Abstract] Content.")
        bodies["02"] = "One-sentence summary without a pointer."
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["section_pointers"], "pass")
        self.assertEqual(codes(report)["optional_section_pointers"], "warning")

    def test_every_section_covered_passes(self) -> None:
        report = AUDIT.audit(build_card(all_bodies()), None, "structure-grounded")

        self.assertEqual(report["summary"]["errors"], 0)


class EvidenceMatchTests(unittest.TestCase):
    """FIX 5c: alias matching must be token-exact."""

    def test_figure_ten_does_not_satisfy_figure_one(self) -> None:
        self.assertFalse(AUDIT.evidence_item_mentioned("See Figure 10.", "Figure 1"))
        self.assertTrue(AUDIT.evidence_item_mentioned("See Figure 1.", "Figure 1"))

    def test_lettered_panel_does_not_satisfy_the_bare_number(self) -> None:
        self.assertFalse(AUDIT.evidence_item_mentioned("See Figure 1A.", "Figure 1"))
        self.assertTrue(AUDIT.evidence_item_mentioned("See Figure 1A.", "Figure 1A"))

    def test_chinese_aliases_still_match_next_to_cjk_characters(self) -> None:
        self.assertTrue(AUDIT.evidence_item_mentioned("如图1所示", "Figure 1"))
        self.assertFalse(AUDIT.evidence_item_mentioned("如表10所示", "Table 1"))

    def test_coverage_error_reports_the_unmatched_item(self) -> None:
        card = build_card(all_bodies("[Paper: Synthetic source, Abstract] Figure 10."))
        bundle = {
            "evidence_inventory": {
                "figures": [{"id": "Figure 1"}],
                "tables": [],
                "equations": [],
            }
        }
        report = AUDIT.audit(card, bundle, "structure-grounded")

        finding = next(
            item for item in report["findings"] if item["code"] == "figures_coverage"
        )
        self.assertEqual(finding["level"], "error")
        self.assertEqual(finding["details"]["missing"], ["Figure 1"])


class OutOfScopeTests(unittest.TestCase):
    """FIX 6: excluded terms only count in an instruction/intent context."""

    def test_ordinary_prose_does_not_error(self) -> None:
        bodies = all_bodies()
        bodies["13"] = (
            "[Paper: Synthetic source, Abstract] It is not a public article and no "
            "comprehension quiz is needed."
        )
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["out_of_scope"], "pass")
        self.assertEqual(report["summary"]["errors"], 0)

    def test_referring_to_the_excluded_outputs_in_prose_is_allowed(self) -> None:
        bodies = all_bodies()
        bodies["14"] = (
            "[Paper: Synthetic source, Abstract] No academic-English collection, "
            "comprehension quiz, or public-article draft was added."
        )
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["out_of_scope"], "pass")

    def test_out_of_scope_heading_still_errors(self) -> None:
        card = build_card(all_bodies()) + "\n## 学术英文词汇表\n\n写作词汇清单。\n"
        report = AUDIT.audit(card, None, "structure-grounded")

        self.assertEqual(codes(report)["out_of_scope"], "error")
        details = next(
            item["details"]
            for item in report["findings"]
            if item["code"] == "out_of_scope"
        )
        self.assertIn("学术英文", details["terms"])

    def test_production_verb_near_the_term_errors(self) -> None:
        card = build_card(all_bodies()) + "\n## Notes\n\nGenerate a comprehension quiz.\n"
        report = AUDIT.audit(card, None, "structure-grounded")

        self.assertEqual(codes(report)["out_of_scope"], "error")


class ReportSchemaTests(unittest.TestCase):
    """The JSON report schema stays backward compatible."""

    def test_existing_keys_are_unchanged(self) -> None:
        report = AUDIT.audit(build_card(all_bodies()), None, "structure-grounded")

        self.assertEqual(report["schema_version"], "1.0")
        for key in ("status", "passes", "warnings", "errors"):
            self.assertIn(key, report["summary"])
        for key in (
            "locator_mode",
            "sections_found",
            "paper_pointer_count",
            "figures_in_bundle",
            "tables_in_bundle",
            "equations_in_bundle",
        ):
            self.assertIn(key, report["metrics"])
        for item in report["findings"]:
            self.assertIn("level", item)
            self.assertIn("code", item)
            self.assertIn("message", item)
        json.dumps(report)

    def test_all_check_codes_still_emit(self) -> None:
        report = AUDIT.audit(build_card(all_bodies()), None, "structure-grounded")
        emitted = {item["code"] for item in report["findings"]}

        for code in (
            "sections",
            "forbidden_sections",
            "status_header",
            "locator_mode_declaration",
            "paper_pointers",
            "page_pointers_forbidden",
            "source_inventory_unavailable",
            "limitations_split",
            "idea_status",
            "idea_validation",
            "idea_failure",
            "out_of_scope",
        ):
            self.assertIn(code, emitted)

    def test_page_pointers_are_forbidden_in_structure_mode(self) -> None:
        bodies = all_bodies()
        bodies["10"] = "[Paper: Synthetic source, PDF p. 4, Methods] Evidence."
        report = AUDIT.audit(build_card(bodies), None, "structure-grounded")

        self.assertEqual(codes(report)["page_pointers_forbidden"], "error")


if __name__ == "__main__":
    unittest.main()
