# -*- coding: utf-8 -*-
"""Conservative same-page faculty category metadata enumeration.

No UA override, Input commands, challenge interaction, downloads or submissions.
Only this session's newly-created tab is navigated and closed.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

__all__ = ['cdp_available', 'ensure_browser', 'enumerate_by_clicking', 'CdpUnavailable']
_DEFAULT_UA = ''  # compatibility only: never sent or spoofed
_SAFETY_JS = Path(__file__).with_name('click_safety.js').read_text(encoding='utf-8')
_FACULTY_JS = Path(__file__).with_name('faculty_click_logic.js').read_text(encoding='utf-8')
_CARD_SELECTORS = ['ul.teacher-list li', 'ul.teacher_list li', 'div.teacher-list li',
                   'ul.list-teachers-item li', 'ul.teachers-list li', 'div.teacher-con li']


class CdpUnavailable(RuntimeError):
    pass


def _http_json(port, path, method='GET', timeout=8):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method=method)
    with urllib.request.urlopen(req, timeout=max(0.001, timeout)) as response:
        return json.loads(response.read().decode('utf-8'))


def _find_browser(browser=None):
    from browser_runtime import find_browser
    return find_browser(browser)


def cdp_available(port, timeout=8):
    try:
        return bool(_http_json(port, '/json/version', timeout=timeout).get('webSocketDebuggerUrl'))
    except Exception:
        return False


def ensure_browser(port=9222, *, headless=True, browser=None, wait=30):
    """Use the shared launcher, browser discovery and persistent profile paths."""
    started = time.monotonic()
    if cdp_available(port, timeout=max(0.001, wait)):
        try:
            from browser_runtime import probe_endpoint
            actual = probe_endpoint(port, timeout=min(4, max(0.001, wait)))
            if browser is not None:
                from cdp_pool import launch_one
                wd = Path(os.environ.get('PAPER_WORKDIR') or Path(__file__).resolve().parents[1])
                launch_one(wd, port, browser=browser, wait=max(0.001, wait))
            return actual['endpoint_version']['Browser'] + ' (existing endpoint)'
        except Exception as exc:
            raise CdpUnavailable(f'Existing CDP endpoint cannot be verified: {exc}') from exc
    try:
        from cdp_pool import launch_one
        exe = _find_browser(browser)
        wd = Path(os.environ.get('PAPER_WORKDIR') or Path(__file__).resolve().parents[1])
        if launch_one(wd, port, browser=exe, extra_args=['--headless=new'] if headless else [], wait=max(0.001, wait-(time.monotonic()-started))):
            return exe
    except Exception as exc:
        raise CdpUnavailable(f'Browser launch failed on port {port}: {exc}') from exc
    raise CdpUnavailable(f'CDP port {port} unavailable; optional manual prestart: {_prestart_command(port, browser)}')


def _prestart_command(port, browser=None):
    """Legacy diagnostic command; launch failures have environment-specific causes."""
    try:
        exe = _find_browser(browser)
    except (FileNotFoundError, PermissionError):
        exe = 'chrome'
    prof = Path(os.environ.get('TEMP') or '/tmp') / f'cdp-{port}'
    prefix = 'start "" ' if os.name == 'nt' else ''
    return f'{prefix}"{exe}" --headless=new --remote-debugging-port={port} --user-data-dir="{prof}" about:blank'


class _Session:
    def __init__(self, port, url, user_agent='', *, deadline=None):
        self.port = port
        self.deadline = deadline if deadline is not None else time.monotonic() + 60
        self._id = 0
        self._backend = ''
        self._ws = self._loop = self._async_ws = None
        self.tab_id = None
        try:
            target = _http_json(port, '/json/new?about:blank', method='PUT', timeout=self.remaining())
            self.tab_id = target['id']
            self.ws_url = target['webSocketDebuggerUrl']
            self._connect()
        except Exception:
            self.close()
            raise

    def remaining(self, timeout=None):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('absolute enumeration budget exhausted')
        return min(left, timeout) if timeout is not None else left

    def _connect(self):
        try:
            import websocket
            self._ws = websocket.create_connection(self.ws_url, timeout=self.remaining(), suppress_origin=True)
            self._backend = 'websocket-client'
            return
        except ImportError:
            pass
        except Exception:
            if self._ws:
                self._ws.close()
            self._ws = None
        try:
            import websockets
        except ImportError as exc:
            raise CdpUnavailable('websocket-client or websockets is required') from exc
        self._loop = asyncio.new_event_loop()
        async def connect():
            return await asyncio.wait_for(websockets.connect(self.ws_url, max_size=32*1024*1024), timeout=self.remaining())
        self._async_ws = self._loop.run_until_complete(connect())
        self._backend = 'websockets'

    def send(self, method, params=None, timeout=60):
        # This adapter supports metadata operations only; never send browser input.
        if method.startswith('Input.'):
            raise ValueError('Input methods are forbidden in faculty metadata enumeration')
        deadline = time.monotonic() + self.remaining(timeout)
        self._id += 1
        mid = self._id
        payload = {'id': mid, 'method': method, 'params': params or {}}
        if self._backend == 'websocket-client':
            self._ws.settimeout(max(0.001, deadline - time.monotonic()))
            self._ws.send(json.dumps(payload))
            while True:
                left = min(self.remaining(), deadline - time.monotonic())
                if left <= 0:
                    raise TimeoutError(f'{method}: absolute call timeout')
                self._ws.settimeout(left)
                msg = json.loads(self._ws.recv())
                if msg.get('id') == mid:
                    return self._result(msg, method)
        return self._loop.run_until_complete(asyncio.wait_for(self._asend(mid, payload), timeout=max(0.001, deadline-time.monotonic())))

    @staticmethod
    def _result(msg, method):
        if 'error' in msg:
            raise RuntimeError(f'{method}: {msg["error"]}')
        return msg.get('result', {})

    async def _asend(self, mid, payload):
        await self._async_ws.send(json.dumps(payload))
        while True:
            msg = json.loads(await self._async_ws.recv())
            if msg.get('id') == mid:
                return self._result(msg, payload['method'])

    def js(self, expression, timeout=60):
        result = self.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True}, timeout)
        if 'exceptionDetails' in result:
            raise RuntimeError(str(result['exceptionDetails']))
        return result.get('result', {}).get('value')

    def close(self):
        # Close only the target we created, even on connection/navigation failure.
        if self.tab_id:
            try:
                _http_json(self.port, '/json/close/' + urllib.parse.quote(self.tab_id, safe=''), timeout=2)
            except Exception:
                pass
            self.tab_id = None
        try:
            if self._ws:
                self._ws.close()
            if self._async_ws is not None and self._loop is not None:
                self._loop.run_until_complete(asyncio.wait_for(self._async_ws.close(), timeout=1))
        except Exception:
            pass
        if self._loop is not None:
            self._loop.close()


def _expression(cfg, action, identity=None):
    return '(() => {const safety = ' + _SAFETY_JS + ';const cfg = ' + json.dumps(cfg) + ';const action = ' + json.dumps(action) + ';const identity = ' + json.dumps(identity) + ';return (' + _FACULTY_JS + ');})()'


def _completion(before, after, category):
    if after.get('challenge'):
        return 'challenge'
    # Explicit completion is category-correlated, and requires a changed token/state
    # unless this category was already explicitly complete before the click.
    state, old = after.get('state') or {}, before.get('state') or {}
    if category.get('data_key') and state.get('key') == category['data_key'] and state.get('state') == 'complete':
        if state != old or (old.get('key') == category['data_key'] and old.get('state') == 'complete'):
            return 'completion_marker'
    if after.get('signature') != before.get('signature'):
        return 'signature_changed'
    return ''


def enumerate_by_clicking(url, *, port=9222, click_selector='a.col_item_link',
                          card_selector='', wait=3.0, max_clicks=30, user_agent=_DEFAULT_UA,
                          headless=True, browser=None, budget=60.0, completion_selector='.faculty-load-state'):
    """Enumerate safe same-page categories; incomplete evidence yields partial.

    wait is a per-category maximum evidence wait, not a fixed post-click sleep.
    completion_selector may expose data-category-key/data-state=complete and an
    optional data-load-token; plain unchanged data is never assigned to a new category.
    per_category is a list to preserve repeated names and evidence; old counts are
    available under per_category_counts (keys are stable category identities).
    """
    start = time.monotonic()
    deadline = start + max(0.001, float(budget))
    ensure_browser(port, headless=headless, browser=browser, wait=max(0.001, deadline-time.monotonic()))
    session = _Session(port, url, user_agent, deadline=deadline)
    records, collected, categories = [], {}, []
    cfg = {'click_selector': click_selector, 'card_selector': card_selector or _CARD_SELECTORS[0], 'completion_selector': completion_selector}
    result = {'teachers': [], 'categories': [], 'per_category': records, 'per_category_counts': {}, 'clicks': 0, 'source_url': url, 'why': 'partial', 'partial': True, 'manual_required': False}
    try:
        session.send('Page.enable')
        session.send('Runtime.enable')
        session.send('Page.navigate', {'url': url})
        initial_end = min(deadline, time.monotonic() + max(1.0, float(wait)))
        while True:
            listing = session.js(_expression(cfg, 'list')) or {}
            if listing.get('challenge'):
                result['manual_required'] = True
                result['reason'] = listing['challenge']
                return result
            categories = listing.get('categories') or []
            if categories or time.monotonic() >= initial_end:
                break
            time.sleep(min(0.1, max(0, initial_end-time.monotonic())))
        if not card_selector:
            for candidate in _CARD_SELECTORS:
                if session.js(f'document.querySelectorAll({json.dumps(candidate)}).length'):
                    cfg['card_selector'] = candidate
                    break
        chosen = categories[:max(0, int(max_clicks))]
        result['categories'] = [c['text'] for c in chosen]
        result['truncated'] = len(chosen) < len(categories)
        result['category_total'] = len(categories)
        for category in chosen:
            rec = {'identity': category, 'category': category['text'], 'attempted': False, 'clicked': False, 'verified': False, 'blocked': False, 'status': 'partial', 'reason': '', 'count': 0}
            records.append(rec)
            clicked = session.js(_expression(cfg, 'click', category)) or {}
            rec.update({k: clicked[k] for k in ('attempted', 'clicked', 'blocked', 'reason') if k in clicked})
            if clicked.get('challenge'):
                result['manual_required'] = True
                rec['reason'] = clicked['challenge']
                break
            if not clicked.get('clicked'):
                rec['status'] = 'blocked'
                continue
            result['clicks'] += 1
            before = clicked['before']
            rec['before_signature'] = before.get('signature')
            until = min(deadline, time.monotonic() + max(0.0, float(wait)))
            evidence = ''
            after = before
            while True:
                after = session.js(_expression(cfg, 'snapshot')) or {}
                evidence = _completion(before, after, category)
                if evidence or time.monotonic() >= until:
                    break
                time.sleep(min(0.1, max(0, until-time.monotonic())))
            rec['after_signature'] = after.get('signature')
            rec['completion_state'] = after.get('state')
            rec['evidence'] = evidence
            if evidence == 'challenge':
                result['manual_required'] = True
                rec['reason'] = after.get('challenge')
                break
            if not evidence:
                rec['reason'] = 'unchanged_or_timeout'
                continue
            valid = []
            for item in after.get('items', []):
                name = (item.get('name') or '').strip()
                if not name or len(name) > 20 or re.search(r'[，,。；;、/（）()]', name):
                    continue
                valid.append(item)
            rec['count'] = len(valid)
            if not valid:
                rec['reason'] = 'empty_category'
                continue
            rec.update(verified=True, status='verified')
            for item in valid:
                key = item.get('href') or item['name']
                teacher = collected.setdefault(key, {'name': item['name'], 'url': item.get('href') or '', 'categories': []})
                if category['text'] not in teacher['categories']:
                    teacher['categories'].append(category['text'])
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
        if records and not records[-1]['verified']:
            records[-1]['reason'] = result['reason']
    finally:
        session.close()
        result['elapsed'] = time.monotonic()-start
        result['teachers'] = list(collected.values())
        result['card_selector'] = cfg['card_selector']
        result['per_category_counts'] = {r['identity']['key']: r['count'] for r in records}
        result['partial'] = not bool(records) or result.get('truncated', False) or len(records) != len(categories) or any(not r['verified'] for r in records)
        result['why'] = 'partial' if result['partial'] else 'ok'
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', nargs='?')
    parser.add_argument('--port', type=int, default=9222)
    parser.add_argument('--browser')
    parser.add_argument('--click-selector', default='a.col_item_link')
    parser.add_argument('--start-browser', action='store_true')
    args = parser.parse_args()
    if args.start_browser:
        print(ensure_browser(args.port, browser=args.browser))
    elif args.url:
        print(json.dumps(enumerate_by_clicking(args.url, port=args.port, browser=args.browser, click_selector=args.click_selector), ensure_ascii=False, indent=2))
    else:
        parser.print_help()
