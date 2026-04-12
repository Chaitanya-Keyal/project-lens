"""Semantic search service — hybrid embedding similarity + keyword rarity boost."""

import asyncio
from dataclasses import dataclass, field

from project_lens.api.config import LensConfig

# Collection name → source_type in results
COLLECTION_SOURCE_TYPES = {
    "pr_assessments": "pr_assessment",
    "telegram_threads": "telegram_thread",
    "issue_assessments": "issue_assessment",
}

ALL_COLLECTIONS = list(COLLECTION_SOURCE_TYPES.keys())


@dataclass
class SearchResult:
    """A single search result from hybrid search."""
    source_type: str
    score: float
    text: str
    id: str
    metadata: dict = field(default_factory=dict)


class SearchService:
    """Owns the hybrid ranking algorithm, embedding model, and ChromaDB access."""

    def __init__(self, config: LensConfig):
        self._config = config
        self._client = None
        self._ef = None

    def _get_client(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(
                path=str(self._config.chroma_path),
            )
        return self._client

    def _get_ef(self):
        if self._ef is None:
            gpu_url = getattr(self._config, "gpu_service_url", None)
            if gpu_url:
                try:
                    from gpu_client import ServiceEmbeddingFunction

                    self._ef = ServiceEmbeddingFunction(
                        service_url=gpu_url,
                        model_name=self._config.embedding_model,
                    )
                    return self._ef
                except ImportError:
                    pass

            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )
            device = "cpu"
            try:
                import torch
                if torch.cuda.is_available():
                    device = "cuda"
            except ImportError:
                pass
            self._ef = SentenceTransformerEmbeddingFunction(
                model_name=self._config.embedding_model,
                device=device,
            )
        return self._ef

    def _get_collection(self, name: str):
        return self._get_client().get_or_create_collection(
            name=name,
            embedding_function=self._get_ef(),
            metadata={"hnsw:space": "cosine"},
        )

    def _search_collection_sync(
        self, collection_name: str, query: str, n_results: int, query_terms: list[str],
    ) -> list[SearchResult]:
        """Sync hybrid search on a single collection."""
        try:
            collection = self._get_collection(collection_name)
            if collection.count() == 0:
                return []

            # Over-fetch so keyword boost can re-rank effectively
            fetch_count = min(max(n_results * 10, 50), collection.count())
            results = collection.query(
                query_texts=[query],
                n_results=fetch_count,
                include=["documents", "metadatas", "distances"],
            )

            # Count how many documents contain each query term (for rarity weighting)
            term_doc_counts: dict[str, int] = {}
            if query_terms:
                all_docs = collection.get(include=["documents"])
                for term in query_terms:
                    term_doc_counts[term] = sum(
                        1 for doc in all_docs["documents"] if term in doc.lower()
                    )

            source_type = COLLECTION_SOURCE_TYPES.get(collection_name, collection_name)
            scored = []
            for doc_id, doc, meta, dist in zip(
                results["ids"][0],
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                embedding_score = 1 - dist  # cosine distance → similarity

                # Keyword boost: rare terms boost strongly (1/hit_count)
                keyword_boost = 0.0
                doc_lower = doc.lower()
                for term in query_terms:
                    if term in doc_lower and term_doc_counts.get(term, 0) > 0:
                        keyword_boost += 1.0 / term_doc_counts[term]

                # Strip internal metadata from results
                clean_meta = {
                    k: v for k, v in (meta or {}).items() if k != "content_hash"
                }

                scored.append(SearchResult(
                    source_type=source_type,
                    score=embedding_score + keyword_boost,
                    text=doc,
                    id=doc_id,
                    metadata=clean_meta,
                ))

            return scored
        except Exception:
            return []

    async def search(
        self,
        query: str,
        n_results: int = 15,
        collections: list[str] | None = None,
    ) -> list[SearchResult]:
        """Hybrid search across embedded collections.

        Combines embedding similarity with keyword rarity boost, merges
        results across collections, and returns the top n_results.
        """
        target_collections = collections or ALL_COLLECTIONS
        query_terms = [t.lower() for t in query.split() if len(t) >= 3]

        all_results: list[SearchResult] = []
        for name in target_collections:
            results = await asyncio.to_thread(
                self._search_collection_sync, name, query, n_results, query_terms,
            )
            all_results.extend(results)

        all_results.sort(key=lambda r: -r.score)
        return all_results[:n_results]

    async def search_related(
        self,
        query: str,
        exclude_id: str | None = None,
        n_results: int = 5,
    ) -> list[SearchResult]:
        """Find related context, optionally excluding one document."""
        fetch = n_results + (1 if exclude_id else 0)
        results = await self.search(query, n_results=fetch)
        if exclude_id:
            results = [r for r in results if r.id != exclude_id]
        return results[:n_results]

    async def get_document(
        self, collection: str, doc_id: str,
    ) -> SearchResult | None:
        """Fetch a single document by its stable ID."""
        def _fetch():
            try:
                coll = self._get_collection(collection)
                result = coll.get(
                    ids=[doc_id],
                    include=["documents", "metadatas"],
                )
                if not result["ids"]:
                    return None
                source_type = COLLECTION_SOURCE_TYPES.get(collection, collection)
                meta = result["metadatas"][0] or {}
                return SearchResult(
                    source_type=source_type,
                    score=1.0,
                    text=result["documents"][0],
                    id=doc_id,
                    metadata={k: v for k, v in meta.items() if k != "content_hash"},
                )
            except Exception:
                return None

        return await asyncio.to_thread(_fetch)
