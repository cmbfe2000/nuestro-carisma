#!/usr/bin/env python3
"""Run from the repository root: python tools/sync_content.py
Python 3.12; standard library only. Imports published WordPress posts.
Existing app-only pages, curated sections, tags and timeline are preserved.
Images/PDFs remain remote links, as in the existing app.
"""
import argparse
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

API = 'https://nuestrocarisma.org/index.php/wp-json/wp/v2/posts'
NEW_SECTION = 'Nuevas publicaciones'
ALLOWED = set('p h2 h3 h4 h5 h6 strong b em i u s blockquote ul ol li a img br hr table thead tbody tr th td sup sub figure figcaption pre code div'.split())
VOID = {'img', 'br', 'hr'}
DROP = {'script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'svg', 'noscript', 'template'}
BLOCK = set('p h2 h3 h4 h5 h6 blockquote ul ol li div tr table figure figcaption pre'.split())


def safe_url(value, base):
    url = urljoin(base, value.strip())
    return url if urlsplit(url).scheme.lower() in {'http', 'https'} else ''


class Cleaner(HTMLParser):
    """Keep reading markup; discard scripts, event handlers and site styling."""
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.output, self.words, self.stack = base, [], [], []
        self.pdf = False

    def handle_starttag(self, tag, attrs):
        blocked = (self.stack and self.stack[-1][2]) or tag in DROP
        emitted = tag in ALLOWED and not blocked
        attrs = dict(attrs)
        clean = {}
        if emitted and tag == 'a':
            url = safe_url(attrs.get('href', ''), self.base)
            if attrs.get('href') and url:
                clean = {'href': url, 'target': '_blank', 'rel': 'noopener noreferrer'}
                if urlsplit(url).path.lower().endswith('.pdf'):
                    clean['class'] = 'btn pdf'
                    self.pdf = True
        if emitted and tag == 'img':
            src = attrs.get('data-src') or attrs.get('src', '')
            url = safe_url(src, self.base)
            if not src or not url:
                emitted = False
            else:
                clean = {'src': url, 'alt': attrs.get('alt', ''), 'loading': 'lazy'}
        if emitted:
            self.output.append('<' + tag + ''.join(
                f' {k}="{html.escape(v, quote=True)}"' for k, v in clean.items()) + '>')
            if tag in BLOCK or tag == 'br':
                self.words.append(' ')
        if tag not in VOID and tag not in {'input', 'embed', 'source', 'meta', 'link', 'wbr', 'area', 'base', 'col', 'param', 'track'}:
            self.stack.append((tag, emitted, blocked))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        matches = [i for i, entry in enumerate(self.stack) if entry[0] == tag]
        if not matches:
            return
        i = matches[-1]
        for name, emitted, _ in reversed(self.stack[i:]):
            if emitted:
                self.output.append(f'</{name}>')
                if name in BLOCK:
                    self.words.append(' ')
        del self.stack[i:]

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][2]:
            self.output.append(html.escape(data))
            self.words.append(data)

    def result(self):
        while self.stack:
            self.handle_endtag(self.stack[-1][0])
        return ''.join(self.output), re.sub(r'\s+', ' ', ''.join(self.words)).strip()


def fetch_posts():
    posts, page = [], 1
    while True:
        query = urlencode({'per_page': 100, 'page': page, 'orderby': 'id', 'order': 'asc',
                           '_fields': 'id,date,modified_gmt,link,status,title,content'})
        for attempt in range(3):
            try:
                req = Request(API + '?' + query, headers={'User-Agent': 'NuestroCarisma-content-sync/1.0'})
                with urlopen(req, timeout=60) as response:
                    total = int(response.headers['X-WP-TotalPages'])
                    batch = json.load(response)
                if not isinstance(batch, list) or any(p.get('status') != 'publish' for p in batch):
                    raise ValueError('Unexpected WordPress response')
                posts.extend(batch)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        if page >= total:
            break
        page += 1
    if not posts:
        raise ValueError('Empty website response; refusing to update')
    ids = [p['id'] for p in posts]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate IDs during pagination; retry the workflow')
    return posts


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def replace_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = tmp.name
        tmp.write(data)
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(root, posts, dry_run=False, refresh_existing=False):
    index_path = root / 'data/index.json'
    index = json.loads(index_path.read_text())
    search = json.loads((root / 'data/search.json').read_text())
    original = copy.deepcopy(index)
    rows = {str(row[0]): row for row in search}
    pending, changed = {}, []
    baseline = index['fetched'] + 'T00:00:00'
    section = next((s for s in index['sections'] if s['t'] == NEW_SECTION), None)
    for post in posts:
        pid = str(post['id'])
        if not pid.isdecimal():
            raise ValueError('Invalid post ID')
        old = index['docs'].get(pid)
        modified = post['modified_gmt']
        # Existing articles are curated locally (Directorios from the Derecho IVE
        # texts, local images, full texts replacing PDFs). WordPress re-touches old
        # posts often, so by default only NEW posts are imported.
        if old and (not refresh_existing or modified <= old.get('m', baseline)):
            continue
        cleaner = Cleaner(post['link'])
        cleaner.feed(post['content']['rendered'])
        body, text = cleaner.result()
        title_cleaner = Cleaner(post['link'])
        title_cleaner.feed(post['title']['rendered'])
        _, title = title_cleaner.result()
        if not title or not (text or '<img ' in body):
            raise ValueError(f'Post {pid} is empty or unsupported; no files written')
        if not old:
            if section is None:
                section = {'t': NEW_SECTION, 'items': [], 'i': 'book',
                           's': 'Publicaciones importadas del sitio web', 'n': 0}
                index['sections'].append(section)
            section['items'].insert(0, {'id': int(pid), 't': title})
        meta = dict(old or {})
        meta.update(t=title, d=post['date'][:10], u=post['link'],
                    w=len(text.split()), p=old['p'] if old else [NEW_SECTION],
                    f=int(cleaner.pdf), x=text[:240], m=modified)
        index['docs'][pid] = meta
        rows[pid] = [pid, title, text]
        pending[root / f'data/c/{pid}.html'] = body.encode()
        changed.append(pid)

    if not changed:
        print('No new or modified posts. No files changed.')
        return

    def update_items(items):
        count = 0
        for item in items:
            if 'id' in item:
                pid = str(item['id'])
                if pid in changed:
                    item['t'] = index['docs'][pid]['t']
                count += 1
            if 'g' in item:
                count += update_items(item['g'])
        return count

    for s in index['sections']:
        s['n'] = update_items(s['items'])
    today = datetime.now(timezone.utc).date().isoformat()
    index['built'] = index['fetched'] = today
    index['v'] = hashlib.sha256(encode(index) + encode(rows)).hexdigest()[:10]
    sw_path = root / 'sw.js'
    sw = sw_path.read_text()
    for constant, value in [('DATA', index['v']), ('SHELL', hashlib.sha256(
            (sw + index['v']).encode()).hexdigest()[:10])]:
        sw, count = re.subn(rf"const {constant} = '[^']+';", f"const {constant} = '{value}';", sw)
        if count != 1:
            raise ValueError(f'Cannot update {constant} version in sw.js')
    if len(index['docs']) < len(original['docs']):
        raise ValueError('Refusing to remove existing articles')
    pending[index_path] = encode(index)
    pending[root / 'data/search.json'] = encode(list(rows.values()))
    pending[sw_path] = sw.encode()
    print(f'{len(changed)} new/updated posts; {len(pending)} files: ' + ', '.join(changed))
    if dry_run:
        print('Dry run: no files written.')
        return
    # Fetching, conversion and validation finish before any file is changed.
    # Each replacement is atomic. The workflow commits only after success.
    for path, data in pending.items():
        replace_file(path, data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--refresh-existing', action='store_true',
                        help='also re-import existing posts modified on the website '
                             '(overwrites local edits to those articles)')
    args = parser.parse_args()
    run(args.root, fetch_posts(), args.dry_run, args.refresh_existing)
