#!/usr/bin/env python3
"""Extract contributor profiles from git history and GitHub activity.

Scans git commits, PRs, and PR assessments to build a profile for each
contributor. Profiles include:
  - GitHub login (canonical identity)
  - Display names seen across systems (git author, PR author)
  - Activity stats (commits, PRs authored/reviewed/merged, date range)

No manual configuration required — profiles are derived entirely from
observable data. Run this after index_git_history.py and github_scan.py.

Usage:
    python scripts/extract_contributors.py          # extract all
    python scripts/extract_contributors.py --show   # extract and print summary
"""
import asyncio
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine, async_session
from backend.models import (
    Base, GitCommit, GithubPR, PRAssessment, ContributorProfile, AnalysisRun,
)


# ---------------------------------------------------------------------------
# Identity resolution: map git emails/names to GitHub logins
# ---------------------------------------------------------------------------

def extract_login_from_email(email: str) -> str | None:
    """Extract GitHub login from noreply email addresses.

    Patterns:
      - 12345+username@users.noreply.github.com
      - username@users.noreply.github.com
    """
    m = re.match(r"(?:\d+\+)?([^@]+)@users\.noreply\.github\.com", email)
    if m:
        return m.group(1)
    return None


def extract_login_from_merge_message(message: str) -> str | None:
    """Extract the PR author's GitHub login from a merge commit message.

    Pattern: 'Merge pull request #NNN from username/branch-name'
    """
    m = re.match(r"Merge pull request #\d+ from ([^/]+)/", message)
    if m:
        return m.group(1)
    return None


async def build_identity_map(session) -> dict[str, str]:
    """Build a mapping from git_email → github_login.

    Uses multiple safe signals:
      1. GitHub noreply emails contain the login directly
      2. Git author name matches a GitHub PR author (case-insensitive)
      3. Email local part matches a GitHub login exactly
    """
    email_to_login: dict[str, str] = {}

    # Collect all distinct git identities
    result = await session.execute(
        select(GitCommit.author_name, GitCommit.author_email).distinct()
    )
    all_git_identities = result.all()

    # Collect all known GitHub logins from PR authors
    result = await session.execute(select(GithubPR.author).distinct())
    pr_authors = {row[0] for row in result.all()}

    # --- Pass 1: noreply emails ---
    for name, email in all_git_identities:
        login = extract_login_from_email(email)
        if login:
            email_to_login[email] = login

    # --- Pass 2: git author name matches a PR author ---
    for name, email in all_git_identities:
        if email in email_to_login:
            continue
        for pr_author in pr_authors:
            if name.lower() == pr_author.lower():
                email_to_login[email] = pr_author
                break

    # --- Pass 3: email local part matches a GitHub login exactly ---
    for name, email in all_git_identities:
        if email in email_to_login:
            continue
        local_part = email.split("@")[0].lower()
        for pr_author in pr_authors:
            if local_part == pr_author.lower():
                email_to_login[email] = pr_author
                break

    return email_to_login


async def extract_profiles():
    """Extract contributor profiles from all available data."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        email_to_login = await build_identity_map(session)

        # Aggregate data per contributor
        # Key: github_login, Value: profile data
        profiles: dict[str, dict] = defaultdict(lambda: {
            "display_names": set(),
            "commits": 0,
            "merge_commits": 0,
            "insertions": 0,
            "deletions": 0,
            "prs_authored": 0,
            "prs_merged": 0,
            "prs_closed": 0,
            "prs_open": 0,
            "reviews_given": 0,
            "review_comments_given": 0,
            "pr_comments_given": 0,
            "repos_active": set(),
            "dates": [],
        })

        # --- Git commits ---
        result = await session.execute(select(GitCommit))
        for commit in result.scalars():
            login = email_to_login.get(commit.author_email)
            if not login:
                # Use the email as a fallback key
                login = commit.author_email

            p = profiles[login]
            p["display_names"].add(commit.author_name)
            if commit.is_merge:
                p["merge_commits"] += 1
            else:
                p["commits"] += 1
                p["insertions"] += commit.insertions or 0
                p["deletions"] += commit.deletions or 0
            p["repos_active"].add(commit.repo)
            if commit.date:
                p["dates"].append(commit.date)

        # --- GitHub PRs (authorship) ---
        result = await session.execute(select(GithubPR))
        for pr in result.scalars():
            login = pr.author
            p = profiles[login]
            p["display_names"].add(pr.author)

            if pr.state == "merged":
                p["prs_merged"] += 1
            elif pr.state == "closed":
                p["prs_closed"] += 1
            elif pr.state == "open":
                p["prs_open"] += 1
            p["prs_authored"] += 1
            p["repos_active"].add(pr.repo)

            # Track dates
            for dt in [pr.created_at_gh, pr.merged_at_gh, pr.closed_at_gh]:
                if dt:
                    p["dates"].append(dt)

            # --- Reviews from reviews_json (exclude self-reviews) ---
            for review in (pr.reviews_json or []):
                reviewer_login = review.get("user", {}).get("login")
                if reviewer_login and reviewer_login != pr.author:
                    rp = profiles[reviewer_login]
                    rp["display_names"].add(reviewer_login)
                    rp["reviews_given"] += 1
                    rp["repos_active"].add(pr.repo)

            # --- Review comments (inline code comments, exclude own PR) ---
            for comment in (pr.review_comments_json or []):
                commenter_login = comment.get("user", {}).get("login")
                if commenter_login and commenter_login != pr.author:
                    cp = profiles[commenter_login]
                    cp["display_names"].add(commenter_login)
                    cp["review_comments_given"] += 1

            # --- Issue comments on PRs (discussion comments, exclude own PR) ---
            for comment in (pr.issue_comments_json or []):
                commenter_login = comment.get("user", {}).get("login")
                if commenter_login and commenter_login != pr.author:
                    cp = profiles[commenter_login]
                    cp["pr_comments_given"] += 1

        # --- Merge authority from merge commit messages ---
        result = await session.execute(
            select(GitCommit.author_name, GitCommit.author_email, GitCommit.message)
            .where(GitCommit.is_merge == True)  # noqa: E712
        )
        merges_by_login: dict[str, int] = defaultdict(int)
        for name, email, message in result.all():
            login = email_to_login.get(email, email)
            merges_by_login[login] += 1

        # --- Merge fragmented profiles ---
        # Some contributors ended up keyed by raw email because the identity
        # map didn't resolve them during git commit processing. Now that we
        # have a complete email_to_login map, merge those fragments.
        merged_profiles: dict[str, dict] = defaultdict(lambda: {
            "display_names": set(),
            "commits": 0,
            "merge_commits": 0,
            "insertions": 0,
            "deletions": 0,
            "prs_authored": 0,
            "prs_merged": 0,
            "prs_closed": 0,
            "prs_open": 0,
            "reviews_given": 0,
            "review_comments_given": 0,
            "pr_comments_given": 0,
            "repos_active": set(),
            "dates": [],
        })

        for key, data in profiles.items():
            # Resolve the key: if it's an email, try to map it to a login
            canonical = email_to_login.get(key, key)
            target = merged_profiles[canonical]
            target["display_names"].update(data["display_names"])
            for field in ["commits", "merge_commits", "insertions", "deletions",
                          "prs_authored", "prs_merged", "prs_closed", "prs_open",
                          "reviews_given", "review_comments_given", "pr_comments_given"]:
                target[field] += data[field]
            target["repos_active"].update(data["repos_active"])
            target["dates"].extend(data["dates"])

        profiles = merged_profiles

        # --- Compute per-dimension tiers using standard deviation bands ---
        # For each dimension (merges, prs, reviews), compute mean and stdev
        # across non-zero contributors. Then classify each person as:
        #   high:     > mean + 1 stdev  (statistical outliers — dominant in this dimension)
        #   moderate: > mean            (above average)
        #   low:      > 0              (some activity)
        #   none:     0                (no activity in this dimension)
        import statistics as _stats

        def compute_bands(values: list[int]) -> tuple[float, float]:
            """Return (mean, mean+stdev) thresholds from non-zero values."""
            nonzero = [v for v in values if v > 0]
            if len(nonzero) < 2:
                return (0, 0)
            mean = _stats.mean(nonzero)
            stdev = _stats.stdev(nonzero)
            return (mean, mean + stdev)

        def classify(value: int, mean: float, high_threshold: float) -> str:
            if value == 0:
                return "none"
            if value > high_threshold:
                return "high"
            if value > mean:
                return "moderate"
            return "low"

        all_merges = [d["merge_commits"] for d in profiles.values()]
        all_prs = [d["prs_merged"] for d in profiles.values()]
        all_reviews = [d["reviews_given"] + d["review_comments_given"] + d["pr_comments_given"]
                       for d in profiles.values()]

        merge_mean, merge_high = compute_bands(all_merges)
        pr_mean, pr_high = compute_bands(all_prs)
        review_mean, review_high = compute_bands(all_reviews)

        # --- Build final profiles ---
        now = datetime.now(timezone.utc)
        written = 0

        for login, data in profiles.items():
            dates = [d for d in data["dates"] if d is not None]
            first_active = min(dates) if dates else None
            last_active = max(dates) if dates else None

            total_review_engagement = data["reviews_given"] + data["review_comments_given"] + data["pr_comments_given"]

            merge_tier = classify(data["merge_commits"], merge_mean, merge_high)
            pr_tier = classify(data["prs_merged"], pr_mean, pr_high)
            review_tier = classify(total_review_engagement, review_mean, review_high)

            stats = {
                "merge_tier": merge_tier,
                "pr_tier": pr_tier,
                "review_tier": review_tier,
                "commits": data["commits"],
                "merge_commits": data["merge_commits"],
                "insertions": data["insertions"],
                "deletions": data["deletions"],
                "prs_authored": data["prs_authored"],
                "prs_merged": data["prs_merged"],
                "prs_closed": data["prs_closed"],
                "prs_open": data["prs_open"],
                "reviews_given": data["reviews_given"],
                "review_comments_given": data["review_comments_given"],
                "pr_comments_given": data["pr_comments_given"],
                "total_review_engagement": total_review_engagement,
                "repos_active": sorted(data["repos_active"]),
            }

            display_names = sorted(data["display_names"])

            # Upsert
            result = await session.execute(
                select(ContributorProfile)
                .where(ContributorProfile.github_login == login)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.display_names = display_names
                existing.stats = stats
                existing.first_active = first_active
                existing.last_active = last_active
                existing.updated_at = now
            else:
                session.add(ContributorProfile(
                    github_login=login,
                    display_names=display_names,
                    stats=stats,
                    first_active=first_active,
                    last_active=last_active,
                ))
            written += 1

        await session.commit()

    return written


async def show_summary():
    """Print a summary of extracted profiles."""
    async with async_session() as session:
        result = await session.execute(
            select(ContributorProfile)
            .order_by(ContributorProfile.last_active.desc())
        )
        profiles = result.scalars().all()

        print(f"Contributor Profiles: {len(profiles)}")
        print(f"{'=' * 80}")
        print(f"{'Login':<25} {'Names':<20} {'Merges':>6} {'M':>4} {'Shipped':>7} {'P':>4} {'Engagement':>10} {'R':>4}")
        print(f"{'-' * 90}")

        sorted_profiles = sorted(
            profiles,
            key=lambda p: -(p.stats.get("merge_commits", 0) + p.stats.get("prs_merged", 0) + p.stats.get("total_review_engagement", 0)),
        )

        for p in sorted_profiles:
            s = p.stats
            merges = s.get("merge_commits", 0)
            shipped = s.get("prs_merged", 0)
            engagement = s.get("total_review_engagement", 0)
            if merges + shipped + engagement == 0:
                continue

            mt = s.get("merge_tier", "none")
            pt = s.get("pr_tier", "none")
            rt = s.get("review_tier", "none")
            names = ", ".join(n for n in p.display_names if n != p.github_login)[:18]

            print(f"{p.github_login:<25} {names:<20} {merges:>6} {mt:>4} {shipped:>7} {pt:>4} {engagement:>10} {rt:>4}")


async def main():
    run_start = datetime.now(timezone.utc)

    print("Extracting contributor profiles from git history and GitHub data...")
    count = await extract_profiles()
    print(f"Extracted {count} contributor profiles.")

    # Record the run
    async with async_session() as session:
        session.add(AnalysisRun(
            run_type="extract_contributors",
            started_at=run_start,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=f"Extracted {count} contributor profiles.",
        ))
        await session.commit()

    if "--show" in sys.argv:
        print()
        await show_summary()


if __name__ == "__main__":
    asyncio.run(main())
