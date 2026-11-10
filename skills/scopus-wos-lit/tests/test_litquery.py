# -*- coding: utf-8 -*-
"""Offline tests for the litquery toolkit.

Run from the project root:
    python -m unittest discover -s tests -v

No network and no browser are used here. Live browser behaviour is documented in
references/browser-cdp.md and was verified separately against Scopus/WoS.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from litquery import journals as journal_mod  # noqa: E402
from litquery import query as query_mod  # noqa: E402
from litquery import records as recmod  # noqa: E402
from litquery import report as report_mod  # noqa: E402
from litquery import screen as screen_mod  # noqa: E402
from litquery import taxonomy as taxonomy_mod  # noqa: E402
from litquery import web  # noqa: E402
from litquery.util import Log, parse_number  # noqa: E402

TAXONOMY = HERE.parent / "assets" / "direction_taxonomy.json"


def base_plan():
    return {
        "schema_version": "litquery-plan/1.0",
        "topic": {"zh": "肠道菌群干预与抑郁", "en": "gut microbiota intervention and depression"},
        "concepts": [
            {
                "id": "c1",
                "label": "暴露",
                "role": "core",
                "terms": ["gut microbiome", "microbiota"],
                "synonyms": ["gut microbiota"],
                "broader": ["human microbiome"],
                "narrower": ["fecal microbiota transplantation"],
                "proximity": 2,
                "exact": True,
            },
            {
                "id": "c2",
                "label": "结局",
                "role": "context",
                "terms": ["depression"],
                "synonyms": ["depressive disorder"],
                "broader": ["mood disorder"],
                "narrower": ["postpartum depression"],
                "proximity": 2,
                "exact": True,
            },
        ],
        "exclude": ["animal model"],
        "filters": {"year_from": 2018, "year_to": None, "document_types": ["review"], "languages": ["English"]},
    }


class TaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tax = taxonomy_mod.load(TAXONOMY)

    def test_path_match_reaches_leaf(self):
        hit = taxonomy_mod.match("生物学 > 免疫与微生物 > 人体微生物组学", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["path"][-1], "人体微生物组学")
        self.assertEqual(hit["match_type"], "path")

    def test_chinese_leaf_name(self):
        hit = taxonomy_mod.match("人体微生物组学", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["node"]["en"], "Human Microbiome")

    def test_english_keyword(self):
        hit = taxonomy_mod.match("microbiome", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["node"]["zh"], "人体微生物组学")

    def test_chinese_conjunction_variant(self):
        hit = taxonomy_mod.match("神经与退行性疾病", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["path"][-1], "神经退行性疾病")
        self.assertEqual(hit["match_type"], "exact")

    def test_english_neurodegeneration(self):
        hit = taxonomy_mod.match("neurodegeneration", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["node"]["zh"], "神经退行性疾病")

    def test_neural_stem_cell_direction(self):
        hit = taxonomy_mod.match("神经干细胞多样性与人脑发育功能", self.tax)
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["path"][-1], "神经干细胞与脑发育")

    def test_unmatched(self):
        hit = taxonomy_mod.match("某某不存在的方向", self.tax)
        self.assertFalse(hit["matched"])

    def test_plan_has_keywords_and_concepts(self):
        plan = taxonomy_mod.build_plan("免疫与微生物", self.tax)
        self.assertTrue(plan["english_keywords"])
        self.assertTrue(plan["concepts"])
        self.assertEqual(plan["filters"]["document_types"], ["review"])

    def test_list_tree(self):
        rows = taxonomy_mod.list_tree(self.tax, "免疫")
        self.assertTrue(any(r["leaf"] for r in rows))


class QueryBuildTests(unittest.TestCase):
    def test_scopus_exact_query(self):
        built = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus"))
        q = built["query"]
        self.assertTrue(q.startswith("TITLE-ABS-KEY(("))
        self.assertIn("{gut microbiome} OR microbiota OR {gut microbiota}", q)
        self.assertIn("(depression OR {depressive disorder})", q)
        self.assertIn("PUBYEAR > 2017", q)
        self.assertIn("DOCTYPE(re)", q)
        self.assertIn("LANGUAGE(english)", q)
        self.assertIn("AND NOT TITLE-ABS-KEY({animal model})", q)

    def test_wos_query(self):
        built = query_mod.build(base_plan(), query_mod.BuildOptions(engine="wos"))
        q = built["query"]
        self.assertTrue(q.startswith("TS=(("))
        self.assertIn('"gut microbiome" OR microbiota OR "gut microbiota"', q)
        self.assertIn("DT=(Review)", q)
        self.assertIn("LA=(English)", q)
        self.assertIn("NOT TS=(\"animal model\")", q)
        self.assertNotIn("{", q)

    def test_proximity_mode(self):
        built = query_mod.build(
            base_plan(),
            query_mod.BuildOptions(engine="scopus", phrase_mode="proximity", proximity=3),
        )
        self.assertIn("gut W/3 microbiome", built["query"])
        self.assertNotIn("{gut microbiome}", built["query"])

    def test_wildcards_are_conservative(self):
        built = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus", wildcards=True))
        self.assertIn("microbiota*", built["query"])
        # Multi-word phrases keep their exact form; no wildcard is injected.
        self.assertIn("{gut microbiome}", built["query"])
        self.assertNotIn("{gut microbiome*}", built["query"])

    def test_filters_can_be_dropped(self):
        built = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus", include_filters=False))
        self.assertNotIn("PUBYEAR", built["query"])
        self.assertNotIn("DOCTYPE", built["query"])

    def test_drop_context(self):
        built = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus", drop_roles=["context"]))
        self.assertIn("gut microbiome", built["query"])
        self.assertNotIn("depressive disorder", built["query"])

    def test_sha_is_stable(self):
        a = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus"))
        b = query_mod.build(base_plan(), query_mod.BuildOptions(engine="scopus"))
        self.assertEqual(a["sha256"], b["sha256"])
        self.assertEqual(len(a["sha256"]), 64)

    def test_empty_concepts_rejected(self):
        with self.assertRaises(ValueError):
            query_mod.build({"concepts": []}, query_mod.BuildOptions(engine="scopus"))


class LintTests(unittest.TestCase):
    def test_valid(self):
        result = query_mod.lint("TS=(gut AND microbiota) AND PY=(2018-2026) AND DT=(Review)", "wos")
        self.assertTrue(result["ok"], result)

    def test_unbalanced(self):
        result = query_mod.lint("TITLE-ABS-KEY((gut AND microbiota)", "scopus")
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["code"] == "paren" for e in result["errors"]))

    def test_brace_in_wos(self):
        result = query_mod.lint('TS=({gut microbiome})', "wos")
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["code"] == "brace_wos" for e in result["errors"]))

    def test_and_not_in_wos(self):
        result = query_mod.lint('TS=(gut) AND NOT TS=(animal)', "wos")
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["code"] == "and_not_wos" for e in result["errors"]))

    def test_bad_proximity(self):
        result = query_mod.lint("TITLE-ABS-KEY(gut W/0 microbiome)", "scopus")
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["code"] == "prox_range" for e in result["errors"]))

    def test_near_in_scopus(self):
        result = query_mod.lint("TITLE-ABS-KEY(gut NEAR/3 microbiome)", "scopus")
        self.assertFalse(result["ok"])

    def test_unknown_tag_warns(self):
        result = query_mod.lint("TITLE-ABS-KEY(gut) AND XYZ=(foo)", "scopus")
        self.assertTrue(result["ok"])
        self.assertTrue(any(w["code"] == "field_tag" for w in result["warnings"]))

    def test_empty(self):
        self.assertFalse(query_mod.lint("", "scopus")["ok"])


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.table = journal_mod.JournalTable(
            [
                {"title": "Nature Reviews Microbiology", "cas_tier": 1, "jcr_quartile": "Q1"},
                {"title": "Top Journal", "jif_percentile": 92.5, "jcr_quartile": "Q1"},
                {"title": "Mid Journal", "jif_percentile": 61.0, "jcr_quartile": "Q2"},
                {"title": "Q1 Only Journal", "jcr_quartile": "Q1"},
            ],
            source="user",
        )

    def test_normalize(self):
        self.assertEqual(journal_mod.normalize_title("The Nature Reviews: Microbiology"), "nature reviews microbiology")

    def test_percentile_pass(self):
        rank = self.table.rank("Top Journal", top_percent=20)
        self.assertTrue(rank["passes"])
        self.assertFalse(rank["approx"])

    def test_percentile_fail(self):
        rank = self.table.rank("Mid Journal", top_percent=20)
        self.assertFalse(rank["passes"])

    def test_cas_proxy_is_marked(self):
        rank = self.table.rank("Nature Reviews Microbiology", top_percent=20)
        self.assertTrue(rank["passes"])
        self.assertTrue(rank["approx"])

    def test_q1_is_not_automatically_top20(self):
        self.assertFalse(self.table.rank("Q1 Only Journal", top_percent=20)["passes"])
        self.assertTrue(self.table.rank("Q1 Only Journal", top_percent=20, accept_q1=True)["passes"])

    def test_fuzzy(self):
        entry, how = self.table.lookup("Nature Review Microbiology")
        self.assertIsNotNone(entry)
        self.assertEqual(how, "fuzzy")

    def test_unknown(self):
        rank = self.table.rank("Some Unknown Journal")
        self.assertFalse(rank["matched"])
        self.assertFalse(rank["passes"])

    def test_csv_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = pathlib.Path(tmp) / "jcr.csv"
            csv_path.write_text(
                "Journal name,ISSN,2024 JIF,JIF Quartile,JIF Percentile,Category\n"
                "Journal A,1234-5678,12.3,Q1,95.5,Microbiology\n"
                "Journal B,8765-4321,3.1,Q2,55.0,Microbiology\n",
                encoding="utf-8",
            )
            out = pathlib.Path(tmp) / "tiers.json"
            result = journal_mod.import_csv(csv_path, "jcr", out)
            self.assertEqual(result["imported"], 2)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["journals"][0]["jif_percentile"], 95.5)

    def test_seed_loads(self):
        seed = journal_mod.load_seed()
        self.assertGreater(seed.stats()["entries"], 50)


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.table = journal_mod.JournalTable(
            [
                {"title": "Top Journal", "jif_percentile": 95},
                {"title": "Nature Reviews Microbiology", "cas_tier": 1},
                {"title": "Mid Journal", "jif_percentile": 50},
            ]
        )
        self.opts = screen_mod.SelectOptions(now_year=2026, limit=5)

    def record(self, **kwargs):
        record = {
            "id": kwargs.get("id", "doi:10.1/x"),
            "title": kwargs.get("title", "A review"),
            "journal": kwargs.get("journal", "Top Journal"),
            "year": kwargs.get("year", 2022),
            "cited_by": kwargs.get("cited_by", 100),
            "document_type": kwargs.get("document_type", "review"),
            "abstract": kwargs.get("abstract", "abstract"),
            "doi": kwargs.get("doi", "10.1/x"),
        }
        return recmod._finalize(record)

    def test_good_review_selected(self):
        result = screen_mod.select([self.record()], self.table, self.opts)
        self.assertEqual(len(result["selected"]), 1)
        self.assertTrue(result["selected"][0]["journal_rank"]["passes"])

    def test_mid_journal_rejected(self):
        result = screen_mod.select([self.record(journal="Mid Journal")], self.table, self.opts)
        self.assertEqual(result["selected"], [])
        self.assertGreater(result["stats"]["rejected_by_reason"]["journal_not_top20"], 0)

    def test_old_but_foundational_kept(self):
        old = self.record(year=2005, cited_by=1200, id="doi:10.1/old", doi="10.1/old")
        result = screen_mod.select([old], self.table, self.opts)
        self.assertEqual(len(result["selected"]), 1)
        self.assertIn("foundational", result["selected"][0].get("flags", []))

    def test_old_and_weak_rejected(self):
        old = self.record(year=2005, cited_by=20, id="doi:10.1/old2", doi="10.1/old2")
        result = screen_mod.select([old], self.table, self.opts)
        self.assertEqual(result["selected"], [])

    def test_new_review_uses_lower_floor(self):
        new = self.record(year=2026, cited_by=6, id="doi:10.1/new", doi="10.1/new")
        result = screen_mod.select([new], self.table, self.opts)
        self.assertEqual(len(result["selected"]), 1)

    def test_unknown_rank_strict_rejected(self):
        result = screen_mod.select([self.record(journal="Unknown Journal")], self.table, self.opts)
        self.assertEqual(result["selected"], [])

    def test_unknown_rank_balanced(self):
        opts = screen_mod.SelectOptions(now_year=2026, rank_policy="balanced", limit=5)
        result = screen_mod.select([self.record(journal="Unknown Journal", cited_by=300)], self.table, opts)
        self.assertEqual(len(result["selected"]), 1)

    def test_non_review_rejected(self):
        result = screen_mod.select([self.record(document_type="article", title="Random article")], self.table, self.opts)
        self.assertEqual(result["selected"], [])

    def test_journal_cap(self):
        records = [
            self.record(id=f"doi:10.1/{i}", doi=f"10.1/{i}", title=f"Review {i}")
            for i in range(4)
        ]
        opts = screen_mod.SelectOptions(now_year=2026, limit=5, max_per_journal=2)
        result = screen_mod.select(records, self.table, opts)
        self.assertLessEqual(len(result["selected"]), 2)

    def test_off_topic_rejected(self):
        opts = screen_mod.SelectOptions(now_year=2026, limit=5, require_topic_terms=["microbiome", "microbiota"])
        record = self.record(title="Hallmarks of aging", abstract="A review of aging and metabolism.")
        result = screen_mod.select([record], self.table, opts)
        self.assertEqual(result["selected"], [])
        self.assertGreater(result["stats"]["rejected_by_reason"]["off_topic"], 0)

    def test_topic_title_hit_passes(self):
        opts = screen_mod.SelectOptions(now_year=2026, limit=5, require_topic_terms=["microbiome"])
        record = self.record(title="The human microbiome in health", abstract="Short abstract.")
        result = screen_mod.select([record], self.table, opts)
        self.assertEqual(len(result["selected"]), 1)

    def test_topic_unverified_without_abstract(self):
        opts = screen_mod.SelectOptions(now_year=2026, limit=5, require_topic_terms=["microbiome"])
        record = self.record(title="Aging and metabolism", abstract="")
        result = screen_mod.select([record], self.table, opts)
        self.assertEqual(len(result["selected"]), 1)
        self.assertIn("unverified", " ".join(result["selected"][0]["selection"]["reasons"]))

    def test_out_of_scope_rejected(self):
        opts = screen_mod.SelectOptions(now_year=2026, limit=5, scope_terms=["human", "patient"])
        record = self.record(title="Plant-microbiome interactions", abstract="Soil and root communities.")
        result = screen_mod.select([record], self.table, opts)
        self.assertEqual(result["selected"], [])
        self.assertGreater(result["stats"]["rejected_by_reason"]["out_of_scope"], 0)


class RecordTests(unittest.TestCase):
    def test_inverted_index(self):
        text = recmod.inverted_index_to_text({"gut": [0], "microbiome": [1], "and": [2], "depression": [3]})
        self.assertEqual(text, "gut microbiome and depression")

    def test_openalex_conversion(self):
        work = {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1000/ABC",
            "display_name": "Gut microbiome and depression: a review",
            "publication_year": 2023,
            "cited_by_count": 42,
            "type": "review",
            "abstract_inverted_index": {"Gut": [0], "microbes": [1]},
            "authorships": [
                {
                    "author_position": "first",
                    "author": {"display_name": "Jane Doe", "orcid": "https://orcid.org/0000-0001"},
                    "institutions": [{"display_name": "Example University"}],
                    "is_corresponding": False,
                },
                {
                    "author_position": "last",
                    "author": {"display_name": "John Smith"},
                    "institutions": [],
                    "is_corresponding": True,
                },
            ],
            "primary_location": {"source": {"display_name": "Nature Reviews Microbiology", "issn_l": "1740-1526"}},
            "best_oa_location": {"pdf_url": "https://example.org/x.pdf"},
            "open_access": {"is_oa": True},
        }
        record = recmod.from_openalex(work)
        self.assertEqual(record["doi"], "10.1000/abc")
        self.assertEqual(record["journal"], "Nature Reviews Microbiology")
        self.assertEqual(record["document_type"], "review")
        self.assertEqual(record["first_author"], "Jane Doe")
        self.assertEqual(record["corresponding_authors"], ["John Smith"])
        self.assertFalse(record["corresponding_inferred"])
        self.assertEqual(record["abstract"], "Gut microbes")

    def test_crossref_first_author(self):
        item = {
            "DOI": "10.1/ABC",
            "title": ["A review of things"],
            "author": [
                {"given": "A", "family": "One", "sequence": "first"},
                {"given": "B", "family": "Two", "sequence": "additional"},
            ],
            "container-title": ["Journal X"],
            "issued": {"date-parts": [[2021]]},
            "is-referenced-by-count": 7,
            "type": "journal-article",
        }
        record = recmod.from_crossref(item)
        self.assertEqual(record["first_author"], "A One")
        self.assertEqual(record["doi"], "10.1/abc")
        self.assertIn("review", record["document_type"])

    def test_corresponding_inferred(self):
        record = recmod._finalize(
            {"title": "X", "authors": [{"name": "First"}, {"name": "Last"}]}
        )
        self.assertEqual(record["corresponding_authors"], ["Last"])
        self.assertTrue(record["corresponding_inferred"])

    def test_dedupe_and_merge(self):
        a = {"title": "Same title", "doi": "10.1/x", "journal": "J", "authors": []}
        b = {"title": "Same title", "doi": "10.1/X", "abstract": "filled later", "cited_by": 5}
        result = recmod.dedupe([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["abstract"], "filled later")

    def test_stats(self):
        stats = recmod.stats([{"year": 2020, "doi": "x", "abstract": "a", "document_type": "review"}])
        self.assertEqual(stats["records"], 1)
        self.assertEqual(stats["reviews"], 1)


class WebParseTests(unittest.TestCase):
    def test_scopus_count(self):
        self.assertEqual(web.count_from_text("93,652 documents found", "scopus")[0], 93652)

    def test_scopus_zero(self):
        self.assertEqual(web.count_from_text("No documents found", "scopus")[0], 0)

    def test_wos_count(self):
        text = "17,142 results from Web of Science Core Collection for:"
        self.assertEqual(web.count_from_text(text, "wos")[0], 17142)

    def test_wos_zero(self):
        self.assertEqual(web.count_from_text("No results found", "wos")[0], 0)

    def test_unparsed_is_none(self):
        self.assertIsNone(web.count_from_text("Loading...", "scopus")[0])

    def test_split_source(self):
        journal, volume, issue = web._split_source("Medical Gas Research\n, 17(1)")
        self.assertEqual(journal, "Medical Gas Research")
        self.assertEqual((volume, issue), ("17", "1"))

    def test_first_author_multiline(self):
        text = "Gopal, R.K.\n, \nGanesh, P.S.\n, \nPathoor, N.N"
        self.assertEqual(web._first_author(text), "Gopal, R.K")

    def test_extract_dois(self):
        html = '<a href="https://doi.org/10.1038/s41586-020-1234-5">x</a> 10.1126/science.abc123.'
        dois = web.extract_dois(html)
        self.assertIn("10.1038/s41586-020-1234-5", dois)
        self.assertIn("10.1126/science.abc123", dois)

    def test_detect_challenge(self):
        state, _ = web.detect_state("Verify you are human", "https://x", "Just a moment...")
        self.assertEqual(state, "challenge")

    def test_detect_elsevier_login(self):
        state, _ = web.detect_state(
            "Enter your work email",
            "https://id.elsevier.com/as/authorization.oauth2?prompt=login&client_id=SCOPUS",
            "Welcome",
        )
        self.assertEqual(state, "login")

    def test_garbage_abstract_fragment_rejected(self):
        fragment = (
            "s Service (CAS) to ensure accurate identification and tracking of chemicals across scientific literature. "
            "brexpiprazole 913611-97-9 diazepam 439-14-5, 11100-37-1, 53320-84-6 melatonin 73-31-4 metformin 1115-70-4"
        )
        self.assertFalse(web.looks_like_abstract(fragment))

    def test_real_abstract_accepted(self):
        abstract = (
            "Alzheimer disease is the most common form of dementia worldwide. "
            "Neuroinflammation has a prominent role in its pathogenesis. "
            "This review discusses the interrelationships between neuroinflammation and amyloid pathology. "
            "We also consider how microglia could be modulated as a therapeutic strategy."
        )
        self.assertTrue(web.looks_like_abstract(abstract))

    def test_scopus_url(self):
        url = web.scopus_url('TITLE-ABS-KEY("gut microbiome")', page=2)
        self.assertIn("page=2", url)
        self.assertIn("results.uri", url)


class NumberTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_number("1,234"), 1234)
        self.assertEqual(parse_number("1.234"), 1234)
        self.assertEqual(parse_number("12 345"), 12345)
        self.assertEqual(parse_number("1,234,567"), 1234567)
        self.assertEqual(parse_number(42), 42)
        self.assertIsNone(parse_number("none"))


class EuropePMCTests(unittest.TestCase):
    def test_abstracts_batch(self):
        from litquery.backends.europepmc import EuropePMC

        payload = {
            "resultList": {
                "result": [
                    {"doi": "10.1/A", "abstractText": "<p>First abstract</p>"},
                    {"doi": "10.1/B", "abstractText": "Second  abstract"},
                ]
            }
        }

        class FakeHTTP:
            def get_json(self, url, **kwargs):
                return payload

        client = EuropePMC(FakeHTTP())
        out = client.abstracts(["10.1/a", "10.1/b", "10.1/c"])
        self.assertEqual(out["10.1/a"], "First abstract")
        self.assertEqual(out["10.1/b"], "Second abstract")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.record = recmod.from_openalex(
            {
                "id": "https://openalex.org/W1",
                "doi": "10.1000/xyz",
                "display_name": "Review of gut-brain signalling",
                "publication_year": 2022,
                "cited_by_count": 300,
                "type": "review",
                "abstract_inverted_index": {"Abstract": [0], "text": [1]},
                "authorships": [
                    {"author_position": "first", "author": {"display_name": "First Author"}, "is_corresponding": True}
                ],
                "primary_location": {"source": {"display_name": "Nature Reviews Microbiology"}},
            }
        )
        self.record["journal_rank"] = {"percentile": 95.0, "passes": True, "cas_tier": 1}
        self.discover = {
            "engine": "scopus-web",
            "chosen": {"query": "TITLE-ABS-KEY(test)", "sha256": "abc", "count": 120},
            "total": 120,
            "candidates": 10,
            "selected": [self.record],
            "stats": {"rejected_by_reason": {"not_a_review": 1}, "warnings": ["only 1 review passed"]},
        }

    def test_report_without_digest_has_stop_gate(self):
        text = report_mod.reviews_report(self.discover, {})
        self.assertIn("[STOP] 待填写", text)
        self.assertIn("需要你确认", text)
        self.assertIn("Nature Reviews Microbiology", text)

    def test_report_with_digest(self):
        digest = {
            "reviews": [
                {
                    "id": self.record["id"],
                    "abstract_zh": "这是摘要的中文翻译。",
                    "interpretation": ["第一条解读", "第二条解读"],
                    "sub_directions": [{"name": "方向一", "evidence": "证据"}, {"name": "方向二"}],
                }
            ],
            "sub_direction_map": [{"name": "方向一", "reviews": [self.record["title"]], "note": "说明"}],
            "summary_zh": "领域概览" * 60,
        }
        text = report_mod.reviews_report(self.discover, digest)
        self.assertIn("这是摘要的中文翻译。", text)
        self.assertIn("方向一", text)
        self.assertNotIn("[STOP] 待填写：摘要中文翻译", text)

    def test_digest_check_reports_missing(self):
        tasks = report_mod.digest_tasks([self.record])
        result = report_mod.digest_check(tasks, {"reviews": []})
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["field"] == "review" for e in result["errors"]))

    def test_digest_check_accepts_valid(self):
        tasks = report_mod.digest_tasks([self.record])
        filled = {
            "reviews": [
                {
                    "id": self.record["id"],
                    "abstract_zh": "这是一段足够长的中文摘要翻译，用于通过质量控制校验，覆盖研究背景、方法、主要发现与局限。" * 2,
                    "interpretation": ["第一条" * 30, "第二条" * 30],
                    "sub_directions": [{"name": "a", "evidence": "x"}, {"name": "b", "evidence": "y"}],
                }
            ]
        }
        result = report_mod.digest_check(tasks, filled)
        self.assertTrue(result["ok"], result)

    def test_list_report_contains_authors_and_url(self):
        run = {
            "status": "ok",
            "engine": "scopus-web",
            "chosen": {"query": "TITLE-ABS-KEY(test)", "count": 30, "sha256": "abc"},
            "records": [self.record],
            "attempts": [{"label": "A_strict", "rationale": "r", "count": 30, "sha256": "abc"}],
        }
        text = report_mod.list_report(run)
        self.assertIn("First Author", text)
        self.assertIn("doi.org/10.1000/xyz", text)
        self.assertIn("[YES]", text)


class FieldCatalogTests(unittest.TestCase):
    def test_catalog_is_valid(self):
        data = json.loads((HERE.parent / "assets" / "field_catalog.json").read_text(encoding="utf-8"))
        fields = data.get("fields") or []
        self.assertGreaterEqual(len(fields), 10)
        ids = [f["id"] for f in fields]
        self.assertEqual(len(ids), len(set(ids)))
        for field in fields:
            self.assertTrue(field.get("zh") and field.get("en"))
            self.assertGreaterEqual(len(field.get("keywords") or []), 5)
            self.assertTrue(field.get("overview_zh"))


class VariantTests(unittest.TestCase):
    def test_ladder_is_superset(self):
        variants = query_mod.attempt_variants()
        self.assertEqual(len(variants), 4)
        first, last = variants[0], variants[-1]
        self.assertFalse(first["opts"]["wildcards"])
        self.assertTrue(last["opts"]["wildcards"])
        self.assertFalse(last["opts"]["include_filters"])

    def test_summarize(self):
        summary = query_mod.summarize_plan(base_plan())
        self.assertEqual(len(summary["concepts"]), 2)
        self.assertEqual(summary["concepts"][0]["role"], "core")


class FakeRuntime:
    """Minimal stand-in for Runtime: counts are scripted, no browser."""

    def __init__(self, counts, records=None):
        self.counts = list(counts)
        self.records = records or []
        self.log = Log(verbose=False)

    def count(self, engine, query, refresh=False):
        value = self.counts.pop(0) if self.counts else 0
        return {"count": value, "state": {"engine": engine}, "cached": False}

    def fetch_records(self, engine, query, max_records=20, pages=1, **kwargs):
        return {"records": self.records, "total": len(self.records), "meta": {"records_available": bool(self.records)}}


class SearchOrchestrationTests(unittest.TestCase):
    def test_escalates_until_target(self):
        from litquery import search as search_mod

        runtime = FakeRuntime([5, 20])
        run = search_mod.run(
            base_plan(),
            runtime,
            search_mod.RunOptions(engine="scopus", min_results=15, max_attempts=2, fetch=False, enrich=False),
        )
        self.assertEqual(run["status"], "ok")
        self.assertEqual(len(run["attempts"]), 2)
        self.assertEqual(run["chosen"]["count"], 20)

    def test_reports_insufficient(self):
        from litquery import search as search_mod

        runtime = FakeRuntime([5, 4])
        run = search_mod.run(
            base_plan(),
            runtime,
            search_mod.RunOptions(engine="scopus", min_results=15, max_attempts=2, fetch=False, enrich=False),
        )
        self.assertEqual(run["status"], "insufficient")
        self.assertEqual(run["chosen"]["count"], 5)

    def test_first_attempt_sufficient(self):
        from litquery import search as search_mod

        runtime = FakeRuntime([300, 999])
        run = search_mod.run(
            base_plan(),
            runtime,
            search_mod.RunOptions(engine="scopus", min_results=15, max_attempts=4, fetch=False, enrich=False),
        )
        self.assertEqual(run["status"], "ok")
        self.assertEqual(len(run["attempts"]), 1)

    def test_fetch_returns_records(self):
        from litquery import search as search_mod

        record = recmod._finalize(
            {
                "title": "A topic review",
                "doi": "10.1/topic",
                "journal": "Top Journal",
                "year": 2022,
                "cited_by": 30,
                "document_type": "review",
                "abstract": "topic body",
            }
        )
        runtime = FakeRuntime([30], records=[record])
        run = search_mod.run(
            base_plan(),
            runtime,
            search_mod.RunOptions(engine="scopus", min_results=15, max_attempts=1, fetch=True, enrich=False, relevance_filter=False),
        )
        self.assertEqual(len(run["records"]), 1)
        self.assertEqual(run["stats"]["records"], 1)

    def test_concept_evidence_requires_real_mentions(self):
        from litquery import search as search_mod

        plan = {
            "concepts": [
                {"role": "core", "label": "disease", "terms": ["Alzheimer disease"]},
                {"role": "core", "label": "intervention", "terms": ["disease-modifying therapy"]},
                {"role": "core", "label": "trial", "terms": ["clinical trial"]},
            ]
        }
        good = {
            "title": "Disease-modifying therapy for Alzheimer disease: a randomized clinical trial",
            "abstract": "This randomized clinical trial tested a disease-modifying therapy in Alzheimer disease.",
        }
        bad = {
            "title": "Biomarkers of aging",
            "abstract": "We mention disease-modifying therapy once and clinical trial once.",
        }
        self.assertTrue(search_mod.concept_evidence(good, plan)["ok"])
        self.assertFalse(search_mod.concept_evidence(bad, plan)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
