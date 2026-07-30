"""SDK-wide configuration, applied once at startup.

Two things, both easy to get wrong:

**The API key must be handed to the SDK.** Its default client reads `OPENAI_API_KEY` from the
process environment, not from our `Settings`. Loading the key from `.env` into `settings` and
stopping there produces a `Missing credentials` error at the first model call, long after
startup — exactly the failure mode `core/config.py` exists to prevent.

**Tracing is on by default**, and exports spans to OpenAI using that key. Those spans carry
tool arguments and model output: party names, contract text, clause bodies. Nothing in this
deployment's logging policy permits that, so it is switched off centrally rather than per-run.
"""

from __future__ import annotations

from agents import set_default_openai_key, set_tracing_disabled

from backend.core.config import settings

_CONFIGURED = False


def configure_sdk() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    # use_for_tracing=False: no trace exporter should ever authenticate, let alone upload.
    set_default_openai_key(settings.openai_api_key, use_for_tracing=False)
    set_tracing_disabled(True)
    _CONFIGURED = True
