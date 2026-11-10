# -*- coding: utf-8 -*-
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from keyword_contract import keyword_contract
from vosviewer_export import load_documents, export_stage


class ExportPipelineTests(unittest.TestCase):
    def make_stage(self,root,stage='interpret'):
        folder=Path(root)/'.work_cdp'/stage
        (folder/'tasks').mkdir(parents=True)
        (folder/'results').mkdir()
        manifest=[]
        for i in (1,2):
            doi=f'10.5555/test.{i}'
            title='machine learning and mass spectrometry'
            packet={'doi':doi,'title':title,'abstract':'','text':'',
                    'interpret_tier':'L1','keyword_contract':keyword_contract()}
            card={'doi':doi,'one_line':'测试论文定位','problem_solved':'测试边界',
                  'methods':[],'verified_conclusions':['合成测试，不是真实结论'],
                  'key_20':['测试说明'],'quantified_gains':[],'open_questions':[],
                  'direction_tags':['计算与模拟'],'paradigm_tags':['computational'],
                  'evidence':{'source':'abstract'},'keywords':['machine learning','mass spectrometry'],
                  'keyword_source_evidence':[{'term':t,'surface':t,'source_field':'title','quote':title}
                                      for t in ('machine learning','mass spectrometry')]}
            tp=folder/'tasks'/f'paper{i}.json';rp=folder/'results'/tp.name
            tp.write_text(json.dumps(packet,ensure_ascii=False),encoding='utf-8')
            rp.write_text(json.dumps(card,ensure_ascii=False),encoding='utf-8')
            manifest.append({'seq':i,'slug':tp.stem,'doi':doi,'title':title,'year':2024,
                             'task':str(tp),'result':str(rp),'evidence_source':'abstract'})
        (folder/'manifest.json').write_text(json.dumps({'tasks':manifest,'interpret_tier':'L1','interpret_engine':'legacy'}),encoding='utf-8')
        return folder

    def test_all_three_stage_exports_and_exact_counts(self):
        with tempfile.TemporaryDirectory() as root:
            for stage,name in [('H','interpret'),('H2','refs'),('R2','reviews')]:
                self.make_stage(root,name)
                meta=export_stage(root,stage)
                self.assertEqual((meta['terms'],meta['links']),(2,1))
                network=(Path(root)/'.work_cdp'/name/'vosviewer/vosviewer_network.txt').read_text(encoding='utf-8')
                self.assertEqual(network.strip().split('\t')[-1],'2')

    def test_custom_stage_and_pmid_only(self):
        with tempfile.TemporaryDirectory() as root:
            folder=self.make_stage(root,'reviews')
            tp=folder/'tasks/paper1.json';packet=json.loads(tp.read_text(encoding='utf-8'))
            packet['doi']='';packet['pmid']='12345678'
            tp.write_text(json.dumps(packet),encoding='utf-8')
            (folder/'results/paper1.json').unlink()
            docs,notes=load_documents(root,'R2',stage_dir=folder)
            self.assertIn('pmid:12345678',{d['id'] for d in docs})

    def test_interpret_build_integrated_and_no_token_fragments(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'PAPER_WORKDIR':root,'PAPER_AUTHOR':'自检'}):
            folder=self.make_stage(root)
            import subprocess
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name('interpret_build.py'))],
                                       env=os.environ.copy(), text=True, encoding='utf-8', capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)
            report=(Path(root)/'自检_论文解读.md').read_text(encoding='utf-8')
            self.assertIn('VOSviewer',report)
            self.assertIn('machine learning',report)
            self.assertNotIn('| single |',report)
            meta=json.loads((folder/'vosviewer/export_metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['links'],1)

    def test_invalid_new_contract_does_not_export_ungrounded_card_words(self):
        with tempfile.TemporaryDirectory() as root:
            folder=self.make_stage(root)
            tp=folder/'tasks/paper1.json';packet=json.loads(tp.read_text(encoding='utf-8'))
            packet['title']='An unrelated topic';tp.write_text(json.dumps(packet),encoding='utf-8')
            docs,notes=load_documents(root)
            self.assertEqual(next(d for d in docs if d['id']=='10.5555/test.1')['keywords'],[])


if __name__=='__main__':
    unittest.main()
