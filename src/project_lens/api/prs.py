"""PR and assessment data queries."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, or_, select

from project_lens.models import ContributorProfile, GithubPR, PRAssessment
from project_lens.api.config import LensConfig


@dataclass
class PRWithAssessment:
    """Open PR joined with its assessment data."""
    repo: str
    number: int
    title: str
    author: str
    state: str
    review_state: str | None
    created_at: datetime | None
    updated_at: datetime | None
    tier: int | None
    complexity: str | None
    risk: str | None
    summary: str | None
    recommendation: str | None
    participant_contributions: list | None
    outcome: dict | None


@dataclass
class PRDetail:
    """Full PR detail with all assessment fields and raw GitHub data."""
    # Core PR fields
    repo: str
    number: int
    title: str
    author: str
    state: str
    review_state: str | None
    created_at: datetime | None
    updated_at: datetime | None
    # Assessment fields
    tier: int | None
    complexity: str | None
    risk: str | None
    summary: str | None
    recommendation: str | None
    participant_contributions: list | None
    outcome: dict | None
    diff_size: int | None
    merge_readiness: str | None
    merge_readiness_rationale: str | None
    pending_actions: dict | None
    pending_actions_updated_at: datetime | None
    reviewer_sentiment: list | None
    stale_reason: str | None
    cross_references: list | None
    reviews: list | None
    # Raw GitHub API responses (already parsed from JSON)
    raw_reviews: list = field(default_factory=list)
    raw_review_comments: list = field(default_factory=list)
    raw_issue_comments: list = field(default_factory=list)


@dataclass
class ContributorActivity:
    """Contributor activity stats derived from profiles."""
    github_username: str
    pr_count: int
    review_count: int
    comment_count: int
    last_active: datetime | None


class PRService:
    """Queries for PR data, assessments, and contributor discovery."""

    def __init__(self, config: LensConfig):
        self._config = config

    async def get_open_prs_with_assessments(self) -> list[PRWithAssessment]:
        """All open PRs joined with assessments, ordered by tier DESC, updated DESC."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(GithubPR, PRAssessment)
                .outerjoin(
                    PRAssessment,
                    and_(
                        GithubPR.repo == PRAssessment.repo,
                        GithubPR.number == PRAssessment.pr_number,
                    ),
                )
                .where(GithubPR.state == "open")
                .order_by(
                    PRAssessment.tier.desc().nulls_last(),
                    GithubPR.updated_at_gh.desc(),
                )
            )
            return [
                PRWithAssessment(
                    repo=pr.repo,
                    number=pr.number,
                    title=pr.title,
                    author=pr.author,
                    state=pr.state,
                    review_state=pr.review_state,
                    created_at=pr.created_at_gh,
                    updated_at=pr.updated_at_gh,
                    tier=a.tier if a else None,
                    complexity=a.complexity if a else None,
                    risk=a.risk if a else None,
                    summary=a.summary if a else None,
                    recommendation=a.recommendation if a else None,
                    participant_contributions=a.participant_contributions if a else None,
                    outcome=a.outcome if a else None,
                )
                for pr, a in result.all()
            ]

    async def get_pr_detail(self, repo: str, number: int) -> PRDetail | None:
        """Full PR detail with all assessment fields and raw GitHub data."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(GithubPR, PRAssessment)
                .outerjoin(
                    PRAssessment,
                    and_(
                        GithubPR.repo == PRAssessment.repo,
                        GithubPR.number == PRAssessment.pr_number,
                    ),
                )
                .where(GithubPR.repo == repo, GithubPR.number == number)
            )
            row = result.first()
            if not row:
                return None

            pr, a = row
            return PRDetail(
                repo=pr.repo,
                number=pr.number,
                title=pr.title,
                author=pr.author,
                state=pr.state,
                review_state=pr.review_state,
                created_at=pr.created_at_gh,
                updated_at=pr.updated_at_gh,
                tier=a.tier if a else None,
                complexity=a.complexity if a else None,
                risk=a.risk if a else None,
                summary=a.summary if a else None,
                recommendation=a.recommendation if a else None,
                participant_contributions=a.participant_contributions if a else None,
                outcome=a.outcome if a else None,
                diff_size=pr.diff_size,
                merge_readiness=a.merge_readiness if a else None,
                merge_readiness_rationale=a.merge_readiness_rationale if a else None,
                pending_actions=a.pending_actions if a else None,
                pending_actions_updated_at=a.pending_actions_updated_at if a else None,
                reviewer_sentiment=a.reviewer_sentiment if a else None,
                stale_reason=a.stale_reason if a else None,
                cross_references=a.cross_references if a else None,
                reviews=a.reviews if a else None,
                raw_reviews=pr.reviews_json or [],
                raw_review_comments=pr.review_comments_json or [],
                raw_issue_comments=pr.issue_comments_json or [],
            )

    async def get_pr_titles(
        self, keys: list[tuple[str, int]],
    ) -> dict[tuple[str, int], str]:
        """Batch lookup of PR titles by (repo, number) pairs."""
        if not keys:
            return {}
        async with self._config.async_session() as session:
            conditions = [
                and_(GithubPR.repo == repo, GithubPR.number == number)
                for repo, number in keys
            ]
            result = await session.execute(
                select(GithubPR.repo, GithubPR.number, GithubPR.title)
                .where(or_(*conditions))
            )
            return {
                (repo, number): title for repo, number, title in result.all()
            }

    async def discover_contributors(self) -> list[ContributorActivity]:
        """All contributors with activity stats, ordered by last active."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(ContributorProfile)
                .order_by(ContributorProfile.last_active.desc())
            )
            return [
                ContributorActivity(
                    github_username=p.github_login,
                    pr_count=p.stats.get("prs_authored", 0),
                    review_count=p.stats.get("reviews_given", 0),
                    comment_count=(
                        p.stats.get("review_comments_given", 0)
                        + p.stats.get("pr_comments_given", 0)
                    ),
                    last_active=p.last_active,
                )
                for p in result.scalars()
            ]
