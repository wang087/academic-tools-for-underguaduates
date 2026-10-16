#!/usr/bin/env python3
"""Regression tests for source-map page-locator normalization."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "prepare_paper.py"
SPEC = importlib.util.spec_from_file_location("nature_paper_card_prepare", SCRIPT_PATH)
assert SPEC and SPEC.loader
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)

LONG_TEXT = (
    "This source block is deliberately longer than fifty characters so bundle "
    "validation accepts it."
)


class SourceMapLocatorTests(unittest.TestCase):
    def prepare(self, blocks: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source-map.json"
            source.write_text(
                json.dumps({"metadata": {"title": "Test paper"}, "blocks": blocks}),
                encoding="utf-8",
            )
            bundle = PREPARE.prepare_source_map(source)
            bundle["validation"] = PREPARE.validate_bundle(bundle)
            return bundle

    def test_verified_page_one_remains_page_one(self) -> None:
        bundle = self.prepare([{"id": "S001", "page": 1, "text": LONG_TEXT}])

        self.assertEqual([page["pdf_page"] for page in bundle["pages"]], [1])
        self.assertEqual(bundle["unlocated_blocks"], [])
        self.assertEqual(bundle["locator_summary"]["verified_page_blocks"], 1)

    def test_missing_locator_does_not_become_page_one(self) -> None:
        bundle = self.prepare([{"id": "S001", "text": LONG_TEXT}])

        self.assertEqual(bundle["pages"], [])
        self.assertEqual(bundle["page_count"], 0)
        self.assertEqual(bundle["unlocated_blocks"][0]["locator_status"], "missing")
        self.assertEqual(bundle["validation"]["recommended_locator_mode"], "structure-grounded")
        self.assertEqual(bundle["validation"]["status"], "valid_with_warnings")
        self.assertTrue(
            any("no page locator" in warning for warning in bundle["validation"]["warnings"])
        )

    def test_non_numeric_locator_is_preserved_as_invalid(self) -> None:
        bundle = self.prepare(
            [{"id": "S001", "page": "not-a-page", "text": LONG_TEXT}]
        )

        block = bundle["unlocated_blocks"][0]
        self.assertEqual(bundle["pages"], [])
        self.assertEqual(block["locator_status"], "invalid")
        self.assertEqual(block["locator_field"], "page")
        self.assertEqual(block["locator_value"], "not-a-page")

    def test_zero_locator_is_preserved_as_invalid(self) -> None:
        bundle = self.prepare([{"id": "S001", "page": 0, "text": LONG_TEXT}])

        block = bundle["unlocated_blocks"][0]
        self.assertEqual(bundle["pages"], [])
        self.assertEqual(block["locator_status"], "invalid")
        self.assertEqual(block["locator_value"], 0)

    def test_unknown_location_text_remains_available_for_structure_analysis(self) -> None:
        equation_text = (
            "Methods\nThe model minimizes the following objective while retaining "
            "all structural evidence from the supplied source map. (2)"
        )
        bundle = self.prepare([{"id": "S009", "text": equation_text}])

        self.assertEqual(bundle["unlocated_blocks"][0]["text"], equation_text)
        self.assertEqual(bundle["sections"][0]["title"], "Methods")
        self.assertIsNone(bundle["sections"][0]["pdf_page"])
        self.assertEqual(bundle["evidence_inventory"]["equations"][0]["id"], "Equation 2")
        self.assertIsNone(bundle["evidence_inventory"]["equations"][0]["pdf_page"])

    def test_mixed_locators_never_merge_unknown_text_into_page_one(self) -> None:
        bundle = self.prepare(
            [
                {"id": "S001", "page": 1, "text": LONG_TEXT},
                {"id": "S002", "text": "Unlocated " + LONG_TEXT},
                {"id": "S003", "page": "bad", "text": "Invalid " + LONG_TEXT},
            ]
        )

        self.assertEqual(len(bundle["pages"]), 1)
        self.assertEqual(bundle["pages"][0]["text"], LONG_TEXT)
        self.assertEqual(len(bundle["unlocated_blocks"]), 2)
        self.assertEqual(
            bundle["locator_summary"],
            {
                "verified_page_blocks": 1,
                "missing_page_locator_blocks": 1,
                "invalid_page_locator_blocks": 1,
                "page_locator_reliability": "mixed",
            },
        )

    def test_cli_preserves_an_invalid_locator_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-map.json"
            output = root / "source-bundle.json"
            source.write_text(
                json.dumps(
                    {
                        "metadata": {"title": "CLI test"},
                        "blocks": [{"page": "not-a-page", "text": LONG_TEXT}],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(source), "--output", str(output)],
                check=False,
                capture_output=True,
                text=True,
            )
            bundle = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(bundle["pages"], [])
        self.assertEqual(bundle["unlocated_blocks"][0]["locator_status"], "invalid")
        self.assertIn("invalid page locators", "\n".join(bundle["validation"]["warnings"]))


class NatureReaderSchemaTests(unittest.TestCase):
    """FIX 1: the official nature-reader block field is `original_text`."""

    def prepare(self, blocks: list[dict], top_level: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source-map.json"
            payload: dict = {"blocks": blocks}
            payload.update(top_level or {})
            source.write_text(json.dumps(payload), encoding="utf-8")
            bundle = PREPARE.prepare_source_map(source)
            bundle["validation"] = PREPARE.validate_bundle(bundle)
            return bundle

    def test_original_text_is_read(self) -> None:
        bundle = self.prepare(
            [{"id": "S001", "page": 1, "original_text": LONG_TEXT, "translation": "翻译"}]
        )

        self.assertEqual(bundle["page_count"], 1)
        self.assertEqual(bundle["pages"][0]["text"], LONG_TEXT)
        self.assertNotEqual(bundle["validation"]["status"], "invalid")
        self.assertEqual(
            bundle["validation"]["recommended_locator_mode"], "page-grounded"
        )

    def test_translation_is_the_last_resort(self) -> None:
        translated = "这是一个只有译文而没有原文的来源区块，长度足够通过校验阈值。"
        bundle = self.prepare([{"id": "S001", "page": 1, "translation": translated}])

        self.assertEqual(bundle["pages"][0]["text"], translated)

    def test_paper_title_is_read_from_the_reader_paper_object(self) -> None:
        bundle = self.prepare(
            [{"id": "S001", "page": 1, "original_text": LONG_TEXT}],
            top_level={"paper": {"title": "Reader title"}},
        )

        self.assertEqual(bundle["metadata"]["title"], "Reader title")
        self.assertNotIn(
            "document title is unavailable", bundle["validation"]["warnings"]
        )


class LocatorModeRecommendationTests(unittest.TestCase):
    """FIX 2: the recommendation must follow the computed locator summary."""

    def prepare(self, blocks: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source-map.json"
            source.write_text(
                json.dumps({"metadata": {"title": "Test paper"}, "blocks": blocks}),
                encoding="utf-8",
            )
            bundle = PREPARE.prepare_source_map(source)
            bundle["validation"] = PREPARE.validate_bundle(bundle)
            return bundle

    def test_reliable_locators_recommend_page_grounded(self) -> None:
        bundle = self.prepare(
            [
                {"id": "S001", "page": 3, "text": LONG_TEXT},
                {"id": "S002", "page": 7, "text": "Second " + LONG_TEXT},
            ]
        )

        self.assertEqual(bundle["locator_summary"]["page_locator_reliability"], "reliable")
        self.assertEqual(bundle["locator_summary"]["verified_page_blocks"], 2)
        self.assertEqual(
            bundle["validation"]["recommended_locator_mode"], "page-grounded"
        )

    def test_mixed_locators_stay_structure_grounded(self) -> None:
        bundle = self.prepare(
            [
                {"id": "S001", "page": 3, "text": LONG_TEXT},
                {"id": "S002", "text": "Unlocated " + LONG_TEXT},
            ]
        )

        self.assertEqual(bundle["locator_summary"]["page_locator_reliability"], "mixed")
        self.assertEqual(
            bundle["validation"]["recommended_locator_mode"], "structure-grounded"
        )


class CaptionPatternTests(unittest.TestCase):
    """FIX 4: real publisher captions separate the number with . | tab or 2+ spaces."""

    def inventory(self, lines: list[str]) -> dict:
        page = {
            "pdf_page": 1,
            "printed_page": None,
            "text": "\n".join(lines),
            "character_count": sum(len(line) for line in lines),
        }
        return PREPARE.evidence_from_pages([page])

    def figure_ids(self, lines: list[str]) -> list[str]:
        return [item["id"] for item in self.inventory(lines)["figures"]]

    def table_ids(self, lines: list[str]) -> list[str]:
        return [item["id"] for item in self.inventory(lines)["tables"]]

    def test_period_separator_is_a_caption(self) -> None:
        lines = ["Figure 1. Stabilization of mouse haploid embryonic stem cells."]
        self.assertEqual(self.figure_ids(lines), ["Figure 1"])

    def test_pipe_separator_is_a_caption(self) -> None:
        lines = ["Fig. 2 | Chemical cocktail restrains self-diploidization."]
        self.assertEqual(self.figure_ids(lines), ["Figure 2"])

    def test_tab_and_multi_space_separators_are_captions(self) -> None:
        self.assertEqual(self.table_ids(["Table 1\tRT-qPCR Primer Sequences."]), ["Table 1"])
        self.assertEqual(self.table_ids(["Table 2  Bisulfite PCR Primer Sequences."]), ["Table 2"])

    def test_colon_separator_still_works(self) -> None:
        self.assertEqual(self.figure_ids(["Figure 3: Legacy colon caption."]), ["Figure 3"])

    def test_inline_mentions_are_not_captions(self) -> None:
        lines = [
            "As shown in Fig. 1A, the growth rate increases.",
            "Figure 5 lists every condition.",
            "Compared with Table 2, the effect is larger.",
        ]
        self.assertEqual(self.figure_ids(lines), [])
        self.assertEqual(self.table_ids(lines), [])

    def test_numbered_sub_table_is_not_a_caption(self) -> None:
        self.assertEqual(self.table_ids(["Table 1.2 Primer sequences for the assay."]), [])

    def test_lettered_panel_caption_keeps_its_suffix(self) -> None:
        self.assertEqual(self.figure_ids(["Fig. 4A. Representative morphologies."]), ["Figure 4A"])


class TextSoftWrapTests(unittest.TestCase):
    """FIX 7: long page text must not become one unbounded JSON line."""

    def prepare(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source-map.json"
            source.write_text(
                json.dumps(
                    {"metadata": {"title": "Wrap test"}, "blocks": [{"page": 1, "text": text}]}
                ),
                encoding="utf-8",
            )
            bundle = PREPARE.prepare_source_map(source)
            bundle["validation"] = PREPARE.validate_bundle(bundle)
            return bundle

    def assert_wrapped(self, page: dict, text: str) -> None:
        chunks = page["text_wrapped_lines"]
        self.assertTrue(page["text_soft_wrapped"])
        self.assertEqual(page["text_wrap_width"], PREPARE.TEXT_WRAP_WIDTH)
        self.assertEqual(page["text"], text)
        self.assertGreater(page["text_wrapped_line_count"], 1)
        self.assertLessEqual(page["text_wrapped_max_line_length"], PREPARE.TEXT_WRAP_WIDTH)
        self.assertGreater(page["serialized_text_line_length"], PREPARE.TEXT_WRAP_WIDTH)
        # lossless: the wrapped view restores the canonical text exactly
        self.assertEqual("".join(chunks), text)
        # and every chunk is a bounded physical JSON line
        for chunk in chunks:
            self.assertLessEqual(
                len(json.dumps(chunk, ensure_ascii=False)) - 2, PREPARE.TEXT_WRAP_WIDTH
            )

    def test_long_page_text_is_soft_wrapped_and_preserved(self) -> None:
        words = " ".join(f"w{index}" for index in range(900))
        bundle = self.prepare(words)

        self.assert_wrapped(bundle["pages"][0], words)
        self.assertEqual(bundle["validation"]["errors"], [])

    def test_wrap_bound_holds_without_any_whitespace(self) -> None:
        unbroken = "x" * 5000
        self.assert_wrapped(self.prepare(unbroken)["pages"][0], unbroken)

    def test_multi_line_page_text_round_trips_through_json(self) -> None:
        text = "\n".join(
            [
                "Figure 1. " + " ".join(f"token{index}" for index in range(700)),
                "Results",
                "Methods",
            ]
        )
        bundle = self.prepare(text)
        on_disk = json.loads(json.dumps(bundle))
        page = on_disk["pages"][0]

        self.assertEqual(page["pdf_page"], 1)
        self.assertEqual("".join(page["text_wrapped_lines"]), text)

    def test_wrapped_lines_are_declared_in_the_bundle(self) -> None:
        bundle = self.prepare("short page text")

        layout = bundle["text_layout"]
        self.assertTrue(layout["text_soft_wrapped"])
        self.assertEqual(layout["text_wrap_width"], PREPARE.TEXT_WRAP_WIDTH)
        self.assertEqual(layout["canonical_field"], "text")
        self.assertEqual(layout["wrapped_field"], "text_wrapped_lines")
        self.assertEqual(bundle["pages"][0]["text_wrapped_lines"], ["short page text"])


if __name__ == "__main__":
    unittest.main()
