"""Offline regressions: python -m unittest discover -s scripts -p selftest_keyword_contract.py."""
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_poll
from keyword_contract import keyword_contract, validate_keyword_contract


def legacy_card(stage="H"):
    card = {k: "available" for k in agent_poll._STR_FIELDS[stage]}
    card.update({k: [] for k in agent_poll._STR_LIST_FIELDS[stage]})
    card.update({k: [] for k in agent_poll._DICT_LIST_FIELDS[stage]})
    card["evidence"] = {"source": "abstract"}
    return card


def task(**sources):
    return {"keyword_contract": keyword_contract(), **sources}


def evidence(term, surface=None, field="abstract", quote=None):
    return {"term": term, "surface": surface or term, "source_field": field,
            "quote": quote or surface or term}


class ContractTests(unittest.TestCase):
    def test_legacy_eleven_fields_still_valid(self):
        self.assertEqual(len(legacy_card()), 11)
        self.assertEqual(agent_poll.card_shape_problems(legacy_card(), "H"), [])
        report = validate_keyword_contract({"keywords": ["glutamate"]}, {})
        self.assertEqual(report["keywords"], ["glutamate"])
        self.assertTrue(report["warnings"])

    def test_new_task_requires_both_fields_all_stages(self):
        for stage in agent_poll.STAGES:
            self.assertTrue(agent_poll.card_shape_problems(legacy_card(stage), stage, task()))
        for card in ({"keywords": []}, {"keyword_source_evidence": []}):
            self.assertTrue(validate_keyword_contract(card, task())["errors"])

    def test_empty_is_valid_and_warns(self):
        r = validate_keyword_contract({"keywords": [], "keyword_source_evidence": []}, task())
        self.assertFalse(r["errors"])
        self.assertTrue(r["warnings"])

    def test_strict_types(self):
        cards = [
            {"keywords": "glutamate", "keyword_source_evidence": []},
            {"keywords": [1], "keyword_source_evidence": []},
            {"keywords": [], "keyword_source_evidence": {}},
            {"keywords": [], "keyword_source_evidence": ["bad"]},
            {"keywords": [], "keyword_source_evidence": [{"term": "x"}]},
            {"keywords": ["x"], "keyword_source_evidence": [evidence("x", field="methods")]},
        ]
        for c in cards:
            with self.subTest(card=c):
                self.assertTrue(validate_keyword_contract(c, task())["errors"])
        for version in (True, "1", 2, None):
            self.assertTrue(validate_keyword_contract({}, {"keyword_contract": {"version": version}})["errors"])
        self.assertTrue(validate_keyword_contract({}, {"keyword_contract": {}})["errors"])

    def test_complete_terms_and_single_scientific_words(self):
        words = ["single-cell RNA sequencing", "glutamate", "electrophysiology", "突触可塑性"]
        c = {"keywords": words, "keyword_source_evidence": [evidence(w, field="author_keywords") for w in words]}
        r = validate_keyword_contract(c, task(author_keywords=words))
        self.assertFalse(r["errors"])
        self.assertEqual(r["keywords"], words)

    def test_one_untrusted_term_does_not_reject_card(self):
        c = legacy_card()
        c.update(keywords=["glutamate", "fabricated"], keyword_source_evidence=[evidence("glutamate"), evidence("fabricated")])
        t = task(abstract="glutamate signalling")
        before = copy.deepcopy(c)
        r = validate_keyword_contract(c, t)
        self.assertFalse(agent_poll.card_shape_problems(c, "H", t))
        self.assertEqual(r["keywords"], ["glutamate"])
        self.assertTrue(r["rejected"])
        self.assertEqual(c, before)

    def test_exact_quote_surface_and_field(self):
        t = task(title="Glutamate", abstract="glutamate response")
        for row in (evidence("glutamate", quote="Glutamate"),
                    evidence("glutamate", field="text"),
                    evidence("glutamate", quote="glutamate  response")):
            r = validate_keyword_contract({"keywords": ["glutamate"], "keyword_source_evidence": [row]}, t)
            self.assertEqual(r["keywords"], [])
            self.assertFalse(r["errors"])

    def test_alias_template_and_explicit_parenthetical(self):
        cases = [("machine learning", "机器学习", "机器学习"),
                 ("novel assay", "NA", "novel assay (NA) measures activity")]
        for term, surface, quote in cases:
            c = {"keywords": [term], "keyword_source_evidence": [evidence(term, surface, quote=quote)]}
            self.assertEqual(validate_keyword_contract(c, task(abstract=quote))["keywords"], [term])
        c = {"keywords": ["made up translation"], "keyword_source_evidence": [evidence("made up translation", "原词")]}
        self.assertFalse(validate_keyword_contract(c, task(abstract="原词"))["keywords"])

    def test_short_chinese_is_warned_and_max_is_advice(self):
        c = {"keywords": ["海马"], "keyword_source_evidence": [evidence("海马")]}
        r = validate_keyword_contract(c, task(abstract="海马"))
        self.assertEqual(r["keywords"], ["海马"])
        self.assertTrue(any("terminology review" in w for w in r["warnings"]))
        words = [f"protein {i}" for i in range(13)]
        c = {"keywords": words, "keyword_source_evidence": [evidence(w, field="author_keywords") for w in words]}
        r = validate_keyword_contract(c, task(author_keywords=words))
        self.assertEqual(len(r["keywords"]), 13)
        self.assertFalse(r["errors"])

    def test_poll_requires_remake_only_for_structural_error(self):
        with tempfile.TemporaryDirectory() as root:
            base = pathlib.Path(root)
            d = base / "interpret"
            (d / "tasks").mkdir(parents=True)
            (d / "results").mkdir()
            (d / "tasks" / "a.json").write_text(json.dumps(task(abstract="glutamate")), encoding="utf-8")
            path = d / "results" / "a.json"
            c = legacy_card()
            path.write_text(json.dumps(c), encoding="utf-8")
            with patch.object(agent_poll, "CDPDIR", base):
                self.assertEqual(agent_poll.survey("H", 0)["exit_code"], 4)
                c.update(keywords=["untrusted"], keyword_source_evidence=[evidence("untrusted")])
                path.write_text(json.dumps(c), encoding="utf-8")
                r = agent_poll.survey("H", 0)
                self.assertEqual(r["exit_code"], 0)
                self.assertEqual(r["keyword_warnings"][0]["keywords"], [])
                self.assertEqual(r["keyword_warning_count"], 1)
                path.write_text(json.dumps(["wrong root"]), encoding="utf-8")
                self.assertEqual(agent_poll.survey("H", 0)["exit_code"], 4)

    def test_h_and_h2_prepare_emit_versioned_source_fields(self):
        scripts = pathlib.Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as root:
            base = pathlib.Path(root)
            cdp = base / ".work_cdp"
            (cdp / "refs").mkdir(parents=True)
            record = {"doi": "10.5555/keyword-contract-fixture",
                      "title": "Synthetic glutamate review", "year": 2026,
                      "authors": [], "abstract": "glutamate response",
                      "author_keywords": ["glutamate"]}
            (cdp / "crossref_all.json").write_text(json.dumps({record["doi"]: record}), encoding="utf-8")
            sidecar = base / "synthetic.txt"
            sidecar.write_text("glutamate response in synthetic text", encoding="utf-8")
            ref = {**record, "fulltext_path": str(sidecar)}
            (cdp / "refs" / "final_list.json").write_text(json.dumps({"items": [ref]}), encoding="utf-8")
            env = {**os.environ, "PAPER_WORKDIR": root, "PAPER_AUTHOR": "Synthetic fixture"}
            for name, dirname, extra in (("interpret_prepare.py", "interpret", ["--no-text", "--engine=legacy"]),
                                          ("refs_prepare.py", "refs", [])):
                completed = subprocess.run([sys.executable, str(scripts / name), "--tier=L1", *extra],
                                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding="utf-8", timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                packet = json.loads(next((cdp / dirname / "tasks").glob("*.json")).read_text(encoding="utf-8"))
                self.assertEqual(packet["keyword_contract"]["version"], 1)
                self.assertEqual(packet["author_keywords"], ["glutamate"])
                self.assertEqual(packet["interpret_tier"], "L1")
                if dirname == "refs":
                    self.assertEqual(packet["text"], packet["fulltext"])
                    self.assertIn("synthetic text", packet["text"])

    def test_prepare_declares_contract_and_r2_preserves_author_keywords(self):
        import reviews_prepare
        with tempfile.TemporaryDirectory() as root:
            out = pathlib.Path(root)
            reviews_prepare.build([{"doi": "10.1/example", "title": "A review",
                                    "author_keywords": ["glutamate"]}], out, tier="L1")
            packet = json.loads(next((out / "tasks").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(packet["keyword_contract"]["version"], 1)
            self.assertEqual(packet["author_keywords"], ["glutamate"])
            self.assertEqual(packet["interpret_tier"], "L1")
        for name in ("interpret_prepare.py", "refs_prepare.py", "reviews_prepare.py"):
            source = pathlib.Path(__file__).with_name(name).read_text(encoding="utf-8")
            self.assertIn('"keyword_contract": keyword_contract()', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
