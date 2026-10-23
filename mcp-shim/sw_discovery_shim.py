#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SolidWorks MCP discovery shim.

The upstream `solidworks-automation-skill` MCP server registers all 66 of its
tools at startup. Every MCP client therefore pays for the whole catalogue in its
context window before the user has typed anything -- about 23.4k tokens measured
with tiktoken ``cl100k_base``.

This shim sits between the client and that server and advertises exactly two
tools instead:

    search_tools(query, top_k)      -> ranked tool names + one-line descriptions
    call_tool(name, arguments)      -> forwarded verbatim to the upstream server

The catalogue itself is cached on disk, so the upstream process is only spawned
when a tool is actually invoked. Idle cost drops from ~23.4k tokens to ~0.4k.

Design notes
------------
* stdlib only -- no dependency beyond the Python the upstream server already uses
* newline-delimited JSON-RPC 2.0 over stdio, which is what MCP stdio transport is
* upstream stdout is parsed defensively; non-JSON lines are ignored, because the
  upstream server redirects ``sys.stdout`` to stderr only when run as __main__
* resources/list, resources/read and prompts/list are forwarded transparently so
  artifact delivery keeps working
* nothing about the upstream repository is modified
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

SHIM_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG = SHIM_DIR / "sw_catalog.json"
DEFAULT_LEXICON = SHIM_DIR / "sw_lexicon.json"
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "solidworks-discovery"
SERVER_VERSION = "1.0.0"

MAX_DESC_CHARS = 160


def log(msg: str) -> None:
    if os.environ.get("SW_SHIM_DEBUG"):
        sys.stderr.write("[sw-shim] " + msg + "\n")
        sys.stderr.flush()


def ascii_out(obj) -> str:
    """Serialise one JSON-RPC frame. ensure_ascii keeps the wire pure ASCII."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def write_frame(obj) -> None:
    sys.stdout.write(ascii_out(obj) + "\n")
    sys.stdout.flush()


def read_frame(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# catalogue + search index
# --------------------------------------------------------------------------- #

CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
SPLIT_RE = re.compile(r"[^0-9a-zA-Z" + CJK + r"]+")
WS_RE = re.compile(r"\s+")


def norm(text: str) -> str:
    return WS_RE.sub(" ", (text or "")).strip().lower()


def tokenize(text: str):
    return [t for t in SPLIT_RE.split(norm(text)) if t]


def first_sentence(desc: str, limit: int = MAX_DESC_CHARS) -> str:
    desc = norm(desc)
    if not desc:
        return ""
    cut = len(desc)
    for sep in (". ", "\u3002", ";"):
        idx = desc.find(sep)
        if idx != -1:
            cut = min(cut, idx + 1)
    out = desc[: min(cut, limit)].strip()
    return out


class Lexicon:
    """Bilingual alias groups used to expand a query before scoring."""

    def __init__(self, path: Path):
        self.groups = []
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.groups = [[norm(a) for a in g if norm(a)] for g in data.get("groups", [])]
            except Exception as exc:  # pragma: no cover - defensive
                log("lexicon load failed: %r" % (exc,))
        self.groups = [g for g in self.groups if g]
        self.alias_owner = {}
        for gid, group in enumerate(self.groups):
            for alias in group:
                self.alias_owner.setdefault(alias, gid)

    def expand_weighted(self, terms):
        """alias -> (group id, weight).

        A longer, more specific alias beats a generic one: '干涉检查' hits the
        interference group with its full 4-char alias (weight 1.0) and the generic
        check group with 2 chars (weight 0.5), so interference tools outrank
        everything that merely 'checks' something.

        Group ids matter because scores are summed per group, not per alias --
        otherwise a tool whose description happens to list many export formats
        ('step, stl, iges, parasolid, pdf') would pile up points from synonyms of
        one single concept.
        """
        result = {}
        for term in terms:
            gid = self.alias_owner.get(term, ("t", term))
            prev = result.get(term)
            if prev is None or prev[1] < 1.0:
                result[term] = (gid, 1.0)
            for group_id, group in enumerate(self.groups):
                best = 0.0
                for alias in group:
                    if alias == term:
                        best = max(best, 1.0)
                    elif len(term) >= 2 and term in alias:
                        # the alias is more specific than the query: weak evidence
                        best = max(best, len(term) / float(len(alias)))
                    elif len(alias) >= 2 and alias in term:
                        # the query is longer than the alias: how much it covers
                        best = max(best, len(alias) / float(len(term)))
                if best > 0:
                    weight = min(1.0, best)
                    for alias in group:
                        prev = result.get(alias)
                        if prev is None or prev[1] < weight:
                            result[alias] = (group_id, weight)
        return result


_TERM_CACHE = {}


def find_term(hay: str, term: str) -> int:
    """Position of term in hay, or -1. Latin terms need a word boundary so that
    'view' does not match inside 'preview'."""
    if not hay or not term:
        return -1
    if term.isascii():
        pattern = _TERM_CACHE.get(term)
        if pattern is None:
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
            _TERM_CACHE[term] = pattern
        match = pattern.search(hay)
        return match.start() if match else -1
    return hay.find(term)


def extract_args(schema):
    """Flatten the tool's arguments into readable names, resolving $ref params objects."""
    props = (schema or {}).get("properties") or {}
    defs = (schema or {}).get("$defs") or {}
    required = set((schema or {}).get("required") or [])
    names, must = [], []
    for key, value in props.items():
        ref = value.get("$ref") if isinstance(value, dict) else None
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            sub = defs.get(ref.rsplit("/", 1)[-1]) or {}
            sub_props = sub.get("properties") or {}
            sub_req = set(sub.get("required") or [])
            for sub_key in sub_props:
                names.append(sub_key)
                if sub_key in sub_req:
                    must.append(sub_key)
        else:
            names.append(key)
            if key in required:
                must.append(key)
    return names, must


class Catalogue:
    def __init__(self, tools, lexicon: Lexicon):
        self.tools = []
        self.by_name = {}
        self.lexicon = lexicon
        for t in tools:
            name = t.get("name") or ""
            if not name:
                continue
            schema = t.get("inputSchema") or {}
            arg_names, arg_required = extract_args(schema)
            entry = {
                "name": name,
                "desc": t.get("description") or "",
                "short": first_sentence(t.get("description") or ""),
                "args": arg_names,
                "required": arg_required,
                "hay_name": norm(name.replace("_", " ")),
                "hay_desc": norm(t.get("description") or ""),
                "hay_args": norm(" ".join(arg_names)),
            }
            self.tools.append(entry)
            self.by_name[name] = entry

    def __len__(self):
        return len(self.tools)

    def search(self, query: str, top_k: int = 8):
        q = norm(query)
        if not q:
            return [(t, 0.0) for t in self.tools[:top_k]]

        terms = tokenize(q)
        weights = self.lexicon.expand_weighted(terms)
        q_compact = q.replace(" ", "")

        scored = []
        for t in self.tools:
            name_lower = t["name"].lower()
            name_compact = name_lower.replace("_", "")
            base = 0.0
            if q_compact and q_compact == name_compact:
                base += 400
            elif len(q_compact) >= 3 and q_compact in name_compact:
                base += 90

            per_group = {}
            for term, (gid, weight) in weights.items():
                hit = 0.0
                if term == name_lower:
                    hit = 260.0
                elif find_term(t["hay_name"], term) != -1:
                    hit = 70.0 if len(term) > 3 else 40.0
                pos = find_term(t["hay_desc"], term)
                if pos != -1 and len(term) >= 2:
                    hit = max(hit, 42.0 / (1.0 + pos / 50.0))
                if len(term) >= 3 and find_term(t["hay_args"], term) != -1:
                    hit = max(hit, 6.0)
                if hit:
                    per_group[gid] = max(per_group.get(gid, 0.0), hit * weight)

            score = base + sum(per_group.values())
            if score > 0:
                scored.append((t, score))

        scored.sort(key=lambda pair: (-pair[1], len(pair[0]["name"]), pair[0]["name"]))
        if not scored:
            return []
        cutoff = scored[0][1] * 0.50
        kept = [pair for pair in scored if pair[1] >= cutoff]
        return kept[: max(1, top_k)]


def load_catalogue(tools, lexicon) -> Catalogue:
    return Catalogue(tools, lexicon)


# --------------------------------------------------------------------------- #
# upstream child process
# --------------------------------------------------------------------------- #

class Upstream:
    """Minimal MCP client speaking newline-delimited JSON-RPC to a child process."""

    def __init__(self, python: str, server: str, env=None):
        self.python = python
        self.server = server
        self.env = env
        self.proc = None
        self._next_id = 1000
        self._pending = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._reader = None
        self._drain = None
        self.dead_reason = None

    # -- lifecycle ---------------------------------------------------------- #

    def start(self, timeout=120.0):
        if self.proc is not None:
            return
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [self.python, self.server],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self._drain.start()

        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
            timeout=timeout,
        )
        self.notify("notifications/initialized")
        log("upstream ready: pid=%s" % (self.proc.pid,))

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    # -- io ----------------------------------------------------------------- #

    def _drain_stderr(self):
        proc = self.proc
        if proc is None:
            return
        for line in proc.stderr:
            if os.environ.get("SW_SHIM_DEBUG"):
                sys.stderr.write("[sw-upstream] " + line)
                sys.stderr.flush()

    def _read_loop(self):
        proc = self.proc
        if proc is None:
            return
        for line in proc.stdout:
            msg = read_frame(line)
            if msg is None:
                continue
            msg_id = msg.get("id")
            if msg_id is not None and ("result" in msg or "error" in msg):
                with self._cond:
                    self._pending[msg_id] = msg
                    self._cond.notify_all()
                continue
            if msg_id is not None and msg.get("method"):
                # server -> client request: refuse politely so nobody hangs
                try:
                    write_frame(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {"code": -32601, "message": "unsupported: " + str(msg.get("method"))},
                        }
                    )
                except Exception:
                    pass
                continue
            log("upstream notification: %s" % (msg.get("method"),))
        with self._cond:
            self.dead_reason = "upstream closed stdout (exit=%s)" % (
                None if proc.poll() is None else proc.returncode,
            )
            self._cond.notify_all()

    def notify(self, method, params=None):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("upstream not running")
        frame = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        self.proc.stdin.write(ascii_out(frame) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=600.0):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("upstream not running")
        with self._cond:
            self._next_id += 1
            req_id = self._next_id
        frame = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            frame["params"] = params
        self.proc.stdin.write(ascii_out(frame) + "\n")
        self.proc.stdin.flush()

        deadline = time.time() + timeout
        with self._cond:
            while req_id not in self._pending:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("upstream timeout after %.0fs on %s" % (timeout, method))
                if self.dead_reason:
                    raise RuntimeError(self.dead_reason)
                self._cond.wait(min(remaining, 0.25))
            return self._pending.pop(req_id)


# --------------------------------------------------------------------------- #
# shim server
# --------------------------------------------------------------------------- #

TOOL_SEARCH = {
    "name": "search_tools",
    "description": (
        "Search the SolidWorks tool catalogue by intent, in Chinese or English. "
        "Call this first: it returns the exact tool names, their one-line purpose and "
        "required parameters. Then invoke the chosen tool with call_tool. "
        "Pass an empty query to list every tool name."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you want to do, e.g. 'fillet edges', '拉伸凸台', 'export STEP', '干涉检查'.",
            },
            "top_k": {"type": "integer", "description": "How many matches to return (default 8)."},
        },
        "required": ["query"],
    },
}

TOOL_CALL = {
    "name": "call_tool",
    "description": (
        "Invoke a SolidWorks tool by exact name with its arguments object. "
        "Use search_tools first to discover the name and its required parameters."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact tool name returned by search_tools."},
            "arguments": {"type": "object", "description": "Arguments object for that tool."},
        },
        "required": ["name"],
    },
}

INSTRUCTIONS = (
    "SolidWorks automation over Windows COM. This server advertises only two tools to keep "
    "the context small: call search_tools with the user's intent (Chinese or English) to get "
    "exact tool names, then call call_tool to run one. Tool calls are serialised; SolidWorks "
    "must be running in the same Windows session."
)


class Shim:
    def __init__(self, python, server, catalog_path, lexicon_path, top_k, upstream_timeout, upstream_env):
        self.lexicon = Lexicon(lexicon_path)
        self.catalog_path = catalog_path
        self.top_k = top_k
        self.upstream_timeout = upstream_timeout
        self.upstream = Upstream(python, server, upstream_env)
        self.catalogue = None
        self.catalog_source = None
        self._load_cache()

    # -- catalogue ---------------------------------------------------------- #

    def _load_cache(self):
        if not self.catalog_path.is_file():
            return
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            tools = data.get("tools") if isinstance(data, dict) else data
            if tools:
                self.catalogue = load_catalogue(tools, self.lexicon)
                self.catalog_source = "cache"
                log("catalogue loaded from cache: %d tools" % len(self.catalogue))
        except Exception as exc:
            log("catalog cache unreadable: %r" % (exc,))

    def refresh_from_upstream(self):
        self.upstream.start()
        resp = self.upstream.request("tools/list", {}, timeout=120.0)
        tools = (resp.get("result") or {}).get("tools") or []
        if not tools:
            raise RuntimeError("upstream returned an empty tool list")
        fresh = load_catalogue(tools, self.lexicon)
        changed = self.catalogue is None or len(self.catalogue) != len(fresh)
        self.catalogue = fresh
        self.catalog_source = "live"
        if changed:
            self._write_cache(tools)
        return fresh

    def _write_cache(self, tools):
        try:
            payload = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "tools": tools}
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            self.catalog_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            log("catalogue cache written: %s" % (self.catalog_path,))
        except Exception as exc:
            log("catalogue cache write failed: %r" % (exc,))

    def ensure_catalogue(self):
        if self.catalogue is not None and self.catalog_source == "live":
            return self.catalogue
        try:
            return self.refresh_from_upstream()
        except Exception as exc:
            log("live catalogue unavailable: %r" % (exc,))
            if self.catalogue is not None:
                return self.catalogue
            raise

    # -- tools -------------------------------------------------------------- #

    def do_search(self, args):
        query = args.get("query")
        query = "" if query is None else str(query)
        try:
            top_k = int(args.get("top_k") or self.top_k)
        except Exception:
            top_k = self.top_k
        top_k = max(1, min(top_k, 40))

        try:
            cat = self.ensure_catalogue()
        except Exception as exc:
            return _text_result(
                "Tool catalogue unavailable: %s\nSet SW_UPSTREAM_PYTHON / SW_UPSTREAM_SERVER, "
                "or run this shim once with --build-catalog." % (exc,),
                is_error=True,
            )

        if not norm(query):
            names = sorted(t["name"] for t in cat.tools)
            body = "%d tools. Call search_tools with a narrower query, then call_tool.\n\n" % len(names)
            body += "\n".join(names)
            return _text_result(body)

        hits = cat.search(query, top_k)
        if not hits:
            return _text_result(
                "No match for %r. Try a broader word (e.g. 'drawing', 'hole', '工程图') "
                "or an empty query to list all %d tool names." % (query, len(cat)),
                is_error=False,
            )

        lines = ["%d match(es) for %r. Run one with call_tool(name=..., arguments={...})." % (len(hits), query), ""]
        for idx, (tool, _score) in enumerate(hits, 1):
            lines.append("%d. %s" % (idx, tool["name"]))
            if tool["short"]:
                lines.append("   %s" % tool["short"])
            if tool["required"]:
                lines.append("   required args: %s" % _clip(tool["required"]))
            elif tool["args"]:
                lines.append("   args: %s" % _clip(tool["args"]))
        return _text_result("\n".join(lines))

    def do_call(self, args):
        name = args.get("name")
        if not isinstance(name, str) or not name:
            return _text_result("call_tool requires a non-empty 'name'.", is_error=True)
        arguments = args.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _text_result("'arguments' must be a JSON object.", is_error=True)

        cat = self.catalogue
        if cat is not None and cat.by_name and name not in cat.by_name:
            near = [t["name"] for t, _ in cat.search(name, 3)]
            hint = (" Did you mean: %s?" % ", ".join(near)) if near else ""
            return _text_result(
                "Unknown tool %r. Use search_tools to find the exact name.%s" % (name, hint),
                is_error=True,
            )

        try:
            self.upstream.start()
        except Exception as exc:
            return _text_result("Could not start the SolidWorks MCP server: %s" % (exc,), is_error=True)

        try:
            resp = self.upstream.request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=self.upstream_timeout,
            )
        except Exception as exc:
            return _text_result("Tool %r failed to execute: %s" % (name, exc), is_error=True)

        if "error" in resp:
            err = resp["error"] or {}
            return _text_result(
                "Tool %r returned an error: %s" % (name, err.get("message") or err), is_error=True
            )
        result = resp.get("result") or {}
        return result

    def forward(self, method, params):
        self.upstream.start()
        resp = self.upstream.request(method, params, timeout=self.upstream_timeout)
        if "error" in resp:
            raise RuntimeError((resp["error"] or {}).get("message") or "upstream error")
        return resp.get("result") or {}

    # -- dispatch ----------------------------------------------------------- #

    def handle(self, msg):
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method is None:
            return None
        if msg_id is None:
            log("notification: %s" % method)
            return None

        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION
                return _ok(
                    msg_id,
                    {
                        "protocolVersion": version,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"subscribe": False, "listChanged": False},
                            "prompts": {"listChanged": False},
                        },
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                        "instructions": INSTRUCTIONS,
                    },
                )
            if method == "ping":
                return _ok(msg_id, {})
            if method == "tools/list":
                return _ok(msg_id, {"tools": [TOOL_SEARCH, TOOL_CALL]})
            if method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                if name == "search_tools":
                    return _ok(msg_id, self.do_search(args))
                if name == "call_tool":
                    return _ok(msg_id, self.do_call(args))
                return _ok(
                    msg_id,
                    _text_result(
                        "Unknown tool %r. This server exposes search_tools and call_tool only." % (name,),
                        is_error=True,
                    ),
                )
            if method in ("resources/list", "resources/templates/list", "prompts/list"):
                try:
                    return _ok(msg_id, self.forward(method, params))
                except Exception as exc:
                    return _ok(msg_id, {} if method != "resources/read" else _text_result(str(exc), True))
            if method == "resources/read":
                return _ok(msg_id, self.forward(method, params))
            return _err(msg_id, -32601, "Method not found: " + str(method))
        except Exception as exc:  # pragma: no cover - defensive
            return _err(msg_id, -32603, "%s: %s" % (type(exc).__name__, exc))


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _clip(names, limit=6):
    names = list(names)
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + ", ... (+%d)" % (len(names) - limit)


def _text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def build_catalog(python, server, catalog_path, lexicon_path):
    lexicon = Lexicon(lexicon_path)
    up = Upstream(python, server)
    try:
        up.start()
        resp = up.request("tools/list", {}, timeout=120.0)
        tools = (resp.get("result") or {}).get("tools") or []
        if not tools:
            raise SystemExit("upstream returned no tools")
        payload = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "tools": tools}
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        cat = load_catalogue(tools, lexicon)
        chars = len(json.dumps(tools, ensure_ascii=False))
        print("catalog written: %s" % catalog_path)
        print("tools: %d  catalog chars: %d  lexicon groups: %d" % (len(cat), chars, len(lexicon.groups)))
    finally:
        up.close()


def main():
    ap = argparse.ArgumentParser(description="SolidWorks MCP discovery shim")
    ap.add_argument("--python", default=os.environ.get("SW_UPSTREAM_PYTHON"), help="Python that runs the upstream server")
    ap.add_argument("--server", default=os.environ.get("SW_UPSTREAM_SERVER"), help="Path to upstream server.py")
    ap.add_argument("--catalog", default=os.environ.get("SW_SHIM_CATALOG", str(DEFAULT_CATALOG)))
    ap.add_argument("--lexicon", default=os.environ.get("SW_SHIM_LEXICON", str(DEFAULT_LEXICON)))
    ap.add_argument("--top-k", type=int, default=int(os.environ.get("SW_SHIM_TOP_K", "8")))
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("SW_SHIM_TIMEOUT", "900")))
    ap.add_argument("--build-catalog", action="store_true", help="Generate the catalogue cache and exit")
    args = ap.parse_args()

    if not args.python or not args.server:
        sys.stderr.write(
            "sw_discovery_shim: --python and --server are required "
            "(or set SW_UPSTREAM_PYTHON / SW_UPSTREAM_SERVER)\n"
        )
        raise SystemExit(2)

    catalog_path = Path(args.catalog)
    lexicon_path = Path(args.lexicon)

    if args.build_catalog:
        build_catalog(args.python, args.server, catalog_path, lexicon_path)
        return

    shim = Shim(
        python=args.python,
        server=args.server,
        catalog_path=catalog_path,
        lexicon_path=lexicon_path,
        top_k=args.top_k,
        upstream_timeout=args.timeout,
        upstream_env={},
    )
    atexit.register(shim.upstream.close)

    for line in sys.stdin:
        msg = read_frame(line)
        if msg is None:
            continue
        reply = shim.handle(msg)
        if reply is not None:
            write_frame(reply)
    shim.upstream.close()


if __name__ == "__main__":
    main()
