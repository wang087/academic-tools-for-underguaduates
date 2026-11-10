# -*- coding: utf-8 -*-
"""Local file-fixture consent regression; requires an explicitly owned browser.

Run: python scripts/selftest_consent.py --browser PATH --port 9251 [--keep]
This test launches a browser. It never contacts remote sites or clicks challenges.
For browser-free unit tests, run selftest_click_logic.py instead.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile

FIXTURE = """<!doctype html><html><head><meta charset="utf-8"><title>consent fixture</title></head>
<body><h1>Local article body</h1>
<button id="benign" onclick="hit('benign')">Accept cookies</button>
<div id="CybotCookiebotDialog" style="position:fixed;left:0;bottom:0;width:400px;height:100px;background:#eee">
<p>We use cookies.</p><button onclick="hit('fixed');this.parentElement.remove()">Accept all cookies</button></div>
<div id="cookie-banner" style="position:fixed;left:0;top:0;width:400px;height:100px;background:#ddd">
<p>Cookies</p><button onclick="hit('decorated');this.parentElement.remove()">Accept all →</button></div>
<div id="usercentrics-root"></div>
<script>window.__clicked=[];function hit(id){window.__clicked.push(id)}
const host=document.querySelector('#usercentrics-root');
host.attachShadow({mode:'open'}).innerHTML=`<div id="inner-banner" style="position:fixed;left:0;top:200px;width:400px;height:100px;background:#ccc"><p>Cookies</p><button onclick="window.hit('shadow');this.parentElement.remove()">同意并继续</button></div>`;
</script></body></html>"""

NOOP_FIXTURE = """<!doctype html><meta charset="utf-8"><title>no-op consent fixture</title>
<div id="usercentrics-root"></div><script>window.__clicked=[];
document.querySelector('#usercentrics-root').attachShadow({mode:'open'}).innerHTML=`<div id="noop-banner" style="position:fixed;left:20px;top:20px;width:400px;height:100px;background:#ccc"><p>We use cookies</p><button onclick="window.__clicked.push('noop')">Accept cookies</button></div>`;
</script>"""


def _port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.25):
            return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9251)
    parser.add_argument('--browser', help='Browser executable; shared pool discovery if omitted')
    parser.add_argument('--keep', action='store_true', help='Keep fixture, reports and profile artifacts after closing owned browser')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be in 1..65535')
    # Refuse reuse before allocating or changing anything.
    if _port_open(args.port):
        print(f'[X] Port {args.port} is occupied; refusing to reuse or close that browser')
        return 2
    wd = Path(tempfile.mkdtemp(prefix='consent_selftest_'))
    old_env = {key: os.environ.get(key) for key in ('PAPER_WORKDIR', 'CDP_PORT')}
    os.environ['PAPER_WORKDIR'] = str(wd)
    os.environ['CDP_PORT'] = str(args.port)
    import cdp
    import cdp_pool
    session = None
    launch_attempted = False
    rc = 2
    reports = {}
    print(f'Work directory: {wd}\nCDP port: {args.port}')
    try:
        # Recheck immediately before pool launch; pool is permitted to reuse ports
        # generally, whereas this isolated selftest must not.
        if _port_open(args.port):
            raise RuntimeError('Port became occupied before launch; refusing reuse')
        launch_attempted = True
        if not cdp_pool.launch_one(wd, args.port, level=0, browser=args.browser, wait=40):
            raise RuntimeError('Owned browser launch failed')
        tab = cdp.new_tab('about:blank', args.port)
        session = cdp.CDP(tab['webSocketDebuggerUrl'], port=args.port, tab_id=tab['id'], consent=False, timeout=10)
        checks = []
        positive = wd / 'positive.html'
        positive.write_text(FIXTURE, encoding='utf-8')
        session.navigate(positive.as_uri(), settle=0, load_timeout=10)
        positive_report = session.dismiss_consent_report(timeout=5)
        clicked = session.eval('window.__clicked || []', timeout=3) or []
        remaining = session.eval("({fixed:!!document.querySelector('#CybotCookiebotDialog'),decorated:!!document.querySelector('#cookie-banner'),shadow:!!document.querySelector('#usercentrics-root').shadowRoot.querySelector('#inner-banner')})", timeout=3)
        reports['positive'] = {'report': positive_report, 'clicked': clicked, 'remaining': remaining}
        checks.extend([
            ('fixed banner removed by its handler', clicked.count('fixed') == 1 and not remaining['fixed']),
            ('decorated label and real removal', clicked.count('decorated') == 1 and not remaining['decorated']),
            ('zero-size shadow host with fixed child', clicked.count('shadow') == 1 and not remaining['shadow']),
            ('outside consent button untouched', 'benign' not in clicked),
            ('three real disappearances verified', positive_report.get('verified') == 3),
            ('legacy bool true after verified disappearance', session.dismiss_consent(timeout=.5) is True),
        ])
        noop = wd / 'noop.html'
        noop.write_text(NOOP_FIXTURE, encoding='utf-8')
        session.navigate(noop.as_uri(), settle=0, load_timeout=10)
        noop_report = session.dismiss_consent_report(timeout=.75)
        legacy = session.dismiss_consent(timeout=.2)
        noop_state = session.eval("({clicked:window.__clicked,visible:!!document.querySelector('#usercentrics-root').shadowRoot.querySelector('#noop-banner')})", timeout=3)
        reports['noop'] = {'report': noop_report, 'legacy_bool': legacy, 'state': noop_state}
        checks.extend([
            ('no-op click dispatched exactly once', noop_state['clicked'] == ['noop']),
            ('no-op shadow banner remains', noop_state['visible']),
            ('no-op cannot be verified', noop_report.get('clicked') == 1 and noop_report.get('verified') == 0 and noop_report.get('unchanged') is True and legacy is False),
        ])
        rc = 0 if all(ok for _, ok in checks) else 1
        reports['checks'] = [{'name': name, 'pass': ok} for name, ok in checks]
        for name, ok in checks:
            print(f"{'[OK]' if ok else '[X]'} {name}")
    except Exception as exc:
        print(f'[X] {type(exc).__name__}: {exc}')
        reports['error'] = f'{type(exc).__name__}: {exc}'
        rc = 2
    finally:
        if session:
            session.close_tab_self()
        # Exact work-directory/profile ownership is required for cleanup.
        if launch_attempted:
            try:
                cdp_pool.kill_port(args.port, wd=wd)
            except Exception as exc:
                print(f'[X] Owned browser cleanup failed: {exc}')
                reports['cleanup_error'] = str(exc)
                rc = 2
        (wd / 'consent-report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if args.keep:
            print(f'--keep: artifacts preserved at {wd}')
        else:
            shutil.rmtree(wd, ignore_errors=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
