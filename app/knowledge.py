"""Backward-compatible company knowledge interface.

Delegates to the managed enterprise knowledge architecture in app.knowledge_base.
Preserves existing symbols (COMPANY_KNOWLEDGE, is_in_domain_query, search_company_knowledge)
for seamless backward compatibility across test suites and services.
"""

from .knowledge_base import (
    COMPANY_POLICIES as COMPANY_KNOWLEDGE,
    DOMAIN_TOKENS as DOMAIN_KEYWORDS,
    is_in_domain_query,
    search_company_knowledge,
)

__all__ = [
    "COMPANY_KNOWLEDGE",
    "DOMAIN_KEYWORDS",
    "is_in_domain_query",
    "search_company_knowledge",
]
