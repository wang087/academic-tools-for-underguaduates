#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
run_with_llm —— 把「启动 → 跑任务 → 精准释放」包成一个上下文
================================================================
用途：让任何实验脚本（本仓库的 task_keywords.py / extract_keywords_zjy.py …
以及将来换掉的 harness）自动获得"用完即释放"的能力，
**不需要改动那些脚本本身**。

两种用法
--------
① 作为 Python 上下文管理器（推荐，进程崩了也能释放）：

    from run_with_llm import llm_session
    with llm_session(model="Qwen3-0.6B", port=8083, ctx=32768) as s:
        print(s["base_url"], s["api_key"])
        run_my_experiment(base_url=s["base_url"], api_key=s["api_key"])
    # 退出 with 即释放本次服务 + 清临时文件

② 作为命令行包装器（不改任何现有脚本）：

    python run_with_llm.py --model Qwen3-0.6B --port 8083 -- \
        python ..\zjy_kwcloud\extract_keywords_zjy.py

设计要点
--------
· 全程复用 llm_lifecycle 的 **pin 登记 + 四重校验**，因此释放永远只针对本次进程
· 异常/中断（含 KeyboardInterrupt）也会走 finally 释放
· `--keep-on-failure` 可在失败时保留服务供排查（默认不保留，符合"用完即释放"）
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import llm_lifecycle as LL        # noqa: E402


def _serve_kwargs(**kw):
    """把 llm_session 的参数整理成 cmd_serve 需要的 argparse 命名空间。"""
    class NS:
        pass
    a = NS()
    a.workspace = kw.get("workspace")
    a.llama_server = kw.get("llama_server")
    a.model = kw.get("model")
    a.model_path = kw.get("model_path")
    a.port = kw.get("port")
    a.ctx = kw.get("ctx", 32768)
    a.ngl = kw.get("ngl", 99)
    a.slots = kw.get("slots", 1)
    a.alias = kw.get("alias")
    a.api_key = kw.get("api_key")
    a.jinja = kw.get("jinja", True)
    a.extra = kw.get("extra")
    a.wait = kw.get("wait", 120)
    a.tag = kw.get("tag")
    return a


@contextmanager
def llm_session(auto_release: bool = True, keep_on_failure: bool = False, **kw):
    """启动服务 → yield 会话信息 → 释放（只释放本次）。"""
    LL.ensure_deps()
    a = _serve_kwargs(**kw)
    rc = LL.cmd_serve(a)
    if rc != 0:
        raise RuntimeError(f"服务启动失败（exit {rc}）")

    ws = Path(a.workspace).resolve() if a.workspace else LL.default_workspace()
    sd = LL.state_dir(ws)
    # 必须按 **本次的 tag** 找回 pin，不能用 pins[-1]
    #   —— 目录里可能残留其它（含手工伪造/已失效）的 pin，
    #      实测 pins[-1] 会取错并 KeyError。
    want = a.tag
    pin_file = sd / "pins" / f"{want}.json"
    if not pin_file.exists():
        latest = sd / "latest"
        if latest.exists():
            pin_file = Path(latest.read_text(encoding="utf-8").strip())
    if not pin_file.exists():
        raise RuntimeError(f"启动成功但找不到 pin 记录（tag={want}）")
    rec = json.loads(pin_file.read_text(encoding="utf-8"))
    if rec.get("pid") is None:
        raise RuntimeError(f"pin 内容异常：{pin_file}")

    info = {"base_url": f"http://127.0.0.1:{rec['port']}/v1",
            "api_url": f"http://127.0.0.1:{rec['port']}/v1/chat/completions",
            "props_url": f"http://127.0.0.1:{rec['port']}/props",
            "port": rec["port"], "api_key": rec.get("api_key", ""),
            "pid": rec["pid"], "model": rec.get("model", ""),
            "alias": rec.get("alias") or "local",
            "pin": str(pin_file), "log": rec.get("err_log"),
            "env": {"OPENAI_BASE_URL": f"http://127.0.0.1:{rec['port']}/v1",
                    "OPENAI_API_KEY": rec.get("api_key", ""),
                    "LLM_BASE_URL": f"http://127.0.0.1:{rec['port']}/v1",
                    "LLM_API_KEY": rec.get("api_key", ""),
                    "LLM_MODEL": rec.get("alias") or "local"}}

    released = {"done": False, "failed": False}

    def _release():
        if released["done"] or not auto_release:
            return
        if released["failed"] and keep_on_failure:
            print(f"[i] --keep-on-failure：保留服务 pid={rec['pid']} "
                  f"端口 {rec['port']}（pin={pin_file}）")
            released["done"] = True
            return
        print(f"\n[i] 释放本次服务（pid={rec['pid']}，端口 {rec['port']}）…")
        class R:
            pass
        r = R()
        r.workspace = a.workspace
        r.pin = str(pin_file)
        r.all = False
        r.latest = False
        r.grace = kw.get("grace", 8.0)
        r.keep_logs = kw.get("keep_logs", False)
        r.prune = True
        r.clean_pycache = kw.get("clean_pycache", False)
        r.unload_url = kw.get("unload_url")
        LL.cmd_release(r)
        released["done"] = True

    atexit.register(_release)
    try:
        os.environ.update(info["env"])
        yield info
    except BaseException:
        released["failed"] = True
        raise
    finally:
        _release()


# ══════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="启动 llama 服务 → 跑命令 → 用完精准释放（不改动被包装的脚本）")
    ap.add_argument("--model", default=None)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--ctx", type=int, default=32768)
    ap.add_argument("--ngl", type=int, default=99)
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--llama-server", default=None)
    ap.add_argument("--keep-on-failure", action="store_true",
                    help="被包装命令失败时保留服务供排查")
    ap.add_argument("--keep-logs", action="store_true")
    ap.add_argument("--clean-pycache", action="store_true")
    ap.add_argument("--no-release", action="store_true",
                    help="只启动不释放（自己手动 release）")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="要执行的命令（放在 -- 之后）")
    a = ap.parse_args()

    cmd = a.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[X] 没给要执行的命令。示例：\n"
              "    python run_with_llm.py --model Qwen3-0.6B -- "
              "python my_experiment.py")
        return 2

    with llm_session(model=a.model, model_path=a.model_path, port=a.port,
                     ctx=a.ctx, ngl=a.ngl, workspace=a.workspace,
                     llama_server=a.llama_server,
                     keep_on_failure=a.keep_on_failure,
                     keep_logs=a.keep_logs, clean_pycache=a.clean_pycache,
                     auto_release=not a.no_release) as s:
        print(f"[i] 服务就绪 {s['api_url']}  pid={s['pid']}")
        print(f"[i] 已注入环境变量 OPENAI_BASE_URL / LLM_BASE_URL / LLM_API_KEY")
        print(f"[i] 执行：{' '.join(cmd)}\n" + "-" * 66)
        env = dict(os.environ)
        r = subprocess.run(cmd, env=env)
        print("-" * 66 + f"\n[i] 子命令退出码 {r.returncode}")
        if r.returncode != 0 and a.keep_on_failure:
            raise SystemExit(r.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
