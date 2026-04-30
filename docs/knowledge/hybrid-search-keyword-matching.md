# Hybrid Search — What's Findable and Why

The search pipeline in `project_lens/api/search.py` is a two-signal hybrid:
embedding cosine similarity from ChromaDB, plus a rarity-weighted keyword
boost applied as an additive re-rank on the top-`max(n*10, 50)` embedding
hits. Two properties of this pipeline interact in ways that can surprise a
user who types an "obvious" keyword and gets no result.

## 1. Only the embedded text is searchable

ChromaDB stores a single text blob per document, built by
`scripts/embed_digests.py::fetch_pr_assessment_texts` (and the issue/telegram
counterparts). The blob is what the embedding model sees and also what the
keyword boost scans. Anything omitted from that blob is invisible to search,
full stop.

Historically the PR blob was built from `PRAssessment` fields only — summary,
recommendation, outcome.impact, lessons_learned — and **did not include
`GithubPR.title`**. Same gap on the issue side. This meant a PR titled
"GITHUB_TOKEN permissions hardening" could be embedded with a blob whose
narrative said only "kdmukai hardens all CI workflows" — the AI's rephrasing
— and the literal word "hardening" never reached the index.

Fix: both `fetch_pr_assessment_texts` and `fetch_issue_assessment_texts` now
join the GitHub title and prepend a `Title: <title>` line to the blob. Any
change to the blob content flips `content_hash`, so the next
`embed_digests.py` run incrementally re-embeds every PR/issue doc; no
`--rebuild` needed.

## 2. The keyword boost is substring match, not stemming

In `_search_collection_sync`:

```python
query_terms = [t.lower() for t in query.split() if len(t) >= 3]
# ...
for term in query_terms:
    if term in doc_lower and term_doc_counts[term] > 0:
        keyword_boost += 1.0 / term_doc_counts[term]
```

A query term matches only if it is a **literal substring** of the document
text. Implications that matter:

- `"harden"` matches `"hardens"`, `"hardened"`, `"hardening"`.
- `"hardening"` does **not** match `"hardens"`. Morphology bites here.
- Tokens shorter than 3 characters are dropped before boosting (`"ci"`,
  `"to"`, etc.). Acronyms of 2 chars never contribute to the boost even
  when they're the most specific thing in the query.
- There is no stopword filter. A three-letter filler word that happens to
  appear in few docs can contribute a misleadingly large rarity boost.

Rarity is per-term, per-collection: `1 / (number of docs in this collection
containing this term as a substring)`. Boost is additive across terms, so a
rare-rare two-term query can push a hit from ~0.5 cosine similarity to >1.0.
The cosine+boost sum is not normalized; "higher is better within a single
query" is the only meaningful ordering.

## 3. Embeddings gate what can be boosted

ChromaDB returns only the top `max(n_results * 10, 50)` by embedding
similarity. The keyword boost re-ranks within that candidate set. If a
document's embedding similarity for the query is poor enough that it falls
past rank 50 before boosting, it will never appear in the results even if
its text contains a perfectly unique keyword match.

This is the compound failure mode seen on PR 926's "hardening" query:

- Title wasn't embedded → "hardening" wasn't a substring of the blob →
  no keyword boost would have fired even if the embedding surfaced it.
- Narrative was dominated by CI/GITHUB_TOKEN/supply-chain vocabulary →
  the `BAAI/bge-large-en-v1.5` model ranked the doc below the top-50
  candidates for the single-word query "hardening" → no chance to boost.

Adding the title fixes both axes simultaneously: it ships "hardening" into
the boost's substring space, and it gives the embedding a high-signal
token that's more central to the query.

## 4. Full-collection scan on every search

`term_doc_counts` is computed by `collection.get(include=["documents"])`,
which pulls every document text into Python memory per query, then scans
for each term. Fine at the current scale (thousands of docs) but is a hot
spot to watch if the corpus grows by a couple of orders of magnitude.

## Takeaways for future changes

- If you change what goes into the blob in `embed_digests.py`, all docs
  get re-embedded on the next run because `content_hash` is derived from
  the blob. This is cheap to trigger but does mean re-running the
  embedding model across the whole corpus.
- If you want `"hardens" == "hardening"` to work at the keyword layer,
  the natural place is to stem both sides in `_search_collection_sync`
  before substring-matching. This changes re-ranking only; the
  embeddings are unaffected.
- The ChromaDB keyword boost is a rough-and-ready mechanism. For true
  lexical recall against PR bodies, issue bodies, comments, or commit
  messages — none of which are in ChromaDB at all — a separate SQLite
  FTS5 or `LIKE` pathway would be required, merged with the ChromaDB
  results at query time.
