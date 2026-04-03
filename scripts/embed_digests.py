#!/usr/bin/env python3
"""Embed AI digests into ChromaDB for semantic search.

Reads digested content from the ai_digests table and embeds it into
ChromaDB collections, organized by source type. Uses content hashing
to skip unchanged digests — only new or modified content gets embedded.

Collections:
  - telegram_threads: discussion thread summaries from Telegram digestion
  - pr_assessments: PR assessment summaries + recommendations
  (more to come: github_issues, contributors, etc.)

Usage:
    python scripts/embed_digests.py                # embed all new/changed digests
    python scripts/embed_digests.py --stats        # show collection stats
    python scripts/embed_digests.py --search "query text"  # test search
    python scripts/embed_digests.py --rebuild      # delete and re-embed everything
"""
import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.database import async_session
from backend.models import AIDigest, PRAssessment


# ---------------------------------------------------------------------------
# ChromaDB setup
# ---------------------------------------------------------------------------

def get_chroma_client():
    """Create a persistent ChromaDB client."""
    import chromadb
    return chromadb.PersistentClient(path=str(settings.chroma_path))


def get_embedding_function():
    """Create embedding function with auto GPU detection."""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    device = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            name = torch.cuda.get_device_name(0)
            print(f"Using GPU: {name}")
    except ImportError:
        pass

    if device == "cpu":
        print("Using CPU for embeddings")

    return SentenceTransformerEmbeddingFunction(
        model_name=settings.embedding_model,
        device=device,
    )


def get_collection(client, name: str, ef):
    """Get or create a ChromaDB collection."""
    return client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Source type → collection mapping
# ---------------------------------------------------------------------------

COLLECTION_MAP = {
    "telegram_thread": "telegram_threads",
    "github_pr": "pr_assessments",
}


# ---------------------------------------------------------------------------
# Embed digests
# ---------------------------------------------------------------------------

async def fetch_digests(source_type: str | None = None) -> list[dict]:
    """Fetch all AI digests, optionally filtered by source type."""
    async with async_session() as session:
        query = select(AIDigest)
        if source_type:
            query = query.where(AIDigest.source_type == source_type)
        result = await session.execute(query.order_by(AIDigest.created_at))
        digests = result.scalars().all()

        return [{
            "id": d.id,
            "source_type": d.source_type,
            "source_ref": d.source_ref,
            "digest_type": d.digest_type,
            "content_hash": d.content_hash,
            "digest_text": d.digest_text,
        } for d in digests]


async def fetch_pr_assessment_texts() -> list[dict]:
    """Fetch PR assessment summaries for embedding.

    Combines summary + recommendation + key participant info into
    an embeddable text block per PR.
    """
    async with async_session() as session:
        result = await session.execute(
            select(PRAssessment).order_by(PRAssessment.repo, PRAssessment.pr_number)
        )
        assessments = result.scalars().all()

        texts = []
        for a in assessments:
            parts = [
                f"PR #{a.pr_number} ({a.repo})",
                f"Tier: {a.tier}, Complexity: {a.complexity}, Risk: {a.risk}",
            ]
            if a.summary:
                parts.append(a.summary)
            if a.recommendation:
                parts.append(a.recommendation)
            if a.outcome and isinstance(a.outcome, dict):
                impact = a.outcome.get("impact")
                if impact:
                    parts.append(f"Impact: {impact}")
            if a.lessons_learned:
                for lesson in a.lessons_learned:
                    if isinstance(lesson, dict) and lesson.get("principle"):
                        parts.append(f"Lesson: {lesson['principle']}")

            texts.append({
                "id": f"pr_{a.repo}_{a.pr_number}",
                "source_ref": f"{a.repo}#{a.pr_number}",
                "text": "\n".join(parts),
                "metadata": {
                    "source_type": "pr_assessment",
                    "repo": a.repo,
                    "pr_number": a.pr_number,
                    "tier": a.tier or 0,
                    "complexity": a.complexity or "",
                    "state": a.merge_readiness or "",
                },
            })
        return texts


def embed_collection(client, ef, collection_name: str, items: list[dict], rebuild: bool = False):
    """Embed items into a ChromaDB collection.

    Each item: {id, text, metadata}
    Uses content hashing to detect changes — new items get added,
    changed items get updated, unchanged items are skipped.
    """
    import hashlib

    collection = get_collection(client, collection_name, ef)

    if rebuild:
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
            print(f"  Deleted {len(existing['ids'])} existing documents")

    # Add content hash to each item's metadata
    for item in items:
        content_hash = hashlib.sha256(item["text"].encode()).hexdigest()[:16]
        item.setdefault("metadata", {})["content_hash"] = content_hash

    # Get existing items with their content hashes
    existing_hashes = {}
    if not rebuild:
        existing = collection.get(include=["metadatas"])
        for eid, emeta in zip(existing["ids"], existing["metadatas"]):
            existing_hashes[eid] = (emeta or {}).get("content_hash", "")

    # Split into: new (not in collection), changed (hash differs), unchanged (skip)
    to_upsert = []
    skipped = 0
    for item in items:
        existing_hash = existing_hashes.get(item["id"])
        if existing_hash == item["metadata"]["content_hash"]:
            skipped += 1
        else:
            to_upsert.append(item)

    if not to_upsert:
        print(f"  {collection_name}: {len(items)} items, all up to date")
        return 0

    new_count = sum(1 for item in to_upsert if item["id"] not in existing_hashes)
    updated_count = len(to_upsert) - new_count

    # Batch upsert
    BATCH_SIZE = 100
    for i in range(0, len(to_upsert), BATCH_SIZE):
        batch = to_upsert[i:i + BATCH_SIZE]
        collection.upsert(
            ids=[item["id"] for item in batch],
            documents=[item["text"] for item in batch],
            metadatas=[item["metadata"] for item in batch],
        )

    print(f"  {collection_name}: {new_count} new, {updated_count} updated, {skipped} unchanged")
    return len(to_upsert)


# ---------------------------------------------------------------------------
# Main operations
# ---------------------------------------------------------------------------

async def embed_all(rebuild: bool = False):
    """Embed all digest types into their respective collections."""
    client = get_chroma_client()
    ef = get_embedding_function()

    start = time.time()
    total_embedded = 0

    # 1. Telegram thread digests
    digests = await fetch_digests("telegram_thread")
    if digests:
        items = []
        for d in digests:
            # Extract topic and period from digest text for useful metadata
            topic = ""
            period = ""
            category = ""
            for line in d["digest_text"].split("\n"):
                if line.startswith("Topic: "):
                    topic = line[7:]
                elif line.startswith("Period: "):
                    period = line[8:]
                elif line.startswith("Category: "):
                    category = line[10:]
            items.append({
                "id": f"tg_{d['source_ref']}",
                "text": d["digest_text"],
                "metadata": {
                    "source_type": "telegram_thread",
                    "topic": topic,
                    "period": period,
                    "category": category,
                },
            })
        total_embedded += embed_collection(client, ef, "telegram_threads", items, rebuild)
    else:
        print("  telegram_threads: no digests to embed")

    # 2. PR assessments (directly from pr_assessments table, richer than ai_digests)
    pr_items = await fetch_pr_assessment_texts()
    if pr_items:
        total_embedded += embed_collection(client, ef, "pr_assessments", pr_items, rebuild)
    else:
        print("  pr_assessments: no assessments to embed")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s. Embedded {total_embedded} new documents.")


async def show_stats():
    """Show ChromaDB collection statistics."""
    client = get_chroma_client()
    ef = get_embedding_function()

    print(f"ChromaDB: {settings.chroma_path}")
    print(f"Embedding model: {settings.embedding_model}")
    print()

    for name in ["telegram_threads", "pr_assessments"]:
        try:
            collection = get_collection(client, name, ef)
            count = collection.count()
            print(f"  {name}: {count} documents")
        except Exception as e:
            print(f"  {name}: {e}")


async def search(query: str, n_results: int = 5):
    """Hybrid search: embedding similarity + keyword relevance boost.

    The keyword component is weighted by 1/keyword_hit_count — rare terms
    get a strong boost (each hit is highly specific), common terms get
    negligible boost (keyword matching adds no ranking value).
    """
    client = get_chroma_client()
    ef = get_embedding_function()

    # Extract search terms for keyword matching (lowercase, split on spaces)
    query_terms = [t.lower() for t in query.split() if len(t) >= 3]

    for name in ["telegram_threads", "pr_assessments"]:
        try:
            collection = get_collection(client, name, ef)
            if collection.count() == 0:
                continue

            # Fetch a large candidate pool — keyword boost can only re-rank
            # documents that were fetched, so we need enough candidates
            fetch_count = min(max(n_results * 10, 50), collection.count())
            results = collection.query(
                query_texts=[query],
                n_results=fetch_count,
                include=["documents", "metadatas", "distances"],
            )

            # Count keyword hits across ALL documents in the collection
            # to determine how specific each term is
            all_docs = collection.get(include=["documents"])
            term_doc_counts = {}
            for term in query_terms:
                term_doc_counts[term] = sum(
                    1 for doc in all_docs["documents"] if term in doc.lower()
                )

            # Score each result: embedding similarity + keyword boost
            scored = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                embedding_score = 1 - dist  # cosine distance → similarity

                # Keyword boost: for each query term found in this doc,
                # add 1/total_hits_for_that_term. Rare terms boost strongly.
                keyword_boost = 0.0
                doc_lower = doc.lower()
                for term in query_terms:
                    if term in doc_lower and term_doc_counts.get(term, 0) > 0:
                        keyword_boost += 1.0 / term_doc_counts[term]

                combined = embedding_score + keyword_boost
                scored.append((combined, embedding_score, keyword_boost, doc, meta))

            # Sort by combined score, take top n
            scored.sort(key=lambda x: -x[0])
            top = scored[:n_results]

            # Build label
            print(f"\n=== {name} ({collection.count()} docs) ===")
            for i, (combined, emb, kw, doc, meta) in enumerate(top):
                if name == "telegram_threads":
                    label = meta.get("topic", "?")
                elif name == "pr_assessments":
                    label = f"PR #{meta.get('pr_number', '?')} ({meta.get('repo', '?')})"
                else:
                    label = meta.get("source_ref", "?")

                kw_indicator = f" +kw:{kw:.3f}" if kw > 0 else ""
                print(f"\n  [{i+1}] {combined:.3f} (emb:{emb:.3f}{kw_indicator})  {label}")
                print(f"      {doc[:300]}...")

        except Exception as e:
            print(f"  {name}: {e}")


async def main():
    if "--stats" in sys.argv:
        await show_stats()
    elif "--search" in sys.argv:
        idx = sys.argv.index("--search")
        if idx + 1 >= len(sys.argv):
            print("Usage: embed_digests.py --search 'query text'")
            sys.exit(1)
        await search(sys.argv[idx + 1])
    elif "--rebuild" in sys.argv:
        await embed_all(rebuild=True)
    else:
        await embed_all()


if __name__ == "__main__":
    asyncio.run(main())
