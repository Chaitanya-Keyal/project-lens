"""Issue and assessment data queries."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, or_, select

from project_lens.models import GithubIssue, IssueAssessment
from project_lens.api.config import LensConfig


@dataclass
class IssueWithAssessment:
    """Open issue joined with its assessment data."""
    repo: str
    number: int
    title: str
    author: str
    state: str
    labels: list[str] | None
    comment_count: int
    created_at: datetime | None
    updated_at: datetime | None
    closed_at: datetime | None
    # Assessment fields (None if not yet assessed)
    tier: int | None
    category: str | None
    complexity: str | None
    impact: str | None
    summary: str | None
    recommendation: str | None
    discussion_state: str | None
    stale_reason: str | None
    participant_contributions: list | None


@dataclass
class IssueDetail:
    """Full issue detail with all assessment fields and raw issue data."""
    # Core issue fields
    repo: str
    number: int
    title: str
    author: str
    state: str
    labels: list[str] | None
    comment_count: int
    body: str | None
    created_at: datetime | None
    updated_at: datetime | None
    closed_at: datetime | None
    # Assessment fields
    tier: int | None
    category: str | None
    complexity: str | None
    impact: str | None
    summary: str | None
    recommendation: str | None
    discussion_state: str | None
    stale_reason: str | None
    participant_contributions: list | None
    discussion_positions: list | None
    cross_references: list | None
    labels_suggested: list | None
    # Raw comment thread (already parsed from JSON)
    comments: list = field(default_factory=list)


class IssueService:
    """Queries for issue data and assessments."""

    def __init__(self, config: LensConfig):
        self._config = config

    async def get_open_issues_with_assessments(self) -> list[IssueWithAssessment]:
        """All open issues joined with assessments, ordered by tier DESC, updated DESC."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(GithubIssue, IssueAssessment)
                .outerjoin(
                    IssueAssessment,
                    and_(
                        GithubIssue.repo == IssueAssessment.repo,
                        GithubIssue.number == IssueAssessment.issue_number,
                    ),
                )
                .where(GithubIssue.state == "open")
                .order_by(
                    IssueAssessment.tier.desc().nulls_last(),
                    GithubIssue.updated_at_gh.desc(),
                )
            )
            return [
                IssueWithAssessment(
                    repo=issue.repo,
                    number=issue.number,
                    title=issue.title,
                    author=issue.author,
                    state=issue.state,
                    labels=issue.labels,
                    comment_count=issue.comment_count,
                    created_at=issue.created_at_gh,
                    updated_at=issue.updated_at_gh,
                    closed_at=issue.closed_at_gh,
                    tier=a.tier if a else None,
                    category=a.category if a else None,
                    complexity=a.complexity if a else None,
                    impact=a.impact if a else None,
                    summary=a.summary if a else None,
                    recommendation=a.recommendation if a else None,
                    discussion_state=a.discussion_state if a else None,
                    stale_reason=a.stale_reason if a else None,
                    participant_contributions=a.participant_contributions if a else None,
                )
                for issue, a in result.all()
            ]

    async def get_issue_detail(self, repo: str, number: int) -> IssueDetail | None:
        """Full issue detail with all assessment fields and raw comment data."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(GithubIssue, IssueAssessment)
                .outerjoin(
                    IssueAssessment,
                    and_(
                        GithubIssue.repo == IssueAssessment.repo,
                        GithubIssue.number == IssueAssessment.issue_number,
                    ),
                )
                .where(GithubIssue.repo == repo, GithubIssue.number == number)
            )
            row = result.first()
            if not row:
                return None

            issue, a = row
            return IssueDetail(
                repo=issue.repo,
                number=issue.number,
                title=issue.title,
                author=issue.author,
                state=issue.state,
                labels=issue.labels,
                comment_count=issue.comment_count,
                body=issue.body,
                created_at=issue.created_at_gh,
                updated_at=issue.updated_at_gh,
                closed_at=issue.closed_at_gh,
                tier=a.tier if a else None,
                category=a.category if a else None,
                complexity=a.complexity if a else None,
                impact=a.impact if a else None,
                summary=a.summary if a else None,
                recommendation=a.recommendation if a else None,
                discussion_state=a.discussion_state if a else None,
                stale_reason=a.stale_reason if a else None,
                participant_contributions=a.participant_contributions if a else None,
                discussion_positions=a.discussion_positions if a else None,
                cross_references=a.cross_references if a else None,
                labels_suggested=a.labels_suggested if a else None,
                comments=issue.comments_json or [],
            )

    async def get_issue_titles(
        self, keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], str]:
        """Batch lookup of issue titles by (repo, number) pairs."""
        if not keys:
            return {}
        async with self._config.async_session() as session:
            conditions = [
                and_(GithubIssue.repo == repo, GithubIssue.number == number)
                for repo, number in keys
            ]
            result = await session.execute(
                select(GithubIssue.repo, GithubIssue.number, GithubIssue.title)
                .where(or_(*conditions))
            )
            return {
                (repo, number): title for repo, number, title in result.all()
            }
