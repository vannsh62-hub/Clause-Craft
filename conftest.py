"""Repo-root conftest.

Its presence pins pytest's rootdir here, so `backend/` never lands on `sys.path` and
never shadows the SDK's `agents` import root. See tests/test_import_guard.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# backend.core.config validates OPENAI_API_KEY at import time, by design. Key-free tests
# still need to import modules that read settings, so supply a non-functional value.
#
# Only when there is no real key to be had. An environment variable outranks `.env` in
# pydantic-settings, so setting the stub unconditionally shadows a developer's real key and
# silently skips every `requires_api_key` test — which looks exactly like them passing.
_DOTENV = Path(__file__).resolve().parent / ".env"
_HAS_REAL_KEY = _DOTENV.is_file() and "OPENAI_API_KEY" in _DOTENV.read_text()

if not _HAS_REAL_KEY:
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")
