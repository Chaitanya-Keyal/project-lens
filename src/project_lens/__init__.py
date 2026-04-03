"""Project Lens — AI-powered project knowledge base and indexer.

Public API re-exported from project_lens.api for convenience:

    from project_lens import LensClient
"""

from project_lens.api import (
    ContributorActivity,
    ContributorProfile,
    LensClient,
    PRDetail,
    PRWithAssessment,
    SearchResult,
)

__all__ = [
    "LensClient",
    "SearchResult",
    "ContributorProfile",
    "ContributorActivity",
    "PRWithAssessment",
    "PRDetail",
]
