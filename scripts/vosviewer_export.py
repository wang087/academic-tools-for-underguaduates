# -*- coding: utf-8 -*-
"""Offline, phrase-preserving VOSviewer export for H/H2/R2 task-card pairs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from keyword_terms import collect_documents, export_vosviewer

STAGES={'H':'interpret','H2':'refs','R2':'reviews'}


def load_documents(workdir, stage='H', stage_dir=None):
    root=Path(stage_dir) if stage_dir else Path(workdir)/'.work_cdp'/STAGES[stage]
    if not root.exists():
        raise FileNotFoundError(f'No stage data: {root}')
    docs=[];notes=[]
    for taskpath in sorted((root/'tasks').glob('*.json')):
        packet=json.loads(taskpath.read_text(encoding='utf-8'))
        if not isinstance(packet,dict):
            notes.append({'file':str(taskpath),'reason':'task-not-object'});continue
        result=root/'results'/taskpath.name
        card={}
        if result.exists():
            try:
                card=json.loads(result.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:
                notes.append({'file':str(result),'reason':str(exc)})
        if not isinstance(card,dict):
            notes.append({'file':str(result),'reason':'card-not-object'});card={}
        rec=packet.get('record') or packet.get('paper') or packet.get('meta') or {}
        rec=rec if isinstance(rec,dict) else {}
        identity=(packet.get('doi') or rec.get('doi') or packet.get('id')
                  or ('pmid:'+str(packet['pmid']) if packet.get('pmid') else '')
                  or ('pmcid:'+str(packet['pmcid']) if packet.get('pmcid') else ''))
        if not identity:
            notes.append({'file':str(taskpath),'reason':'no-document-id'});continue
        if card.get('doi') and str(card['doi']).lower()!=str(identity).lower():
            notes.append({'file':str(result),'reason':'card-doi-mismatch'});card={}
        original_card = dict(card)
        layer_present = any(k in card for k in ('keywords_core','keywords_method','keywords_reagent'))
        try:
            from keyword_contract import validate_keyword_contract
            checked=validate_keyword_contract(card,packet)
        except ImportError:
            checked=None
        if checked and checked.get('errors'):
            notes.append({'file':str(result),'reason':'keyword-contract-structure','errors':checked['errors']})
            card={}
        elif checked:
            card={**card,'keywords':checked['keywords'],'keyword_source_evidence':checked['keyword_source_evidence']}
        if layer_present:
            from keywords_three_layer import gated_keywords, fulltext_of
            layer_checked = gated_keywords(original_card, packet, fulltext=fulltext_of(taskpath.name, root/'results', root/'tasks'))
            card = {**card, 'keywords': layer_checked['terms'], 'keyword_source_evidence': layer_checked['keyword_source_evidence']}
            notes.extend({'file':str(result), **row} for row in layer_checked['audit'])
        docs.append({'id':str(identity),'title':packet.get('title') or rec.get('title') or '',
                     'abstract':packet.get('abstract') or rec.get('abstract') or '',
                     'text':packet.get('text') or packet.get('fulltext') or '',
                     'author_keywords':packet.get('author_keywords') or rec.get('author_keywords') or [],
                     'keywords':card.get('keywords'), 'keyword_source_evidence':card.get('keyword_source_evidence'),
                     'methods':card.get('methods') or [],'keyword_contract_audit':checked or {},
                      'keyword_layer_terms': card.get('keywords', []) if layer_present else None})
    if not docs:
        raise ValueError(f'No self-contained task packets in {root}/tasks')
    return docs,notes


def export_stage(workdir, stage='H', *, outdir=None, aliases=None, min_occurrences=2, stage_dir=None):
    docs, notes = load_documents(workdir, stage, stage_dir)
    result = collect_documents(docs, aliases)
    result['input_notes'] = notes
    out = Path(outdir) if outdir else Path(workdir)/'.work_cdp'/STAGES[stage]/'vosviewer'
    return export_vosviewer(result, out, min_occurrences=min_occurrences)


def main(argv=None):
    parser=argparse.ArgumentParser(description='VOSviewer完整术语/每文献二值共现导出（零网络）')
    parser.add_argument('--workdir',required=True)
    parser.add_argument('--stage',choices=STAGES,default='H')
    parser.add_argument('--aliases',help='精确同义词表JSON；不要传领域分组词族')
    parser.add_argument('--out')
    parser.add_argument('--stage-dir',help='显式tasks/results所在阶段目录（支持旧式outputs布局）')
    parser.add_argument('--min-occurrences',type=int,default=2)
    parser.add_argument('--max-terms',type=int,default=500)
    args=parser.parse_args(argv)
    if args.min_occurrences<1 or args.max_terms<1:
        parser.error('thresholds must be positive')
    docs,notes=load_documents(args.workdir,args.stage,args.stage_dir)
    result=collect_documents(docs,args.aliases)
    result['input_notes']=notes
    out=Path(args.out) if args.out else Path(args.workdir)/'.work_cdp'/STAGES[args.stage]/'vosviewer'
    meta=export_vosviewer(result,out,min_occurrences=args.min_occurrences,max_terms=args.max_terms)
    print(json.dumps({'out':str(out),'counts':result['counts'],'export':meta},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
