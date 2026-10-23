#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the numbers in README.md.

Spawns the shim over stdio, measures the real `tools/list` and `initialize`
frames it puts on the wire, compares them with the upstream server's catalogue,
and runs a batch of Chinese/English searches to show the top hit for each.

    python verify_shim.py --python <venv python> --server <path to server.py>

Token counts use tiktoken `cl100k_base` when it is installed; otherwise only
character counts are reported.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUERIES = [
    ("", 1), ("拉伸凸台", 2), ("画个圆柱", 2), ("圆角", 2), ("倒角", 2), ("抽壳", 2),
    ("螺纹孔", 2), ("孔", 2), ("工程图", 3), ("剖视图", 2), ("材料明细表", 2),
    ("导出STEP", 2), ("导出pdf", 2), ("导入STEP", 2), ("干涉检查", 2), ("检查干涉", 2),
    ("装配配合", 3), ("新建装配体", 2), ("打开零件", 2), ("保存", 2), ("质量属性", 2),
    ("自定义属性", 2), ("尺寸", 2), ("配置", 2), ("运动算例", 2), ("添加马达", 2),
    ("有限元分析", 2), ("dfm 检查", 2), ("routing", 2), ("预览截图", 2), ("批量导出", 2),
    ("设计规格", 2), ("health check", 2), ("fillet", 2), ("曲面", 2), ("阵列", 2),
]


def counter():
    try:
        import tiktoken
    except ImportError:
        return lambda text: None
    enc = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(enc.encode(text))


class Client:
    def __init__(self, cmd):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.msgs, self.raws = {}, {}
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("id") is not None:
                with self.lock:
                    self.msgs[obj["id"]] = obj
                    self.raws[obj["id"]] = line.strip()

    def _drain(self):
        for line in self.proc.stderr:
            if "--verbose" in sys.argv:
                sys.stderr.write("[upstream] " + line)

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def wait(self, msg_id, timeout=240):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if msg_id in self.msgs:
                    return self.msgs[msg_id], self.raws[msg_id]
            time.sleep(0.05)
        raise SystemExit("timeout waiting for id=%s" % msg_id)

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="Python that runs the upstream server")
    ap.add_argument("--server", required=True, help="Path to the upstream server.py")
    ap.add_argument("--shim", default=str(HERE / "sw_discovery_shim.py"))
    ap.add_argument("--catalog", default=str(HERE / "sw_catalog.json"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    tok = counter()
    catalog_path = Path(args.catalog)
    if not catalog_path.is_file():
        raise SystemExit("catalogue missing: %s (run --build-catalog first)" % catalog_path)
    upstream_tools = json.loads(catalog_path.read_text(encoding="utf-8"))["tools"]

    cli = Client([args.python, args.shim, "--python", args.python, "--server", args.server])
    try:
        cli.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "verify", "version": "0"}}})
        init, init_raw = cli.wait(1)
        cli.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        cli.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools, tools_raw = cli.wait(2)

        upstream_raw = json.dumps(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": upstream_tools}},
            ensure_ascii=False, separators=(",", ":"),
        )
        shim_tokens, up_tokens = tok(tools_raw), tok(upstream_raw)

        print("serverInfo : %s" % (init["result"]["serverInfo"],))
        print("tools      : %s" % ([t["name"] for t in tools["result"]["tools"]],))
        print("")
        print("%-10s %8s %10s" % ("", "chars", "tokens"))
        print("%-10s %8d %10s" % ("shim", len(tools_raw), shim_tokens or "-"))
        print("%-10s %8d %10s" % ("upstream", len(upstream_raw), up_tokens or "-"))
        if shim_tokens:
            print("saving   : %d tokens (%.1f%%)" % (
                up_tokens - shim_tokens, 100.0 * (up_tokens - shim_tokens) / up_tokens))
        print("initialize frame: %d chars, %s tokens" % (len(init_raw), tok(init_raw) or "-"))

        print("\nsearches (%d queries):" % len(QUERIES))
        bad = []
        for i, (query, top_k) in enumerate(QUERIES):
            cli.send({"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call", "params": {
                "name": "search_tools", "arguments": {"query": query, "top_k": top_k}}})
            resp, _ = cli.wait(100 + i)
            text = resp["result"]["content"][0]["text"]
            names = [ln.split(". ", 1)[1] for ln in text.split("\n")
                     if re.match(r"^\d+\. \S+$", ln)]
            if query and not names:
                bad.append(query)
            print("  %-12s %-4s %s" % (
                query or "<empty>", "%d tok" % (tok(text) or len(text) // 4), ", ".join(names) or "(none)"))

        print("\nmisses: %s" % (bad or "none"))
    finally:
        cli.close()


if __name__ == "__main__":
    main()
