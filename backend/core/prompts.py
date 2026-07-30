"""Prompt loading.

Prompts live in `backend/prompts/*.md`, versioned in git, and are hashed so a draft can be
tied to the exact instructions that produced it. Behaviour is specified, not hoped for —
that is the fourth of the four deep-agent properties.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PromptError(Exception):
    """A prompt file is missing or empty. Raised at startup, never mid-run."""


@cache
def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt at {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise PromptError(f"prompt {path} is empty")
    return text


def prompt_sha(name: str) -> str:
    return hashlib.sha256(load_prompt(name).encode()).hexdigest()[:16]
