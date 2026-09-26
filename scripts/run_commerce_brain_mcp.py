#!/usr/bin/env python3
"""Launch the local Commerce Brain MCP with the local Laya credential in memory."""

from __future__ import annotations

import os
import runpy
from pathlib import Path


KEY_PATH = Path.home() / ".local/share/commerce-laya/config/api-key"
SERVER_PATH = Path(__file__).resolve().parents[1] / "bridge" / "server.py"


def main() -> None:
    try:
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if key:
        os.environ["COMMERCE_BRAIN_LAYA_API_KEY"] = key
    else:
        os.environ.pop("COMMERCE_BRAIN_LAYA_API_KEY", None)
    runpy.run_path(str(SERVER_PATH), run_name="__main__")


if __name__ == "__main__":
    main()
