# GitHub GraphQL Migration Notes

## Why GraphQL over REST

The REST approach had two phases:
- **Phase A**: List all PRs/issues (paginated, ~4-5 calls per repo) — fetched everything every run
- **Phase B**: Fetch reviews/comments per changed item (3 calls per PR, 1 per issue)

GraphQL solves both problems:
1. **Date filtering** via search query syntax (`updated:>YYYY-MM-DD`) — only fetch items changed since last scan
2. **Nested data** — PR metadata + reviews + comments in a single request

Result: ~14 queries for 7 repos (2 per repo) vs dozens of REST calls.

## Rate Limit Cost Model

GraphQL uses points (5,000/hour), not request count. Cost is calculated from the `first`/`last` arguments on connections, **multiplied through nesting levels**.

The `reviewThreads` nesting is the cost driver:
```
search(first: 100) × reviewThreads(first: 60) × comments(first: 20) = 120,000 nodes
```

Max cost per PR search query: ~1,482 points. However, GitHub charges **actual cost** based on nodes returned, not the theoretical max. An incremental query returning 2 PRs costs ~30 points. A typical 7-repo incremental scan uses ~460 points total.

For a fresh full ingest, the rate limiter watches the `rateLimit.remaining` field from each response and pauses when it drops below 500 points.

## Gotcha: `mergeable` Field Type Mismatch

REST API returns `mergeable` as a boolean (`true`/`false`/`null`). GraphQL returns an enum:
- `MERGEABLE` → `True`
- `CONFLICTING` → `False`
- `UNKNOWN` → `None`

The DB column is `Boolean`, so the transformation layer must map the enum.

## JSON Format Compatibility

GraphQL responses use camelCase and a different author structure. The transformation layer maps to the REST format that downstream code expects:
- `author { login }` → `{"user": {"login": ...}}`
- `submittedAt` → `submitted_at`
- `createdAt` → `created_at`

Downstream consumers (`fetch_unassessed_prs.py`, `extract_contributors.py`) access only these fields and are unaffected by the loss of extra REST fields (id, node_id, avatar_url, etc.).
