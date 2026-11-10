# -*- coding: utf-8 -*-
"""Mock-only runtime regression tests. Never launch/kill a real browser."""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

import browser_runtime as runtime
import cdp_pool as pool

PROBE = {"endpoint_version": {"Browser": "Chrome/150"},
         "browser_version": {"product": "Chrome/150"}, "dom_usable": True, "tabs": 1}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wd = pathlib.Path(self.temp.name).resolve()
        self.chrome = self.exe("stable/chrome.exe")
        self.custom = self.exe("build/chrome.exe")
        self.edge = self.exe("edge/msedge.exe")
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        # Any accidental unmocked process mutation in a test is an immediate failure.
        self.spawn = patch.object(subprocess, "Popen", side_effect=AssertionError("real spawn forbidden"))
        self.spawn.start(); self.addCleanup(self.spawn.stop)
        self.run = patch.object(subprocess, "run", side_effect=AssertionError("real subprocess forbidden"))
        self.run.start(); self.addCleanup(self.run.stop)
        self.kill = patch.object(os, "kill", side_effect=AssertionError("real kill forbidden"))
        self.kill.start(); self.addCleanup(self.kill.stop)
        self.log = Mock()

    def exe(self, name):
        p = self.wd / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("mock"); p.chmod(0o755)
        return str(p.resolve())

    def entry(self, port=9222):
        profile = pool.profile_for(self.wd, port, browser=self.chrome)
        return {"owned": True, "workdir": str(self.wd), "pid": 123,
                "binary": self.chrome, "profile": str(profile), "level": 2,
                "extra_args": ["--lang=en-US"], "created": "stamp"}

    def info(self, entry, port=9222):
        return {"pid": 123, "binary": entry["binary"], "created": "stamp",
                "args": [entry["binary"], f"--remote-debugging-port={port}",
                         f"--user-data-dir={entry['profile']}"]}

    def test_selection_precedence_and_runtime_env(self):
        with patch.object(runtime, "browser_candidates", return_value=[self.edge]):
            self.assertEqual(runtime.find_browser(), self.edge)
            os.environ["EDGE_PATH"] = self.edge
            os.environ["CHROME_PATH"] = self.chrome
            self.assertEqual(runtime.find_browser(), self.chrome)
            os.environ["PAPER_BROWSER"] = self.custom
            self.assertEqual(runtime.find_browser(), self.custom)
            self.assertEqual(runtime.find_browser(self.edge), self.edge)
            os.environ["PAPER_BROWSER"] = self.chrome
            self.assertEqual(runtime.find_edge(), self.chrome)

    def test_invalid_config_does_not_fallback(self):
        os.environ["PAPER_BROWSER"] = self.chrome
        for value in ("", str(self.wd), str(self.wd / "absent.exe")):
            with self.assertRaises(FileNotFoundError): runtime.find_browser(value)
        os.environ["PAPER_BROWSER"] = str(self.wd / "absent.exe")
        with self.assertRaises(FileNotFoundError): runtime.find_browser()

    def test_profiles_brand_and_binary_isolation(self):
        legacy = self.wd / ".edge-cdp-profile"; legacy.mkdir()
        a = pool.profile_for(self.wd, 9222, browser=self.chrome)
        b = pool.profile_for(self.wd, 9222, browser=self.custom)
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent, self.wd / pool.PROFILE_HOME)
        self.assertEqual(pool.profile_for(self.wd, 9222, browser=self.edge), legacy)
        with self.assertRaises(ValueError): pool.profile_for(self.wd, 9222, "../bad", self.chrome)

    def test_exact_port_and_process_identity(self):
        text = "TCP 127.0.0.1:92220 0.0.0.0:0 LISTENING 77\nTCP [::1]:9222 [::]:0 LISTENING 123"
        self.assertEqual(runtime.parse_listener_pids(text, 9222), {123})
        entry = self.entry(); info = self.info(entry)
        self.assertTrue(runtime.process_matches(info, entry, 9222))
        self.assertFalse(runtime.process_matches(self.info(entry, 92220), entry, 9222))
        for key, value in (("pid", 999), ("binary", self.custom), ("created", "different")):
            modified = dict(info); modified[key] = value
            self.assertFalse(runtime.process_matches(modified, entry, 9222))
        info["args"].append("--type=renderer")
        self.assertFalse(runtime.process_matches(info, entry, 9222))

    def test_probe_requires_browser_version_and_real_dom(self):
        version = {"Browser": "Chrome/150", "webSocketDebuggerUrl": "ws://browser"}
        tabs = [{"type": "page", "webSocketDebuggerUrl": "ws://page"}]
        def response(data):
            value = Mock(); value.__enter__ = Mock(return_value=io.StringIO(json.dumps(data)))
            value.__exit__ = Mock(return_value=False); return value
        with patch.object(runtime.urllib.request, "urlopen", side_effect=[response(version), response(tabs)]), patch.object(runtime, "_ws_call", side_effect=[{"product": "Chrome/150"}, {"root": {"nodeId": 1}}]) as call:
            self.assertTrue(runtime.probe_endpoint(9222)["dom_usable"])
            self.assertEqual([c.args[1] for c in call.call_args_list], ["Browser.getVersion", "DOM.getDocument"])
        with patch.object(runtime.urllib.request, "urlopen", side_effect=[response(version), response(tabs)]), patch.object(runtime, "_ws_call", side_effect=[{}, RuntimeError("renderer timeout")]):
            with self.assertRaises(RuntimeError): runtime.probe_endpoint(9222)

    def test_nonowned_kill_and_down_refused(self):
        with self.assertRaises(PermissionError): pool.kill_port(9222, wd=self.wd)
        with patch.object(pool, "_open", return_value=True):
            with self.assertRaises(PermissionError): pool.down(self.wd, 9222, 1)

    def test_owned_mismatch_refused(self):
        entry = self.entry(); pool._record(self.wd, 9222, entry)
        with patch.object(pool, "process_info", return_value=self.info(entry, 92220)):
            with self.assertRaises(PermissionError): pool.kill_port(9222, wd=self.wd)
        with patch.object(pool, "process_info", return_value=self.info(entry)), patch.object(pool, "_open", return_value=True), patch.object(pool, "listener_pids", return_value={77}):
            with self.assertRaises(PermissionError): pool.kill_port(9222, wd=self.wd)

    def test_existing_endpoint_attach_does_not_register_or_fresh(self):
        with patch.object(pool, "_open", return_value=True), patch.object(pool, "probe_endpoint", return_value=PROBE):
            self.assertTrue(pool.launch_one(self.wd, 9222, fresh=True, log=self.log))
            self.assertFalse((self.wd / pool.REGISTRY).exists())
            with self.assertRaises(PermissionError): pool.launch_one(self.wd, 9222, browser=self.chrome, log=self.log)
        with patch.object(pool, "_open", return_value=True), patch.object(pool, "probe_endpoint", side_effect=RuntimeError("DOM unavailable")):
            with self.assertRaises(RuntimeError): pool.launch_one(self.wd, 9222, log=self.log)

    def test_runtime_args_and_no_sandbox_default(self):
        self.assertNotIn("--no-sandbox", pool.build_args(9222, self.wd, 0))
        os.environ["PAPER_CDP_LEVEL"] = "3"
        os.environ["PAPER_CDP_EXTRA_ARGS"] = '--lang=fr --test="two words"'
        self.assertEqual(pool.default_level(), 3)
        self.assertIn("--test=two words", pool.build_args(9222, self.wd, 3))
        os.environ["PAPER_CDP_EXTRA_ARGS"] = "--remote-debugging-port=92220"
        with self.assertRaises(ValueError): pool.build_args(9222, self.wd, 0)

    def test_fresh_user_profile_refused(self):
        with patch.object(pool, "_open", return_value=False):
            with self.assertRaises(PermissionError):
                pool.launch_one(self.wd, 9222, profile=self.wd / "user", fresh=True, browser=self.chrome, log=self.log)

    def test_renew_preserves_binary_level_args_and_profiles(self):
        entry = self.entry(); pathlib.Path(entry["profile"]).mkdir(parents=True)
        pool._record(self.wd, 9222, entry)
        os.environ["PAPER_BROWSER"] = self.custom
        os.environ["PAPER_CDP_EXTRA_ARGS"] = "--lang=fr"
        with patch.object(pool, "process_info", return_value=self.info(entry)), patch.object(pool, "_open", return_value=False), patch.object(pool, "kill_port", return_value=1), patch.object(pool, "launch_one", return_value=True) as launch:
            self.assertTrue(pool.renew(self.wd, 9222, log=self.log))
            self.assertEqual(launch.call_args.kwargs["browser"], self.chrome)
            self.assertEqual(launch.call_args.kwargs["level"], 2)
            self.assertEqual(launch.call_args.kwargs["extra_args"], ["--lang=en-US"])
            self.assertTrue(pathlib.Path(entry["profile"]).exists())

    def test_launch_records_real_identity_and_endpoint(self):
        profile = pool.profile_for(self.wd, 19331, browser=self.custom)
        entry = {"binary": self.custom, "profile": str(profile)}
        info = self.info(entry, 19331)
        proc = Mock(pid=123); proc.poll.return_value = None
        with patch.object(pool, "_open", side_effect=[False, True]), patch.object(subprocess, "Popen", return_value=proc) as spawn, patch.object(pool, "process_info", return_value=info), patch.object(pool, "listener_pids", return_value={123}), patch.object(pool, "probe_endpoint", return_value=PROBE):
            self.assertTrue(pool.launch_one(self.wd, 19331, browser=self.custom, level=1, log=self.log, extra_args=["--lang=en"]))
        saved = pool._load_registry(self.wd)["19331"]
        self.assertEqual(saved["binary"], self.custom)
        self.assertEqual(saved["pid"], 123)
        self.assertTrue(saved["owned"] and saved["dom_usable"])
        self.assertEqual(saved["endpoint_version"], PROBE["endpoint_version"])
        self.assertEqual(spawn.call_args.args[0][0], self.custom)
        self.assertNotIn("--no-sandbox", spawn.call_args.args[0])

    def test_dead_owned_restart_inherits_config_despite_env_change(self):
        entry = self.entry(); pool._record(self.wd, 9222, entry)
        os.environ['PAPER_BROWSER'] = self.custom
        os.environ['PAPER_CDP_LEVEL'] = '3'
        os.environ['PAPER_CDP_EXTRA_ARGS'] = '--lang=fr'
        proc = Mock(pid=123); proc.poll.return_value = None
        with patch.object(pool, '_open', side_effect=[False, True]), patch.object(subprocess, 'Popen', return_value=proc) as spawn, patch.object(pool, 'process_info', side_effect=[None, self.info(entry), self.info(entry)]), patch.object(pool, 'listener_pids', return_value={123}), patch.object(pool, 'probe_endpoint', return_value=PROBE):
            self.assertTrue(pool.launch_one(self.wd, 9222, log=self.log))
        saved = pool._load_registry(self.wd)['9222']
        for key in ('binary', 'profile', 'level', 'extra_args'):
            self.assertEqual(saved[key], entry[key])
        self.assertEqual(spawn.call_args.args[0][0], self.chrome)

    def test_dead_owned_restart_explicit_overrides(self):
        entry = self.entry(); pool._record(self.wd, 9222, entry)
        profile = pool.profile_for(self.wd, 9222, browser=self.custom)
        current = dict(entry, binary=self.custom, profile=str(profile))
        proc = Mock(pid=123); proc.poll.return_value = None
        with patch.object(pool, '_open', side_effect=[False, True]), patch.object(subprocess, 'Popen', return_value=proc), patch.object(pool, 'process_info', side_effect=[None, self.info(current), self.info(current)]), patch.object(pool, 'listener_pids', return_value={123}), patch.object(pool, 'probe_endpoint', return_value=PROBE):
            self.assertTrue(pool.launch_one(self.wd, 9222, browser=self.custom, level=1, extra_args=[], log=self.log))
        saved = pool._load_registry(self.wd)['9222']
        self.assertEqual(saved['binary'], self.custom)
        self.assertEqual(saved['profile'], str(profile))
        self.assertEqual(saved['level'], 1)
        self.assertEqual(saved['extra_args'], [])

    def test_optional_launcher_delegates_and_probes_without_brand_processes(self):
        import importlib.util
        path = pathlib.Path(__file__).resolve().parent.parent / 'optional-cdp-fulltext/cdp_up_nosandbox.py'
        spec = importlib.util.spec_from_file_location('runtime_optional_launcher_test', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(pool, 'launch_one', return_value=True) as launch:
            self.assertTrue(module.launch(self.wd, 9222, None, browser=self.chrome, extra_args=['--lang=en']))
            self.assertEqual(launch.call_args.kwargs['extra_args'], ['--lang=en', '--no-sandbox'])
            self.assertIsNone(launch.call_args.kwargs['level'])
        with patch.object(module, 'probe_endpoint', return_value=PROBE):
            self.assertEqual(module.renderer_count(9222), 1)
        with patch.object(module, 'probe_endpoint', side_effect=RuntimeError('DOM timeout')):
            self.assertEqual(module.renderer_count(9222, attempts=1), -1)

    def test_downloader_helpers_probe_and_warn_on_unowned_cleanup(self):
        # Execute only the two pure helpers: importing the downloader creates workdir state.
        import ast
        path = pathlib.Path(__file__).resolve().parent.parent / 'optional-cdp-fulltext/download_all_fulltexts.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('_dom_ready', '_cleanup_owned')]
        namespace = {'cdp_pool': pool, 'WD': self.wd}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), str(path), 'exec'), namespace)
        with patch.object(pool, 'probe_endpoint', return_value=PROBE):
            self.assertTrue(namespace['_dom_ready'](9222, self.log))
        with patch.object(pool, 'probe_endpoint', side_effect=RuntimeError('DOM timeout')):
            self.assertFalse(namespace['_dom_ready'](9222, self.log))
        with patch.object(pool, 'down', side_effect=PermissionError('unowned')):
            self.assertEqual(namespace['_cleanup_owned'](9222, 8, self.log), 0)
            self.log.warn.assert_called()

    def test_running_registered_root_without_listener_cannot_be_overwritten(self):
        entry = self.entry(); pool._record(self.wd, 9222, entry)
        before = (self.wd / pool.REGISTRY).read_text()
        with patch.object(pool, "_open", return_value=False), patch.object(pool, "process_info", return_value=self.info(entry)):
            with self.assertRaises(PermissionError):
                pool.launch_one(self.wd, 9222, browser=self.chrome, log=self.log)
        self.assertEqual((self.wd / pool.REGISTRY).read_text(), before)

    def test_windows_command_line_roundtrip(self):
        argv = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", "--remote-debugging-port=9222", r"--user-data-dir=C:\a b\profile"]
        with patch.object(runtime.os, "name", "nt"):
            self.assertEqual(runtime.split_command_line(subprocess.list2cmdline(argv)), argv)

    def test_cli_browser_forms_failure_help_and_no_default_launch(self):
        with patch.object(pool, "ensure", return_value=[9222]) as ensure:
            for argv in (["up", "--browser", self.chrome], ["up", "--browser=" + self.chrome]):
                self.assertEqual(pool.main(argv), 0)
                self.assertEqual(ensure.call_args.kwargs["browser"], self.chrome)
        with patch.object(pool, "ensure", return_value=[]): self.assertEqual(pool.main(["up"]), 1)
        for argv, status in ((["--help"], 0), ([], 2)):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc: pool.main(argv)
                self.assertEqual(exc.exception.code, status)

    def test_clean_apply_preserves_cookie(self):
        legacy = self.wd / ".edge-cdp-profile"; legacy.mkdir()
        (legacy / "Cookies").write_text("keep")
        with patch.object(pool, "_open", return_value=False):
            self.assertEqual(pool.clean(self.wd)["moved"], 0)
            self.assertEqual(pool.clean(self.wd, apply=True)["moved"], 1)
        self.assertEqual((self.wd / pool.PROFILE_HOME / legacy.name / "Cookies").read_text(), "keep")
        with self.assertRaises(PermissionError): pool.clean(self.wd, apply=True, purge=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
