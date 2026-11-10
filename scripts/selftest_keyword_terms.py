# -*- coding: utf-8 -*-
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from keyword_terms import collect_documents, export_vosviewer, parse_author_keywords


class KeywordTermsTests(unittest.TestCase):
    def test_complete_phrases_and_symbols(self):
        doc={'id':'p1','title':'Single-cell RNA sequencing and machine learning with CD8 T cells',
             'abstract':'We study synaptic plasticity in the hippocampus.',
             'keywords':['Single-cell RNA sequencing','machine learning','CD8','T cells']}
        r=collect_documents([doc]);terms=set(r['documents'][0]['terms'])
        self.assertTrue({'single-cell RNA sequencing','machine learning','CD8','T cell','synaptic plasticity','hippocampus'}<=terms)
        self.assertFalse({'single','cell','learning','machine','经元','鼠海'}&terms)

    def test_legacy_fragments_and_prose_rejected(self):
        text='本研究通过培养基处理小鼠海马神经元，研究谷氨酸效应。'
        doc={'id':'p1','title':'神经元与谷氨酸','text':text,
             'keywords':['经元','鼠海','养基','谷氨','本研究通过培养基处理小鼠海马神经元','unknown invention'],
             'methods':[text]}
        r=collect_documents([doc]);terms=r['documents'][0]['terms']
        self.assertNotIn('经元',terms);self.assertNotIn('鼠海',terms)
        self.assertTrue(r['audit']);self.assertIn('neuron',terms);self.assertIn('glutamate',terms)

    def test_quote_and_canonical_evidence(self):
        r=collect_documents([{'id':'p','title':'机器学习辅助单细胞测序',
            'keywords':['machine learning','deep learning'],
            'keyword_source_evidence':[{'term':'machine learning','surface':'机器学习','source_field':'title','quote':'机器学习辅助单细胞测序'},
                                {'term':'deep learning','surface':'机器学习','source_field':'title','quote':'机器学习辅助单细胞测序'}]}])
        self.assertIn('machine learning',r['documents'][0]['terms'])
        self.assertNotIn('deep learning',r['documents'][0]['terms'])
        self.assertTrue(any(a['reason']=='unsupported-canonical-or-translation' for a in r['audit']))

    def test_invented_quote_not_evidence(self):
        r=collect_documents([{'id':'p','title':'A study',
            'keywords':['long molecular phrase'],
            'keyword_source_evidence':[{'term':'long molecular phrase','surface':'long molecular phrase','source_field':'abstract','quote':'long molecular phrase'}]}])
        self.assertEqual(r['documents'][0]['terms'],[])

    def test_substring_word_boundary(self):
        r=collect_documents([{'id':'p','title':'Donor microlearning and scrna-sequencinglike'}])
        self.assertNotIn('RNA',r['documents'][0]['terms'])
        self.assertNotIn('machine learning',r['documents'][0]['terms'])

    def test_author_keywords_preserve_slash_hyphen_numbers(self):
        text='Keywords: CRISPR/Cas9; IL-6; T cells; single-cell RNA sequencing\nIntroduction\nThis is prose.'
        terms=parse_author_keywords(text)
        self.assertIn('CRISPR/Cas9',terms);self.assertIn('IL-6',terms)
        r=collect_documents([{'id':'p','text':text}])
        self.assertIn('CRISPR-Cas9',r['documents'][0]['terms'])
        self.assertFalse(any('Introduction' in t for t in r['documents'][0]['terms']))

    def test_alias_ambiguity_not_silently_assigned(self):
        r=collect_documents([{'id':'p','title':'ABC protein','keywords':['ABC']}],
                            {'canonical one':['ABC'],'canonical two':['ABC']})
        self.assertNotIn('canonical one',r['documents'][0]['terms'])
        self.assertTrue(any(a['reason']=='ambiguous-alias' for a in r['audit']))

    def test_binary_count_deduplication_and_export(self):
        docs=[{'id':'p1','title':'machine learning and mass spectrometry machine learning'},
              {'id':'p2','title':'machine learning and mass spectrometry'},
              {'id':'p1','title':'machine learning and mass spectrometry'}]
        result=collect_documents(docs)
        by={r['term']:r['document_frequency'] for r in result['terms']}
        self.assertEqual(by['machine learning'],2)
        with tempfile.TemporaryDirectory() as folder:
            meta=export_vosviewer(result,folder,min_occurrences=2)
            rows=list(csv.reader((Path(folder)/'vosviewer_map.txt').read_text(encoding='utf-8').splitlines(),delimiter='\t'))
            edges=list(csv.reader((Path(folder)/'vosviewer_network.txt').read_text(encoding='utf-8').splitlines(),delimiter='\t'))
            self.assertEqual(meta['terms'],2);self.assertEqual(meta['links'],1)
            self.assertEqual(edges[0][2],'2')
            self.assertEqual(rows[0][2],'weight<Occurrences>')
            self.assertEqual({r[1] for r in rows[1:]},{'machine learning','mass spectrometry'})
            self.assertTrue(all(len(r)==3 for r in edges))
            self.assertTrue(all(int(r[0])<int(r[1]) for r in edges))

    def test_empty_and_singleton_network_honest(self):
        result=collect_documents([{'id':'p','title':'machine learning'}])
        with tempfile.TemporaryDirectory() as folder:
            meta=export_vosviewer(result,folder,min_occurrences=2)
            self.assertEqual(meta['terms'],0);self.assertTrue(meta['empty_network'])
            self.assertEqual((Path(folder)/'vosviewer_network.txt').read_text(encoding='utf-8'),'')
            self.assertNotIn('cluster',(Path(folder)/'vosviewer_map.txt').read_text(encoding='utf-8'))

    def test_keyword_section_cannot_swallow_single_newline_prose(self):
        self.assertEqual(parse_author_keywords('Keywords: CRISPR/Cas9; DNA\nCells were maintained in culture.'), ['CRISPR/Cas9','DNA'])

    def test_casing_alias_and_literal_evidence(self):
        r=collect_documents([{'id':'a','author_keywords':['Graph Neural Networks']},
                             {'id':'b','author_keywords':['graph neural networks']}])
        self.assertEqual(r['terms'][0]['document_frequency'],2)
        r=collect_documents([{'id':'a','title':'CRISPR/Cas9 targets DNA.'}])
        for ev in r['documents'][0]['evidence']:
            self.assertIn(ev['quote'],'CRISPR/Cas9 targets DNA.')
        quote='functional ultrasound imaging (fUSI)'
        r=collect_documents([{'id':'a','title':quote,'keywords':['functional ultrasound imaging'],
             'keyword_source_evidence':[{'term':'functional ultrasound imaging','surface':'fUSI','source_field':'title','quote':quote}]}])
        self.assertIn('functional ultrasound imaging',r['documents'][0]['terms'])

    def test_long_chinese_phrase_not_split(self):
        term='高通量空间分辨转录组测序'
        r=collect_documents([{'id':'p','title':term,'keywords':[term]}])
        self.assertIn(term,r['documents'][0]['terms'])
        self.assertFalse('高通' in r['documents'][0]['terms'])


if __name__=='__main__':
    unittest.main()
