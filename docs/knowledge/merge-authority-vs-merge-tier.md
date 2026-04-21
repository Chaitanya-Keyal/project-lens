# Merge Authority ≠ Merge Tier

`merge_tier` is a statistical classification computed from the count of merge commits a contributor has authored in git history. **It is not a signal of current merge authority.** The two are distinct and must not be conflated in prompts, UI, or downstream logic.

## The bug this document prevents

At one point the AI assessment prompts said:

> Contributors with **high** merge tier are merge authorities — their ACK carries the most weight.

The model correctly followed this instruction, which produced assessments like:

- "Self-ACKed by kdmukai who is a merge authority"
- "From kdmukai (merge authority) but no explicit ACK"

kdmukai is **not** a merge authority for the SeedSigner project. He does have a `merge_tier=high` — 135 historical merge commits — but that stat is inflated by a legacy circumstance: he originally created several repositories (some of the auxiliary SeedSigner repos) and performed merges on them while repo-owner, before transferring ownership to the SeedSigner organization. The merge-commit authorship in the git log is accurate historical data; it is not an operational authorization signal.

## Source of truth

Current merge authority is defined **only** by the `merge_authorities` block in the project's `config.yaml`:

```yaml
merge_authorities:
  - login: newtonick
    scope: technical
    description: "..."
  - login: SeedSigner
    scope: non-technical
    description: "..."
```

This list is persisted in the `project_config` table (key `merge_authorities`) when the project config is loaded, and injected into AI prompt context by `scripts/shared/fetch_contributor_context.py` when called with `include_merge_authorities=True`. The prefetch scripts (`fetch_unassessed_prs.py`, `fetch_unassessed_issues.py`) always pass that flag.

## Why merge_tier was removed from the AI prompt context

Originally `merge_tier` (and the raw `merge_commits` count) was included in the per-contributor line emitted by `fetch_contributor_context.py`. The prompt had to explicitly warn the model that merge_tier was a historical signal and not authority — essentially telling the model to ignore a field we had chosen to include.

When the only purpose of including a field is to tell the model not to draw conclusions from it, it's cleaner to not include the field at all. `merge_tier` was removed from the emitted context; the explanatory paragraph in the prompt was deleted; the `Merge Authorities` block remains (unchanged) as the sole authority signal.

## merge_tier is still stored

The stat is still computed and stored in `ContributorProfile.stats` because:
- The `extract_contributors.py --show` summary uses it for debug display
- It may be useful for other future analyses (e.g., "who has actually pressed the merge button in this repo's history" is a legitimate question for certain provenance investigations)
- Removing it would be a bigger, unnecessary change

Just don't surface it in AI prompt context, and don't let any UI or logic treat it as an authority signal.

## Rule for future changes

- **The `merge_authorities` config section is the sole source of truth** for who can merge. If you need to know whether someone is a maintainer, check that list — nothing else.
- **Never add `merge_tier` back to AI prompt context** without a concrete non-authority use case. If something else would benefit from this signal, think hard about whether it's actually going to get interpreted as authority by the model.
- **Other tiers** (`pr_tier`, `review_tier`, `total_tier`, `group_tier`) are fine in prompts — they represent volume/experience, not authority, and none of them have the same legacy-inflation problem that `merge_tier` has.
