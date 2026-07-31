"""Knowledge providers and the contract they implement."""

import backend.knowledge.providers  # noqa: F401  (registers the shipped providers)
from backend.knowledge.base import PRECEDENCE, KnowledgeProvider, order_by_precedence
from backend.knowledge.registry import (
    ProviderError,
    available_providers,
    get_provider,
    register_provider,
    registered_providers,
    temporary_registration,
)

__all__ = [
    "PRECEDENCE",
    "KnowledgeProvider",
    "ProviderError",
    "available_providers",
    "get_provider",
    "order_by_precedence",
    "register_provider",
    "registered_providers",
    "temporary_registration",
]
