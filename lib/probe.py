# -*- coding: utf-8 -*-
"""Report whether the COM servers this bundle drives are registered.

Prints one JSON object on stdout. The check is the registry ProgID, not the
install directory: without a ProgID, COM automation cannot start the
application, no matter where its files sit. It does not launch either
application.
"""
import json
import sys
import winreg

PROG_IDS = {
    "autocad": r"AutoCAD.Application",
    "solidworks": r"SldWorks.Application",
}


def registered(prog_id):
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    winreg.CloseKey(key)
    return True


if __name__ == "__main__":
    print(json.dumps({name: registered(pid) for name, pid in PROG_IDS.items()}))
    sys.exit(0)
