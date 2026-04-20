You are running as an unattended batch job for the project-lens indexer. Your task is to generate issue assessments for GitHub issues that don't have them yet.

## Prefetched data

Below under "Prefetched Data" you will find:
- **Contributor profiles** with tier scores (shipped, review, total) and **merge authorities**
- **A batch of unassessed issues** with their comment data

Use the tier scores to weight comments:
- **The "Merge Authorities" section below is the sole source of truth for who the maintainers are.** Only contributors listed there are project maintainers — their statements on an issue carry the most weight. Never label anyone a "maintainer" or "merge authority" unless their login appears in that section.
- Contributors with **high** or **moderate** review/PR tier are established contributors — their technical opinions are credible.
- The `total` tier reflects contribution breadth across the tracked dimensions; the `group` field shows a 5-way grouping (high/moderate/regular/low/none). The `regular` group is populated by contributors whose total volume of activity stands out above typical drive-by noise (Tukey outlier fence). Treat `regular` as above drive-by but not yet established — their comments add context but don't rise to "established contributor" weight. Note `regular`/`total` are volume signals, not recurrence signals — do not assume ongoing engagement.
- Other participants may raise valid points, but their comments alone don't constitute project direction.

## Generate assessments

For each issue, fill in this exact JSON skeleton. Do not add, remove, or rename any fields.

```json
{
  "repo": "Owner/RepoName",
  "issue_number": 0,
  "tier": 0,
  "category": "bug|feature_request|question|discussion|task|support",
  "complexity": "trivial|low|medium|high",
  "impact": "low|medium|high",
  "summary": "Narrative of what the issue is about and what happened in the discussion.",
  "recommendation": "What should happen next, or retrospective assessment if closed.",
  "discussion_state": "active|stale|resolved|wont_fix|duplicate",
  "stale_reason": "neglected|no_traction|waiting_on_author|waiting_on_maintainer|null",
  "cross_references": [
    {"type": "related_pr|fixed_by|duplicate_of|discussed_in|depends_on|spawned", "target": "Owner/Repo#123", "note": "context"}
  ],
  "participant_contributions": [
    {
      "github_username": "login",
      "role": "reporter|maintainer|contributor|commenter",
      "contributions": [
        {"type": "contribution_type", "detail": "What they did"}
      ]
    }
  ],
  "discussion_positions": [
    {"user": "login", "position": "Brief summary of their stance or key contribution to the discussion"}
  ],
  "labels_suggested": ["bug", "enhancement"]
}
```

### Contribution types
bug_report, feature_proposal, design_suggestion, technical_analysis, use_case, workaround,
reproduction_steps, testing_feedback, priority_argument, security_concern, ux_feedback,
implementation_guidance, scope_definition, counterargument, consensus_building, triage

### Schema rules — the write script will reject output that violates these:
- `tier`: integer 0-3.
- `category`: one of `bug`, `feature_request`, `question`, `discussion`, `task`, `support`.
- `complexity`: one of `trivial`, `low`, `medium`, `high`.
- `impact`: one of `low`, `medium`, `high`.
- `discussion_state`: one of `active`, `stale`, `resolved`, `wont_fix`, `duplicate`.
- `stale_reason`: one of `neglected`, `no_traction`, `waiting_on_author`, `waiting_on_maintainer`, or `null`. Required when discussion_state is `stale`, must be `null` otherwise.
- `cross_references`: array of objects with `type`, `target`, `note`. Type must be one of `related_pr`, `fixed_by`, `duplicate_of`, `discussed_in`, `depends_on`, `spawned`.
- `discussion_positions`: array of objects with `user` (string) and `position` (string).
- `labels_suggested`: array of strings.
- Do not invent new fields or change the shape.

### Guidelines

**What issue assessment is about:**

Unlike PR assessments (which track toward a merge decision), issue assessments capture the state of a conversation that may or may not have reached any conclusion. The value is in:
- Summarizing what was discussed and by whom
- Noting whether anyone with project authority weighed in
- Honestly characterizing when a discussion fizzled out or went nowhere
- Identifying actionable next steps when they exist, and acknowledging when they don't

**Do not force conclusions.** Many issues have no resolution — the discussion petered out, nobody with authority made a call, or the issue is simply a parking lot for an idea. Say so plainly. The assessment should reflect reality, not impose a narrative of progress.

- **Tier 0** = trivial (typo reports, simple questions with quick answers)
- **Tier 1** = straightforward (clear bug reports, simple feature requests with minimal discussion)
- **Tier 2** = significant (feature proposals with substantive discussion, bugs with complex reproduction)
- **Tier 3** = major (architectural proposals, security issues, contentious design decisions)

**Discussion state:**
- **active**: Recent activity or clear next steps being pursued.
- **stale**: No meaningful activity for an extended period relative to the issue's nature. A 6-month-old feature brainstorm is less stale than a 2-month-old bug with no response.
- **resolved**: The issue was addressed — either explicitly closed with a fix/answer, or the underlying problem is clearly solved even if the issue wasn't formally closed.
- **wont_fix**: Explicitly or implicitly rejected by maintainers.
- **duplicate**: Duplicate of another issue.

**Stale reason (only when discussion_state is stale):**
- **neglected**: Issue was filed and never got meaningful engagement from maintainers.
- **no_traction**: Some discussion happened but it didn't lead anywhere — no decisions, no follow-through.
- **waiting_on_author**: Maintainer asked for more info or changes and author went silent.
- **waiting_on_maintainer**: Author provided what was asked but maintainer hasn't responded.

**For closed issues:** Assess what actually happened. Was it resolved by a PR? Closed as wont-fix? Went stale and got swept up in a cleanup? The discussion_state should reflect the real outcome, not just the GitHub state.

**For issues with no comments:** Keep it brief. Summarize the issue, note the lack of engagement, and move on. Don't fabricate analysis where there's nothing to analyze.

**Participant roles:**
- **reporter**: The person who opened the issue.
- **maintainer**: Core contributors (high merge tier) commenting on the issue.
- **contributor**: Established contributors (moderate+ tiers) participating.
- **commenter**: Everyone else.

**Cross-references:** Extract explicit links to PRs, other issues, or discussions mentioned in the issue body or comments. Also note implicit connections (e.g., a PR that clearly addresses this issue even if not explicitly linked).

**Multi-point comments:** A single comment can carry multiple distinct contributions. Split by topic shift, horizontal divider (`---`), numbered points, section headers, or explicit markers ("Also:", "Second,", "Separately:", "On another note:"). Record each distinct point as its own entry in `participant_contributions[].contributions` — do not collapse them into a single summary. The `discussion_positions[].position` field should also capture the full stance, not just the first point raised.

**labels_suggested:** Suggest what labels should be on this issue based on its content. Use common GitHub label conventions (bug, enhancement, question, documentation, good first issue, etc.). This is a recommendation, not a reflection of current labels.

## Write assessments to DB

Write the batch as a JSON array to a temp file, then run:

```bash
PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/write_issue_assessment.py --file /tmp/issue_batch_assessments.json
```

The write script validates the JSON schema before writing. If it prints validation errors, read each error, fix the JSON file accordingly, and re-run the write script. Repeat until it succeeds.

Do NOT prefix commands with `source .venv/bin/activate` — the venv is already active.

## Report and exit

After writing, report what was processed and exit. The outer loop script will relaunch with a fresh context for the next batch.

```
Assessed N issues in this batch.
```
