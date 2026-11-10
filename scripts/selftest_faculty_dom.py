# -*- coding: utf-8 -*-
"""Synthetic offline regression tests; no bundled real-person sample data."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from faculty_dom import name_title, absolute, semantic_tables, profile_cards
from faculty_engines import is_person_name, select_parser, looks_js_rendered


class FacultyDomTests(unittest.TestCase):
    def test_name_title_order_and_spaces(self):
        self.assertEqual(name_title('李 华 副教授', is_person_name), ('李华', '副教授'))
        self.assertEqual(name_title('王明教授', is_person_name), ('王明', '教授'))
        self.assertEqual(name_title('周芳 助理实验师', is_person_name), ('周芳', '助理实验师'))
        self.assertEqual(name_title('Jane Smith', is_person_name), ('Jane Smith', ''))

    def test_relative_url_and_unsafe_link(self):
        self.assertEqual(absolute('https://x.edu.cn/a/b/list.htm','../info/1.htm'), 'https://x.edu.cn/a/info/1.htm')
        self.assertEqual(absolute('https://x.edu.cn/a/list.htm','/info/1.htm'), 'https://x.edu.cn/info/1.htm')
        self.assertEqual(absolute('https://x.edu.cn/a/list.htm','//x.edu.cn/info/1.htm'), 'https://x.edu.cn/info/1.htm')
        for url in ('javascript:alert(1)', 'mailto:x@y.cn', '/system/site/column/news/addnews.jsp?id=1'):
            self.assertEqual(absolute('https://x.edu.cn/', url), '')

    def test_tables_do_not_merge_and_preserve_link(self):
        page = '''<table><tr><td>姓名</td><td>职称</td><td>研究方向</td></tr>
        <tr><td><a href='../info/1.htm'>王明</a></td><td>副教授</td><td>细胞代谢</td></tr></table>
        <table><tr><td>课程</td><td>姓名</td></tr><tr><td>数学</td><td>李华</td></tr></table>'''
        got = semantic_tables(page, 'https://x.edu.cn/szdw/list.htm', '师资', is_person_name)
        self.assertEqual([p['name'] for p in got], ['王明'])
        self.assertEqual(got[0]['url'], 'https://x.edu.cn/info/1.htm')
        self.assertEqual(got[0]['directions'], ['细胞代谢'])

    def test_rowspan(self):
        page = '''<table><tr><td>系所</td><td>姓名</td><td>职称</td></tr>
        <tr><td rowspan='2'>研究组</td><td>李华</td><td>讲师</td></tr>
        <tr><td>王明</td><td>教授</td></tr></table>'''
        got = semantic_tables(page, 'https://x.edu.cn/people', '', is_person_name)
        self.assertEqual([p['name'] for p in got], ['李华', '王明'])
        self.assertEqual([p['title'] for p in got], ['讲师', '教授'])

    def test_group_article_semantic_biography(self):
        page = '''<table><tr><td>姓名</td><td>个人简介</td><td>照片</td></tr>
        <tr><td>李华</td><td>李华，教授。研究方向：细胞代谢。主持项目。</td><td><img></td></tr></table>'''
        got = semantic_tables(page, 'https://x.edu.cn/info/1.htm', '', is_person_name)
        self.assertEqual(got[0]['title'], '教授')
        self.assertEqual(got[0]['directions'], ['细胞代谢'])

    def test_card_truncation_is_hint(self):
        page = '''<a href='/info/1/2.htm'><h3>李华</h3><p>副教授</p>
        <p>主要研究方向：细胞代谢和...</p></a>'''
        got = profile_cards(page, 'https://x.edu.cn/szdw/list.htm', '', is_person_name)
        self.assertEqual(got[0]['directions'], [])
        self.assertTrue(got[0]['direction_hint'])

    def test_photo_grid_no_false_image_association(self):
        page = '''<table><tr><td><a href='/info/1/8.htm'><img></a>
        <a href='/info/1/2.htm'>李华 教授</a></td><td><img>王明 副教授</td></tr></table>'''
        got = profile_cards(page, 'https://x.edu.cn/list.htm', '', is_person_name)
        by_name = {p['name']:p for p in got}
        self.assertEqual(by_name['李华']['url'], 'https://x.edu.cn/info/1/2.htm')
        self.assertEqual(by_name['王明']['url'], '')

    def test_navigation_and_directory_not_people(self):
        page = '''<nav><a href='/info/1/2.htm'>教师团队</a></nav>
        <a href='/szdw/jcrc.htm'>杰出人才</a><a href='/info/1/3.htm'>教研室人员简介</a>
        <a href='/jxtz.jsp'>办公服务</a>'''
        got = select_parser(page,'https://x.edu.cn/','',{})
        self.assertEqual(got['people'], [])
        self.assertEqual(got['verdict'], 'reject')

    def test_directions_stop_before_contact_fields(self):
        page = '''<a href='/info/1/2.htm'><h3>李华</h3><p>教授</p>
        <p>研究领域：计算机网络</p><p>办公电话：12345</p><p>电子邮件：x example.cn</p></a>'''
        got = profile_cards(page, 'https://x.edu.cn/', '', is_person_name)
        self.assertEqual(got[0]['directions'], ['计算机网络'])

    def test_generic_cluster_does_not_accept_navigation(self):
        words = ['办事指南', '服务门户', '党务公开', '院务公开', '政策文件',
                 '机构设置', '研究领域', '课题研究', '教学管理', '课程建设']
        page = '<nav>' + ''.join(f'<a href="/info/1001/{i}.htm">{w}</a>' for i,w in enumerate(words)) + '</nav>'
        got = select_parser(page, 'https://x.edu.cn/', '', {})
        self.assertEqual(got['people'], [])

    def test_unlinked_same_name_different_rows_preserved(self):
        page = '''<table><tr><td>姓名</td><td>职称</td><td>研究方向</td></tr>
        <tr><td>王伟</td><td>教授</td><td>计算机视觉</td></tr>
        <tr><td>王伟</td><td>讲师</td><td>材料力学</td></tr></table>'''
        got = semantic_tables(page, 'https://x.edu.cn/', '', is_person_name)
        self.assertEqual(len(got), 2)
        self.assertNotEqual(got[0]['directions'], got[1]['directions'])

    def test_ordinary_scripts_not_js_shell(self):
        self.assertFalse(looks_js_rendered('<script src="ajax.js"></script>',0))


if __name__ == '__main__':
    unittest.main()
