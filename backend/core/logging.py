"""Logging setup.

Contract text, clause bodies, and prompt bodies carry counterparty PII and must never
be emitted above DEBUG. Production runs at INFO, so `log_body()` is a no-op there.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_body(logger: logging.Logger, label: str, body: str, **fields: Any) -> None:
    """Log a sensitive body (draft, clause, prompt) at DEBUG only.

    Callers may pass this freely; at INFO it costs a level check and nothing leaks.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("%s (%d chars) %s\n%s", label, len(body), fields or "", body)
