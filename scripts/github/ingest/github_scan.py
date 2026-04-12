#!/usr/bin/env python3
"""Fetch PR and issue data from GitHub GraphQL API and store in DB.

Uses GitHub's GraphQL search with date filtering to only fetch items updated
since the last scan. Metadata and detail data (reviews, comments) are fetched
in a single query per item type, eliminating the old REST two-phase approach.

Nested connections that exceed a single page (>100 reviews, >100 comments, etc.)
are automatically paginated with follow-up queries.

Rate limit: GraphQL uses a point-based system (5,000 points/hour). Each query's
actual cost is returned in the response and tracked by the rate limiter.

Usage:
    python scripts/github_scan.py                        # all repos in config
    python scripts/github_scan.py SeedSigner/seedsigner  # specific repo
    python scripts/github_scan.py --list-only             # metadata only (no reviews/comments)
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, func, or_

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.settings import settings
from project_lens.database import engine, async_session
from project_lens.models import Base, GithubPR, GithubIssue, AnalysisRun, ProjectConfigEntry
from project_lens.project_config import load_project_config

GRAPHQL_URL = "https://api.github.com/graphql"

if not settings.github_token:
    print("Error: GITHUB_TOKEN is required for GraphQL API access.")
    print("Set it in your project .env file.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {settings.github_token}",
    "Content-Type": "application/json",
}


def parse_gh_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# GraphQL query templates
# ---------------------------------------------------------------------------

# Fragment for rateLimit — included in every query
RATE_LIMIT_FRAGMENT = "rateLimit { cost remaining resetAt }"

PR_SEARCH_QUERY = """
query($q: String!, $cursor: String) {
  RATE_LIMIT
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title state merged mergeable
        author { login }
        labels(first: 20) { nodes { name } }
        body createdAt updatedAt closedAt mergedAt
        reviews(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { author { login } state submittedAt body }
        }
        reviewThreads(first: 60) {
          pageInfo { hasNextPage endCursor }
          nodes {
            comments(first: 20) {
              pageInfo { hasNextPage endCursor }
              nodes { author { login } body createdAt }
            }
          }
        }
        comments(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { author { login } body createdAt }
        }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

PR_SEARCH_LIST_ONLY_QUERY = """
query($q: String!, $cursor: String) {
  RATE_LIMIT
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title state merged mergeable
        author { login }
        labels(first: 20) { nodes { name } }
        body createdAt updatedAt closedAt mergedAt
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

ISSUE_SEARCH_QUERY = """
query($q: String!, $cursor: String) {
  RATE_LIMIT
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number title state
        author { login }
        labels(first: 20) { nodes { name } }
        body createdAt updatedAt closedAt
        comments(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { author { login } body createdAt }
        }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

ISSUE_SEARCH_LIST_ONLY_QUERY = """
query($q: String!, $cursor: String) {
  RATE_LIMIT
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number title state
        author { login }
        labels(first: 20) { nodes { name } }
        body createdAt updatedAt closedAt
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

# Follow-up queries for paginating truncated nested connections

PR_REVIEWS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  RATE_LIMIT
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { author { login } state submittedAt body }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

PR_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  RATE_LIMIT
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { author { login } body createdAt }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

PR_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  RATE_LIMIT
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 60, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          comments(first: 20) {
            pageInfo { hasNextPage endCursor }
            nodes { author { login } body createdAt }
          }
        }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)

ISSUE_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  RATE_LIMIT
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { author { login } body createdAt }
      }
    }
  }
}
""".replace("RATE_LIMIT", RATE_LIMIT_FRAGMENT)


# ---------------------------------------------------------------------------
# Rate limiter (GraphQL point-based)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Rate limiter for GitHub GraphQL API (point-based, 5,000 points/hour)."""
    MIN_DELAY = 2.0
    SAFETY_THRESHOLD = 500  # pause when remaining drops below this

    def __init__(self):
        self.remaining = 5000
        self.reset_at = 0
        self.total_requests = 0
        self.total_points = 0
        self.last_request_at = 0

    def update(self, rate_limit_data: dict | None):
        self.total_requests += 1
        self.last_request_at = time.time()
        if rate_limit_data:
            self.remaining = rate_limit_data.get("remaining", self.remaining)
            self.total_points += rate_limit_data.get("cost", 0)
            reset_at = rate_limit_data.get("resetAt")
            if reset_at:
                self.reset_at = parse_gh_datetime(reset_at).timestamp()

    async def wait_if_needed(self):
        # Enforce minimum delay between requests
        elapsed = time.time() - self.last_request_at
        if elapsed < self.MIN_DELAY:
            await asyncio.sleep(self.MIN_DELAY - elapsed)

        # Pause if approaching rate limit
        if self.remaining <= self.SAFETY_THRESHOLD:
            wait = max(self.reset_at - time.time() + 2, 0)
            if wait > 0:
                print(f"    GraphQL rate limit low ({self.remaining} points left). Waiting {wait:.0f}s until reset...")
                await asyncio.sleep(wait)
                self.remaining = 5000  # reset after waiting


# ---------------------------------------------------------------------------
# GraphQL API call
# ---------------------------------------------------------------------------

async def graphql_query(client: httpx.AsyncClient, limiter: RateLimiter, query: str, variables: dict) -> dict:
    """Make a rate-limited GraphQL query. Returns the 'data' dict from the response."""
    await limiter.wait_if_needed()
    resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=HEADERS)

    if resp.status_code == 403:
        print(f"    403 response — backing off 60s...")
        await asyncio.sleep(60)
        resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=HEADERS)

    resp.raise_for_status()
    result = resp.json()

    # Check for GraphQL-level errors
    if "errors" in result:
        error_msgs = [e.get("message", str(e)) for e in result["errors"]]
        # Rate limit errors come as 200 with error messages
        if any("rate limit" in m.lower() for m in error_msgs):
            print(f"    GraphQL rate limit hit. Waiting 60s...")
            await asyncio.sleep(60)
            resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=HEADERS)
            resp.raise_for_status()
            result = resp.json()
            if "errors" in result:
                raise RuntimeError(f"GraphQL errors after retry: {result['errors']}")
        else:
            raise RuntimeError(f"GraphQL errors: {result['errors']}")

    data = result.get("data", {})
    limiter.update(data.get("rateLimit"))
    return data


# ---------------------------------------------------------------------------
# Response transformation: GraphQL -> REST-compatible format
# ---------------------------------------------------------------------------

def _get_login(author: dict | None) -> str:
    """Extract login from a GraphQL author field, handling deleted accounts."""
    if author and author.get("login"):
        return author["login"]
    return "ghost"


def transform_review(gql_review: dict) -> dict:
    """GraphQL review node -> REST /pulls/{n}/reviews format."""
    return {
        "user": {"login": _get_login(gql_review.get("author"))},
        "state": gql_review["state"],
        "submitted_at": gql_review.get("submittedAt"),
        "body": gql_review.get("body"),
    }


def transform_comment(gql_comment: dict) -> dict:
    """GraphQL comment node -> REST /issues/{n}/comments format."""
    return {
        "user": {"login": _get_login(gql_comment.get("author"))},
        "created_at": gql_comment.get("createdAt"),
        "body": gql_comment.get("body"),
    }


def transform_pr_node(node: dict) -> tuple[dict, dict]:
    """Transform a GraphQL PullRequest node into (metadata_dict, details_dict).

    metadata_dict: compatible with upsert_pr()
    details_dict: {reviews, review_comments, issue_comments} in REST format
    """
    labels = [n["name"] for n in node.get("labels", {}).get("nodes", [])]

    # GraphQL returns mergeable as enum (MERGEABLE/CONFLICTING/UNKNOWN),
    # REST returned boolean (true/false/null). Convert to match DB Boolean column.
    mergeable_raw = node.get("mergeable")
    if mergeable_raw == "MERGEABLE":
        mergeable = True
    elif mergeable_raw == "CONFLICTING":
        mergeable = False
    else:
        mergeable = None

    metadata = {
        "number": node["number"],
        "title": node["title"],
        "user": {"login": _get_login(node.get("author"))},
        "state": "closed" if node["state"] == "CLOSED" else "open",
        "merged_at": node.get("mergedAt"),
        "labels": [{"name": name} for name in labels],
        "mergeable": mergeable,
        "body": node.get("body"),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "closed_at": node.get("closedAt"),
    }

    reviews_data = node.get("reviews", {})
    review_threads_data = node.get("reviewThreads", {})
    comments_data = node.get("comments", {})

    reviews = [transform_review(r) for r in reviews_data.get("nodes", [])]

    # Flatten review thread comments into a single list
    review_comments = []
    for thread in review_threads_data.get("nodes", []):
        for comment in thread.get("comments", {}).get("nodes", []):
            review_comments.append(transform_comment(comment))

    issue_comments = [transform_comment(c) for c in comments_data.get("nodes", [])]

    details = {
        "reviews": reviews,
        "review_comments": review_comments,
        "issue_comments": issue_comments,
        # Carry pageInfo for pagination detection
        "_reviews_page_info": reviews_data.get("pageInfo", {}),
        "_review_threads_page_info": review_threads_data.get("pageInfo", {}),
        "_review_threads_nodes": review_threads_data.get("nodes", []),
        "_comments_page_info": comments_data.get("pageInfo", {}),
    }

    return metadata, details


def transform_issue_node(node: dict) -> tuple[dict, dict]:
    """Transform a GraphQL Issue node into (metadata_dict, details_dict).

    metadata_dict: compatible with upsert_issue()
    details_dict: {comments} in REST format
    """
    labels = [n["name"] for n in node.get("labels", {}).get("nodes", [])]
    comments_data = node.get("comments", {})

    metadata = {
        "number": node["number"],
        "title": node["title"],
        "user": {"login": _get_login(node.get("author"))},
        "state": node["state"].lower(),
        "labels": [{"name": name} for name in labels],
        "comments": len(comments_data.get("nodes", [])),
        "body": node.get("body"),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "closed_at": node.get("closedAt"),
    }

    details = {
        "comments": [transform_comment(c) for c in comments_data.get("nodes", [])],
        "_comments_page_info": comments_data.get("pageInfo", {}),
    }

    return metadata, details


# ---------------------------------------------------------------------------
# Paginated search + nested pagination
# ---------------------------------------------------------------------------

async def graphql_search(client: httpx.AsyncClient, limiter: RateLimiter,
                         owner: str, name: str, item_type: str,
                         since: datetime | None, list_only: bool) -> list[dict]:
    """Run a paginated GraphQL search, returning all matching nodes.

    item_type: "pr" or "issue"
    """
    q = f"repo:{owner}/{name} is:{item_type}"
    if since:
        # Format as ISO date for GitHub search
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        q += f" updated:>{since_str}"

    if item_type == "pr":
        query = PR_SEARCH_LIST_ONLY_QUERY if list_only else PR_SEARCH_QUERY
    else:
        query = ISSUE_SEARCH_LIST_ONLY_QUERY if list_only else ISSUE_SEARCH_QUERY

    all_nodes = []
    cursor = None
    page = 1

    while True:
        variables = {"q": q}
        if cursor:
            variables["cursor"] = cursor

        data = await graphql_query(client, limiter, query, variables)
        search = data.get("search", {})
        nodes = search.get("nodes", [])

        # Filter out empty nodes (can happen with union types)
        nodes = [n for n in nodes if n]
        all_nodes.extend(nodes)

        print(f"    {item_type.upper()} page {page}: {len(nodes)} items (total: {len(all_nodes)}, "
              f"cost: {data.get('rateLimit', {}).get('cost', '?')} pts, "
              f"remaining: {limiter.remaining} pts)")

        page_info = search.get("pageInfo", {})
        if page_info.get("hasNextPage") and page_info.get("endCursor"):
            cursor = page_info["endCursor"]
            page += 1
        else:
            break

    return all_nodes


async def paginate_pr_nested(client: httpx.AsyncClient, limiter: RateLimiter,
                             owner: str, name: str, pr_number: int,
                             details: dict) -> dict:
    """Fetch remaining pages for any truncated nested connections on a PR.

    Modifies and returns the details dict with complete data.
    """
    # Paginate reviews
    page_info = details.pop("_reviews_page_info", {})
    if page_info.get("hasNextPage"):
        print(f"      PR #{pr_number}: paginating reviews...")
        cursor = page_info["endCursor"]
        while True:
            data = await graphql_query(client, limiter, PR_REVIEWS_QUERY,
                {"owner": owner, "name": name, "number": pr_number, "cursor": cursor})
            reviews_conn = data["repository"]["pullRequest"]["reviews"]
            for node in reviews_conn.get("nodes", []):
                details["reviews"].append(transform_review(node))
            pi = reviews_conn.get("pageInfo", {})
            if pi.get("hasNextPage") and pi.get("endCursor"):
                cursor = pi["endCursor"]
            else:
                break

    # Paginate review threads (and their nested comments)
    threads_page_info = details.pop("_review_threads_page_info", {})
    thread_nodes = details.pop("_review_threads_nodes", [])

    # First, check if any existing thread had truncated comments
    for thread in thread_nodes:
        thread_comments_pi = thread.get("comments", {}).get("pageInfo", {})
        if thread_comments_pi.get("hasNextPage"):
            # Rare: a single review thread with >20 comments.
            # We'd need the thread ID to paginate, which we don't have in the current
            # query. Log it and accept the truncation — this is extremely uncommon.
            print(f"      PR #{pr_number}: review thread has >20 comments (truncated)")

    # Paginate to get more review threads if the connection was truncated
    if threads_page_info.get("hasNextPage"):
        print(f"      PR #{pr_number}: paginating review threads...")
        cursor = threads_page_info["endCursor"]
        while True:
            data = await graphql_query(client, limiter, PR_REVIEW_THREADS_QUERY,
                {"owner": owner, "name": name, "number": pr_number, "cursor": cursor})
            threads_conn = data["repository"]["pullRequest"]["reviewThreads"]
            for thread in threads_conn.get("nodes", []):
                for comment in thread.get("comments", {}).get("nodes", []):
                    details["review_comments"].append(transform_comment(comment))
            pi = threads_conn.get("pageInfo", {})
            if pi.get("hasNextPage") and pi.get("endCursor"):
                cursor = pi["endCursor"]
            else:
                break

    # Paginate issue comments (general discussion)
    page_info = details.pop("_comments_page_info", {})
    if page_info.get("hasNextPage"):
        print(f"      PR #{pr_number}: paginating comments...")
        cursor = page_info["endCursor"]
        while True:
            data = await graphql_query(client, limiter, PR_COMMENTS_QUERY,
                {"owner": owner, "name": name, "number": pr_number, "cursor": cursor})
            comments_conn = data["repository"]["pullRequest"]["comments"]
            for node in comments_conn.get("nodes", []):
                details["issue_comments"].append(transform_comment(node))
            pi = comments_conn.get("pageInfo", {})
            if pi.get("hasNextPage") and pi.get("endCursor"):
                cursor = pi["endCursor"]
            else:
                break

    return details


async def paginate_issue_nested(client: httpx.AsyncClient, limiter: RateLimiter,
                                owner: str, name: str, issue_number: int,
                                details: dict) -> dict:
    """Fetch remaining pages for truncated comment connections on an issue."""
    page_info = details.pop("_comments_page_info", {})
    if page_info.get("hasNextPage"):
        print(f"      Issue #{issue_number}: paginating comments...")
        cursor = page_info["endCursor"]
        while True:
            data = await graphql_query(client, limiter, ISSUE_COMMENTS_QUERY,
                {"owner": owner, "name": name, "number": issue_number, "cursor": cursor})
            comments_conn = data["repository"]["issue"]["comments"]
            for node in comments_conn.get("nodes", []):
                details["comments"].append(transform_comment(node))
            pi = comments_conn.get("pageInfo", {})
            if pi.get("hasNextPage") and pi.get("endCursor"):
                cursor = pi["endCursor"]
            else:
                break

    return details


# ---------------------------------------------------------------------------
# DB upsert functions
# ---------------------------------------------------------------------------

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


async def upsert_pr_details(session, repo: str, pr_number: int, details: dict):
    """Update review/comment JSON and detail_fetched_at for a PR."""
    result = await session.execute(
        select(GithubPR).where(GithubPR.repo == repo, GithubPR.number == pr_number)
    )
    pr = result.scalar_one()
    pr.reviews_json = details["reviews"]
    pr.review_comments_json = details["review_comments"]
    pr.issue_comments_json = details["issue_comments"]
    pr.detail_fetched_at = datetime.now(timezone.utc)


async def upsert_issue_details(session, repo: str, issue_number: int, details: dict):
    """Update comments JSON and detail_fetched_at for an issue."""
    result = await session.execute(
        select(GithubIssue).where(GithubIssue.repo == repo, GithubIssue.number == issue_number)
    )
    issue = result.scalar_one()
    issue.comments_json = details["comments"]
    issue.detail_fetched_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Since-date logic
# ---------------------------------------------------------------------------

async def get_last_updated(repo: str) -> datetime | None:
    """Get the most recent updated_at_gh across PRs and issues for a repo."""
    async with async_session() as session:
        pr_max = await session.execute(
            select(func.max(GithubPR.updated_at_gh)).where(GithubPR.repo == repo)
        )
        issue_max = await session.execute(
            select(func.max(GithubIssue.updated_at_gh)).where(GithubIssue.repo == repo)
        )
        dates = [d for d in [pr_max.scalar(), issue_max.scalar()] if d]
        return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

async def scan_repo(client: httpx.AsyncClient, limiter: RateLimiter,
                    owner: str, name: str, list_only: bool) -> dict:
    """Scan a single repo using GraphQL. Returns stats dict."""
    repo = f"{owner}/{name}"
    since = await get_last_updated(repo)

    if since:
        print(f"  Incremental scan: items updated since {since.isoformat()}")
    else:
        print(f"  Full scan: no prior data")

    # Fetch PRs
    print(f"  Fetching PRs...")
    pr_nodes = await graphql_search(client, limiter, owner, name, "pr", since, list_only)

    async with async_session() as session:
        for node in pr_nodes:
            metadata, details = transform_pr_node(node)
            await upsert_pr(session, repo, metadata)

            if not list_only:
                details = await paginate_pr_nested(client, limiter, owner, name, metadata["number"], details)
                await upsert_pr_details(session, repo, metadata["number"], details)

        await session.commit()

    # Fetch issues
    print(f"  Fetching issues...")
    issue_nodes = await graphql_search(client, limiter, owner, name, "issue", since, list_only)

    async with async_session() as session:
        for node in issue_nodes:
            metadata, details = transform_issue_node(node)
            await upsert_issue(session, repo, metadata)

            if not list_only:
                details = await paginate_issue_nested(client, limiter, owner, name, metadata["number"], details)
                await upsert_issue_details(session, repo, metadata["number"], details)

        await session.commit()

    stats = {
        "repo": repo,
        "prs": len(pr_nodes),
        "issues": len(issue_nodes),
    }

    if since:
        print(f"  Done: {len(pr_nodes)} PRs, {len(issue_nodes)} issues updated since last scan.")
    else:
        print(f"  Done: {len(pr_nodes)} PRs, {len(issue_nodes)} issues (full scan).")

    return stats


async def main():
    config = load_project_config()

    # Parse args
    list_only = "--list-only" in sys.argv
    target = None
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--config-dir":
            skip_next = True
            continue
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

    # Sync governance config from YAML to DB
    async with async_session() as session:
        config_entries = {
            "merge_authorities": [
                {"login": ma.login, "scope": ma.scope, "description": ma.description}
                for ma in config.merge_authorities
            ],
        }
        for key, value in config_entries.items():
            result = await session.execute(
                select(ProjectConfigEntry).where(ProjectConfigEntry.key == key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(ProjectConfigEntry(key=key, value=value))
        await session.commit()

    run_start = datetime.now(timezone.utc)
    limiter = RateLimiter()
    stats = []

    print(f"GitHub scan (GraphQL) for {len(repos)} repo(s): {config.display_name}")
    print(f"Mode: {'list only' if list_only else 'full (metadata + details)'}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for repo in repos:
            print(f"\n  [{repo.full_name}]")
            try:
                repo_stats = await scan_repo(client, limiter, repo.owner, repo.name, list_only)
                stats.append(repo_stats)
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
    print(f"\nDone in {elapsed}. {limiter.total_requests} GraphQL queries, {limiter.total_points} points used.")
    for s in stats:
        print(f"  {s}")


if __name__ == "__main__":
    asyncio.run(main())
