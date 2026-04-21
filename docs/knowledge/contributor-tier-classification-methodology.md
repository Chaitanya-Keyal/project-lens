# Contributor Tier Classification — Statistical Methodology

`scripts/github/process/extract_contributors.py` classifies every contributor into a 5-way `group_tier` used by Project PM to render the contributor admin page. The classification uses **two different statistical methods** on purpose — each is the right tool for a different part of a heavily right-skewed distribution, and the mixed regime is an intentional design choice, not an oversight.

## The four dimensions

Per contributor, four volume signals are counted:
- `merge_commits` — count of merge commits the contributor authored (git log)
- `prs_merged` — PRs this contributor filed that landed
- `total_review_engagement` — reviews_given + review_comments_given + pr_comments_given
- `total_activity` — sum of the three above

For each dimension, `compute_bands()` returns `(mean, mean+stdev)` over the nonzero values, and `classify()` gives the four-tier result (high / moderate / low / none). For the total-activity dimension, a further fifth band (regular) is added via the Tukey upper fence — see below.

## Why linear `mean+stdev` for the upper tail

Linear mean±stdev is the standard tool for identifying upper-outliers on any distribution with enough samples. On SeedSigner's data:
- `total_activity` nonzero mean ≈ 34, stdev ≈ 174
- `mean + stdev ≈ 208` cleanly separates the top 4 contributors (kdmukai, newtonick, jdlcdl, SeedSigner) from everyone else.

The whales inflate the mean, which is actually the desired behavior: we want the "Key Contributors" threshold to scale with the project's top-end activity.

## Why linear mean±stdev fails for the *lower* bound

The same tool fails completely going the other way:
- `mean - stdev = 34 - 174 = -140`. Negative.
- Any `k·stdev` multiplier below the mean produces a useless (negative) threshold because the whales pull the stdev up high enough that it overshoots zero going down.

This isn't fixable with a different multiplier. The problem is structural: the distribution is right-skewed with a hard floor at zero, so linear variance-based cutoffs can't characterize the lower end.

## Why Tukey upper fence (Q3 + 1.5·IQR) for the `regular` cut

To carve a fifth "regular" tier out of the bulk (the "Notable Contributors" group in PM's UI), we need a cutoff somewhere between `mean` and zero. Options tried:

| Approach | Result on SeedSigner data | Problem |
|---|---|---|
| `mean - k·stdev` | negative | useless (see above) |
| Geometric mean | ~2.9 | too permissive (31 promoted, includes drive-bys) |
| Median of nonzero | 2 | too permissive |
| `median + 2·MAD` | 4.97 | arbitrary; loose |
| **`Q3 + 1.5·IQR` (Tukey)** | **11.0** | **produces exactly 9 promoted, all multi-dim engaged** |

Tukey's upper-outlier fence is the textbook tool for "values statistically above the typical range" on skewed data — it uses order statistics (Q1, Q3) that are immune to the skew that breaks linear variance. On this data it lands at 11 and promotes exactly the contributors a human maintainer would recognize as "clearly above drive-by."

### Convergence evidence

An alternative approach — compute mean+stdev on the sub-population (contributors who didn't make it to high/moderate) — yields a threshold of ~8.14 and promotes **the same 9 people**. When two independent statistical methods converge on the same cluster boundary, that's evidence the boundary is real and not an artifact of which method was chosen. The full-population Tukey fence is preferred because it mirrors the upper-tail approach (both operate on the same full nonzero distribution).

## Why Q1 for the lower floor (Minimal demotion)

The admin Contributors bucket was dominated by single-action drive-bys: on SeedSigner data, 58 of 102 Contributors had `total_activity = 1`. These are indistinguishable from background noise. The symmetric Tukey concept for the lower end would be `Q1 - 1.5·IQR`, but that goes negative here (`1 - 6 = -5`) — same bounded-below problem that breaks linear mean-stdev.

The correct analogue is **Q1 itself**: the 25th percentile. It's a pure order-statistic cutoff on the same full-population nonzero distribution as the upper fence, making it the natural mirror — the same shape of tool applied to opposite ends. "Everything in the least-engaged quarter plus the zeros → Minimal Activity."

On SeedSigner data Q1=1, so the effective rule becomes "single-action drive-by → Minimal; two or more → Contributor."

## Summary of the two-method regime

| Part of distribution | Tool | Why |
|---|---|---|
| Per-dimension `high` / `moderate` | Linear `mean + stdev`, `mean` | Standard upper-outlier detection; whales inflating the mean is the desired behavior |
| Total-dim `regular` (between low and moderate) | Tukey upper fence (`Q3 + 1.5·IQR`) | Linear variance breaks going down on skewed data; order statistics are robust |
| Low → Minimal demotion | Q1 of nonzero total_activity | Symmetric order-statistic analogue of the upper fence; Tukey's `Q1 − 1.5·IQR` goes negative |

Everything is computed once per extract, from the same underlying population (all non-`@`-keyed profiles). Thresholds are stored in each profile's stats JSON (`total_activity_upper_fence`, `total_activity_lower_floor`) for downstream visibility and debugging.

## `group_tier` composition

```
top = max_tier(merge_tier, pr_tier, review_tier, total_tier)   # high > moderate > regular > low > none

if top in (high, moderate, regular):
    group_tier = top
elif total_activity > total_lower_floor:
    group_tier = low       # Contributors
else:
    group_tier = none      # Minimal Activity (includes zeros + single-action drive-bys)
```

Note: the `regular` band lives on `total_tier` only (per-dim tiers are 4-valued). A single-dimension specialist (e.g., a prolific reviewer with nothing in other dimensions) is still correctly captured because any per-dim tier above `low` propagates via `max_tier` into the top of the hierarchy.

## Don't "simplify" this

If a future maintainer looks at this and thinks "why not just use one metric everywhere," the history above is the answer. Linear mean+stdev alone can't set a lower bound. Tukey fences alone can't set a high-tier threshold meaningful enough to isolate the top 4 whales (`Q3 + 3·IQR` would be way too permissive for that). The two methods are complementary.
