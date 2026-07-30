"""Provider registration.

Deliberately **dynamic**, which is the opposite of `backend/tools/registry.py` — and the
difference is worth stating, because copying the wrong one of the two would quietly lose
something.

Tools are a static tuple on purpose: `assert_error_handlers_are_explicit` sweeps them at
build time and fails if any tool would fall back to the SDK's leaky default error
formatter. That guarantee only exists because the full set is knowable without running
anything. Tools stay static.

Providers are the reverse. The claim spec 05 §5 makes is that adding a knowledge source
touches one new file and no pipeline code — and a static tuple would make that false, since
the tuple *is* pipeline code. So providers register themselves, and
`tests/test_provider_extensibility.py` holds the claim to account by defining a provider
inside the test module and asserting it participates fully.

Registration is process-global, which is the right scope for "what kinds of knowledge does
this deployment understand" and the wrong scope for a test to mutate permanently — hence
`temporary_registration`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from backend.knowledge.base import KnowledgeProvider, precedence_of
from backend.schemas.errors import ContractToolError

__all__ = [
    "ProviderError",
    "available_providers",
    "get_provider",
    "register_provider",
    "registered_providers",
    "temporary_registration",
    "unregister_provider",
]

_PROVIDERS: dict[str, KnowledgeProvider] = {}


class ProviderError(ContractToolError):
    """An unknown provider, or one that cannot be registered."""


def register_provider(provider: KnowledgeProvider) -> KnowledgeProvider:
    """Register `provider`. Returns it, so this reads well as a decorator.

    Refuses a duplicate name rather than overwriting. Two providers answering to
    "template" would mean the winner depended on import order, and import order is not
    something anyone reviews.
    """
    if not getattr(provider, "name", ""):
        raise ProviderError("a knowledge provider must have a name")
    if provider.name in _PROVIDERS:
        raise ProviderError(
            f"a provider named {provider.name!r} is already registered; "
            "two providers with one name would be resolved by import order"
        )
    _PROVIDERS[provider.name] = provider
    return provider


def unregister_provider(name: str) -> None:
    _PROVIDERS.pop(name, None)


def get_provider(name: str) -> KnowledgeProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ProviderError(
            f"no knowledge provider named {name!r}; registered: {sorted(_PROVIDERS)}"
        ) from None


def registered_providers() -> tuple[KnowledgeProvider, ...]:
    """Every registered provider, highest authority first."""
    return tuple(sorted(_PROVIDERS.values(), key=lambda p: (precedence_of(p.name), p.name)))


async def available_providers(intent: object, ctx: object) -> tuple[KnowledgeProvider, ...]:
    """Those that have something to offer for this run, in precedence order.

    Availability checks run concurrently — they are independent lookups, and running five
    of them in series adds latency to every run for no reason.
    """
    candidates = registered_providers()
    verdicts = await asyncio.gather(
        *(p.available(intent, ctx) for p in candidates)  # type: ignore[arg-type]
    )
    return tuple(p for p, ok in zip(candidates, verdicts, strict=True) if ok)


@contextmanager
def temporary_registration(provider: KnowledgeProvider) -> Iterator[KnowledgeProvider]:
    """Register for the duration of a block. For tests.

    Registration is process-global; without this a test that registers a provider leaks it
    into every test that runs afterwards, and the resulting failures appear in unrelated
    files.
    """
    register_provider(provider)
    try:
        yield provider
    finally:
        unregister_provider(provider.name)
