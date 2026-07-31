"""Knowledge providers.

Importing this package registers every shipped provider. Registration is a side effect of
import, which is why they are imported here rather than lazily: a provider that is only
registered once something happens to import it participates in some runs and not others,
depending on what else ran first.
"""

from backend.knowledge.providers.clause_library import ClauseLibraryProvider
from backend.knowledge.providers.llm import LLMProvider
from backend.knowledge.providers.playbook import PlaybookProvider
from backend.knowledge.providers.reference import ReferenceProvider
from backend.knowledge.providers.template import TemplateProvider

__all__ = [
    "ClauseLibraryProvider",
    "LLMProvider",
    "PlaybookProvider",
    "ReferenceProvider",
    "TemplateProvider",
]
