# -*- coding: utf-8 -*-
"""Small, dependency-free DOM helpers for public university faculty metadata.

No school names, CSS selectors or production fixture data are required.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}
HIDDEN = {'script', 'style', 'template', 'noscript', 'svg'}


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    def text(self, separator=' '):
        parts = []
        def walk(node):
            if isinstance(node, str):
                parts.append(node)
            elif node.tag not in HIDDEN:
                if node.tag == 'br':
                    parts.append(separator)
                else:
                    for child in node.children:
                        walk(child)
                    parts.append(separator)
        walk(self)
        return re.sub(r'\s+', ' ', ''.join(parts)).strip()

    def nodes(self, tags=None):
        for child in self.children:
            if isinstance(child, Node):
                if tags is None or child.tag in tags:
                    yield child
                yield from child.nodes(tags)

    def ancestor(self, tags):
        node = self.parent
        while node is not None:
            if node.tag in tags:
                return node
            node = node.parent
        return None


class Tree(HTMLParser):
    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.root = Node('document')
        self.stack = [self.root]
        self.feed(page or '')
        self.close()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        # Common optional closing tags in CMS-generated tables/lists.
        autoclose = {'td': {'td', 'th'}, 'th': {'td', 'th'},
                     'tr': {'td', 'th', 'tr'}, 'li': {'li'}}
        if tag in autoclose:
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in autoclose[tag]:
                    del self.stack[i:]
                    break
                if self.stack[i].tag in {'table', 'ul', 'ol'}:
                    break
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def absolute(page_url, href):
    href = (href or '').strip()
    if not href or href.startswith('#'):
        return ''
    url = urljoin(page_url, href)
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        return ''
    if '/system/' in parsed.path.lower() and any(
            k in parsed.path.lower() for k in ('addnews', 'login', 'admin')):
        return ''
    return url


def document_url(root, url):
    base = next(root.nodes({'base'}), None)
    return absolute(url, base.attrs.get('href')) or url if base else url


def in_navigation(node):
    while node:
        if node.tag in HIDDEN | {'nav', 'header', 'footer'}:
            return True
        sig = ' '.join((node.attrs.get('class') or '', node.attrs.get('id') or '')).lower()
        if re.search(r'(?:^|[ _-])(?:nav|menu|footer|breadcrumb)(?:$|[ _-])', sig):
            return True
        node = node.parent
    return False


TITLE_RE = re.compile(
    r'(?:教授级高级工程师|正高级工程师|特聘副研究员|特任副研究员|'
    r'副研究员|助理研究员|特聘研究员|特任研究员|百人计划研究员|助理实验师|'
    r'副教授|助理教授|讲席教授|特聘教授|特任教授|客座教授|二级教授|'
    r'教授|研究员|高级实验师|高级工程师|讲师|实验师|工程师|博士后|院士)')


def name_title(text, is_name):
    text = re.sub(r'\s+', ' ', text or '').strip()
    if re.search(r'[\u4e00-\u9fff]', text):
        text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    # Strip an explicit title before validating a 2-character name+2-character title.
    match = TITLE_RE.search(text)
    if match and match.end() == len(text):
        name = text[:match.start()].strip()
        if is_name(name):
            return name, match.group()
    if is_name(text):
        return text, ''
    m = re.fullmatch(r'(.{2,45}?)\s*[（(]?(' + TITLE_RE.pattern + r')[）)]?', text)
    if m:
        name = m.group(1).strip()
        if is_name(name):
            return name, m.group(2)
    return '', ''


def direction_excerpt(text):
    """Only complete, explicitly labelled research text enters directions."""
    m = re.search(r'(?:主要)?研究(?:方向|领域|兴趣)\s*(?:为|是|包括)?\s*[：:]\s*'
                  r'([^。\n]{4,400})(?:[。]|$)', text)
    if not m:
        return [], ''
    value = m.group(1).strip()
    value = re.split(r'主持|先后主持|工作邮箱|电子邮箱|电子邮件|办公电话|联系电话|电话[：:]|邮箱[：:]|Email|E-mail|联系方式|教育经历|工作经历|代表性论文', value, flags=re.I)[0]
    if not value or re.search(r'\.{3}|…', value):
        return [], value
    return [value.strip(' ，,；;')], ''


def _grid(table):
    """Expand rowspan/colspan inside one table; never merge independent tables."""
    grid, spans = [], {}
    for ri, row in enumerate(table.nodes({'tr'})):
        if row.ancestor({'table'}) is not table:
            continue
        cells = [c for c in row.nodes({'td', 'th'}) if c.ancestor({'tr'}) is row]
        values, ci = {}, 0
        for col, (until, cell) in list(spans.items()):
            if until >= ri:
                values[col] = cell
            else:
                del spans[col]
        for cell in cells:
            while ci in values:
                ci += 1
            try:
                colspan = max(1, min(30, int(cell.attrs.get('colspan') or 1)))
                rowspan = max(1, min(300, int(cell.attrs.get('rowspan') or 1)))
            except ValueError:
                colspan = rowspan = 1
            for col in range(ci, ci + colspan):
                values[col] = cell
                if rowspan > 1:
                    spans[col] = (ri + rowspan - 1, cell)
            ci += colspan
        if values:
            grid.append([values.get(i) for i in range(max(values) + 1)])
    return grid


def semantic_tables(page, url, section, is_name):
    root = Tree(page).root
    url = document_url(root, url)
    people, seen = [], set()
    for table in root.nodes({'table'}):
        if in_navigation(table):
            continue
        rows = _grid(table)
        headers, header_i = [], -1
        for i, row in enumerate(rows[:8]):
            texts = [re.sub(r'\s+', '', c.text()) if c else '' for c in row]
            if any(t in {'姓名', '教师姓名', '导师姓名', 'Name'} for t in texts) and any(
                    any(k in t for k in ('职称', '研究方向', '研究领域', '个人简介', '教师简介'))
                    for t in texts):
                headers, header_i = texts, i
                break
        if header_i < 0:
            continue
        def column(*words):
            return next((i for i, t in enumerate(headers) if any(w in t for w in words)), -1)
        ni = column('姓名', 'Name')
        ti = column('职称', '职级')
        di = column('研究方向', '研究领域', '研究兴趣')
        bi = column('个人简介', '教师简介')
        ei = column('邮箱', 'Email', 'E-mail')
        for row in rows[header_i + 1:]:
            def cell(i):
                return row[i] if 0 <= i < len(row) else None
            name_cell = cell(ni)
            if not name_cell:
                continue
            name, title = name_title(name_cell.text().replace(' ', ''), is_name)
            if not name:
                continue
            if cell(ti):
                title = cell(ti).text()
            bio = cell(bi).text() if cell(bi) else ''
            if not title:
                match = TITLE_RE.search(bio)
                title = match.group() if match else ''
            directions, hint = [], ''
            if cell(di):
                value = cell(di).text()
                if re.search(r'\.{3}|…', value):
                    hint = value
                elif value and value not in {'无', '暂无', '-', '待更新'}:
                    directions = [value]
            elif bio:
                directions, hint = direction_excerpt(bio)
            profile = ''
            for link in name_cell.nodes({'a'}):
                link_name, _ = name_title(link.text(), is_name)
                if link_name == name:
                    profile = absolute(url, link.attrs.get('href'))
                    break
            key = (name, profile, title, tuple(directions), bio)
            if key in seen:
                continue
            seen.add(key)
            people.append({'name': name, 'title': title, 'directions': directions,
                           'email': cell(ei).text() if cell(ei) else '',
                           'url': profile, 'section': section, 'source_url': url,
                           'direction_source': '列表页语义表格' if directions else '',
                           'direction_hint': hint, '_dir_from_title': bool(directions),
                           '_semantic_row': True})
    return people


def profile_cards(page, url, section, is_name):
    root = Tree(page).root
    url = document_url(root, url)
    people, seen = [], set()
    for link in root.nodes({'a'}):
        if in_navigation(link):
            continue
        href = absolute(url, link.attrs.get('href'))
        if not href or re.search(r'\.(?:png|jpe?g|gif|pdf|zip)(?:$|\?)', href, re.I):
            continue
        name, title = name_title(link.text(), is_name)
        if not name:
            # A separate name node / first standalone text fragment inside a card.
            for node in link.nodes({'h2', 'h3', 'h4', 'h5', 'strong', 'b', 'span', 'p', 'div'}):
                name, title = name_title(node.text(), is_name)
                if name:
                    break
        text = link.text()
        if not name:
            lead = re.match(r'^([\u4e00-\u9fff]{2,4})(?=\s|[,，。])', text)
            if lead and is_name(lead.group(1)) and TITLE_RE.search(text):
                name = lead.group(1)
        if not name:
            continue
        # Pure links require profile-shaped URLs; long cards need a biography/title.
        profile_path = bool(re.search(r'/(?:info|teacher|teachers|faculty|people|staff|person)/|/[^/]+/zh_CN/',
                                      urlsplit(href).path, re.I))
        if not profile_path and not TITLE_RE.search(text):
            continue
        if len(text) > 1000 or (len(text) > 70 and not TITLE_RE.search(text)):
            continue
        if not title:
            match = TITLE_RE.search(text)
            title = match.group() if match else ''
        # Do not infer the title from a broad page/section name.
        directions, hint = direction_excerpt(text)
        if any(p['name'] == name and p['url'] == href for p in people):
            continue
        seen.add((name, href))
        people.append({'name': name, 'title': title, 'directions': directions,
                       'direction_hint': hint, 'email': '', 'url': href, 'section': section,
                       'source_url': url, 'direction_source': '列表页明确方向字段' if directions else '',
                       '_dir_from_title': bool(directions), '_profile_card': True})
    # Photo grids include teachers with no anchor; use an explicit name+title
    # inside an individual td, never infer a URL from an unrelated image.
    for cell in root.nodes({'td'}):
        if in_navigation(cell) or len(cell.text()) > 90:
            continue
        name, title = name_title(cell.text(), is_name)
        has_named_link = any(name_title(a.text(), is_name)[0] == name
                             for a in cell.nodes({'a'}))
        if not name or not title or has_named_link:
            continue
        if any(p['name'] == name and p['title'] == title and not p['url'] for p in people):
            continue
        people.append({'name': name, 'title': title, 'directions': [], 'email': '',
                       'url': '', 'section': section, 'source_url': url,
                       '_no_homepage': True, '_profile_card': True})
    return people
