# ChromaDB Embedding Function Name Compatibility

## Problem
ChromaDB persists the embedding function name in collection metadata and enforces it on `get_or_create_collection`. If you create a collection with one EF name (e.g., `"gpu_service"`) and later try to open it with a different one (e.g., `"sentence_transformer"`), ChromaDB raises a `ValueError` — even if both produce identical vectors from the same model.

## Root Cause
The `ServiceEmbeddingFunction` in `gpu-client` originally reported its name as `"gpu_service"`. This leaked an infrastructure detail (how embeddings are computed) into the persisted data. When the gpu-service was unavailable and code fell back to `SentenceTransformerEmbeddingFunction`, the name mismatch caused collection access to fail.

## Solution
`ServiceEmbeddingFunction.name()` now returns `"sentence_transformer"` — matching what `SentenceTransformerEmbeddingFunction` reports. Since both produce identical embeddings from the same model, this is semantically correct. The gpu-service is a transport optimization, not a different embedding function.

## Implications
- Collections created via gpu-service are fully compatible with plain `SentenceTransformerEmbeddingFunction`
- ChromaDB data can be shared with developers who don't have a GPU
- The `--rebuild` flag in `embed_digests.py` deletes the collection before recreating it (not just the documents) to handle any legacy name mismatches
- Any new embedding function wrapper must report the same `name()` as the underlying model's EF to maintain collection portability
