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
        items = [{
            "id": f"tg_{d['source_ref']}",
            "text": d["digest_text"],
            "metadata": {
                "source_type": d["source_type"],
                "source_ref": d["source_ref"],
                "digest_type": d["digest_type"],
            },
        } for d in digests]
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
    """Search across all collections."""
    client = get_chroma_client()
    ef = get_embedding_function()

    for name in ["telegram_threads", "pr_assessments"]:
        try:
            collection = get_collection(client, name, ef)
            if collection.count() == 0:
                continue

            results = collection.query(
                query_texts=[query],
                n_results=min(n_results, collection.count()),
            )

            print(f"\n=== {name} ({collection.count()} docs) ===")
            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )):
                score = 1 - dist  # cosine distance → similarity
                print(f"\n  [{i+1}] Score: {score:.3f} | {meta.get('source_ref', '?')}")
                # Show first 300 chars of document
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
