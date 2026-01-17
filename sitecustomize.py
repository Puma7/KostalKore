"""Test environment tweaks for Windows event loop compatibility."""

from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")
