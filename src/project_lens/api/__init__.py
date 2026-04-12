"""Project Lens API — importable Python package for consuming project knowledge data.

Usage:
    from project_lens import LensClient

    lens = LensClient(config_dir="/path/to/project-lens-config")

    # Search
    results = await lens.search("raqm", collections=["telegram_threads"])

    # Identity
    github_map = await lens.resolve_display_names(["Kaylee Frye", "Malcolm Reynolds"])

    # Document retrieval
    thread = await lens.get_document("telegram_threads", "tg_chat_123/thread_1")

    # PR data
    open_prs = await lens.get_open_prs_with_assessments()
    detail = await lens.get_pr_detail("SeedSigner/seedsigner", 787)
    titles = await lens.get_pr_titles([("SeedSigner/seedsigner", 787)])
    contributors = await lens.discover_contributors()
"""

from datetime import datetime
from pathlib import Path

from project_lens.api.config import LensConfig
from project_lens.api.identity import ContributorProfile, IdentityService
from project_lens.api.issues import (
    IssueDetail,
    IssueService,
    IssueWithAssessment,
)
from project_lens.api.prs import (
    ContributorActivity,
    PRDetail,
    PRService,
    PRWithAssessment,
)
from project_lens.api.search import SearchResult, SearchService
from project_lens.api.telegram import TelegramDigestSummary, TelegramService


class LensClient:
    """Facade for all project-lens data access.

    Created once at app startup and reused. Holds the SQLAlchemy engine,
    ChromaDB client (lazy-initialized), and embedding function.
    """

    def __init__(self, config_dir: str | Path):
        self._config = LensConfig(config_dir)
        self._search = SearchService(self._config)
        self._identity = IdentityService(self._config)
        self._prs = PRService(self._config)
        self._issues = IssueService(self._config)
        self._telegram = TelegramService(self._config)

    # --- Search ---

    async def search(
        self,
        query: str,
        n_results: int = 15,
        collections: list[str] | None = None,
    ) -> list[SearchResult]:
        """Hybrid search across embedded collections."""
        return await self._search.search(
            query, n_results=n_results, collections=collections,
        )

    async def search_related(
        self,
        query: str,
        exclude_id: str | None = None,
        n_results: int = 5,
    ) -> list[SearchResult]:
        """Find related context, optionally excluding one document."""
        return await self._search.search_related(
            query, exclude_id=exclude_id, n_results=n_results,
        )

    async def get_document(
        self, collection: str, doc_id: str,
    ) -> SearchResult | None:
        """Fetch a single document by its stable ID."""
        return await self._search.get_document(collection, doc_id)

    # --- Identity ---

    async def resolve_display_names(self, names: list[str]) -> dict[str, str]:
        """Map display names to GitHub logins."""
        return await self._identity.resolve_display_names(names)

    async def link_display_name(
        self, github_login: str, display_name: str,
    ) -> bool:
        """Link a display name to a contributor profile."""
        return await self._identity.link_display_name(github_login, display_name)

    async def unlink_display_name(
        self, github_login: str, display_name: str,
    ) -> bool:
        """Unlink a display name from a contributor profile."""
        return await self._identity.unlink_display_name(github_login, display_name)

    async def list_contributor_profiles(self) -> list[ContributorProfile]:
        """Return all contributor profiles."""
        return await self._identity.list_contributor_profiles()

    # --- PR Data ---

    async def get_open_prs_with_assessments(self) -> list[PRWithAssessment]:
        """All open PRs joined with assessments, ordered by tier DESC."""
        return await self._prs.get_open_prs_with_assessments()

    async def get_recently_closed_prs(
        self, since: datetime | None = None,
    ) -> list[PRWithAssessment]:
        """Merged and closed PRs since a cutoff date."""
        return await self._prs.get_recently_closed_prs(since=since)

    async def get_pr_detail(self, repo: str, number: int) -> PRDetail | None:
        """Full PR detail with all assessment fields and raw GitHub data."""
        return await self._prs.get_pr_detail(repo, number)

    async def get_pr_titles(
        self, keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], str]:
        """Batch lookup of PR titles by (repo, number) pairs."""
        return await self._prs.get_pr_titles(keys)

    async def get_pr_participants(
        self, keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], set[str]]:
        """Batch lookup of participant usernames (author + contributors) by (repo, number) pairs."""
        return await self._prs.get_pr_participants(keys)

    async def discover_contributors(self) -> list[ContributorActivity]:
        """All contributors with activity stats."""
        return await self._prs.discover_contributors()

    # --- Telegram Data ---

    async def list_telegram_participants(self) -> list[str]:
        """Distinct Telegram display names from message history."""
        return await self._telegram.list_participants()

    async def get_recent_telegram_digests(
        self,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TelegramDigestSummary]:
        """Recent Telegram thread digests, ordered by most recent first."""
        return await self._telegram.get_recent_digests(since=since, limit=limit)

    # --- Issue Data ---

    async def get_open_issues_with_assessments(self) -> list[IssueWithAssessment]:
        """All open issues joined with assessments, ordered by tier DESC."""
        return await self._issues.get_open_issues_with_assessments()

    async def get_issue_detail(self, repo: str, number: int) -> IssueDetail | None:
        """Full issue detail with all assessment fields and raw comment data."""
        return await self._issues.get_issue_detail(repo, number)

    async def get_issue_titles(
        self, keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], str]:
        """Batch lookup of issue titles by (repo, number) pairs."""
        return await self._issues.get_issue_titles(keys)

    # --- Lifecycle ---

    async def close(self):
        """Dispose the engine and release resources."""
        await self._config.close()


__all__ = [
    "LensClient",
    "SearchResult",
    "ContributorProfile",
    "ContributorActivity",
    "PRWithAssessment",
    "PRDetail",
    "IssueWithAssessment",
    "IssueDetail",
    "TelegramDigestSummary",
]
