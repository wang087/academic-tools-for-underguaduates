# -*- coding: utf-8 -*-
"""Offline discovery/harvest regression tests. Run with python -m unittest discover."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import faculty_discover as discover
import faculty_engines
import harvest_faculty as harvest


def selection(page, url, section, cfg):
    verdict = 'review' if 'REVIEW' in page else 'reject' if 'EMPTY' in page else 'accept'
    people = [] if verdict == 'reject' else [{
        'name': '李四' if 'SECOND' in page else '张三', 'title': '教授',
        'directions': [], 'email': '', 'url': discover.safe_link(url, '../bio/one.htm'),
        'section': section}]
    return {'engine': 'fixture', 'people': people, 'verdict': verdict,
            'score': 80, 'reasons': ['fixture evidence'], 'candidates': []}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.missing = self.root / 'absent.json'
        self.cfg = {'universities': {}, 'list_types': {'engine_dispatch': {'_engine_dispatch': True}}}
        self.selector = mock.patch.object(faculty_engines, 'select_parser', side_effect=selection, create=True)
        self.selector.start()
        self.addCleanup(self.selector.stop)

    def quiet(self, callback, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return callback(args)

    def test_missing_default_and_explicit_config(self):
        with mock.patch.object(discover, 'CONFIG_PATH', self.missing), mock.patch.object(harvest, 'CONFIG_PATH', self.missing):
            self.assertEqual(discover.load_cfg()['universities'], {})
            self.assertEqual(harvest.load_config()['universities'], {})
            for loader in (discover.load_cfg, harvest.load_config):
                with self.assertRaises(SystemExit):
                    loader(self.missing)
            self.missing.write_text('not json', encoding='utf-8')
            for loader in (discover.load_cfg, harvest.load_config):
                with self.assertRaises(SystemExit):
                    loader(self.missing)

    def test_relative_base_protocol_and_boundary(self):
        html = '''<base href="../faculty/"><a href="./list.htm">专任教师</a>
        <a href="../research/list.htm">研究人员</a>
        <a href="//dept.example.edu.cn/tutors.htm">博士生导师</a>
        <a href="https://evil.example.net/">师资队伍</a>
        <a href="/news/one.htm">教师名录</a>
        <a href="/event.htm">教授做客讲座</a>
        <a href="javascript:void(0)">教师</a>'''
        urls = [r['url'] for r in discover.discover_faculty_pages('https://dept.example.edu.cn/cn/index.htm', html)]
        self.assertEqual(set(urls), {'https://dept.example.edu.cn/faculty/list.htm',
            'https://dept.example.edu.cn/research/list.htm', 'https://dept.example.edu.cn/tutors.htm'})
        self.assertEqual(harvest._abs_url('https://dept.example.edu.cn/lists/index.htm', '../bio/a.htm'),
                         'https://dept.example.edu.cn/bio/a.htm')

    def test_next_links_actual_url_and_no_guessed_pagination(self):
        first = 'https://dept.example.edu.cn/people/index.html'
        second = 'https://dept.example.edu.cn/more/staff?p=2'
        pages = {first: '<a rel="next" href="../more/staff?p=2">下一页</a>', second: 'SECOND'}
        fetcher = mock.Mock(side_effect=pages.__getitem__)
        rec = discover.collect_page(first, self.cfg, fetcher=fetcher)
        self.assertEqual([c.args[0] for c in fetcher.call_args_list], [first, second])
        self.assertEqual(rec['pages_fetched'], 2)
        self.assertEqual(rec['records'][1]['source_url'], second)
        self.assertEqual(rec['records'][1]['url'], 'https://dept.example.edu.cn/bio/one.htm')
        self.assertEqual(rec['verdict'], 'accept')
        self.assertFalse(rec['complete'])
        self.assertEqual(rec['coverage'], 'unverified')
        rec = discover.collect_page(first, self.cfg, fetcher=lambda u: '页码 1/3 共3人')
        self.assertEqual(rec['pages_fetched'], 1)
        self.assertEqual(rec['verdict'], 'review')
        self.assertTrue(rec['partial'])
        self.assertTrue(rec['evidence'])

    def test_next_cap_and_external_rejected(self):
        start = 'https://dept.example.edu.cn/people/'
        rec = discover.collect_page(start, self.cfg, fetcher=lambda u: '<a href="two" rel="next">next</a>', max_pages=1)
        self.assertTrue(rec['partial'])
        self.assertIn('达到分页上限', rec['reasons'])
        self.assertEqual(discover.next_page_url('<a rel="next" href="https://outside.net/x">下一页</a>', start), '')

    def test_review_reject_and_url_isolation(self):
        url = 'https://dept.example.edu.cn/a/list.htm'
        with mock.patch.object(discover, 'CONFIG_PATH', self.missing), mock.patch.object(discover, 'fetch', return_value='REVIEW'):
            status = self.quiet(discover.main, ['--site', url, '--direct', '--write', '--out', str(self.root / 'out')])
        self.assertEqual(status, 4)
        self.assertFalse(self.missing.exists())
        result = json.loads(next((self.root / 'out').glob('*_faculty.json')).read_text(encoding='utf-8'))
        self.assertEqual(result['verdict'], 'review')
        self.assertFalse(result['complete'])
        self.assertEqual(result['source_url'], url)
        self.assertEqual(result['evidence'][0]['reasons'], ['fixture evidence'])
        other = url.replace('/a/', '/b/')
        with mock.patch.object(discover, 'fetch', return_value='ACCEPT'):
            self.quiet(discover.main, ['--url', other, '--out', str(self.root / 'out')])
        self.assertEqual(len(list((self.root / 'out').glob('*_faculty.json'))), 2)
        with mock.patch.object(discover, 'fetch', return_value='EMPTY'):
            self.assertEqual(self.quiet(discover.main, ['--url', other, '--dry-run']), 3)

    def test_two_hop_directory_stays_review(self):
        site = 'https://dept.example.edu.cn/'
        pages = {site: '<a href="faculty/">师资队伍</a>',
            site + 'faculty/': 'EMPTY <a href="../lab/">教研室人员简介</a>',
            site + 'lab/': 'EMPTY <a href="../third/">研究人员</a>'}
        fetcher = mock.Mock(side_effect=pages.__getitem__)
        recs = discover.discover_site(site, self.cfg, fetcher=fetcher)
        self.assertEqual(len(recs), 2)
        self.assertTrue(all(r['verdict'] == 'review' and r['partial'] for r in recs))
        self.assertEqual(recs[1]['discovery_depth'], 2)
        self.assertTrue(recs[1]['evidence'][-1]['directory_links'])

    def test_dry_run_never_writes_or_opens_logger(self):
        site = 'https://dept.example.edu.cn/'
        def fake_fetch(url):
            return '<a href="people/">师资队伍</a>' if url == site else 'ACCEPT'
        with mock.patch.object(discover, 'CONFIG_PATH', self.missing), mock.patch.object(harvest, 'CONFIG_PATH', self.missing), \
             mock.patch.object(discover, 'fetch', side_effect=fake_fetch), mock.patch.object(harvest, 'get') as logger, \
             mock.patch.object(pathlib.Path, 'mkdir', side_effect=AssertionError('mkdir during dry run')), \
             mock.patch.object(pathlib.Path, 'write_text', side_effect=AssertionError('write during dry run')):
            self.assertEqual(self.quiet(discover.main, ['--site', site + 'people/', '--direct', '--dry-run', '--out', str(self.root / 'none')]), 0)
            self.assertEqual(self.quiet(harvest.main, ['--site', site, '--dry-run', '--out', str(self.root / 'none')]), 0)
            logger.assert_not_called()
        self.assertFalse((self.root / 'none').exists())

    def test_harvest_url_and_site_formal_list_pipeline(self):
        site = 'https://dept.example.edu.cn/'
        page_url = site + 'people/index.html'
        def fake_fetch(url):
            return '<a href="people/index.html">师资队伍</a>' if url == site else 'ACCEPT'
        for flag, source in [('--url', page_url), ('--site', site)]:
            out = self.root / flag[2:]
            with mock.patch.object(harvest, 'CONFIG_PATH', self.missing), \
                 mock.patch.object(discover, 'fetch', side_effect=fake_fetch), \
                 mock.patch.object(harvest, 'fetch', side_effect=fake_fetch), mock.patch.object(harvest, 'get', return_value=mock.Mock()):
                self.assertEqual(self.quiet(harvest.main, [flag, source, '--unit-name', '院系名称非栏目名', '--stage', 'list', '--out', str(out)]), 0)
            directory = out / discover.url_key(source)
            self.assertTrue((directory / 'faculty_list.json').exists())
            records = json.loads((directory / 'faculty_list.json').read_text(encoding='utf-8'))
            self.assertEqual(records[0]['source_url'], page_url)
            evidence = json.loads((directory / 'faculty_evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence[0]['engine'], 'fixture')
            self.assertFalse(evidence[0]['complete'])
            self.assertEqual(evidence[0]['coverage'], 'unverified')
            self.assertEqual(evidence[0]['verdict'], 'accept')
            self.assertTrue((directory / 'raw_list' / (discover.url_key(page_url) + '.html')).exists())

    def test_real_selector_preserves_relative_profile_and_engine(self):
        self.selector.stop()
        page = '<p>共2人</p><table><tr><th>姓名</th><th>研究方向</th></tr>' \
               '<tr><td><a href="../bio/zhang.htm">张三</a></td><td>结构生物学</td></tr>' \
               '<tr><td><a href="../bio/li.htm">李四</a></td><td>分子生物学</td></tr></table>'
        url = 'https://dept.example.edu.cn/faculty/index.htm'
        rec = discover.collect_page(url, self.cfg, fetcher=lambda u: page)
        self.assertTrue(rec['records'])
        self.assertEqual(rec['records'][0]['url'], 'https://dept.example.edu.cn/bio/zhang.htm')
        self.assertEqual(rec['records'][0]['source_url'], url)
        self.assertTrue(rec['records'][0]['parser_engine'])

    def test_harvest_review_without_records_keeps_evidence(self):
        site = 'https://dept.example.edu.cn/'
        pages = {site: '<a href="lab/">教研室人员简介</a>', site + 'lab/': 'EMPTY'}
        with mock.patch.object(harvest, 'CONFIG_PATH', self.missing), \
             mock.patch.object(discover, 'fetch', side_effect=pages.__getitem__), \
             mock.patch.object(harvest, 'get', return_value=mock.Mock()):
            status = self.quiet(harvest.main, ['--site', site, '--stage', 'list', '--out', str(self.root / 'review')])
        self.assertEqual(status, 4)
        evidence = self.root / 'review' / discover.url_key(site) / 'faculty_discovery.json'
        rec = json.loads(evidence.read_text(encoding='utf-8'))[0]
        self.assertEqual(rec['verdict'], 'review')
        self.assertTrue(rec['partial'])
        self.assertTrue(rec['evidence'])

    def test_response_charset_and_final_url_for_both_fetchers(self):
        url = 'https://dept.example.edu.cn/old'
        final = 'https://dept.example.edu.cn/cn/faculty/index.htm'
        for module in (discover, harvest):
            for headers, body in [({'Content-Type': 'text/html; charset=gb2312'}, '专任教师'.encode('gb2312')),
                                  ({}, '<meta charset="gb18030">研究人员'.encode('gb18030'))]:
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.headers = headers
                response.read.return_value = body
                response.geturl.return_value = final
                with mock.patch.object(module.urllib.request, 'urlopen', return_value=response):
                    page = module.fetch(url, retries=1)
                self.assertIsInstance(page, str)
                self.assertEqual(page.final_url, final)
                self.assertTrue('教师' in page or '研究人员' in page)

    def test_redirected_next_and_scope_are_final_url_based(self):
        start = 'https://dept.example.edu.cn/go'
        final = 'https://dept.example.edu.cn/faculty/index.htm'
        following = 'https://dept.example.edu.cn/faculty/more.htm'
        pages = {start: discover.FetchedPage('<title>教授 ALL</title>共2条<a rel="next" href="more.htm">下一页</a>', final),
                 following: 'SECOND 共2条'}
        fetcher = mock.Mock(side_effect=pages.__getitem__)
        rec = discover.collect_page(start, self.cfg, '教授', fetcher=fetcher)
        self.assertEqual(fetcher.call_args_list[1].args[0], following)
        self.assertEqual(rec['source_url'], final)
        self.assertEqual(rec['evidence'][0]['requested_url'], start)
        self.assertEqual(rec['records'][0]['url'], 'https://dept.example.edu.cn/bio/one.htm')
        self.assertEqual(rec['declared_records'], 2)
        self.assertEqual(rec['record_count'], 2)
        self.assertEqual(rec['unique_name_count'], 2)
        self.assertEqual(rec['scope']['title'], '教授 ALL')
        self.assertEqual(rec['scope']['unit_coverage'], 'unverified')
        self.assertEqual(rec['coverage'], 'verified')
        self.assertTrue(rec['complete'])

    def test_same_name_different_urls_is_review_and_records_preserved(self):
        start = 'https://dept.example.edu.cn/a/list.htm'
        def same_name(page, url, section, cfg):
            return {'engine': 'fixture', 'verdict': 'accept', 'reasons': [],
                    'people': [{'name': '张三', 'url': url + '/profile', 'directions': [], 'section': section}]}
        with mock.patch.object(discover, 'select_page', side_effect=same_name):
            rec = discover.collect_page(start, self.cfg, fetcher=lambda u: '共2条<a rel="next" href="../b/list.htm">next</a>' if u == start else '共2条')
        self.assertEqual(rec['record_count'], 2)
        self.assertEqual(rec['unique_name_count'], 1)
        self.assertEqual(rec['verdict'], 'review')
        self.assertTrue(rec['identity_ambiguity'])
        self.assertTrue(rec['partial'])
        kept = harvest.dedupe_people(rec['records'])
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(p['identity_ambiguity'] for p in kept))

    def test_detail_keeps_ambiguous_identity_records_separate(self):
        people = [{'name': '张三', 'section': '师资', 'url': 'https://dept.example.edu.cn/' + path,
                   'directions': [], 'identity_ambiguity': True} for path in ('one', 'two')]
        directory = self.root / 'detail'
        directory.mkdir()
        (directory / 'faculty_list.json').write_text(json.dumps(people), encoding='utf-8')
        with mock.patch.object(harvest, 'fetch', side_effect=lambda url, **kw: url), \
             mock.patch.object(harvest, 'extract_directions_from_detail', side_effect=lambda page, cfg: [page]), \
             mock.patch.object(harvest.time, 'sleep'):
            result = harvest.stage_detail({'list_type': 'engine_dispatch'}, directory, mock.Mock(), self.cfg)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['directions'], result[1]['directions'])
        self.assertEqual(len(list((directory / 'raw_detail').glob('*.html'))), 2)

    def test_error_page_cannot_accept_navigation_and_hint_is_not_direction(self):
        url = 'https://dept.example.edu.cn/faculty/'
        rec = discover.collect_page(url, self.cfg, fetcher=lambda u: '<title>404 页面不存在</title>张三')
        self.assertEqual(rec['verdict'], 'reject')
        self.assertEqual(rec['record_count'], 0)
        self.assertTrue(rec['evidence'][0]['reasons'])
        with mock.patch.object(discover, 'select_page', return_value={'engine': 'fixture', 'verdict': 'accept', 'reasons': [],
                'people': [{'name': '张三', 'url': url + 'one', 'directions': [], 'direction_hint': '研究方向摘要…'}]}):
            rec = discover.collect_page(url, self.cfg, fetcher=lambda u: '共1条')
        self.assertEqual(rec['records'][0]['direction_hint'], '研究方向摘要…')
        self.assertEqual(rec['records'][0]['directions'], [])

    def test_direct_directory_explains_site_traversal(self):
        output = io.StringIO()
        with mock.patch.object(discover, 'fetch', return_value='EMPTY'), contextlib.redirect_stdout(output):
            status = discover.main(['--site', 'https://dept.example.edu.cn/', '--direct', '--dry-run'])
        self.assertEqual(status, 3)
        self.assertIn('--site URL', output.getvalue())

    def test_personnel_article_heading_ignores_long_summary_and_theme_news_word(self):
        site = 'https://dept.example.edu.cn/faculty/index.htm'
        title = '《学习习近平新时代中国特色社会主义思想概论》教研室人员简介'
        page = '<a href="../info/one.htm"><h4>' + title + '</h4><span>' + ('很长的教研室简介摘要' * 20) + '</span></a>'
        links = discover.discover_faculty_pages(site, page)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['label'], title)
        titled = '<a href="../info/two.htm" title="专任教师"><h4>其他内容</h4>很长摘要</a>'
        self.assertEqual(discover.discover_faculty_pages(site, titled)[0]['label'], '专任教师')

    def test_no_url_same_name_distinct_content_kept_exact_duplicate_collapsed(self):
        url = 'https://dept.example.edu.cn/faculty/'
        people = [{'name': '张三', 'url': '', 'title': title, 'directions': [], 'section': '师资'} for title in ('教授', '副教授', '教授')]
        with mock.patch.object(discover, 'select_page', return_value={'engine': 'fixture', 'verdict': 'accept', 'people': people, 'reasons': []}):
            rec = discover.collect_page(url, self.cfg, fetcher=lambda u: '共2条')
        self.assertEqual(rec['record_count'], 2)
        self.assertEqual(rec['unique_name_count'], 1)
        self.assertEqual(rec['verdict'], 'review')
        kept = harvest.dedupe_people(people)
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(p['identity_ambiguity'] for p in kept))
        self.assertNotEqual(kept[0]['record_id'], kept[1]['record_id'])

    def test_report_warns_partial_scope_and_keeps_legacy_without_evidence(self):
        directory = self.root / 'report'
        directory.mkdir()
        unit = {'name': '学院', 'base': 'https://dept.example.edu.cn/', 'sections': [{'name': '教授'}]}
        people = [{'name': '张三', 'title': '教授', 'directions': [], 'direction_hint': '摘要…',
                   'identity_ambiguity': True, 'url': '', 'section': '教授'}]
        source = {'source_url': unit['base'] + 'faculty/', 'engine': 'semantic_table',
                  'verdict': 'accept', 'coverage': 'unverified', 'complete': False, 'partial': True,
                  'pages': 3, 'pages_fetched': 1, 'declared_records': 10,
                  'scope': {'section': '教授', 'title': '教授 ALL'}, 'evidence': []}
        (directory / 'faculty_evidence.json').write_text(json.dumps([source]), encoding='utf-8')
        with mock.patch.object(harvest, 'load_rules', return_value={}), \
             mock.patch.object(harvest, 'classify', return_value={'未分类': people}):
            payload = harvest.build_report(directory, {'name': '大学'}, unit, people, mock.Mock())
        text = (directory / '导师名单与方向归类.md').read_text(encoding='utf-8')
        for expected in ('当前栏目采集记录，非全院完整或导师资格认证', 'unverified', '教授 ALL', '缺页：**2**', 'direction_hint', '唯一姓名数', '条记录'):
            self.assertIn(expected, text)
        self.assertEqual(payload['coverage_evidence']['record_count'], 1)
        self.assertEqual(payload['coverage_evidence']['identity_ambiguity_records'], 1)
        self.assertEqual(payload['total_unit'], 'records')
        machine = json.loads((directory / 'faculty_grouped.json').read_text(encoding='utf-8'))
        self.assertEqual(machine['coverage_evidence']['coverage'], 'unverified')
        legacy = self.root / 'legacy'
        legacy.mkdir()
        with mock.patch.object(harvest, 'load_rules', return_value={}), \
             mock.patch.object(harvest, 'classify', return_value={'未分类': people}):
            old_payload = harvest.build_report(legacy, {'name': '大学'}, unit, people, mock.Mock())
        self.assertNotIn('coverage_evidence', old_payload)
        old_text = (legacy / '导师名单与方向归类.md').read_text(encoding='utf-8')
        self.assertIn('导师名单与方向归类', old_text)
        self.assertNotIn('非全院完整', old_text)

    def test_fixed_engine_receives_actual_page_url(self):
        with mock.patch.object(faculty_engines, 'parse_with', return_value=[]) as parse:
            harvest.parse_list_page('x', {'_engine_dispatch': True}, self.cfg,
                {'engine': 'fixture', '_url': 'https://dept.example.edu.cn/list/index.htm'}, 'https://dept.example.edu.cn')
            self.assertEqual(parse.call_args.args[2], 'https://dept.example.edu.cn/list/index.htm')


if __name__ == '__main__':
    unittest.main(verbosity=2)
