# -*- coding: utf-8 -*-
"""Offline regression tests for evidence-backed graph keyword consumers."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import knowledge_graph as graph


class GraphKeywordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.patcher = patch.object(graph, 'CDPDIR', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def save(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def fixture(self):
        terms = ['双光子钙离子荧光成像', 'whole cell patch clamp recording']
        text = 'Keywords: ' + '; '.join(terms) + '\n\nIntroduction\nWe studied neurons.'
        self.save('interpret/tasks/paper.json', {'doi': '10.test/a', 'text': text})
        self.save('interpret/manifest.json', {'tasks': [{'slug': 'paper', 'doi': '10.test/a'}]})
        cards = {'10.test/a': {'doi': '10.test/a', 'keywords': ['经元', '鼠海', 'student', 'unfounded technique'],
                               'methods': ['我们采用双光子钙离子荧光成像，并得到显著结果。',
                                           'whole cell patch clamp recording in neurons'],
                               'direction_tags': ['成像']}}
        return terms, cards

    def test_complete_phrases_reach_cloud_methods_and_overview(self):
        terms, cards = self.fixture()
        weights, colors, _, dropped, stats = graph.compute_wordcloud_weights(
            cards, {}, {}, {'10.test/a': '成像'}, families={})
        self.assertEqual(set(weights), set(terms))
        self.assertEqual(set(colors), set(terms))
        self.assertTrue({'经元', '鼠海', 'student', 'unfounded technique'} <= set(dropped))
        self.assertEqual(stats['n_fragment_candidates'], 0)
        with patch('keyword_terms.DEFAULT_ALIASES', self.root / 'aliases.json'):
            self.save('aliases.json', {})
            methods = graph.method_terms_by_topics(cards)
            self.assertEqual(set(methods), set(terms))
            nodes, _, _, _, direction_methods, _, _, _ = graph.build_overview(
                {}, cards, {}, {}, {}, {})
            self.assertEqual({v['label'] for v in nodes.values() if v['kind'] == 'method'}, set(terms))
            self.assertEqual(set(direction_methods['成像']), set(terms))

    def test_legacy_unknown_prose_is_empty_not_padded(self):
        cards = {'legacy': {'keywords': ['经元', 'unsupported invention'],
                            'methods': ['神经元培养基实验分析得到结果' * 12,
                                        'complex technical prose with repeated unknown words' * 12]}}
        result = graph.compute_wordcloud_weights(cards, {'legacy': {'title': 'Uncatalogued investigations'}},
                                                  {}, {}, families={})
        self.assertFalse(result[0])
        self.assertEqual(result[4]['n_terms_kept'], 0)
        self.assertTrue(result[4]['keyword_audit'])

    def test_stage_matching_fulltext_and_manifest_items(self):
        self.save('interpret/tasks/same.json', {'doi': '10.test/a', 'text': 'H evidence'})
        self.save('refs/tasks/same.json', {'doi': '10.test/a', 'fulltext': 'H2 evidence'})
        self.save('reviews/tasks/same.json', {'doi': '10.test/a', 'abstract': 'R2 evidence'})
        for name in ('interpret', 'refs', 'reviews'):
            self.save(name + '/manifest.json', {'items': [{'slug': 'same', 'doi': '10.test/a', 'id': 'same'}]})
        cards = {'same': {'keywords': []}}
        self.assertEqual(graph.load_keyword_documents(cards)[0]['text'], 'H evidence')
        self.assertEqual(graph.load_keyword_documents(cards, stage='H2')[0]['text'], 'H2 evidence')
        self.assertEqual(graph.load_keyword_documents(cards, stage='reviews')[0]['abstract'], 'R2 evidence')

    def test_deduplicated_evidence_weights_and_language(self):
        terms, cards = self.fixture()
        card = cards['10.test/a']
        card['keywords'] = terms * 2
        card['keyword_source_evidence'] = [{'term': t, 'surface': t, 'source_field': 'text',
                                     'quote': t} for t in terms] * 2
        result = graph.compute_wordcloud_weights(cards, {}, {}, {}, families={})
        self.assertEqual(dict(result[0]), {t: 3 for t in terms})
        self.assertEqual(set(graph.compute_wordcloud_weights(cards, {}, {}, {}, lang='zh', families={})[0]), {terms[0]})
        self.assertEqual(set(graph.compute_wordcloud_weights(cards, {}, {}, {}, lang='en', families={})[0]), {terms[1]})

    def test_declared_contract_rejects_fabricated_quote_and_keeps_card(self):
        term = 'unsupported invented scientific phrase'
        self.save('interpret/tasks/a.json', {
            'doi': 'a', 'text': 'The actual available source.',
            'keyword_contract': {'version': 1}})
        cards = {'a': {'keywords': [term], 'keyword_source_evidence': [
            {'term': term, 'surface': term, 'source_field': 'text', 'quote': term}],
            'direction_tags': ['保留方向']}}
        result = graph.compute_wordcloud_weights(cards, {}, {}, {}, families={})
        self.assertFalse(result[0])
        self.assertTrue(any('quote' in row['reason'] for row in result[4]['keyword_audit']))
        with patch('keyword_terms.DEFAULT_ALIASES', self.root / 'aliases.json'):
            self.save('aliases.json', {})
            nodes, _, _, _, _, _, _, _ = graph.build_overview({}, cards, {}, {}, {}, {})
        self.assertTrue(any(node['label'] == '保留方向' for node in nodes.values()))
        self.assertFalse(any(node['kind'] == 'method' for node in nodes.values()))
        self.assertEqual(cards['a']['keywords'], [term])
        # External metadata cannot retrospectively validate a new task contract.
        cards['a']['keyword_source_evidence'] = [
            {'term': term, 'surface': term, 'source_field': 'title', 'quote': term}]
        result = graph.compute_wordcloud_weights(cards, {'a': {'title': term}}, {}, {}, families={})
        self.assertFalse(result[0])
        self.assertTrue(result[4]['keyword_audit'])
        cards['a'].pop('keyword_source_evidence')
        result = graph.compute_wordcloud_weights(cards, {}, {}, {}, families={})
        self.assertFalse(result[0])
        self.assertTrue(any('required' in row['reason'] for row in result[4]['keyword_audit']))

    def test_report_and_hotspots_use_complete_verified_terms(self):
        import datetime
        phrase = 'whole cell patch clamp recording'
        self.save('refs/tasks/ref.json', {'doi': '10.test/ref', 'title': phrase})
        self.save('refs/manifest.json', {'tasks': [{'slug': 'ref', 'doi': '10.test/ref', 'id': 'W123'}]})
        final = {'items': [{'id': 'W123', 'doi': '10.test/ref', 'title': phrase,
                            'year': datetime.date.today().year-1}]}
        cards = {'W123': {'keywords': [phrase], 'direction_tags': ['成像']}}
        with patch('keyword_terms.DEFAULT_ALIASES', self.root / 'aliases.json'):
            self.save('aliases.json', {})
            rows, hotspots, _ = graph.compute_direction_clusters(final, cards, {}, {}, 5, {})
        self.assertEqual(rows[0]['tokens'], [phrase])
        self.assertEqual(hotspots['成像'], [phrase])
        _, source_cards = self.fixture()
        result = graph.compute_wordcloud_weights(source_cards, {}, {}, {}, families={})
        report = graph.build_report({}, [], [], result[3], {}, [], {},
                                    {'source': 'none'}, {'dpi': 300, 'recent_years': 5}, result[4])
        self.assertIn('完整术语', report)
        self.assertNotIn('中文取 2-gram', report)

    def test_exact_dictionary_phrase_not_english_sentence_tokens(self):
        phrase = 'single cell RNA sequencing'
        cards = {'x': {'methods': ['unrelated invented method']}}
        recs = {'x': {'title': 'We compare single cell RNA sequencing across samples'}}
        result = graph.compute_wordcloud_weights(cards, recs, {}, {}, families={phrase: ['scRNA-seq']})
        self.assertEqual(set(result[0]), {phrase})


if __name__ == '__main__':
    unittest.main(verbosity=2)
