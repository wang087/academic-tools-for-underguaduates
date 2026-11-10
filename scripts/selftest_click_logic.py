# -*- coding: utf-8 -*-
"""Offline policy tests: mocked CDP and Node fake DOM, never a real browser."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch
import faculty_interact as f
import cdp


class ClickLogicTests(unittest.TestCase):
    def test_completion_evidence(self):
        before = {'signature': 'old', 'state': None}
        self.assertEqual(f._completion(before, dict(before), {'data_key': 'b'}), '')
        self.assertEqual(f._completion(before, {'signature': 'new'}, {}), 'signature_changed')
        self.assertEqual(f._completion(before, {'signature': 'old', 'state': {'key': 'b', 'state': 'complete'}}, {'data_key': 'b'}), 'completion_marker')
        self.assertEqual(f._completion(before, {'challenge': 'challenge_element'}, {}), 'challenge')

    def test_cdp_lock_respects_report_budget(self):
        import threading
        client = object.__new__(cdp.CDP)
        client.timeout = 60
        client._lock = threading.Lock()
        client._lock.acquire()
        start = time.monotonic()
        try:
            report = client.dismiss_consent_report(timeout=.02)
        finally:
            client._lock.release()
        self.assertEqual(report['verified'], 0)
        self.assertIn('error', report)
        self.assertLess(time.monotonic()-start, .2)

    def test_consent_bool_requires_verified(self):
        client = object.__new__(cdp.CDP)
        client.dismiss_consent_report = lambda timeout: {'clicked': 1, 'verified': 0}
        self.assertFalse(client.dismiss_consent(1))
        client.dismiss_consent_report = lambda timeout: {'clicked': 1, 'verified': 1}
        self.assertTrue(client.dismiss_consent(1))

    def test_only_owned_target_closed(self):
        calls = []
        def http(port, path, **kwargs):
            calls.append(path)
            if path.startswith('/json/new'):
                return {'id': 'owned', 'webSocketDebuggerUrl': 'ws://fixture'}
            return {}
        with patch.object(f, '_http_json', side_effect=http), patch.object(f._Session, '_connect'):
            session = f._Session(9999, 'https://fixture/', deadline=time.monotonic()+1)
            session.close()
        self.assertEqual(calls, ['/json/new?about:blank', '/json/close/owned'])

    def test_shared_launcher(self):
        import cdp_pool
        with patch.object(f, 'cdp_available', return_value=False), patch.object(f, '_find_browser', return_value='chosen.exe'), patch.object(cdp_pool, 'launch_one', return_value=True) as launch:
            self.assertEqual(f.ensure_browser(1234, browser='chosen.exe'), 'chosen.exe')
            self.assertEqual(launch.call_args.kwargs['browser'], 'chosen.exe')
            self.assertEqual(launch.call_args.kwargs['extra_args'], ['--headless=new'])

    def test_sync_events_cannot_extend_timeout(self):
        class Socket:
            def settimeout(self, timeout): pass
            def send(self, value): pass
            def recv(self):
                time.sleep(.002)
                return '{"method":"event"}'
        session = object.__new__(f._Session)
        session.deadline = time.monotonic()+1
        session._backend = 'websocket-client'
        session._id = 0
        session._ws = Socket()
        start = time.monotonic()
        with self.assertRaises(TimeoutError): session.send('Runtime.enable', timeout=.02)
        self.assertLess(time.monotonic()-start, .2)

    def test_async_absolute_timeout(self):
        class Socket:
            async def send(self, value): pass
            async def recv(self):
                await asyncio.sleep(.002)
                return '{"method":"event"}'
        session = object.__new__(f._Session)
        session.deadline = time.monotonic()+1
        session._backend = 'websockets'
        session._id = 0
        session._async_ws = Socket()
        session._loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(TimeoutError): session.send('Runtime.enable', timeout=.02)
        finally: session._loop.close()

    def test_partial_does_not_inherit_previous_teachers(self):
        cats = [{'text': 'same', 'data_key': key, 'key': key, 'repeated_name': True} for key in ('a', 'b')]
        class FakeSession:
            closed = False
            def __init__(self, *args, **kwargs): self.current = None
            def send(self, *args, **kwargs): return {}
            def js(self, expr):
                cfg, action, identity = expr
                if action == 'list': return {'categories': cats}
                if action == 'click':
                    self.current = identity['key']
                    return {'attempted': True, 'clicked': True, 'before': {'signature': 'old', 'items': []}}
                return {'signature': 'new' if self.current == 'a' else 'old', 'items': [{'name': 'Lee', 'href': '/teacher/a'}]}
            def close(self): FakeSession.closed = True
        with patch.object(f, 'ensure_browser'), patch.object(f, '_Session', FakeSession), patch.object(f, '_expression', side_effect=lambda cfg, action, identity=None: (cfg, action, identity)):
            result = f.enumerate_by_clicking('https://fixture/', card_selector='li', wait=0)
        self.assertEqual(result['why'], 'partial')
        self.assertTrue(result['per_category'][0]['verified'])
        self.assertFalse(result['per_category'][1]['verified'])
        self.assertEqual(result['per_category'][1]['count'], 0)
        self.assertEqual(len(result['per_category']), 2)
        self.assertTrue(FakeSession.closed)

    def test_consent_selftest_refuses_occupied_port_both_arg_forms(self):
        import selftest_consent
        for args in (['--port', '9251', '--browser', 'chosen.exe'], ['--port=9251', '--browser=chosen.exe']):
            with patch.object(selftest_consent, '_port_open', return_value=True), patch.object(selftest_consent.tempfile, 'mkdtemp') as mktemp:
                self.assertEqual(selftest_consent.main(args), 2)
                mktemp.assert_not_called()

    def test_input_forbidden(self):
        session = object.__new__(f._Session)
        with self.assertRaises(ValueError): session.send('Input.dispatchMouseEvent')

    @unittest.skipUnless(shutil.which('node'), 'Node optional')
    def test_full_faculty_expression_and_stable_identity(self):
        expression = f._expression({'click_selector': 'a', 'card_selector': 'li'}, 'list')
        code = r'''
const assert=require('assert');
let nodes=['a','b','a'].map(k=>({textContent:'same',getAttribute(n){return n==='data-key'?k:n==='href'?'javascript:void(0)':null}}));
global.document={title:'fixture',body:{innerText:''},querySelectorAll(q){return q==='a'?nodes:[]}};
const src=process.argv[1];
const before=eval(src);assert.equal(before.categories.length,3);
assert.equal(before.categories[0].repeated_name,true);
const stable=before.categories[1].key;
nodes=[nodes[1],nodes[0],nodes[2]];
const after=eval(src);assert.equal(after.categories[0].key,stable);
assert.notEqual(before.categories[0].key,before.categories[2].key);
'''
        result = subprocess.run([shutil.which('node'), '-e', code, expression], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node optional')
    def test_js_fake_dom_policy(self):
        # A deliberately tiny fake DOM tests policy branches, not browser rendering.
        js = r'''
const assert = require('assert');
const fs = require('fs');
const safetySrc = fs.readFileSync(process.argv[1], 'utf8');
const consentSrc = fs.readFileSync(process.argv[2], 'utf8');
function fixture(label, mode='normal', known=true) {
 global.window = {}; global.innerWidth=800;global.innerHeight=600;
 global.location={href:'https://fixture.test/page'};
 global.setInterval=()=>0;global.clearInterval=()=>{};
 function el(tag, text) { return {tagName:tag,textContent:text,innerText:text,isConnected:true,style:{},id:'',className:'',hidden:false,inert:false,disabled:false,attrs:{},getAttribute(k){return this.attrs[k]||null},hasAttribute(k){return k in this.attrs},getRootNode(){return document},getBoundingClientRect(){return {width:100,height:30,left:10,right:110,top:10,bottom:40}},matches(){return false},querySelectorAll(){return []}};}
 const box=el('DIV','Cookie policy '+label);box.className='cookie-banner';
 const button=el('BUTTON',label);button.parentElement=box;button.hits=0;
 button.click=()=>{button.hits++;if(mode!=='noop')box.isConnected=false;};
 if(mode==='zero_host')box.getBoundingClientRect=()=>({width:0,height:0,left:0,right:0,top:0,bottom:0});
 if(mode==='disabled')button.disabled=true;
 if(mode==='hidden')button.hidden=true;
 if(mode==='inert')box.inert=true;
 if(mode==='offscreen')button.getBoundingClientRect=()=>({width:100,height:30,left:900,right:1000,top:10,bottom:40});
 box.querySelectorAll=(q)=>q==='*'?[button]:[button];
 if(mode.startsWith('direct_shadow')) {
   box.id='usercentrics-root'; box.className='';
   box.getBoundingClientRect=()=>({width:0,height:0,left:0,right:0,top:0,bottom:0});
   box.querySelectorAll=()=>[];
   const shadow={host:box,querySelectorAll(q){return button.isConnected && (q==='*'||q.startsWith('button,'))?[button]:[];},elementFromPoint(){return button.isConnected?button:box}};
   box.shadowRoot=shadow;button.parentElement=null;button.getRootNode=()=>shadow;
   button.click=()=>{button.hits++;if(mode!=='direct_shadow_noop')button.isConnected=false;};
 }
 const blocker=el('DIV','other');
 global.getComputedStyle=e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1',pointerEvents:'auto'});
 global.document={title:mode==='challenge'?'Verify you are human':'fixture',readyState:'complete',body:{innerText:''},querySelectorAll(q){if(q==='*')return [box,button];if(q==='iframe, [id], [class], [role=dialog]')return [box,button];return known?[box]:[];},elementFromPoint(){return mode==='covered'?blocker:button}};
 const safety=eval(safetySrc);
 const report=eval('(() => {const safety = '+safetySrc+'; return ('+consentSrc+');})()');
 return {report,button,box};
}
let r=fixture('Accept cookies');assert.equal(r.button.hits,1);assert.equal(r.report.verified,1);
r=fixture('Accept cookies','zero_host');assert.equal(r.button.hits,1);assert.equal(r.report.verified,1);
r=fixture('Accept cookies','direct_shadow');assert.equal(r.button.hits,1);assert.equal(r.report.verified,1);
r=fixture('Accept cookies','direct_shadow_noop');assert.equal(r.button.hits,1);assert.equal(r.report.verified,0);assert.equal(r.report.unchanged,true);
r=fixture('Accept cookies','noop');assert.equal(r.button.hits,1);assert.equal(r.report.verified,0);assert.equal(r.report.status,'clicked');assert.equal(r.box.isConnected,true);
for(const mode of ['disabled','hidden','inert','offscreen','covered','challenge']){r=fixture('Accept cookies',mode);assert.equal(r.button.hits,0,mode);assert.equal(r.box.isConnected,true,mode);}
for(const label of ['OK','确认','Verify','Accept payment']){r=fixture(label);assert.equal(r.button.hits,0,label);}
r=fixture('Accept cookies','normal',false);assert.equal(r.button.hits,0);
console.log('fake DOM policy branches passed');
'''
        result = subprocess.run([shutil.which('node'), '-e', js, str(Path(f.__file__).with_name('click_safety.js')), str(Path(f.__file__).with_name('consent_logic.js'))], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
