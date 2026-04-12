"""Project Lens — AI-powered project knowledge base and indexer.

Public API re-exported from project_lens.api for convenience:

    from project_lens import LensClient
"""

from project_lens.api import (
    ContributorActivity,
    ContributorProfile,
    IssueWithAssessment,
    LensClient,
    PRDetail,
    PRWithAssessment,
    SearchResult,
    TelegramDigestSummary,
)

__all__ = [
    "LensClient",
    "SearchResult",
    "ContributorProfile",
    "ContributorActivity",
    "PRWithAssessment",
    "PRDetail",
    "IssueWithAssessment",
    "TelegramDigestSummary",
]
