"""Entry point for `dsh-sw-cad deliver`.

The implementation lives in the `cad-deliver-mcp` package. This file is the
stable path everything already calls — the CLI arguments, the exit codes
(0 pass, 1 fail, 2 unknown) and the delivery.json shape are unchanged.

There used to be a frozen copy next to this file as a fallback. It is gone on
purpose: two copies of a judgement this consequential is exactly how they drift
apart, and a fallback that silently runs *some other version* of the checks is a
worse answer than a clear error.

What is preserved is the report contract. A caller that reads delivery.json must
still find one when the package is missing, saying the delivery could not be
judged — that is the whole point of the third verdict.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

INSTALL_COMMAND = "pip install cad-deliver-mcp"


def _report_path(argv: list[str]) -> str:
    """Find --out in the arguments without importing the real parser."""
    for index, item in enumerate(argv):
        if item == "--out" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--out="):
            return item.split("=", 1)[1]
    return "delivery.json"


def _write_unavailable(argv: list[str], reason: str) -> str:
    """Write the report a caller expects, with verdict unknown.

    Returning no file would make a script that checks `if report: ...` conclude
    "nothing to complain about", which is the exact failure mode this tool exists
    to refuse.
    """
    out = _report_path(argv)
    out = out if os.path.isabs(out) else os.path.abspath(out)
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": "LEGACY_COMPAT",
        "rule_profile": "LEGACY_COMPAT",
        "delivery_mode": "normal",
        "targets": [],
        "verdict": "unknown",
        "required": [],
        "missing": [],
        "not_checked": [],
        "counts": {"files": 0, "checked": 0, "failures": 0, "warnings": 0, "blocked": 1},
        "blocked": [
            {
                "path": "",
                "id": "cad_deliver.imported",
                "detail": f"the delivery check could not run: {reason}",
            }
        ],
        "rules": [],
        "files": [],
    }
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return out


try:
    from cad_deliver.cli import main
except ImportError:
    # One line, one command, no traceback: this is an installation problem, and
    # a stack trace would bury the sentence that actually helps.
    _out = _write_unavailable(sys.argv[1:], "cad-deliver-mcp is not installed")
    sys.stderr.write(
        "[dsh-sw-cad] cad-deliver-mcp is not installed, so the delivery was NOT judged. "
        f"Install it with: {INSTALL_COMMAND}\n"
    )
    sys.stderr.write(f"[dsh-sw-cad] wrote {_out} with verdict=unknown (exit 2)\n")
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))