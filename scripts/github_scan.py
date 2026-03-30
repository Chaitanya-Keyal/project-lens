#!/usr/bin/env python3
"""Fetch PR and issue data from GitHub REST API (unauthenticated) and store in DB.

Two-phase approach:
  Phase A (fast): List all PRs/issues, upsert metadata. ~4-5 API calls.
  Phase B (slow): Fetch reviews + comments per PR. Committed per-PR, resumable.

Rate limit: 60 requests/hour unauthenticated. The script tracks remaining quota
and sleeps when necessary. Progress is saved after each PR so it can be interrupted
and resumed without losing work.

Usage:
    python scripts/github_scan.py                        # all repos in config
    python scripts/github_scan.py SeedSigner/seedsigner  # specific repo
    python scripts/github_scan.py --list-only             # Phase A only (metadata)
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.database import engine, async_session
from backend.models import Base, GithubPR, GithubIssue, AnalysisRun
from backend.project_config import load_project_config

GITHUB_API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github.v3+json"}
if settings.github_token:
    HEADERS["Authorization"] = f"Bearer {settings.github_token}"


def parse_gh_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class RateLimiter:
    """Conservative rate limiter for GitHub API.

    Even with an authenticated token (5,000 req/hr), we stay well below
    the limit to avoid any risk of account suspension. Default: max 500
    requests per hour (~1 every 7 seconds). This is 10% of the authenticated
    limit — extremely conservative.
    """
    # Hard ceiling: never exceed this many requests per hour
    MAX_REQUESTS_PER_HOUR = 1000
    # Minimum seconds between requests
    MIN_DELAY = 2.0

    def __init__(self):
        self.remaining = 60  # GitHub's reported remaining (updated from headers)
        self.reset_at = 0
        self.total_requests = 0
        self.hour_start = time.time()
        self.requests_this_hour = 0
        self.last_request_at = 0

    def update(self, resp: httpx.Response):
        self.remaining = int(resp.headers.get("X-RateLimit-Remaining", self.remaining))
        self.reset_at = int(resp.headers.get("X-RateLimit-Reset", self.reset_at))
        self.total_requests += 1
        self.requests_this_hour += 1
        self.last_request_at = time.time()

        # Reset hourly counter
        if time.time() - self.hour_start > 3600:
            self.hour_start = time.time()
            self.requests_this_hour = 0

    async def wait_if_needed(self):
        # 1. Enforce minimum delay between requests
        elapsed = time.time() - self.last_request_at
        if elapsed < self.MIN_DELAY:
            await asyncio.sleep(self.MIN_DELAY - elapsed)

        # 2. Enforce our own hourly ceiling (well below GitHub's limit)
        if self.requests_this_hour >= self.MAX_REQUESTS_PER_HOUR:
            wait = 3600 - (time.time() - self.hour_start) + 2
            if wait > 0:
                print(f"    Self-imposed hourly limit reached ({self.requests_this_hour} requests). Waiting {wait:.0f}s...")
                await asyncio.sleep(wait)
                self.hour_start = time.time()
                self.requests_this_hour = 0

        # 3. Respect GitHub's reported limit as a safety net
        if self.remaining <= 5:
            wait = max(self.reset_at - time.time() + 2, 0)
            if wait > 0:
                print(f"    GitHub rate limit nearly exhausted ({self.remaining} left). Waiting {wait:.0f}s...")
                await asyncio.sleep(wait)


async def api_get(client: httpx.AsyncClient, limiter: RateLimiter, url: str, params: dict = None) -> dict | list:
    """Make a rate-limited GET request to the GitHub API."""
    await limiter.wait_if_needed()
    resp = await client.get(url, params=params or {}, headers=HEADERS)
    limiter.update(resp)

    if resp.status_code == 403:
        # Rate limited — back off and retry once
        print(f"    403 response — backing off 60s...")
        await asyncio.sleep(60)
        resp = await client.get(url, params=params or {}, headers=HEADERS)
        limiter.update(resp)

    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Phase A: List PRs and Issues (fast, metadata only)
# ---------------------------------------------------------------------------

async def fetch_all_prs(client: httpx.AsyncClient, limiter: RateLimiter, owner: str, name: str) -> list[dict]:
    """Fetch ALL PRs (open + closed + merged), paginating through entire history."""
    all_prs = []
    page = 1
    while True:
        data = await api_get(client, limiter,
            f"{GITHUB_API}/repos/{owner}/{name}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100, "page": page},
        )
        if not data:
            break
        all_prs.extend(data)
        print(f"    PRs page {page}: {len(data)} items (total so far: {len(all_prs)})")
        if len(data) < 100:
            break
        page += 1
    return all_prs


async def fetch_all_issues(client: httpx.AsyncClient, limiter: RateLimiter, owner: str, name: str) -> list[dict]:
    """Fetch all open issues (excluding PRs)."""
    all_issues = []
    page = 1
    while True:
        data = await api_get(client, limiter,
            f"{GITHUB_API}/repos/{owner}/{name}/issues",
            {"state": "open", "sort": "updated", "direction": "desc", "per_page": 100, "page": page},
        )
        if not data:
            break
        for item in data:
            if "pull_request" not in item:
                all_issues.append(item)
        if len(data) < 100:
            break
        page += 1
    return all_issues


async def upsert_pr(session, repo: str, data: dict):
    result = await session.execute(
        select(GithubPR).where(GithubPR.repo == repo, GithubPR.number == data["number"])
    )
    pr = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    labels = [label["name"] for label in data.get("labels", [])]

    fields = dict(
        title=data["title"],
        author=data["user"]["login"],
        state="merged" if data.get("merged_at") else data["state"],
        labels=labels,
        mergeable=data.get("mergeable"),
        body=data.get("body"),
        created_at_gh=parse_gh_datetime(data.get("created_at")),
        updated_at_gh=parse_gh_datetime(data.get("updated_at")),
        closed_at_gh=parse_gh_datetime(data.get("closed_at")),
        merged_at_gh=parse_gh_datetime(data.get("merged_at")),
        last_synced=now,
    )

    if pr:
        for k, v in fields.items():
            setattr(pr, k, v)
    else:
        pr = GithubPR(repo=repo, number=data["number"], **fields)
        session.add(pr)


async def upsert_issue(session, repo: str, data: dict):
    result = await session.execute(
        select(GithubIssue).where(GithubIssue.repo == repo, GithubIssue.number == data["number"])
    )
    issue = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    labels = [label["name"] for label in data.get("labels", [])]

    fields = dict(
        title=data["title"],
        author=data["user"]["login"],
        state=data["state"],
        labels=labels,
        comment_count=data.get("comments", 0),
        body=data.get("body"),
        created_at_gh=parse_gh_datetime(data.get("created_at")),
        updated_at_gh=parse_gh_datetime(data.get("updated_at")),
        closed_at_gh=parse_gh_datetime(data.get("closed_at")),
        last_synced=now,
    )

    if issue:
        for k, v in fields.items():
            setattr(issue, k, v)
    else:
        issue = GithubIssue(repo=repo, number=data["number"], **fields)
        session.add(issue)


# ---------------------------------------------------------------------------
# Phase B: Fetch PR detail data (reviews + comments), per-PR commit
# ---------------------------------------------------------------------------

async def fetch_pr_details(client: httpx.AsyncClient, limiter: RateLimiter, owner: str, name: str, pr_number: int) -> dict:
    """Fetch reviews, review comments, and issue comments for a single PR."""
    reviews = await api_get(client, limiter,
        f"{GITHUB_API}/repos/{owner}/{name}/pulls/{pr_number}/reviews",
        {"per_page": 100},
    )
    review_comments = await api_get(client, limiter,
        f"{GITHUB_API}/repos/{owner}/{name}/pulls/{pr_number}/comments",
        {"per_page": 100},
    )
    issue_comments = await api_get(client, limiter,
        f"{GITHUB_API}/repos/{owner}/{name}/issues/{pr_number}/comments",
        {"per_page": 100},
    )
    return {
        "reviews": reviews,
        "review_comments": review_comments,
        "issue_comments": issue_comments,
    }


async def backfill_pr_details(client: httpx.AsyncClient, limiter: RateLimiter, owner: str, name: str):
    """Fetch detail data for PRs that need it. Commits per-PR, fully resumable.

    Fetches details for:
    1. PRs that have never had details fetched (initial backfill)
    2. PRs where GitHub activity happened after our last detail fetch (ongoing updates)
    """
    repo = f"{owner}/{name}"

    async with async_session() as session:
        from sqlalchemy import or_
        result = await session.execute(
            select(GithubPR)
            .where(
                GithubPR.repo == repo,
                or_(
                    # Never fetched
                    GithubPR.detail_fetched_at.is_(None),
                    # GitHub activity since our last fetch
                    GithubPR.updated_at_gh > GithubPR.detail_fetched_at,
                ),
            )
            .order_by(GithubPR.number.desc())
        )
        prs_to_fetch = result.scalars().all()

    if not prs_to_fetch:
        print(f"  All PRs up to date.")
        return 0

    never_fetched = sum(1 for p in prs_to_fetch if p.detail_fetched_at is None)
    stale = len(prs_to_fetch) - never_fetched
    print(f"  {len(prs_to_fetch)} PRs need detail data ({never_fetched} new, {stale} updated). 3 API calls each.")

    fetched = 0
    for pr in prs_to_fetch:
        try:
            details = await fetch_pr_details(client, limiter, owner, name, pr.number)

            # Save immediately in its own session/transaction
            async with async_session() as session:
                result = await session.execute(
                    select(GithubPR).where(GithubPR.id == pr.id)
                )
                db_pr = result.scalar_one()
                db_pr.reviews_json = details["reviews"]
                db_pr.review_comments_json = details["review_comments"]
                db_pr.issue_comments_json = details["issue_comments"]
                db_pr.detail_fetched_at = datetime.now(timezone.utc)
                await session.commit()

            fetched += 1
            review_count = len(details["reviews"])
            comment_count = len(details["review_comments"]) + len(details["issue_comments"])
            print(f"    PR #{pr.number}: {review_count} reviews, {comment_count} comments "
                  f"[{fetched}/{len(prs_to_fetch)}, API remaining: {limiter.remaining}]")

        except httpx.HTTPStatusError as e:
            print(f"    PR #{pr.number}: HTTP {e.response.status_code} — skipping")
        except Exception as e:
            print(f"    PR #{pr.number}: {e} — skipping")

    return fetched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    config = load_project_config()

    # Parse args
    list_only = "--list-only" in sys.argv
    target = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            target = arg
            break

    if target:
        repos = [r for r in config.repos if r.full_name == target]
        if not repos:
            print(f"Repo '{target}' not found in config. Available:")
            for r in config.repos:
                print(f"  {r.full_name}")
            sys.exit(1)
    else:
        repos = config.repos

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run_start = datetime.now(timezone.utc)
    limiter = RateLimiter()
    stats = []

    print(f"GitHub scan for {len(repos)} repo(s): {config.display_name}")
    print(f"Mode: {'list only' if list_only else 'full (list + detail backfill)'}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for repo in repos:
            print(f"\n  [{repo.full_name}]")
            try:
                # Phase A: List all PRs and issues
                print(f"  Phase A: Fetching PR/issue list...")
                prs = await fetch_all_prs(client, limiter, repo.owner, repo.name)
                issues = await fetch_all_issues(client, limiter, repo.owner, repo.name)

                async with async_session() as session:
                    for pr_data in prs:
                        await upsert_pr(session, repo.full_name, pr_data)
                    for issue_data in issues:
                        await upsert_issue(session, repo.full_name, issue_data)
                    await session.commit()

                print(f"  Phase A done: {len(prs)} PRs, {len(issues)} issues saved.")

                # Phase B: Fetch detail data for PRs missing it
                if not list_only:
                    print(f"  Phase B: Fetching PR detail data (reviews/comments)...")
                    detail_count = await backfill_pr_details(client, limiter, repo.owner, repo.name)
                    stats.append({
                        "repo": repo.full_name,
                        "prs_listed": len(prs),
                        "issues_listed": len(issues),
                        "prs_detailed": detail_count,
                    })
                else:
                    stats.append({
                        "repo": repo.full_name,
                        "prs_listed": len(prs),
                        "issues_listed": len(issues),
                    })

            except Exception as e:
                print(f"  Error: {e}")
                stats.append({"repo": repo.full_name, "error": str(e)})

    # Record the analysis run
    async with async_session() as session:
        run = AnalysisRun(
            run_type="github_scan",
            started_at=run_start,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=str(stats),
        )
        session.add(run)
        await session.commit()

    elapsed = datetime.now(timezone.utc) - run_start
    print(f"\nDone in {elapsed}. {limiter.total_requests} API requests made.")
    for s in stats:
        print(f"  {s}")


if __name__ == "__main__":
    asyncio.run(main())
