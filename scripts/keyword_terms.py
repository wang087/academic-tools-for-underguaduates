# -*- coding: utf-8 -*-
"""Evidence-backed scientific terms shared by reports, graphs and VOSviewer.

Deliberately no Chinese n-grams, stemming, inferred translations or sentence
splitting into English words. Missing terms are preferable to invented coverage.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

DEFAULT_ALIASES = Path(__file__).resolve().parent.parent / 'templates' / '关键词同义词表.json'
SCHEMA_VERSION = 1
GENERIC = set('''a an the of in on at to for from by with and or but as is are was were be
this that these those our their results result study studies paper article method methods
analysis data research approach approaches effect effects novel new high low significant
using used use based show shown conclusion conclusions discussion introduction abstract
keywords keyword background materials references figure figures fig table tables
single cell cells machine learning deep neural network networks model models
however therefore furthermore provide provides potential important obtained found
student min max hour hours day days ml kg mg mm nm pm doi copyright author authors'''.split())
GENERIC_ZH = {'研究','方法','结果','分析','数据','结论','本文','文章','论文','实验','模型','机制',
              '显著','表明','发现','采用','使用','获得','重要','影响','具有','提供','进行',
              '关键信息','正文','摘要','参考文献','进一步','实验方法','未来研究',
              '经元','鼠海','养基','谷氨','马神','氨酸','触前','中的','与统计'}
SENTENCE = re.compile(r'研究了|表明了|结果表明|结果显示|我们|本文|本研究|通过.*发现|'
                      r'\b(?:we|our|this study|these results|was used|were used|showed that)\b', re.I)
KW_HEADER = re.compile(r'(?im)(?:^|\n)[ \t]*(?:key[ \t-]*words?|关键词|关键字)[ \t]*[:：]?[ \t]*')
KW_SECTION = re.compile(r'(?i)\b(?:introduction|abstract|background|materials and methods|results|discussion|references|acknowledg\w*)\b|引言|参考文献|正文')


def normalize(value):
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.replace('\u00ad', '').replace('\u200b', '')
    text = re.sub('[‐‑‒–—−]', '-', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def key(value):
    return normalize(value).casefold()


def term_problem(value, *, explicit=False, known=False):
    if not isinstance(value, str):
        return 'term-not-string'
    value = normalize(value).strip(' ;；,，.。')
    low = key(value)
    if not value or len(value) > 100 or len(value.split()) > 10:
        return 'empty-or-overlong'
    if any(ch in value for ch in ('\t', '\n', '\r', '\ufffd')):
        return 'control-or-decoding-artifact'
    if re.search(r'https?://|www\.|@|10\.\d{4,}/|\.pdf\b', value, re.I):
        return 'url-email-or-identifier'
    if re.search(r'\.\.{2,}|…|placeholder|待填写|待补充|未获取|暂无|自定义:', value, re.I):
        return 'placeholder-or-truncation'
    if re.search(r'[。！？!?；;]|\[[0-9, -]+\]', value) or SENTENCE.search(value):
        return 'sentence-or-reference'
    if low in GENERIC or value in GENERIC_ZH:
        return 'generic-word-or-fragment'
    if re.fullmatch(r'[\d\s.,+\-±%/]+', value) or re.fullmatch(r'\d+(?:\s*)(?:mg|ml|mm|nm|min|h|s|kg|μm|um|%)', value, re.I):
        return 'measurement-or-number'
    if not re.search(r'[A-Za-z一-鿿Α-ω]', value):
        return 'no-term-characters'
    if not known:
        # Chinese slices are not made meaningful by merely finding them in prose.
        if re.fullmatch(r'[一-鿿]{2}', value) and not explicit:
            return 'unverified-short-chinese-fragment'
    if not known and not explicit:
        # Broad unknown English single words and 2-character CJK slices are not
        # an automatic keyword vocabulary; retain them for manual review only.
        if re.fullmatch(r'[一-鿿]{2}', value):
            return 'unverified-short-chinese-fragment'
        if re.fullmatch(r'[A-Za-z]+', value) and not value.isupper():
            return 'unverified-single-word'
    if len(value) < 2:
        return 'too-short'
    return ''


def _aliases(families=None):
    if families is None:
        data = json.loads(DEFAULT_ALIASES.read_text(encoding='utf-8'))
    elif isinstance(families, (str, Path)):
        data = json.loads(Path(families).read_text(encoding='utf-8'))
    else:
        data = families
    if not isinstance(data, dict):
        raise ValueError('Exact alias table must be an object')
    owners, displays = defaultdict(set), {}
    for canonical, aliases in data.items():
        if str(canonical).startswith('_'):
            continue
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise ValueError(f'Invalid alias list: {canonical}')
        canonical = normalize(canonical)
        displays[key(canonical)] = canonical
        for value in [canonical] + aliases:
            if normalize(value):
                owners[key(value)].add(canonical)
    ambiguous = {a for a, labels in owners.items() if len(labels) != 1}
    mapping = {a:next(iter(labels)) for a, labels in owners.items() if a not in ambiguous}
    return mapping, ambiguous


def _match(text, surface):
    """Case/punctuation normalization with Latin alphanumeric boundaries."""
    blob, needle = key(text), key(surface)
    if not needle:
        return None
    left = r'(?<![a-z0-9])' if re.match(r'[a-z0-9]', needle) else ''
    right = r'(?![a-z0-9])' if re.search(r'[a-z0-9]$', needle) else ''
    return re.search(left + re.escape(needle) + right, blob)


def parse_author_keywords(text):
    """Read only a labelled Keywords section, stopping before prose/next section.

    Semicolons/commas separate terms; slashes and hyphens inside scientific
    nomenclature (CRISPR/Cas9, IL-6, RNA-seq) are preserved.
    """
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    result = []
    for mark in KW_HEADER.finditer(text):
        segment = text[mark.end():mark.end()+1000]
        stop = KW_SECTION.search(segment)
        if stop:
            segment = segment[:stop.start()]
        segment = segment.split('\n\n', 1)[0]
        # A line break ends a keyword list unless the preceding line explicitly
        # ends with a separator (wrapped lists). Never swallow a prose paragraph.
        lines = segment.splitlines()
        kept_lines = []
        for line in lines:
            if kept_lines and not kept_lines[-1].rstrip().endswith((';', '；', ',', '，')):
                break
            kept_lines.append(line)
        segment = ' '.join(kept_lines)
        for item in re.split(r'[;；,，、|•·]+', segment):
            item = normalize(item).strip(' .。')
            if not item:
                continue
            if term_problem(item, explicit=True):
                break
            if len(item.split()) > 10:
                break
            result.append(item)
        if result:
            break
    return list(dict.fromkeys(result))[:20]


def collect_documents(documents, families=None):
    mapping, ambiguous = _aliases(families)
    audit, output = [], []
    display_by_key = {}
    merged = {}
    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            audit.append({'document_id':str(index), 'term':'', 'source':'document', 'reason':'document-not-object'})
            continue
        identity = str(doc.get('id') or doc.get('doi') or '').strip().lower()
        if not identity:
            audit.append({'document_id':str(index), 'term':'', 'source':'document', 'reason':'missing-stable-document-id'})
            continue
        if identity not in merged:
            merged[identity] = dict(doc)
        else:
            audit.append({'document_id':identity, 'term':'', 'source':'document', 'reason':'duplicate-document-id-not-counted-twice'})
            # Prefer original text/metadata, but do not concatenate repeated abstracts.
            for field in ('title','abstract','text','keywords','keyword_source_evidence','author_keywords'):
                if not merged[identity].get(field) and doc.get(field):
                    merged[identity][field] = doc[field]
    for identity, doc in sorted(merged.items()):
        sources = {field:str(doc.get(field) or '') for field in ('title','abstract','text')}
        contract_audit = doc.get('keyword_contract_audit')
        if isinstance(contract_audit, dict):
            for why in contract_audit.get('errors', []) + contract_audit.get('warnings', []):
                audit.append({'document_id':identity,'term':'','source':'keyword_contract','reason':str(why)})
        original = '\n'.join(sources.values())
        terms, evidence, unique_ev = set(), [], set()
        def reject(term, source, why):
            audit.append({'document_id':identity,'term':str(term),'source':source,'reason':why})
        def add(surface, field, quote, origin, proposed=None, explicit=False):
            if not isinstance(surface, str) or not isinstance(quote, str):
                reject(surface,field,'evidence-types'); return
            surface = normalize(surface).strip(' ;；,，.。')
            if key(surface) in ambiguous:
                reject(surface,field,'ambiguous-alias'); return
            canonical = mapping.get(key(surface), surface if re.search(r'[一-鿿]',surface) or surface.isupper() else key(surface))
            if proposed is not None and key(proposed) not in {key(surface), key(canonical)}:
                from keyword_contract import _explicit_alias
                if _explicit_alias(proposed, surface, quote):
                    canonical = normalize(proposed)
                else:
                    reject(proposed,field,'unsupported-canonical-or-translation'); return
            # Explicit card evidence permits a new scientific word, but not a
            # short Chinese slice merely copied from somewhere in a sentence.
            if origin == 'card_keywords' and re.fullmatch(r'[一-鿿]{2}', surface) and key(surface) not in mapping:
                reject(surface, field, 'short-chinese-term-needs-author-keywords-or-alias'); return
            why = term_problem(surface, explicit=explicit, known=key(surface) in mapping)
            if why:
                reject(surface,field,why); return
            canonical = display_by_key.setdefault(key(canonical), canonical)
            terms.add(canonical)
            source = 'keywords' if origin in ('author_keywords','card_keywords') else field
            record = (canonical,key(surface),field,origin)
            if record not in unique_ev:
                evidence.append({'term':canonical,'surface':surface,'source_field':field,
                                 'source':source,'quote':quote,'origin':origin,'reason':'verified-source-match'})
                unique_ev.add(record)
        author = doc.get('author_keywords')
        if isinstance(author, list):
            for term in author:
                if isinstance(term, str):
                    # This field must be provided by metadata/packet, never card text.
                    add(term,'author_keywords',term,'author_keywords',explicit=True)
                else:
                    reject(term,'author_keywords','term-not-string')
        parsed = parse_author_keywords(sources['text'])
        for term in parsed:
            add(term,'text',term,'author_keywords',explicit=True)
        from keyword_contract import source_evidence
        supplied = source_evidence(doc)
        if supplied is not None and not isinstance(supplied, list):
            reject('','keyword_source_evidence','evidence-not-list'); supplied=[]
        evidenced = set()
        for ev in supplied or []:
            if not isinstance(ev,dict):
                reject(ev,'keyword_source_evidence','evidence-not-object'); continue
            term,surface,field,quote = (ev.get(k) for k in ('term','surface','source_field','quote'))
            if not all(isinstance(v,str) and v.strip() for v in (term,surface,field,quote)):
                reject(term,'keyword_source_evidence','missing-evidence-fields'); continue
            if field not in ('title','abstract','text','author_keywords'):
                reject(term,field,'unsupported-source-field'); continue
            available = '; '.join(author) if field=='author_keywords' and isinstance(author,list) and all(isinstance(a,str) for a in author) else sources.get(field,'')
            if not available or quote not in available or surface not in quote:
                reject(term,field,'quote-or-surface-not-in-packet'); continue
            before = len(evidence)
            # Verified source evidence allows complete novel scientific words;
            # two-character CJK slices still require a known alias/list source.
            add(surface,field,quote,'card_keywords',proposed=term,explicit=True)
            if len(evidence)>before:
                evidenced.add(key(term))
        own = doc.get('keywords')
        if own is not None and not isinstance(own,list):
            reject(own,'keywords','keywords-not-list'); own=[]
        for term in own or []:
            if not isinstance(term,str):
                reject(term,'keywords','term-not-string'); continue
            if key(term) in evidenced:
                continue
            # Legacy card terms may be accepted only if unchanged in original
            # source and a complete phrase/symbol (never card prose itself).
            field = next((f for f,t in sources.items() if _match(t,term)), None)
            if field is None:
                reject(term,'keywords','not-grounded-in-original'); continue
            if supplied:
                reject(term,'keywords','missing-verified-evidence-entry'); continue
            add(term,field,term,'card_keywords',explicit=False)
        # Conservative fallback: exact dictionary phrases in title/abstract only.
        # Do not scan full-text references, conclusions or generated methods prose.
        for field in ('title','abstract'):
            matches=[]
            for alias, canonical in mapping.items():
                if term_problem(alias, known=True):
                    continue
                pattern = (r'(?<![a-z0-9])' if re.match(r'[a-z0-9]',alias) else '') + re.escape(alias) + (r'(?![a-z0-9])' if re.search(r'[a-z0-9]$',alias) else '')
                for match in re.finditer(pattern,key(sources[field])):
                    matches.append((match.start(),match.end(),alias))
            chosen=[]
            for start,end,alias in sorted(matches,key=lambda m: (-(m[1]-m[0]),m[0])):
                if any(start<b and end>a for a,b in chosen):
                    continue
                chosen.append((start,end))
                # Keep literal source spelling in evidence, never fabricate a
                # lower-case quote from the normalized matching string.
                raw = sources[field]
                literal = re.search(re.escape(alias), raw, re.I)
                if literal:
                    add(literal.group(), field, literal.group(), 'controlled_dictionary')
        layer_terms = doc.get('keyword_layer_terms')
        if isinstance(layer_terms, list):
            allowed = {key(mapping.get(key(t), t)) for t in layer_terms}
            # Layer classification + original substring gate is sufficient for legacy three-layer cards.
            for term in layer_terms:
                canonical = mapping.get(key(term), term)
                if not term_problem(term, explicit=True):
                    terms.add(canonical)
                    if not any(key(e['term']) == key(canonical) for e in evidence):
                        evidence.append({'term':canonical,'surface':term,'source_field':'text',
                                         'source':'keywords','quote':'','origin':'three_layer_gate','reason':'verified-layer-original-evidence'})
            for term in list(terms):
                if key(term) not in allowed:
                    reject(term, 'three_layer_gate', 'outside-selected-layer')
            terms = {t for t in terms if key(t) in allowed}
            evidence = [row for row in evidence if row['term'] in terms]
        output.append({'id':identity,'terms':sorted(terms,key=key),'evidence':evidence})
    counts=Counter()
    docs_by_term=defaultdict(list)
    sources_by_term=defaultdict(set)
    for doc in output:
        counts.update(doc['terms'])
        for term in doc['terms']:
            docs_by_term[term].append(doc['id'])
        for ev in doc['evidence']:
            sources_by_term[ev['term']].add(ev['source'])
    terms=[{'term':term,'document_frequency':n,'documents':docs_by_term[term],
            'sources':sorted(sources_by_term[term])} for term,n in sorted(counts.items(),key=lambda p:(-p[1],key(p[0])))]
    return {'schema_version':SCHEMA_VERSION,'documents':output,'terms':terms,'audit':audit,
            'counts':{'documents':len(output),'documents_with_terms':sum(bool(d['terms']) for d in output),
                      'terms':len(terms),'rejected':len(audit)}}


def export_vosviewer(result, outdir, *, min_occurrences=1, max_terms=500):
    """Binary document-level co-occurrence, full counting, deterministic IDs.

    min_occurrences 默认 1：保留仅出现 1 篇的孤立词（VOSviewer 可显示孤立节点）。
    小语料/高度专业化论文里绝大多数词是篇内特异词，阈值 2 会剔掉约 95% 的词
    （实测 30 篇 149 词 → 8 词），故默认放宽到 1；max_terms 按篇频降序截断，
    大语料仍优先保留高频共现词。要聚焦跨篇共享词时显式调高 --min-occurrences。
    """
    if min_occurrences<1 or max_terms<1:
        raise ValueError('min_occurrences and max_terms must be positive')
    folder=Path(outdir); folder.mkdir(parents=True,exist_ok=True)
    selected=[t for t in result['terms'] if t['document_frequency']>=min_occurrences][:max_terms]
    ids={item['term']:i+1 for i,item in enumerate(sorted(selected,key=lambda t:key(t['term'])))}
    edges=Counter()
    for doc in result['documents']:
        edges.update(combinations(sorted({ids[t] for t in doc['terms'] if t in ids}),2))
    strengths=Counter()
    links=Counter()
    for (a,b),weight in edges.items():
        strengths[a]+=weight; strengths[b]+=weight; links[a]+=1;links[b]+=1
    def save_tsv(path, rows):
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream,delimiter='\t',lineterminator='\n',quoting=csv.QUOTE_MINIMAL)
            writer.writerows(rows)
    by_term={t['term']:t for t in selected}
    map_file=folder/'vosviewer_map.txt'
    network_file=folder/'vosviewer_network.txt'
    save_tsv(map_file,[['id','label','weight<Occurrences>','weight<Links>','weight<Total link strength>']]+[
        [ids[t],t,by_term[t]['document_frequency'],links[ids[t]],strengths[ids[t]]] for t in sorted(ids,key=ids.get)])
    save_tsv(network_file,[[a,b,w] for (a,b),w in sorted(edges.items())])
    # Applied aliases only; exclusion audit is deliberately separate to avoid
    # accidentally turning unrelated terms into semantic equivalences.
    replacements={}
    for doc in result['documents']:
        for ev in doc['evidence']:
            if ev['term'] in ids and key(ev['surface'])!=key(ev['term']):
                replacements[ev['surface']]=ev['term']
    save_tsv(folder/'vosviewer_thesaurus.txt',[['label','replace by']]+[[a,b] for a,b in sorted(replacements.items())])
    (folder/'keyword_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    metadata={'counting':'binary document-level co-occurrence; full counting',
              'min_occurrences':min_occurrences,'max_terms':max_terms,'documents':len(result['documents']),
              'terms':len(ids),'links':len(edges),'isolates':sum(not links[i] for i in ids.values()),
              'clustering':'not computed; choose VOSviewer layout/clustering after import',
              'empty_network':not bool(edges),'files':[map_file.name,network_file.name,'vosviewer_thesaurus.txt','keyword_evidence.json']}
    (folder/'export_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    guidance = ['# VOSviewer 导入与统计口径','','在 VOSviewer 选择创建基于网络数据的图，读取 vosviewer_map.txt 与 vosviewer_network.txt。',
                '词项采用每文献二值计数：同篇出现多次只计1；边权为共同出现的文献数。未计算或伪造聚类编号。',
                f'文献{metadata["documents"]}篇；过阈值词{metadata["terms"]}个；共现边{metadata["links"]}条。',
                f'最低文献出现次数={min_occurrences}；最多词={max_terms}。空网络={metadata["empty_network"]}。',
                '空网络或仅孤立节点时不得宣称形成词群；可先核对证据，再显式降低阈值。',
                f'默认 min_occurrences={min_occurrences}：阈值1保留仅1篇的孤立词（map中weight<Links>=0），阈值≥2只留跨篇共享词。小语料用1避免词量过少，聚焦共现结构时调高。',
                'keyword_evidence.json保留逐文献词项、来源与剔除原因。thesaurus仅包含已实际应用的精确别名。',
                '默认保留完整中英文专业短语；不使用中文滑窗、英文逐词切分或自动翻译。',
                '官方文件格式：https://app.vosviewer.com/docs/file-types/map-and-network-file-type/','']
    (folder/'README.md').write_text('\n'.join(guidance),encoding='utf-8')
    return metadata
