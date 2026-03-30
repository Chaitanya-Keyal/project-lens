#!/usr/bin/env python3
"""Clone a repo (or pull if exists) and index its full git history into the DB.

Usage:
    python scripts/index_git_history.py                  # all repos in config
    python scripts/index_git_history.py SeedSigner/seedsigner  # specific repo

Run from the project root directory.
"""
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.database import engine, async_session
from backend.models import Base, GitCommit, AnalysisRun
from backend.project_config import load_project_config


def ensure_clone(clone_dir: Path, clone_url: str, repo_name: str) -> Path:
    """Clone the repo if it doesn't exist, otherwise fetch latest.

    Uses a full clone (not bare) so that git log --numstat produces diff stats.
    """
    repo_path = clone_dir / repo_name.replace("/", "_")

    if repo_path.exists():
        print(f"  Fetching latest for {repo_name}...")
        subprocess.run(
            ["git", "fetch", "--all"],
            cwd=repo_path, capture_output=True, check=True,
        )
        # Reset to origin default branch
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=repo_path, capture_output=True, text=True,
        )
        if result.returncode == 0:
            default_branch = result.stdout.strip().split("/")[-1]
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{default_branch}"],
                cwd=repo_path, capture_output=True, check=True,
            )
    else:
        print(f"  Cloning {repo_name}...")
        subprocess.run(
            ["git", "clone", clone_url, str(repo_path)],
            capture_output=True, check=True,
        )

    return repo_path


def parse_git_log(repo_path: Path) -> list[dict]:
    """Parse full git log with numstat into structured commit data.

    Format: separator, then hash/author/email/date/parents/subject, then numstat.
    The separator goes BEFORE the header so that numstat lines (which git appends
    after the format output) group with their own commit block.
    """
    separator = "---COMMIT_SEP---"
    fmt = f"{separator}%n%H%n%an%n%ae%n%aI%n%P%n%s"

    result = subprocess.run(
        ["git", "log", "--all", f"--format={fmt}", "--numstat"],
        cwd=repo_path, capture_output=True, text=True, check=True,
    )

    commits = []
    raw_commits = result.stdout.split(separator)

    for raw in raw_commits:
        raw = raw.strip()
        if not raw:
            continue

        lines = raw.split("\n")
        # First 6 lines are the formatted fields, rest is numstat
        if len(lines) < 6:
            continue

        commit_hash = lines[0].strip()
        author_name = lines[1].strip()
        author_email = lines[2].strip()
        date_str = lines[3].strip()
        parents = lines[4].strip()
        subject = lines[5].strip()

        # Parse numstat lines (insertions\tdeletions\tfilepath)
        files_changed = []
        total_insertions = 0
        total_deletions = 0

        for line in lines[6:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue

            ins_str, del_str, filepath = parts
            # Binary files show "-" for insertions/deletions
            ins = int(ins_str) if ins_str != "-" else 0
            dels = int(del_str) if del_str != "-" else 0

            files_changed.append({
                "path": filepath,
                "insertions": ins,
                "deletions": dels,
            })
            total_insertions += ins
            total_deletions += dels

        # Parse date
        try:
            date = datetime.fromisoformat(date_str)
        except ValueError:
            continue

        is_merge = " " in parents  # Multiple parents = merge commit

        commits.append({
            "hash": commit_hash,
            "author_name": author_name,
            "author_email": author_email,
            "date": date,
            "message": subject,
            "files_changed": files_changed,
            "insertions": total_insertions,
            "deletions": total_deletions,
            "is_merge": is_merge,
        })

    return commits


async def get_existing_hashes(session, repo: str) -> set[str]:
    """Get all commit hashes already in the DB for this repo."""
    result = await session.execute(
        select(GitCommit.hash).where(GitCommit.repo == repo)
    )
    return {row[0] for row in result.all()}


async def index_repo(repo_full_name: str, clone_url: str):
    """Clone/update repo and index all commits."""
    clone_dir = settings.clones_path
    repo_path = ensure_clone(clone_dir, clone_url, repo_full_name)

    print(f"  Parsing git log for {repo_full_name}...")
    commits = parse_git_log(repo_path)
    print(f"  Found {len(commits)} commits")

    async with async_session() as session:
        existing = await get_existing_hashes(session, repo_full_name)
        new_commits = [c for c in commits if c["hash"] not in existing]
        print(f"  {len(new_commits)} new commits to index")

        now = datetime.now(timezone.utc)
        for commit_data in new_commits:
            commit = GitCommit(
                repo=repo_full_name,
                last_synced=now,
                **commit_data,
            )
            session.add(commit)

        await session.commit()

    return {"repo": repo_full_name, "total": len(commits), "new": len(new_commits)}


async def main():
    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    config = load_project_config()
    run_start = datetime.now(timezone.utc)

    # Filter to specific repo if given as CLI arg
    if len(sys.argv) > 1:
        target = sys.argv[1]
        repos = [r for r in config.repos if r.full_name == target]
        if not repos:
            print(f"Repo '{target}' not found in config. Available:")
            for r in config.repos:
                print(f"  {r.full_name}")
            sys.exit(1)
    else:
        repos = config.repos

    print(f"Indexing git history for {len(repos)} repo(s): {config.display_name}")
    stats = []

    for repo in repos:
        try:
            result = await index_repo(repo.full_name, repo.clone_url)
            stats.append(result)
            print(f"  {result['repo']}: {result['new']} new / {result['total']} total commits")
        except Exception as e:
            print(f"  {repo.full_name}: Error — {e}")
            stats.append({"repo": repo.full_name, "error": str(e)})

    # Record the analysis run
    async with async_session() as session:
        run = AnalysisRun(
            run_type="git_history_index",
            started_at=run_start,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=str(stats),
        )
        session.add(run)
        await session.commit()

    total_new = sum(s.get("new", 0) for s in stats)
    total_all = sum(s.get("total", 0) for s in stats)
    print(f"Done: {total_new} new commits indexed ({total_all} total) across {len(repos)} repo(s).")


if __name__ == "__main__":
    asyncio.run(main())
